from __future__ import annotations

import asyncio
import errno
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import uuid
from collections import OrderedDict
from pathlib import Path

import pytest

from harness.plugins.hooks.checkpoints import shadow_repo


def _executable(path: Path, source: str) -> Path:
    path.write_text(f"#!/bin/sh\n{source}", encoding="utf-8")
    path.chmod(0o755)
    return path


def _fake_git(path: Path, log: Path) -> Path:
    return _executable(
        path,
        f"printf '%s\\t%s\\n' \"$PWD\" \"$*\" >> {shlex.quote(str(log))}\n"
        "printf 'fixture-git\\n'\n",
    )


def _fake_xcrun(path: Path, output: str, *, exit_code: int = 0) -> Path:
    return _executable(
        path,
        "printf '%s\\t%s\\n' \"$*\" \"${DEVELOPER_DIR-}\" >> \"$XCRUN_LOG\"\n"
        f"printf '%s' {shlex.quote(output)}\n"
        f"exit {exit_code}\n",
    )


@pytest.fixture(autouse=True)
def _reset_git_resolution_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        shadow_repo, "_GIT_EXECUTABLE_CACHE", OrderedDict(), raising=False
    )


@pytest.mark.asyncio
async def test_darwin_system_launcher_uses_discovered_git_and_caches_by_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(shadow_repo, "_IS_DARWIN", True, raising=False)
    monkeypatch.setattr(shutil, "which", lambda name, path=None: "/usr/bin/git")
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("DEVELOPER_DIR", "/Developer/First")
    git_log = tmp_path / "git.jsonl"
    discovered_git = _fake_git(tmp_path / "developer-git", git_log)
    discoveries: list[tuple[Path, str | None, str | None]] = []

    async def discover_git(cwd: Path, environment: dict[str, str]) -> str:
        discoveries.append(
            (cwd, environment.get("PATH"), environment.get("DEVELOPER_DIR"))
        )
        return str(discovered_git)

    monkeypatch.setattr(shadow_repo, "_discover_git", discover_git)

    cwd = tmp_path / "working-directory"
    cwd.mkdir()
    first = await shadow_repo._run_git(["status", "--short"], cwd)
    second = await shadow_repo._run_git(["rev-parse", "HEAD"], cwd)

    assert first == (0, "fixture-git\n", "")
    assert second == (0, "fixture-git\n", "")
    assert discoveries == [(cwd, "/usr/bin", "/Developer/First")]
    assert git_log.read_text().splitlines() == [
        f"{cwd}\tstatus --short",
        f"{cwd}\trev-parse HEAD",
    ]

    monkeypatch.setenv("DEVELOPER_DIR", "/Developer/Second")
    changed_developer = await shadow_repo._run_git(["--version"], cwd)
    assert changed_developer == (0, "fixture-git\n", "")
    assert discoveries == [
        (cwd, "/usr/bin", "/Developer/First"),
        (cwd, "/usr/bin", "/Developer/Second"),
    ]

    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    changed_path = await shadow_repo._run_git(["--version"], cwd)
    assert changed_path == (0, "fixture-git\n", "")
    assert discoveries[-1] == (cwd, "/usr/bin:/bin", "/Developer/Second")
    assert len(discoveries) == 3


@pytest.mark.asyncio
async def test_vendor_discovery_uses_validated_absolute_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(shadow_repo, "_IS_DARWIN", True, raising=False)
    monkeypatch.setattr(shutil, "which", lambda name, path=None: "/usr/bin/git")
    monkeypatch.delenv("DEVELOPER_DIR", raising=False)
    git_dir = tmp_path / "bin"
    git_dir.mkdir()
    git_log = tmp_path / "git.jsonl"
    discovered_git = _fake_git(tmp_path / "developer-git", git_log)
    _fake_git(git_dir / "git", tmp_path / "fallback-git.jsonl")
    monkeypatch.setenv("PATH", str(git_dir))
    xcrun_log = tmp_path / "xcrun.jsonl"
    xcrun = _fake_xcrun(tmp_path / "xcrun", str(discovered_git) + "\n")
    monkeypatch.setattr(shadow_repo, "_XCRUN_PATH", str(xcrun), raising=False)
    monkeypatch.setenv("XCRUN_LOG", str(xcrun_log))

    result = await shadow_repo._run_git(["status", "--short"], tmp_path)

    assert result == (0, "fixture-git\n", "")
    assert xcrun_log.read_text().splitlines() == ["--find git\t"]
    assert git_log.read_text().splitlines() == [f"{tmp_path}\tstatus --short"]
    assert not (tmp_path / "fallback-git.jsonl").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path_kind", ["custom", "symlink", "relative", "empty-entry"]
)
async def test_darwin_custom_or_ambiguous_path_keeps_literal_git_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path_kind: str
) -> None:
    monkeypatch.setattr(shadow_repo, "_IS_DARWIN", True, raising=False)
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    git_log = tmp_path / "git.jsonl"
    if path_kind == "custom":
        git_dir = tmp_path / "custom-bin"
        git_dir.mkdir()
        _fake_git(git_dir / "git", git_log)
        path = str(git_dir)
    elif path_kind == "symlink":
        git_dir = tmp_path / "real-bin"
        git_dir.mkdir()
        _fake_git(git_dir / "git", git_log)
        linked_dir = tmp_path / "linked-bin"
        linked_dir.symlink_to(git_dir, target_is_directory=True)
        path = str(linked_dir)
    elif path_kind == "relative":
        git_dir = cwd / "relative-bin"
        git_dir.mkdir()
        _fake_git(git_dir / "git", git_log)
        path = "relative-bin"
    else:
        _fake_git(cwd / "git", git_log)
        path = os.pathsep + "/usr/bin"
    xcrun_log = tmp_path / "xcrun.jsonl"
    xcrun = _fake_xcrun(tmp_path / "xcrun", "/not-used/git\n")
    monkeypatch.setattr(shadow_repo, "_XCRUN_PATH", str(xcrun), raising=False)
    monkeypatch.setenv("XCRUN_LOG", str(xcrun_log))
    monkeypatch.setenv("PATH", path)

    result = await shadow_repo._run_git(["custom-argument"], cwd)

    assert result == (0, "fixture-git\n", "")
    assert not xcrun_log.exists()
    assert git_log.read_text().splitlines() == [f"{cwd}\tcustom-argument"]


@pytest.mark.asyncio
async def test_non_darwin_keeps_literal_custom_git_without_vendor_discovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(shadow_repo, "_IS_DARWIN", False, raising=False)
    git_log = tmp_path / "git.jsonl"
    git_dir = tmp_path / "custom-bin"
    git_dir.mkdir()
    _fake_git(git_dir / "git", git_log)
    xcrun_log = tmp_path / "xcrun.jsonl"
    xcrun = _fake_xcrun(tmp_path / "xcrun", "/not-used/git\n")
    monkeypatch.setattr(shadow_repo, "_XCRUN_PATH", str(xcrun), raising=False)
    monkeypatch.setenv("XCRUN_LOG", str(xcrun_log))
    monkeypatch.setenv("PATH", str(git_dir))

    result = await shadow_repo._run_git(["--version"], tmp_path)

    assert result == (0, "fixture-git\n", "")
    assert not xcrun_log.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "discovery_result",
    ["empty", "multiline", "relative", "missing", "non-executable", "nonzero", "bad-utf8"],
)
async def test_invalid_vendor_discovery_falls_back_and_is_not_cached(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    discovery_result: str,
) -> None:
    monkeypatch.setattr(shadow_repo, "_IS_DARWIN", True, raising=False)
    monkeypatch.setattr(shutil, "which", lambda name, path=None: "/usr/bin/git")
    git_log = tmp_path / "git.jsonl"
    git_dir = tmp_path / "bin"
    git_dir.mkdir()
    _fake_git(git_dir / "git", git_log)
    candidate = tmp_path / "not-executable"
    candidate.write_text("not executable\n", encoding="utf-8")
    outputs = {
        "empty": "",
        "multiline": str(git_dir / "git") + "\nextra\n",
        "relative": "relative/git\n",
        "missing": str(tmp_path / "absent-git") + "\n",
        "non-executable": str(candidate) + "\n",
        "nonzero": str(git_dir / "git") + "\n",
        "bad-utf8": "",
    }
    exit_code = 1 if discovery_result == "nonzero" else 0
    xcrun = _fake_xcrun(tmp_path / "xcrun", outputs[discovery_result], exit_code=exit_code)
    if discovery_result == "bad-utf8":
        xcrun = _executable(
            tmp_path / "xcrun",
            "printf '%s\\n' \"$*\" >> \"$XCRUN_LOG\"\n"
            "printf '\\377\\n'\n",
        )
    monkeypatch.setattr(shadow_repo, "_XCRUN_PATH", str(xcrun), raising=False)
    xcrun_log = tmp_path / "xcrun.jsonl"
    monkeypatch.setenv("XCRUN_LOG", str(xcrun_log))
    monkeypatch.setenv("PATH", str(git_dir))
    discoveries: list[Path] = []
    discover_git = shadow_repo._discover_git

    async def count_discovery(cwd: Path, environment: dict[str, str]) -> str | None:
        discoveries.append(cwd)
        return await discover_git(cwd, environment)

    monkeypatch.setattr(shadow_repo, "_discover_git", count_discovery)

    first = await shadow_repo._run_git(["--version"], tmp_path)
    assert not shadow_repo._GIT_EXECUTABLE_CACHE
    second = await shadow_repo._run_git(["--version"], tmp_path)

    assert first == second == (0, "fixture-git\n", "")
    assert len(discoveries) == 2
    assert len(git_log.read_text().splitlines()) == 2


def _blocking_xcrun(path: Path, pid_file: Path) -> Path:
    pid_temp = pid_file.with_name(f".{pid_file.name}.tmp")
    path.write_text(
        "#!/bin/sh\n"
        "trap '' TERM\n"
        f"printf '%s\\n' \"$$\" > {shlex.quote(str(pid_temp))}\n"
        f"/bin/mv {shlex.quote(str(pid_temp))} {shlex.quote(str(pid_file))}\n"
        "exec /bin/sleep 30\n",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


async def _wait_for_file(path: Path) -> None:
    async with asyncio.timeout(2):
        while not path.exists():
            await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_discovery_timeout_reaps_child_then_falls_back_to_original_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(shadow_repo, "_IS_DARWIN", True, raising=False)
    monkeypatch.setattr(shutil, "which", lambda name, path=None: "/usr/bin/git")
    monkeypatch.setattr(shadow_repo, "_GIT_DISCOVERY_TIMEOUT", 1.0, raising=False)
    git_log = tmp_path / "git.jsonl"
    git_dir = tmp_path / "bin"
    git_dir.mkdir()
    _fake_git(git_dir / "git", git_log)
    pid_file = tmp_path / "xcrun.pid"
    xcrun = _blocking_xcrun(tmp_path / "xcrun", pid_file)
    monkeypatch.setattr(shadow_repo, "_XCRUN_PATH", str(xcrun), raising=False)
    monkeypatch.setenv("PATH", str(git_dir))
    children: list[asyncio.subprocess.Process] = []
    fixture_ready_pids: list[int] = []
    observed_discovery_deadlines: list[float] = []
    create_subprocess = asyncio.create_subprocess_exec
    wait_for = asyncio.wait_for

    async def record_child(*args: object, **kwargs: object) -> asyncio.subprocess.Process:
        child = await create_subprocess(*args, **kwargs)
        children.append(child)
        if args and args[0] == str(xcrun):
            try:
                # Keep fixture startup outside _discover_git's communication budget.
                await _wait_for_file(pid_file)
                published_pid = int(pid_file.read_text(encoding="ascii"))
                assert published_pid == child.pid
            except BaseException:
                if child.returncode is None:
                    child.kill()
                    await child.wait()
                raise
            fixture_ready_pids.append(published_pid)
        return child

    async def observe_wait_for(awaitable: object, timeout: float | None = None) -> object:
        if timeout == shadow_repo._GIT_DISCOVERY_TIMEOUT:
            assert fixture_ready_pids, "discovery communication began before fixture readiness"
            observed_discovery_deadlines.append(timeout)
        return await wait_for(awaitable, timeout=timeout)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", record_child)
    monkeypatch.setattr(asyncio, "wait_for", observe_wait_for)
    task = asyncio.create_task(shadow_repo._run_git(["--version"], tmp_path))
    try:
        await _wait_for_file(pid_file)
        result = await task

        assert result == (0, "fixture-git\n", "")
        assert fixture_ready_pids == [children[0].pid]
        assert observed_discovery_deadlines == [1.0]
        assert children[0].returncode is not None
        assert len(git_log.read_text().splitlines()) == 1
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        for child in children:
            if child.returncode is None:
                child.kill()
                await child.wait()


@pytest.mark.asyncio
async def test_cancelled_discovery_reaps_child_without_running_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(shadow_repo, "_IS_DARWIN", True, raising=False)
    monkeypatch.setattr(shutil, "which", lambda name, path=None: "/usr/bin/git")
    monkeypatch.setattr(shadow_repo, "_GIT_DISCOVERY_TIMEOUT", 10.0, raising=False)
    git_log = tmp_path / "git.jsonl"
    git_dir = tmp_path / "bin"
    git_dir.mkdir()
    _fake_git(git_dir / "git", git_log)
    pid_file = tmp_path / "xcrun.pid"
    xcrun = _blocking_xcrun(tmp_path / "xcrun", pid_file)
    monkeypatch.setattr(shadow_repo, "_XCRUN_PATH", str(xcrun), raising=False)
    monkeypatch.setenv("PATH", str(git_dir))
    children: list[asyncio.subprocess.Process] = []
    create_subprocess = asyncio.create_subprocess_exec

    async def record_child(*args: object, **kwargs: object) -> asyncio.subprocess.Process:
        child = await create_subprocess(*args, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(asyncio, "create_subprocess_exec", record_child)
    task = asyncio.create_task(shadow_repo._run_git(["--version"], tmp_path))
    await _wait_for_file(pid_file)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert children[0].returncode is not None
    assert not git_log.exists()


@pytest.mark.asyncio
async def test_missing_xcrun_falls_back_to_literal_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(shadow_repo, "_IS_DARWIN", True, raising=False)
    monkeypatch.setattr(shutil, "which", lambda name, path=None: "/usr/bin/git")
    git_log = tmp_path / "git.jsonl"
    git_dir = tmp_path / "bin"
    git_dir.mkdir()
    _fake_git(git_dir / "git", git_log)
    monkeypatch.setattr(shadow_repo, "_XCRUN_PATH", str(tmp_path / "missing-xcrun"), raising=False)
    monkeypatch.setenv("PATH", str(git_dir))

    result = await shadow_repo._run_git(["--version"], tmp_path)

    assert result == (0, "fixture-git\n", "")
    assert len(git_log.read_text().splitlines()) == 1


@pytest.mark.asyncio
async def test_success_cache_is_bounded_to_eight_environment_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(shadow_repo, "_IS_DARWIN", True, raising=False)
    monkeypatch.setattr(shutil, "which", lambda name, path=None: "/usr/bin/git")
    monkeypatch.setenv("PATH", "/usr/bin")
    git_log = tmp_path / "git.jsonl"
    discovered_git = _fake_git(tmp_path / "developer-git", git_log)
    discoveries: list[Path] = []

    async def discover_git(cwd: Path, environment: dict[str, str]) -> str:
        discoveries.append(cwd)
        return str(discovered_git)

    monkeypatch.setattr(shadow_repo, "_discover_git", discover_git)

    for index in range(shadow_repo._GIT_EXECUTABLE_CACHE_LIMIT + 1):
        monkeypatch.setenv("DEVELOPER_DIR", f"/Developer/{index}")
        result = await shadow_repo._run_git(["--version"], tmp_path)
        assert result == (0, "fixture-git\n", "")
    assert len(shadow_repo._GIT_EXECUTABLE_CACHE) == 8

    monkeypatch.setenv("DEVELOPER_DIR", "/Developer/0")
    result = await shadow_repo._run_git(["--version"], tmp_path)

    assert result == (0, "fixture-git\n", "")
    assert len(discoveries) == 10


@pytest.mark.asyncio
async def test_real_shadow_checkpoint_preserves_user_git_and_size_guard(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "source.txt"
    target.write_text("initial\n", encoding="utf-8")

    def user_git(*args: str) -> str:
        return subprocess.run(
            ["git", *args],
            cwd=workspace,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    user_git("init", "--quiet")
    user_git("config", "user.name", "Workspace Owner")
    user_git("config", "user.email", "owner@example.test")
    user_git("add", "source.txt")
    user_git("commit", "--quiet", "-m", "user baseline")
    user_head = user_git("rev-parse", "HEAD")
    user_index = user_git("ls-files", "--stage")
    user_config = (
        user_git("config", "--local", "--get", "user.name"),
        user_git("config", "--local", "--get", "user.email"),
    )
    user_exclude = (workspace / ".git" / "info" / "exclude").read_bytes()

    repo = shadow_repo.ShadowRepo(workspace)
    first = await repo.snapshot_with_status("first")
    assert first.created and first.sha and re.fullmatch(r"[0-9a-f]{40}", first.sha)
    target.write_text("changed\n", encoding="utf-8")
    extra = workspace / "new.txt"
    extra.write_text("new file\n", encoding="utf-8")

    oversized = await repo.snapshot_with_status("guarded", max_bytes=1)
    assert not oversized.created
    assert oversized.sha is None
    assert oversized.skipped_reason == "workspace exceeds the 1-byte checkpoint limit"
    assert await repo.head() == first.sha

    second = await repo.snapshot_with_status("second")
    assert second.created and second.sha and second.sha != first.sha
    assert second.diff and "changed" in second.diff
    assert await repo.restore(first.sha) == f"Restored workspace to checkpoint '{first.sha}'."
    assert target.read_text(encoding="utf-8") == "initial\n"
    assert not extra.exists()

    assert user_git("rev-parse", "HEAD") == user_head
    assert user_git("ls-files", "--stage") == user_index
    assert (
        user_git("config", "--local", "--get", "user.name"),
        user_git("config", "--local", "--get", "user.email"),
    ) == user_config
    assert (workspace / ".git" / "info" / "exclude").read_bytes() == user_exclude
    assert subprocess.run(
        ["git", "--git-dir", str(repo.git_dir), "config", "--get", "user.name"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip() == "Harness Checkpoint"
    assert subprocess.run(
        ["git", "--git-dir", str(repo.git_dir), "config", "--get", "user.email"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip() == "checkpoints@harness.internal"


def _python_executable(path: Path, source: str) -> Path:
    path.write_text(f"#!{sys.executable}\n{source}", encoding="utf-8")
    path.chmod(0o755)
    return path


def _route_discovery_child(
    path: Path,
    *,
    nonce: str,
    report: Path,
    ready: Path,
    release_fifo: Path,
    developer_a: str,
    developer_b: str,
    git_a: Path,
    git_b: Path,
) -> Path:
    source = (
        "#!/bin/sh\n"
        "[ \"$#\" -eq 2 ] && [ \"$1\" = '--find' ] && [ \"$2\" = 'git' ] || exit 91\n"
        f"[ \"$G1_TEST_NONCE\" = {shlex.quote(nonce)} ] || exit 93\n"
        f"printf '%s\\n' \"$PATH\" \"$DEVELOPER_DIR\" \"$G1_TEST_NONCE\" \"$G1_TEST_MARKER\" > {shlex.quote(str(report))}\n"
        f"printf '%s' \"$G1_TEST_NONCE\" > {shlex.quote(str(ready))}\n"
        f"IFS= read -r _ < {shlex.quote(str(release_fifo))}\n"
        f"if [ \"$DEVELOPER_DIR\" = {shlex.quote(developer_a)} ]; then\n"
        f"  printf '%s\\n' {shlex.quote(str(git_a))}\n"
        f"elif [ \"$DEVELOPER_DIR\" = {shlex.quote(developer_b)} ]; then\n"
        f"  printf '%s\\n' {shlex.quote(str(git_b))}\n"
        "else\n"
        "  exit 92\n"
        "fi\n"
    )
    return _executable(path, source.removeprefix("#!/bin/sh\n"))


def _controlled_route_environment(
    *,
    path: str,
    developer_dir: str,
    nonce: str,
) -> dict[str, str]:
    return {
        "PATH": path,
        "DEVELOPER_DIR": developer_dir,
        "G1_TEST_MARKER": f"known-marker-{nonce}",
        "G1_TEST_NONCE": nonce,
    }


def _set_route(
    monkeypatch: pytest.MonkeyPatch,
    controlled: dict[str, str],
    *,
    path: str,
    developer_dir: str,
) -> None:
    monkeypatch.setenv("PATH", path)
    monkeypatch.setenv("DEVELOPER_DIR", developer_dir)
    controlled["PATH"] = path
    controlled["DEVELOPER_DIR"] = developer_dir


async def _wait_for_nonce(path: Path, nonce: str) -> None:
    async with asyncio.timeout(3):
        while not path.exists():
            await asyncio.sleep(0.005)
    assert path.read_text(encoding="ascii") == nonce


async def _release_fifo(path: Path, nonce: str) -> None:
    async with asyncio.timeout(3):
        while True:
            try:
                descriptor = os.open(path, os.O_WRONLY | os.O_NONBLOCK)
            except OSError as error:
                if error.errno != errno.ENXIO:
                    raise
                await asyncio.sleep(0.005)
                continue
            try:
                os.write(descriptor, nonce.encode("ascii"))
            finally:
                os.close(descriptor)
            return


def _install_gated_child_launcher(
    monkeypatch: pytest.MonkeyPatch,
    *,
    xcrun: Path,
    controlled: dict[str, str],
    readiness: tuple[Path, str, asyncio.Event] | None = None,
) -> tuple[asyncio.Event, asyncio.Event, list[asyncio.subprocess.Process], list[dict[str, str | None]]]:
    launch_requested = asyncio.Event()
    permit_launch = asyncio.Event()
    children: list[asyncio.subprocess.Process] = []
    launched_routes: list[dict[str, str | None]] = []
    create_subprocess = asyncio.create_subprocess_exec

    async def controlled_create_subprocess(*args: object, **kwargs: object) -> asyncio.subprocess.Process:
        is_discovery = os.fspath(args[0]) == str(xcrun)
        if is_discovery:
            launch_requested.set()
            await permit_launch.wait()
        child_env = kwargs.get("env")
        if child_env is None:
            child_env = dict(controlled)
            kwargs["env"] = child_env
        if is_discovery:
            launched_routes.append({
                key: child_env.get(key)
                for key in ("PATH", "DEVELOPER_DIR", "G1_TEST_NONCE", "G1_TEST_MARKER")
            })
        child = await create_subprocess(*args, **kwargs)
        if is_discovery:
            children.append(child)
            if readiness is not None:
                ready_path, nonce, spawn_returned = readiness
                try:
                    await _wait_for_nonce(ready_path, nonce)
                except BaseException:
                    if child.returncode is None:
                        child.kill()
                        await child.wait()
                    raise
                spawn_returned.set()
        return child

    monkeypatch.setattr(asyncio, "create_subprocess_exec", controlled_create_subprocess)
    return launch_requested, permit_launch, children, launched_routes


def _route_selector(path_a: str, path_b: str, git_b: Path):
    def which(name: str, path: str | None = None) -> str | None:
        if name != "git":
            return None
        if path == path_a:
            return "/usr/bin/git"
        if path == path_b:
            return str(git_b)
        return None

    return which


@pytest.mark.asyncio
async def test_route_change_during_discovery_uses_captured_child_env_then_falls_back_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    nonce = uuid.uuid4().hex
    path_a = str(tmp_path / "system-route")
    path_b_dir = tmp_path / "custom-route"
    path_b_dir.mkdir()
    path_b = str(path_b_dir)
    developer_a = f"test-developer-A-{nonce}"
    developer_b = f"test-developer-B-{nonce}"
    git_a = _executable(tmp_path / "git-a", "printf 'route-A\\n'\n")
    git_b = _executable(path_b_dir / "git", "printf 'route-B\\n'\n")
    report = tmp_path / "child-route.txt"
    ready = tmp_path / "child-ready"
    release_fifo = tmp_path / "child-release.fifo"
    os.mkfifo(release_fifo)
    controlled = _controlled_route_environment(
        path=path_a, developer_dir=developer_a, nonce=nonce
    )
    xcrun = _route_discovery_child(
        tmp_path / "xcrun", nonce=nonce, report=report, ready=ready,
        release_fifo=release_fifo, developer_a=developer_a,
        developer_b=developer_b, git_a=git_a, git_b=git_b,
    )
    monkeypatch.setattr(shadow_repo, "_IS_DARWIN", True, raising=False)
    monkeypatch.setattr(shadow_repo, "_XCRUN_PATH", str(xcrun), raising=False)
    monkeypatch.setattr(shadow_repo, "_capture_git_environment", lambda: dict(controlled), raising=False)
    monkeypatch.setattr(shutil, "which", _route_selector(path_a, path_b, git_b))
    monkeypatch.setenv("PATH", path_a)
    monkeypatch.setenv("DEVELOPER_DIR", developer_a)
    launch_requested, permit_launch, children, launched_routes = _install_gated_child_launcher(
        monkeypatch, xcrun=xcrun, controlled=controlled
    )
    task = asyncio.create_task(shadow_repo._run_git(["--version"], tmp_path))
    try:
        await asyncio.wait_for(launch_requested.wait(), timeout=3)
        _set_route(monkeypatch, controlled, path=path_b, developer_dir=developer_b)
        permit_launch.set()
        await _wait_for_nonce(ready, nonce)
        child_report = report.read_text(encoding="utf-8").splitlines()
        await _release_fifo(release_fifo, nonce)
        result = await task

        assert child_report == [path_a, developer_a, nonce, f"known-marker-{nonce}"]
        assert launched_routes == [{
            "PATH": path_a,
            "DEVELOPER_DIR": developer_a,
            "G1_TEST_NONCE": nonce,
            "G1_TEST_MARKER": f"known-marker-{nonce}",
        }]
        assert result == (0, "route-B\n", "")
        assert not shadow_repo._GIT_EXECUTABLE_CACHE
        assert children[0].returncode == 0
    finally:
        if not task.done():
            await _release_fifo(release_fifo, nonce)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        for child in children:
            if child.returncode is None:
                child.kill()
                await child.wait()


@pytest.mark.asyncio
async def test_route_a_to_b_to_a_cannot_cache_b_child_as_route_a(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    nonce = uuid.uuid4().hex
    path_a = str(tmp_path / "system-route")
    path_b_dir = tmp_path / "custom-route"
    path_b_dir.mkdir()
    path_b = str(path_b_dir)
    developer_a = f"test-developer-A-{nonce}"
    developer_b = f"test-developer-B-{nonce}"
    git_a = _executable(tmp_path / "git-a", "printf 'route-A\\n'\n")
    git_b = _executable(path_b_dir / "git", "printf 'route-B\\n'\n")
    report = tmp_path / "child-route.txt"
    ready = tmp_path / "child-ready"
    release_fifo = tmp_path / "child-release.fifo"
    os.mkfifo(release_fifo)
    controlled = _controlled_route_environment(
        path=path_a, developer_dir=developer_a, nonce=nonce
    )
    xcrun = _route_discovery_child(
        tmp_path / "xcrun", nonce=nonce, report=report, ready=ready,
        release_fifo=release_fifo, developer_a=developer_a,
        developer_b=developer_b, git_a=git_a, git_b=git_b,
    )
    monkeypatch.setattr(shadow_repo, "_IS_DARWIN", True, raising=False)
    monkeypatch.setattr(shadow_repo, "_XCRUN_PATH", str(xcrun), raising=False)
    monkeypatch.setattr(shadow_repo, "_capture_git_environment", lambda: dict(controlled), raising=False)
    monkeypatch.setattr(shutil, "which", _route_selector(path_a, path_b, git_b))
    monkeypatch.setenv("PATH", path_a)
    monkeypatch.setenv("DEVELOPER_DIR", developer_a)
    launch_requested, permit_launch, children, launched_routes = _install_gated_child_launcher(
        monkeypatch, xcrun=xcrun, controlled=controlled
    )
    task = asyncio.create_task(shadow_repo._run_git(["--version"], tmp_path))
    try:
        await asyncio.wait_for(launch_requested.wait(), timeout=3)
        _set_route(monkeypatch, controlled, path=path_b, developer_dir=developer_b)
        permit_launch.set()
        await _wait_for_nonce(ready, nonce)
        _set_route(monkeypatch, controlled, path=path_a, developer_dir=developer_a)
        child_report = report.read_text(encoding="utf-8").splitlines()
        await _release_fifo(release_fifo, nonce)
        first = await task

        assert child_report == [path_a, developer_a, nonce, f"known-marker-{nonce}"]
        assert first == (0, "route-A\n", "")
        assert shadow_repo._GIT_EXECUTABLE_CACHE[(path_a, developer_a)] == str(git_a)
        assert launched_routes == [{
            "PATH": path_a,
            "DEVELOPER_DIR": developer_a,
            "G1_TEST_NONCE": nonce,
            "G1_TEST_MARKER": f"known-marker-{nonce}",
        }]
        second = await shadow_repo._run_git(["--version"], tmp_path)
        assert second == (0, "route-A\n", "")
        assert len(children) == 1
    finally:
        if not task.done():
            await _release_fifo(release_fifo, nonce)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        for child in children:
            if child.returncode is None:
                child.kill()
                await child.wait()


def _term_ignoring_python_child(
    path: Path, *, nonce: str, ready: Path, terminated: Path,
    handlers_installed: Path, ready_write_started: Path,
) -> Path:
    source = (
        "import os, select, signal\n"
        "from pathlib import Path\n"
        f"nonce = {nonce!r}\n"
        "def publish(path, content):\n"
        "    temporary = path.with_name(f'.{path.name}.{os.getpid()}.tmp')\n"
        "    temporary.write_text(content, encoding='ascii')\n"
        "    os.replace(temporary, path)\n"
        "def ignore_term(signum, frame):\n"
        "    publish(Path(os.environ['G1_TEST_TERMINATED']), nonce)\n"
        "previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGUSR1})\n"
        "signal.signal(signal.SIGTERM, ignore_term)\n"
        "signal.signal(signal.SIGUSR1, lambda signum, frame: None)\n"
        "try:\n"
        "    publish(Path(os.environ['G1_TEST_HANDLERS_INSTALLED']), nonce)\n"
        "    ready_path = Path(os.environ['G1_TEST_READY'])\n"
        "    ready_temp = ready_path.with_name(f'.{ready_path.name}.{os.getpid()}.tmp')\n"
        "    fifo_fd = os.open(os.environ['G1_TEST_READY_RELEASE_FIFO'], os.O_RDONLY | os.O_NONBLOCK)\n"
        "    try:\n"
        "        with ready_temp.open('w', encoding='ascii') as output:\n"
        "            publish(Path(os.environ['G1_TEST_READY_WRITE_STARTED']), nonce)\n"
        "            select.select([fifo_fd], [], [])\n"
        "            fifo_token = os.read(fifo_fd, len(nonce) + 1).decode('ascii')\n"
        "            assert fifo_token == nonce\n"
        "            publish(Path(os.environ['G1_TEST_FIFO_CONSUMED']), nonce)\n"
        "            released_signal = signal.sigwait({signal.SIGUSR1})\n"
        "            assert released_signal == signal.SIGUSR1\n"
        "            publish(Path(os.environ['G1_TEST_READY_SIGNAL_CONSUMED']), nonce)\n"
        "            output.write(nonce)\n"
        "        os.replace(ready_temp, ready_path)\n"
        "    finally:\n"
        "        os.close(fifo_fd)\n"
        "finally:\n"
        "    signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)\n"
        "while True:\n"
        "    signal.pause()\n"
    )
    return _python_executable(path, source)


@pytest.mark.asyncio
async def test_ready_signal_after_fifo_release_is_consumed_before_atomic_publication(
    tmp_path: Path,
) -> None:
    nonce = uuid.uuid4().hex
    ready = tmp_path / "child-ready"
    terminated = tmp_path / "child-terminated"
    handlers_installed = tmp_path / "child-handlers-installed"
    ready_write_started = tmp_path / "child-ready-write-started"
    release_fifo = tmp_path / "child-ready-release.fifo"
    fifo_consumed = tmp_path / "child-ready-fifo-consumed"
    ready_signal_consumed = tmp_path / "child-ready-signal-consumed"
    os.mkfifo(release_fifo)
    controlled = {
        "PATH": str(tmp_path / "system-route"),
        "DEVELOPER_DIR": f"test-developer-{nonce}",
        "G1_TEST_NONCE": nonce,
        "G1_TEST_MARKER": f"known-marker-{nonce}",
        "G1_TEST_READY": str(ready),
        "G1_TEST_TERMINATED": str(terminated),
        "G1_TEST_HANDLERS_INSTALLED": str(handlers_installed),
        "G1_TEST_READY_WRITE_STARTED": str(ready_write_started),
        "G1_TEST_READY_RELEASE_FIFO": str(release_fifo),
        "G1_TEST_FIFO_CONSUMED": str(fifo_consumed),
        "G1_TEST_READY_SIGNAL_CONSUMED": str(ready_signal_consumed),
    }
    xcrun = _term_ignoring_python_child(
        tmp_path / "xcrun", nonce=nonce, ready=ready, terminated=terminated,
        handlers_installed=handlers_installed, ready_write_started=ready_write_started,
    )
    child = await asyncio.create_subprocess_exec(str(xcrun), cwd=tmp_path, env=controlled)
    try:
        await _wait_for_nonce(handlers_installed, nonce)
        await _wait_for_nonce(ready_write_started, nonce)
        ready_temp = ready.with_name(f".{ready.name}.{child.pid}.tmp")
        assert ready_temp.exists()
        assert ready_temp.read_text(encoding="ascii") == ""
        assert not ready.exists()
        await _release_fifo(release_fifo, nonce)
        await _wait_for_nonce(fifo_consumed, nonce)
        assert not ready.exists()
        child.send_signal(signal.SIGUSR1)
        await _wait_for_nonce(ready_signal_consumed, nonce)
        await _wait_for_nonce(ready, nonce)
        assert child.returncode is None
    finally:
        if child.returncode is None:
            child.kill()
            await child.wait()


@pytest.mark.asyncio
async def test_repeated_cancellation_waits_for_owned_child_kill_and_reap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    nonce = uuid.uuid4().hex
    path_a = str(tmp_path / "system-route")
    developer_a = f"test-developer-A-{nonce}"
    ready = tmp_path / "child-ready"
    terminated = tmp_path / "child-terminated"
    handlers_installed = tmp_path / "child-handlers-installed"
    ready_write_started = tmp_path / "child-ready-write-started"
    release_fifo = tmp_path / "child-ready-release.fifo"
    fifo_consumed = tmp_path / "child-ready-fifo-consumed"
    ready_signal_consumed = tmp_path / "child-ready-signal-consumed"
    os.mkfifo(release_fifo)
    controlled = {
        "PATH": path_a,
        "DEVELOPER_DIR": developer_a,
        "G1_TEST_NONCE": nonce,
        "G1_TEST_MARKER": f"known-marker-{nonce}",
        "G1_TEST_READY": str(ready),
        "G1_TEST_TERMINATED": str(terminated),
        "G1_TEST_HANDLERS_INSTALLED": str(handlers_installed),
        "G1_TEST_READY_WRITE_STARTED": str(ready_write_started),
        "G1_TEST_READY_RELEASE_FIFO": str(release_fifo),
        "G1_TEST_FIFO_CONSUMED": str(fifo_consumed),
        "G1_TEST_READY_SIGNAL_CONSUMED": str(ready_signal_consumed),
    }
    xcrun = _term_ignoring_python_child(
        tmp_path / "xcrun", nonce=nonce, ready=ready, terminated=terminated,
        handlers_installed=handlers_installed, ready_write_started=ready_write_started,
    )
    monkeypatch.setattr(shadow_repo, "_IS_DARWIN", True, raising=False)
    monkeypatch.setattr(shadow_repo, "_XCRUN_PATH", str(xcrun), raising=False)
    monkeypatch.setattr(shadow_repo, "_capture_git_environment", lambda: dict(controlled), raising=False)
    monkeypatch.setattr(shutil, "which", lambda name, path=None: "/usr/bin/git")
    monkeypatch.setenv("PATH", path_a)
    monkeypatch.setenv("DEVELOPER_DIR", developer_a)
    spawn_returned = asyncio.Event()
    launch_requested, permit_launch, children, _ = _install_gated_child_launcher(
        monkeypatch, xcrun=xcrun, controlled=controlled,
        readiness=(ready, nonce, spawn_returned),
    )
    permit_launch.set()
    create_task = asyncio.create_task
    task = create_task(shadow_repo._run_git(["--version"], tmp_path))
    created_tasks: list[asyncio.Task[object]] = []

    def track_task(coro: object, *args: object, **kwargs: object) -> asyncio.Task[object]:
        child_task = create_task(coro, *args, **kwargs)
        created_tasks.append(child_task)
        return child_task

    monkeypatch.setattr(asyncio, "create_task", track_task)
    try:
        await asyncio.wait_for(launch_requested.wait(), timeout=3)
        await _wait_for_nonce(handlers_installed, nonce)
        assert not ready.exists()
        await _wait_for_nonce(ready_write_started, nonce)
        ready_temp = ready.with_name(f".{ready.name}.{children[0].pid}.tmp")
        assert ready_temp.exists()
        assert ready_temp.read_text(encoding="ascii") == ""
        assert not ready.exists()
        children[0].send_signal(signal.SIGUSR1)
        assert not ready.exists()
        await _release_fifo(release_fifo, nonce)
        await _wait_for_nonce(fifo_consumed, nonce)
        await _wait_for_nonce(ready_signal_consumed, nonce)
        await _wait_for_nonce(ready, nonce)
        await asyncio.wait_for(spawn_returned.wait(), timeout=3)
        task.cancel()
        await _wait_for_nonce(terminated, nonce)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert children[0].returncode is not None
        assert all(child_task.done() for child_task in created_tasks)
        assert not shadow_repo._GIT_EXECUTABLE_CACHE
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        for child in children:
            if child.returncode is None:
                child.kill()
                await child.wait()
        if created_tasks:
            await asyncio.gather(*created_tasks, return_exceptions=True)
