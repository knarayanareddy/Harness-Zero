"""The web client is one coherent styling system (consolidation P3, review §4.2 / §4.3).

Two checks, no daemon needed:

* the static audit (``additions/tools/ui_audit.py --strict``): every id JS touches exists and every id in the
  page is referenced; every class used is defined in ``styles.css`` or the compiled ``vendor/tw.css`` and
  every class ``styles.css`` defines is used; no inline ``style=`` anywhere; exactly those two stylesheets,
  in that order; scripts only from ``/static``;
* the cascade as a browser would resolve it (jsdom): everything marked ``hidden`` in the shipped page
  really computes to ``display: none`` and the things that must be visible are. Before P3 the page had no
  ``.hidden`` rule at all, so the approval banner, the proceed banner, the onboarding banner, the Stop
  button, the session list and the economics view were all visible at once on load (R14).
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "harness" / "frontends" / "web" / "static"
AUDIT = ROOT / "additions" / "tools" / "ui_audit.py"


def _load_audit():
    spec = importlib.util.spec_from_file_location("ui_audit", AUDIT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_static_audit_is_clean():
    audit = _load_audit()
    rep = audit.audit()
    findings = audit.strict_findings(rep)
    assert not findings, "\n".join(findings)
    # The numbers the plan promised, pinned so they cannot drift back silently.
    assert rep["inline_style_attributes"] == {k: 0 for k in rep["inline_style_attributes"]}
    assert rep["stylesheets"] == ["/static/styles.css", "/static/vendor/tw.css"]


def test_compiled_utilities_cover_what_the_templates_use():
    """`vendor/tw.css` is a committed build artifact; it must contain the utilities the markup relies on."""
    tw = (STATIC / "vendor" / "tw.css").read_text(encoding="utf-8")
    for cls in (".hidden {", ".truncate {", ".uppercase {", ".whitespace-pre-wrap {", ".flex {", ".items-center {"):
        assert cls in tw, f"{cls} missing from vendor/tw.css — run `npm run build:css`"
    assert "@tailwind" not in tw, "vendor/tw.css must be the compiled output, not the input"
    # Preflight is off: the compiled file must not restyle elements wholesale (styles.css owns the reset).
    assert "html {" not in tw and "body {" not in tw


_CASCADE_PROBE = r"""
import { JSDOM } from 'jsdom';
import fs from 'node:fs';
const S = process.argv[2].replace(/\/?$/, '/');
let html = fs.readFileSync(S + 'index.html', 'utf8');
html = html
  .replace(/<link rel="stylesheet" href="\/static\/styles.css">/, `<style>${fs.readFileSync(S + 'styles.css', 'utf8')}</style>`)
  .replace(/<link rel="stylesheet" href="\/static\/vendor\/tw.css">/, `<style>${fs.readFileSync(S + 'vendor/tw.css', 'utf8')}</style>`)
  .replace(/<script[^>]*><\/script>/g, '');
const { window } = new JSDOM(html, { pretendToBeVisual: true });
const display = (sel) => { const el = window.document.querySelector(sel); return el ? window.getComputedStyle(el).display : 'MISSING'; };
const out = { on_load: {}, modal_after_unhide: null, stop_after_unhide: null };
for (const sel of process.argv.slice(3)) out.on_load[sel] = display(sel);
const modal = window.document.querySelector('#settings-modal'); modal.classList.remove('hidden'); out.modal_after_unhide = display('#settings-modal');
const stop = window.document.querySelector('#btn-stop'); stop.classList.remove('hidden'); out.stop_after_unhide = display('#btn-stop');
console.log(JSON.stringify(out));
"""

HIDDEN_ON_LOAD = ["#approval-banner", "#onboarding-banner", "#proceed-banner", "#session-list", "#economics-view", "#btn-stop", "#settings-modal", "#context-inspector"]
VISIBLE_ON_LOAD = ["#timeline", "#btn-send", "#studio-content-area", "#composer-form"]


def test_hidden_elements_are_hidden_in_the_cascade():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is not installed on this runner")
    if subprocess.run([node, "-e", "import('jsdom')"], cwd=ROOT, capture_output=True).returncode != 0:
        pytest.skip("jsdom is not installed (npm install)")
    probe = ROOT / ".ui_cascade_probe.mjs"  # must live under the repo root so Node resolves jsdom
    probe.write_text(_CASCADE_PROBE, encoding="utf-8")
    try:
        res = subprocess.run([node, str(probe), str(STATIC), *HIDDEN_ON_LOAD, *VISIBLE_ON_LOAD], cwd=ROOT, capture_output=True, text=True, timeout=60)
    finally:
        probe.unlink(missing_ok=True)
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout.strip().splitlines()[-1])
    hidden = {sel: out["on_load"][sel] for sel in HIDDEN_ON_LOAD}
    visible = {sel: out["on_load"][sel] for sel in VISIBLE_ON_LOAD}
    assert all(v == "none" for v in hidden.values()), hidden
    assert all(v not in ("none", "MISSING") for v in visible.values()), visible
    assert out["modal_after_unhide"] == "flex"
    assert out["stop_after_unhide"] == "flex"
