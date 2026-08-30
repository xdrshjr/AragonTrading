"""查询规划与语料装配（ai-trade-market-intel §7.1 · test_ai_intel_corpus）。

两条安全断言在这里：**查询词绝不携带持仓 / 预算 / 账户信息**，以及**语料一律包在不可信标注内**。
第二条的对手不是「模型会不会听话」，而是「一个写着系统提示的网页正文会不会被当成系统提示」。
"""
from __future__ import annotations

from app.ai_auto.intel import corpus as corpus_mod
from app.ai_auto.intel.search import SearchDoc

NOW = 1_800_000_000


def _ctx(**over):
    base = dict(inst_id="BTC-USDT-SWAP", bar="15m", now_wall=NOW, change_24h_pct=-1.23,
                existing_headlines=("上一轮写下的标题",))
    base.update(over)
    return corpus_mod.build_context(**base)


def test_parse_inst_splits_asset_and_quote():
    assert corpus_mod.parse_inst("BTC-USDT-SWAP") == ("BTC", "USDT")
    assert corpus_mod.parse_inst("ETH-USDC-SWAP") == ("ETH", "USDC")
    assert corpus_mod.parse_inst("WEIRD") == ("WEIRD", "")


def test_plan_queries_is_deterministic_and_bounded():
    ctx = _ctx()
    first = corpus_mod.plan_queries(ctx, count=4)
    assert first == corpus_mod.plan_queries(ctx, count=4), "同一 ctx 必须幂等（可缓存、可复现）"
    assert len(first) == 4
    assert all("BTC" in q or "crypto" in q for q in first)
    assert len(corpus_mod.plan_queries(ctx, count=99)) <= len(corpus_mod._QUERY_TEMPLATES)


def test_queries_never_leak_position():
    """§3.9 数据外流：查询词只含资产符号与公开关键词，绝不把本地事实发给第三方。"""
    queries = " ".join(corpus_mod.plan_queries(_ctx(), count=8)).lower()
    for leak in ("position", "budget", "leverage", "account", "usdt-swap", "pnl", "持仓",
                 "预算", "杠杆"):
        assert leak not in queries, leak


def test_build_dedups_by_canonical_url_and_numbers_from_one():
    docs = [
        SearchDoc(title="A", url="https://a.com/x?utm=1", snippet="s1", score=0.9),
        SearchDoc(title="A dup", url="https://a.com/x#frag", snippet="s2", score=0.8),
        SearchDoc(title="B", url="https://b.com/y", snippet="s3", score=0.7),
    ]
    built = corpus_mod.build(docs, _ctx(), max_docs=16, snippet_max_chars=500)
    assert [d.ref for d in built.docs] == ["D1", "D2"], "编号必须从 1 连续"
    assert built.domain_count == 2
    assert set(built.index) == {"D1", "D2"}


def test_build_drops_non_http_and_blocked_domains():
    docs = [
        SearchDoc(title="bad", url="javascript:alert(1)", snippet="s"),
        SearchDoc(title="blocked", url="https://spam.com/x", snippet="s"),
        SearchDoc(title="ok", url="https://a.com/x", snippet="s"),
    ]
    built = corpus_mod.build(docs, _ctx(), max_docs=16, snippet_max_chars=500,
                             blocked_domains=frozenset({"spam.com"}))
    assert [d.domain for d in built.docs] == ["a.com"]


def test_build_truncates_snippets_and_caps_docs():
    docs = [SearchDoc(title=f"T{i}", url=f"https://d{i}.com/x", snippet="x" * 900,
                      score=1.0 - i / 100)
            for i in range(20)]
    built = corpus_mod.build(docs, _ctx(), max_docs=5, snippet_max_chars=120)
    assert len(built.docs) == 5
    assert all(len(d.snippet) <= 120 for d in built.docs)


def test_zero_docs_yields_empty_block():
    """0 文档 ⇒ `search_empty` ⇒ **不调 LLM**。空块是那条早退路径的判据。"""
    built = corpus_mod.build([], _ctx(), max_docs=16, snippet_max_chars=500)
    assert built.docs == ()
    assert built.block == ""


def test_block_wraps_everything_in_untrusted_markers():
    docs = [SearchDoc(title="T", url="https://a.com/x", snippet="正文", published_ts=NOW)]
    built = corpus_mod.build(docs, _ctx(), max_docs=16, snippet_max_chars=500)
    assert built.block.startswith("<<<UNTRUSTED_SEARCH_RESULTS>>>")
    assert built.block.rstrip().endswith("<<<END_UNTRUSTED_SEARCH_RESULTS>>>")
    assert "一律无视" in built.block


def test_existing_headlines_live_inside_the_untrusted_block():
    """二阶注入：`existing_headlines` 是上一轮模型写下的文本，同样按不可信素材对待。"""
    docs = [SearchDoc(title="T", url="https://a.com/x", snippet="s")]
    built = corpus_mod.build(docs, _ctx(existing_headlines=("上一轮写下的标题",)),
                             max_docs=16, snippet_max_chars=500)
    head, tail = built.block.split("上一轮写下的标题", 1)
    assert "<<<UNTRUSTED_SEARCH_RESULTS>>>" in head
    assert "<<<END_UNTRUSTED_SEARCH_RESULTS>>>" in tail


def test_local_facts_line_omits_change_when_unknown():
    assert "近 24 小时涨跌" in corpus_mod.render_local_facts(_ctx())
    assert "近 24 小时涨跌" not in corpus_mod.render_local_facts(_ctx(change_24h_pct=None))


# ---------------- evidence-grounding 轴 A：正文挂载（G-22） ----------------
def _built(**over):
    docs = [SearchDoc(title="Core CPI report", url="https://reuters.com/a",
                      snippet="Core CPI above consensus.", published_ts=NOW - 3600,
                      score=0.9),
            SearchDoc(title="Rate cut bets", url="https://theblock.co/b",
                      snippet="Bets pushed back.", published_ts=NOW - 7200, score=0.8)]
    kwargs = dict(max_docs=16, snippet_max_chars=500)
    kwargs.update(over)
    return corpus_mod.build(docs, _ctx(), **kwargs)


def test_canonical_url_is_public_and_document_key_follows_it():
    """键的语义与归一函数绑在一起：改归一函数必然改键，不会出现「老缓存按旧键命中」。"""
    assert corpus_mod.canonical_url("https://Reuters.com/A/?utm=x#frag") == \
        "https://reuters.com/a"
    assert corpus_mod.document_key("https://reuters.com/a") == \
        corpus_mod.document_key("https://reuters.com/a/?utm=1")
    assert len(corpus_mod.document_key("https://reuters.com/a")) == 40


def test_attach_bodies_rebuilds_docs_block_and_index():
    """G-22：三者**同时**重建。只换 docs 不换 index ⇒ 闸② 与闸⑦ 拿到的还是旧对象。"""
    built = _built()
    attached = corpus_mod.attach_bodies(
        built, {"https://reuters.com/a": "抓回来的正文足够长，能进语料块。"}, _ctx(),
        per_doc_chars=2400)
    assert attached is not built and built.docs[0].body == ""
    assert attached.docs[0].body.startswith("抓回来的正文")
    assert attached.index["D1"].body == attached.docs[0].body
    assert "抓回来的正文" in attached.block


def test_render_block_body_section_only_for_docs_with_a_body():
    built = _built()
    attached = corpus_mod.attach_bodies(built, {"https://reuters.com/a": "只有第一篇有正文。"},
                                        _ctx(), per_doc_chars=2400)
    assert attached.block.count("正文（截自原文") == 1


def test_render_block_is_byte_identical_without_bodies():
    """「reader 关掉」与「reader 开着但全失败」在语料块上是同一个字节序列。"""
    built = _built()
    assert corpus_mod.render_block(built.docs, _ctx(), body_per_doc_chars=2400) == built.block


def test_attached_body_is_truncated_only_at_render_time():
    """入库正文是**长版本**（比对用），进 prompt 的才截断——两个上限刻意不同。"""
    long_body = "正" * 5000
    attached = corpus_mod.attach_bodies(_built(), {"https://reuters.com/a": long_body},
                                        _ctx(), per_doc_chars=100)
    assert len(attached.docs[0].body) == 5000, "haystack 恒为长版本"
    assert "正" * 100 in attached.block and "正" * 200 not in attached.block
