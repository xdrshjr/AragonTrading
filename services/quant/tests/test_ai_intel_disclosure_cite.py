"""注入行的编号契约与零回归冻结（ai-trade-intel-closed-loop §7.1 · test_ai_intel_disclosure_cite）。

G-1 的字节冻结在这里：`cite=False` 时 `render_line` / `build_digest` / `format_intel` 的产物必须
与 52f466e **逐字节**相同。冻结用 sha256 而不是「不抛异常」——后者在任何一次文案改动上都恒绿。
"""
from __future__ import annotations

import hashlib

from app.ai_auto.intel.disclosure import build_digest, format_intel, render_line
from app.ai_auto.intel.gate import IntelItem
from app.ai_auto.intel.taxonomy import IntelLimits

NOW = 1_800_000_000
LIMITS = IntelLimits()


def _item(item_id: int, **over) -> IntelItem:
    base = dict(item_id=item_id, inst_id="BTC-USDT-SWAP", tier="mid", category="macro",
                headline=f"标题{item_id}", summary=f"摘要{item_id}" * 3, stance="neutral",
                impact="high", confidence=0.7, event_ts=NOW - 3600, captured_ts=NOW - 3600,
                expires_ts=NOW + 86400)
    base.update(over)
    return IntelItem(**base)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_cite_off_renders_byte_identical_to_v1():
    """G-1：`ref=""` / `cite=False` ⇒ 行体与 wire 键集都与合入前逐字节相同。"""
    item = _item(1)
    plain = render_line(item, now=NOW)

    assert _sha(render_line(item, now=NOW, ref="")) == _sha(plain)
    assert not plain.startswith("[")

    wire = build_digest([item], limits=LIMITS, now=NOW, last_collect_ts=NOW - 600)
    assert sorted(wire) == ["hidden", "lines"], "cite 关时 refs / citeEnabled 必须**整键缺席**"
    assert _sha(format_intel(wire)) == _sha(format_intel({"lines": [plain], "hidden": 0}))


def test_ref_prefix_is_exactly_one_half_width_space():
    """行体一个字节不改，前缀恒为 `f"[{ref}] "`——冻结断言因此只需在前缀上加。"""
    item = _item(1)
    assert render_line(item, now=NOW, ref="I07") == f"[I07] {render_line(item, now=NOW)}"


def test_ref_tokens_are_contiguous_and_match_wire():
    """G-2：编号从 01 连续递增、与 `wire["refs"]` 一一对应且无空洞。"""
    items = [_item(i) for i in range(1, 6)]
    wire = build_digest(items, limits=LIMITS, now=NOW, last_collect_ts=NOW - 600, cite=True)

    assert wire["citeEnabled"] is True
    assert list(wire["refs"]) == [f"I0{i}" for i in range(1, len(wire["lines"]) + 1)]
    for ref, line in zip(wire["refs"], wire["lines"]):
        assert line.startswith(f"[{ref}] ")
    assert set(wire["refs"].values()) <= {it.item_id for it in items}


def test_refuted_item_is_not_injected():
    """G-6 的注入侧：被证伪的条目**立即停止注入**，且过滤与 `status == "active"` 同一行判据。"""
    live = _item(1)
    refuted = _item(2, verdict="refuted")
    wire = build_digest([live, refuted], limits=LIMITS, now=NOW, last_collect_ts=NOW - 600,
                        cite=True)

    assert len(wire["lines"]) == 1
    assert set(wire["refs"].values()) == {1}
    assert wire["hidden"] == 0, "被证伪的条目不是「装不下」，不该记进 hidden"


def test_protected_refuted_item_is_still_not_injected():
    """G-8 的注入侧：置顶 / 手改保护的是「别被系统删掉」，不是「必须继续影响真实资金」。"""
    wire = build_digest([_item(1, verdict="refuted", pinned=True),
                         _item(2, verdict="refuted", user_edited=True)],
                        limits=LIMITS, now=NOW, last_collect_ts=NOW - 600, cite=True)

    assert wire["lines"] == []


def test_budget_accounts_for_ref_prefix():
    """`len(line)` 天然含前缀 ⇒ 预算自动把编号算进去；「装不下就停」这条纪律不变。"""
    item = _item(1)
    body = render_line(item, now=NOW)
    limits = IntelLimits(prompt_max_chars=len(body) + 2)   # 装得下裸行，装不下带前缀的行

    plain = build_digest([item], limits=limits, now=NOW, last_collect_ts=NOW - 600)
    cited = build_digest([item], limits=limits, now=NOW, last_collect_ts=NOW - 600, cite=True)

    assert len(plain["lines"]) == 1
    assert cited["lines"] == [] and cited["hidden"] == 1


def test_cite_note_appears_only_when_cite_enabled():
    """段末说明是**陈述句**（评审 P1-10）：它会原样进复核官上下文，一句祈使对它无法执行。"""
    item = _item(1)
    off = format_intel(build_digest([item], limits=LIMITS, now=NOW, last_collect_ts=NOW - 600))
    on = format_intel(build_digest([item], limits=LIMITS, now=NOW, last_collect_ts=NOW - 600,
                                   cite=True))

    assert "intelRefs" not in off
    assert "intelRefs" in on
    assert "不影响本轮任何风控判定" in on
    assert "请在" not in on and "请你" not in on


def test_never_collected_stays_empty_even_with_cite_on():
    """`last_collect_ts is None` ⇒ wire 恒为空 ⇒ prompt 逐字节回退（cite 开着也一样）。"""
    assert build_digest([_item(1)], limits=LIMITS, now=NOW, last_collect_ts=None,
                        cite=True) == {"lines": [], "hidden": 0}
