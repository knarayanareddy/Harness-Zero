from pathlib import Path
import pytest
from harness.sandboxing.guardrails import RiskLevel, SecurityPolicy


def test_command_risk_classification(tmp_path: Path):
    safe_cmd = "pytest tests/ -v"
    eval_safe = SecurityPolicy.evaluate_command_risk(safe_cmd, tmp_path)
    assert eval_safe.level == RiskLevel.SAFE
    assert eval_safe.requires_approval is False

    destructive_cmd = "rm -rf /tmp/test"
    eval_dest = SecurityPolicy.evaluate_command_risk(destructive_cmd, tmp_path)
    assert eval_dest.level == RiskLevel.DESTRUCTIVE
    assert eval_dest.requires_approval is True

    sudo_cmd = "sudo systemctl restart nginx"
    eval_sudo = SecurityPolicy.evaluate_command_risk(sudo_cmd, tmp_path)
    assert eval_sudo.level == RiskLevel.DESTRUCTIVE
    assert eval_sudo.requires_approval is True


def test_file_write_risk_classification(tmp_path: Path):
    ws = tmp_path / "workspace"
    ws.mkdir()

    safe_file = ws / "src" / "index.ts"
    safe_eval = SecurityPolicy.evaluate_file_write_risk(safe_file, ws)
    assert safe_eval.level == RiskLevel.SAFE
    assert safe_eval.requires_approval is False

    external_file = tmp_path / "outside.txt"
    ext_eval = SecurityPolicy.evaluate_file_write_risk(external_file, ws)
    assert ext_eval.level == RiskLevel.ELEVATED
    assert ext_eval.requires_approval is True

    protected_file = Path.home() / ".ssh" / "id_rsa"
    prot_eval = SecurityPolicy.evaluate_file_write_risk(protected_file, ws)
    assert prot_eval.level == RiskLevel.DESTRUCTIVE
    assert prot_eval.requires_approval is True
