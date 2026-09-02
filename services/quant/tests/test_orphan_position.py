"""孤儿持仓侦测（ai-trade-fill-truth-and-effective-controls 轴 B）。

门禁 `G-B1`（与轴 A 互斥的**反向**门禁）、`G-B2`、`G-B3`（告警幂等）、`G-B6`（前后端双侧文案
不含字面 `**`，此处是后端那一侧）、`G-B7`（总闸关 ⇒ 逐字回退）。
"""
from __future__ import annotations

import pytest

from app.ai_auto.orphan import (
    DEGRADE_ORPHAN_BLOCKED,
    OrphanFacts,
    classify_orphan,
    orphan_message,
    orphan_wire,
)
from app.ai_auto.open_funnel import LAYER_LABELS, LAYERS, classify_block
from app.config import Settings
from app.engine.simulator import RiskParams
from app.live_auto.executor import LiveExecutor
from app.okx.accounts import AccountCredentials

_INST = "SOL-USDT-SWAP"


def _facts(**over) -> OrphanFacts:
    base = {"projected": "flat", "abs_pos": 2.47, "settling": False,
            "fill_grace": False, "streak": 0}
    base.update(over)
    return OrphanFacts(**base)


# ---------- G-B1：与轴 A / 既有宽限互斥（**反向**门禁） ----------

def test_settling_and_fill_grace_are_never_reported_as_orphans():
    """两条轴不得互相误报。

    `settling` 期间「交易所有仓、本地 flat」是轴 A 刻意冻结立场的那 90 秒；`fill_grace` 是刚采纳
    完开仓成交而持仓读数还没跟上的那一轮。两者都是**正常的中间态**——把它们判成孤儿，等于让两条
    新轴互相把对方的告警变成噪音。
    """
    assert classify_orphan(_facts(settling=True, streak=99), confirm_rounds=3) == "none"
    assert classify_orphan(_facts(fill_grace=True, streak=99), confirm_rounds=3) == "none"


def test_a_local_position_is_axis_a_territory_not_axis_b():
    assert classify_orphan(_facts(projected="long"), confirm_rounds=3) == "none"


@pytest.mark.parametrize("abs_pos", [0.0, -1.0, float("nan"), float("inf"), None, "x"])
def test_fail_closed_on_anything_uncertain(abs_pos):
    """fail-closed 到 `"none"`：一次误报等于该标的停止交易，而误报会把真报一起废掉。"""
    assert classify_orphan(_facts(abs_pos=abs_pos), confirm_rounds=3) == "none"


# ---------- G-B2：连续确认 ----------

def test_needs_consecutive_rounds_before_confirming():
    assert classify_orphan(_facts(streak=0), confirm_rounds=3) == "pending"
    assert classify_orphan(_facts(streak=1), confirm_rounds=3) == "pending"
    assert classify_orphan(_facts(streak=2), confirm_rounds=3) == "confirmed"


def test_confirm_rounds_one_confirms_immediately():
    assert classify_orphan(_facts(streak=0), confirm_rounds=1) == "confirmed"


# ---------- wire / 文案 ----------

def test_wire_is_absent_unless_something_is_actually_there():
    assert orphan_wire("none", qty=0, avg_px=0, upl_usd=0, since_ts=0) is None
    wire = orphan_wire("confirmed", qty=2.47, avg_px=100.89, upl_usd=-1.2, since_ts=1788)
    assert wire == {"state": "confirmed", "qty": 2.47, "sinceTs": 1788,
                    "avgPx": 100.89, "uplUsd": -1.2}


def test_message_offers_three_things_the_user_can_actually_click():
    wire = orphan_wire("confirmed", qty=2.47, avg_px=100.89, upl_usd=-1.2, since_ts=1788)
    text = orphan_message(wire, inst_id=_INST, block_open=True)
    assert _INST in text and "2.47" in text
    assert "接管这笔持仓" in text and "手动平掉" in text and "停止该标的的自动交易" in text
    assert "自动开仓已暂停" in text


def test_message_never_contains_literal_bold_markers():
    """`G-B6` 后端侧：JSX 会把 `**粗体**` 原样显示成星号（本仓库第 5 次登记同一条纪律）。"""
    for block in (True, False):
        for wire in (None, orphan_wire("pending", qty=1, avg_px=0, upl_usd=0, since_ts=1)):
            assert "**" not in orphan_message(wire, inst_id=_INST, block_open=block)


# ---------- 漏斗层序与归类 ----------

def test_the_funnel_layer_sits_between_base_rule_and_horizon():
    """事实性阻断排在任何纪律之前——但仍在基础策略铁律之后。"""
    assert LAYERS.index("orphanPosition") == LAYERS.index("baseRuleMaBand") + 1
    assert LAYERS.index("orphanPosition") < LAYERS.index("horizon")
    assert LAYER_LABELS["orphanPosition"] == "交易所侧存在未接管持仓"


def test_the_degrade_code_routes_to_its_own_cell_and_collides_with_nothing():
    assert classify_block(f"{DEGRADE_ORPHAN_BLOCKED}(qty=2.47)") == "orphanPosition"
    # 与既有任何前缀都不撞：既有码一个都不许被它吞掉。
    for existing in ("base_rule_ma_band", "horizon_min_hold", "low_consensus_size",
                     "counter_trend_size", "reflection_abort", "edge_below_cost"):
        assert classify_block(existing) != "orphanPosition", existing


# ---------- G-B7：总闸关 ⇒ 对称分支整条不存在 ----------

def _executor(settings):
    async def positions_fn():
        return [{"instId": _INST, "pos": 2.47, "avgPx": 100.89, "upl": -1.2}]

    return LiveExecutor(
        creds=AccountCredentials("ak-fake", "sk-fake-000000", "pp-fake-000000"),
        account_id=1, inst_id=_INST, td_mode="cross", pos_mode="long_short_mode",
        leverage=3.0, budget_usd=1000.0, risk=RiskParams(), ct_val=1.0,
        lot_sz=0.01, min_sz=0.01, settings=settings, positions_fn=positions_fn)


@pytest.mark.anyio
async def test_detection_off_leaves_no_trace_at_all():
    ex = _executor(Settings(ai_trade_orphan_detect_enabled=False))
    for tick in range(5):
        events = await ex.reconcile(100.0, 1788 + tick)
        assert [e for e in events if e["type"] == "error"] == []
    assert ex.orphan is None and ex._orphan_streak == 0


@pytest.mark.anyio
async def test_confirmed_orphan_raises_exactly_one_alert_and_then_stays_quiet():
    """`G-B3`：转 `confirmed` ⇒ 恰好推**一条**；再持续 10 轮不再推第二条。"""
    ex = _executor(Settings(ai_trade_orphan_confirm_rounds=3))
    alerts = []
    for tick in range(13):
        events = await ex.reconcile(100.0, 1788 + tick)
        alerts += [e for e in events
                   if e["type"] == "error" and e["data"]["code"] == "AI_TRADE_ORPHAN_POSITION"]
    assert len(alerts) == 1
    assert ex.orphan is not None and ex.orphan["state"] == "confirmed"
    assert ex.orphan["qty"] == 2.47 and ex.orphan["avgPx"] == 100.89
    assert "**" not in alerts[0]["data"]["message"]


@pytest.mark.anyio
async def test_the_orphan_state_clears_when_the_position_disappears():
    """消失即复位——下次再出现要能重新告警（否则一次告警之后这条通路就永久哑了）。"""
    rows = [[{"instId": _INST, "pos": 2.47, "avgPx": 100.89, "upl": -1.2}]] * 3 + [[]]
    calls = {"n": 0}

    async def positions_fn():
        calls["n"] += 1
        return rows[min(calls["n"] - 1, len(rows) - 1)]

    ex = LiveExecutor(
        creds=AccountCredentials("ak-fake", "sk-fake-000000", "pp-fake-000000"),
        account_id=1, inst_id=_INST, td_mode="cross", pos_mode="long_short_mode",
        leverage=3.0, budget_usd=1000.0, risk=RiskParams(), ct_val=1.0,
        lot_sz=0.01, min_sz=0.01, settings=Settings(), positions_fn=positions_fn)
    for tick in range(3):
        await ex.reconcile(100.0, 1788 + tick)
    assert ex.orphan is not None and ex.orphan_alerted is True
    await ex.reconcile(100.0, 1800)
    assert ex.orphan is None and ex.orphan_alerted is False and ex._orphan_streak == 0


@pytest.mark.anyio
async def test_axis_b_never_issues_an_exchange_write(monkeypatch):
    """不变式 I-4：本轴只写状态与事件，**零交易所写请求**。"""
    writes = []

    async def boom(*args, **kwargs):
        writes.append(args)
        raise AssertionError("轴 B 绝不允许发出任何交易所写请求")

    ex = _executor(Settings())
    ex._place_order = boom
    ex._close_position = boom
    ex._cancel_order = boom
    for tick in range(6):
        await ex.reconcile(100.0, 1788 + tick)
    assert writes == []
