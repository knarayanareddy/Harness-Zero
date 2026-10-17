"""macOS Seatbelt (`sandbox-exec`) backend.

This backend is selected only on Darwin. Apple has deprecated `sandbox-exec`; the availability probe tests that
the command-line profile syntax still works on the host. The profile is generated from the same workspace,
writable-path, extra-readable-path, and network policy as bubblewrap. It is labelled `partial` because the OS
profile language is a policy mechanism rather than a new namespace: the status surface must not call it a VM.
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path

from harness.sdk.types import ExecRequest, SandboxAvailability, SandboxPolicy

_SYSTEM_READ = ("/System", "/usr", "/bin", "/sbin", "/Library", "/etc", "/dev")
_DEFAULT_PATH = "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"


def _quote(value: str | Path) -> str:
    raw = str(value)
    return '"' + raw.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _guest_env(req: ExecRequest, policy: SandboxPolicy) -> dict[str, str]:
    host = {k: os.environ[k] for k in policy.env_allow if k in os.environ}
    host.update({str(k): str(v) for k, v in req.env_overlay.items() if str(k) in policy.env_allow})
    roots = [policy.workspace, *policy.readable_extra, *(Path(p) for p in _SYSTEM_READ)]
    path_entries = []
    for entry in host.get("PATH", _DEFAULT_PATH).split(os.pathsep):
        candidate = Path(entry).expanduser()
        resolved = candidate.resolve(strict=False)
        if any(_under(resolved, root.resolve(strict=False)) for root in roots):
            path_entries.append(str(candidate))
    return {
        "PATH": os.pathsep.join(dict.fromkeys([*_DEFAULT_PATH.split(os.pathsep), *path_entries])),
        "HOME": "/private/tmp",
        "TMPDIR": "/private/tmp",
        "LANG": host.get("LANG", "C.UTF-8"),
        "LC_ALL": host.get("LC_ALL", "C.UTF-8"),
        "TERM": host.get("TERM", "dumb"),
        "USER": "sandbox",
        "SHELL": "/bin/sh",
        "GIT_PAGER": "cat",
        "GIT_TERMINAL_PROMPT": "0",
        "PAGER": "cat",
    }


class SeatbeltBackend:
    name = "seatbelt"

    def __init__(self) -> None:
        self._availability: SandboxAvailability | None = None
        self._executable: str | None = None

    def available(self) -> SandboxAvailability:
        if self._availability is not None:
            return self._availability
        if platform.system() != "Darwin":
            self._availability = SandboxAvailability(
                backend=self.name,
                available=False,
                reason="Seatbelt sandbox-exec is available only on macOS",
                enforcement="none",
            )
            return self._availability
        executable = shutil.which("sandbox-exec")
        if not executable:
            self._availability = SandboxAvailability(
                backend=self.name,
                available=False,
                reason="sandbox-exec not found on PATH",
                enforcement="none",
            )
            return self._availability
        self._executable = executable
        profile = (
            "(version 1) (deny default) (allow process*) (allow sysctl-read) "
            '(allow file-read* (subpath "/System") (subpath "/usr") (subpath "/bin") '
            '(subpath "/sbin") (subpath "/Library") (subpath "/etc") (subpath "/dev"))'
        )
        try:
            probe = subprocess.run(
                [executable, "-p", profile, "/usr/bin/true"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as err:
            self._availability = SandboxAvailability(
                backend=self.name,
                available=False,
                reason=f"Seatbelt probe failed: {type(err).__name__}: {err}",
                enforcement="none",
            )
            return self._availability
        if probe.returncode != 0:
            detail = (probe.stderr or probe.stdout or "no diagnostic").strip().splitlines()[-1]
            self._availability = SandboxAvailability(
                backend=self.name,
                available=False,
                reason=f"sandbox-exec could not apply the probe profile: {detail}",
                enforcement="none",
            )
            return self._availability
        self._availability = SandboxAvailability(
            backend=self.name, available=True, reason="Seatbelt profile accepted by this host", enforcement="partial"
        )
        return self._availability

    def _profile(self, policy: SandboxPolicy) -> str:
        workspace = policy.workspace.resolve(strict=True)
        if workspace == Path("/"):
            raise ValueError("Workspace '/' is too broad for OS sandboxing")
        read_rules = [f"(subpath {_quote(p)})" for p in _SYSTEM_READ]
        read_rules.append(f"(subpath {_quote(workspace)})")
        read_rules.extend(f"(subpath {_quote(Path(p).resolve(strict=True))})" for p in policy.readable_extra)
        write_roots = [workspace]
        write_roots.extend(Path(p).resolve(strict=True) for p in policy.writable)
        if any(not _under(p, workspace) for p in write_roots):
            raise ValueError("Seatbelt writable paths must remain inside the workspace")
        write_rules = " ".join(f"(subpath {_quote(p)})" for p in dict.fromkeys(write_roots))
        network_rule = "(allow network-outbound)" if policy.network else "(deny network*)"
        return " ".join(
            (
                "(version 1)",
                "(deny default)",
                "(allow process*)",
                "(allow sysctl-read)",
                f"(allow file-read* {' '.join(read_rules)})",
                f"(allow file-write* {write_rules} (subpath \"/private/tmp\"))",
                network_rule,
            )
        )

    def wrap(self, req: ExecRequest, policy: SandboxPolicy) -> ExecRequest:
        availability = self.available()
        if not availability.available:
            raise RuntimeError(f"Seatbelt is unavailable: {availability.reason}")
        executable = self._executable or shutil.which("sandbox-exec")
        if not executable:
            raise RuntimeError("sandbox-exec disappeared after its availability probe")
        workspace = policy.workspace.resolve(strict=True)
        cwd = Path(req.cwd).resolve(strict=True)
        if not _under(cwd, workspace) or not cwd.is_dir():
            raise ValueError(f"Seatbelt cwd must be a directory inside the workspace: {cwd}")
        if any(Path(p).resolve(strict=True) == Path("/") for p in policy.readable_extra):
            raise ValueError("Refusing kernel.sandbox.extra_readable='/' because it exposes the whole host filesystem")
        profile = self._profile(policy)
        if req.shell or isinstance(req.command, str):
            command = req.command if isinstance(req.command, str) else " ".join(req.command)
            inner = ["/bin/sh", "-c", command]
        else:
            inner = list(req.command)
        if not inner:
            raise ValueError("Cannot sandbox an empty command")
        env = _guest_env(req, policy)
        env_args = [f"{key}={value}" for key, value in sorted(env.items())]
        argv = [executable, "-p", profile, "/usr/bin/env", "-i", *env_args, *inner]
        return ExecRequest(
            command=argv,
            cwd=workspace,
            env_overlay={},
            timeout_s=req.timeout_s,
            interactive=req.interactive,
            shell=False,
        )
