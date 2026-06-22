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
    """每次 run 注入：DB session 工厂 + 当前 UI 上下文 + 协作式取消探针（H11，可选）。"""

    session_factory: Callable[[], AsyncSession]
    ui: UiContext = field(default_factory=UiContext)
    cancel_check: Callable[[], bool] | None = None


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

    def validate(self, args: dict) -> str | None:
        """校验模型产出的工具入参（外部输入，边界一次性校验，H2）。

        Args:
            args: 模型产出的入参 dict（malformed JSON 已在 loop 回退为 ``{}``）。

        Returns:
            ``None`` 表示放行；返回错误信息字符串即视为非法（registry 据此拒绝执行并回喂模型自纠）。
        """
        return None

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        raise NotImplementedError


# ============================ 入参校验原语（H2，各工具 validate 复用） ============================
BAR_ENUM = ("1m", "5m", "15m", "30m", "1H", "4H", "1D")


def first_error(*messages: str | None) -> str | None:
    """返回首个非空错误信息（链式校验：全部通过则 ``None``）。"""
    for message in messages:
        if message:
            return message
    return None


def _is_pos_int(value: object) -> bool:
    """正整数判定（显式排除 bool，避免 ``True`` 被当作 1）。"""
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def require_str(args: dict, key: str) -> str | None:
    """必填非空字符串。"""
    value = args.get(key)
    if not isinstance(value, str) or not value.strip():
        return f"缺少必填参数 {key}（应为非空字符串）"
    return None


def check_enum(args: dict, key: str, allowed: tuple, *, required: bool = False) -> str | None:
    """枚举校验：缺省按 ``required`` 决定是否报错；给值则须在 ``allowed`` 内。"""
    value = args.get(key)
    if value is None:
        return f"缺少必填参数 {key}" if required else None
    if value not in allowed:
        return f"{key} 必须为 {'/'.join(allowed)} 之一"
    return None


def require_pos_int(args: dict, key: str) -> str | None:
    """必填正整数。"""
    return None if _is_pos_int(args.get(key)) else f"{key} 必须为正整数"


def check_pos_int_list(args: dict, key: str) -> str | None:
    """必填非空数组，元素均为正整数。"""
    value = args.get(key)
    if not isinstance(value, list) or not value:
        return f"{key} 必须为非空数组"
    if not all(_is_pos_int(item) for item in value):
        return f"{key} 的元素必须为正整数"
    return None


def check_int_list(args: dict, key: str, *, max_len: int, max_val: int) -> str | None:
    """可选有界正整数数组：缺省放行；给值则须为长度 ``1..max_len``、每元素 ``1..max_val`` 的整数。

    与 ``check_pos_int_list`` 区分——后者必填非空、不限上界；本原语可选且对长度与元素同时设上界
    （供 ``horizons`` 等「给则有界」的列表参数复用）。
    """
    value = args.get(key)
    if value is None:
        return None
    if not isinstance(value, list) or not 1 <= len(value) <= max_len:
        return f"{key} 必须为长度 1..{max_len} 的数组"
    if not all(_is_pos_int(item) and item <= max_val for item in value):
        return f"{key} 的元素必须为 1..{max_val} 之间的整数"
    return None


def check_limit(args: dict, low: int, high: int) -> str | None:
    """可选 ``limit``：给值则须为 ``low..high`` 的整数。"""
    value = args.get("limit")
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
        return f"limit 必须为 {low}..{high} 之间的整数"
    return None


def check_required_range(args: dict) -> str | None:
    """``startTs``/``endTs`` 必填、正整数且 ``startTs < endTs``。"""
    return first_error(
        require_pos_int(args, "startTs"), require_pos_int(args, "endTs"),
        None if not (_is_pos_int(args.get("startTs")) and _is_pos_int(args.get("endTs")))
        else ("startTs 必须小于 endTs" if args["startTs"] >= args["endTs"] else None))


def check_optional_range(args: dict) -> str | None:
    """``startTs``/``endTs`` 可选：给则须为正整数且有序（任一缺失不报顺序错）。"""
    start, end = args.get("startTs"), args.get("endTs")
    if start is None and end is None:
        return None
    for key in ("startTs", "endTs"):
        if args.get(key) is not None and not _is_pos_int(args.get(key)):
            return f"{key} 必须为正整数"
    if _is_pos_int(start) and _is_pos_int(end) and start >= end:
        return "startTs 必须小于 endTs"
    return None
