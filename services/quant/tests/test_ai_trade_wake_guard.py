"""`ai_auto/wake_guard.py` 纯函数单测（ai-trade-reflection-hold-inertia-fix F4c §六-4/§六-8
+ ai-trade-wake-scheduler-closed-loop §6.1 T-6~T-8）。

`should_skip_interval_wake` 全分支表驱动：仅「守卫开启 + 空仓 + 无挂单 + 无新确认根 + 两价有效
且相对变动 < 阈值」才跳过（True）；任一条件不满足一律唤醒（False）。`should_skip_wake` 覆盖
`SkipPolicy` 迁移后的 heartbeat 逐字不变 + 轴 B 的 armed / idle / unavailable 三分与上限。
无 IO、无状态，不构造 executor / session、不落库、不联网。
"""
from __future__ import annotations

import pytest

from app.ai_auto.profit_guard import (
    STATUS_ARMED,
    STATUS_BREAKEVEN_ARMED,
    STATUS_IDLE,
    STATUS_UNAVAILABLE,
)
from app.ai_auto.wake_guard import SkipPolicy, should_skip_interval_wake, should_skip_wake


def _kwargs(**overrides) -> dict:
    """基线入参：守卫开启（0.05%）、空仓、无挂单、无新根、价格完全未动 → 应跳过。"""
    values = dict(position_side="flat", has_resting=False,
                  bar_index=5, last_round_bar_index=5,
                  px=30000.0, last_round_px=30000.0, threshold=0.0005)
    values.update(overrides)
    return values


def test_skips_when_flat_unchanged_and_micro_move():
    """基线：空仓+无挂单+同根+价格零变动 → 跳过。"""
    assert should_skip_interval_wake(**_kwargs()) is True


def test_skips_when_price_move_below_threshold():
    """价格微动（0.02% < 0.05%）→ 仍跳过。"""
    assert should_skip_interval_wake(**_kwargs(px=30006.0)) is True


@pytest.mark.parametrize("overrides, label", [
    ({"threshold": 0.0}, "阈值 0 = 守卫关闭"),
    ({"position_side": "long"}, "持多仓永不跳过"),
    ({"position_side": "short"}, "持空仓永不跳过"),
    ({"has_resting": True}, "挂单轮永不跳过"),
    ({"bar_index": 6}, "自上一决策轮以来有新确认根"),
    ({"last_round_bar_index": None}, "尚无决策轮（守卫直通）"),
    ({"px": 30015.1}, "价格越阈（0.0503% >= 0.05%）"),
    ({"px": None}, "本次实时价缺失（价不可信宁可唤醒）"),
    ({"last_round_px": None}, "上一轮参考价缺失"),
    ({"px": 0.0}, "本次实时价非正数"),
    ({"last_round_px": -1.0}, "上一轮参考价非正数"),
], ids=lambda v: v if isinstance(v, str) else "")
def test_wakes_when_any_condition_fails(overrides, label):
    """任一条件不满足 → False（唤醒）；条件之间无隐式耦合。"""
    assert should_skip_interval_wake(**_kwargs(**overrides)) is False


def test_threshold_boundary_is_strict_less_than():
    """边界：相对变动恰等于阈值 → 不跳过（严格 < 判定，用二进制可精确表示的值避免浮点毛刺）；
    略小于阈值 → 跳过。"""
    # 45000 / 30000 - 1 = 0.5 恰为阈值（1.5 与 0.5 均可被二进制精确表示）→ False
    assert should_skip_interval_wake(**_kwargs(px=45000.0, threshold=0.5)) is False
    # 30014 / 30000 - 1 ≈ 0.000467 < 0.0005 → True
    assert should_skip_interval_wake(**_kwargs(px=30014.0)) is True


def test_downward_move_uses_absolute_change():
    """下跌方向同样按 |变动| 判定：-0.02% 跳过、-0.1% 唤醒。"""
    assert should_skip_interval_wake(**_kwargs(px=29994.0)) is True
    assert should_skip_interval_wake(**_kwargs(px=29970.0)) is False


# ============================ 结构化唤醒对象版守卫（`SkipPolicy`）============================
def _policy(**overrides) -> SkipPolicy:
    """基线策略：守卫开启（pct + ATR 双判据）、空仓上限 5、轴 B 总闸开、持仓上限 3。"""
    values = dict(threshold_pct=0.0005, threshold_atr_mult=0.5, max_skip_streak=5,
                  position_watch_enabled=True, position_watch_max_skip_streak=3)
    values.update(overrides)
    return SkipPolicy(**values)


def _wake(kind: str, **overrides) -> dict:
    return {"kind": kind, "skipStreak": 0, "guardStatus": None, **overrides}


# ---------- T-8：heartbeat 分支在迁移后逐字不变 ----------
@pytest.mark.parametrize(("wake", "policy", "expected"), [
    (_wake("heartbeat"), _policy(), True),
    (_wake("heartbeat", skipStreak=4), _policy(), True),
    (_wake("heartbeat", skipStreak=5), _policy(), False),              # 触到上限即强制放行
    (_wake("heartbeat", skipStreak=99), _policy(max_skip_streak=0), True),   # 0 = 不封顶
    (_wake("heartbeat"), _policy(threshold_pct=0.0, threshold_atr_mult=0.0), False),  # 守卫关
    (_wake("candle_close"), _policy(), False),
    (_wake("bar_advanced"), _policy(), False),
    (_wake("signal_flip"), _policy(), False),
    (_wake("resting_order"), _policy(), False),
    (_wake("price_breakout"), _policy(), False),
])
def test_heartbeat_branch_is_unchanged_after_the_skip_policy_migration(wake, policy, expected):
    assert should_skip_wake(wake, policy) is expected


def test_atr_criterion_alone_keeps_the_guard_on():
    """两条阈值任一 > 0 即「守卫开启」（pct 被调成 0、只留 ATR 判据是合法配置）。"""
    assert should_skip_wake(_wake("heartbeat"), _policy(threshold_pct=0.0)) is True
    assert should_skip_wake(_wake("heartbeat"), _policy(threshold_atr_mult=0.0)) is True


# ---------- T-6：轴 B 的 armed / idle / unavailable 三分 ----------
@pytest.mark.parametrize(("status", "expected"), [
    (STATUS_ARMED, True),
    (STATUS_BREAKEVEN_ARMED, True),
    (STATUS_IDLE, False),
    (STATUS_UNAVAILABLE, False),
    (None, False),
])
def test_position_watch_is_skipped_only_when_the_deterministic_layer_is_proven_on_duty(
        status, expected):
    """只认 `armed`/`breakeven_armed`：那两态意味着存在一条**只收紧不放松**的退出线。

    `unavailable` 是利润保护层的 **fail-open 降级态**——它只表示「本层算不出保护线」，**不代表
    交易所侧有兜底**。此时还跳过唤醒，就等于在一个既无本地保护线、又可能无交易所侧挂单的持仓上
    停止一切研判，这是本轮唯一可能造成真实亏损的路径。`None`（保护层未装配）同理。
    """
    assert should_skip_wake(_wake("position_watch", guardStatus=status), _policy()) is expected


# ---------- T-7：上限与总闸 ----------
def test_position_watch_streak_ceiling_is_tighter_than_the_flat_one():
    """持仓时「什么都不做」的机会成本更高，故上限更紧（默认 3 vs 5）。"""
    armed = {"guardStatus": STATUS_ARMED}
    assert should_skip_wake(_wake("position_watch", skipStreak=2, **armed), _policy()) is True
    assert should_skip_wake(_wake("position_watch", skipStreak=3, **armed), _policy()) is False
    # 同一个 skipStreak 在空仓上限（5）下仍可跳过 ⇒ 两条上限确实各管各的
    assert should_skip_wake(_wake("heartbeat", skipStreak=3), _policy()) is True


def test_position_watch_master_switch_off_falls_back_to_never_skipping():
    """总闸关 ⇒ 持仓轮**逐字回退**「永不跳过」，与本轮合入前完全一致。"""
    wake = _wake("position_watch", guardStatus=STATUS_ARMED)
    assert should_skip_wake(wake, _policy(position_watch_enabled=False)) is False
    assert should_skip_wake(_wake("heartbeat"), _policy(position_watch_enabled=False)) is True


def test_position_watch_ceiling_of_one_forces_a_round_after_every_single_skip():
    """上限 1 = 跳一轮就必放行一轮（最紧的合法配置）。

    「0=不封顶」这条极性在**配置层**被堵死（`ge=1`，见 `test_config`）而不是在本纯函数里——守卫
    与既有 `max_skip_streak` 保持同一形状，安全约束立在唯一的入口上，不在两处各写一遍。
    """
    armed = {"guardStatus": STATUS_ARMED}
    policy = _policy(position_watch_max_skip_streak=1)
    assert should_skip_wake(_wake("position_watch", skipStreak=0, **armed), policy) is True
    assert should_skip_wake(_wake("position_watch", skipStreak=1, **armed), policy) is False
