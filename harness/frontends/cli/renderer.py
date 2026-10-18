"""
CLI Renderer: formats and renders UI cards and event stream into Rich terminal components (§7).
"""
from __future__ import annotations

import json
from typing import Any

from rich.console import Console, RenderableType
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

from harness.sdk.cards import BUILTIN_CARD_KINDS, Card, fallback
from harness.sdk.events import Event


def render_card(card: Card, console: Console | None = None) -> RenderableType:
    """
    Renders any Card into a Rich Renderable.
    Enforces mandatory fallback rendering for unknown card kinds (§7).
    """
    if card.kind not in BUILTIN_CARD_KINDS:
        # Mandatory fallback: converts unknown card to pretty-printed JSON code card
        card = fallback(card)

    k = card.kind
    data = card.data
    renderable: RenderableType

    if k == "markdown":
        body = data.get("body", "")
        renderable = Panel(
            Markdown(body),
            title=card.title or "Markdown",
            border_style="cyan",
        )

    elif k == "code":
        code_body = data.get("body", "")
        lang = data.get("language", "text")
        path = data.get("path")
        title = card.title or (f"Code: {path}" if path else f"Code ({lang})")
        renderable = Panel(
            Syntax(code_body, lang, theme="monokai", line_numbers=True),
            title=title,
            border_style="bright_blue",
        )

    elif k == "diff":
        unified = data.get("unified", "")
        path = data.get("path", "")
        stats = data.get("stats", {})
        added = stats.get("added", 0)
        removed = stats.get("removed", 0)
        stats_str = f"+{added} / -{removed}"
        title = card.title or f"Diff: {path} ({stats_str})"
        renderable = Panel(
            Syntax(unified, "diff", theme="monokai", line_numbers=False),
            title=title,
            border_style="magenta",
        )

    elif k == "table":
        cols = data.get("columns", [])
        rows = data.get("rows", [])
        table = Table(title=card.title or "Data Table")
        for col in cols:
            table.add_column(str(col), style="cyan")
        for row in rows:
            table.add_row(*[str(cell) for cell in row])
        renderable = table

    elif k == "notice":
        body = data.get("body", "")
        level = data.get("level", "info")
        color_map = {
            "info": "blue",
            "warn": "yellow",
            "error": "red",
        }
        color = color_map.get(level, "blue")
        title = card.title or f"Notice [{level.upper()}]"
        renderable = Panel(
            Text(body, style=color),
            title=title,
            border_style=color,
        )

    elif k == "progress":
        label = data.get("label", "Progress")
        val = data.get("value")
        if val is not None:
            pct = int(val * 100)
            text = f"{label}: [{pct}%]"
        else:
            text = f"{label}..."
        renderable = Text(text, style="green")

    elif k == "tree":
        tree = Tree(card.title or "Tree View")

        def _add_nodes(parent: Tree, node_list: list[dict[str, Any]]) -> None:
            for item in node_list:
                label = item.get("label", "")
                children = item.get("children", [])
                sub_tree = parent.add(label)
                if children:
                    _add_nodes(sub_tree, children)

        _add_nodes(tree, data.get("nodes", []))
        renderable = tree

    elif k == "approval":
        app_id = data.get("approval_id", "")
        summary = data.get("summary", "")
        details = data.get("details", {})
        opts = data.get("options", ["approve", "deny"])
        details_str = json.dumps(details, indent=2) if details else ""
        content = f"[bold yellow]Approval ID:[/bold yellow] {app_id}\n[bold]{summary}[/bold]"
        if details_str:
            content += f"\n\n[dim]{details_str}[/dim]"
        content += f"\n\n[bold cyan]Options:[/bold cyan] {', '.join(opts)}"
        renderable = Panel(
            content,
            title=card.title or "Action Requires Approval",
            border_style="yellow",
        )

    elif k == "artifact":
        name = data.get("name", "")
        media_type = data.get("media_type", "")
        url = data.get("url", "")
        rf = data.get("request_feedback", False)
        rf_str = " (Feedback Requested)" if rf else ""
        content = (
            f"[bold cyan]Artifact:[/bold cyan] {name}{rf_str}\n"
            f"[dim]Type:[/dim] {media_type}\n"
            f"[dim]Location:[/dim] {url}"
        )
        renderable = Panel(
            content,
            title=card.title or f"Artifact: {name}",
            border_style="green",
        )

    elif k == "form":
        form_id = data.get("form_id", "")
        schema = data.get("schema", {})
        submit_label = data.get("submit_label", "Submit")
        schema_props = list(schema.get("properties", {}).keys())
        content = (
            f"[bold cyan]Form ID:[/bold cyan] {form_id}\n"
            f"[bold]Fields:[/bold] {', '.join(schema_props) if schema_props else 'none'}\n"
            f"[dim]Action:[/dim] {submit_label}"
        )
        renderable = Panel(
            content,
            title=card.title or f"Form: {form_id}",
            border_style="blue",
        )

    else:
        # Fallback for unexpected branches
        renderable = Text(f"[{card.kind}] {json.dumps(data)}", style="dim")

    if console is not None:
        console.print(renderable)

    return renderable


def render_event(event: Event | Any, console: Console | None = None) -> Any:
    """
    Renders an Event or kernel event into formatted console output.
    """
    event_type = getattr(event, "type", "")
    data = getattr(event, "data", {})

    if event_type == "model.text.delta":
        text = data.get("text", "")
        if console:
            console.print(text, end="")
        return text

    if event_type == "model.thinking.delta":
        text = data.get("text", "")
        if console:
            console.print(f"[dim italic]{text}[/dim italic]", end="")
        return text

    if event_type == "model.tool_call":
        call_id = data.get("call_id", "")
        name = data.get("name", "")
        args = data.get("arguments", {})
        args_str = json.dumps(args, indent=2)
        panel = Panel(
            f"[bold cyan]Tool:[/bold cyan] {name}\n[bold cyan]Call ID:[/bold cyan] {call_id}\n[dim]Arguments:[/dim]\n{args_str}",
            title="Model Requested Tool Call",
            border_style="blue",
        )
        if console:
            console.print(panel)
        return panel

    if event_type == "tool.decision":
        action = data.get("action", "")
        policy = data.get("policy", "")
        reason = data.get("reason", "")
        color = "green" if action == "allow" else "yellow" if action == "escalate" else "red"
        text = f"[{color}]PolicyGate [{action.upper()}][/] ({policy}): {reason}"
        if console:
            console.print(text)
        return text

    if event_type == "tool.result":
        name = data.get("name", "")
        is_err = data.get("is_error", False)
        preview = data.get("preview", "")
        dur = data.get("duration_ms", 0)
        style = "red" if is_err else "green"
        panel = Panel(
            f"[bold]Status:[/bold] [{'ERROR' if is_err else 'SUCCESS'}] in {dur:.1f}ms\n[dim]Preview:[/dim]\n{preview}",
            title=f"Tool Result: {name}",
            border_style=style,
        )
        if console:
            console.print(panel)
        return panel

    if event_type == "ui.card":
        card_dict = data.get("card", {})
        if card_dict:
            c = Card.from_dict(card_dict) if not isinstance(card_dict, Card) else card_dict
            return render_card(c, console=console)

    if event_type == "approval.required":
        app_id = data.get("approval_id", "")
        summary = data.get("summary", "")
        opts = data.get("options", ["approve", "deny"])
        panel = Panel(
            f"[bold yellow]Approval Required:[/bold yellow] {summary}\n[dim]ID: {app_id}[/dim]\n[cyan]Options: {', '.join(opts)}[/cyan]",
            title="Action Escalated for Approval",
            border_style="yellow",
        )
        if console:
            console.print(panel)
        return panel

    if event_type == "approval.resolved":
        approved = data.get("approved", False)
        by = data.get("by", "")
        reason = data.get("reason", "")
        color = "green" if approved else "red"
        status_text = f"[{color}]Approval {'ACCEPTED' if approved else 'REJECTED'}[/] by {by} ({reason})"
        if console:
            console.print(status_text)
        return status_text

    if event_type == "question.asked":
        questions = data.get("questions", [])
        lines = [f"[bold]{item.get('question', '')}[/bold]" for item in questions]
        panel = Panel("\n".join(lines), title="Model is waiting for your answer", border_style="cyan")
        if console:
            console.print(panel)
        return panel

    if event_type == "question.answered":
        status = data.get("status", "answered")
        text = "Question answered." if status == "answered" else f"Question {status}."
        if console:
            console.print(f"[dim]{text}[/dim]")
        return text

    if event_type == "cost.updated":
        turn_usd = data.get("turn_usd", 0.0)
        session_usd = data.get("session_usd", 0.0)
        cap = data.get("cap_usd", 5.0)
        text = f"[dim]Cost: turn=${turn_usd:.4f} | session=${session_usd:.4f} / cap=${cap:.2f}[/dim]"
        if console:
            console.print(text)
        return text

    if event_type == "turn.completed":
        reason = data.get("stop_reason", "end_turn")
        dur = data.get("duration_ms", 0)
        calls = data.get("tool_calls", 0)
        text = f"[bold green]✓ Turn Completed[/bold green] [dim]({reason}, {calls} tools, {dur:.0f}ms)[/dim]"
        if console:
            console.print(text)
        return text

    if event_type == "turn.error":
        msg = data.get("message", "")
        panel = Panel(f"[bold red]Turn Error:[/bold red] {msg}", border_style="red")
        if console:
            console.print(panel)
        return panel

    if event_type == "turn.cancelled":
        text = "[bold yellow]⚠ Turn Cancelled[/bold yellow]"
        if console:
            console.print(text)
        return text

    return None
