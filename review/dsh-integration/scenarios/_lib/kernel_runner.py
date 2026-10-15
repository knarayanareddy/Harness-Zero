"""Runs a scenario through the real kernel (``bootstrap_kernel`` + ``Commands``).

Information flow exercised:  prompt → Commands.submit_turn → TurnRunner →
ContextAssembler → ReplayProvider (what the model saw is captured) → PolicyGate
→ tool handlers → transcript store on disk → EventBus → (fresh process) reload.
"""
from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from harness.kernel.bootstrap import bootstrap_kernel

from .normalize import Normalizer, collapse_deltas
from .replay_provider import MODEL, PROVIDER_NAME, ReplayProvider, golden_view, request_summary
from .scenario import Scenario


@dataclass
class RunResult:
    session_id: str
    workspace: Path
    turn_results: list[dict[str, Any]]
    events: list[dict[str, Any]]
    transcript_live: list[dict[str, Any]]
    transcript_reloaded: list[dict[str, Any]]
    provider_requests: list[dict[str, Any]]
    approvals_seen: list[dict[str, Any]]
    errors: list[str] = field(default_factory=list)
    tools_called: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)
    dom: dict[str, Any] | None = None  # kernel lane has no DOM

    def normalized(self) -> dict[str, Any]:
        n = Normalizer(workspace=self.workspace, session_id=self.session_id)
        return {
            "transcript": n.transcript(self.transcript_reloaded),
            "events": n.events(collapse_deltas(self.events)),
            "requests": n.value(golden_view(self.provider_requests)),
        }


def seed_workspace(ws: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        p = ws / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")


def base_overrides(sc: Scenario) -> dict[str, Any]:
    ov: dict[str, Any] = {
        "kernel.provider": PROVIDER_NAME,
        "kernel.model": MODEL.id,
    }
    ov.update(sc.settings)
    if sc.mcp_servers:
        ov["plugins.bridge-mcp-client.servers"] = _substitute(sc.mcp_servers)
    return ov


def _substitute(obj: Any) -> Any:
    """Expands ``${PYTHON}`` and ``${REPO}`` in scenario config values."""
    import sys

    repo = Path(__file__).resolve().parents[4]
    table = {"${PYTHON}": sys.executable, "${REPO}": str(repo)}
    if isinstance(obj, dict):
        return {k: _substitute(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_substitute(v) for v in obj]
    if isinstance(obj, str):
        for k, v in table.items():
            obj = obj.replace(k, v)
    return obj


class EventCollector:
    """Subscribes to the bus and records every event as a dict.

    ``interactive`` decides whether the kernel believes a human is present
    (the approval broker denies immediately when no interactive subscriber exists).
    """

    def __init__(self, bus: Any, *, interactive: bool) -> None:
        self.bus = bus
        self.sub = bus.subscribe(interactive=interactive)
        self.events: list[dict[str, Any]] = []
        self.raw: list[Any] = []
        self._task = asyncio.create_task(self._pump())

    async def _pump(self) -> None:
        while True:
            evt = await self.sub.get()
            self.raw.append(evt)
            self.events.append(evt.to_dict())

    async def close(self) -> None:
        await asyncio.sleep(0.05)  # let trailing events land
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self.bus.unsubscribe(self.sub)


class ScriptedApprover:
    """Answers ``approval.required`` events in order from ``decisions``."""

    def __init__(self, bus: Any, commands: Any, decisions: list[str]) -> None:
        self.commands = commands
        self.decisions = list(decisions)
        self.seen: list[dict[str, Any]] = []
        self.sub = bus.subscribe(types=["approval.required"], interactive=True)
        self.bus = bus
        self._task = asyncio.create_task(self._loop())

    async def _loop(self) -> None:
        while True:
            evt = await self.sub.get()
            self.seen.append(evt.to_dict())
            decision = self.decisions.pop(0) if self.decisions else "deny"
            await asyncio.sleep(0.01)
            await self.commands.resolve_approval(
                approval_id=evt.data.get("approval_id"),
                approved=(decision == "approve"),
                reason=f"scenario:{decision}",
            )

    async def close(self) -> None:
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self.bus.unsubscribe(self.sub)


def _jsonable(obj: Any) -> Any:
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, Path):
        return str(obj)
    return obj


async def run_kernel_scenario(sc: Scenario, tmp: Path, *, env_conf: Path) -> RunResult:
    ws = tmp / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    seed_workspace(ws, sc.workspace_files)

    rt = await bootstrap_kernel(workspace=ws, config_dir=env_conf, overrides=base_overrides(sc))
    if sc.secrets:
        rt.secrets.set_many(sc.secrets)  # the gate redacts stored secret values from tool output
    provider = ReplayProvider.from_file(sc.provider_file, strict=sc.strict_exhaustion)
    rt.registry.register_provider(provider, plugin_name="scenario")

    wants_approver = bool(sc.approvals)
    collector = EventCollector(rt.bus, interactive=wants_approver)
    approver = ScriptedApprover(rt.bus, rt.commands, sc.approvals) if wants_approver else None

    errors: list[str] = []
    turn_results: list[dict[str, Any]] = []
    session = await rt.session_manager.create_session()
    sid = session.session_id
    try:
        for turn in sc.turns:
            options = {"provider": PROVIDER_NAME, "model": turn.model or MODEL.id}
            try:
                res = await asyncio.wait_for(
                    rt.commands.submit_turn(session_id=sid, prompt=turn.prompt, options=options),
                    timeout=float(os.environ.get("SCENARIO_TURN_TIMEOUT", "60")),
                )
                turn_results.append(_jsonable(res))
            except Exception as e:  # the error itself is part of the observed flow
                errors.append(f"{type(e).__name__}: {e}")
                turn_results.append({"error": f"{type(e).__name__}: {e}"})
        live_records = await session.get_history()
    finally:
        if approver:
            await approver.close()
        await collector.close()
        await rt.registry.unload_all()

    # Fresh process: what a restarted daemon would see for this session.
    rt2 = await bootstrap_kernel(workspace=ws, config_dir=env_conf, overrides=base_overrides(sc))
    try:
        reloaded = await rt2.commands.sessions_get(session_id=sid)
        reloaded_records = reloaded.get("records", [])
    finally:
        await rt2.registry.unload_all()

    tools_called = [e["data"].get("name") or e["data"].get("tool") for e in collector.events if e["type"] == "tool.call"]
    return RunResult(
        session_id=sid,
        workspace=ws,
        turn_results=turn_results,
        events=collector.events,
        transcript_live=live_records,
        transcript_reloaded=reloaded_records,
        provider_requests=[request_summary(r) for r in provider.requests],
        approvals_seen=approver.seen if approver else [],
        errors=errors,
        tools_called=[t for t in tools_called if t],
        extra={"replay_remaining": provider.remaining, "live_vs_reloaded_equal": json.dumps(live_records, sort_keys=True) == json.dumps(reloaded_records, sort_keys=True)},
    )
