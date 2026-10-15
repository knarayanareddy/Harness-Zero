"""Minimal SSE reader over httpx, with the browser's framing semantics."""
from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx


class SSEReader:
    def __init__(self, client: httpx.AsyncClient, url: str, *, headers: dict[str, str] | None = None) -> None:
        self.client = client
        self.url = url
        self.headers = headers or {}
        self.events: list[dict[str, Any]] = []
        self.frames: list[dict[str, Any]] = []  # raw frames: {event, id, data}
        self.status: int | None = None
        self._task: asyncio.Task | None = None
        self._opened = asyncio.Event()
        self._new = asyncio.Condition()

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run())
        await asyncio.wait_for(self._opened.wait(), timeout=10)

    async def _run(self) -> None:
        try:
            async with self.client.stream("GET", self.url, headers={"Accept": "text/event-stream", **self.headers}, timeout=None) as r:
                self.status = r.status_code
                self._opened.set()
                if r.status_code != 200:
                    return
                buf = ""
                async for chunk in r.aiter_text():
                    buf += chunk
                    while "\n\n" in buf:
                        frame, buf = buf.split("\n\n", 1)
                        self._handle(frame)
        except (httpx.ReadError, httpx.RemoteProtocolError, asyncio.CancelledError):
            pass
        finally:
            self._opened.set()

    def _handle(self, frame: str) -> None:
        data_lines: list[str] = []
        event_type = "message"
        event_id = None
        for line in frame.split("\n"):
            if line.startswith(":"):
                continue
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
            elif line.startswith("event:"):
                event_type = line[6:].strip()
            elif line.startswith("id:"):
                event_id = line[3:].strip()
        if not data_lines:
            return
        raw = "\n".join(data_lines)
        self.frames.append({"event": event_type, "id": event_id, "data": raw})
        try:
            env = json.loads(raw)
        except json.JSONDecodeError:
            env = {"type": event_type, "data": {"raw": raw}}
        if isinstance(env, dict):
            env.setdefault("type", event_type)
            env["_sse_event_name"] = event_type
            self.events.append(env)
        asyncio.get_event_loop().call_soon(self._notify)

    def _notify(self) -> None:
        async def _n() -> None:
            async with self._new:
                self._new.notify_all()

        asyncio.create_task(_n())

    def count(self, predicate) -> int:
        return sum(1 for e in self.events if predicate(e))

    async def wait_for(self, predicate, *, timeout: float, min_count: int = 1) -> bool:
        """Waits until at least `min_count` events match `predicate` (use a running count for multi-turn runs)."""
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if self.count(predicate) >= min_count:
                return True
            await asyncio.sleep(0.05)
        return self.count(predicate) >= min_count

    async def close(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
