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
from harness.kernel.transcript import MESSAGE_KINDS, TranscriptLog, upgrade_record_v1
from harness.kernel.validators import validate_session_id
from harness.sdk.errors import TurnInProgress
from harness.sdk.events import Event, SessionStarted, SessionSwitched
from harness.sdk.types import HookEvent, SessionInfo, SessionRow

logger = logging.getLogger(__name__)


class _NullTranscriptStore:
    """Fallback transcript store when no transcript store plugin is registered."""

    def __init__(self, session_dir: Path | str | None = None) -> None:
        self.session_dir = Path(session_dir) if session_dir else Path(".")
        self.compact_path = self.session_dir / "transcript.jsonl"
        self.full_path = self.session_dir / "transcript_full.jsonl"
        self._records: list[dict[str, Any]] = []

    async def append(self, record: dict[str, Any] | Any) -> int:
        rec = dict(record.to_dict() if hasattr(record, "to_dict") else record)
        rec["step_index"] = len(self._records) + 1
        self._records.append(rec)
        if self.session_dir:
            self.session_dir.mkdir(parents=True, exist_ok=True)
            line = json.dumps(rec) + "\n"
            with open(self.compact_path, "a", encoding="utf-8") as f:
                f.write(line)
            with open(self.full_path, "a", encoding="utf-8") as f:
                f.write(line)
        return rec["step_index"]

    async def read(self, since_step: int = 0, compact: bool = True) -> list[dict[str, Any]]:
        path = self.compact_path if compact else self.full_path
        if path.exists():
            records: list[dict[str, Any]] = []
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        records.append(json.loads(line))
            return [r for r in records if r.get("step_index", 0) >= since_step]
        return [r for r in self._records if r.get("step_index", 0) >= since_step]

    async def last_compaction(self) -> dict[str, Any] | None:
        return None

    async def list_sessions(self) -> list[dict[str, Any]]:
        return []

    def for_session(self, session_dir: Path | str) -> _NullTranscriptStore:
        return _NullTranscriptStore(session_dir)


def _summarize_transcript(path: Path, title_chars: int = 60) -> dict[str, Any]:
    """Cheap scan of a transcript.jsonl: record count, number of user prompts, and the first prompt as title."""
    out: dict[str, Any] = {"records": 0, "messages": 0, "turns": 0, "title": ""}
    if not path.exists():
        return out
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                out["records"] += 1
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                # `message_count` answers "how much conversation", not "how many lines": turn markers and
                # decision records would inflate it every turn. Upgraded per line so the answer is the
                # same for a v1 file as for a v2 one.
                try:
                    if upgrade_record_v1(rec).get("kind") in MESSAGE_KINDS:
                        out["messages"] += 1
                except Exception:
                    pass
                if rec.get("role") != "user" or not rec.get("content"):
                    continue  # tool-result records are user-role with empty content
                out["turns"] += 1
                if not out["title"]:
                    text = " ".join(str(rec["content"]).split())
                    out["title"] = text[:title_chars] + ("…" if len(text) > title_chars else "")
    except OSError:
        pass
    return out


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

        transcript_store = None
        if self.registry and hasattr(self.registry, "stores") and "transcript" in self.registry.stores:
            reg_store = self.registry.stores["transcript"][0]
            if hasattr(reg_store, "for_session"):
                transcript_store = reg_store.for_session(self.session_dir)
            elif callable(reg_store):
                transcript_store = reg_store(self.session_dir)
            else:
                transcript_store = reg_store
        self.transcript = transcript_store or _NullTranscriptStore(self.session_dir)
        # WS-04: the log is the session's single record stream, built here rather than lazily so that
        # whoever touches it first — the runner, `sessions.get`, a test — gets *this* store instance.
        # Two stores over one JSONL file each keep their own step counter, which is how the runner and
        # the session came to disagree about what had been written.
        self._log = TranscriptLog.for_session(
            self.session_dir, session_id=self.session_id, store=self.transcript
        )

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

    @property
    def log(self) -> TranscriptLog:
        """`TranscriptLog` over this session's store (WS-04)."""
        return self._log

    async def get_history(self, since_step: int = 0) -> list[dict[str, Any]]:
        """Returns transcript records for this session, upgraded to the current record version.

        Reads through the log rather than the store so a v1 file and a freshly written v2 record are
        indistinguishable to callers (`sessions.get`, the reload-equals-live test, the web client).
        """
        return await self.log.read(since_step=since_step, compact=False)

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
                    data=SessionStarted(
                        session_id=self.session_id,
                        workspace=str(self.workspace),
                        strategy=strat,
                        provider=provider,
                        model=model,
                    ),
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
        """Lists every session in the workspace, most recently updated first.

        Rows are ``harness.sdk.types.SessionRow.to_dict()``: ``session_id, workspace, created_at,
        updated_at, title, turns, message_count, active, busy``.
        """
        results = []
        if not self.sessions_dir.exists():
            return results

        for child in self.sessions_dir.iterdir():
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

            created_at = float(meta_data.get("created_at", child.stat().st_mtime))
            t_file = child / "transcript.jsonl"
            summary = _summarize_transcript(t_file)
            updated_at = t_file.stat().st_mtime if t_file.exists() else created_at
            live = self._sessions.get(sess_id)

            results.append(
                SessionRow(
                    session_id=sess_id,
                    workspace=str(self.workspace),
                    created_at=created_at,
                    updated_at=updated_at,
                    title=summary["title"],
                    turns=summary["turns"],
                    message_count=summary["messages"],
                    active=self.active_session.session_id == sess_id if self.active_session else False,
                    busy=bool(live and live.is_busy),
                ).to_dict()
            )
        results.sort(key=lambda r: r["updated_at"], reverse=True)
        return results

    async def resume_last(self) -> Session | None:
        """Makes the most recently updated session that has at least one recorded turn the active one.

        Called at daemon start so a browser refresh — or a daemon restart — lands on the conversation the
        user was in, instead of a fresh empty session per start. Empty sessions are skipped. A resume is
        not a start: SESSION_START hooks and ``session.started`` are not fired (they ran when the session
        was created); ``session.switched`` is published so connected frontends can follow.
        """
        for row in self.list_sessions():
            if row["turns"] <= 0:
                continue
            session = self.get_session(row["session_id"])
            if session is None:
                continue
            self.active_session = session
            if self.bus:
                self.bus.publish(
                    Event(
                        type="session.switched",
                        session_id=session.session_id,
                        data=SessionSwitched(session_id=session.session_id, workspace=str(self.workspace)),
                    )
                )
            return session
        return None

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
                    data=SessionSwitched(
                        session_id=session_id,
                        workspace=str(self.workspace),
                    ),
                )
            )
        return session
