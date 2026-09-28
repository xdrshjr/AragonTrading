"""账户工具仅适配共享只读服务，不接受模型指定账户。"""
from __future__ import annotations

from pydantic import BaseModel

from ...agent_trading import schemas
from ...agent_trading.errors import TradingError
from .base import Tool, ToolContext, ToolResult


class AccountTool(Tool):
    """由同源 Pydantic 模式约束输入的只读工具。"""

    category = "account"

    def __init__(self, name: str, kind: str, description: str,
                 request_model: type[BaseModel]):
        self.name = name
        self.kind = kind
        self.description = description
        self.request_model = request_model
        self.input_schema = request_model.model_json_schema()

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        """读取冻结账户的白名单事实并提供可恢复卡片。"""
        from ...agent_trading.read_service import read_account
        if ctx.call is None:
            raise TradingError("ACCOUNT_REQUIRED")
        params = schemas.parse_request(self.request_model, args)
        snapshot = await read_account(
            self.kind, params.model_dump(exclude_none=True), ctx.call,
            session_factory=ctx.session_factory,
        )
        if self.kind == "order" and params.intentId:
            payload = {"type": "trade_intent", "intent": snapshot}
        elif self.kind == "context":
            payload = {"type": "auto_trade_context", "context": snapshot}
        else:
            payload = {"type": "account_snapshot", "snapshot": snapshot}
        summary = snapshot.get("summary") or f"{self.description}：查询完成"
        return ToolResult(True, summary, payload=payload,
                          model_payload=snapshot)


ACCOUNT_TOOLS = [
    AccountTool("list_trading_accounts", "accounts", "查询可用交易账户",
                schemas.EmptyRequest),
    AccountTool("get_account_balance", "balance", "查询账户余额",
                schemas.EmptyRequest),
    AccountTool("get_account_positions", "positions", "查询真实持仓",
                schemas.PositionQuery),
    AccountTool("get_open_orders", "orders", "查询普通及保护委托",
                schemas.PositionQuery),
    AccountTool("get_recent_fills", "fills", "查询近期成交",
                schemas.FillQuery),
    AccountTool("get_order_status", "order", "查询指定订单状态",
                schemas.OrderStatusQuery),
    AccountTool("get_ai_trade_context", "context", "查询自动交易只读证据",
                schemas.ContextQuery),
]
