"""
Interactive CLI and TUI for Harness-Zero.
Supports interactive pair programming, one-shot turns, slash commands, and plugin management.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import click
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from harness import __version__
from harness.frontends.cli import CliApp
from harness.kernel.bootstrap import bootstrap_kernel

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
@click.option("--provider", "-p", default=None, help="Model provider (openai, anthropic, gemini, local, mock)")
@click.option("--model", "-m", default=None, help="Model name")
@click.option("--kernel", is_flag=True, default=True, help="Use modular kernel runtime (default: True)")
@click.option("--verbose", "-v", is_flag=True, default=False, help="Verbose logging")
@click.option("--log-file", default=None, help="Log file path")
def run(
    prompt: str,
    workspace: str,
    provider: str | None,
    model: str | None,
    kernel: bool,
    verbose: bool,
    log_file: str | None,
):
    """Execute a single autonomous turn via modular kernel."""
    asyncio.run(_run_kernel_turn(prompt, Path(workspace), provider, model, verbose, log_file))


async def _run_kernel_turn(
    prompt: str,
    workspace: Path,
    provider: str | None,
    model: str | None,
    verbose: bool,
    log_file: str | None,
):
    try:
        runtime = await bootstrap_kernel(
            workspace=workspace,
            log_file=Path(log_file) if log_file else None,
            verbose=verbose,
        )
        app = CliApp(commands=runtime.commands, bus=runtime.bus, interactive=False)
        options: dict[str, Any] = {}
        if provider:
            options["provider"] = provider
        if model:
            options["model"] = model

        result = await app.run_turn(prompt, options=options)
        if result.output:
            console.print(Panel(Markdown(result.output), title="[bold magenta]Agent Response[/bold magenta]", border_style="green"))
    except Exception as e:
        console.print(f"[bold red]Kernel execution error: {e}[/bold red]")


@main.command()
@click.option("--workspace", "-w", default=".", help="Workspace path")
@click.option("--provider", "-p", default=None, help="Model provider")
@click.option("--model", "-m", default=None, help="Model name")
@click.option("--kernel", is_flag=True, default=True, help="Use modular kernel runtime (default: True)")
@click.option("--verbose", "-v", is_flag=True, default=False, help="Verbose logging")
@click.option("--log-file", default=None, help="Log file path")
def interactive(
    workspace: str,
    provider: str | None,
    model: str | None,
    kernel: bool,
    verbose: bool,
    log_file: str | None,
):
    """Start an interactive pair-programming session with slash commands."""
    asyncio.run(_run_kernel_interactive(Path(workspace), provider, model, verbose, log_file))


async def _run_kernel_interactive(
    workspace: Path,
    provider: str | None,
    model: str | None,
    verbose: bool,
    log_file: str | None,
):
    try:
        runtime = await bootstrap_kernel(
            workspace=workspace,
            log_file=Path(log_file) if log_file else None,
            verbose=verbose,
        )
        if provider:
            runtime.settings.set("kernel.provider", provider)
        if model:
            runtime.settings.set("kernel.model", model)

        app = CliApp(commands=runtime.commands, bus=runtime.bus, interactive=True)
        await app.run_interactive()
    except Exception as e:
        console.print(f"[bold red]Kernel interactive session error: {e}[/bold red]")


@main.command()
@click.option("--workspace", "-w", default=".", help="Workspace path")
@click.option("--port", "-p", default=8000, help="HTTP port")
@click.option("--sandbox-port", "-s", default=8001, help="Sandbox port")
@click.option("--host", "-h", default="127.0.0.1", help="HTTP host")
@click.option("--allow-host", "-a", multiple=True, help="Allowed Host header hostnames (repeatable)")
def daemon(workspace: str, port: int, sandbox_port: int, host: str, allow_host: tuple[str, ...]):
    """Run the background daemon IPC server."""
    console.print(Panel(
        "[yellow]Harness-Zero daemon server is unavailable during modular kernel cutover.\n"
        "Web and daemon frontends are being rebuilt in Phase 4 (WEB-01).\n"
        "Please use [bold]harness interactive[/bold] or [bold]harness run[/bold].[/yellow]",
        title="[bold yellow]Migration Notice[/bold yellow]",
        border_style="yellow",
    ))


@main.command()
@click.option("--workspace", "-w", default=".", help="Workspace path")
def url(workspace: str):
    """Print the daemon URL with embedded auth token from .harness/daemon.json."""
    daemon_file = Path(workspace).resolve() / ".harness" / "daemon.json"
    if not daemon_file.exists():
        console.print("[bold red]Error: Daemon is not running or .harness/daemon.json not found.[/bold red]")
        sys.exit(1)
    try:
        data = json.loads(daemon_file.read_text(encoding="utf-8"))
        port = data.get("port", 8000)
        token = data.get("token", "")
        pid = data.get("pid")
        if pid:
            try:
                os.kill(pid, 0)
            except OSError:
                console.print(f"[yellow]Warning: Daemon PID {pid} is not running.[/yellow]")
        login_url = f"http://127.0.0.1:{port}/?token={token}"
        console.print(Panel(
            f"[bold green]Harness-Zero Web UI URL:[/bold green]\n{login_url}\n",
            title="[bold cyan]Harness-Zero Remote Access[/bold cyan]",
            border_style="green",
        ))
    except Exception as e:
        console.print(f"[bold red]Failed to read daemon metadata: {e}[/bold red]")
        sys.exit(1)


@main.command()
@click.option("--workspace", "-w", default=".", help="Workspace path")
@click.option("--port", "-p", default=8000, help="HTTP port")
@click.option("--sandbox-port", "-s", default=8001, help="Sandbox port")
@click.option("--host", "-h", default="127.0.0.1", help="HTTP host")
@click.option("--allow-host", "-a", multiple=True, help="Allowed Host header hostnames (repeatable)")
@click.option("--no-open", is_flag=True, help="Don't open browser automatically")
def ui(workspace: str, port: int, sandbox_port: int, host: str, allow_host: tuple[str, ...], no_open: bool):
    """Launch the Sovereign Agent Studio Web UI."""
    console.print(Panel(
        "[yellow]Harness-Zero Sovereign Agent Studio is unavailable during modular kernel cutover.\n"
        "Web and daemon frontends are being rebuilt in Phase 4 (WEB-01).\n"
        "Please use [bold]harness interactive[/bold] or [bold]harness run[/bold].[/yellow]",
        title="[bold yellow]Migration Notice[/bold yellow]",
        border_style="yellow",
    ))


@main.group()
def events():
    """Event bus schemas and inspection."""
    pass


@events.command(name="schema")
def events_schema():
    """Output JSON schema for Harness-Zero event envelopes."""
    from harness.sdk.events import schema
    click.echo(json.dumps(schema(), indent=2))


@main.group()
def plugin():
    """Manage plugins (list, info, enable, disable)."""
    pass


@plugin.command(name="list")
@click.option("--workspace", "-w", default=".", help="Workspace path")
def plugin_list(workspace: str):
    """List all discovered plugins and their load status."""
    async def _do():
        runtime = await bootstrap_kernel(workspace=workspace)
        plugins = await runtime.commands.plugins_list()
        table = Table(title="Discovered Plugins")
        table.add_column("Name", style="cyan")
        table.add_column("Version", style="dim")
        table.add_column("Tier", style="green")
        table.add_column("Provides", style="yellow")
        table.add_column("Capabilities", style="blue")
        table.add_column("Loaded", style="magenta")
        for p in plugins:
            table.add_row(
                p["name"],
                p["version"],
                p["tier"],
                ", ".join(p["provides"]),
                ", ".join(p["capabilities"]),
                "✓" if p["loaded"] else "✗",
            )
        console.print(table)
    asyncio.run(_do())


@plugin.command(name="info")
@click.argument("name")
@click.option("--workspace", "-w", default=".", help="Workspace path")
def plugin_info(name: str, workspace: str):
    """Display manifest and capability details for a plugin."""
    async def _do():
        runtime = await bootstrap_kernel(workspace=workspace)
        info = await runtime.commands.plugins_info(name)
        if not info:
            console.print(f"[red]Plugin '{name}' not found.[/red]")
            return
        console.print(Panel(
            f"[bold cyan]Plugin:[/bold cyan] {info['name']} (v{info['version']})\n"
            f"[bold]Summary:[/bold] {info['summary']}\n"
            f"[bold]Tier:[/bold] {info['tier']}\n"
            f"[bold]Provides:[/bold] {', '.join(info['provides'])}\n"
            f"[bold]Capabilities:[/bold] {', '.join(info['capabilities'])}\n"
            f"[bold]Loaded:[/bold] {'Yes' if info['loaded'] else 'No'}\n"
            f"[bold]Path:[/bold] {info['path']}",
            title=f"Plugin Info: {name}",
            border_style="cyan",
        ))
    asyncio.run(_do())


@plugin.command(name="enable")
@click.argument("name")
@click.option("--workspace", "-w", default=".", help="Workspace path")
def plugin_enable(name: str, workspace: str):
    """Enable a disabled plugin."""
    async def _do():
        runtime = await bootstrap_kernel(workspace=workspace)
        ok = await runtime.commands.plugins_enable(name)
        if ok:
            console.print(f"[green]✓ Plugin '{name}' enabled.[/green]")
        else:
            console.print(f"[yellow]Plugin '{name}' was not disabled.[/yellow]")
    asyncio.run(_do())


@plugin.command(name="disable")
@click.argument("name")
@click.option("--workspace", "-w", default=".", help="Workspace path")
def plugin_disable(name: str, workspace: str):
    """Disable a plugin."""
    async def _do():
        runtime = await bootstrap_kernel(workspace=workspace)
        ok = await runtime.commands.plugins_disable(name)
        if ok:
            console.print(f"[yellow]✓ Plugin '{name}' disabled.[/yellow]")
        else:
            console.print(f"[dim]Plugin '{name}' is already disabled.[/dim]")
    asyncio.run(_do())


if __name__ == "__main__":
    main()
