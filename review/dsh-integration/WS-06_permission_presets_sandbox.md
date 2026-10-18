# WS-06 — Permission presets and OS process confinement behind the executor seam

| | |
|---|---|
| Status | executed (P15; WS-06a + WS-06b) |
| Depends on | WS-01 (scenarios), WS-02 (settings declared); WS-04 for `tool.decision` records (not blocking) |
| Effort | 1 day presets + 3–4 days sandbox |
| Principles | P3 (primary), P6 |
| dsh reference (@86a40220) | `packages/core/permission/` (`PermissionPreset` = sandbox mode × approval policy; presets `strict / standard / permissive / unrestricted`-style names, "deny when unanswerable"), `packages/core/sandbox/` (service definition: `SandboxProvider.wrap(command, policy) → command`, `enforcement: "full" | "partial"`, `SANDBOX_UNAVAILABLE`), `packages/platform/sandbox-landlock/` (Linux, own Node addon), `packages/platform/sandbox-seatbelt/` (macOS `sandbox-exec` profile), `packages/platform/sandbox-windows/` (restricted token). `docs/security.md` ("enforcement is a reported fact") |
| Harness-Zero touchpoints | `harness/plugins/policy/permission_modes/policy.py:25-90` (modes `allow | ask | plan | bypass`, order 900; `kernel.security.permission_mode`, `kernel.security.destructive_blocking`), `harness/kernel/settings.py:41-44`, `harness/kernel/approvals.py:65-67` (no interactive subscriber ⇒ deny — fail-closed already for the broker), `harness/kernel/gate.py:592-597` (executor selection: `registry.executors["default"]` else `["local"]`), `harness/sdk/contracts.py:101-107` (`Executor.start(ExecRequest) -> Process`, `isolation` literal `none|worktree|container|vm`), `harness/sdk/types.py:764-771` (`ExecRequest`: command, cwd, env_overlay, timeout_s, interactive, shell), `harness/plugins/executors/local/executor.py:177-200` (`create_subprocess_shell/exec`), `harness/plugins/executors/worktree/`, `harness/plugins/policy/workspace_jail/` (path jail — *policy*, not OS-level), `docs/05_EXECUTION_SANDBOX_AND_GIT_WORKTREES.md`, `docs/research/12_TOOL_SAFETY_GUARDRAILS_AND_PERMISSIONS.md` (design intent, not implemented) |

## 1. Outcome

**Part A — presets (1 day).** `kernel.security.preset ∈ {locked, careful, standard, open}` is the one knob the user touches; each preset expands to the existing settings plus the new sandbox ones:

| preset | permission_mode | destructive_blocking | sandbox.mode | network in sandbox | approval timeout |
|---|---|---|---|---|---|
| `locked` | `plan` | true | `required` | off | n/a |
| `careful` (default) | `ask` | true | `preferred` (unavailable ⇒ ask before unconfined fallback) | off | 300 s |
| `standard` | `ask` for writes; EXEC may be allowed only after a successful sandbox probe | true | `preferred` | off | 300 s |
| `open` | `allow` | true | `off` (explicitly unconfined) | on | n/a |

`careful` also sets `exec_in_sandbox=ask`; `standard` sets `exec_in_sandbox=allow`, but that only fast-paths EXEC after the selected backend reports available. `bypass` stays available only as an explicit `kernel.security.permission_mode=bypass`, never as a preset. Required mode denies before spawn even if permission mode is `bypass`; preferred mode asks before unconfined execution, except for that explicit bypass choice, which is still reported as `enforcement: none`. Setting a preset is one `settings.set`; the resolver keeps its expansion virtual, explicit stored values win, and `security.status` reports effective values plus customisations.

**Part B — sandbox.** `run_command` and other tools using `ctx.executor` are wrapped at the executor seam when `kernel.sandbox.mode != off`; the wrapper covers the selected executor, rather than relying on the local executor alone:

- Linux: bubblewrap (`bwrap`) is namespace-probed (user, PID, IPC, UTS, network) before use. It exposes system runtime paths read-only, the workspace read/write, a private `/tmp`, and a cleared allowlisted environment; the host home is not mounted. Network is isolated unless explicitly allowed. Explicit extra-readable paths are read-only and downgrade the reported enforcement to `partial`.
- macOS: `sandbox-exec` receives a generated Seatbelt profile for the workspace, explicit readable paths, temporary directory, and network setting. It is reported as `partial`; Apple has deprecated `sandbox-exec`.
- Unsupported host, missing/disabled backend, failed probe, malformed paths, or wrapping errors: **unavailable/fail closed**; required never falls through to the host executor.

Enforcement is a **reported fact** on `ExecResult.metadata.sandbox` and decision metadata; the public status shape is `{mode, backend, available, enforcement, reason, network}`. `security.status`, `/health`, `harness security show`, and the Studio badge share that source. Required + unavailable denies before spawn; preferred + unavailable returns ASK before unconfined fallback; `off` means explicitly unconfined and still reports `enforcement: none`.

## 2. Implemented state (verified 2026-10-16)

- Presets are a resolver layer: explicit in-memory override or stored value → preset expansion → call-site default → schema default. Expansions are not materialised in `settings.json`; `bypass` is not a preset.
- `SandboxService` is owned by `PluginRegistry`; registered `SandboxBackend`s are wrapped around the selected executor by the policy gate after policy/approval. `local`, `worktree`, and other executor choices cannot skip the wrapper.
- Linux bubblewrap and macOS Seatbelt adapters are implemented. Required-unavailable denies before spawn; preferred-unavailable asks; explicit `off` runs unconfined and reports `none`. Malformed modes, availability facts, paths, or wrappers do not fall back to host execution.
- `security.status`, `/health`, `harness security show`, `tool.decision`, final process-result metadata, and the Studio badge report the effective posture. Sandbox-setting changes refresh the badge.
- The approval-broker HTTP path without an interactive SSE approver is covered by `test_daemon_without_an_sse_approver_denies_write_before_handler`; the existing workspace-jail policy remains complementary, not a substitute for process confinement.
- The Linux host used for this run has no `bwrap`; the real bubblewrap integration tests and full-enforcement scenario therefore skipped with an explicit reason. CI installs bubblewrap on Linux. Seatbelt profile generation has mocked unit coverage only; there is no real macOS confinement integration evidence in this run.

## 3. Design

### 3.1 Presets (kernel)

`harness/kernel/presets.py`:

```python
PRESETS: dict[str, dict[str, Any]] = {
  "locked":   {"kernel.security.permission_mode": "plan", "kernel.security.destructive_blocking": True, "kernel.sandbox.mode": "required", "kernel.sandbox.network": False},
  "careful":  {"kernel.security.permission_mode": "ask", "kernel.security.destructive_blocking": True, "kernel.security.approval_timeout_s": 300, "kernel.security.exec_in_sandbox": "ask", "kernel.sandbox.mode": "preferred", "kernel.sandbox.network": False},
  "standard": {"kernel.security.permission_mode": "ask", "kernel.security.destructive_blocking": True, "kernel.security.approval_timeout_s": 300, "kernel.security.exec_in_sandbox": "allow", "kernel.sandbox.mode": "preferred", "kernel.sandbox.network": False},
  "open":     {"kernel.security.permission_mode": "allow", "kernel.security.destructive_blocking": True, "kernel.sandbox.mode": "off", "kernel.sandbox.network": True},
}
def resolve(settings, path): ...                       # explicit value → preset expansion → UNSET
```

`SettingsStore.get` applies in-memory override / stored value → resolver → call-site default → schema default. No expansion is persisted. The durable `settings.changed` event still contains paths, not derived values.

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

`PluginContext.register_sandbox_backend(backend)` registers the platform adapters (`executors/sandbox_bwrap`, `executors/sandbox_seatbelt`). `SandboxService` selects/probes one, and the gate wraps whichever executor is selected after policy/approval; this covers `local`, `worktree`, and third-party executor plugins. `SandboxedProcess` preserves process exit status and attaches the sandbox fact to the final `ExecResult.metadata`.

### 3.3 Policy

`permission_modes` (order 900) applies the sandbox dimension to executor-backed calls. Required-unavailable is a hard deny, including under explicit permission bypass. Preferred-unavailable returns ASK unless the user explicitly selected `bypass`; an approved fallback is marked unconfined. `exec_in_sandbox=allow` fast-paths only an EXEC call after the backend probe succeeds; WRITE/DESTRUCTIVE actions remain governed by the permission mode and destructive-block setting. `Decision.metadata["sandbox"]` carries the status used by the UI and event log. The executor wrapper checks again immediately before spawn, so a backend disappearing or throwing cannot become a host fallback.

### 3.4 `status` and UI

`security.status` adds `{preset, customised, pinned, settings, sandbox: {mode, backend, available, enforcement, reason, network}}`; `/health` and `harness security show` use the same kernel status. The composer badge reflects that status and refreshes on sandbox-setting changes. The schema-driven "Security & Sandboxing" settings group exposes the preset, backend/mode/network controls, and explicit extra-readable paths; there is no bespoke preset HTML.

## 4. Files

### ADD

```
harness/kernel/{presets.py,sandbox.py}
harness/sdk/{sandbox_bwrap.py,sandbox_seatbelt.py}
harness/plugins/executors/sandbox_{bwrap,seatbelt}/{__init__.py,harness_plugin.toml,plugin.py,backend.py,README.md}
harness/sdk/testing/kits.py                    # sandbox conformance helpers

tests/kernel/test_presets.py
tests/kernel/test_sandbox.py                   # fail-closed cases, profile/argv tests, and conditional real bwrap confinement
tests/frontends/test_web_turn.py               # daemon/HTTP request without an interactive SSE approver
tests/scenarios/{preset_locked_denies_exec,sandbox_unavailable_required_denies,sandbox_bwrap_full_badge,security_preset_badge_web_dom}/…
# Seatbelt profile is implemented independently; no verbatim dsh code was ported, so no third-party notice was needed.
```

### MODIFY

```
harness/sdk/contracts.py                       # SandboxBackend protocol; Executor isolation includes os-sandbox
harness/sdk/types.py                           # SandboxPolicy, SandboxAvailability, ExecResult.metadata
harness/kernel/settings.py                     # preset resolver + kernel.sandbox.{mode,backend,network,extra_readable}
harness/kernel/{bootstrap,gate,registry,commands}.py # service ownership, wrapping after policy/approval, shared status
harness/kernel/inbox.py                        # preserve process exit code/result metadata through background tasks
harness/plugins/policy/permission_modes/{plugin.py,policy.py,settings.schema.json}
harness/frontends/web/static/{app.js,settings.js,styles.css} # settings editor and truthful status badge
harness/cli.py                                  # harness security show
.github/workflows/ci.yml                       # install bubblewrap on Linux
docs/research/{05_EXECUTION_SANDBOX_AND_GIT_WORKTREES,12_TOOL_SAFETY_GUARDRAILS_AND_PERMISSIONS}.md
```

### Original skeleton: `backend.py` (design sketch, superseded by the tested implementation)

The executable adapter is `harness/sdk/sandbox_bwrap.py`; the sketch below is retained as design history only.
The production probe and path handling are stricter than this pseudocode, so do not copy it as an implementation.

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

The README documents the `.venv`/interpreter problem: tools outside the runtime/workspace (for example `~/.local/bin` or a language-manager directory) require an explicit absolute `kernel.sandbox.extra_readable` grant; extra-readable paths are reported as `partial`, and `/` is refused.

## 5. Regression coverage and evidence

| test | evidence |
|---|---|
| `tests/kernel/test_presets.py` | preset expansion, explicit overrides, `bypass` exclusion, no materialized values, and idempotent resolution |
| `tests/kernel/test_sandbox.py` | required/preferred/off modes, malformed or mismatched backend facts, path validation, result metadata/exit status, bwrap argv and conditional real confinement, plus mocked Seatbelt profile rules |
| `tests/frontends/test_web_turn.py::test_daemon_without_an_sse_approver_denies_write_before_handler` | HTTP/daemon path denies without an interactive SSE approver |
| scenarios | `preset_locked_denies_exec`, `sandbox_unavailable_required_denies`, `sandbox_bwrap_full_badge`, `security_preset_badge_web_dom` |

The full suite reported **655 passed, 4 skipped**; all four skips were real-bwrap-dependent on this runner (three kernel integrations and one scenario). The scenario lane independently reported **30 passed, 1 skipped**. The local host has no `bwrap`, so real namespace confinement was not verified here. The Linux CI job installs bubblewrap and runs pytest with `-rs`, retaining explicit skip reasons when user namespaces are unavailable.

## 6. Acceptance (executed)

```bash
.venv/bin/pytest -q -rs                                      # 655 passed, 4 bwrap-dependent skips
.venv/bin/pytest -q -rs tests/scenarios/test_scenarios.py    # 30 passed, 1 bwrap-dependent skip
.venv/bin/harness plugin test --all                          # 220 passed
.venv/bin/harness catalog --check --section drift --section settings --section ui
.venv/bin/ruff check .
python tests/frontends/ui_audit.py --strict                  # 0 findings
```

The focused preset/sandbox, fail-closed, unavailable-host, policy, CLI and web tests are included in the full suite. `harness catalog --check --format json` also reports existing advisory debt (incomplete READMEs and scenario-coverage gaps); the required drift/settings/UI sections pass. No local command is represented as proof of real bubblewrap confinement: `bwrap` was absent here, and CI is responsible for exercising the real kernel boundary on a suitably configured Linux runner.

## 7. Non-goals / cautions

- Not a VM, not Docker (the `container|vm` isolation literal stays reserved). Containers are a different executor plugin.
- `bwrap` needs unprivileged user namespaces; many CI/containers disable them — the probe handles it, and `enforcement: none` must be truthful.
- Interactive PTY tasks (`manage_task`) under bwrap need `--new-session` considered carefully; if PTY handling breaks, run interactive tasks with `enforcement: "partial"` and say so.
- MCP servers and the provider HTTP clients are *not* sandboxed by this WS; only `ctx.executor` processes.
- Do not weaken `workspace_jail`; the two layers are complementary (arguments vs processes).

## 8. Execution record

WS-06a and WS-06b were implemented together as P15 on the existing branch/PR. The resolver, executor seam, both backend adapters, fail-closed policy, status/CLI/Studio surfaces, CI bubblewrap installation, regression tests, and four scenarios are in place. No new PR was opened for the two subphases. The changes remain uncommitted and unpushed; the existing PR state was not changed. This runner did not have bubblewrap, so real Linux confinement remains unverified locally; macOS Seatbelt has mocked profile-builder coverage but no real macOS integration run. See `review/CONSOLIDATION_LOG.md` §P15 for the implementation findings and complete gate record.
