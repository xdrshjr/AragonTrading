"""HTML → 正文的确定性抽取（evidence-grounding §7.1 · test_ai_intel_extract）。

**纯核心、零 IO**：本文件一个 DB 会话、一个网络请求都不需要。

HTML 不是可信输入，故这里钉的不只是「切得对不对」，还有「畸形输入下不崩、不指数爆炸、有界返回」。
"""
from __future__ import annotations

from app.ai_auto.intel.extract import extract_main_text

_LONG = "这是一段足够长的正文，用来越过最短块阈值，因为导航与版权行都比它短得多。" * 2


def test_drops_script_style_nav_footer():
    html = (f"<html><head><style>body{{color:red}}</style></head><body>"
            f"<nav>{_LONG}</nav><script>var x = '{_LONG}';</script>"
            f"<p>{_LONG}</p><footer>{_LONG}</footer></body></html>")
    result = extract_main_text(html, max_chars=5000)
    assert _LONG in result.text
    assert result.text.count(_LONG) == 1, "导航 / 脚本 / 页脚的内容一个字都不该进正文"


def test_keeps_sentence_across_inline_tags():
    """行内标签**不切句**：一句被 `<b>` 切成两半的话，逐字比对永远命中不了它的后半句。"""
    html = f"<body><p>核心 CPI 同比 <b>3.1%</b>，高于预期。{_LONG}</p></body>"
    result = extract_main_text(html, max_chars=5000)
    assert "核心 CPI 同比 3.1%，高于预期。" in result.text


def test_unbalanced_close_tag_does_not_swallow_the_document():
    """`max(0, depth - 1)` 那个坑：未配对的 `</script>` 绝不能把 skip 计数打到负数。

    打到负数之后，**整篇正文都会被当成 skip 区丢掉**，而返回值是一个合法的空串——
    一个会静默产出空正文的坑。
    """
    html = f"</script></div></nav><body><p>{_LONG}</p><script>bad()</script></body>"
    result = extract_main_text(html, max_chars=5000)
    assert _LONG in result.text and result.block_count >= 1


def test_picks_longest_contiguous_block_run():
    """评论区被短块（时间戳、作者名）切开 ⇒ 天然落在另一个区间里。"""
    body = "".join([f"<p>{_LONG}</p>", f"<p>{_LONG}</p>",
                    "<p>2 小时前</p>", "<p>匿名</p>", "<p>短评一句</p>"])
    result = extract_main_text(f"<body>{body}</body>", max_chars=5000)
    assert result.block_count == 2
    assert "短评一句" not in result.text


def test_truncates_on_block_boundary():
    """**在块边界截**，不在句子中间截：半句话作为「原文」既不能被引用也不该被展示。"""
    html = f"<body><p>{_LONG}</p><p>{_LONG}</p><p>{_LONG}</p></body>"
    result = extract_main_text(html, max_chars=len(_LONG) + 5)
    assert result.truncated is True
    assert result.text == _LONG, "只该留下完整的第一块"


def test_single_oversized_block_is_still_returned():
    """单块页面（整篇文章一个 `<div>`）不能产出空正文——那是一次静默失效。"""
    html = f"<body><div>{_LONG * 20}</div></body>"
    result = extract_main_text(html, max_chars=200)
    assert len(result.text) == 200 and result.truncated is True


def test_malformed_html_returns_empty_not_raise():
    for html in ("<p><p><p", "<<<>>>", "<plaintext>x" + "y" * 100, "", "<div" * 500):
        result = extract_main_text(html, max_chars=1000)
        assert isinstance(result.text, str)


def test_entities_are_decoded():
    html = f"<body><p>{_LONG}&nbsp;&amp;&nbsp;3.1&#37;</p></body>"
    result = extract_main_text(html, max_chars=5000)
    assert "& 3.1%" in result.text


def test_extracts_title():
    html = f"<html><head><title> 核心 CPI 同比 3.1% </title></head><body><p>{_LONG}</p></body>"
    result = extract_main_text(html, max_chars=5000)
    assert result.title == "核心 CPI 同比 3.1%"


def test_short_blocks_alone_yield_no_body():
    """全是导航与版权 ⇒ `block_count == 0` ⇒ 调用方记 `empty` 并回落 snippet。"""
    result = extract_main_text("<body><p>首页</p><p>关于我们</p><p>© 2026</p></body>",
                               max_chars=5000)
    assert result.block_count == 0 and result.text == ""
