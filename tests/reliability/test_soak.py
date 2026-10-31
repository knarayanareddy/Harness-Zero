"""Fast negative evaluator controls for the offline T29 source soak runner."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/qualify_release.py"
MIN_TURNS = 100
SOAK_SECONDS = 7200
FIXTURE_VIEW_TEXT = "    1 | T29 offline source-soak fixture\n"
NEW_SOURCE_PATHS = ["scripts/qualify_release.py", "tests/reliability/test_soak.py"]


def load_runner():
    assert SCRIPT.is_file(), "T29 offline soak runner is missing"
    spec = importlib.util.spec_from_file_location("t29_qualify_release", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def closed_loop_fixture():
    prompts_by_session: dict[str, list[str]] = {"session-a": [], "session-b": []}
    acknowledged: list[dict[str, str]] = []
    records: dict[str, list[dict]] = {key: [] for key in prompts_by_session}
    for index in range(MIN_TURNS):
        session_id = "session-a" if index % 2 == 0 else "session-b"
        prompt = f"synthetic evaluator prompt {index}"
        prompts_by_session[session_id].append(prompt)
        acknowledged.append({"session_id": session_id, "prompt": prompt})
        records[session_id].append({"kind": "user.message", "role": "user", "content": prompt})
        if index == 0:
            records[session_id].extend([
                {"kind": "assistant.message", "tool_calls": [{"id": "tool-1", "name": "view_file"}]},
                {
                    "kind": "tool.result",
                    "tool_results": [{
                        "tool_use_id": "tool-1",
                        "is_error": False,
                        "content": [{"type": "text", "text": FIXTURE_VIEW_TEXT}],
                    }],
                },
            ])
        records[session_id].append({"kind": "turn.completed"})
    return {
        "started_monotonic": 100.0,
        "ended_monotonic": 100.0 + SOAK_SECONDS,
        "monotonic_checkpoints": [100.0, 3700.0, 7300.0],
        "acknowledged_turns": acknowledged,
        "expected_prompts": prompts_by_session,
        "records_by_session": records,
        "expected_tool_ids": ["tool-1"],
        "offline_fixture": True,
        "provider_request_count": MIN_TURNS + 1,
        "provider_responses_remaining": 0,
        "pending_jobs": [],
        "pending_interactions": [],
        "metric_samples": [
            {"elapsed_s": 0.0, "rss_bytes": 10_000, "fd_count": 7, "owned_child_count": 0},
            {"elapsed_s": float(SOAK_SECONDS), "rss_bytes": 10_000, "fd_count": 7, "owned_child_count": 0},
        ],
        "child_exit_code": 0,
        "runtime_unloaded": True,
        "process_metric_scope": "direct child processes only; descendants are not fully enumerated",
    }


def test_evaluator_accepts_closed_loop_positive_control_without_claiming_soak_execution():
    """Synthetic evaluator fixture only; never substitutes for a real two-hour run."""
    result = load_runner().evaluate_soak(closed_loop_fixture())
    assert result["passed"] is True
    assert result["status"] == "evaluator-pass"
    assert result["turn_count"] == MIN_TURNS
    assert result["elapsed_s"] == SOAK_SECONDS
    assert result["tool_call_count"] == 1
    assert result["growth_review_required"] is False


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda report: report.update(ended_monotonic=report["started_monotonic"] + SOAK_SECONDS - 1), "duration"),
        (lambda report: report.update(monotonic_checkpoints=[101.0, 99.0]), "monotonic"),
        (lambda report: report["acknowledged_turns"].pop(), "100"),
        (
            lambda report: report["records_by_session"]["session-a"].pop(0),
            "acknowledged",
        ),
        (
            lambda report: report["records_by_session"]["session-a"].pop(2),
            "tool",
        ),
        (lambda report: report["pending_jobs"].append({"job_id": "orphan", "state": "running"}), "job"),
        (lambda report: report["pending_interactions"].append({"id": "dangling"}), "interaction"),
    ],
)
def test_evaluator_rejects_realistic_negative_controls_even_when_child_exited_zero(mutate, reason):
    report = closed_loop_fixture()
    mutate(report)
    assert report["child_exit_code"] == 0
    result = load_runner().evaluate_soak(report)
    assert result["passed"] is False
    assert any(reason in error.lower() for error in result["errors"]), result


def test_evaluator_marks_observed_resource_growth_review_required_not_pass():
    report = closed_loop_fixture()
    report["metric_samples"][-1]["rss_bytes"] += 1
    result = load_runner().evaluate_soak(report)
    assert result["passed"] is False
    assert result["status"] == "review-required"
    assert result["growth_review_required"] is True
    assert any("growth" in item.lower() for item in result["review_required"])


def test_evaluator_requires_honest_direct_child_metric_scope():
    report = closed_loop_fixture()
    report["process_metric_scope"] = "complete process tree"
    result = load_runner().evaluate_soak(report)
    assert result["passed"] is False
    assert any("direct-child-only" in error for error in result["errors"])


@pytest.mark.parametrize(
    "result_block",
    [
        {"tool_use_id": "tool-1", "is_error": True,
         "content": [{"type": "text", "text": FIXTURE_VIEW_TEXT}]},
        {"tool_use_id": "tool-1", "is_error": False, "content": []},
        {"tool_use_id": "tool-1", "is_error": False,
         "content": [{"type": "text", "text": "provider claims it read the file"}]},
    ],
)
def test_evaluator_rejects_failed_or_unsubstantiated_builtin_read(result_block):
    report = closed_loop_fixture()
    report["records_by_session"]["session-a"][2]["tool_results"] = [result_block]
    result = load_runner().evaluate_soak(report)
    assert report["child_exit_code"] == 0
    assert result["passed"] is False
    assert any("view_file" in error.lower() or "tool result content" in error.lower()
               for error in result["errors"]), result


def _make_manifest(root: Path, tracked: list[str], candidate_paths: list[str] | None = None):
    paths = sorted(set(tracked) | set(candidate_paths or NEW_SOURCE_PATHS))
    entries = []
    for relative in paths:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(relative.encode("utf-8"))
        target.chmod(0o644)
        entries.append({
            "path": relative,
            "mode": oct(stat.S_IMODE(target.stat().st_mode)),
            "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        })
    module = load_runner()
    manifest = {
        "digest_format": module.DIGEST_FORMAT,
        "files": entries,
        "candidate_sha256": module.snapshot_digest(entries),
    }
    manifest_path = root.parent / "candidate.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path, manifest


def _make_git_manifest(root: Path):
    manifest_path, manifest = _make_manifest(root, ['.gitignore', 'harness/__init__.py'])
    (root / '.gitignore').write_text('archived-qualification/\nbuild/\ndist/\n__pycache__/\n', encoding='utf-8')
    for entry in manifest['files']:
        entry['sha256'] = hashlib.sha256((root / entry['path']).read_bytes()).hexdigest()
    manifest['candidate_sha256'] = load_runner().snapshot_digest(manifest['files'])
    manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
    subprocess.run(['git', 'init', '-q', str(root)], check=True, capture_output=True, timeout=10)
    return manifest_path, manifest


def test_git_inventory_preserves_ignored_noninput_archive_without_reclassifying_it_as_source(tmp_path):
    module = load_runner()
    root = tmp_path / 'repo'
    manifest_path, manifest = _make_git_manifest(root)
    archived = root / 'archived-qualification' / 'retained-failure.log'
    archived.parent.mkdir()
    archived.write_bytes(b'preserve this historical evidence unchanged\n')
    before = archived.read_bytes()
    ignore_before = (root / '.gitignore').read_bytes()

    result = module.verify_candidate_manifest(manifest_path, manifest['candidate_sha256'], root)

    assert result['candidate_sha256'] == manifest['candidate_sha256']
    assert archived.read_bytes() == before
    assert (root / '.gitignore').read_bytes() == ignore_before


@pytest.mark.parametrize('hidden_directory', ['build', 'dist', '__pycache__'])
def test_git_inventory_rejects_ignored_runtime_plugin_source_even_in_transient_named_directories(tmp_path, hidden_directory):
    module = load_runner()
    root = tmp_path / 'repo'
    manifest_path, manifest = _make_git_manifest(root)
    extra = root / 'harness' / 'plugins' / 'unexpected' / hidden_directory
    extra.mkdir(parents=True)
    (extra / 'harness_plugin.toml').write_text('unmanifested active plugin input\n', encoding='utf-8')
    (extra / 'plugin.py').write_text('UNMANIFESTED = True\n', encoding='utf-8')

    with pytest.raises(ValueError, match='unmanifested|path set|source tree'):
        module.verify_candidate_manifest(manifest_path, manifest['candidate_sha256'], root)


def test_namespace_origin_accepts_only_real_directories_with_pinned_source_children(tmp_path):
    module = load_runner()
    root = tmp_path / 'source'
    namespace = root / 'harness/plugins/tools'
    namespace.mkdir(parents=True)
    child = namespace / 'tool.py'
    child.write_text('VALUE = 1\n')
    module.validate_import_origins(
        {'harness.plugins.tools': None}, root, {'harness/plugins/tools/tool.py'},
        namespace_paths={'harness.plugins.tools': [str(namespace)]},
    )


@pytest.mark.parametrize('kind', ['external', 'symlink', 'unpinned', 'mixed', 'empty'])
def test_namespace_origin_rejects_untrusted_search_paths(tmp_path, kind):
    module = load_runner()
    root = tmp_path / 'source'
    root.mkdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    target = root / 'namespace'
    pinned = {'harness/other.py'}
    locations = [str(target)]
    if kind == 'external':
        target = outside
        locations = [str(target)]
    elif kind == 'symlink':
        target.symlink_to(outside, target_is_directory=True)
    elif kind == 'mixed':
        target.mkdir()
        (target / 'tool.py').write_text('VALUE = 1\n')
        pinned = {'harness/namespace/tool.py'}
        locations = [str(target), str(outside)]
    elif kind == 'empty':
        locations = []
    else:
        target.mkdir()
    with pytest.raises(ValueError, match='namespace|source|checkout|symlink|pin'):
        module.validate_import_origins(
            {'harness.plugins.tools': None}, root, pinned,
            namespace_paths={'harness.plugins.tools': locations},
        )


@pytest.mark.asyncio
async def test_actual_soak_bootstrap_reaches_first_real_tool_turn_without_claiming_endurance(tmp_path, monkeypatch):
    from harness.kernel.commands import Commands

    module = load_runner()
    output = tmp_path / 'startup-control'
    output.mkdir()
    (output / 'workspace').mkdir()
    (output / 'config').mkdir()
    home = tmp_path / 'startup-control-home'
    home.mkdir()
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setenv('HARNESS_IMPORT_HERMES_KEYS', '0')
    observed = {}
    original_submit = Commands.submit_turn

    class StartupControlFinished(RuntimeError):
        pass

    async def submit_one_real_turn(self, *args, **kwargs):
        result = await original_submit(self, *args, **kwargs)
        assert result.error is None and result.stop_reason == 'end_turn'
        assert result.tool_calls == 1
        history = await self.sessions_get(kwargs['session_id'])
        results = [item for record in history['records'] for item in record.get('tool_results', [])]
        assert any(item.get('tool_use_id') == 't29-read-000' and item.get('is_error') is False
                   and ''.join(block.get('text', '') for block in item.get('content', [])) == module.EXPECTED_VIEW_FILE_TEXT
                   for item in results)
        observed['registry'] = self.registry
        raise StartupControlFinished('Intentional startup-only stop after an acknowledged real tool turn')

    monkeypatch.setattr(Commands, 'submit_turn', submit_one_real_turn)
    with pytest.raises(StartupControlFinished, match='startup-only'):
        await module._run_offline_soak(ROOT, output, module._regular_source_paths(ROOT))
    assert observed and not observed['registry']._loaded
    assert module.MIN_ELAPSED_S == 7200 and module.MIN_TURNS == 100


def test_preflight_rejects_missing_manifest_and_wrong_expected_digest(tmp_path):
    module = load_runner()
    with pytest.raises((OSError, ValueError), match="manifest|No such|cannot read|not found"):
        module.verify_candidate_manifest(tmp_path / "missing.json", "a" * 64, tmp_path, [])

    manifest_path, manifest = _make_manifest(tmp_path / "repo", ["harness/__init__.py"])
    with pytest.raises(ValueError, match="expected|receipt|digest"):
        module.verify_candidate_manifest(manifest_path, "f" * 64, manifest_path.parent / "repo", ["harness/__init__.py"])


def test_preflight_rejects_manifest_that_omits_a_tracked_source_path(tmp_path):
    module = load_runner()
    root = tmp_path / "repo"
    manifest_path, _ = _make_manifest(root, ["harness/__init__.py"])
    with pytest.raises(ValueError, match="complete|path set|tracked|required"):
        module.verify_candidate_manifest(
            manifest_path,
            json.loads(manifest_path.read_text())["candidate_sha256"],
            root,
            ["harness/__init__.py", "harness/kernel/commands.py"],
        )


@pytest.mark.parametrize("version", [(3, 11, 16), (3, 12, 16), (3, 13, 0)])
def test_preflight_rejects_unpinned_python_patch(version):
    with pytest.raises(ValueError, match="3.12.15|3.11.17|Python"):
        load_runner().validate_python_version(version)


@pytest.mark.parametrize("origin", [None, "/installed/site-packages/harness/__init__.py"])
def test_preflight_rejects_missing_or_foreign_source_import_origin(tmp_path, origin):
    root = tmp_path / "checkout"
    root.mkdir()
    origins = {"harness": origin}
    with pytest.raises(ValueError, match="origin|source|checkout"):
        load_runner().validate_import_origins(origins, root)


def test_preflight_rejects_changed_candidate_file_bytes(tmp_path):
    module = load_runner()
    root = tmp_path / "repo"
    manifest_path, manifest = _make_manifest(root, ["harness/__init__.py"])
    (root / "harness/__init__.py").write_bytes(b"changed after pin")
    with pytest.raises(ValueError, match="content|changed"):
        module.verify_candidate_manifest(manifest_path, manifest["candidate_sha256"], root, ["harness/__init__.py"])


def test_preflight_rejects_a_rehashed_change_to_a_seeded_non_allowlisted_file(tmp_path):
    module = load_runner()
    root = tmp_path / "repo"
    manifest_path, manifest = _make_manifest(root, ["harness/__init__.py"])
    base_entry = dict(next(entry for entry in manifest["files"] if entry["path"] == "harness/__init__.py"))
    target = root / "harness/__init__.py"
    target.write_bytes(b"changed and rehashed")
    base = target.read_bytes()
    next(entry for entry in manifest["files"] if entry["path"] == "harness/__init__.py")["sha256"] = hashlib.sha256(base).hexdigest()
    manifest["candidate_sha256"] = module.snapshot_digest(manifest["files"])
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="seeded source changed"):
        module.verify_candidate_manifest(
            manifest_path,
            manifest["candidate_sha256"],
            root,
            ["harness/__init__.py"],
            [base_entry],
        )


def test_preflight_accepts_generic_complete_manifest_without_frozen_source_names(tmp_path):
    module = load_runner()
    root = tmp_path / "alternate-checkout"
    manifest_path, manifest = _make_manifest(
        root, [], candidate_paths=["app/__init__.py", "plugins/entry.py"]
    )
    result = module.verify_candidate_manifest(
        manifest_path, manifest["candidate_sha256"], root, []
    )
    assert result["candidate_sha256"] == manifest["candidate_sha256"]
    assert result["file_count"] == 2


def test_preflight_requires_runner_to_be_inside_selected_candidate_root(tmp_path):
    module = load_runner()
    root = tmp_path / "other-checkout"
    root.mkdir()
    with pytest.raises(ValueError, match="runner is outside"):
        module._runner_source_path(root, set())
    assert module._runner_source_path(ROOT, {"scripts/qualify_release.py"}) == "scripts/qualify_release.py"


def test_source_identity_names_source_checkout_bytes_not_installed_package_bytes():
    module = load_runner()
    package_files = {
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "harness").rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    }
    identity = module._source_identity(ROOT, package_files)
    assert identity["import_origins"]
    assert all(Path(origin).resolve().is_relative_to(ROOT) for origin in identity["import_origins"].values())
    assert identity["source_package_byte_scope"] == "source checkout files under imported harness package directory"
    assert identity["source_package_files"] == len(package_files)
    assert len(identity["source_package_byte_sha256"]) == 64
    assert "installed_package_byte_sha256" not in identity
    assert "installed_package_files" not in identity


@pytest.mark.parametrize("transient_tree", ["build/plugin.py", "dist/plugin.py"])
def test_candidate_inventory_does_not_hide_build_or_dist_inputs(tmp_path, transient_tree):
    module = load_runner()
    root = tmp_path / "checkout"
    manifest_path, manifest = _make_manifest(root, [])
    added = root / transient_tree
    added.parent.mkdir(parents=True, exist_ok=True)
    added.write_text("active plugin input", encoding="utf-8")
    with pytest.raises(ValueError, match="complete candidate path set"):
        module.verify_candidate_manifest(
            manifest_path, manifest["candidate_sha256"], root, []
        )


@pytest.mark.parametrize("mutation", ["bytes", "mode", "unmanifested"])
def test_final_source_pin_rejects_candidate_drift_after_worker_callback(tmp_path, mutation):
    module = load_runner()
    root = tmp_path / "checkout"
    manifest_path, manifest = _make_manifest(root, [])
    initial_pin = module.verify_candidate_manifest(manifest_path, manifest["candidate_sha256"], root, [])

    def worker_completion_callback():
        target = root / NEW_SOURCE_PATHS[0]
        if mutation == "bytes":
            target.write_text("changed after soak", encoding="utf-8")
        elif mutation == "mode":
            target.chmod(0o755)
        else:
            (root / "unmanifested.py").write_text("late input", encoding="utf-8")

    worker_completion_callback()
    with pytest.raises(ValueError, match="source tree differs|mode changed|content changed"):
        module.verify_final_source_pin(
            manifest_path, manifest["candidate_sha256"], root, initial_pin
        )


def _run_direct_worker(tmp_path: Path, *, execute: bool, receipt: bool, parent_pid: int | None,
                       note: bool = False, existing_result: bool = False,
                       in_root_output: bool = False
                       ) -> tuple[subprocess.CompletedProcess[str], bool]:
    module = load_runner()
    root = tmp_path / "root"
    root.mkdir(exist_ok=True)
    output = root / "evidence" if in_root_output else tmp_path / "output"
    output.mkdir(exist_ok=True)
    for name in ("workspace", "config", "home", "tmp"):
        (output / name).mkdir(exist_ok=True)
    if note:
        (output / "workspace/soak-note.txt").write_text("preexisting", encoding="utf-8")
    if existing_result:
        (output / "worker-report.json").write_text("preexisting result", encoding="utf-8")
    start_receipt = output / "start-receipt.json"
    if receipt:
        start_receipt.write_text(json.dumps({
            "receipt": "started",
            "parent_pid": parent_pid,
            "root": str(root.resolve()),
            "output": str(output.resolve()),
            "candidate_manifest": str((tmp_path / "candidate.json").resolve()),
            "candidate_sha256": "a" * 64,
        }), encoding="utf-8")
    args = [
        sys.executable, str(SCRIPT), "--worker", "--root", str(root),
        "--candidate-manifest", str(tmp_path / "candidate.json"),
        "--expected-digest", "a" * 64, "--output", str(output),
        "--start-receipt", str(start_receipt), "--parent-pid", str(parent_pid or -1),
    ]
    if execute:
        args.append("--execute")
    snapshot_root = root if in_root_output else output

    def snapshot():
        return {
            path.relative_to(snapshot_root).as_posix(): (
                stat.S_IMODE(path.stat().st_mode),
                hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None,
            )
            for path in snapshot_root.rglob("*")
        }

    before = snapshot()
    env = module._isolated_environment(
        output / "config", output / "home", output / "tmp", root
    )
    completed = subprocess.run(args, text=True, capture_output=True, check=False, env=env)
    after = snapshot()
    return completed, before == after


def test_direct_worker_without_execute_is_read_only(tmp_path):
    completed, changed = _run_direct_worker(
        tmp_path, execute=False, receipt=True, parent_pid=os.getpid()
    )
    assert completed.returncode != 0
    assert "--execute" in completed.stderr
    assert changed is True


@pytest.mark.parametrize(
    ("receipt", "parent_pid", "note", "reason"),
    [
        (False, os.getpid(), False, "start receipt"),
        (True, os.getpid() + 1, False, "parent"),
        (True, os.getpid(), True, "soak-note"),
        (True, os.getpid(), False, "worker result"),
    ],
)
def test_direct_worker_rejects_unowned_or_dirty_output_without_mutation(
    tmp_path, receipt, parent_pid, note, reason
):
    completed, changed = _run_direct_worker(
        tmp_path, execute=True, receipt=receipt, parent_pid=parent_pid,
        note=note, existing_result=reason == "worker result",
    )
    assert completed.returncode != 0
    assert reason.lower() in completed.stderr.lower()
    assert changed is True


def test_direct_worker_rejects_valid_in_root_output_before_any_source_mutation(tmp_path):
    completed, source_unchanged = _run_direct_worker(
        tmp_path, execute=True, receipt=True, parent_pid=os.getpid(), in_root_output=True
    )
    assert completed.returncode != 0
    assert source_unchanged is True
    assert "outside the source checkout" in completed.stderr.lower(), completed.stderr


def test_worker_start_receipt_accepts_owned_external_output_without_mutation(tmp_path, monkeypatch):
    module = load_runner()
    root = tmp_path / "root"
    root.mkdir()
    output = tmp_path / "owned-output"
    output.mkdir()
    for name in ("workspace", "home", "config", "tmp"):
        (output / name).mkdir()
    parent_pid = os.getppid()
    manifest = tmp_path / "candidate.json"
    digest = "a" * 64
    receipt_path = output / "start-receipt.json"
    receipt = {
        "receipt": "started",
        "parent_pid": parent_pid,
        "root": str(root.resolve()),
        "output": str(output.resolve()),
        "candidate_manifest": str(manifest.resolve()),
        "candidate_sha256": digest,
    }
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    environment = module._isolated_environment(
        output / "config", output / "home", output / "tmp", root
    )
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    before = {
        path.relative_to(output).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in output.rglob("*") if path.is_file()
    }
    assert module._worker_start_receipt(
        root, manifest, digest, output, receipt_path, parent_pid
    ) == receipt
    after = {
        path.relative_to(output).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in output.rglob("*") if path.is_file()
    }
    assert after == before


def test_soak_metric_and_worker_diagnostic_bounds_do_not_lower_operation_minimum():
    module = load_runner()
    assert module.MIN_ELAPSED_S == SOAK_SECONDS
    assert module.WORKER_DIAGNOSTIC_DEADLINE_S >= SOAK_SECONDS


def test_worker_environment_keeps_venv_and_disables_host_credentials():
    module = load_runner()
    env = module._isolated_environment(Path("/fresh/config"), Path("/fresh/home"),
                                       Path("/fresh/tmp"), ROOT)
    assert env["PATH"].split(":", 1)[0] == str(Path(sys.executable).absolute().parent)
    assert env["HARNESS_IMPORT_HERMES_KEYS"] == "0"
    assert "OPENAI_API_KEY" not in env
    assert "ANTHROPIC_API_KEY" not in env


def test_process_metric_subprocess_has_a_visible_timeout(monkeypatch):
    module = load_runner()
    calls = []

    def timed_out(command, **kwargs):
        calls.append((command, kwargs))
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(module.subprocess, "run", timed_out)
    with pytest.raises(RuntimeError, match="ps timed out"):
        module._mac_process_metrics(os.getpid())
    assert calls
    assert calls[0][1]["timeout"] == module.PROCESS_SAMPLE_TIMEOUT_S
