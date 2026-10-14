"""
Dual-Log JSONL Transcript Store (§5.2, §5.5, PLG-08).
Absorbs LOOP-05 and MDL-03.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

TRUNCATION_LIMIT = 500


class JsonlTranscriptStore:
    """
    Append-only transcript store maintaining dual logs:
    - transcript_full.jsonl: unabridged records with exact tool payloads
    - transcript.jsonl: compact chronological trajectory for fast scanning
    Tolerates truncated or corrupt lines during crash recovery.
    """

    def __init__(self, session_dir: Path | str) -> None:
        self.session_dir = Path(session_dir)
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.compact_path = self.session_dir / "transcript.jsonl"
        self.full_path = self.session_dir / "transcript_full.jsonl"
        self.index_path = self.session_dir.parent / "sessions_index.jsonl"
        self._lock = asyncio.Lock()
        self._step_counter = 0
        self._init_counter()

    def _init_counter(self) -> None:
        """Counts existing records, tolerating trailing corrupted lines."""
        if not self.compact_path.exists():
            self._step_counter = 0
            return

        count = 0
        try:
            with open(self.compact_path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    if line.strip():
                        count += 1
        except Exception as err:
            logger.warning(f"Error reading transcript index for {self.session_dir}: {err}")
        self._step_counter = count

    async def append(self, record: dict[str, Any]) -> int:
        """
        Appends record to both full and compact logs.
        Enriches records with call_id/tool_name/is_error metadata and byte offsets.
        """
        async with self._lock:
            self._step_counter += 1
            step_idx = self._step_counter

            # Deep copy and enrich record
            full_rec = dict(record)
            full_rec["step_index"] = step_idx
            full_rec.setdefault("timestamp", time.time())

            # Normalize tool call / result metadata
            meta = full_rec.setdefault("metadata", {})
            if "call_id" not in full_rec and "call_id" in meta:
                full_rec["call_id"] = meta["call_id"]
            if "tool_name" not in full_rec and "tool_name" in meta:
                full_rec["tool_name"] = meta["tool_name"]
            if "is_error" not in full_rec and "is_error" in meta:
                full_rec["is_error"] = meta["is_error"]

            full_line = json.dumps(full_rec) + "\n"

            # Create compact version
            compact_rec = dict(full_rec)
            truncated_fields = []

            for key in ("content", "thinking", "tool_calls"):
                val = compact_rec.get(key)
                if isinstance(val, str) and len(val) > TRUNCATION_LIMIT:
                    compact_rec[key] = val[:TRUNCATION_LIMIT] + "... [TRUNCATED]"
                    truncated_fields.append(key)
                elif isinstance(val, list) and len(json.dumps(val)) > TRUNCATION_LIMIT:
                    compact_rec[key] = f"[{len(val)} items] ... [TRUNCATED]"
                    truncated_fields.append(key)

            if truncated_fields:
                compact_rec["truncated_fields"] = truncated_fields

            def _write():
                # Append to full log and track byte offset
                offset = 0
                if self.full_path.exists():
                    offset = self.full_path.stat().st_size

                with open(self.full_path, "a", encoding="utf-8") as f:
                    f.write(full_line)
                    f.flush()

                compact_rec["full_offset"] = offset
                updated_compact_line = json.dumps(compact_rec) + "\n"

                with open(self.compact_path, "a", encoding="utf-8") as f:
                    f.write(updated_compact_line)
                    f.flush()

                # Update sessions index file
                try:
                    index_entry = {
                        "session_id": self.session_dir.name,
                        "session_dir": str(self.session_dir),
                        "last_updated": time.time(),
                        "step_count": step_idx,
                    }
                    with open(self.index_path, "a", encoding="utf-8") as f_idx:
                        f_idx.write(json.dumps(index_entry) + "\n")
                except Exception:
                    pass

            await asyncio.to_thread(_write)
            return step_idx

    async def read(self, since_step: int = 0, compact: bool = True) -> list[dict[str, Any]]:
        """Reads transcript records, tolerating corrupted or malformed lines."""
        target_path = self.compact_path if compact else self.full_path
        if not target_path.exists():
            return []

        def _read():
            records = []
            with open(target_path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    line_str = line.strip()
                    if not line_str:
                        continue
                    try:
                        rec = json.loads(line_str)
                        if rec.get("step_index", 0) >= since_step:
                            records.append(rec)
                    except json.JSONDecodeError:
                        logger.warning(f"Skipping corrupt transcript line in {target_path}")
            return records

        return await asyncio.to_thread(_read)

    async def last_compaction(self) -> dict[str, Any] | None:
        """Searches backwards for the latest COMPACTION_SUMMARY record."""
        records = await self.read(compact=True)
        for r in reversed(records):
            if str(r.get("type", "")).upper() == "COMPACTION_SUMMARY":
                return r
            if r.get("metadata", {}).get("compaction_summary"):
                return r
        return None

    async def list_sessions(self) -> list[dict[str, Any]]:
        """Returns sessions metadata from the sessions index file or directory."""
        if not self.index_path.exists():
            return [{
                "session_id": self.session_dir.name,
                "session_dir": str(self.session_dir),
                "step_count": self._step_counter,
            }]

        def _read_index():
            seen = {}
            with open(self.index_path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        entry = json.loads(line)
                        sid = entry.get("session_id")
                        if sid:
                            seen[sid] = entry
                    except json.JSONDecodeError:
                        continue
            return list(seen.values())

        sessions = await asyncio.to_thread(_read_index)
        return sessions or [{
            "session_id": self.session_dir.name,
            "session_dir": str(self.session_dir),
            "step_count": self._step_counter,
        }]
