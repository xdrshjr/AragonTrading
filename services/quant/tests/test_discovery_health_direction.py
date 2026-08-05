"""AL-1 双向门禁（discovery-asset-ledger §2.4 / §7.3-1）—— 本轮**最重要**的一条。

体检必须用资产落库时**冻结的方向**重评，严禁再走 ``resolve_direction(..., "auto", ...)``。
``auto`` 是在 IS 段（体检语境下 = fresh 段之前那一小截**暖机段**）定号的：一条已经在暖机窗口
里翻了面的因子，``auto`` 会顺着把方向也翻过去，于是 fresh 段的 ``rankIc`` 重新变正、H4 通过、
**衰减被判成健康**——而这正是最该被抓到的那一类资产。

构造让 ``split_at`` 落进反号段深处，使暖机段本身已经翻面：
``two_regime_candles(300, 900)``（前 300 根趋势、后 900 根反向）+ ``split_at=900``
⇒ 暖机段 = 300 趋势 + 600 反向（反向占优）；fresh 段 = 末 300 根反向。

**若第 2 条判不出差异，不许改判据，必须回设计文档改 AL-1。**
"""
from __future__ import annotations

import pytest

from app.config import Settings
from app.discovery import health
from app.discovery.expression import parse_expression
from app.discovery.validation import ValidationContext, evaluate_split
from app.engine.backtester import candles_to_df

from .discovery_synthetic import two_regime_candles

EXPR = "sub(ts_mean(ret,20),ts_mean(ret,60))"
SPLIT_AT = 900
FROZEN_DIRECTION = "long"        # 挖掘时冻结的那一面


@pytest.fixture
def two_regime_df():
    return candles_to_df(two_regime_candles(300, 900))


def _evaluate(df, direction: str):
    return evaluate_split(parse_expression(EXPR), ValidationContext(
        df_full=df, horizon=6, split_at=SPLIT_AT, frozen_direction=direction))


def _fresh_of(result, df) -> dict:
    stat = result.res_oos.horizons[0]
    return {"rankIc": stat.rankIc, "ir": stat.ir, "coverage": 1.0,
            "samples": int(result.res_oos.occurrences),
            "freshBars": len(df) - SPLIT_AT, "warmupBars": SPLIT_AT}


def _limits() -> health.HealthLimits:
    return health.HealthLimits.of(Settings())


def test_frozen_direction_detects_the_flip(two_regime_df) -> None:
    """① 冻结 ``long`` ⇒ fresh 段 ``rankIc < 0``，判 ``H4``（正确检出衰减）。"""
    result = _evaluate(two_regime_df, FROZEN_DIRECTION)
    fresh = _fresh_of(result, two_regime_df)
    assert result.direction == FROZEN_DIRECTION
    assert float(fresh["rankIc"]) < 0.0
    reasons = health.grade("factor", fresh, {"rankIc": 0.05}, {"minSamples": 10},
                           _limits())
    assert "H4" in reasons


def test_auto_direction_would_silence_the_whole_defense(two_regime_df) -> None:
    """② 不冻结（现状 auto）⇒ 定号翻成 ``short``、``rankIc > 0`` 且**不判** ``H4``。

    这条证明「不冻结方向」确实会让整条防线空转，而不是我们凭空担心。
    """
    result = _evaluate(two_regime_df, "")
    fresh = _fresh_of(result, two_regime_df)
    assert result.direction == "short"
    assert float(fresh["rankIc"]) > 0.0
    reasons = health.grade("factor", fresh, {"rankIc": 0.05}, {"minSamples": 10},
                           _limits())
    assert "H4" not in reasons


def test_the_difference_comes_only_from_the_sign(two_regime_df) -> None:
    """③ 两次调用的 ``abs(rankIc)`` 逐位相等——差异 100% 来自定号，不是别处混进的噪声。"""
    frozen = _evaluate(two_regime_df, FROZEN_DIRECTION).res_oos.horizons[0]
    auto = _evaluate(two_regime_df, "").res_oos.horizons[0]
    assert round(abs(float(frozen.rankIc)), 12) == round(abs(float(auto.rankIc)), 12)
