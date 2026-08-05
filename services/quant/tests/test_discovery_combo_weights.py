"""BL-12（评审 P0-3）：权重必须**逐成员烘焙**进 ``weight`` 键。

``factors/specs.py::validate_members`` 对缺 ``weight`` 的成员填 **0.0** 且不报错，而
``FactorScoreStrategy.prepare`` 在无顶层 ``weights`` 时走的正是成员烘焙路径。两件事叠起来的
结果是：复合得分恒 0 ⇒ 永远越不过阈值 ⇒ **零成交** ⇒ 全轴卡 S1「样本量不足」。

本文件的反向用例（不烘焙 ⇒ 零成交）就是「这个错误确实是静默的」的证据。
"""
from __future__ import annotations

import math
import random

from app.config import Settings
from app.discovery.backtest_lane import FactorComboAssembler, costs_of
from app.discovery.catalogs import SearchInputs
from app.discovery.genomes import ComboGenome, ComboMemberRef
from app.discovery.searchers.combo_searcher import ComboSearcher
from app.engine.backtester import run_backtest
from app.okx.types import Candle

_TS = 1_700_000_000
_EXPRS = ("sub(ts_mean(close,20),ts_mean(close,60))",
          "div(volume,ts_mean(volume,20))",
          "div(sub(high,low),close)")


def _members() -> tuple:
    return tuple(ComboMemberRef(factor_key="expr", params={"expr": expr}, direction="long",
                                name=f"expr:{i:08x}") for i, expr in enumerate(_EXPRS))


def _genome(weights=(0.5, 0.3, 0.2)) -> ComboGenome:
    return ComboGenome(members=_members(), weights=tuple(weights),
                       thresholds={"enterLong": 0.2, "enterShort": 0.2, "exitBand": 0.05})


def _candles(n: int = 600) -> list[Candle]:
    rng = random.Random(99)
    out, price = [], 100.0
    for i in range(n):
        sig = math.sin(i / 7.0)
        close = price * (1.0 + 0.012 * sig + 0.002 * (rng.random() - 0.5))
        out.append(Candle(ts=_TS + i * 3600, o=round(price, 4),
                          h=round(max(price, close) * 1.002, 4),
                          l=round(min(price, close) * 0.998, 4), c=round(close, 4),
                          vol=1000.0 * (1.0 + 0.5 * sig)))
        price = close
    return out


def test_baked_members_all_carry_a_weight_that_sums_to_one():
    """BL-12 的正向判据：装配出的每个成员 dict 都带 ``weight``，且和恒为 1.0。"""
    members = FactorComboAssembler(_genome().baked_members(), {}).members()
    assert len(members) == 3
    assert all("weight" in member for member in members)
    assert abs(sum(float(m["weight"]) for m in members) - 1.0) < 1e-9


def test_searcher_weights_are_strictly_normalised():
    """归一化必须**严格**（残差补给最大分量），否则残差每次读写放大。"""
    searcher = ComboSearcher()
    inputs = SearchInputs(members=_members(), combo_size=(2, 3))
    rng = random.Random(3)
    for _ in range(200):
        genome = searcher.seed(rng, inputs)
        assert genome is not None
        assert abs(sum(genome.weights) - 1.0) < 1e-9
        assert all(weight > 0.0 for weight in genome.weights)


def test_not_baking_the_weights_silently_yields_zero_trades():
    """反向用例：不烘焙 ⇒ ``validate_members`` 填 0.0 ⇒ 复合得分恒 0 ⇒ **零成交且不报错**。"""
    raw = [{"factorKey": m.factor_key, "params": m.params, "direction": m.direction,
            "name": m.name} for m in _members()]              # ← 故意不带 weight
    settings = Settings()
    initial, fee, slip, leverage = costs_of(settings)
    candles = _candles()
    broken = run_backtest(FactorComboAssembler(raw, {"enterLong": 0.2, "enterShort": 0.2,
                                                     "exitBand": 0.05}).build(),
                          candles, "1H", initial_capital=initial, taker_fee_bps=fee,
                          slippage_bps=slip, leverage=leverage)
    assert broken.metrics["tradeCount"] == 0, "不烘焙权重居然有成交？那这条承重就白立了"
    baked = run_backtest(FactorComboAssembler(_genome().baked_members(),
                                              {"enterLong": 0.2, "enterShort": 0.2,
                                               "exitBand": 0.05}).build(),
                         candles, "1H", initial_capital=initial, taker_fee_bps=fee,
                         slippage_bps=slip, leverage=leverage)
    assert baked.metrics["tradeCount"] > 0, "烘焙后仍零成交 ⇒ 整条 factor_combo 轴不可能产出"


def test_member_direction_is_frozen_never_auto():
    """``validate_members`` 见 ``auto`` 直接 400——搜索器绝不能写它。"""
    from fastapi import HTTPException

    raw = [{"factorKey": "expr", "params": {"expr": _EXPRS[0]}, "direction": "auto",
            "name": "expr:00000000", "weight": 1.0}]
    try:
        FactorComboAssembler(raw, {}).members()
        raise AssertionError("方向为 auto 竟然通过了边界校验")
    except HTTPException as exc:
        assert exc.detail["code"] == "FACTOR_DIRECTION"
