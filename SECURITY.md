# Security Policy

Harness-Zero provides a modular, extensible AI agent runtime. Because agent runtimes execute arbitrary tool operations and can interface with LLMs and local file systems, security is an architectural priority.

This document outlines the Harness-Zero threat model, trust boundaries, defense-in-depth mitigations, plugin capability enforcement, and vulnerability reporting procedures.

---

## 1. Threat Model & Trust Boundaries

Harness-Zero is designed as a **local, single-user agent daemon**. It is **not** intended to be deployed as an unauthenticated multi-tenant public web service.

```
                     +---------------------------------------+
                     |             Local User                |
                     +---------------------------------------+
                                         |
                       Bearer Token / SOP / Same-Origin
                                         v
   +------------------------------------------------------------------------+
   |  Kernel Daemon (:8000)                                                 |
   |                                                                        |
   |  +------------------------+        +--------------------------------+  |
   |  | Web UI & REST API      |        | Kernel Security Layers         |  |
   |  | - Token Auth           |        | - Path Traversal Sandbox       |  |
   |  | - Host/Origin Check    |------->| - Environment Sanitization     |  |
   |  | - Ticket/Cookie Auth   |        | - Tirith Policy & AST Capsule  |  |
   |  +------------------------+        | - Approval Policy Engine       |  |
   |                                    +--------------------------------+  |
   |                                                     |                  |
   |                                                     v                  |
   |                                    +--------------------------------+  |
   |                                    | In-Process Plugins & SDK       |  |
   |                                    | - Boundary: harness.sdk only   |  |
   |                                    | - Declared Capabilities        |  |
   |                                    +--------------------------------+  |
   +------------------------------------------------------------------------+
                                         |
                     Separate Origin / Sandboxed Iframe
                                         v
   +------------------------------------------------------------------------+
   |  Artifact Sandbox Server (:8001)                                       |
   |  - Renders user/agent generated HTML, SVG, JS                          |
   |  - Strictly isolated from :8000 cookies, tokens, and localStorage      |
   +------------------------------------------------------------------------+
```

### Trust Assumptions
1. **Host OS & Operator**: The user running `harness` has legitimate local permissions on the operating system. Commands executed by the agent execute with the permissions of this local OS user.
2. **Untrusted Model Outputs**: Content returned by external LLMs, scraped web pages, and user-facing artifacts must be treated as untrusted (risk of prompt injection, SSRF, XSS, and malicious shell snippets).
3. **Dual-Origin Isolation**: Browser execution of untrusted artifacts must never have ambient access to the management daemon's authentication tokens.

---

## 2. Core Security Mitigations

### 2.1 Dual-Origin Web Architecture (SOP Sandbox)
To eliminate Cross-Site Scripting (XSS) and cross-origin token theft from agent-generated web artifacts, Harness-Zero runs two distinct HTTP origins:
- **Admin & Control Daemon (`http://127.0.0.1:8000`)**: Serves the Harness web UI and control API (`/commands/*`, `/events`, `/health`). Requests are guarded by token auth and origin checking.
- **Artifact Sandbox (`http://127.0.0.1:8001`)**: Serves static HTML/JS/SVG artifacts generated during sessions (`/artifacts/*`). By operating on port `8001`, the browser enforces strict Same-Origin Policy (SOP): scripts executing inside generated artifacts cannot read credentials, tokens, or event streams from port `8000`.

### 2.2 CSRF, Origin, and Host Validation
- **Host Header Enforcement**: The daemon validates that the `Host` header strictly matches `localhost`, `127.0.0.1`, or explicit allowed loopback addresses, blocking DNS rebinding attacks with HTTP 421.
- **Origin Header Check**: Mutating requests check `Origin` and `Sec-Fetch-Site`. Cross-origin or cross-site requests are rejected with HTTP 403.
- **Single-Use SSE Tickets & HttpOnly Cookies**: Native browser `EventSource` cannot pass custom Authorization headers. Instead, authentication is supported via `HttpOnly` session cookies set on `/`, or short-lived (60s) single-use tickets acquired via `POST /auth/ticket` and burned upon the `/events` handshake. Master tokens in query parameters are strictly forbidden.

### 2.3 Workspace Boundary & Path Traversal Jail
- All filesystem tools (`read_file`, `write_file`, `list_files`, `edit_file`) resolve paths through `safe_path()`.
- Paths are canonicalized (`resolve()`) and verified to reside within the active workspace root.
- Directory traversal sequences (`../`, `..\\`) and symlinks escaping the workspace boundary are rejected with a permission error.

### 2.4 Human-in-the-Loop Approval Policies
- Sensitive operations (destructive shell execution, filesystem mutations outside scratch dirs, credential access) are classified under the `approval` capability.
- When an action triggers the policy, execution halts and creates an `ApprovalRequest` with a cryptographic approval token.
- The user can inspect the proposed tool call in the UI or CLI before explicitly approving or denying the action.

### 2.5 Environment Sanitization & Secret Redaction
- In-process execution scrub subprocess environments. Sensitive variables (`HARNESS_API_KEY`, cloud credentials, system master tokens) are stripped from default subprocess environments unless explicitly passed via secrets management.
- Logs and streaming events redact known tokens and API keys matching high-entropy regex patterns before dispatching over the event bus.

### 2.6 Tirith Security Capsule
- Shell and code execution tools pass through AST and command inspection before dispatch.
- Tirith rules inspect shell commands for dangerous patterns (`rm -rf /`, `mkfs`, raw curl pipes to bash, unauthorized socket listeners) and trigger mandatory approval or hard denial.

---

## 3. Plugin Capability & Trust Model

Harness-Zero plugins run **in-process** inside the Python runtime for maximum performance and low-latency event processing. Because in-process code has access to the Python interpreter, isolation is enforced through static boundaries, manifest declarations, and runtime doctor audits:

### 3.1 Capability Declarations
Every plugin must define a `harness_plugin.toml` manifest declaring exactly which capabilities it requires from the `Capability` enum:

| Capability | Description | Risk Level |
|---|---|---|
| `fs:read` | Read workspace files | Medium |
| `fs:write` | Write or modify workspace files | High |
| `exec` | Execute shell commands or external processes | High |
| `net` | Make outbound HTTP or network connections | Medium |
| `secrets` | Request credentials from the configured secrets provider | Critical |
| `approval` | Require human approval before execution | Low (Safety) |
| `approval:bypass` | Bypass interactive approval checks | Critical |
| `ui` | Register custom UI panels or generative widgets | Low |

Unregistered or arbitrary capability strings are rejected at manifest load time.

### 3.2 Import Boundary Enforcement
- Plugins are strictly prohibited from importing internal kernel modules (`harness.kernel.*`, `harness.web.*`, `harness.cli.*`).
- Plugins may **only** import from `harness.sdk` or standard library / approved third-party dependencies.
- The SDK exposes typed, stable `Protocol` interfaces (`Tool`, `Provider`, `ContextSource`, `Policy`, `Executor`, `Strategy`, `Bridge`, `Store`).
- Architectural boundary tests (`test_import_boundaries.py`) and `harness plugin doctor` verify compliance before release.

### 3.3 What is NOT Provided (Out of Scope)
- **OS-Level Virtualization**: Unless deployed inside an external container (Docker, Podman, Firecracker), in-process plugins and child shell commands share the privileges of the host user running `harness`.
- **Malicious Plugin Prevention**: While static linting and import checks prevent accidental leaks, in-process plugins are trusted Python code. **Do not install plugins from untrusted third parties without auditing their source code.**

---

## 4. Security Best Practices for Users

1. **Keep Harness-Zero Local**: Do not expose port `8000` or `8001` to public network interfaces (`0.0.0.0`) without a reverse proxy implementing TLS and mutual authentication.
2. **Use Sandboxed Workspaces**: Run agents in isolated project directories rather than home (`~`) or root (`/`) directories.
3. **Audit Plugins**: Use `harness plugin doctor --all` to verify integrity and inspect declared capabilities before enabling third-party plugins.
4. **Maintain Approvals**: Leave the `ApprovalPolicy` enabled for shell execution and filesystem write operations on critical projects.

---

## 5. Reporting a Vulnerability

We take security issues seriously. If you discover a security vulnerability in Harness-Zero, please report it responsibly:

- **Do NOT open a public GitHub issue.**
- Submit your vulnerability report privately via email to: **security@harness-zero.local** (or the project maintainers).
- Please include:
  1. A description of the vulnerability and potential impact.
  2. Step-by-step reproduction instructions or a minimal proof of concept (PoC).
  3. Affected versions, OS, and configurations.
- We will acknowledge receipt within 48 hours and work with you to validate and remediate the issue prior to public disclosure.
