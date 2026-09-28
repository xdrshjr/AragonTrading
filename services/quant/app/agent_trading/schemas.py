"""工具与 REST 共用的严格输入模式及冻结调用身份。"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Annotated, Literal, Union

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    model_validator,
)

from .errors import TradingError


def decimal_text(value):
    """仅接受有界、有限的正十进制字符串，规范化用于静态去重。"""
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError("decimal_string_required")
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("invalid_decimal") from exc
    if not number.is_finite() or number <= 0 or abs(number.adjusted()) > 18:
        raise ValueError("positive_finite_decimal_required")
    return (
        format(number, "f").rstrip("0").rstrip(".")
        if "." in format(number, "f")
        else format(number, "f")
    )


PositiveDecimal = Annotated[str, BeforeValidator(decimal_text)]
Identifier = Annotated[str, Field(min_length=1, max_length=128)]


@dataclass(frozen=True)
class AgentCallContext:
    """消息接收时冻结身份；单个工具只替换 tool_use_id。"""

    conversation_id: str
    client_message_id: str
    tool_use_id: str = ""
    account_id: int | None = None
    environment: str | None = None
    binding_version: int = 0
    account_mode: str | None = None
    credential_fingerprint: str | None = None


class EmptyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ContextQuery(EmptyRequest):
    instId: Identifier | None = None


class PositionQuery(ContextQuery):
    offset: int = Field(default=0, ge=0, le=100000)
    limit: int = Field(default=20, ge=1, le=50)


class FillQuery(ContextQuery):
    limit: int = Field(default=20, ge=1, le=100)


class OrderStatusQuery(EmptyRequest):
    intentId: Identifier | None = None
    instId: Identifier | None = None
    ordId: Identifier | None = None

    @model_validator(mode="after")
    def validate_identity(self):
        if self.intentId and not (self.instId or self.ordId):
            return self
        if not self.intentId and self.instId and self.ordId:
            return self
        raise ValueError("exclusive_order_identity_required")


class SizeRequest(EmptyRequest):
    unit: Literal["contracts", "notional_usdt"]
    value: PositiveDecimal


class PriceRequest(EmptyRequest):
    ordType: Literal["market", "limit"]
    px: PositiveDecimal | None = None

    @model_validator(mode="after")
    def validate_price(self):
        if (self.ordType == "limit") != (self.px is not None):
            raise ValueError("price_only_required_for_limit")
        return self


class OpenTradeRequest(PriceRequest):
    action: Literal["open"]
    instId: Identifier
    direction: Literal["long", "short"]
    tdMode: Literal["cross", "isolated"]
    size: SizeRequest
    tpTriggerPx: PositiveDecimal | None = None
    slTriggerPx: PositiveDecimal | None = None


class CloseSize(EmptyRequest):
    contracts: PositiveDecimal


class CloseTradeRequest(PriceRequest):
    action: Literal["close"]
    positionKey: Identifier
    closeSize: Literal["all"] | CloseSize


class CancelRequest(EmptyRequest):
    instId: Identifier
    ordId: Identifier


class BindingRequest(EmptyRequest):
    accountId: int | None = Field(default=None, gt=0)
    accountMode: Literal["sim", "live"]
    expectedVersion: int = Field(ge=0)


class ExecuteRequest(EmptyRequest):
    version: int = Field(ge=1)
    previewHash: str = Field(min_length=64, max_length=64)


class DismissRequest(EmptyRequest):
    version: int = Field(ge=1)


TradeRequest = Annotated[
    Union[OpenTradeRequest, CloseTradeRequest], Field(discriminator="action")
]


def parse_request(schema, params):
    """校验输入，拒绝时只输出受控错误，不回显敏感原文。"""
    try:
        return TypeAdapter(schema).validate_python(params)
    except (ValidationError, ValueError) as exc:
        raise TradingError("INVALID_TRADING_REQUEST") from exc


def parse_trade(params):
    """解析开仓/平仓判别联合。"""
    return parse_request(TradeRequest, params)
