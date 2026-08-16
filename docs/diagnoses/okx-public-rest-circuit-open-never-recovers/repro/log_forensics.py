"""从桌面端 backend.log 重新导出本诊断引用的全部证据（只读，不改任何东西）。

用法（仓库根目录）::

    python docs/diagnoses/okx-public-rest-circuit-open-never-recovers/repro/log_forensics.py
    python docs/diagnoses/.../repro/log_forensics.py --log <某个 backend.log 路径>

默认读 ``%APPDATA%/aragon-desktop/logs/backend.log``（生产日志，本机 135MB，全量扫一遍约 30s）。

输出三张表：

1. **事件时间线**：公共读成功 / 清扫协程探针失败 / 进程重启 三种事件的**状态切换点**。
   本次故障的判据就在这张表上——绝对不恢复的那几段，出口无一例外是「进程重启」。
2. **逐小时公共 vs 私有成功数**：同一进程、同一代理、同一主机下，公共读归零而私有读满速，
   证明「网络坏了」不成立。
3. **清扫协程失败间隔直方图**：60s 睡眠 + 单次调用耗时。峰值落在 80.0s ⇒ 单次调用恒为 20.0s，
   即 ``httpx.Timeout(20.0, connect=10.0)`` 的 read/pool 档，而**从不**是 10s 的 connect 档。

Windows 控制台若为 GBK 代码页，中文会乱码；``chcp 65001`` 可修。
"""
from __future__ import annotations

import argparse
import collections
import datetime
import io
import os
import re

TS = re.compile(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d")
PUB = ("okx.com/api/v5/market/", "okx.com/api/v5/public/")
SWEEPER = "okx link sweeper tick skipped"
RESTART = "Application startup complete"


def default_log() -> str:
    root = os.environ.get("APPDATA") or os.path.expanduser("~/AppData/Roaming")
    return os.path.join(root, "aragon-desktop", "logs", "backend.log")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default=default_log())
    ap.add_argument("--since", default="2026-08-13", help="只统计该日期起的记录")
    args = ap.parse_args()

    timeline: list[tuple[str, str]] = []
    pub_hour: collections.Counter[str] = collections.Counter()
    priv_hour: collections.Counter[str] = collections.Counter()
    sweeper_ts: list[datetime.datetime] = []
    ts = None

    with io.open(args.log, "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if TS.match(line):
                ts = line[:19]
            if ts is None or ts[:10] < args.since:
                continue
            hour = ts[:13]
            if "okx.com/api/v5/" in line and "200 OK" in line:
                if any(p in line for p in PUB):
                    pub_hour[hour] += 1
                    timeline.append((ts, "PUB_OK"))
                else:
                    priv_hour[hour] += 1
            elif SWEEPER in line:
                timeline.append((ts, "PROBE_FAIL"))
                sweeper_ts.append(
                    datetime.datetime.strptime(line[:23], "%Y-%m-%d %H:%M:%S,%f"))
            elif RESTART in line:
                timeline.append((ts, "RESTART"))

    print("== 1. 事件时间线（只打状态切换点）==")
    prev = None
    for stamp, kind in timeline:
        if kind != prev:
            print(f"{stamp}  {kind}")
        prev = kind

    print("\n== 2. 逐小时 OKX 200 OK 计数 ==")
    print("hour            public  private")
    for hour in sorted(set(list(pub_hour) + list(priv_hour))):
        print(f"{hour} {pub_hour.get(hour, 0):8d} {priv_hour.get(hour, 0):8d}")

    print("\n== 3. 清扫协程失败间隔直方图（60s 睡眠 + 单次调用耗时）==")
    deltas: collections.Counter[float] = collections.Counter()
    for a, b in zip(sweeper_ts, sweeper_ts[1:]):
        seconds = (b - a).total_seconds()
        if seconds < 600:
            deltas[round(seconds, 1)] += 1
    print(f"samples={len(sweeper_ts)}")
    for key, count in sorted(deltas.items()):
        print(f"{key:8.1f}s  {count}")


if __name__ == "__main__":
    main()
