"""模型只创建交易预览；执行权只存在于独立 REST 入口。"""
from __future__ import annotations

from pydantic import TypeAdapter

from ...agent_trading import schemas
from ...agent_trading.errors import TradingError
from .base import Tool, ToolContext, ToolResult


class PrepareTradeTool(Tool):
    """将明确开平仓指令交给确定性预览服务。"""

    name = "prepare_trade"
    description = "生成开仓或平仓预览；不会发单，需用户在卡片提交"
    category = "trading"
    input_schema = TypeAdapter(schemas.TradeRequest).json_schema()

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        """返回持久意图快照，不把模型文字当授权。"""
        from ...agent_trading.intent_service import prepare_trade
        if ctx.call is None:
            raise TradingError("ACCOUNT_REQUIRED")
        intent = await prepare_trade(args, ctx.call,
                                     session_factory=ctx.session_factory)
        return _preview_result(intent)


class PrepareCancelOrderTool(Tool):
    """普通订单撤销预览；不支持算法委托撤销。"""

    name = "prepare_cancel_order"
    description = "生成普通委托撤销预览；不会撤单，需用户在卡片提交"
    category = "trading"
    input_schema = schemas.CancelRequest.model_json_schema()

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        """按绑定账户核实目标订单，再创建撤销预览。"""
        from ...agent_trading.intent_service import prepare_cancel_order
        if ctx.call is None:
            raise TradingError("ACCOUNT_REQUIRED")
        intent = await prepare_cancel_order(
            args, ctx.call, session_factory=ctx.session_factory,
        )
        return _preview_result(intent)


def _preview_result(intent: dict) -> ToolResult:
    return ToolResult(
        True, "交易预览已生成，请在卡片核对具体账户与订单后提交。",
        payload={"type": "trade_intent", "intent": intent},
        model_payload=intent,
    )
