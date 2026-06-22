"""Pydantic 请求 / 响应模型 (spec §9)。字段用 camelCase 与前端 TS 类型对齐。"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from .db.models import AgentProvider, EquityPoint, OkxAccount, StrategyRun, Trade
from .engine.metrics import _empty_metrics


# ---------------- Requests ----------------
class CreateStrategyReq(BaseModel):
    templateKey: str
    name: str
    params: dict[str, Any] = Field(default_factory=dict)


class UpdateStrategyReq(BaseModel):
    name: str | None = None
    params: dict[str, Any] | None = None
    enabled: bool | None = None


class BacktestReq(BaseModel):
    instId: str
    bar: str = "1H"
    limit: int = Field(default=500, ge=10, le=1000)   # 同步回测上限 (C12)
    startTs: int = Field(ge=0)
    endTs: int = Field(ge=0)
    initialCapital: float | None = None
    fees: float | None = None        # taker_fee_bps
    slippage: float | None = None    # slippage_bps
    leverage: float | None = None

    @model_validator(mode="after")
    def validate_time_range(self) -> "BacktestReq":
        if self.startTs >= self.endTs:
            raise ValueError("startTs must be less than endTs")
        return self


class CompareReq(BaseModel):
    # 2..12 个策略横向对比（去重后逐一回测，复用同一批 candles，R1）
    strategyIds: list[int] = Field(min_length=2, max_length=12)
    instId: str
    bar: str = "1H"
    limit: int = Field(default=500, ge=10, le=1000)   # 对齐 BacktestReq (C9)
    startTs: int = Field(ge=0)
    endTs: int = Field(ge=0)
    initialCapital: float | None = None
    fees: float | None = None        # taker_fee_bps
    slippage: float | None = None    # slippage_bps
    leverage: float | None = None

    @model_validator(mode="after")
    def validate_time_range(self) -> "CompareReq":
        if self.startTs >= self.endTs:
            raise ValueError("startTs must be less than endTs")
        return self


class PaperStartReq(BaseModel):
    strategyId: int
    instId: str
    bar: str = "1H"
    initialCapital: float | None = None
    # 数据来源：live=实时 WS；replay=真实历史加速回放（C2，"模拟快速交易"可演示）
    source: Literal["live", "replay"] = "live"
    speed: float = Field(default=1.0, gt=0, le=64)        # 回放倍速；gt=0 杜绝除零
    replayBars: int = Field(default=300, ge=10, le=600)   # 回放根数；≤_WINDOW_MAX=600
    # 预热回放（warm-start priming，B1）：live 启动时是否对近端真实历史做因果微回测建仓。
    priming: bool = True
    primingBars: int = Field(default=300, ge=10, le=600)  # 预热回看根数；会话内再夹取 [warmup+5,600]


class PaperBatchStartReq(BaseModel):
    strategyIds: list[int] = Field(min_length=1, max_length=12)
    instId: str
    bar: str = "1H"
    initialCapital: float | None = None
    source: Literal["live", "replay"] = "live"
    speed: float = Field(default=1.0, gt=0, le=64)
    replayBars: int = Field(default=300, ge=10, le=600)
    priming: bool = True
    primingBars: int = Field(default=300, ge=10, le=600)


class PaperBatchSessionOut(BaseModel):
    strategyId: int
    name: str
    sessionId: str
    runId: int
    state: str


class PaperBatchStartOut(BaseModel):
    groupId: str
    instId: str
    bar: str
    sessions: list[PaperBatchSessionOut]


class PaperBatchStopRunOut(BaseModel):
    strategyId: int
    sessionId: str
    run: dict[str, Any]


class PaperBatchStopOut(BaseModel):
    groupId: str
    runs: list[PaperBatchStopRunOut]


# ---------------- Response serializers ----------------
def trade_out(t: Trade) -> dict:
    return {
        "id": t.id, "runId": t.run_id, "ts": t.ts, "side": t.side, "price": t.price,
        "qty": t.qty, "fee": t.fee, "pnl": t.pnl, "positionAfter": t.position_after,
        "reason": t.reason,
    }


def equity_out(e: EquityPoint) -> dict:
    return {"ts": e.ts, "equity": e.equity, "drawdown": e.drawdown}


def run_out(run: StrategyRun) -> dict:
    import json
    metrics = _empty_metrics()
    if run.metrics_json:
        try:
            metrics = json.loads(run.metrics_json)
        except json.JSONDecodeError:
            pass
    return {
        "id": run.id, "strategyId": run.strategy_id, "instId": run.inst_id, "bar": run.bar,
        "mode": run.mode, "status": run.status, "startTs": run.start_ts, "endTs": run.end_ts,
        "metrics": metrics,
    }


class AgentConfigOut(BaseModel):
    """GET /agent/config 响应：是否已配置密钥 + 当前生效服务（绝不含 key，R3）。"""

    configured: bool
    provider: str
    model: str
    providerId: int | None = None      # 当前默认服务库内 id（env 回落 / 未配置为 None）
    label: str = ""                    # 当前默认服务展示名（追加字段，向后兼容）


# ---------------- AI 模型服务商（设置中心） ----------------
def _key_preview(api_key: str) -> str:
    """密钥脱敏预览：仅末 4 位，前缀点号；无 key 返回空串（绝不外泄明文，R-Sec）。"""
    key = (api_key or "").strip()
    return f"····{key[-4:]}" if key else ""


def provider_out(row: AgentProvider) -> dict:
    """AgentProvider 行 → 前端 dict（camelCase）。**绝不含 raw key**，仅 hasKey/keyPreview。"""
    has_key = bool((row.api_key or "").strip())
    return {
        "id": row.id, "slug": row.slug, "kind": row.kind, "label": row.label,
        "apiStandard": row.api_standard, "baseUrl": row.base_url, "model": row.model,
        "userAgent": row.user_agent, "hasKey": has_key, "keyPreview": _key_preview(row.api_key),
        "isDefault": bool(row.is_default), "deletable": row.kind == "custom",
        "createdTs": int(row.created_at.timestamp()) if row.created_at else 0,
        "updatedTs": int(row.updated_at.timestamp()) if row.updated_at else 0,
    }


class AgentProviderIn(BaseModel):
    """POST /agent/providers：新增自定义服务（kind 固定 custom）。

    写入字段补 max_length（SQLite 不强制 VARCHAR 长度，须在边界一次性校验，C5/H3）。
    """

    label: str = Field(min_length=1, max_length=128)
    apiStandard: Literal["anthropic", "openai"]
    baseUrl: str = Field(min_length=1, max_length=512)
    apiKey: str = Field(default="", max_length=512)
    model: str = Field(min_length=1, max_length=128)
    userAgent: str | None = Field(default=None, max_length=128)


class AgentProviderUpdate(BaseModel):
    """PUT /agent/providers/{id}：编辑（apiKey 缺/空 = 保留原 key；官方项锁 slug/kind/标准）。

    可空字段「缺省=不改」语义不变（None 不触发 min_length）；但「显式给出」时校验非空 +
    上限——堵住 PUT 把 label/model 置空串的隐患（H3）。
    """

    label: str | None = Field(default=None, min_length=1, max_length=128)
    apiStandard: Literal["anthropic", "openai"] | None = None
    baseUrl: str | None = Field(default=None, min_length=1, max_length=512)
    apiKey: str | None = Field(default=None, max_length=512)
    model: str | None = Field(default=None, min_length=1, max_length=128)
    userAgent: str | None = Field(default=None, max_length=128)


class ProviderTestReq(BaseModel):
    """POST /agent/providers/test：测草稿连通性（首存前，须带显式 apiKey）。"""

    apiStandard: Literal["anthropic", "openai"]
    baseUrl: str = Field(min_length=1, max_length=512)
    apiKey: str = Field(default="", max_length=512)
    model: str = Field(min_length=1, max_length=128)
    userAgent: str | None = Field(default=None, max_length=128)


class ProviderTestResult(BaseModel):
    """连通性测试结果：ok + 时延 / 错误码（code: AGENT_AUTH_ERROR|CONNECTION_FAILED|…）。"""

    ok: bool
    latencyMs: float | None = None
    model: str | None = None
    code: str | None = None
    message: str | None = None


class ProviderActiveOut(BaseModel):
    configured: bool
    providerId: int | None = None
    label: str = ""
    provider: str
    model: str


class ProvidersListOut(BaseModel):
    """GET /agent/providers：全量服务商（脱敏）+ 当前默认 id + 解析后的当前生效服务摘要。"""

    providers: list[dict]
    defaultId: int | None = None
    active: ProviderActiveOut


# ---------------- Agent 工具管理（设置中心 · Agent 工具管理） ----------------
class AgentToolOut(BaseModel):
    """单个工具的展示项（GET 列表 / PUT 回包）：元信息来自注册表 + 合并后的启用态。"""

    name: str
    description: str
    category: str
    isUiAction: bool
    enabled: bool
    paramsSchema: dict


class AgentToolsListOut(BaseModel):
    """GET /agent/tools、POST /agent/tools/bulk 响应：全量工具 + 启用计数概览。"""

    tools: list[dict]
    enabledCount: int
    totalCount: int


class AgentToolUpdate(BaseModel):
    """PUT /agent/tools/{name}：设置单工具启用态。"""

    enabled: bool


class AgentToolBulkUpdate(BaseModel):
    """POST /agent/tools/bulk：批量启用 / 禁用全部工具。"""

    enabled: bool


def tool_out(meta: dict, enabled: bool) -> dict:
    """注册表元信息 + 启用态 → 前端 dict（camelCase；工具是否存在以注册表为准，C6）。"""
    return {"name": meta["name"], "description": meta["description"],
            "category": meta["category"], "isUiAction": meta["isUiAction"],
            "enabled": enabled, "paramsSchema": meta["paramsSchema"]}


# ---------------- 实盘账户（设置中心 · 实盘 OKX 账户凭证） ----------------
def account_out(row: OkxAccount) -> dict:
    """OkxAccount 行 → 前端 dict（camelCase）。**绝不含 raw secret/passphrase / 完整 api_key**。

    掩码复用 `_key_preview`（P2-Naming：字段名取 `apiKeyPreview` 而非 `keyPreview`，多密钥实体下更清晰）；
    时间照 `provider_out` 输出秒级 UTC 整数（P2-Time，全系统时间口径一致）。
    """
    return {
        "id": row.id, "label": row.label,
        "apiKeyPreview": _key_preview(row.api_key),
        "hasSecret": bool((row.secret_key or "").strip()),
        "hasPassphrase": bool((row.passphrase or "").strip()),
        "ipWhitelist": row.ip_whitelist, "permissions": row.permissions,
        "isDefault": bool(row.is_default),
        "createdTs": int(row.created_at.timestamp()) if row.created_at else 0,
        "updatedTs": int(row.updated_at.timestamp()) if row.updated_at else 0,
    }


class OkxAccountIn(BaseModel):
    """POST /accounts：新增账户。label/apiKey/secretKey/passphrase 必填。

    写入字段补 max_length（SQLite 不强制 VARCHAR 长度，须在边界一次性校验，C5/H3）。
    """

    label: str = Field(min_length=1, max_length=128)
    apiKey: str = Field(min_length=1, max_length=128)
    secretKey: str = Field(min_length=1, max_length=128)
    passphrase: str = Field(min_length=1, max_length=128)
    ipWhitelist: str = Field(default="", max_length=512)
    permissions: str = Field(default="", max_length=128)


class OkxAccountUpdate(BaseModel):
    """PUT /accounts/{id}：编辑（None/空=不改；secretKey/passphrase 空=保留原值）。"""

    label: str | None = Field(default=None, min_length=1, max_length=128)
    apiKey: str | None = Field(default=None, min_length=1, max_length=128)
    secretKey: str | None = Field(default=None, max_length=128)
    passphrase: str | None = Field(default=None, max_length=128)
    ipWhitelist: str | None = Field(default=None, max_length=512)
    permissions: str | None = Field(default=None, max_length=128)


class OkxAccountTestReq(BaseModel):
    """POST /accounts/test：测草稿连通性（首存前，须带显式 apiKey/secretKey/passphrase）。"""

    apiKey: str = Field(min_length=1, max_length=128)
    secretKey: str = Field(min_length=1, max_length=128)
    passphrase: str = Field(min_length=1, max_length=128)
    ipWhitelist: str = Field(default="", max_length=512)


class OkxAccountTestResult(BaseModel):
    """连通性测试结果：ok + 时延 / 语义错误码（OKX_AUTH_*/OKX_IP_DENIED/CONNECTION_FAILED/…）。"""

    ok: bool
    latencyMs: float | None = None
    code: str | None = None
    message: str | None = None


class OkxAccountBalanceRow(BaseModel):
    ccy: str
    eq: float | None = None
    availBal: float | None = None
    frozenBal: float | None = None
    eqUsd: float | None = None


class OkxAccountPosition(BaseModel):
    instId: str
    instType: str
    posSide: str
    pos: float | None = None
    avgPx: float | None = None
    last: float | None = None
    upl: float | None = None
    uplRatio: float | None = None
    lever: float | None = None
    marginMode: str


class OkxAccountInfo(BaseModel):
    uid: str
    acctLevel: str
    acctLevelLabel: str
    permissions: list[str]


class OkxAccountBalance(BaseModel):
    totalEqUsd: float | None = None
    details: list[OkxAccountBalanceRow]


class OkxAccountDetailOut(BaseModel):
    """GET /accounts/{id}/detail：信息 + 资产 + 持仓（真实 OKX，鉴权失败→502，绝不假数据）。"""

    info: OkxAccountInfo
    balance: OkxAccountBalance
    positions: list[OkxAccountPosition]


class OkxAccountsListOut(BaseModel):
    """GET /accounts：全量账户（脱敏）+ 当前默认 id。"""

    accounts: list[dict]
    defaultId: int | None = None


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody
