"""因子层 Agent 工具（6 数据工具 + show_factor ui-action，category="factor"）(spec §4.4)。

薄包装因子 engine / service：取真实 OKX K 线 → 因子计算 / 评估 / 挖掘 / 训练 / 打分 → 组装精简
``summary``（回喂模型 / 前端展示）+ 结构化 ``payload``（细节，不回喂模型，控上下文 R10）。异常统一在
``ToolRegistry.execute`` 收敛为 ``ToolResult(ok=False)``（OKX 不可达 / 训练器缺失 / 校验失败皆如实回报，
绝不假数据 C1）。
"""
from __future__ import annotations

import numpy as np

from ...engine.backtester import candles_to_df
from ...engine.factors import (
    FACTOR_LIBRARY,
    available_factors,
    compose_score,
    compute_factor,
    eval_result_to_dict,
    evaluate_factor_key,
    factor_meta,
    resolve_norm_contract,
    score_points,
)
from ...okx.rest_client import get_rest_client
from .. import services
from ..prompts import FACTOR_REASONING_SCAFFOLD
from ...db import repositories as repo
from ...factors import service as factor_service
from ...factors.service import FactorWindow, resolve_set_to_spec
from ...factors.specs import normalize_thresholds, validate_factor_strategy_params
from .base import (
    BAR_ENUM,
    Tool,
    ToolContext,
    ToolResult,
    check_enum,
    check_int_list,
    check_limit,
    check_optional_range,
    check_required_range,
    first_error,
    require_pos_int,
    require_str,
)

FACTOR_KEY_ENUM = tuple(FACTOR_LIBRARY)
_DEFAULT_LIMIT = 300
_MAX_LIMIT = 300
_DEFAULT_HORIZONS = (1, 3, 6, 12)
_MAX_HORIZONS = 8
_MAX_HORIZON_VAL = 100
_DIRECTION_ENUM = ("long", "short", "auto")
_FROZEN_DIRECTION_ENUM = ("long", "short")
_METHOD_ENUM = ("equal", "ic_weight", "linear", "random_forest", "xgboost")


async def _load_df(args: dict):
    """取数：给区间走 ``load_range_candles``（空区间 400），否则取最新 limit 根并剔 forming（C1）。"""
    limit = min(int(args.get("limit", _DEFAULT_LIMIT)), _MAX_LIMIT)
    start_ts, end_ts = args.get("startTs"), args.get("endTs")
    if start_ts is not None and end_ts is not None:
        candles = await services.load_range_candles(
            get_rest_client(), inst_id=args["instId"], bar=args["bar"],
            max_bars=limit, start_ts=start_ts, end_ts=end_ts)
    else:
        raw = await get_rest_client().get_candles(args["instId"], args["bar"], limit)
        candles = [c for c in raw if getattr(c, "confirm", True)]
    return candles_to_df(candles)


def _last_finite(series) -> float | None:
    arr = np.asarray(series, dtype=float)
    finite = arr[np.isfinite(arr)]
    return round(float(finite[-1]), 6) if finite.size else None


class ListFactorsTool(Tool):
    name = "list_factors"
    category = "factor"
    description = "列出因子库目录（key/中文名/类别/方向提示）与已存因子（含激活态），作为因子层的入口。"
    input_schema = {
        "type": "object",
        "properties": {
            "enabledOnly": {"type": "boolean", "description": "仅列已激活的已存因子（默认 false）"},
        },
    }

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        catalog = available_factors()
        enabled_only = bool(args.get("enabledOnly"))
        async with ctx.session_factory() as session:
            stored = await repo.list_factors(session, enabled=True if enabled_only else None)
        lib_lines = [f"  - {f['key']}：{f['cn']}（{f['category']}，方向提示 {f['directionHint']}）"
                     for f in catalog]
        summary = (f"因子库共 {len(catalog)} 个因子，已存 {len(stored)} 个（"
                   f"{'仅激活' if enabled_only else '全部'}）：\n" + "\n".join(lib_lines))
        payload = {"library": catalog,
                   "stored": [{"id": r.id, "factorKey": r.factor_key, "name": r.name,
                               "direction": r.direction, "enabled": bool(r.enabled),
                               "source": r.source} for r in stored]}
        return ToolResult(True, summary, payload=payload)


class ComputeFactorTool(Tool):
    name = "compute_factor"
    category = "factor"
    description = "计算某因子在某标的/周期上的值序列，返回最近值与覆盖率（用于上图或快速查看因子走向）。"
    input_schema = {
        "type": "object",
        "properties": {
            "factorKey": {"type": "string", "enum": list(FACTOR_KEY_ENUM)},
            "params": {"type": "object", "description": "可选覆盖库默认参数"},
            "instId": {"type": "string", "description": "如 BTC-USDT-SWAP"},
            "bar": {"type": "string", "enum": list(BAR_ENUM)},
            "startTs": {"type": "integer", "description": "秒级 UTC 起（可选；缺省取最新 limit 根）"},
            "endTs": {"type": "integer", "description": "秒级 UTC 止（可选）"},
            "limit": {"type": "integer", "description": "取数根数（默认 300，硬上限 300）"},
        },
        "required": ["factorKey", "instId", "bar"],
    }

    def validate(self, args: dict) -> str | None:
        return first_error(
            check_enum(args, "factorKey", FACTOR_KEY_ENUM, required=True),
            require_str(args, "instId"), check_enum(args, "bar", BAR_ENUM, required=True),
            check_optional_range(args), check_limit(args, 1, _MAX_LIMIT))

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        _ = ctx
        df = await _load_df(args)
        series = compute_factor(df, args["factorKey"], args.get("params"), bar=args["bar"])
        latest = _last_finite(series)
        coverage = round(float(np.isfinite(np.asarray(series, dtype=float)).mean()), 4) if len(series) else 0.0
        meta = factor_meta(args["factorKey"]) or {}
        summary = (f"{args['instId']} {args['bar']} · 因子 {args['factorKey']}（{meta.get('cn')}）"
                   f"最近值 {latest}，覆盖率 {coverage:.0%}，共 {len(df)} 根。")
        return ToolResult(True, summary, payload={"factorKey": args["factorKey"], "latest": latest,
                                                  "coverage": coverage, "bars": len(df)})


def _eval_summary(args: dict, payload: dict) -> str:
    meta = factor_meta(args["factorKey"]) or {}
    header = (f"{args['instId']} {args['bar']} · 因子 {args['factorKey']}（{meta.get('cn')}，方向 "
              f"{payload['direction']}）评估（{payload['bars']} 根，配对 {payload['occurrences']}，"
              f"覆盖 {payload['coverage']:.0%}）：")
    lines = []
    for stat in payload["horizons"]:
        if stat["ic"] is None:
            lines.append(f"  h={stat['h']}：样本不足")
            continue
        t_txt = f"，t={round(stat['tStat'], 2)}" if stat["tStat"] is not None else "（分段不足，无 t）"
        lines.append(f"  h={stat['h']}：IC={stat['ic']}，RankIC={stat['rankIc']}，IR={stat['ir']}"
                     f"，单调性={stat['monotonicity']}{t_txt}")
    return "\n".join([header, *lines, FACTOR_REASONING_SCAFFOLD])


class EvaluateFactorTool(Tool):
    name = "evaluate_factor"
    category = "factor"
    description = ("评估某因子对未来收益的解释力：逐 horizon 的 IC / RankIC / 分段 IR / t 值 / 分位单调性，"
                  "用于先验证因子有没有预测力，再决定要不要加入组合做权重训练。")
    input_schema = {
        "type": "object",
        "properties": {
            "factorKey": {"type": "string", "enum": list(FACTOR_KEY_ENUM)},
            "params": {"type": "object", "description": "可选覆盖库默认参数"},
            "direction": {"type": "string", "enum": list(_DIRECTION_ENUM),
                          "description": "long/short/auto（auto 取评估窗口 IC 符号一次性定号）"},
            "instId": {"type": "string"},
            "bar": {"type": "string", "enum": list(BAR_ENUM)},
            "horizons": {"type": "array", "items": {"type": "integer"},
                         "description": "前瞻根数，默认 [1,3,6,12]，至多 8 个、每个 1..100"},
            "startTs": {"type": "integer", "description": "秒级 UTC 起（可选）"},
            "endTs": {"type": "integer", "description": "秒级 UTC 止（可选）"},
            "limit": {"type": "integer", "description": "取数根数（默认 300，硬上限 300）"},
        },
        "required": ["factorKey", "instId", "bar"],
    }

    def validate(self, args: dict) -> str | None:
        return first_error(
            check_enum(args, "factorKey", FACTOR_KEY_ENUM, required=True),
            require_str(args, "instId"), check_enum(args, "bar", BAR_ENUM, required=True),
            check_enum(args, "direction", _DIRECTION_ENUM),
            check_int_list(args, "horizons", max_len=_MAX_HORIZONS, max_val=_MAX_HORIZON_VAL),
            check_optional_range(args), check_limit(args, 1, _MAX_LIMIT))

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        _ = ctx
        df = await _load_df(args)
        horizons = tuple(sorted({int(h) for h in args["horizons"]})) if args.get("horizons") else _DEFAULT_HORIZONS
        result = evaluate_factor_key(df, args["factorKey"], args.get("params"),
                                     args.get("direction", "auto"), bar=args["bar"], horizons=horizons)
        payload = eval_result_to_dict(result)
        return ToolResult(True, _eval_summary(args, payload), payload=payload)


class MineFactorsTool(Tool):
    name = "mine_factors"
    category = "factor"
    description = ("在某标的/周期/区间上扫描整库因子并按 RankIC IR 降序排名 top-N，"
                  "用于快速发现该段行情里最有预测力的因子（工作量过大会拒绝）。")
    input_schema = {
        "type": "object",
        "properties": {
            "instId": {"type": "string"},
            "bar": {"type": "string", "enum": list(BAR_ENUM)},
            "startTs": {"type": "integer", "description": "秒级 UTC 起"},
            "endTs": {"type": "integer", "description": "秒级 UTC 止"},
            "categories": {"type": "array", "items": {"type": "string"},
                           "description": "可选类别白名单（momentum/trend/reversal/volatility/volume/liquidity/mean_revert）"},
            "topN": {"type": "integer", "description": "返回前 N 名（默认 10，上限 50）"},
        },
        "required": ["instId", "bar", "startTs", "endTs"],
    }

    def validate(self, args: dict) -> str | None:
        return first_error(
            require_str(args, "instId"), check_enum(args, "bar", BAR_ENUM, required=True),
            check_required_range(args))

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        top_n = min(int(args.get("topN", 10)), 50)
        window = FactorWindow(inst_id=args["instId"], bar=args["bar"],
                              start_ts=args["startTs"], end_ts=args["endTs"])
        async with ctx.session_factory() as session:
            result = await factor_service.mine(
                session, get_rest_client(), window, categories=args.get("categories"),
                horizons=_DEFAULT_HORIZONS, top_n=top_n, persist=False)
        lines = [f"  #{i + 1} {it['factorKey']}{it['params']}：IR={it['ir']}，RankIC={it['rankIc']}"
                 for i, it in enumerate(result["items"]) if it.get("ir") is not None]
        summary = f"{args['instId']} {args['bar']} 挖掘 top{top_n}（按 RankIC IR）：\n" + "\n".join(lines or ["（无有效因子）"])
        return ToolResult(True, summary, payload=result)


class TrainFactorWeightsTool(Tool):
    name = "train_factor_weights"
    category = "factor"
    description = ("对一组因子成员训练组合权重（linear/equal/ic_weight/random_forest/xgboost 均恒可用："
                  "rf/xgb 缺可选依赖时自动回落内置树集成、装库则加速，经 trainerVersion 如实回报后端），"
                  "返回权重与样本内/外复合得分 IC（样本外显著低于样本内 = 过拟合风险）。")
    input_schema = {
        "type": "object",
        "properties": {
            "members": {"type": "array", "description": "成员 [{factorKey,direction(long/short),params?,weight?}]",
                        "items": {"type": "object"}},
            "method": {"type": "string", "enum": list(_METHOD_ENUM), "description": "默认 linear"},
            "instId": {"type": "string"},
            "bar": {"type": "string", "enum": list(BAR_ENUM)},
            "startTs": {"type": "integer"},
            "endTs": {"type": "integer"},
            "horizon": {"type": "integer", "description": "标签前瞻根数（默认 6）"},
        },
        "required": ["members", "instId", "bar", "startTs", "endTs"],
    }

    def validate(self, args: dict) -> str | None:
        members = args.get("members")
        if not isinstance(members, list) or not members:
            return "members 必须为非空数组"
        return first_error(
            require_str(args, "instId"), check_enum(args, "bar", BAR_ENUM, required=True),
            check_enum(args, "method", _METHOD_ENUM), check_required_range(args))

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        _ = ctx
        window = FactorWindow(inst_id=args["instId"], bar=args["bar"],
                              start_ts=args["startTs"], end_ts=args["endTs"])
        result = await factor_service.train(
            get_rest_client(), window, members=args["members"], method=args.get("method", "linear"),
            config={}, horizon=int(args.get("horizon", 6)))
        metrics = result["metrics"]
        weight_txt = ", ".join(f"{k}={v}" for k, v in result["weights"].items())
        summary = (f"训练完成（method={result['method']}）：权重 {weight_txt}；"
                   f"样本内 IC={metrics.get('icIn')}，样本外 IC={metrics.get('icOut')}"
                   f"（n_train={metrics.get('nTrain')}, n_valid={metrics.get('nValid')}）。")
        return ToolResult(True, summary, payload=result)


class ScoreFactorComboTool(Tool):
    name = "score_factor_combo"
    category = "factor"
    description = "对某个已保存的因子组合计算最近的复合得分（≥进多阈值偏多、≤-进空阈值偏空），用于查看当前打分。"
    input_schema = {
        "type": "object",
        "properties": {
            "factorSetId": {"type": "integer", "description": "已保存因子组合 id"},
            "instId": {"type": "string"},
            "bar": {"type": "string", "enum": list(BAR_ENUM)},
            "limit": {"type": "integer", "description": "取数根数（默认 300，硬上限 300）"},
        },
        "required": ["factorSetId", "instId", "bar"],
    }

    def validate(self, args: dict) -> str | None:
        return first_error(
            require_pos_int(args, "factorSetId"), require_str(args, "instId"),
            check_enum(args, "bar", BAR_ENUM, required=True), check_limit(args, 1, _MAX_LIMIT))

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        async with ctx.session_factory() as session:
            row = await repo.get_factor_set(session, args["factorSetId"])
        if row is None:
            return ToolResult(False, f"因子组合 #{args['factorSetId']} 不存在。")
        spec = resolve_set_to_spec(row)
        norm_window, clip = resolve_norm_contract(
            norm_stats=spec.get("normStats"), thresholds=spec["thresholds"])
        df = await _load_df(args)
        score = compose_score(df, spec["members"], None,
                              norm_window=norm_window, clip=clip,
                              squash=bool(spec["thresholds"].get("squash", True)), bar=args["bar"])
        points = score_points(df, score)
        latest = points[-1]["score"] if points else None
        summary = (f"因子组合「{row.name}」在 {args['instId']} {args['bar']} 最近复合得分 {latest}"
                   f"（{len(points)} 个有效点 / {len(df)} 根）。")
        return ToolResult(True, summary, payload={"factorSetId": row.id, "name": row.name,
                                                  "latest": latest, "validPoints": len(points)})


class SaveFactorSetTool(Tool):
    name = "save_factor_set"
    category = "factor"
    description = ("把一组（通常已训练的）因子成员持久化为可复用的因子组合，闭合「挖掘→评估→训练→保存」链路；"
                  "成员方向须冻结 long/short，校验与 REST 完全同轨（非法成员/契约漂移即如实回报）。")
    input_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "组合名称"},
            "members": {"type": "array", "description": "成员 [{factorKey,direction(long/short),params?,weight?}]（1..12）",
                        "items": {"type": "object"}},
            "method": {"type": "string", "enum": list(_METHOD_ENUM), "description": "训练方法（默认 equal）"},
            "modelJson": {"type": "object", "description": "训练产物（weights/normStats/provenance/trainerVersion）"},
            "config": {"type": "object", "description": "组合配置（含 thresholds）"},
            "metricsJson": {"type": "object", "description": "训练复合 IC 等指标"},
            "enabled": {"type": "boolean", "description": "是否启用（默认 true）"},
        },
        "required": ["name", "members"],
    }

    def validate(self, args: dict) -> str | None:
        members = args.get("members")
        if not isinstance(members, list) or not members:
            return "members 必须为非空数组"
        return first_error(require_str(args, "name"), check_enum(args, "method", _METHOD_ENUM))

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        method = args.get("method", "equal")
        async with ctx.session_factory() as session:
            result = await factor_service.create_set(
                session, name=args["name"], members_raw=args["members"], method=method,
                config=args.get("config"), model=args.get("modelJson"),
                metrics=args.get("metricsJson"), enabled=bool(args.get("enabled", True)))
        summary = (f"已保存组合「{result['name']}」(#{result['id']})，成员 {len(result['members'])} 个，"
                   f"方法 {result['method']}。")
        return ToolResult(True, summary, payload=result)


class CreateFactorStrategyTool(Tool):
    name = "create_factor_strategy"
    category = "factor"
    description = ("以内联快照部署一个 factor_score 策略，使其立即可在回测/对比/Walk-forward 中使用，闭合"
                  "「保存→部署」链路；成员方向须冻结、权重已烘焙，normStats/provenance 仅记录不参与打分。")
    input_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "策略名称"},
            "members": {"type": "array", "description": "成员 [{factorKey,direction(long/short),params?,weight}]（1..12）",
                        "items": {"type": "object"}},
            "thresholds": {"type": "object",
                           "description": "交易阈值 enterLong/enterShort/exitBand/normWindow/squash（缺省取默认）"},
            "normStats": {"type": "object", "description": "归一化契约 {normWindow,clip}（供打分取同窗口）"},
            "provenance": {"type": "object", "description": "训练溯源（仅 UI 展示训练区间，绝不参与打分）"},
        },
        "required": ["name", "members"],
    }

    def validate(self, args: dict) -> str | None:
        members = args.get("members")
        if not isinstance(members, list) or not members:
            return "members 必须为非空数组"
        return require_str(args, "name")

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        # 复用 R1 边界校验单源（冻结方向 / 已知 key / 成员数）；非法即 HTTPException → registry 收敛 ok=False。
        validate_factor_strategy_params({"members": args["members"]})
        params: dict = {"members": args["members"],
                        "thresholds": normalize_thresholds(args.get("thresholds"))}
        if args.get("normStats") is not None:
            params["normStats"] = args["normStats"]
        if args.get("provenance") is not None:
            params["provenance"] = args["provenance"]
        async with ctx.session_factory() as session:
            strategy = await repo.create_strategy(session, "factor_score", args["name"], params)
        summary = (f"已创建 factor_score 策略「{strategy['name']}」(#{strategy['id']})，"
                   f"已可在回测/对比中使用。")
        return ToolResult(True, summary, payload={"strategyId": strategy["id"], "name": strategy["name"]})


class ShowFactorTool(Tool):
    name = "show_factor"
    category = "factor"
    description = "把某因子值或某因子组合的复合得分作为振荡 pane 叠加到左侧主图。未指定的字段沿用当前工作台上下文。"
    is_ui_action = True
    input_schema = {
        "type": "object",
        "properties": {
            "factorKey": {"type": "string", "enum": list(FACTOR_KEY_ENUM),
                          "description": "要叠加的单因子（与 factorSetId 二选一）"},
            "factorSetId": {"type": "integer", "description": "要叠加复合得分的因子组合 id"},
            "instId": {"type": "string", "description": "如 BTC-USDT-SWAP（可选）"},
            "bar": {"type": "string", "enum": list(BAR_ENUM), "description": "周期（可选）"},
        },
    }

    def validate(self, args: dict) -> str | None:
        return first_error(
            check_enum(args, "factorKey", FACTOR_KEY_ENUM), check_enum(args, "bar", BAR_ENUM))

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        directive: dict = {"action": "show_factor"}
        for key in ("factorKey", "factorSetId", "instId", "bar"):
            if args.get(key) is not None:
                directive[key] = args[key]
        target = directive.get("factorKey") or (
            f"组合 #{directive['factorSetId']}" if "factorSetId" in directive else "因子")
        inst = directive.get("instId") or (ctx.ui.instId if ctx.ui else None) or "当前标的"
        return ToolResult(True, f"已在 {inst} 主图叠加 {target} 的因子 pane。", payload=directive)
