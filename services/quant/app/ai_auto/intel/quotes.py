"""逐字引文的归一化与比对（ai-trade-intel-evidence-grounding §3.2.2）。

**纯核心**：无 IO、无网络、无 DB、无时钟。本模块是本轮可信度承诺的物理来源——模型能编造文字，
**编不出一段后端手里没有的原文**。

归一化只做五件事（NFKC → 引号族 → 破折号族 → 空白折叠 → casefold），**刻意不做的三件事**每一条
都有理由，实现时不得「顺手加上」：

- **不去标点**：去了之后 `3.1%` 与 `31%` 会撞在一起，而数字正是本轮最要保护的东西。
- **不做繁简转换**：一段繁体原文与一段简体摘要不是同一段文本，硬判相同等于允许模型改写。
- **不做任何模糊匹配 / 编辑距离 / 词袋**：「逐字」是这道闸的**全部**价值。允许 1 个字的差异，
  就等于允许模型在关键数字上改 1 个字。

**归一化只有一个实现**（交付条件 C-6）：`normalize_for_match(t) ≡ normalize_with_map(t)[0]`。
`unicodedata.normalize` 在本文件里**只出现一次**，且是**逐字符**调用的——这与对整串调一次
NFKC **结果不同**（组合符序列在整串下会被合并），但 haystack 与 needle 走的是同一个函数，两侧
同源即自洽。把它「优化」成一句 `unicodedata.normalize("NFKC", s).casefold()` 的那一刻，两条路
就分叉了，症状是越南语 / 带音标的西语 / 部分韩文的引文**系统性比不中**，而拒绝码
`quote_not_found` 看上去像模型在乱抄。
"""
from __future__ import annotations

import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass

from . import taxonomy as tax
from .taxonomy import IntelLimits

# 引号族 / 破折号族 / 省略号的归一表（**只归形态，不去标点**）。
_TRANSLATE: dict[int, str] = {
    **{ord(ch): '"' for ch in '“”„‟″＂'},
    **{ord(ch): "'" for ch in '‘’‚‛′｀´'},
    **{ord(ch): "-" for ch in '‒–—―−'},
    ord("…"): "...",
}
# `str.isspace()` 认不出的「空白」。零宽空格与 BOM 在网页正文里比想象中常见。
_EXTRA_SPACE: frozenset[str] = frozenset("​‌‍﻿ 　")

# 比对可命中的三个字段，**先命中先返回**（正文优先：它才是本轮花钱抓回来的那个东西）。
_MATCH_FIELDS: tuple[str, ...] = ("body", "snippet", "title")


def normalize_with_map(text: str) -> tuple[str, tuple[int, ...]]:
    """归一串 + 索引映射。**唯一**的归一实现。

    Args:
        text: 任意原串。

    Returns:
        `(normalized, idx_map)`，其中 ``len(idx_map) == len(normalized) + 1``：
        ``idx_map[i]`` 是归一串第 i 个字符在**原串**中的起始下标，末位是哨兵（原串长度）。
        回切恒为 ``raw[idx_map[start]:idx_map[end]]``。

    Note:
        **末位哨兵不能省**。只有起点表时 ``raw[idx_map[start]:?]`` 的右端写不出来——用
        ``idx_map[end - 1] + 1`` 之类的算法会在「归一串一个字符对应原串多个字符」的位置切错，
        而空白折叠恰恰**天天**制造这种位置。
    """
    raw = text or ""
    chars: list[str] = []
    index: list[int] = []
    pending_space_at = -1
    for position, source_char in enumerate(raw):
        expanded = unicodedata.normalize("NFKC", source_char).translate(_TRANSLATE)
        for piece in expanded:
            if piece.isspace() or piece in _EXTRA_SPACE:
                if pending_space_at < 0:
                    pending_space_at = position
                continue
            if pending_space_at >= 0:
                if chars:                       # 首部空白直接丢弃（等价于 strip）
                    chars.append(" ")
                    index.append(pending_space_at)
                pending_space_at = -1
            for folded in piece.casefold():     # 'ß'.casefold() == 'ss'：1 → 2
                chars.append(folded)
                index.append(position)
    index.append(len(raw))                      # 尾部空白同样被丢弃（等价于 strip）
    return "".join(chars), tuple(index)


def normalize_for_match(text: str) -> str:
    """比对用归一。**必须**等价于 ``normalize_with_map(text)[0]``，不得另写一遍。"""
    return normalize_with_map(text)[0]


@dataclass(frozen=True)
class VerifiedQuote:
    """一段已通过逐字比对的原文引证。

    `text` 是**文档里的原文形态**（未归一），不是模型手里那一串——证据的价值在于它逐字来自
    原文，把模型给的那串存下来，用户在页面上看到的就不是网页里那段字了。
    """

    ref: str                  # "D3"
    text: str
    source_field: str         # body | snippet | title
    doc_url: str = ""
    doc_domain: str = ""
    doc_published_ts: int = 0
    char_start: int = 0       # 归一串偏移，**只为审计**，不参与任何判定
    has_imperative: bool = False   # §3.6 第 2 条：入库但打中性标记，**不拒收**


def _slice_back(raw: str, index: tuple[int, ...], start: int, end: int) -> str:
    """归一串区间 → 原串子串（回切）。"""
    return raw[index[start]:index[end]]


def verify_one(quote_text: str, doc, *, min_chars: int,
               span_max_chars: int) -> tuple[VerifiedQuote | None, str]:
    """单条引文比对。

    Args:
        quote_text: 模型给的那一段字。
        doc: 一篇 `CorpusDoc`（鸭子类型：只读 `ref` / `body` / `snippet` / `title` /
            `url` / `domain` / `published_ts`）。
        min_chars: 归一后的最短长度（短于此不足以作为证据）。
        span_max_chars: 回切后**原文跨度**的上限。超了**丢弃并计数**，绝不静默截断——
            一段被截断的「原文」已经不是原文了，而 SQLite 不强制 `VARCHAR(n)`，列宽声明
            拦不住任何东西。

    Returns:
        ``(命中, 拒绝码)``；命中时拒绝码为空串。
    """
    needle = normalize_for_match(quote_text)
    if len(needle) < max(1, int(min_chars)):
        return None, "quote_too_short"
    for field in _MATCH_FIELDS:
        raw = str(getattr(doc, field, "") or "")
        if not raw:
            continue
        hay, index = normalize_with_map(raw)
        position = hay.find(needle)
        if position < 0:
            continue
        original = _slice_back(raw, index, position, position + len(needle))
        if len(original) > max(1, int(span_max_chars)):
            return None, "quote_span_too_wide"
        return VerifiedQuote(
            ref=str(getattr(doc, "ref", "") or ""), text=original, source_field=field,
            doc_url=str(getattr(doc, "url", "") or ""),
            doc_domain=str(getattr(doc, "domain", "") or ""),
            doc_published_ts=int(getattr(doc, "published_ts", 0) or 0),
            char_start=position, has_imperative=tax.has_imperative(original)), ""
    return None, "quote_not_found"


def _bump(counter: dict[str, int], code: str) -> None:
    counter[code] = counter.get(code, 0) + 1


def _verify_batch(entries, allowed: Mapping[str, object], *, limits: IntelLimits
                  ) -> tuple[tuple[VerifiedQuote, ...], dict[str, int]]:
    """一批 `{ref, text}` 的逐条比对（采集轮与核查轮共用同一段判据）。"""
    verified: list[VerifiedQuote] = []
    rejects: dict[str, int] = {}
    for entry in list(entries or []):
        if not isinstance(entry, dict):
            _bump(rejects, "quote_bad_ref")
            continue
        ref = str(entry.get("ref") or "").strip().upper()
        doc = allowed.get(ref)
        if doc is None:
            _bump(rejects, "quote_bad_ref")
            continue
        if len(verified) >= max(1, int(limits.quote_max_per_item)):
            _bump(rejects, "quote_over_cap")
            continue
        quote, code = verify_one(str(entry.get("text") or ""), doc,
                                 min_chars=limits.quote_min_chars,
                                 span_max_chars=limits.quote_span_max_chars)
        if quote is None:
            _bump(rejects, code)
            continue
        verified.append(quote)
    return tuple(verified), rejects


def verify_item_quotes(proposal: dict, docs, *, limits: IntelLimits
                       ) -> tuple[tuple[VerifiedQuote, ...], dict[str, int]]:
    """采集轮：一条提案的全部引文。

    `ref` 的合法集合是**本条情报自己的 `sourceRefs`**（即 `docs`），不是「本轮语料」——
    引用一篇自己没声明为来源的文档，是把两件事拼在一起。

    **不因为任何一条引文失败而丢掉整条情报**：那是 `_admit_one` 在 `strict` 下的决定，
    不是本函数的。
    """
    allowed = {str(getattr(doc, "ref", "") or ""): doc for doc in (docs or [])}
    return _verify_batch(proposal.get("quotes"), allowed, limits=limits)


def verify_verdict_quotes(verdicts, index: Mapping[str, object], *, limits: IntelLimits
                          ) -> tuple[dict[str, tuple[VerifiedQuote, ...]], dict[str, int]]:
    """核查轮：按 **`C` 编号**（`itemRef`）索引的引文表。

    键**是 `C` 编号不是 `D` 编号**：两张表在 closed-loop 里就是分开的命名空间，串号不报错。
    `ref` 的合法集合是本轮语料全集（`corpus.index`）——核查员引用的是新检索到的材料，
    它当然不在被核查条目的 `sourceRefs` 里。

    Returns:
        ``({itemRef: 引文元组}, 拒绝码直方图)``。
    """
    out: dict[str, tuple[VerifiedQuote, ...]] = {}
    rejects: dict[str, int] = {}
    for entry in list(verdicts or []):
        if not isinstance(entry, dict):
            continue
        token = str(entry.get("itemRef") or "").strip().upper()
        if not token:
            continue
        quotes, codes = _verify_batch(entry.get("quotes"), index, limits=limits)
        for code, n in codes.items():
            rejects[code] = rejects.get(code, 0) + n
        if quotes:
            out[token] = out.get(token, ()) + quotes
    return out, rejects


def count_proposed(entries) -> int:
    """一批提案里模型**声称**给出的引文条数（命中率的分母）。"""
    total = 0
    for entry in list(entries or []):
        if not isinstance(entry, dict):
            continue
        quotes = entry.get("quotes")
        if isinstance(quotes, (list, tuple)):
            total += len(quotes)
    return total
