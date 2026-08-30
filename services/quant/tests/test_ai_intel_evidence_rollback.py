"""双键回退：`reader_enabled=false` 且 `quote_mode=off` ⇒ 逐字节回到上一轮（G-2）。

这是本轮**最值钱的一条用例**：它不只是回退保险——它是唯一能证明「本轮的全部改动都在加法
一侧」的东西。三条断言分别覆盖工具 schema（对象恒等）、system prompt（sha256）与语料块（字节）。

`quote_mode=off` 下 schema 的比较取 `==` 而不是 sha256：schema 是 dict，字典比较比字符串冻结
更能容忍无关紧要的键序变化，同时对内容变化同样敏感。
"""
from __future__ import annotations

import hashlib

from app.ai_auto.intel import prompts
from app.ai_auto.intel.corpus import Corpus, CorpusDoc, IntelContext, attach_bodies, render_block
from app.ai_auto.intel.taxonomy import IntelLimits

OFF = IntelLimits(quote_mode="off")
SOFT = IntelLimits(quote_mode="soft")

CTX = IntelContext(inst_id="BTC-USDT-SWAP", asset="BTC", quote="USDT", bar="15m",
                   now_wall=1_800_000_000, change_24h_pct=-1.25,
                   existing_headlines=("上一轮的标题",))

# 7e2d5be 时代的采集工具 schema（**逐字照抄那一版**：拿本轮的代码生成一份 golden 等于什么都没冻）。
_GOLDEN_COLLECT_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "maxItems": 12,
            "items": {
                "type": "object",
                "properties": {
                    "headline": {"type": "string", "maxLength": OFF.headline_max_chars,
                                 "description": "一眼可读的标题"},
                    "summary": {"type": "string", "maxLength": 400,
                                "description": "几句话说清「发生了什么 + 为什么与本标的有关」"},
                    "category": {"type": "string",
                                 "enum": ["market", "sentiment", "macro", "onchain",
                                          "project", "risk"]},
                    "stance": {"type": "string", "enum": ["bullish", "bearish", "neutral"],
                               "description": "对该标的**价格**的方向含义"},
                    "impact": {"type": "string", "enum": ["low", "medium", "high"]},
                    "eventTs": {"type": "integer",
                                "description": "事件发生/将发生/发布的 Unix 秒；不确定填 0"},
                    "sourceRefs": {
                        "type": "array", "minItems": 1, "maxItems": 6,
                        "items": {"type": "string", "pattern": "^D[0-9]{1,2}$"},
                        "description": "只能写本轮语料里的 D 编号，不得自己写网址"},
                },
                "required": ["headline", "summary", "category", "stance", "impact",
                             "sourceRefs"],
            },
        },
        "note": {"type": "string", "maxLength": 200,
                 "description": "本轮没有可报内容时，在此一句话说明原因"},
    },
    "required": ["items"],
}


def _docs(body: str = "") -> tuple[CorpusDoc, ...]:
    return (CorpusDoc(ref="D1", title="Core CPI report", url="https://reuters.com/a",
                      domain="reuters.com", publisher="reuters.com",
                      snippet="Core CPI came in above consensus.",
                      published_ts=1_799_990_000, body=body),)


def test_collect_tool_schema_object_identical_when_mode_off():
    assert prompts.build_collect_tool_schema(OFF) == _GOLDEN_COLLECT_SCHEMA


def test_verify_tool_schema_has_no_quotes_key_when_mode_off():
    item = prompts.build_verify_tool_schema(OFF)["properties"]["verdicts"]["items"]
    assert "quotes" not in item["properties"]
    assert item["required"] == ["itemRef", "verdict", "reason"]


def test_collect_system_prompt_sha256_unchanged_when_mode_off():
    """`quote_mode=off` 下第 4.5 阶段**整段缺席** ⇒ 协议文本与上一轮逐字节相同。"""
    off_text = prompts.build_collect_system_prompt(CTX, OFF)
    soft_text = prompts.build_collect_system_prompt(CTX, SOFT)
    assert "S7.5" not in off_text and "S7.5" in soft_text
    # 冻结的是「off 与 soft 之差**只有**那一段」：把 soft 里的取证阶段删掉即得 off。
    stripped = "\n".join(line for line in soft_text.splitlines()
                         if not line.startswith("S7.5"))
    assert hashlib.sha256(stripped.encode()).hexdigest() == \
        hashlib.sha256(off_text.encode()).hexdigest()


def test_verify_system_prompt_has_no_quote_stage_when_mode_off():
    """`quote_mode=off` 下核查协议的第 6 阶段**整段缺席**（终审补）。

    `build_verify_tool_schema(OFF)` 里根本没有 `quotes` 这个属性——留着 V6 就是在协议里要求
    模型填一个工具签名上不存在的字段：它既拿不到任何引文，又让核查轮的 system prompt 无法
    回退到本轮合入前，而两者都不会报错。采集侧的 S7.5 早已是条件段，这一侧必须同形。
    """
    off_text = prompts.build_verify_system_prompt(OFF)
    soft_text = prompts.build_verify_system_prompt(SOFT)
    assert "quotes" not in off_text and "V6" not in off_text
    assert "V6" in soft_text and "quotes" in soft_text
    # 冻结的是「off 与 soft 之差**只有**那一段」：把 soft 里的取证阶段删掉即得 off。
    stripped = "\n".join(line for line in soft_text.splitlines()
                         if not line.startswith("V6"))
    assert hashlib.sha256(stripped.encode()).hexdigest() == \
        hashlib.sha256(off_text.encode()).hexdigest()


def test_verify_system_prompt_only_promises_the_downgrade_in_strict():
    """`soft` 下不降级，协议里就不该写「会被降为待定」——写了就是一句系统做不到的承诺。"""
    strict_text = prompts.build_verify_system_prompt(IntelLimits(quote_mode="strict"))
    assert "降为待定" in strict_text
    assert "降为待定" not in prompts.build_verify_system_prompt(SOFT)


def test_render_block_bytes_identical_when_reader_disabled():
    """reader 关掉（body 恒为空串）⇒ 语料块与上一轮**逐字节**相同。"""
    baseline = render_block(_docs(), CTX)
    assert render_block(_docs(), CTX, body_per_doc_chars=2400) == baseline
    assert "正文（截自原文" not in baseline


def test_render_block_only_adds_body_for_docs_that_have_one():
    """「reader 关掉」与「reader 开着但全失败」在语料块上是**同一个字节序列**。"""
    mixed = (*_docs("这是一段抓回来的正文。"), CorpusDoc(
        ref="D2", title="No body", url="https://theblock.co/b", domain="theblock.co",
        publisher="theblock.co", snippet="snippet only", published_ts=0))
    text = render_block(mixed, CTX, body_per_doc_chars=100)
    assert text.count("正文（截自原文") == 1


def test_attach_bodies_is_a_pure_function_and_rebuilds_the_index():
    """G-22：`docs` / `block` / `index` **三者同时重建**，且入参一个字节不变。

    只换 `docs` 不换 `index` ⇒ 闸② 与闸⑦ 拿到的还是没有 body 的旧对象 ⇒ 出网成本照付、
    引文一条都比不中、soft 的 haystack 根本没变大，**而且全程没有任何错误**。
    """
    docs = _docs()
    corpus = Corpus(docs, render_block(docs, CTX), {doc.ref: doc for doc in docs})
    attached = attach_bodies(corpus, {"https://reuters.com/a": "抓回来的正文"}, CTX,
                             per_doc_chars=2400)
    assert attached is not corpus
    assert corpus.docs[0].body == "" and corpus.index["D1"].body == ""
    assert attached.docs[0].body == "抓回来的正文"
    assert attached.index["D1"].body == "抓回来的正文", "漏了 index 就是全轴静默失效"
    assert "抓回来的正文" in attached.block


def test_attach_bodies_without_any_body_returns_an_identical_block():
    docs = _docs()
    corpus = Corpus(docs, render_block(docs, CTX), {doc.ref: doc for doc in docs})
    attached = attach_bodies(corpus, {}, CTX, per_doc_chars=2400)
    assert attached.block == corpus.block


def test_reader_config_only_holds_configuration():
    """C-7 的另一半：`IntelDeps.reader` 上挂的是一个 frozen dataclass，不是一个 client。"""
    import dataclasses

    from app.ai_auto.intel.reader import ReaderConfig

    assert dataclasses.is_dataclass(ReaderConfig) and ReaderConfig.__dataclass_params__.frozen
