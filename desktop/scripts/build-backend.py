"""build:backend (spec §4.6 ①)：用既有 venv 安装依赖 + PyInstaller（构建期工具，不写 requirements.txt），
跑 aragon-quant.spec 产 onedir，拷到 desktop/resources/backend/，最后冒烟跑一次 exe + GET /api/health（P2-2）。

跨平台纯 Python；Windows 一等公民（PyInstaller 不能交叉编译，mac/linux 需各自平台构建，R12）。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

DESKTOP = Path(__file__).resolve().parents[1]
REPO = DESKTOP.parent
QUANT = REPO / "services" / "quant"
IS_WIN = os.name == "nt"
VENV_PY = QUANT / ".venv" / ("Scripts/python.exe" if IS_WIN else "bin/python")

PYI_DIST = DESKTOP / "build" / "pyi-dist"
PYI_WORK = DESKTOP / "build" / "pyi-work"
OUT = DESKTOP / "resources" / "backend"
EXE_NAME = "aragon-quant.exe" if IS_WIN else "aragon-quant"
SMOKE_PORT = 18790  # 冒烟用独立端口，避免撞 18760


def _force_utf8_stdio() -> None:
    """把本脚本 stdout/stderr 重配为 UTF-8——否则在 GBK(cp936) 控制台打印 ✓ / 中文会抛
    UnicodeEncodeError 打挂脚本（同 scripts/dev.py 的处理）。子进程另注入 PYTHONUTF8。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass


def py() -> str:
    return str(VENV_PY) if VENV_PY.exists() else sys.executable


def run(cmd: list[str], cwd: Path | None = None) -> None:
    print("[build-backend] $", " ".join(str(c) for c in cmd), flush=True)
    rc = subprocess.call([str(c) for c in cmd], cwd=str(cwd) if cwd else None)
    if rc != 0:
        print(f"[build-backend] ✗ 命令失败（rc={rc}）")
        sys.exit(rc)


def smoke(exe: Path) -> None:
    """直接跑 exe + GET /api/health 兜底验证冻结后端可起（R4 / P2-2）。OKX 不可达不影响（health 仍返回）。"""
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    print(f"[build-backend] 冒烟：启动 {exe.name} :{SMOKE_PORT} …", flush=True)
    proc = subprocess.Popen(
        [str(exe), "--port", str(SMOKE_PORT)],
        cwd=str(exe.parent), env=env,
    )
    try:
        url = f"http://127.0.0.1:{SMOKE_PORT}/api/health"
        ok = False
        for _ in range(60):  # 至多 ~60s（onedir 首次解压 + OKX 探活）
            if proc.poll() is not None:
                print(f"[build-backend] ✗ 冒烟失败：后端 exe 提前退出（rc={proc.returncode}）")
                sys.exit(1)
            try:
                with urllib.request.urlopen(url, timeout=3) as resp:
                    body = json.loads(resp.read().decode("utf-8"))
                    print(f"[build-backend] ✓ /api/health 响应：{body}")
                    ok = True
                    break
            except Exception:
                time.sleep(1)
        if not ok:
            print("[build-backend] ✗ 冒烟失败：/api/health 60s 内无响应")
            sys.exit(1)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()


def main() -> None:
    _force_utf8_stdio()
    p = py()
    print(f"[build-backend] Python: {p}")
    # 依赖：后端运行依赖 + pyinstaller（构建期工具，不写入 requirements.txt）
    run([p, "-m", "pip", "install", "-r", str(QUANT / "requirements.txt")])
    run([p, "-m", "pip", "install", "pyinstaller"])

    # 清旧产物
    for d in (PYI_DIST, PYI_WORK):
        if d.exists():
            shutil.rmtree(d)

    run(
        [
            p, "-m", "PyInstaller", "--noconfirm", "--clean",
            "--distpath", str(PYI_DIST),
            "--workpath", str(PYI_WORK),
            str(DESKTOP / "aragon-quant.spec"),
        ],
        cwd=DESKTOP,
    )

    src = PYI_DIST / "aragon-quant"
    if not src.exists():
        print(f"[build-backend] ✗ 未找到 PyInstaller 产物目录：{src}")
        sys.exit(1)
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, OUT)

    exe = OUT / EXE_NAME
    print(f"[build-backend] ✓ 后端可执行体：{exe}  exists={exe.exists()}")
    if not exe.exists():
        sys.exit(1)

    smoke(exe)
    print("[build-backend] ✓ build:backend 完成。")


if __name__ == "__main__":
    main()
