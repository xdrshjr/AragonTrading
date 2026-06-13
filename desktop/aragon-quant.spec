# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec：把 services/quant 的 FastAPI 后端打成 onedir 自包含可执行体 (spec §4.6 / §8.3 / R4)。
# 由 desktop/scripts/build-backend.py 调用：pyinstaller aragon-quant.spec（cwd=desktop/）。
#
# hiddenimports 兜底动态导入（uvicorn loops/protocols、aiosqlite、sqlalchemy sqlite 方言、
# pydantic_core、websockets 等），并用 collect_all 收齐数据文件；collect_submodules('app') 收齐后端包。
import os
import sys

from PyInstaller.utils.hooks import collect_all, collect_submodules

# SPECPATH：PyInstaller 注入的「本 spec 所在目录」= desktop/
DESKTOP = SPECPATH  # noqa: F821 — PyInstaller 运行期注入
REPO_ROOT = os.path.abspath(os.path.join(DESKTOP, os.pardir))
QUANT = os.path.join(REPO_ROOT, "services", "quant")

# 使分析期能 import app.* 与 collect_submodules('app')
if QUANT not in sys.path:
    sys.path.insert(0, QUANT)

datas = []
binaries = []
hiddenimports = []

# collect_all：收齐这些包的子模块 + 数据文件 + 动态库（pydantic/pydantic_core/sqlalchemy 等）
for pkg in ("uvicorn", "anyio", "pydantic", "pydantic_core", "sqlalchemy", "aiosqlite", "websockets"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception as exc:  # 某些包可能无数据文件，忽略
        print(f"[aragon-quant.spec] collect_all({pkg}) skipped: {exc}")

# 后端自身包
hiddenimports += collect_submodules("app")

# 显式兜底（动态导入，分析器可能漏掉）
hiddenimports += [
    "aiosqlite",
    "sqlalchemy.dialects.sqlite",
    "sqlalchemy.dialects.sqlite.aiosqlite",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.protocols.websockets.wsproto_impl",
    "websockets.legacy",
    "websockets.legacy.client",
    "websockets.legacy.server",
    "pydantic_settings",
]

block_cipher = None

a = Analysis(
    [os.path.join(DESKTOP, "scripts", "backend_entry.py")],
    pathex=[QUANT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "PyQt5", "PySide6"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="aragon-quant",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="aragon-quant",
)
