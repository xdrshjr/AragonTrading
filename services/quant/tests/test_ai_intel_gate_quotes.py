"""第七道闸在 `admit` 里的行为（evidence-grounding §7.1 · test_ai_intel_gate_quotes）。

**纯核心、零 IO**。G-5 / G-9 / G-27 在这里。

G-9 是本轮最值钱的一条不变式：**给定同一批提案、同一份语料**，`soft` 的 accepted 集合
⊇ `off` 的 accepted 集合。它是**纯核心**的保证，不是线上准入率的保证——`soft` 同时改了工具
schema 与采集协议，模型的提案集本身会变。
"""
from __future__ import annotations

from dataclasses import replace

from app.ai_auto.intel import gate
from app.ai_auto.intel.corpus import Corpus, CorpusDoc
from app.ai_auto.intel.taxonomy import IntelLimits

NOW = 1_800_000_000
INST = "BTC-USDT-SWAP"

_BODY = ("The July core CPI print came in at 3.1% year over year, above the 3.0% consensus, "
         "and traders pushed back the expected timing of the first rate cut.")


def _limits(mode: str = "soft", **over) -> IntelLimits:
    return IntelLimits(quote_mode=mode, promote_requires_quote=(mode == "strict"), **over)


def _corpus(*, body: str = _BODY, docs: int = 1) -> Corpus:
    built = tuple(
        CorpusDoc(ref=f"D{i + 1}", title=f"Core CPI report {i + 1}",
                  url=f"https://reuters{i + 1}.com/a", domain=f"reuters{i + 1}.com",
                  publisher=f"reuters{i + 1}.com",
                  snippet="Core CPI came in at 3.1%, above consensus.",
                  published_ts=NOW - 3600,
                  body=body)
        for i in range(docs))
    return Corpus(built, "block", {doc.ref: doc for doc in built})


def _proposal(*, quotes=None, refs=("D1",), headline="美国核心 CPI 同比 3.1%",
              summary="核心 CPI 同比 3.1%，高于市场预期，降息路径预期后移。"):
    out = {"headline": headline, "summary": summary, "category": "macro",
           "stance": "bearish", "impact": "high", "eventTs": NOW - 3600,
           "sourceRefs": list(refs)}
    if quotes is not None:
        out["quotes"] = quotes
    return out


_GOOD_QUOTE = [{"ref": "D1", "text": "The July core CPI print came in at 3.1% year over year"}]


def test_soft_admits_item_with_verified_quote_and_marks_state():
    result = gate.admit([_proposal(quotes=_GOOD_QUOTE)], _corpus(), {},
                        inst_id=INST, limits=_limits("soft"), now=NOW)
    assert len(result.accepted) == 1
    item = result.accepted[0]
    assert item.quoted is True and item.quote_count == 1 and item.quote_state == ""
    assert result.quotes[item.dedup_key][0].text.startswith("The July core CPI print")


def test_soft_admits_item_without_quote_and_says_why():
    result = gate.admit([_proposal()], _corpus(), {}, inst_id=INST,
                        limits=_limits("soft"), now=NOW)
    item = result.accepted[0]
    assert item.quoted is False and item.quote_state == "modelOmitted"


def test_state_is_no_body_when_nothing_was_fetched():
    result = gate.admit([_proposal()], _corpus(body=""), {}, inst_id=INST,
                        limits=_limits("soft"), now=NOW)
    assert result.accepted[0].quote_state == "noBody"


def test_state_is_mode_off_when_the_axis_is_disabled():
    result = gate.admit([_proposal()], _corpus(), {}, inst_id=INST,
                        limits=_limits("off"), now=NOW)
    assert result.accepted[0].quote_state == "modeOff"


def test_state_is_verify_failed_when_the_model_made_the_quote_up():
    made_up = "核心 CPI 同比 9.9%，创下多年以来的历史新高，市场剧烈反应。"
    result = gate.admit([_proposal(quotes=[{"ref": "D1", "text": made_up}])],
                        _corpus(), {}, inst_id=INST, limits=_limits("soft"), now=NOW)
    item = result.accepted[0]
    assert item.quote_state == "verifyFailed"
    assert result.rejects.get("quote_not_found") == 1


def test_strict_rejects_item_without_verified_quote():
    result = gate.admit([_proposal()], _corpus(), {}, inst_id=INST,
                        limits=_limits("strict"), now=NOW)
    assert result.accepted == () and result.rejects.get("no_verified_quote") == 1


def test_soft_never_admits_fewer_items_than_off():
    """G-9：**同一批提案、同一份语料**，soft 的 accepted ⊇ off 的 accepted。

    断言的是**集合包含**而不是计数比较——计数相等也可能换了条目。
    """
    proposals = [
        _proposal(quotes=_GOOD_QUOTE),
        _proposal(headline="降息押注后移", summary="交易员下调今年的降息押注，风险资产走弱。",
                  quotes=[{"ref": "D1", "text": "traders pushed back the expected timing"}]),
        _proposal(headline="核心通胀高于共识", summary="核心通胀高于共识 0.1 个百分点。"),
    ]
    corpus = _corpus()
    off = gate.admit(proposals, corpus, {}, inst_id=INST, limits=_limits("off"), now=NOW)
    soft = gate.admit(proposals, corpus, {}, inst_id=INST, limits=_limits("soft"), now=NOW)
    off_keys = {item.dedup_key for item in off.accepted}
    soft_keys = {item.dedup_key for item in soft.accepted}
    assert off_keys <= soft_keys, "soft 只加不减：haystack 只增不减"


def test_strict_number_must_appear_inside_a_verified_quote():
    """`strict` 下闸② 的 haystack **就是**闸⑦ 的产物：正文里有、引文里没有的数字照样拒。"""
    quote = [{"ref": "D1", "text": "traders pushed back the expected timing of the first rate"}]
    result = gate.admit([_proposal(quotes=quote)], _corpus(), {}, inst_id=INST,
                        limits=_limits("strict"), now=NOW)
    assert result.accepted == () and result.rejects.get("unsourced_number") == 1


def test_quote_bonus_is_bounded_by_0_95():
    high = gate.solve_confidence(domain_count=3, has_published=True, impact="high",
                                 undated=False, all_same_domain=False, has_trusted=True,
                                 trust_bonus=0.3, has_quote=True, quote_bonus=0.3)
    assert high == 0.95


def test_quote_bonus_defaults_to_zero():
    """默认 `has_quote=False, quote_bonus=0.0` ⇒ 既有全部置信度断言逐字节不变。"""
    before = gate.solve_confidence(domain_count=2, has_published=True, impact="high",
                                   undated=False, all_same_domain=False)
    after = gate.solve_confidence(domain_count=2, has_published=True, impact="high",
                                  undated=False, all_same_domain=False, has_quote=False)
    assert before == after


def test_promote_requires_quote_only_in_strict():
    item = gate.IntelItem(
        item_id=1, inst_id=INST, tier="mid", category="macro", headline="h",
        summary="s", stance="neutral", impact="high", confidence=0.8,
        event_ts=NOW - 10 * 86400, captured_ts=NOW - 10 * 86400, expires_ts=0,
        domain_count=3, quoted=False)
    assert gate._review_promote(item, "long", limits=_limits("soft"), now=NOW) is None
    assert gate._review_promote(item, "long", limits=_limits("strict"),
                                now=NOW) == "need_quote"


def test_quotes_of_merged_items_land_on_the_prior_item():
    """一条被再次报道的老情报正是最该更新证据的：丢掉它等于「越被证实、证据越旧」。"""
    first = gate.admit([_proposal(quotes=_GOOD_QUOTE)], _corpus(), {}, inst_id=INST,
                       limits=_limits("soft"), now=NOW)
    prior = replace(first.accepted[0], item_id=77, quote_count=2, quoted=True)
    again = gate.admit([_proposal(quotes=_GOOD_QUOTE)], _corpus(), {prior.dedup_key: prior},
                       inst_id=INST, limits=_limits("soft"), now=NOW)
    assert again.accepted == () and len(again.merges) == 1
    merge = again.merges[0]
    assert merge.item_id == 77 and merge.prior_quote_count == 2 and merge.prior_quoted
    assert again.quotes[merge.dedup_key], "合并分支的引文照样有归宿"


def test_orphan_quotes_are_dropped_and_counted():
    """G-27：`over_cap` 与同轮同题的引文**没有宿主**，落库只会写出看不见的孤儿行。"""
    limits = _limits("soft", items_per_round=1)
    corpus = _corpus(docs=2)
    proposals = [
        _proposal(quotes=_GOOD_QUOTE),
        _proposal(quotes=_GOOD_QUOTE),                     # 同轮同题
        _proposal(headline="降息押注后移", summary="交易员下调今年的降息押注，风险资产走弱。",
                  refs=("D2",),
                  quotes=[{"ref": "D2", "text": "traders pushed back the expected timing"}]),
    ]
    result = gate.admit(proposals, corpus, {}, inst_id=INST, limits=limits, now=NOW)
    assert len(result.accepted) == 1
    assert result.rejects.get("quote_orphan_dropped", 0) >= 1
    assert set(result.quotes) == {result.accepted[0].dedup_key}, \
        "引文表里只该剩下真的有宿主的那一条"
