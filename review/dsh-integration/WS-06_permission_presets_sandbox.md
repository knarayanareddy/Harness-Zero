# WS-06 — Permission presets and OS process confinement behind the executor seam

| | |
|---|---|
| Status | specified |
| Depends on | WS-01 (scenarios), WS-02 (settings declared); WS-04 for `tool.decision` records (not blocking) |
| Effort | 1 day presets + 3–4 days sandbox |
| Principles | P3 (primary), P6 |
| dsh reference (@86a40220) | `packages/core/permission/` (`PermissionPreset` = sandbox mode × approval policy; presets `strict / standard / permissive / unrestricted`-style names, "deny when unanswerable"), `packages/core/sandbox/` (service definition: `SandboxProvider.wrap(command, policy) → command`, `enforcement: "full" | "partial"`, `SANDBOX_UNAVAILABLE`), `packages/platform/sandbox-landlock/` (Linux, own Node addon), `packages/platform/sandbox-seatbelt/` (macOS `sandbox-exec` profile), `packages/platform/sandbox-windows/` (restricted token). `docs/security.md` ("enforcement is a reported fact") |
| Harness-Zero touchpoints | `harness/plugins/policy/permission_modes/policy.py:25-90` (modes `allow | ask | plan | bypass`, order 900; `kernel.security.permission_mode`, `kernel.security.destructive_blocking`), `harness/kernel/settings.py:41-44`, `harness/kernel/approvals.py:65-67` (no interactive subscriber ⇒ deny — fail-closed already for the broker), `harness/kernel/gate.py:592-597` (executor selection: `registry.executors["default"]` else `["local"]`), `harness/sdk/contracts.py:101-107` (`Executor.start(ExecRequest) -> Process`, `isolation` literal `none|worktree|container|vm`), `harness/sdk/types.py:764-771` (`ExecRequest`: command, cwd, env_overlay, timeout_s, interactive, shell), `harness/plugins/executors/local/executor.py:177-200` (`create_subprocess_shell/exec`), `harness/plugins/executors/worktree/`, `harness/plugins/policy/workspace_jail/` (path jail — *policy*, not OS-level), `docs/05_EXECUTION_SANDBOX_AND_GIT_WORKTREES.md`, `docs/12_TOOL_SAFETY_GUARDRAILS_AND_PERMISSIONS.md` (design intent, not implemented) |

## 1. Outcome

**Part A — presets (1 day).** `kernel.security.preset ∈ {locked, careful, standard, open}` is the one knob the user touches; each preset expands to the existing settings plus the new sandbox ones:

| preset | permission_mode | destructive_blocking | sandbox.mode | network in sandbox | approval timeout |
|---|---|---|---|---|---|
| `locked` | `plan` | true | `required` | off | n/a |
| `careful` (default) | `ask` | true | `required` if available else `ask` | off | 300 s |
| `standard` | `ask` for WRITE/DESTRUCTIVE, `allow` for EXEC inside sandbox | true | `preferred` | off | 300 s |
| `open` | `allow` | true | `off` | on | n/a |

(`bypass` stays available only as the explicit `kernel.security.permission_mode=bypass` for tests — never as a preset.) Setting a preset is a `settings.set` of one key; the expansion is a kernel function, the effective values are visible in `status` and the UI shows "preset: careful (customised)" when any expanded key was overridden.

**Part B — sandbox (3–4 days).** `run_command` (and any tool that uses `ctx.executor`) runs inside an OS confinement when `sandbox.mode != off`:

- Linux: `bwrap` (bubblewrap) if present — ro-bind `/usr /lib /lib64 /bin /etc`, rw-bind `<workspace>` and `<ws>/.harness/tmp`, `--unshare-net` unless network allowed, `--die-with-parent`, tmpfs `/tmp`. Landlock via a Python binding is a later option; `bwrap` is what ships.
- macOS: `sandbox-exec -p <profile>` with a generated profile (`(deny default) (allow process*) (allow file-read* …) (allow file-write* (subpath "<ws>"))`, network `(deny network*)` unless allowed).
- Windows / unknown / tool missing: **unavailable**.

Enforcement is a **reported fact** on every exec result: `metadata.sandbox = {"backend": "bwrap", "enforcement": "full" | "partial" | "none", "network": false}`; the UI shows a badge; the record has it (WS-04). `sandbox.mode=required` + unavailable ⇒ EXEC/WRITE tools are **denied** by the policy with reason `sandbox unavailable on this host (install bubblewrap)` — never silently unconfined (P3). `mode=ask` ⇒ unavailable turns the decision into ASK with that reason.

## 2. Current state (verified)

- Four modes exist, work, and are tested at the policy level; "preset" semantics and the sandbox dimension do not exist.
- Executors: `local` (plain subprocess) and `worktree` (git worktree cwd). `isolation` literal has no value for OS confinement. Nothing wraps the command.
- Approval broker denies without an interactive subscriber (good, keep; add a kernel-level test via the daemon with no SSE client connected — the review found the daemon path untested).
- `workspace_jail` policy prevents *tool arguments* from escaping the workspace; it cannot stop `run_command` from `cat ~/.ssh/id_rsa`. That gap is what this WS closes.

## 3. Design

### 3.1 Presets (kernel)

`harness/kernel/presets.py`:

```python
PRESETS: dict[str, dict[str, Any]] = {
  "locked":   {"kernel.security.permission_mode": "plan",  "kernel.security.destructive_blocking": True, "kernel.sandbox.mode": "required",  "kernel.sandbox.network": False},
  "careful":  {"kernel.security.permission_mode": "ask",   "kernel.security.destructive_blocking": True, "kernel.sandbox.mode": "required",  "kernel.sandbox.network": False},
  "standard": {"kernel.security.permission_mode": "ask",   "kernel.security.destructive_blocking": True, "kernel.sandbox.mode": "preferred", "kernel.sandbox.network": False, "kernel.security.exec_in_sandbox": "allow"},
  "open":     {"kernel.security.permission_mode": "allow", "kernel.security.destructive_blocking": True, "kernel.sandbox.mode": "off",       "kernel.sandbox.network": True},
}
def effective(settings) -> dict[str, Any]: ...        # preset expansion overlaid by explicit keys; used by SettingsStore.get via a resolver layer
def customised(settings) -> list[str]: ...            # keys differing from the preset
```

`SettingsStore` gets a resolver chain: explicit value → preset expansion → schema default. `settings.changed` record (WS-04) is written with the effective diff.

### 3.2 Sandbox seam (SDK)

```python
# harness/sdk/contracts.py
class SandboxBackend(Protocol):
    name: str                                     # "bwrap" | "seatbelt" | "none"
    def available(self) -> SandboxAvailability: ...    # (ok: bool, reason: str, enforcement: "full"|"partial")
    def wrap(self, req: ExecRequest, policy: SandboxPolicy) -> ExecRequest: ...   # returns a new ExecRequest whose command is the confined one

@dataclass(frozen=True)
class SandboxPolicy:
    workspace: Path; writable: tuple[Path, ...]; readable_extra: tuple[Path, ...] = (); network: bool = False; env_allow: tuple[str, ...] = ("PATH","HOME","LANG","TERM")
```

Registry slot `register_sandbox_backend(backend)`; built-in plugins `executors/sandbox_bwrap`, `executors/sandbox_seatbelt`. The **`local` executor** is modified to consult `registry.sandbox` and wrap when `kernel.sandbox.mode != off`; `worktree` inherits via composition. `Process` result metadata carries `sandbox`.

### 3.3 Policy

`permission_modes` policy gains the sandbox dimension (same plugin, order 900): for EXEC/WRITE calls it reads `sandbox.availability()` (cached per process, refreshed on `settings.changed`) and applies the matrix in §1. Decision `reason` strings are the exact user-facing text; `Decision.metadata["sandbox"]` carries the availability for the UI.

### 3.4 `status` and UI

`status` command adds `security: {preset, customised: [...], sandbox: {backend, available, enforcement, reason}}`. The composer shows a badge `careful · bwrap full` / `careful · sandbox unavailable` (red). Settings tab "Security & Sandboxing" gets the preset select at the top (schema `x-ui.order: 0`) — via WS-05 schema rendering, no bespoke HTML.

## 4. Files

### ADD

```
harness/kernel/presets.py
harness/plugins/executors/sandbox_bwrap/{__init__.py,harness_plugin.toml,plugin.py,backend.py,settings.schema.json,README.md,tests/test_conformance.py}
harness/plugins/executors/sandbox_seatbelt/{…same…,profile.py}
harness/sdk/testing/kits.py                    # SandboxKit (wrap() produces a command that cannot read a file outside ws, when backend available; skip otherwise)
tests/kernel/test_presets.py
tests/kernel/test_sandbox_fail_closed.py
tests/kernel/test_sandbox_bwrap.py            # @skipif(no bwrap): `cat /etc/hostname` ok (ro), `touch /tmp/x` ok (tmpfs), `cat $HOME/.netrc`-style outside-ws read fails, network curl fails with network=False
tests/kernel/test_daemon_no_approver_denies.py
tests/scenarios/{preset_locked_denies_exec,sandbox_unavailable_required_denies,sandbox_bwrap_full_badge}/…
THIRD_PARTY_NOTICES.md                        # only if the seatbelt profile text is ported verbatim from dsh (MIT)
```

### MODIFY

```
harness/sdk/contracts.py                       # SandboxBackend protocol; Executor.isolation literal += "os-sandbox"
harness/sdk/types.py                           # SandboxPolicy, SandboxAvailability; Decision.metadata (if absent)
harness/kernel/settings.py                     # kernel.security.preset (enum, default careful), kernel.sandbox.{mode,network,extra_readable}; resolver chain
harness/kernel/registry.py                     # sandbox slot
harness/kernel/commands.py                     # status.security
harness/plugins/executors/local/executor.py    # wrap via sandbox backend; metadata.sandbox
harness/plugins/policy/permission_modes/policy.py   # sandbox dimension; exec_in_sandbox
harness/plugins/policy/permission_modes/settings.schema.json
docs/12_TOOL_SAFETY_GUARDRAILS_AND_PERMISSIONS.md    # mark what is implemented vs design
```

### Skeleton: `backend.py` (bwrap)

```python
class BwrapBackend:
    name = "bwrap"
    def available(self) -> SandboxAvailability:
        exe = shutil.which("bwrap")
        if not exe: return SandboxAvailability(False, "bubblewrap (bwrap) not found on PATH", "none")
        # probe: bwrap --ro-bind / / --unshare-net true  (user namespaces may be disabled)
        ok = subprocess.run([exe, "--ro-bind", "/", "/", "--unshare-net", "--", "true"], capture_output=True, timeout=5).returncode == 0
        return SandboxAvailability(ok, "" if ok else "bwrap present but user namespaces unavailable", "full" if ok else "none")

    def wrap(self, req: ExecRequest, policy: SandboxPolicy) -> ExecRequest:
        argv = ["bwrap", "--die-with-parent", "--new-session", "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp"]
        for p in ("/usr", "/bin", "/lib", "/lib64", "/etc", "/opt"):
            if Path(p).exists(): argv += ["--ro-bind", p, p]
        for p in policy.readable_extra: argv += ["--ro-bind", str(p), str(p)]
        for p in (policy.workspace, *policy.writable): argv += ["--bind", str(p), str(p)]
        if not policy.network: argv += ["--unshare-net"]
        argv += ["--chdir", str(req.cwd), "--clearenv", *sum((["--setenv", k, os.environ[k]] for k in policy.env_allow if k in os.environ), [])]
        cmd = req.command if isinstance(req.command, list) else ["/bin/sh", "-c", req.command]
        return dataclasses.replace(req, command=[*argv, "--", *cmd], shell=False)
```

Note the `.venv`/interpreter problem: a workspace that needs tools outside `/usr` (e.g. `~/.local/bin`, nvm) is why `kernel.sandbox.extra_readable` exists; the README must say so, and the "partial" enforcement label is used when the user adds broad extra paths (`/home`, `/`).

## 5. Tests (fail-first)

| test | fails on main because |
|---|---|
| `test_presets.py::test_preset_expansion_and_customised` | no presets |
| `test_sandbox_fail_closed.py::test_required_and_unavailable_denies_exec` | no sandbox dimension; exec allowed unconfined |
| `test_sandbox_fail_closed.py::test_result_metadata_reports_enforcement` | no metadata |
| `test_daemon_no_approver_denies.py` | may pass (broker denies) — still add: it documents the daemon path and guards WS-08 |
| `test_sandbox_bwrap.py::test_outside_workspace_read_is_blocked` (skip if no bwrap) | unconfined |
| scenarios (3) | new |

CI: add `sudo apt-get install -y bubblewrap` to the Linux job so the bwrap tests run (a job without bwrap must still pass by skipping — assert skip reason in the job summary).

## 6. Acceptance

```bash
pytest tests/kernel/test_presets.py tests/kernel/test_sandbox_fail_closed.py tests/kernel/test_sandbox_bwrap.py -q
harness run -w /tmp/ws -p mock "run: cat ~/.ssh/id_rsa" → tool result: permission denied / no such file inside sandbox; UI badge "bwrap full"
harness settings set kernel.security.preset locked && harness run … "run: touch x" → denied by plan mode
# unavailable host (rename bwrap): preset careful → run_command denied with the install hint; status.security.sandbox.available == false
harness catalog --check && pytest -q
```

## 7. Non-goals / cautions

- Not a VM, not Docker (the `container|vm` isolation literal stays reserved). Containers are a different executor plugin.
- `bwrap` needs unprivileged user namespaces; many CI/containers disable them — the probe handles it, and `enforcement: none` must be truthful.
- Interactive PTY tasks (`manage_task`) under bwrap need `--new-session` considered carefully; if PTY handling breaks, run interactive tasks with `enforcement: "partial"` and say so.
- MCP servers and the provider HTTP clients are *not* sandboxed by this WS; only `ctx.executor` processes.
- Do not weaken `workspace_jail`; the two layers are complementary (arguments vs processes).

## 8. Agent brief

> Implement WS-06 from `review/dsh-integration/WS-06_permission_presets_sandbox.md` as two PRs. **A:** `harness/kernel/presets.py`, `kernel.security.preset` with resolver chain in `SettingsStore`, `status.security`, `tests/kernel/test_presets.py`, scenario `preset_locked_denies_exec`. **B:** `SandboxBackend` seam + `sandbox_bwrap` and `sandbox_seatbelt` plugins + `local` executor wrapping + permission_modes sandbox dimension, with `test_sandbox_fail_closed.py` red first. Enforcement must be reported in `ToolResult.metadata.sandbox` and in `status`. Install bubblewrap in CI. If you port the seatbelt profile text from dsh, add `THIRD_PARTY_NOTICES.md`. Open PRs `WS-06a: permission presets`, `WS-06b: OS sandbox behind executor`.
