"""WS-06a — permission presets: the one knob a user turns, and what it expands to.

A preset is not a new kind of state. `kernel.security.permission_mode`, `destructive_blocking` and
`approval_timeout_s` already exist, are validated against the schema, are read by the policy plugin on every
call, and are editable in the Studio. What was missing was a *named combination*: a user who wants "be careful
with my machine" had to know that that means `ask` plus destructive blocking plus a bounded approval window, and
a distribution that ships a default had to write all three keys.

So a preset is a **resolution layer**, not a write:

    value = explicit user value  →  preset expansion  →  schema default

`SettingsStore.get` consults resolvers in that order (`harness/kernel/settings.py:UNSET` is how a resolver says
"no opinion"), which has three consequences worth stating because they are the whole design:

* Nothing is materialised into `settings.json`. Switching from `careful` to `locked` is one key, `settings get`
  answers the effective value for every key the preset controls, and switching back restores whatever the user had
  said before — because they never lost it.
* A user who pins one key keeps the rest. `permission_mode = allow` on top of `careful` wins — the preset is a
  default, not a policy — and `customised()` is how the UI says "preset: careful (customised)" instead of quietly
  disagreeing with what the settings panel shows.
* Plugins read presets for free. `permission_modes` calls `settings.get(...)` per decision, so it never learns
  the word "preset"; a resolver is the only place this could live without duplicating the read path.

`bypass` is deliberately **not** reachable from a preset: it remains available only when a caller explicitly
sets `kernel.security.permission_mode=bypass` (the tests and automation sometimes need to exercise a tool without
an approver). Offering it in a menu a user clicks is how an agent ends up with write access to a home directory by
accident. The four presets below all keep
`destructive_blocking` on, including `open` — "open" means "stop asking me", not "let `rm -rf` through".
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from harness.kernel.settings import UNSET, SettingsStore

#: The preset a kernel runs with when the user has not said otherwise. `careful`, on purpose: a fresh install
#: asks before it writes, and the settings panel explains the alternatives rather than the log doing it later.
DEFAULT_PRESET = "careful"

PRESET_ORDER = ("locked", "careful", "standard", "open")

#: What each preset means. Presets are defaults over settings; the executor wrapper still enforces the chosen
#: confinement, even when the permission policy allows a tool call.
PRESETS: dict[str, dict[str, Any]] = {
    # The model may read and plan; the policy denies every state-changing action. If an executor is invoked for a
    # read, `required` still refuses to spawn it unless an OS backend is available.
    "locked": {
        "kernel.security.permission_mode": "plan",
        "kernel.security.destructive_blocking": True,
        "kernel.sandbox.mode": "required",
        "kernel.sandbox.network": False,
    },
    # The default. Every mutating call asks; where the sandbox backend is absent, an executor still asks before
    # any unconfined fallback. A no-answer approval expires after five minutes.
    "careful": {
        "kernel.security.permission_mode": "ask",
        "kernel.security.destructive_blocking": True,
        "kernel.security.approval_timeout_s": 300,
        "kernel.security.exec_in_sandbox": "ask",
        "kernel.sandbox.mode": "preferred",
        "kernel.sandbox.network": False,
    },
    # Writes and destructive operations ask, but an executor call may proceed without a click only when the host
    # actually reported full/partial sandbox enforcement. If the backend is unavailable, policy falls back to ASK.
    "standard": {
        "kernel.security.permission_mode": "ask",
        "kernel.security.destructive_blocking": True,
        "kernel.security.approval_timeout_s": 300,
        "kernel.security.exec_in_sandbox": "allow",
        "kernel.sandbox.mode": "preferred",
        "kernel.sandbox.network": False,
    },
    # Mutating calls run without approval. Destructive commands are still blocked, and OS confinement is off by
    # explicit choice; this is "I am watching, do not interrupt me", not a default or an absence-of-backend guess.
    "open": {
        "kernel.security.permission_mode": "allow",
        "kernel.security.destructive_blocking": True,
        "kernel.sandbox.mode": "off",
        "kernel.sandbox.network": True,
    },
}

#: Every key any preset controls. `settings list` and the UI mark these as preset-owned; a key outside this set is
#: never described as customised.
CONTROLLED: tuple[str, ...] = tuple(sorted({key for row in PRESETS.values() for key in row}))


def is_preset(value: Any) -> bool:
    return str(value) in PRESETS


def preset_name(settings: SettingsStore) -> str:
    """The preset in effect — the user's value if they set one, else `careful`.

    Read with `get()`, which is safe here and only here: the preset key is in no preset's expansion, so the
    resolver abstains for it (`resolve` returns before consulting the table) and this cannot ask itself. Every
    other key in this module is answered *by* the resolver and must not be read through it.
    """
    # The literal here is the wiring, not a style choice: `settings_report()` calls a declared UI setting dead
    # unless some code *reads* it, and it matches call sites, so a constant would leave the key looking unread —
    # and a rename on either side is caught by `test_settings_declared.py` rather than by a pin of my own.
    name = str(settings.get("kernel.security.preset") or DEFAULT_PRESET)
    # A typo in a setting is not a reason to lose the safety defaults: an unknown name falls back to the
    # default rather than to "no expansion", and `settings set` cannot produce one anyway (enum-validated).
    return name if is_preset(name) else DEFAULT_PRESET


def expansion(settings: SettingsStore) -> dict[str, Any]:
    """The keys the current preset stands for, before any user override."""
    return dict(PRESETS.get(preset_name(settings), {}))


def resolve(settings: SettingsStore, path: str) -> Any:
    """The resolver `install()` puts in front of `SettingsStore.get`."""
    if path not in CONTROLLED:
        return UNSET
    if settings.has_explicit(path):
        return UNSET  # an explicit value is not a default to override
    return PRESETS.get(preset_name(settings), {}).get(path, UNSET)


def customised(settings: SettingsStore) -> list[str]:
    """Preset-controlled keys whose effective value differs from what the preset says.

    Sorted and absolute (full setting paths) because these strings end up in `status`, in the catalogue, and in a
    settings-panel note — a user reading them has to find the same key they can click on.
    """
    name = preset_name(settings)
    out = []
    for path, want in PRESETS.get(name, {}).items():
        if settings.has_explicit(path) and settings.get(path) != want:
            out.append(path)
    return sorted(out)


def summary(settings: SettingsStore) -> dict[str, Any]:
    """The block `/health`, `harness settings show` and the Studio's status pill render.

    `effective` repeats the controlled keys *after* overrides so a caller never has to re-derive the resolution
    order — the mistake this whole layer exists to prevent is a surface that reads the schema default and reports
    that instead of what the kernel will do.
    """
    name = preset_name(settings)
    overridden = set(customised(settings))
    effective = {}
    for path in CONTROLLED:
        effective[path] = settings.get(path)
    return {
        "preset": name,
        "customised": sorted(overridden),
        # `customised` alone answers "is this still the preset?", which is what the badge says; `pinned` answers
        # "did the user write any of these keys", which is what the settings panel has to show so that a value
        # equal to the preset is not reported as derived when it is a choice.
        "pinned": sorted(
            [path for path in CONTROLLED if settings.has_explicit(path)]
            + (["kernel.security.preset"] if settings.has_explicit("kernel.security.preset") else [])
        ),
        "effective": effective,
    }


def install(settings: SettingsStore) -> Callable[[], None]:
    """Wire the preset layer into a settings. Returns the unsubscribe, for tests that want it gone again.

    Idempotent: bootstrapping the same settings twice (a kernel rebuilt over an existing settings, a test that installs
    and then boots) must not stack resolvers, because a stale one would keep answering for a preset that has
    since been switched.
    """
    previous = getattr(settings, "_preset_unsubscribe", None)
    if callable(previous):
        previous()
    # One named object per layer, because `remove_resolver` compares by identity: the callable that goes in has to
    # be the one that comes out, which rules out a lambda created twice.
    layer = _layer(settings)
    settings.add_resolver(layer)

    def unsubscribe() -> None:
        settings.remove_resolver(layer)
        if getattr(settings, "_preset_unsubscribe", None) is unsubscribe:
            settings._preset_unsubscribe = None  # type: ignore[assignment]

    settings._preset_unsubscribe = unsubscribe  # type: ignore[attr-defined]
    return unsubscribe


def _layer(settings: SettingsStore) -> Callable[[str], Any]:
    """The resolver itself, closed over the settings it answers for."""
    return lambda path: resolve(settings, path)
