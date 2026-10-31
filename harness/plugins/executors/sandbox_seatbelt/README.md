# sandbox-seatbelt

## Summary

The built-in `sandbox-seatbelt` plugin registers macOS Seatbelt (`sandbox-exec`) as an OS sandbox backend for processes started through `ctx.executor`. It creates a profile from the workspace, explicit readable paths, and network setting. The backend reports `partial` enforcement rather than presenting the profile as a VM or a fully isolated operating system.

## Configuration

| Key | Default | Meaning |
|---|---|---|
| — | — | This plugin reads no plugin-specific settings. Configure the kernel-wide sandbox in **Security & Sandboxing** or through `kernel.sandbox.*`; permission presets are selected with `kernel.security.preset`. |

Use `kernel.sandbox.mode` to choose `required`, `preferred`, or `off`; `kernel.sandbox.backend=auto` selects Seatbelt on macOS. `kernel.sandbox.network` controls outbound network access, and `kernel.sandbox.extra_readable` explicitly grants read-only access to additional absolute paths.

## Extension points

The plugin implements the SDK `SandboxBackend` contract and registers it through `PluginContext.register_sandbox_backend`. The platform implementation lives in `harness.sdk.sandbox_seatbelt`; tools and policies must use `ctx.executor`, not import `subprocess` or invoke `sandbox-exec` themselves.

## Model experience

The generated profile allows process creation, reads from system runtime paths and the workspace, writes within the workspace plus the allowed `/private/tmp` path, and denies network access by default. It preserves the validated requested working directory, including nested paths; outside cwd and outward symlink targets are rejected. An approved unconfined fallback is reported as `enforcement: none`; the kernel never substitutes the host executor when `required` confinement is unavailable.

## Known limitations

Apple has deprecated `sandbox-exec`, so availability is based on a live profile probe and may change across macOS releases. Seatbelt does not create a private filesystem namespace. Loader bootstrap may read the exact root directory, not its descendants. Native nested-cwd/relative-write and outside-file denial tests pass on the current Mac; these do not prove every filesystem or network rule on all macOS versions. Adding broad extra-readable paths exposes those files to the child process; enforcement remains `partial`. See `docs/testing/T11_CONFINED_CWD.md` for scoped native evidence and remaining qualification limits.
