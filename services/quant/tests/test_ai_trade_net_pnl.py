"""轴 B · 净额口径全链贯通（ai-trade-cost-truth-and-net-pnl §3.2）。

覆盖 G-1（已实现净额）/ G-2（未实现净额 + `or` 回退支无口径跳变）/ G-12（`live_auto` 逐字节回退）/
B-3 的三个新增消费方（影子策略权益、落库 KPI、持仓卡回退支）与三处复位点。

**为什么这些用例值钱**：交易所 `upl` 按标记价算纯价差、不含任何费用，故哪怕把已实现那一侧切成净额，
持仓期间显示的浮盈仍比「此刻平掉能拿到手的钱」多出一个开仓费加一个预估平仓费——那正是用户报告的
偏差里跨持仓的那一半。
"""
from __future__ import annotations

import pytest

from app.config import Settings
from app.engine.simulator import RiskParams
from app.live_auto.executor import LiveExecutor
from app.live_auto.fee_ledger import build_fee_ledger
from app.okx.accounts import AccountCredentials

_CT_VAL = 0.01
_ENTRY = 60_000.0
_SZ = 10.0


def _executor(*, net: bool = True, with_ledger: bool = True) -> LiveExecutor:
    settings = Settings(ai_trade_net_pnl_enabled=net)
    ledger = build_fee_ledger(settings, ct_val=_CT_VAL) if with_ledger else None

    async def _noop_positions():
        return []

    return LiveExecutor(
        creds=AccountCredentials("ak-fake", "sk-fake-000000", "pp-fake-000000"),
        account_id=1, inst_id="BTC-USDT-SWAP", td_mode="cross", pos_mode="long_short_mode",
        leverage=3.0, budget_usd=1_000.0, risk=RiskParams(), ct_val=_CT_VAL,
        lot_sz=1.0, min_sz=1.0, settings=settings, positions_fn=_noop_positions,
        fee_ledger=ledger)


def _hold_long(ex: LiveExecutor, *, entry_fee: float) -> None:
    """把执行器置于「持有多头」断面（不走下单路径，只测口径）。"""
    ex.projected = "long"
    ex.entry_price = _ENTRY
    ex.entry_qty_sz = _SZ
    ex.entry_fee_usd = entry_fee


# ---------- 用例 1：G-1 已实现净额 ----------
def test_realized_pnl_deducts_both_legs():
    """`ct_val=0.01 / sz=10 / 60000→60600 / taker 双边 5bps` ⇒ 毛 60.0、双边费 6.03 ⇒ 净 53.97。"""
    ex = _executor()
    entry_fee = ex.fee_ledger.leg_fee(sz=_SZ, px=_ENTRY, ord_type="market")
    _hold_long(ex, entry_fee=entry_fee)
    assert entry_fee == pytest.approx(3.0)
    assert ex._close_pnl(60_600.0) == pytest.approx(60.0 - 3.0 - 3.03)


def test_realized_pnl_falls_back_to_gross_when_the_switch_is_off():
    ex = _executor(net=False)
    _hold_long(ex, entry_fee=3.0)
    assert ex._close_pnl(60_600.0) == pytest.approx(60.0)


@pytest.mark.parametrize("original_basis,current_net,recorded", [
    ("net", False, 53.97), ("gross", True, 60.0),
])
async def test_recovered_run_closes_on_original_basis_across_settings_change(
        original_basis, current_net, recorded):
    """恢复后真实 executor 平仓与运行期归因同源，不使用新配置改写旧 run 口径。"""
    from app.ai_auto.cost_runtime import CostRuntime
    from app.ai_auto.resume import _restore_cost_basis

    ex = _executor(net=current_net)
    runtime = CostRuntime(ex.inst_id, ex.settings, ex.fee_ledger)
    _restore_cost_basis(ex, original_basis)
    opened = {"id": 1, "runId": 7, "ts": 1, "side": "long", "qty": _SZ,
              "price": _ENTRY, "fee": 3.0, "pnl": 0, "positionAfter": _SZ,
              "explain": {"meta": {"feeBasis": "measured", "feeKnown": True,
                                    "feeCurrency": "USDT"}}}
    runtime.restore_accounting([opened],
        {"runId": 7, "costBasis": original_basis, "ctVal": _CT_VAL})
    _hold_long(ex, entry_fee=runtime.remaining_open_fee_usd)

    async def close_position(_request):
        return {"ok": True, "clOrdId": "recovered-close", "ordId": "close-1"}

    ex._close_position = close_position
    events = await ex._do_close(60_600, 2, "恢复后平仓")
    trade = next(event["data"] for event in events if event["type"] == "trade")
    assert trade["pnl"] == pytest.approx(recorded)
    runtime.record_close(trade)
    economics = runtime.economics_snapshot()
    assert economics["roundTripCount"] == 1
    assert economics["grossPnlUsd"] == pytest.approx(60)
    assert economics["feeUsd"] == pytest.approx(6.03)
    assert economics["tradingNetUsd"] == pytest.approx(53.97)
    assert economics["fundingCostUsd"] is None and economics["netAfterFundingUsd"] is None
    assert runtime.cost_view(bar_seconds=3600)["netBasis"] is (original_basis == "net")
    assert runtime.remaining_open_fee_usd == 0 and ex.entry_fee_usd == 0


# ---------- 用例 2：G-2 未实现净额 ----------
def test_unrealized_deducts_the_entry_fee_and_an_estimated_exit_leg():
    ex = _executor()
    _hold_long(ex, entry_fee=3.0)
    mark = 60_600.0
    exit_fee = _SZ * _CT_VAL * mark * 5e-4
    assert ex._unrealized_at(mark) == pytest.approx(60.0 - 3.0 - exit_fee)


def test_position_payload_fallback_branch_stays_on_the_same_basis():
    """`unrealized_usd or _unrealized_at(...)` 的两侧在净额下**同为净额**（无口径跳变，P1-8）。"""
    ex = _executor()
    _hold_long(ex, entry_fee=3.0)
    mark = 60_600.0
    ex.unrealized_usd = 0.0                                  # 恰为 0 ⇒ 触发 `or` 回退支
    assert ex.position_payload(mark)["unrealizedPnl"] == pytest.approx(ex._unrealized_at(mark))
    ex.unrealized_usd = ex._net_unrealized(60.0, mark)       # 主值路径
    assert ex.position_payload(mark)["unrealizedPnl"] == pytest.approx(ex.unrealized_usd)


def test_reconcile_keeps_the_gross_reading_beside_the_net_one():
    ex = _executor()
    _hold_long(ex, entry_fee=3.0)
    ex._on_reconcile_ok(60_600.0, [{"instId": "BTC-USDT-SWAP", "pos": _SZ, "upl": 60.0}], 1)
    assert ex.gross_unrealized_usd == pytest.approx(60.0)    # 交易所 upl 原样留档（供对账）
    assert ex.unrealized_usd < ex.gross_unrealized_usd       # 展示值 = 到手值
    assert ex.session_pnl_usd == pytest.approx(ex.unrealized_usd)


def test_flatten_resets_both_unrealized_readings():
    """漏掉毛值复位即在下一次持仓期把上一次的毛浮盈泄漏给对账层。"""
    ex = _executor()
    _hold_long(ex, entry_fee=3.0)
    ex._on_reconcile_ok(60_600.0, [{"instId": "BTC-USDT-SWAP", "pos": _SZ, "upl": 60.0}], 1)
    ex._on_reconcile_ok(60_600.0, [], 2)                     # 外部平仓 ⇒ 归 flat + 三处复位之一
    assert ex.projected == "flat"
    assert (ex.unrealized_usd, ex.gross_unrealized_usd) == (0.0, 0.0)
    assert ex.entry_fee_usd == 0.0


# ---------- 用例 3：B-3 影子策略权益（有意的语义变更，用例存在是为了让它变化时有人看得见）----------
def test_shadow_strategy_equity_follows_the_net_basis():
    net_ex, gross_ex = _executor(), _executor(net=False)
    for ex in (net_ex, gross_ex):
        _hold_long(ex, entry_fee=3.0)
    mark = 60_600.0
    exit_fee = _SZ * _CT_VAL * mark * 5e-4
    assert gross_ex.context(1, mark).equity == pytest.approx(1_000.0 + 60.0)
    assert net_ex.context(1, mark).equity == pytest.approx(1_000.0 + 60.0 - 3.0 - exit_fee)


# ---------- 用例 4：G-12 live_auto（无账本）逐字节回退 ----------
def test_live_auto_path_is_byte_for_byte_unchanged():
    ex = _executor(with_ledger=False)
    _hold_long(ex, entry_fee=3.0)
    mark = 60_600.0
    gross = _SZ * _CT_VAL * (mark - _ENTRY)
    assert ex.fee_ledger is None
    assert ex._net_unrealized(gross, mark) is gross          # 位相同，不是「数值相等」
    assert ex._unrealized_at(mark) == pytest.approx(gross)
    assert ex._close_pnl(mark) == pytest.approx(gross)
    assert ex.context(1, mark).equity == pytest.approx(1_000.0 + gross)


def test_flat_position_never_deducts_anything():
    ex = _executor()
    assert ex.projected == "flat"
    assert ex._net_unrealized(0.0, 60_000.0) == 0.0
    assert ex._unrealized_at(60_000.0) == 0.0


# ---------- 用例 5：会话累计已付手续费（进 costSnapshot.cost）----------
def test_session_fee_accumulates_across_legs():
    ex = _executor()
    ex.fee_ledger.on_trade(cl_ord_id="c1", side="long", sz=_SZ, px=_ENTRY,
                           ord_type="market", expected_px=None)
    ex.fee_ledger.on_trade(cl_ord_id="c2", side="close", sz=_SZ, px=60_600.0,
                           ord_type="market", expected_px=None)
    assert ex.fee_ledger.session_fee_usd == pytest.approx(3.0 + 3.03)


def test_session_fee_stays_zero_when_accounting_is_disabled():
    settings = Settings(ai_trade_fee_accounting_enabled=False)
    ledger = build_fee_ledger(settings, ct_val=_CT_VAL)
    trade = ledger.on_trade(cl_ord_id="c1", side="long", sz=_SZ, px=_ENTRY,
                            ord_type="market", expected_px=None)
    assert trade["feeKnown"] is False and trade["feeBasis"] == "unknown"
    assert ledger.session_fee_usd == 0.0
