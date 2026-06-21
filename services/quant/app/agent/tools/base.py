"""工具基类 / 上下文 / 结果类型 (spec §2.4)。

工具是既有后端能力的薄包装：统一 `name + JSON Schema + async execute(args, ctx)`。
异常统一在 `ToolRegistry.execute` 收敛为 `ToolResult(ok=False)`，故工具实现保持精简。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class UiContext:
    """前端连接时上报的当前工作台上下文（驱动 system prompt 注入与 ui-action 默认值）。"""

    instId: str | None = None
    bar: str | None = None
    mode: str | None = None
    strategyId: int | None = None
    backtestStartTs: int | None = None
    backtestEndTs: int | None = None

    @classmethod
    def from_dict(cls, data: dict | None) -> "UiContext":
        data = data or {}
        return cls(
            instId=data.get("instId"),
            bar=data.get("bar"),
            mode=data.get("mode"),
            strategyId=data.get("strategyId"),
            backtestStartTs=data.get("backtestStartTs"),
            backtestEndTs=data.get("backtestEndTs"),
        )

    def to_dict(self) -> dict:
        return {
            "instId": self.instId, "bar": self.bar, "mode": self.mode,
            "strategyId": self.strategyId, "backtestStartTs": self.backtestStartTs,
            "backtestEndTs": self.backtestEndTs,
        }


@dataclass
class ToolContext:
    """每次 run 注入：DB session 工厂 + 当前 UI 上下文。"""

    session_factory: Callable[[], AsyncSession]
    ui: UiContext = field(default_factory=UiContext)


@dataclass
class ToolResult:
    """工具结果：summary 给模型读 + 前端摘要；payload 为 ui-action directive 或结构化数据。"""

    ok: bool
    summary: str
    payload: dict | None = None


class Tool:
    """工具基类：子类设类属性 name/description/input_schema/is_ui_action 并实现 execute。"""

    name: str = ""
    description: str = ""
    input_schema: dict = {}
    is_ui_action: bool = False

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        raise NotImplementedError
