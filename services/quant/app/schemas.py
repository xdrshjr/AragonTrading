"""Pydantic 请求 / 响应模型 (spec §9)。字段用 camelCase 与前端 TS 类型对齐。"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from .db.models import EquityPoint, StrategyRun, Trade
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
    """GET /agent/config 响应：是否已配置密钥 + 当前 provider/model（绝不含 key，R3）。"""

    configured: bool
    provider: str
    model: str


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody
