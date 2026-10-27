"""跨仓源码门禁：导出墙钟预算必须留在 Next 代理的硬墙之内（评审 RP0-2）。

`apps/web/next.config.mjs` 的 `experimental.proxyTimeout` 对**每一个** `/api/py/*` 请求生效，
而导出走的正是同一条 rewrite。本仓已经为同一形状付过三次代价
（`docs/diagnoses/desktop-backtest-proxy-timeout-socket-hang-up/`、
`factor-lab-train-long-range-socket-hang-up/`、`desktop-paper-batch-start-socket-reset/`），
症状一律是传输层的 `socket hang up / ECONNRESET`，而**后端日志全绿**。

这道门禁的价值不在今天——它在于**下一个人把 `proxyTimeout` 调小的时候会当场判红**，
而不是让用户在半年后收到一个 0 字节的文件。

⚠️ 解析不出数字时**判红而不是跳过**：一道会自己变成空转的门禁等于没有门禁。
"""
from __future__ import annotations

import pathlib
import re

from app.config import Settings

_ROOT = pathlib.Path(__file__).resolve().parents[3]
_NEXT_CONFIG = _ROOT / "apps" / "web" / "next.config.mjs"

# 预算与硬墙之间必须留的余量（秒）：覆盖首字节前的 COUNT / 索引扫描与桌面端的磁盘抖动。
REQUIRED_HEADROOM_S = 30


def _proxy_timeout_ms() -> int:
    assert _NEXT_CONFIG.exists(), (
        f"{_NEXT_CONFIG} 不存在 ⇒ 这道门禁失去了它的参照物。**不要把这条改成 skip**："
        "一道会自己变成空转的门禁等于没有门禁。")
    text = _NEXT_CONFIG.read_text(encoding="utf-8")
    match = re.search(r"proxyTimeout\s*:\s*(\d+)", text)
    assert match, "next.config.mjs 里解析不出 proxyTimeout 的字面量 ⇒ 判红（同上，不许跳过）"
    return int(match.group(1))


def test_export_time_budget_leaves_headroom_under_the_proxy_wall():
    budget = float(Settings().ai_trade_journal_export_time_budget_s)
    wall_s = _proxy_timeout_ms() / 1000.0
    assert budget + REQUIRED_HEADROOM_S <= wall_s, (
        f"AI_TRADE_JOURNAL_EXPORT_TIME_BUDGET_S({budget}) + {REQUIRED_HEADROOM_S}s 余量必须 ≤ "
        f"next.config.mjs 的 proxyTimeout({wall_s}s)，否则导出会被代理在传输层撕断，"
        "而后端日志全绿")


def test_the_wall_itself_is_still_a_positive_number():
    """`proxyTimeout` 为 0 会被 Next 的 `proxyTimeout || 30000` 退回 30 秒——那是另一堵更矮的墙。"""
    assert _proxy_timeout_ms() > 0
