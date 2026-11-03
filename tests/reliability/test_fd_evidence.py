"""Descriptor evidence controls; synthetic fixtures never qualify endurance."""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load_runner():
    spec = importlib.util.spec_from_file_location("qa32_qualify_release", ROOT / "scripts/qualify_release.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sample(pid: int = 123) -> bytes:
    return (
        f"p{pid}\0\n".encode()
        + b"fcwd\0tDIR\0n/fixture\0\n"
        + b"f3\0tREG\0D0x1\0i42\0n/fixture/memory.db\0\n"
    )


def test_parse_native_fields_preserves_identity_and_separates_pseudo_records():
    result = load_runner().parse_fd_lsof(sample(), expected_pid=123)
    assert result["pid"] == 123
    assert result["numeric_fd_count"] == 1
    assert result["pseudo_record_count"] == 1
    assert result["records"] == [
        {"descriptor": "cwd", "type": "DIR", "name": "/fixture", "device": None, "inode": None},
        {"descriptor": "3", "type": "REG", "name": "/fixture/memory.db", "device": "0x1", "inode": "42"},
    ]


def test_names_cannot_forge_record_boundaries_and_invalid_utf8_is_display_safe():
    raw = sample().replace(b"/fixture/memory.db", b"/fixture/line\nf999\xff")
    result = load_runner().parse_fd_lsof(raw, expected_pid=123)
    assert result["numeric_fd_count"] == 1
    assert result["records"][1]["name"] == "/fixture/line\nf999\ufffd"


def test_repeated_pseudo_records_are_not_duplicate_numeric_fds():
    raw = sample() + b"ftxt\0tREG\0n/fixture/lib\0\n" * 2
    result = load_runner().parse_fd_lsof(raw, expected_pid=123)
    assert result["numeric_fd_count"] == 1
    assert result["pseudo_record_count"] == 3


@pytest.mark.parametrize("raw", [
    b"", sample(124), sample()[:-1], sample().replace(b"\nfcwd", b"fcwd"),
    sample().replace(b"tREG\0", b"tREG\0tREG\0"),
    sample().replace(b"tREG\0", b""),
    sample() + b"p124\0\nf4\0tREG\0n/fixture/other\0\n",
    sample() + b"f03\0tREG\0n/fixture/duplicate\0\n",
    b"p123\0\nfcwd\0tDIR\0n/fixture\0\n",
])
def test_parser_rejects_incomplete_foreign_ambiguous_or_empty_numeric_evidence(raw):
    with pytest.raises(ValueError):
        load_runner().parse_fd_lsof(raw, expected_pid=123)


@pytest.mark.parametrize("pid", [True, 0, -1, "123", 123.0])
def test_parser_requires_a_real_positive_integer_pid(pid):
    with pytest.raises(ValueError):
        load_runner().parse_fd_lsof(sample(), expected_pid=pid)


def test_parser_rejects_byte_and_record_overflow_without_a_partial_table():
    module = load_runner()
    with pytest.raises(ValueError, match="oversized"):
        module.parse_fd_lsof(b"x" * (module.FD_RAW_BYTE_LIMIT + 1), expected_pid=123)
    with pytest.raises(ValueError, match="record limit"):
        module.parse_fd_lsof(sample() + b"ftxt\0tREG\0n/lib\0\n" * module.FD_RECORD_LIMIT, expected_pid=123)
    exact = sample() + b"ftxt\0tREG\0n/lib\0\n" * (module.FD_RECORD_LIMIT - 2)
    assert len(module.parse_fd_lsof(exact, expected_pid=123)["records"]) == module.FD_RECORD_LIMIT


def test_parser_accepts_the_actual_host_lsof_format():
    if sys.platform != "darwin":
        pytest.skip("actual macOS lsof descriptor capture is unverified on this platform")
    assert Path("/usr/sbin/lsof").is_file(), "native FD-evidence preparation requires lsof"
    result = subprocess.run(
        ["/usr/sbin/lsof", "-nP", "-p", str(os.getpid()), "-F0ftnDi"],
        capture_output=True, timeout=10, check=True,
    )
    table = load_runner().parse_fd_lsof(result.stdout, expected_pid=os.getpid())
    assert table["pid"] == os.getpid()
    assert table["numeric_fd_count"] >= 3
    assert table["pseudo_record_count"] >= 1


def capture(module, raw: bytes | None = None, **changes):
    kwargs = dict(expected_pid=123, phase="cadence", index=0,
                  started_monotonic_ns=10, finished_monotonic_ns=20,
                  observer_owned_fds=[2, 1])
    kwargs.update(changes)
    return module.encode_fd_capture(sample() if raw is None else raw, **kwargs)


def test_capture_encoding_is_canonical_lossless_and_revalidated():
    module = load_runner()
    raw = sample().replace(b"memory.db", b"line\nf999\xff")
    payload = capture(module, raw)
    value = json.loads(payload)
    assert value["schema"] == "t29.fd-evidence.v1"
    assert base64.b64decode(value["raw_lsof_base64"], validate=True) == raw
    assert value["raw_byte_count"] == len(raw)
    assert value["raw_sha256"] == hashlib.sha256(raw).hexdigest()
    assert value["observer_owned_fds"] == [1, 2]
    assert payload == (json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n").encode()
    assert module.decode_fd_capture(payload, expected_pid=123) == value


@pytest.mark.parametrize("changes", [
    {"phase": "unknown"}, {"index": None}, {"index": True}, {"index": 121},
    {"phase": "after_unload", "index": 0}, {"started_monotonic_ns": -1},
    {"finished_monotonic_ns": 9}, {"started_monotonic_ns": True},
    {"observer_owned_fds": [1, 1]}, {"observer_owned_fds": [-1]},
    {"observer_owned_fds": [True]}, {"observer_owned_fds": (1, 2)},
])
def test_capture_rejects_invalid_phase_timing_and_observer_metadata(changes):
    with pytest.raises(ValueError):
        capture(load_runner(), **changes)


def test_post_unload_phase_is_not_limited_by_the_old_soak_duration():
    module = load_runner()
    payload = capture(module, phase="after_unload", index=None,
                      started_monotonic_ns=9000, finished_monotonic_ns=9010)
    assert module.decode_fd_capture(payload, expected_pid=123)["phase"] == "after_unload"


@pytest.mark.parametrize("mutation", [
    "pid", "raw_count", "raw_hash", "table_name", "count_type", "extra", "missing", "base64",
])
def test_decoder_reparses_raw_and_rejects_forged_envelopes(mutation):
    module = load_runner()
    value = json.loads(capture(module))
    if mutation == "pid":
        value["worker_pid"] = 124
    elif mutation == "raw_count":
        value["raw_byte_count"] += 1
    elif mutation == "raw_hash":
        value["raw_sha256"] = "0" * 64
    elif mutation == "table_name":
        value["table"]["records"][1]["name"] = "/forged/path"
    elif mutation == "count_type":
        value["table"]["numeric_fd_count"] = 1.0
    elif mutation == "extra":
        value["extra"] = "unexpected"
    elif mutation == "missing":
        del value["table"]
    else:
        value["raw_lsof_base64"] = "!!!!"
    payload = (json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n").encode()
    with pytest.raises(ValueError):
        module.decode_fd_capture(payload, expected_pid=123)


def test_decoder_rejects_duplicate_keys_and_noncanonical_serialization():
    module = load_runner()
    payload = capture(module)
    duplicate = payload.replace(b'"schema":"t29.fd-evidence.v1"',
                                b'"schema":"wrong","schema":"t29.fd-evidence.v1"')
    with pytest.raises(ValueError, match="duplicate"):
        module.decode_fd_capture(duplicate, expected_pid=123)
    for malformed in (b" " + payload, payload[:-1], payload + b"\n"):
        with pytest.raises(ValueError):
            module.decode_fd_capture(malformed, expected_pid=123)


def test_capture_and_decoder_enforce_the_serialized_byte_limit():
    module = load_runner()
    raw = sample().replace(b"/fixture/memory.db", ("🛠" * 250_000).encode())
    assert len(raw) < module.FD_RAW_BYTE_LIMIT
    with pytest.raises(ValueError, match="envelope exceeds"):
        capture(module, raw)
    with pytest.raises(ValueError, match="oversized"):
        module.decode_fd_capture(b"x" * (module.FD_CAPTURE_BYTE_LIMIT + 1), expected_pid=123)


def test_decoder_rejects_deeply_nested_json_as_a_validation_error():
    payload = b"[" * 2000 + b"0" + b"]" * 2000 + b"\n"
    with pytest.raises(ValueError):
        load_runner().decode_fd_capture(payload, expected_pid=123)


def test_decoder_normalizes_an_upstream_recursion_error(monkeypatch):
    module = load_runner()
    payload = capture(module)

    def recursion_failure(*args, **kwargs):
        raise RecursionError("synthetic JSON decoder recursion boundary")

    monkeypatch.setattr(module.json, "loads", recursion_failure)
    with pytest.raises(ValueError, match="invalid FD capture JSON"):
        module.decode_fd_capture(payload, expected_pid=123)


def test_collector_drains_owned_helper_and_returns_observer_pipe_fds():
    module = load_runner()
    encoded = base64.b64encode(sample(os.getpid())).decode("ascii")
    command = [sys.executable, "-c", f"import base64,os; os.write(1,base64.b64decode('{encoded}'))"]
    capture = module.collect_fd_lsof(_command=command)
    assert capture["raw"] == sample(os.getpid())
    assert capture["started_monotonic_ns"] <= capture["finished_monotonic_ns"]
    assert len(capture["observer_owned_fds"]) == 2
    assert all(type(fd) is int and fd >= 0 for fd in capture["observer_owned_fds"])
    assert module.FD_HELPER_TIMEOUT_S == 10.0


def producer(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def encoded_sample(pid: int) -> str:
    return base64.b64encode(sample(pid)).decode("ascii")


def run_owned_failure(
    monkeypatch, command: list[str], *, timeout: float = 2.0,
    failure: str | None = None,
):
    module = load_runner()
    real_popen = module.subprocess.Popen
    children = []

    def recording_popen(*args, **kwargs):
        child = real_popen(*args, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(module.subprocess, "Popen", recording_popen)
    expected = f"{failure}.*owned_helper_cleanup=reaped" if failure else "owned_helper_cleanup=reaped"
    with pytest.raises(RuntimeError, match=expected):
        module.collect_fd_lsof(_command=command, timeout=timeout)
    assert len(children) == 1
    assert children[0].returncode is not None
    return module, children[0]


def test_collector_rejects_warning_stderr_and_still_reaps(monkeypatch):
    pid = os.getpid()
    code = f"import base64,os; os.write(1,base64.b64decode('{encoded_sample(pid)}')); os.write(2,b'warning')"
    run_owned_failure(monkeypatch, producer(code))


def test_collector_rejects_foreign_pid_and_still_reaps(monkeypatch):
    code = f"import base64,os; os.write(1,base64.b64decode('{encoded_sample(os.getpid() + 1)}'))"
    run_owned_failure(monkeypatch, producer(code))


def test_collector_rejects_nonzero_helper_exit_and_still_reaps(monkeypatch):
    code = f"import base64,os; os.write(1,base64.b64decode('{encoded_sample(os.getpid())}')); raise SystemExit(4)"
    run_owned_failure(monkeypatch, producer(code))


def test_collector_timeout_kills_and_reaps_only_its_helper(monkeypatch):
    started = time.monotonic()
    _, child = run_owned_failure(monkeypatch, producer("import time; time.sleep(10)"), timeout=0.5)
    assert time.monotonic() - started < 1.5
    assert child.returncode is not None


def test_collector_overflow_reads_one_detection_byte_then_kills_and_reaps(monkeypatch):
    module = load_runner()
    real_read = module.os.read
    requested_sizes = []

    def bounded_read(fd: int, size: int) -> bytes:
        requested_sizes.append(size)
        return real_read(fd, size)

    monkeypatch.setattr(module.os, "read", bounded_read)
    code = "import os\nwhile True: os.write(1,b'x'*65536)"
    run_owned_failure(monkeypatch, producer(code), timeout=2.0)
    assert requested_sizes
    assert max(requested_sizes) <= module.FD_HELPER_CHUNK_BYTES
    assert 1 in requested_sizes


def test_collector_rejects_invalid_test_budget_without_launching():
    module = load_runner()
    with pytest.raises(ValueError, match="finite positive"):
        module.collect_fd_lsof(_command=producer("pass"), timeout=0)


@pytest.mark.parametrize("timeout", [10.000001, 10**1000, float("inf"), float("nan")])
def test_collector_rejects_overlimit_or_nonfinite_budget_before_popen(monkeypatch, timeout):
    module = load_runner()

    def unexpected_popen(*args, **kwargs):
        pytest.fail("invalid FD helper budget launched a subprocess")

    monkeypatch.setattr(module.subprocess, "Popen", unexpected_popen)
    with pytest.raises(ValueError):
        module.collect_fd_lsof(_command=producer("pass"), timeout=timeout)


def test_capture_directory_closes_root_and_child_fds_when_child_fstat_fails(tmp_path, monkeypatch):
    module = load_runner()
    root = private_root(tmp_path / "run")
    real_open = module.os.open
    real_fstat = module.os.fstat
    opened_fds = []
    child_failed = False

    def recording_open(path, *args, **kwargs):
        fd = real_open(path, *args, **kwargs)
        if os.fspath(path) == str(root) or path == "fd-evidence":
            opened_fds.append(fd)
        return fd

    def fail_child_fstat(fd):
        nonlocal child_failed
        if len(opened_fds) == 2 and fd == opened_fds[1] and not child_failed:
            child_failed = True
            raise OSError("injected child fstat failure")
        return real_fstat(fd)

    monkeypatch.setattr(module.os, "open", recording_open)
    monkeypatch.setattr(module.os, "fstat", fail_child_fstat)
    leaked = []
    try:
        with pytest.raises(OSError, match="injected child fstat failure"):
            module._fd_open_capture_directory(root, create=True)
        for fd in opened_fds:
            try:
                real_fstat(fd)
            except OSError:
                continue
            leaked.append(fd)
    finally:
        for fd in opened_fds:
            try:
                real_fstat(fd)
            except OSError:
                continue
            os.close(fd)
    assert len(opened_fds) == 2
    assert not leaked


def test_collector_reports_unconfirmed_owned_helper_reap(monkeypatch):
    module = load_runner()
    real_popen = module.subprocess.Popen
    child_handles = []

    class CleanupFailure:
        def __init__(self, child):
            self.child = child
            self.wait_calls = 0
            self.kill_calls = 0
            self.reaped_at_injected_failure = []

        def __getattr__(self, name):
            return getattr(self.child, name)

        def kill(self):
            self.kill_calls += 1
            return self.child.kill()

        def wait(self, timeout=None):
            self.wait_calls += 1
            self.reaped_at_injected_failure.append(self.child.returncode is not None)
            raise subprocess.TimeoutExpired("synthetic-owned-helper", timeout)

    def fail_cleanup_popen(*args, **kwargs):
        handle = CleanupFailure(real_popen(*args, **kwargs))
        child_handles.append(handle)
        return handle

    monkeypatch.setattr(module.subprocess, "Popen", fail_cleanup_popen)
    try:
        with pytest.raises(RuntimeError, match="owned_helper_cleanup=failed"):
            module.collect_fd_lsof(_command=producer("import time; time.sleep(30)"), timeout=1.0)
    finally:
        # The injected wait failure must not leave a real child behind in pytest.
        for handle in child_handles:
            if handle.child.poll() is None:
                handle.child.kill()
            handle.child.wait(timeout=2.0)
    assert len(child_handles) == 1
    assert child_handles[0].child.returncode is not None
    assert child_handles[0].kill_calls == 1
    assert child_handles[0].wait_calls == 1
    assert child_handles[0].reaped_at_injected_failure == [False]


def test_collector_drains_stdout_and_stderr_concurrently_without_deadlock(monkeypatch):
    module = load_runner()
    real_popen = module.subprocess.Popen
    real_read = module.os.read
    child_handles = []
    read_fds = []

    def recording_popen(*args, **kwargs):
        child = real_popen(*args, **kwargs)
        child_handles.append((child, (child.stdout.fileno(), child.stderr.fileno())))
        return child

    def recording_read(fd, size):
        read_fds.append(fd)
        return real_read(fd, size)

    monkeypatch.setattr(module.subprocess, "Popen", recording_popen)
    monkeypatch.setattr(module.os, "read", recording_read)
    code = (
        "import os,threading\n"
        "def emit(fd,n,byte):\n"
        " data=byte*4096\n"
        " while n:\n"
        "  part=data[:min(n,len(data))]; n-=os.write(fd,part)\n"
        "a=threading.Thread(target=emit,args=(1,700000,b'x'))\n"
        "b=threading.Thread(target=emit,args=(2,65536,b'w'))\n"
        "a.start(); b.start(); a.join(); b.join()\n"
    )
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="warning or diagnostic stderr.*owned_helper_cleanup=reaped"):
        module.collect_fd_lsof(_command=producer(code), timeout=2.0)
    assert time.monotonic() - started < 2.0
    assert len(child_handles) == 1 and child_handles[0][0].returncode is not None
    assert set(child_handles[0][1]) <= set(read_fds)


def test_collector_bounds_stderr_overflow_and_reaps_owned_helper(monkeypatch):
    module = load_runner()
    real_read = module.os.read
    requested_sizes = []

    def recording_read(fd, size):
        requested_sizes.append(size)
        return real_read(fd, size)

    monkeypatch.setattr(module.os, "read", recording_read)
    module, child = run_owned_failure(
        monkeypatch, producer("import os\nwhile True: os.write(2,b'w'*65536)"),
        timeout=2.0, failure="stderr exceeds byte limit",
    )
    assert child.returncode is not None
    assert max(requested_sizes) <= module.FD_HELPER_CHUNK_BYTES
    assert 1 in requested_sizes


def test_collector_uses_native_lsof_for_its_own_pid_on_macos():
    if sys.platform != "darwin":
        pytest.skip("actual macOS lsof descriptor capture is unverified on this platform")
    result = load_runner().collect_fd_lsof()
    table = load_runner().parse_fd_lsof(result["raw"], expected_pid=os.getpid())
    assert table["numeric_fd_count"] >= 3
    assert result["observer_owned_fds"]


def capture_for(module, *, pid: int = 123, phase: str = "cadence", index: int | None = 0) -> bytes:
    return module.encode_fd_capture(
        sample(pid), expected_pid=pid, phase=phase, index=index,
        started_monotonic_ns=10, finished_monotonic_ns=20,
        observer_owned_fds=[4, 5],
    )


def private_root(path: Path) -> Path:
    path.mkdir(mode=0o700)
    os.chmod(path, 0o700)
    return path


@pytest.mark.parametrize(("phase", "index", "filename"), [
    ("cadence", 0, "cadence-000.json"),
    ("cadence", 120, "cadence-120.json"),
    ("after_first_turn", None, "after-first-turn.json"),
    ("after_unload", None, "after-unload.json"),
])
def test_writer_uses_fixed_phase_filename_and_private_exclusive_file(tmp_path, phase, index, filename):
    module = load_runner()
    root = private_root(tmp_path / "run")
    payload = capture_for(module, phase=phase, index=index)
    reference = module._write_fd_capture(
        root, payload, expected_pid=123, expected_phase=phase, expected_index=index,
    )
    path = root / "fd-evidence" / filename
    assert reference == {
        "path": f"fd-evidence/{filename}", "phase": phase, "index": index,
        "pid": 123, "byte_count": len(payload), "sha256": hashlib.sha256(payload).hexdigest(),
    }
    assert path.is_file() and not path.is_symlink()
    assert os.stat(path).st_mode & 0o777 == 0o600
    assert os.stat(path.parent).st_mode & 0o777 == 0o700
    assert path.read_bytes() == payload


def test_writer_refuses_duplicate_filename_without_overwriting_original(tmp_path):
    module = load_runner()
    root = private_root(tmp_path / "run")
    payload = capture_for(module)
    module._write_fd_capture(root, payload, expected_pid=123, expected_phase="cadence", expected_index=0)
    path = root / "fd-evidence" / "cadence-000.json"
    with pytest.raises((FileExistsError, OSError)):
        module._write_fd_capture(root, payload, expected_pid=123,
                                 expected_phase="cadence", expected_index=0)
    assert path.read_bytes() == payload


def test_writer_rejects_noncanonical_or_nonprivate_output_root(tmp_path):
    module = load_runner()
    root = private_root(tmp_path / "run")
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    payload = capture_for(module)
    with pytest.raises(ValueError):
        module._write_fd_capture(alias, payload, expected_pid=123,
                                 expected_phase="cadence", expected_index=0)
    os.chmod(root, 0o750)
    with pytest.raises(ValueError):
        module._write_fd_capture(root, payload, expected_pid=123,
                                 expected_phase="cadence", expected_index=0)


def write_reference(module, root: Path, *, phase: str = "cadence", index: int | None = 0):
    payload = capture_for(module, phase=phase, index=index)
    reference = module._write_fd_capture(root, payload, expected_pid=123,
                                         expected_phase=phase, expected_index=index)
    return payload, reference


@pytest.mark.parametrize(("phase", "index"), [
    ("cadence", 0), ("cadence", 120), ("after_first_turn", None), ("after_unload", None),
])
def test_reader_roundtrips_only_expected_phase_index_and_pid(tmp_path, phase, index):
    module = load_runner()
    root = private_root(tmp_path / "run")
    payload, reference = write_reference(module, root, phase=phase, index=index)
    decoded = module._read_fd_capture(root, reference, expected_phase=phase,
                                      expected_index=index, expected_pid=123)
    assert decoded == module.decode_fd_capture(payload, expected_pid=123)


@pytest.mark.parametrize("mutation", ["path", "phase", "index", "pid", "length", "hash", "extra"])
def test_reader_rejects_forged_or_wrong_context_references(tmp_path, mutation):
    module = load_runner()
    root = private_root(tmp_path / "run")
    _, reference = write_reference(module, root)
    forged = dict(reference)
    if mutation == "path":
        forged["path"] = "fd-evidence/../outside.json"
    elif mutation == "phase":
        forged["phase"] = "after_unload"
    elif mutation == "index":
        forged["index"] = True
    elif mutation == "pid":
        forged["pid"] = 124
    elif mutation == "length":
        forged["byte_count"] += 1
    elif mutation == "hash":
        forged["sha256"] = "0" * 64
    else:
        forged["unknown"] = "unexpected"
    with pytest.raises(ValueError):
        module._read_fd_capture(root, forged, expected_phase="cadence",
                                expected_index=0, expected_pid=123)


def test_reader_rejects_unknown_directory_entries_and_capture_symlinks(tmp_path):
    module = load_runner()
    root = private_root(tmp_path / "run")
    _, reference = write_reference(module, root)
    evidence_dir = root / "fd-evidence"
    (evidence_dir / "unexpected.json").write_text("unknown")
    with pytest.raises(ValueError):
        module._read_fd_capture(root, reference, expected_phase="cadence",
                                expected_index=0, expected_pid=123)
    (evidence_dir / "unexpected.json").unlink()
    capture_path = evidence_dir / "cadence-000.json"
    capture_path.unlink()
    outside = root / "outside.json"
    outside.write_bytes(capture_for(module))
    capture_path.symlink_to(outside)
    with pytest.raises(ValueError):
        module._read_fd_capture(root, reference, expected_phase="cadence",
                                expected_index=0, expected_pid=123)


def test_reader_rejects_duplicate_canonical_names_for_the_same_file(tmp_path):
    module = load_runner()
    root = private_root(tmp_path / "run")
    _, reference = write_reference(module, root)
    evidence_dir = root / "fd-evidence"
    os.link(evidence_dir / "cadence-000.json", evidence_dir / "cadence-001.json")
    with pytest.raises(ValueError, match="duplicate"):
        module._read_fd_capture(root, reference, expected_phase="cadence",
                                expected_index=0, expected_pid=123)


def test_reader_bounds_incremental_reads_if_capture_grows_after_fstat(tmp_path, monkeypatch):
    module = load_runner()
    root = private_root(tmp_path / "run")
    _, reference = write_reference(module, root)
    path = root / reference["path"]
    real_read = module.os.read
    requested_sizes = []
    grew = False

    def grow_then_read(fd: int, size: int) -> bytes:
        nonlocal grew
        requested_sizes.append(size)
        if not grew:
            grew = True
            with path.open("ab") as target:
                target.write(b"x" * (module.FD_CAPTURE_BYTE_LIMIT + 1))
        return real_read(fd, size)

    monkeypatch.setattr(module.os, "read", grow_then_read)
    with pytest.raises(ValueError):
        module._read_fd_capture(root, reference, expected_phase="cadence",
                                expected_index=0, expected_pid=123)
    assert requested_sizes
    assert max(requested_sizes) <= module.FD_HELPER_CHUNK_BYTES
    assert 1 in requested_sizes


def _start_receipt_fixture(tmp_path: Path, module, *, mode_marker=...):
    tmp_path.mkdir(parents=True)
    root = tmp_path / "root"
    root.mkdir()
    output = tmp_path / "owned-output"
    output.mkdir()
    for name in ("workspace", "home", "config", "tmp"):
        (output / name).mkdir()
    parent_pid = os.getppid()
    manifest = tmp_path / "candidate.json"
    digest = "a" * 64
    receipt = {
        "receipt": "started", "parent_pid": parent_pid,
        "root": str(root.resolve()), "output": str(output.resolve()),
        "candidate_manifest": str(manifest.resolve()), "candidate_sha256": digest,
    }
    if mode_marker is not ...:
        receipt["fd_evidence_mode"] = mode_marker
    receipt_path = output / "start-receipt.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    environment = module._isolated_environment(
        output / "config", output / "home", output / "tmp", root
    )
    return root, output, manifest, digest, parent_pid, receipt_path, environment


def test_requested_fd_mode_rejects_missing_or_non_boolean_start_receipt(tmp_path, monkeypatch):
    module = load_runner()
    for marker in (..., 1):
        root, output, manifest, digest, parent_pid, receipt_path, environment = _start_receipt_fixture(
            tmp_path / str(marker), module, mode_marker=marker
        )
        for key, value in environment.items():
            monkeypatch.setenv(key, value)
        with pytest.raises(ValueError, match="FD evidence mode"):
            module._worker_start_receipt(
                root, manifest, digest, output, receipt_path, parent_pid, fd_evidence=True
            )


def test_worker_missing_fd_flag_rejects_enabled_prelaunch_receipt_before_boot(tmp_path, monkeypatch):
    module = load_runner()
    root, output, manifest, digest, parent_pid, receipt_path, environment = _start_receipt_fixture(
        tmp_path / "worker", module, mode_marker=True
    )
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(module, "preflight", lambda *args: pytest.fail("worker booted after mode mismatch"))
    with pytest.raises(ValueError, match="FD evidence mode"):
        module._worker_main(root, manifest, digest, output, receipt_path, parent_pid)


def test_cli_fd_evidence_flag_reaches_actual_execute_call(tmp_path, monkeypatch):
    module = load_runner()
    observed = {}
    monkeypatch.setattr(module, "preflight", lambda *args: {"candidate": {"candidate_sha256": "a" * 64}})

    def execute(*args, fd_evidence=False):
        observed["fd_evidence"] = fd_evidence
        return 0

    monkeypatch.setattr(module, "_execute", execute)
    monkeypatch.setattr(sys, "argv", [
        str(ROOT / "scripts/qualify_release.py"), "--candidate-manifest", str(tmp_path / "manifest.json"),
        "--expected-digest", "a" * 64, "--root", str(ROOT), "--execute",
        "--output", str(tmp_path / "output"), "--fd-evidence",
    ])
    assert module.main() == 0
    assert observed == {"fd_evidence": True}


def test_fd_evidence_flag_without_execute_fails_instead_of_silent_preflight(tmp_path, monkeypatch, capsys):
    module = load_runner()
    monkeypatch.setattr(module, "preflight", lambda *args: {"valid": True})
    monkeypatch.setattr(sys, "argv", [
        str(ROOT / "scripts/qualify_release.py"), "--candidate-manifest", str(tmp_path / "manifest.json"),
        "--expected-digest", "a" * 64, "--root", str(ROOT), "--fd-evidence",
    ])
    assert module.main() == 1
    assert "requires explicit --execute" in capsys.readouterr().err


def test_worker_launch_receipt_uses_actual_pid_private_mode_and_exclusive_create(tmp_path):
    module = load_runner()
    output = private_root(tmp_path / "run")
    command = ["/pinned/python", "qualify_release.py", "--worker", "--fd-evidence"]
    receipt = module._write_worker_launch_receipt(
        output, worker_pid=321, parent_pid=123, argv=command,
        root=tmp_path / "source", manifest=tmp_path / "manifest.json",
        expected_digest="a" * 64, fd_evidence=True,
    )
    path = output / "worker-launch-receipt.json"
    assert path.is_file() and not path.is_symlink()
    assert os.stat(path).st_mode & 0o777 == 0o600
    assert json.loads(path.read_text()) == receipt
    assert receipt["worker_pid"] == 321 and receipt["parent_pid"] == 123
    assert receipt["argv"] == command and receipt["fd_evidence_mode"] is True
    with pytest.raises((FileExistsError, OSError)):
        module._write_worker_launch_receipt(
            output, worker_pid=322, parent_pid=123, argv=command,
            root=tmp_path / "source", manifest=tmp_path / "manifest.json",
            expected_digest="a" * 64, fd_evidence=True,
        )
    assert json.loads(path.read_text()) == receipt


def synthetic_full_evidence(module, tmp_path: Path, *, actual_pid: int = 123):
    output = private_root(tmp_path / "run")
    root = tmp_path / "source"
    manifest = tmp_path / "manifest.json"
    digest = "a" * 64
    argv = ["/pinned/python", "qualify_release.py", "--worker", "--fd-evidence"]
    launch = module._write_worker_launch_receipt(
        output, worker_pid=actual_pid, parent_pid=456, argv=argv,
        root=root, manifest=manifest, expected_digest=digest, fd_evidence=True,
    )
    captures = []
    metric_samples = []
    started = 100.0
    for index in range(module.SCHEDULED_TURNS + 1):
        elapsed = index * 0.1
        sample_started = int((started + elapsed) * 1_000_000_000) + 1
        payload = module.encode_fd_capture(
            sample(actual_pid), expected_pid=actual_pid, phase="cadence", index=index,
            started_monotonic_ns=sample_started, finished_monotonic_ns=sample_started + 10,
            observer_owned_fds=[2, 1],
        )
        reference = module._write_fd_capture(
            output, payload, expected_pid=actual_pid, expected_phase="cadence", expected_index=index,
        )
        if index == 0:
            captures.append(reference)
            lifecycle_payload = module.encode_fd_capture(
                sample(actual_pid), expected_pid=actual_pid, phase="after_first_turn", index=None,
                started_monotonic_ns=sample_started + 20, finished_monotonic_ns=sample_started + 30,
                observer_owned_fds=[2, 1],
            )
            captures.append(module._write_fd_capture(
                output, lifecycle_payload, expected_pid=actual_pid,
                expected_phase="after_first_turn", expected_index=None,
            ))
        else:
            captures.append(reference)
        metric_samples.append({"elapsed_s": elapsed, "fd_evidence_capture": reference})
    unload_started = 200_000_000_010
    unload_payload = module.encode_fd_capture(
        sample(actual_pid), expected_pid=actual_pid, phase="after_unload", index=None,
        started_monotonic_ns=unload_started, finished_monotonic_ns=unload_started + 10,
        observer_owned_fds=[2, 1],
    )
    captures.append(module._write_fd_capture(
        output, unload_payload, expected_pid=actual_pid,
        expected_phase="after_unload", expected_index=None,
    ))
    report = {
        "worker_pid": actual_pid, "fd_evidence_mode": True,
        "fd_evidence_captures": captures, "metric_samples": metric_samples,
        "started_monotonic": started, "ended_monotonic": 200.0,
        "runtime_unloaded": True, "acknowledged_turns": [{"session_id": "s", "prompt": "p"}],
        "source_pin": {
            "root": str(root.resolve()), "output": str(output.resolve()),
            "candidate_sha256_before": digest, "candidate_sha256_after": digest,
            "checked_after_soak": True,
        },
    }
    return output, report, launch, root, manifest, digest, argv


def test_synthetic_123_capture_fixture_validates_coverage_and_actual_pid(tmp_path):
    module = load_runner()
    output, report, launch, root, manifest, digest, argv = synthetic_full_evidence(module, tmp_path)
    result = module.validate_fd_evidence(
        report, output, actual_worker_pid=123, launch_receipt=launch,
        expected_parent_pid=456, expected_argv=argv, expected_root=root,
        expected_manifest=manifest, expected_digest=digest,
    )
    assert result == {
        "valid": True, "worker_pid": 123, "capture_count": 123,
        "cadence_count": 121, "after_first_turn": True, "after_unload": True,
    }


def test_fd_evidence_validator_rejects_symlinked_output_anchor(tmp_path):
    module = load_runner()
    output, report, launch, root, manifest, digest, argv = synthetic_full_evidence(module, tmp_path)
    alias = tmp_path / "output-alias"
    alias.symlink_to(output, target_is_directory=True)
    with pytest.raises(ValueError):
        module.validate_fd_evidence(
            report, alias, actual_worker_pid=123, launch_receipt=launch,
            expected_parent_pid=456, expected_argv=argv, expected_root=root,
            expected_manifest=manifest, expected_digest=digest,
        )


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "hash", "phase", "worker_pid", "mode", "source"])
def test_fd_evidence_validator_rejects_incomplete_forged_or_inconsistent_report(tmp_path, mutation):
    module = load_runner()
    output, report, launch, root, manifest, digest, argv = synthetic_full_evidence(module, tmp_path)
    if mutation == "missing":
        report["fd_evidence_captures"].pop()
    elif mutation == "duplicate":
        report["fd_evidence_captures"][2] = report["fd_evidence_captures"][1]
    elif mutation == "hash":
        report["fd_evidence_captures"][0]["sha256"] = "0" * 64
    elif mutation == "phase":
        report["fd_evidence_captures"][0]["phase"] = "after_unload"
    elif mutation == "worker_pid":
        report["worker_pid"] = 999
    elif mutation == "mode":
        report["fd_evidence_mode"] = 1
    else:
        report["source_pin"]["output"] = str(tmp_path / "foreign-output")
    with pytest.raises(ValueError):
        module.validate_fd_evidence(
            report, output, actual_worker_pid=123, launch_receipt=launch,
            expected_parent_pid=456, expected_argv=argv, expected_root=root,
            expected_manifest=manifest, expected_digest=digest,
        )


@pytest.mark.asyncio
async def test_native_startup_only_capture_retains_baseline_first_turn_and_unload(tmp_path, monkeypatch):
    if sys.platform != "darwin":
        pytest.skip("native lsof startup control is macOS-only")
    module = load_runner()
    assert any(Path(path).is_file() for path in module.FD_LSOF_PATHS), "macOS FD evidence requires lsof"
    from harness.kernel.commands import Commands

    output = tmp_path / "startup-only"
    output.mkdir(mode=0o700)
    os.chmod(output, 0o700)
    for name in ("workspace", "config"):
        (output / name).mkdir(mode=0o700)
    home = tmp_path / "startup-home"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("HARNESS_IMPORT_HERMES_KEYS", "0")
    observed = {}
    original_submit = Commands.submit_turn
    original_sleep = module._sleep_until
    sleep_calls = 0

    class IntentionalStartupStop(RuntimeError):
        pass

    async def one_real_tool_turn(self, *args, **kwargs):
        result = await original_submit(self, *args, **kwargs)
        assert result.error is None and result.stop_reason == "end_turn" and result.tool_calls == 1
        fetched = await self.sessions_get(kwargs["session_id"])
        results = [item for record in fetched["records"] for item in record.get("tool_results", [])]
        assert any(item.get("tool_use_id") == "t29-read-000" and item.get("is_error") is False
                   and "T29 offline source-soak fixture" in "".join(
                       block.get("text", "") for block in item.get("content", [])
                   ) for item in results)
        observed["registry"] = self.registry
        return result

    async def stop_on_second_wait(deadline):
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls == 2:
            raise IntentionalStartupStop("intentional stop after first acknowledgement and captures")
        await original_sleep(deadline)

    monkeypatch.setattr(Commands, "submit_turn", one_real_tool_turn)
    monkeypatch.setattr(module, "_sleep_until", stop_on_second_wait)
    with pytest.raises(IntentionalStartupStop, match="intentional stop"):
        await module._run_offline_soak(ROOT, output, module._regular_source_paths(ROOT), fd_evidence=True)
    assert observed and not observed["registry"]._loaded
    paths = {path.name for path in (output / "fd-evidence").iterdir()}
    assert paths == {"cadence-000.json", "after-first-turn.json", "after-unload.json"}
    captures = {
        name: module.decode_fd_capture((output / "fd-evidence" / name).read_bytes(), expected_pid=os.getpid())
        for name in paths
    }
    assert captures["cadence-000.json"]["phase"] == "cadence"
    assert captures["after-first-turn.json"]["phase"] == "after_first_turn"
    assert captures["after-unload.json"]["phase"] == "after_unload"
    assert module.MIN_ELAPSED_S == 7200 and module.MIN_TURNS == 100 and module.TURN_CADENCE_S == 60


@pytest.mark.asyncio
async def test_primary_fd_failure_survives_a_later_unload_capture_failure(tmp_path, monkeypatch):
    module = load_runner()
    output = tmp_path / "primary-error"
    output.mkdir(mode=0o700)
    os.chmod(output, 0o700)
    for name in ("workspace", "config"):
        (output / name).mkdir(mode=0o700)
    home = tmp_path / "primary-home"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("HARNESS_IMPORT_HERMES_KEYS", "0")
    monkeypatch.setattr(module, "_require_fd_evidence_capability", lambda: None)
    calls = []

    def fail_each_capture(*args, **kwargs):
        calls.append(kwargs.get("phase"))
        if len(calls) == 1:
            raise RuntimeError("primary baseline capture failure")
        raise RuntimeError("secondary post-unload capture failure")

    monkeypatch.setattr(module, "_capture_fd_evidence", fail_each_capture)
    with pytest.raises(RuntimeError, match="primary baseline capture failure") as raised:
        await module._run_offline_soak(ROOT, output, module._regular_source_paths(ROOT), fd_evidence=True)
    assert calls == ["cadence", "after_unload"]
    assert any("secondary post-unload capture failure" in note for note in raised.value.__notes__)


@pytest.mark.parametrize("forged_pid", [None, 4243, "malformed-report"])
def test_actual_execute_cannot_pass_with_valid_soak_evaluator_and_invalid_fd_evidence(
    tmp_path, monkeypatch, capsys, forged_pid,
):
    module = load_runner()
    digest = "b" * 64
    output = tmp_path / "actual-execute"
    source_root = tmp_path / "source-root"
    source_root.mkdir()
    preflight = {"candidate": {"candidate_sha256": digest, "file_count": 1, "candidate_paths": []}}
    monkeypatch.setattr(module, "_require_fd_evidence_capability", lambda: None)
    monkeypatch.setattr(module, "verify_final_source_pin", lambda *args: {
        "candidate_sha256": digest, "file_count": 1, "candidate_paths": [],
    })
    monkeypatch.setattr(module, "validate_import_origins", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "evaluate_soak", lambda report: {"passed": True, "status": "evaluator-pass"})

    class TrustedFakePopenHandle:
        pid = 4242
        returncode = 0

        def wait(self, timeout=None):
            return 0

        def poll(self):
            return 0

    def fake_popen(command, **kwargs):
        child_output = Path(command[command.index("--output") + 1])
        report_pid = 4242 if forged_pid is None else forged_pid
        report_value = {
            "worker_pid": report_pid, "fd_evidence_mode": True, "fd_evidence_captures": [],
            "source_pin": {
                "root": str(source_root.resolve()), "output": str(child_output.resolve()),
                "candidate_sha256_before": digest, "candidate_sha256_after": digest,
                "file_count_before": 1, "file_count_after": 1, "checked_after_soak": True,
            },
            "loaded_import_origins": {}, "loaded_namespace_search_locations": {},
        }
        (child_output / "worker-report.json").write_text(
            json.dumps([] if forged_pid == "malformed-report" else report_value), encoding="utf-8"
        )
        return TrustedFakePopenHandle()

    monkeypatch.setattr(module.subprocess, "Popen", fake_popen)
    result = module._execute(
        source_root, tmp_path / "manifest.json", digest, output, preflight, fd_evidence=True,
    )
    assert result != 0
    failure = json.loads((output / "fd-evidence-evaluation-receipt.json").read_text())
    assert failure["fd_evidence_mode"] is True and failure["evaluation"]["valid"] is False
    if forged_pid == "malformed-report":
        assert not (output / "evaluation-receipt.json").exists()
    else:
        assert json.loads((output / "evaluation-receipt.json").read_text())["passed"] is True
    if type(forged_pid) is int:
        assert "PID" in failure["evaluation"]["errors"][0]
    elif forged_pid is None:
        assert "coverage" in failure["evaluation"]["errors"][0]
    else:
        assert "report" in failure["evaluation"]["errors"][0].lower()
    assert json.loads(capsys.readouterr().out)["valid"] is False


def test_disabled_execute_rejects_worker_report_upgrade_to_fd_evidence(tmp_path, monkeypatch, capsys):
    module = load_runner()
    digest = "c" * 64
    output = tmp_path / "legacy-execute"
    source_root = tmp_path / "source-root"
    source_root.mkdir()
    preflight = {"candidate": {"candidate_sha256": digest, "file_count": 1, "candidate_paths": []}}
    monkeypatch.setattr(module, "verify_final_source_pin", lambda *args: {
        "candidate_sha256": digest, "file_count": 1, "candidate_paths": [],
    })
    monkeypatch.setattr(module, "validate_import_origins", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "evaluate_soak", lambda report: {"passed": True, "status": "evaluator-pass"})
    observed_command = {}

    class TrustedFakePopenHandle:
        pid = 4343
        returncode = 0

        def wait(self, timeout=None):
            return 0

        def poll(self):
            return 0

    def fake_popen(command, **kwargs):
        observed_command["argv"] = command
        child_output = Path(command[command.index("--output") + 1])
        (child_output / "worker-report.json").write_text(json.dumps({
            "worker_pid": 4343, "fd_evidence_mode": True,
            "fd_evidence_captures": [{"path": "forged"}],
            "source_pin": {
                "root": str(source_root.resolve()), "output": str(child_output.resolve()),
                "candidate_sha256_before": digest, "candidate_sha256_after": digest,
                "file_count_before": 1, "file_count_after": 1, "checked_after_soak": True,
            },
            "loaded_import_origins": {}, "loaded_namespace_search_locations": {},
        }), encoding="utf-8")
        return TrustedFakePopenHandle()

    monkeypatch.setattr(module.subprocess, "Popen", fake_popen)
    result = module._execute(source_root, tmp_path / "manifest.json", digest, output, preflight)
    assert result != 0
    assert "--fd-evidence" not in observed_command["argv"]
    failure = json.loads((output / "fd-evidence-mode-failure-receipt.json").read_text())
    assert failure["fd_evidence_mode"] is False and failure["worker_fd_evidence_mode"] is True
    assert json.loads(capsys.readouterr().out)["valid"] is False
