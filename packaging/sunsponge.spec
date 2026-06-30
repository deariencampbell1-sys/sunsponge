# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for RHOBEAR Captur'd (Windows desktop).

Bundles: the FastAPI app + uvicorn, the static ui/, Playwright (driver + the
Chromium installed at build time with PLAYWRIGHT_BROWSERS_PATH=0), and pywebview
for the native window. Entry point: packaging/desktop.py.

Build (from repo root):  pyinstaller packaging/sunsponge.spec --noconfirm
"""
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH).resolve().parent  # repo root (spec lives in packaging/)

datas = [(str(ROOT / "ui"), "ui")]
binaries = []
hiddenimports = [
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
]

# Pull in everything Playwright and pywebview need (data files, binaries, submodules).
for pkg in ("playwright", "webview"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

a = Analysis(
    [str(ROOT / "packaging" / "desktop.py")],
    pathex=[str(ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "pytest"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="RHOBEAR Captur'd",
    console=False,
    icon=str(ROOT / "packaging" / "capturd.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="RHOBEAR-Capturd",
)
