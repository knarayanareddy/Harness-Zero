"""
Whole-codebase AST symbol mapping engine (§PLG-20).
Extracts classes, functions, and key definitions into a dense, token-budgeted outline.
Absorbs LOOP-07 (repo map) and D13.
"""
from __future__ import annotations

import ast
import hashlib
import re
from pathlib import Path

IGNORE_DIRS = frozenset({
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    ".pytest_cache",
    ".harness",
    "brain",
})


def calculate_workspace_fingerprint(workspace: Path, max_files: int = 500) -> str:
    """Calculates a quick fingerprint of workspace files and modification times."""
    hasher = hashlib.sha256()
    count = 0
    for p in sorted(workspace.rglob("*")):
        if p.is_file() and not any(part in IGNORE_DIRS for part in p.parts):
            hasher.update(str(p.relative_to(workspace)).encode("utf-8"))
            try:
                hasher.update(str(p.stat().st_mtime_ns).encode("utf-8"))
            except OSError:
                pass
            count += 1
            if count >= max_files:
                break
    return hasher.hexdigest()[:16]


class RepoMapGenerator:
    """Generates a dense markdown summary of codebase symbols within a token budget."""

    def __init__(self, max_tokens: int = 1024, max_files_threshold: int = 2000) -> None:
        self.max_tokens = max_tokens
        self.max_files_threshold = max_files_threshold

    def generate(self, workspace: Path, force: bool = False) -> str:
        if not workspace.exists():
            return "(Empty workspace)"

        # Check total file count
        all_source_files: list[Path] = []
        for file_path in sorted(workspace.rglob("*")):
            if file_path.is_file() and not any(part in IGNORE_DIRS for part in file_path.parts):
                all_source_files.append(file_path)

        if len(all_source_files) > self.max_files_threshold and not force:
            return (
                f"(Repo map skipped: codebase has {len(all_source_files)} files, "
                f"exceeding threshold of {self.max_files_threshold}. "
                "Set context.repo_map.force=true to override)"
            )

        symbols_by_file: dict[str, list[str]] = {}

        for file_path in all_source_files:
            rel_path = str(file_path.relative_to(workspace))

            # Python files via AST
            if file_path.suffix == ".py":
                symbols = self._extract_python_symbols(file_path)
                if symbols:
                    symbols_by_file[rel_path] = symbols
            # Other source languages via regex
            elif file_path.suffix in (".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".c", ".cpp"):
                symbols = self._extract_generic_symbols(file_path)
                if symbols:
                    symbols_by_file[rel_path] = symbols

        if not symbols_by_file:
            return "(No source code symbols discovered)"

        # Pack into compact outline within token budget
        output_lines: list[str] = ["## Codebase Map:"]
        char_budget = self.max_tokens * 4  # ~4 chars per token heuristic
        total_chars = len(output_lines[0])

        for rel_path, syms in symbols_by_file.items():
            file_header = f"{rel_path}:"
            if total_chars + len(file_header) > char_budget:
                output_lines.append("... [remaining files omitted for token budget]")
                break

            output_lines.append(file_header)
            total_chars += len(file_header)

            for sym in syms:
                sym_line = f"  {sym}"
                if total_chars + len(sym_line) > char_budget:
                    output_lines.append("  ... [truncated]")
                    break
                output_lines.append(sym_line)
                total_chars += len(sym_line)

        return "\n".join(output_lines)

    def _extract_python_symbols(self, file_path: Path) -> list[str]:
        symbols: list[str] = []
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(content, filename=str(file_path))
            for node in tree.body:
                if isinstance(node, ast.ClassDef):
                    symbols.append(f"class {node.name}")
                    for sub in node.body:
                        if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            if not sub.name.startswith("__") or sub.name == "__init__":
                                args = [a.arg for a in sub.args.args if a.arg != "self"]
                                symbols.append(f"  def {sub.name}({', '.join(args)})")
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    args = [a.arg for a in node.args.args]
                    symbols.append(f"def {node.name}({', '.join(args)})")
        except Exception:
            pass
        return symbols

    def _extract_generic_symbols(self, file_path: Path) -> list[str]:
        symbols: list[str] = []
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
            pattern = re.compile(
                r"^\s*(export\s+)?(async\s+)?(function|class|interface|type|struct|enum)\s+([A-Za-z0-9_]+)",
                re.MULTILINE,
            )
            for match in pattern.finditer(content):
                kind = match.group(3)
                name = match.group(4)
                symbols.append(f"{kind} {name}")
        except Exception:
            pass
        return symbols
