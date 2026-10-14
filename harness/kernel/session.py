"""
Session and SessionManager: workspace session isolation, transcript persistence,
turn concurrency locking, git exclude protection, and lifecycle hooks.
Fulfills KRN-09 (§5.1, §5.2) and absorbs SEC-06 and LOOP-05/06.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from harness.kernel.bus import EventBus
from harness.kernel.registry import PluginRegistry
from harness.kernel.settings import SettingsStore
from harness.kernel.validators import validate_session_id
from harness.sdk.errors import TurnInProgress
from harness.sdk.events import Event
from harness.sdk.types import HookEvent, SessionInfo

logger = logging.getLogger(__name__)


class JsonlTranscriptStore:
    """
    Concrete TranscriptStore implementation backed by append-only JSONL files:
    - transcript.jsonl: compact chronological trajectory
    - transcript_full.jsonl: full un-truncated records
    Tolerates truncated or corrupt trailing lines.
    """

    def __init__(self, session_dir: Path) -> None:
        self.session_dir = Path(session_dir)
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.compact_path = self.session_dir / "transcript.jsonl"
        self.full_path = self.session_dir / "transcript_full.jsonl"
        self._lock = asyncio.Lock()
        self._step_counter = 0
        self._init_counter()

    def _init_counter(self) -> None:
        """Counts existing records, tolerating trailing bad lines."""
        if not self.compact_path.exists():
            self._step_counter = 0
            return

        count = 0
        try:
            with open(self.compact_path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    if line.strip():
                        count += 1
        except Exception as e:
            logger.warning(f"Error reading transcript index for {self.session_dir}: {e}")
        self._step_counter = count

    async def append(self, record: dict[str, Any]) -> int:
        """Appends record to compact and full JSONL logs. Returns step_index."""
        async with self._lock:
            self._step_counter += 1
            step_idx = self._step_counter
            rec_copy = dict(record)
            rec_copy["step_index"] = step_idx
            rec_copy.setdefault("timestamp", time.time())

            line = json.dumps(rec_copy) + "\n"

            def _write():
                with open(self.compact_path, "a", encoding="utf-8") as f:
                    f.write(line)
                    f.flush()
                with open(self.full_path, "a", encoding="utf-8") as f:
                    f.write(line)
                    f.flush()

            await asyncio.to_thread(_write)
            return step_idx

    async def read(self, since_step: int = 0, compact: bool = True) -> list[dict[str, Any]]:
        """Reads transcript records, tolerating corrupt lines."""
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
                        # Tolerate corrupt line (crash recovery)
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
        """Returns metadata for this session."""
        return [{
            "session_dir": str(self.session_dir),
            "step_count": self._step_counter,
        }]


class Session:
    """
    Session instance representing an isolated conversation context within a workspace.
    Owns transcript logs, session lock (prevents concurrent turns), and lifecycle hooks (§5.1, §5.2).
    """

    def __init__(
        self,
        session_id: str,
        workspace: Path,
        registry: PluginRegistry,
        settings: SettingsStore | None = None,
        bus: EventBus | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        validate_session_id(session_id)
        self.session_id = session_id
        self.workspace = Path(workspace)
        self.registry = registry
        self.settings = settings
        self.bus = bus
        self.metadata = metadata or {}
        self.created_at = float(self.metadata.get("created_at", time.time()))

        self.session_dir = self.workspace / ".harness" / "sessions" / self.session_id
        self.session_dir.mkdir(parents=True, exist_ok=True)

        self.transcript = JsonlTranscriptStore(self.session_dir)

        # Concurrency control: one turn per session
        self._lock = asyncio.Lock()
        self._busy: bool = False

        self._save_metadata()

    def _save_metadata(self) -> None:
        meta_file = self.session_dir / "session.json"
        data = {
            "session_id": self.session_id,
            "workspace": str(self.workspace),
            "created_at": self.created_at,
            "metadata": self.metadata,
        }
        with open(meta_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    @property
    def info(self) -> SessionInfo:
        return SessionInfo(
            session_id=self.session_id,
            workspace=self.workspace,
            created_at=self.created_at,
            metadata=self.metadata,
        )

    @property
    def is_busy(self) -> bool:
        return self._busy

    @asynccontextmanager
    async def turn_lock(self):
        """
        Acquires exclusive execution lock for a turn.
        Raises TurnInProgress (HTTP 409 equivalent) if another turn is actively executing.
        """
        if self._busy:
            raise TurnInProgress(self.session_id)
        await self._lock.acquire()
        self._busy = True
        try:
            yield
        finally:
            self._busy = False
            if self._lock.locked():
                self._lock.release()

    def ensure_git_exclude(self) -> None:
        """Writes /.harness/ to .git/info/exclude if applicable (§5.1)."""
        git_dir = self.workspace / ".git"
        if not git_dir.is_dir():
            return

        exclude_file = git_dir / "info" / "exclude"
        exclude_file.parent.mkdir(parents=True, exist_ok=True)

        pattern = "/.harness/"
        existing_content = ""
        if exclude_file.exists():
            existing_content = exclude_file.read_text(encoding="utf-8", errors="replace")

        if pattern not in existing_content and ".harness" not in existing_content:
            with open(exclude_file, "a", encoding="utf-8") as f:
                f.write(f"\n{pattern}\n")
            logger.info(f"Added {pattern} to {exclude_file}")

    async def open(self) -> None:
        """
        Initializes session: ensures git exclude, fires SESSION_START hooks,
        and emits session.started event (§5.1).
        """
        self.ensure_git_exclude()

        # Fire SESSION_START hooks
        hooks = self.registry.get_hooks_for_event(HookEvent.SESSION_START)
        hook_ctx = self.info
        for h in hooks:
            try:
                await asyncio.wait_for(
                    h.on(HookEvent.SESSION_START, {"session_id": self.session_id}, hook_ctx),
                    timeout=h.timeout_s,
                )
            except Exception as e:
                logger.warning(f"Error in SESSION_START hook '{h.name}': {e}")

        if self.bus:
            strat = self.settings.get("kernel.strategy", "single_loop") if self.settings else "single_loop"
            provider = self.settings.get("kernel.provider", "") if self.settings else ""
            model = self.settings.get("kernel.model", "") if self.settings else ""
            self.bus.publish(
                Event(
                    type="session.started",
                    session_id=self.session_id,
                    data={
                        "session_id": self.session_id,
                        "workspace": str(self.workspace),
                        "strategy": strat,
                        "provider": provider,
                        "model": model,
                    },
                )
            )


class SessionManager:
    """
    Manages session lifecycle, listing, and switching within a workspace.
    """

    def __init__(
        self,
        workspace: Path,
        registry: PluginRegistry,
        settings: SettingsStore | None = None,
        bus: EventBus | None = None,
    ) -> None:
        self.workspace = Path(workspace)
        self.registry = registry
        self.settings = settings
        self.bus = bus
        self.sessions_dir = self.workspace / ".harness" / "sessions"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

        self._sessions: dict[str, Session] = {}
        self.active_session: Session | None = None

    async def create_session(
        self,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Session:
        """Creates a new session, opens it, and sets it as active."""
        if not session_id:
            session_id = f"session_{uuid.uuid4().hex[:12]}"

        validate_session_id(session_id)
        session = Session(
            session_id=session_id,
            workspace=self.workspace,
            registry=self.registry,
            settings=self.settings,
            bus=self.bus,
            metadata=metadata,
        )
        await session.open()
        self._sessions[session_id] = session
        self.active_session = session
        return session

    def get_session(self, session_id: str) -> Session | None:
        """Retrieves active or persisted session by id."""
        validate_session_id(session_id)
        if session_id in self._sessions:
            return self._sessions[session_id]

        target_dir = self.sessions_dir / session_id
        if target_dir.is_dir():
            meta_file = target_dir / "session.json"
            meta = {}
            if meta_file.exists():
                try:
                    meta = json.loads(meta_file.read_text(encoding="utf-8"))
                except Exception:
                    pass
            session = Session(
                session_id=session_id,
                workspace=self.workspace,
                registry=self.registry,
                settings=self.settings,
                bus=self.bus,
                metadata=meta.get("metadata", {}),
            )
            self._sessions[session_id] = session
            return session
        return None

    def list_sessions(self) -> list[dict[str, Any]]:
        """Lists all historical and active sessions in workspace."""
        results = []
        if not self.sessions_dir.exists():
            return results

        for child in sorted(self.sessions_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if not child.is_dir():
                continue
            sess_id = child.name
            try:
                validate_session_id(sess_id)
            except Exception:
                continue

            meta_file = child / "session.json"
            meta_data = {}
            if meta_file.exists():
                try:
                    meta_data = json.loads(meta_file.read_text(encoding="utf-8"))
                except Exception:
                    pass

            created_at = meta_data.get("created_at", child.stat().st_ctime)
            t_file = child / "transcript.jsonl"
            msg_count = 0
            if t_file.exists():
                try:
                    with open(t_file, encoding="utf-8", errors="replace") as f:
                        msg_count = sum(1 for line in f if line.strip())
                except Exception:
                    pass

            results.append({
                "session_id": sess_id,
                "workspace": str(self.workspace),
                "created_at": created_at,
                "message_count": msg_count,
                "active": self.active_session.session_id == sess_id if self.active_session else False,
            })
        return results

    async def switch_session(self, session_id: str) -> Session:
        """Switches the active session, emitting session.switched."""
        session = self.get_session(session_id)
        if not session:
            session = await self.create_session(session_id=session_id)

        self.active_session = session
        if self.bus:
            self.bus.publish(
                Event(
                    type="session.switched",
                    session_id=session_id,
                    data={
                        "session_id": session_id,
                        "workspace": str(self.workspace),
                    },
                )
            )
        return session
