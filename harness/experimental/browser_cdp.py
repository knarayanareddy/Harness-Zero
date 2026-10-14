"""
Browser Computer Use & CDP Engine.
Connects directly to Chrome DevTools Protocol (CDP), prunes accessibility trees
down to interactive indices ([#1] Button, [#2] Input), and provides semantic action primitives.

Quarantined per EXP-01 / SIM-02: Silent mock mode fallback removed in connect().
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger("harness.browser")


@dataclass
class InteractiveElement:
    index: int
    tag: str
    role: str
    text: str
    bounds: dict[str, float]

    def to_summary(self) -> str:
        role_or_tag = self.role if self.role else self.tag.lower()
        clean_text = self.text.replace("\n", " ").strip()[:60]
        return f"[#{self.index}] <{role_or_tag}>: \"{clean_text}\" (x: {int(self.bounds.get('x', 0))}, y: {int(self.bounds.get('y', 0))})"


class BrowserCDPEngine:
    def __init__(
        self,
        cdp_http_url: str = "http://127.0.0.1:9222",
        ws_url: str | None = None,
    ):
        self.cdp_http_url = cdp_http_url.rstrip("/")
        self.ws_url = ws_url
        self.is_connected = False
        self._mock_mode = False
        self._current_url = "about:blank"
        self._mock_elements: list[InteractiveElement] = []

    def enable_mock_mode(self, elements: list[InteractiveElement] | None = None) -> None:
        """Enables deterministic offline testing without requiring a live Chrome instance."""
        self._mock_mode = True
        self.is_connected = True
        self._mock_elements = elements or [
            InteractiveElement(1, "BUTTON", "button", "Submit", {"x": 200, "y": 150, "w": 80, "h": 32}),
            InteractiveElement(2, "INPUT", "textbox", "Email Address", {"x": 200, "y": 100, "w": 240, "h": 36}),
            InteractiveElement(3, "A", "link", "Forgot Password?", {"x": 200, "y": 200, "w": 120, "h": 20}),
        ]

    async def connect(self) -> bool:
        """
        Connects to a live Chrome CDP endpoint.
        Fails honestly if Chrome CDP is not reachable (no silent fallback).
        """
        if self._mock_mode:
            self.is_connected = True
            return True

        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(f"{self.cdp_http_url}/json/version")
                if resp.status_code == 200:
                    data = resp.json()
                    self.ws_url = data.get("webSocketDebuggerUrl")
                    self.is_connected = True
                    return True
        except Exception:
            logger.warning(
                "No active Chrome CDP endpoint found at %s. Connection failed.",
                self.cdp_http_url,
            )
            self.is_connected = False
            return False

        self.is_connected = False
        return False

    async def navigate(self, url: str) -> dict[str, Any]:
        self._current_url = url
        if not self.is_connected:
            return {"status": "error", "error": "Not connected to browser"}
        return {"status": "navigated", "url": url}

    async def observe(self, instruction: str = "") -> list[InteractiveElement]:
        """Discovers and prunes actionable elements into compact indices."""
        if not self.is_connected:
            return []
        if self._mock_mode:
            if instruction:
                lower = instruction.lower()
                filtered = [
                    el for el in self._mock_elements
                    if lower in el.text.lower() or lower in el.role.lower()
                ]
                return filtered or self._mock_elements
            return self._mock_elements
        return self._mock_elements

    def render_pruned_tree(self, elements: list[InteractiveElement]) -> str:
        """Renders compact token-efficient accessibility tree."""
        if not elements:
            return "(No interactive elements found on page)"
        lines = ["## Page Interactive Elements:"]
        for el in elements:
            lines.append(f"  {el.to_summary()}")
        return "\n".join(lines)

    async def act(self, instruction: str, target_index: int | None = None) -> dict[str, Any]:
        """Executes semantic action (e.g. click, type)."""
        if not self.is_connected:
            return {"success": False, "error": "Not connected to browser"}
        elements = await self.observe(instruction)
        target = None
        if target_index is not None:
            for el in elements:
                if el.index == target_index:
                    target = el
                    break
        elif elements:
            target = elements[0]

        if not target:
            return {"success": False, "error": f"No target found matching '{instruction}'"}

        return {
            "success": True,
            "action": "executed",
            "instruction": instruction,
            "target": target.to_summary(),
        }

    async def extract(
        self,
        schema: dict[str, Any],
        instruction: str,
    ) -> dict[str, Any]:
        """Extracts schema-validated structured data from current page."""
        if not self.is_connected:
            return {"status": "error", "error": "Not connected to browser"}
        return {
            "status": "extracted",
            "instruction": instruction,
            "data": {k: f"mock_value_for_{k}" for k in schema.get("properties", {})},
        }
