"""信号前瞻收益工具 test_signal_forward_returns (spec §2.5 / §4)。

薄包装：取真实 OKX K 线 → ``candles_to_df`` → ``engine.signal_edge.forward_returns`` →
组装含三步推理脚手架的 ``summary``（回喂模型 / 前端展示的唯一通道，P1-1）+ 结构化 ``payload``
（仅供单测断言 / 未来接线，当前不回喂模型、也不下发前端）。

取数严格剔除尾部未确认（forming）K 线（因果 / 不假数据 C1）：无区间走 ``get_candles`` 后过滤；
给区间走 ``services.load_range_candles``（自带 confirm + 越界 + 空区间 400）。
"""
from __future__ import annotations

from ...engine.backtester import candles_to_df
from ...engine.signal_edge import (
    MIN_SIGNALS,
    SIGNAL_LIBRARY,
    HorizonStat,
    SignalEdgeResult,
    forward_returns,
    horizon_stat_to_dict,
)
from ...okx.rest_client import get_rest_client
from .. import services
from ..prompts import SIGNAL_EDGE_REASONING_SCAFFOLD
from .base import (
    BAR_ENUM,
    Tool,
    ToolContext,
    ToolResult,
    check_enum,
    check_int_list,
    check_limit,
    check_optional_range,
    first_error,
    require_str,
)

_DEFAULT_LIMIT = 300
_MAX_LIMIT = 300
_DEFAULT_HORIZONS = (1, 3, 6, 12)
_MAX_HORIZONS = 8
_MAX_HORIZON_VAL = 100
_ENTRY_ENUM = ("next_open", "signal_close")
SIGNAL_ENUM = tuple(SIGNAL_LIBRARY)


def _drop_forming(candles: list) -> list:
    """剔除未确认（forming）K 线，杜绝用未收盘数据（C1，语义同 services 区间过滤）。"""
    return [c for c in candles if getattr(c, "confirm", True)]


def _spct(value: float) -> str:
    return f"{value * 100:+.2f}%"


def _horizon_line(stat: HorizonStat, cost_bps: float) -> str:
    """单 horizon 一行关键数字（细节如分位 / avgWin 仅入 payload，控上下文 R8）。"""
    if stat.count == 0:
        return f"  h={stat.h}：样本 0（窗口不足以覆盖该 horizon）"
    line = (f"  h={stat.h}：样本 {stat.count}，胜率 {stat.winRate * 100:.1f}%，"
            f"均值 {_spct(stat.meanRet)}，中位 {_spct(stat.medianRet)}，"
            f"t={stat.tStat}，相对基线 edge={_spct(stat.edgeVsBaseline)}")
    if cost_bps > 0:
        line += f"（扣 {cost_bps:g}bps 后均值 {_spct(stat.netMeanRet)}）"
    return line


def _adequacy_line(occurrences: int) -> str:
    if occurrences < MIN_SIGNALS:
        return (f"样本充分性：⚠ 仅 {occurrences} 次触发，样本偏少（<{MIN_SIGNALS}），"
                "统计结论需谨慎；窗口重叠时 t 值偏乐观。")
    return f"样本充分性：{occurrences} 次触发，达基本阈值（≥{MIN_SIGNALS}）；窗口重叠时 t 值偏乐观。"


def _candidates_text(candidates: list[dict]) -> str:
    if not candidates:
        return "（当前无匹配的内置策略）"
    return " / ".join(f"{c['key']}（{c['name']}）" for c in candidates)


def _candidates_line(result: SignalEdgeResult) -> str:
    return f"候选策略（若验证有 edge 可落成）：{_candidates_text(result.candidateStrategies)}"


def _format_summary(args: dict, result: SignalEdgeResult) -> str:
    spec = SIGNAL_LIBRARY[result.signal]
    if result.occurrences == 0:
        empty = (f"{args['instId']} {args['bar']} · 信号 {result.signal}"
                 f"（{spec.cn}，方向 {result.direction}）触发 0 次："
                 f"该信号在近 {result.bars} 根从未出现，无从评估。")
        return "\n".join([empty, _candidates_line(result), SIGNAL_EDGE_REASONING_SCAFFOLD])
    header = (f"{args['instId']} {args['bar']} · 信号 {result.signal}"
              f"（{spec.cn}，方向 {result.direction}）前瞻收益"
              f"（近 {result.bars} 根，触发 {result.occurrences} 次，入场={result.entryAt}）：")
    lines = [_horizon_line(stat, result.costBps) for stat in result.horizons]
    return "\n".join([header, *lines, _adequacy_line(result.occurrences),
                      _candidates_line(result), SIGNAL_EDGE_REASONING_SCAFFOLD])


def _payload(args: dict, result: SignalEdgeResult) -> dict:
    return {
        "instId": args["instId"], "bar": args["bar"], "signal": result.signal,
        "direction": result.direction, "entryAt": result.entryAt, "costBps": result.costBps,
        "windowStartTs": result.windowStartTs, "windowEndTs": result.windowEndTs,
        "bars": result.bars, "lastTs": result.lastTs, "occurrences": result.occurrences,
        "horizons": [horizon_stat_to_dict(stat) for stat in result.horizons],
        "candidateStrategies": result.candidateStrategies,
    }


class TestSignalForwardReturnsTool(Tool):
    name = "test_signal_forward_returns"
    category = "analysis"
    description = (
        "不跑完整策略，只统计某具名信号（如放量突破 / RSI 超卖 / 触及布林下轨）出现后未来 N 根 "
        "K 线的前瞻收益：返回每个 horizon 的样本量、胜率、均值 / 中位收益、t 值与相对无条件基线的"
        "超额收益(edge)，用于先验证信号本身有没有统计优势，再决定要不要做成完整策略。"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "instId": {"type": "string", "description": "如 BTC-USDT-SWAP / ETH-USDT"},
            "bar": {"type": "string", "enum": list(BAR_ENUM)},
            "signal": {"type": "string", "enum": list(SIGNAL_ENUM),
                       "description": "要检验的具名信号"},
            "horizons": {"type": "array", "items": {"type": "integer"},
                         "description": "前瞻根数列表，默认 [1,3,6,12]，至多 8 个、每个 1..100"},
            "entryAt": {"type": "string", "enum": list(_ENTRY_ENUM),
                        "description": "入场口径：next_open=下一根开盘(默认，与回测撮合一致)；"
                                       "signal_close=信号根收盘"},
            "costBps": {"type": "number",
                        "description": "可选成本(基点)，>0 时额外给扣成本后的净均值；默认 0=原始信号收益"},
            "startTs": {"type": "integer",
                        "description": "秒级 UTC 起（可选；给区间则按区间取数，缺省取最新 limit 根）"},
            "endTs": {"type": "integer", "description": "秒级 UTC 止（可选）"},
            "limit": {"type": "integer", "description": "取数根数（默认 300，硬上限 300=OKX 单页）"},
        },
        "required": ["instId", "bar", "signal"],
    }

    def validate(self, args: dict) -> str | None:
        return first_error(
            require_str(args, "instId"),
            check_enum(args, "bar", BAR_ENUM, required=True),
            check_enum(args, "signal", SIGNAL_ENUM, required=True),
            check_enum(args, "entryAt", _ENTRY_ENUM),
            check_int_list(args, "horizons", max_len=_MAX_HORIZONS, max_val=_MAX_HORIZON_VAL),
            check_optional_range(args),
            check_limit(args, 1, _MAX_LIMIT),
        )

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        candles = await self._load_candles(args)
        df = candles_to_df(candles)
        try:
            result = forward_returns(
                df, args["signal"], horizons=_horizons(args),
                entry_at=args.get("entryAt", "next_open"), cost_bps=_cost_bps(args))
        except ValueError as exc:
            return ToolResult(False, f"{args['instId']} {args['bar']} 无法评估信号 "
                                     f"{args['signal']}：{exc}")
        return ToolResult(True, _format_summary(args, result), payload=_payload(args, result))

    async def _load_candles(self, args: dict) -> list:
        limit = min(int(args.get("limit", _DEFAULT_LIMIT)), _MAX_LIMIT)
        start_ts, end_ts = args.get("startTs"), args.get("endTs")
        if start_ts is not None and end_ts is not None:
            return await services.load_range_candles(
                get_rest_client(), inst_id=args["instId"], bar=args["bar"],
                limit=limit, start_ts=start_ts, end_ts=end_ts)
        candles = await get_rest_client().get_candles(args["instId"], args["bar"], limit)
        return _drop_forming(candles)


def _horizons(args: dict) -> tuple[int, ...]:
    """缺省取默认；给值经 validate 后去重 + 升序归一，杜绝重复 / 乱序行（RV-4）。"""
    raw = args.get("horizons")
    if not raw:
        return _DEFAULT_HORIZONS
    return tuple(sorted({int(value) for value in raw}))


def _cost_bps(args: dict) -> float:
    """非法值兜底为 0（不进 validate 以保持原语集精简，与 RunBacktestTool 同风格）。"""
    try:
        return float(args.get("costBps") or 0.0)
    except (TypeError, ValueError):
        return 0.0
