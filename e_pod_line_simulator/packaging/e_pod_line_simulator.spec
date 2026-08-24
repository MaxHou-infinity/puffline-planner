# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller 打包配置（V3.2 P2）

用法（在 e_pod_line_simulator 目录）：
    pyinstaller --noconfirm packaging/e_pod_line_simulator.spec
"""

import os
import re

from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
with open(os.path.join(ROOT, "src", "version.py"), encoding="utf-8") as version_file:
    VERSION = re.search(
        r'^__version__\s*=\s*"([^"]+)"',
        version_file.read(),
        re.MULTILINE,
    ).group(1)

datas = [
    (os.path.join(ROOT, "assets"), "assets"),
    (os.path.join(ROOT, "configs"), "configs"),
]

hiddenimports = (
    collect_submodules("simpy")
    + collect_submodules("pandas")
    + collect_submodules("openpyxl")
    + collect_submodules("reportlab")
)

a = Analysis(
    [os.path.join(ROOT, "run.py")],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PuffLine-Planner",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    name="PuffLine-Planner",
)

app = BUNDLE(
    coll,
    name="PuffLine Planner.app",
    icon=os.path.join(ROOT, "assets", "puffline-icon.icns"),
    version=VERSION,
    bundle_identifier="com.maxhou.pufflineplanner",
)
