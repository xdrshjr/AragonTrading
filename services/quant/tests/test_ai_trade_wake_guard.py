"""`ai_auto/wake_guard.py` 纯函数单测（ai-trade-reflection-hold-inertia-fix F4c §六-4/§六-8）。

`should_skip_interval_wake` 全分支表驱动：仅「守卫开启 + 空仓 + 无挂单 + 无新确认根 + 两价有效
且相对变动 < 阈值」才跳过（True）；任一条件不满足一律唤醒（False）。无 IO、无状态，不构造
executor / session、不落库、不联网。
"""
from __future__ import annotations

import pytest

from app.ai_auto.wake_guard import should_skip_interval_wake


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
