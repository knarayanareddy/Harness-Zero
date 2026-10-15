"""Runs a scenario through the real web daemon (``HarnessWebServer``).

Two lanes, both against the same in-process server:

* **API lane** — a Python client that sends exactly the payloads the shipped
  browser client sends (``POST /commands/submit_turn`` with
  ``{"prompt", "session_id", "model"}``), reads ``/events`` as a browser would
  (named SSE frames), and reloads the session via ``sessions.get``.
* **DOM lane** (optional, needs node + jsdom) — the shipped ``index.html`` and
  ES modules run in jsdom against the server; the composer is driven and the
  timeline text is captured (``headless_timeline.mjs``).
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from harness.frontends.web.server import HarnessWebServer
from harness.kernel.bootstrap import bootstrap_kernel

from .kernel_runner import ScriptedApprover, base_overrides, seed_workspace
from .normalize import Normalizer, collapse_deltas
from .replay_provider import MODEL, ReplayProvider, request_summary
from .scenario import Scenario
from .sse import SSEReader

HERE = Path(__file__).parent
NODE_DIR = HERE / "node"
STATIC_DIR = Path(__import__("harness.frontends.web.server", fromlist=["x"]).__file__).parent / "static"


@dataclass
class WebRunResult:
    session_id: str | None
    workspace: Path
    http: list[dict[str, Any]]  # request/response log of the API lane
    events: list[dict[str, Any]]  # SSE envelopes as a browser would see them
    frames: list[dict[str, Any]]
    transcript_reloaded: list[dict[str, Any]]
    provider_requests: list[dict[str, Any]]
    dom: dict[str, Any] | None  # headless report, if the DOM lane ran
    errors: list[str] = field(default_factory=list)
    tools_called: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def normalized(self) -> dict[str, Any]:
        n = Normalizer(workspace=self.workspace, session_id=self.session_id)
        out = {
            "transcript": n.transcript(self.transcript_reloaded),
            "events": n.events(collapse_deltas(self.events)),
            "http": n.value(self.http),
            "requests": n.value(self.provider_requests),
        }
        if self.dom is not None:
            out["timeline"] = n.text(self.dom.get("timeline_text", "") or "")
        return out


def dom_lane_available() -> tuple[bool, str]:
    if os.environ.get("SCENARIO_DOM", "1") == "0":
        return False, "SCENARIO_DOM=0"
    if not shutil.which("node"):
        return False, "node not on PATH"
    if not (NODE_DIR / "node_modules" / "jsdom").exists():
        return False, f"jsdom not installed (run: npm --prefix {NODE_DIR} ci)"
    return True, ""


async def run_web_scenario(sc: Scenario, tmp: Path, *, env_conf: Path, dom: bool = True) -> WebRunResult:
    ws = tmp / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    seed_workspace(ws, sc.workspace_files)

    rt = await bootstrap_kernel(workspace=ws, config_dir=env_conf, overrides=base_overrides(sc))
    provider = ReplayProvider.from_file(sc.provider_file, strict=sc.strict_exhaustion)
    rt.registry.register_provider(provider, plugin_name="scenario")

    server = HarnessWebServer(commands=rt.commands, bus=rt.bus, host="127.0.0.1", port=0, sandbox_port=0, workspace=ws)
    await server.start()
    base = f"http://127.0.0.1:{server.port}"
    token = server.auth_token
    headers = {"Authorization": f"Bearer {token}", "X-Harness-Token": token, "Content-Type": "application/json"}

    http_log: list[dict[str, Any]] = []
    errors: list[str] = []
    sid: str | None = None
    dom_report: dict[str, Any] | None = None
    approver = ScriptedApprover(rt.bus, rt.commands, sc.approvals) if sc.approvals and not (dom and sc.expect.get("dom_approves")) else None

    async with httpx.AsyncClient(base_url=base, timeout=30) as client:

        async def call(name: str, body: dict[str, Any]) -> tuple[int, Any]:
            r = await client.post(f"/commands/{name}", headers=headers, content=json.dumps(body))
            try:
                data = r.json()
            except Exception:
                data = {"raw": r.text[:300]}
            http_log.append({"command": name, "request": body, "status": r.status_code, "response": _trim(data)})
            return r.status_code, data

        # Browser SSE connection (ticket flow exactly like events.js)
        r = await client.post("/auth/ticket", headers=headers, content="{}")
        ticket = r.json().get("ticket") if r.status_code == 200 else None
        http_log.append({"command": "auth.ticket", "request": {}, "status": r.status_code, "response": {"ticket": bool(ticket)}})
        sse = SSEReader(client, f"/events?ticket={ticket}" if ticket else "/events", headers={} if ticket else headers)
        await sse.start()
        http_log.append({"command": "events.sse", "request": {}, "status": sse.status, "response": {}})

        try:
            health = (await client.get("/health", headers=headers)).json()
            http_log.append({"command": "health", "request": {}, "status": 200, "response": {"active_session": bool(health.get("active_session"))}})

            if dom:
                ok, why = dom_lane_available()
                if ok:
                    dom_report = await _run_dom_lane(base, sc, token)
                    sid = dom_report.get("active_session_after") or dom_report.get("active_session_before_send")
                else:
                    errors.append(f"dom lane skipped: {why}")

            if sid is None:
                status, created = await call("sessions.create", {})
                sid = (created.get("result") or {}).get("session_id") if status == 200 else None
                if sid is None:
                    errors.append(f"sessions.create failed: {status} {created}")
                else:
                    terminal = lambda e: e.get("type") in ("turn.completed", "turn.error", "turn.cancelled")  # noqa: E731
                    for turn in sc.turns:
                        # Exactly what app.js sends: prompt, session_id and the selected model.
                        body = {"prompt": turn.prompt, "session_id": sid, "model": turn.model or MODEL.id}
                        seen_terminal = sse.count(terminal)
                        status, data = await call("submit_turn", body)
                        if status != 200:
                            # No turn was started, so no terminal event will ever arrive: fail fast.
                            errors.append(f"submit_turn -> {status}: {data.get('error')}")
                            break
                        finished = await sse.wait_for(terminal, timeout=20, min_count=seen_terminal + 1)
                        if not finished:
                            errors.append(f"turn {turn.prompt!r} never reached a terminal event within 20s")
                            break

            # Reload what a page refresh would render
            if sid:
                status, got = await call("sessions.get", {"session_id": sid})
                reloaded = (got.get("result") or {}).get("records", []) if status == 200 else []
            else:
                reloaded = []
            await asyncio.sleep(0.2)
        finally:
            await sse.close()
            if approver:
                await approver.close()
            await server.stop()
            await rt.registry.unload_all()

    tools_called = [e.get("data", {}).get("name") or e.get("data", {}).get("tool") for e in sse.events if e.get("type") == "tool.call"]
    return WebRunResult(
        session_id=sid,
        workspace=ws,
        http=http_log,
        events=sse.events,
        frames=sse.frames,
        transcript_reloaded=reloaded,
        provider_requests=[request_summary(r) for r in provider.requests],
        dom=dom_report,
        errors=errors,
        tools_called=[t for t in tools_called if t],
        extra={"replay_remaining": provider.remaining, "named_sse_frames": sum(1 for f in sse.frames if f["event"] != "message")},
    )


def _trim(data: Any) -> Any:
    """Keeps responses small and free of volatile fields for the golden."""
    if isinstance(data, dict):
        out = {}
        for k, v in data.items():
            if k in ("records",):
                out[k] = f"<{len(v)} records>"
            elif k in ("result",) and isinstance(v, dict):
                out[k] = {kk: (vv if kk in ("session_id", "status", "output", "stop_reason", "error") else "…") for kk, vv in v.items()}
            else:
                out[k] = v if isinstance(v, (str, int, float, bool)) or v is None else "…"
        return out
    return data


async def _run_dom_lane(base: str, sc: Scenario, token: str) -> dict[str, Any]:
    env = dict(os.environ)
    env.update(
        {
            "BASE": base,
            "STATIC": str(STATIC_DIR),
            "PROMPT": sc.turns[0].prompt if sc.turns else "",
            "PROMPTS": json.dumps([t.prompt for t in sc.turns]),
            "WAIT_MS": os.environ.get("SCENARIO_DOM_WAIT_MS", "6000"),
            "NEW_SESSION": "1" if sc.expect.get("dom_new_session", True) else "0",
            "APPROVE": "1" if sc.expect.get("dom_approves") else "0",
            "MODEL": MODEL.id,
        }
    )
    proc = await asyncio.create_subprocess_exec(
        "node",
        str(NODE_DIR / "headless_timeline.mjs"),
        cwd=str(NODE_DIR),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=90)
    except TimeoutError:
        proc.kill()
        return {"error": "headless timeline timed out"}
    text = out.decode("utf-8", "replace")
    try:
        report = json.loads(text[text.index("{") :])
    except Exception:
        report = {"error": "unparseable headless output", "stdout": text[-2000:], "stderr": err.decode("utf-8", "replace")[-2000:]}
    return report
