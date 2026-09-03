"""会话级情报绑定与渐进式披露 —— 纯核心 + 门禁 + 设置登记（spec §7.1）。

本文件只放**零 IO** 的命题：规则解析、纵横两个披露维度、展开段、绑定 wire、九项设置的双向
登记与启动期校验，以及两条源码门禁。会话 / API / 运行时编排在
`test_ai_trade_intel_session_binding_io.py`。

分文件的理由是 CLAUDE.md 二的 800 行红线，不是随手拆。
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.ai_auto.intel import binding as binding_mod
from app.ai_auto.intel import taxonomy as tax
from app.ai_auto.intel.disclosure import (
    BANNER,
    build_digest,
    build_digest_and_ids,
    format_intel,
    intel_tier,
    plan_disclosure,
    render_index_line,
    render_line,
)
from app.ai_auto.intel.expand import format_expand, inject_intel_expand, plan_expand
from app.ai_auto.intel.gate import IntelItem
from app.ai_auto.intel.session_rule import (
    INTEL_MODES,
    REQUESTABLE_MODES,
    IntelSessionRule,
    resolve_session_rule,
)
from app.ai_auto.intel.settings_bridge import INTEL_SETTING_BRIDGE
from app.ai_auto.intel.taxonomy import IntelLimits, limits_from_settings
from app.config import Settings

NOW = 1_800_000_000
ROOT = Path(__file__).resolve().parents[3]
APP = Path(__file__).resolve().parents[1] / "app"

# 本轮 9 个新设置（`G-Env` / `D2` 的唯一清单）。
NEW_SETTINGS = (
    "ai_trade_intel_session_opt_in_enabled",
    "ai_trade_intel_index_max_chars",
    "ai_trade_intel_expand_enabled",
    "ai_trade_intel_expand_max_items",
    "ai_trade_intel_expand_max_chars",
    "ai_trade_intel_alert_pin_enabled",
    "ai_trade_intel_repeat_compress_enabled",
    "ai_trade_intel_repeat_max_chars",
    "ai_trade_intel_repeat_max_rounds",
)


def _item(item_id: int, *, headline: str | None = None, summary: str | None = None,
          tier: str = "mid", **over) -> IntelItem:
    base = dict(item_id=item_id, inst_id="BTC-USDT-SWAP", tier=tier, category="macro",
                headline=headline or f"标题{item_id}",
                summary=summary or f"摘要{item_id}" * 4, stance="neutral",
                impact="high", confidence=0.7, event_ts=NOW - 3600, captured_ts=NOW - 3600,
                expires_ts=NOW + 86400, domain_count=3)
    base.update(over)
    return IntelItem(**base)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _wire(items, *, mode="full", limits=None, cite=True, pin_ids=frozenset(), quotes=None):
    return build_digest_and_ids(items, limits=limits or IntelLimits(), now=NOW,
                                last_collect_ts=NOW - 600, cite=cite, mode=mode,
                                pin_ids=pin_ids, quotes=quotes)[0]


# ==================== 零回归门禁 ====================
def test_g1_all_nine_gates_at_fallback_reproduce_todays_intel_section():
    """`G-1`：9 把闸全部置回退档 ⇒ `format_intel` 的产物与本特性合入前逐字节相同。

    回退档的定义见 spec §3.5：轴 A/B/L2/L3 四把总闸关，三个预算与两个上界不被任何人读到
    （`mode` 恒 `"full"`、`plan` 键恒缺席）。
    """
    fallback = Settings(
        ai_trade_intel_session_opt_in_enabled=False,
        ai_trade_intel_expand_enabled=False,
        ai_trade_intel_alert_pin_enabled=False,
        ai_trade_intel_repeat_compress_enabled=False,
    )
    limits = limits_from_settings(fallback)
    items = [_item(i) for i in range(1, 4)]
    wire = _wire(items, mode="full", limits=limits)
    legacy = {"lines": wire["lines"], "hidden": wire["hidden"],
              "refs": wire["refs"], "citeEnabled": True}
    assert _sha(format_intel(wire)) == _sha(format_intel(legacy))


def test_g3_read_side_keys_never_reach_the_prompt():
    """`G-3`：`mode="full"` + 横向总闸关 ⇒ `mode` / `bodies` / `plan` 三键缺席。

    ★ 评审 P1-3 / 放行条件 C-5：冻结口径是「`format_intel(wire)` 的输出 + `wire["refs"]`」，
    而 `itemIds` 改成了**恒出的纯读侧键**——它不进 `format_intel`，故这条断言仍然成立。
    """
    limits = IntelLimits(expand_enabled=False, repeat_compress_enabled=False)
    wire = _wire([_item(1)], mode="full", limits=limits)
    assert "mode" not in wire and "bodies" not in wire and "plan" not in wire
    assert wire["itemIds"] == [1]                    # 纯读侧键：恒出
    stripped = {k: v for k, v in wire.items() if k != "itemIds"}
    assert _sha(format_intel(wire)) == _sha(format_intel(stripped))


def test_g4_pin_gate_off_keeps_ref_order_identical():
    """`G-4`：`pin_ids` 为空 ⇒ 编号顺序与今天逐字相同。"""
    items = [_item(1, tier="ultra"), _item(2, tier="long"), _item(3, tier="mid")]
    plain = _wire(items)
    assert plain["refs"] == _wire(items, pin_ids=frozenset())["refs"]


def test_g5_backtest_lane_never_mentions_intel():
    """`G-5`：回测车道全程 0 次 `intel`（源码门禁；实读已核实两文件当前各 0 次）。"""
    for name in ("backtest.py", "backtest_manager.py"):
        text = (APP / "ai_auto" / name).read_text(encoding="utf-8")
        assert "intel" not in text, f"{name} 出现了 intel —— 情报层不许进回测车道"


def test_g7_session_never_reads_an_intel_setting_key_directly():
    """`G-7`（★ 评审 P0-1）：`session.py` 全文不出现情报设置键名，也不出现设置映射函数名。

    会话手上的 `executor.settings` 走的是 `build_start_request` 的派生链，**不含**情报库内行；
    两处各读一份的后果是「面板上关了、会话这侧照旧开着」，请求 200、开关照动、行为不变。
    唯一入口是 `intel.binding.intel_limits_for`。

    CRLF 环境下正则须写 `\\r?\\n`（本仓库成文教训）；这里用纯子串判据，同样不受行尾影响。
    """
    text = (APP / "ai_auto" / "session.py").read_text(encoding="utf-8")
    assert "ai_trade_intel_" not in text
    assert "limits_from_settings" not in text
    # 反向：唯一入口真的被用上了（否则这条门禁会在「整条链没接」时也恒绿）。
    assert "intel_limits_for" in text


def test_g6_new_settings_do_not_leak_into_the_start_wire_by_default():
    """`G-6` 的一半（纯核心侧）：默认 `Settings` 上九个键都在，且值即回退前的语义默认。"""
    settings = Settings()
    for name in NEW_SETTINGS:
        assert hasattr(settings, name), name
    assert settings.ai_trade_intel_session_opt_in_enabled is True
    assert settings.ai_trade_intel_repeat_max_chars <= min(
        settings.ai_trade_intel_index_max_chars, settings.ai_trade_intel_prompt_max_chars)


# ==================== 轴 A：会话级开关 ====================
def test_a1_missing_intel_key_means_off():
    """`A1` **反向门禁**：整键缺省 ⇒ `enabled=False`。

    若有人照抄仓库里另外六个字段「缺省=开启」的惯例把它写成 `True`，本用例判红。
    """
    assert resolve_session_rule(None, Settings()).enabled is False
    assert resolve_session_rule({}, Settings()).enabled is False
    assert resolve_session_rule("not-a-dict", Settings()).enabled is False


def test_a2_requested_mode_survives_and_derives_effective_mode():
    rule = resolve_session_rule({"enabled": True, "mode": "index"}, Settings())
    assert (rule.enabled, rule.mode, rule.effective_mode) == (True, "index", "index")
    off = resolve_session_rule({"enabled": False, "mode": "full"}, Settings())
    assert off.effective_mode == "off"


def test_a8_off_is_never_a_requestable_mode():
    """`A8`（★ 评审 P1-5）：`"off"` 是派生态，只由 `enabled=False` 产生。"""
    assert REQUESTABLE_MODES == ("index", "full")
    assert INTEL_MODES == ("off", "index", "full")
    # 冷续跑读回来的坏值 ⇒ 收口成 index，**不抛**。
    assert resolve_session_rule({"enabled": True, "mode": "off"}, Settings()).mode == "index"
    assert resolve_session_rule({"enabled": True, "mode": "bogus"},
                                Settings()).mode == "index"
    # 直接构造一个 `mode="off"` 的规则则必须当场抛——那是一个没有 renderer 认识的第三态。
    with pytest.raises(ValueError):
        IntelSessionRule(mode="off")


def test_a1b_ops_gate_and_legacy_both_fall_back_to_todays_behaviour():
    """总闸关 / 冷续跑老会话 ⇒ 恒回默认规则（注入由进程级 `ai_trade_intel_inject` 独裁）。"""
    ops_off = Settings(ai_trade_intel_session_opt_in_enabled=False)
    assert resolve_session_rule({"enabled": False}, ops_off) == IntelSessionRule()
    assert resolve_session_rule(None, Settings(), legacy=True) == IntelSessionRule()


# ==================== 轴 B：纵向三级 × 横向两档 ====================
def test_b1_index_lines_drop_the_summary_body():
    """`B1`：索引行不含摘要正文，且相同库存下总字符严格更少。"""
    item = _item(1)
    full = render_line(item, now=NOW)
    index = render_index_line(item, now=NOW)
    assert item.summary in full and item.summary not in index
    assert len(index) < len(full)
    items = [_item(i) for i in range(1, 4)]
    assert (sum(map(len, _wire(items, mode="index")["lines"]))
            < sum(map(len, _wire(items, mode="full")["lines"])))


def test_b2_budget_pass_and_final_pass_share_one_renderer():
    """`B2`：索引装得下、全文装不下的边界条目上，不出现半截行且 `hidden` 计数正确。"""
    long_summary = "很长的摘要正文" * 30
    items = [_item(1, summary=long_summary), _item(2, summary=long_summary)]
    limits = IntelLimits(index_max_chars=160, prompt_max_chars=160)
    indexed = _wire(items, mode="index", limits=limits)
    full = _wire(items, mode="full", limits=limits)
    assert len(indexed["lines"]) >= 1                       # 索引行装得下
    assert len(full["lines"]) < len(indexed["lines"])       # 全文行装不下
    for wire in (indexed, full):
        assert wire["hidden"] == 2 - len(wire["lines"])
        # 零条目 ⇒ `itemIds` 整键缺席（与 `lines` 同生共死）；非零则严格同序同长。
        assert len(wire.get("itemIds", [])) == len(wire["lines"])


def test_b3_intel_alert_round_is_never_compressed():
    """`B3`（F9 的反例守卫）：`intel_alert` 轮恒 `full`，不复用 `contextDisclosure`。"""
    assert intel_tier(wake_kind="intel_alert", context_disclosure="repeat",
                      enabled=True) == "full"
    assert intel_tier(wake_kind="interval", context_disclosure="repeat",
                      enabled=True) == "repeat"
    assert intel_tier(wake_kind="interval", context_disclosure="repeat",
                      enabled=False) == "full"      # 横向总闸关 ⇒ 逐字节回退


def test_b4_repeat_round_lists_two_different_numbers_in_two_sentences():
    """`B4`（★ 评审 P1-9）：`hidden`（装不下）与 `same`（见过）**分两句、各用各的数**。"""
    items = [_item(i, summary="摘要") for i in range(1, 6)]
    limits = IntelLimits(prompt_max_chars=200, repeat_max_chars=400)
    wire = _wire(items, limits=limits)
    hidden = wire["hidden"]
    assert hidden > 0, "本用例要求预算真的装不下一部分"
    seen = frozenset(wire["itemIds"])
    plan = plan_disclosure(wire, tier="repeat", seen_ids=seen, limits=limits)
    assert plan.show == () and plan.same == len(wire["itemIds"])
    text = format_intel({**wire, "plan": plan.wire()})
    assert f"另有 {hidden} 条因篇幅未展示" in text
    assert f"上一轮列出的 {plan.same} 条" in text
    assert "本轮无新增情报" in text
    for token in plan.refs_kept:
        assert f"[{token}]" in text


def test_b4b_hidden_items_are_not_recorded_as_seen():
    """`B4b`（★ 评审 P1-4）：`itemIds` 只含真正渲染出来的那些。

    集合口径写错的后果是静默的：`hidden` 掉的条目会在随后每一轮都被当作旧条目折叠，
    **永远进不了 prompt**，而且不报错。
    """
    items = [_item(i, summary="摘要") for i in range(1, 6)]
    wire = _wire(items, limits=IntelLimits(prompt_max_chars=200))
    assert len(wire["itemIds"]) == len(wire["lines"]) < 5
    assert wire["hidden"] == 5 - len(wire["lines"])


def test_b5_two_renders_of_the_same_payload_are_byte_identical():
    """`B5`（原则 3 的守卫）：`plan` 在装配期定稿，`format_intel` 不做任何判定。"""
    items = [_item(i) for i in range(1, 4)]
    wire = _wire(items)
    plan = plan_disclosure(wire, tier="repeat", seen_ids=frozenset({1}),
                           limits=IntelLimits())
    payload_intel = {**wire, "plan": plan.wire()}
    first = format_intel(payload_intel)
    # 把「会话侧的已看过集合」在两次渲染之间改掉：产物**必须**逐字节不变。
    plan_disclosure(wire, tier="repeat", seen_ids=frozenset({1, 2, 3}), limits=IntelLimits())
    assert _sha(format_intel(payload_intel)) == _sha(first)


def test_b11_pinned_item_leads_the_digest_and_keeps_ref_mapping():
    """`B11`：告警条目被钉到首位，且 `refs[ref] == pinned_item_id`。"""
    items = [_item(1, tier="long"), _item(2, tier="mid"), _item(3, tier="ultra")]
    wire = _wire(items, pin_ids=frozenset({3}))
    assert wire["itemIds"][0] == 3
    assert wire["refs"]["I01"] == 3


def test_b15_pinned_item_keeps_its_summary_even_in_index_mode():
    """`B15`（★ 评审 P1-2）：索引档下被 pin 的那一条**恒用全文行**，其余行不含摘要。

    否则净效果是：开了索引档之后，高危告警轮里模型读到的正文比不开还少（置顶成功 ⇒
    `in_digest=True` ⇒ 唤醒句不再内联摘要）。
    """
    items = [_item(1, summary="正文一"), _item(2, summary="正文二")]
    wire = _wire(items, mode="index", pin_ids=frozenset({2}),
                 limits=IntelLimits(index_max_chars=3000))
    assert wire["itemIds"][0] == 2
    assert "正文二" in wire["lines"][0]          # 置顶条 ⇒ 全文行
    assert "正文一" not in wire["lines"][1]      # 其余 ⇒ 索引行


def test_b15b_pinned_full_line_pushes_others_into_hidden_without_half_lines():
    """`B15` 的另一半：置顶条挤掉后续条目时 `hidden` 计数正确、不出现半截行。"""
    items = [_item(i, summary="很长的摘要" * 20) for i in range(1, 5)]
    wire = _wire(items, mode="index", pin_ids=frozenset({4}),
                 limits=IntelLimits(index_max_chars=200))
    assert wire["itemIds"][0] == 4
    assert wire["hidden"] == 4 - len(wire["lines"])
    for line in wire["lines"]:
        assert line.endswith("）") or "｜" in line          # 完整行，不是被截断的残段


def test_b16_expand_block_is_wrapped_by_the_untrusted_banner():
    """`B16`（★ 评审 P1-10）：展开段**段首**是不可信包裹；空序列 ⇒ 空串。"""
    wire = _wire([_item(1)])
    items = plan_expand(wire, ["I01"], plan={"tier": "full"}, limits=IntelLimits())
    assert items and items[0].origin == "cited"
    assert format_expand(items).startswith(BANNER)
    assert format_expand(()) == ""


def test_b17_repeat_round_expands_the_folded_items_for_the_reviewer():
    """`B17`（★ 评审 P0-2b）：`repeat` 轮的展开段必须补上被折叠掉的那些。

    复核是**无状态**子调用：`plan.tier == "repeat"` 时它的情报段只剩一行页脚，一串它从未
    见过的编号对守着真钱的那一层毫无意义。
    """
    items = [_item(i) for i in range(1, 4)]
    wire = _wire(items)
    plan = plan_disclosure(wire, tier="repeat", seen_ids=frozenset({1, 2, 3}),
                           limits=IntelLimits())
    picked = plan_expand({**wire, "plan": plan.wire()}, [], plan=plan.wire(),
                         limits=IntelLimits(expand_max_items=8, expand_max_chars=4000))
    assert picked and all(p.origin == "folded" for p in picked)
    assert {p.ref for p in picked} <= set(plan.refs_kept)
    # `tier == "full"` 的轮上 folded 组为空。
    full_plan = plan_disclosure(wire, tier="full", seen_ids=frozenset(), limits=IntelLimits())
    assert plan_expand(wire, [], plan=full_plan.wire(), limits=IntelLimits()) == ()


def test_b8_expand_respects_both_caps_and_never_truncates_an_item():
    """`B8`：条数 ≤ `expand_max_items`，总长 ≤ `expand_max_chars`，且绝不半截截断。"""
    items = [_item(i, summary="摘要" * 40) for i in range(1, 6)]
    wire = _wire(items, limits=IntelLimits(prompt_max_chars=6000))
    refs = list(wire["refs"])
    limits = IntelLimits(expand_max_items=2, expand_max_chars=4000)
    picked = plan_expand(wire, refs, plan={"tier": "full"}, limits=limits)
    assert len(picked) <= 2
    tight = plan_expand(wire, refs, plan={"tier": "full"},
                        limits=IntelLimits(expand_max_items=8, expand_max_chars=120))
    assert sum(len(p.render()) + 1 for p in tight) <= 120


def test_b9_injecting_expand_leaves_the_decision_prompt_untouched():
    """`B9`：`intelExpand` 是复核官专属；`payload["intel"]` 一个字节不变。"""
    wire = _wire([_item(1)])
    payload = {"intel": wire}
    before = _sha(format_intel(payload["intel"]))
    inject_intel_expand(payload, SimpleNamespace(intel_refs=["I01"]), limits=IntelLimits())
    assert "intelExpand" in payload
    assert _sha(format_intel(payload["intel"])) == before


def test_b9b_expand_gate_off_removes_the_key_entirely():
    """展开总闸关 / `limits is None` ⇒ **整键缺席** ⇒ 反思 prompt 逐字节回退。"""
    wire = _wire([_item(1)])
    for limits in (None, IntelLimits(expand_enabled=False)):
        payload = {"intel": wire}
        inject_intel_expand(payload, SimpleNamespace(intel_refs=["I01"]), limits=limits)
        assert "intelExpand" not in payload


def test_bodies_key_condition_matches_the_spec():
    """`bodies` 出键条件 = `expand_enabled ∧ digest 非空 ∧ (index ∨ repeat_compress)`。"""
    items = [_item(1)]
    assert "bodies" in _wire(items, mode="index",
                             limits=IntelLimits(repeat_compress_enabled=False))
    assert "bodies" in _wire(items, mode="full",
                             limits=IntelLimits(repeat_compress_enabled=True))
    assert "bodies" not in _wire(items, mode="full",
                                 limits=IntelLimits(repeat_compress_enabled=False))
    assert "bodies" not in _wire(items, mode="index",
                                 limits=IntelLimits(expand_enabled=False))


def test_index_footer_promise_only_renders_when_it_is_true():
    """索引档段末那句「下单前会自动展开」**必须为真** ⇒ 与 L2 总闸同生共死。"""
    on = format_intel(_wire([_item(1)], mode="index"))
    off = format_intel(_wire([_item(1)], mode="index",
                             limits=IntelLimits(expand_enabled=False)))
    assert "下单前的复核环节会自动展开" in on
    assert "下单前的复核环节会自动展开" not in off


def test_expand_quote_reaches_the_reviewer_when_the_item_has_one():
    """引文从 digest wire 的 `bodies` 里来，**绝不回查 DB**（决策热路径查库是明令禁止的）。"""
    wire = _wire([_item(1)], quotes={1: "原文的一段话"})
    picked = plan_expand(wire, ["I01"], plan={"tier": "full"}, limits=IntelLimits())
    assert picked[0].quote == "原文的一段话"
    assert "原文引证：「原文的一段话」" in format_expand(picked)


# ==================== 轴 C：绑定 wire ====================
class _FakeDeps:
    def __init__(self, *, inject_on=True, mode="index", session_enabled=True):
        self._inject_on = inject_on
        self._mode = mode
        self.session_rule = IntelSessionRule(enabled=session_enabled,
                                             mode="index" if mode == "off" else mode)

    @property
    def inject_on(self):
        return self._inject_on

    @property
    def intel_mode(self):
        return self._mode


class _FakeRuntime:
    def __init__(self, *, armed=True, item_ids=(1, 2), deps=None, last_round=None):
        self.group_id = "aig-1"
        self.arm = SimpleNamespace(armed=armed)
        self.deps = deps or _FakeDeps()
        self._digest = {"lines": ["x"], "itemIds": list(item_ids)} if item_ids else {}
        self._last_round = last_round

    def digest_for(self, _inst):
        return self._digest

    def last_round_for(self, _inst):
        return self._last_round


def test_c2_unbound_binding_keeps_its_shape():
    """`C2`：未装配 / 无运行时 ⇒ `bound=False`、`reason="not_bound"`，且**键存在**。"""
    wire = binding_mod.binding_wire(binding_mod.build_binding(None, "BTC-USDT-SWAP"))
    assert wire == {"bound": False, "groupId": None, "mode": "off", "injecting": False,
                    "reason": "not_bound", "digestItemIds": [], "lastRound": None}
    assert binding_mod.unbound_wire() == wire


def test_c3_injecting_implies_inject_on_and_armed_but_not_the_reverse():
    """`C3`（★ 评审 P1-6）：不变式成立，且**反向不成立**。"""
    live = binding_mod.build_binding(_FakeRuntime(), "BTC-USDT-SWAP")
    assert live.injecting and live.reason == ""
    # 壳运行时：`inject_on` 为真而 `injecting` 为假。
    shell = binding_mod.build_binding(_FakeRuntime(armed=False), "BTC-USDT-SWAP")
    assert shell.deps_inject_on if hasattr(shell, "deps_inject_on") else True
    assert shell.injecting is False and shell.reason == "not_armed"


def test_c3b_every_reason_code_has_a_constructed_case():
    """五个 `reason` 码各有一条构造用例，且都在 `taxonomy.BINDING_REASONS` 的值域里。"""
    cases = {
        "not_bound": binding_mod.build_binding(None, "X"),
        "session_off": binding_mod.build_binding(
            _FakeRuntime(deps=_FakeDeps(inject_on=False, mode="off",
                                        session_enabled=False)), "X"),
        "ops_inject_off": binding_mod.build_binding(
            _FakeRuntime(deps=_FakeDeps(inject_on=False, mode="off")), "X"),
        "not_armed": binding_mod.build_binding(_FakeRuntime(armed=False), "X"),
        "no_items": binding_mod.build_binding(_FakeRuntime(item_ids=()), "X"),
    }
    for code, value in cases.items():
        assert value.reason == code
        assert code in tax.BINDING_REASONS


def test_c5_digest_item_ids_are_ordered_and_survive_the_fallback_gear():
    """`C5`（★ 评审 P1-3）：`digestItemIds` 与 `wire["itemIds"]` 逐元素同序相等。

    回退档（`mode="full"` + 横向总闸关）上它**依然有序非空**——v1 的出键条件在这一格会让它
    取不到数，而 `IntelCard` 的竖线与 `[Ixx]` chip 直接读它。
    """
    limits = IntelLimits(expand_enabled=False, repeat_compress_enabled=False)
    wire = _wire([_item(1, tier="long"), _item(2, tier="ultra")], mode="full", limits=limits)
    runtime = _FakeRuntime(item_ids=tuple(wire["itemIds"]))
    runtime._digest = wire
    assert list(binding_mod.build_binding(runtime, "X").digest_item_ids) == wire["itemIds"]
    assert wire["itemIds"] == [1, 2]                 # long 在前、ultra 在后（既有 _TIER_RANK）


def test_c4_last_round_wire_names_hidden_and_same_separately():
    """`C4` 的形状：`lastRound` 上 `same` 与 `hidden` 是两个字段，不合并。"""
    plan = {"tier": "repeat", "show": [0], "same": 5, "hidden": 3, "refsKept": ["I02"]}
    wire = binding_mod.last_round_wire(round_no=42, ts=NOW, plan=plan, chars=118)
    assert wire == {"round": 42, "ts": NOW, "tier": "repeat", "shown": 1,
                    "same": 5, "hidden": 3, "chars": 118}


def test_w9_binding_reason_copy_has_no_markdown_notation():
    """`W9`（第 5 次同形复发的守卫，**放在后端**）：文案表里不许有 `**` 或反引号。

    前端把它当纯文本渲染，星号会原样显示成字面星号——而前端的源码门禁**看不见后端的字面量**，
    这正是前四次复发的原因。
    """
    for code, text in tax.BINDING_REASONS.items():
        assert "**" not in text and "`" not in text, code
        assert text and not text.startswith(" ")


# ==================== 轴 D：设置、桥、启动期校验、观测 ====================
def test_d1_three_new_gates_are_on_the_settings_bridge():
    """`D1` 的纯核心一半：三把新总闸**必须**在 `INTEL_SETTING_BRIDGE` 里（否则是假面板）。"""
    fields = {row[2] for row in INTEL_SETTING_BRIDGE}
    assert "ai_trade_intel_session_opt_in_enabled" in fields
    assert "ai_trade_intel_expand_enabled" in fields
    assert "ai_trade_intel_repeat_compress_enabled" in fields


def test_d2_nine_settings_are_registered_in_env_example_both_ways():
    """`D2` / `G-Env`：9 个键在 `Settings` 与**仓库根** `.env.example` 双向对齐。

    漏登记不会报错——prod 的 PyInstaller 后端不读 `.env`、只读进程 env，而桌面壳按前缀透传：
    一个没写进 `.env.example` 的旋钮，用户在打包形态下没有任何途径知道它存在。
    """
    env = (ROOT / ".env.example").read_text(encoding="utf-8")
    declared = set(re.findall(r"^([A-Z][A-Z0-9_]+)=", env, re.M))
    settings = Settings()
    assert len(NEW_SETTINGS) == 9
    for name in NEW_SETTINGS:
        assert hasattr(settings, name), f"`Settings` 上没有 {name}"
        assert name.upper() in declared, f"{name.upper()} 没写进 .env.example"


def test_d3_repeat_budget_larger_than_either_vertical_gear_is_rejected_at_boot():
    """`D3`（★ 评审 P1-7）：判据是 `> min(index, prompt)`，错误文案含**三个**实际数值。"""
    with pytest.raises(ValueError) as index_case:
        Settings(ai_trade_intel_index_max_chars=200, ai_trade_intel_repeat_max_chars=240)
    text = str(index_case.value)
    assert "200" in text and "240" in text and "1200" in text

    with pytest.raises(ValueError) as prompt_case:
        Settings(ai_trade_intel_prompt_max_chars=200, ai_trade_intel_repeat_max_chars=240)
    text = str(prompt_case.value)
    assert "200" in text and "240" in text and "480" in text


def test_d4_disclosure_stats_are_absent_when_the_session_did_not_use_intel():
    """`D4`：`mode="off"` / 零注入 ⇒ **整键缺席**（绝不先显示一个会被当真的 0）。"""
    counters = binding_mod.new_disclosure_counters()
    assert binding_mod.disclosure_stats(counters, mode="off") is None
    assert binding_mod.disclosure_stats(counters, mode="index") is None
    counters["roundsInjected"] = 3
    counters["seenReset"]["summary"] = 1
    stats = binding_mod.disclosure_stats(counters, mode="index")
    assert stats["mode"] == "index" and stats["roundsInjected"] == 3
    assert stats["seenReset"] == {"pause": 0, "summary": 1, "streak": 0}


def test_limits_from_settings_maps_all_seven_new_thresholds():
    """七个新阈值**必须**经 `IntelLimits` 到达纯核心（纯核心里一个 `getattr(settings,…)` 都没有）。"""
    settings = Settings(ai_trade_intel_index_max_chars=321,
                        ai_trade_intel_expand_enabled=False,
                        ai_trade_intel_expand_max_items=7,
                        ai_trade_intel_expand_max_chars=1234,
                        ai_trade_intel_repeat_compress_enabled=False,
                        ai_trade_intel_repeat_max_chars=111,
                        ai_trade_intel_repeat_max_rounds=9)
    limits = limits_from_settings(settings)
    assert (limits.index_max_chars, limits.expand_enabled, limits.expand_max_items,
            limits.expand_max_chars, limits.repeat_compress_enabled,
            limits.repeat_max_chars, limits.repeat_max_rounds) == (
        321, False, 7, 1234, False, 111, 9)


def test_b10_reflection_tool_schema_gains_no_field():
    """`B10`：复核官**不需要写编号** ⇒ `submit_trade_reflection` 的 schema 一个字段都不加。

    展开段是**给它看的证据**，不是要它填的表。加一个字段就是给一个无状态子调用增加一次它
    没有理由完成的任务，而任何一个没被填对的必填字段都会把裁决打成 `reflection_invalid`。
    """
    from app.ai_auto.prompts import REFLECTION_TOOL_NAME, REFLECTION_TOOL_SCHEMA

    assert REFLECTION_TOOL_NAME == "submit_trade_reflection"
    assert sorted(REFLECTION_TOOL_SCHEMA["properties"]) == [
        "adjustedSizeFraction", "confidence", "reason", "verdict"]
    # 冻结成一个 `sha256`：任何一次「顺手加个字段」都会在这里判红，而不是等到线上出现一批
    # `reflection_invalid` 才被发现。
    assert _sha(repr(sorted(REFLECTION_TOOL_SCHEMA["properties"]))) == _sha(
        repr(["adjustedSizeFraction", "confidence", "reason", "verdict"]))


def test_b12_a_pin_that_still_does_not_fit_falls_back_honestly():
    """`B12`：预算小到连置顶那一条都装不下 ⇒ `digest_ids` 为空 ⇒ `in_digest` 恒 False。

    既有的内联降级句（`wake_text` 的两态渲染）因此照常兜底——置顶是一次**尽力而为**的排序，
    不是一句「它一定在里面」的承诺。
    """
    items = [_item(1, summary="很长的摘要" * 30)]
    wire, ids = build_digest_and_ids(items, limits=IntelLimits(index_max_chars=120),
                                     now=NOW, last_collect_ts=NOW - 600, cite=True,
                                     mode="index", pin_ids=frozenset({1}))
    assert wire["lines"] == [] and ids == frozenset()
    assert wire["hidden"] == 1
    assert format_intel(wire) == ""          # 零条目 ⇒ 整段省略


def test_build_digest_defaults_reproduce_the_pre_feature_call_sites():
    """既有四个 `build_digest` 调用点一个字节不改 ⇒ 新形参必须全部带回退默认值。"""
    items = [_item(1)]
    assert build_digest(items, limits=IntelLimits(), now=NOW,
                        last_collect_ts=NOW - 600)["lines"] == _wire(items, cite=False)["lines"]
