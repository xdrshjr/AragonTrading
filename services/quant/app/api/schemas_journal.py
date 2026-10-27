"""交易日志路由的出入参模型（ai-trade-audit-journal §3.1）。

与 `schemas_intel_*.py` 平级、同一条纪律：**只给结构确定的入参与出参挂模型**。带嵌套领域对象
的读端点（决策详情 / 事件 / 成交）一律**不声明 `response_model`**——未在模型里声明的 wire 键会
被 FastAPI **静默丢弃**，本仓库已为此栽过（backstop-repair 评审、`/health` 的加法键注释）。
"""
from __future__ import annotations

from pydantic import BaseModel, Field

# 导出格式的值域。`Literal` 写在这里而不是端点上：三处（预检入参 / 导出 query / 前端类型）
# 读同一份枚举，各写一遍必然漂移。
EXPORT_FORMATS = ("audit-csv", "audit-jsonl", "sft-jsonl")


class JournalScopeIn(BaseModel):
    """导出范围（与 `GET /decisions` 的过滤参数同构）。全部可选 = 全部历史。"""

    groupId: str | None = None
    instId: str | None = None
    bar: str | None = None
    sinceTs: int | None = None
    untilTs: int | None = None
    action: str | None = None
    trigger: str | None = None
    executedOnly: bool = False
    degradedOnly: bool = False
    gateVetoedOnly: bool = False
    q: str | None = Field(default=None, max_length=64)
    beforeId: int | None = None


class SftOptionsIn(BaseModel):
    """后训练导出的八条筛选纪律。默认值即「能直接拿去训练」的口径。"""

    excludeGateVetoed: bool = True
    excludeDegraded: bool = True
    onlySettled: bool = True
    includeHold: bool = False
    # `auto` 是默认值且**不能**换成恒定的 `exact`：纸面 / 无凭证会话在结构上没有交易所单号，
    # 恒定 `exact` 会让它导出 0 条而界面上说不出为什么（评审 RP1-6）。
    minLinkConfidence: str = "auto"
    # `full` = 按上下文链重建整段多轮对话，重建不出来即排除；`lastTurnOnly` = 只要最后一轮，
    # 样本强制带 `quality.contextTruncated=true`。**默认永远是 `full`**。
    contextMode: str = "full"


class ExportPreviewIn(BaseModel):
    """`POST /export/preview` 的入参。"""

    format: str = "sft-jsonl"
    scope: JournalScopeIn = Field(default_factory=JournalScopeIn)
    sft: SftOptionsIn = Field(default_factory=SftOptionsIn)


class PruneIn(BaseModel):
    """`POST /prune` 的入参。`dryRun` 默认 **True**——一个默认就删的删除端点是不可接受的。"""

    dryRun: bool = True


class PruneOut(BaseModel):
    """剪枝回执。`dryRun` 时 `deleted` 为 `null`，反之 `wouldDelete` 为 `null`。"""

    wouldDelete: dict | None = None
    deleted: dict | None = None
    protectedGroups: list[str] = Field(default_factory=list)
    prunedTs: int = 0
