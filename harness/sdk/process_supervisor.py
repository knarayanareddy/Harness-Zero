"""Small POSIX identity anchor; private pipes, stdlib only, no shell FD dialect.

Run with the current interpreter by absolute filename, or via the frozen
engine's private entrypoint. Payloads inherit standard IO only. Only this live
session/group leader signals its own group; the engine never guesses a PGID.
"""
from __future__ import annotations

import os
import selectors
import signal
import stat
import sys
from pathlib import Path

FROZEN_ENTRY = "--harness-process-supervisor"
_WORKER_STATE = ("_PYI_ARCHIVE_FILE", "_PYI_APPLICATION_HOME_DIR", "_PYI_PARENT_PROCESS_LEVEL",
                 "_PYI_LINUX_PROCESS_NAME", "_PYI_SPLASH_IPC")


def supervisor_environment(payload_env: dict[str, str]) -> dict[str, str]:
    """Keep trusted same-bundle worker state out of caller-controlled overlays.

    PyInstaller documents these inherited private values for sys.executable
    worker processes. Preserve actual values; never synthesize a process level.
    Bootloader controls are reserved and are not payload environment settings.
    """
    env = {k: v for k, v in payload_env.items() if not k.startswith(("_PYI_", "PYINSTALLER_"))}
    if getattr(sys, "frozen", False):
        env.update({k: os.environ[k] for k in _WORKER_STATE if k in os.environ})
    return env


def _payload_environment() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("_PYI_", "PYINSTALLER_"))}
    if getattr(sys, "frozen", False):
        # Do not make ordinary payloads load the frozen engine's bundled libs.
        original = env.pop("LD_LIBRARY_PATH_ORIG", None)
        if original:
            env["LD_LIBRARY_PATH"] = original
        else:
            env.pop("LD_LIBRARY_PATH", None)
    return env


def supervisor_command(argv: list[str], status_fd: int, control_fd: int) -> list[str]:
    if not argv or any(type(fd) is not int or fd < 10 for fd in (status_fd, control_fd)) or status_fd == control_fd:
        raise ValueError("Supervisor requires argv and distinct private descriptors above 9")
    prefix = [sys.executable, FROZEN_ENTRY] if getattr(sys, "frozen", False) else [sys.executable, "-I", str(Path(__file__).resolve())]
    return [*prefix, str(status_fd), str(control_fd), "--", *argv]


def _end_group() -> None:
    # The caller is the still-live identity anchor, never an observed old PID.
    os.killpg(os.getpgrp(), signal.SIGKILL)
    os._exit(125)


def supervisor_main(arguments: list[str]) -> int:
    import subprocess  # Only the isolated, single-threaded helper launches payloads.

    if len(arguments) < 4 or arguments[2] != "--":
        return 125
    try:
        status_fd, control_fd = int(arguments[0]), int(arguments[1])
        if min(status_fd, control_fd) < 10 or status_fd == control_fd:
            return 125
        if os.getpid() != os.getpgrp() or os.getpid() != os.getsid(0):
            return 125  # Never signal an embedding caller's process group.
        if not all(stat.S_ISFIFO(os.fstat(fd).st_mode) for fd in (status_fd, control_fd)):
            return 125
    except (ValueError, OSError):
        return 125

    wake_read, wake_write = os.pipe()
    for fd in (wake_read, wake_write, control_fd):
        os.set_blocking(fd, False)
    for fd in (status_fd, control_fd, wake_read, wake_write):
        os.set_inheritable(fd, False)
    signal.set_wakeup_fd(wake_write, warn_on_full_buffer=False)
    for event in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP, signal.SIGCHLD):
        signal.signal(event, lambda _number, _frame: None)

    selector = selectors.DefaultSelector()
    selector.register(control_fd, selectors.EVENT_READ, "control")
    selector.register(wake_read, selectors.EVENT_READ, "signal")
    buffer = b""
    reported = False
    try:
        os.write(status_fd, b"HZSUP1 READY\n")
        payload = subprocess.Popen(arguments[3:], close_fds=True, restore_signals=True, env=_payload_environment())
        while True:
            code = payload.poll()
            if code is not None and not reported:
                # Popen preserves negative signal status, unlike shell `$?`.
                os.write(status_fd, f"HZSUP1 EXIT {code}\n".encode("ascii"))
                reported = True
            for key, _events in selector.select():
                if key.data == "signal":
                    try:
                        os.read(wake_read, 4096)
                    except BlockingIOError:
                        pass
                    continue
                try:
                    data = os.read(control_fd, 128)
                except BlockingIOError:
                    continue
                if not data:
                    _end_group()  # Owning engine died or cancelled startup.
                buffer += data
                if len(buffer) > 128:
                    _end_group()
                while b"\n" in buffer:
                    command, buffer = buffer.split(b"\n", 1)
                    if command == b"TERM":
                        os.killpg(os.getpgrp(), signal.SIGTERM)
                    elif command == b"KILL":
                        _end_group()
                    else:
                        _end_group()
    except BaseException as err:
        # Protocol/exec/observation failures cannot leave an unobservable group.
        try:
            os.write(status_fd, f"HZSUP1 ERROR {type(err).__name__}\n".encode("ascii"))
        finally:
            _end_group()
    return 125


if __name__ == "__main__":
    raise SystemExit(supervisor_main(sys.argv[1:]))
