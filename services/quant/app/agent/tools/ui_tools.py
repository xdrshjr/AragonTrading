"""UI-action 工具：show_chart（仅产出 directive，前端应用到 store）(spec §2.4)。

不触碰后端数据，只把模型意图转成 `{action:"show_chart", ...}`，由 loop emit `ui_action`
事件，前端按 `lib/agentActions` 映射为 store setter，真正驱动左图。
"""
from __future__ import annotations

from .base import Tool, ToolContext, ToolResult, check_enum, check_optional_range, first_error

_BAR_ENUM = ["1m", "5m", "15m", "30m", "1H", "4H", "1D"]


class ShowChartTool(Tool):
    name = "show_chart"
    category = "ui"
    description = ("把左侧主图切换到指定标的/周期/时间区间；带 runId 时叠加该次回测的买卖点与净值。"
                  "未指定的字段沿用当前工作台上下文。")
    is_ui_action = True
    input_schema = {
        "type": "object",
        "properties": {
            "instId": {"type": "string", "description": "如 BTC-USDT-SWAP（可选，默认当前标的）"},
            "bar": {"type": "string", "enum": _BAR_ENUM, "description": "周期（可选）"},
            "startTs": {"type": "integer", "description": "秒级 UTC 起（可选）"},
            "endTs": {"type": "integer", "description": "秒级 UTC 止（可选）"},
            "runId": {"type": "integer", "description": "叠加该次 run 的买卖点（可选）"},
        },
    }

    def validate(self, args: dict) -> str | None:
        # 全字段可选：给则校验 bar 枚举与 ts 顺序，未指定沿用当前工作台上下文。
        return first_error(
            check_enum(args, "bar", _BAR_ENUM),
            check_optional_range(args),
        )

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        directive: dict = {"action": "show_chart"}
        for key in ("instId", "bar", "startTs", "endTs", "runId"):
            if args.get(key) is not None:
                directive[key] = args[key]
        inst = directive.get("instId") or (ctx.ui.instId if ctx.ui else None) or "当前标的"
        bar = directive.get("bar") or (ctx.ui.bar if ctx.ui else None) or "当前周期"
        tail = f"，叠加 run {directive['runId']} 买卖点" if "runId" in directive else ""
        summary = f"已切换主图到 {inst} {bar}{tail}。"
        return ToolResult(True, summary, payload=directive)
