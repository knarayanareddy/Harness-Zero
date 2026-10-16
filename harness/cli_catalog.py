"""`harness catalog` — generate the reference docs from the running kernel, or check them (WS-02).

    harness catalog gen              rewrite docs/catalog/* + docs/events.schema.json
    harness catalog --check          fail on drift, undeclared settings, client/command mismatch
    harness catalog readme <plugin>  print the plugin's Configuration table for its README
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import click
from rich.console import Console

from harness.kernel.catalog import (
    ALL_CHECKS,
    CATALOG_DIR,
    REPO_ROOT,
    build_catalog,
    render_configuration_table,
    run_checks,
    write_outputs,
)
from harness.sdk.events import schema as events_schema

console = Console()


@click.group(name="catalog", invoke_without_command=True)
@click.option("--check", "check", is_flag=True, help="Verify the generated docs and the freshness gates.")
@click.option(
    "--section",
    "sections",
    multiple=True,
    type=click.Choice(ALL_CHECKS),
    help="Run only these checks (repeatable). Default: all.",
)
@click.option("--format", "fmt", type=click.Choice(["text", "json"]), default="text", show_default=True)
@click.option("--strict", "strict", is_flag=True, help="Turn warnings (README sections, coverage gaps) into failures.")
@click.option("--workspace", "-w", default=".", help="Workspace to boot the kernel in.")
@click.pass_context
def catalog(
    ctx: click.Context, check: bool, sections: tuple[str, ...], fmt: str, strict: bool, workspace: str
) -> None:
    """Generated catalogues for plugins, tools, settings, events, commands and the web client."""
    if ctx.invoked_subcommand is not None:
        return
    if not check:
        click.echo(ctx.get_help())
        raise SystemExit(2)

    async def _run() -> int:
        report = await run_checks(
            root=Path(workspace).resolve() if Path(workspace).resolve() != REPO_ROOT else REPO_ROOT,
            sections=tuple(sections) or ALL_CHECKS,
            strict_scenarios=strict,
        )
        if strict:
            for result in report.results.values():
                if result["status"] == "warn":
                    result["status"] = "fail"
        click.echo(report.render(fmt))
        return 0 if report.ok else 1

    raise SystemExit(asyncio.run(_run()))


@catalog.command(name="gen")
@click.option("--out", "out_dir", default=None, help="Output directory (default: docs/catalog).")
def catalog_gen(out_dir: str | None) -> None:
    """Rewrite docs/catalog/* and docs/events.schema.json from a booted kernel."""
    target = Path(out_dir) if out_dir else REPO_ROOT / CATALOG_DIR

    async def _run() -> None:
        cat = await build_catalog()
        written = write_outputs(cat, target)
        schema_path = REPO_ROOT / "docs" / "events.schema.json"
        schema_path.write_text(json.dumps(events_schema(), indent=2) + "\n", encoding="utf-8")
        written.append(schema_path)
        for path in written:
            console.print(f"[green]wrote[/green] {path.relative_to(REPO_ROOT)}")

    asyncio.run(_run())


@catalog.command(name="readme")
@click.argument("plugin")
def catalog_readme(plugin: str) -> None:
    """Print the Configuration table a plugin's README.md should carry."""

    async def _run() -> None:
        cat = await build_catalog()
        if not any(p["name"] == plugin for p in cat["plugins"]):
            names = ", ".join(sorted(p["name"] for p in cat["plugins"]))
            raise click.UsageError(f"unknown plugin '{plugin}'. Known: {names}")
        click.echo(render_configuration_table(cat, plugin))

    asyncio.run(_run())
