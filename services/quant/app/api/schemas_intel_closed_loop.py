"""情报闭环新增端点的出入参模型（ai-trade-intel-closed-loop §4.1）。

**外移到独立文件**是为了不把 `schemas.py` 推过 CLAUDE.md 的 800 行阈值（§十二 已登记）；
`api/ai_intel.py` 从这里 import，导入面单一。

一条贯穿本模块的纪律（v1 终审记过的教训）：**新 wire 键必须同时出现在响应模型里**。
FastAPI 的 `response_model` 会**静默丢弃**未声明的键——症状是「后端算了、前端永远是 0」，
而不是一条报错。故本模块的每一个 Out 模型都与它的装配点逐键同构。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .schemas_intel_actionability import IntelEfficacyOut


class IntelFeedbackIn(BaseModel):
    """写一条来源取舍（`POST /intel/feedback` 与 `POST /intel/feedback/preview` 共用）。

    `instId` 缺省 = **全局生效**；给了则只对该标的生效。破坏性操作的作用域必须由用户显式选择，
    而不是由实现替他猜。
    """

    scope: Literal["domain"] = "domain"
    key: str = Field(min_length=1, max_length=128)
    action: Literal["block", "trust"]
    instId: str = Field(default="", max_length=32)


class IntelFeedbackRowOut(BaseModel):
    id: int
    scope: str
    key: str
    action: str
    instId: str
    createdTs: int


class IntelFeedbackListOut(BaseModel):
    rows: list[IntelFeedbackRowOut]


class IntelFeedbackWriteOut(BaseModel):
    """`POST /intel/feedback` 的回执。`archived` 是**存量扫除**真的归档了几条（G-9②）。"""

    ok: bool
    archived: int = 0


class IntelFeedbackPreviewOut(BaseModel):
    """`POST /intel/feedback/preview` 的回执（**纯读**，绝不写库）。

    确认框必须显示受影响条数——一个说不清后果的破坏性操作按钮不该出现在这个产品里。
    """

    affected: int


class IntelProviderHealthOut(BaseModel):
    """`GET /intel/settings/health`：最近 N 轮的检索源成败聚合 + 熔断剩余秒。

    **不新建任何表、不新建任何计数器**：`ai_intel_rounds.search_provider` 已经逐轮记着实际用的
    slug，熔断剩余秒来自进程内 breaker。
    """

    provider: str
    rounds: int
    ok: int
    failed: int
    degraded: int
    breakerRemainingS: int


class IntelSourceRowOut(BaseModel):
    """一个来源域名的聚合行。

    `stateInstId` 是 `state` 那条取舍记录的作用域（``""`` = 全局）。**它必须下发**：撤销走
    `clear_intel_feedback(key, inst_id)` 的精确匹配，界面上猜一个作用域就会撤销不掉，而且不报错。
    """

    domain: str
    itemCount: int
    lastTs: int
    state: str          # "" | blocked | trusted
    stateInstId: str = ""
    # ---- ai-trade-intel-actionability 轴 B：信誉三列 + 准确率 ----
    # ⚠️ **不扩这四个字段 = 后端算了、界面上永远是空**：本模型挂在 `GET /intel/{instId}/sources`
    # 上，`response_model` 会静默丢弃未声明的键（E-19 / R-5）。
    # `accuracy` **可空**：`None` = 样本不足（低于 `efficacy_min_domain_samples`），**不是 0.0**
    # ——一个用两条样本算出来的 100%，比不显示更容易让人拉黑错的那个源。
    confirmed: int = 0
    refuted: int = 0
    unresolved: int = 0
    accuracy: float | None = None


class IntelSourceListOut(BaseModel):
    rows: list[IntelSourceRowOut]


class IntelImpactTotalsOut(BaseModel):
    """三张读数卡。`netPnlUsd` 是**关联**净额（共现统计，不是因果归因，R-1）。"""

    citeCount: int
    citeEntryCount: int
    settledCount: int
    winCount: int
    netPnlUsd: float


class IntelImpactOut(BaseModel):
    """`GET /intel/{instId}/impact`：影响力页签的全部读数。

    `top` / `worst` 是**条目 wire 原样**（`intel_item_out` 的产物），故这里不声明子模型——
    再抄一份键表就是给「改这里必须同时改那里」多加一处漂移点。
    """

    instId: str
    totals: IntelImpactTotalsOut
    top: list[dict]
    worst: list[dict]
    neverCited: int
    # ---- ai-trade-intel-actionability 轴 B：战绩与信誉块 ----
    # ⚠️ 同上，本模型挂在 `GET /intel/{instId}/impact` 上，不扩即**静默吞键**（E-19）。
    # 带默认值 ⇒ 未装配 / 总闸关时形状仍然一致（前端不会读到 `undefined`）。
    efficacy: IntelEfficacyOut | None = None


class IntelCitationRowOut(BaseModel):
    """一条引用明细。`netPnlUsd` **可空**：`None` = 结算不可得（`outcome="void"`）。"""

    id: int
    itemId: int
    instId: str
    runId: int
    roundNo: int
    citedTs: int
    refToken: str
    clOrdId: str
    role: str
    proposedAction: str
    finalAction: str
    traded: bool
    positionSeq: int
    settled: bool
    settledTs: int
    netPnlUsd: float | None
    holdS: int
    exitSource: str
    outcome: str
    createdTs: int


class IntelCitationListOut(BaseModel):
    rows: list[IntelCitationRowOut]
    hasMore: bool


class IntelQueuedOut(BaseModel):
    """手动触发核查轮的 202 回执（复用 `/collect` / `/curate` 的既有范式）。"""

    queued: bool
