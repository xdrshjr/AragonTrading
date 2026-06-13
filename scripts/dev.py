"""跨平台开发编排器 (F1)：一条命令完成预检 + 依赖就绪 + 同时拉起前后端。

用法（仓库根目录）：
    python scripts/dev.py setup                 # 建 venv + 装依赖（Python + pnpm）
    python scripts/dev.py preflight             # OKX 连通性预检
    python scripts/dev.py up [--skip-preflight] # 预检 → 端口检查 → 并发起后端(18760) + 前端(18761)
    python scripts/dev.py test                  # 后端 pytest + 前端 vitest

纯 Python，无第三方依赖；Windows / macOS / Linux 行为一致：
- 子进程注入 PYTHONUTF8=1，透传 HTTP(S)_PROXY；正斜杠路径。
- Windows 用 CREATE_NEW_PROCESS_GROUP，POSIX 用进程组，Ctrl-C 双进程优雅退出。
"""
from __future__ import annotations

import argparse
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QUANT = ROOT / "services" / "quant"
WEB = ROOT / "apps" / "web"
VENV = QUANT / ".venv"
IS_WIN = os.name == "nt"

# 端口（F1）：刻意选用不常用端口，规避与 3000/8000/8080/5173 等常见开发端口、以及
# Windows 动态/保留端口段（50000+）的冲突。改动时必须同步以下四处保持一致：
#   - 前端监听端口：apps/web/package.json 的 dev 脚本（next dev -p 18761）
#   - Next 代理目标：apps/web/next.config.mjs 的 PY_BACKEND_URL 默认值
#   - 浏览器 WS：    apps/web/lib/ws.ts 的 NEXT_PUBLIC_PY_WS_URL 默认值
#   - 容器编排：     docker-compose.yml 与两个 Dockerfile
API_PORT = 18760   # FastAPI / uvicorn 后端
WEB_PORT = 18761   # Next.js 前端

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


# ----------------------------- 端口 / 进程树工具 -----------------------------
def _port_free(port: int, host: str = "127.0.0.1") -> bool:
    """端口能否绑定（即未被占用）。仅做无害的 bind 探测后立即释放，不残留监听。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def _holder_hint(port: int) -> str:
    """定位占用该端口进程的命令提示（不代杀，交由用户判断是否为本项目残留）。"""
    if IS_WIN:
        return (f"PowerShell> Get-NetTCPConnection -LocalPort {port} | "
                f"Select OwningProcess ; Stop-Process -Id <PID> -Force")
    return f"lsof -i :{port}   # 再  kill <PID>"


def _signal_group(proc: subprocess.Popen) -> None:
    """优雅终止子进程整组：Win 发 CTRL_BREAK_EVENT（依赖 CREATE_NEW_PROCESS_GROUP），
    POSIX 发 SIGTERM 给进程组，使 uvicorn(--reload) 的工作子进程与 next 一并收敛。"""
    if proc.poll() is not None:
        return
    try:
        if IS_WIN:
            proc.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, OSError):
        pass


def _kill_tree(proc: subprocess.Popen) -> None:
    """强杀子进程整棵树（兜底）：杜绝 uvicorn --reload 工作子进程 / next 子进程变孤儿占住端口
    ——正是这种孤儿导致再次启动时 Windows 报 [WinError 10013]。"""
    if proc.poll() is not None:
        return
    if IS_WIN:
        subprocess.call(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, OSError):
            pass


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

    # 端口预检（F1）：启动前确认两个端口都空闲，避免被占用时撞上 Windows 那条隐晦的
    # [WinError 10013] —— 直接给出占用端口与定位/释放命令，而非让 uvicorn 崩在半路。
    busy = [p for p in (API_PORT, WEB_PORT) if not _port_free(p)]
    if busy:
        for p in busy:
            print(f"[dev] ✗ 端口 {p} 已被占用，无法启动。定位并释放后重试：")
            print(f"        {_holder_hint(p)}")
        return 1

    popen_kwargs: dict = dict(
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        bufsize=1, encoding="utf-8", errors="replace",
    )
    if IS_WIN:
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True

    print(f"[dev] 启动后端 uvicorn (:{API_PORT}) 与前端 pnpm dev (:{WEB_PORT}) …  Ctrl-C 退出")
    api = subprocess.Popen(
        [py_exe, "-m", "uvicorn", "app.main:app", "--port", str(API_PORT), "--reload"],
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
        # 先整组优雅终止（让 uvicorn/next 善后）；超时未退再强杀整棵进程树，
        # 防止 reload 工作子进程残留占住端口（否则下次启动即 WinError 10013）。
        for proc in (web, api):
            _signal_group(proc)
        for proc in (web, api):
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                _kill_tree(proc)
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


def _force_utf8_stdio() -> None:
    """把本编排器自身的 stdout/stderr 重配为 UTF-8。否则在 GBK(cp936) 控制台或输出被重定向到
    文件时，聚合打印子进程日志里的 ✓ / 中文 / emoji 会抛 UnicodeEncodeError 直接打挂 _stream 线程
    （子进程已注入 PYTHONUTF8，问题只在父进程这一侧）。与 smoke_e2e.py 的处理一致。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass


def main() -> int:
    _force_utf8_stdio()
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
