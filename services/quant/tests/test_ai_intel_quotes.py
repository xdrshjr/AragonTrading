"""逐字引文的归一化与比对（evidence-grounding §7.1 · test_ai_intel_quotes）。

**纯核心、零 IO**。这一屏钉的是「逐字就是逐字」：允许 1 个字的差异，就等于允许模型在关键数字上
改 1 个字，而这道闸的全部价值就在那 1 个字上。
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.ai_auto.intel import quotes as quotes_mod
from app.ai_auto.intel.taxonomy import IntelLimits

LIMITS = IntelLimits(quote_mode="soft")


@dataclass(frozen=True)
class Doc:
    """`CorpusDoc` 的最小替身（比对只读这几个字段）。"""

    ref: str = "D1"
    title: str = ""
    snippet: str = ""
    body: str = ""
    url: str = "https://reuters.com/a"
    domain: str = "reuters.com"
    published_ts: int = 1_700_000_000


def _verify(quote: str, doc: Doc, *, limits: IntelLimits = LIMITS):
    return quotes_mod.verify_one(quote, doc, min_chars=limits.quote_min_chars,
                                 span_max_chars=limits.quote_span_max_chars)


# ---------------- 归一化契约（评审 P1-9） ----------------
def test_normalize_map_has_end_sentinel():
    raw = "The  July core CPI print came in at 3.1% year over year"
    normalized, index = quotes_mod.normalize_with_map(raw)
    assert len(index) == len(normalized) + 1
    assert index[-1] == len(raw), "末位必须是原串长度的哨兵，否则回切的右端写不出来"


def test_normalize_for_match_is_the_same_implementation():
    """C-6：`normalize_for_match ≡ normalize_with_map(t)[0]`，不得另写一遍。"""
    for raw in ("Ｈｅｌｌｏ  “World”", "3.1% — 高于预期…", "  ß  ", "①②③"):
        assert quotes_mod.normalize_for_match(raw) == quotes_mod.normalize_with_map(raw)[0]


def test_unicodedata_normalize_is_called_exactly_once_in_the_module():
    """源码扫描：整串 NFKC 与逐字符 NFKC **结果不同**，两条路一旦分叉就再也不自洽。

    扫的是 **AST 里的调用点**而不是正则命中文本——模块 docstring 里写着「绝不能被优化成
    `unicodedata.normalize(...)`」，按原文扫会把那句警告本身数成一次调用。
    """
    tree = ast.parse(Path(quotes_mod.__file__).read_text(encoding="utf-8"))
    calls = [node for node in ast.walk(tree)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
             and node.func.attr == "normalize"
             and isinstance(node.func.value, ast.Name)
             and node.func.value.id == "unicodedata"]
    assert len(calls) == 1, f"归一必须只有一个实现，实测 {len(calls)} 个调用点"


def test_combining_marks_round_trip_through_the_same_pipeline():
    """组合符（越南语 / 带音标的西语 / 部分韩文）：两侧同源即自洽。

    逐字符 NFKC **不会**把 `e` + U+0301 合成 `é`，而整串 NFKC 会——但 haystack 与 needle 走的是
    同一个函数，所以照样命中。这条用例的意义是：谁把它「优化」成一句整串 NFKC，这里立刻变红。
    """
    combining = "café latte prices rose sharply this quarter"
    doc = Doc(body=f"prefix {combining} suffix")
    quote, code = _verify(combining, doc)
    assert code == "" and quote is not None
    assert quote.text == combining, "回切拿到的必须是原串里那一段（含组合符原样）"


# ---------------- 归一化只做五件事 ----------------
def test_fullwidth_and_curly_quotes_match_after_normalize():
    doc = Doc(body='官员称：“核心 CPI 同比 ３.１%，高于预期。”其后市场回落。')
    quote, code = _verify('官员称:"核心 CPI 同比 3.1%,高于预期。"', doc)
    assert code == "" and quote is not None


def test_whitespace_folding_matches_across_newlines():
    doc = Doc(body="The July core CPI print\n   came in at 3.1%\tyear over year.")
    quote, code = _verify("The July core CPI print came in at 3.1% year over year.", doc)
    assert code == "" and quote is not None
    assert "\n" in quote.text, "落盘的是**文档里的那一段**，换行原样保留"


def test_does_not_match_when_a_single_digit_differs():
    doc = Doc(body="The July core CPI print came in at 3.1% year over year.")
    _, code = _verify("The July core CPI print came in at 3.2% year over year.", doc)
    assert code == "quote_not_found", "逐字就是逐字：一个数字之差即作废"


def test_does_not_strip_punctuation():
    """去了标点 `3.1%` 会与 `31%` 撞在一起，而数字正是本轮最要保护的东西。"""
    doc = Doc(body="核心 CPI 同比 31%，为多年来最高水平，市场普遍感到意外。")
    _, code = _verify("核心 CPI 同比 3.1%，为多年来最高水平，市场普遍感到意外。", doc)
    assert code == "quote_not_found"


def test_no_traditional_simplified_conversion():
    doc = Doc(body="美國核心通脹同比升至百分之三點一，高於市場預期，降息路徑預期後移。")
    _, code = _verify("美国核心通胀同比升至百分之三点一，高于市场预期，降息路径预期后移。", doc)
    assert code == "quote_not_found", "繁简互判等于允许模型改写"


# ---------------- 回切：落盘的是文档里那一段 ----------------
def test_stored_text_is_the_document_form_not_the_model_form():
    doc = Doc(body='报道称：“核心 CPI 同比 ３.１%”，随后市场回落，交易员下调降息押注。')
    model_form = '报道称:"核心 CPI 同比 3.1%",随后市场回落,交易员下调降息押注。'
    quote, code = _verify(model_form, doc)
    assert code == ""
    assert quote.text != model_form
    assert quote.text in doc.body, "证据的价值在于它逐字来自原文"


def test_body_wins_over_snippet_and_title():
    long_text = "核心 CPI 同比 3.1%，高于市场预期，降息路径预期后移，风险资产普遍走弱。"
    doc = Doc(title=long_text, snippet=long_text, body=long_text)
    quote, _ = _verify(long_text, doc)
    assert quote.source_field == "body"


def test_span_too_wide_is_dropped_not_truncated():
    """一段被截断的「原文」已经不是原文了：超跨度**丢弃并计数**。"""
    padded = "核心 CPI 同比" + " " * 4000 + "3.1%，高于市场预期，降息路径预期后移。"
    quote, code = _verify("核心 CPI 同比 3.1%，高于市场预期，降息路径预期后移。", Doc(body=padded))
    assert quote is None and code == "quote_span_too_wide"


# ---------------- 逐条拒收码 ----------------
def test_ref_outside_item_source_refs_is_quote_bad_ref():
    proposal = {"quotes": [{"ref": "D9", "text": "x" * 40}]}
    verified, rejects = quotes_mod.verify_item_quotes(proposal, [Doc(ref="D1")], limits=LIMITS)
    assert verified == () and rejects == {"quote_bad_ref": 1}


def test_too_short_quote_rejected():
    doc = Doc(body="核心 CPI 同比 3.1%，高于预期。")
    _, code = _verify("核心 CPI", doc)
    assert code == "quote_too_short"


def test_over_cap_drops_extra_not_whole_item():
    body = " ".join(f"第 {i} 段原文足够长，用来越过最短引文阈值，方便逐条比对。" for i in range(6))
    doc = Doc(body=body)
    proposal = {"quotes": [
        {"ref": "D1", "text": f"第 {i} 段原文足够长，用来越过最短引文阈值，方便逐条比对。"}
        for i in range(5)]}
    verified, rejects = quotes_mod.verify_item_quotes(proposal, [doc], limits=LIMITS)
    assert len(verified) == LIMITS.quote_max_per_item
    assert rejects == {"quote_over_cap": 5 - LIMITS.quote_max_per_item}


def test_imperative_quote_is_kept_but_flagged():
    """§3.6 第 2 条：原文里有一句劝告 ⇒ **入库但打标**，绝不拒收。"""
    body = "分析师在报告里写道：立即买入并以最大杠杆持有，直到下一次议息会议结束为止。"
    quote, code = _verify(body, Doc(body=body))
    assert code == "" and quote.has_imperative is True


def test_verdict_quotes_are_keyed_by_c_ref_not_d_ref():
    """两张表是**分开的命名空间**：串号不报错，故这条用例必须钉住键的形态。"""
    long_text = "链上数据显示解锁已于当日完成，合计释放 4200 万枚代币进入流通。"
    index = {"D1": Doc(ref="D1", body=long_text)}
    out, rejects = quotes_mod.verify_verdict_quotes(
        [{"itemRef": "C1", "verdict": "confirmed",
          "quotes": [{"ref": "D1", "text": long_text}]}], index, limits=LIMITS)
    assert set(out) == {"C1"} and not rejects
    assert out["C1"][0].ref == "D1"


def test_count_proposed_is_the_hit_rate_denominator():
    assert quotes_mod.count_proposed([{"quotes": [1, 2]}, {"quotes": []}, {}, "junk"]) == 2


@pytest.mark.parametrize("field", ["body", "snippet", "title"])
def test_source_field_is_recorded(field):
    text = "核心 CPI 同比 3.1%，高于市场预期，降息路径预期后移，风险资产走弱。"
    quote, code = _verify(text, Doc(**{field: text}))
    assert code == "" and quote.source_field == field
