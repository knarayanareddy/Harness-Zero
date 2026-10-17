"""WS-06b: a setting is not confinement; the host probe, policy and executor result agree about the boundary.

The fake backend exercises all decisions on every CI host. Linux's real bwrap test is deliberately conditional on
the backend's namespace probe (not on `which bwrap`): a present binary with disabled user namespaces is unavailable,
not evidence that the filesystem has been confined.
"""
from __future__ import annotations

import asyncio
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from harness.kernel.inbox import BackgroundInbox
from harness.kernel.sandbox import SandboxedProcess, SandboxService, SandboxUnavailableError
from harness.kernel.settings import SettingsStore
from harness.plugins.executors.local.executor import LocalExecutor
from harness.plugins.executors.sandbox_bwrap.backend import BwrapBackend
from harness.plugins.policy.permission_modes.policy import PermissionModesPolicy
from harness.sdk.sandbox_seatbelt import SeatbeltBackend
from harness.sdk.types import (
    Action,
    ExecRequest,
    ExecResult,
    RiskClass,
    SandboxAvailability,
    SandboxPolicy,
    ToolCall,
    ToolSpec,
)


class FakeProcess:
    id = "fake-process"
    pid = 101

    def __init__(self, exit_code: int = 0):
        self._expected_exit_code = exit_code
        self.returncode: int | None = None

    async def output(self):
        if False:
            yield "stdout", ""

    async def send_input(self, text: str) -> None:
        return None

    async def kill(self, grace_s: float = 2.0) -> None:
        return None

    async def wait(self) -> ExecResult:
        self.returncode = self._expected_exit_code
        return ExecResult(exit_code=self._expected_exit_code, stdout="ran", stderr="", truncated=False, duration_ms=1)


class RecordingExecutor:
    name = "recording"
    isolation = "none"

    def __init__(self):
        self.requests: list[ExecRequest] = []

    async def start(self, req: ExecRequest) -> FakeProcess:
        self.requests.append(req)
        return FakeProcess()


class FakeBackend:
    name = "bwrap"

    def __init__(self, *, available: bool):
        self._available = available
        self.requests: list[tuple[ExecRequest, SandboxPolicy]] = []

    def available(self) -> SandboxAvailability:
        return SandboxAvailability(
            backend=self.name,
            available=self._available,
            reason="fake probe passed" if self._available else "fake probe blocked",
            enforcement="full" if self._available else "none",
        )

    def wrap(self, req: ExecRequest, policy: SandboxPolicy) -> ExecRequest:
        self.requests.append((req, policy))
        return replace(req, command=["sandbox-wrapper", "--", str(req.command)], shell=False)


EXEC_SPEC = ToolSpec(
    name="run_command",
    description="test executor tool",
    input_schema={"type": "object"},
    risk_class=RiskClass.EXEC,
    capabilities=frozenset({"exec"}),
)
EXEC_CALL = ToolCall(id="c1", name="run_command", arguments={"command": "true"})
POLICY_CONTEXT = SimpleNamespace(workspace=Path("."), capabilities=frozenset())


def settings_at(tmp_path: Path, preset: str = "careful") -> SettingsStore:
    settings = SettingsStore(tmp_path / "settings.json")
    settings.set("kernel.security.preset", preset)
    return settings


def service_for(settings: SettingsStore, workspace: Path, backend: FakeBackend | None) -> SandboxService:
    service = SandboxService(settings, workspace)
    if backend is not None:
        service.register_backend(backend, "test-sandbox")
    return service


def test_invalid_extra_readable_symlink_is_reported_unavailable(tmp_path: Path):
    a = tmp_path / "loop-a"
    b = tmp_path / "loop-b"
    a.symlink_to(b)
    b.symlink_to(a)
    settings = settings_at(tmp_path, "careful")
    settings.set("kernel.sandbox.extra_readable", [str(a)])
    settings.set("kernel.sandbox.backend", "bwrap")
    service = service_for(settings, tmp_path, FakeBackend(available=True))

    status = service.status()
    assert status["available"] is False
    assert status["enforcement"] == "none"
    assert "cannot be resolved" in status["reason"]


def test_invalid_backend_availability_is_normalized_to_fail_closed(tmp_path: Path):
    class InvalidAvailabilityBackend(FakeBackend):
        def available(self):
            return None

    settings = settings_at(tmp_path, "careful")
    settings.set("kernel.sandbox.backend", "bwrap")
    service = service_for(settings, tmp_path, InvalidAvailabilityBackend(available=True))

    status = service.status()
    assert status["available"] is False
    assert status["enforcement"] == "none"
    assert "invalid availability result" in status["reason"]


def test_malformed_availability_fields_are_normalized_to_fail_closed(tmp_path: Path):
    class MalformedAvailabilityBackend(FakeBackend):
        def available(self):
            return SandboxAvailability(
                backend=self.name,
                available="yes",  # type: ignore[arg-type]
                reason="the backend supplied a non-boolean availability fact",
                enforcement="full",
            )

    settings = settings_at(tmp_path, "careful")
    settings.set("kernel.sandbox.backend", "bwrap")
    service = service_for(settings, tmp_path, MalformedAvailabilityBackend(available=True))

    status = service.status()
    assert status["available"] is False
    assert status["enforcement"] == "none"
    assert "malformed availability facts" in status["reason"]


def test_partial_backend_reason_is_preserved_without_extra_readable_paths(tmp_path: Path):
    class PartialBackend(FakeBackend):
        name = "seatbelt"

        def available(self):
            return SandboxAvailability(
                backend=self.name,
                available=True,
                reason="Seatbelt profile accepted by this host",
                enforcement="partial",
            )

    settings = settings_at(tmp_path, "careful")
    settings.set("kernel.sandbox.backend", "seatbelt")
    status = service_for(settings, tmp_path, PartialBackend(available=True)).status()

    assert status["available"] is True
    assert status["enforcement"] == "partial"
    assert status["reason"] == "Seatbelt profile accepted by this host"


def test_extra_readable_path_downgrades_full_enforcement_truthfully(tmp_path: Path):
    readable = tmp_path / "sdk"
    readable.mkdir()
    settings = settings_at(tmp_path, "careful")
    settings.set("kernel.sandbox.backend", "bwrap")
    settings.set("kernel.sandbox.extra_readable", [str(readable)])
    status = service_for(settings, tmp_path, FakeBackend(available=True)).status()

    assert status["available"] is True
    assert status["enforcement"] == "partial"
    assert status["reason"] == "Extra readable paths are exposed; review kernel.sandbox.extra_readable."


@pytest.mark.asyncio
async def test_required_unavailable_denies_even_bypass_before_executor_start(tmp_path: Path):
    settings = settings_at(tmp_path, "open")
    settings.set("kernel.security.permission_mode", "bypass")
    settings.set("kernel.sandbox.mode", "required")
    settings.set("kernel.sandbox.backend", "none")
    service = service_for(settings, tmp_path, None)
    decision = await PermissionModesPolicy(settings, service).evaluate(
        EXEC_CALL, EXEC_SPEC, POLICY_CONTEXT
    )

    assert decision.action == Action.DENY
    assert "sandbox unavailable on this host" in decision.reason
    assert decision.metadata["sandbox"]["available"] is False

    delegate = RecordingExecutor()
    wrapped = service.wrap_executor(delegate, allow_unconfined=True)
    with pytest.raises(SandboxUnavailableError, match="Required OS sandbox unavailable"):
        await wrapped.start(ExecRequest(command="true", cwd=tmp_path))
    assert delegate.requests == [], "required mode must not start the host executor even under bypass"


@pytest.mark.asyncio
async def test_preferred_unavailable_asks_before_unconfined_execution(tmp_path: Path):
    settings = settings_at(tmp_path, "open")
    settings.set("kernel.security.permission_mode", "allow")
    settings.set("kernel.sandbox.mode", "preferred")
    settings.set("kernel.sandbox.backend", "none")
    service = service_for(settings, tmp_path, None)

    decision = await PermissionModesPolicy(settings, service).evaluate(
        EXEC_CALL, EXEC_SPEC, POLICY_CONTEXT
    )
    assert decision.action == Action.ASK
    assert "approve unconfined execution" in decision.reason

    delegate = RecordingExecutor()
    with pytest.raises(SandboxUnavailableError, match="requires explicit approval"):
        await service.wrap_executor(delegate).start(ExecRequest(command="true", cwd=tmp_path))
    assert delegate.requests == []

    # A real approval from the gate permits the fallback, but the result names it `none`, not `full`.
    process = await service.wrap_executor(delegate, allow_unconfined=True).start(
        ExecRequest(command="true", cwd=tmp_path)
    )
    result = await process.wait()
    assert result.metadata["sandbox"]["available"] is False
    assert result.metadata["sandbox"]["enforcement"] == "none"
    assert len(delegate.requests) == 1


@pytest.mark.asyncio
async def test_standard_allows_exec_only_after_backend_probe_succeeds(tmp_path: Path):
    settings = settings_at(tmp_path, "standard")
    settings.set("kernel.sandbox.backend", "bwrap")
    service = service_for(settings, tmp_path, FakeBackend(available=True))
    decision = await PermissionModesPolicy(settings, service).evaluate(
        EXEC_CALL, EXEC_SPEC, POLICY_CONTEXT
    )

    assert decision.action == Action.ALLOW
    assert decision.metadata["sandbox"]["available"] is True
    assert decision.metadata["sandbox"]["enforcement"] == "full"


@pytest.mark.asyncio
async def test_standard_does_not_auto_allow_write_tools_that_use_an_executor(tmp_path: Path):
    settings = settings_at(tmp_path, "standard")
    settings.set("kernel.sandbox.backend", "bwrap")
    service = service_for(settings, tmp_path, FakeBackend(available=True))
    write_spec = ToolSpec(
        name="write_via_executor",
        description="a write-risk tool that delegates to a process",
        input_schema={"type": "object"},
        risk_class=RiskClass.WRITE,
        capabilities=frozenset({"exec"}),
    )

    decision = await PermissionModesPolicy(settings, service).evaluate(
        ToolCall(id="c-write", name=write_spec.name, arguments={}), write_spec, POLICY_CONTEXT
    )

    assert decision.action == Action.ASK, "standard only auto-allows EXEC, never a WRITE action"


@pytest.mark.asyncio
async def test_careful_still_asks_even_when_backend_is_available(tmp_path: Path):
    settings = settings_at(tmp_path, "careful")
    settings.set("kernel.sandbox.backend", "bwrap")
    service = service_for(settings, tmp_path, FakeBackend(available=True))
    decision = await PermissionModesPolicy(settings, service).evaluate(
        EXEC_CALL, EXEC_SPEC, POLICY_CONTEXT
    )
    assert decision.action == Action.ASK
    assert decision.metadata["sandbox"]["available"] is True


@pytest.mark.asyncio
async def test_off_mode_runs_but_result_still_reports_no_confinement(tmp_path: Path):
    settings = settings_at(tmp_path, "open")
    service = service_for(settings, tmp_path, None)
    delegate = RecordingExecutor()
    process = await service.wrap_executor(delegate).start(ExecRequest(command="true", cwd=tmp_path))
    result = await process.wait()

    assert result.metadata["sandbox"]["mode"] == "off"
    assert result.metadata["sandbox"]["enforcement"] == "none"
    assert result.metadata["sandbox"]["network"] is True
    assert len(delegate.requests) == 1


@pytest.mark.asyncio
async def test_background_task_reports_exec_result_exit_code_and_sandbox_metadata():
    sandbox_fact = {
        "mode": "required",
        "backend": "bwrap",
        "available": True,
        "enforcement": "full",
        "reason": "",
        "network": False,
    }
    process = SandboxedProcess(FakeProcess(exit_code=7), sandbox_fact)
    inbox = BackgroundInbox()
    inbox.register_task("task_nonzero", process, label="failing command")

    assert await inbox.wait_task("task_nonzero") == 7
    status = inbox.get_status("task_nonzero")
    assert status is not None
    assert status["exit_code"] == 7
    assert status["sandbox"] == sandbox_fact
    assert process.exit_code == process.returncode == 7


def test_seatbelt_profile_is_workspace_scoped_and_network_off_by_default(tmp_path: Path, monkeypatch):
    import subprocess

    monkeypatch.setattr("harness.sdk.sandbox_seatbelt.platform.system", lambda: "Darwin")
    monkeypatch.setattr("harness.sdk.sandbox_seatbelt.shutil.which", lambda _name: "/usr/bin/sandbox-exec")
    monkeypatch.setattr(
        "harness.sdk.sandbox_seatbelt.subprocess.run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 0, stdout="", stderr=""),
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    backend = SeatbeltBackend()
    availability = backend.available()
    assert availability.available is True
    assert availability.enforcement == "partial"

    wrapped = backend.wrap(
        ExecRequest(command="touch inside.txt", cwd=workspace, shell=True),
        SandboxPolicy(workspace=workspace, writable=(workspace,), network=False),
    )

    assert isinstance(wrapped.command, list)
    assert wrapped.command[0] == "/usr/bin/sandbox-exec"
    profile = wrapped.command[2]
    assert "(deny network*)" in profile
    assert f'(subpath "{workspace}")' in profile
    assert f'(allow file-write* (subpath "{workspace}") (subpath "/private/tmp"))' in profile
    assert wrapped.command[-3:] == ["/bin/sh", "-c", "touch inside.txt"]
    assert "HOME=/private/tmp" in wrapped.command
    assert wrapped.shell is False


def test_bwrap_builder_is_argv_based_workspace_only_and_network_off_by_default(tmp_path: Path, monkeypatch):
    import subprocess

    monkeypatch.setattr("harness.sdk.sandbox_bwrap.platform.system", lambda: "Linux")
    monkeypatch.setattr("harness.sdk.sandbox_bwrap.shutil.which", lambda _name: "/usr/bin/bwrap")
    monkeypatch.setattr(
        "harness.sdk.sandbox_bwrap.subprocess.run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 0, stdout="", stderr=""),
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    backend = BwrapBackend()
    assert backend.available().available

    req = ExecRequest(command="touch inside.txt", cwd=workspace, shell=True)
    wrapped = backend.wrap(req, SandboxPolicy(workspace=workspace, writable=(workspace,), network=False))

    argv = wrapped.command
    assert isinstance(argv, list) and argv[0] == "/usr/bin/bwrap"
    assert ["--bind", str(workspace), str(workspace)] == argv[argv.index("--bind"):argv.index("--bind") + 3]
    assert "--tmpfs" in argv and argv[argv.index("--tmpfs") + 1] == "/tmp"
    assert "--unshare-net" in argv
    assert "--clearenv" in argv and "--setenv" in argv
    assert argv[-4:] == ["--", "/bin/sh", "-c", "touch inside.txt"]
    assert "/home" not in argv, "host home directories are absent unless the user granted an extra readable path"
    assert wrapped.cwd == workspace and wrapped.shell is False


@pytest.mark.asyncio
async def test_bwrap_real_backend_blocks_host_file_and_records_full_enforcement(tmp_path: Path):
    if not sys.platform.startswith("linux"):
        pytest.skip("bubblewrap integration is Linux-only")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("not mounted into the sandbox", encoding="utf-8")
    settings = settings_at(tmp_path, "careful")
    settings.set("kernel.sandbox.mode", "required")
    settings.set("kernel.sandbox.backend", "bwrap")
    service = SandboxService(settings, workspace)
    backend = BwrapBackend()
    service.register_backend(backend, "test-sandbox")
    availability = service.availability()
    if not availability.available:
        pytest.skip(f"bubblewrap integration unavailable: {availability.reason}")

    executor = service.wrap_executor(LocalExecutor(default_cwd=workspace))
    req = ExecRequest(command=["/bin/cat", str(outside)], cwd=workspace, shell=False)
    proc = await executor.start(req)
    result = await proc.wait()

    assert result.exit_code != 0
    assert "not mounted into the sandbox" not in result.stdout
    assert result.metadata["sandbox"]["available"] is True
    assert result.metadata["sandbox"]["enforcement"] == "full"


@pytest.mark.asyncio
async def test_bwrap_real_backend_writes_workspace_but_not_host_tmp(tmp_path: Path):
    if not sys.platform.startswith("linux"):
        pytest.skip("bubblewrap integration is Linux-only")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    settings = settings_at(tmp_path, "careful")
    settings.set("kernel.sandbox.mode", "required")
    settings.set("kernel.sandbox.backend", "bwrap")
    service = SandboxService(settings, workspace)
    backend = BwrapBackend()
    service.register_backend(backend, "test-sandbox")
    availability = service.availability()
    if not availability.available:
        pytest.skip(f"bubblewrap integration unavailable: {availability.reason}")

    host_tmp_marker = tmp_path / "host-tmp-marker"
    executor = service.wrap_executor(LocalExecutor(default_cwd=workspace))
    req = ExecRequest(
        command=["/bin/sh", "-c", f"touch inside.txt; touch {host_tmp_marker}"],
        cwd=workspace,
        shell=False,
    )
    proc = await executor.start(req)
    result = await proc.wait()

    assert result.exit_code == 0, result.stderr
    assert (workspace / "inside.txt").exists()
    assert not host_tmp_marker.exists(), "sandbox /tmp is private; the host path is not a writable mount"


@pytest.mark.asyncio
async def test_bwrap_real_backend_unshares_loopback_network(tmp_path: Path):
    if not sys.platform.startswith("linux"):
        pytest.skip("bubblewrap integration is Linux-only")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    settings = settings_at(tmp_path, "careful")
    settings.set("kernel.sandbox.mode", "required")
    settings.set("kernel.sandbox.backend", "bwrap")
    service = SandboxService(settings, workspace)
    backend = BwrapBackend()
    service.register_backend(backend, "test-sandbox")
    availability = service.availability()
    if not availability.available:
        pytest.skip(f"bubblewrap integration unavailable: {availability.reason}")

    connected = asyncio.Event()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        connected.set()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    script = (
        "import socket,sys; s=socket.socket(); s.settimeout(1); "
        f"\ntry: s.connect(('127.0.0.1',{port})); sys.exit(9) "
        "\nexcept OSError: sys.exit(0)"
    )
    try:
        executor = service.wrap_executor(LocalExecutor(default_cwd=workspace))
        proc = await executor.start(
            ExecRequest(command=[sys.executable, "-c", script], cwd=workspace, shell=False)
        )
        result = await proc.wait()
        assert result.exit_code == 0, result.stderr
        assert not connected.is_set(), "the sandbox's private network namespace must not reach host loopback"
    finally:
        server.close()
        await server.wait_closed()
