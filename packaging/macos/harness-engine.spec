# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

# Collect all plugin modules for hiddenimports dynamically
plugin_dir = Path("harness/plugins")
plugin_hidden_imports = []
if plugin_dir.exists():
    for py_file in plugin_dir.rglob("*.py"):
        rel_parts = list(py_file.with_suffix("").parts)
        mod_name = ".".join(rel_parts)
        plugin_hidden_imports.append(mod_name)

datas = [
    ('harness/frontends/web/static', 'harness/frontends/web/static'),
    ('harness/plugins', 'harness/plugins'),
]
if Path("builtin").exists():
    datas.append(('builtin', 'builtin'))

a = Analysis(
    ['harness/engine_main.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=plugin_hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='harness-engine',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
