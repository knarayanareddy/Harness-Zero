"""
Static contract test suite for the modular web frontend (Step 3).

Verifies:
1. All DOM element IDs referenced by app.js exist in static/index.html.
2. Zero occurrences of alert() across static/*.js and static/*.html.
3. Zero occurrences of /turn/stream across static/*.js and static/*.html.
4. Zero inline event handlers (onclick, onsubmit, onchange) in static/*.html and static/*.js.
5. Zero inline <script> tags without src in static/index.html.
6. The web server emits strict CSP with `script-src 'self'` without `'unsafe-inline'` or `'unsafe-eval'`.
"""

import re
from pathlib import Path

import httpx
import pytest

from harness.frontends.web.server import HarnessWebServer
from harness.kernel.approvals import ApprovalBroker
from harness.kernel.bus import EventBus
from harness.kernel.commands import Commands
from harness.kernel.gate import PolicyGate
from harness.kernel.jail import WorkspaceJail
from harness.kernel.registry import PluginRegistry
from harness.kernel.runner import TurnRunner
from harness.kernel.secrets import Secrets
from harness.kernel.session import SessionManager
from harness.kernel.settings import SettingsStore

STATIC_DIR = Path(__file__).parent.parent.parent / "harness" / "frontends" / "web" / "static"


@pytest.fixture
def workspace_dir(tmp_path: Path) -> Path:
    ws = tmp_path / "test_workspace"
    ws.mkdir(parents=True, exist_ok=True)
    return ws


@pytest.fixture
def settings(tmp_path: Path) -> SettingsStore:
    return SettingsStore(tmp_path / "settings.json")


@pytest.fixture
def secrets(tmp_path: Path) -> Secrets:
    return Secrets(tmp_path / "secrets.json")


@pytest.fixture
def event_bus() -> EventBus:
    return EventBus()


@pytest.fixture
def commands(
    workspace_dir: Path,
    settings: SettingsStore,
    secrets: Secrets,
    event_bus: EventBus,
) -> Commands:
    reg = PluginRegistry(workspace=workspace_dir, settings=settings, secrets=secrets, bus=event_bus)
    jail = WorkspaceJail(workspace_dir)
    approvals = ApprovalBroker(event_bus)
    gate = PolicyGate(
        registry=reg,
        jail=jail,
        settings=settings,
        secrets=secrets,
        approvals=approvals,
        bus=event_bus,
    )
    runner = TurnRunner(registry=reg, gate=gate, settings=settings, bus=event_bus)
    sm = SessionManager(workspace=workspace_dir, registry=reg, settings=settings, bus=event_bus)
    return Commands(
        workspace=workspace_dir,
        session_manager=sm,
        runner=runner,
        gate=gate,
        settings=settings,
        secrets=secrets,
        registry=reg,
        bus=event_bus,
    )


def _client_js() -> dict[str, str]:
    """Every client module the page loads or imports — including the feature-owned ones in subdirectories.

    WS-05b moved painters and panels into `static/renderers/` and `static/panels/`, so a check that read only
    `app.js` would quietly stop covering the code it was written about. `vendor/` is third-party.
    """
    files = sorted(p for p in STATIC_DIR.rglob("*.js") if "vendor" not in p.relative_to(STATIC_DIR).parts)
    return {str(p.relative_to(STATIC_DIR)): p.read_text(encoding="utf-8") for p in files}


def test_element_ids_contract():
    """All DOM element IDs queried by any client module must exist in index.html."""
    sources = _client_js()
    index_html_path = STATIC_DIR / "index.html"
    assert index_html_path.exists(), f"Missing {index_html_path}"
    assert "app.js" in sources, "no client modules found — this test would pass on an empty set"

    index_content = index_html_path.read_text(encoding="utf-8")
    id_matches = [(name, m) for name, src in sources.items() for m in re.findall(r"getElementById\(['\"]([^'\"]+)['\"]\)", src)]
    assert len(id_matches) > 0, "No getElementById found in the client"

    missing_ids = []
    for name, elem_id in id_matches:
        # Look for id="elem_id" or id='elem_id'
        if f'id="{elem_id}"' not in index_content and f"id='{elem_id}'" not in index_content:
            missing_ids.append(f"{name}: {elem_id}")

    assert not missing_ids, f"DOM element IDs referenced by the client but missing from index.html: {missing_ids}"


def test_no_alert_calls_in_frontend():
    """Frontend files must have zero alert() calls (use dynamic UI notices instead)."""
    frontend_files = list(STATIC_DIR.rglob("*.js")) + list(STATIC_DIR.rglob("*.html"))
    frontend_files = [p for p in frontend_files if "vendor" not in p.relative_to(STATIC_DIR).parts]
    assert len(frontend_files) > 0

    violations = []
    for f in frontend_files:
        content = f.read_text(encoding="utf-8")
        if "alert(" in content:
            violations.append(f.name)

    assert not violations, f"Files containing alert() calls: {violations}"


def test_no_turn_stream_in_static_frontend():
    """The modular frontend must use /commands and /events, not the legacy /turn/stream."""
    frontend_files = list(STATIC_DIR.glob("*.js")) + list(STATIC_DIR.glob("*.html"))
    violations = []
    for f in frontend_files:
        content = f.read_text(encoding="utf-8")
        if "/turn/stream" in content:
            violations.append(f.name)

    assert not violations, f"Files referencing /turn/stream: {violations}"


def test_no_inline_scripts_or_handlers():
    """index.html must not contain inline scripts or inline event handlers."""
    index_path = STATIC_DIR / "index.html"
    content = index_path.read_text(encoding="utf-8")

    # Check for inline handlers
    handler_pattern = re.compile(r"\s(onclick|onsubmit|onchange|onkeydown|onkeyup)\s*=", re.IGNORECASE)
    assert not handler_pattern.search(content), "Found inline event handlers in index.html"

    # Check that any <script> tags have a src attribute
    script_pattern = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.IGNORECASE | re.DOTALL)
    inline_scripts = script_pattern.findall(content)
    # Filter empty tags if any
    inline_scripts = [s.strip() for s in inline_scripts if s.strip()]
    assert not inline_scripts, f"Found inline script blocks in index.html: {inline_scripts}"


@pytest.mark.asyncio
async def test_strict_csp_header(commands: Commands, event_bus: EventBus, workspace_dir: Path):
    """Web server must emit strict CSP without unsafe-inline or unsafe-eval in script-src."""
    server = HarnessWebServer(
        commands=commands,
        bus=event_bus,
        host="127.0.0.1",
        port=0,
        sandbox_port=0,
        workspace=workspace_dir,
    )
    await server.start()
    base_url = f"http://127.0.0.1:{server.port}"

    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(f"{base_url}/")
            assert res.status_code == 200
            csp = res.headers.get("Content-Security-Policy", "")

            assert "script-src 'self'" in csp, f"Expected script-src 'self' in CSP: {csp}"
            # Ensure script-src does not have unsafe-inline or unsafe-eval
            script_src_part = [part for part in csp.split(";") if "script-src" in part]
            assert script_src_part, "Missing script-src directive in CSP"
            assert "'unsafe-inline'" not in script_src_part[0], f"unsafe-inline in script-src: {script_src_part[0]}"
            assert "'unsafe-eval'" not in script_src_part[0], f"unsafe-eval in script-src: {script_src_part[0]}"
    finally:
        await server.stop()


def test_no_mockup_facade_or_dead_elements():
    """index.html must not contain fake notices, no-ops, hardcoded user placeholders, or malformed tags."""
    index_path = STATIC_DIR / "index.html"
    content = index_path.read_text(encoding="utf-8")

    # 1. Zero data-notice fake confirmation attributes
    assert "data-notice" not in content, "Found fake confirmation 'data-notice' attributes in index.html"

    # 2. Zero data-onclick="noop" attributes
    assert 'data-onclick="noop"' not in content, "Found 'data-onclick=\"noop\"' in index.html"

    # 3. No hard-coded user/email/UUID placeholders
    forbidden_placeholders = [
        "kiranreddy",
        "user@local.device",
        "00000000-0000-0000-0000-000000000000",
        "Replicated directly from",
    ]
    for placeholder in forbidden_placeholders:
        assert placeholder not in content, f"Found hard-coded placeholder '{placeholder}' in index.html"

    # 4. Exactly one <head> and <body> structure
    assert content.count("<head>") == 1, "index.html must have exactly one <head> tag"
    assert content.count("</head>") == 1, "index.html must have exactly one </head> tag"
    assert content.count("<body>") == 1, "index.html must have exactly one <body> tag"
    assert content.count("</body>") == 1, "index.html must have exactly one </body> tag"


def test_events_subscription_contract():
    """Verify that every schema event type is subscribed on EventSource and app.js handlers are valid."""
    import json

    schema_path = Path(__file__).parent.parent.parent / "docs" / "events.schema.json"
    assert schema_path.exists(), f"Missing {schema_path}"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    schema_types = set(schema["properties"]["type"]["enum"])

    events_js_path = STATIC_DIR / "events.js"
    events_js_content = events_js_path.read_text(encoding="utf-8")
    app_js_path = STATIC_DIR / "app.js"
    app_js_content = app_js_path.read_text(encoding="utf-8")

    # 1. events.js must subscribe via addEventListener for named SSE frames
    assert "addEventListener" in events_js_content, "events.js must use addEventListener for typed events"

    # 2. Every schema event type must be in KNOWN_EVENT_TYPES in events.js
    for t in schema_types:
        assert f"'{t}'" in events_js_content or f'"{t}"' in events_js_content, (
            f"Schema event type '{t}' missing from events.js subscription list"
        )

    # 3. Every events.on('<type>') in app.js must exist in schema (excluding synthetic '$*' events)
    app_event_matches = re.findall(r"this\.events\.on\(['\"]([^'\"]+)['\"]", app_js_content)
    for app_type in set(app_event_matches):
        if app_type.startswith("$"):
            continue
        assert app_type in schema_types, f"app.js listens to unknown event type '{app_type}' not in schema"


def test_the_client_only_calls_methods_it_defines():
    """Every `app.x()` a module calls, and every `this.x()` in the app, must exist (WS-05b).

    Lifting bodies out of `app.js` turns direct calls into indirect ones: a row in the sessions pane calls
    `ctx.select(...)`, which reaches `app.handlePanelSelect`, which calls `this.switchSession(...)`. A name in
    that chain that does not exist is a `TypeError` when a user clicks — invisible to every static check, and
    to any test that never clicks. The DOM lane found exactly that here (`this.selectSession`, a method this
    client never had), so the chain is now pinned without a browser.
    """
    sources = _client_js()
    app = sources["app.js"]
    defined = set(re.findall(r"^  (?:async |get |set )?([A-Za-z_$][\w$]*)\(", app, re.M))
    defined |= set(re.findall(r"this\.([A-Za-z_$][\w$]*) = ", app))  # collaborators assigned in the constructor
    offenders = []
    for name, src in sources.items():
        if name == "app.js":
            continue
        for called in sorted(set(re.findall(r"\bapp\.([A-Za-z_$][\w$]*)\s*(?:\?\.)?\(", src))):
            if called not in defined:
                offenders.append(f"{name}: app.{called}()")
        for called in sorted(set(re.findall(r"\bctx\.app\.([A-Za-z_$][\w$]*)\s*(?:\?\.)?\(", src))):
            if called not in defined:
                offenders.append(f"{name}: ctx.app.{called}()")
    for called in sorted(set(re.findall(r"\bthis\.([A-Za-z_$][\w$]*)\s*\(", app))):
        if called not in defined:
            offenders.append(f"app.js: this.{called}()")
    assert not offenders, f"the client calls methods it does not define: {sorted(offenders)}"


def _js_function_body(source: str, name: str) -> str:
    """Returns the text of the function or method ``name``, without its header.

    Three shapes have to work, because WS-05b moved bodies out of ``app.js``: a class method (``  renderRecord(rec)
    {``), a module-level function (``export default function render(ctx) {``) and an object member (``  async
    render(ctx) {`` inside ``export default { ... }``). The body ends at the closing brace on the definition's own
    indent, which is why the search is anchored to that indent rather than to a fixed two spaces — and it is why
    a body that moved to a module still has teeth in these assertions instead of passing on an empty string.
    """
    m = re.search(
        rf"^([ \t]*)(?:export\s+)?(?:default\s+)?(?:async\s+)?(?:function\s+)?{re.escape(name)}\s*\([^)]*\)\s*\{{",
        source,
        re.M,
    )
    assert m, f"{name} not found"
    rest = source[m.end():]
    # The block ends at the brace that sits at the definition's own indent: nested blocks are deeper, so this is
    # the method's real end for all three shapes (class method, module function, object member).
    close = re.search(rf"^{m.group(1)}\}}", rest, re.M)
    return rest[: close.start()] if close else rest


def _js_method_params(source: str, name: str) -> list[str]:
    m = re.search(rf"^  (?:async )?{re.escape(name)}\(([^)]*)\)\s*\{{", source, re.M)
    assert m, f"{name} not found"
    return [p.strip().split("=")[0].strip() for p in m.group(1).split(",") if p.strip()]


def _member_refs(body: str, var: str) -> set[str]:
    """Property names read off ``var`` in a JS body (``var.x``, ``var?.x``, ``var.x.y`` -> x)."""
    return set(re.findall(rf"\b{re.escape(var)}\??\.([A-Za-z_]\w*)", body))


def _event_handlers(app_js: str) -> dict[str, str]:
    """Maps each ``this.events.on('<type>', (<param>) => {...})`` to the text of its handler body."""
    handlers: dict[str, str] = {}
    for m in re.finditer(r"this\.events\.on\(['\"]([^'\"$]+)['\"],\s*(?:async\s*)?\(([^)]*)\)\s*=>\s*\{", app_js):
        etype, param = m.group(1), m.group(2).strip()
        depth, i = 1, m.end()
        while depth and i < len(app_js):
            depth += {"{": 1, "}": -1}.get(app_js[i], 0)
            i += 1
        body = app_js[m.end() : i - 1]
        if param and param != "data":
            body = re.sub(rf"\b{re.escape(param)}\b", "data", body)
        handlers[etype] = body
    return handlers


def _payload_properties(schema: dict, etype: str) -> set[str]:
    return set(schema["$defs"]["payloads"][etype]["properties"])


def test_event_handlers_read_only_contract_fields():
    """Every key app.js reads from an event payload must be declared by that event's payload dataclass.

    This is the regression guard for review finding R-4.1 (no payload contract): handlers such as
    `turn.error` -> `data.message`, `tool.decision` -> `data.action`, `cost.updated` -> `data.session_usd`
    are checked against `docs/events.schema.json` ($defs/payloads), which `harness events schema`
    generates from `harness/sdk/events.py`. Delegated helpers (`this.foo(data)`) are followed one level.
    """
    import json

    schema = json.loads((Path(__file__).parent.parent.parent / "docs" / "events.schema.json").read_text(encoding="utf-8"))
    app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    handlers = _event_handlers(app_js)
    assert handlers, "no event handlers found in app.js"

    problems: list[str] = []
    for etype, body in handlers.items():
        allowed = _payload_properties(schema, etype)
        refs = _member_refs(body, "data")
        # Follow one level of delegation: this.helper(data) / this.helper(data.x) -> helper's first param.
        for helper in set(re.findall(r"this\.([A-Za-z_]\w*)\(data\)", body)):
            params = _js_method_params(app_js, helper)
            if params:
                refs |= _member_refs(_js_function_body(app_js, helper), params[0])
        unknown = sorted(refs - allowed)
        if unknown:
            problems.append(f"{etype}: app.js reads data.{unknown} but the payload declares {sorted(allowed)}")
    assert not problems, "\n".join(problems)

    # Nested: context.assembled blocks are ContextBlockInfo.
    inspector = _js_function_body(app_js, "updateContextInspector")
    block_props = set(schema["$defs"]["ContextBlockInfo"]["properties"])
    assert _member_refs(inspector, "b") <= block_props, f"updateContextInspector reads b.{sorted(_member_refs(inspector, 'b') - block_props)}"


def test_card_renderers_read_only_contract_fields():
    """cards.js renders exactly the SDK card contract (harness/sdk/cards.py CARD_FIELDS) — no alias chains.

    Regression guard for review finding R8: `Card.to_dict()` is `{kind, id, title, data}` while the old
    renderer read `card.payload` with different keys, so every plugin card rendered empty or as fallback.
    """
    import json

    from harness.sdk.cards import CARD_FIELDS
    from harness.sdk.events import EVENT_PAYLOADS

    cards_js = (STATIC_DIR / "cards.js").read_text(encoding="utf-8")
    assert "card.payload" not in cards_js and ".payload" not in cards_js, "cards.js must read card.data, not card.payload"

    def fields_of(etype: str) -> set[str]:
        from dataclasses import fields

        return {f.name for f in fields(EVENT_PAYLOADS[etype])}

    expected: dict[str, set[str]] = {kind: set(keys) for kind, keys in CARD_FIELDS.items()}
    # Web-internal kinds carry kernel event payloads (+ a client-side status).
    expected["tool"] = fields_of("tool.call") | {"status"}
    expected["task"] = fields_of("task.started") | fields_of("task.completed") | {"status"}
    expected["compaction"] = fields_of("compaction.completed")

    problems: list[str] = []
    for kind, allowed in expected.items():
        method = "render" + kind[0].upper() + kind[1:]
        body = _js_function_body(cards_js, method)
        params = _js_method_params(cards_js, method)
        assert params[:2] == ["card", "data"], f"{method} must be declared as {method}(card, data)"
        refs = _member_refs(body, "data")
        unknown = sorted(refs - allowed)
        if unknown:
            problems.append(f"{method} reads data.{unknown}; contract allows {sorted(allowed)}")
        card_refs = _member_refs(body, "card")
        assert card_refs <= {"id", "title", "kind"}, f"{method} reads card.{sorted(card_refs)}"
    assert not problems, "\n".join(problems)

    # Every SDK kind has a renderer branch; the dead `plan` kind is gone.
    for kind in CARD_FIELDS:
        assert f"case '{kind}':" in cards_js, f"cards.js has no branch for SDK card kind {kind!r}"
    assert "renderPlan" not in cards_js

    # The JS mirror of KNOWN_EVENT_TYPES matches the SDK (plus the server heartbeat frame).
    events_js = (STATIC_DIR / "events.js").read_text(encoding="utf-8")
    js_types = set(re.findall(r"^\s+'([a-z._]+)',$", events_js, re.M))
    assert js_types == set(EVENT_PAYLOADS) | {"heartbeat"}, f"events.js KNOWN_EVENT_TYPES drift: {sorted(js_types ^ (set(EVENT_PAYLOADS) | {'heartbeat'}))}"
    assert json.loads((Path(__file__).parent.parent.parent / "docs" / "events.schema.json").read_text(encoding="utf-8"))["properties"]["type"]["enum"] == sorted(EVENT_PAYLOADS)


def test_transcript_reload_reads_transcript_record_fields():
    """reloadTranscript() must read the TranscriptRecord shape the kernel writes (runner.record / sessions.get)."""
    from dataclasses import fields

    from harness.sdk.types import TranscriptRecord

    app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    body = _js_function_body(app_js, "reloadTranscript")
    rec_fields = {f.name for f in fields(TranscriptRecord)}
    assert _member_refs(body, "rec") <= rec_fields, f"reloadTranscript reads rec.{sorted(_member_refs(body, 'rec') - rec_fields)}"
    assert _member_refs(body, "tc") <= {"id", "name", "arguments"}  # KernelServicesImpl.record tool_calls entries
    assert _member_refs(body, "tr") <= {"tool_use_id", "content", "is_error"}  # ... tool_results entries
    assert "rec.type" not in body and "PLANNER_RESPONSE" not in body, "legacy record discriminators must not come back"


async def test_economics_payload_keys_match_app_js(commands: Commands):
    """The Economics tab and the header indicator read the EconomicsSnapshot the kernel actually returns.

    Regression: 158c836 rendered `data.session_usd`, `data.cap_usd`, `data.token_usage.*` and
    `data.totals.total_cost_usd` while the command returned `{spend_cap_usd, totals}`, so the tab
    always showed $0.0000 / $5.00 and 0 tokens. Since P1 the command, `cost.updated` and
    `/health.spend` all carry `EconomicsSnapshot.to_dict()`.
    """
    from dataclasses import fields

    from harness.sdk.events import CostUpdated
    from harness.sdk.types import EconomicsSnapshot, Usage

    payload = await commands.economics()
    snapshot_keys = {f.name for f in fields(EconomicsSnapshot)}
    assert set(payload) == snapshot_keys
    assert {f.name for f in fields(CostUpdated)} == snapshot_keys, "cost.updated must carry the EconomicsSnapshot shape"
    usage_keys = set(Usage().to_dict())

    app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    # WS-05b: the Spend pane's markup lives in `static/panels/economics.js`; `app.js` keeps only the delegation.
    panel_js = (STATIC_DIR / "panels" / "economics.js").read_text(encoding="utf-8")
    tab = _js_function_body(panel_js, "render")
    assert "refresh('economics')" in _js_function_body(app_js, "renderEconomicsTab"), (
        "renderEconomicsTab must stay a delegation to the economics panel, not a second copy of the markup"
    )
    assert "economics(ctx.app.activeSessionId)" in tab, "the panel must read the snapshot from `economics` for the active session, not a second source"
    assert _member_refs(tab, "data") <= snapshot_keys, f"renderEconomicsTab reads {sorted(_member_refs(tab, 'data') - snapshot_keys)}"
    assert _member_refs(tab, "usage") <= usage_keys, f"renderEconomicsTab reads usage.{sorted(_member_refs(tab, 'usage') - usage_keys)}"
    assert "token_usage" not in tab and "total_cost_usd" not in tab and "totals" not in tab

    indicator = _js_function_body(app_js, "updateEconomics")
    assert _member_refs(indicator, "snapshot") <= snapshot_keys, f"updateEconomics reads {sorted(_member_refs(indicator, 'snapshot') - snapshot_keys)}"
    assert "spend_cap_usd" not in indicator and "totals" not in indicator


def test_session_picker_reads_session_row_fields_and_boot_creates_no_session():
    """P2: sessions are kernel-owned. `loadSessions()` renders `SessionRow` keys only, and the client never
    creates a session at boot or on submit — `submit_turn` without a session id makes the kernel create one,
    which it announces via `session.started` (regression for review §4.4: three restarts → three empty sessions).
    """
    from dataclasses import fields

    from harness.sdk.types import SessionRow

    app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    # WS-05b: the rows are painted by `static/panels/sessions.js`, so that is where the row shape is asserted;
    # `loadSessions` must stay a delegation to that panel rather than growing a second copy of the markup.
    panel_js = (STATIC_DIR / "panels" / "sessions.js").read_text(encoding="utf-8")
    picker = _js_function_body(panel_js, "render")
    row_keys = {f.name for f in fields(SessionRow)}
    assert _member_refs(picker, "s") <= row_keys, f"the sessions panel reads s.{sorted(_member_refs(picker, 's') - row_keys)}"
    assert "s.title" in picker and "s.turns" in picker, "the picker must show a human-readable title and turn count"
    assert "getSessions" in picker, "the panel must read the kernel's session rows, not a cached copy"
    assert "noteSessionRows" in picker, "the app has to learn the rows: the active id and the header come from them"
    assert _member_refs(_js_function_body(app_js, "noteSessionRows"), "row") <= row_keys | {"session_id"}

    init = _js_function_body(app_js, "init")
    assert "createNewSession" not in init, "init() must not create a session; the kernel resumes or creates lazily"
    assert init.index("this.events.connect()") < init.index("this.reloadTranscript()"), "SSE must be connected before the transcript is rendered"

    handlers = _event_handlers(app_js)
    assert "session.started" in handlers and "activeSessionId" in handlers["session.started"], "the client must adopt the session the kernel created for its first prompt"

    controls = _js_function_body(app_js, "bindControls")
    assert "createNewSession" not in controls.split("btn-new-session")[0], "submit must not create a session client-side"
    assert "session_id: this.activeSessionId || undefined" in controls


def test_model_picker_reads_model_row_fields_and_groups_by_provider_status():
    """P4: provider truth lives in the provider. `populateModels()` renders `ModelRow` keys only and groups by
    the kernel-derived `status` (ready / needs_key / unreachable) instead of guessing from `configured`."""
    from dataclasses import fields

    from harness.sdk.types import ModelRow

    app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    picker = _js_function_body(app_js, "populateModels")
    row_keys = {f.name for f in fields(ModelRow)} | {"status"}
    assert _member_refs(picker, "m") <= row_keys, f"populateModels reads m.{sorted(_member_refs(picker, 'm') - row_keys)}"
    for status in ("ready", "needs_key", "unreachable"):
        assert status in picker, f"picker must have a group for status {status!r}"
    assert "Unconfigured (Requires API Key)" not in app_js, "the old unreachable optgroup is gone"
    assert "m.configured !== false" not in picker, "status is derived by the kernel, not re-derived client-side"

