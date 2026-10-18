# sandbox-bwrap

## Summary

The built-in `sandbox-bwrap` plugin registers Linux bubblewrap as an OS sandbox backend for processes started through `ctx.executor`. The kernel probes bubblewrap's user, PID, IPC, UTS, and network namespace support before it reports the backend as available. It is process confinement, not a container or VM.

## Configuration

| Key | Default | Meaning |
|---|---|---|
| — | — | This plugin reads no plugin-specific settings. Configure the kernel-wide sandbox in **Security & Sandboxing** or through `kernel.sandbox.*`; permission presets are selected with `kernel.security.preset`. |

The principal controls are `kernel.sandbox.mode` (`required`, `preferred`, or `off`), `kernel.sandbox.backend` (`auto`, `bwrap`, `seatbelt`, or `none`), `kernel.sandbox.network`, and `kernel.sandbox.extra_readable`. `standard` allows executor calls without a click only when the selected backend's probe succeeds; `careful` still asks before state-changing actions.

## Extension points

The plugin implements the SDK `SandboxBackend` contract and registers it through `PluginContext.register_sandbox_backend`. The platform implementation lives in `harness.sdk.sandbox_bwrap`; tools and policies must use `ctx.executor`, not import `subprocess` or invoke bubblewrap themselves.

## Model experience

A confined command sees the workspace as writable, the base runtime paths as read-only, a private `/tmp`, and a cleared allowlisted environment. The host home directory is not mounted. Networking is isolated by default; the explicit `kernel.sandbox.network=true` setting allows it. Each process result carries the backend, availability, enforcement, and network facts in `metadata.sandbox`.

## Known limitations

Bubblewrap must be installed and the required unprivileged namespaces must work on the host; a present executable is not enough. A toolchain outside the workspace (for example, `~/.local/bin` or an SDK under a language-manager directory) needs an explicit absolute `kernel.sandbox.extra_readable` path. Such grants are read-only and downgrade the reported enforcement to `partial`. The plugin is Linux-only; Windows and unsupported hosts report the backend unavailable, and `required` mode refuses to fall back to the host executor.

The integration tests perform real confinement only when the namespace probe succeeds. A skipped test is not evidence of enforcement; CI prints the skip reason.
