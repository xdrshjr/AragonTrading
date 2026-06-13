"""PyInstaller 冻结后端入口 (spec §4.6 ① / P1-4)。

解析 --port（默认 18760）后**直接导入 app 对象再启动**：
    from app.main import app
    uvicorn.run(app, host="127.0.0.1", port=port)   # 不传字符串、不开 reload
冻结包内字符串导入解析易失败，直接传对象更稳健；reload 仅 dev 需要，prod 必须关。
DB 路径、代理等由 Electron 主进程经环境变量注入（DB_PATH / HTTP(S)_PROXY），后端配置已支持。
"""
from __future__ import annotations

import argparse
import multiprocessing
import os
import sys


def main() -> None:
    multiprocessing.freeze_support()  # Windows 冻结态多进程安全（兜底）

    parser = argparse.ArgumentParser(description="Aragon Quant backend (frozen)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("ARAGON_BACKEND_PORT", "18760")))
    parser.add_argument("--host", default="127.0.0.1")
    args, _ = parser.parse_known_args()

    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

    import uvicorn
    from app.main import app  # noqa: WPS433 — 延迟导入，确保冻结环境路径已就绪

    uvicorn.run(app, host=args.host, port=args.port, reload=False, log_level="info")


if __name__ == "__main__":
    sys.exit(main())
