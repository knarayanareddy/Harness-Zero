"""Read-only source preflight and explicitly opted-in, offline T29 real-time soak."""
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import importlib
import importlib.machinery
import importlib.metadata
import json
import logging
import math
import os
import re
import select
import signal
import stat
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

DIGEST_FORMAT = "harness-zero.snapshot.v1"
SUPPORTED_PYTHON = {(3, 11, 17), (3, 12, 15)}
MIN_TURNS = 100
MIN_ELAPSED_S = 7200.0
WORKER_DIAGNOSTIC_DEADLINE_S = MIN_ELAPSED_S + 900.0
PROCESS_SAMPLE_TIMEOUT_S = 10.0
TURN_CADENCE_S = 60.0
SCHEDULED_TURNS = 120
TRANSIENT_DIRS = {".git", ".venv", ".pytest_cache", ".ruff_cache", "__pycache__", "node_modules"}
TRANSIENT_FILES = {".git"}
RUNTIME_INPUT_ROOTS = ("harness", "builtin", "packaging", "launcher")
EXPECTED_VIEW_FILE_TEXT = "    1 | T29 offline source-soak fixture\n"
PROCESS_METRIC_SCOPE = "direct child processes only; descendants are not fully enumerated"
FD_RAW_BYTE_LIMIT = 1_048_576
FD_RECORD_LIMIT = 4096
FD_CAPTURE_BYTE_LIMIT = 4_194_304
FD_LAUNCH_RECEIPT_BYTE_LIMIT = 65_536
FD_EVIDENCE_SCHEMA = "t29.fd-evidence.v1"
FD_HELPER_TIMEOUT_S = 10.0
FD_HELPER_CLEANUP_RESERVE_S = 1.0
FD_HELPER_CHUNK_BYTES = 65_536
FD_LSOF_PATHS = ("/usr/sbin/lsof", "/usr/bin/lsof")


def parse_fd_lsof(raw: bytes, *, expected_pid: int) -> dict[str, Any]:
    """Parse the verified NUL-delimited lsof field format, never opening names."""
    if type(expected_pid) is not int or expected_pid <= 0:
        raise ValueError("expected worker PID must be a positive integer")
    if type(raw) is not bytes or not raw or len(raw) > FD_RAW_BYTE_LIMIT:
        raise ValueError("invalid or oversized FD capture bytes")
    if not raw.endswith(b"\0\n"):
        raise ValueError("truncated FD capture")
    fields = raw.split(b"\0")
    if fields[0] != f"p{expected_pid}".encode("ascii"):
        raise ValueError("FD capture process header does not match worker PID")
    records: list[dict[str, Any]] = []
    numeric: set[int] = set()
    current: dict[str, Any] | None = None
    seen: set[bytes] = set()
    names = {b"t": "type", b"n": "name", b"D": "device", b"i": "inode"}

    def finish_record() -> None:
        if current is None:
            return
        if not {b"t", b"n"} <= seen:
            raise ValueError("FD record lacks type or name")
        records.append(current)
        if len(records) > FD_RECORD_LIMIT:
            raise ValueError("FD capture exceeds record limit")

    for field in fields[1:-1]:
        if field.startswith(b"\n"):
            field = field[1:]
            if not field.startswith(b"f"):
                raise ValueError("invalid FD record boundary")
            finish_record()
            descriptor = field[1:]
            if not descriptor or len(descriptor) > 64 or any(byte < 33 or byte > 126 for byte in descriptor):
                raise ValueError("invalid FD descriptor")
            label = descriptor.decode("ascii")
            if label.isdecimal():
                number = int(label)
                if number in numeric:
                    raise ValueError("duplicate numeric FD")
                numeric.add(number)
            current = {"descriptor": label, "type": None, "name": None, "device": None, "inode": None}
            seen = set()
            continue
        tag = field[:1]
        if current is None or tag not in names or tag in seen:
            raise ValueError("unknown, duplicate, or misplaced FD field")
        if tag == b"t" and len(field) == 1:
            raise ValueError("FD record has an empty type")
        current[names[tag]] = field[1:].decode("utf-8", errors="replace")
        seen.add(tag)
    finish_record()
    if not records or not numeric:
        raise ValueError("FD capture lacks numeric descriptor evidence")
    return {"pid": expected_pid, "records": records, "numeric_fd_count": len(numeric),
            "pseudo_record_count": len(records) - len(numeric)}


def _fd_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)
            + "\n").encode("utf-8")


def encode_fd_capture(
    raw: bytes, *, expected_pid: int, phase: str, index: int | None,
    started_monotonic_ns: int, finished_monotonic_ns: int,
    observer_owned_fds: list[int],
) -> bytes:
    """Encode a bounded capture; this does not collect, write, or qualify it."""
    if type(phase) is not str or phase not in {"cadence", "after_first_turn", "after_unload"}:
        raise ValueError("unknown FD capture phase")
    if phase == "cadence":
        if type(index) is not int or not 0 <= index <= SCHEDULED_TURNS:
            raise ValueError("invalid FD cadence index")
    elif index is not None:
        raise ValueError("FD lifecycle capture must not have a cadence index")
    if (type(started_monotonic_ns) is not int or type(finished_monotonic_ns) is not int
            or started_monotonic_ns < 0 or finished_monotonic_ns < started_monotonic_ns):
        raise ValueError("invalid FD capture monotonic interval")
    if (type(observer_owned_fds) is not list or len(observer_owned_fds) > FD_RECORD_LIMIT
            or any(type(fd) is not int or fd < 0 for fd in observer_owned_fds)
            or len(set(observer_owned_fds)) != len(observer_owned_fds)):
        raise ValueError("invalid observer-owned FD identities")
    table = parse_fd_lsof(raw, expected_pid=expected_pid)
    envelope = {
        "schema": FD_EVIDENCE_SCHEMA,
        "worker_pid": expected_pid,
        "phase": phase,
        "index": index,
        "started_monotonic_ns": started_monotonic_ns,
        "finished_monotonic_ns": finished_monotonic_ns,
        "observer_owned_fds": sorted(observer_owned_fds),
        "raw_lsof_base64": base64.b64encode(raw).decode("ascii"),
        "raw_byte_count": len(raw),
        "raw_sha256": hashlib.sha256(raw).hexdigest(),
        "table": table,
    }
    payload = _fd_json_bytes(envelope)
    if len(payload) > FD_CAPTURE_BYTE_LIMIT:
        raise ValueError("FD capture envelope exceeds byte limit")
    return payload


def _fd_unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate FD capture JSON key")
        result[key] = value
    return result


def decode_fd_capture(payload: bytes, *, expected_pid: int) -> dict[str, Any]:
    """Validate exact serialized bytes and rederive all table data from raw bytes."""
    if type(payload) is not bytes or not payload or len(payload) > FD_CAPTURE_BYTE_LIMIT:
        raise ValueError("invalid or oversized FD capture envelope")
    try:
        envelope = json.loads(payload.decode("ascii"), object_pairs_hook=_fd_unique_json_object)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError("invalid FD capture JSON") from error
    keys = {"schema", "worker_pid", "phase", "index", "started_monotonic_ns",
            "finished_monotonic_ns", "observer_owned_fds", "raw_lsof_base64",
            "raw_byte_count", "raw_sha256", "table"}
    if type(envelope) is not dict or set(envelope) != keys or envelope["schema"] != FD_EVIDENCE_SCHEMA:
        raise ValueError("invalid FD capture schema or fields")
    encoded_raw = envelope["raw_lsof_base64"]
    if type(encoded_raw) is not str or len(encoded_raw) > 4 * ((FD_RAW_BYTE_LIMIT + 2) // 3):
        raise ValueError("invalid or oversized FD raw base64")
    try:
        raw = base64.b64decode(encoded_raw, validate=True)
    except ValueError as error:
        raise ValueError("invalid FD raw base64") from error
    rebuilt = encode_fd_capture(
        raw, expected_pid=expected_pid, phase=envelope["phase"], index=envelope["index"],
        started_monotonic_ns=envelope["started_monotonic_ns"],
        finished_monotonic_ns=envelope["finished_monotonic_ns"],
        observer_owned_fds=envelope["observer_owned_fds"],
    )
    if rebuilt != payload:
        raise ValueError("FD capture bytes, PID, raw hash, or parsed table are inconsistent")
    return envelope


def collect_fd_lsof(*, _command: list[str] | None = None, timeout: float = FD_HELPER_TIMEOUT_S) -> dict[str, Any]:
    """Collect this process's lsof bytes with bounded reads and exact-child cleanup.

    ``_command`` and ``timeout`` are private synthetic-producer controls for tests;
    no CLI accepts arbitrary commands. Synchronous process creation itself cannot
    be interrupted by this userspace deadline on every platform.
    """
    if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
            or timeout <= 0 or timeout > FD_HELPER_TIMEOUT_S or not math.isfinite(timeout)):
        raise ValueError(f"FD helper timeout must be a finite positive number no greater than {FD_HELPER_TIMEOUT_S:g} seconds")
    pid = os.getpid()
    if _command is None:
        executable = next((path for path in FD_LSOF_PATHS if os.path.isfile(path) and os.access(path, os.X_OK)), None)
        if executable is None:
            raise RuntimeError("verified native lsof executable is unavailable")
        command = [executable, "-nP", "-p", str(pid), "-F0ftnDi"]
    else:
        if type(_command) is not list or not _command or any(type(part) is not str or not part for part in _command):
            raise ValueError("invalid private synthetic helper command")
        command = list(_command)

    started_ns = time.monotonic_ns()
    deadline = time.monotonic() + float(timeout)
    reserve = min(FD_HELPER_CLEANUP_RESERVE_S, float(timeout) / 5)
    read_deadline = deadline - reserve
    process: subprocess.Popen[bytes] | None = None
    stdout = bytearray()
    stderr = bytearray()
    observer_fds: list[int] = []
    failure: BaseException | None = None
    try:
        process = subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, close_fds=True,
        )
        assert process.stdout is not None and process.stderr is not None
        streams = {process.stdout.fileno(): (stdout, FD_RAW_BYTE_LIMIT),
                   process.stderr.fileno(): (stderr, 65_536)}
        observer_fds = list(streams)
        for fd in streams:
            os.set_blocking(fd, False)
        while streams:
            remaining = read_deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("FD helper exceeded its collection budget")
            readable, _, _ = select.select(list(streams), [], [], min(remaining, 0.1))
            for fd in readable:
                buffer, limit = streams[fd]
                room = limit + 1 - len(buffer)
                chunk = os.read(fd, min(FD_HELPER_CHUNK_BYTES, room))
                if not chunk:
                    del streams[fd]
                else:
                    buffer.extend(chunk)
                    if len(buffer) > limit:
                        label = "stdout" if buffer is stdout else "stderr"
                        raise ValueError(f"FD helper {label} exceeds byte limit")
        remaining = read_deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("FD helper exceeded its collection budget")
        process.wait(timeout=remaining)
        if process.returncode != 0:
            raise RuntimeError(f"FD helper exited with status {process.returncode}")
        if stderr:
            raise RuntimeError("FD helper wrote warning or diagnostic stderr")
        parse_fd_lsof(bytes(stdout), expected_pid=pid)
    except BaseException as error:
        failure = error

    cleanup_status = "not_started"
    if process is not None:
        try:
            if process.poll() is None:
                process.kill()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(command, timeout)
            process.wait(timeout=remaining)
            cleanup_status = "reaped"
        except BaseException as cleanup_error:
            failure = RuntimeError(f"owned helper cleanup failed: {type(cleanup_error).__name__}")
            cleanup_status = "failed"
        finally:
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
    finished_ns = time.monotonic_ns()
    if failure is not None:
        message = f"FD helper failed: {failure}; owned_helper_cleanup={cleanup_status}"
        if isinstance(failure, Exception):
            raise RuntimeError(message) from failure
        raise failure
    return {"raw": bytes(stdout), "started_monotonic_ns": started_ns,
            "finished_monotonic_ns": finished_ns, "observer_owned_fds": observer_fds}


def _fd_capture_filename(phase: str, index: int | None) -> str:
    if phase == "cadence" and type(index) is int and 0 <= index <= SCHEDULED_TURNS:
        return f"cadence-{index:03d}.json"
    if phase == "after_first_turn" and index is None:
        return "after-first-turn.json"
    if phase == "after_unload" and index is None:
        return "after-unload.json"
    raise ValueError("invalid FD capture phase or index")


def _fd_open_capture_directory(output_root: Path, *, create: bool) -> tuple[int, int]:
    root = Path(output_root)
    if not root.is_absolute():
        raise ValueError("FD evidence output root must be an absolute canonical path")
    if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        raise ValueError("FD evidence requires directory and no-follow descriptor support")
    try:
        if root.resolve(strict=True) != root:
            raise ValueError("FD evidence output root is not canonical")
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        root_fd = os.open(root, flags)
    except (OSError, RuntimeError) as error:
        raise ValueError("FD evidence output root is unavailable or noncanonical") from error
    child_fd: int | None = None
    try:
        root_stat = os.fstat(root_fd)
        if (not stat.S_ISDIR(root_stat.st_mode) or stat.S_IMODE(root_stat.st_mode) != 0o700
                or root_stat.st_uid != os.getuid()):
            raise ValueError("FD evidence output root must be an owned private 0700 directory")
        if create:
            try:
                os.mkdir("fd-evidence", mode=0o700, dir_fd=root_fd)
            except FileExistsError:
                pass
        child_fd = os.open("fd-evidence", flags, dir_fd=root_fd)
        child_stat = os.fstat(child_fd)
        if (not stat.S_ISDIR(child_stat.st_mode) or stat.S_IMODE(child_stat.st_mode) != 0o700
                or child_stat.st_uid != os.getuid()):
            raise ValueError("FD evidence child must be an owned private 0700 directory")
        return root_fd, child_fd
    except BaseException as original_error:
        close_errors = []
        if child_fd is not None:
            try:
                os.close(child_fd)
            except OSError as error:
                close_errors.append(error)
        try:
            os.close(root_fd)
        except OSError as error:
            close_errors.append(error)
        if close_errors:
            original_error.add_note(f"FD evidence directory descriptor cleanup failed: {close_errors!r}")
        raise


def _write_fd_capture(
    output_root: Path, payload: bytes, *, expected_pid: int,
    expected_phase: str, expected_index: int | None,
) -> dict[str, Any]:
    """Write one validated envelope with dir-fd confinement and exclusive creation."""
    envelope = decode_fd_capture(payload, expected_pid=expected_pid)
    if envelope["phase"] != expected_phase or envelope["index"] != expected_index:
        raise ValueError("FD capture payload disagrees with expected phase or index")
    filename = _fd_capture_filename(expected_phase, expected_index)
    root_fd, child_fd = _fd_open_capture_directory(Path(output_root), create=True)
    file_fd: int | None = None
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        file_fd = os.open(filename, flags, 0o600, dir_fd=child_fd)
        os.fchmod(file_fd, 0o600)
        metadata = os.fstat(file_fd)
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid()
                or stat.S_IMODE(metadata.st_mode) != 0o600):
            raise ValueError("FD evidence capture must be an owned regular 0600 file")
        view = memoryview(payload)
        offset = 0
        while offset < len(view):
            try:
                written = os.write(file_fd, view[offset:offset + FD_HELPER_CHUNK_BYTES])
            except InterruptedError:
                continue
            if written <= 0:
                raise OSError("short write to FD evidence capture")
            offset += written
    finally:
        if file_fd is not None:
            os.close(file_fd)
        os.close(child_fd)
        os.close(root_fd)
    return {"path": f"fd-evidence/{filename}", "phase": expected_phase,
            "index": expected_index, "pid": expected_pid, "byte_count": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest()}


def _read_fd_capture(
    output_root: Path, reference: dict[str, Any], *, expected_phase: str,
    expected_index: int | None, expected_pid: int,
) -> dict[str, Any]:
    """Read one referenced envelope under size/hash/schema and directory bounds."""
    keys = {"path", "phase", "index", "pid", "byte_count", "sha256"}
    if type(reference) is not dict or set(reference) != keys:
        raise ValueError("invalid FD capture reference fields")
    if type(expected_pid) is not int or expected_pid <= 0:
        raise ValueError("expected worker PID must be a positive integer")
    filename = _fd_capture_filename(expected_phase, expected_index)
    if (type(reference["path"]) is not str or reference["path"] != f"fd-evidence/{filename}"
            or type(reference["phase"]) is not str or reference["phase"] != expected_phase
            or reference["index"] != expected_index
            or (expected_index is not None and type(reference["index"]) is not int)
            or type(reference["pid"]) is not int or reference["pid"] != expected_pid
            or type(reference["byte_count"]) is not int or not 0 < reference["byte_count"] <= FD_CAPTURE_BYTE_LIMIT
            or type(reference["sha256"]) is not str
            or re.fullmatch(r"[0-9a-f]{64}", reference["sha256"]) is None):
        raise ValueError("FD capture reference disagrees with expected identity or bounds")
    try:
        root_fd, child_fd = _fd_open_capture_directory(Path(output_root), create=False)
    except OSError as error:
        raise ValueError("FD evidence directory is unavailable or unsafe") from error
    file_fd: int | None = None
    try:
        allowed = {"after-first-turn.json", "after-unload.json"}
        allowed.update(_fd_capture_filename("cadence", item) for item in range(SCHEDULED_TURNS + 1))
        names = os.listdir(child_fd)
        seen_files: set[tuple[int, int]] = set()
        for name in names:
            if name not in allowed:
                raise ValueError("FD evidence directory contains an unknown file")
            try:
                entry = os.stat(name, dir_fd=child_fd, follow_symlinks=False)
            except OSError as error:
                raise ValueError("FD evidence directory entry is unavailable") from error
            if (not stat.S_ISREG(entry.st_mode) or entry.st_uid != os.getuid()
                    or stat.S_IMODE(entry.st_mode) != 0o600 or entry.st_size > FD_CAPTURE_BYTE_LIMIT):
                raise ValueError("FD evidence directory contains an unsafe file")
            identity = (entry.st_dev, entry.st_ino)
            if identity in seen_files:
                raise ValueError("FD evidence directory contains a duplicate file")
            seen_files.add(identity)
        try:
            before = os.stat(filename, dir_fd=child_fd, follow_symlinks=False)
        except OSError as error:
            raise ValueError("FD capture file is missing or unsafe") from error
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid()
                or stat.S_IMODE(before.st_mode) != 0o600 or before.st_size > FD_CAPTURE_BYTE_LIMIT):
            raise ValueError("FD capture is not a bounded owned private regular file")
        flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        try:
            file_fd = os.open(filename, flags, dir_fd=child_fd)
        except OSError as error:
            raise ValueError("FD capture could not be opened without following links") from error
        opened = os.fstat(file_fd)
        if (not stat.S_ISREG(opened.st_mode) or opened.st_uid != os.getuid()
                or stat.S_IMODE(opened.st_mode) != 0o600
                or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
                or opened.st_size > FD_CAPTURE_BYTE_LIMIT):
            raise ValueError("FD capture changed or is not a bounded owned regular file")
        content = bytearray()
        digest = hashlib.sha256()
        while True:
            room = FD_CAPTURE_BYTE_LIMIT + 1 - len(content)
            if room <= 0:
                raise ValueError("FD capture exceeds readback byte limit")
            chunk = os.read(file_fd, min(FD_HELPER_CHUNK_BYTES, room))
            if not chunk:
                break
            if len(content) + len(chunk) > FD_CAPTURE_BYTE_LIMIT:
                raise ValueError("FD capture exceeds readback byte limit")
            content.extend(chunk)
            digest.update(chunk)
        after = os.fstat(file_fd)
        if (not stat.S_ISREG(after.st_mode) or after.st_uid != os.getuid()
                or stat.S_IMODE(after.st_mode) != 0o600 or after.st_size != len(content)
                or (after.st_dev, after.st_ino) != (opened.st_dev, opened.st_ino)
                or len(content) != reference["byte_count"] or digest.hexdigest() != reference["sha256"]):
            raise ValueError("FD capture size, identity, or SHA-256 does not match reference")
        envelope = decode_fd_capture(bytes(content), expected_pid=expected_pid)
        if envelope["phase"] != expected_phase or envelope["index"] != expected_index:
            raise ValueError("FD capture contents disagree with expected phase or index")
        return envelope
    finally:
        if file_fd is not None:
            os.close(file_fd)
        os.close(child_fd)
        os.close(root_fd)


def canonical_snapshot_bytes(entries: list[dict[str, str]]) -> bytes:
    """The exact harness-zero.snapshot.v1 aggregate encoding."""
    if not isinstance(entries, list):
        raise ValueError("manifest files must be a list")
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"path", "mode", "sha256"}:
            raise ValueError("each manifest file needs exactly path, mode and sha256")
        if not all(isinstance(value, str) for value in entry.values()):
            raise ValueError("manifest file identity values must be strings")
        path = entry["path"]
        parsed = PurePosixPath(path)
        if (not path or parsed.is_absolute() or "\0" in path or "\\" in path
                or ".." in parsed.parts or parsed.as_posix() != path or path in seen):
            raise ValueError(f"invalid or duplicate relative snapshot path: {path!r}")
        seen.add(path)
        mode = entry["mode"]
        if (not re.fullmatch(r"0o[0-7]+", mode) or int(mode[2:], 8) > 0o7777
                or oct(int(mode[2:], 8)) != mode):
            raise ValueError(f"non-canonical file mode for {path}")
        if not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]):
            raise ValueError(f"invalid SHA-256 for {path}")
        normalized.append({"path": path, "mode": mode, "sha256": entry["sha256"]})
    return json.dumps(
        sorted(normalized, key=lambda row: row["path"]),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def snapshot_digest(entries: list[dict[str, str]]) -> str:
    return hashlib.sha256(canonical_snapshot_bytes(entries)).hexdigest()


def validate_python_version(version: tuple[int, ...]) -> None:
    if tuple(version[:3]) not in SUPPORTED_PYTHON:
        raise ValueError("Python must be exactly 3.12.15 or 3.11.17")


def validate_import_origins(origins: dict[str, str | None], root: Path,
                            pinned_paths: set[str] | None = None, *,
                            namespace_paths: dict[str, list[str]] | None = None) -> None:
    resolved_root = root.resolve(strict=True)
    if namespace_paths is None:
        namespace_paths = {}
    if not isinstance(namespace_paths, dict):
        raise ValueError("namespace search locations must be a module-to-path mapping")
    if set(namespace_paths) - set(origins):
        raise ValueError("namespace search locations do not match loaded imports")
    for name, origin in origins.items():
        if origin is None:
            locations = namespace_paths.get(name)
            if not isinstance(locations, list) or not locations:
                raise ValueError(f"missing source import origin for {name}")
            if pinned_paths is None:
                raise ValueError(f"namespace import cannot be verified without a source pin: {name}")
            if any(not isinstance(location, str) or not location for location in locations):
                raise ValueError(f"namespace search location is invalid for {name}")
            if len(locations) != len(set(locations)):
                raise ValueError(f"namespace import has duplicate search locations: {name}")
            for location in locations:
                location_path = Path(location)
                if not location_path.is_absolute():
                    raise ValueError(f"namespace search location is not absolute: {name} -> {location}")
                current = Path(location_path.anchor)
                for part in location_path.parts[1:]:
                    current = current / part
                    if current.is_symlink():
                        raise ValueError(f"namespace search location contains a symlink: {name} -> {location}")
                try:
                    resolved = location_path.resolve(strict=True)
                except OSError as error:
                    raise ValueError(f"namespace search location is unavailable: {name} -> {location}") from error
                if resolved != location_path or not resolved.is_dir():
                    raise ValueError(f"namespace search location is not a canonical directory: {name} -> {location}")
                try:
                    relative = resolved.relative_to(resolved_root).as_posix()
                except ValueError as error:
                    raise ValueError(f"namespace search location outside checkout: {name} -> {resolved}") from error
                source_prefix = relative.rstrip("/") + "/"
                pinned_source = next((
                    source_path for source_path in pinned_paths
                    if source_path.startswith(source_prefix) and source_path.endswith(".py")
                ), None)
                if pinned_source is None:
                    raise ValueError(f"namespace search location has no pinned source child: {name} -> {relative}")
                source_file = resolved_root / PurePosixPath(pinned_source)
                source_current = resolved_root
                for part in PurePosixPath(pinned_source).parts:
                    source_current = source_current / part
                    if source_current.is_symlink():
                        raise ValueError(f"namespace source path contains a symlink: {name} -> {pinned_source}")
                if not source_file.is_file():
                    raise ValueError(f"namespace pinned source is unavailable: {name} -> {pinned_source}")
            continue
        if not isinstance(origin, str) or not origin:
            raise ValueError(f"source import origin is invalid for {name}")
        if name in namespace_paths:
            raise ValueError(f"file-backed import unexpectedly has namespace paths: {name}")
        try:
            resolved = Path(origin).resolve(strict=True)
        except OSError as error:
            raise ValueError(f"source import origin is unavailable: {name} -> {origin}") from error
        try:
            relative = resolved.relative_to(resolved_root).as_posix()
        except ValueError as error:
            raise ValueError(f"source import origin outside checkout: {name} -> {resolved}") from error
        if pinned_paths is not None and relative not in pinned_paths:
            raise ValueError(f"source import origin is not in the candidate pin: {name} -> {relative}")


def _loaded_import_facts(modules: dict[str, Any]) -> tuple[dict[str, str | None], dict[str, list[str]]]:
    origins: dict[str, str | None] = {}
    namespace_paths: dict[str, list[str]] = {}
    for name, module in modules.items():
        origin = getattr(module, "__file__", None)
        origins[name] = origin
        if origin is not None:
            continue
        spec = getattr(module, "__spec__", None)
        if (spec is None or spec.origin is not None
                or not isinstance(getattr(spec, "loader", None), importlib.machinery.NamespaceLoader)):
            raise ValueError(f"missing source import origin for {name}")
        locations = spec.submodule_search_locations
        if locations is None:
            raise ValueError(f"namespace import has no search locations: {name}")
        namespace_paths[name] = [str(location) for location in locations]
    return origins, namespace_paths


def _safe_regular_file(root: Path, relative: str) -> Path:
    path = root / PurePosixPath(relative)
    current = root
    for part in PurePosixPath(relative).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"snapshot path contains a symlink: {relative}")
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError(f"snapshot path is not a regular file: {relative}")
    return path


def _generated_bytecode(path: Path) -> bool:
    """Recognize only cache files corresponding to an existing regular source file."""
    if path.parent.name != "__pycache__":
        return False
    match = re.fullmatch(r"(.+)\.(?:cpython-\d+|pypy\d+)(?:\.opt-\d+)?\.pyc", path.name)
    if match is None:
        return False
    source = path.parent.parent / (match.group(1) + ".py")
    return source.is_file() and not source.is_symlink()


def _walk_source_paths(root: Path, *, skip_root_transients: bool) -> set[str]:
    paths: set[str] = set()
    for directory, children, files in os.walk(root, topdown=True, followlinks=False):
        retained_children = []
        for name in children:
            if skip_root_transients and Path(directory) == root and name in TRANSIENT_DIRS - {"__pycache__"}:
                continue
            child = Path(directory) / name
            if child.is_symlink():
                raise ValueError(f"source tree contains a symlink directory: {child.relative_to(root)}")
            retained_children.append(name)
        children[:] = retained_children
        for name in files:
            path = Path(directory) / name
            if skip_root_transients and path.parent == root and name in TRANSIENT_FILES:
                continue
            if path.is_symlink():
                raise ValueError(f"source tree contains a symlink file: {path.relative_to(root)}")
            if _generated_bytecode(path):
                continue
            if not path.is_file():
                raise ValueError(f"source tree contains a non-regular file: {path.relative_to(root)}")
            paths.add(path.relative_to(root).as_posix())
    return paths


def _regular_source_paths(root: Path) -> set[str]:
    """Use the established VCS source scope without hiding ignored runtime inputs.

    Historical ignored evidence is not a build input. Conversely, discovery and
    packaging recursively read runtime trees, so ignored additions there must
    still fail the candidate pin. Clean source exports remain supported without
    consulting an ancestor checkout's Git metadata.
    """
    git_entry = root / ".git"
    if git_entry.is_symlink():
        raise ValueError("source Git entry must not be a symlink")
    if git_entry.exists():
        environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        try:
            result = subprocess.run(
                ["git", "-c", "core.fsmonitor=false", "ls-files", "-c", "-o", "--exclude-standard", "-z"],
                cwd=root, env=environment, check=True, capture_output=True, timeout=PROCESS_SAMPLE_TIMEOUT_S,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise ValueError("cannot enumerate the selected source checkout's Git inventory") from error
        paths = {os.fsdecode(value) for value in result.stdout.split(b"\0") if value}
    else:
        paths = _walk_source_paths(root, skip_root_transients=True)
    for prefix in RUNTIME_INPUT_ROOTS:
        runtime = root / prefix
        if runtime.is_symlink():
            raise ValueError(f"runtime input root is a symlink: {prefix}")
        if runtime.exists():
            if not runtime.is_dir():
                raise ValueError(f"runtime input root is not a directory: {prefix}")
            paths.update(prefix + "/" + path for path in _walk_source_paths(runtime, skip_root_transients=False))
    return paths


def verify_candidate_manifest(manifest_path: Path, expected_digest: str, root: Path,
                              tracked_paths: list[str] | None = None,
                              base_entries: list[dict[str, str]] | None = None) -> dict[str, Any]:
    """Verify an independently pinned, complete regular-file source inventory."""
    if not manifest_path.is_absolute():
        raise ValueError("candidate manifest path must be absolute")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_digest):
        raise ValueError("expected candidate digest must be 64 lowercase hexadecimal characters")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read candidate manifest: {error}") from error
    if not isinstance(manifest, dict) or manifest.get("digest_format") != DIGEST_FORMAT:
        raise ValueError("unknown or missing candidate digest format")
    entries = manifest.get("files")
    aggregate = snapshot_digest(entries)
    declared = manifest.get("candidate_sha256")
    if not isinstance(declared, str) or not re.fullmatch(r"[0-9a-f]{64}", declared):
        raise ValueError("manifest candidate_sha256 must be a lowercase SHA-256")
    if aggregate != declared:
        raise ValueError("manifest aggregate digest does not match candidate_sha256")
    if declared != expected_digest:
        raise ValueError("manifest digest differs from the expected candidate receipt")

    root = root.resolve(strict=True)
    declared_paths = {entry["path"] for entry in entries}
    actual_paths = _regular_source_paths(root)
    if declared_paths != actual_paths:
        missing = sorted(actual_paths - declared_paths)
        vanished = sorted(declared_paths - actual_paths)
        raise ValueError(f"source tree differs from complete candidate path set (unmanifested={missing[:5]}, missing={vanished[:5]})")
    required_paths = set(tracked_paths or [])
    if required_paths - declared_paths:
        raise ValueError(f"candidate manifest omits required source paths: {sorted(required_paths - declared_paths)[:5]}")

    if base_entries is not None:
        base_by_path = {entry["path"]: entry for entry in base_entries}
        candidate_by_path = {entry["path"]: entry for entry in entries}
        for path, base_entry in base_by_path.items():
            candidate_entry = candidate_by_path.get(path)
            if candidate_entry is None or candidate_entry["mode"] != base_entry["mode"] or candidate_entry["sha256"] != base_entry["sha256"]:
                raise ValueError(f"old seeded source changed outside the two-file T29 allowlist: {path}")

    errors: list[str] = []
    for entry in entries:
        path = _safe_regular_file(root, entry["path"])
        actual_mode = oct(stat.S_IMODE(path.stat().st_mode))
        if actual_mode != entry["mode"]:
            errors.append(f"{entry['path']}: mode changed")
        if hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
            errors.append(f"{entry['path']}: content changed")
    if errors:
        raise ValueError("candidate files differ from manifest: " + "; ".join(errors[:8]))
    return {
        "digest_format": DIGEST_FORMAT,
        "candidate_sha256": declared,
        "file_count": len(entries),
        "required_path_count": len(required_paths),
        "candidate_paths": sorted(declared_paths),
    }


def verify_final_source_pin(manifest_path: Path, expected_digest: str, root: Path,
                           initial_pin: dict[str, Any]) -> dict[str, Any]:
    """Recheck path, mode and byte identity against the initial candidate after work."""
    if (not isinstance(initial_pin, dict)
            or initial_pin.get("candidate_sha256") != expected_digest
            or not isinstance(initial_pin.get("file_count"), int)):
        raise ValueError("initial source pin is missing or bound to another candidate")
    final_pin = verify_candidate_manifest(manifest_path, expected_digest, root)
    if (final_pin["candidate_sha256"] != initial_pin["candidate_sha256"]
            or final_pin["file_count"] != initial_pin["file_count"]
            or final_pin["candidate_paths"] != initial_pin.get("candidate_paths")):
        raise ValueError("final source pin differs from the initial candidate")
    return final_pin


def _source_identity(root: Path, pinned_paths: set[str]) -> dict[str, Any]:
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(root))
    modules = [
        importlib.import_module(name)
        for name in (
            "harness",
            "harness.kernel.bootstrap",
            "harness.kernel.commands",
            "harness.sdk.testing.scripted",
            "harness.plugins.tools.fs.tools",
        )
    ]
    loaded_modules = {
        name: module for name, module in tuple(sys.modules.items())
        if name == "harness" or name.startswith("harness.")
    }
    loaded_origins, namespace_paths = _loaded_import_facts(loaded_modules)
    validate_import_origins(loaded_origins, root, pinned_paths, namespace_paths=namespace_paths)
    origins = {module.__name__: loaded_origins[module.__name__] for module in modules}
    dist = importlib.metadata.distribution("harness-zero")
    package_root = Path(modules[0].__file__).resolve().parent
    package_files: list[dict[str, str]] = []
    for path in sorted(package_root.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        relative = path.resolve().relative_to(root).as_posix()
        if relative not in pinned_paths:
            raise ValueError(f"source package file is missing from candidate pin: {relative}")
        package_files.append({
            "path": relative,
            "mode": oct(stat.S_IMODE(path.stat().st_mode)),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        })
    return {
        "python_version": ".".join(map(str, sys.version_info[:3])),
        "python_executable": str(Path(sys.executable).resolve()),
        "distribution": {"name": "harness-zero", "version": dist.version},
        "import_origins": origins,
        "namespace_search_locations": namespace_paths,
        "source_package_byte_scope": "source checkout files under imported harness package directory",
        "source_package_byte_sha256": snapshot_digest(package_files),
        "source_package_files": len(package_files),
    }


def _runner_source_path(root: Path, pinned_paths: set[str]) -> str:
    runner = Path(__file__).resolve(strict=True)
    try:
        relative = runner.relative_to(root.resolve(strict=True)).as_posix()
    except ValueError as error:
        raise ValueError(f"qualification runner is outside the selected candidate root: {runner}") from error
    if relative not in pinned_paths:
        raise ValueError(f"qualification runner is not included in the candidate pin: {relative}")
    return relative


def preflight(manifest_path: Path, expected_digest: str, root: Path) -> dict[str, Any]:
    validate_python_version(sys.version_info[:3])
    root = root.resolve(strict=True)
    pin = verify_candidate_manifest(manifest_path, expected_digest, root)
    pinned_paths = set(pin["candidate_paths"])
    runner_source_path = _runner_source_path(root, pinned_paths)
    source = _source_identity(root, pinned_paths)
    return {
        "valid": True,
        "mode": "source-only-offline-preflight",
        "root": str(root),
        "candidate": pin,
        "runner_source_path": runner_source_path,
        "source": source,
        "qualification_limits": [
            "No artifact, frozen binary, native sandbox, live provider, cancellation-trial or restart-trial evidence.",
            "ScriptedProvider is an explicitly registered offline fixture, not provider support evidence.",
            "Process resource samples count direct children only, not a complete descendant process tree.",
        ],
    }


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def evaluate_soak(report: dict[str, Any]) -> dict[str, Any]:
    """Evaluate canonical turn/tool/job/interaction facts and real elapsed/resource samples."""
    errors: list[str] = []
    review_required: list[str] = []
    started = report.get("started_monotonic")
    ended = report.get("ended_monotonic")
    checkpoints = report.get("monotonic_checkpoints")
    elapsed = float(ended) - float(started) if _number(started) and _number(ended) else -1.0
    if elapsed < MIN_ELAPSED_S:
        errors.append(f"duration is below the required {MIN_ELAPSED_S:.0f} real monotonic seconds")
    if not isinstance(checkpoints, list) or not checkpoints or any(not _number(item) for item in checkpoints):
        errors.append("monotonic checkpoints are missing or invalid")
    else:
        clock_values = [float(started), *(float(item) for item in checkpoints), float(ended)] if _number(started) and _number(ended) else []
        if any(later < earlier for earlier, later in zip(clock_values, clock_values[1:], strict=False)):
            errors.append("monotonic clock moved backwards")

    acknowledged = report.get("acknowledged_turns")
    expected_prompts = report.get("expected_prompts")
    records_by_session = report.get("records_by_session")
    if not isinstance(acknowledged, list) or len(acknowledged) < MIN_TURNS:
        errors.append(f"fewer than {MIN_TURNS} actual turns were acknowledged")
        acknowledged = acknowledged if isinstance(acknowledged, list) else []
    if not isinstance(expected_prompts, dict) or len(expected_prompts) < 2:
        errors.append("mixed-session evidence requires at least two expected sessions")
        expected_prompts = expected_prompts if isinstance(expected_prompts, dict) else {}
    if not isinstance(records_by_session, dict):
        errors.append("canonical session records are missing")
        records_by_session = {}
    elif set(records_by_session) != set(expected_prompts):
        errors.append("canonical record session set differs from mixed-session expectation")

    ack_by_session: dict[str, list[str]] = {}
    for row in acknowledged:
        if not isinstance(row, dict) or not isinstance(row.get("session_id"), str) or not isinstance(row.get("prompt"), str):
            errors.append("an acknowledged turn lacks its canonical session/prompt identity")
            continue
        ack_by_session.setdefault(row["session_id"], []).append(row["prompt"])

    actual_calls: list[str] = []
    actual_results: list[str] = []
    tool_call_names: dict[str, list[str]] = {}
    valid_read_results: dict[str, int] = {}
    for session_id, prompts in expected_prompts.items():
        if not isinstance(prompts, list) or any(not isinstance(prompt, str) for prompt in prompts):
            errors.append(f"expected prompts are invalid for session {session_id}")
            continue
        records = records_by_session.get(session_id)
        if not isinstance(records, list):
            errors.append(f"canonical records are missing for session {session_id}")
            continue
        actual_prompts = [
            str(record.get("content", ""))
            for record in records
            if isinstance(record, dict) and record.get("kind") == "user.message" and record.get("role") == "user"
        ]
        if actual_prompts != prompts:
            errors.append(f"acknowledged turn records are missing, extra or reordered for session {session_id}")
        if ack_by_session.get(session_id, []) != prompts:
            errors.append(f"acknowledgement receipts do not match persisted turns for session {session_id}")
        completed = sum(
            isinstance(record, dict) and record.get("kind") == "turn.completed" for record in records
        )
        if completed != len(prompts):
            errors.append(f"missing terminal acknowledgement records for session {session_id}")
        for record in records:
            if not isinstance(record, dict):
                continue
            for call in record.get("tool_calls") or []:
                if isinstance(call, dict) and isinstance(call.get("id"), str) and call["id"]:
                    actual_calls.append(call["id"])
                    tool_call_names.setdefault(call["id"], []).append(str(call.get("name", "")))
            for result in record.get("tool_results") or []:
                if isinstance(result, dict) and isinstance(result.get("tool_use_id"), str) and result["tool_use_id"]:
                    result_id = result["tool_use_id"]
                    actual_results.append(result_id)
                    content = result.get("content")
                    if (result.get("is_error") is False
                            and content == [{"type": "text", "text": EXPECTED_VIEW_FILE_TEXT}]):
                        valid_read_results[result_id] = valid_read_results.get(result_id, 0) + 1
                    else:
                        errors.append(f"view_file tool result content is missing, failed or unexpected for {result_id}")
            metadata = record.get("metadata")
            if (record.get("kind") == "tool.result" and not record.get("tool_results")
                    and isinstance(metadata, dict) and metadata.get("call_id")):
                actual_results.append(str(metadata["call_id"]))
    if set(ack_by_session) != set(expected_prompts):
        errors.append("acknowledgement session set differs from mixed-session expectation")
    if len(actual_calls) != len(set(actual_calls)):
        errors.append("canonical transcript contains duplicate tool call IDs")
    if len(actual_results) != len(set(actual_results)):
        errors.append("canonical transcript contains duplicate tool result IDs")
    dangling = sorted(set(actual_calls) - set(actual_results))
    orphan_results = sorted(set(actual_results) - set(actual_calls))
    expected_tool_ids = report.get("expected_tool_ids")
    if dangling:
        errors.append(f"dangling tool call IDs: {dangling[:5]}")
    if orphan_results:
        errors.append(f"orphan tool result IDs: {orphan_results[:5]}")
    expected_ids_valid = isinstance(expected_tool_ids, list) and all(isinstance(item, str) for item in expected_tool_ids)
    if (not expected_ids_valid or len(expected_tool_ids) != len(set(expected_tool_ids))
            or set(actual_calls) != set(expected_tool_ids)):
        errors.append("canonical tool call IDs differ from the independently expected tool IDs")
    if expected_ids_valid:
        for tool_id in expected_tool_ids:
            if tool_call_names.get(tool_id) != ["view_file"]:
                errors.append(f"expected actual builtin view_file call is missing or ambiguous for {tool_id}")
            if valid_read_results.get(tool_id) != 1:
                errors.append(f"view_file tool result content is not independently verified for {tool_id}")
    if report.get("offline_fixture") is not True:
        errors.append("soak did not identify its provider as an explicit offline fixture")
    expected_requests = len(acknowledged) + len(expected_tool_ids) if expected_ids_valid else -1
    if report.get("provider_request_count") != expected_requests or report.get("provider_responses_remaining") != 0:
        errors.append("offline fixture request/response consumption is incomplete or inconsistent")

    if report.get("pending_jobs") != []:
        errors.append("orphan or pending jobs remain at quiescence")
    if report.get("pending_interactions") != []:
        errors.append("dangling interactions remain at quiescence")
    if report.get("child_exit_code") != 0:
        errors.append("owned worker did not exit successfully")
    if report.get("runtime_unloaded") is not True:
        errors.append("runtime did not confirm graceful plugin unload")
    if report.get("process_metric_scope") != PROCESS_METRIC_SCOPE:
        errors.append("process metrics do not declare their direct-child-only scope")

    samples = report.get("metric_samples")
    required_metrics = ("rss_bytes", "fd_count", "owned_child_count")
    if not isinstance(samples, list) or len(samples) < 2:
        errors.append("RSS/FD/owned-child metric trend is missing")
        samples = samples if isinstance(samples, list) else []
    for sample in samples:
        if (not isinstance(sample, dict) or not _number(sample.get("elapsed_s"))
                or any(not _number(sample.get(metric)) or sample[metric] < 0 for metric in required_metrics)):
            errors.append("RSS/FD/owned-child metrics are unavailable or invalid")
            break
    if len(samples) >= 2 and all(isinstance(sample, dict) and _number(sample.get("elapsed_s")) for sample in samples):
        sample_times = [float(sample["elapsed_s"]) for sample in samples]
        if any(later < earlier for earlier, later in zip(sample_times, sample_times[1:], strict=False)):
            errors.append("resource sample cadence moved backwards")
        if any(sample_time > elapsed for sample_time in sample_times):
            errors.append("resource sample is outside the measured soak interval")
    if len(samples) >= 2 and all(
        isinstance(sample, dict) and all(_number(sample.get(metric)) for metric in required_metrics)
        for sample in samples
    ):
        for metric in required_metrics:
            if max(sample[metric] for sample in samples) > samples[0][metric]:
                review_required.append(f"observed {metric} growth needs investigation; no numeric growth threshold is defined")

    if review_required:
        status = "review-required"
    elif errors:
        status = "failed"
    else:
        status = "evaluator-pass"
    return {
        "status": status,
        "passed": status == "evaluator-pass",
        "elapsed_s": elapsed,
        "turn_count": len(acknowledged),
        "session_count": len(expected_prompts),
        "tool_call_count": len(actual_calls),
        "dangling_tool_ids": dangling,
        "orphan_tool_result_ids": orphan_results,
        "growth_review_required": bool(review_required),
        "review_required": review_required,
        "errors": errors,
    }


def _mac_process_metrics(pid: int) -> dict[str, int]:
    ps_path = next((path for path in (Path("/bin/ps"), Path("/usr/bin/ps")) if path.is_file()), None)
    if ps_path is None:
        raise RuntimeError("ps is unavailable; RSS and owned-child samples are required")
    try:
        listing = subprocess.run(
            [str(ps_path), "-axo", "pid=,ppid=,rss=,comm="],
            check=True,
            text=True,
            capture_output=True,
            timeout=PROCESS_SAMPLE_TIMEOUT_S,
        )
    except subprocess.CalledProcessError as error:
        detail = error.stderr.strip() if error.stderr else "no diagnostic"
        raise RuntimeError(f"ps failed; RSS and owned-child samples unavailable: {detail}") from error
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("ps timed out; RSS and owned-child samples unavailable") from error
    rss_kb: int | None = None
    child_count = 0
    for line in listing.stdout.splitlines():
        fields = line.split(None, 3)
        if len(fields) != 4:
            continue
        try:
            child_pid, parent_pid, rss = map(int, fields[:3])
        except ValueError:
            continue
        if child_pid == pid:
            rss_kb = rss
        elif parent_pid == pid and Path(fields[3]).name != "ps":
            child_count += 1
    if rss_kb is None:
        raise RuntimeError("ps did not report the soak worker RSS")
    fd_count: int | None = None
    lsof = next((path for path in (Path("/usr/sbin/lsof"), Path("/usr/bin/lsof")) if path.is_file()), None)
    if lsof is not None:
        try:
            opened = subprocess.run(
                [str(lsof), "-nP", "-p", str(pid), "-Ff"],
                check=False,
                text=True,
                capture_output=True,
                timeout=PROCESS_SAMPLE_TIMEOUT_S,
            )
            if opened.returncode == 0:
                fd_count = sum(line.startswith("f") for line in opened.stdout.splitlines())
        except subprocess.TimeoutExpired:
            fd_count = None
    if fd_count is None:
        try:
            fd_count = len(list(os.scandir("/dev/fd")))
        except OSError as error:
            raise RuntimeError(f"lsof and /dev/fd are unavailable for FD sampling: {error}") from error
    return {"rss_bytes": rss_kb * 1024, "fd_count": fd_count, "owned_child_count": child_count}


async def _sleep_until(deadline: float) -> None:
    delay = deadline - time.monotonic()
    if delay > 0:
        await asyncio.sleep(delay)


def _require_fd_evidence_capability() -> None:
    if sys.platform != "darwin":
        raise RuntimeError("native FD evidence capture is supported only on the verified macOS host")
    if (not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW")
            or os.open not in os.supports_dir_fd or os.mkdir not in os.supports_dir_fd
            or os.stat not in os.supports_dir_fd or os.stat not in os.supports_follow_symlinks):
        raise RuntimeError("native FD evidence capture requires directory-relative no-follow filesystem support")
    if not any(os.path.isfile(path) and os.access(path, os.X_OK) for path in FD_LSOF_PATHS):
        raise RuntimeError("native FD evidence capture requires a verified executable lsof")


def _capture_fd_evidence(output: Path, *, phase: str, index: int | None) -> dict[str, Any]:
    captured = collect_fd_lsof()
    payload = encode_fd_capture(
        captured["raw"], expected_pid=os.getpid(), phase=phase, index=index,
        started_monotonic_ns=captured["started_monotonic_ns"],
        finished_monotonic_ns=captured["finished_monotonic_ns"],
        observer_owned_fds=captured["observer_owned_fds"],
    )
    return _write_fd_capture(
        output, payload, expected_pid=os.getpid(), expected_phase=phase, expected_index=index,
    )


def _offline_provider(responses: list[list[Any]]):
    from harness.sdk.testing.scripted import ScriptedProvider
    from harness.sdk.types import ModelInfo

    class OfflineScriptedProvider(ScriptedProvider):
        offline_fixture = True

    return OfflineScriptedProvider(
        responses=responses,
        models=[ModelInfo(id="t29-offline", display_name="T29 offline fixture")],
        strict_exhaustion=True,
    )


async def _run_offline_soak(root: Path, output: Path, candidate_paths: set[str],
                            *, fd_evidence: bool = False) -> dict[str, Any]:
    from harness.kernel.bootstrap import bootstrap_kernel
    from harness.sdk.types import Done, TextDelta, ToolCallEnd

    if type(fd_evidence) is not bool:
        raise ValueError("FD evidence mode must be a native boolean")
    if fd_evidence:
        _require_fd_evidence_capability()
        if os.path.lexists(output / "fd-evidence"):
            raise ValueError("FD evidence directory must be fresh for the enabled worker")
    workspace = output / "workspace"
    config_dir = output / "config"
    with (workspace / "soak-note.txt").open("x", encoding="utf-8") as note:
        note.write("T29 offline source-soak fixture\n")
    runtime = await bootstrap_kernel(workspace=workspace, config_dir=config_dir, resume_session=False)
    report: dict[str, Any] | None = None
    fd_captures: list[dict[str, Any]] = []
    try:
        loaded_modules = {
            name: module for name, module in tuple(sys.modules.items())
            if name == "harness" or name.startswith("harness.")
        }
        loaded_origins, loaded_namespace_paths = _loaded_import_facts(loaded_modules)
        validate_import_origins(
            loaded_origins, root, candidate_paths, namespace_paths=loaded_namespace_paths
        )
        if "view_file" not in runtime.registry.tools:
            raise RuntimeError("the real builtin view_file tool did not register")
        provider = _offline_provider([])
        if getattr(provider, "offline_fixture", False) is not True:
            raise RuntimeError("soak provider is not explicitly marked as an offline fixture")
        expected_tool_ids: list[str] = []
        responses: list[list[Any]] = []
        prompts_by_session: dict[str, list[str]] = {}
        ack: list[dict[str, str]] = []
        session_ids: list[str] = []
        for ordinal in range(2):
            session = await runtime.commands.sessions_create(metadata={"t29": "offline-source-soak", "ordinal": ordinal})
            session_ids.append(session["session_id"])
            prompts_by_session[session["session_id"]] = []
        for index in range(SCHEDULED_TURNS):
            if index % 10 == 0:
                call_id = f"t29-read-{index:03d}"
                expected_tool_ids.append(call_id)
                responses.extend([
                    [ToolCallEnd(id=call_id, name="view_file", arguments={"file_path": "soak-note.txt"}), Done(stop_reason="tool_use")],
                    [TextDelta(text=f"read completed for {index}"), Done(stop_reason="end_turn")],
                ])
            else:
                responses.append([TextDelta(text=f"offline turn {index} completed"), Done(stop_reason="end_turn")])
        provider.responses = responses
        runtime.registry.register_provider(provider, plugin_name="t29_offline_fixture")

        started = time.monotonic()
        checkpoints = [started]
        metric_samples: list[dict[str, Any]] = []
        next_sample_at = started
        for index in range(SCHEDULED_TURNS):
            await _sleep_until(next_sample_at)
            sample_at = time.monotonic()
            sample_row = {"elapsed_s": sample_at - started, **_mac_process_metrics(os.getpid())}
            if fd_evidence:
                reference = _capture_fd_evidence(output, phase="cadence", index=index)
                sample_row["fd_evidence_capture"] = reference
                fd_captures.append(reference)
            metric_samples.append(sample_row)
            checkpoints.append(sample_at)
            session_id = session_ids[index % len(session_ids)]
            prompt = f"T29 offline mixed-session turn {index:03d}"
            result = await runtime.commands.submit_turn(
                session_id=session_id,
                prompt=prompt,
                options={"provider": "scripted", "model": "t29-offline"},
            )
            if getattr(result, "error", None):
                raise RuntimeError(f"offline fixture turn failed: {result.error}")
            prompts_by_session[session_id].append(prompt)
            ack.append({"session_id": session_id, "prompt": prompt})
            fetched = await runtime.commands.sessions_get(session_id=session_id)
            if fetched["busy"]:
                raise RuntimeError(f"session {session_id} remained busy after acknowledged submit_turn")
            next_sample_at = time.monotonic() + TURN_CADENCE_S
            if fd_evidence and index == 0:
                fd_captures.append(_capture_fd_evidence(output, phase="after_first_turn", index=None))

        await _sleep_until(max(started + MIN_ELAPSED_S, next_sample_at))
        end_sample_at = time.monotonic()
        end_sample = {"elapsed_s": end_sample_at - started, **_mac_process_metrics(os.getpid())}
        if fd_evidence:
            reference = _capture_fd_evidence(output, phase="cadence", index=SCHEDULED_TURNS)
            end_sample["fd_evidence_capture"] = reference
            fd_captures.append(reference)
        metric_samples.append(end_sample)
        checkpoints.append(end_sample_at)
        records_by_session: dict[str, list[dict[str, Any]]] = {}
        for session_id in session_ids:
            fetched = await runtime.commands.sessions_get(session_id=session_id)
            records_by_session[session_id] = fetched.get("records", [])
            if fetched["busy"]:
                raise RuntimeError(f"session {session_id} remained busy at final quiescence")
        jobs = runtime.registry.get_service("jobs")
        if jobs is None or not callable(getattr(jobs, "list_tasks", None)):
            raise RuntimeError("actual jobs service unavailable; cannot claim quiescence")
        pending_jobs = [
            {"session_id": session_id, **job}
            for session_id in session_ids
            for job in jobs.list_tasks(session_id=session_id)
            if job.get("state") in {"starting", "running"}
        ]
        pending_interactions = await runtime.commands.interactions_list()
        expected_provider_requests = SCHEDULED_TURNS + len(expected_tool_ids)
        if provider.remaining != 0 or len(provider.requests) != expected_provider_requests:
            raise RuntimeError(
                "offline fixture consumption mismatch: "
                f"requests={len(provider.requests)} expected={expected_provider_requests} remaining={provider.remaining}"
            )
        end = time.monotonic()
        checkpoints.append(end)
        report = {
            "started_monotonic": started,
            "ended_monotonic": end,
            "monotonic_checkpoints": checkpoints,
            "acknowledged_turns": ack,
            "expected_prompts": prompts_by_session,
            "records_by_session": records_by_session,
            "expected_tool_ids": expected_tool_ids,
            "offline_fixture": True,
            "provider_request_count": len(provider.requests),
            "provider_responses_remaining": provider.remaining,
            "pending_jobs": pending_jobs,
            "pending_interactions": pending_interactions,
            "metric_samples": metric_samples,
            "worker_pid": os.getpid(),
            "fd_evidence_mode": fd_evidence,
            "fd_evidence_captures": list(fd_captures),
            "loaded_import_origins": loaded_origins,
            "loaded_namespace_search_locations": loaded_namespace_paths,
            "process_metric_scope": PROCESS_METRIC_SCOPE,
        }
    finally:
        if fd_evidence:
            primary_error = sys.exc_info()[1]
            registry_logger = logging.getLogger("harness.kernel.registry")
            teardown_errors: list[str] = []

            class TeardownCapture(logging.Handler):
                def emit(self, record: logging.LogRecord) -> None:
                    teardown_errors.append(record.getMessage())

            capture = TeardownCapture(level=logging.WARNING)
            registry_logger.addHandler(capture)
            unload_error: BaseException | None = None
            try:
                try:
                    await runtime.registry.unload_all()
                except BaseException as error:
                    unload_error = error
            finally:
                registry_logger.removeHandler(capture)
            if teardown_errors and unload_error is None:
                unload_error = RuntimeError("runtime plugin teardown reported errors: " + "; ".join(teardown_errors))
            if not runtime.registry._loaded:
                try:
                    fd_captures.append(_capture_fd_evidence(output, phase="after_unload", index=None))
                    if report is not None:
                        report["fd_evidence_captures"] = list(fd_captures)
                except BaseException as error:
                    if primary_error is not None:
                        primary_error.add_note(f"post-unload FD evidence capture failed: {type(error).__name__}: {error}")
                    elif unload_error is None:
                        unload_error = error
            if unload_error is not None:
                if primary_error is not None:
                    primary_error.add_note(f"runtime unload failed: {type(unload_error).__name__}: {unload_error}")
                else:
                    raise unload_error
        else:
            registry_logger = logging.getLogger("harness.kernel.registry")
            teardown_errors: list[str] = []

            class TeardownCapture(logging.Handler):
                def emit(self, record: logging.LogRecord) -> None:
                    teardown_errors.append(record.getMessage())

            capture = TeardownCapture(level=logging.WARNING)
            registry_logger.addHandler(capture)
            try:
                await runtime.registry.unload_all()
            finally:
                registry_logger.removeHandler(capture)
            if teardown_errors:
                raise RuntimeError("runtime plugin teardown reported errors: " + "; ".join(teardown_errors))
    if report is None:
        raise RuntimeError("soak worker produced no report")
    report["runtime_unloaded"] = not runtime.registry._loaded
    if not report["runtime_unloaded"]:
        raise RuntimeError("runtime registry retained loaded plugins after graceful unload")
    loaded_modules = {
        name: module for name, module in tuple(sys.modules.items())
        if name == "harness" or name.startswith("harness.")
    }
    final_origins, final_namespace_paths = _loaded_import_facts(loaded_modules)
    validate_import_origins(
        final_origins, root, candidate_paths, namespace_paths=final_namespace_paths
    )
    report["loaded_import_origins"] = final_origins
    report["loaded_namespace_search_locations"] = final_namespace_paths
    return report


def _write_json_exclusive(path: Path, value: dict[str, Any]) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False)
        stream.write("\n")


def _write_worker_launch_receipt(
    output: Path, *, worker_pid: int, parent_pid: int, argv: list[str], root: Path,
    manifest: Path, expected_digest: str, fd_evidence: bool,
) -> dict[str, Any]:
    if (type(worker_pid) is not int or worker_pid <= 0 or type(parent_pid) is not int or parent_pid <= 0
            or type(fd_evidence) is not bool or type(argv) is not list
            or any(type(part) is not str or not part for part in argv)
            or type(expected_digest) is not str
            or re.fullmatch(r"[0-9a-f]{64}", expected_digest) is None):
        raise ValueError("invalid worker launch receipt identity")
    output = Path(output)
    if (not output.is_absolute() or output.resolve(strict=True) != output
            or stat.S_IMODE(output.stat().st_mode) != 0o700 or output.stat().st_uid != os.getuid()):
        raise ValueError("worker launch receipt requires an owned canonical private output directory")
    receipt = {
        "schema": "t29.worker-launch.v1", "receipt": "worker-launched",
        "parent_pid": parent_pid, "worker_pid": worker_pid, "argv": list(argv),
        "root": str(Path(root).resolve(strict=False)), "output": str(output),
        "candidate_manifest": str(Path(manifest).resolve(strict=False)),
        "candidate_sha256": expected_digest, "fd_evidence_mode": fd_evidence,
    }
    payload = _fd_json_bytes(receipt)
    if len(payload) > FD_LAUNCH_RECEIPT_BYTE_LIMIT:
        raise ValueError("worker launch receipt exceeds byte limit")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(output / "worker-launch-receipt.json", flags, 0o600)
    try:
        os.fchmod(fd, 0o600)
        metadata = os.fstat(fd)
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid()
                or stat.S_IMODE(metadata.st_mode) != 0o600):
            raise ValueError("worker launch receipt must be an owned regular 0600 file")
        view = memoryview(payload)
        offset = 0
        while offset < len(view):
            written = os.write(fd, view[offset:offset + FD_HELPER_CHUNK_BYTES])
            if written <= 0:
                raise OSError("short write to worker launch receipt")
            offset += written
    finally:
        os.close(fd)
    return receipt


def validate_fd_evidence(
    report: dict[str, Any], output: Path, *, actual_worker_pid: int,
    launch_receipt: dict[str, Any], expected_parent_pid: int, expected_argv: list[str],
    expected_root: Path, expected_manifest: Path, expected_digest: str,
) -> dict[str, Any]:
    """Validate complete descriptor evidence against the parent's actual child handle."""
    if type(actual_worker_pid) is not int or actual_worker_pid <= 0:
        raise ValueError("actual launched worker PID is invalid")
    output = Path(output)
    try:
        output_metadata = output.lstat()
        if (not output.is_absolute() or output.resolve(strict=True) != output
                or not stat.S_ISDIR(output_metadata.st_mode) or output_metadata.st_uid != os.getuid()
                or stat.S_IMODE(output_metadata.st_mode) != 0o700):
            raise ValueError("FD evidence output anchor must be a canonical owned private directory")
    except OSError as error:
        raise ValueError("FD evidence output anchor is unavailable or unsafe") from error
    if (type(report) is not dict or report.get("worker_pid") != actual_worker_pid
            or type(report.get("worker_pid")) is not int or report.get("fd_evidence_mode") is not True):
        raise ValueError("worker report PID or FD evidence mode does not match parent intent")
    launch_path = output / "worker-launch-receipt.json"
    try:
        before = os.lstat(launch_path)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid()
                or stat.S_IMODE(before.st_mode) != 0o600
                or before.st_size > FD_LAUNCH_RECEIPT_BYTE_LIMIT):
            raise ValueError("worker launch receipt is not a bounded private regular file")
        fd = os.open(launch_path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
    except OSError as error:
        raise ValueError("worker launch receipt is missing or unsafe") from error
    try:
        opened = os.fstat(fd)
        if ((opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
                or not stat.S_ISREG(opened.st_mode) or opened.st_uid != os.getuid()
                or stat.S_IMODE(opened.st_mode) != 0o600
                or opened.st_size > FD_LAUNCH_RECEIPT_BYTE_LIMIT):
            raise ValueError("worker launch receipt changed during readback")
        payload = bytearray()
        while True:
            chunk = os.read(fd, min(FD_HELPER_CHUNK_BYTES, FD_LAUNCH_RECEIPT_BYTE_LIMIT + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
            if len(payload) > FD_LAUNCH_RECEIPT_BYTE_LIMIT:
                raise ValueError("worker launch receipt exceeds readback limit")
        after = os.fstat(fd)
        if ((after.st_dev, after.st_ino) != (opened.st_dev, opened.st_ino)
                or after.st_size != len(payload) or after.st_uid != os.getuid()
                or stat.S_IMODE(after.st_mode) != 0o600):
            raise ValueError("worker launch receipt changed during readback")
    finally:
        os.close(fd)
    try:
        on_disk = json.loads(bytes(payload).decode("ascii"), object_pairs_hook=_fd_unique_json_object)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError("worker launch receipt JSON is invalid") from error
    if (_fd_json_bytes(on_disk) != bytes(payload) or on_disk != launch_receipt
            or on_disk.get("worker_pid") != actual_worker_pid
            or on_disk.get("parent_pid") != expected_parent_pid
            or on_disk.get("argv") != expected_argv or on_disk.get("fd_evidence_mode") is not True
            or on_disk.get("root") != str(Path(expected_root).resolve(strict=False))
            or on_disk.get("output") != str(output)
            or on_disk.get("candidate_manifest") != str(Path(expected_manifest).resolve(strict=False))
            or on_disk.get("candidate_sha256") != expected_digest):
        raise ValueError("worker launch receipt disagrees with trusted parent launch facts")
    source_pin = report.get("source_pin")
    if (type(source_pin) is not dict or source_pin.get("root") != str(Path(expected_root).resolve(strict=False))
            or source_pin.get("output") != str(output)
            or source_pin.get("candidate_sha256_before") != expected_digest
            or source_pin.get("candidate_sha256_after") != expected_digest
            or source_pin.get("checked_after_soak") is not True):
        raise ValueError("worker FD report source/output anchors do not match parent pins")
    started, ended = report.get("started_monotonic"), report.get("ended_monotonic")
    samples, references = report.get("metric_samples"), report.get("fd_evidence_captures")
    if (not _number(started) or not _number(ended) or ended < started
            or type(samples) is not list or len(samples) != SCHEDULED_TURNS + 1
            or type(references) is not list or len(references) != SCHEDULED_TURNS + 3
            or report.get("runtime_unloaded") is not True
            or type(report.get("acknowledged_turns")) is not list or not report["acknowledged_turns"]):
        raise ValueError("FD report lacks full cadence or lifecycle coverage")
    if any(type(sample_row) is not dict or not _number(sample_row.get("elapsed_s")) for sample_row in samples):
        raise ValueError("FD report contains invalid linked metric sample timing")
    phases = [("cadence", 0), ("after_first_turn", None)]
    phases.extend(("cadence", index) for index in range(1, SCHEDULED_TURNS + 1))
    phases.append(("after_unload", None))
    if len({reference.get("path") for reference in references if type(reference) is dict}) != len(references):
        raise ValueError("FD report contains duplicate capture paths")
    decoded: list[dict[str, Any]] = []
    prior_finished = -1
    for reference, (phase, index) in zip(references, phases, strict=True):
        envelope = _read_fd_capture(
            output, reference, expected_phase=phase, expected_index=index,
            expected_pid=actual_worker_pid,
        )
        if envelope["started_monotonic_ns"] < prior_finished:
            raise ValueError("FD captures are out of monotonic phase order")
        prior_finished = envelope["finished_monotonic_ns"]
        decoded.append(envelope)
    cadence = [decoded[0], *decoded[2:-1]]
    if len(cadence) != SCHEDULED_TURNS + 1:
        raise ValueError("FD cadence coverage is not 121 captures")
    for index, (sample_row, envelope) in enumerate(zip(samples, cadence, strict=True)):
        if (type(sample_row) is not dict or sample_row.get("fd_evidence_capture") != references[0 if index == 0 else index + 1]
                or not _number(sample_row.get("elapsed_s"))):
            raise ValueError("FD cadence reference is not linked to its original metric sample")
        sample_ns = int((float(started) + float(sample_row["elapsed_s"])) * 1_000_000_000)
        if envelope["started_monotonic_ns"] + 1_000_000 < sample_ns:
            raise ValueError("FD cadence capture precedes its linked metric sample")
        if index < SCHEDULED_TURNS:
            next_ns = int((float(started) + float(samples[index + 1]["elapsed_s"])) * 1_000_000_000)
            if envelope["finished_monotonic_ns"] > next_ns + 1_000_000:
                raise ValueError("FD cadence capture extends beyond the next original sample")
        elif envelope["finished_monotonic_ns"] > int(float(ended) * 1_000_000_000):
            raise ValueError("final FD cadence capture extends beyond the original report end")
    first_turn, final_capture = decoded[1], decoded[-1]
    if (first_turn["started_monotonic_ns"] < decoded[0]["finished_monotonic_ns"]
            or first_turn["finished_monotonic_ns"] > decoded[2]["started_monotonic_ns"]
            or final_capture["started_monotonic_ns"] < int(float(ended) * 1_000_000_000)):
        raise ValueError("FD lifecycle capture timing does not match first-turn or unload phase")
    return {"valid": True, "worker_pid": actual_worker_pid, "capture_count": len(decoded),
            "cadence_count": len(cadence), "after_first_turn": True, "after_unload": True}


def _record_fd_evidence_evaluation(
    output: Path, report: dict[str, Any] | None, *, child_pid: int | None,
    launch_receipt: dict[str, Any] | None, parent_pid: int, command: list[str],
    root: Path, manifest: Path, expected_digest: str, reason: str | None = None,
) -> dict[str, Any]:
    try:
        if child_pid is None or launch_receipt is None:
            raise ValueError("parent did not retain an actual worker PID and launch receipt")
        result = validate_fd_evidence(
            report, output, actual_worker_pid=child_pid, launch_receipt=launch_receipt,
            expected_parent_pid=parent_pid, expected_argv=command, expected_root=root,
            expected_manifest=manifest, expected_digest=expected_digest,
        )
    except Exception as error:
        result = {"valid": False, "worker_pid": child_pid, "errors": [f"{type(error).__name__}: {error}"]}
    if reason is not None:
        result = {"valid": False, "worker_pid": child_pid,
                  "errors": [reason, *result.get("errors", [])]}
    _write_json_exclusive(output / "fd-evidence-evaluation-receipt.json", {
        "schema": "t29.fd-evidence-evaluation.v1", "fd_evidence_mode": True,
        "evaluation": result,
    })
    return result


def _worker_start_receipt(root: Path, manifest: Path, expected_digest: str, output: Path,
                          receipt_path: Path, parent_pid: int, *, fd_evidence: bool = False) -> dict[str, Any]:
    if type(fd_evidence) is not bool:
        raise ValueError("FD evidence mode must be a native boolean")
    if not output.is_absolute() or output.is_symlink() or not output.is_dir():
        raise ValueError("worker output must be an existing real directory owned by its parent")
    real_output = output.resolve(strict=True)
    if real_output != output.absolute():
        raise ValueError("worker output path is not canonical")
    real_root = root.resolve(strict=True)
    if real_output.is_relative_to(real_root):
        raise ValueError("worker output must be outside the source checkout")
    if parent_pid != os.getppid():
        raise ValueError("worker parent PID does not match its launching process")
    if not receipt_path.is_absolute() or receipt_path.is_symlink():
        raise ValueError("parent start receipt must be an absolute regular file")
    expected_receipt = real_output / "start-receipt.json"
    if not os.path.lexists(receipt_path):
        raise ValueError("parent start receipt is missing")
    if receipt_path.resolve(strict=True) != expected_receipt or not receipt_path.is_file():
        raise ValueError("parent start receipt is missing or outside the worker output")
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"parent start receipt is invalid: {error}") from error
    if (not isinstance(receipt, dict) or receipt.get("receipt") != "started"
            or receipt.get("parent_pid") != parent_pid
            or receipt.get("root") != str(real_root)
            or receipt.get("output") != str(real_output)
            or receipt.get("candidate_manifest") != str(manifest.resolve(strict=False))
            or receipt.get("candidate_sha256") != expected_digest):
        raise ValueError("parent start receipt does not bind this source, output and candidate")
    receipt_mode = receipt.get("fd_evidence_mode", False)
    if type(receipt_mode) is not bool or receipt_mode is not fd_evidence:
        raise ValueError("FD evidence mode disagrees between worker argv and start receipt")
    if fd_evidence and os.path.lexists(real_output / "fd-evidence"):
        raise ValueError("FD evidence directory must be fresh for the enabled worker")
    if os.path.lexists(real_output / "workspace/soak-note.txt"):
        raise ValueError("preexisting soak-note; refusing to overwrite worker workspace")
    for name in ("workspace", "home", "config", "tmp"):
        directory = real_output / name
        if directory.is_symlink() or not directory.is_dir() or directory.resolve(strict=True).parent != real_output:
            raise ValueError(f"worker {name} must be a fresh real directory inside its output")
        if next(os.scandir(directory), None) is not None:
            raise ValueError(f"worker {name} is not fresh; refusing existing files")
    if os.environ.get("HOME") != str(real_output / "home"):
        raise ValueError("worker HOME is not the fresh contained output directory")
    if os.environ.get("HARNESS_CONFIG_DIR") != str(real_output / "config"):
        raise ValueError("worker config is not the fresh contained output directory")
    if any(os.environ.get(name) != str(real_output / "tmp") for name in ("TMPDIR", "TEMP", "TMP")):
        raise ValueError("worker temporary directories are not fresh and contained")
    expected_python_bin = str(Path(sys.executable).absolute().parent)
    if os.environ.get("PATH", "").split(os.pathsep, 1)[0] != expected_python_bin:
        raise ValueError("worker PATH does not retain the active virtual-environment bin")
    if (os.environ.get("PYTHONPATH") != str(real_root)
            or os.environ.get("PYTHONNOUSERSITE") != "1"
            or os.environ.get("HARNESS_IMPORT_HERMES_KEYS") != "0"):
        raise ValueError("worker Python import environment is not isolated")
    for name in ("worker-preflight.json", "worker-report.json", "worker-failure.json"):
        if os.path.lexists(real_output / name):
            raise ValueError(f"worker result already exists: {name}")
    return receipt


def _worker_main(root: Path, manifest: Path, expected_digest: str, output: Path,
                 start_receipt: Path, parent_pid: int, *, fd_evidence: bool = False) -> int:
    _worker_start_receipt(root, manifest, expected_digest, output, start_receipt, parent_pid,
                          fd_evidence=fd_evidence)
    root = root.resolve(strict=True)
    output = output.resolve(strict=True)
    try:
        preflight_result = preflight(manifest, expected_digest, root)
        _write_json_exclusive(output / "worker-preflight.json", preflight_result)
        candidate_paths = set(preflight_result["candidate"]["candidate_paths"])
        report = asyncio.run(_run_offline_soak(root, output, candidate_paths, fd_evidence=fd_evidence))
        final_pin = verify_final_source_pin(
            manifest, expected_digest, root, preflight_result["candidate"]
        )
        current_modules = {
            name: module for name, module in tuple(sys.modules.items())
            if name == "harness" or name.startswith("harness.")
        }
        current_origins, current_namespace_paths = _loaded_import_facts(current_modules)
        if (current_origins != report["loaded_import_origins"]
                or current_namespace_paths != report["loaded_namespace_search_locations"]):
            raise ValueError("loaded import facts changed after the offline soak")
        validate_import_origins(
            current_origins, root, candidate_paths, namespace_paths=current_namespace_paths
        )
        report["source_pin"] = {
            "root": str(root),
            "output": str(output),
            "candidate_sha256_before": preflight_result["candidate"]["candidate_sha256"],
            "candidate_sha256_after": final_pin["candidate_sha256"],
            "file_count_before": preflight_result["candidate"]["file_count"],
            "file_count_after": final_pin["file_count"],
            "checked_after_soak": True,
        }
        report["child_exit_code"] = 0
        evaluation = evaluate_soak(report)
        report["evaluation"] = evaluation
        _write_json_exclusive(output / "worker-report.json", report)
        return 0
    except Exception as error:
        _write_json_exclusive(output / "worker-failure.json", {
            "status": "blocked",
            "error_type": type(error).__name__,
            "error": str(error),
        })
        raise


def _isolated_environment(config: Path, home: Path, tmp: Path, root: Path) -> dict[str, str]:
    python_bin = str(Path(sys.executable).absolute().parent)
    return {
        "HOME": str(home),
        "HARNESS_CONFIG_DIR": str(config),
        "TMPDIR": str(tmp),
        "TEMP": str(tmp),
        "TMP": str(tmp),
        "PATH": f"{python_bin}:/usr/bin:/bin:/usr/sbin:/sbin",
        "PYTHONPATH": str(root),
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "HARNESS_IMPORT_HERMES_KEYS": "0",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
    }


def _execute(root: Path, manifest: Path, expected_digest: str, output: Path,
             preflight_result: dict[str, Any], *, fd_evidence: bool = False) -> int:
    if type(fd_evidence) is not bool:
        raise ValueError("FD evidence mode must be a native boolean")
    if fd_evidence:
        _require_fd_evidence_capability()
    if not output.is_absolute():
        raise ValueError("--output must be an absolute new evidence directory")
    root = root.resolve(strict=True)
    output_parent = output.parent.resolve(strict=True)
    output = output_parent / output.name
    if not output.name or os.path.lexists(output):
        raise ValueError("--output must name an unused new evidence directory")
    if output.is_relative_to(root):
        raise ValueError("soak evidence output must be outside the source checkout")
    output.mkdir(mode=0o700, exist_ok=False)
    started_wall = datetime.now(UTC).isoformat()
    started_monotonic = time.monotonic()
    workspace, config, home = output / "workspace", output / "config", output / "home"
    temp_dir = output / "tmp"
    home.mkdir(mode=0o700)
    workspace.mkdir(mode=0o700)
    config.mkdir(mode=0o700)
    temp_dir.mkdir(mode=0o700)
    start_receipt = {
        "receipt": "started",
        "parent_pid": os.getpid(),
        "started_utc": started_wall,
        "started_monotonic": started_monotonic,
        "root": str(root),
        "output": str(output),
        "candidate_manifest": str(manifest.resolve(strict=False)),
        "candidate_sha256": preflight_result["candidate"]["candidate_sha256"],
        "candidate_file_count": preflight_result["candidate"]["file_count"],
        "preflight": preflight_result,
        "operation": "120 fixed-cadence turns and at least 7200 real monotonic seconds",
        "offline_provider": "explicit ScriptedProvider fixture; no vendor credentials or network use",
        "fd_evidence_mode": fd_evidence,
    }
    _write_json_exclusive(output / "start-receipt.json", start_receipt)
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--worker",
        "--execute",
        "--root",
        str(root),
        "--candidate-manifest",
        str(manifest),
        "--expected-digest",
        expected_digest,
        "--output",
        str(output),
        "--start-receipt",
        str(output / "start-receipt.json"),
        "--parent-pid",
        str(os.getpid()),
    ]
    if fd_evidence:
        command.append("--fd-evidence")
    env = _isolated_environment(config, home, temp_dir, root)
    stdout_path, stderr_path = output / "worker.stdout", output / "worker.stderr"
    child: subprocess.Popen[bytes] | None = None
    worker_launch_receipt: dict[str, Any] | None = None
    signal_sent: int | None = None
    timed_out = False
    try:
        with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
            child = subprocess.Popen(command, cwd=workspace, env=env, stdin=subprocess.DEVNULL,
                                    stdout=stdout, stderr=stderr, close_fds=True)
            if fd_evidence:
                worker_launch_receipt = _write_worker_launch_receipt(
                    output, worker_pid=child.pid, parent_pid=os.getpid(), argv=command,
                    root=root, manifest=manifest, expected_digest=expected_digest,
                    fd_evidence=True,
                )
            try:
                exit_code = child.wait(timeout=WORKER_DIAGNOSTIC_DEADLINE_S)
            except KeyboardInterrupt:
                signal_sent = int(signal.SIGINT)
                child.send_signal(signal.SIGINT)
                try:
                    exit_code = child.wait(timeout=120)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    if child.poll() is None:
                        child.terminate()
                    try:
                        exit_code = child.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        timed_out = True
                        if child.poll() is None:
                            child.kill()
                        exit_code = child.wait(timeout=15)
            except subprocess.TimeoutExpired:
                timed_out = True
                signal_sent = int(signal.SIGINT)
                if child.poll() is None:
                    child.send_signal(signal.SIGINT)
                try:
                    exit_code = child.wait(timeout=120)
                except subprocess.TimeoutExpired:
                    if child.poll() is None:
                        child.terminate()
                    try:
                        exit_code = child.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        if child.poll() is None:
                            child.kill()
                        exit_code = child.wait(timeout=15)
    except BaseException as error:
        if child is not None and child.poll() is None:
            signal_sent = int(signal.SIGINT)
            child.send_signal(signal.SIGINT)
            try:
                child.wait(timeout=120)
            except subprocess.TimeoutExpired:
                timed_out = True
                if child.poll() is None:
                    child.terminate()
                try:
                    child.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    if child.poll() is None:
                        child.kill()
                    child.wait(timeout=15)
        if not (output / "terminal-exit-receipt.json").exists():
            _write_json_exclusive(output / "terminal-exit-receipt.json", {
                "receipt": "terminal-exit",
                "finished_utc": datetime.now(UTC).isoformat(),
                "elapsed_s": time.monotonic() - started_monotonic,
                "exit_code": child.poll() if child is not None else None,
                "signal_sent": signal_sent,
                "timed_out": timed_out,
                "failure_type": type(error).__name__,
                "failure": str(error),
                "stdout": str(stdout_path),
                "stderr": str(stderr_path),
            })
        raise
    finished_monotonic = time.monotonic()
    terminal = {
        "receipt": "terminal-exit",
        "finished_utc": datetime.now(UTC).isoformat(),
        "elapsed_s": finished_monotonic - started_monotonic,
        "exit_code": exit_code,
        "signal_sent": signal_sent,
        "timed_out": timed_out,
        "fd_evidence_mode": fd_evidence,
        "stdout": str(stdout_path),
        "stderr": str(stderr_path),
    }
    _write_json_exclusive(output / "terminal-exit-receipt.json", terminal)
    report_path = output / "worker-report.json"
    if exit_code != 0 or timed_out or not report_path.is_file():
        fd_evaluation = None
        if fd_evidence:
            fd_evaluation = _record_fd_evidence_evaluation(
                output, None, child_pid=child.pid if child is not None else None,
                launch_receipt=worker_launch_receipt, parent_pid=os.getpid(), command=command,
                root=root, manifest=manifest, expected_digest=expected_digest,
                reason="worker failed, timed out or report missing before complete FD validation",
            )
        print(json.dumps({"valid": False, "terminal": terminal,
                          "fd_evidence_evaluation": fd_evaluation,
                          "reason": "worker failed, timed out or report missing"}, indent=2))
        return 1
    try:
        final_pin = verify_final_source_pin(
            manifest, expected_digest, root, preflight_result["candidate"]
        )
    except (OSError, ValueError) as error:
        _write_json_exclusive(output / "source-drift-receipt.json", {
            "receipt": "source-pin-failed-after-worker",
            "candidate_sha256_expected": expected_digest,
            "error": str(error),
        })
        if fd_evidence:
            _record_fd_evidence_evaluation(
                output, None, child_pid=child.pid, launch_receipt=worker_launch_receipt,
                parent_pid=os.getpid(), command=command, root=root, manifest=manifest,
                expected_digest=expected_digest, reason=f"parent source pin failed: {error}",
            )
        print(json.dumps({"valid": False, "terminal": terminal,
                          "reason": f"source pin failed after worker completion: {error}"}, indent=2))
        return 1
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        if fd_evidence:
            _record_fd_evidence_evaluation(
                output, None, child_pid=child.pid, launch_receipt=worker_launch_receipt,
                parent_pid=os.getpid(), command=command, root=root, manifest=manifest,
                expected_digest=expected_digest, reason=f"worker report could not be decoded: {error}",
            )
        print(json.dumps({"valid": False, "terminal": terminal, "reason": "worker report is invalid"}, indent=2))
        return 1
    if type(report) is not dict:
        if fd_evidence:
            _record_fd_evidence_evaluation(
                output, None, child_pid=child.pid, launch_receipt=worker_launch_receipt,
                parent_pid=os.getpid(), command=command, root=root, manifest=manifest,
                expected_digest=expected_digest, reason="worker report root must be an object",
            )
        print(json.dumps({"valid": False, "terminal": terminal, "reason": "worker report root is not an object"}, indent=2))
        return 1
    source_pin = report.get("source_pin")
    if (not isinstance(source_pin, dict) or source_pin.get("root") != str(root)
            or source_pin.get("output") != str(output)
            or source_pin.get("candidate_sha256_before") != expected_digest
            or source_pin.get("candidate_sha256_after") != expected_digest
            or source_pin.get("file_count_before") != final_pin["file_count"]
            or source_pin.get("file_count_after") != final_pin["file_count"]
            or source_pin.get("checked_after_soak") is not True):
        _write_json_exclusive(output / "source-drift-receipt.json", {
            "receipt": "source-pin-receipt-mismatch",
            "candidate_sha256_expected": expected_digest,
            "worker_source_pin": source_pin,
        })
        if fd_evidence:
            _record_fd_evidence_evaluation(
                output, report, child_pid=child.pid, launch_receipt=worker_launch_receipt,
                parent_pid=os.getpid(), command=command, root=root, manifest=manifest,
                expected_digest=expected_digest, reason="worker source pin failed parent validation",
            )
        print(json.dumps({"valid": False, "terminal": terminal,
                          "reason": "worker did not bind initial and final source pins"}, indent=2))
        return 1
    try:
        validate_import_origins(
            report["loaded_import_origins"], root, set(final_pin["candidate_paths"]),
            namespace_paths=report["loaded_namespace_search_locations"],
        )
    except (OSError, ValueError, KeyError) as error:
        _write_json_exclusive(output / "import-origin-drift-receipt.json", {
            "receipt": "import-origin-validation-failed-after-worker",
            "candidate_sha256_expected": expected_digest,
            "error": str(error),
        })
        if fd_evidence:
            _record_fd_evidence_evaluation(
                output, report, child_pid=child.pid, launch_receipt=worker_launch_receipt,
                parent_pid=os.getpid(), command=command, root=root, manifest=manifest,
                expected_digest=expected_digest, reason=f"parent import-origin validation failed: {error}",
            )
        print(json.dumps({"valid": False, "terminal": terminal,
                          "reason": f"worker import origins failed parent revalidation: {error}"}, indent=2))
        return 1
    _write_json_exclusive(output / "parent-final-source-pin.json", {
        "receipt": "parent-final-source-pin",
        "root": str(root),
        "output": str(output),
        "candidate_sha256_before": preflight_result["candidate"]["candidate_sha256"],
        "candidate_sha256_after": final_pin["candidate_sha256"],
        "file_count_before": preflight_result["candidate"]["file_count"],
        "file_count_after": final_pin["file_count"],
        "worker_final_pin_verified": True,
        "checked_after_worker": True,
    })
    report["child_exit_code"] = exit_code
    if not fd_evidence and (type(report.get("fd_evidence_mode")) is not bool
                            or report.get("fd_evidence_mode") is not False
                            or report.get("fd_evidence_captures") != []):
        mismatch = {"receipt": "fd-evidence-disabled-mode-mismatch", "fd_evidence_mode": False,
                    "worker_fd_evidence_mode": report.get("fd_evidence_mode"),
                    "worker_capture_reference_count": len(report.get("fd_evidence_captures", []))
                    if type(report.get("fd_evidence_captures")) is list else None}
        _write_json_exclusive(output / "fd-evidence-mode-failure-receipt.json", mismatch)
        print(json.dumps({"valid": False, "terminal": terminal, "reason": "worker FD evidence mode mismatch"}, indent=2))
        return 1
    fd_evaluation = None
    if fd_evidence:
        fd_evaluation = _record_fd_evidence_evaluation(
            output, report, child_pid=child.pid, launch_receipt=worker_launch_receipt,
            parent_pid=os.getpid(), command=command, root=root, manifest=manifest,
            expected_digest=expected_digest,
        )
    evaluation = evaluate_soak(report)
    _write_json_exclusive(output / "evaluation-receipt.json", evaluation)
    success = evaluation["passed"] and (fd_evaluation is None or fd_evaluation["valid"])
    print(json.dumps({"valid": success, "fd_evidence_mode": fd_evidence,
                      "terminal": terminal, "evaluation": evaluation,
                      "fd_evidence_evaluation": fd_evaluation}, indent=2))
    return 0 if success else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-manifest", type=Path, required=True)
    parser.add_argument("--expected-digest", required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--execute", action="store_true", help="opt in to the uninterrupted offline 2-hour soak")
    parser.add_argument("--fd-evidence", action="store_true", help="collect native per-descriptor evidence (macOS only)")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--start-receipt", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--parent-pid", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        if args.fd_evidence and not args.execute:
            raise ValueError("--fd-evidence requires explicit --execute")
        if args.worker:
            if not args.execute:
                raise ValueError("direct worker entry requires explicit --execute")
            if args.output is None or args.start_receipt is None or args.parent_pid is None:
                raise ValueError("worker requires output, parent start receipt and parent PID")
            return _worker_main(args.root, args.candidate_manifest, args.expected_digest,
                                args.output, args.start_receipt, args.parent_pid,
                                fd_evidence=args.fd_evidence)
        if args.output is not None and not args.execute:
            raise ValueError("--output is accepted only with explicit --execute")
        preflight_result = preflight(args.candidate_manifest, args.expected_digest, args.root)
        if args.execute:
            if args.output is None:
                raise ValueError("--execute requires an absolute --output new evidence directory")
            return _execute(args.root, args.candidate_manifest, args.expected_digest, args.output,
                            preflight_result, fd_evidence=args.fd_evidence)
        print(json.dumps(preflight_result, indent=2, ensure_ascii=False))
        return 0
    except (OSError, ValueError, subprocess.CalledProcessError, importlib.metadata.PackageNotFoundError) as error:
        print(json.dumps({"valid": False, "errors": [str(error)]}, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
