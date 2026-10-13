"""
Harness-Zero Red Teaming & Adversarial Penetration Test Suite.
Simulates real-world attack vectors, permission escapes, shell injection,
SSRF, memory floods, and credential extraction to verify embedded guardrails.
"""
import asyncio
from pathlib import Path
import tempfile
import time
import pytest
from aiohttp import web

from harness.core.coordinator import AgentCoordinator
from harness.core.daemon import HarnessDaemon
from harness.core.transcript import TranscriptEngine
from harness.core.types import StepSource, StepType
from harness.memory.fts5_index import FTS5MemoryIndex
from harness.models.router import ModelRouter
from harness.sandboxing.executor import CommandExecutor
from harness.sandboxing.guardrails import RiskLevel, SecurityPolicy
from harness.sandboxing.tirith_security import SecurityVerdict, TirithSecurityCapsule
from harness.skills.registry import SkillRegistry


# =============================================================================
# 1. Directory Traversal & Credential Leakage Attacks
# =============================================================================

@pytest.mark.asyncio
async def test_red_team_path_traversal_sibling_prefix():
    """Attacks prefix-matching flaw (e.g. /workspace-evil vs /workspace)."""
    with tempfile.TemporaryDirectory() as base_tmp:
        base = Path(base_tmp)
        legit_ws = base / "project"
        legit_ws.mkdir()
        evil_ws = base / "project-evil"
        evil_ws.mkdir()
        (evil_ws / "passwords.txt").write_text("SUPER_SECRET_EVIL_DATA")

        coord = AgentCoordinator(workspace=legit_ws)
        daemon = HarnessDaemon(coordinator=coord)

        # Attempt to read sibling directory via GET /file/content?path=../project-evil/passwords.txt
        req = web.Request
        class DummyRequest:
            query = {"path": "../project-evil/passwords.txt"}

        resp = await daemon._handle_file_content(DummyRequest())
        assert resp.status == 403, "Sibling directory traversal must be blocked with HTTP 403"


@pytest.mark.asyncio
async def test_red_team_path_traversal_etc_passwd():
    """Attacks system files via view_file tool."""
    with tempfile.TemporaryDirectory() as ws_tmp:
        coord = AgentCoordinator(workspace=Path(ws_tmp), router=ModelRouter(default_provider="mock"))
        
        # Test directly via _dispatch_tool
        from harness.core.types import ToolCall
        tc = ToolCall(id="call_1", name="view_file", arguments={"AbsolutePath": "/etc/passwd"})
        res_tool = await coord._dispatch_tool(tc)
        assert res_tool.is_error is True
        assert "Security Violation" in res_tool.output
        assert "blocked" in res_tool.output.lower()

        # Attack 2: Relative traversal path
        tc2 = ToolCall(id="call_2", name="view_file", arguments={"AbsolutePath": "../../../../../etc/passwd"})
        res_tool2 = await coord._dispatch_tool(tc2)
        assert res_tool2.is_error is True
        assert "Security Violation" in res_tool2.output or "does not exist" in res_tool2.output


@pytest.mark.asyncio
async def test_red_team_protected_ssh_key_read():
    """Attempts to exfiltrate SSH private key via view_file."""
    with tempfile.TemporaryDirectory() as ws_tmp:
        coord = AgentCoordinator(workspace=Path(ws_tmp), router=ModelRouter(default_provider="mock"))
        from harness.core.types import ToolCall
        ssh_path = str(Path.home() / ".ssh" / "id_rsa")
        tc = ToolCall(id="call_ssh", name="view_file", arguments={"AbsolutePath": ssh_path})
        res_tool = await coord._dispatch_tool(tc)
        assert res_tool.is_error is True
        assert "Security Violation" in res_tool.output


# =============================================================================
# 2. Command Safety, SSRF & Tirith Blast-Radius Attacks
# =============================================================================

def test_red_team_cloud_metadata_ssrf_attacks():
    """Attempts SSRF against AWS/GCP/Alibaba link-local metadata endpoints."""
    # AWS/GCP metadata
    audit_aws = TirithSecurityCapsule.audit_command("curl -s http://169.254.169.254/latest/meta-data/iam/security-credentials/", Path.cwd())
    assert audit_aws.verdict == SecurityVerdict.BLOCK
    assert any("SSRF" in r for r in audit_aws.reasons)

    # Google internal metadata
    audit_gcp = TirithSecurityCapsule.audit_command("curl http://metadata.google.internal/computeMetadata/v1/", Path.cwd())
    assert audit_gcp.verdict == SecurityVerdict.BLOCK

    # Alibaba metadata
    audit_ali = TirithSecurityCapsule.audit_command("curl http://100.100.100.200/latest/meta-data/", Path.cwd())
    assert audit_ali.verdict == SecurityVerdict.BLOCK


def test_red_team_destructive_command_blocking():
    """Attempts destructive disk wipes, root privilege escalation, and force operations."""
    destructive_commands = [
        "rm -rf / --no-preserve-root",
        "rm -rf *",
        "git reset --hard HEAD~10",
        "git push origin main --force",
        "sudo apt-get install malware",
        "mkfs.ext4 /dev/sda1",
        "dd if=/dev/zero of=/dev/sda",
        "pkill -9 -u root",
    ]

    for cmd in destructive_commands:
        eval_res = SecurityPolicy.evaluate_command_risk(cmd, Path.cwd())
        assert eval_res.level == RiskLevel.DESTRUCTIVE, f"Failed to block destructive command: {cmd}"
        assert eval_res.requires_approval is True


def test_red_team_credential_scraping_in_commands():
    """Detects leaked tokens embedded in command arguments."""
    leaked_cmd = "git clone https://ghp_abcdefghijklmnopqrstuvwxyz0123456789@github.com/org/repo"
    audit = TirithSecurityCapsule.audit_command(leaked_cmd, Path.cwd())
    assert audit.verdict == SecurityVerdict.BLOCK
    assert "GitHub Personal Access Token" in audit.leaked_secrets[0]


# =============================================================================
# 3. Terminal Execution & Buffer Flood Attacks
# =============================================================================

@pytest.mark.asyncio
async def test_red_team_executor_infinite_output_buffer_cap():
    """Ensures runaway output generator doesn't cause out-of-memory denial of service."""
    executor = CommandExecutor()
    with tempfile.TemporaryDirectory() as tmp_dir:
        # Generate 20,000 rapid output lines
        cmd = "python3 -c 'for i in range(25000): print(f\"FLOOD_LINE_{i}\")'"
        res = await executor.run(cmd, Path(tmp_dir), wait_ms_before_async=5000)
        assert res["status"] == "completed"
        # Verify that output was capped and did not blow up memory
        assert "[OUTPUT TRUNCATED: Stream buffer limit reached]" in res["stdout"]


@pytest.mark.asyncio
async def test_red_team_process_group_clean_kill():
    """Ensures child processes in subshell are cleanly killed without orphaning."""
    executor = CommandExecutor()
    with tempfile.TemporaryDirectory() as tmp_dir:
        # Run sleep in background
        res = await executor.run("sleep 60", Path(tmp_dir), wait_ms_before_async=200)
        assert res["is_background"] is True
        task_id = res["task_id"]

        # Kill the task
        killed = await executor.kill_task(task_id)
        assert killed is True
        task = executor.get_task(task_id)
        assert task.status == "killed"


# =============================================================================
# 4. Authentication & Single-Use Remote Token Attacks
# =============================================================================

@pytest.mark.asyncio
async def test_red_team_login_token_replay_attack():
    """Replays a single-use login token to verify it cannot be used twice."""
    with tempfile.TemporaryDirectory() as ws_tmp:
        coord = AgentCoordinator(workspace=Path(ws_tmp))
        daemon = HarnessDaemon(coordinator=coord)

        login_url = daemon.generate_login_url()
        token = login_url.split("token=")[1]

        class DummyRequest:
            def __init__(self, t):
                self.query = {"token": t}

        # Attempt 1: First login succeeds
        resp1 = await daemon._handle_login(DummyRequest(token))
        assert resp1.status == 200

        # Attempt 2: Replay attack fails with 403 Forbidden
        resp2 = await daemon._handle_login(DummyRequest(token))
        assert resp2.status == 403


@pytest.mark.asyncio
async def test_red_team_expired_token_rejected():
    """Ensures expired tokens are immediately rejected."""
    with tempfile.TemporaryDirectory() as ws_tmp:
        coord = AgentCoordinator(workspace=Path(ws_tmp))
        daemon = HarnessDaemon(coordinator=coord)

        token = "expired_token_123"
        # Artificially set expiration in the past
        daemon._login_tokens[token] = time.time() - 10.0

        class DummyRequest:
            query = {"token": token}

        resp = await daemon._handle_login(DummyRequest())
        assert resp.status == 403


# =============================================================================
# 5. SQLite FTS5 Injection Attacks
# =============================================================================

def test_red_team_fts5_syntax_injection():
    """Attempts SQLite FTS5 syntax injection with wildcards, quotes, and boolean operators."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "test_fts5.db"
        mem = FTS5MemoryIndex(db_path=db_path)
        mem.register_conversation("c1", "Title", "/tmp")
        mem.index_turn("c1", 1, Path("/tmp/t.jsonl"), 0, "USER", "USER_INPUT", "Normal user query")

        # Malicious FTS5 syntax attempts that would crash raw queries
        injections = [
            "\"\"\" OR 1=1 --",
            "NEAR(foo, bar, 10) AND NOT (",
            "* * * * *",
            "'; DROP TABLE conversations_fts; --",
            "content: OR (MATCH) NOT",
        ]

        for inj in injections:
            matches = mem.search(inj)
            # Must return list (empty or matching) without raising sqlite3.OperationalError
            assert isinstance(matches, list)


# =============================================================================
# 6. Skill Registry Fault Injection
# =============================================================================

def test_red_team_malformed_skill_recovery():
    """Ensures broken YAML or corrupted skill files do not crash the registry."""
    with tempfile.TemporaryDirectory() as ws_tmp:
        skills_dir = Path(ws_tmp) / "skills" / "broken-skill"
        skills_dir.mkdir(parents=True)
        (skills_dir / "SKILL.md").write_text("---\nthis is : not: valid: yaml: [[\n---\nCorrupted playbook")

        registry = SkillRegistry(workspace_root=Path(ws_tmp))
        # Broken skill should either fallback gracefully or not crash
        skills = registry.list_skills()
        assert isinstance(skills, list)
        catalog = registry.render_catalog()
        assert isinstance(catalog, str)


# =============================================================================
# 7. Transcript Corrupted Byte Offset Resilience
# =============================================================================

def test_red_team_transcript_corrupted_offset_recovery():
    """Ensures transcript engine recovers gracefully from corrupted byte positions."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        te = TranscriptEngine(session_dir=Path(tmp_dir))
        step, off = te.append_step(StepSource.USER, StepType.USER_INPUT, content="Hello")
        
        # Ask for non-existent or out-of-range step
        bad_step = te.get_step(999)
        assert bad_step is None

        # Corrupt full file with invalid UTF-8 bytes at offset
        with open(te.full_file, "r+b") as f:
            f.seek(off)
            f.write(b"\xff\xfe\x00\x00")

        # Must not crash, should return None or handled record
        res = te.get_step(step)
        assert res is None or isinstance(res, dict)
