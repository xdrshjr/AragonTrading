"""工具注册表：汇总全部工具，提供 schemas() 与 execute(name, args, ctx) (spec §2.4)。

异常在此统一收敛为 ToolResult(ok=False)：OKX 不可达 / 校验失败 / 未知异常都如实回报，
绝不假数据（C1），模型据此告知用户。
"""
from __future__ import annotations

import logging
import uuid

from fastapi import HTTPException
from ...agent_trading.errors import TradingError
from ...config import get_settings

from ...okx.rest_client import OKXError
from ..llm.base import ToolSchema
from .base import Tool, ToolContext, ToolResult
from .account_tools import ACCOUNT_TOOLS
from .trade_tools import PrepareTradeTool, PrepareCancelOrderTool
from .market_tools import GetCandlesTool, GetMarketSummaryTool, ListInstrumentsTool
from .regime_tools import LabelMarketRegimeTool
from .run_tools import (
    GetRunEquityTool,
    GetRunMetricsTool,
    GetRunTradesTool,
    ListRunsTool,
)
from .factor_tools import (
    ComputeFactorTool,
    CreateFactorStrategyTool,
    EvaluateFactorTool,
    ListFactorsTool,
    MineFactorsTool,
    SaveFactorSetTool,
    ScoreFactorComboTool,
    ShowFactorTool,
    TrainFactorWeightsTool,
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
    ListFactorsTool(),
    ComputeFactorTool(),
    EvaluateFactorTool(),
    MineFactorsTool(),
    TrainFactorWeightsTool(),
    ScoreFactorComboTool(),
    SaveFactorSetTool(),
    CreateFactorStrategyTool(),
    ShowFactorTool(),
    *ACCOUNT_TOOLS,
    PrepareTradeTool(),
    PrepareCancelOrderTool(),
]


class ToolRegistry:
    """工具集合 + 统一调度。"""

    def __init__(self, tools: list[Tool] | None = None):
        self._tools: dict[str, Tool] = {t.name: t for t in (tools or _ALL_TOOLS)}

    def schemas(self, enabled: set[str] | None = None) -> list[ToolSchema]:
        """工具声明列表：`enabled is None` 返回全部（向后兼容既有调用 / 单测）；否则只返回启用项。"""
        return [ToolSchema(name=t.name, description=t.description, input_schema=t.input_schema)
                for t in self._tools.values()
                if enabled is None or t.name in enabled]

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def is_ui_action(self, name: str) -> bool:
        tool = self._tools.get(name)
        return bool(tool and tool.is_ui_action)

    def all_names(self) -> set[str]:
        """注册表内全部工具名（启用集解析的全集，C6 唯一真源）。"""
        return set(self._tools)

    def tool_meta(self) -> list[dict]:
        """按 `_ALL_TOOLS` 顺序返回展示元信息（设置中心 · Agent 工具管理用，不含启用态）。"""
        return [self._meta_of(t) for t in self._tools.values()]

    def meta_for(self, name: str) -> dict | None:
        """单个工具的展示元信息（PUT 回包用）；未知名返回 None。"""
        tool = self._tools.get(name)
        return self._meta_of(tool) if tool is not None else None

    @staticmethod
    def _meta_of(tool: Tool) -> dict:
        return {"name": tool.name, "description": tool.description, "category": tool.category,
                "isUiAction": tool.is_ui_action, "paramsSchema": tool.input_schema}

    def disabled_names(self, enabled: set[str] | None) -> list[str]:
        """供系统提示用的已禁用工具名（升序）；`enabled is None` 不过滤即返回 `[]`。"""
        if enabled is None:
            return []
        return sorted(self.all_names() - enabled)

    async def execute(self, name: str, args: dict, ctx: ToolContext, *,
                      enabled: set[str] | None = None) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(False, f"未知工具：{name}")
        is_trading = tool.category in {"account", "trading"}
        if is_trading and not get_settings().agent_trading_enabled:
            return ToolResult(False, "对话交易功能已关闭",
                              code="AGENT_TRADING_DISABLED")
        # 纵深防御（在「未知工具」判定之后、校验之前）：禁用工具二次兜底拒绝，schema 硬闸门外再加一道。
        if enabled is not None and name not in enabled:
            return ToolResult(
                False, f"工具 {name} 已被禁用，请在设置中心 · Agent 工具管理中开启后再使用。",
                code="TOOL_DISABLED")
        args = args or {}
        # 模型产出的入参即外部输入：边界处一次性校验，非法即拒并回喂模型自纠（H2，不执行）。
        invalid = tool.validate(args)
        if invalid is not None:
            return ToolResult(False, f"参数无效：{invalid}")
        try:
            return await tool.execute(args, ctx)
        except TradingError as exc:
            return ToolResult(False, exc.message, code=exc.code)
        except OKXError as exc:
            if is_trading:
                return ToolResult(False, "账户读取失败，请稍后查询。",
                                  code="ACCOUNT_READ_FAILED")
            return ToolResult(False, f"OKX 暂不可达，无法取数（{exc.message}）。请稍后重试。")
        except HTTPException as exc:
            if is_trading:
                return ToolResult(False, "账户操作暂不可用，请核对状态。",
                                  code="ACCOUNT_READ_FAILED")
            detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
            return ToolResult(False, f"操作失败：{detail.get('message', detail)}")
        except Exception as exc:  # noqa: BLE001 —— 工具边界统一兜底，避免单工具异常拖垮整轮 run
            if is_trading:
                correlation_id = uuid.uuid4().hex[:12]
                logger.error("account tool failed name=%s ref=%s type=%s",
                             name, correlation_id, type(exc).__name__)
                return ToolResult(
                    False, f"账户工具暂不可用，请查询状态（编号 {correlation_id}）。",
                    code="TOOL_EXECUTION_FAILED",
                )
            # 脱敏有界根因：仅类型名 + 截断消息，便于模型 / 用户纠正，杜绝栈与密钥（H7 / C7）。
            logger.exception("tool %s execute failed", name)
            return ToolResult(False, f"工具 {name} 执行出错：{type(exc).__name__}: {str(exc)[:200]}")
