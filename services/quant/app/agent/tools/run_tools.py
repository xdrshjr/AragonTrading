"""运行历史 / 绩效工具：list_runs / get_run_metrics / get_run_trades / get_run_equity (spec §2.4)。"""
from __future__ import annotations

import json

from ...db import repositories as repo
from .base import Tool, ToolContext, ToolResult, require_pos_int

_TRADES_LIMIT = 50
_EQUITY_POINTS = 60


def _parse_metrics(run) -> dict:
    if not run.metrics_json:
        return {}
    try:
        return json.loads(run.metrics_json)
    except json.JSONDecodeError:
        return {}


class ListRunsTool(Tool):
    name = "list_runs"
    category = "runs"
    description = "列出运行历史（回测 / 实时模拟），按最新在前，支持按策略/标的/模式过滤。"
    input_schema = {
        "type": "object",
        "properties": {
            "strategyId": {"type": "integer"},
            "instId": {"type": "string"},
            "mode": {"type": "string", "enum": ["backtest", "paper"]},
            "limit": {"type": "integer", "description": "默认 20，上限 50"},
        },
    }

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        limit = min(int(args.get("limit", 20)), 50)
        async with ctx.session_factory() as session:
            runs = await repo.list_runs(
                session, strategy_id=args.get("strategyId"), inst_id=args.get("instId"),
                mode=args.get("mode"), limit=limit)
        lines = [f"run {r.id}: {r.inst_id} {r.bar} {r.mode} 状态={r.status}" for r in runs]
        summary = f"共 {len(runs)} 条运行历史：\n" + "\n".join(lines)
        payload = {"items": [{"runId": r.id, "instId": r.inst_id, "bar": r.bar,
                              "mode": r.mode, "status": r.status} for r in runs]}
        return ToolResult(True, summary, payload=payload)


class GetRunMetricsTool(Tool):
    name = "get_run_metrics"
    category = "runs"
    description = "查某次 run 的绩效指标（收益 / 胜率 / 夏普 / 回撤等）。"
    input_schema = {
        "type": "object",
        "properties": {"runId": {"type": "integer"}},
        "required": ["runId"],
    }

    def validate(self, args: dict) -> str | None:
        return require_pos_int(args, "runId")

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        async with ctx.session_factory() as session:
            run = await repo.get_run(session, args["runId"])
        if run is None:
            return ToolResult(False, f"run {args['runId']} 不存在。")
        metrics = _parse_metrics(run)
        summary = f"run {run.id}（{run.inst_id} {run.bar} {run.mode}）绩效：{metrics}"
        return ToolResult(True, summary, payload={"runId": run.id, "metrics": metrics})


class GetRunTradesTool(Tool):
    name = "get_run_trades"
    category = "runs"
    description = "查某次 run 的成交明细（截断 Top N）。"
    input_schema = {
        "type": "object",
        "properties": {
            "runId": {"type": "integer"},
            "limit": {"type": "integer", "description": f"默认 {_TRADES_LIMIT}"},
        },
        "required": ["runId"],
    }

    def validate(self, args: dict) -> str | None:
        return require_pos_int(args, "runId")

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        limit = min(int(args.get("limit", _TRADES_LIMIT)), _TRADES_LIMIT)
        async with ctx.session_factory() as session:
            trades = await repo.list_trades(session, args["runId"], limit=limit)
        lines = [f"{t.ts} {t.side} 价 {t.price} 量 {t.qty} 盈亏 {t.pnl}" for t in trades]
        shown = len(trades)
        # 截断透明化（RS-15）：返回满页即可能还有更多（repo 已按 limit 取数，真总数未知，故 totalCount 省略）。
        truncated = shown >= limit
        note = (f"（已截断，仅返回最近 {shown} 笔；如需更多请缩小范围或提高 limit）" if truncated
                else f"（共 {shown} 笔）")
        summary = f"run {args['runId']} 成交{note}：\n" + "\n".join(lines)
        payload = {"runId": args["runId"], "count": shown, "truncated": truncated}
        return ToolResult(True, summary, payload=payload)


class GetRunEquityTool(Tool):
    name = "get_run_equity"
    category = "runs"
    description = "查某次 run 的净值 / 回撤序列（下采样）。"
    input_schema = {
        "type": "object",
        "properties": {"runId": {"type": "integer"}},
        "required": ["runId"],
    }

    def validate(self, args: dict) -> str | None:
        return require_pos_int(args, "runId")

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        async with ctx.session_factory() as session:
            points = await repo.list_equity(session, args["runId"])
        if not points:
            return ToolResult(False, f"run {args['runId']} 无净值数据。")
        step = max(1, len(points) // _EQUITY_POINTS)
        sampled = points[::step]
        first, last = points[0], points[-1]
        max_dd = min((p.drawdown for p in points), default=0.0)
        total = len(points)
        # 截断透明化（RS-15）：下采样后明确告知原始点数与返回点数。
        downsampled = len(sampled) < total
        note = (f"（已下采样：共 {total} 点，返回 {len(sampled)} 点）" if downsampled
                else f"（共 {total} 点）")
        summary = (f"run {args['runId']} 净值：起 {first.equity} → 末 {last.equity}，"
                   f"最大回撤 {max_dd}{note}。")
        payload = {"runId": args["runId"], "first": first.equity, "last": last.equity,
                   "maxDrawdown": max_dd, "truncated": downsampled, "totalCount": total}
        return ToolResult(True, summary, payload=payload)
