"""Fresh-process headless imports must not initialize unused console rendering."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BLOCK_CONSOLE = """
import sys
class RejectConsoleRendering:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'rich.markdown' or fullname == 'harness.frontends.cli' or fullname.startswith('harness.frontends.cli.'):
            raise RuntimeError('Unused console rendering imported by headless CLI: ' + fullname)
sys.meta_path.insert(0, RejectConsoleRendering())
"""


def child_environment(tmp_path):
    env = {key: value for key, value in os.environ.items() if key != 'HARNESS_MOCK_SCRIPT' and not any(
        marker in key.upper() for marker in ('API_KEY', 'TOKEN', 'SECRET', 'PASSWORD', 'CREDENTIAL', 'PRIVATE_KEY', 'ACCESS_KEY')
    )}
    config, home = tmp_path / 'config', tmp_path / 'home'
    config.mkdir(exist_ok=True)
    home.mkdir(exist_ok=True)
    env.update(HARNESS_CONFIG_DIR=str(config), HOME=str(home), PYTHONPATH=str(ROOT))
    return env


def test_child_environment_discards_user_selected_mock_script(tmp_path, monkeypatch):
    monkeypatch.setenv('HARNESS_MOCK_SCRIPT', str(tmp_path / 'user-selected/mock.json'))
    env = child_environment(tmp_path)
    assert 'HARNESS_MOCK_SCRIPT' not in env
    assert os.environ['HARNESS_MOCK_SCRIPT'].endswith('user-selected/mock.json')


@pytest.mark.parametrize('operation', ['import', 'mcp'])
def test_headless_cli_never_imports_interactive_rendering(tmp_path, operation):
    script = BLOCK_CONSOLE + '\nimport harness.cli\n'
    if operation == 'mcp':
        script += f'harness.cli.main(["mcp", "--workspace", {str(tmp_path / "workspace")!r}], standalone_mode=False)\n'
    script += "assert 'rich.markdown' not in sys.modules and 'harness.frontends.cli' not in sys.modules\n"
    # This diagnostic ceiling is not the separate strict ACP response deadline.
    result = subprocess.run(
        [sys.executable, '-c', script], input='{"jsonrpc":"2.0","id":1,"method":"initialize"}\n',
        cwd=tmp_path, env=child_environment(tmp_path), capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    if operation == 'mcp':
        responses = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        assert len(responses) == 1 and responses[0]['id'] == 1
        assert responses[0]['result']['serverInfo']['name'] == 'harness-zero-mcp-server'


def test_one_shot_cli_still_loads_console_rendering_on_real_use(tmp_path):
    mock_script = tmp_path / 'mock.json'
    mock_script.write_text(json.dumps([{'text': 'CLI_RENDER_LAZY_OK', 'stop_reason': 'end_turn'}]))
    script = (
        'import sys\nfrom harness.cli import main\n'
        f'main(["run", "offline rendering control", "--workspace", {str(tmp_path / "workspace")!r}, '
        f'"--mock-script", {str(mock_script)!r}, "--provider", "mock", "--model", "mock-v1"], standalone_mode=False)\n'
        "assert 'rich.markdown' in sys.modules and 'harness.frontends.cli' in sys.modules\n"
    )
    result = subprocess.run(
        [sys.executable, '-c', script], cwd=tmp_path, env=child_environment(tmp_path),
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'CLI_RENDER_LAZY_OK' in result.stdout
