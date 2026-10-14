"""
UI Timeline Cards for Harness-Zero SDK.
Typed timeline cards with validation and safe frontend fallback rendering.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

BUILTIN_CARD_KINDS = frozenset({
    "markdown",
    "code",
    "diff",
    "table",
    "notice",
    "progress",
    "tree",
    "approval",
    "artifact",
    "form",
})

NoticeLevel = Literal["info", "warn", "error"]


@dataclass(slots=True)
class Card:
    kind: str
    id: str = field(default_factory=lambda: f"card_{uuid.uuid4().hex[:8]}")
    title: str | None = None
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "id": self.id,
            "title": self.title,
            "data": self.data,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Card:
        card = cls(
            kind=data.get("kind", ""),
            id=data.get("id") or f"card_{uuid.uuid4().hex[:8]}",
            title=data.get("title"),
            data=data.get("data", {}),
        )
        return validate_card(card)

    # Convenience builder methods
    @classmethod
    def markdown(cls, body: str, title: str | None = None) -> Card:
        return validate_card(cls(kind="markdown", title=title, data={"body": body}))

    @classmethod
    def code(cls, body: str, language: str = "text", path: str | None = None, title: str | None = None) -> Card:
        d: dict[str, Any] = {"language": language, "body": body}
        if path:
            d["path"] = path
        return validate_card(cls(kind="code", title=title, data=d))

    @classmethod
    def diff(cls, path: str, unified: str, added: int = 0, removed: int = 0, title: str | None = None) -> Card:
        return validate_card(
            cls(
                kind="diff",
                title=title,
                data={"path": path, "unified": unified, "stats": {"added": added, "removed": removed}},
            )
        )

    @classmethod
    def table(cls, columns: list[str], rows: list[list[Any]], title: str | None = None) -> Card:
        return validate_card(cls(kind="table", title=title, data={"columns": columns, "rows": rows}))

    @classmethod
    def notice(cls, body: str, level: NoticeLevel = "info", title: str | None = None) -> Card:
        return validate_card(cls(kind="notice", title=title, data={"level": level, "body": body}))

    @classmethod
    def progress(cls, label: str, value: float | None = None, title: str | None = None) -> Card:
        return validate_card(cls(kind="progress", title=title, data={"label": label, "value": value}))

    @classmethod
    def tree(cls, nodes: list[dict[str, Any]], title: str | None = None) -> Card:
        return validate_card(cls(kind="tree", title=title, data={"nodes": nodes}))

    @classmethod
    def approval(cls, approval_id: str, summary: str, details: dict[str, Any], options: list[str] | None = None, title: str | None = None) -> Card:
        return validate_card(
            cls(
                kind="approval",
                title=title,
                data={
                    "approval_id": approval_id,
                    "summary": summary,
                    "details": details,
                    "options": options or ["approve", "deny"],
                },
            )
        )

    @classmethod
    def artifact(cls, name: str, media_type: str, url: str, request_feedback: bool = False, title: str | None = None) -> Card:
        return validate_card(
            cls(
                kind="artifact",
                title=title,
                data={
                    "name": name,
                    "media_type": media_type,
                    "url": url,
                    "request_feedback": request_feedback,
                },
            )
        )

    @classmethod
    def form(cls, form_id: str, schema: dict[str, Any], submit_label: str = "Submit", title: str | None = None) -> Card:
        return validate_card(
            cls(
                kind="form",
                title=title,
                data={"form_id": form_id, "schema": schema, "submit_label": submit_label},
            )
        )


def validate_card(card: Card) -> Card:
    """
    Validates that a Card has all required fields for its declared kind.
    Raises ValueError on missing or malformed required fields.
    Custom plugin cards (e.g. 'x-plugin-widget') pass through unconstrained.
    """
    if not card.kind:
        raise ValueError("Card 'kind' cannot be empty")

    data = card.data
    k = card.kind

    if k == "markdown":
        if "body" not in data or not isinstance(data["body"], str):
            raise ValueError("Card 'markdown' missing required string field 'body'")

    elif k == "code":
        if "body" not in data or not isinstance(data["body"], str):
            raise ValueError("Card 'code' missing required string field 'body'")
        if "language" not in data or not isinstance(data["language"], str):
            raise ValueError("Card 'code' missing required string field 'language'")

    elif k == "diff":
        if "path" not in data or not isinstance(data["path"], str):
            raise ValueError("Card 'diff' missing required string field 'path'")
        if "unified" not in data or not isinstance(data["unified"], str):
            raise ValueError("Card 'diff' missing required string field 'unified'")
        if "stats" not in data or not isinstance(data["stats"], dict):
            raise ValueError("Card 'diff' missing required dict field 'stats'")
        stats = data["stats"]
        if "added" not in stats or "removed" not in stats:
            raise ValueError("Card 'diff' stats must contain 'added' and 'removed' integers")

    elif k == "table":
        if "columns" not in data or not isinstance(data["columns"], list):
            raise ValueError("Card 'table' missing required list field 'columns'")
        if "rows" not in data or not isinstance(data["rows"], list):
            raise ValueError("Card 'table' missing required list field 'rows'")

    elif k == "notice":
        if "body" not in data or not isinstance(data["body"], str):
            raise ValueError("Card 'notice' missing required string field 'body'")
        level = data.get("level", "info")
        if level not in ("info", "warn", "error"):
            raise ValueError(f"Card 'notice' level must be 'info', 'warn', or 'error', got '{level}'")

    elif k == "progress":
        if "label" not in data or not isinstance(data["label"], str):
            raise ValueError("Card 'progress' missing required string field 'label'")
        val = data.get("value")
        if val is not None:
            if not isinstance(val, (int, float)) or not (0.0 <= float(val) <= 1.0):
                raise ValueError("Card 'progress' value must be between 0.0 and 1.0 or None")

    elif k == "tree":
        if "nodes" not in data or not isinstance(data["nodes"], list):
            raise ValueError("Card 'tree' missing required list field 'nodes'")

    elif k == "approval":
        for req in ("approval_id", "summary", "details", "options"):
            if req not in data:
                raise ValueError(f"Card 'approval' missing required field '{req}'")
        if not isinstance(data["options"], list):
            raise ValueError("Card 'approval' options must be a list of strings")

    elif k == "artifact":
        for req in ("name", "media_type", "url"):
            if req not in data or not isinstance(data[req], str):
                raise ValueError(f"Card 'artifact' missing required string field '{req}'")
        if "request_feedback" not in data or not isinstance(data["request_feedback"], bool):
            raise ValueError("Card 'artifact' missing required boolean field 'request_feedback'")

    elif k == "form":
        for req in ("form_id", "schema", "submit_label"):
            if req not in data:
                raise ValueError(f"Card 'form' missing required field '{req}'")
        if not isinstance(data["schema"], dict):
            raise ValueError("Card 'form' schema must be a JSON Schema dict")

    return card


def fallback(card: Card) -> Card:
    """
    Mandatory fallback renderer: converts any unknown or custom plugin card
    into a safe code card showing title and pretty-printed JSON data.
    """
    title = card.title or f"Unsupported card ({card.kind})"
    content = json.dumps(card.data, indent=2, ensure_ascii=False)
    return Card(
        kind="code",
        id=card.id,
        title=title,
        data={"language": "json", "body": content},
    )
