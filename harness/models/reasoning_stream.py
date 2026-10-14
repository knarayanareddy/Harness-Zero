"""
Reasoning Stream Parser & Cross-Harness Tool Format Adapters
=============================================================
Provides streaming de-multiplexing of DeepSeek-R1 `<think>...</think>` tags,
Qwen-Agent ChatML function calling, and ZAI / GLM-5.3 tool formats.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


@dataclass
class StreamChunk:
    is_thought: bool
    content: str


class ReasoningStreamParser:
    """
    Parses streaming LLM outputs that interleave reasoning traces
    (e.g., DeepSeek-R1, Qwen-2.5 32B-Instruct, GLM-4/5 reasoning modes)
    inside `<think>...</think>` or `[THOUGHT]...[/THOUGHT]` tags.
    """

    def __init__(self, open_tag: str = "<think>", close_tag: str = "</think>"):
        self.open_tag = open_tag
        self.close_tag = close_tag
        self.in_thought = False
        self.buffer = ""

    def process_chunk(self, raw_chunk: str) -> list[StreamChunk]:
        """
        Processes a single streaming text chunk, cleanly isolating thought
        tokens from user-facing answer tokens across arbitrary chunk splits.
        """
        self.buffer += raw_chunk
        chunks: list[StreamChunk] = []

        while self.buffer:
            if not self.in_thought:
                # Looking for opening tag
                idx = self.buffer.find(self.open_tag)
                if idx == -1:
                    # Check if buffer ends with a prefix of open_tag
                    partial_len = self._matching_prefix_len(self.buffer, self.open_tag)
                    if partial_len > 0:
                        emit_text = self.buffer[:-partial_len]
                        self.buffer = self.buffer[-partial_len:]
                        if emit_text:
                            chunks.append(StreamChunk(is_thought=False, content=emit_text))
                        break
                    else:
                        chunks.append(StreamChunk(is_thought=False, content=self.buffer))
                        self.buffer = ""
                else:
                    # Found open tag
                    if idx > 0:
                        chunks.append(StreamChunk(is_thought=False, content=self.buffer[:idx]))
                    self.buffer = self.buffer[idx + len(self.open_tag):]
                    self.in_thought = True
            else:
                # Inside thought, looking for close tag
                idx = self.buffer.find(self.close_tag)
                if idx == -1:
                    partial_len = self._matching_prefix_len(self.buffer, self.close_tag)
                    if partial_len > 0:
                        emit_text = self.buffer[:-partial_len]
                        self.buffer = self.buffer[-partial_len:]
                        if emit_text:
                            chunks.append(StreamChunk(is_thought=True, content=emit_text))
                        break
                    else:
                        chunks.append(StreamChunk(is_thought=True, content=self.buffer))
                        self.buffer = ""
                else:
                    # Found close tag
                    if idx > 0:
                        chunks.append(StreamChunk(is_thought=True, content=self.buffer[:idx]))
                    self.buffer = self.buffer[idx + len(self.close_tag):]
                    self.in_thought = False

        return chunks

    def flush(self) -> list[StreamChunk]:
        """Flushes any remaining text in buffer at end of stream."""
        if not self.buffer:
            return []
        chunk = StreamChunk(is_thought=self.in_thought, content=self.buffer)
        self.buffer = ""
        return [chunk]

    @staticmethod
    def parse_full_text(text: str, open_tag: str = "<think>", close_tag: str = "</think>") -> tuple[str, str]:
        """
        Separates completed static text into (thought, answer).
        """
        pattern = re.compile(rf"{re.escape(open_tag)}(.*?){re.escape(close_tag)}", re.DOTALL)
        thoughts = []
        
        def _replace_thought(match: re.Match) -> str:
            thoughts.append(match.group(1).strip())
            return ""

        cleaned = pattern.sub(_replace_thought, text).strip()
        joined_thoughts = "\n\n".join(thoughts).strip()
        return joined_thoughts, cleaned

    def _matching_prefix_len(self, text: str, target: str) -> int:
        for length in range(min(len(text), len(target) - 1), 0, -1):
            if target.startswith(text[-length:]):
                return length
        return 0


class QwenToolAdapter:
    """
    Qwen-Agent ChatML tool call encoder and decoder.
    Matches Alibaba's qwen-agent specification:
    <|im_start|>assistant
    <tool_call>
    {"name": "bash", "arguments": {"command": "ls -la"}}
    </tool_call><|im_end|>
    """

    @staticmethod
    def encode_tools(tools_schema: list[dict[str, Any]]) -> str:
        """Formats OpenAPI tools into Qwen ChatML system prompt instructions."""
        formatted = ["# Tools\n\nYou have access to the following tools:"]
        for tool in tools_schema:
            formatted.append(f"## {tool.get('name')}\nDescription: {tool.get('description', '')}\nParameters: {json.dumps(tool.get('parameters', {}))}")
        formatted.append("\nTo call a tool, format your output as:\n<tool_call>\n{\"name\": \"tool_name\", \"arguments\": { ... }}\n</tool_call>")
        return "\n\n".join(formatted)

    @staticmethod
    def parse_tool_calls(text: str) -> list[dict[str, Any]]:
        """Extracts JSON tool calls from Qwen-Agent markup."""
        calls = []
        pattern = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.DOTALL)
        for match in pattern.finditer(text):
            try:
                raw_json = match.group(1).strip()
                parsed = json.loads(raw_json)
                calls.append(parsed)
            except Exception:
                continue
        return calls


class ZAIToolAdapter:
    """
    ZAI / GLM-5.3 tool protocol parser.
    Supports ZCode blocks:
    ```tool_call
    tool_name(param1="val1", param2=123)
    ```
    or structured JSON tool call blocks.
    """

    @staticmethod
    def parse_zcode_calls(text: str) -> list[dict[str, Any]]:
        calls = []
        # JSON block style
        json_pattern = re.compile(r"```tool_call\s*\n\s*({.*?})\s*```", re.DOTALL)
        for match in json_pattern.finditer(text):
            try:
                calls.append(json.loads(match.group(1).strip()))
            except Exception:
                pass

        # Pythonic call style: name(arg="val")
        fn_pattern = re.compile(r"```tool_call\s*\n\s*([a-zA-Z0-9_]+)\((.*?)\)\s*```", re.DOTALL)
        for match in fn_pattern.finditer(text):
            fn_name = match.group(1)
            raw_args = match.group(2).strip()
            # Try parsing key=value arguments
            kwargs = {}
            kv_pairs = re.findall(r'([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|([0-9]+|true|false))', raw_args, re.IGNORECASE)
            for k, s1, s2, lit in kv_pairs:
                val = s1 or s2 or lit
                if val.lower() == "true":
                    kwargs[k] = True
                elif val.lower() == "false":
                    kwargs[k] = False
                elif val.isdigit():
                    kwargs[k] = int(val)
                else:
                    kwargs[k] = val
            calls.append({"name": fn_name, "arguments": kwargs})

        return calls
