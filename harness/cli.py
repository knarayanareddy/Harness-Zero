"""
Interactive CLI and TUI for Harness-Zero.
Supports interactive pair programming, one-shot turns, slash commands, and plugin management.
"""
from __future__ import annotations

import asyncio
import json
import os
import signal
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
from harness.sdk.errors import HarnessError, ProviderError

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
@click.option("--mock-script", default=None, help="Path to mock script JSON file for --provider mock")
@click.option("--kernel", is_flag=True, default=True, help="Use modular kernel runtime (default: True)")
@click.option("--verbose", "-v", is_flag=True, default=False, help="Verbose logging")
@click.option("--log-file", default=None, help="Log file path")
def run(
    prompt: str,
    workspace: str,
    provider: str | None,
    model: str | None,
    mock_script: str | None,
    kernel: bool,
    verbose: bool,
    log_file: str | None,
):
    """Execute a single autonomous turn via modular kernel."""
    asyncio.run(_run_kernel_turn(prompt, Path(workspace), provider, model, mock_script, verbose, log_file))


async def _run_kernel_turn(
    prompt: str,
    workspace: Path,
    provider: str | None,
    model: str | None,
    mock_script: str | None,
    verbose: bool,
    log_file: str | None,
):
    try:
        runtime = await bootstrap_kernel(
            workspace=workspace,
            log_file=Path(log_file) if log_file else None,
            verbose=verbose,
        )
        if mock_script:
            runtime.settings.set("providers.mock.script_file", mock_script)
            if not provider:
                provider = "mock"

        app = CliApp(commands=runtime.commands, bus=runtime.bus, interactive=False)
        options: dict[str, Any] = {}
        if provider:
            options["provider"] = provider
        if model:
            options["model"] = model

        result = await app.run_turn(prompt, options=options)
        if result.output:
            console.print(Panel(Markdown(result.output), title="[bold magenta]Agent Response[/bold magenta]", border_style="green"))
    except ProviderError as e:
        console.print(Panel(
            f"[bold yellow]Provider Error ({e.kind}):[/bold yellow] {e.message}",
            title="[bold yellow]Model Provider Configuration[/bold yellow]",
            border_style="yellow",
        ))
    except HarnessError as e:
        console.print(Panel(f"[bold red]{e}[/bold red]", title="[bold red]Kernel Error[/bold red]", border_style="red"))
    except Exception as e:
        console.print(f"[bold red]Kernel execution error: {e}[/bold red]")


@main.command()
@click.option("--workspace", "-w", default=".", help="Workspace path")
@click.option("--provider", "-p", default=None, help="Model provider")
@click.option("--model", "-m", default=None, help="Model name")
@click.option("--mock-script", default=None, help="Path to mock script JSON file for --provider mock")
@click.option("--kernel", is_flag=True, default=True, help="Use modular kernel runtime (default: True)")
@click.option("--verbose", "-v", is_flag=True, default=False, help="Verbose logging")
@click.option("--log-file", default=None, help="Log file path")
def interactive(
    workspace: str,
    provider: str | None,
    model: str | None,
    mock_script: str | None,
    kernel: bool,
    verbose: bool,
    log_file: str | None,
):
    """Start an interactive pair-programming session with slash commands."""
    asyncio.run(_run_kernel_interactive(Path(workspace), provider, model, mock_script, verbose, log_file))


async def _run_kernel_interactive(
    workspace: Path,
    provider: str | None,
    model: str | None,
    mock_script: str | None,
    verbose: bool,
    log_file: str | None,
):
    try:
        runtime = await bootstrap_kernel(
            workspace=workspace,
            log_file=Path(log_file) if log_file else None,
            verbose=verbose,
        )
        if mock_script:
            runtime.settings.set("providers.mock.script_file", mock_script)
            if not provider:
                provider = "mock"

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
    asyncio.run(_run_web_server(Path(workspace), port, sandbox_port, host, allow_host, open_browser=False))


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
    asyncio.run(_run_web_server(Path(workspace), port, sandbox_port, host, allow_host, open_browser=not no_open))


async def _run_web_server(
    workspace: Path,
    port: int,
    sandbox_port: int,
    host: str,
    allow_host: tuple[str, ...],
    open_browser: bool = False,
):
    try:
        from harness.frontends.web.server import HarnessWebServer
        runtime = await bootstrap_kernel(workspace=workspace)
        server = HarnessWebServer(
            commands=runtime.commands,
            bus=runtime.bus,
            host=host,
            port=port,
            sandbox_port=sandbox_port,
            allowed_hosts=list(allow_host),
            workspace=workspace,
        )
        await server.start()

        access_url = f"http://{host}:{server.port}/?token={server.auth_token}"
        console.print(Panel(
            f"[bold green]Harness-Zero Sovereign Agent Studio active![/bold green]\n\n"
            f"• API / UI:      [cyan]{access_url}[/cyan]\n"
            f"• Sandbox:       [cyan]http://{host}:{server.sandbox_port}/[/cyan]\n"
            f"• Auth Token:    [yellow]{server.auth_token}[/yellow]\n"
            f"• Workspace:     [dim]{workspace.resolve()}[/dim]\n\n"
            f"[dim]Press Ctrl+C to shut down gracefully.[/dim]",
            title="[bold cyan]Harness-Zero Studio[/bold cyan]",
            border_style="green",
        ))

        if open_browser:
            import webbrowser
            webbrowser.open(access_url)

        stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop_event.set)
            except (NotImplementedError, RuntimeError):
                pass

        try:
            await stop_event.wait()
        finally:
            console.print("\n[yellow]Shutting down Harness Web Server...[/yellow]")
            await server.stop()
            console.print("[green]Shutdown complete.[/green]")
    except Exception as e:
        console.print(f"[bold red]Web server error: {e}[/bold red]")


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
    """Manage plugins (new, list, info, enable, disable)."""
    pass


@plugin.command(name="new")
@click.argument("name")
@click.option(
    "--kind",
    "-k",
    required=True,
    type=click.Choice(
        ["tool", "context", "provider", "policy", "executor", "strategy", "hook", "store"],
        case_sensitive=False,
    ),
    help="Plugin component kind",
)
@click.option(
    "--dir",
    "-d",
    "target_dir",
    default=None,
    help="Target directory for plugin (defaults to ./<name>)",
)
@click.option("--summary", "-s", default="", help="Short summary of the plugin")
def plugin_new(name: str, kind: str, target_dir: str | None, summary: str):
    """Scaffold a new plugin directory from SDK template."""
    from harness.sdk.scaffold import scaffold_plugin

    try:
        out = scaffold_plugin(name=name, kind=kind, target_dir=target_dir, summary=summary)
        console.print(
            f"[bold green]Successfully scaffolded {kind} plugin '{name}' at:[/bold green] {out}\n"
            f"[dim]Run tests with: harness plugin test {out}[/dim]"
        )
    except Exception as e:
        console.print(f"[bold red]Failed to scaffold plugin: {e}[/bold red]")
        sys.exit(1)


@plugin.command(name="test")
@click.argument("target", required=False, default=None)
@click.option("--all", "all_plugins", is_flag=True, help="Test all builtin plugins")
@click.option("--live", is_flag=True, help="Run live model tests (opt-in)")
def plugin_test(target: str | None, all_plugins: bool, live: bool):
    """Run conformance tests for a plugin or all plugins."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    env = os.environ.copy()
    if live:
        env["HARNESS_LIVE"] = "1"
    else:
        env["HARNESS_LIVE"] = "0"

    test_paths: list[str] = []

    if all_plugins:
        test_paths.append("tests/plugins")
    elif target:
        tp = Path(target).resolve()
        if tp.is_dir():
            if (tp / "tests").exists():
                test_paths.append(str(tp / "tests"))
            else:
                test_paths.append(str(tp))
        elif tp.is_file():
            test_paths.append(str(tp))
        else:
            # Maybe a plugin name, e.g. "fs" or "anthropic"
            matched = list(Path("tests/plugins").glob(f"*{target}*.py"))
            if matched:
                test_paths.extend(str(m) for m in matched)
            else:
                console.print(f"[bold red]Target '{target}' not found as file, directory, or test suite.[/bold red]")
                sys.exit(1)
    else:
        if Path("tests").exists():
            test_paths.append("tests")
        else:
            test_paths.append("tests/plugins")

    console.print(f"[bold cyan]Running plugin conformance tests for:[/bold cyan] {', '.join(test_paths)}")
    cmd = [sys.executable, "-m", "pytest", "-q"] + test_paths
    res = subprocess.run(cmd, env=env)
    if res.returncode == 0:
        console.print("[bold green]✓ All plugin conformance tests passed.[/bold green]")
    else:
        console.print("[bold red]✗ Conformance tests failed.[/bold red]")
        sys.exit(res.returncode)


@plugin.command(name="doctor")
@click.argument("target", required=False, default=None)
@click.option("--all", "all_plugins", is_flag=True, help="Diagnose all discovered plugins")
@click.option("--workspace", "-w", default=".", help="Workspace path")
def plugin_doctor(target: str | None, all_plugins: bool, workspace: str):
    """Diagnose plugin manifests, import boundaries, capabilities, and schemas."""
    import sys
    from pathlib import Path

    from harness.sdk.doctor import diagnose_plugin

    plugins_to_check: list[Path] = []

    if all_plugins:
        builtin_root = Path(__file__).resolve().parent / "plugins"
        if builtin_root.exists():
            for mf in sorted(builtin_root.rglob("harness_plugin.toml")):
                plugins_to_check.append(mf.parent)
    elif target:
        tp = Path(target).resolve()
        if tp.exists():
            plugins_to_check.append(tp if tp.is_dir() else tp.parent)
        else:
            builtin_root = Path(__file__).resolve().parent / "plugins"
            matched = [mf.parent for mf in sorted(builtin_root.rglob("harness_plugin.toml")) if target in mf.parent.name]
            if matched:
                plugins_to_check.extend(matched)
            else:
                console.print(f"[bold red]Plugin target '{target}' not found.[/bold red]")
                sys.exit(1)
    else:
        plugins_to_check.append(Path.cwd())

    has_errors = False
    for p_dir in plugins_to_check:
        report = diagnose_plugin(p_dir)
        status_color = "green" if report.passed else "red"
        status_icon = "✓" if report.passed else "✗"
        console.print(f"\n[bold {status_color}]{status_icon} Diagnostic Report for {report.plugin_name}[/bold {status_color}] ([dim]{report.plugin_path}[/dim])")

        table = Table(box=None, padding=(0, 2))
        table.add_column("Category", style="cyan")
        table.add_column("Status", style="bold")
        table.add_column("Message")

        for check in report.checks:
            icon = "✓" if check.status == "OK" else ("⚠" if check.status == "WARN" else "✗")
            color = "green" if check.status == "OK" else ("yellow" if check.status == "WARN" else "red")
            table.add_row(check.category, f"[{color}]{icon} {check.status}[/{color}]", check.message)
            if check.details:
                table.add_row("", "", f"[dim]{check.details}[/dim]")

        console.print(table)
        if not report.passed:
            has_errors = True

    if has_errors:
        console.print("\n[bold red]Plugin doctor detected errors that must be resolved.[/bold red]")
        sys.exit(1)
    else:
        console.print("\n[bold green]✓ Plugin doctor checks passed cleanly.[/bold green]")


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


@main.command()
@click.option("--workspace", "-w", default=".", help="Workspace path")
def mcp(workspace: str):
    """Run Harness as a Model Context Protocol stdio server for external AI tools."""
    from harness.plugins.bridges.mcp_server import MCPServer, run_mcp_server_from_stdio

    async def _run():
        runtime = await bootstrap_kernel(workspace=workspace)
        expose = runtime.settings.get("plugins.mcp-server.expose")
        server = MCPServer(
            tools_provider=lambda: [spec for spec, _, _ in runtime.registry.tools.values()],
            tool_executor=lambda call: runtime.gate.execute_tool(call),
            expose_list=expose,
        )
        await run_mcp_server_from_stdio(server)

    asyncio.run(_run())


@main.command()
@click.option("--workspace", "-w", default=".", help="Workspace path")
def acp(workspace: str):
    """Run Harness as an Agent Client Protocol stdio server for IDE editors."""
    from harness.plugins.bridges.acp.server import run_acp_server_from_stdio

    async def _run():
        runtime = await bootstrap_kernel(workspace=workspace)
        await run_acp_server_from_stdio(commands=runtime.commands, bus=runtime.bus)

    asyncio.run(_run())


if __name__ == "__main__":
    main()

