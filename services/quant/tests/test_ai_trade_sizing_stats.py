"""仓位统计加法键与 `_finalize_run` 合并落点（vol-sizing-and-drawdown-brake §3.9 / T-21）。

两条判据：
1. **键冲突即抛**（T-21）：绝不静默覆盖既有 `aiStats` 键——统计口径一旦被悄悄改写，事后无从发现。
2. **合并落在调用点**：`_compute_ai_stats` 是**模块级纯函数**，拿不到 `self`，故「在其内部合并」物理上
   不可实现；本仓库既有的 `wakeSkips` / `profitGuardExits` 四键都走调用点合并，本层顺势复用。
"""
from __future__ import annotations

import inspect

import pytest

from app.ai_auto.drawdown_brake import BrakeCounters, initial_state
from app.ai_auto.session import AiTradeSession, _compute_ai_stats
from app.ai_auto.sizing_runtime import SizingRuntime, build_sizing_runtime
from app.ai_auto.stats_ext import build_sizing_stats, merge_sizing_stats
from app.config import Settings


def _counters(**over):
    runtime = SizingRuntime()
    for key, value in over.items():
        setattr(runtime.counters, key, value)
    return runtime.counters


def test_build_sizing_stats_shape():
    stats = build_sizing_stats(_counters(gated_rounds=10, scaled_rounds=4,
                                         quantize_erased_rounds=2, atr_stop_rounds=9,
                                         reversal_skips=1, resting_skips=3,
                                         corr_scaled_rounds=2,
                                         exposure_unreachable_rounds=1,
                                         scale_ratios=[0.5, 0.5, 0.25, 1.0]),
                               BrakeCounters(slow_rounds=6, halt_rounds=2, cooldown_hits=1,
                                             halt_unrealized_shares=[0.8, 0.6]),
                               initial_state(1000.0))
    sizing = stats["sizing"]
    assert sizing["scaleRate"] == 0.4 and sizing["avgScaleRatio"] == 0.5625
    assert sizing["quantizeErasedRounds"] == 2      # 与 brakeSlowRounds 分开看：一个是「缩了但被吃回去」，
    assert sizing["brakeSlowRounds"] == 6           # 一个是「减速在这些轮是激活的」，二者不可互相换算
    assert sizing["brakeHaltRounds"] == 2 and sizing["lossStreakCooldowns"] == 1
    assert sizing["haltUnrealizedShareAvg"] == 0.7
    assert sizing["atrStopRounds"] == 9
    assert sizing["restingSkips"] == 3 and sizing["reversalSkips"] == 1
    assert sizing["corrScaledRounds"] == 2 and sizing["exposureUnreachableRounds"] == 1
    assert sizing["maxDrawdownPct"] == 0.0


def test_empty_session_stats_are_honest():
    stats = build_sizing_stats(_counters(), BrakeCounters(), initial_state(1000.0))["sizing"]
    assert stats["scaleRate"] == 0.0
    assert stats["avgScaleRatio"] is None            # 一次都没缩过 ⇒ None，绝不填 0（0 = 缩到零）
    assert stats["haltUnrealizedShareAvg"] is None


# ---------- T-21：键冲突即抛 ----------
def test_sizing_stats_merge_conflict_raises():
    base = {"holdRate": 0.5, "sizing": {"scaledRounds": 1}}
    with pytest.raises(ValueError) as exc:
        merge_sizing_stats(base, {"sizing": {"scaledRounds": 9}})
    assert "sizing" in str(exc.value)
    assert base["sizing"]["scaledRounds"] == 1       # 抛出后原 dict 不被污染


def test_merge_is_additive_and_leaves_existing_keys():
    merged = merge_sizing_stats({"holdRate": 0.5}, {"sizing": {"scaledRounds": 3}})
    assert merged["holdRate"] == 0.5 and merged["sizing"]["scaledRounds"] == 3


# ---------- 合并落点：调用点，而不是 `_compute_ai_stats` 内部 ----------
def test_merge_lands_at_the_call_site_not_inside_compute_ai_stats():
    source = inspect.getsource(_compute_ai_stats)
    assert "sizing" not in source                    # 纯函数一字不动（拿不到 self，物理上也做不到）
    assert _compute_ai_stats([], 0).get("sizing") is None
    # ai-trade-pause-resume §3.1.3 ⑦：metrics 构造已从 `_finalize_run` 抽成 `_build_run_metrics`
    # （暂停快照与停止落库共用同一段富化，绝不复制）。**本条断言的口径一字未改**——合并仍必须发生
    # 在调用点，只是那个调用点现在叫 `_build_run_metrics`。
    finalize = inspect.getsource(AiTradeSession._build_run_metrics)
    # 断言口径不变（合并必须发生在调用点），但**不再逐字匹配整个调用表达式**：
    # fill-truth-and-effective-controls 轴 E 给 `merge_into` 加了第二个实参（`self.executor`，
    # 三个挂单去留计数住在执行器上），调用因此换行了。把一个源码门禁写成对格式敏感的整串比对，
    # 会让它在一次纯粹的换行上变红，而那与它要守的不变式毫无关系。
    assert "self._sizing.merge_into(" in finalize
    assert 'metrics["aiStats"]' in finalize


def test_runtime_merge_into_produces_the_additive_key():
    runtime = build_sizing_runtime(Settings(ai_trade_sizing_enabled=True), "BTC-USDT-SWAP", 900)
    merged = runtime.merge_into({"holdRate": 0.5})
    assert merged["holdRate"] == 0.5
    assert merged["sizing"]["gatedRounds"] == 0
