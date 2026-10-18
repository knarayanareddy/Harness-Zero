"""Linux bubblewrap backend. It creates a new user/PID/IPC/UTS mount namespace per process.

Only the runtime tree is read-only, the active workspace is read/write, `/tmp` is private, and networking is a
policy bit. The user's home is deliberately not mounted. This is why a local tool installed in `~/.local/bin` or
a language manager under `~/.nvm` may need an explicit `kernel.sandbox.extra_readable` entry; extra paths are
reported as partial enforcement, not quietly described as a full boundary.
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path

from harness.sdk.types import ExecRequest, SandboxAvailability, SandboxPolicy

_SYSTEM_RO_BIND = ("/usr", "/bin", "/sbin", "/lib", "/lib64", "/etc", "/opt")
_DEFAULT_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


def _under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _mounted_system_path(path: Path) -> bool:
    return any(_under(path, Path(root)) for root in _SYSTEM_RO_BIND)


def _guest_env(req: ExecRequest, policy: SandboxPolicy) -> dict[str, str]:
    """Construct only the environment the sandbox explicitly permits; never inherit arbitrary host secrets."""
    host = {k: os.environ[k] for k in policy.env_allow if k in os.environ}
    explicit = {str(k): str(v) for k, v in req.env_overlay.items() if str(k) in policy.env_allow}
    host.update(explicit)
    path_entries = [entry for entry in host.get("PATH", _DEFAULT_PATH).split(os.pathsep) if entry]
    roots = [policy.workspace, *policy.readable_extra, *(Path(p) for p in _SYSTEM_RO_BIND)]
    safe_entries = []
    for entry in path_entries:
        candidate = Path(entry).expanduser()
        try:
            resolved = candidate.resolve(strict=False)
        except OSError:
            continue
        if any(_under(resolved, root.resolve(strict=False)) for root in roots):
            safe_entries.append(str(candidate))
    path = os.pathsep.join(dict.fromkeys([*_DEFAULT_PATH.split(os.pathsep), *safe_entries]))
    return {
        "PATH": path,
        "HOME": "/tmp",
        "TMPDIR": "/tmp",
        "LANG": host.get("LANG", "C.UTF-8"),
        "LC_ALL": host.get("LC_ALL", "C.UTF-8"),
        "TERM": host.get("TERM", "dumb"),
        "USER": "sandbox",
        "SHELL": "/bin/sh",
        "GIT_PAGER": "cat",
        "GIT_TERMINAL_PROMPT": "0",
        "PAGER": "cat",
    }


def _add_missing_parents(argv: list[str], dest: Path, *, created: set[Path], populated_roots: tuple[Path, ...]) -> None:
    """Create destination parents in bwrap's otherwise empty root, respecting mounted directory trees."""
    for parent in reversed(dest.parents):
        if parent == Path("/") or parent in created:
            continue
        if any(_under(parent, root) for root in populated_roots):
            continue
        argv.extend(("--dir", str(parent)))
        created.add(parent)


class BwrapBackend:
    name = "bwrap"

    def __init__(self) -> None:
        self._availability: SandboxAvailability | None = None
        self._executable: str | None = None

    def available(self) -> SandboxAvailability:
        """Probe the exact namespaces this backend relies on; a binary alone is not enforcement."""
        if self._availability is not None:
            return self._availability
        if platform.system() != "Linux":
            self._availability = SandboxAvailability(
                backend=self.name,
                available=False,
                reason="bubblewrap is available only on Linux",
                enforcement="none",
            )
            return self._availability
        executable = shutil.which("bwrap")
        if not executable:
            self._availability = SandboxAvailability(
                backend=self.name,
                available=False,
                reason="bubblewrap (bwrap) not found on PATH; install bubblewrap to enable process confinement",
                enforcement="none",
            )
            return self._availability
        self._executable = executable
        command = [
            executable,
            "--die-with-parent",
            "--new-session",
            "--unshare-user",
            "--unshare-pid",
            "--unshare-ipc",
            "--unshare-uts",
            "--unshare-net",
            "--ro-bind",
            "/",
            "/",
            "--",
            "/usr/bin/true",
        ]
        try:
            probe = subprocess.run(command, capture_output=True, text=True, timeout=5, check=False)
        except (OSError, subprocess.TimeoutExpired) as err:
            self._availability = SandboxAvailability(
                backend=self.name,
                available=False,
                reason=f"bwrap namespace probe failed: {type(err).__name__}: {err}",
                enforcement="none",
            )
            return self._availability
        if probe.returncode != 0:
            detail = (probe.stderr or probe.stdout or "no diagnostic").strip().splitlines()[-1]
            self._availability = SandboxAvailability(
                backend=self.name,
                available=False,
                reason=f"bwrap is present but the required user/PID/network namespaces are unavailable: {detail}",
                enforcement="none",
            )
            return self._availability
        self._availability = SandboxAvailability(
            backend=self.name, available=True, reason="", enforcement="full"
        )
        return self._availability

    def wrap(self, req: ExecRequest, policy: SandboxPolicy) -> ExecRequest:
        """Return a fresh argv request. It never shells out to an interpolated user command."""
        availability = self.available()
        if not availability.available:
            raise RuntimeError(f"bwrap is unavailable: {availability.reason}")
        executable = self._executable or shutil.which("bwrap")
        if not executable:
            raise RuntimeError("bwrap disappeared after its availability probe")

        workspace = policy.workspace.resolve(strict=True)
        if workspace == Path("/"):
            raise ValueError("Workspace '/' is too broad for OS sandboxing")
        cwd = Path(req.cwd).resolve(strict=True)
        if not _under(cwd, workspace):
            raise ValueError(f"Sandbox cwd must be inside the workspace: {cwd}")
        if not cwd.is_dir():
            raise ValueError(f"Sandbox cwd is not a directory: {cwd}")
        for writable in policy.writable:
            resolved = writable.resolve(strict=True)
            if not _under(resolved, workspace):
                raise ValueError(f"Sandbox writable path must be inside the workspace: {resolved}")

        argv = [
            executable,
            "--die-with-parent",
            "--new-session",
            "--unshare-user",
            "--unshare-pid",
            "--unshare-ipc",
            "--unshare-uts",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--tmpfs",
            "/tmp",
        ]
        # tmpfs is an empty mount, so its descendants must be created before a workspace below /tmp is bound.
        # `/proc` and `/dev` only need their mount point here; neither is a parent of a granted path.
        created: set[Path] = {Path("/tmp"), Path("/proc"), Path("/dev")}
        populated: list[Path] = []
        for source in _SYSTEM_RO_BIND:
            src = Path(source)
            if not src.exists():
                continue
            _add_missing_parents(argv, src, created=created, populated_roots=tuple(populated))
            argv.extend(("--ro-bind", source, source))
            populated.append(src)
        for extra in policy.readable_extra:
            path = extra.resolve(strict=True)
            if path == Path("/"):
                raise ValueError("Refusing kernel.sandbox.extra_readable='/' because it exposes the whole host filesystem")
            if _under(path, workspace) or _mounted_system_path(path):
                continue  # already present; don't overlay a workspace with a read-only bind
            _add_missing_parents(argv, path, created=created, populated_roots=tuple(populated))
            argv.extend(("--ro-bind", str(path), str(path)))
            populated.append(path)

        # Bind the workspace last: if an explicit readable ancestor (e.g. /home/user) contains it, this rw bind
        # must win over that ancestor's read-only mount.
        _add_missing_parents(argv, workspace, created=created, populated_roots=tuple(populated))
        argv.extend(("--bind", str(workspace), str(workspace)))
        populated.append(workspace)

        if not policy.network:
            argv.append("--unshare-net")
        env = _guest_env(req, policy)
        argv.append("--clearenv")
        for key, value in sorted(env.items()):
            argv.extend(("--setenv", key, value))
        argv.extend(("--chdir", str(cwd)))
        if req.shell or isinstance(req.command, str):
            command = req.command if isinstance(req.command, str) else " ".join(req.command)
            inner = ["/bin/sh", "-c", command]
        else:
            inner = list(req.command)
        if not inner:
            raise ValueError("Cannot sandbox an empty command")
        argv.extend(("--", *inner))
        return ExecRequest(
            command=argv,
            cwd=workspace,
            env_overlay={},
            timeout_s=req.timeout_s,
            interactive=req.interactive,
            shell=False,
        )
