"""
Tiered Transcript Engine: Compact transcript.jsonl with pointer truncation,
full transcript_full.jsonl archive, and trajectory compaction.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from harness.core.types import MediaRef, StepSource, StepType, ToolCall, TranscriptRecord


class TranscriptEngine:
    def __init__(self, session_dir: Path, truncation_threshold: int = 1500):
        self.session_dir = Path(session_dir)
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.compact_file = self.session_dir / "transcript.jsonl"
        self.full_file = self.session_dir / "transcript_full.jsonl"
        self.truncation_threshold = truncation_threshold
        self.step_counter = 0
        self._full_offsets: List[int] = []

        # Initialize existing step count and byte offsets if files exist
        self._index_existing_logs()

    def _index_existing_logs(self) -> None:
        self._full_offsets.clear()
        if self.full_file.exists():
            with open(self.full_file, "rb") as f:
                offset = 0
                line = f.readline()
                while line:
                    self._full_offsets.append(offset)
                    offset = f.tell()
                    line = f.readline()
            self.step_counter = len(self._full_offsets)
        elif self.compact_file.exists():
            with open(self.compact_file, "r", encoding="utf-8") as f:
                lines = f.readlines()
                self.step_counter = len(lines)

    def append_step(
        self,
        source: str | StepSource,
        step_type: str | StepType,
        content: str = "",
        thinking: str = "",
        tool_calls: Optional[List[Dict[str, Any] | ToolCall]] = None,
        media: Optional[List[Dict[str, Any] | MediaRef]] = None,
        status: str = "DONE",
    ) -> Tuple[int, int]:
        """
        Appends a step to both full and compact logs.
        Returns: (step_index, byte_offset_in_full_file)
        """
        self.step_counter += 1
        src = source.value if isinstance(source, StepSource) else str(source)
        stype = step_type.value if isinstance(step_type, StepType) else str(step_type)

        serialized_tool_calls = []
        if tool_calls:
            for tc in tool_calls:
                if isinstance(tc, ToolCall):
                    serialized_tool_calls.append(tc.to_dict())
                elif isinstance(tc, dict):
                    serialized_tool_calls.append(tc)

        serialized_media = []
        if media:
            for m in media:
                if isinstance(m, MediaRef):
                    serialized_media.append(m.to_dict())
                elif isinstance(m, dict):
                    serialized_media.append(m)

        record = TranscriptRecord(
            step_index=self.step_counter,
            source=src,
            type=stype,
            content=content or "",
            thinking=thinking or "",
            tool_calls=serialized_tool_calls,
            media=serialized_media,
            status=status,
        )

        # 1. Write full record and track byte offset
        full_json_str = json.dumps(record.to_dict(), ensure_ascii=False) + "\n"
        full_bytes = full_json_str.encode("utf-8")

        with open(self.full_file, "ab") as f:
            offset = f.tell()
            f.write(full_bytes)
            self._full_offsets.append(offset)

        # 2. Write compact record with pointer truncation
        compact_data = record.to_dict()
        truncated_fields: List[str] = []

        if len(compact_data.get("content", "")) > self.truncation_threshold:
            compact_data["content"] = (
                compact_data["content"][: self.truncation_threshold]
                + "... [TRUNCATED]"
            )
            truncated_fields.append("content")

        if len(compact_data.get("thinking", "")) > self.truncation_threshold:
            compact_data["thinking"] = (
                compact_data["thinking"][: self.truncation_threshold]
                + "... [TRUNCATED]"
            )
            truncated_fields.append("thinking")

        raw_tools_str = json.dumps(compact_data.get("tool_calls", []))
        if len(raw_tools_str) > self.truncation_threshold:
            compact_data["tool_calls"] = [
                {"summary": f"{len(serialized_tool_calls)} tool calls (truncated)"}
            ]
            truncated_fields.append("tool_calls")

        if truncated_fields:
            compact_data["truncated_fields"] = truncated_fields

        compact_json_str = json.dumps(compact_data, ensure_ascii=False) + "\n"
        with open(self.compact_file, "a", encoding="utf-8") as f:
            f.write(compact_json_str)

        return self.step_counter, offset

    def get_step(self, step_index: int, full: bool = True) -> Optional[Dict[str, Any]]:
        """
        O(1) seek directly to step_index in transcript_full.jsonl using cached byte offsets.
        """
        if step_index < 1 or step_index > len(self._full_offsets):
            return None

        target_file = self.full_file if full else self.compact_file
        if not target_file.exists():
            return None

        if full and step_index <= len(self._full_offsets):
            offset = self._full_offsets[step_index - 1]
            try:
                with open(self.full_file, "rb") as f:
                    f.seek(offset)
                    line = f.readline().decode("utf-8", errors="replace")
                    return json.loads(line)
            except Exception:
                pass

        # Fallback linear scan
        try:
            with open(target_file, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                        if rec.get("step_index") == step_index:
                            return rec
                    except Exception:
                        continue
        except Exception:
            pass
        return None

    def read_compact_history(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Reads compact transcript lines for prompt assembly."""
        if not self.compact_file.exists():
            return []
        records = []
        with open(self.compact_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
        if limit and len(records) > limit:
            return records[-limit:]
        return records

    def compact_trajectory(
        self,
        task_overview: str,
        completed_steps: List[str],
        active_context: List[str],
        open_commitments: List[str],
    ) -> str:
        """
        Builds a structured markdown trajectory summary and appends it to the log.
        """
        summary_lines = [
            "<CONTEXT_SUMMARY>",
            f"# Task Overview\n{task_overview}\n",
            "# Completed Steps",
        ]
        for step in completed_steps:
            summary_lines.append(f"- {step}")

        summary_lines.append("\n# Active Context & Key Findings")
        for ctx in active_context:
            summary_lines.append(f"- {ctx}")

        summary_lines.append("\n# Open Decisions & Commitments")
        for comm in open_commitments:
            summary_lines.append(f"- {comm}")

        summary_lines.append("</CONTEXT_SUMMARY>")
        summary_text = "\n".join(summary_lines)

        self.append_step(
            source=StepSource.SYSTEM,
            step_type=StepType.COMPACTION_SUMMARY,
            content=summary_text,
        )
        return summary_text
