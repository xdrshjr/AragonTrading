"""HTML → 正文的确定性抽取（ai-trade-intel-evidence-grounding §3.1.3）。

**纯核心**：无 IO、无网络、无 DB、无时钟、**零三方依赖**（只用标准库 `html.parser`）。

为什么是一条 50 行读得懂的确定性规则而不是一份抄来的 readability 启发式：本模块的产出会成为
「服务端裁决」的一部分——闸⑦ 拿它当 haystack 做逐字比对。抽错的代价是**引文比不中**（一个
可观测的数字，会出现在轮次页签上），不是悄悄产出一段假证据；而一个能被 20 条用例钉死的规则，
比一个谁也说不清它为什么这么切的启发式更适合站在这个位置。

三条实现约束，每一条都对应一个会静默产出空正文的坑：

1. **闭标签补偿必须写成 `max(0, depth - 1)`**。一篇带未配对 `</div>` 的页面（真实世界里非常
   常见）会把 `_skip_depth` 打到负数，此后每一个 `<script>` 的闭合都让它更负，**整篇正文被当成
   skip 区丢掉**——而返回值是一个合法的空串，没有任何地方会报错。
2. **块级边界只由块级标签产出**。`<b>` / `<span>` / `<a>` 一律不切——一句被切成两半的话，
   逐字比对永远命中不了它的后半句。
3. **截断在块边界**，不在句子中间。半句话作为「原文」既不能被引用，也不该被展示给用户。
"""
from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser

# 这些子树的**内容**一律不收：它们是导航、样式、脚本与表单，不是正文。
_SKIP_TAGS: frozenset[str] = frozenset({
    "script", "style", "noscript", "svg", "canvas", "template", "iframe",
    "form", "select", "nav", "header", "footer", "aside",
})

# 这些标签的开 / 闭都产出一个块边界（其余标签一个字都不切）。
_BLOCK_TAGS: frozenset[str] = frozenset({
    "p", "div", "li", "tr", "td", "th", "section", "article", "blockquote", "br",
    "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "table", "figcaption", "main", "pre",
})

# 短于此的块一律丢弃：导航、版权行、标签云、面包屑几乎全部落在这个区间。
DEFAULT_MIN_BLOCK_CHARS = 40


@dataclass(frozen=True)
class ExtractResult:
    """一次正文抽取的产物。`block_count <= 0` 即抽取失败（调用方记 `empty`，回落 snippet）。"""

    text: str = ""
    title: str = ""
    block_count: int = 0
    truncated: bool = False


class _TextCollector(HTMLParser):
    """把 HTML 收成「块」的序列。**容错优先**：畸形输入只影响切分质量，绝不抛异常。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[str] = []
        self.title: str = ""
        self._buf: list[str] = []
        self._skip_depth = 0
        self._in_title = False

    # ---- 块边界 ----
    def _flush(self) -> None:
        chunk = " ".join("".join(self._buf).split())
        self._buf.clear()
        if chunk:
            self.blocks.append(chunk)

    # ---- HTMLParser 回调 ----
    def handle_starttag(self, tag: str, attrs) -> None:      # noqa: D102 —— 见类 docstring
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if tag == "title":
            self._in_title = True
            return
        if tag in _BLOCK_TAGS:
            self._flush()

    def handle_endtag(self, tag: str) -> None:               # noqa: D102
        if tag in _SKIP_TAGS:
            # ★ 实现约束 1：未配对的闭标签绝不能把计数打到负数。
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if tag == "title":
            self._in_title = False
            return
        if tag in _BLOCK_TAGS:
            self._flush()

    def handle_data(self, data: str) -> None:                # noqa: D102
        if self._in_title:
            self.title += data
            return
        if self._skip_depth > 0:
            return
        self._buf.append(data)

    def close(self) -> None:                                 # noqa: D102
        super().close()
        self._flush()

    def collect(self, html: str) -> None:
        """喂完整篇 HTML。**绝不抛**：畸形输入只意味着这一篇少收几个块。"""
        try:
            self.feed(html or "")
            self.close()
        except Exception:      # noqa: BLE001 —— HTML 不是可信输入（见模块 docstring）
            self._flush()      # 取回已经收到的那一部分，而不是整篇丢掉

    def error(self, message: str) -> None:                   # pragma: no cover —— py<3.10 兼容面
        """`HTMLParser` 在 3.5+ 已不再调用它；保留一个不抛的实现，杜绝畸形输入路径上的异常。"""


def _longest_run(blocks: list[str], *, min_block_chars: int) -> list[str]:
    """丢掉过短的块，再取剩余块在**原序号上最长的连续区间**（按总字符数比长短）。

    不打分、不做 DOM 密度分析：新闻正文在丢掉导航与版权之后，几乎必然是一段连续的长块序列，
    而评论区 / 相关阅读被短块（时间戳、作者名、标签）切开，天然落在另一个区间里。
    """
    best: list[str] = []
    best_len = 0
    current: list[str] = []
    current_len = 0
    for block in blocks:
        if len(block) < max(1, int(min_block_chars)):
            if current_len > best_len:
                best, best_len = current, current_len
            current, current_len = [], 0
            continue
        current.append(block)
        current_len += len(block)
    if current_len > best_len:
        best, best_len = current, current_len
    return best


def _join_bounded(blocks: list[str], *, max_chars: int) -> tuple[str, bool]:
    """按 `max_chars` **在块边界**拼接。

    唯一的例外是「第一个块自己就超长」：单块页面（很多新闻站把整篇文章放在一个 `<div>` 里）
    此时会产出空正文——那是一个静默失效。故该情形下**只截第一个块**并标记 truncated。
    """
    cap = max(1, int(max_chars))
    picked: list[str] = []
    total = 0
    for block in blocks:
        extra = len(block) + (1 if picked else 0)
        if total + extra > cap:
            if not picked:
                return block[:cap], True
            return "\n".join(picked), True
        picked.append(block)
        total += extra
    return "\n".join(picked), False


def extract_main_text(html: str, *, max_chars: int,
                      min_block_chars: int = DEFAULT_MIN_BLOCK_CHARS) -> ExtractResult:
    """从一篇 HTML 里抽出正文与 `<title>`。

    Args:
        html: 原始 HTML（**不可信输入**：畸形、未闭合、超深嵌套、`<plaintext>`、10MB 单行
            都必须不崩、不指数爆炸、有界返回）。
        max_chars: 正文上限（`reader_body_max_chars`）。**在块边界截**。
        min_block_chars: 短于此的块视为导航 / 版权 / 标签云，直接丢弃。

    Returns:
        `ExtractResult`。抽不出任何块时 `block_count == 0` 且 `text == ""`——调用方据此记
        `empty` 并让该文档**退回 snippet 形态**，而不是让整轮失败。
    """
    parser = _TextCollector()
    parser.collect(html)
    picked = _longest_run(parser.blocks, min_block_chars=min_block_chars)
    text, truncated = _join_bounded(picked, max_chars=max_chars)
    title = " ".join((parser.title or "").split())[:300]
    return ExtractResult(text=text, title=title,
                         block_count=len(picked) if text else 0, truncated=truncated)
