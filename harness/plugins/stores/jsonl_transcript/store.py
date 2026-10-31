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
        self._lock = asyncio.Lock()
        self._step_counter = 0
        self._init_counter()

    def _init_counter(self) -> None:
        """Reserve valid persisted indices from BOTH logs; never count corrupt tails.

        A full-only write may be the residue of a failed dual-log append. Keep
        that index reserved, even though the append was never acknowledged.
        Unreadable existing files fail closed rather than resetting the counter.
        """
        for path in (self.full_path, self.compact_path):
            if not path.exists():
                continue
            valid_ordinal = 0
            with path.open("rb") as stream:
                for line in stream:
                    try:
                        record = json.loads(line)
                    except (ValueError, RecursionError):
                        continue
                    if not isinstance(record, dict):
                        continue
                    index = record.get("step_index")
                    if "step_index" in record and (type(index) is not int or index < 1):
                        continue
                    valid_ordinal += 1
                    self._step_counter = max(self._step_counter, index or valid_ordinal)

    def for_session(self, session_dir: Path | str) -> JsonlTranscriptStore:
        """Returns a JsonlTranscriptStore scoped to the specified session directory."""
        return JsonlTranscriptStore(session_dir)

    async def append(self, record: dict[str, Any] | Any) -> int:
        """
        Appends record to both full and compact logs.
        Enriches records with call_id/tool_name/is_error metadata and byte offsets.
        """
        async with self._lock:
            self._step_counter += 1
            step_idx = self._step_counter

            # Convert to dict if TranscriptRecord or object with to_dict
            if hasattr(record, "to_dict"):
                full_rec = record.to_dict()
            else:
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
                # Open both before mutation. Seal any incomplete physical tail
                # IN PLACE: preserve every old byte, only append a delimiter.
                with self.full_path.open("a+b") as full, self.compact_path.open("a+b") as compact:
                    for path, stream in ((self.full_path, full), (self.compact_path, compact)):
                        stream.seek(0, 2)
                        if stream.tell():
                            stream.seek(-1, 2)
                            if stream.read(1) != b"\n":
                                stream.seek(0, 2)
                                stream.write(b"\n")
                                stream.flush()
                                logger.warning("Sealed unterminated transcript tail without discarding bytes: %s", path)
                        stream.seek(0, 2)
                    offset = full.tell()
                    full.write(full_line.encode("utf-8"))
                    full.flush()
                    compact_rec["full_offset"] = offset
                    compact.write((json.dumps(compact_rec) + "\n").encode("utf-8"))
                    compact.flush()

            writer = asyncio.create_task(asyncio.to_thread(_write))
            try:
                await asyncio.shield(writer)
            except asyncio.CancelledError:
                # Cancelling an await cannot stop its filesystem thread. Keep
                # the serialization lock until that commit/failure is known.
                while not writer.done():
                    try:
                        await asyncio.shield(writer)
                    except asyncio.CancelledError:
                        continue
                if not writer.cancelled():
                    writer.exception()
                raise
            return step_idx

    async def read(self, since_step: int = 0, compact: bool = True) -> list[dict[str, Any]]:
        """Reads transcript records, tolerating corrupted or malformed lines."""
        target_path = self.compact_path if compact else self.full_path
        if not target_path.exists():
            return []

        def _read():
            records = []
            with open(target_path, "rb") as f:
                for line in f:
                    line_str = line.strip()
                    if not line_str:
                        continue
                    try:
                        rec = json.loads(line_str)
                        if not isinstance(rec, dict):
                            logger.warning("Skipping non-record transcript JSON in %s", target_path)
                            continue
                        index = rec.get("step_index", 0)
                        if "step_index" in rec and (type(index) is not int or index < 1):
                            logger.warning("Skipping invalid transcript index in %s", target_path)
                            continue
                        if index >= since_step:
                            records.append(rec)
                    except (ValueError, RecursionError):
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
