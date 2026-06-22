"""行情体制识别工具 label_market_regime (spec §2.5 / §4)。

薄包装：取真实 OKX K 线 → ``candles_to_df`` → ``engine.regime`` → 组装含三步 CoT 脚手架的
``summary``（回喂模型 / 前端展示的唯一通道，P1-1）+ 结构化 ``payload``（仅供单测断言 /
未来接线，当前不回喂模型、也不下发前端）。

取数严格剔除尾部未确认（forming）K 线（P1-4 / 因果 R3 / 不假数据 C1）：snapshot 与
timeline-无区间走 ``get_candles`` 后过滤；timeline-有区间走 ``services.load_range_candles``
（自带 confirm + 越界过滤 + 空区间 400）。
"""
from __future__ import annotations

from ...engine.backtester import candles_to_df
from ...engine.regime import (
    RegimeResult,
    RegimeSegment,
    classify_regime,
    count_structural_runs,
    evidence_to_dict,
    segment_regime,
    segment_to_dict,
)
from ...okx.rest_client import get_rest_client
from .. import services
from ..prompts import REGIME_REASONING_SCAFFOLD
from .base import (
    BAR_ENUM,
    Tool,
    ToolContext,
    ToolResult,
    check_enum,
    check_limit,
    check_optional_range,
    first_error,
    require_str,
)

_DEFAULT_SNAPSHOT_LIMIT = 200
_DEFAULT_TIMELINE_LIMIT = 300
_MAX_LIMIT = 300
_MODE_ENUM = ("snapshot", "timeline")

# 标签 → 简短中文（仅用于 summary 展示；定义真源仍是 engine.regime.REGIME_LABELS）。
_LABEL_CN = {
    "trend_up": "趋势上行", "trend_down": "趋势下行", "range": "区间震荡",
    "high_volatility": "高波动", "low_volatility": "低波动",
    "crash": "急跌", "breakout": "突破",
}


def _drop_forming(candles: list) -> list:
    """剔除未确认（forming）K 线，杜绝用未收盘数据（P1-4 / R3 / C1，语义同 services 区间过滤）。"""
    return [c for c in candles if getattr(c, "confirm", True)]


def _spct(value: float) -> str:
    return f"{value * 100:+.2f}%"


def _pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def _candidates_text(candidates: list[dict]) -> str:
    if not candidates:
        return "（当前无匹配的内置策略）"
    return " / ".join(f"{c['key']}（{c['name']}）" for c in candidates)


def _labels_cn(labels: list[str]) -> str:
    return " / ".join(_LABEL_CN.get(label, label) for label in labels)


def _format_snapshot_summary(args: dict, result: RegimeResult) -> str:
    ev = result.evidence
    header = f"{args['instId']} {args['bar']} 体制研判（近 {ev.bars} 根，截至 {ev.lastTs}）："
    evidence = (
        f"证据：ER={ev.er}、净变化={_spct(ev.netChange)}、EMA 斜率={_spct(ev.emaSlopePct)}、"
        f"中轴上方占比={ev.fracAbove}；ATR%={_pct(ev.atrPct)}（近端分位 {ev.atrPctRank}）、"
        f"带宽分位={ev.bbWidthRank}；短窗跌幅={_spct(ev.fastDrop)}、RSI={ev.rsi}；"
        f"前高={ev.priorHigh} / 前低={ev.priorLow}，最新放量 {ev.volRatio}×。")
    verdict = (
        f"确定性初判（仅供参考，非最终）：primary={result.primary}、"
        f"volatility={result.volatility}、event={result.event}，置信 {result.confidence}；"
        f"候选策略：{_candidates_text(result.candidateStrategies)}。")
    return "\n".join([header, evidence, verdict, REGIME_REASONING_SCAFFOLD])


def _format_timeline_summary(args: dict, segments: list[RegimeSegment], merged: bool) -> str:
    note = "（已按时长合并最短段）" if merged else ""
    header = f"{args['instId']} {args['bar']} 体制时间线（共 {len(segments)} 段{note}）："
    lines = [
        f"子段{idx + 1}: [{seg.startTs}~{seg.endTs}] primary={seg.primary}"
        f"（{_labels_cn(seg.labels)}），{seg.bars} 根，置信 {seg.confidence}"
        for idx, seg in enumerate(segments)
    ]
    tip = "提示：把 get_run_trades 的成交按 ts 落到对应子段，即得「在哪种市场里赚 / 亏」的分体制盈亏。"
    return "\n".join([header, *lines, tip, REGIME_REASONING_SCAFFOLD])


def _snapshot_payload(args: dict, result: RegimeResult) -> dict:
    return {
        "mode": "snapshot", "instId": args["instId"], "bar": args["bar"],
        "lastTs": result.evidence.lastTs, "primary": result.primary,
        "volatility": result.volatility, "event": result.event, "labels": result.labels,
        "confidence": result.confidence, "scores": result.scores,
        "evidence": evidence_to_dict(result.evidence),
        "candidateStrategies": result.candidateStrategies,
    }


def _timeline_payload(args: dict, segments: list[RegimeSegment], merged: bool) -> dict:
    return {
        "mode": "timeline", "instId": args["instId"], "bar": args["bar"],
        "segments": [segment_to_dict(seg) for seg in segments],
        "segmentCount": len(segments), "merged": merged,
    }


class LabelMarketRegimeTool(Tool):
    name = "label_market_regime"
    description = (
        "对某标的某周期的当前或指定区间行情做体制识别：返回趋势上 / 下、区间、高 / 低波动、"
        "急跌、突破等标签的打分与带证据初判，以及按体制推荐的候选策略；mode=timeline 时把"
        "区间切成因果体制子段，便于把策略表现按体制拆开看。"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "instId": {"type": "string", "description": "如 BTC-USDT-SWAP / ETH-USDT"},
            "bar": {"type": "string", "enum": list(BAR_ENUM)},
            "mode": {"type": "string", "enum": list(_MODE_ENUM),
                     "description": "snapshot=最新窗口单点判定（默认）；timeline=区间切体制子段"},
            "startTs": {"type": "integer",
                        "description": "秒级 UTC 起（可选；timeline 建议给，snapshot 缺省取最新）"},
            "endTs": {"type": "integer", "description": "秒级 UTC 止（可选）"},
            "limit": {"type": "integer",
                      "description": "取数根数（snapshot 默认 200 / timeline 默认 300，硬上限 300）"},
        },
        "required": ["instId", "bar"],
    }

    def validate(self, args: dict) -> str | None:
        return first_error(
            require_str(args, "instId"),
            check_enum(args, "bar", BAR_ENUM, required=True),
            check_enum(args, "mode", _MODE_ENUM),
            check_optional_range(args),
            check_limit(args, 1, _MAX_LIMIT),
        )

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        if args.get("mode") == "timeline":
            return await self._timeline(args)
        return await self._snapshot(args)

    async def _snapshot(self, args: dict) -> ToolResult:
        candles = await self._snapshot_candles(args)
        df = candles_to_df(candles)
        try:
            result = classify_regime(df, args["bar"])
        except ValueError as exc:
            return ToolResult(False, f"{args['instId']} {args['bar']} 无法判定体制：{exc}")
        return ToolResult(True, _format_snapshot_summary(args, result),
                          payload=_snapshot_payload(args, result))

    async def _timeline(self, args: dict) -> ToolResult:
        candles = await self._timeline_candles(args)
        df = candles_to_df(candles)
        try:
            segments = segment_regime(df, args["bar"])
        except ValueError as exc:
            return ToolResult(False, f"{args['instId']} {args['bar']} 无法生成体制时间线：{exc}")
        merged = len(segments) < count_structural_runs(df)
        return ToolResult(True, _format_timeline_summary(args, segments, merged),
                          payload=_timeline_payload(args, segments, merged))

    async def _snapshot_candles(self, args: dict) -> list:
        limit = min(int(args.get("limit", _DEFAULT_SNAPSHOT_LIMIT)), _MAX_LIMIT)
        candles = await get_rest_client().get_candles(args["instId"], args["bar"], limit)
        return _drop_forming(candles)

    async def _timeline_candles(self, args: dict) -> list:
        limit = min(int(args.get("limit", _DEFAULT_TIMELINE_LIMIT)), _MAX_LIMIT)
        start_ts, end_ts = args.get("startTs"), args.get("endTs")
        if start_ts is not None and end_ts is not None:
            return await services.load_range_candles(
                get_rest_client(), inst_id=args["instId"], bar=args["bar"],
                limit=limit, start_ts=start_ts, end_ts=end_ts)
        candles = await get_rest_client().get_candles(args["instId"], args["bar"], limit)
        return _drop_forming(candles)
