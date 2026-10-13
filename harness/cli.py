"""
Interactive CLI and TUI for Harness-Zero.
Supports interactive pair programming, one-shot turns, slash commands, and daemon mode.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from typing import Optional

import click
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from harness import __version__
from harness.core.coordinator import AgentCoordinator
from harness.core.daemon import HarnessDaemon
from harness.models.router import ModelRouter

console = Console()


@click.group(invoke_without_command=True)
@click.pass_context
@click.version_option(version=__version__, prog_name="harness-zero")
def main(ctx: click.Context):
    """Harness-Zero: Sovereign Autonomous Agent Engine."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(interactive)


@main.command()
@click.argument("prompt")
@click.option("--workspace", "-w", default=".", help="Workspace path")
@click.option("--provider", "-p", default="mock", help="Model provider (openai, anthropic, gemini, local, mock)")
@click.option("--model", "-m", default="mock-model", help="Model name")
def run(prompt: str, workspace: str, provider: str, model: str):
    """Execute a single autonomous turn."""
    asyncio.run(_run_single_turn(prompt, Path(workspace), provider, model))


async def _run_single_turn(prompt: str, workspace: Path, provider: str, model: str):
    router = ModelRouter(default_provider=provider, default_model=model)
    coordinator = AgentCoordinator(workspace=workspace, router=router)

    console.print(
        Panel(
            f"[bold cyan]User Prompt:[/bold cyan] {prompt}",
            title="[bold green]Harness-Zero[/bold green]",
            border_style="cyan",
        )
    )

    with console.status("[bold green]Agent executing turn...[/bold green]", spinner="dots"):
        result = await coordinator.execute_turn(prompt, provider=provider, model=model)

    console.print(Panel(Markdown(result), title="[bold magenta]Agent Response[/bold magenta]", border_style="green"))


@main.command()
@click.option("--workspace", "-w", default=".", help="Workspace path")
@click.option("--provider", "-p", default="mock", help="Model provider")
@click.option("--model", "-m", default="mock-model", help="Model name")
def interactive(workspace: str, provider: str, model: str):
    """Start an interactive pair-programming session with slash commands."""
    asyncio.run(_run_interactive(Path(workspace), provider, model))


async def _run_interactive(workspace: Path, initial_provider: str, initial_model: str):
    current_provider = initial_provider
    current_model = initial_model
    router = ModelRouter(default_provider=current_provider, default_model=current_model)
    coordinator = AgentCoordinator(workspace=workspace, router=router)

    console.print(
        Panel(
            f"[bold cyan]Harness-Zero v{__version__}[/bold cyan]\n"
            f"Workspace: [yellow]{coordinator.workspace}[/yellow]\n"
            f"Active Provider: [green]{current_provider}[/green] | Model: [green]{current_model}[/green]\n"
            f"Installed Skills: [magenta]{len(coordinator.skills.skills)}[/magenta]\n\n"
            f"Type [bold]/help[/bold] for commands or enter your prompt below.",
            title="[bold green]Sovereign Agent Engine[/bold green]",
            border_style="bright_blue",
        )
    )

    while True:
        try:
            user_input = console.input("[bold blue]harness>[/bold blue] ").strip()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[yellow]Exiting Harness-Zero session.[/yellow]")
            break

        if not user_input:
            continue

        if user_input in ("/exit", "/quit"):
            console.print("[yellow]Exiting Harness-Zero session.[/yellow]")
            break

        elif user_input == "/help":
            table = Table(title="Available Slash Commands")
            table.add_column("Command", style="cyan")
            table.add_column("Description", style="white")
            table.add_row("/skills", "List progressively discovered skills")
            table.add_row("/model <prov> <mdl>", "Switch active model and provider")
            table.add_row("/search <query>", "Instant search past turns with SQLite FTS5")
            table.add_row("/tasks", "List background tasks")
            table.add_row("/status", "Display session stats and transcript paths")
            table.add_row("/help", "Display this help menu")
            table.add_row("/exit", "Exit session")
            console.print(table)
            continue

        elif user_input == "/skills":
            table = Table(title="Installed Progressive Skills")
            table.add_column("Name", style="cyan")
            table.add_column("Tier", style="green")
            table.add_column("Description", style="white")
            for s in coordinator.skills.list_skills():
                table.add_row(s.name, s.tier, s.description)
            console.print(table)
            continue

        elif user_input.startswith("/model"):
            parts = user_input.split()
            if len(parts) >= 3:
                current_provider = parts[1]
                current_model = parts[2]
                console.print(f"[green]Switched model to provider '{current_provider}' and model '{current_model}'[/green]")
            else:
                console.print(f"[yellow]Current model: {current_provider}:{current_model}. Usage: /model <provider> <model>[/yellow]")
            continue

        elif user_input.startswith("/search"):
            query = user_input[7:].strip()
            results = coordinator.memory.search(query)
            if not results:
                console.print("[yellow]No memory matches found.[/yellow]")
            else:
                table = Table(title=f"Memory Search Results for '{query}'")
                table.add_column("Step", style="cyan")
                table.add_column("Snippet", style="white")
                table.add_column("Rank", style="green")
                for r in results:
                    table.add_row(str(r.step_index), r.snippet, f"{r.rank:.2f}")
                console.print(table)
            continue

        elif user_input == "/tasks":
            tasks = coordinator.executor.list_tasks()
            if not tasks:
                console.print("[yellow]No background tasks currently tracked.[/yellow]")
            else:
                table = Table(title="Background Tasks")
                table.add_column("Task ID", style="cyan")
                table.add_column("Status", style="green")
                table.add_column("Command", style="white")
                for t in tasks:
                    table.add_row(t["task_id"], t["status"], t["command"])
                console.print(table)
            continue

        elif user_input == "/status":
            console.print(
                Panel(
                    f"Session ID: {coordinator.session_id}\n"
                    f"Session Dir: {coordinator.session_dir}\n"
                    f"Step Count: {coordinator.transcripts.step_counter}\n"
                    f"Full Log: {coordinator.transcripts.full_file}\n"
                    f"Compact Log: {coordinator.transcripts.compact_file}\n"
                    f"Memory DB: {coordinator.memory.db_path}",
                    title="Session Status",
                    border_style="magenta",
                )
            )
            continue

        # Standard Agent Turn
        with console.status("[bold green]Agent thinking and executing tools...[/bold green]", spinner="dots"):
            try:
                response = await coordinator.execute_turn(
                    user_input,
                    provider=current_provider,
                    model=current_model,
                )
                console.print(Panel(Markdown(response), title="[bold magenta]Agent Response[/bold magenta]", border_style="green"))
            except Exception as e:
                console.print(f"[bold red]Error executing turn: {e}[/bold red]")


@main.command()
@click.option("--workspace", "-w", default=".", help="Workspace path")
@click.option("--port", "-p", default=8080, help="HTTP port")
@click.option("--host", "-h", default="127.0.0.1", help="HTTP host")
def daemon(workspace: str, port: int, host: str):
    """Run the background daemon IPC server."""
    asyncio.run(_run_daemon(Path(workspace), host, port))


async def _run_daemon(workspace: Path, host: str, port: int):
    coordinator = AgentCoordinator(workspace=workspace)
    d = HarnessDaemon(coordinator=coordinator, host=host, port=port)
    console.print(f"[bold green]Starting Harness-Zero daemon on http://{host}:{port}...[/bold green]")
    api_runner, sandbox_runner = await d.start()
    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, asyncio.CancelledError):
        console.print("[yellow]Stopping daemon...[/yellow]")
        await api_runner.cleanup()
        await sandbox_runner.cleanup()



@main.command()
@click.option("--workspace", "-w", default=".", help="Workspace path")
@click.option("--port", "-p", default=8000, help="HTTP port")
@click.option("--host", "-h", default="127.0.0.1", help="HTTP host")
def url(workspace: str, port: int, host: str):
    """Generate a single-use 3-minute login link (Claude Cowork pattern)."""
    coordinator = AgentCoordinator(workspace=Path(workspace))
    d = HarnessDaemon(coordinator=coordinator, host=host, port=port)
    login_url = d.generate_login_url()
    console.print(Panel(
        f"[bold green]Single-Use Remote Login Link:[/bold green]\n{login_url}\n\n"
        "[yellow]Link expires in 3 minutes. Paste into browser to connect over SSH / remote session.[/yellow]",
        title="[bold cyan]Harness-Zero Remote Access[/bold cyan]",
        border_style="green",
    ))


@main.command()
@click.option("--workspace", "-w", default=".", help="Workspace path")
@click.option("--port", "-p", default=8000, help="HTTP port")
@click.option("--sandbox-port", "-s", default=8001, help="Sandbox port")
@click.option("--host", "-h", default="127.0.0.1", help="HTTP host")
@click.option("--no-open", is_flag=True, help="Don't open browser automatically")
def ui(workspace: str, port: int, sandbox_port: int, host: str, no_open: bool):
    """Launch the full-featured Sovereign Agent Studio Web UI."""
    asyncio.run(_run_ui(Path(workspace), host, port, sandbox_port, not no_open))


async def _run_ui(workspace: Path, host: str, port: int, sandbox_port: int, open_browser: bool):
    import webbrowser
    coordinator = AgentCoordinator(workspace=workspace)
    d = HarnessDaemon(coordinator=coordinator, host=host, port=port, sandbox_port=sandbox_port)
    console.print(Panel(
        f"[bold green]Harness-Zero Sovereign Agent Studio[/bold green]\n\n"
        f"• Core UI & API: [bold cyan]http://{host}:{port}/[/bold cyan]\n"
        f"• SOP Preview Sandbox: [bold magenta]http://{host}:{sandbox_port}/[/bold magenta]\n"
        f"• Workspace: [yellow]{coordinator.workspace}[/yellow]\n",
        title="[bold green]UI Online[/bold green]",
        border_style="green",
    ))
    api_runner, sandbox_runner = await d.start()
    if open_browser:
        webbrowser.open(f"http://{host}:{port}/")
    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, asyncio.CancelledError):
        console.print("[yellow]Stopping UI Studio...[/yellow]")
        await api_runner.cleanup()
        await sandbox_runner.cleanup()


if __name__ == "__main__":
    main()
