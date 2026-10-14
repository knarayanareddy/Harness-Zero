import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from harness.cli import main
from harness.sdk import (
    KNOWN_EVENT_TYPES,
    Card,
    Event,
    Policy,
    ToolHandler,
    events_schema,
    fallback,
    validate_card,
)


def test_runtime_checkable_contracts():
    class DummyTool:
        async def __call__(self, args: dict[str, Any], ctx: Any):
            return None

    assert isinstance(DummyTool(), ToolHandler)

    class IncompleteTool:
        pass

    assert not isinstance(IncompleteTool(), ToolHandler)

    class DummyPolicy:
        name = "test_policy"
        order = 50

        async def evaluate(self, call: Any, spec: Any, ctx: Any):
            return None

    assert isinstance(DummyPolicy(), Policy)


def test_cards_creation_and_validation():
    # 1. Markdown
    c_md = Card.markdown("# Header\nContent")
    assert c_md.kind == "markdown"
    assert c_md.data["body"] == "# Header\nContent"

    # 2. Code
    c_code = Card.code("print('hello')", language="python", path="test.py")
    assert c_code.kind == "code"
    assert c_code.data["language"] == "python"

    # 3. Diff
    c_diff = Card.diff(path="file.py", unified="@@ -1 +1 @@", added=1, removed=1)
    assert c_diff.kind == "diff"
    assert c_diff.data["stats"]["added"] == 1

    # 4. Table
    c_table = Card.table(columns=["A", "B"], rows=[[1, 2], [3, 4]])
    assert c_table.kind == "table"
    assert len(c_table.data["rows"]) == 2

    # 5. Notice
    c_notice = Card.notice(body="Alert", level="warn")
    assert c_notice.kind == "notice"
    assert c_notice.data["level"] == "warn"

    # 6. Progress
    c_prog = Card.progress(label="Downloading", value=0.75)
    assert c_prog.kind == "progress"
    assert c_prog.data["value"] == 0.75

    # 7. Tree
    c_tree = Card.tree(nodes=[{"label": "root", "children": []}])
    assert c_tree.kind == "tree"

    # 8. Approval
    c_appr = Card.approval(approval_id="appr_1", summary="Confirm commit", details={"files": 1})
    assert c_appr.kind == "approval"
    assert c_appr.data["options"] == ["approve", "deny"]

    # 9. Artifact
    c_art = Card.artifact(name="plan.md", media_type="text/markdown", url="/artifacts/plan.md", request_feedback=True)
    assert c_art.kind == "artifact"
    assert c_art.data["request_feedback"] is True

    # 10. Form
    c_form = Card.form(form_id="f1", schema={"type": "object"}, submit_label="Run")
    assert c_form.kind == "form"


def test_cards_validation_failures():
    # Missing body in markdown
    with pytest.raises(ValueError) as exc:
        validate_card(Card(kind="markdown", data={}))
    assert "missing required string field 'body'" in str(exc.value)

    # Missing stats in diff
    with pytest.raises(ValueError) as exc:
        validate_card(Card(kind="diff", data={"path": "a.txt", "unified": "..."}))
    assert "missing required dict field 'stats'" in str(exc.value)

    # Invalid notice level
    with pytest.raises(ValueError) as exc:
        validate_card(Card(kind="notice", data={"body": "msg", "level": "critical"}))
    assert "level must be 'info', 'warn', or 'error'" in str(exc.value)

    # Progress value out of range
    with pytest.raises(ValueError) as exc:
        validate_card(Card(kind="progress", data={"label": "Loading", "value": 1.5}))
    assert "value must be between 0.0 and 1.0" in str(exc.value)


def test_cards_fallback():
    custom_card = Card(
        kind="x-custom-widget",
        title="Custom 3D Scene",
        data={"vertices": 500, "textures": ["sky.png"]},
    )
    rendered = fallback(custom_card)
    assert rendered.kind == "code"
    assert rendered.data["language"] == "json"
    assert "sky.png" in rendered.data["body"]
    assert "Custom 3D Scene" in (rendered.title or "")


def test_events_envelope_and_known_types():
    for evt_type in KNOWN_EVENT_TYPES:
        evt = Event(type=evt_type, data={"sample": 123}, session_id="sess_1", turn_id="turn_1")
        d = evt.to_dict()
        assert d["type"] == evt_type
        assert d["data"] == {"sample": 123}
        assert d["session_id"] == "sess_1"

        roundtrip = Event.from_dict(d)
        assert roundtrip.type == evt_type
        assert roundtrip.session_id == "sess_1"


def test_events_schema_drift(tmp_path: Path):
    generated = events_schema()
    schema_path = Path("docs/events.schema.json")
    assert schema_path.exists(), "docs/events.schema.json must exist in repository"

    committed = json.loads(schema_path.read_text(encoding="utf-8"))
    assert generated == committed, "docs/events.schema.json has drifted from events_schema()"


def test_cli_events_schema_command():
    runner = CliRunner()
    result = runner.invoke(main, ["events", "schema"])
    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert parsed["title"] == "HarnessZeroEventEnvelope"
    assert "turn.completed" in parsed["properties"]["type"]["enum"]
