"""置信度求解（ai-trade-market-intel §3.3.5 · test_ai_intel_confidence）。

**服务端算，不是模型自报**：让模型给自己的产出打分，得到的永远是 0.8。这条用例同时钉死
「工具 schema 里没有 confidence 字段」——一个悄悄回来的自评字段不会报错，只会让整套证据分级失真。
"""
from __future__ import annotations

from app.ai_auto.intel import gate, prompts
from app.ai_auto.intel.corpus import Corpus, CorpusDoc
from app.ai_auto.intel.taxonomy import IntelLimits

NOW = 1_800_000_000
LIMITS = IntelLimits()


def test_baseline_and_each_term():
    base = gate.solve_confidence(domain_count=0, has_published=False, impact="low",
                                 undated=False, all_same_domain=False)
    assert base == 0.30
    assert gate.solve_confidence(domain_count=1, has_published=False, impact="low",
                                 undated=False, all_same_domain=False) == 0.45
    # 独立域名最多加 3 个（+0.45）；第四个不再加分。
    assert gate.solve_confidence(domain_count=3, has_published=False, impact="low",
                                 undated=False, all_same_domain=False) == \
        gate.solve_confidence(domain_count=9, has_published=False, impact="low",
                              undated=False, all_same_domain=False)
    assert gate.solve_confidence(domain_count=0, has_published=True, impact="low",
                                 undated=False, all_same_domain=False) == 0.40
    assert gate.solve_confidence(domain_count=0, has_published=False, impact="high",
                                 undated=False, all_same_domain=False) == 0.40


def test_penalties_and_clamp():
    low = gate.solve_confidence(domain_count=0, has_published=False, impact="low",
                                undated=True, all_same_domain=True)
    assert low == 0.05, "夹紧下界：一条情报的置信度不该是 0（那等于说它不存在）"
    high = gate.solve_confidence(domain_count=9, has_published=True, impact="high",
                                 undated=False, all_same_domain=False)
    assert high == 0.95, "夹紧上界：服务端不发放 1.0 这种绝对断言"


def test_model_reported_confidence_is_ignored():
    """模型即便在 payload 里塞了 `confidence`，落库的也只能是服务端求解的那个数。"""
    doc = CorpusDoc(ref="D1", title="t", url="https://a.com/1", domain="a.com",
                    publisher="a.com", snippet="net redemption of 5 units",
                    published_ts=NOW - 600)
    corpus = Corpus((doc,), "block", {"D1": doc})
    result = gate.admit([{
        "headline": "标题", "summary": "净赎回 5 单位，规模有限。", "category": "macro",
        "stance": "bearish", "impact": "medium", "eventTs": NOW - 600,
        "sourceRefs": ["D1"], "confidence": 0.99,
    }], corpus, {}, inst_id="BTC-USDT-SWAP", limits=LIMITS, now=NOW)
    assert len(result.accepted) == 1
    assert result.accepted[0].confidence == gate.solve_confidence(
        domain_count=1, has_published=True, impact="medium", undated=False,
        all_same_domain=True)


def test_collect_tool_schema_has_no_confidence_field():
    """schema 里一旦出现 `confidence`，模型自评就会悄悄回到链路里且不报任何错。"""
    schema = prompts.build_collect_tool_schema(LIMITS)
    props = schema["properties"]["items"]["items"]["properties"]
    assert "confidence" not in props
    assert set(schema["properties"]["items"]["items"]["required"]) == {
        "headline", "summary", "category", "stance", "impact", "sourceRefs"}
