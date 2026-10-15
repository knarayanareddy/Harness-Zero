"""(Re)generates the synthetic ``provider.jsonl`` recordings for the built-in scenarios.

Real recordings come from ``HARNESS_REPLAY=record`` against a configured
provider; these are hand-authored event streams that use the same codec, so
the kernel cannot tell the difference. Run from this directory::

    python make_recordings.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from _lib.codec import write_streams  # noqa: E402

from harness.sdk.types import Done, TextDelta, ThinkingDelta, ToolCallEnd, Usage, UsageEvent  # noqa: E402

HERE = Path(__file__).parent


def usage(i: int = 20, o: int = 8) -> UsageEvent:
    return UsageEvent(usage=Usage(input_tokens=i, output_tokens=o))


def text(*parts: str, stop: str = "end_turn") -> list:
    return [*(TextDelta(text=p) for p in parts), usage(), Done(stop_reason=stop)]


def tool(call_id: str, name: str, arguments: dict, preface: str | None = None) -> list:
    evs = []
    if preface:
        evs.append(TextDelta(text=preface))
    evs += [ToolCallEnd(id=call_id, name=name, arguments=arguments), usage(), Done(stop_reason="tool_use")]
    return evs


EDIT_DIFF = """<<<<<<< SEARCH
def greet(name):
    return f"Hello, {name}!"
=======
def hello(name):
    return f"Hello, {name}!"
>>>>>>> REPLACE
"""

RECORDINGS: dict[str, list[list]] = {
    # 1. A plain streamed answer; two deltas so chunk collapsing is exercised.
    "plain_answer_kernel": [text("The capital of the Netherlands ", "is Amsterdam.")],
    "plain_answer_web": [text("The capital of the Netherlands ", "is Amsterdam.")],
    # 2. Read → edit → confirm (diff card, checkpoint hook, file on disk).
    "read_then_edit_kernel": [
        tool("c1", "view_file", {"file_path": "app.py"}, preface="Let me look at the file first."),
        tool("c2", "edit_file", {"file_path": "app.py", "diff": EDIT_DIFF}),
        text("Renamed greet() to hello() in app.py."),
    ],
    # 3. Approval: a WRITE in `ask` mode with a scripted human approving.
    "approval_allow_kernel": [
        tool("c1", "write_to_file", {"file_path": "notes.txt", "content": "approved write\n"}),
        text("Wrote notes.txt."),
    ],
    # 4. Approval with nobody listening: must fail closed (denied), model told.
    "approval_deny_no_approver_kernel": [
        tool("c1", "write_to_file", {"file_path": "notes.txt", "content": "should never land\n"}),
        text("Understood — the write was not permitted, so I did not change anything."),
    ],
    # 5. Memory round-trip in ONE scenario across a restart (two sessions).
    "memory_remember_search_kernel": [
        tool("c1", "remember", {"text": "Project codename is Falcon", "key": "codename"}, preface="Noting that."),
        text("Saved."),
        tool("c2", "search_memory", {"query": "Falcon"}),
        text("Found it: the codename is Falcon."),
    ],
    # 6. MCP tool through the kernel (stdio echo fixture from tests/fixtures).
    "mcp_echo_kernel": [
        tool("c1", "mcp__echo__echo", {"message": "through the kernel"}),
        text("The MCP server echoed the message."),
    ],
    # 7. Two turns in the web lane, then reload: history must contain both.
    "session_resume_web": [text("First answer."), text("Second answer, and I remember the first.")],
    # 8. Approval through the real UI buttons (jsdom clicks approve).
    "approval_allow_web_dom": [
        tool("c1", "write_to_file", {"file_path": "notes.txt", "content": "approved from the browser\n"}),
        text("Done — notes.txt written."),
    ],
    # 9. Thinking + text: thinking must be visible in the UI but never fed back as text.
    "thinking_then_answer_web": [
        [ThinkingDelta(text="The user wants a short answer."), TextDelta(text="Forty-two."), usage(), Done(stop_reason="end_turn")]
    ],
    # 10. Tool loop that never ends: the kernel's max-iterations guard must stop it and the recording must NOT be exhausted silently.
    "runaway_tool_loop_kernel": [tool(f"c{i}", "view_file", {"file_path": "app.py"}) for i in range(1, 26)],
}


def main() -> None:
    for name, streams in RECORDINGS.items():
        d = HERE / name
        if not d.exists():
            print(f"skip {name}: no scenario dir")
            continue
        write_streams(d / "provider.jsonl", streams)
        print(f"wrote {name}/provider.jsonl ({len(streams)} requests)")


if __name__ == "__main__":
    main()
