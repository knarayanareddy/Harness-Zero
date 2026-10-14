"""
Repo Map Engine: Whole-Codebase AST Symbol Mapping.
Extracts classes, functions, and key definitions across files,
ranking them to fit within a compact token budget (Aider pattern).
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class SymbolDef:
    file_path: Path
    kind: str  # class, function, method
    name: str
    line: int
    signature: str = ""


class RepoMap:
    def __init__(self, workspace_root: Path, max_tokens: int = 1024):
        self.workspace_root = Path(workspace_root)
        self.max_tokens = max_tokens
        self.ignore_dirs = {
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
        }

    def generate_map(self) -> str:
        """Generates a dense markdown summary of codebase symbols."""
        if not self.workspace_root.exists():
            return "(Empty workspace)"

        symbols_by_file: dict[str, list[str]] = {}

        for file_path in sorted(self.workspace_root.rglob("*")):
            if not file_path.is_file():
                continue
            if any(part in self.ignore_dirs for part in file_path.parts):
                continue

            rel_path = str(file_path.relative_to(self.workspace_root))

            # Python files via AST
            if file_path.suffix == ".py":
                symbols = self._extract_python_symbols(file_path)
                if symbols:
                    symbols_by_file[rel_path] = symbols
            # JS/TS/Go/Rust via regex
            elif file_path.suffix in (".js", ".ts", ".tsx", ".jsx", ".go", ".rs"):
                symbols = self._extract_generic_symbols(file_path)
                if symbols:
                    symbols_by_file[rel_path] = symbols

        if not symbols_by_file:
            return "(No source code symbols discovered)"

        # Pack into compact outline within token budget
        output_lines: list[str] = ["## Codebase Map:"]
        char_budget = self.max_tokens * 4  # ~4 chars per token heuristic
        total_chars = 0

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
                        if isinstance(sub, ast.FunctionDef) or isinstance(
                            sub, ast.AsyncFunctionDef
                        ):
                            if not sub.name.startswith("__") or sub.name == "__init__":
                                args = [a.arg for a in sub.args.args if a.arg != "self"]
                                symbols.append(f"  def {sub.name}({', '.join(args)})")
                elif isinstance(node, ast.FunctionDef) or isinstance(
                    node, ast.AsyncFunctionDef
                ):
                    args = [a.arg for a in node.args.args]
                    symbols.append(f"def {node.name}({', '.join(args)})")
        except Exception:
            pass
        return symbols

    def _extract_generic_symbols(self, file_path: Path) -> list[str]:
        symbols: list[str] = []
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
            # Match function declarations, class definitions, structs
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
