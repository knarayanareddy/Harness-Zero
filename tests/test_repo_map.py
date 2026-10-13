from pathlib import Path
from harness.memory.repo_map import RepoMap


def test_repo_map_symbol_extraction(tmp_path: Path):
    src_file = tmp_path / "service.py"
    src_file.write_text(
        """class PaymentService:
    def process_payment(amount: float, currency: str):
        pass

    def refund(tx_id: str):
        pass

def calculate_tax(rate: float):
    pass
""",
        encoding="utf-8",
    )

    repo_map = RepoMap(workspace_root=tmp_path)
    output = repo_map.generate_map()

    assert "service.py:" in output
    assert "class PaymentService" in output
    assert "def process_payment" in output
    assert "def calculate_tax" in output
