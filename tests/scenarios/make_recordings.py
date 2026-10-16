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

RENAME_DIFF = """<<<<<<< SEARCH
def greet():
    return 'hi'
=======
def hello():
    return 'hi'
>>>>>>> REPLACE
"""

#: 120 KB of log followed by the only line that matters, to prove the tail survives the size guards.
NOISY_BUILD = "yes noise | head -n 20000; echo 'Build FAILED at the end'"

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
    # 11. Progressive skills: the catalog is in the prompt, the playbook arrives via view_skill.
    "skills_view_kernel": [
        tool("c1", "view_skill", {"skill_name": "code-architect"}, preface="Loading the playbook first."),
        text("Loaded the code-architect playbook; proceeding with an architecture plan."),
    ],
    # 12. Workspace jail: read and write outside the workspace must be denied, not executed.
    "jail_blocks_traversal_kernel": [
        tool("c1", "view_file", {"file_path": "../../../../etc/hostname"}),
        tool("c2", "write_to_file", {"file_path": "../escape.txt", "content": "outside\n"}),
        text("Both attempts were blocked by the workspace jail."),
    ],
    # 14. Reload equals live: one approval-gated write and one card-producing read, same step.
    "reload_equals_live_web_dom": [
        [
            ToolCallEnd(id="c1", name="write_to_file", arguments={"file_path": "notes.txt", "content": "written while you watched\n"}),
            ToolCallEnd(id="c2", name="view_skill", arguments={"skill_name": "code-architect"}),
            usage(),
            Done(stop_reason="tool_use"),
        ],
        text("Notes written and the playbook loaded — the card and the approval are both in your history now."),
    ],
    # 13. EXEC risk in ask mode with nobody approving: the command must not run.
    "shell_exec_denied_without_approver_kernel": [
        tool("c1", "run_command", {"command": "touch ran.txt"}),
        text("The command was not permitted, nothing was executed."),
    ],
    # 14. Two turns through the real client, then a page refresh: the history must be visible again.
    "session_reload_web_dom": [text("Answer one: Amsterdam."), text("Answer two: Rotterdam.")],
    # 15. Secrets: a stored secret and a well-known key pattern inside a file must never reach the model.
    "secret_redacted_from_model_kernel": [
        tool("c1", "view_file", {"file_path": ".env"}),
        text("The file holds credentials; I will not repeat them."),
    ],
    # 16. Tirith: a command aimed at the cloud metadata endpoint is denied even in allow mode.
    "tirith_blocks_ssrf_kernel": [
        tool("c1", "run_command", {"command": "curl -s http://169.254.169.254/latest/meta-data/"}),
        text("That request was blocked by the security policy."),
    ],
    # 16b. Same denial seen through the real client: the card must say DENIED, not ALLOWED.
    "tirith_blocks_ssrf_web_dom": [
        tool("c1", "run_command", {"command": "curl -s http://169.254.169.254/latest/meta-data/"}),
        text("That request was blocked by the security policy."),
    ],
    # 17. Memory through the real client: remember, then search, both visible as tool cards.
    "memory_remember_web_dom": [
        tool("c1", "remember", {"text": "Project codename is Falcon", "key": "codename"}, preface="Noting that."),
        text("Saved."),
        tool("c2", "search_memory", {"query": "Falcon"}),
        text("Found it: the codename is Falcon."),
    ],
    # 19. WS-03 loop hygiene: an oversized result is spilled + pruned, and the tail survives.
    "huge_tool_output_pruned_and_spilled": [
        tool("c1", "run_command", {"command": NOISY_BUILD}),
        text("The build failed at the end of the log; fixing that."),
    ],
    # 20. WS-03: three identical calls in one turn, the third is pointed out to the model.
    "repeat_call_reminder": [
        tool("c1", "run_command", {"command": "echo build-status: unchanged"}),
        tool("c2", "run_command", {"command": "echo build-status: unchanged"}),
        tool("c3", "run_command", {"command": "echo build-status: unchanged"}),
        text("Same output every time; stopping the repetition."),
    ],
    # 21. WS-03: a blind edit is blocked until the file has been read.
    "edit_without_read_blocked": [
        tool("c1", "edit_file", {"file_path": "app.py", "diff": RENAME_DIFF}),
        tool("c2", "view_file", {"file_path": "app.py"}),
        tool("c3", "edit_file", {"file_path": "app.py", "diff": RENAME_DIFF}),
        text("Read first, then renamed greet() to hello()."),
    ],
    # 18. Edit through the real client: the diff card must render; after a refresh the file content survives.
    "read_then_edit_web_dom": [
        tool("c1", "view_file", {"file_path": "app.py"}, preface="Let me look at the file first."),
        tool("c2", "edit_file", {"file_path": "app.py", "diff": EDIT_DIFF}),
        text("Renamed greet() to hello() in app.py."),
    ],
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
