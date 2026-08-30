"""引用编号的服务端裁决与结果码判定（ai-trade-intel-closed-loop §7.1 · test_ai_intel_citation）。

轴 A / 轴 B 的纯核心：全部用例零 IO、零网络、零 DB、零时钟。
"""
from __future__ import annotations

from app.ai_auto.intel.citation import (
    REF_RE,
    classify_outcome,
    format_ref,
    is_settleable,
    resolve_citations,
)

REFMAP = {"I01": 11, "I02": 22, "I03": 33}


def test_unknown_ref_is_dropped_and_counted():
    """G-3：编号表里查不到的 token 被丢弃并单独计数，合法的那些照常裁决。"""
    out = resolve_citations(["I01", "I99", "I03"], REFMAP)

    assert out.valid == (("I01", 11), ("I03", 33))
    assert out.unknown == ("I99",)
    assert out.no_refmap == ()
    assert out.item_ids == (11, 33)


def test_missing_refmap_makes_every_ref_unknown():
    """§3.1.3 的刻意选择：这一轮压根没有编号表 ⇒ 全部落 `no_refmap`，**而不是全部落 valid**。

    「模型引用了编号，但这一轮根本没有编号表」是一个应当可观测的异常，不是一个可以静默当成空
    的情形。
    """
    for refmap in (None, {}):
        out = resolve_citations(["I01", "I02"], refmap)
        assert out.valid == ()
        assert out.unknown == ()
        assert out.no_refmap == ("I01", "I02")


def test_no_refmap_is_not_counted_as_fabricated():
    """评审 P2-2：两个计数出口必须分开——把系统故障读成模型不诚实比没有读数更坏。"""
    fabricated = resolve_citations(["I99"], REFMAP)
    degraded = resolve_citations(["I01"], None)

    assert fabricated.unknown and not fabricated.no_refmap
    assert degraded.no_refmap and not degraded.unknown


def test_duplicate_refs_collapse_and_order_is_preserved():
    out = resolve_citations(["I03", "I01", "I03"], REFMAP)

    assert out.valid == (("I03", 33), ("I01", 11))


def test_classify_outcome_none_is_void_not_flat():
    """§3.2.2：`None` ⇒ `void`，**绝不写成 0.0**——把一次失败伪装成一次平局比缺一个数更坏。"""
    assert classify_outcome(None) == "void"
    assert classify_outcome(0.0) == "flat"
    assert classify_outcome(1e-12) == "flat"
    assert classify_outcome(3.2) == "win"
    assert classify_outcome(-3.2) == "loss"


def test_classify_outcome_survives_garbage():
    assert classify_outcome("nope") == "void"     # type: ignore[arg-type]


def test_ref_regex_rejects_lowercase_and_three_digits():
    """R-11：三套编号空间互斥且各自锚定 `^…$`——串号不会报错，只会安静地记到别的对象上。"""
    assert REF_RE.match("I01")
    for bad in ("i01", "I1", "I001", "D01", "C1", " I01", "I01 "):
        assert not REF_RE.match(bad), bad


def test_lowercase_token_is_normalised_before_lookup():
    """解析器已 upper() 过，但裁决层自己也归一：两处都做的成本是一行，漏做是一次静默的丢弃。"""
    assert resolve_citations(["i01"], REFMAP).valid == (("I01", 11),)


def test_format_ref_is_two_digit_and_one_based():
    assert format_ref(1) == "I01"
    assert format_ref(20) == "I20"


def test_only_entry_and_exit_are_settleable():
    """§3.2.2：`hold` 行永远不结算——「情报影响了这次继续持有」不可证伪。"""
    assert is_settleable("entry")
    assert is_settleable("exit")
    assert not is_settleable("hold")
    assert not is_settleable("")
