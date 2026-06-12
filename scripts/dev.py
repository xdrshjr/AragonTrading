"""跨平台开发编排器 (F1)：一条命令完成预检 + 依赖就绪 + 同时拉起前后端。

用法（仓库根目录）：
    python scripts/dev.py setup                 # 建 venv + 装依赖（Python + pnpm）
    python scripts/dev.py preflight             # OKX 连通性预检
    python scripts/dev.py up [--skip-preflight] # 预检 → 并发起后端(8000) + 前端(3000)
    python scripts/dev.py test                  # 后端 pytest + 前端 vitest

纯 Python，无第三方依赖；Windows / macOS / Linux 行为一致：
- 子进程注入 PYTHONUTF8=1，透传 HTTP(S)_PROXY；正斜杠路径。
- Windows 用 CREATE_NEW_PROCESS_GROUP，POSIX 用进程组，Ctrl-C 双进程优雅退出。
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QUANT = ROOT / "services" / "quant"
WEB = ROOT / "apps" / "web"
VENV = QUANT / ".venv"
IS_WIN = os.name == "nt"

# 颜色前缀（现代终端 / Windows Terminal 支持 ANSI）
C_API = "\033[36m"   # cyan
C_WEB = "\033[35m"   # magenta
C_RESET = "\033[0m"


def _enable_ansi() -> None:
    if IS_WIN:
        os.system("")  # 触发 Windows 控制台启用 VT 处理


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if IS_WIN else "bin/python")


def child_env() -> dict:
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _pnpm() -> str:
    exe = shutil.which("pnpm")
    if exe is None:
        print("[dev] ✗ 未找到 pnpm，请先安装：npm i -g pnpm")
        sys.exit(1)
    return exe


# ----------------------------- setup -----------------------------
def cmd_setup(_: argparse.Namespace) -> int:
    if not VENV.exists():
        print(f"[dev] 创建虚拟环境 {VENV} …")
        rc = subprocess.call([sys.executable, "-m", "venv", str(VENV)])
        if rc != 0:
            print("[dev] ✗ 创建 venv 失败")
            return rc
    py = str(venv_python())
    print("[dev] 安装后端依赖 (requirements.txt) …")
    rc = subprocess.call([py, "-m", "pip", "install", "-r", str(QUANT / "requirements.txt")])
    if rc != 0:
        print("[dev] ✗ 后端依赖安装失败")
        return rc
    print("[dev] 安装前端依赖 (pnpm install) …")
    rc = subprocess.call([_pnpm(), "-C", str(WEB), "install"], env=child_env())
    if rc != 0:
        print("[dev] ✗ 前端依赖安装失败")
        return rc
    print("[dev] ✓ setup 完成。下一步：pnpm dev（或 python scripts/dev.py up）")
    return 0


# ----------------------------- preflight -----------------------------
def cmd_preflight(_: argparse.Namespace) -> int:
    py = venv_python()
    py_exe = str(py) if py.exists() else sys.executable
    print("[dev] OKX 连通性预检 …")
    rc = subprocess.call([py_exe, str(QUANT / "scripts" / "check_okx.py")],
                         cwd=str(QUANT), env=child_env())
    if rc != 0:
        print("[dev] ✗ 预检失败：请先解决网络 / 代理（HTTP_PROXY/HTTPS_PROXY）后再启动。")
    return rc


# ----------------------------- up -----------------------------
def _stream(proc: subprocess.Popen, prefix: str, color: str) -> None:
    assert proc.stdout is not None
    for raw in iter(proc.stdout.readline, ""):
        line = raw.rstrip("\n")
        print(f"{color}[{prefix}]{C_RESET} {line}", flush=True)


def cmd_up(args: argparse.Namespace) -> int:
    _enable_ansi()
    if not args.skip_preflight:
        if cmd_preflight(args) != 0:
            return 1

    py = venv_python()
    py_exe = str(py) if py.exists() else sys.executable
    env = child_env()

    popen_kwargs: dict = dict(
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        bufsize=1, encoding="utf-8", errors="replace",
    )
    if IS_WIN:
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True

    print("[dev] 启动后端 uvicorn (:8000) 与前端 pnpm dev (:3000) …  Ctrl-C 退出")
    api = subprocess.Popen(
        [py_exe, "-m", "uvicorn", "app.main:app", "--port", "8000", "--reload"],
        cwd=str(QUANT), env=env, **popen_kwargs)
    web = subprocess.Popen(
        [_pnpm(), "-C", str(WEB), "dev"],
        cwd=str(ROOT), env=env, **popen_kwargs)

    t_api = threading.Thread(target=_stream, args=(api, "api", C_API), daemon=True)
    t_web = threading.Thread(target=_stream, args=(web, "web", C_WEB), daemon=True)
    t_api.start()
    t_web.start()

    try:
        while True:
            if api.poll() is not None or web.poll() is not None:
                break
            t_api.join(0.3)
            t_web.join(0.3)
    except KeyboardInterrupt:
        print("\n[dev] 收到 Ctrl-C，正在优雅停止前后端 …")
    finally:
        for proc in (web, api):
            if proc.poll() is None:
                proc.terminate()
        for proc in (web, api):
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
    print("[dev] 已退出。")
    return 0


# ----------------------------- test -----------------------------
def cmd_test(_: argparse.Namespace) -> int:
    py = venv_python()
    py_exe = str(py) if py.exists() else sys.executable
    print("[dev] 后端 pytest（不含 network）…")
    rc = subprocess.call([py_exe, "-m", "pytest", "-q"], cwd=str(QUANT), env=child_env())
    if rc != 0:
        return rc
    print("[dev] 前端 vitest …")
    return subprocess.call([_pnpm(), "-C", str(WEB), "test"], env=child_env())


def main() -> int:
    parser = argparse.ArgumentParser(description="Aragon dev 编排器")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("setup")
    sub.add_parser("preflight")
    up = sub.add_parser("up")
    up.add_argument("--skip-preflight", action="store_true")
    sub.add_parser("test")
    args = parser.parse_args()

    return {
        "setup": cmd_setup,
        "preflight": cmd_preflight,
        "up": cmd_up,
        "test": cmd_test,
    }[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
