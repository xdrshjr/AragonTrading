"""费率记账与实测审计单测（ai-trade-cost-accounting-and-edge-gate §7.2 用例 1~10）。

三条**阻断级**判据在这里被钉死，每条都写成可证伪形式：
- 用例 2（RV-5）：部分成交按**本轮采纳增量**计费，断言写成「不是 leg_fee(3)+leg_fee(5)」；
- 用例 8（RV-3）：`entry_fee_usd` 的三处复位点全覆盖，漏掉 `_on_reconcile_ok` 那处时第二笔的净额
  会被多扣一个开仓费——这是**静默**的，只有第二笔的数字能抓住它；
- 用例 9（RV-4）：OKX `fee` 的符号归一（收费为负），漏掉即 `measuredFeeBps` 方向性出错。

用例 10 是 Z-7 的单测面：`fee_ledger=None` 的 executor（= live_auto 的全部路径）逐字节回退。
"""
from __future__ import annotations

import pytest

from app.ai_auto.manager import AiTradeManager  # noqa: F401  —— 确保 ai_auto 侧导入无环
from app.config import Settings
from app.engine.simulator import RiskParams
from app.live_auto.executor import LiveExecutor
from app.live_auto.fee_ledger import (
    FeeLedger,
    audit_fills,
    build_fee_ledger,
    explain_meta_fields,
    resolve_fee_params,
)
from app.okx.accounts import AccountCredentials
from app.okx.private_client import OKXPrivateError
from app.live_auto.resting import build_resting

_CT_VAL = 0.01
_PX = 30_000.0


def _ledger(**over) -> FeeLedger:
    settings = Settings(**over)
    return FeeLedger(ct_val=_CT_VAL, params=resolve_fee_params(settings))


def _fill(cl_ord_id: str, *, px: float = _PX, sz: float = 1.0, fee: float = -0.15,
          ccy: str = "USDT") -> dict:
    return {"ts": 1, "instId": "BTC-USDT-SWAP", "side": "buy", "fillPx": px, "fillSz": sz,
            "fee": fee, "feeCcy": ccy, "ordId": "o1", "clOrdId": cl_ord_id, "tradeId": "t1"}


# ---------- 用例 1：maker / taker 归属 ----------
def test_ordinary_limit_uses_conservative_taker_fee():
    ledger = _ledger()
    assert ledger.leg_fee_bps("market") == 5.0
    assert ledger.leg_fee_bps("limit") == 5.0
    assert ledger.leg_fee(sz=1.0, px=_PX, ord_type="market") == pytest.approx(300.0 * 5e-4)
    assert ledger.leg_fee(sz=1.0, px=_PX, ord_type="limit") == pytest.approx(300.0 * 5e-4)


def test_accounting_disabled_keeps_zero_placeholder_and_unknown_source():
    """账本关闭的旧数值占位保持0，v2来源必须明确未知。"""
    ledger = _ledger(ai_trade_fee_accounting_enabled=False)
    assert ledger.leg_fee(sz=1.0, px=_PX, ord_type="market") == 0.0
    trade = ledger.on_trade(cl_ord_id="c1", side="long", sz=1.0, px=_PX,
                            ord_type="market", expected_px=None)
    assert trade["feeKnown"] is False and trade["feeBasis"] == "unknown"
    assert "feeBps" not in trade


# ---------- 用例 2（RV-5）：部分成交按增量计费 ----------
def test_partial_fills_charge_the_delta_not_the_cumulative_size():
    ex, _ = _executor(fee_ledger=_ledger())
    order = build_resting(cl_ord_id="c1", ord_id="o1", intent="open", side="long", px=_PX,
                          sz=5.0, strength=1.0, reason="r", now_ts=0, ttl_s=300.0)
    ex.resting = order
    ex._adopt_partial(order, {"accFillSz": 3.0, "avgPx": _PX}, 1)
    ex._adopt_partial(ex.resting, {"accFillSz": 5.0, "avgPx": _PX}, 2)
    leg = ex.fee_ledger.leg_fee
    expected = (leg(sz=3.0, px=_PX, ord_type="limit") + leg(sz=2.0, px=_PX, ord_type="limit"))
    wrong = (leg(sz=3.0, px=_PX, ord_type="limit") + leg(sz=5.0, px=_PX, ord_type="limit"))
    assert ex.entry_fee_usd == pytest.approx(expected)
    assert ex.entry_fee_usd != pytest.approx(wrong)      # 可证伪：按累计量算即高估 ~1.5 倍


# ---------- 用例 3 / 4：net_close_pnl 开关等价性与计价基准 ----------
def test_net_close_pnl_returns_gross_verbatim_when_switch_off():
    ledger = _ledger(ai_trade_net_pnl_enabled=False)     # cost-truth B-1 起默认**开**，此处显式关
    gross = 123.456
    assert ledger.net_close_pnl(gross, entry_fee_usd=9.9, exit_px=_PX, sz=1.0) is gross


def test_net_close_pnl_deducts_both_legs_on_exit_price():
    ledger = _ledger(ai_trade_net_pnl_enabled=True)
    exit_px = 31_000.0
    net = ledger.net_close_pnl(100.0, entry_fee_usd=1.5, exit_px=exit_px, sz=2.0)
    exit_fee = 2.0 * _CT_VAL * exit_px * 5e-4            # 平仓腿恒 taker，按**平仓价**名义额
    assert net == pytest.approx(100.0 - 1.5 - exit_fee)


# ---------- 用例 5：非 USDT 计价一律跳过 ----------
def test_non_usdt_fee_currency_is_skipped_not_mixed():
    ledger = _ledger()
    ledger.register(cl_ord_id="c1", direction="buy", ord_type="market", expected_px=_PX,
                    sz=1.0, model_fee_usd=0.15)
    assert ledger.resolve([_fill("c1", ccy="BTC")]) == 1
    stats = ledger.audit_stats()
    assert stats["feeAuditSkipped"] == 1
    assert stats["measuredFeeBps"] is None and stats["feeAuditSamples"] == 0


# ---------- 用例 6：滑点符号 ----------
def test_slippage_sign_is_adverse_positive_per_direction():
    buy, sell = _ledger(), _ledger()
    for ledger, direction in ((buy, "buy"), (sell, "sell")):
        ledger.register(cl_ord_id="c1", direction=direction, ord_type="market",
                        expected_px=_PX, sz=1.0, model_fee_usd=0.15)
        ledger.resolve([_fill("c1", px=_PX * 1.001)])
    assert buy.audit_stats()["slipBps"] > 0      # 买在更高价 = 不利
    assert sell.audit_stats()["slipBps"] < 0     # 卖在更高价 = 有利
    assert buy.audit_stats()["slipBps"] == pytest.approx(10.0, abs=0.01)


# ---------- 用例 7：同一 clOrdId 多条 fills 量加权 ----------
def test_multiple_fills_of_one_order_are_size_weighted():
    ledger = _ledger()
    ledger.register(cl_ord_id="c1", direction="buy", ord_type="market", expected_px=100.0,
                    sz=3.0, model_fee_usd=0.0)
    ledger.resolve([_fill("c1", px=100.0, sz=1.0, fee=-0.1),
                    _fill("c1", px=110.0, sz=1.0, fee=-0.1)])
    # 均价 105 ⇒ 滑点 +500bps；费率求和 0.2 USD / (105×2×0.01) = 952.4 bps
    stats = ledger.audit_stats()
    assert stats["slipBps"] == pytest.approx(500.0, abs=0.01)
    assert stats["measuredFeeBps"] == pytest.approx(0.2 / (105.0 * 2 * _CT_VAL) * 1e4, rel=1e-6)


# ---------- 用例 8（RV-3）：三处复位点 ----------
@pytest.mark.parametrize("path", ["settle_close", "benign_close", "reconcile_ok"])
async def test_entry_fee_is_reset_on_all_three_close_paths(path):
    ex, _ = _executor(fee_ledger=_ledger(ai_trade_net_pnl_enabled=True))
    await ex.apply([_Sig("long")], ref_px=_PX, bar_index=0, ts=1000)
    first_entry_fee = ex.entry_fee_usd
    assert first_entry_fee > 0
    if path == "settle_close":
        await ex.apply([_Sig("close")], ref_px=_PX, bar_index=1, ts=1001)
    elif path == "benign_close":
        ex._settle_benign_close(_PX, 1001)
    else:
        ex._on_reconcile_ok(_PX, [], 1001)
    assert ex.entry_fee_usd == 0.0
    # 第二笔往返：净额只应扣**自己的**两条腿；漏掉复位会让它凭空少一个 first_entry_fee。
    await ex.apply([_Sig("long")], ref_px=_PX, bar_index=2, ts=1002)
    assert ex.entry_fee_usd == pytest.approx(first_entry_fee)
    events = await ex.apply([_Sig("close")], ref_px=_PX, bar_index=3, ts=1003)
    close_trade = [e["data"] for e in events if e["type"] == "trade"][0]
    exit_fee = ex.fee_ledger.leg_fee(sz=close_trade["qty"], px=_PX, ord_type="market")
    # 同价开平 ⇒ 毛盈亏为 0 ⇒ 净额恰为**本笔自己的**两条腿；漏掉复位会再多扣一个 first_entry_fee。
    assert close_trade["pnl"] == pytest.approx(0.0 - first_entry_fee - exit_fee, abs=1e-9)
    assert close_trade["pnl"] != pytest.approx(-2 * first_entry_fee - exit_fee, abs=1e-9)


def test_limit_close_deducts_the_same_conservative_fee_it_recorded():
    """平仓腿的 `trade["fee"]` 与净额里扣掉的那一笔必须**同口径**（spec §十二 I-7）。

    普通限价未取得真实费用时采用 taker，记账金额必须等于净额扣除金额。
    """
    ex, _ = _executor(fee_ledger=_ledger(ai_trade_net_pnl_enabled=True))
    ex.projected, ex.entry_price, ex.entry_qty_sz = "long", _PX, 10.0
    ex.entry_fee_usd = ex.fee_ledger.leg_fee(sz=10.0, px=_PX, ord_type="market")
    order = build_resting(cl_ord_id="c9", ord_id="o9", intent="close", side="long", px=_PX,
                          sz=10.0, strength=0.0, reason="r", now_ts=0, ttl_s=300.0)
    events = ex._adopt_close_fill(order, _PX, 1)
    trade = events[0]["data"]
    assert trade["ordType"] == "limit" and trade["feeBps"] == 5.0
    assert trade["feeBasis"] == "estimated"
    # 同价开平的净额恰为两条腿已记录的费用。
    exit_fee = ex.fee_ledger.leg_fee(sz=10.0, px=_PX, ord_type="limit")
    assert trade["pnl"] == pytest.approx(-ex_entry_fee(ex, trade) - exit_fee, abs=1e-9)
    assert trade["fee"] == pytest.approx(exit_fee)     # 记的与扣的是同一个数


def ex_entry_fee(ex, trade) -> float:
    """开仓腿费率（`entry_fee_usd` 已在 `_settle_close` 里复位，故按同一口径重算）。"""
    return ex.fee_ledger.leg_fee(sz=trade["qty"], px=_PX, ord_type="market")


# ---------- 用例 9（RV-4）：OKX fee 符号归一 ----------
def test_okx_fee_sign_is_normalised_to_expense_positive():
    ledger = _ledger()
    ledger.register(cl_ord_id="c1", direction="buy", ord_type="market", expected_px=_PX,
                    sz=1.0, model_fee_usd=0.15)
    ledger.resolve([_fill("c1", fee=-0.184)])
    stats = ledger.audit_stats()
    assert stats["measuredFeeBps"] > 0 and stats["feeAuditSamples"] == 1
    assert stats["feeRebateSamples"] == 0


def test_rebate_samples_are_counted_separately_and_excluded_from_the_mean():
    ledger = _ledger()
    for cl in ("c1", "c2"):
        ledger.register(cl_ord_id=cl, direction="buy", ord_type="market", expected_px=_PX,
                        sz=1.0, model_fee_usd=0.15)
    ledger.resolve([_fill("c1", fee=-0.15), _fill("c2", fee=+0.05)])
    stats = ledger.audit_stats()
    assert stats["feeRebateSamples"] == 1 and stats["feeAuditSamples"] == 1
    # 返佣若混入均值会把 R-4 的探针读数拉向 0：断言均值只由收费样本决定。
    assert stats["measuredFeeBps"] == pytest.approx(0.15 / (_PX * _CT_VAL) * 1e4, rel=1e-6)


def test_unsettled_zero_fee_keeps_the_pending_entry():
    ledger = _ledger()
    ledger.register(cl_ord_id="c1", direction="buy", ord_type="market", expected_px=_PX,
                    sz=1.0, model_fee_usd=0.15)
    assert ledger.resolve([_fill("c1", fee=0.0)]) == 0
    assert ledger.has_pending() is True                  # 挂账保留，等下一轮审计
    assert ledger.audit_stats()["feeAuditSamples"] == 0


# ---------- 用例 10（RV-2 / Z-7）：fee_ledger=None 的 executor 逐字节回退 ----------
async def test_executor_without_ledger_is_byte_identical():
    ex, _ = _executor()                                  # live_auto 的全部路径
    events = await ex.apply([_Sig("long")], ref_px=_PX, bar_index=0, ts=1000)
    trade = events[0]["data"]
    assert trade["fee"] == 0.0
    assert not {"feeBps", "ordType", "expectedPx"} & set(trade)
    assert explain_meta_fields(trade) == {}
    ex.projected, ex.entry_price, ex.entry_qty_sz = "long", _PX, 1.0
    assert ex._close_pnl(31_000.0) == ex._gross_close_pnl(31_000.0)


@pytest.mark.parametrize("net_pnl", [True, False])
@pytest.mark.parametrize("accounting", [True, False])
async def test_ai_trade_switches_never_leak_into_non_ai_executor(net_pnl, accounting):
    """Z-7 四配置矩阵：`ai_trade_*` 取任意值，**非 AI** executor 的输出恒等。"""
    settings = Settings(ai_trade_net_pnl_enabled=net_pnl,
                        ai_trade_fee_accounting_enabled=accounting)
    ex, _ = _executor(settings=settings)
    events = await ex.apply([_Sig("long")], ref_px=_PX, bar_index=0, ts=1000)
    open_trade = events[0]["data"]
    closed = await ex.apply([_Sig("close")], ref_px=31_000.0, bar_index=1, ts=1001)
    close_trade = closed[0]["data"]
    assert open_trade["fee"] == close_trade["fee"] == 0.0
    assert set(open_trade) == _BASE_OPEN_KEYS
    gross = open_trade["qty"] * _CT_VAL * (31_000.0 - _PX)
    assert close_trade["pnl"] == pytest.approx(gross)     # 恒毛口径，与合入前位相同


# ---------- audit_fills 自由函数：总闸 / 节流 / 异常全吞 ----------
async def test_audit_fills_respects_switch_throttle_and_swallows_errors():
    calls = {"n": 0}

    async def fills_fn():
        calls["n"] += 1
        raise OKXPrivateError("LIVE_UNAUTHORIZED", "boom")

    ledger = _ledger()
    ledger.register(cl_ord_id="c1", direction="buy", ord_type="market", expected_px=_PX,
                    sz=1.0, model_fee_usd=0.1)
    assert await audit_fills(None, fills_fn=fills_fn, now_wall=1e6, settings=Settings()) == 0
    off = Settings(ai_trade_fill_audit_enabled=False)
    assert await audit_fills(ledger, fills_fn=fills_fn, now_wall=1e6, settings=off) == 0
    assert calls["n"] == 0                               # 关闭 / 无账本 ⇒ 一个请求都不发
    assert await audit_fills(ledger, fills_fn=fills_fn, now_wall=1e6,
                             settings=Settings()) == 0   # 异常全吞
    assert calls["n"] == 1
    assert await audit_fills(ledger, fills_fn=fills_fn, now_wall=1e6 + 1,
                             settings=Settings()) == 0   # 节流：60s 内不再发
    assert calls["n"] == 1


async def test_audit_fills_short_circuits_without_pending():
    calls = {"n": 0}

    async def fills_fn():
        calls["n"] += 1
        return []

    assert await audit_fills(_ledger(), fills_fn=fills_fn, now_wall=1e6,
                             settings=Settings()) == 0
    assert calls["n"] == 0


def test_build_fee_ledger_is_the_ai_only_construction_seam():
    ledger = build_fee_ledger(Settings(), ct_val=_CT_VAL)
    assert ledger.ct_val == _CT_VAL and ledger.params.taker_fee_bps == 5.0


# ---------- 夹具 ----------
class _Sig:
    def __init__(self, side: str, strength: float = 1.0, reason: str = ""):
        self.side = side
        self.strength = strength
        self.reason = reason


_BASE_OPEN_KEYS = {"ts", "side", "price", "qty", "fee", "pnl", "positionAfter", "reason",
                   "stopLossPct", "takeProfitPct", "maxHoldBars", "stopLossPrice",
                   "takeProfitPrice", "seeded", "explain", "positionPct", "clOrdId", "ordId"}


def _executor(**over):
    """构造带桩 OKX 回调的 executor（不触网；占位假凭证，绝不写真实凭证）。"""
    calls = {"orders": [], "closes": []}

    async def place_order_fn(order):
        calls["orders"].append(order)
        return {"ok": True, "ordId": "x1", "clOrdId": "aragonx"}

    async def close_position_fn(close):
        calls["closes"].append(close)
        return {"ok": True, "clOrdId": "aragoncx"}

    async def positions_fn():
        return []

    executor = LiveExecutor(
        creds=AccountCredentials("ak-fake", "sk-fake-000000", "pp-fake-000000"),
        account_id=1, inst_id="BTC-USDT-SWAP", td_mode="cross", pos_mode="long_short_mode",
        leverage=3.0, budget_usd=10_000.0, risk=over.pop("risk", RiskParams()),
        ct_val=_CT_VAL, lot_sz=1.0, min_sz=1.0, settings=over.pop("settings", Settings()),
        place_order_fn=place_order_fn, close_position_fn=close_position_fn,
        positions_fn=positions_fn, fee_ledger=over.pop("fee_ledger", None))
    return executor, calls


def test_pending_audits_stay_bounded_when_nothing_ever_settles():
    """审计关闭 / 交易所长期不结算时挂账永不消化——它必须有界，否则随成交笔数无界增长。

    可证伪写法：挂 5 倍容量的账，断言挂账数封顶且**最新**一条仍在（淘汰的是最旧的）。
    """
    ledger = FeeLedger(ct_val=_CT_VAL, params=resolve_fee_params(Settings()),
                       audit_capacity=10)
    for i in range(50):
        ledger.register(cl_ord_id=f"c{i}", direction="buy", ord_type="market",
                        expected_px=_PX, sz=1.0, model_fee_usd=0.15)
    assert len(ledger._pending) == 20                    # 10 × _PENDING_CAPACITY_FACTOR
    assert ledger.resolve([_fill("c49")]) == 1           # 最新挂账仍可被消化
    assert ledger.resolve([_fill("c0")]) == 0            # 最旧的已被淘汰


def test_repeated_registration_of_one_order_never_evicts_other_pendings():
    """部分成交对同一 `clOrdId` 逐笔挂账 ⇒ 原地覆盖，绝不占新槽位、绝不淘汰别人。"""
    ledger = FeeLedger(ct_val=_CT_VAL, params=resolve_fee_params(Settings()),
                       audit_capacity=2)
    for cl in ("c1", "c2", "c3", "c4"):
        ledger.register(cl_ord_id=cl, direction="buy", ord_type="market", expected_px=_PX,
                        sz=1.0, model_fee_usd=0.15)
    for _ in range(20):                                  # 同一笔限价单的 20 次部分成交
        ledger.register(cl_ord_id="c4", direction="buy", ord_type="market", expected_px=_PX,
                        sz=1.0, model_fee_usd=0.15)
    assert len(ledger._pending) == 4
    assert ledger.resolve([_fill("c1")]) == 1            # 最旧的那条仍在


# ---------- 用例 9：净额浮盈 / 会话累计费用 / 官方费率热更新（cost-truth-and-net-pnl 轴 A/B）----------
def test_net_unrealized_is_structurally_identical_to_net_close_pnl():
    """浮盈净额与平仓净额**同构**：都扣「已付开仓费 + 按当前价估的平仓腿 taker 费」。

    这条同构是「持仓期显示的钱 == 此刻平掉能拿到的钱」的全部依据；两者若各写一份公式，切换持仓
    状态的那一刻数字就会跳一下，而那一跳没有任何真实事件与之对应。
    """
    ledger = _ledger(ai_trade_net_pnl_enabled=True)
    px = 31_000.0
    net_unreal = ledger.net_unrealized(100.0, entry_fee_usd=1.5, mark_px=px, sz=2.0)
    net_close = ledger.net_close_pnl(100.0, entry_fee_usd=1.5, exit_px=px, sz=2.0)
    assert net_unreal == pytest.approx(net_close)


def test_net_unrealized_returns_gross_verbatim_when_the_switch_is_off():
    ledger = _ledger(ai_trade_net_pnl_enabled=False)
    gross = 123.456
    assert ledger.net_unrealized(gross, entry_fee_usd=9.9, mark_px=_PX, sz=1.0) is gross


def test_session_fee_accumulates_only_through_booked_legs():
    """`sessionFeeUsd` 只由**真的记过账**的腿推进——它是「本会话已经付掉多少」的读数，不是估算。"""
    ledger = _ledger()
    assert ledger.session_fee_usd == 0.0
    first = ledger.on_trade(cl_ord_id="c1", side="long", sz=1.0, px=_PX,
                            ord_type="market", expected_px=None)
    second = ledger.on_trade(cl_ord_id="c2", side="close", sz=1.0, px=_PX,
                             ord_type="limit", expected_px=None)
    assert ledger.session_fee_usd == pytest.approx(first["fee"] + second["fee"])


def test_retune_switches_the_rate_source_for_subsequent_legs_only():
    ledger = _ledger()
    before = ledger.leg_fee(sz=1.0, px=_PX, ord_type="market")
    ledger.retune(maker_bps=1.5, taker_bps=4.0, source="exchange")
    after = ledger.leg_fee(sz=1.0, px=_PX, ord_type="market")
    assert before == pytest.approx(300.0 * 5e-4)      # 配置常数 5 bps
    assert after == pytest.approx(300.0 * 4e-4)       # 官方档位 4 bps
    assert ledger.rate_source == "exchange"
