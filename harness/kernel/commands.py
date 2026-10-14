"""
Commands: central frontend API dispatcher for Session management, turns, approvals,
settings, models, files, plugins, and economics.
Fulfills KRN-09 (§5.1, §5.2) and absorbs SEC-06 and LOOP-05/06.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from harness.kernel.bus import EventBus
from harness.kernel.gate import PolicyGate
from harness.kernel.registry import PluginRegistry
from harness.kernel.runner import TurnRunner
from harness.kernel.secrets import Secrets
from harness.kernel.session import SessionManager
from harness.kernel.settings import SettingsStore
from harness.kernel.validators import validate_session_id
from harness.sdk.errors import HarnessError
from harness.sdk.types import TurnResult

logger = logging.getLogger(__name__)


class Commands:
    """
    Universal API gateway invoked by frontends (CLI, Web, ACP).
    Translates RPC / JSON commands to kernel services under security invariants.
    """

    def __init__(
        self,
        workspace: Path,
        session_manager: SessionManager,
        runner: TurnRunner,
        gate: PolicyGate,
        settings: SettingsStore,
        secrets: Secrets,
        registry: PluginRegistry,
        bus: EventBus | None = None,
    ) -> None:
        self.workspace = Path(workspace)
        self.session_manager = session_manager
        self.runner = runner
        self.gate = gate
        self.settings = settings
        self.secrets = secrets
        self.registry = registry
        self.bus = bus
        self._custom_commands: dict[str, Callable[..., Any]] = {}

    def register_command(self, name: str, handler: Callable[..., Any]) -> None:
        """Registers a custom command from plugins or extensions."""
        self._custom_commands[name] = handler

    async def submit_turn(
        self,
        session_id: str,
        prompt: str,
        media: list[Any] | None = None,
        options: dict[str, Any] | None = None,
    ) -> TurnResult:
        """
        Submits a user prompt turn to a session.
        Enforces one active turn per session via session lock (raises TurnInProgress / 409).
        """
        validate_session_id(session_id)
        session = self.session_manager.get_session(session_id)
        if not session:
            session = await self.session_manager.create_session(session_id=session_id)

        async with session.turn_lock():
            return await self.runner.execute_turn(
                session=session.info,
                prompt=prompt,
                media=media,
                options=options,
            )

    async def cancel_turn(self, session_id: str) -> bool:
        """Signals cancellation to an active turn in session."""
        validate_session_id(session_id)
        return self.runner.cancel_turn(session_id)

    async def resolve_approval(
        self,
        approval_id: str,
        approved: bool,
        by: str = "user",
        reason: str = "",
    ) -> bool:
        """Resolves an in-flight approval request."""
        return self.gate.approvals.resolve(
            approval_id=approval_id,
            approved=approved,
            by=by,
            reason=reason,
        )

    async def sessions_list(self) -> list[dict[str, Any]]:
        """Lists historical and active sessions in the workspace."""
        return self.session_manager.list_sessions()

    async def sessions_create(
        self,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Creates and initializes a new session."""
        session = await self.session_manager.create_session(
            session_id=session_id, metadata=metadata
        )
        return session.info.to_dict()

    async def sessions_get(self, session_id: str) -> dict[str, Any]:
        """Retrieves session details and full history trajectory."""
        validate_session_id(session_id)
        session = self.session_manager.get_session(session_id)
        if not session:
            raise HarnessError(f"Session '{session_id}' not found")

        records = await session.transcript.read(compact=False)
        return {
            "session_id": session_id,
            "workspace": str(session.workspace),
            "created_at": session.created_at,
            "metadata": session.metadata,
            "records": records,
            "busy": session.is_busy,
        }

    async def sessions_switch(self, session_id: str) -> dict[str, Any]:
        """Switches active session and emits session.switched."""
        session = await self.session_manager.switch_session(session_id)
        return session.info.to_dict()

    async def settings_get(self, path: str, default: Any = None) -> Any:
        """Reads namespaced setting value."""
        return self.settings.get(path, default)

    async def settings_set(self, path: str, value: Any) -> dict[str, Any]:
        """Updates setting value under schema validation."""
        self.settings.set(path, value)
        return {"path": path, "value": value, "status": "ok"}

    async def models_list(self) -> list[dict[str, Any]]:
        """Aggregates models supported across all registered providers."""
        results: list[dict[str, Any]] = []
        for p in self.registry.get_all_providers():
            try:
                models = await p.list_models()
                for m in models:
                    d = m.to_dict()
                    d["provider"] = p.name
                    results.append(d)
            except Exception as e:
                logger.warning(f"Error querying models from provider '{p.name}': {e}")
        return results

    async def files_read(self, path: str) -> dict[str, Any]:
        """Reads file within workspace jail boundaries as user principal."""
        target = self.gate.jail.resolve(path, for_write=False)
        if not target.exists():
            raise FileNotFoundError(f"File not found: {path}")

        try:
            content = target.read_text(encoding="utf-8")
            is_binary = False
        except UnicodeDecodeError:
            content = base64.b64encode(target.read_bytes()).decode("ascii")
            is_binary = True

        return {
            "path": str(target.relative_to(self.workspace)),
            "content": content,
            "is_binary": is_binary,
            "size": target.stat().st_size,
        }

    async def files_write(
        self,
        path: str,
        content: str,
        is_base64: bool = False,
    ) -> dict[str, Any]:
        """Writes file within workspace jail atomically as user principal."""
        target = self.gate.jail.resolve(path, for_write=True)
        target.parent.mkdir(parents=True, exist_ok=True)

        data = base64.b64decode(content) if is_base64 else content.encode("utf-8")

        # Atomic write via temp file in same directory
        fd, temp_path = tempfile.mkstemp(dir=target.parent, prefix=".harness_write_")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_path, target)
        except Exception:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
            raise

        return {
            "path": str(target.relative_to(self.workspace)),
            "bytes_written": len(data),
            "status": "ok",
        }

    async def plugins_list(self) -> list[dict[str, Any]]:
        """Lists all discovered plugins and their active load status."""
        results: list[dict[str, Any]] = []
        for name, dp in self.registry.discovered.items():
            results.append({
                "name": name,
                "version": dp.manifest.version,
                "summary": dp.manifest.summary,
                "provides": dp.manifest.provides,
                "capabilities": [
                    c.value if hasattr(c, "value") else str(c)
                    for c in dp.manifest.capabilities
                ],
                "tier": dp.tier.value,
                "loaded": self.registry.is_loaded(name),
            })
        return results

    async def plugins_info(self, name: str) -> dict[str, Any] | None:
        """Retrieves manifest details and capabilities for a named plugin."""
        dp = self.registry.discovered.get(name)
        if not dp:
            return None
        return {
            "name": name,
            "version": dp.manifest.version,
            "summary": dp.manifest.summary,
            "provides": dp.manifest.provides,
            "capabilities": [
                c.value if hasattr(c, "value") else str(c)
                for c in dp.manifest.capabilities
            ],
            "tier": dp.tier.value,
            "loaded": self.registry.is_loaded(name),
            "path": str(dp.path),
        }

    async def plugins_enable(self, name: str) -> bool:
        """Removes plugin from disabled list in settings."""
        disabled: list[str] = list(self.settings.get("kernel.plugins.disabled", []))
        if name in disabled:
            disabled.remove(name)
            self.settings.set("kernel.plugins.disabled", disabled)
            return True
        return False

    async def plugins_disable(self, name: str) -> bool:
        """Adds plugin to disabled list in settings."""
        disabled: list[str] = list(self.settings.get("kernel.plugins.disabled", []))
        if name not in disabled:
            disabled.append(name)
            self.settings.set("kernel.plugins.disabled", disabled)
            return True
        return False

    async def approvals_list(self) -> list[dict[str, Any]]:
        """Lists pending approval requests waiting for human resolution."""
        return self.gate.approvals.list_pending()

    async def economics(self, session_id: str | None = None) -> dict[str, Any]:
        """Returns spend and token usage ledger totals."""
        turn_cost = self.runner.cost_ledger
        totals = None
        if turn_cost:
            totals = await turn_cost.totals(session_id=session_id)

        spend_cap = self.settings.get("kernel.security.spend_cap_usd", 5.0)
        return {
            "spend_cap_usd": spend_cap,
            "totals": totals.to_dict() if totals else None,
        }

    async def dispatch(self, command: str, args: dict[str, Any] | None = None) -> Any:
        """Generic command dispatcher for JSON-based frontend interfaces."""
        params = args or {}
        cmd_map = {
            "submit_turn": self.submit_turn,
            "cancel_turn": self.cancel_turn,
            "resolve_approval": self.resolve_approval,
            "sessions.list": self.sessions_list,
            "sessions.create": self.sessions_create,
            "sessions.get": self.sessions_get,
            "sessions.switch": self.sessions_switch,
            "settings.get": self.settings_get,
            "settings.set": self.settings_set,
            "models.list": self.models_list,
            "files.read": self.files_read,
            "files.write": self.files_write,
            "plugins.list": self.plugins_list,
            "plugins.info": self.plugins_info,
            "plugins.enable": self.plugins_enable,
            "plugins.disable": self.plugins_disable,
            "approvals.list": self.approvals_list,
            "economics": self.economics,
        }

        if command in cmd_map:
            handler = cmd_map[command]
            return await handler(**params)

        if command in self._custom_commands:
            handler = self._custom_commands[command]
            res = handler(**params)
            if asyncio.iscoroutine(res):
                return await res
            return res

        raise HarnessError(f"Unknown command: '{command}'")
