"""情报可行动化新增 / 扩展端点的出入参模型（ai-trade-intel-actionability §4.1）。

**外移到独立文件**是为了不把 `schemas_intel_closed_loop.py` 推过 CLAUDE.md 的 800 行阈值，
与它自己当初从 `schemas.py` 外移是同一条处置；`api/ai_intel.py` 从这里 import，导入面单一。

一条贯穿本模块的纪律（历轮共同的教训）：**新 wire 键必须同时出现在响应模型里**。
FastAPI 的 `response_model` 会**静默丢弃**未声明的键——症状是「后端算了、前端永远是 0」，
而不是一条报错。

⚠️ **`GET /intel/{instId}/alerts` 刻意不挂 `response_model`**（评审 P2-9），故本模块里的
`IntelAlertOut` / `IntelAlertListOut` 只作为**文档与类型意图**存在，不参与序列化：告警行 wire
上有 `reasons` 这个**自由形状**的判据明细字典，挂模型只会得到「加了新判据、界面上看不到、
也不报错」。这与 `api/ai_intel.py` 模块 docstring 第一条纪律（读端点一律不挂 `response_model`）
一致——`/citations` 挂着模型是那一轮的例外，本轮不跟随它。
"""
from __future__ import annotations

from pydantic import BaseModel


class IntelAlertOut(BaseModel):
    """一行告警台账（**含「没告警」与「告警了但没能唤醒」**）。

    `suppressedText` / `outcomeText` 由后端下发，跟随 `degrade_wire` 的既有裁决——进 wire 的
    文案由后端管，只在轮存根直方图里出现的码才由前端 `CODE_LABEL` 管。
    """

    id: int
    instId: str
    groupId: str
    itemId: int
    roundId: int
    createdTs: int
    severity: float
    fired: bool
    suppressedCode: str
    suppressedText: str
    outcome: str
    outcomeText: str
    outcomeTs: int
    runId: int
    roundNo: int
    headline: str
    category: str
    tier: str
    stance: str
    impact: str
    domainCount: int
    quoted: bool
    confidence: float
    ref: str
    reasons: dict


class IntelAlertListOut(BaseModel):
    """`GET /intel/{instId}/alerts` 的形状（游标分页，游标是 `id` 不是时间戳）。"""

    alerts: list[IntelAlertOut]
    hasMore: bool


class IntelStatOut(BaseModel):
    """一个战绩 / 信誉桶。`accuracy` **可空**：`None` = 样本不足，**绝不是 0.0**。"""

    settled: int
    wins: int
    netPnlUsd: float
    confirmed: int
    refuted: int
    unresolved: int
    samples: int
    accuracy: float | None = None


class IntelEfficacyCategoryOut(IntelStatOut):
    """按类别的一行（多一个类别名与它当前生效的权重）。"""

    category: str
    weight: float


class IntelEfficacyOut(BaseModel):
    """`GET /intel/{instId}/impact` 上新增的 `efficacy` 块。

    `enabled=False` ⇒ 前端显示「战绩统计已关闭」而不是一排 0——一个假装「没有数据」的面板会被
    读成「情报没用」。
    """

    enabled: bool
    kMin: int
    sampleN: int
    overall: IntelStatOut
    byCategory: list[IntelEfficacyCategoryOut]


class IntelConflictOut(BaseModel):
    """`GET /intel/{instId}` 上新增的 `conflicts` 行。

    `openRounds` **不落库**：它由两端的 `last_curated_ts` 与整理轮流水推算（新增一列去记它会
    立刻带来「这一列谁写、谁清」的问题）。
    """

    aId: int
    bId: int
    overlap: float
    windowH: float
    category: str
    openRounds: int
