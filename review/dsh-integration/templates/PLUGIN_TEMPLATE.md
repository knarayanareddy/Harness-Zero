# Plugin template — the layout every new plugin in this pack follows

Harness-Zero plugins are directories with a `harness_plugin.toml` manifest discovered from `harness/plugins/**` (built-in), `~/.harness/plugins/` and `<workspace>/.harness/plugins/` (`kernel/bootstrap.py:88-93`). `harness plugin new --kind context <name>` scaffolds a context plugin (`sdk/scaffold.py` supports only `context` today — WS-02 adds `tool`, `policy`, `hook`, `store`); for the other kinds copy this layout.

```
harness/plugins/<family>/<snake_name>/
├── __init__.py
├── harness_plugin.toml      # manifest (fields: sdk/manifest.py:36-48)
├── plugin.py                # lifecycle class: setup(ctx) / teardown()
├── <feature>.py             # the implementation (handlers, policy, hook, store …)
├── settings.schema.json     # ONLY if the plugin has settings (P6)
├── README.md                # the README contract below (P4)
└── tests/
    ├── __init__.py
    └── test_conformance.py  # runs the matching sdk.testing kit
```

Unit tests that exercise the plugin **through the kernel** live under `tests/kernel/` or `tests/plugins/`, not inside the plugin (those are conformance only).

## `harness_plugin.toml`

```toml
[plugin]
name = "<family>-<name>"            # registry name; settings namespace is plugins.<name>
version = "0.1.0"
api_version = "1"
summary = "<one sentence, present tense, no adjectives>"
provides = ["tool"]                  # tool | policy | hook | store | context | provider | executor | strategy
capabilities = []                    # subset of sdk.types.Capability values the plugin needs (fs:read, fs:write, exec, net …)
entry = "harness.plugins.<family>.<snake_name>.plugin:<ClassName>Plugin"

[settings]
schema = "settings.schema.json"      # omit the whole table when there are no settings
```

## `plugin.py`

```python
"""<family>-<name>: <summary>."""
from __future__ import annotations

from harness.sdk.context import PluginContext
from harness.sdk.manifest import PluginManifest

from .<feature> import <Thing>


class <ClassName>Plugin:
    def __init__(self, manifest: PluginManifest | None = None) -> None:
        self.manifest = manifest
        self._thing: <Thing> | None = None

    async def setup(self, ctx: PluginContext) -> None:
        # Read settings only through ctx.settings (namespaced view, P6).
        self._thing = <Thing>(settings=ctx.settings, workspace=ctx.workspace, data_dir=ctx.data_dir)
        ctx.register_<kind>(self._thing)          # register_tool(spec, handler) for tools

    async def teardown(self) -> None:
        if self._thing is not None:
            await self._thing.close()
            self._thing = None
```

Rules: no module-level side effects; no `subprocess` outside executors (`tests/kernel/test_no_direct_subprocess.py`); shared paths only via `harness.sdk.paths` (never `ctx.data_dir / "memory.db"`-style private paths for shared artefacts); plugin→plugin imports are forbidden — talk through SDK protocols and registry slots.

## `settings.schema.json`

JSON Schema `object` whose property names are the keys **below** `plugins.<name>.`; each property carries `title`, `type`, `default`, `description` and an `x-ui` block (`group`, `widget`) — that is what `settings.schema` (`kernel/commands.py:176`) serves and what the client renders (WS-05).

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "threshold_chars": {
      "title": "Prune threshold (chars)",
      "type": "integer", "minimum": 1024, "default": 8192,
      "description": "Trim tool output longer than this.",
      "x-ui": {"group": "Execution & Strategies", "widget": "number"}
    }
  }
}
```

## `README.md` contract (verified by `harness catalog --check readmes`, WS-02)

Sections, in order, all mandatory:

1. **Summary** — what it does, in the user's terms (2–4 sentences).
2. **Configuration** — table `| Key | Default | Meaning |` generated from `settings.schema.json` (`harness catalog readme <plugin>` prints it; paste, do not hand-edit).
3. **Extension points** — what it registers (tools, events emitted, hooks it listens to) and how another plugin would replace it.
4. **Model experience** — what the model sees: tool description(s), result shape, the exact text of any reminder/notice it injects.
5. **Known limitations** — honest list; "none" is not acceptable in the first version.

## `tests/test_conformance.py`

```python
from harness.sdk.testing.kits import ToolKit   # or PolicyKit / HookKit / StoreKit / ContextKit

def test_conformance(tmp_path):
    ToolKit.run(plugin_dir=__file__, workspace=tmp_path)
```

(`harness plugin test --all` runs these in CI.)

## Kernel-level test (mandatory for anything model- or user-visible)

Pattern from `tests/kernel/test_memory_roundtrip.py` / `test_mcp_tool_through_kernel.py`:

```python
rt = await bootstrap_kernel(workspace=ws, config_dir=conf, overrides={...})
provider = ScriptedProvider(models=[MODEL]); provider.add_response([...]); rt.registry.register_provider(provider, plugin_name="test_scripted")
session = await rt.session_manager.create_session()
result = await rt.runner.execute_turn(session=session.info, prompt="...", options={"provider": "scripted", "model": MODEL.id})
# assert on provider.requests[n].messages (what the model saw), on the transcript on disk, on bus events
```

A mocked `ctx.data_dir` / hand-built `ToolContext` is **not** a kernel-level test (see review Addendum A for why).
