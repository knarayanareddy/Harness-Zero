"""WS-06b — the OS-sandbox seam between a policy decision and an executor.

Policies decide *whether* a tool may run. An executor starts it. This layer is the seam that guarantees a process
whose tool asked for `ctx.executor` either runs inside the selected OS backend or carries an explicit, truthful
`enforcement: none` fact after the policy gate obtained approval (or the user explicitly chose `bypass`). No
backend, a failed namespace probe, a malformed extra path, or a wrapper error must never mean "try the host
subprocess instead".

The service is owned by `PluginRegistry`, shared with the `permission_modes` policy plugin via `PluginContext`,
and consulted again by the executor wrapper at start time. The policy's availability check is not a lock: the
backend can disappear between the check and the spawn, which the wrapper treats as a hard failure.
"""
from __future__ import annotations

import platform
from dataclasses import replace
from pathlib import Path
from typing import Any

from harness.sdk.contracts import Executor, Process, SandboxBackend
from harness.sdk.errors import PluginLoadError
from harness.sdk.types import ExecRequest, ExecResult, SandboxAvailability, SandboxPolicy


class SandboxUnavailableError(RuntimeError):
    """A required sandbox could not be enforced; the wrapped request was not sent to the host executor."""


class SandboxConfigurationError(ValueError):
    """A user's explicit sandbox path/environment configuration is invalid; do not guess around it."""


class SandboxService:
    """Platform backend registry plus the current, effective process policy."""

    MODES = frozenset({"required", "preferred", "off"})
    BACKENDS = frozenset({"auto", "bwrap", "seatbelt", "none"})

    def __init__(self, settings: Any, workspace: Path):
        self.settings = settings
        self.workspace = Path(workspace).resolve()
        self._backends: dict[str, tuple[SandboxBackend, str]] = {}

    def register_backend(self, backend: SandboxBackend, plugin_name: str) -> None:
        """Register one named backend; duplicates are a boot error, not a last-plugin-wins race."""
        name = str(getattr(backend, "name", ""))
        if not name or not callable(getattr(backend, "available", None)) or not callable(getattr(backend, "wrap", None)):
            raise PluginLoadError(
                f"Sandbox backend from '{plugin_name}' must have a name, available(), and wrap()"
            )
        if name in self._backends:
            _old, origin = self._backends[name]
            raise PluginLoadError(
                f"Duplicate sandbox backend '{name}' registered by '{plugin_name}', already provided by '{origin}'"
            )
        self._backends[name] = (backend, plugin_name)

    def clear_backends(self) -> None:
        self._backends.clear()

    def backend(self, name: str) -> SandboxBackend | None:
        row = self._backends.get(name)
        return row[0] if row else None

    def backend_origin(self, name: str) -> str | None:
        row = self._backends.get(name)
        return row[1] if row else None

    def mode(self) -> str:
        return str(self.settings.get("kernel.sandbox.mode", "preferred"))

    def _backend_name(self) -> tuple[str | None, str]:
        requested = str(self.settings.get("kernel.sandbox.backend", "auto"))
        if requested == "none":
            return None, "No sandbox backend was selected (kernel.sandbox.backend=none)."
        if requested not in self.BACKENDS:
            return None, f"Unknown sandbox backend '{requested}'. Choose auto, bwrap, seatbelt, or none."
        if requested != "auto":
            return requested, ""
        system = platform.system()
        if system == "Linux":
            return "bwrap", ""
        if system == "Darwin":
            return "seatbelt", ""
        return None, f"No OS sandbox backend is supported on {system or 'this host'}."

    def policy(self) -> SandboxPolicy:
        """Build a per-process policy from effective settings, rejecting paths we cannot safely interpret."""
        if self.workspace == Path("/"):
            raise SandboxConfigurationError("Workspace '/' would make every host file writable; choose a project directory")
        extras = self.settings.get("kernel.sandbox.extra_readable", [])
        if extras is None:
            extras = []
        if not isinstance(extras, (list, tuple)) or any(not isinstance(p, str) for p in extras):
            raise SandboxConfigurationError("kernel.sandbox.extra_readable must be a list of path strings")
        resolved: list[Path] = []
        for value in extras:
            raw = Path(value).expanduser()
            if not raw.is_absolute():
                raise SandboxConfigurationError(
                    f"Extra readable path must be absolute, got {value!r}; relative paths are ambiguous"
                )
            try:
                path = raw.resolve(strict=True)
            except (OSError, RuntimeError) as err:
                raise SandboxConfigurationError(
                    f"Extra readable path does not exist or cannot be resolved: {value!r} ({err})"
                ) from err
            if path == Path("/"):
                raise SandboxConfigurationError("Extra readable path '/' exposes the entire host filesystem")
            resolved.append(path)
        network = self.settings.get("kernel.sandbox.network", False)
        if not isinstance(network, bool):
            raise SandboxConfigurationError("kernel.sandbox.network must be a boolean")
        # The workspace is the only writable host tree. `.harness/tmp` is already below it and is therefore
        # writable without another, wider bind. A plugin executor does not get to add a second host directory.
        return SandboxPolicy(
            workspace=self.workspace,
            writable=(self.workspace,),
            readable_extra=tuple(resolved),
            network=network,
        )

    def availability(self) -> SandboxAvailability:
        """Report the selected backend after probing it and validating the path policy."""
        if self.mode() == "off":
            return SandboxAvailability(
                backend="none", available=False, reason="Sandbox mode is off.", enforcement="none"
            )
        if self.mode() not in self.MODES:
            return SandboxAvailability(
                backend="none",
                available=False,
                reason=f"Invalid sandbox mode {self.mode()!r}; refusing to run without confinement.",
                enforcement="none",
            )
        try:
            policy = self.policy()
        except SandboxConfigurationError as err:
            name, _ = self._backend_name()
            return SandboxAvailability(
                backend=name or "none", available=False, reason=str(err), enforcement="none"
            )
        name, reason = self._backend_name()
        if name is None:
            return SandboxAvailability(backend="none", available=False, reason=reason, enforcement="none")
        backend = self.backend(name)
        if backend is None:
            return SandboxAvailability(
                backend=name,
                available=False,
                reason=f"Sandbox backend '{name}' is not registered (its plugin may be disabled or missing).",
                enforcement="none",
            )
        try:
            available = backend.available()
        except Exception as err:  # a probe is advisory only if it returns; an exception is unavailable
            return SandboxAvailability(
                backend=name,
                available=False,
                reason=f"Sandbox backend '{name}' probe failed: {type(err).__name__}: {err}",
                enforcement="none",
            )
        if not isinstance(available, SandboxAvailability):
            return SandboxAvailability(
                backend=name,
                available=False,
                reason=f"Sandbox backend '{name}' returned an invalid availability result.",
                enforcement="none",
            )
        if (
            type(available.available) is not bool
            or not isinstance(available.backend, str)
            or not isinstance(available.reason, str)
        ):
            return SandboxAvailability(
                backend=name,
                available=False,
                reason=(
                    f"Sandbox backend '{name}' returned malformed availability facts; "
                    "refusing to run without confinement."
                ),
                enforcement="none",
            )
        if not available.available:
            return SandboxAvailability(
                backend=name,
                available=False,
                reason=available.reason or f"Sandbox backend '{name}' is unavailable.",
                enforcement="none",
            )
        if available.backend != name:
            return SandboxAvailability(
                backend=name,
                available=False,
                reason=f"Sandbox backend '{name}' reported a mismatched backend name {available.backend!r}.",
                enforcement="none",
            )
        if available.enforcement not in ("full", "partial"):
            return SandboxAvailability(
                backend=name,
                available=False,
                reason=f"Sandbox backend '{name}' reported available without an enforcement level.",
                enforcement="none",
            )
        # Additional readable paths are an explicit user grant outside the ordinary system runtime and workspace.
        # They may be necessary for local SDKs and tools, but calling the resulting boundary "full" would hide
        # the fact that the host files are now in scope.
        enforcement = available.enforcement
        reason = available.reason
        if policy.readable_extra:
            enforcement = "partial"
            reason = "Extra readable paths are exposed; review kernel.sandbox.extra_readable."
        return SandboxAvailability(
            backend=name,
            available=True,
            reason=reason,
            enforcement=enforcement,
        )

    def status(self) -> dict[str, Any]:
        """The one public representation used by status, /health, the CLI, and the composer badge."""
        mode = self.mode()
        network = self.settings.get("kernel.sandbox.network", False) is True
        avail = self.availability()
        return {
            "mode": mode,
            "backend": avail.backend,
            "available": avail.available,
            "enforcement": avail.enforcement,
            "reason": avail.reason,
            "network": network,
        }

    def wrap_executor(self, delegate: Executor, *, allow_unconfined: bool = False) -> Executor:
        """Wrap *any* selected executor, not just `LocalExecutor`, so worktree/plugins cannot bypass the seam."""
        return SandboxedExecutor(delegate, self, allow_unconfined=allow_unconfined)


class SandboxedProcess:
    """A transparent Process facade that adds the enforcement fact to its final result."""

    def __init__(self, delegate: Process, metadata: dict[str, Any]):
        self._delegate = delegate
        self.sandbox = dict(metadata)
        self.id = delegate.id
        self.pid = delegate.pid
        self._exit_code: int | None = getattr(delegate, "exit_code", None)
        if self._exit_code is None:
            self._exit_code = getattr(delegate, "returncode", None)

    @property
    def returncode(self) -> int | None:
        # The SDK Process protocol exposes its exit status through wait()'s ExecResult, not a live subprocess
        # attribute. Preserve the convenience property for gate cleanup/background consumers once wait completes.
        return self._exit_code if self._exit_code is not None else getattr(self._delegate, "returncode", None)

    @property
    def exit_code(self) -> int | None:
        return self.returncode

    def output(self):
        return self._delegate.output()

    async def send_input(self, text: str) -> None:
        await self._delegate.send_input(text)

    async def kill(self, grace_s: float = 2.0) -> None:
        await self._delegate.kill(grace_s=grace_s)

    async def wait(self) -> ExecResult:
        result = await self._delegate.wait()
        if isinstance(result, ExecResult):
            self._exit_code = result.exit_code
            return replace(result, metadata={**result.metadata, "sandbox": dict(self.sandbox)})
        if isinstance(result, int):  # defensive for older third-party Process implementations
            self._exit_code = result
        return result


class SandboxedExecutor:
    """Per-tool executor adapter. Required mode never falls through to a bare host process."""

    def __init__(self, delegate: Executor, service: SandboxService, *, allow_unconfined: bool = False):
        self._delegate = delegate
        self._service = service
        self._allow_unconfined = allow_unconfined
        self.name = getattr(delegate, "name", "executor")
        status = service.status()
        self.isolation = "os-sandbox" if status["available"] else getattr(delegate, "isolation", "none")

    async def start(self, req: ExecRequest) -> Process:
        status = self._service.status()
        mode = status["mode"]
        if mode not in SandboxService.MODES:
            raise SandboxUnavailableError(status["reason"])

        if mode == "off":
            return SandboxedProcess(await self._delegate.start(req), status)

        if not status["available"]:
            reason = status["reason"] or f"Sandbox backend '{status['backend']}' is unavailable."
            if mode == "required":
                raise SandboxUnavailableError(f"Required OS sandbox unavailable: {reason}")
            if not self._allow_unconfined:
                raise SandboxUnavailableError(
                    f"Sandbox unavailable; unconfined execution requires explicit approval: {reason}"
                )
            # The gate only sets this flag after an approval, or for the explicit `bypass` permission mode.
            # Do not relabel this as a sandboxed run: the unconfined fact is what lands on the tool result.
            unconfined = {**status, "enforcement": "none", "reason": reason}
            return SandboxedProcess(await self._delegate.start(req), unconfined)

        backend = self._service.backend(status["backend"])
        if backend is None:  # defensive against a backend being removed between status and dispatch
            raise SandboxUnavailableError(f"Sandbox backend '{status['backend']}' disappeared before execution")
        policy = self._service.policy()
        confined_request = backend.wrap(req, policy)
        # If wrap() failed, propagate the error. Falling back would turn a packaging/configuration defect into
        # silent host execution, exactly the failure mode this seam is here to make impossible.
        process = await self._delegate.start(confined_request)
        return SandboxedProcess(process, self._service.status())
