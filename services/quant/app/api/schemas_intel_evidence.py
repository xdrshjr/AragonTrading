"""证据接地的出参模型（ai-trade-intel-evidence-grounding §4.1）。

与 `schemas_intel_closed_loop.py` 平级、同一条纪律：**只给结构确定的出参挂 `response_model`**。
带嵌套领域对象的读端点一律不挂——未在模型里声明的 wire 键会被 FastAPI **静默丢弃**，
本仓库为此栽过（backstop-repair 评审）。

`PUT /settings/intel` 的三个新键**必须在 pydantic 模型上收值域**，不能只在前端拦：设置端点是
公开的，而 `quoteMode` 的一个坏值会经 `settings.model_copy(update=…)` 绕过 `Literal`
（`model_copy` 不做校验）——第二道保险在 `intel/service.py::resolve_intel_settings` 的读回处。
"""
from __future__ import annotations

from pydantic import BaseModel


class IntelQuoteOut(BaseModel):
    """一段已通过逐字比对的原文引证。

    `text` 是**文档里的原文形态**，不是模型给的那一串——证据的价值在于它逐字来自原文。
    """

    id: int
    itemId: int
    instId: str
    roundId: int
    role: str            # admit|verify
    ref: str             # "D3"
    text: str
    sourceField: str     # body|snippet|title
    docUrl: str
    docDomain: str
    docPublishedTs: int
    # 原文里含劝告性措辞 ⇒ 前端加一枚中性标记。**入库不拒收**：拒收会把「原文有句劝告」
    # 变成「这条情报没有证据」，两件事差得太远。
    hasImperative: bool
    verifiedTs: int


class IntelDocStateOut(BaseModel):
    """一篇来源网页在正文缓存里的**当前**状态。

    缓存已被淘汰 ⇒ 该 URL **不出现**在 `docs` 里，前端渲染「原文快照已过期，下方引文仍为当时
    逐字留存」——那句话必须出现，否则用户会把「没有 doc 行」误读成「这条情报没有来源」。
    """

    url: str
    finalUrl: str
    domain: str
    title: str
    status: str
    httpStatus: int
    fetchedTs: int
    bodyChars: int
    truncated: bool


class IntelEvidenceOut(BaseModel):
    """`GET /intel/items/{itemId}/evidence` 的回执。无引文 ⇒ 200 + 空数组（**不是** 404）。"""

    itemId: int
    quoteMode: str
    quotes: list[IntelQuoteOut]
    docs: list[IntelDocStateOut]


class IntelDocumentHealthOut(BaseModel):
    """设置卡「原文缓存」那一行。`bodyBytes` 是**真实字节**（`length(CAST(body AS BLOB))`），
    不是前端拿字符数乘出来的——中文正文在 UTF-8 下是 3 字节/字。"""

    cachedDocs: int
    bodyBytes: int
    oldestFetchedTs: int
    lastPrunedTs: int
    readerEnabled: bool
