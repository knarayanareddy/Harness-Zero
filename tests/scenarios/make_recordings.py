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

ASK_USER_QUESTION = {
    "id": "format",
    "question": "Which format should I use?",
    "options": [
        {"id": "markdown", "label": "Markdown", "description": "Readable plain text"},
        {"id": "html", "label": "HTML", "description": "Rich browser layout"},
    ],
    "allow_custom": True,
}

RECORDINGS: dict[str, list[list]] = {
    "ask_user_answered": [
        tool("c1", "ask_user", {"questions": [ASK_USER_QUESTION]}),
        text("I will use Markdown."),
    ],
    "ask_user_headless": [
        tool("c1", "ask_user", {"questions": [ASK_USER_QUESTION]}),
        text("I will proceed with the best assumption."),
    ],
    # 1. A plain streamed answer; two deltas so chunk collapsing is exercised.
    "plain_answer_kernel": [text("The capital of the Netherlands ", "is Amsterdam.")],
    # WS-10a: start a durable job, let the turn complete independently, then surface its completion next turn.
    "job_finishes_between_turns": [
        tool(
            "job-start-1",
            "job_start",
            {"command": "printf 'job finished between turns\\n'", "label": "short-job"},
        ),
        text("The short job has been started."),
        text("The background job is still running."),
        text("The background job finished successfully."),
    ],
    "plain_answer_web": [text("The capital of the Netherlands ", "is Amsterdam.")],
    # The two WS-05c scenarios are about the *surfaces* a plugin owns, but a web scenario needs a session to
    # paint, so each carries one throwaway turn. The answer is a sentence, not a claim about the system: the
    # numbers a plugin contributes are asserted from `ui.contributions` and `plugins.list`, never from here.
    "plugin_panel_appears_when_enabled": [text("The capital of the Netherlands ", "is Amsterdam.")],
    "settings_tab_from_plugin_schema": [text("Hello — what should I look at?")],
    # 1b. WS-07 per-turn workspace summary: read first, make two changes, then finish the turn.
    "turn_changes_summary": [
        tool("c0", "view_file", {"file_path": "app.py"}),
        tool("c1", "write_to_file", {"file_path": "app.py", "content": "new value\n"}),
        tool("c2", "write_to_file", {"file_path": "notes.txt", "content": "line one\nline two\n"}),
        text("Both files are ready."),
    ],
    # WS-07 artifact flow: the user-facing artifact is created, then explicitly presented and opened.
    "present_artifact": [
        tool(
            "c1",
            "create_artifact",
            {
                "name": "final.md",
                "content": "# Final report\n\nThe requested result.\n",
                "media_type": "text/markdown",
                "user_facing": True,
                "request_feedback": False,
                "summary": "Final report",
            },
        ),
        tool("c2", "present", {"path": "final.md", "caption": "Final report"}),
        text("The final report is ready."),
    ],
    "present_workspace_file": [
        tool("c1", "present", {"path": "report.md", "caption": "Final report"}),
        text("The workspace report is ready."),
    ],
    # WS-07 restore flow: a write followed by the destructive, explicitly approved turn-start restore.
    "restore_turn": [
        tool("c1", "write_to_file", {"file_path": "draft.txt", "content": "after\n"}),
        tool("c2", "checkpoint_restore", {"checkpoint_id": "turn-start"}),
        text("Restored draft.txt to its original contents."),
    ],
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
    # 13b. The `locked` preset is a one-setting route to plan mode; the denial is written by the kernel before
    # the executor exists in this call, so even an installed and available sandbox cannot soften it.
    "preset_locked_denies_exec": [
        tool("c1", "run_command", {"command": "touch ran.txt"}),
        text("The command was denied in plan mode; nothing was executed."),
    ],
    # 13c. Browser status is sourced from security.status / /health, not from a client-side list of presets or
    # an optimistic guess from settings.schema. `backend=none` makes the enforcement label deterministic on CI.
    "security_preset_badge_web_dom": [text("Hello — what should I look at?")],
    # 13d. An unavailable required backend is a deterministic kernel deny; the setting `backend=none` makes this
    # true even on Linux CI after bubblewrap is installed, and explicit `bypass` proves policy cannot weaken it.
    "sandbox_unavailable_required_denies": [
        tool("c1", "run_command", {"command": "touch ran.txt"}),
        text("The command was denied because the required OS sandbox is unavailable; nothing ran."),
    ],
    # 13e. This web scenario is skipped with a reason when the host's bubblewrap probe cannot enforce namespaces.
    "sandbox_bwrap_full_badge": [text("Hello — what should I look at?")],
    # 14. Two turns through the real client, then a page refresh: the history must be visible again.
    "session_reload_web_dom": [text("Answer one: Amsterdam."), text("Answer two: Rotterdam.")],
    # 14b. The same two turns, but the *daemon* restarts before the page reloads: one session row, full
    # history, no click. The client must not need the process that wrote the records.
    "daemon_restart_web_dom": [text("Answer one: Amsterdam."), text("Answer two: Rotterdam.")],
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
    """Writes every synthetic fixture — and refuses to run if the corpus is only partly reproducible.

    A fixture nobody can regenerate is a fixture nobody can review, so the generator and the corpus are kept
    in step by `test_make_recordings.py`. Streams recorded from a real provider (`recorded_from: <name>`) are
    deliberately not part of this file: they are written by ``HARNESS_REPLAY=record``.
    """
    synthetic = {
        d.name
        for d in HERE.iterdir()
        if (d / "scenario.yaml").exists() and "recorded_from: synthetic" in (d / "scenario.yaml").read_text()
    }
    unaccounted = sorted(synthetic - set(RECORDINGS))
    if unaccounted:
        raise SystemExit(
            "no generator entry for synthetic scenario(s): " + ", ".join(unaccounted) + " — add the stream to "
            "RECORDINGS (or mark the scenario `recorded_from: <provider>/<model>` if it came from a real one)"
        )
    for name, streams in RECORDINGS.items():
        d = HERE / name
        if not d.exists():
            print(f"skip {name}: no scenario dir")
            continue
        write_streams(d / "provider.jsonl", streams)
        print(f"wrote {name}/provider.jsonl ({len(streams)} requests)")


if __name__ == "__main__":
    main()
