"""轴 E-1 / E-2：孤儿平仓腿费不结转、预设对照改池化（G-18 / G-19）。"""
from __future__ import annotations

import json

from sqlalchemy import delete, or_, select

from app.db.ai_auto_repositories import (
    _aggregate_group,
    _pair_round_trips,
    _pooled_rate,
    _summarize_pairs,
    _summarize_preset,
)
from app.db.models import Strategy, StrategyRun, Trade
from app.db.session import get_sessionmaker, init_db


class _T:
    """最小 Trade 替身（`_pair_round_trips` 只读这几个属性）。"""

    def __init__(self, tid: int, side: str, fee: float, pnl: float = 0.0,
                 price: float = 100.0) -> None:
        self.id, self.side, self.fee, self.pnl, self.price = tid, side, fee, pnl, price
        self.ts = tid
        self.peak_pnl = None
        self.stop_loss_price = None
        self.take_profit_price = None
        self.explain_json = None


def test_g18_orphan_close_leg_fee_does_not_carry_into_the_next_trip():
    """G-18（D-7 回归）：`open, close, close, open, close` ⇒ 第二笔往返**不含**孤儿平仓的费用。"""
    trades = [_T(1, "long", 0.01), _T(2, "close", 0.02, pnl=1.0),
              _T(3, "close", 0.99, pnl=0.5),        # ← 孤儿平仓（无未配对开仓）
              _T(4, "long", 0.03), _T(5, "close", 0.04, pnl=2.0)]
    trips = _pair_round_trips(trades, "gross")
    assert len(trips) == 2
    assert trips[0].leg_fee_usd == 0.01 + 0.02
    assert trips[1].leg_fee_usd == 0.03 + 0.04     # 合入前这里会是 0.99+0.03+0.04


def test_orphan_free_history_is_bit_identical():
    """§十.10：当前生产库零孤儿 ⇒ 已发布数字逐位不变（E-1 只改那条从不命中的分支）。"""
    trades = [_T(1, "long", 0.01), _T(2, "close", 0.02, pnl=1.0),
              _T(3, "short", 0.03), _T(4, "close", 0.04, pnl=-0.5)]
    trips = _pair_round_trips(trades, "gross")
    assert [round(t.leg_fee_usd, 6) for t in trips] == [0.03, 0.07]


def test_g19_summarize_pairs_only_gains_an_additive_wins_key():
    """G-19（[P1-7]）：`_summarize_pairs` 的**既有键**逐位不变，只多一个加法分子 `wins`。"""
    trades = [_T(1, "long", 0.0), _T(2, "close", 0.0, pnl=1.0),
              _T(3, "long", 0.0), _T(4, "close", 0.0, pnl=-0.5)]
    out = _summarize_pairs(_pair_round_trips(trades, "gross"), 100.0)
    assert out["tradeCount"] == 2 and out["winRate"] == 0.5
    assert out["realizedPnlUsd"] == 0.5 and out["bestUsd"] == 1.0 and out["worstUsd"] == -0.5
    assert out["wins"] == 1                       # 新增的那一个键
    assert out["captureRatio"] == 0.0 and out["noPeakCount"] == 2


def test_g19_preset_comparison_is_pooled_not_equally_weighted():
    """G-19（D-2 回归）：1 局 1 笔全胜 + 1 局 10 笔 3 胜 ⇒ 池化 4/11，而**不是**等权的 0.65。"""
    rows = [{"netPnlUsd": 1.0, "roundTrips": 1, "wins": 1,
             "edgeGated": 2, "edgeVeto": 2, "openIntents": 1, "openFilled": 1},
            {"netPnlUsd": -1.0, "roundTrips": 10, "wins": 3,
             "edgeGated": 100, "edgeVeto": 10, "openIntents": 20, "openFilled": 4}]
    out = _summarize_preset("balanced", rows)
    assert out["winRate"] == 4 / 11
    assert out["winRate"] != (1.0 + 0.3) / 2
    assert out["edgeVetoRate"] == 12 / 102
    assert out["openConversion"] == 5 / 21
    assert out["roundTrips"] == 11 and out["samples"] == 2


def test_pooled_rate_returns_none_on_a_zero_denominator():
    """比例的分母为 0 ⇒ `None`，**绝不填 0 冒充**（0 与「没有这个读数」是两个结论）。"""
    assert _pooled_rate([{"wins": 0, "roundTrips": 0}], "wins", "roundTrips") is None
    assert _pooled_rate([], "wins", "roundTrips") is None


async def test_aggregate_group_output_is_untouched():
    """G-19 的另一半：历史列表卡片的数字不得被本轮追溯改写（`_aggregate_group` 一个字不动）。"""
    await init_db()
    sm = get_sessionmaker()
    async with sm() as s:
        old = (await s.execute(select(StrategyRun.id).where(
            or_(StrategyRun.mode == "ai-trade",
                StrategyRun.group_id.like("aipool_%"))))).scalars().all()
        if old:
            await s.execute(delete(Trade).where(Trade.run_id.in_(old)))
            await s.execute(delete(StrategyRun).where(StrategyRun.id.in_(old)))
        await s.commit()
        sid = (await s.execute(
            select(Strategy.id).order_by(Strategy.id.asc()).limit(1))).scalar_one()
        run = StrategyRun(
            strategy_id=sid, inst_id="BTC-USDT-SWAP", bar="1H", mode="ai-trade",
            status="stopped", start_ts=1, end_ts=9,
            config_json=json.dumps({"budgetUsd": 100.0, "strategyNames": ["动量"]}),
            group_id="aipool_A", group_started_ts=1, group_total_budget_usd=100.0)
        s.add(run)
        await s.commit()
        await s.refresh(run)
        for tid, (side, price, pnl) in enumerate(
                [("long", 100.0, 0.0), ("close", 110.0, 10.0)], start=1):
            s.add(Trade(run_id=run.id, ts=tid, side=side, price=price, qty=1.0, fee=0.0,
                        pnl=pnl, position_after=0.0, reason=""))
        await s.commit()
        trades = {run.id: (await s.execute(
            select(Trade).where(Trade.run_id == run.id))).scalars().all()}
        agg = _aggregate_group([run], trades)
    assert agg["summary"]["realizedPnlUsd"] == 10.0
    assert agg["summary"]["tradeCount"] == 1 and agg["summary"]["winRate"] == 1.0
