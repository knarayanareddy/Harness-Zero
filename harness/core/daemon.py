"""
Daemon IPC Server for Harness-Zero.
Implements Dual-Port Origin Separation (Port 8000 API vs Port 8001 Preview Sandbox)
synthesized from Claude Cowork (Operon) for browser SOP defense, plus JSON-RPC 2.0.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
import uuid

from aiohttp import web

from harness.core.coordinator import AgentCoordinator

logger = logging.getLogger("harness.daemon")


class HarnessDaemon:
    def __init__(
        self,
        coordinator: AgentCoordinator,
        host: str = "127.0.0.1",
        port: int = 8000,
        sandbox_port: int = 8001,
        socket_path: Optional[Path] = None,
    ):
        self.coordinator = coordinator
        self.host = host
        self.port = port
        self.sandbox_port = sandbox_port
        self.socket_path = socket_path

        self._login_tokens: Dict[str, float] = {}
        self._active_turn_tasks: Dict[str, asyncio.Task] = {}

        # Dual-Port Isolation Applications
        self.app = web.Application()
        self.sandbox_app = web.Application()

        self._setup_api_routes()
        self._setup_sandbox_routes()

    def generate_login_url(self) -> str:
        """
        Generates a cryptographically random, single-use 3-minute login link.
        Synthesized from Claude Cowork's 'claude-science url' remote SSH access pattern.
        """
        import secrets
        import time

        token = secrets.token_urlsafe(32)
        # Valid for 180 seconds (3 minutes)
        self._login_tokens[token] = time.time() + 180.0
        return f"http://{self.host}:{self.port}/login?token={token}"

    def _setup_api_routes(self) -> None:
        self.app.router.add_get("/", self._handle_index)
        self.app.router.add_get("/ui", self._handle_index)
        self.app.router.add_get("/health", self._handle_health)
        self.app.router.add_post("/rpc", self._handle_rpc)
        self.app.router.add_get("/skills", self._handle_skills)
        self.app.router.add_get("/tasks", self._handle_tasks)
        self.app.router.add_get("/files", self._handle_files)
        self.app.router.add_get("/artifacts", self._handle_artifacts_list)
        self.app.router.add_get("/artifacts/{filename}", self._handle_get_artifact)
        self.app.router.add_get("/spawn-tree", self._handle_spawn_tree)
        self.app.router.add_get("/economics", self._handle_economics)
        self.app.router.add_post("/search/code", self._handle_search_code)
        self.app.router.add_get("/settings", self._handle_settings_get)
        self.app.router.add_post("/settings", self._handle_settings_post)
        self.app.router.add_get("/file/content", self._handle_file_content)
        self.app.router.add_post("/file/save", self._handle_file_save)
        self.app.router.add_get("/diffs", self._handle_diffs)
        self.app.router.add_post("/turn/stream", self._handle_turn_stream)
        self.app.router.add_post("/turn/cancel", self._handle_turn_cancel)
        self.app.router.add_get("/events", self._handle_events)
        self.app.router.add_get("/login", self._handle_login)
        self.app.router.add_post("/media/upload", self._handle_media_upload)
        self.app.router.add_get("/media/{filename}", self._handle_media_serve)
        self.app.router.add_get("/sessions", self._handle_sessions_list)
        self.app.router.add_get("/sessions/{session_id}", self._handle_session_get)
        self.app.router.add_post("/sessions/new", self._handle_session_new)
        self.app.router.add_post("/sessions/switch", self._handle_session_switch)
        self.app.router.add_get("/skills/{name}", self._handle_skill_playbook)
        self.app.router.add_get("/plugins", self._handle_plugins)

        # Serve static assets (Tailwind, Jetbox, Prism)
        assets_dir = Path(__file__).resolve().parent.parent / "web" / "assets"
        if assets_dir.exists():
            self.app.router.add_static("/assets", str(assets_dir))

    def _setup_sandbox_routes(self) -> None:
        """Port 8001: Untrusted sandbox serving generated artifacts & Generative UI."""
        self.sandbox_app.router.add_get("/", self._handle_sandbox_index)
        self.sandbox_app.router.add_get("/artifacts/{filename}", self._handle_sandbox_artifact)
        self.sandbox_app.router.add_get("/health", self._handle_sandbox_health)

    async def _handle_files(self, request: web.Request) -> web.Response:
        """Returns workspace files tree for the Explorer view."""
        files = []
        for p in self.coordinator.workspace.rglob("*"):
            if any(part.startswith(".") for part in p.parts):
                continue
            if "__pycache__" in p.parts or "node_modules" in p.parts or "dist" in p.parts:
                continue
            if p.is_file():
                try:
                    rel = p.relative_to(self.coordinator.workspace)
                    files.append({
                        "path": str(rel),
                        "name": p.name,
                        "size": p.stat().st_size,
                        "ext": p.suffix,
                    })
                except (OSError, ValueError):
                    continue
        return web.json_response({"files": files[:100]})

    async def _handle_index(self, request: web.Request) -> web.Response:
        web_file = Path(__file__).resolve().parent.parent / "web" / "index.html"
        if web_file.exists():
            return web.Response(text=web_file.read_text(encoding="utf-8"), content_type="text/html")
        return web.Response(text="<h1>Harness-Zero Web UI not found</h1>", content_type="text/html", status=404)

    async def _handle_artifacts_list(self, request: web.Request) -> web.Response:
        manifest = self.coordinator.artifacts.load_manifest()
        return web.json_response({"manifest": manifest})

    async def _handle_get_artifact(self, request: web.Request) -> web.Response:
        filename = request.match_info.get("filename", "")
        content = self.coordinator.artifacts.get_artifact(filename)
        if content is None:
            return web.Response(text="Artifact not found", status=404)
        return web.Response(text=content, content_type="text/plain")

    async def _handle_spawn_tree(self, request: web.Request) -> web.Response:
        tree = []
        if hasattr(self.coordinator, "spawn_ledger"):
            tree = self.coordinator.spawn_ledger.get_tree()
        return web.json_response({"tree": tree})

    async def _handle_economics(self, request: web.Request) -> web.Response:
        summary = {}
        if hasattr(self.coordinator, "cost_tracker"):
            summary = self.coordinator.cost_tracker.get_summary()
        return web.json_response(summary)

    async def _handle_search_code(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
            pattern = body.get("pattern", "")
            matches = await self.coordinator.ripgrep.search(pattern)
            return web.json_response({"matches": [
                {"path": m.file_path, "line": m.line_number, "text": m.line_content}
                for m in matches
            ]})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    def _settings_file(self) -> Path:
        p = self.coordinator.workspace / ".harness" / "settings.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def _get_cowork_settings(self) -> Dict[str, Any]:
        sf = self._settings_file()
        stored = {}
        if sf.exists():
            try:
                stored = json.loads(sf.read_text(encoding="utf-8"))
            except Exception:
                pass

        router = self.coordinator.router
        defaults = {
            "general": {
                "theme": "dark",
                "locale": "en-US",
                "launch_at_login": False,
                "keep_in_background": True,
                "telemetry_enabled": False,
            },
            "account": {
                "account_id": "eb2ed2bf-f314-4c1d-a782-febe96c771f9",
                "email": "brightech@local.device",
                "organization_id": "90abf3fd-1c92-4b4e-8733-809314ab824b",
                "is_signed_in": True,
                "keyring_status": "unlocked",
            },
            "cowork": {
                "remote_control_enabled": True,
                "cowork_user_files_path": str(self.coordinator.workspace),
                "trusted_folders": [
                    str(self.coordinator.workspace),
                    str(Path.home() / "Claude"),
                ],
                "bypass_permissions_gate": False,
                "ble_maker_enabled": False,
            },
            "capabilities": {
                "web_search_enabled": True,
                "browser_tools_enabled": True,
                "preferred_browser": "built_in",
                "allowed_sites": [
                    "127.0.0.1:8000",
                    "127.0.0.1:8001",
                    "localhost:8000",
                    "localhost:8001",
                    "github.com",
                    "openrouter.ai",
                ],
                "scheduled_tasks_enabled": False,
            },
            "extensions": {
                "developer_mode": True,
                "unpacked_extensions_enabled": True,
                "mcp_servers": {
                    "filesystem_mcp": {"status": "connected", "command": "npx -y @modelcontextprotocol/server-filesystem"},
                    "database_mcp": {"status": "connected", "command": "npx -y @modelcontextprotocol/server-sqlite"},
                    "github_mcp": {"status": "connected", "command": "npx -y @modelcontextprotocol/server-github"},
                },
            },
            "developer": {
                "developer_mode": True,
                "allow_custom_mcp_servers": True,
                "mcp_debug_logging": True,
            },
            "models": {
                "default_provider": router.default_provider,
                "default_model": router.default_model,
                "auto_fallback": True,
                "temperature": 0.2,
                "max_tokens": 4096,
                "reasoning_effort": "medium",
                "max_tool_iterations": 10,
                "local_base_url": router.local_base_url,
            },
            "security": {
                "default_permission_mode": "allow",
                "tirith_ssrf_shield": True,
                "destructive_command_blocking": True,
                "ssh_key_shield": True,
                "protected_locations": ["/etc", "/var", "~/.ssh", "~/.gnupg"],
                "session_spend_cap_usd": 5.0,
            },
            "privacy": {
                "data_retention": "local_only",
                "crash_diagnostics": False,
                "analytics_logging": False,
            },
            "billing": {
                "plan": "Free",
                "spend_cap_usd": 10.0,
                "total_usage_usd": 0.04,
                "currency": "USD",
            },
            "memory": {
                "generate_memory_from_chats": False,
            },
            "reflect": {
                "thinking_budget": "medium",
                "reflection_depth": "balanced",
            },
            "time_focus": {
                "quiet_hours_enabled": False,
                "focus_interval_minutes": 25,
            },
            "claude_code": {
                "classify_session_states": True,
                "switch_models_when_flagged": True,
                "code_theme": "dark",
            },
            "system": {
                "user_files_path": str(Path.home() / "Claude"),
                "permissions_gate": False,
                "preferred_browser": "built_in",
                "device_name": "macbook-air-van-brightech-local",
            },
            "skills": {
                "docx": {"name": "docx", "enabled": True, "description": "Create, read, edit Word documents (.docx)"},
                "pdf": {"name": "pdf", "enabled": True, "description": "PDF manipulation, merge, encrypt, OCR"},
                "pdf-reading": {"name": "pdf-reading", "enabled": True, "description": "Read, inspect, extract content from PDF files"},
                "pptx": {"name": "pptx", "enabled": True, "description": "PowerPoint presentations creation and editing"},
                "xlsx": {"name": "xlsx", "enabled": True, "description": "Spreadsheet manipulation, data cleaning, formulas"},
                "frontend-design": {"name": "frontend-design", "enabled": True, "description": "Distinctive intentional UI visual design guidance"},
            },
            "connectors": {
                "directory_search_enabled": True,
                "active_connectors": [],
            },
            "plugins": {
                "active_plugins": [
                    {"id": "rea", "name": "REA: Reverse Engineer Anything", "version": "6.4.0", "status": "active"},
                ],
            },
            "configured_providers": {
                p: bool(router.api_keys.get(p))
                for p in ("anthropic", "openai", "gemini", "deepseek", "openrouter")
            },
            "active_session_id": self.coordinator.session_id,
        }

        # Also pull live claude_desktop_config.json if present
        try:
            claude_cfg = Path.home() / "Library/Application Support/Claude/claude_desktop_config.json"
            if claude_cfg.exists():
                cc_data = json.loads(claude_cfg.read_text(encoding="utf-8"))
                prefs = cc_data.get("preferences", {})
                if "coworkUserFilesPath" in cc_data:
                    defaults["system"]["user_files_path"] = cc_data["coworkUserFilesPath"]
                if "remoteToolsDeviceName" in prefs:
                    defaults["system"]["device_name"] = prefs["remoteToolsDeviceName"]
                if "coworkWebSearchEnabled" in prefs:
                    defaults["capabilities"]["web_search_enabled"] = prefs["coworkWebSearchEnabled"]
                if "coworkBrowserToolsEnabled" in prefs:
                    defaults["capabilities"]["browser_tools_enabled"] = prefs["coworkBrowserToolsEnabled"]
        except Exception:
            pass

        for section, vals in stored.items():
            if isinstance(vals, dict) and section in defaults and isinstance(defaults[section], dict):
                defaults[section].update(vals)
            else:
                defaults[section] = vals

        defaults["configured_providers"] = {
            p: bool(router.api_keys.get(p))
            for p in ("anthropic", "openai", "gemini", "deepseek", "openrouter")
        }
        defaults["models"]["default_provider"] = router.default_provider
        defaults["models"]["default_model"] = router.default_model
        return defaults

    async def _handle_settings_get(self, request: web.Request) -> web.Response:
        settings = self._get_cowork_settings()
        return web.json_response(settings)

    async def _handle_settings_post(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
            cur = self._get_cowork_settings()

            all_sections = (
                "general", "account", "privacy", "billing", "capabilities",
                "memory", "reflect", "time_focus", "claude_code", "system",
                "extensions", "developer", "skills", "connectors", "plugins",
                "models", "security", "cowork"
            )
            for section in all_sections:
                if section in body and isinstance(body[section], dict):
                    if section not in cur:
                        cur[section] = {}
                    cur[section].update(body[section])

            keys = body.get("api_keys")
            provider = body.get("default_provider")
            model = body.get("default_model")

            if keys is not None or provider is not None or model is not None:
                self.coordinator.router.save_config(
                    keys=keys if keys else None,
                    default_provider=provider,
                    default_model=model,
                )

            sf = self._settings_file()
            sf.write_text(json.dumps(cur, indent=2), encoding="utf-8")
            return web.json_response({"status": "saved", "settings": cur, "active_provider": self.coordinator.router.default_provider})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def _handle_turn_cancel(self, request: web.Request) -> web.Response:
        sid = self.coordinator.session_id
        task = self._active_turn_tasks.get(sid)
        if task and not task.done():
            task.cancel()
            return web.json_response({"status": "cancelled", "message": "Turn stopped by user."})
        return web.json_response({"status": "idle", "message": "No active turn was running."})

    async def _handle_login(self, request: web.Request) -> web.Response:
        import time

        token = request.query.get("token")
        if not token or token not in self._login_tokens:
            return web.Response(text="Invalid or expired login link.", status=403)

        expires_at = self._login_tokens.pop(token)
        if time.time() > expires_at:
            return web.Response(text="Login link has expired.", status=403)

        return web.json_response({
            "status": "authenticated",
            "session_id": self.coordinator.session_id,
            "workspace": str(self.coordinator.workspace),
            "message": "Authenticated successfully with single-use token.",
        })

    async def _handle_events(self, request: web.Request) -> web.StreamResponse:
        """Server-Sent Events (SSE) stream for live agent state updates."""
        response = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )
        await response.prepare(request)

        # Emit initial session state
        initial_event = json.dumps({
            "type": "SESSION_CONNECTED",
            "session_id": self.coordinator.session_id,
            "step_count": self.coordinator.transcripts.step_counter,
        })
        await response.write(f"data: {initial_event}\n\n".encode("utf-8"))
        return response

    async def _handle_file_content(self, request: web.Request) -> web.Response:
        """Reads file content safely from workspace."""
        rel_path = request.query.get("path", "").strip()
        if not rel_path:
            return web.json_response({"error": "Missing path parameter"}, status=400)

        target = (self.coordinator.workspace / rel_path).resolve()
        ws_resolved = self.coordinator.workspace.resolve()
        try:
            target.relative_to(ws_resolved)
        except ValueError:
            return web.json_response({"error": "Access denied: Path outside workspace"}, status=403)

        if not target.exists() or not target.is_file():
            return web.json_response({"error": f"File '{rel_path}' not found"}, status=404)

        try:
            content = target.read_text(encoding="utf-8", errors="replace")
            lines = content.splitlines()
            return web.json_response({
                "path": rel_path,
                "name": target.name,
                "ext": target.suffix,
                "lines_count": len(lines),
                "size": target.stat().st_size,
                "content": content,
            })
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_file_save(self, request: web.Request) -> web.Response:
        """Saves file content to workspace atomically."""
        try:
            body = await request.json()
            rel_path = body.get("path", "").strip()
            content = body.get("content", "")
            if not rel_path:
                return web.json_response({"error": "Missing path parameter"}, status=400)

            target = (self.coordinator.workspace / rel_path).resolve()
            ws_resolved = self.coordinator.workspace.resolve()
            try:
                target.relative_to(ws_resolved)
            except ValueError:
                return web.json_response({"error": "Access denied: Path outside workspace"}, status=403)

            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return web.json_response({
                "status": "saved",
                "path": rel_path,
                "size": target.stat().st_size,
            })
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_diffs(self, request: web.Request) -> web.Response:
        """Returns workspace git diff for the Diff Studio view."""
        import subprocess
        try:
            res = subprocess.run(
                ["git", "diff", "HEAD"],
                cwd=str(self.coordinator.workspace),
                capture_output=True,
                text=True,
                timeout=5
            )
            raw_diff = res.stdout
            if not raw_diff:
                res_unstaged = subprocess.run(
                    ["git", "diff"],
                    cwd=str(self.coordinator.workspace),
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                raw_diff = res_unstaged.stdout

            lines = raw_diff.splitlines()
            files_changed = []
            for line in lines:
                if line.startswith("diff --git a/"):
                    parts = line.split()
                    if len(parts) >= 4:
                        files_changed.append(parts[2][2:])
            return web.json_response({
                "diff": raw_diff,
                "modified_files": list(dict.fromkeys(files_changed)),
                "has_diff": bool(raw_diff.strip()),
            })
        except Exception as e:
            return web.json_response({"diff": "", "modified_files": [], "has_diff": False, "error": str(e)})

    async def _handle_turn_stream(self, request: web.Request) -> web.StreamResponse:
        """SSE streaming endpoint for live agent character output and thinking."""
        try:
            body = await request.json()
        except Exception:
            body = {}
        prompt = body.get("prompt", "")
        model = body.get("model")
        mode = body.get("mode", "autonomous")
        media = body.get("media")

        response = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )
        await response.prepare(request)

        async def send_sse(event_type: str, data: dict):
            payload = json.dumps({"type": event_type, **data})
            await response.write(f"data: {payload}\n\n".encode("utf-8"))

        current_task = asyncio.current_task()
        self._active_turn_tasks[self.coordinator.session_id] = current_task

        try:
            await send_sse("turn_start", {"prompt": prompt, "mode": mode, "model": model})

            if mode == "architect":
                from harness.orchestration.architect_editor import ArchitectEditorLoop
                loop = ArchitectEditorLoop(
                    workspace_root=self.coordinator.workspace,
                    router=self.coordinator.router,
                )
                await send_sse("thinking_delta", {"delta": "🧠 Formulating architectural plan across codebase...\n"})
                arch_res = await loop.execute(
                    task_prompt=prompt,
                    target_files=list(self.coordinator.workspace.glob("*.py"))[:10],
                )
                await send_sse("turn_complete", {
                    "result": {
                        "output": arch_res.final_message or arch_res.plan,
                        "thinking": f"Architect Plan:\n{arch_res.plan}",
                        "tool_calls": [
                            {"name": "architect_editor", "arguments": {"iterations": len(arch_res.iterations)}, "output": f"Commit: {arch_res.commit_sha or 'complete'}", "is_error": not arch_res.success}
                        ],
                    }
                })
            else:
                res = await self.coordinator.execute_turn(prompt, model=model, media=media)
                data = res.to_dict() if hasattr(res, "to_dict") else (res if isinstance(res, dict) else {"output": str(res)})

                # Stream thinking if present
                thinking = data.get("thinking", "")
                if thinking:
                    for line in thinking.splitlines(keepends=True):
                        await send_sse("thinking_delta", {"delta": line})
                        await asyncio.sleep(0.005)

                # Stream tool calls
                for tc in data.get("tool_calls", []):
                    await send_sse("tool_call", tc)

                # Stream output text in small chunks for smooth UI animation
                text = data.get("output", "")
                chunk_size = 28
                for i in range(0, len(text), chunk_size):
                    await send_sse("text_delta", {"delta": text[i:i+chunk_size]})
                    await asyncio.sleep(0.01)

                await send_sse("turn_complete", {"result": data})
        except asyncio.CancelledError:
            try:
                await send_sse("turn_cancelled", {"message": "Execution stopped by user."})
            except Exception:
                pass
            raise
        except Exception as e:
            await send_sse("error", {"message": str(e)})
        finally:
            self._active_turn_tasks.pop(self.coordinator.session_id, None)

        return response

    async def _handle_sandbox_index(self, request: web.Request) -> web.Response:
        """Port 8001 index: Lists sandboxed preview artifacts."""
        manifest = self.coordinator.artifacts.load_manifest()
        items_html = ""
        for name, meta in manifest.items():
            link = f"/artifacts/{name}"
            items_html += f"""
            <div style="background:#1e1e24; border:1px solid #2e2e38; border-radius:8px; padding:16px; margin-bottom:12px;">
                <div style="display:flex; justify-content:space-between; align-items:center;">
                    <strong style="color:#38bdf8; font-size:14px;">{name}</strong>
                    <a href="{link}" target="_blank" style="color:#22c55e; text-decoration:none; font-weight:600; font-size:12px;">Open Preview ↗</a>
                </div>
                <div style="color:#94a3b8; font-size:12px; margin-top:6px;">{meta.get('summary', 'Generated artifact')}</div>
            </div>
            """
        if not items_html:
            items_html = "<p style='color:#64748b; font-style:italic;'>No artifacts generated in this session yet.</p>"

        page_html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Harness-Zero SOP Sandbox | Port 8001</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0e0e11; color: #f1f5f9; padding: 32px; max-width: 800px; margin: 0 auto; }}
        h1 {{ font-size: 20px; color: #f8fafc; display: flex; align-items: center; gap: 10px; }}
        .badge {{ background: rgba(34, 197, 94, 0.15); color: #4ade80; border: 1px solid rgba(34, 197, 94, 0.3); padding: 3px 8px; border-radius: 9999px; font-size: 11px; }}
        .notice {{ background: rgba(56, 189, 248, 0.08); border: 1px solid rgba(56, 189, 248, 0.2); padding: 12px 16px; border-radius: 8px; font-size: 13px; color: #cbd5e1; margin-bottom: 24px; }}
    </style>
</head>
<body>
    <h1><span>🛡️ SOP Untrusted Sandbox</span> <span class="badge">Isolated Port 8001</span></h1>
    <div class="notice">
        <strong>Browser Same-Origin Policy (SOP) Active:</strong> Scripts running in this origin cannot access Port 8000 credentials, API keys, or localStorage.
    </div>
    <div id="artifacts-list">{items_html}</div>
</body>
</html>"""
        return web.Response(text=page_html, content_type="text/html")

    async def _handle_health(self, request: web.Request) -> web.Response:
        return web.json_response({
            "status": "online",
            "workspace": str(self.coordinator.workspace),
            "session_id": self.coordinator.session_id,
            "skills_count": len(self.coordinator.skills.skills),
            "sandbox_port": self.sandbox_port,
        })

    async def _handle_sandbox_health(self, request: web.Request) -> web.Response:
        return web.json_response({"status": "sandbox_online", "isolated": True})

    async def _handle_sandbox_artifact(self, request: web.Request) -> web.Response:
        filename = request.match_info.get("filename", "")
        content = self.coordinator.artifacts.get_artifact(filename)
        if content is None:
            return web.Response(text="Artifact not found", status=404)

        if filename.endswith(".html"):
            sandboxed_html = self.coordinator.artifacts.render_sandboxed_html(content, title=filename)
            return web.Response(text=sandboxed_html, content_type="text/html")
        elif filename.endswith(".json"):
            return web.Response(text=content, content_type="application/json")
        return web.Response(text=content, content_type="text/plain")

    async def _handle_skills(self, request: web.Request) -> web.Response:
        skills_data = [
            {
                "name": s.name,
                "tier": s.tier,
                "description": s.description,
                "path": str(s.path),
            }
            for s in self.coordinator.skills.list_skills()
        ]
        return web.json_response({"skills": skills_data})

    async def _handle_tasks(self, request: web.Request) -> web.Response:
        tasks = self.coordinator.executor.list_tasks()
        return web.json_response({"tasks": tasks})

    async def _handle_rpc(self, request: web.Request) -> web.Response:
        """JSON-RPC 2.0 handler."""
        try:
            body = await request.json()
        except Exception:
            return web.json_response(
                {"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error"}, "id": None},
                status=400,
            )

        rpc_id = body.get("id")
        method = body.get("method")
        params = body.get("params", {})

        if method == "turn.submit":
            prompt = params.get("prompt", "")
            provider = params.get("provider")
            model = params.get("model")
            media = params.get("media")
            try:
                res = await self.coordinator.execute_turn(prompt, provider=provider, model=model, media=media)
                data = res.to_dict() if hasattr(res, "to_dict") else (res if isinstance(res, dict) else {"output": str(res)})
                return web.json_response({"jsonrpc": "2.0", "result": data, "id": rpc_id})
            except Exception as e:
                return web.json_response(
                    {"jsonrpc": "2.0", "error": {"code": -32603, "message": str(e)}, "id": rpc_id},
                    status=500,
                )

        elif method == "architect.submit":
            prompt = params.get("prompt", "")
            files = [Path(f) for f in params.get("files", [])]
            test_cmd = params.get("test_command")
            from harness.orchestration.architect_editor import ArchitectEditorLoop
            loop = ArchitectEditorLoop(
                workspace_root=self.coordinator.workspace,
                router=self.coordinator.router,
            )
            try:
                arch_res = await loop.execute(
                    task_prompt=prompt,
                    target_files=files or list(self.coordinator.workspace.glob("*.py")),
                    test_command=test_cmd,
                )
                return web.json_response({
                    "jsonrpc": "2.0",
                    "result": {
                        "success": arch_res.success,
                        "plan": arch_res.plan,
                        "final_message": arch_res.final_message,
                        "commit_sha": arch_res.commit_sha,
                        "iterations_count": len(arch_res.iterations),
                    },
                    "id": rpc_id,
                })
            except Exception as e:
                return web.json_response(
                    {"jsonrpc": "2.0", "error": {"code": -32603, "message": str(e)}, "id": rpc_id},
                    status=500,
                )

        elif method == "memory.search":
            q = params.get("query", "")
            limit = int(params.get("limit", 5))
            results = self.coordinator.memory.search(q, limit=limit)
            return web.json_response({
                "jsonrpc": "2.0",
                "result": [
                    {"step": r.step_index, "snippet": r.snippet, "rank": r.rank}
                    for r in results
                ],
                "id": rpc_id,
            })

        return web.json_response(
            {"jsonrpc": "2.0", "error": {"code": -32601, "message": f"Method {method} not found"}, "id": rpc_id},
            status=404,
        )

    async def _handle_media_upload(self, request: web.Request) -> web.Response:
        """Uploads media (images, files) into active session media store."""
        media_dir = self.coordinator.session_dir / "media"
        media_dir.mkdir(parents=True, exist_ok=True)
        uploaded = []

        if request.content_type.startswith("multipart/"):
            reader = await request.multipart()
            while True:
                field = await reader.next()
                if field is None:
                    break
                if field.filename:
                    safe_name = Path(field.filename).name.replace(" ", "_")
                    dest = media_dir / safe_name
                    if dest.exists():
                        safe_name = f"{uuid.uuid4().hex[:6]}_{safe_name}"
                        dest = media_dir / safe_name
                    size = 0
                    with open(dest, "wb") as f:
                        while True:
                            chunk = await field.read_chunk()
                            if not chunk:
                                break
                            size += len(chunk)
                            f.write(chunk)
                    mime_type = field.headers.get("Content-Type", "application/octet-stream")
                    uploaded.append({
                        "filename": safe_name,
                        "url": f"/media/{safe_name}",
                        "mime_type": mime_type,
                        "size": size,
                    })
        else:
            try:
                body = await request.json()
                filename = body.get("filename", f"upload_{uuid.uuid4().hex[:8]}.png")
                safe_name = Path(filename).name.replace(" ", "_")
                b64data = body.get("data", "")
                if "," in b64data:
                    b64data = b64data.split(",", 1)[1]
                raw_bytes = base64.b64decode(b64data)
                dest = media_dir / safe_name
                dest.write_bytes(raw_bytes)
                uploaded.append({
                    "filename": safe_name,
                    "url": f"/media/{safe_name}",
                    "mime_type": body.get("mime_type", "image/png"),
                    "size": len(raw_bytes),
                })
            except Exception as e:
                return web.json_response({"error": f"Failed to parse upload: {str(e)}"}, status=400)

        return web.json_response({"status": "ok", "files": uploaded})

    async def _handle_media_serve(self, request: web.Request) -> web.Response:
        """Serves media file from session media repository."""
        filename = request.match_info.get("filename", "")
        safe_name = Path(filename).name
        target = self.coordinator.session_dir / "media" / safe_name
        if not target.exists() or not target.is_file():
            # Fallback across session media subdirectories
            for s_dir in (self.coordinator.workspace / ".harness" / "sessions").glob(f"*/media/{safe_name}"):
                if s_dir.exists() and s_dir.is_file():
                    target = s_dir
                    break
        if not target.exists() or not target.is_file():
            return web.Response(text="Media not found", status=404)

        ext = target.suffix.lower()
        content_types = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".webp": "image/webp",
            ".svg": "image/svg+xml",
            ".pdf": "application/pdf",
            ".json": "application/json",
            ".txt": "text/plain",
        }
        ct = content_types.get(ext, "application/octet-stream")
        return web.Response(body=target.read_bytes(), content_type=ct)

    async def _handle_sessions_list(self, request: web.Request) -> web.Response:
        """Returns all historical and active sessions."""
        sessions = self.coordinator.list_sessions()
        return web.json_response({
            "active_session_id": self.coordinator.session_id,
            "sessions": sessions,
        })

    async def _handle_session_get(self, request: web.Request) -> web.Response:
        """Returns history transcript for a specific historical session."""
        session_id = request.match_info.get("session_id", "")
        s_dir = self.coordinator.workspace / ".harness" / "sessions" / session_id
        if not s_dir.exists():
            return web.json_response({"error": "Session not found"}, status=404)
        from harness.core.transcript import TranscriptEngine
        engine = TranscriptEngine(s_dir)
        history = engine.read_compact_history()
        return web.json_response({
            "session_id": session_id,
            "is_current": session_id == self.coordinator.session_id,
            "history": history,
        })

    async def _handle_session_new(self, request: web.Request) -> web.Response:
        """Initializes a fresh session."""
        new_sid = self.coordinator.new_session()
        return web.json_response({"status": "created", "session_id": new_sid})

    async def _handle_session_switch(self, request: web.Request) -> web.Response:
        """Switches active session to the requested session_id."""
        try:
            body = await request.json()
            sid = body.get("session_id")
            if not sid:
                return web.json_response({"error": "Missing session_id"}, status=400)
            self.coordinator.switch_session(sid)
            return web.json_response({"status": "switched", "session_id": sid})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def _handle_skill_playbook(self, request: web.Request) -> web.Response:
        """Returns the full markdown playbook of a skill."""
        name = request.match_info.get("name", "")
        playbook = self.coordinator.skills.view_skill(name)
        if not playbook:
            return web.json_response({"error": f"Skill '{name}' not found"}, status=404)
        return web.json_response({"name": name, "playbook": playbook})

    async def _handle_plugins(self, request: web.Request) -> web.Response:
        """Returns configured Model Context Protocol (MCP) servers and tools."""
        servers = getattr(self.coordinator.mcp, "_servers", {})
        tools = self.coordinator.mcp.get_tool_definitions() if hasattr(self.coordinator, "mcp") else []
        return web.json_response({
            "servers": servers,
            "tools": tools,
        })

    async def start(self) -> Tuple[web.AppRunner, web.AppRunner]:
        """Starts both Port 8000 API server and Port 8001 SOP Sandbox preview server."""
        # 1. API Runner
        api_runner = web.AppRunner(self.app)
        await api_runner.setup()
        if self.socket_path:
            api_site = web.UnixSite(api_runner, path=str(self.socket_path))
        else:
            api_site = web.TCPSite(api_runner, host=self.host, port=self.port)
        await api_site.start()

        # 2. Sandbox Preview Runner (Port 8001)
        sandbox_runner = web.AppRunner(self.sandbox_app)
        await sandbox_runner.setup()
        sandbox_site = web.TCPSite(sandbox_runner, host=self.host, port=self.sandbox_port)
        await sandbox_site.start()

        logger.info(f"Harness-Zero Core API online at http://{self.host}:{self.port}")
        logger.info(f"Harness-Zero SOP Sandbox Preview online at http://{self.host}:{self.sandbox_port}")

        return api_runner, sandbox_runner
