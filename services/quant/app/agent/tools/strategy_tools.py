"""策略 / 回测工具：list_strategies / run_backtest / compare_strategies (spec §2.4)。

回测 / 对比复用 `agent/services.py`（与 UI 触发结果一致，不重复实现）。
"""
from __future__ import annotations

from ...db import repositories as repo
from ...okx.rest_client import get_rest_client
from .. import services
from .base import (
    Tool,
    ToolContext,
    ToolResult,
    check_enum,
    check_limit,
    check_pos_int_list,
    check_required_range,
    first_error,
    require_pos_int,
    require_str,
)

_BAR_ENUM = ["1m", "5m", "15m", "30m", "1H", "4H", "1D"]


def _metrics_line(metrics: dict) -> str:
    return (f"总收益 {metrics.get('totalReturn')}, 胜率 {metrics.get('winRate')}, "
            f"夏普 {metrics.get('sharpe')}, 最大回撤 {metrics.get('maxDrawdown')}, "
            f"成交 {metrics.get('tradeCount')} 笔")


class ListStrategiesTool(Tool):
    name = "list_strategies"
    description = "列出内置与自定义策略及其当前参数。"
    input_schema = {"type": "object", "properties": {}}

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        async with ctx.session_factory() as session:
            strategies = await repo.list_strategies(session)
        lines = [
            f"#{s['id']} {s['name']}（{s['templateKey']}）：{s['description']}"
            for s in strategies if not s.get("unavailable")
        ]
        summary = f"共 {len(lines)} 个可用策略：\n" + "\n".join(lines)
        payload = {"items": [{"id": s["id"], "name": s["name"],
                              "templateKey": s["templateKey"]} for s in strategies]}
        return ToolResult(True, summary, payload=payload)


class RunBacktestTool(Tool):
    name = "run_backtest"
    description = "对指定策略在某标的/周期/时间区间上运行回测，返回 runId 与绩效指标。"
    input_schema = {
        "type": "object",
        "properties": {
            "strategyId": {"type": "integer", "description": "策略 id（来自 list_strategies）"},
            "instId": {"type": "string", "description": "如 BTC-USDT-SWAP"},
            "bar": {"type": "string", "enum": _BAR_ENUM},
            "startTs": {"type": "integer", "description": "秒级 UTC 起"},
            "endTs": {"type": "integer", "description": "秒级 UTC 止"},
            "limit": {"type": "integer", "description": "取数根数上限（默认 500，上限 1000）"},
            "params": {"type": "object", "description": "可选参数覆盖"},
        },
        "required": ["strategyId", "instId", "bar", "startTs", "endTs"],
    }

    def validate(self, args: dict) -> str | None:
        return first_error(
            require_pos_int(args, "strategyId"),
            require_str(args, "instId"),
            check_enum(args, "bar", _BAR_ENUM, required=True),
            check_required_range(args),
            check_limit(args, 1, 1000),
        )

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        limit = min(int(args.get("limit", 500)), 1000)
        async with ctx.session_factory() as session:
            result = await services.run_single_backtest(
                session, get_rest_client(),
                strategy_id=args["strategyId"], inst_id=args["instId"], bar=args["bar"],
                limit=limit, start_ts=args["startTs"], end_ts=args["endTs"],
                params=args.get("params"),
            )
        summary = (f"回测完成 runId={result['runId']}：{_metrics_line(result['metrics'])}。"
                   f"可用 show_chart 叠加该 run 的买卖点。")
        return ToolResult(True, summary, payload=result)


class CompareStrategiesTool(Tool):
    name = "compare_strategies"
    description = "多个策略在同一标的/周期/时间区间上横向对比绩效。"
    input_schema = {
        "type": "object",
        "properties": {
            "strategyIds": {"type": "array", "items": {"type": "integer"},
                            "description": "2~12 个策略 id"},
            "instId": {"type": "string"},
            "bar": {"type": "string", "enum": _BAR_ENUM},
            "startTs": {"type": "integer", "description": "秒级 UTC 起"},
            "endTs": {"type": "integer", "description": "秒级 UTC 止"},
            "limit": {"type": "integer", "description": "取数根数上限（默认 500，上限 1000）"},
        },
        "required": ["strategyIds", "instId", "bar", "startTs", "endTs"],
    }

    def validate(self, args: dict) -> str | None:
        return first_error(
            check_pos_int_list(args, "strategyIds"),
            require_str(args, "instId"),
            check_enum(args, "bar", _BAR_ENUM, required=True),
            check_required_range(args),
            check_limit(args, 1, 1000),
        )

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        limit = min(int(args.get("limit", 500)), 1000)
        async with ctx.session_factory() as session:
            result = await services.run_compare(
                session, get_rest_client(),
                strategy_ids=args["strategyIds"], inst_id=args["instId"], bar=args["bar"],
                limit=limit, start_ts=args["startTs"], end_ts=args["endTs"],
                cancel_check=ctx.cancel_check,
            )
        lines = [
            f"#{it['strategyId']} {it['name']} (run {it['runId']})：{_metrics_line(it['metrics'])}"
            for it in result["items"]
        ]
        summary = f"对比 {result['instId']} {result['bar']}：\n" + "\n".join(lines)
        payload = {"instId": result["instId"], "bar": result["bar"],
                   "items": [{"strategyId": it["strategyId"], "runId": it["runId"],
                              "name": it["name"], "metrics": it["metrics"]}
                             for it in result["items"]]}
        return ToolResult(True, summary, payload=payload)
