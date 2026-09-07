"""【基础策略】铁律层纯核心单测（ai-trade-base-strategy-ma-band §6.1 用例 1~12）。

纯函数、无网络、无 LLM、无 broker：合成 K 线 → 通道 → 锚区判定 → 门禁。三条被冻结的不变式各有专项
用例把守：**限价按委托价判定**（P0-3，用例 6）、**通道不可用 fail-closed**（P0-4，用例 8）、
**close/hold/cancel_order 永不被拦**（P1-7 豁免矩阵，用例 7）。
"""
from __future__ import annotations

import math

import pandas as pd
import pytest

from app.ai_auto.base_rules import (
    MaBandRule,
    apply_ma_band_gate,
    build_ma_band,
    classify_zone,
    compliant_price_ranges,
    entry_reference_px,
    format_band_for_prompt,
    format_charter,
    resolve_ma_band_rule,
    rule_wire,
)
from app.ai_auto.decider import TradeDecision
from app.config import Settings
from app.engine.indicators import bollinger

_RULE = MaBandRule()


def _df(closes: list[float]) -> pd.DataFrame:
    """把收盘价序列包成 `candles_to_df` 同构帧（本模块只读 `c` 列）。"""
    return pd.DataFrame({
        "ts": [1_700_000_000 + i * 3600 for i in range(len(closes))],
        "o": closes, "h": [c + 1 for c in closes], "l": [c - 1 for c in closes],
        "c": closes, "vol": [100.0] * len(closes)})


def _wave(n: int = 60) -> pd.DataFrame:
    """有真实波动的合成序列（σ>0，保证带宽为正）。"""
    return _df([30000.0 + 200.0 * math.sin(i / 3.0) for i in range(n)])


def _band_at(pct_b: float, tolerance: float = 0.15) -> dict:
    """构造一个 lower=100 / upper=200 的通道，并把 pctB 摆到指定位置（门禁用例的最小夹具）。"""
    return {"ruleId": "ma_band_entry", "period": 20, "stdMult": 2.0, "tolerance": tolerance,
            "upper": 200.0, "mid": 150.0, "lower": 100.0, "bandWidthPct": 66.67,
            "pctB": pct_b, "zone": classify_zone(pct_b, tolerance), "directional": False}


def _px_at(band: dict, pct_b: float) -> float:
    return band["lower"] + pct_b * (band["upper"] - band["lower"])


def _open(action: str = "open_long", *, ord_type: str = "market",
          limit_px: float | None = None) -> TradeDecision:
    return TradeDecision(action, 0.5, 0.8, "r", ord_type, limit_px)


# ---------------- 1. 通道与真源一致 ----------------
def test_builds_band_from_execution_frame():
    df = _wave()
    band = build_ma_band(df, _RULE)
    upper, mid, lower = bollinger(df["c"], _RULE.period, _RULE.std_mult)
    assert band["upper"] == float(upper.iloc[-1])
    assert band["mid"] == float(mid.iloc[-1])
    assert band["lower"] == float(lower.iloc[-1])
    assert band["ruleId"] == "ma_band_entry" and band["period"] == 20


# ---------------- 2/3/4. 锚区分类与容差边界 ----------------
@pytest.mark.parametrize("pct_b,expected", [(0.0, "lower"), (0.5, "mid"), (1.0, "upper"),
                                            (0.30, None), (0.72, None)])
def test_classifies_three_anchor_zones(pct_b, expected):
    assert classify_zone(pct_b, 0.15) == expected


@pytest.mark.parametrize("pct_b", [0.15, 0.35, 0.65, 0.85, -0.15, 1.15])
def test_tolerance_edges_are_inclusive(pct_b):
    """`|%B−anchor| == tolerance` 判为合规（边界含）。"""
    assert classify_zone(pct_b, 0.15) is not None


def test_tolerance_quarter_degenerates_to_inside_band():
    """tolerance=0.25：三锚区首尾相接 ⇒ 带内任意 %B 合规；带外仍否决。"""
    assert all(classify_zone(p, 0.25) is not None for p in (0.0, 0.24, 0.4, 0.6, 0.9, 1.0))
    assert classify_zone(1.4, 0.25) is None and classify_zone(-0.4, 0.25) is None


# ---------------- 5. 带外开仓被否 ----------------
@pytest.mark.parametrize("pct_b", [1.4, -0.3])
def test_rejects_entry_outside_band(pct_b):
    band = _band_at(pct_b)
    decision, reason = apply_ma_band_gate(_open(), band, _RULE, last_px=_px_at(band, pct_b))
    assert decision.action == "hold"
    assert reason is not None and reason.startswith("base_rule_ma_band(pctB=")


# ---------------- 6. P0-3 冻结：限价按委托价判定 ----------------
def test_limit_order_is_judged_by_limit_price():
    band = _band_at(0.5)
    last_px = _px_at(band, 0.5)                     # 最新价恰在中轨
    outside = _px_at(band, 1.6)                     # 委托价挂到带外
    decision, reason = apply_ma_band_gate(
        _open(ord_type="limit", limit_px=outside), band, _RULE, last_px=last_px)
    assert decision.action == "hold" and reason.startswith("base_rule_ma_band(")

    band2 = _band_at(0.7)
    decision2, reason2 = apply_ma_band_gate(
        _open(ord_type="limit", limit_px=_px_at(band2, 0.02)),   # 委托价落在下轨锚区
        band2, _RULE, last_px=_px_at(band2, 0.7))               # 最新价不在任何锚区
    assert reason2 is None and decision2.action == "open_long"


def test_entry_reference_px_prefers_limit_price():
    assert entry_reference_px(_open(ord_type="limit", limit_px=123.0), 999.0) == 123.0
    assert entry_reference_px(_open(), 999.0) == 999.0
    assert entry_reference_px(_open(), None) is None


# ---------------- 7. P1-7 冻结：豁免矩阵 ----------------
@pytest.mark.parametrize("action", ["close", "hold", "cancel_order"])
def test_never_blocks_close_hold_cancel(action):
    """离场 / 观望 / 撤单在最恶劣 %B 下全部原样直通（`is` 相等，绝不 replace）。"""
    decision = TradeDecision(action, None, None, "r")
    for band in (_band_at(5.0), None):
        out, reason = apply_ma_band_gate(decision, band, _RULE, last_px=_px_at(_band_at(5.0), 5.0))
        assert out is decision and reason is None


# ---------------- 8. P0-4 冻结：fail-closed ----------------
def test_unavailable_band_fails_closed():
    decision, reason = apply_ma_band_gate(_open(), None, _RULE, last_px=150.0)
    assert decision.action == "hold" and reason == "base_rule_band_unavailable"
    close = TradeDecision("close", None, None, "r")
    assert apply_ma_band_gate(close, None, _RULE, last_px=150.0) == (close, None)


def test_missing_last_px_fails_closed():
    """无判定价（取价失败）同样是「无法验证合规」⇒ 绝不放行。"""
    decision, reason = apply_ma_band_gate(_open(), _band_at(0.5), _RULE, last_px=None)
    assert decision.action == "hold" and reason == "base_rule_band_unavailable"


# ---------------- 9. 未装配 = 恒等直通 ----------------
@pytest.mark.parametrize("rule", [None, MaBandRule(enabled=False)])
def test_disabled_rule_is_identity(rule):
    decision = _open()
    out, reason = apply_ma_band_gate(decision, _band_at(5.0), rule, last_px=1e9)
    assert out is decision and reason is None
    assert build_ma_band(_wave(), rule) is None
    assert format_charter(rule) == "" and rule_wire(rule) is None


# ---------------- 10. 方向性模式（默认关） ----------------
def test_directional_mode_blocks_chasing():
    band = _band_at(1.0)
    px = _px_at(band, 1.0)
    directional = MaBandRule(directional=True)
    _, long_reason = apply_ma_band_gate(_open("open_long"), band, directional, last_px=px)
    _, short_reason = apply_ma_band_gate(_open("open_short"), band, directional, last_px=px)
    assert long_reason == "base_rule_ma_band_direction(zone=upper)" and short_reason is None
    # 默认 directional=False 时两者皆放行（默认行为冻结）。
    assert apply_ma_band_gate(_open("open_long"), band, _RULE, last_px=px)[1] is None
    assert apply_ma_band_gate(_open("open_short"), band, _RULE, last_px=px)[1] is None


# ---------------- 11/12. 通道构造的安全降级 ----------------
def test_insufficient_bars_returns_none():
    assert build_ma_band(_df([30000.0 + i for i in range(_RULE.period)]), _RULE) is None
    assert build_ma_band(None, _RULE) is None


def test_zero_width_band_returns_none():
    """常数序列 σ=0 ⇒ 带宽为零 ⇒ None（不产生除零 / Inf）。"""
    assert build_ma_band(_df([30000.0] * 60), _RULE) is None


# ---------------- 解析：默认开启 / 显式关闭 / 总闸 ----------------
def test_resolve_defaults_to_enabled_from_settings():
    rule = resolve_ma_band_rule(None, Settings())
    assert rule is not None and rule.enabled and rule.period == 20
    assert rule.tolerance == 0.15 and rule.directional is False


def test_resolve_explicit_disable_and_master_switch():
    assert resolve_ma_band_rule({"enabled": False}, Settings()) is None
    assert resolve_ma_band_rule(None, Settings(ai_trade_base_rules_enabled=False)) is None


def test_resolve_overrides_and_partial_fallback():
    rule = resolve_ma_band_rule({"enabled": True, "period": 30, "tolerance": 0.05}, Settings())
    assert rule.period == 30 and rule.tolerance == 0.05
    assert rule.std_mult == 2.0 and rule.directional is False    # 缺省字段回落 settings
    assert rule_wire(rule) == {"maBandEntry": {"enabled": True, "period": 30, "stdMult": 2.0,
                                               "tolerance": 0.05, "directional": False}}


# ---------------- 文案渲染（与门禁同真源） ----------------
def test_compliant_ranges_match_gate_verdict():
    """prompt 给出的合规价区间必须与门禁判定逐点一致（否则 Agent 会按区间挂单却被否）。"""
    band = _band_at(0.5)
    for _name, lo, hi in compliant_price_ranges(band):
        for px in (lo, (lo + hi) / 2, hi):
            _, reason = apply_ma_band_gate(_open(), band, _RULE, last_px=px)
            assert reason is None, f"{px} 落在宣称的合规区间却被门禁否决"


def test_format_band_for_prompt_states_verdict():
    assert "允许开仓" in format_band_for_prompt(_band_at(0.5))
    assert "降级为观望" in format_band_for_prompt(_band_at(0.3))
    assert "暂不可用" in format_band_for_prompt(None)


def test_format_charter_declares_hard_constraint():
    text = format_charter(_RULE)
    assert "最高优先级铁律" in text and "close" in text
    assert "方向性" not in text                       # 默认关时不提方向性
    assert "方向性" in format_charter(MaBandRule(directional=True))
