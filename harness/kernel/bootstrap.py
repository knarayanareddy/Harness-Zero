"""
Kernel Bootstrap: wires together the modular kernel runtime and returns Commands & EventBus (§1, §5).
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from harness.kernel.approvals import ApprovalBroker
from harness.kernel.bus import EventBus
from harness.kernel.commands import Commands
from harness.kernel.compaction import Compactor
from harness.kernel.context import ContextAssembler
from harness.kernel.gate import PolicyGate
from harness.kernel.inbox import BackgroundInbox
from harness.kernel.jail import WorkspaceJail
from harness.kernel.registry import PluginRegistry
from harness.kernel.runner import TurnRunner
from harness.kernel.secrets import Secrets
from harness.kernel.session import SessionManager
from harness.kernel.settings import SettingsStore

logger = logging.getLogger(__name__)


@dataclass
class KernelRuntime:
    commands: Commands
    bus: EventBus
    registry: PluginRegistry
    settings: SettingsStore
    secrets: Secrets
    gate: PolicyGate
    session_manager: SessionManager
    runner: TurnRunner


async def bootstrap_kernel(
    workspace: Path | str,
    config_dir: Path | str | None = None,
    log_file: Path | str | None = None,
    verbose: bool = False,
) -> KernelRuntime:
    """
    Initializes and boots all kernel services:
    EventBus -> SettingsStore -> Secrets -> WorkspaceJail -> PluginRegistry ->
    PolicyGate -> SessionManager -> ContextAssembler -> Compactor -> TurnRunner -> Commands.
    """
    ws = Path(workspace).resolve()
    env_cfg = os.environ.get("HARNESS_CONFIG_DIR")
    default_cfg = Path(env_cfg) if env_cfg else (Path.home() / ".harness")
    conf = Path(config_dir).resolve() if config_dir else default_cfg.resolve()
    conf.mkdir(parents=True, exist_ok=True)

    if log_file:
        lf = Path(log_file).resolve()
        lf.parent.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(
            filename=str(lf),
            level=logging.DEBUG if verbose else logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            force=True,
        )
    elif verbose:
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            force=True,
        )

    bus = EventBus()
    settings = SettingsStore(path=conf)
    secrets = Secrets(path=conf)
    jail = WorkspaceJail(ws)
    registry = PluginRegistry(
        workspace=ws,
        settings=settings,
        secrets=secrets,
        bus=bus,
    )
    builtin_dir = Path(__file__).resolve().parents[1] / "plugins"
    registry.discover_all(
        builtin_dir=builtin_dir,
        local_dir=conf / "plugins",
        workspace_dir=ws / ".harness" / "plugins",
    )
    await registry.load_all()

    approvals = ApprovalBroker(bus=bus)
    inbox = BackgroundInbox(bus=bus)
    gate = PolicyGate(
        registry=registry,
        jail=jail,
        settings=settings,
        secrets=secrets,
        approvals=approvals,
        bus=bus,
        inbox=inbox,
    )
    session_manager = SessionManager(
        workspace=ws,
        registry=registry,
        settings=settings,
        bus=bus,
    )
    assembler = ContextAssembler(registry=registry, settings=settings)
    cost_ledger = registry.stores.get("cost")[0] if "cost" in registry.stores else None
    transcript_store = registry.stores.get("transcript")[0] if "transcript" in registry.stores else None
    search_index = registry.stores.get("search")[0] if "search" in registry.stores else None
    compactor = Compactor(settings=settings, bus=bus, registry=registry)
    runner = TurnRunner(
        registry=registry,
        gate=gate,
        bus=bus,
        settings=settings,
        assembler=assembler,
        compactor=compactor,
        inbox=inbox,
        transcript_store=transcript_store,
        search_index=search_index,
        cost_ledger=cost_ledger,
    )
    commands = Commands(
        workspace=ws,
        session_manager=session_manager,
        runner=runner,
        gate=gate,
        settings=settings,
        secrets=secrets,
        registry=registry,
        bus=bus,
    )

    return KernelRuntime(
        commands=commands,
        bus=bus,
        registry=registry,
        settings=settings,
        secrets=secrets,
        gate=gate,
        session_manager=session_manager,
        runner=runner,
    )
