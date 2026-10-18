"""
CLI interactive prompts for approvals and forms (§7).
"""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from rich.console import Console
from rich.panel import Panel


def prompt_approval(
    approval_id: str,
    summary: str,
    details: dict[str, Any] | None = None,
    options: list[str] | None = None,
    console: Console | None = None,
    input_fn: Callable[[str], str] | None = None,
) -> bool:
    """
    Prompts the user to approve or deny an escalated action.
    Returns True if approved, False otherwise.
    """
    c = console or Console()
    ask = input_fn or input

    details_str = json.dumps(details or {}, indent=2) if details else ""
    content = (
        f"[bold yellow]Approval Request [id={approval_id}][/bold yellow]\n\n"
        f"[bold]{summary}[/bold]"
    )
    if details_str:
        content += f"\n\n[dim]Details:\n{details_str}[/dim]"

    c.print(Panel(content, border_style="yellow", title="Human Approval Required"))

    opts = options or ["approve", "deny"]
    prompt_str = f"Action options ({'/'.join(opts)}) [default: {opts[0]}]: "

    try:
        choice = ask(prompt_str).strip().lower()
        if not choice:
            choice = opts[0].lower()

        if choice in ("y", "yes", "approve", "a"):
            c.print("[green]✓ Action Approved[/green]")
            return True
        else:
            c.print("[red]✗ Action Denied[/red]")
            return False
    except (EOFError, KeyboardInterrupt):
        c.print("\n[yellow]Approval prompt aborted; denying action.[/yellow]")
        return False


def prompt_question(
    interaction_id: str,
    questions: list[dict[str, Any]],
    console: Console | None = None,
    input_fn: Callable[[str], str] | None = None,
) -> dict[str, dict[str, str]] | None:
    """Prompt for structured question answers; ``None`` signals that the prompt was aborted."""
    c = console or Console()
    ask = input_fn or input
    c.print(Panel(
        f"[bold cyan]Question Request [id={interaction_id}][/bold cyan]",
        border_style="cyan",
        title="The model needs your input",
    ))
    answers: dict[str, dict[str, str]] = {}
    for question in questions:
        c.print(f"\n[bold]{question['question']}[/bold]")
        options = question.get("options", [])
        for index, option in enumerate(options, start=1):
            description = f" — {option['description']}" if option.get("description") else ""
            c.print(f"  [cyan]{index}.[/cyan] {option['label']}[dim]{description}[/dim]")
        allow_custom = question.get("allow_custom", True)
        prompt = "Choose an option by number"
        if allow_custom:
            prompt += " (or c for a custom answer)"
        prompt += ": "
        try:
            choice = ask(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            c.print("\n[yellow]Question prompt aborted; cancelling the turn.[/yellow]")
            return None

        if allow_custom and choice.lower() in {"c", "custom"}:
            try:
                custom_text = ask("Your answer: ").strip()
            except (EOFError, KeyboardInterrupt):
                c.print("\n[yellow]Question prompt aborted; cancelling the turn.[/yellow]")
                return None
            answers[question["id"]] = {"option_id": "custom", "custom_text": custom_text}
            continue

        selected = None
        if choice.isdigit() and 1 <= int(choice) <= len(options):
            selected = options[int(choice) - 1]
        else:
            selected = next(
                (option for option in options if choice.casefold() in {
                    str(option.get("id", "")).casefold(), str(option.get("label", "")).casefold()
                }),
                None,
            )
        if selected is None:
            c.print("[red]Invalid choice; the turn will be cancelled.[/red]")
            return None
        custom_text = ""
        if allow_custom:
            try:
                custom_text = ask("Optional custom detail (press Enter to skip): ").strip()
            except (EOFError, KeyboardInterrupt):
                c.print("\n[yellow]Question prompt aborted; cancelling the turn.[/yellow]")
                return None
        answers[question["id"]] = {
            "option_id": str(selected["id"]),
            "custom_text": custom_text,
        }
    c.print("[green]✓ Answers submitted[/green]")
    return answers


def prompt_form(
    form_id: str,
    schema: dict[str, Any],
    submit_label: str = "Submit",
    console: Console | None = None,
    input_fn: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    """
    Prompts the user to fill out fields declared in a form schema.
    Returns the submitted dictionary of inputs.
    """
    c = console or Console()
    ask = input_fn or input

    c.print(Panel(
        f"[bold cyan]Form:[/bold cyan] {form_id}\n[dim]Please provide values for the required parameters below:[/dim]",
        border_style="cyan",
        title="Form Input Request",
    ))

    properties: dict[str, Any] = schema.get("properties", {})
    required: list[str] = schema.get("required", [])
    results: dict[str, Any] = {}

    for prop_name, prop_meta in properties.items():
        desc = prop_meta.get("description", "")
        default = prop_meta.get("default", "")
        is_req = prop_name in required

        req_indicator = " [required]" if is_req else " [optional]"
        desc_str = f" ({desc})" if desc else ""
        prompt_label = f"  {prop_name}{req_indicator}{desc_str}: "

        while True:
            try:
                val = ask(prompt_label).strip()
            except (EOFError, KeyboardInterrupt):
                c.print("\n[yellow]Form input aborted.[/yellow]")
                return results

            if not val and default != "":
                val = default

            if is_req and not val:
                c.print("  [red]This field is required. Please enter a value.[/red]")
                continue

            results[prop_name] = val
            break

    c.print(f"[green]✓ Form submitted ({submit_label})[/green]")
    return results
