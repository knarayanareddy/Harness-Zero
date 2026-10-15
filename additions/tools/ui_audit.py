#!/usr/bin/env python3
"""Static coherence audit for the shipped web UI (harness/frontends/web/static).

Reports, without a browser, the things that make a UI feel "all over the place":

  * element ids referenced from JS that do not exist in index.html (hard breakage),
    and ids in index.html that no JS ever touches (static mock-ups);
  * CSS classes used in index.html / JS templates that styles.css never defines
    (markup that silently renders unstyled), and CSS classes styles.css defines
    that nothing uses (dead styling from removed pages);
  * inline ``style="..."`` attributes per file (styling that lives outside styles.css);
  * ``<script src>`` entries, so a second styling/runtime system (e.g. a Tailwind CDN)
    cannot sneak in unnoticed under the strict CSP.

Usage:
    python additions/tools/ui_audit.py                 # human-readable report, exit 0
    python additions/tools/ui_audit.py --json          # machine-readable
    python additions/tools/ui_audit.py --strict        # exit 1 on missing ids or undefined classes

The thresholds are deliberately not enforced by default: the numbers are an input to the
consolidation plan in review/CODE_REVIEW_158c836.md, and the ``--strict`` mode is the
acceptance check once the plan's styling workstream lands.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


def _repo_root(start: Path) -> Path:
    for p in [start, *start.parents]:
        if (p / "pyproject.toml").exists() and (p / "harness").is_dir():
            return p
    raise SystemExit("could not locate the repository root (pyproject.toml + harness/)")


ROOT = _repo_root(Path(__file__).resolve())
STATIC = ROOT / "harness" / "frontends" / "web" / "static"

_JS_FILES = sorted(p for p in STATIC.glob("*.js"))
_HTML = STATIC / "index.html"
_CSS = STATIC / "styles.css"

# Tailwind-style utilities app code uses (``text-xs``, ``bg-amber-900/60``, ``hover:bg-blue-700``, ``max-w-[180px]``)
_CLASS_TOKEN = re.compile(r"^[A-Za-z0-9_:/\-\.\[\]%]+$")


def _classes_in_attr(value: str) -> set[str]:
    out: set[str] = set()
    for tok in re.split(r"\s+", value.strip()):
        if tok and "${" not in tok and "{" not in tok and _CLASS_TOKEN.match(tok):
            out.add(tok)
    return out


def audit() -> dict:
    html = _HTML.read_text(encoding="utf-8")
    css = _CSS.read_text(encoding="utf-8") if _CSS.exists() else ""
    js = {p.name: p.read_text(encoding="utf-8") for p in _JS_FILES}

    # --- ids -------------------------------------------------------------------------
    html_ids = set(re.findall(r'\bid="([^"]+)"', html))
    js_id_refs: dict[str, set[str]] = {}
    for name, src in js.items():
        for m in re.findall(r"getElementById\(\s*['\"]([^'\"]+)['\"]", src):
            js_id_refs.setdefault(m, set()).add(name)
        for m in re.findall(r"querySelector(?:All)?\(\s*['\"`]#([A-Za-z0-9_-]+)", src):
            js_id_refs.setdefault(m, set()).add(name)
    all_js = "\n".join(js.values())
    missing_ids = {k: sorted(v) for k, v in js_id_refs.items() if k not in html_ids}
    orphan_ids = sorted(i for i in html_ids if i not in all_js)

    # --- classes ---------------------------------------------------------------------
    css_no_bodies = re.sub(r"\{[^}]*\}", "", css)
    # ``.py-1\.5`` in CSS is the escaped form of the class ``py-1.5``
    defined = {c.replace("\\", "") for c in re.findall(r"\.((?:[A-Za-z0-9_\-]|\\[^\s])+)", css_no_bodies)}
    used_html: set[str] = set()
    for m in re.findall(r'class="([^"]*)"', html):
        used_html |= _classes_in_attr(m)
    used_js: dict[str, set[str]] = {}
    for name, src in js.items():
        s: set[str] = set()
        for m in re.findall(r"class(?:Name)?\s*=\s*[`\"']([^`\"']*)[`\"']", src):
            s |= _classes_in_attr(m)
        for m in re.findall(r"classList\.(?:add|remove|toggle)\(([^)]*)\)", src):
            for c in re.findall(r"['\"]([^'\"]+)['\"]", m):
                s |= _classes_in_attr(c)
        used_js[name] = s
    used_all = used_html | set().union(*used_js.values()) if used_js else used_html
    # Conservative "unused": a class is considered used if its literal name occurs anywhere
    # in the page or the JS (covers names assembled in template strings or variables).
    corpus = html + "\n" + all_js
    used_all |= {c for c in defined if c in corpus}
    undefined = {
        "index.html": sorted(c for c in used_html if c not in defined),
        **{name: sorted(c for c in s if c not in defined) for name, s in used_js.items()},
    }
    unused_css = sorted(c for c in defined if c not in used_all and not c.startswith(("prism", "token", "language-")))

    # --- inline styling & scripts ----------------------------------------------------
    inline_styles = {"index.html": len(re.findall(r'\bstyle="', html))}
    inline_styles.update({name: len(re.findall(r'\bstyle="', src)) for name, src in js.items()})
    scripts = re.findall(r'<script[^>]*\bsrc="([^"]+)"', html)
    stylesheets = re.findall(r'<link[^>]*rel="stylesheet"[^>]*href="([^"]+)"', html)

    return {
        "static_dir": str(STATIC.relative_to(ROOT)),
        "lines": {"index.html": html.count("\n") + 1, "styles.css": css.count("\n") + 1, **{n: s.count("\n") + 1 for n, s in js.items()}},
        "ids": {"in_html": len(html_ids), "referenced_by_js": len(js_id_refs), "missing_in_html": missing_ids, "never_referenced_by_js": orphan_ids},
        "classes": {
            "defined_in_css": len(defined),
            "undefined_but_used": undefined,
            "undefined_total": sum(len(v) for v in undefined.values()),
            "defined_but_unused": unused_css,
        },
        "inline_style_attributes": inline_styles,
        "scripts": scripts,
        "stylesheets": stylesheets,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--json", action="store_true", help="print the full report as JSON")
    ap.add_argument("--strict", action="store_true", help="exit 1 when ids are missing or classes are undefined")
    args = ap.parse_args(argv)
    rep = audit()
    if args.json:
        print(json.dumps(rep, indent=2))
    else:
        print(f"UI static audit — {rep['static_dir']}")
        print("  lines:", ", ".join(f"{k} {v}" for k, v in rep["lines"].items()))
        ids = rep["ids"]
        print(f"  ids: {ids['in_html']} in index.html, {ids['referenced_by_js']} referenced by JS, "
              f"{len(ids['missing_in_html'])} missing, {len(ids['never_referenced_by_js'])} never referenced")
        if ids["missing_in_html"]:
            print("    MISSING:", ", ".join(f"{k} ({', '.join(v)})" for k, v in sorted(ids["missing_in_html"].items())))
        if ids["never_referenced_by_js"]:
            print("    static-only ids:", " ".join(ids["never_referenced_by_js"]))
        cl = rep["classes"]
        print(f"  classes: {cl['defined_in_css']} defined in styles.css, {cl['undefined_total']} used-but-undefined, "
              f"{len(cl['defined_but_unused'])} defined-but-unused")
        for f, lst in cl["undefined_but_used"].items():
            if lst:
                print(f"    {f}: {len(lst)} undefined → {' '.join(lst[:25])}{' …' if len(lst) > 25 else ''}")
        if cl["defined_but_unused"]:
            print(f"    unused css: {' '.join(cl['defined_but_unused'][:40])}{' …' if len(cl['defined_but_unused']) > 40 else ''}")
        print("  inline style= attributes:", ", ".join(f"{k} {v}" for k, v in rep["inline_style_attributes"].items()))
        print("  scripts:", ", ".join(rep["scripts"]))
        print("  stylesheets:", ", ".join(rep["stylesheets"]))
    if args.strict and (rep["ids"]["missing_in_html"] or rep["classes"]["undefined_total"]):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
