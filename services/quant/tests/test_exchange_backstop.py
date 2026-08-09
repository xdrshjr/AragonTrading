"""交易所侧兜底真值化单测（ai-trade-turnover-budget-and-backstop §6.1 · B 组）。

覆盖三态 `basis` / 只认止损腿 / 方向过滤 / `mismatch` 判据 / 部分覆盖 / `stale` 降级 /
G6 只拦同向加仓且未核实与覆盖未知一律放行 / 取数异常全吞 / 无凭证零请求 /
[P0-2] 的四条真实数据形态（`sz` 缺省 + `closeFraction`、两者都不可用、`reduceOnly` 缺省、
`position_sz <= 0`）/ [P1-7] 复用既有归一 / 既有归一加键后逐键零回归。
"""
from __future__ import annotations

import ast
import asyncio
import pathlib

import pytest

from app.ai_auto.decider import TradeDecision
from app.ai_auto.exchange_backstop import (
    BASIS_EXCHANGE,
    BASIS_LOCAL,
    BASIS_UNAVAILABLE,
    REASON_ERROR,
    REASON_NO_CREDS,
    REASON_NOT_RUN,
    REASON_STALE,
    apply_backstop_gate,
    classify_backstop,
    fetch_algo_backstop,
)
from app.okx.trading import _normalize_algo_order

_NOW = 1_700_000_000.0


def _algo(**over) -> dict:
    """`_normalize_algo_order` 产物形状的一条止损腿（[P1-7]：本组绝不定义第二种形状）。"""
    base = {"kind": "sl", "instId": "BTC-USDT-SWAP", "posSide": "long", "side": "sell",
            "sz": 3.0, "closeFraction": None, "state": "live", "reduceOnly": True,
            "ordId": None, "algoId": "a1", "createdTs": 0, "triggerPx": 62140.0,
            "px": None}
    return {**base, **over}


def _classify(orders, **over) -> dict:
    base = {"side": "long", "position_sz": 3.0, "local_sl_px": 62140.0,
            "local_tp_px": 68000.0, "checked_at_wall": _NOW - 42.0, "now_wall": _NOW,
            "max_age_s": 300.0, "unavailable_reason": None}
    return classify_backstop(orders, **{**base, **over})


def _open(action="open_long") -> TradeDecision:
    return TradeDecision(action, 0.5, 0.7, "signal")


# ---------------- B-1 / B-6：三态 basis ----------------
def test_three_state_basis_covers_each_unavailable_reason():
    """B-1：`no_creds` / `error` / `stale` 三条路径各自的 `unavailableReason`。"""
    assert _classify(None, unavailable_reason=REASON_NO_CREDS)["basis"] == BASIS_UNAVAILABLE
    assert _classify(None, unavailable_reason=REASON_ERROR)["unavailableReason"] == REASON_ERROR
    never = _classify(None, unavailable_reason=None)
    assert never["basis"] == BASIS_LOCAL and never["unavailableReason"] == REASON_NOT_RUN


def test_a_stale_reading_is_downgraded_and_stops_publishing_exchange_prices():
    """B-6：超过 `max_age_s` ⇒ `unavailable(stale)`，且**不再下发交易所价**（绝不拿旧读数背书）。"""
    wire = _classify([_algo()], checked_at_wall=_NOW - 900.0)
    assert wire["basis"] == BASIS_UNAVAILABLE and wire["unavailableReason"] == REASON_STALE
    assert wire["stopLossPx"] is None and wire["coverageRatio"] is None
    assert wire["mismatch"] is False          # 「不知道」绝不渲染成确定的坏消息


def test_local_basis_never_carries_exchange_prices():
    """`local` / `unavailable` 两态的 `stopLossPx` 恒为 `null`——本地价另有两键承载。"""
    wire = _classify(None, unavailable_reason=REASON_NO_CREDS)
    assert wire["stopLossPx"] is None and wire["takeProfitPx"] is None
    assert wire["localStopLossPx"] == 62140.0 and wire["localTakeProfitPx"] == 68000.0


# ---------------- B-2 / B-3 / B-4 / B-5：覆盖判据 ----------------
def test_only_the_stop_leg_counts_as_a_backstop():
    """B-2 / D-7：只有止盈腿 ⇒ 覆盖率 0、`stopLossPx` 为空（止盈腿不构成兜底）。"""
    wire = _classify([_algo(kind="tp", triggerPx=68000.0)])
    assert wire["stopLossPx"] is None
    assert wire["hasStop"] is False
    assert wire["coverageRatio"] == 0.0
    assert wire["takeProfitPx"] == 68000.0


def test_orders_for_the_wrong_direction_do_not_count():
    """B-3：反向委托不计入覆盖（它不是当前持仓的平仓腿）。"""
    wire = _classify([_algo(posSide="short", side="buy", reduceOnly=False)])
    assert wire["hasStop"] is False and wire["coverageRatio"] == 0.0


def test_mismatch_is_local_claims_a_stop_but_exchange_confirms_none():
    """B-4：本轮存在的**全部理由**——本地声称挂过止损，交易所侧确认一条都没有。"""
    wire = _classify([])
    assert wire["basis"] == BASIS_EXCHANGE
    assert wire["mismatch"] is True and wire["coverageRatio"] == 0.0
    assert _classify([], local_sl_px=None)["mismatch"] is False   # 本地也没声称 ⇒ 不是错配


def test_partial_coverage_is_measured_not_rounded_away():
    """B-5：两腿共 2 张 / 持仓 3 张 ⇒ 覆盖率 2/3。"""
    wire = _classify([_algo(sz=1.0, algoId="a1"), _algo(sz=1.0, algoId="a2")])
    assert wire["coverageRatio"] == pytest.approx(2 / 3, abs=1e-6)
    assert wire["mismatch"] is False


# ---------------- B-11 ~ B-14：[P0-2] 真实数据形态 ----------------
def test_size_absent_but_close_fraction_one_means_full_coverage():
    """B-11 [P0-2]：随单 TP/SL 常见形态是 `sz=0` 配 `closeFraction="1"`。

    只看 `sz` 会得到 `coveredSz=0 ⇒ coverageRatio=0 ⇒ mismatch=True`：prompt 会对模型高喊
    「此刻没有任何持久化兜底」、G6 会拦掉全部加仓。**本轮唯一的存在理由是消除一句没核实的话，
    落地却造出一句核实错的话——那比原状更坏。**
    """
    wire = _classify([_algo(sz=None, closeFraction="1")])
    assert wire["coverageRatio"] == 1.0
    assert wire["hasStop"] is True and wire["mismatch"] is False


def test_neither_size_nor_close_fraction_yields_unknown_not_zero():
    """B-12 [P0-2]：两者都不可用 ⇒ `coverageRatio is None`（还不知道），G6 **放行**。"""
    wire = _classify([_algo(sz=None, closeFraction=None)])
    assert wire["coverageRatio"] is None
    assert wire["hasStop"] is True and wire["mismatch"] is False
    decision = _open()
    assert apply_backstop_gate(decision, wire, position_side="long", enabled=True,
                               min_coverage=0.999) == (decision, None)


def test_reduce_only_absent_but_direction_correct_still_counts():
    """B-13 [P0-2]：净持仓多头 + `side=="sell"` + `posSide=="net"` ⇒ 计入覆盖。

    `reduceOnly` 走 `_as_bool`、缺省即 `False`，而单向持仓模式下随单 OCO 未必带该字段——
    拿它当**必要**条件会把真实的止损腿全部剔掉。
    """
    wire = _classify([_algo(posSide="net", side="sell", reduceOnly=False)])
    assert wire["hasStop"] is True and wire["coverageRatio"] == 1.0


@pytest.mark.parametrize("position_sz", [0.0, None])
def test_no_division_when_position_size_is_unavailable(position_sz):
    """B-14 [P0-2]：限价挂单期 `projected` 被刻意不写 ⇒ `entry_qty_sz` 可能仍是 0。"""
    wire = _classify([_algo()], position_sz=position_sz)
    assert wire["coverageRatio"] is None and wire["positionSz"] is None
    assert wire["mismatch"] is False


def test_unrecognised_close_fraction_is_never_treated_as_full():
    """R-10：`closeFraction` 是字符串字段，取值未必只有 `"1"`——不认识的一律不当成全仓。"""
    assert _classify([_algo(sz=None, closeFraction="abc")])["coverageRatio"] is None
    assert _classify([_algo(sz=None, closeFraction="0.5")])["coverageRatio"] == 0.5


# ---------------- B-7 / B-8：G6 ----------------
@pytest.mark.parametrize("side,action", [
    ("flat", "open_long"),        # 空仓开仓：新单自带随单 OCO，拦它只会制造裸露
    ("long", "open_short"),       # 反手：由 hold_horizon 与换手闸负责，且会先平掉旧仓
    ("long", "close"),
    ("long", "hold"),
    ("long", "cancel_order"),
])
def test_g6_only_blocks_a_same_side_scale_in(side, action):
    """B-7 / D-8：其余一律直通（同一对象）——范围越窄，误伤越少。"""
    wire = _classify([])              # 确认裸露的最坏一格
    decision = TradeDecision(action, 0.5, 0.7, "x")
    assert apply_backstop_gate(decision, wire, position_side=side, enabled=True,
                               min_coverage=0.999) == (decision, None)


def test_g6_blocks_a_scale_in_only_when_coverage_is_measured_and_short():
    """G6 的正例：核实过 + 覆盖率有数且低于阈 ⇒ 拦下同向加仓，降级码带百分比。"""
    wire = _classify([_algo(sz=1.0)])          # 1 / 3
    gated, reason = apply_backstop_gate(_open("open_long"), wire, position_side="long",
                                        enabled=True, min_coverage=0.999)
    assert gated.action == "hold"
    assert reason == "horizon_backstop_uncovered(33%)"


@pytest.mark.parametrize("basis_kwargs", [
    {"orders": None, "unavailable_reason": REASON_ERROR},
    {"orders": None, "unavailable_reason": REASON_NO_CREDS},
    {"orders": [_algo(sz=1.0)], "checked_at_wall": _NOW - 900.0},   # stale
])
def test_g6_fails_open_when_the_backstop_was_never_verified(basis_kwargs):
    """B-8 / 放行条件 7：`basis != "exchange"` ⇒ 恒放行——未核实就拦等于用一个不确定性剥夺交易能力。"""
    orders = basis_kwargs.pop("orders")
    wire = _classify(orders, **basis_kwargs)
    assert wire["basis"] != BASIS_EXCHANGE
    decision = _open("open_long")
    assert apply_backstop_gate(decision, wire, position_side="long", enabled=True,
                               min_coverage=0.999) == (decision, None)


def test_g6_can_be_turned_off_and_then_only_reports():
    """`ai_trade_backstop_gate_scale_in=False` ⇒ 只报不拦。"""
    wire = _classify([])
    decision = _open("open_long")
    assert apply_backstop_gate(decision, wire, position_side="long", enabled=False,
                               min_coverage=0.999) == (decision, None)


# ---------------- B-9 / B-10：取数 ----------------
class _FakeCreds:
    api_key = "k"
    secret_key = "s"
    passphrase = "p"


class _CountingClient:
    """假客户端：记调用次数，可选抛异常（B-9 / B-10 的断言对象）。"""

    calls = 0

    def __init__(self, *_a, **_kw) -> None:
        pass

    async def get_algo_pending_orders(self, ord_type, inst_id=None):
        type(self).calls += 1
        raise RuntimeError("boom")

    async def aclose(self) -> None:
        return None


def test_fetch_swallows_every_exception_and_returns_none(monkeypatch):
    """B-9：客户端抛（含 OKXError / 超时）⇒ 返回 `None`，**绝不外泄异常**。

    这条取数跑在盯市 tick 的后台任务里，一次网络抖动不该变成一次会话降级。
    """
    _CountingClient.calls = 0
    monkeypatch.setattr("app.ai_auto.exchange_backstop.OKXTradeClient", _CountingClient)
    out = asyncio.run(fetch_algo_backstop(_FakeCreds(), "BTC-USDT-SWAP", timeout_s=1.0))
    assert out is None
    assert _CountingClient.calls > 0


def test_no_credentials_means_not_a_single_request(monkeypatch):
    """B-10：`creds is None`（模拟盘 / 无凭证）⇒ 一个请求都不发。"""
    _CountingClient.calls = 0
    monkeypatch.setattr("app.ai_auto.exchange_backstop.OKXTradeClient", _CountingClient)
    assert asyncio.run(fetch_algo_backstop(None, "BTC-USDT-SWAP", timeout_s=1.0)) is None
    assert _CountingClient.calls == 0


# ---------------- B-15 / B-16：复用既有归一 ----------------
def test_module_does_not_define_a_second_algo_order_parser():
    """B-15 [P1-7]：两处各归一一份正是本仓库反复出事的形态。"""
    src = (pathlib.Path(__file__).resolve().parents[1] / "app" / "ai_auto"
           / "exchange_backstop.py").read_text(encoding="utf-8")
    literals = {node.value for node in ast.walk(ast.parse(src))
                if isinstance(node, ast.Constant) and isinstance(node.value, str)}
    assert "slTriggerPx" not in literals and "tpTriggerPx" not in literals
    assert "_normalize_algo_order" in src        # 复用的是既有那一个


def test_normalize_algo_order_gains_only_close_fraction():
    """B-16 / R-11：加键零影响——其余键与加键前**逐键相等**（它同时服务非 AI 的三个读点）。"""
    raw = {"instId": "BTC-USDT-SWAP", "posSide": "long", "side": "sell", "sz": "3",
           "state": "live", "reduceOnly": "true", "algoId": "a1", "cTime": "1700000000000",
           "slTriggerPx": "62140", "slOrdPx": "-1", "tpTriggerPx": "68000",
           "tpOrdPx": "-1", "closeFraction": "1"}
    rows = _normalize_algo_order(raw)
    assert [r["kind"] for r in rows] == ["tp", "sl"]
    for row in rows:
        assert row["closeFraction"] == "1"
        legacy = {k: v for k, v in row.items() if k != "closeFraction"}
        assert set(legacy) == {"instId", "posSide", "side", "sz", "state", "reduceOnly",
                               "ordId", "algoId", "createdTs", "kind", "triggerPx", "px"}
        assert legacy["sz"] == 3.0 and legacy["reduceOnly"] is True
    assert _normalize_algo_order({**raw, "closeFraction": ""})[0]["closeFraction"] is None
