"""轴 E-0 风险单位体检：ATR 定标不报警 / 固定百分比超倍报警 / 成本下界（G-17 / G-17b）。"""
from __future__ import annotations

import pytest

from app.api.ai_auto import _preset_settings
from app.ai_auto.discipline import PRESET_IDS, resolve_updates
from app.ai_auto.sizing import parse_stop_rule, resolve_stop_distance
from app.ai_auto.tradability import BracketInputs, bracket_verdict
from app.ai_auto.tradability_probe import _bracket
from app.ai_auto.tradability import TradabilityVerdict
from app.config import get_settings


def _inputs(**over) -> BracketInputs:
    base = {"atr_p50_bps": 28.1, "stop_source": "fixed", "stop_bps_p50": None,
            "stop_pct": 0.05, "stop_atr_mult": 2.0, "max_atr_mult": 8.0}
    base.update(over)
    return BracketInputs(**base)


def test_fixed_stop_far_beyond_the_bar_volatility_is_flagged():
    """出厂默认 500 bps 止损对 15m 的 28.1 bps ATR 是 17.8 倍 ⇒ `oversized`。"""
    out = bracket_verdict(_inputs())
    assert out["level"] == "oversized" and out["stopSource"] == "fixed"
    assert out["stopAtrMult"] == pytest.approx(500 / 28.1, rel=1e-6)
    assert out["suggestedStopPct"] == round(2.0 * 28.1 / 1e4, 6)
    assert "**" not in out["text"] and "几乎不会" in out["text"]


def test_fixed_stop_within_the_threshold_is_not_flagged():
    """1H 的 ATR 中位 56.5 bps ⇒ 500/56.5 = 8.8 倍…… 而 300 bps 只有 5.3 倍 ⇒ 不报警。"""
    out = bracket_verdict(_inputs(atr_p50_bps=56.5, stop_pct=0.03))
    assert out["level"] == "aligned" and "同一量级" in out["text"]


def test_g17_atr_scaled_never_warns():
    """G-17（阻断级）：ATR 真的胜出时**恒不报警**——守的是「在已经正确定标的会话上误报」。"""
    out = bracket_verdict(_inputs(stop_source="atr", stop_bps_p50=56.2))
    assert out["level"] == "atr_scaled" and out["suggestedStopPct"] is None
    assert "已按 ATR 定标" in out["text"]
    capped = bracket_verdict(_inputs(stop_source="atr_capped", stop_bps_p50=2000.0))
    assert capped["level"] == "atr_scaled"


def test_g17b_cost_floored_is_stated_as_a_cost_floor_not_as_k_times_atr():
    """G-17b（[P1-8]）：`atr_floored` ⇒ `cost_floored`，文案说的是**成本下界**而不是 `k×ATR`。"""
    out = bracket_verdict(_inputs(stop_source="atr_floored", stop_bps_p50=26.0))
    assert out["level"] == "cost_floored"
    assert "成本下界" in out["text"] and "已按 ATR 定标" not in out["text"]


def test_g17b_floor_above_cap_falls_back_to_the_fixed_branch_and_warns():
    """G-17b 的另一半：整条 ATR 覆盖回退（`floor_above_cap`）⇒ 走 `fixed` 分支、该报警就报警。

    这就是「配置上 `stop_atr_mult=2.0`，实际却回退成固定 5%」的那一类断面——按配置反推的判据
    会在这里说「已按 ATR 定标，不报警」，恰恰漏掉最该报警的那一类。
    """
    out = bracket_verdict(_inputs(stop_source="floor_above_cap", stop_atr_mult=2.0))
    assert out["level"] == "oversized"


def test_unknown_or_missing_inputs_make_the_whole_block_absent():
    """ATR 不可得 / 止损为 0 / 来源未知 ⇒ `None` ⇒ 整块缺席（fail-open，与探针同极性）。"""
    assert bracket_verdict(_inputs(atr_p50_bps=None)) is None
    assert bracket_verdict(_inputs(atr_p50_bps=float("nan"))) is None
    assert bracket_verdict(_inputs(stop_pct=0.0)) is None
    assert bracket_verdict(_inputs(stop_source="unknown")) is None


def test_probe_resolves_the_real_stop_source_not_the_configured_one():
    """装配层必须**现解**止损来源：ATR 覆盖装配后 ⇒ `atr*`，未装配 ⇒ `fixed`。

    走的是实盘用的同一个纯函数 `resolve_stop_distance`（成本下界 → 红线 → 冲突三段），
    而不是「`stop_atr_mult>0` 就算 ATR」的配置反推。
    """
    verdict = TradabilityVerdict(level="ok", samples=200, atr_p50=28.1, cost_bps_market=13.0)
    armed = get_settings().model_copy(update={"ai_trade_stop_atr_mult": 2.0,
                                              "ai_trade_stop_cost_floor_mult": 0.0})
    assert _bracket(verdict, armed)["stopSource"] in ("atr", "atr_capped")
    off = get_settings().model_copy(update={"ai_trade_stop_atr_mult": 0.0})
    assert _bracket(verdict, off)["stopSource"] == "fixed"


def test_probe_block_is_absent_when_the_switch_is_off_or_level_is_unknown():
    settings = get_settings().model_copy(update={"ai_trade_bracket_check_enabled": False})
    verdict = TradabilityVerdict(level="ok", samples=200, atr_p50=28.1, cost_bps_market=13.0)
    assert _bracket(verdict, settings) is None
    unknown = TradabilityVerdict(level="unknown", samples=0)
    assert _bracket(unknown, get_settings()) is None


def test_the_probe_judges_the_stop_the_user_typed_not_the_factory_default():
    """弹窗里改过的止损必须一路走到体检判据上——否则判据取自不携带该信息的入参（[P1-8] 的形状）。

    启动路径取的是 `req.stopLossPct or ai_trade_default_sl_pct`（`manager.py:868`），故预检端点
    收到 `slPct` 时必须派生同一个覆盖：一个已经把止损改到 1% 的用户，不该再被按出厂的 5% 告知
    「止损离现价约 500 bps」。
    """
    base = get_settings().model_copy(update={"ai_trade_stop_atr_mult": 0.0,   # ATR 覆盖未装配 ⇒ fixed
                                             "ai_trade_default_sl_pct": 0.05})
    verdict = TradabilityVerdict(level="ok", samples=200, atr_p50=28.1, cost_bps_market=13.0)
    assert _bracket(verdict, base)["stopBps"] == pytest.approx(500.0)

    typed = _preset_settings("off", base, sl_pct=0.01)
    assert typed.ai_trade_default_sl_pct == pytest.approx(0.01)
    out = _bracket(verdict, typed)
    assert out["stopBps"] == pytest.approx(100.0) and out["level"] == "aligned"
    # 越过用户可见红线的输入照启动路径同样夹紧（`manager._resolve_risk` 的 ai_trade_max_sl_pct），
    # 预检说的必须是**真正会挂出去**的那个止损，而不是用户填在框里的那个数。
    clamped = _preset_settings("off", base, sl_pct=5.0)
    assert clamped.ai_trade_default_sl_pct == pytest.approx(base.ai_trade_max_sl_pct)
    # 两个入参都缺省 ⇒ 原对象直通（既有行为逐字不变）。
    assert _preset_settings("off", base) is base


def test_all_three_presets_scale_by_atr_and_therefore_never_warn():
    """G-17 的预设面：三档预设都装配了 `stop_atr_mult>0` ⇒ 解出的来源恒是 ATR 两态之一。"""
    verdict = TradabilityVerdict(level="ok", samples=200, atr_p50=28.1, cost_bps_market=13.0)
    for preset in PRESET_IDS:
        if preset == "off":
            continue
        derived = get_settings().model_copy(update=resolve_updates(preset, {}, 1).updates)
        rule = parse_stop_rule(derived)
        plan, skip = resolve_stop_distance(atr_px=28.1, ref_px=1e4, rule=rule, cost_bps=13.0)
        assert rule is not None and plan is not None and skip is None, preset
        out = _bracket(verdict, derived)
        assert out["level"] in ("atr_scaled", "cost_floored"), (preset, out["level"])
