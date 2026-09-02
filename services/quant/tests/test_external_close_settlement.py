"""`LiveExecutor` 的 settling 状态机（ai-trade-fill-truth-and-effective-controls 轴 A）。

覆盖门禁 `G-A2`~`G-A8`、`G-A10`、`G-A11`、`G-A13`（双记防护，**合入前必须红**）、
`G-A14`（净额口径，**合入前必须红**）、`G-A16`（费率残差为负不静默夹 0）。
"""
from __future__ import annotations

import time

import pytest

from app.config import Settings
from app.engine.simulator import RiskParams
from app.live_auto.executor import LiveExecutor
from app.live_auto.fee_ledger import build_fee_ledger
from app.okx.accounts import AccountCredentials
from app.okx.rest_client import OKXError

_INST = "SOL-USDT-SWAP"
_ENTRY_PX = 100.89
_MARK_PX = 100.52          # 侦测那一刻的本地标记价（今天就是按它捏价的）
_CLOSE_PX = 99.88          # 交易所真实结算价
_SZ = 2.0
_CT_VAL = 1.0
_TS = 1_788_285_000


def _settlement_row(**over) -> dict:
    base = {"posId": "p1", "instId": _INST, "direction": "long",
            "openAvgPx": _ENTRY_PX, "closeAvgPx": _CLOSE_PX,
            "cTime": _TS - 3600, "uTime": _TS + 2,
            "pnl": -2.02, "fee": 0.25, "fundingFee": 0.0, "liqPenalty": 0.0,
            "realizedPnl": -2.27, "identityGapUsd": 0.0}
    base.update(over)
    return base


def _make(*, settlement_rows=None, settlement_exc=None, settings=None, positions_seq=None):
    """带结算缝的执行器；`settlement_rows is None` ⇒ **不传缝** ⇒ 状态机整条不存在（`G-A10`）。"""
    calls = {"settlement": 0, "closes": [], "reads": 0}

    async def positions_fn():
        calls["reads"] += 1
        if positions_seq:
            return positions_seq[min(calls["reads"] - 1, len(positions_seq) - 1)]
        return []

    async def close_position_fn(close):
        calls["closes"].append(close)
        return {"ok": True, "clOrdId": "c1", "ordId": "o1"}

    async def settlement_fn():
        calls["settlement"] += 1
        if settlement_exc is not None:
            raise settlement_exc
        return list(settlement_rows or [])

    executor = LiveExecutor(
        creds=AccountCredentials("ak-fake", "sk-fake-000000", "pp-fake-000000"),
        account_id=1, inst_id=_INST, td_mode="cross", pos_mode="long_short_mode",
        leverage=3.0, budget_usd=1000.0, risk=RiskParams(), ct_val=_CT_VAL,
        lot_sz=0.01, min_sz=0.01, settings=settings or Settings(),
        # 费率真值只能经 `FeeLedger` 落到 `trade["fee"]` 上（`trade_fee_fields` 在账本缺席时返回
        # `{}`，键集合逐字回退）。`live_auto` 恒不装配账本，也恒不装配结算缝——两者天然一致。
        fee_ledger=build_fee_ledger(settings or Settings(), ct_val=_CT_VAL,
                                    fee_rates=None, fee_rates_error=None),
        close_position_fn=close_position_fn, positions_fn=positions_fn,
        settlement_fn=None if settlement_rows is None and settlement_exc is None
        else settlement_fn)
    executor.projected = "long"
    executor.entry_price = _ENTRY_PX
    executor.entry_qty_sz = _SZ
    executor.entry_bar = 0
    executor.trades.append({"side": "long", "ts": _TS - 3600, "wallTs": _TS - 3600,
                            "price": _ENTRY_PX, "qty": _SZ})
    return executor, calls


def _trades(events):
    return [e["data"] for e in events if e["type"] == "trade"]


# ---------- G-A10：取数缝缺席 ⇒ 状态机整条不存在（live_auto 全部路径） ----------

@pytest.mark.anyio
async def test_without_the_seam_the_state_machine_does_not_exist():
    """`settlement_fn is None` ⇒ 一个请求都不发、立刻按 mark_px 合成、`explain.meta` 零加法键。"""
    ex, calls = _make()
    events = await ex.reconcile(_MARK_PX, _TS)
    trades = _trades(events)
    assert len(trades) == 1 and trades[0]["price"] == _MARK_PX
    assert ex.projected == "flat" and ex._settling is None
    assert calls["settlement"] == 0
    assert ex.take_settlement_meta() is None          # A-4 ⓐ 列：轮级槽都没建过


# ---------- G-A8：总闸关 ⇒ 逐字回退 ----------

@pytest.mark.anyio
async def test_master_switch_off_restores_todays_behaviour_byte_for_byte():
    ex, calls = _make(settlement_rows=[_settlement_row()],
                      settings=Settings(ai_trade_settlement_truth_enabled=False))
    events = await ex.reconcile(_MARK_PX, _TS)
    assert _trades(events)[0]["price"] == _MARK_PX
    assert calls["settlement"] == 0 and ex._settling is None
    assert ex.take_settlement_meta() is None


# ---------- G-A4：settling 期间冻结 ----------

@pytest.mark.anyio
async def test_settling_freezes_stance_and_books_nothing():
    """立场 / 已实现盈亏不变、浮盈冻结、**零 trade 事件、零落库**（不变式 I-1 / I-2）。"""
    ex, _ = _make(settlement_rows=[])                       # 恒不命中 ⇒ 一直挂在未决态
    ex.unrealized_usd = ex.gross_unrealized_usd = -0.74
    before = len(ex.trades)
    events = await ex.reconcile(_MARK_PX, _TS)
    assert ex._settling is not None
    assert ex.projected == "long" and ex.realized_pnl_usd == 0.0
    assert _trades(events) == [] and len(ex.trades) == before
    for tick in range(3):                                   # 交易所侧已空 ⇒ _net_upl 恒 0
        await ex.reconcile(_MARK_PX + tick, _TS + tick + 1)
    assert ex.unrealized_usd == -0.74                        # 冻结在侦测那一刻，不被写成 0
    assert ex.gross_unrealized_usd == -0.74


# ---------- G-A2：命中即按交易所口径落账 ----------

@pytest.mark.anyio
async def test_hit_books_the_exchange_price_and_the_fee_residual():
    ex, _ = _make(settlement_rows=[_settlement_row()])
    ex.entry_fee_usd = 0.10                                  # 本地已记的开仓腿费
    await ex.reconcile(_MARK_PX, _TS)                        # ① 侦测
    events = await ex.reconcile(_MARK_PX, _TS + 1)           # ② 轮询命中
    trade = _trades(events)[0]
    assert trade["price"] == _CLOSE_PX                       # 不是 _MARK_PX
    assert trade["fee"] == pytest.approx(0.25 - 0.10)        # 往返总费 − 已记开仓腿费
    assert ex.projected == "flat"
    slot = ex.take_settlement_meta()
    assert slot is not None and slot["fact"].pos_id == "p1"


# ---------- G-A14（RV-P0-2，合入前必须红）：pnl 的口径开关 ----------

@pytest.mark.anyio
async def test_pnl_basis_follows_net_pnl_switch_not_the_accounting_switch():
    """`accounting=True & net_pnl=False` 的断面上必须落**毛额**。

    两把开关是独立的，而 `trades.pnl` 是毛还是净只由 `ai_trade_net_pnl_enabled` 决定
    （`reconcile.local_net_view` 的 `net_mode` 形参取的就是它）。按「fee_ledger 已装配」判，会往
    一个毛额口径的会话里写一条净额 pnl，随后 `local_net_view(net_mode=False)` 再扣一次费 ⇒
    对账差额被人为放大、历史聚合二次扣费（评审 RV-P0-2 / 放行条件 C-2）。
    """
    gross_settings = Settings(ai_trade_fee_accounting_enabled=True,
                              ai_trade_net_pnl_enabled=False)
    ex, _ = _make(settlement_rows=[_settlement_row()], settings=gross_settings)
    await ex.reconcile(_MARK_PX, _TS)
    events = await ex.reconcile(_MARK_PX, _TS + 1)
    assert _trades(events)[0]["pnl"] == pytest.approx(-2.02)          # 毛额，未扣 0.25

    net_settings = Settings(ai_trade_fee_accounting_enabled=True,
                            ai_trade_net_pnl_enabled=True)
    ex2, _ = _make(settlement_rows=[_settlement_row()], settings=net_settings)
    await ex2.reconcile(_MARK_PX, _TS)
    events2 = await ex2.reconcile(_MARK_PX, _TS + 1)
    assert _trades(events2)[0]["pnl"] == pytest.approx(-2.02 - 0.25)  # 净额


# ---------- G-A16（RV-P1-7）：费率残差为负 ⇒ 降级留痕，绝不静默夹 0 ----------

@pytest.mark.anyio
async def test_negative_fee_residual_degrades_to_local_mark_with_a_reason():
    """本地已记开仓腿费 > 交易所往返总费 = 明确的不自洽。宁可缺一笔并说明，绝不 `max(0.0, …)`。"""
    ex, _ = _make(settlement_rows=[_settlement_row(fee=0.05)])
    ex.entry_fee_usd = 0.50
    await ex.reconcile(_MARK_PX, _TS)
    events = await ex.reconcile(_MARK_PX, _TS + 1)
    trade = _trades(events)[0]
    assert trade["price"] == _MARK_PX                        # 退回本地合成
    slot = ex.take_settlement_meta()
    assert slot["fact"] is None and slot["rejectReason"] == "fee_residual_negative"
    assert ex.settlement_local_mark_count == 1 and ex.settlement_exchange_count == 0


# ---------- G-A5：幻影撤销 ----------

@pytest.mark.anyio
async def test_position_coming_back_cancels_the_wait_without_booking_anything():
    ex, _ = _make(settlement_rows=[],
                  positions_seq=[[], [{"instId": _INST, "pos": _SZ, "upl": -0.5}]])
    ex.unrealized_usd = -0.74
    await ex.reconcile(_MARK_PX, _TS)
    assert ex._settling is not None
    before = len(ex.trades)
    events = await ex.reconcile(_MARK_PX, _TS + 1)
    assert ex._settling is None and ex.projected == "long"
    assert len(ex.trades) == before and _trades(events) == []
    assert ex.unrealized_usd == -0.74                        # 冻结值被还回去，不是被清成 0


# ---------- G-A6：取数抛异常 ⇒ 绝不把腿丢掉 ----------

@pytest.mark.anyio
async def test_fetch_exception_never_loses_the_close():
    ex, _ = _make(settlement_exc=OKXError("OKX_DOWN", "boom"))
    ex.settings = Settings(ai_trade_settlement_deadline_s=1.0,
                           ai_trade_settlement_poll_interval_s=0.5)
    await ex.reconcile(_MARK_PX, _TS)
    assert ex._settling is not None
    ex._settling.deadline = time.time() - 1                  # 死线已过
    events = await ex.reconcile(_MARK_PX, _TS + 2)
    trade = _trades(events)[0]
    assert trade["price"] == _MARK_PX and ex.projected == "flat"


# ---------- G-A3：超时落账的价格与今天逐位相同 ----------

@pytest.mark.anyio
async def test_deadline_books_exactly_what_today_would_book():
    ex, _ = _make(settlement_rows=[])
    baseline, _ = _make()                                    # 无缝 ⇒ 今天的行为
    await ex.reconcile(_MARK_PX, _TS)
    ex._settling.deadline = time.time() - 1
    events = await ex.reconcile(_MARK_PX, _TS + 1)
    base_events = await baseline.reconcile(_MARK_PX, _TS)
    got, want = _trades(events)[0], _trades(base_events)[0]
    for key in ("price", "pnl", "fee", "qty", "reason"):
        assert got[key] == want[key], key
    slot = ex.take_settlement_meta()
    assert slot["fact"] is None                              # ⓑ 列：留痕但没拿到


# ---------- G-A13（RV-P0-3，合入前必须红）：双记防护 ----------

@pytest.mark.anyio
async def test_outbound_close_is_short_circuited_while_settling():
    """`settling` 期间 `_do_close` / `flatten` **不发任何交易所写请求**（不变式 I-2b 上半）。"""
    ex, calls = _make(settlement_rows=[])
    await ex.reconcile(_MARK_PX, _TS)
    assert ex._settling is not None
    events = await ex._do_close(_MARK_PX, _TS + 1, "profit_guard")
    assert calls["closes"] == []                             # 零交易所写请求
    assert ex.last_close_block == "settlement_pending"
    assert any(e["type"] == "status" for e in events)


@pytest.mark.anyio
async def test_finish_settling_is_idempotent_when_another_path_booked_first():
    """`_settle_benign_close` 抢先落账后，`_finish_settling` **只清状态、一个字节都不落**。

    这是本轮唯一一条能同时污染真实资金账本与下游全部学习样本的风险：
    `_synthesize_external_close` 的首行守卫只挡住第二条 `trade`，**挡不住调用方那一行
    `realized_pnl_usd +=`**（评审 RV-P0-3 / 放行条件 C-3）。
    """
    ex, _ = _make(settlement_rows=[_settlement_row()])
    await ex.reconcile(_MARK_PX, _TS)
    assert ex._settling is not None
    # 从外部把这笔仓按别的通路落掉（模拟 `_settle_benign_close` 抢先）
    ex._settle_benign_close(_MARK_PX, _TS + 1)
    realized_after_first = ex.realized_pnl_usd
    trades_after_first = len(ex.trades)
    assert ex.projected == "flat" and realized_after_first != 0.0
    # 现在喂一条会命中的结算行
    events = await ex.reconcile(_MARK_PX, _TS + 2)
    assert ex.realized_pnl_usd == realized_after_first        # 只被加过一次
    assert len(ex.trades) == trades_after_first               # 只多一条腿（就是刚才那条）
    assert _trades(events) == []
    assert ex._settling is None


# ---------- G-A7：停手收尾一定落账 ----------

@pytest.mark.anyio
async def test_drain_on_stop_always_books_the_close():
    """绝不把一条已经发生的平仓留在 settling 里带进关停（否则这笔往返在历史里整条消失）。"""
    ex, _ = _make(settlement_rows=[])
    await ex.reconcile(_MARK_PX, _TS)
    assert ex._settling is not None
    events = await ex.drain_settlement(_TS + 5)
    assert ex._settling is None and ex.projected == "flat"
    assert _trades(events)[0]["price"] == _MARK_PX


@pytest.mark.anyio
async def test_flatten_drains_the_pending_settlement_before_closing():
    """`flatten` 先收结算再判早退——绝不在一笔已经平掉的仓位上再发一张平仓单。"""
    ex, calls = _make(settlement_rows=[_settlement_row()])
    await ex.reconcile(_MARK_PX, _TS)
    result = await ex.flatten(_MARK_PX, _TS + 1)
    assert calls["closes"] == []                             # 早退命中，零平仓请求
    assert result["ok"] and ex.projected == "flat"


# ---------- G-A11：posId 淘汰后最坏退化为 local_mark ----------

@pytest.mark.anyio
async def test_claimed_pos_ids_are_capped_and_never_cause_a_wrong_price():
    ex, _ = _make(settlement_rows=[_settlement_row()])
    for index in range(300):
        ex._claim_pos_id(f"p{index}")
    assert len(ex._settled_pos_ids) == 256
    assert ex._settled_pos_ids[0] == "p44"                   # 最旧的被淘汰
    assert ex._settled_pos_ids[-1] == "p299"


# ---------- 轮级槽的异常出口（实现期自查，spec 未列） ----------

@pytest.mark.anyio
async def test_the_meta_slot_is_cleared_when_no_trade_was_produced():
    """合成抛异常被 best-effort 吞掉 ⇒ 槽必须就地清空，绝不让**下一笔**平仓认领它。

    `_synthesize_external_close` 的 `except` 返回 `[]`（合成失败绝不影响 reconcile 主流程）。此时
    槽里那份 meta 已经没有归属者了；不清掉，下一笔任意平仓都会带上一份张冠李戴的结算归因——而它
    看起来完全正常。轮级槽的成文纪律是「消费即清空」，这里是它的异常出口。
    """
    ex, _ = _make(settlement_rows=[_settlement_row()])
    await ex.reconcile(_MARK_PX, _TS)
    assert ex._settling is not None

    def boom(*args, **kwargs):
        raise RuntimeError("explain 合成炸了")

    ex._make_trade = boom
    events = await ex.reconcile(_MARK_PX, _TS + 1)
    assert _trades(events) == []                     # 合成失败：没有成交
    assert ex.take_settlement_meta() is None         # ★ 槽已清空，不会被下一笔认领
    assert ex.projected == "flat"                    # 立场仍照常复位（绝不卡在有仓态）


# ---------- 降级恢复不得被结算窗口推迟（实现期自查，spec 未列） ----------

@pytest.mark.anyio
async def test_connection_recovery_is_still_acknowledged_while_settling():
    """未决态期间读到一份成功的持仓快照 ⇒ 照常消降级、推「已恢复」。

    `_clear_degraded_if_recovered` 原本住在 `_on_reconcile_ok` 里，而未决态**不进**那个函数。
    不在分派处补一次，一次落在结算窗口内的连接恢复就会被推迟最多一整个 deadline（90s）才被
    承认，界面上那条「盯市已降级」会挂着不走——那是一条与轴 A 毫无关系的静默回归。
    """
    ex, _ = _make(settlement_rows=[])
    await ex.reconcile(_MARK_PX, _TS)                    # 进未决态
    assert ex._settling is not None
    ex.degraded = True                                   # 模拟此前已降级
    ex._reconcile_fail_streak = 3
    events = await ex.reconcile(_MARK_PX, _TS + 1)
    assert ex.degraded is False and ex._reconcile_fail_streak == 0
    assert any("已恢复" in (e["data"].get("message") or "")
               for e in events if e["type"] == "status")
    assert ex._settling is not None                      # 恢复不影响未决态本身


# ---------- G-A12：资金费与强平罚金不进本地账本 ----------

@pytest.mark.anyio
async def test_funding_and_liq_never_enter_the_local_ledger():
    """`reconcile` 的可比口径 `realized + funding + liq` 依赖本地不记这两项。"""
    ex, _ = _make(settlement_rows=[_settlement_row(fundingFee=1.5, liqPenalty=2.5)])
    ex.entry_fee_usd = 0.0
    await ex.reconcile(_MARK_PX, _TS)
    events = await ex.reconcile(_MARK_PX, _TS + 1)
    trade = _trades(events)[0]
    assert trade["pnl"] == pytest.approx(-2.02 - 0.25)       # 只有 pnl 与 fee，没有 1.5 / 2.5
    assert trade["fee"] == pytest.approx(0.25)
