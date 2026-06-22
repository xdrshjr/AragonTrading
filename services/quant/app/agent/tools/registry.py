"""工具注册表：汇总全部工具，提供 schemas() 与 execute(name, args, ctx) (spec §2.4)。

异常在此统一收敛为 ToolResult(ok=False)：OKX 不可达 / 校验失败 / 未知异常都如实回报，
绝不假数据（C1），模型据此告知用户。
"""
from __future__ import annotations

import logging

from fastapi import HTTPException

from ...okx.rest_client import OKXError
from ..llm.base import ToolSchema
from .base import Tool, ToolContext, ToolResult
from .market_tools import GetCandlesTool, GetMarketSummaryTool, ListInstrumentsTool
from .regime_tools import LabelMarketRegimeTool
from .run_tools import (
    GetRunEquityTool,
    GetRunMetricsTool,
    GetRunTradesTool,
    ListRunsTool,
)
from .signal_tools import TestSignalForwardReturnsTool
from .strategy_tools import CompareStrategiesTool, ListStrategiesTool, RunBacktestTool
from .ui_tools import ShowChartTool

logger = logging.getLogger("aragon.agent.tools")

_ALL_TOOLS: list[Tool] = [
    ListInstrumentsTool(),
    GetMarketSummaryTool(),
    GetCandlesTool(),
    ShowChartTool(),
    ListStrategiesTool(),
    RunBacktestTool(),
    CompareStrategiesTool(),
    LabelMarketRegimeTool(),
    TestSignalForwardReturnsTool(),
    ListRunsTool(),
    GetRunMetricsTool(),
    GetRunTradesTool(),
    GetRunEquityTool(),
]


class ToolRegistry:
    """工具集合 + 统一调度。"""

    def __init__(self, tools: list[Tool] | None = None):
        self._tools: dict[str, Tool] = {t.name: t for t in (tools or _ALL_TOOLS)}

    def schemas(self) -> list[ToolSchema]:
        return [ToolSchema(name=t.name, description=t.description, input_schema=t.input_schema)
                for t in self._tools.values()]

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def is_ui_action(self, name: str) -> bool:
        tool = self._tools.get(name)
        return bool(tool and tool.is_ui_action)

    async def execute(self, name: str, args: dict, ctx: ToolContext) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(False, f"未知工具：{name}")
        args = args or {}
        # 模型产出的入参即外部输入：边界处一次性校验，非法即拒并回喂模型自纠（H2，不执行）。
        invalid = tool.validate(args)
        if invalid is not None:
            return ToolResult(False, f"参数无效：{invalid}")
        try:
            return await tool.execute(args, ctx)
        except OKXError as exc:
            return ToolResult(False, f"OKX 暂不可达，无法取数（{exc.message}）。请稍后重试。")
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
            return ToolResult(False, f"操作失败：{detail.get('message', detail)}")
        except Exception as exc:  # noqa: BLE001 —— 工具边界统一兜底，避免单工具异常拖垮整轮 run
            # 脱敏有界根因：仅类型名 + 截断消息，便于模型 / 用户纠正，杜绝栈与密钥（H7 / C7）。
            logger.exception("tool %s execute failed", name)
            return ToolResult(False, f"工具 {name} 执行出错：{type(exc).__name__}: {str(exc)[:200]}")
