"""轴 A 纯核心用例（ai-trade-execution-trend-verdict §7.1）。

三条不变式各有专属护栏，其中两条是**评审的回归护栏**（照 v1 的写法实现必红）：

- **I-1 / A-3 / A-3b**：高周期永远翻不动执行周期的结论——两个断面都测（五项全在 / 只剩两项）。
- **I-1b / A-11**：`strength` 只由 `|execScore|` 判，把 `htf` 从 +1 改到 −1 必须逐字不变。
- **A-12**：冲突**只撤销放宽、不降强度**（v1 的「冲突把 strong 降为 moderate」会同时关掉放宽与
  收紧两条通路，净效果是「高周期一有分歧，逆势开仓就免检」，极性正好反了）。
- **I-2 / A-4**：符号对称性，用**构造 wire** 对拍（不跑 `classify_regime`，理由见 §九 RK-2）。
"""
from __future__ import annotations

import pytest

from app.ai_auto.trend_verdict import (
    TrendInputs,
    TrendVerdictRule,
    build_trend_verdict,
    trend_rule_from_settings,
)

RULE = TrendVerdictRule(enabled=True)


def _digest(*, ema_fast=99.0, ema_slow=100.0, slope=-0.216, change=-1.1,
            range_pos=0.35, consec_up=0, consec_down=2, macd=None, roc=None) -> dict:
    """构造一份 `marketDigest` wire（只含本层读到的字段）。"""
    return {
        "priceAction": {"emaFast": ema_fast, "emaSlow": ema_slow, "emaSlopePct": slope,
                        "changePct": change, "rangePos": range_pos,
                        "consecUp": consec_up, "consecDown": consec_down},
        "factors": {"macdHist": {"value": macd}, "rocPct": {"value": roc}},
    }


def _inputs(**over) -> TrendInputs:
    base = dict(execution_bar="15m",
                regime={"primary": "trend_down", "confidence": 0.72},
                digest=_digest(),
                multi_timeframe={"trendAlignment": {"status": "bullish",
                                                    "bullishBars": ["1D", "4H"],
                                                    "bearishBars": []}},
                tactical=None, as_of_ts=1756700000)
    base.update(over)
    return TrendInputs(**base)


def _mirror(inputs: TrendInputs) -> TrendInputs:
    """把**全部**输入的方向取反（I-2 的对拍构造）。"""
    flip = {"trend_up": "trend_down", "trend_down": "trend_up", "range": "range"}
    regime = dict(inputs.regime or {})
    regime["primary"] = flip.get(regime.get("primary"), regime.get("primary"))
    pa = dict((inputs.digest or {}).get("priceAction") or {})
    pa["emaFast"], pa["emaSlow"] = pa["emaSlow"], pa["emaFast"]
    pa["emaSlopePct"] = -pa["emaSlopePct"]
    pa["changePct"] = -pa["changePct"]
    pa["rangePos"] = 1.0 - pa["rangePos"]
    pa["consecUp"], pa["consecDown"] = pa["consecDown"], pa["consecUp"]
    factors = {key: {"value": (None if (value or {}).get("value") is None
                               else -(value or {})["value"])}
               for key, value in ((inputs.digest or {}).get("factors") or {}).items()}
    htf_flip = {"bullish": "bearish", "bearish": "bullish"}
    alignment = dict(((inputs.multi_timeframe or {}).get("trendAlignment") or {}))
    alignment["status"] = htf_flip.get(alignment.get("status"), alignment.get("status"))
    alignment["bullishBars"], alignment["bearishBars"] = (
        list(alignment.get("bearishBars") or []), list(alignment.get("bullishBars") or []))
    return TrendInputs(execution_bar=inputs.execution_bar, regime=regime,
                       digest={"priceAction": pa, "factors": factors},
                       multi_timeframe={"trendAlignment": alignment},
                       tactical=inputs.tactical, as_of_ts=inputs.as_of_ts)


# ============================ A-1 / A-2 ============================
def test_disabled_rule_returns_none_not_empty_dict():
    """A-1：总闸关 ⇒ **返回 None**（不是空 dict）⇒ payload 整键缺席 ⇒ 全链路逐字节回退。"""
    assert build_trend_verdict(_inputs(), TrendVerdictRule(enabled=False)) is None


def test_all_execution_components_bullish_gives_strong_up():
    """A-2：五个执行周期成分一致看多 ⇒ up / long / strong。"""
    out = build_trend_verdict(_inputs(
        regime={"primary": "trend_up", "confidence": 0.9},
        digest=_digest(ema_fast=101.0, ema_slow=100.0, slope=0.9, change=3.0,
                       range_pos=1.0, consec_up=5, consec_down=0, macd=1.2, roc=4.0),
        multi_timeframe=None), RULE)
    assert (out["direction"], out["bias"], out["strength"]) == ("up", "long", "strong")


# ============================ 算例自洽（§3.2.4）============================
def test_wire_matches_spec_worked_example():
    """§3.2.4 的算例可以直接抄进 fixture——它是**算得出来的**，不是手写的。"""
    out = build_trend_verdict(_inputs(), RULE)
    scores = {row["key"]: row["score"] for row in out["components"]}
    assert scores == {"regime": -0.72, "ema": -0.68, "netChange": -0.55,
                      "structure": -0.4, "momentum": None, "htf": 1.0}
    assert out["score"] == -0.32 and out["execScore"] == -0.61
    assert out["availableWeight"] == 0.86 and out["execAvailableWeight"] == 0.84
    assert out["verdictConfidence"] == 0.59
    assert out["missing"] == ["momentum"]
    assert (out["direction"], out["bias"], out["strength"]) == ("down", "short", "strong")


# ============================ I-1：执行周期主导性 ============================
def test_htf_can_never_flip_execution_bar_verdict():
    """A-3 / I-1：执行周期五项各 −1、`htf` 满分 +1 ⇒ `score < 0` 且 `direction == "down"`。"""
    out = build_trend_verdict(_inputs(
        regime={"primary": "trend_down", "confidence": 1.0},
        digest=_digest(ema_fast=99.0, ema_slow=100.0, slope=-1.0, change=-5.0,
                       range_pos=0.0, consec_up=0, consec_down=9, macd=-1.0, roc=-9.0),
        multi_timeframe={"trendAlignment": {"status": "bullish", "bullishBars": ["1D"],
                                            "bearishBars": []}}), RULE)
    assert out["score"] < 0 and out["direction"] == "down"


def test_htf_cannot_flip_when_components_are_partially_missing():
    """A-3b / I-1 的**缺失断面**（评审 RP1-7）：构造成刚好过 `_MIN_EXEC_WEIGHT` 的最小组合。

    `regime`(0.26) + `ema`(0.22) + `netChange`(0.20) = 0.68 ≥ 0.5，三项全 −1、`htf` 满分 +1。
    v1 只用 `_HTF_WEIGHT_CAP` 论证主导性，那条论证在本断面上**不成立**。
    """
    out = build_trend_verdict(_inputs(
        regime={"primary": "trend_down", "confidence": 1.0},
        digest={"priceAction": {"emaFast": 99.0, "emaSlow": 100.0, "emaSlopePct": -1.0,
                                "changePct": -5.0, "rangePos": None,
                                "consecUp": None, "consecDown": None},
                "factors": {}},
        multi_timeframe={"trendAlignment": {"status": "bullish", "bullishBars": ["1D"],
                                            "bearishBars": []}}), RULE)
    assert out["degraded"] is False
    assert out["execAvailableWeight"] == 0.68
    assert out["score"] < 0 and out["direction"] == "down"


# ============================ I-1b：强度与高周期无关 ============================
def test_strength_is_independent_of_htf():
    """A-11 / I-1b（评审 RP0-3 的回归护栏）：`htf` 从 +1 改到 −1 ⇒ `strength` / `execScore` 逐字不变。

    v1 把 `strength` 建在 `|score|` 上，本用例必红。
    """
    bull = build_trend_verdict(_inputs(
        multi_timeframe={"trendAlignment": {"status": "bullish", "bullishBars": ["1D"],
                                            "bearishBars": []}}), RULE)
    bear = build_trend_verdict(_inputs(
        multi_timeframe={"trendAlignment": {"status": "bearish", "bullishBars": [],
                                            "bearishBars": ["1D"]}}), RULE)
    assert bull["strength"] == bear["strength"] == "strong"
    assert bull["execScore"] == bear["execScore"]
    assert bull["score"] != bear["score"]      # 只有含 htf 的合成分会变


def test_conflict_withholds_bonus_but_never_downgrades_strength():
    """A-12（评审 RP0-3）：冲突 ⇒ `bonusWithheld=True`，但 `strength` 仍是 strong、方向不变。

    v1 的「冲突把 strong 降为 moderate」会同时关掉放宽与收紧两条通路，净效果是「高周期一有
    分歧，逆势开仓就免检」——极性正好反了。
    """
    out = build_trend_verdict(_inputs(), RULE)
    assert out["conflict"] == "htf_conflict"
    assert out["bonusWithheld"] is True
    assert out["strength"] == "strong"
    assert out["direction"] == "down"


def test_tactical_divergence_is_reported_as_conflict():
    """战术背离进 `conflict`，且与高周期冲突同时出现时记 `both`（D-1b 据它拒绝放宽）。"""
    out = build_trend_verdict(_inputs(tactical={"alignment": "diverged"}), RULE)
    assert out["conflict"] == "both"
    assert out["tacticalAlignment"] == "diverged"


# ============================ I-2：符号对称性 ============================
def test_trend_verdict_is_sign_symmetric():
    """A-4 / I-2：镜像输入的 `score` 与 `execScore` **严格反号**，三个枚举严格镜像。"""
    # 六个成分全在（含动量），且合成分落在死区之外——否则「方向严格镜像」这一半会退化成
    # `range == range` 的空断言。
    inputs = _inputs(digest=_digest(macd=-0.4, roc=-1.5))
    base = build_trend_verdict(inputs, RULE)
    mirror = build_trend_verdict(_mirror(inputs), RULE)
    assert mirror["score"] == pytest.approx(-base["score"])
    assert mirror["execScore"] == pytest.approx(-base["execScore"])
    assert (base["direction"], mirror["direction"]) == ("down", "up")
    assert (base["bias"], mirror["bias"]) == ("short", "long")
    assert base["strength"] == mirror["strength"]


# ============================ 两个单向阀（§3.2.3 的坑 1 / 坑 2）============================
def test_equal_emas_score_zero_not_bullish():
    """A-5：`emaFast == emaSlow` ⇒ `ema` 的符号半项得 0（**不得**因 `emaRelation` 判多）。"""
    out = build_trend_verdict(_inputs(
        digest=_digest(ema_fast=100.0, ema_slow=100.0, slope=0.0)), RULE)
    ema = next(row for row in out["components"] if row["key"] == "ema")
    assert ema["score"] == 0.0


def test_zero_macd_hist_scores_zero_not_bearish():
    """A-6：`macdHist.value == 0` ⇒ macd 半项得 0（**不得**因 `label="bearish"` 判空）。"""
    out = build_trend_verdict(_inputs(digest=_digest(macd=0.0, roc=0.0)), RULE)
    momentum = next(row for row in out["components"] if row["key"] == "momentum")
    assert momentum["score"] == 0.0


# ============================ 两道 degraded 门槛 ============================
def test_missing_components_do_not_enter_denominator():
    """A-7：只给 `regime` 与 `ema` ⇒ `availableWeight = 0.48/1.18 ≈ 0.41 < 0.5` ⇒ degraded。

    **分母是全部权重和 1.18**（五项 1.00 + htf 0.18），不是某个断面的可用权重（评审 RP1-6）。
    """
    out = build_trend_verdict(_inputs(
        digest={"priceAction": {"emaFast": 99.0, "emaSlow": 100.0, "emaSlopePct": -0.2},
                "factors": {}},
        multi_timeframe=None), RULE)
    assert out["degraded"] is True
    assert out["availableWeight"] == 0.41
    assert out["direction"] == "unknown" and out["bias"] == "neutral"
    assert out["degradeReason"] == "insufficient_components"
    assert out["score"] is None and out["execScore"] is None


def test_exec_weight_floor_degrades_even_when_total_weight_passes():
    """A-7b：满 `htf` + 只给 `regime` ⇒ `execAvailableWeight = 0.26 < 0.5` ⇒ degraded。

    第二道门槛是 I-1 的**构造性**保证：过了它，执行周期可用权重必 ≥ 0.50 > htf 的封顶 0.30。
    """
    out = build_trend_verdict(_inputs(digest=None), RULE)
    assert out["execAvailableWeight"] == 0.26
    assert out["degraded"] is True and out["direction"] == "unknown"


def test_dead_zone_yields_range_and_neutral():
    """A-8：`score` 落在 `(−enter, +enter)` ⇒ range / neutral。"""
    out = build_trend_verdict(_inputs(
        regime={"primary": "range", "confidence": 0.5},
        digest=_digest(ema_fast=100.0, ema_slow=100.0, slope=0.0, change=0.0,
                       range_pos=0.5, consec_up=0, consec_down=0, macd=0.0, roc=0.0),
        multi_timeframe={"trendAlignment": {"status": "mixed"}}), RULE)
    assert (out["direction"], out["bias"]) == ("range", "neutral")


# ============================ A-10：解析点的两处夹紧 ============================
class _Settings:
    ai_trade_trend_verdict_enabled = True
    ai_trade_trend_enter_threshold = 0.60
    ai_trade_trend_strong_threshold = 0.30      # 倒挂
    ai_trade_trend_htf_weight = 0.40            # 越过封顶
    ai_trade_trend_counter_gate_enabled = True
    ai_trade_trend_counter_min_confidence = 0.65
    ai_trade_trend_counter_size_mult = 0.5
    ai_trade_trend_consensus_vote = True
    ai_trade_trend_contrarian_allowance = True


def test_rule_from_settings_clamps_without_raising():
    """A-10：两种越界配置各自被夹紧且**不抛异常**（一个拼错的运维开关不该让会话起不来）。"""
    rule = trend_rule_from_settings(_Settings())
    assert rule.strong_threshold == pytest.approx(0.65)   # enter + 0.05
    assert rule.htf_weight == pytest.approx(0.30)         # _HTF_WEIGHT_CAP
    assert rule.enabled is True


def test_counter_gate_thresholds_ride_along_the_wire():
    """逆势闸的两个**生效阈值**随基调下发（实施期发现 F-1）：渲染层据此不写死算例。"""
    out = build_trend_verdict(_inputs(), TrendVerdictRule(
        enabled=True, counter_min_confidence=0.71, counter_size_mult=0.4))
    assert out["counterGate"] == {"armed": True, "minConfidence": 0.71, "sizeMult": 0.4}
