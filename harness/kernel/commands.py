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
from harness.kernel.economics import economics_snapshot
from harness.kernel.gate import PolicyGate
from harness.kernel.registry import PluginRegistry
from harness.kernel.runner import TurnRunner
from harness.kernel.secrets import Secrets
from harness.kernel.session import SessionManager
from harness.kernel.settings import SettingsStore
from harness.kernel.validators import validate_session_id
from harness.sdk.cards import Card
from harness.sdk.contracts import Provider
from harness.sdk.errors import HarnessError
from harness.sdk.events import Event, SettingsChanged, UiCard
from harness.sdk.types import ModelRow, TurnResult

logger = logging.getLogger(__name__)

#: Every built-in command a frontend can dispatch; the handler is the method ``name.replace(".", "_")``.
#: ``docs/ARCHITECTURE.md`` lists them and ``tests/test_docs_and_versions.py`` keeps the two in sync.
BUILTIN_COMMANDS: tuple[str, ...] = (
    "submit_turn",
    "cancel_turn",
    "resolve_approval",
    "form.submit",
    "form_submit",  # underscore alias of form.submit, kept for external callers
    "sessions.list",
    "sessions.create",
    "sessions.get",
    "sessions.switch",
    "settings.get",
    "settings.set",
    "settings.schema",
    "models.list",
    "files.read",
    "files.write",
    "plugins.list",
    "plugins.info",
    "plugins.enable",
    "plugins.disable",
    "approvals.list",
    "economics",
)


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
        session_id: str | None = None,
        prompt: str = "",
        media: list[Any] | None = None,
        options: dict[str, Any] | None = None,
        model: str | None = None,
        provider: str | None = None,
        strategy: str | None = None,
    ) -> TurnResult:
        """
        Submits a user prompt turn to a session.
        Enforces one active turn per session via session lock (raises TurnInProgress / 409).

        ``model`` / ``provider`` / ``strategy`` are accepted as top-level arguments
        because that is the payload the web client sends (``{prompt, session_id,
        model}``); they are folded into ``options``. When ``session_id`` is omitted
        the active session is used, or a new one is created, so a fresh daemon can
        accept its first prompt without the client having to know about sessions.
        """
        if not prompt or not str(prompt).strip():
            raise HarnessError("Missing 'prompt'")

        if session_id:
            validate_session_id(session_id)
            session = self.session_manager.get_session(session_id)
            if not session:
                session = await self.session_manager.create_session(session_id=session_id)
        else:
            session = self.session_manager.active_session or await self.session_manager.create_session()

        turn_options: dict[str, Any] = dict(options or {})
        for key, value in (("model", model), ("provider", provider), ("strategy", strategy)):
            if value:
                turn_options[key] = value

        async with session.turn_lock():
            return await self.runner.execute_turn(
                session=session.info,
                prompt=prompt,
                media=media,
                options=turn_options or None,
            )

    async def cancel_turn(self, session_id: str) -> bool:
        """Signals cancellation to an active turn in session."""
        validate_session_id(session_id)
        return self.runner.cancel_turn(session_id)

    async def resolve_approval(
        self,
        approval_id: str | None = None,
        request_id: str | None = None,
        approved: bool | None = None,
        decision: str | None = None,
        by: str = "user",
        reason: str = "",
        feedback: str = "",
    ) -> dict[str, Any]:
        """Resolves an in-flight approval request."""
        target_id = approval_id or request_id
        if not target_id:
            raise HarnessError("Missing 'approval_id' or 'request_id'")
        is_approved = approved if approved is not None else (decision == "approved")
        res_reason = reason or feedback
        ok = self.gate.approvals.resolve(
            approval_id=target_id,
            approved=is_approved,
            by=by,
            reason=res_reason,
        )
        return {
            "status": "resolved" if ok else "not_found",
            "approval_id": target_id,
            "decision": "approved" if is_approved else "denied",
        }

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
        validate_session_id(session_id)
        session = self.session_manager.get_session(session_id)
        if not session:
            raise KeyError(f"Session not found: {session_id}")
        session = await self.session_manager.switch_session(session_id)
        res = session.info.to_dict()
        res["status"] = "switched"
        return res

    async def settings_get(self, path: str, default: Any = None) -> Any:
        """Reads namespaced setting value, or checks secret existence with has_secret."""
        if path.startswith("has_secret."):
            secret_key = path.split(".", 1)[1]
            return self.secrets.has(secret_key)
        if path.startswith("secrets.has."):
            secret_key = path.split(".", 2)[2]
            return self.secrets.has(secret_key)
        return self.settings.get(path, default)

    async def settings_set(self, path: str, value: Any) -> dict[str, Any]:
        """Updates setting value under schema validation, routing secrets to Secrets store."""
        if path.startswith("secrets."):
            secret_key = path.split(".", 1)[1]
            self.secrets.set(secret_key, str(value))
            self._publish_settings_changed([path])
            return {"path": path, "status": "ok", "has": True}
        self.settings.set(path, value)
        self._publish_settings_changed([path])
        return {"path": path, "value": value, "status": "ok"}

    def _publish_settings_changed(self, paths: list[str]) -> None:
        """Tells every frontend which setting paths changed (values are never put on the bus)."""
        if self.bus:
            self.bus.publish(Event(type="settings.changed", data=SettingsChanged(paths=paths)))

    async def settings_schema(self) -> dict[str, Any]:
        """Returns consolidated schema with x-ui groups and widgets (§11, WEB-05)."""
        registered = self.settings.get_registered_schemas()
        kernel_props = registered.get("kernel", {}).get("properties", {})
        return {
            "groups": [
                "Models & Providers",
                "Security & Sandboxing",
                "Execution & Strategies",
                "Economics & Spend",
                "Secrets",
            ],
            "properties": {
                "kernel.provider": {
                    "title": "Default Provider",
                    "type": "string",
                    "enum": ["openai_compat", "anthropic", "gemini", "ollama", "mock"],
                    "default": kernel_props.get("provider", {}).get("default", "openai_compat"),
                    "x-ui": {"group": "Models & Providers", "widget": "select"},
                },
                "kernel.model": {
                    "title": "Default Model",
                    "type": "string",
                    "default": kernel_props.get("model", {}).get("default", "gpt-4o"),
                    "x-ui": {"group": "Models & Providers", "widget": "text"},
                },
                "kernel.strategy": {
                    "title": "Turn Strategy",
                    "type": "string",
                    "enum": ["single_loop", "architect_editor"],
                    "default": kernel_props.get("strategy", {}).get("default", "single_loop"),
                    "x-ui": {"group": "Execution & Strategies", "widget": "select"},
                },
                "kernel.max_tool_iterations": {
                    "title": "Max Tool Iterations",
                    "type": "integer",
                    "default": kernel_props.get("max_tool_iterations", {}).get("default", 20),
                    "x-ui": {"group": "Execution & Strategies", "widget": "number"},
                },
                "kernel.security.spend_cap_usd": {
                    "title": "Spend Cap (USD)",
                    "type": "number",
                    "default": 5.0,
                    "minimum": 0.0,
                    "x-ui": {"group": "Economics & Spend", "widget": "number"},
                },
                "kernel.security.jail_enabled": {
                    "title": "Workspace Jail Enforcement",
                    "type": "boolean",
                    "default": True,
                    "x-ui": {"group": "Security & Sandboxing", "widget": "boolean"},
                },
                "secrets.OPENAI_API_KEY": {
                    "title": "OpenAI API Key",
                    "type": "string",
                    "x-ui": {"group": "Secrets", "widget": "secret"},
                },
                "secrets.ANTHROPIC_API_KEY": {
                    "title": "Anthropic API Key",
                    "type": "string",
                    "x-ui": {"group": "Secrets", "widget": "secret"},
                },
                "secrets.GEMINI_API_KEY": {
                    "title": "Gemini API Key",
                    "type": "string",
                    "x-ui": {"group": "Secrets", "widget": "secret"},
                },
                "secrets.TIRITH_API_KEY": {
                    "title": "Tirith Security Key",
                    "type": "string",
                    "x-ui": {"group": "Secrets", "widget": "secret"},
                },
            },
        }

    async def models_list(self) -> list[dict[str, Any]]:
        """``models.list`` → ``ModelRow`` dicts for every model every provider knows about.

        Readiness comes from the provider itself (``is_configured()`` / ``is_reachable()``), never from
        name heuristics here. Probes run concurrently and are short by contract; a provider that fails is
        logged at DEBUG and contributes no rows — this command never raises.
        """

        async def rows_for(p: Provider) -> list[dict[str, Any]]:
            try:
                configured = bool(p.is_configured())
                reachable = await p.is_reachable()
                models = await p.list_models()
            except Exception as e:  # noqa: BLE001 — one broken provider must not hide the others
                logger.debug("models.list: provider %r unavailable: %s", p.name, e)
                return []
            return [ModelRow.from_info(m, p.name, configured=configured, reachable=reachable).to_dict() for m in models]

        results: list[dict[str, Any]] = []
        for rows in await asyncio.gather(*(rows_for(p) for p in self.registry.get_all_providers())):
            results.extend(rows)
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
        return self.gate.approvals.pending()

    async def economics(self, session_id: str | None = None) -> dict[str, Any]:
        """Returns the ``EconomicsSnapshot`` for a session (defaults to the active session).

        Same shape as the ``cost.updated`` event payload and ``GET /health`` ``spend``.
        """
        if session_id is None and self.session_manager.active_session:
            session_id = self.session_manager.active_session.session_id
        snapshot = await economics_snapshot(
            ledger=self.runner.cost_ledger,
            settings=self.settings,
            session_id=session_id,
        )
        return snapshot.to_dict()

    async def form_submit(self, form_id: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        """Handles human interactive form submission from studio frontends."""
        payload = data or {}
        if self.bus:
            self.bus.publish(
                Event(
                    type="ui.card",
                    data=UiCard(
                        card=Card.notice(f"Form '{form_id}' submitted.", level="success", title="Form submitted").to_dict()
                    ),
                    session_id=self.session_manager.active_session.session_id if self.session_manager.active_session else None,
                )
            )
        return {"form_id": form_id, "status": "submitted", "data": payload}

    async def dispatch(self, command: str, args: dict[str, Any] | None = None) -> Any:
        """Generic command dispatcher for JSON-based frontend interfaces (``POST /commands/<name>``)."""
        params = args or {}
        if command in BUILTIN_COMMANDS:
            handler = getattr(self, command.replace(".", "_"))
            return await handler(**params)

        if command in self._custom_commands:
            handler = self._custom_commands[command]
            res = handler(**params)
            if asyncio.iscoroutine(res):
                return await res
            return res

        raise HarnessError(f"Unknown command: '{command}'")
