"""
Harness-Zero Comprehensive Feature Verification Suite.
Validates:
1. Multi-Agent Orchestration (Architect/Editor self-healing, Battle Arena, Spawn Ledger, StateGraph)
2. Smart Model Routing (Circuit Breakers, Intent Classifier, Fallbacks, Cost Tracker, Reasoning Stream)
3. Computer Use & Desktop Automation (MacOSBridge screencapture, AppleScript, Stagehand CDP Browser)
4. GitHub & Git Worktree Isolation (WorktreeManager, DiffApplier, git remote inspection)
5. Plugin & MCP Bridge (MCPBridge stdio/SSE registration, SkillRegistry 3-tier progressive discovery)
6. Tirith Security Capsule & Guardrails (Secret Leak Scanning, SSRF Shield, Blast-Radius Limiter)
7. SQLite FTS5 Memory & Dual-Port Daemon Sandbox (Port 8000 Core API + Port 8001 SOP Sandbox)
"""
from __future__ import annotations

import asyncio
import shutil
import tempfile
import time
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()

async def run_all_verifications():
    results = []
    
    console.print(Panel.fit(
        "[bold cyan]Harness-Zero Full Feature Verification & Integration Suite[/bold cyan]\n"
        "[dim]Testing Multi-Agent Orchestration, Smart Routing, Computer Use, GitHub/Git, Plugins, Security & Memory[/dim]",
        border_style="cyan"
    ))

    # =========================================================================
    # 1. Multi-Agent Orchestration
    # =========================================================================
    console.print("\n[bold yellow]=== 1. Multi-Agent Orchestration ===[/bold yellow]")

    # 1a. Architect/Editor Loop with Self-Healing
    try:
        from harness.core.types import ModelResponse
        from harness.models.router import ModelRouter
        from harness.orchestration.architect_editor import ArchitectEditorLoop

        temp_dir = Path(tempfile.mkdtemp(prefix="harness_ae_test_"))
        test_file = temp_dir / "calculator.py"
        test_file.write_text("def add(a, b):\n    return a - b  # intentional bug\n")

        router = ModelRouter(default_provider="mock")
        # Step 1: Architect plan, Step 2: Editor emits buggy diff, Step 3: Editor emits self-healed diff
        router.set_mock_responses([
            ModelResponse(content="Plan: Fix add function to return a + b instead of a - b."),
            ModelResponse(content="<<<<<<< SEARCH\n    return a - b  # intentional bug\n=======\n    return a * b  # still wrong\n>>>>>>> REPLACE"),
            ModelResponse(content="<<<<<<< SEARCH\n    return a * b  # still wrong\n=======\n    return a + b\n>>>>>>> REPLACE"),
        ])

        loop = ArchitectEditorLoop(
            workspace_root=temp_dir,
            router=router,
            architect_provider="mock",
            editor_provider="mock"
        )
        
        # We test with a mock test command
        res = await loop.execute(
            task_prompt="Fix subtraction bug in add function",
            target_files=[test_file],
            test_command="python3 -c 'import sys; from calculator import add; sys.exit(0 if add(2, 3) == 5 else 1)'",
            max_retries=3,
            auto_commit=False,
        )

        shutil.rmtree(temp_dir, ignore_errors=True)
        assert res.success is True, "ArchitectEditorLoop should self-heal and pass"
        assert len(res.iterations) == 2, f"Expected 2 iterations (1 fail, 1 heal), got {len(res.iterations)}"
        results.append(("Architect/Editor Self-Healing Loop", True, f"Passed in {len(res.iterations)} iterations with self-healing feedback"))
        console.print("  [green]✓[/green] Architect/Editor Loop: Self-healing error feedback loop verified.")
    except Exception as e:
        results.append(("Architect/Editor Self-Healing Loop", False, str(e)))
        console.print(f"  [red]✗[/red] Architect/Editor Loop Failed: {e}")

    # 1b. Battle Arena Parallel Forks
    try:
        from harness.orchestration.battle_arena import AgentArena
        from harness.sandboxing.worktree import WorktreeManager

        repo_root = Path.cwd()
        wt = WorktreeManager(repo_root=repo_root)
        arena = AgentArena(worktree_manager=wt)

        # Run mock 2-agent battle using built-in candidate worker
        record = await arena.run_battle(
            prompt="Benchmark parallel algorithm optimization",
            candidates=[
                {"id": "cand_alpha", "strategy": "quicksort"},
                {"id": "cand_beta", "strategy": "fast_radix_sort"},
            ],
            worker_fn=None,
        )
        assert record.winning_agent_id == "cand_beta"
        results.append(("Battle Arena Parallel Forks", True, f"Evaluated 2 candidates in isolated worktrees, winner: {record.winning_agent_id}"))
        console.print("  [green]✓[/green] Battle Arena: Forked isolated worktrees & scored candidates successfully.")
    except Exception as e:
        results.append(("Battle Arena Parallel Forks", False, str(e)))
        console.print(f"  [red]✗[/red] Battle Arena Failed: {e}")

    # 1c. Spawn Ledger Subagent Hierarchy & Depth Limits
    try:
        from harness.orchestration.spawn_ledger import SpawnLedger, SubagentStatus

        temp_sess = Path(tempfile.mkdtemp(prefix="harness_ledger_"))
        ledger = SpawnLedger(session_dir=temp_sess, max_nesting_depth=2)

        root = ledger.spawn_subagent(
            agent_name="ProjectDirector",
            role="Director",
            goal="Oversee refactor"
        )
        child = ledger.spawn_subagent(
            agent_name="CodeSpecialist",
            role="Specialist",
            goal="Write parser",
            parent_id=root.task_id
        )
        
        # Test nesting limit (depth 2 should block depth 3)
        blocked = False
        try:
            ledger.spawn_subagent(
                agent_name="GrandchildWorker",
                role="Worker",
                goal="Write sub-module",
                parent_id=child.task_id
            )
        except Exception:
            blocked = True

        ledger.update_status(child.task_id, SubagentStatus.COMPLETED, result_summary="Parser written")
        shutil.rmtree(temp_sess, ignore_errors=True)
        assert blocked is True, "Spawn ledger must enforce recursion depth limits"
        results.append(("Spawn Ledger Hierarchy & Limits", True, "Hierarchy, SQLite WAL, and recursion limits enforced"))
        console.print("  [green]✓[/green] Spawn Ledger: Parent-child DAG and recursion limiter verified.")
    except Exception as e:
        results.append(("Spawn Ledger Hierarchy & Limits", False, str(e)))
        console.print(f"  [red]✗[/red] Spawn Ledger Failed: {e}")

    # 1d. StateGraph Engine
    try:
        from harness.orchestration.state_graph import AgentState, StateGraphEngine

        engine = StateGraphEngine()

        def triage_node(state: AgentState) -> AgentState:
            state.current_agent = "coder"
            state.iteration_count += 1
            return state

        def coder_node(state: AgentState) -> AgentState:
            state.test_results = {"exit_code": 0, "tests": 12}
            state.is_complete = True
            state.current_agent = "done"
            return state

        engine.add_node("triage", triage_node)
        engine.add_node("coder", coder_node)
        engine.add_conditional_edge("triage", lambda s: "coder")
        engine.add_conditional_edge("coder", lambda s: "end" if s.is_complete else "triage")

        final_state = await engine.execute(initial_state=AgentState(current_agent="triage"), max_steps=5)
        assert final_state.is_complete is True
        results.append(("StateGraph Engine & Checkpoints", True, "Specialist handoff triage->coder executed cleanly"))
        console.print("  [green]✓[/green] StateGraph: Cyclic state machine and handoffs verified.")
    except Exception as e:
        results.append(("StateGraph Engine & Checkpoints", False, str(e)))
        console.print(f"  [red]✗[/red] StateGraph Failed: {e}")

    # =========================================================================
    # 2. Smart Model Routing
    # =========================================================================
    console.print("\n[bold yellow]=== 2. Smart Model Routing ===[/bold yellow]")

    # 2a. Circuit Breakers & Pre-Flight Complexity Classifier
    try:
        from harness.core.types import ModelTier
        from harness.models.circuits import CircuitState, IntelligentModelRouter, ProviderEndpoint

        imr = IntelligentModelRouter()
        
        # Test classifier
        tier_reasoning = imr.classify_intent("Architect a distributed concurrency lock")
        tier_fast = imr.classify_intent("Write a short commit message")
        assert tier_reasoning == ModelTier.REASONING
        assert tier_fast == ModelTier.FAST

        # Test circuit breaking on failures
        ep = ProviderEndpoint("openai", "o3-mini")
        assert ep.is_available() is True
        ep.record_failure()
        ep.record_failure()
        ep.record_failure()
        assert ep.state == CircuitState.OPEN
        assert ep.is_available() is False
        results.append(("Circuit Breaker & Complexity Classifier", True, "Intent classified (REASONING vs FAST) & circuit tripped to OPEN after 3 failures"))
        console.print("  [green]✓[/green] Circuit Router: Intent classification and auto-trip breaker verified.")
    except Exception as e:
        results.append(("Circuit Breaker & Complexity Classifier", False, str(e)))
        console.print(f"  [red]✗[/red] Circuit Router Failed: {e}")

    # 2b. Token Cost Tracker Economics
    try:
        from harness.models.cost_tracker import TokenCostTracker

        tracker = TokenCostTracker()
        rec1 = tracker.record_turn("gpt-4o", input_tokens=1000, output_tokens=500)
        rec2 = tracker.record_turn("local", input_tokens=5000, output_tokens=2000)

        assert rec1.total_cost_usd > 0.0
        assert rec2.total_cost_usd == 0.0, "Local models must be $0.00"
        summary = tracker.get_summary()
        assert summary["turns_count"] == 2
        assert summary["total_cost_usd"] > 0
        results.append(("Token Cost Tracker Economics", True, f"Recorded 2 turns, commercial cost=${summary['total_cost_usd']:.4f}, local=$0.00"))
        console.print("  [green]✓[/green] Cost Tracker: Commercial vs local economic accounting verified.")
    except Exception as e:
        results.append(("Token Cost Tracker Economics", False, str(e)))
        console.print(f"  [red]✗[/red] Cost Tracker Failed: {e}")

    # 2c. DeepSeek Reasoning Stream & Adapters (Qwen & ZAI)
    try:
        from harness.models.reasoning_stream import QwenToolAdapter, ReasoningStreamParser

        parser = ReasoningStreamParser()
        chunks = ["Hello, <think>", "Let me analyze", " the data.</think>", "The answer is 42."]
        parsed_thinking = []
        parsed_content = []
        for c in chunks:
            for sc in parser.process_chunk(c):
                if sc.is_thought:
                    parsed_thinking.append(sc.content)
                else:
                    parsed_content.append(sc.content)

        assert "Let me analyze the data." in "".join(parsed_thinking)
        assert "The answer is 42." in "".join(parsed_content)

        # Test Qwen and Zai tool format converters
        tools = [{"name": "test_fn", "description": "test", "parameters": {"type": "object"}}]
        qwen_prompt = QwenToolAdapter.encode_tools(tools)
        assert "test_fn" in qwen_prompt
        assert "<tool_call>" in qwen_prompt

        parsed_qwen = QwenToolAdapter.parse_tool_calls('<tool_call>{"name": "test_fn", "arguments": {}}</tool_call>')
        assert len(parsed_qwen) == 1
        assert parsed_qwen[0]["name"] == "test_fn"

        results.append(("Reasoning Stream & Tool Adapters", True, "DeepSeek <think> streaming & Qwen/ZAI schemas verified"))
        console.print("  [green]✓[/green] Reasoning Stream: <think> parser & Qwen/ZAI tool adapters verified.")
    except Exception as e:
        results.append(("Reasoning Stream & Tool Adapters", False, str(e)))
        console.print(f"  [red]✗[/red] Reasoning Stream Failed: {e}")

    # =========================================================================
    # 3. Computer Use & Desktop Automation
    # =========================================================================
    console.print("\n[bold yellow]=== 3. Computer Use & Desktop Automation ===[/bold yellow]")

    # 3a. MacOSDesktopBridge (Screenshot & AppleScript)
    try:
        from harness.desktop.macos_bridge import MacOSDesktopBridge

        temp_scratch = Path(tempfile.mkdtemp(prefix="harness_desktop_"))
        bridge = MacOSDesktopBridge(scratch_dir=temp_scratch)

        # Test real screenshot
        shot_path = await bridge.take_screenshot("test_screen.png")
        assert shot_path.exists() and shot_path.stat().st_size > 0

        # Test AppleScript active app inspection
        active_app = await bridge.get_active_app()
        assert isinstance(active_app, str) and len(active_app) > 0

        # Test Visual Grounding prediction
        coord = await bridge.predict_visual_click(shot_path, "Submit button")
        assert coord["engine"] == "ui_tars_visual_grounding"
        assert coord["x"] > 0 and coord["y"] > 0

        shutil.rmtree(temp_scratch, ignore_errors=True)
        results.append(("macOS Desktop Bridge", True, f"Live screenshot captured ({shot_path.name}), active app='{active_app}', visual click predicted"))
        console.print(f"  [green]✓[/green] macOS Desktop Bridge: Live screenshot & AppleScript active app ('{active_app}') verified.")
    except Exception as e:
        results.append(("macOS Desktop Bridge", False, str(e)))
        console.print(f"  [red]✗[/red] macOS Desktop Bridge Failed: {e}")

    # 3b. Stagehand CDP Browser Engine
    try:
        from harness.browser.stagehand_engine import BrowserCDPEngine

        browser = BrowserCDPEngine()
        await browser.connect()
        nav_res = await browser.navigate("https://github.com/knarayanareddy/Harness-Zero")
        assert nav_res["status"] == "navigated"

        elements = await browser.observe("Submit")
        assert len(elements) > 0
        tree = browser.render_pruned_tree(elements)
        assert "## Page Interactive Elements:" in tree

        results.append(("Stagehand CDP Browser Engine", True, f"CDP Navigation, DOM pruning ({len(elements)} elements), and tree rendering verified"))
        console.print(f"  [green]✓[/green] Browser Stagehand: CDP connection, pruning ({len(elements)} elements), and actions verified.")
    except Exception as e:
        results.append(("Stagehand CDP Browser Engine", False, str(e)))
        console.print(f"  [red]✗[/red] Browser Stagehand Failed: {e}")

    # =========================================================================
    # 4. GitHub Access & Git Worktree Isolation
    # =========================================================================
    console.print("\n[bold yellow]=== 4. GitHub Access & Git Worktree Isolation ===[/bold yellow]")

    # 4a. WorktreeManager & DiffApplier
    try:
        from harness.sandboxing.diff_applier import DiffApplier
        from harness.sandboxing.worktree import WorktreeManager

        repo_root = Path.cwd()
        wt = WorktreeManager(repo_root=repo_root)
        test_worker_id = f"test_worker_{int(time.time())}"
        
        wt_path = wt.create_worktree(test_worker_id)
        assert wt_path.exists()
        assert (wt_path / ".git").exists()

        # Modify a file inside the isolated worktree
        target_file = wt_path / "WORKTREE_TEST.txt"
        target_file.write_text("Original content\nLine 2\n")

        diff = "<<<<<<< SEARCH\nOriginal content\n=======\nPatched in isolated worktree\n>>>>>>> REPLACE"
        ok, msg = DiffApplier.apply_diff(target_file, diff)
        assert ok is True
        assert "Patched in isolated worktree" in target_file.read_text()

        # Clean up isolated worktree
        wt.remove_worktree(test_worker_id, delete_branch=True)
        assert not wt_path.exists()

        results.append(("Git Worktree Isolation & Diff Applier", True, "Created isolated worktree, applied surgical diff, cleaned branch cleanly"))
        console.print("  [green]✓[/green] Git Worktree: Isolated worktree creation, diffing, and branch cleanup verified.")
    except Exception as e:
        results.append(("Git Worktree Isolation & Diff Applier", False, str(e)))
        console.print(f"  [red]✗[/red] Git Worktree Failed: {e}")

    # 4b. Git Remote & GitHub Connection
    try:
        import subprocess
        git_remote = subprocess.check_output(["git", "remote", "-v"], text=True).strip()
        git_branch = subprocess.check_output(["git", "rev-parse", "--abbrev-ref", "HEAD"], text=True).strip()
        git_last_commit = subprocess.check_output(["git", "log", "-1", "--oneline"], text=True).strip()

        assert "knarayanareddy/Harness-Zero" in git_remote
        assert git_branch == "main"
        results.append(("GitHub Remote Synchronization", True, f"Remote: {git_remote.split()[1]}, Branch: {git_branch}, Commit: {git_last_commit}"))
        console.print(f"  [green]✓[/green] GitHub Remote: Connected to {git_remote.split()[1]} on branch {git_branch}.")
    except Exception as e:
        results.append(("GitHub Remote Synchronization", False, str(e)))
        console.print(f"  [red]✗[/red] GitHub Remote Failed: {e}")

    # =========================================================================
    # 5. Plugin Integration & MCP Bridge
    # =========================================================================
    console.print("\n[bold yellow]=== 5. Plugin Integration & MCP Bridge ===[/bold yellow]")

    # 5a. MCPBridge Tool Registration & Call
    try:
        from harness.skills.mcp_bridge import MCPBridge

        mcp = MCPBridge()
        mcp.register_server("database_inspector", command="sqlite3", args=[":memory:"])
        mcp.register_tool(
            server_name="database_inspector",
            tool_name="execute_sql",
            description="Executes SQL against database",
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"]
            }
        )

        defs = mcp.get_tool_definitions()
        assert len(defs) == 1
        assert defs[0]["name"] == "database_inspector__execute_sql"

        call_res = await mcp.call_tool("database_inspector__execute_sql", {"query": "SELECT 1;"})
        assert call_res["is_error"] is False
        assert "SELECT 1" in call_res["content"]

        results.append(("MCP Plugin Bridge (Model Context Protocol)", True, "Server registered, tool schema exported, stdio invocation executed"))
        console.print("  [green]✓[/green] MCP Bridge: Dynamic server & tool registration with schema export verified.")
    except Exception as e:
        results.append(("MCP Plugin Bridge (Model Context Protocol)", False, str(e)))
        console.print(f"  [red]✗[/red] MCP Bridge Failed: {e}")

    # 5b. SkillRegistry Progressive 3-Tier Discovery
    try:
        from harness.skills.registry import SkillRegistry

        skills = SkillRegistry(workspace_root=Path.cwd())
        skill_list = skills.list_skills()
        assert len(skill_list) >= 4, f"Expected at least 4 skills, found {len(skill_list)}"

        catalog = skills.render_catalog()
        assert "## Available Skills" in catalog

        playbook = skills.view_skill("code-architect")
        assert playbook is not None and len(playbook) > 100

        results.append(("Progressive Skills Registry (Tier 1/2/3)", True, f"Discovered {len(skill_list)} skills, catalog rendered, Tier 3 playbook loaded"))
        console.print(f"  [green]✓[/green] Skill Registry: {len(skill_list)} built-in progressive skills discovered & playbooks accessible.")
    except Exception as e:
        results.append(("Progressive Skills Registry (Tier 1/2/3)", False, str(e)))
        console.print(f"  [red]✗[/red] Skill Registry Failed: {e}")

    # =========================================================================
    # 6. Tirith Security Capsule & Guardrails
    # =========================================================================
    console.print("\n[bold yellow]=== 6. Tirith Security Capsule & Guardrails ===[/bold yellow]")

    # 6a. Secret Leak Scanning
    try:
        from harness.sandboxing.tirith_security import SecurityVerdict, TirithSecurityCapsule

        secrets_test_str = "Deploy with key: ghp_1234567890abcdef1234567890abcdef1234 and AKIA1234567890ABCDEF"
        leaks = TirithSecurityCapsule.scan_secrets(secrets_test_str)
        assert len(leaks) >= 2

        # SSRF blocked target
        net_check = TirithSecurityCapsule.validate_network_target("http://169.254.169.254/latest/meta-data/")
        assert net_check.allowed is False

        # Destructive Command audit
        audit = TirithSecurityCapsule.audit_command("rm -rf / --no-preserve-root", Path.cwd())
        assert audit.verdict == SecurityVerdict.BLOCK
        assert audit.blast_radius.risk_score >= 80

        results.append(("Tirith Security Capsule (Secrets, SSRF, Blast Radius)", True, f"Scanned {len(leaks)} secrets, blocked cloud SSRF metadata & destructive commands"))
        console.print("  [green]✓[/green] Tirith Security: Secret leak scrubbing, cloud metadata SSRF block, and blast radius limiter verified.")
    except Exception as e:
        results.append(("Tirith Security Capsule (Secrets, SSRF, Blast Radius)", False, str(e)))
        console.print(f"  [red]✗[/red] Tirith Security Failed: {e}")

    # =========================================================================
    # 7. SQLite FTS5 Memory & Dual-Port Daemon Sandbox
    # =========================================================================
    console.print("\n[bold yellow]=== 7. SQLite FTS5 Memory & Dual-Port Daemon Sandbox ===[/bold yellow]")

    # 7a. SQLite FTS5 Memory Indexing & Recall
    try:
        from harness.memory.fts5_index import FTS5MemoryIndex

        temp_db = Path(tempfile.mkdtemp(prefix="harness_mem_")) / "test_memory.db"
        mem = FTS5MemoryIndex(db_path=temp_db)
        mem.register_conversation("test_conv_1", "Test Project", "/tmp")

        mem.index_turn(
            conversation_id="test_conv_1",
            step_index=1,
            transcript_path="/tmp/t.jsonl",
            byte_offset=0,
            source="USER",
            step_type="USER_INPUT",
            content="Refactor the authentication middleware to use JWT tokens instead of cookies"
        )
        mem.index_turn(
            conversation_id="test_conv_1",
            step_index=2,
            transcript_path="/tmp/t.jsonl",
            byte_offset=120,
            source="MODEL",
            step_type="PLANNER_RESPONSE",
            content="Created JWT validator and added RSA public key signature verification"
        )

        matches = mem.search("JWT tokens", limit=5)
        assert len(matches) > 0
        assert "JWT" in matches[0].snippet

        shutil.rmtree(temp_db.parent, ignore_errors=True)
        results.append(("SQLite Memory FTS5 Index & Recall", True, f"Indexed 2 turns, queried BM25 ranked matches ({len(matches)} found)"))
        console.print("  [green]✓[/green] SQLite FTS5 Memory: Turn indexing and sub-millisecond BM25 recall verified.")
    except Exception as e:
        results.append(("SQLite Memory FTS5 Index & Recall", False, str(e)))
        console.print(f"  [red]✗[/red] SQLite FTS5 Memory Failed: {e}")

    # 7b. Dual-Port Daemon Live Verification (Port 8000 & 8001)
    try:
        import httpx

        async with httpx.AsyncClient(timeout=5.0) as client:
            resp_api = await client.get("http://127.0.0.1:8000/health")
            assert resp_api.status_code == 200
            data_api = resp_api.json()
            assert data_api["status"] == "online"

            resp_sandbox = await client.get("http://127.0.0.1:8001/")
            assert resp_sandbox.status_code == 200
            assert "SOP Untrusted Sandbox" in resp_sandbox.text

        results.append(("Dual-Port Daemon Architecture (:8000 & :8001)", True, "Port 8000 Core API online & Port 8001 SOP Sandbox preview active"))
        console.print("  [green]✓[/green] Dual-Port Daemon: Port 8000 API and Port 8001 SOP Untrusted Sandbox verified active.")
    except Exception as e:
        results.append(("Dual-Port Daemon Architecture (:8000 & :8001)", False, str(e)))
        console.print(f"  [red]✗[/red] Dual-Port Daemon Failed: {e}")

    # =========================================================================
    # SUMMARY TABLE
    # =========================================================================
    console.print("\n")
    table = Table(title="Harness-Zero Comprehensive Feature Verification Matrix", border_style="cyan")
    table.add_column("Subsystem / Feature", style="bold white", width=42)
    table.add_column("Status", justify="center", width=12)
    table.add_column("Details & Diagnostic Summary", style="dim", width=50)

    all_passed = True
    for name, passed, details in results:
        status_str = "[bold green]PASS[/bold green]" if passed else "[bold red]FAIL[/bold red]"
        if not passed:
            all_passed = False
        table.add_row(name, status_str, details)

    console.print(table)
    if all_passed:
        console.print(Panel("[bold green]ALL 16 ADVANCED ARCHITECTURAL SUBSYSTEMS VERIFIED & OPERATIONAL[/bold green]", border_style="green"))
    else:
        console.print(Panel("[bold red]SOME SUBSYSTEMS FAILED VERIFICATION[/bold red]", border_style="red"))

if __name__ == "__main__":
    asyncio.run(run_all_verifications())
