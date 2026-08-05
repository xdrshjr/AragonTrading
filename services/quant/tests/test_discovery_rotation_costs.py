"""BL-11（评审 P0-2）：``turnoverBps`` 是**成本模型参数**，绝不能进搜索空间。

``engine/combo.py::_accrue`` 用它扣每次切换的换手成本。把它交给搜索器等于在优化目标里放一个
「把成本调成 0」的旋钮——搜索会系统性收敛到 ``turnoverBps=0``，挖出来的轮动组合个个都赚。
这与 BL-1（成本被 /1e4 除没了）是同型事故，只是走了另一扇门，而且更隐蔽。
"""
from __future__ import annotations

import random

from app.config import Settings
from app.discovery.backtest_lane import FROZEN_POLICY_KEYS, frozen_policy_values
from app.discovery.catalogs import SearchInputs
from app.discovery.genomes import ParamGenome
from app.discovery.searchers.rotation_searcher import (
    MINABLE_POLICY_KEYS,
    POLICY_LADDERS,
    RotationSearcher,
    assert_no_cost_params,
)
from app.strategies import STRATEGY_REGISTRY

SAMPLES = 500


def _inputs() -> SearchInputs:
    pool = tuple(ParamGenome(key, {"strength": 0.5 + 0.1 * i})
                 for i, key in enumerate(("momentum_breakout", "ema_cross_scalper",
                                          "bollinger_reversion", "vwap_rsi_momentum")))
    return SearchInputs(rotation_pool=pool, rotation_size=(2, 4))


def test_searcher_never_emits_a_cost_parameter():
    """500 次随机采样：产物的 policy 里**恒无**成本类键。"""
    searcher, inputs, rng = RotationSearcher(), _inputs(), random.Random(7)
    for _ in range(SAMPLES):
        genome = searcher.seed(rng, inputs)
        assert genome is not None
        assert not FROZEN_POLICY_KEYS.intersection(genome.policy)
        assert_no_cost_params(genome.policy)
        assert set(genome.policy) == set(MINABLE_POLICY_KEYS)


def test_mutation_and_crossover_keep_the_cost_key_out():
    searcher, inputs, rng = RotationSearcher(), _inputs(), random.Random(11)
    parent = searcher.seed(rng, inputs)
    other = searcher.seed(rng, inputs)
    for _ in range(SAMPLES):
        for genome in (searcher.mutate(rng, parent, inputs),
                       searcher.crossover(rng, parent, other, inputs),
                       searcher.perturb(rng, parent, inputs, None)):
            assert genome is not None
            assert not FROZEN_POLICY_KEYS.intersection(genome.policy)


def test_final_policy_is_locked_to_the_backtest_cost_model():
    """``policy_from_spec`` 收到的最终 policy 里 ``turnoverBps`` 恒等于成本派生值。"""
    from app.discovery.backtest_lane import RotationAssembler

    settings = Settings()
    expected = frozen_policy_values(settings)["turnoverBps"]
    assembler = RotationAssembler(
        [{"templateKey": "momentum_breakout", "params": {}},
         {"templateKey": "ema_cross_scalper", "params": {}}],
        {"rebalanceBars": 24, "turnoverBps": 0.0}, settings)   # ← 故意塞一个 0 进来
    assert assembler.policy().turnover_bps == expected
    assert expected > 0.0, "成本被锁成 0 等于成本消失（BL-1 的另一扇门）"


def test_the_cost_key_really_exists_in_the_template_schema():
    """反向证据：``turnoverBps`` 在 schema 里就是一个普普通通的数值参数——所以必须显式冻结。"""
    schema = STRATEGY_REGISTRY["regime_rotation"].meta.params_schema
    assert schema["turnoverBps"]["type"] == "number"
    assert "turnoverBps" not in POLICY_LADDERS
