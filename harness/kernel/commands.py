"""
Commands: central frontend API dispatcher for Session management, turns, approvals,
settings, models, files, plugins, and economics.
Fulfills KRN-09 (§5.1, §5.2) and absorbs SEC-06 and LOOP-05/06.
"""
from __future__ import annotations

import asyncio
import base64
import inspect
import logging
import os
import tempfile
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from harness.kernel.bus import EventBus
from harness.kernel.economics import economics_snapshot
from harness.kernel.gate import PolicyGate
from harness.kernel.plugin_paths import plugin_dir
from harness.kernel.registry import PluginRegistry, RegisteredPluginCommand
from harness.kernel.runner import TurnRunner
from harness.kernel.secrets import Secrets
from harness.kernel.session import SessionManager
from harness.kernel.settings import SettingsStore, validate_schema_value
from harness.kernel.settings_schema import (
    flatten_schema_properties,
    order_groups,
    settings_owner_index,
    with_ui_defaults,
)
from harness.kernel.validators import validate_session_id
from harness.sdk.cards import Card
from harness.sdk.contracts import Provider
from harness.sdk.errors import HarnessError, SettingsValidationError
from harness.sdk.events import Event, EventPayload, SettingsChanged, UiCard
from harness.sdk.types import RECORD_KINDS, ModelRow, TranscriptRecord, TurnResult

logger = logging.getLogger(__name__)

#: Every built-in command a frontend can dispatch; the handler is the method ``name.replace(".", "_")``.
#: ``docs/ARCHITECTURE.md`` lists them and ``tests/test_docs_and_versions.py`` keeps the two in sync.
BUILTIN_COMMANDS: tuple[str, ...] = (
    "submit_turn",
    "cancel_turn",
    "resolve_approval",
    "question.answer",
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
    "interactions.list",
    "economics",
    # The client's boot-time vocabulary (WS-05): what may be dispatched, and what to mount. Both exist so the
    # shipped client stops hard-coding the answer — a button wired to a command that no longer exists, or a
    # panel a disabled plugin still contributes, is a bug the contract test can only catch if the client asks
    # the kernel instead of remembering.
    "commands.list",
    "ui.contributions",
    # WS-06a: the security posture is state the user is entitled to see without opening the settings modal, so
    # the kernel answers it as a command — the Studio's status pill, `harness security show`, and any frontend
    # that wants a badge all read the same resolution of preset + overrides (never a copy of the schema default).
    "security.status",
)

# Command renames remain dispatchable for old clients, but are marked as non-UI aliases in commands.list.
COMMAND_ALIASES: dict[str, str] = {"approvals.list": "interactions.list"}


class _PluginCommandRuntime:
    """The constrained kernel services passed to a plugin-owned frontend command."""

    def __init__(self, commands: Commands) -> None:
        self._commands = commands
        self.workspace = commands.workspace
        self.settings = commands.settings

    def _session(self, session_id: str):
        validate_session_id(session_id)
        session = self._commands.session_manager.get_session(session_id)
        if session is None:
            raise HarnessError(f"Unknown session: {session_id}")
        return session

    @asynccontextmanager
    async def session_lock(self, session_id: str):
        """Serialize the operation with a turn and fail with TurnInProgress rather than racing it."""
        session = self._session(session_id)
        async with session.turn_lock():
            yield None

    async def read_records(self, session_id: str) -> list[dict[str, Any]]:
        return await self._session(session_id).get_history()

    async def request_approval(
        self,
        *,
        session_id: str,
        kind: str,
        summary: str,
        details: dict[str, Any] | None = None,
        destructive: bool = False,
    ) -> tuple[bool, str]:
        """Ask through the same user-visible broker as tool approvals, never auto-approve a command."""
        validate_session_id(session_id)
        if destructive and bool(self.settings.get("kernel.security.destructive_blocking", True)):
            return False, "destructive action blocked by kernel.security.destructive_blocking"
        return await self._commands.gate.approvals.ask(
            kind=kind,
            summary=summary,
            details=details or {},
            session_id=session_id,
        )

    async def emit_record(
        self,
        *,
        session_id: str,
        turn_id: str | None,
        payload: EventPayload,
        content: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Persist the plugin fact before publishing the matching event."""
        session = self._session(session_id)
        kind = getattr(type(payload), "record_kind", None)
        event_type = getattr(type(payload), "EVENT_TYPE", None)
        if not kind or not event_type:
            raise HarnessError(f"Payload {type(payload).__name__} is not a durable event")
        from harness.kernel.transcript import record_at_step

        step = await session.log.append(
            TranscriptRecord(kind=kind, role="system", content=content, metadata=dict(metadata))
        )
        record = record_at_step(session.log.records, step)
        if self._commands.bus is not None:
            self._commands.bus.publish(
                Event(
                    type=event_type,
                    data=payload,
                    session_id=session_id,
                    turn_id=turn_id,
                    record=record,
                )
            )
        return record


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
        self._custom_commands: dict[str, RegisteredPluginCommand | Callable[..., Any]] = {
            name: registration for name, registration in registry.commands.items()
        }
        self._plugin_command_context = _PluginCommandRuntime(self)

    def register_command(
        self,
        name: str,
        handler: Callable[..., Any],
        *,
        owner: str = "extension",
        args_schema: dict[str, Any] | None = None,
        exposed: bool = True,
    ) -> None:
        """Registers a custom command from an extension (plugins use ``PluginContext.register_command``)."""
        self._custom_commands[name] = RegisteredPluginCommand(
            name=name,
            handler=handler,
            plugin=owner,
            args_schema=args_schema,
            exposed=exposed,
        )

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
        # Log-before-emit (WS-04): the resolution is a record, and the event carries it, so a reloaded
        # session shows who decided what instead of an approval card stuck on "pending".
        ok = await self.gate.approvals.resolve_recorded(
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

        # Read through the session log, not the raw store: v1 lines get upgraded and gain a `kind`, so
        # the client renders a reloaded session with exactly the records the live session produced
        # (WS-04). `records` is the one history shape the UI consumes — there is no second `history` key
        # to keep in step with it.
        records = await session.get_history()
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

    async def security_status(self) -> dict[str, Any]:
        """``security.status`` → the preset in effect, what it resolves to, and what the user changed.

        A preset is a *resolution layer* over ordinary settings (`harness/kernel/presets.py`), so a frontend that
        read `settings.schema`'s defaults would report the wrong thing the moment someone picked `open`: the
        schema default for permission mode stays `ask` while the effective value is `allow`. This answers with the
        effective values plus the two lists that explain them — `customised` (a preset-controlled key whose value
        differs from the preset) and `pinned` (one the user wrote, whether or not it differs).

        Deliberately *not* here: any secret, any configured extra-readable path, or any list of what a host has
        installed. The Studio renders this without a token in some deployments (`/health` carries it), so the
        payload is posture, not inventory. The sandbox block reports the backend probe and enforcement fact rather
        than echoing a sandbox configuration value.
        """
        from harness.kernel import presets

        summary = presets.summary(self.settings)
        return {
            "preset": summary["preset"],
            "customised": summary["customised"],
            "pinned": summary["pinned"],
            "settings": summary["effective"],
            "sandbox": self.registry.sandbox.status(),
        }

    def _publish_settings_changed(self, paths: list[str]) -> None:
        """Tells every frontend which setting paths changed (values are never put on the bus)."""
        if self.bus:
            self.bus.publish(Event(type="settings.changed", data=SettingsChanged(paths=paths)))

    async def settings_schema(self) -> dict[str, Any]:
        """``settings.schema`` → the settings surface aggregated from the *registered* schemas.

        Every property here comes from a JSON Schema the loader actually registered: the kernel's
        ``KERNEL_SCHEMA`` (only properties carrying ``x-ui``, so internal flags such as
        ``kernel.plugins.disabled`` stay out of the panel), each plugin's ``settings.schema.json``
        (WS-02), and the union of secret keys the plugins declared they read. Nothing is a
        hand-kept list, so a setting cannot appear in the UI without a reader — and the provider
        and strategy enums are the components that actually loaded, not a literal.

        Response shape (the client's contract): ``{"groups": [...], "properties": {key: schema}}``
        with ``x-ui.group`` choosing the tab and ``x-ui.widget`` the control.
        """
        registered = self.settings.get_registered_schemas()
        alias_namespaces = getattr(self.registry, "alias_namespaces", frozenset())
        # Which tab a plugin's settings default to, and who owns them, comes from the *registry* (its family
        # directory and its manifest summary) — so adding a plugin needs no entry anywhere else, and the
        # catalogue computes the same thing from the same index.
        owners = settings_owner_index(self.registry)

        props: dict[str, Any] = {}
        for namespace in sorted(registered):
            if namespace in alias_namespaces or namespace == "secrets":
                continue  # aliases duplicate a shown key; secrets are appended as one group
            flat = flatten_schema_properties(registered[namespace].get("properties", {}))
            for key, prop in flat.items():
                if namespace == "kernel" and "x-ui" not in prop:
                    continue
                props[f"{namespace}.{key}"] = with_ui_defaults(prop, namespace, **owners.get(namespace, {}))

        for key, prop in registered.get("secrets", {}).get("properties", {}).items():
            props[f"secrets.{key}"] = with_ui_defaults(prop, "secrets")

        providers = sorted({prov.name for prov in self.registry.get_all_providers()})
        if providers and "kernel.provider" in props:
            props["kernel.provider"] = {**props["kernel.provider"], "enum": providers}
        strategies = self.registry.strategy_names()
        if strategies and "kernel.strategy" in props:
            props["kernel.strategy"] = {**props["kernel.strategy"], "enum": strategies}

        present = {prop.get("x-ui", {}).get("group") for prop in props.values()}
        if "secrets" in registered:
            present.add("Secrets")
        # Ordered by the schemas themselves, not by a list of tab names kept in the kernel: a plugin's group
        # is a tab whether or not anyone here has heard of it (WS-05).
        groups = order_groups(sorted(g for g in present if g), props)
        return {"groups": groups, "properties": props}

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
        """Lists every discovered plugin with what an operator needs in order to judge it.

        Three callers read this row — `harness plugin list`, the TUI, and the Studio's Plugins pane — so it
        carries more than a name: the capability family it came from, whether the user switched it off, how
        many settings it brings and which tab they live on, and whether it contributes any UI at all. A
        plugin browser that cannot answer "does this one do anything I can see?" is a list of directories.

        The two state flags are deliberately not collapsed. ``loaded`` is the live answer
        (`registry.is_loaded`); ``disabled`` is the user's intent for the **next start**, because
        `plugins.enable` / `plugins.disable` only write ``kernel.plugins.disabled`` and nothing hot-unloads a
        plugin mid-session. Reporting one boolean would imply a restart never matters. Settings ownership and
        the tab come from the same derivation the settings surface uses (`settings_owner_index` +
        ``x-ui.group``), so a pane cannot disagree with the modal about where a plugin's controls are.
        """
        registered = self.settings.get_registered_schemas()
        disabled = {str(name) for name in self.settings.get("kernel.plugins.disabled", []) or []}
        owners = settings_owner_index(self.registry)
        results: list[dict[str, Any]] = []
        for name, dp in self.registry.discovered.items():
            namespace = f"plugins.{name}"
            meta = owners.get(namespace, {})
            props = flatten_schema_properties(registered.get(namespace, {}).get("properties", {}))
            group: str | None = None
            for prop in props.values():
                declared = with_ui_defaults(prop, namespace, **meta).get("x-ui", {}).get("group")
                if declared:
                    group = str(declared)
                    break
            ui = getattr(dp.manifest, "ui", None)
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
                "disabled": name in disabled,
                "family": meta.get("family"),
                "has_settings": bool(props),
                "settings_count": len(props),
                "settings_group": group,
                "panels": sorted(panel.id for panel in getattr(ui, "panels", None) or []),
                "record_renderers": sorted(getattr(ui, "record_renderers", None) or []),
                "client_module": getattr(ui, "client_module", None),
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
            "path": str(plugin_dir(dp)),
            "settings_schema": dp.manifest.settings_schema,
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

    async def approvals_list(self, session_id: str | None = None) -> list[dict[str, Any]]:
        """Compatibility alias for ``interactions.list``; includes pending approvals and questions."""
        return await self.interactions_list(session_id=session_id)

    async def interactions_list(self, session_id: str | None = None) -> list[dict[str, Any]]:
        """Lists pending approvals and questions, optionally filtered to one session."""
        return self.gate.approvals.interactions.pending(session_id=session_id)

    async def question_answer(
        self,
        interaction_id: str,
        answers: dict[str, Any],
        by: str = "user",
    ) -> dict[str, Any]:
        """Resolves a pending structured question after validating its option ids and custom text."""
        if not interaction_id:
            raise HarnessError("Missing 'interaction_id'")
        try:
            ok = await self.gate.approvals.interactions.resolve_question(
                interaction_id=interaction_id,
                answers=answers,
                by=by,
            )
        except ValueError as err:
            raise HarnessError(f"Invalid question answer: {err}") from err
        return {
            "status": "answered" if ok else "not_found",
            "interaction_id": interaction_id,
        }

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
        """Handles generic forms; routes pending ask_user forms through the compatibility alias."""
        payload = data or {}
        if self.gate.approvals.interactions.is_pending_question(form_id):
            result = await self.question_answer(interaction_id=form_id, answers=payload)
            return {"form_id": form_id, **result}
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

    async def commands_list(self) -> list[dict[str, Any]]:
        """``commands.list`` → every command a frontend may dispatch, with its argument names.

        The dispatch table is the source: built-ins are this class's methods (``name.replace(".", "_")``) and
        plugin commands are the registered custom ones, so a frontend can check its own bindings against
        reality instead of against a list kept in a JS file. ``alias_of`` marks underscore spellings and
        documented command renames kept for external callers; ``exposed`` is false for them, so a UI does not
        offer a legacy alias as a separate action.
        """
        out: list[dict[str, Any]] = []
        names = set(BUILTIN_COMMANDS) | set(self._custom_commands)
        for name in sorted(names):
            registration = self._custom_commands.get(name)
            if isinstance(registration, RegisteredPluginCommand):
                handler = registration.handler
                schema = registration.args_schema
                owner = registration.plugin
                exposed = registration.exposed
                if schema is not None:
                    args = list((schema.get("properties") or {}).keys())
                else:
                    args = [
                        param.name
                        for param in inspect.signature(handler).parameters.values()
                        if param.kind in (param.POSITIONAL_OR_KEYWORD, param.KEYWORD_ONLY)
                        and param.name != "_context"
                    ]
            else:
                handler = registration or getattr(self, name.replace(".", "_").replace("-", "_"), None)
                owner = None
                exposed = True
                args = [
                    param.name
                    for param in inspect.signature(handler).parameters.values()
                    if param.kind in (param.POSITIONAL_OR_KEYWORD, param.KEYWORD_ONLY)
                ] if handler is not None else []

            alias_of = COMMAND_ALIASES.get(name)
            if alias_of is None and registration is None and "_" in name:
                dotted = name.replace("_", ".", 1)
                if dotted in BUILTIN_COMMANDS:
                    alias_of = dotted
            row = {"name": name, "args": args, "alias_of": alias_of, "exposed": exposed and alias_of is None}
            if owner is not None:
                row["plugin"] = owner
            out.append(row)
        return out

    async def ui_contributions(self) -> dict[str, Any]:
        """``ui.contributions`` → what the client mounts: panels, record renderers, client modules, settings tabs.

        One request at boot replaces three lists the client used to keep by hand. A panel is a declaration plus
        a dispatchable command, so a feature's pane disappears with the feature: disabling it unloads the
        manifest, and an unloaded manifest contributes nothing (WS-05).
        """
        contrib = self.registry.ui_contributions()
        schema = await self.settings_schema()
        rows = await self.commands_list()
        dispatchable = {row["name"] for row in rows if row["exposed"]}
        accepts = {row["name"]: list(row.get("args") or []) for row in rows}
        # A panel whose command does not exist is a tab that shows an error, so it is not handed out at all —
        # and it is reported in the same response, which is what lets the contract test fail on the cause
        # rather than on a panel that silently never renders.
        panels, broken = [], []
        for panel in contrib["panels"]:
            # `accepts` is the argument names the panel's command takes, so a client that has to choose what to
            # send (the generic renderer refreshes with the active session) reads the signature instead of
            # guessing it: `dispatch` forwards args as keyword arguments, so one unexpected `session_id` is a
            # TypeError and an empty pane. The three built-in panels all happened to accept it, which is the
            # only reason no one met this before a plugin declared a fourth over `plugins.list` (R61).
            (panels if panel["command"] in dispatchable else broken).append(
                {**panel, "accepts": accepts.get(panel["command"], [])}
            )
        return {
            **contrib,
            "panels": panels,
            "unbacked_panels": broken,
            "settings_groups": schema["groups"],
            "record_kinds": sorted(RECORD_KINDS),
        }

    async def dispatch(self, command: str, args: dict[str, Any] | None = None) -> Any:
        """Generic command dispatcher for JSON-based frontend interfaces (``POST /commands/<name>``)."""
        params = args or {}
        if not isinstance(params, dict):
            raise HarnessError("Command arguments must be a JSON object")
        if command in BUILTIN_COMMANDS:
            handler = getattr(self, command.replace(".", "_"))
            return await handler(**params)

        registration = self._custom_commands.get(command)
        if registration is None:
            raise HarnessError(f"Unknown command: '{command}'")
        if isinstance(registration, RegisteredPluginCommand):
            if not registration.exposed:
                raise HarnessError(f"Command '{command}' is not exposed to frontends")
            handler = registration.handler
            if registration.args_schema is not None:
                try:
                    validate_schema_value(params, registration.args_schema, path=command)
                except SettingsValidationError as err:
                    raise HarnessError(f"Invalid arguments for command '{command}': {err}") from err
            if "_context" in params:
                raise HarnessError("'_context' is reserved for the kernel plugin-command runtime")
            call_params = dict(params)
            if "_context" in inspect.signature(handler).parameters:
                call_params["_context"] = self._plugin_command_context
        else:
            handler = registration
            call_params = params
        result = handler(**call_params)
        if inspect.isawaitable(result):
            return await result
        return result
