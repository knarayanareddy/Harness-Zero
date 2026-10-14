"""
XML Tool Calling Protocol and Stream De-multiplexer for Local Models (PLG-12).
Supports Qwen-Agent ChatML and DeepSeek-R1 <think> tags.
"""
from __future__ import annotations

import json
import logging
import re

from harness.sdk.types import ModelEvent, TextDelta, ThinkingDelta, ToolCallEnd, ToolSpec

logger = logging.getLogger(__name__)


def format_tool_guidance(tools: list[ToolSpec]) -> str:
    """Formats OpenAPI tool specifications into an XML tool calling protocol block."""
    lines = [
        "# Tools",
        "",
        "You have access to the following tools:",
        "",
    ]
    for spec in tools:
        lines.append(f"## {spec.name}")
        lines.append(f"Description: {spec.description}")
        lines.append(f"Parameters: {json.dumps(spec.input_schema)}")
        lines.append("")

    lines.append("To call a tool, format your output strictly as:")
    lines.append("<tool_call>")
    lines.append('{"name": "tool_name", "arguments": { ... }}')
    lines.append("</tool_call>")
    lines.append("")
    lines.append("You may think before calling a tool using <think>...</think> tags.")
    return "\n".join(lines)


class XmlStreamParser:
    """
    Streaming parser that separates:
    - Thought tokens inside <think>...</think> -> ThinkingDelta
    - Tool calls inside <tool_call>...</tool_call> -> ToolCallEnd
    - Normal assistant text -> TextDelta
    """

    def __init__(self) -> None:
        self.buffer = ""
        self.in_think = False
        self.in_tool = False
        self.tool_buffer = ""
        self.call_counter = 0

    def feed(self, chunk: str) -> list[ModelEvent]:
        self.buffer += chunk
        events: list[ModelEvent] = []

        while self.buffer:
            if not self.in_think and not self.in_tool:
                # Check for <think> or <tool_call>
                think_idx = self.buffer.find("<think>")
                tool_idx = self.buffer.find("<tool_call>")

                if think_idx == -1 and tool_idx == -1:
                    # Check for partial prefix
                    partial = self._partial_tag_len(self.buffer, ("<think>", "<tool_call>"))
                    if partial > 0:
                        emit_text = self.buffer[:-partial]
                        self.buffer = self.buffer[-partial:]
                        if emit_text:
                            events.append(TextDelta(text=emit_text))
                        break
                    else:
                        events.append(TextDelta(text=self.buffer))
                        self.buffer = ""
                elif think_idx != -1 and (tool_idx == -1 or think_idx < tool_idx):
                    # Enter think mode
                    if think_idx > 0:
                        events.append(TextDelta(text=self.buffer[:think_idx]))
                    self.buffer = self.buffer[think_idx + len("<think>"):]
                    self.in_think = True
                else:
                    # Enter tool mode
                    if tool_idx > 0:
                        events.append(TextDelta(text=self.buffer[:tool_idx]))
                    self.buffer = self.buffer[tool_idx + len("<tool_call>"):]
                    self.in_tool = True
                    self.tool_buffer = ""

            elif self.in_think:
                close_idx = self.buffer.find("</think>")
                if close_idx == -1:
                    partial = self._partial_tag_len(self.buffer, ("</think>",))
                    if partial > 0:
                        emit_text = self.buffer[:-partial]
                        self.buffer = self.buffer[-partial:]
                        if emit_text:
                            events.append(ThinkingDelta(text=emit_text))
                        break
                    else:
                        events.append(ThinkingDelta(text=self.buffer))
                        self.buffer = ""
                else:
                    thought_text = self.buffer[:close_idx]
                    if thought_text:
                        events.append(ThinkingDelta(text=thought_text))
                    self.buffer = self.buffer[close_idx + len("</think>"):]
                    self.in_think = False

            elif self.in_tool:
                close_idx = self.buffer.find("</tool_call>")
                if close_idx == -1:
                    self.tool_buffer += self.buffer
                    self.buffer = ""
                else:
                    self.tool_buffer += self.buffer[:close_idx]
                    self.buffer = self.buffer[close_idx + len("</tool_call>"):]
                    self.in_tool = False

                    # Parse tool call
                    tool_call = self._parse_json_tool_call(self.tool_buffer)
                    if tool_call is not None:
                        events.append(tool_call)
                    self.tool_buffer = ""

        return events

    def flush(self) -> list[ModelEvent]:
        """Flushes remaining buffer at end of stream."""
        events: list[ModelEvent] = []
        if self.in_tool and self.tool_buffer:
            tool_call = self._parse_json_tool_call(self.tool_buffer + self.buffer)
            if tool_call is not None:
                events.append(tool_call)
        elif self.in_think and self.buffer:
            events.append(ThinkingDelta(text=self.buffer))
        elif self.buffer:
            # Check if any full <tool_call> tags exist in remaining buffer
            pattern = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.DOTALL)
            m = pattern.search(self.buffer)
            if m:
                tc = self._parse_json_tool_call(m.group(1))
                if tc:
                    events.append(tc)
            else:
                events.append(TextDelta(text=self.buffer))
        self.buffer = ""
        return events

    def _parse_json_tool_call(self, text: str) -> ToolCallEnd | None:
        text = text.strip()
        try:
            data = json.loads(text)
            name = data.get("name", "")
            args = data.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {"input": args}
            if not isinstance(args, dict):
                args = {}
            self.call_counter += 1
            return ToolCallEnd(
                id=f"xml_call_{self.call_counter}",
                name=name,
                arguments=args,
            )
        except Exception as e:
            logger.debug("Failed to parse tool call JSON: %s (%s)", text, e)
            return None

    def _partial_tag_len(self, text: str, tags: tuple[str, ...]) -> int:
        max_len = 0
        for tag in tags:
            for length in range(min(len(text), len(tag) - 1), 0, -1):
                if tag.startswith(text[-length:]):
                    if length > max_len:
                        max_len = length
        return max_len
