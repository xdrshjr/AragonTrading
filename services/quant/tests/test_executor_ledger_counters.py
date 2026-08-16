"""成交台账的计数器与内存护栏（trust-boundary-and-signal-integrity D5）。

`LiveExecutor.trades` 曾经同时是三样东西：明细台账、计数器、**换手闸判据**。只增不减的列表在
长会话上是内存隐患，但直接裁它等于**静默改变交易行为**——`discipline_runtime` 的开仓次数会变小，
换手纪律闸就悄悄放松了，而没有任何东西会红。

所以顺序不可颠倒：**先有计数器，再谈封顶**。本文件把这个顺序钉死。
"""
from __future__ import annotations

import re
from pathlib import Path

from app.config import Settings
from app.engine.simulator import RiskParams
from app.live_auto.executor import LiveExecutor
from app.okx.accounts import AccountCredentials
from app.wire_caps import TRADE_LEDGER_MAX, tail_trades

_EXECUTOR_SOURCE = Path(__file__).resolve().parents[1] / "app" / "live_auto" / "executor.py"


def _executor() -> LiveExecutor:
    """一个不碰网络的执行器：五个 OKX 缝全部注入桩，本文件只用它的台账。"""
    async def _noop(*_args, **_kwargs):
        return {}

    async def _empty(*_args, **_kwargs):
        return []

    return LiveExecutor(
        creds=AccountCredentials("ak", "sk-000000", "pp-000000"), account_id=1,
        inst_id="BTC-USDT-SWAP", td_mode="cross", pos_mode="long_short_mode",
        leverage=3.0, budget_usd=1000.0, risk=RiskParams(stop_loss_pct=0.05),
        ct_val=0.01, lot_sz=0.01, min_sz=0.01, settings=Settings(),
        place_order_fn=_noop, close_position_fn=_noop, positions_fn=_empty, fills_fn=_empty)


def _trade(side: str, *, pnl: float = 0.0) -> dict:
    return {"ts": 1, "side": side, "price": 30000.0, "qty": 1.0, "fee": 0.1, "pnl": pnl}


# ============================ 结构：入账只有一个入口 ============================
def test_every_append_goes_through_record_trade():
    """`self.trades.append(` 在整个文件里只许出现 **1** 次（就是 `_record_trade` 内部那一次）。

    这条是结构性护栏，不是风格洁癖：新增一个 append 点却忘了更新计数器不会报错，只会让换手闸
    悄悄放松、让落库的 `tradeCount` 悄悄低报——那正是本轮要消灭的那类静默缺陷。
    """
    source = _EXECUTOR_SOURCE.read_text(encoding="utf-8")
    assert len(re.findall(r"self\.trades\.append\(", source)) == 1
    assert len(re.findall(r"def _record_trade\(", source)) == 1


def test_realized_pnl_has_exactly_one_accumulator():
    """已实现盈亏只许有 `realized_pnl_usd` 一个累加器。

    加计数器时很容易顺手再加一个「按成交腿累加的总额」，它看起来与 `trade_count` / `open_count`
    对称——但那两个替代的是「数列表长度」，而已实现盈亏本来就有真值，再累一份就是同一个数落两份。
    两份必然漂移：`_synthesize_external_close` 整个包在 `except Exception: return []` 里，
    合成失败时 `realized_pnl_usd` 照加而成交腿那一份不加，而漂移的是**真实资金的盈亏显示**
    （`ai_auto/resume.rebuild_continuity` 的不变式原文）。
    """
    source = _EXECUTOR_SOURCE.read_text(encoding="utf-8")
    assert set(re.findall(r"self\.(realized_[a-z_]+)\s*\+=", source)) == {"realized_pnl_usd"}


def test_ledger_container_is_a_plain_list_not_a_deque():
    """容器类型必须是 `list`。

    `deque(maxlen=…)` 看起来是更"正确"的封顶写法，实测却会炸：`wire_caps.tail_trades` 的实现是
    `trades[-cap:]`，而 deque 不支持切片（`TypeError: sequence index must be integer, not 'slice'`），
    调用点是两条会话的**快照下发主路径**。下面 `test_tail_trades_still_works_after_the_cap_kicks_in`
    是行为侧的看守，这一条是类型侧的。
    """
    assert type(_executor().trades) is list


# ============================ 行为：计数器不受封顶影响 ============================
def test_counters_track_every_recorded_trade():
    executor = _executor()
    for _ in range(3):
        executor._record_trade(_trade("long"))
        executor._record_trade(_trade("close", pnl=5.0))
    executor._record_trade(_trade("short"))

    assert executor.trade_count == 7
    assert executor.open_count == 4          # 3 long + 1 short；close 不算开仓


def test_counters_stay_exact_after_the_cap_trims_the_ledger():
    """灌满超过上限后：明细被裁，三个计数器仍是**全量真值**。"""
    executor = _executor()
    total = TRADE_LEDGER_MAX + 5000
    for i in range(total):
        executor._record_trade(_trade("long" if i % 2 == 0 else "close",
                                      pnl=1.0 if i % 2 else 0.0))

    assert len(executor.trades) <= TRADE_LEDGER_MAX + 500      # 按块裁，允许一个迟滞窗口
    assert len(executor.trades) >= TRADE_LEDGER_MAX
    assert executor.trade_count == total                        # ← 这才是下游读的那个数
    assert executor.open_count == total // 2


def test_the_ledger_keeps_the_newest_trades_not_the_oldest():
    executor = _executor()
    for i in range(TRADE_LEDGER_MAX + 2000):
        trade = _trade("long")
        trade["ts"] = i
        executor._record_trade(trade)

    assert executor.trades[-1]["ts"] == TRADE_LEDGER_MAX + 1999


def test_tail_trades_still_works_after_the_cap_kicks_in():
    """封顶已触发的状态下 `tail_trades` 必须照常返回一个新 list（P0-2 的专用看守）。

    把容器换回 `deque` 的那一刻，这条立刻红——这正是它存在的唯一理由。
    """
    executor = _executor()
    for _ in range(TRADE_LEDGER_MAX + 1000):
        executor._record_trade(_trade("long"))

    tail = tail_trades(executor.trades)
    assert isinstance(tail, list)
    assert len(tail) == 2000
    assert tail is not executor.trades          # 绝不把内部真值列表借出去


# ============================ 恢复：派生而不是落库 ============================
def test_restore_trades_derives_the_counters_in_place():
    rows = [_trade("long"), _trade("close", pnl=5.0), _trade("short"), _trade("close", pnl=-2.0)]

    executor = _executor()
    executor.restore_trades(rows)

    assert executor.trade_count == 4
    assert executor.open_count == 2
    assert executor.trades == rows


def test_restore_then_record_keeps_counting_from_the_restored_total():
    """续跑后新成交要接着数，而不是从 0 重新开始。"""
    executor = _executor()
    executor.restore_trades([_trade("long"), _trade("close", pnl=5.0)])
    executor._record_trade(_trade("short"))

    assert executor.trade_count == 3
    assert executor.open_count == 2


def test_restore_replaces_in_place_and_applies_the_same_cap():
    executor = _executor()
    original = executor.trades
    executor.restore_trades([_trade("long") for _ in range(TRADE_LEDGER_MAX + 3000)])

    assert executor.trades is original                          # 原地替换，不换容器对象
    assert len(executor.trades) <= TRADE_LEDGER_MAX + 500
    assert executor.trade_count == TRADE_LEDGER_MAX + 3000      # 计数仍是全量
