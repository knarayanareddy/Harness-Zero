import asyncio
from pathlib import Path

import pytest

from harness.core.coordinator import AgentCoordinator
from harness.core.types import ToolCall
from harness.kernel.jail import PathOutsideWorkspace, WorkspaceJail
from harness.sandboxing.executor import CommandExecutor


def test_workspace_jail_containment(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_file = outside / "secret.txt"
    outside_file.write_text("secret_content", encoding="utf-8")

    jail = WorkspaceJail(workspace)

    # 1. Normal resolution
    inside_file = workspace / "foo.txt"
    inside_file.write_text("hello", encoding="utf-8")
    assert jail.resolve("foo.txt") == inside_file.resolve()
    assert jail.resolve(str(inside_file)) == inside_file.resolve()

    # 2. Directory traversal escape blocked
    with pytest.raises(PathOutsideWorkspace):
        jail.resolve("../outside/secret.txt")

    with pytest.raises(PathOutsideWorkspace):
        jail.resolve(outside_file)

    # 3. Symlink escape blocked
    symlink_path = workspace / "link_to_outside"
    try:
        symlink_path.symlink_to(outside_file)
        with pytest.raises(PathOutsideWorkspace):
            jail.resolve("link_to_outside")
    except OSError:
        pass  # On systems without symlink permission

    # 4. .git internal write protection
    git_dir = workspace / ".git"
    git_dir.mkdir()
    hooks_dir = git_dir / "hooks"
    hooks_dir.mkdir()
    hook_file = hooks_dir / "pre-commit"

    with pytest.raises(PathOutsideWorkspace):
        jail.resolve(hook_file, for_write=True)

    # Reading .git is allowed (for_write=False)
    assert jail.resolve(hook_file, for_write=False) == hook_file.resolve()

    # .git/info/exclude is allowed for write
    info_dir = git_dir / "info"
    info_dir.mkdir()
    exclude_file = info_dir / "exclude"
    assert jail.resolve(exclude_file, for_write=True) == exclude_file.resolve()


@pytest.mark.asyncio
async def test_coordinator_tools_jailed(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("classified", encoding="utf-8")

    coordinator = AgentCoordinator(workspace=workspace)

    # 1. view_file outside workspace denied
    call_view = ToolCall(id="call_v", name="view_file", arguments={"AbsolutePath": str(secret)})
    res = await coordinator._dispatch_tool(call_view)
    assert res.is_error
    assert "Security Violation" in res.output

    # 2. write_to_file outside workspace denied
    outside_target = outside / "bad_write.txt"
    call_write = ToolCall(id="call_w", name="write_to_file", arguments={"TargetFile": str(outside_target), "CodeContent": "bad"})
    res = await coordinator._dispatch_tool(call_write)
    assert res.is_error
    assert "Security Violation" in res.output
    assert not outside_target.exists()

    # 3. write_to_file inside .git/hooks denied
    git_hook = workspace / ".git" / "hooks" / "post-commit"
    call_git = ToolCall(id="call_g", name="write_to_file", arguments={"TargetFile": str(git_hook), "CodeContent": "malicious"})
    res = await coordinator._dispatch_tool(call_git)
    assert res.is_error
    assert "Security Violation" in res.output
    assert not git_hook.exists()

    # 4. edit_file outside workspace denied
    call_edit = ToolCall(
        id="call_e",
        name="edit_file",
        arguments={
            "TargetFile": str(secret),
            "DiffBlock": "<<<<<<< SEARCH\nclassified\n=======\nhacked\n>>>>>>> REPLACE",
        },
    )
    res = await coordinator._dispatch_tool(call_edit)
    assert res.is_error
    assert "Security Violation" in res.output

    # 5. run_command with Cwd outside workspace denied
    call_cmd = ToolCall(id="call_c", name="run_command", arguments={"CommandLine": "pwd", "Cwd": str(outside)})
    res = await coordinator._dispatch_tool(call_cmd)
    assert res.is_error
    assert "Security Violation" in res.output

    # 6. run_command with non-existent Cwd fails cleanly
    call_cmd_missing = ToolCall(id="call_m", name="run_command", arguments={"CommandLine": "pwd", "Cwd": str(workspace / "non_existent")})
    res = await coordinator._dispatch_tool(call_cmd_missing)
    assert res.is_error
    assert "does not exist" in res.output


@pytest.mark.asyncio
async def test_executor_env_scrubbing(tmp_path: Path):
    executor = CommandExecutor()

    # Infiltrate sensitive keys into process environment
    test_env = {
        "OPENAI_API_KEY": "sk-secret123",
        "GITHUB_TOKEN": "ghp_secret456",
        "AWS_SECRET_ACCESS_KEY": "aws-secret789",
        "MY_DATABASE_PASSWORD": "mypassword",
        "SSH_PRIVATE_KEY": "ssh-rsa...",
        "NORMAL_VAR": "safe_value",
    }
    with pytest.MonkeyPatch.context() as mp:
        for k, v in test_env.items():
            mp.setenv(k, v)

        scrubbed = executor._scrubbed_env()
        assert "OPENAI_API_KEY" not in scrubbed
        assert "GITHUB_TOKEN" not in scrubbed
        assert "AWS_SECRET_ACCESS_KEY" not in scrubbed
        assert "MY_DATABASE_PASSWORD" not in scrubbed
        assert "SSH_PRIVATE_KEY" not in scrubbed
        assert scrubbed.get("NORMAL_VAR") == "safe_value"
        assert scrubbed.get("PAGER") == "cat"
        assert scrubbed.get("GIT_PAGER") == "cat"
        assert scrubbed.get("CI") == "1"
        assert scrubbed.get("HARNESS_AGENT") == "1"
        assert scrubbed.get("TERM") == "dumb"

        # Test running a command prints only safe environment
        res = await executor.run("python3 -c 'import os; print(os.environ.get(\"OPENAI_API_KEY\", \"NONE\"))'", cwd=tmp_path)
        assert "NONE" in res["stdout"]


@pytest.mark.asyncio
async def test_executor_devnull_stdin(tmp_path: Path):
    executor = CommandExecutor()
    # cat with no args expects stdin; with DEVNULL it terminates immediately with EOF
    res = await executor.run("cat", cwd=tmp_path, wait_ms_before_async=2000)
    assert not res.get("is_background")
    assert res.get("exit_code") == 0


@pytest.mark.asyncio
async def test_executor_no_mkdir(tmp_path: Path):
    executor = CommandExecutor()
    non_existent = tmp_path / "does_not_exist"
    with pytest.raises(FileNotFoundError):
        await executor.run("echo hello", cwd=non_existent)
    assert not non_existent.exists()


@pytest.mark.asyncio
async def test_executor_kill_status_stability(tmp_path: Path):
    executor = CommandExecutor()
    # Launch long-running task
    res = await executor.run("sleep 30", cwd=tmp_path, wait_ms_before_async=50)
    assert res.get("is_background")
    task_id = res["task_id"]

    task = executor.get_task(task_id)
    assert task is not None
    assert task.status == "running"

    # Kill task
    killed = await executor.kill_task(task_id)
    assert killed is True
    assert task.status == "killed"
    assert task.killed is True

    # Allow event loop to process process exit
    await asyncio.sleep(0.3)
    assert task.status == "killed"
