"""持仓周期层运行时单测（ai-trade-holding-horizon-modes §6.1 · runtime 组）。

覆盖 epoch 推进 / 部分成交不重置 / 样本入库 / 估算 epoch 不入样本 / 四个 veto 计数 / 零样本口径 /
`merge_into` 键冲突即抛 / 未装配整键缺席 / `horizonArmed` 标记。纯同步，无 IO。
"""
from __future__ import annotations

import pytest

from app.ai_auto.decider import TradeDecision
from app.ai_auto.hold_horizon import HorizonFacts, HorizonRule
from app.ai_auto.horizon_runtime import HorizonRuntime

_NOW = 1_700_000_000.0


def _rule(**over) -> HorizonRule:
    base = {"min_hold_s": 7200.0, "exit_confirm_rounds": 2, "confirm_window_s": 3600.0,
            "hysteresis_k": 1.5, "target_hold_min_s": 14400.0, "target_hold_max_s": 43200.0,
            "expected_hold_bars": 12, "title": "日内波段"}
    return HorizonRule(**{**base, **over})


def _facts(**over) -> HorizonFacts:
    base = {"seq": 1, "side": "long", "entry_px": 100.0, "mark_px": 100.05,
            "leverage": 3.0, "now_wall": _NOW, "px_age_s": 1.0, "cost_bps": 13.0}
    return HorizonFacts(**{**base, **over})


def _close() -> TradeDecision:
    return TradeDecision("close", None, 0.7, "exit")


def test_unarmed_runtime_is_a_no_op():
    """未装配：refresh/gate/inject/stats 全部零动作，payload **整键缺席**。"""
    runtime = HorizonRuntime(None)
    assert runtime.refresh(_facts()) is None
    decision = _close()
    out, degrade, snap = runtime.gate(decision, None, _facts())
    assert out is decision and degrade is None and snap is None
    payload: dict = {}
    runtime.inject(payload, None)
    assert payload == {}
    assert runtime.stats() is None
    assert runtime.merge_into({"a": 1}) == {"a": 1}


def test_note_open_locks_opened_wall_and_partial_fill_does_not_reset():
    """开仓成交锁定 `opened_wall`；**部分成交（同一 epoch）不重置**——否则持仓时长每次增量都归零。"""
    runtime = HorizonRuntime(_rule())
    runtime.note_open({"side": "long"}, _NOW, seq=1)
    runtime.note_open({"side": "long"}, _NOW + 300.0, seq=1)   # 部分成交的第二笔
    assert runtime.state.opened_wall == _NOW
    assert runtime.state.opened_wall_estimated is False
    snap = runtime.refresh(_facts(now_wall=_NOW + 600.0))
    assert snap["holdSeconds"] == pytest.approx(600.0)
    assert snap["openedWallEstimated"] is False


def test_refresh_builds_estimated_epoch_when_open_event_missed():
    """未观察到开仓成交（重连 / 外部干预）⇒ 兜底建 epoch 并**如实标估算**（G1 走 epoch_estimated 豁免）。"""
    runtime = HorizonRuntime(_rule())
    snap = runtime.refresh(_facts())
    assert snap["openedWallEstimated"] is True
    assert snap["releaseReason"] == "epoch_estimated"


def test_note_open_upgrades_an_estimated_epoch():
    """盯市 tick 先于成交事件看到持仓时，`note_open` **必须**把估算标记升级掉。

    `projected` 在成交事件扇出前就已翻面（盯市与决策在不同任务里推进），`refresh()` 会据此兜底建一个
    估算 epoch。若 `note_open` 按「同一 epoch 即早退」处理，该标记会挂满整个持仓期 ⇒ `epoch_estimated`
    豁免每轮命中 ⇒ **G1 恒不生效且不报任何错**（回测侧 I-5 的实盘同款形态）。
    """
    runtime = HorizonRuntime(_rule())
    assert runtime.refresh(_facts())["openedWallEstimated"] is True     # 兜底建的估算 epoch
    runtime.note_open({"side": "long"}, _NOW + 2.0, seq=1)             # 成交事件随后到达
    snap = runtime.refresh(_facts(now_wall=_NOW + 62.0))
    assert snap["openedWallEstimated"] is False
    assert snap["releaseReason"] != "epoch_estimated"
    assert snap["holdSeconds"] == pytest.approx(60.0)
    runtime.note_close({}, _NOW + 3662.0)
    assert runtime.stats()["estimatedEpochs"] == 0                     # 已升级 ⇒ 样本可用
    assert runtime.stats()["roundTrips"] == 1


def test_note_close_records_sample_and_estimated_epoch_excluded():
    """真实持仓时长样本只收**非估算**起点的 epoch；估算的计入 `estimatedEpochs` 而不污染均值。"""
    runtime = HorizonRuntime(_rule())
    runtime.note_open({"side": "long"}, _NOW, seq=1)
    runtime.note_close({}, _NOW + 9000.0)
    runtime.refresh(_facts(seq=2, now_wall=_NOW + 10000.0))     # 估算 epoch
    runtime.note_close({}, _NOW + 20000.0)
    stats = runtime.stats()
    assert stats["roundTrips"] == 2 and stats["estimatedEpochs"] == 1
    assert stats["avgHoldSeconds"] == pytest.approx(9000.0)
    assert stats["withinTargetRate"] == 0.0                     # 9000s < 目标下界 14400s


def test_zero_sample_stats_are_null_not_zero():
    """[P2-3]：零样本时六个派生量一律 `None`——「占比 0%」与「还没有样本」是两个结论。"""
    stats = HorizonRuntime(_rule()).stats()
    for key in ("avgHoldSeconds", "medianHoldSeconds", "minHoldObservedSeconds",
                "maxHoldObservedSeconds", "withinTargetRate", "turnoverPerDay"):
        assert stats[key] is None


def test_veto_counters_split_by_gate_and_reverse():
    """四个 veto 计数分开记：反手被否是**整轮降级**，与平仓被否不可换算。"""
    runtime = HorizonRuntime(_rule())
    runtime.note_open({"side": "long"}, _NOW, seq=1)
    runtime.note_intent("close", None, position_side="long", now_wall=_NOW + 60.0)
    runtime.gate(_close(), None, _facts(now_wall=_NOW + 60.0))          # G1 拦
    runtime.note_intent("open_short", None, position_side="long", now_wall=_NOW + 120.0)
    runtime.gate(TradeDecision("open_short", 0.5, 0.8, "flip"), None,
                 _facts(now_wall=_NOW + 120.0))                          # 反手 + G1 拦
    stats = runtime.stats()
    assert stats["minHoldVetoes"] == 2 and stats["reverseVetoes"] == 1
    assert runtime.state.blocked_streak == 2


def test_note_intent_resets_on_other_actions_but_holds_on_degrade():
    """意图链条：受控动作 +1；无降级的其它动作归零；**降级轮不变**（既非意愿也非观望）。"""
    runtime = HorizonRuntime(_rule())
    runtime.note_open({"side": "long"}, _NOW, seq=1)
    runtime.note_intent("close", None, position_side="long", now_wall=_NOW)
    assert runtime.state.exit_confirm_streak == 1
    runtime.note_intent("hold", "llm_error", position_side="long", now_wall=_NOW + 1)
    assert runtime.state.exit_confirm_streak == 1
    runtime.note_intent("hold", None, position_side="long", now_wall=_NOW + 2)
    assert runtime.state.exit_confirm_streak == 0
    assert runtime.state.first_exit_intent_wall is None


def test_confirm_window_release_is_counted():
    """墙钟上界放行单独计数——该阀门是否真的在用，事后必须能核对（[P0-3] / R-14）。"""
    runtime = HorizonRuntime(_rule(exit_confirm_rounds=3))
    runtime.note_open({"side": "long"}, _NOW, seq=1)
    runtime.note_intent("close", None, position_side="long", now_wall=_NOW + 8000.0)
    facts = _facts(now_wall=_NOW + 20000.0, mark_px=100.5)
    decision = _close()
    out, _, snap = runtime.gate(decision, None, facts)
    assert out is decision and snap["confirmWindowElapsed"] is True
    assert runtime.stats()["confirmWindowReleases"] == 1


def test_inject_marks_profit_guard_armed_and_merge_clash_raises():
    """`inject` 顺带给保护快照打 `horizonArmed`（§2.9 a/b 的两处文案据它切换）；键冲突即抛。"""
    runtime = HorizonRuntime(_rule())
    snap = runtime.refresh(_facts())
    payload = {"profitGuard": {"status": "armed"}}
    runtime.inject(payload, snap)
    assert payload["tradingHorizon"] is snap
    assert payload["profitGuard"]["horizonArmed"] is True
    with pytest.raises(ValueError):
        runtime.merge_into({"horizon": {}})


def test_released_streak_resets_on_normal_round():
    """连续解除计数按**持仓轮**推进，一旦恢复正常即归零（告警不该被历史抖动累计到阈值）。"""
    runtime = HorizonRuntime(_rule())
    runtime.note_open({"side": "long"}, _NOW, seq=1)
    runtime.gate(_close(), None, _facts(stale_feed=True))
    assert runtime.released_streak == 1
    runtime.gate(_close(), None, _facts())
    assert runtime.released_streak == 0
