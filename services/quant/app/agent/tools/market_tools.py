"""行情工具：list_instruments / get_market_summary / get_candles (spec §2.4)。

均直连 OKX 适配层；OKX 不可达时由 registry 统一收敛为 ok=False，绝不假数据（C1）。
所有列表 / 序列在边界处截断并在 summary 注明，避免单次结果撑爆上下文（输出预算）。
"""
from __future__ import annotations

from ...okx.rest_client import get_rest_client
from .base import (
    Tool,
    ToolContext,
    ToolResult,
    check_enum,
    check_limit,
    check_optional_range,
    first_error,
    require_str,
)

_INST_TYPE_ENUM = ["SPOT", "SWAP", "FUTURES"]
_BAR_ENUM = ["1m", "5m", "15m", "30m", "1H", "4H", "1D"]
_TOP_N = 20


def _infer_inst_type(inst_id: str) -> str:
    parts = inst_id.split("-")
    if inst_id.endswith("-SWAP"):
        return "SWAP"
    if len(parts) == 2:
        return "SPOT"
    return "FUTURES"


def _fmt_pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:+.2f}%"


class ListInstrumentsTool(Tool):
    name = "list_instruments"
    description = "列出某类型（SPOT/SWAP/FUTURES）成交额最高的若干标的及其 24h 行情快照。"
    input_schema = {
        "type": "object",
        "properties": {
            "instType": {"type": "string", "enum": _INST_TYPE_ENUM,
                         "description": "标的类型：现货 SPOT / 永续 SWAP / 交割 FUTURES"},
        },
        "required": ["instType"],
    }

    def validate(self, args: dict) -> str | None:
        # 缺省由 execute 回退默认；给值则须为合法类型。
        return check_enum(args, "instType", _INST_TYPE_ENUM)

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        inst_type = args.get("instType", "SWAP")
        instruments = await get_rest_client().get_instruments(inst_type)
        top = instruments[:_TOP_N]
        rows = [
            {"instId": i.instId, "last": i.last, "changePct": i.changePct, "volCcy24h": i.volCcy24h}
            for i in top
        ]
        lines = [
            f"{idx + 1}. {i.instId} 价 {i.last} 24h {_fmt_pct(i.changePct)}"
            for idx, i in enumerate(top)
        ]
        total = len(instruments)
        truncated = total > len(top)
        # 截断透明化（RS-15）：summary 与 payload 一致告知是否裁剪及总数，避免模型据残缺数据下错结论。
        suffix = (f"（按成交额降序，已截断：共 {total} 个，仅返回前 {len(top)}）" if truncated
                  else f"（按成交额降序，共 {total} 个）")
        summary = f"{inst_type} 成交额最高标的{suffix}：\n" + "\n".join(lines)
        return ToolResult(True, summary, payload={"instType": inst_type, "items": rows,
                                                  "truncated": truncated, "totalCount": total})


class GetMarketSummaryTool(Tool):
    name = "get_market_summary"
    description = "查某标的的最新价、24h 涨跌幅与成交额快照。"
    input_schema = {
        "type": "object",
        "properties": {
            "instId": {"type": "string", "description": "如 BTC-USDT-SWAP / ETH-USDT"},
        },
        "required": ["instId"],
    }

    def validate(self, args: dict) -> str | None:
        return require_str(args, "instId")

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        inst_id = args["instId"]
        # 24h 字段取自 tickers 同源（P2-5），不用只回 last 的 get_ticker。
        instruments = await get_rest_client().get_instruments(_infer_inst_type(inst_id))
        match = next((i for i in instruments if i.instId == inst_id), None)
        if match is None:
            return ToolResult(False, f"未在行情中找到标的 {inst_id}（请确认标的或类型）。")
        summary = (f"{inst_id} 最新价 {match.last}，24h 涨跌 {_fmt_pct(match.changePct)}，"
                   f"24h 成交额 {match.volCcy24h}，成交量 {match.vol24h}。")
        payload = {"instId": inst_id, "last": match.last, "changePct": match.changePct,
                   "vol24h": match.vol24h, "volCcy24h": match.volCcy24h}
        return ToolResult(True, summary, payload=payload)


class GetCandlesTool(Tool):
    name = "get_candles"
    description = "拉取某标的某周期的 K 线并返回聚合摘要（首尾价、区间高低、均量）。"
    input_schema = {
        "type": "object",
        "properties": {
            "instId": {"type": "string", "description": "如 BTC-USDT-SWAP"},
            "bar": {"type": "string", "enum": _BAR_ENUM},
            "limit": {"type": "integer", "description": "根数（默认 120，上限 300）"},
            "startTs": {"type": "integer", "description": "秒级 UTC 起（可选）"},
            "endTs": {"type": "integer", "description": "秒级 UTC 止（可选）"},
        },
        "required": ["instId", "bar"],
    }

    def validate(self, args: dict) -> str | None:
        return first_error(
            require_str(args, "instId"),
            check_enum(args, "bar", _BAR_ENUM, required=True),
            check_optional_range(args),
            check_limit(args, 1, 1000),
        )

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        inst_id = args["instId"]
        bar = args["bar"]
        limit = min(int(args.get("limit", 120)), 300)
        before = args.get("endTs")
        after = args.get("startTs")
        candles = await get_rest_client().get_candles(inst_id, bar, limit, before=before, after=after)
        if not candles:
            return ToolResult(False, f"{inst_id} {bar} 该区间无 K 线数据。")
        highs = max(c.h for c in candles)
        lows = min(c.l for c in candles)
        avg_vol = sum(c.vol for c in candles) / len(candles)
        first, last = candles[0], candles[-1]
        change = (last.c - first.c) / first.c if first.c else None
        summary = (f"{inst_id} {bar}：{len(candles)} 根 K 线，开 {first.c} → 收 {last.c}"
                   f"（{_fmt_pct(change)}），区间高 {highs} / 低 {lows}，均量 {avg_vol:.2f}。")
        payload = {"instId": inst_id, "bar": bar, "count": len(candles),
                   "firstTs": first.ts, "lastTs": last.ts, "high": highs, "low": lows}
        return ToolResult(True, summary, payload=payload)
