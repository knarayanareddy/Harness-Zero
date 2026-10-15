"""
CLI Application Frontend: manages turn loop, live event bus rendering, approvals,
and slash command dispatch (§1, §7).
"""
from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from harness.kernel.bus import EventBus
from harness.kernel.commands import Commands
from harness.sdk.types import TurnResult

from .prompts import prompt_approval
from .renderer import render_event


class CliApp:
    """
    Command Line Interface frontend driving turns and sessions via Commands API.
    Interacts with the kernel strictly over Commands and EventBus.
    """

    def __init__(
        self,
        commands: Commands,
        bus: EventBus | None = None,
        console: Console | None = None,
        interactive: bool = True,
        session_id: str | None = None,
        input_fn: Callable[[str], str] | None = None,
    ) -> None:
        self.commands = commands
        self.bus = bus
        self.console = console or Console()
        self.interactive = interactive
        self.session_id = session_id or f"session_{uuid.uuid4().hex[:12]}"
        self.input_fn = input_fn or input
        self._listener_task: asyncio.Task | None = None

    async def start_event_listener(self) -> None:
        """Starts background subscriber loop on EventBus."""
        if not self.bus or self._listener_task is not None:
            return

        sub_queue = self.bus.subscribe(interactive=self.interactive)
        self._sub = sub_queue

        async def _consume():
            try:
                while True:
                    event = await sub_queue.get()
                    try:
                        render_event(event, self.console)

                        # Auto-prompt approval when in interactive mode
                        if event.type == "approval.required" and self.interactive:
                            data = event.data
                            app_id = data.get("approval_id", "")
                            summary = data.get("summary", "")
                            details = data.get("details", {})
                            options = data.get("options", ["approve", "deny"])
                            approved = prompt_approval(
                                approval_id=app_id,
                                summary=summary,
                                details=details,
                                options=options,
                                console=self.console,
                                input_fn=self.input_fn,
                            )
                            await self.commands.resolve_approval(
                                approval_id=app_id,
                                approved=approved,
                                by="user",
                            )
                    except Exception as e:
                        self.console.print(f"[red]Error rendering event {event.type}: {e}[/red]")
            except asyncio.CancelledError:
                pass

        self._listener_task = asyncio.create_task(_consume())

    async def stop_event_listener(self) -> None:
        """Stops background subscriber loop and unregisters."""
        if self._listener_task:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass
            self._listener_task = None

        if hasattr(self, "_sub") and self._sub and self.bus:
            self.bus.unsubscribe(self._sub.id)
            self._sub = None

    async def run_turn(
        self,
        prompt: str,
        session_id: str | None = None,
        options: dict[str, Any] | None = None,
    ) -> TurnResult:
        """Executes a single turn with live event streaming."""
        sid = session_id or self.session_id
        await self.start_event_listener()
        try:
            res = await self.commands.submit_turn(
                session_id=sid,
                prompt=prompt,
                options=options,
            )
            return res
        finally:
            # Let pending events drain briefly
            await asyncio.sleep(0.05)
            await self.stop_event_listener()

    async def run_interactive(self, session_id: str | None = None) -> None:
        """Interactive REPL driving turns and slash commands."""
        if session_id:
            self.session_id = session_id

        self.console.print(
            Panel(
                f"[bold cyan]Harness-Zero Kernel CLI Frontend[/bold cyan]\n"
                f"Session: [yellow]{self.session_id}[/yellow]\n"
                f"Workspace: [dim]{self.commands.workspace}[/dim]\n\n"
                f"Type [bold]/help[/bold] for commands or enter your prompt below.",
                title="[bold green]Kernel Interactive Session[/bold green]",
                border_style="bright_blue",
            )
        )

        await self.start_event_listener()
        try:
            while True:
                try:
                    user_input = self.input_fn("harness> ").strip()
                except (KeyboardInterrupt, EOFError):
                    self.console.print("\n[yellow]Exiting Harness-Zero session.[/yellow]")
                    break

                if not user_input:
                    continue

                if user_input in ("/exit", "/quit"):
                    self.console.print("[yellow]Exiting Harness-Zero session.[/yellow]")
                    break

                if user_input == "/help":
                    table = Table(title="Available Slash Commands")
                    table.add_column("Command", style="cyan")
                    table.add_column("Description", style="white")
                    table.add_row("/plugins", "List loaded and discovered plugins")
                    table.add_row("/models", "List available provider models")
                    table.add_row("/sessions", "List past and active sessions")
                    table.add_row("/switch <id>", "Switch active session")
                    table.add_row("/economics", "Display turn and session spend totals")
                    table.add_row("/cancel", "Cancel currently active turn")
                    table.add_row("/help", "Display this help menu")
                    table.add_row("/exit", "Exit session")
                    self.console.print(table)
                    continue

                if user_input == "/plugins":
                    plugins = await self.commands.plugins_list()
                    table = Table(title="Installed Plugins")
                    table.add_column("Name", style="cyan")
                    table.add_column("Version", style="dim")
                    table.add_column("Tier", style="green")
                    table.add_column("Provides", style="yellow")
                    table.add_column("Loaded", style="magenta")
                    for p in plugins:
                        table.add_row(
                            p["name"],
                            p["version"],
                            p["tier"],
                            ", ".join(p["provides"]),
                            "✓" if p["loaded"] else "✗",
                        )
                    self.console.print(table)
                    continue

                if user_input == "/models":
                    models = await self.commands.models_list()
                    table = Table(title="Available Models")
                    table.add_column("Provider", style="cyan")
                    table.add_column("ID", style="green")
                    table.add_column("Context", style="dim")
                    for m in models:
                        table.add_row(m.get("provider", ""), m.get("id", ""), str(m.get("context_tokens", "")))
                    self.console.print(table)
                    continue

                if user_input == "/sessions":
                    sessions = await self.commands.sessions_list()
                    table = Table(title="Sessions")
                    table.add_column("Session ID", style="cyan")
                    table.add_column("Created", style="dim")
                    for s in sessions:
                        active = " (active)" if s.get("session_id") == self.session_id else ""
                        table.add_row(f"{s.get('session_id')}{active}", str(s.get("created_at", "")))
                    self.console.print(table)
                    continue

                if user_input.startswith("/switch"):
                    parts = user_input.split()
                    if len(parts) >= 2:
                        target_id = parts[1]
                        try:
                            await self.commands.sessions_switch(target_id)
                            self.session_id = target_id
                            self.console.print(f"[green]Switched active session to '{target_id}'[/green]")
                        except Exception as e:
                            self.console.print(f"[red]Error switching session: {e}[/red]")
                    else:
                        self.console.print("[yellow]Usage: /switch <session_id>[/yellow]")
                    continue

                if user_input == "/economics":
                    # EconomicsSnapshot: {turn_usd, session_usd, lifetime_usd, cap_usd, unknown_pricing, usage}
                    econ = await self.commands.economics(self.session_id)
                    cap = econ.get("cap_usd")
                    usage = econ.get("usage") or {}
                    cap_line = f"Spend Cap (per turn): ${cap:.2f}" if cap is not None else "Spend Cap (per turn): none"
                    unknown = "  (pricing unknown for some calls — lower bound)" if econ.get("unknown_pricing") else ""
                    self.console.print(Panel(
                        f"{cap_line}\n"
                        f"Session Cost: ${econ.get('session_usd', 0.0):.4f}{unknown}\n"
                        f"Workspace Lifetime Cost: ${econ.get('lifetime_usd', 0.0):.4f}\n"
                        f"Input Tokens: {usage.get('input_tokens', 0)}\n"
                        f"Output Tokens: {usage.get('output_tokens', 0)}",
                        title="Session Economics",
                        border_style="cyan",
                    ))
                    continue

                if user_input == "/cancel":
                    ok = await self.commands.cancel_turn(self.session_id)
                    if ok:
                        self.console.print("[yellow]Cancellation signal sent.[/yellow]")
                    else:
                        self.console.print("[dim]No active turn running to cancel.[/dim]")
                    continue

                # Standard prompt submission
                try:
                    await self.commands.submit_turn(
                        session_id=self.session_id,
                        prompt=user_input,
                    )
                except Exception as e:
                    self.console.print(f"[bold red]Turn Execution Error: {e}[/bold red]")

        finally:
            await self.stop_event_listener()
