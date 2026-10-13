from pathlib import Path
import pytest
from harness.sandboxing.ripgrep import RipgrepSearcher


@pytest.mark.asyncio
async def test_ripgrep_codebase_search(tmp_path: Path):
    sub = tmp_path / "src"
    sub.mkdir()
    f1 = sub / "auth.py"
    f1.write_text("def authenticate_jwt_token(token: str):\n    return True\n", encoding="utf-8")
    f2 = sub / "db.py"
    f2.write_text("def connect_database():\n    pass\n", encoding="utf-8")

    searcher = RipgrepSearcher(workspace_root=tmp_path)
    matches = await searcher.search("jwt_token")

    assert len(matches) == 1
    assert "auth.py" in matches[0].file_path
    assert "authenticate_jwt_token" in matches[0].line_content
