"""决策工具的 `intelRefs` 可选字段与解析（closed-loop §7.1 · test_ai_trade_decision_intel_refs）。

G-1（对象恒等回退）与 G-3（编造的编号被丢弃且其余字段逐字段相等）的 schema / 解析侧。
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from app.ai_auto.decider import AiTradeDecider, TradeDecision
from app.ai_auto.prompts import DECISION_TOOL_SCHEMA, build_decision_tool_schema
from app.config import get_settings


class _NoopLlm:
    provider = "anthropic"

    async def stream(self, *, system, messages, tools):
        if False:
            yield None


def _decider(**over) -> AiTradeDecider:
    return AiTradeDecider(_NoopLlm(), "sys", context_refresh_rounds=10,
                          settings=get_settings(), **over)


def _raw(**over) -> dict:
    base = {"action": "hold", "reason": "观望", "confidence": 0.5}
    base.update(over)
    return base


# ---------------- schema ----------------
def test_schema_identity_when_cite_off():
    """G-1：总闸关（含全部默认调用）⇒ **对象恒等**返回模块常量。"""
    assert build_decision_tool_schema(0.0) is DECISION_TOOL_SCHEMA
    assert build_decision_tool_schema(0.0, allow_intel_refs=False) is DECISION_TOOL_SCHEMA
    assert "intelRefs" not in DECISION_TOOL_SCHEMA["properties"]


def test_intel_refs_property_appears_only_when_allowed():
    schema = build_decision_tool_schema(0.0, allow_intel_refs=True, intel_max_refs=5)

    prop = schema["properties"]["intelRefs"]
    assert prop["type"] == "array"
    assert prop["items"]["pattern"] == "^I[0-9]{2}$"
    assert prop["maxItems"] == 5
    assert "intelRefs" not in schema["required"], "它永远是可选的"
    assert "不参与任何风控或仓位判定" in prop["description"]
    # 绝不就地改模块常量（`step>0` 时泄漏回常量是本函数栽过的老坑）。
    assert "intelRefs" not in DECISION_TOOL_SCHEMA["properties"]


def test_early_return_guard_lists_the_new_switch():
    """E-1：早退判据必须含每一个总闸——漏一个，该总闸开着时新字段永远露不出来且不报错。"""
    schema = build_decision_tool_schema(0.0, allow_intel_refs=True)
    assert schema is not DECISION_TOOL_SCHEMA


# ---------------- 解析 ----------------
def test_parser_is_a_noop_when_switch_is_off():
    decider = _decider()
    assert decider._resolve_intel_refs(_raw(intelRefs=["I01"])) == ()


def test_parser_filters_normalises_dedupes_and_caps():
    decider = _decider(allow_intel_refs=True, intel_max_refs=2)

    assert decider._resolve_intel_refs(
        _raw(intelRefs=["i01", "I01", "bogus", "I02", "I03"])) == ("I01", "I02")
    assert decider._resolve_intel_refs(_raw(intelRefs="I01")) == ()
    assert decider._resolve_intel_refs(_raw()) == ()


def test_both_return_paths_carry_intel_refs():
    """**两条返回路径都要带上它**——`contrarian_evidence` 的注释已经把「改错行的表现是字段恒为
    None 且不报任何错」写死在那条提前 return 旁边。"""
    decider = _decider(allow_intel_refs=True)

    opened, degrade = decider._map_decision(
        _raw(action="open_long", sizeFraction=0.2, confidence=0.9, intelRefs=["I01"]),
        last_px=100.0)
    assert degrade is None and opened.intel_refs == ("I01",)

    held, degrade = decider._map_decision(_raw(intelRefs=["I02"]), last_px=100.0)
    assert degrade is None and held.intel_refs == ("I02",)


def test_refs_are_kept_for_every_action_not_just_openings():
    """**刻意不限定 action 作用域**：hold 轮的引用同样要记，那正是轴 B 区分 entry/hold 的前提。"""
    decider = _decider(allow_intel_refs=True)
    for action in ("hold", "close"):
        decision, _ = decider._map_decision(_raw(action=action, intelRefs=["I03"]),
                                            last_px=100.0)
        assert decision.intel_refs == ("I03",)


def test_bogus_refs_change_nothing_else_on_the_decision():
    """G-3：写一个编造的编号 ⇒ 其余字段**逐字段相等**于没写它的那一次，且不产生任何降级。"""
    decider = _decider(allow_intel_refs=True)
    clean, degrade_a = decider._map_decision(
        _raw(action="open_long", sizeFraction=0.3, confidence=0.9), last_px=100.0)
    noisy, degrade_b = decider._map_decision(
        _raw(action="open_long", sizeFraction=0.3, confidence=0.9, intelRefs=["I99", "nope"]),
        last_px=100.0)

    assert degrade_a is None and degrade_b is None
    assert replace(noisy, intel_refs=()) == clean


def test_trade_decision_default_is_an_empty_tuple():
    """末位带默认值 ⇒ 既有全部位置构造与元组解包零改动。"""
    decision = TradeDecision("hold", None, None, "r")
    assert decision.intel_refs == ()


@pytest.mark.parametrize("bad", [None, 5, {"a": 1}])
def test_non_list_payloads_never_raise(bad):
    decider = _decider(allow_intel_refs=True)
    assert decider._resolve_intel_refs(_raw(intelRefs=bad)) == ()
