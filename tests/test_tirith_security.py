import pytest
from pathlib import Path
from harness.sandboxing.tirith_security import TirithSecurityCapsule, SecurityVerdict

def test_tirith_secret_detection():
    # AWS Key
    aws_secret = "AKIAIOSFODNN7EXAMPLE"
    findings = TirithSecurityCapsule.scan_secrets(f"export AWS_ACCESS_KEY_ID={aws_secret}")
    assert len(findings) > 0
    assert any("AWS Access Key" in f for f in findings)

    # GitHub PAT
    gh_secret = "ghp_1234567890abcdefghijklmnopqrstuvwxyz"
    findings_gh = TirithSecurityCapsule.scan_secrets(f"GITHUB_TOKEN={gh_secret}")
    assert len(findings_gh) > 0

    # Clean text
    clean_findings = TirithSecurityCapsule.scan_secrets("echo 'Hello World'")
    assert len(clean_findings) == 0

def test_tirith_ssrf_metadata_shield():
    # AWS IMDSv1
    res = TirithSecurityCapsule.validate_network_target("http://169.254.169.254/latest/meta-data/")
    assert not res.allowed
    assert "Blocked cloud metadata" in res.reason

    # GCP Metadata
    res_gcp = TirithSecurityCapsule.validate_network_target("http://metadata.google.internal/computeMetadata/v1/")
    assert not res_gcp.allowed

    # Allowed external URL
    res_clean = TirithSecurityCapsule.validate_network_target("https://api.github.com/repos")
    assert res_clean.allowed

def test_tirith_blast_radius():
    # Low risk
    audit_low = TirithSecurityCapsule.audit_command("ls -la", Path("/tmp"))
    assert audit_low.verdict == SecurityVerdict.ALLOW
    assert audit_low.blast_radius.risk_score < 50

    # High risk
    audit_high = TirithSecurityCapsule.audit_command("rm -rf /", Path("/tmp"))
    assert audit_high.verdict == SecurityVerdict.BLOCK
    assert audit_high.blast_radius.risk_score >= 80
    assert audit_high.blast_radius.is_recursive is True
