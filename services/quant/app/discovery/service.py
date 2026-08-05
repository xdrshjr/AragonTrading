"""发现层 REST 编排（strategy-factor-discovery §2.14 / §四）—— 四道闸 + 配置归一化 + 投影。

**守门序固定**：403（总闸）→ 400（参数）→ 413（工作量）→ 409（并发）。
``manager.reserve()`` 的同步预占放在**最后**，避免参数错也白占一个名额；预占之后的任何一步
抛错都必须 ``release()`` 归还（try/except 兜底，绝不泄漏名额）。
"""
from __future__ import annotations

import json
import logging
import time

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..engine.mtf import bar_to_seconds
from .axis_budget import AXIS_LABEL_CN, plan_axis_budget
from .catalogs import minable_templates
from .evolution import DEFAULT_BUDGET, normalize_budget
from .expression_text import vocabulary
from .genomes import ALL_AXES, IMPLEMENTED_AXES
from .mechanisms import mechanism_catalog
from . import repositories as repo
from .manager import get_discovery_manager
from .runtime import RunSpec
from .strategy_gates import DEFAULT_STRATEGY_THRESHOLDS, normalize_strategy_thresholds
from .validation import DEFAULT_THRESHOLDS, normalize_thresholds

logger = logging.getLogger("aragon")

BAR_ENUM = ("1m", "5m", "15m", "30m", "1H", "4H", "1D")
MIN_WINDOW_BARS = 200
DEFAULT_EVOLUTION = {"budget": dict(DEFAULT_BUDGET), "adaptEvery": 10, "fsaEvery": 25,
                     "fsaThreshold": 0.15, "fsaBanTtlIters": 150, "variantCap": 6,
                     "coldStartMin": 5}
DEFAULT_LLM = {"enabled": True, "reviewSample": 5, "maxCallsPerHour": 24, "timeoutS": 60}

# 一次回测比一次表达式求值贵一到两个数量级（§六）。沿用旧口径（候选数 × 根数）会让
# 「40 个组合候选」与「40 个因子候选」被判成同样的工作量，413 这道闸对策略轴形同虚设。
# ⚠️ ``factor`` 的系数必须是 1：factor 单轴的 workload 由此与既有实现**逐位相同**（评审 P2-7）。
AXIS_COST: dict[str, int] = {"factor": 1, "strategy_param": 8, "factor_combo": 10,
                             "strategy_combo": 20}
COMBO_SIZE_BOUNDS = (2, 5)
ROTATION_SIZE_BOUNDS = (2, 4)
DEFAULT_COMBO_SIZE = {"min": 2, "max": 5}
DEFAULT_ROTATION_SIZE = {"min": 2, "max": 4}
AXIS_INFO: dict[str, dict] = {
    "factor": {"label": "因子", "desc": "在价量表达式空间里搜索单因子",
               "backtestsPerCandidate": 0, "dependsOn": []},
    "factor_combo": {"label": "因子组合策略", "desc": "把因子装成带权重与阈值的组合",
                     "backtestsPerCandidate": 2, "dependsOn": []},
    "strategy_param": {"label": "常规策略", "desc": "在内置策略模板的参数空间里搜索",
                       "backtestsPerCandidate": 2, "dependsOn": []},
    "strategy_combo": {"label": "策略轮动组合", "desc": "把已挖到的策略装进轮动模板",
                       "backtestsPerCandidate": 4, "dependsOn": ["strategy_param"]},
}


def _bad(code: str, message: str, status: int = 400) -> HTTPException:
    return HTTPException(status, detail={"code": code, "message": message})


# ============================ 配置归一化 ============================
def normalize_config(body: dict) -> dict:
    """把请求体夹取为**完整**的归一化配置快照（落 ``config_json``，是回溯真源）。

    夹取而非拒绝的字段（间隔 / 候选数 / 窗口根数）走 settings 的上下界；
    语义性错误（未知 bar / 未实现的轴 / 空轴集）一律 400，**绝不**静默改写用户意图。
    """
    settings = get_settings()
    bar = str(body.get("bar") or settings.default_bar)
    if bar not in BAR_ENUM:
        raise _bad("DISCOVERY_BAR_UNSUPPORTED", f"不支持的周期：{bar}")
    axes = _normalize_axes(body.get("axes"))
    window_bars = _clamp_int(body.get("windowBars", 4000), MIN_WINDOW_BARS,
                             int(settings.discovery_max_window_bars))
    return {
        "windowBars": window_bars,
        "axes": axes,
        "axisWeights": _normalize_axis_weights(body.get("axisWeights"), axes),
        "templates": _normalize_templates(body.get("templates"), bar, axes),
        "includeLibraryFactors": bool(body.get("includeLibraryFactors", True)),
        "comboSize": _normalize_size(body.get("comboSize"), DEFAULT_COMBO_SIZE,
                                     COMBO_SIZE_BOUNDS, "comboSize"),
        "rotationSize": _normalize_size(body.get("rotationSize"), DEFAULT_ROTATION_SIZE,
                                        ROTATION_SIZE_BOUNDS, "rotationSize"),
        "strategyThresholds": normalize_strategy_thresholds(body.get("strategyThresholds")),
        "maxKeptPerAxis": _clamp_int(body.get("maxKeptPerAxis",
                                              body.get("maxKept", 200)), 1, 2000),
        "horizon": _clamp_int(body.get("horizon", 6), 1, 100),
        "candidatesPerIteration": _clamp_int(
            body.get("candidatesPerIteration", 40), 1,
            int(settings.discovery_max_candidates_per_iter)),
        "iterationIntervalS": _clamp_float(
            body.get("iterationIntervalS", settings.discovery_default_interval_s),
            settings.discovery_min_interval_s, settings.discovery_max_interval_s),
        "dataRefreshS": max(0.0, _to_float(body.get("dataRefreshS", 21600.0), 21600.0)),
        "windowMode": "fixed" if str(body.get("windowMode")) == "fixed" else "rolling",
        "isRatio": _clamp_float(body.get("isRatio", 0.7), 0.5, 0.9),
        "maxIterations": max(0, _to_int(body.get("maxIterations", 0), 0)),
        "maxRuntimeHours": max(0.0, _to_float(body.get("maxRuntimeHours", 0.0), 0.0)),
        "maxKept": _clamp_int(body.get("maxKept", 200), 1, 2000),
        "autoResume": bool(body.get("autoResume", True)),
        "autoPromote": bool(body.get("autoPromote", False)),
        "revalidateOnRefresh": bool(body.get("revalidateOnRefresh", True)),
        "llm": _normalize_llm(body.get("llm")),
        "thresholds": normalize_thresholds(body.get("thresholds")),
        "evolution": _normalize_evolution(body.get("evolution")),
        "seed": _to_int(body.get("seed", 0), 0) or int(time.time()),
    }


def _normalize_axes(raw: object) -> list[str]:
    """轴集合校验。

    ``DISCOVERY_AXIS_DEPENDENCY`` 是**无条件**的（评审 P1-7）：轮动成员只来自本 run
    ``strategy_param`` 轴的 kept 池（D-18），根本没有「从 strategies 表取材」的路径。
    若按「库里有策略就放行」实现，会出现「放行启动 ⇒ 该轴 readiness 恒 false ⇒ 每轮整体让渡
    ⇒ 一个候选都不产」——正是 BL-9 明令禁止的空转轴。
    """
    axes = [str(a) for a in (raw or ["factor"])]
    if not axes:
        raise _bad("DISCOVERY_AXES_REQUIRED", "至少选择一条搜索轴")
    for axis in axes:
        if axis not in ALL_AXES:
            raise _bad("DISCOVERY_AXIS_UNSUPPORTED", f"未知搜索轴：{axis}")
        if axis not in IMPLEMENTED_AXES:
            raise _bad("DISCOVERY_AXIS_UNSUPPORTED",
                       f"搜索轴「{axis}」尚未实现，当前仅支持：{', '.join(IMPLEMENTED_AXES)}")
    if "strategy_combo" in axes and "strategy_param" not in axes:
        raise _bad("DISCOVERY_AXIS_DEPENDENCY",
                   "「策略轮动组合」的成员只来自本任务「常规策略」轴的产出，请同时勾选常规策略轴")
    return sorted(set(axes), key=ALL_AXES.index)


def _normalize_axis_weights(raw: object, axes: list[str]) -> dict:
    data = raw if isinstance(raw, dict) else {}
    return {axis: max(0.0, _to_float(data.get(axis, 1.0), 1.0)) or 1.0 for axis in axes}


def _normalize_templates(raw: object, bar: str, axes: list[str]) -> list[str]:
    """模板白名单（空 / 缺省 = 全部可挖模板）；含不可挖模板一律 400 并带原因。"""
    keys = [str(item) for item in (raw or []) if str(item)]
    if not keys or "strategy_param" not in axes:
        return []
    catalog = minable_templates(bar, max_htf_frames=int(
        get_settings().discovery_max_htf_frames))
    by_key = {spec.key: spec for spec in catalog.templates}
    for key in keys:
        spec = by_key.get(key)
        if spec is None:
            raise _bad("DISCOVERY_TEMPLATE_UNSUPPORTED", f"未知策略模板：{key}")
        if not spec.minable:
            raise _bad("DISCOVERY_TEMPLATE_UNSUPPORTED",
                       f"模板「{spec.name}」不可挖：{spec.reason}")
    return sorted(set(keys))


def _normalize_size(raw: object, default: dict, bounds: tuple, field: str) -> dict:
    """组合 / 轮动规模区间；越界一律 400（**不夹取**——静默改写用户意图会挖出别的东西）。"""
    data = raw if isinstance(raw, dict) else {}
    low = _to_int(data.get("min", default["min"]), default["min"])
    high = _to_int(data.get("max", default["max"]), default["max"])
    if not (bounds[0] <= low <= high <= bounds[1]):
        raise _bad("DISCOVERY_COMBO_SIZE",
                   f"{field} 须落在 {bounds[0]}..{bounds[1]} 且 min ≤ max（当前 {low}..{high}）")
    return {"min": low, "max": high}


def _normalize_llm(raw: object) -> dict:
    data = raw if isinstance(raw, dict) else {}
    settings = get_settings()
    return {"enabled": bool(data.get("enabled", True)),
            "reviewSample": _clamp_int(data.get("reviewSample", 5), 0, 20),
            "maxCallsPerHour": _clamp_int(
                data.get("maxCallsPerHour", settings.discovery_llm_max_calls_per_hour),
                0, 600),
            "timeoutS": _clamp_float(data.get("timeoutS", settings.discovery_llm_timeout_s),
                                     5.0, 300.0)}


def _normalize_evolution(raw: object) -> dict:
    data = raw if isinstance(raw, dict) else {}
    return {"budget": normalize_budget(data.get("budget")),
            "adaptEvery": _clamp_int(data.get("adaptEvery", 10), 1, 500),
            "fsaEvery": _clamp_int(data.get("fsaEvery", 25), 1, 500),
            "fsaThreshold": _clamp_float(data.get("fsaThreshold", 0.15), 0.01, 0.9),
            "fsaBanTtlIters": max(0, _to_int(data.get("fsaBanTtlIters", 150), 150)),
            "variantCap": _clamp_int(data.get("variantCap", 6), 1, 50),
            "coldStartMin": _clamp_int(data.get("coldStartMin", 5), 1, 100)}


def _to_int(value: object, default: int) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _to_float(value: object, default: float) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _clamp_int(value: object, low: int, high: int) -> int:
    return max(int(low), min(int(high), _to_int(value, int(low))))


def _clamp_float(value: object, low: float, high: float) -> float:
    return max(float(low), min(float(high), _to_float(value, float(low))))


# ============================ 启动（四道闸） ============================
def workload_of(config: dict) -> int:
    """工作量单位 = 窗口根数 × Σ(轴名额 × 轴代价系数)（§六新口径）。

    对 **factor 单轴恒等**于旧口径（``AXIS_COST["factor"] == 1``，评审 P2-7）：这是本轮
    「不改变既有 413 行为」的承诺，配一条等价用例守门。
    """
    axes = list(config.get("axes") or ["factor"])
    count = int(config["candidatesPerIteration"])
    shares = plan_axis_budget(axes, config.get("axisWeights") or {}, count,
                              {axis: (True, "") for axis in axes}).shares
    weighted = sum(int(slots) * AXIS_COST.get(axis, 1) for axis, slots in shares.items())
    return int(config["windowBars"]) * weighted


async def start_run(session: AsyncSession, rest, body: dict) -> dict:
    """启动挖掘任务；守门序 403 → 400 → 413 → 409（``reserve()`` 放最后）。"""
    settings = get_settings()
    if not bool(settings.discovery_enabled):                       # 闸1：总闸
        raise _bad("DISCOVERY_DISABLED", "策略与因子挖掘已被管理员关闭", 403)
    config = normalize_config(body)                                # 闸2：参数
    inst_id = str(body.get("instId") or settings.default_inst).strip()
    if not inst_id:
        raise _bad("DISCOVERY_INST_REQUIRED", "请选择挖掘标的")
    if workload_of(config) > int(settings.discovery_work_unit_limit):   # 闸3：工作量
        raise _bad("DISCOVERY_TOO_LARGE",
                   f"单轮工作量 {workload_of(config)} 超出上限 "
                   f"{settings.discovery_work_unit_limit}，请减少每轮候选数、缩短数据窗口，"
                   "或减少同时开启的搜索轴", 413)
    manager = get_discovery_manager()
    if not manager.reserve():                                      # 闸4：并发（同步预占）
        raise _bad("DISCOVERY_BUSY",
                   f"已有挖掘任务在运行（上限 {settings.discovery_max_runs}），"
                   "请先暂停或停止后再启动", 409)
    try:
        row = await _create_and_launch(session, rest, inst_id, config, body)
    except Exception:
        manager.release()      # 预占之后任何一步失败都必须归还名额，绝不泄漏
        raise
    return {"runId": int(row.id), "status": row.status, "config": config,
            "startedTs": int(row.started_ts)}


async def _create_and_launch(session: AsyncSession, rest, inst_id: str, config: dict,
                             body: dict):
    bar = str(body.get("bar") or get_settings().default_bar)
    label = str(body.get("label") or f"{inst_id} {bar} 挖掘")[:128]
    row = await repo.create_run(session, values=repo.RunValues(
        label=label, inst_id=inst_id, bar=bar, symbols=[inst_id], config=config,
        checkpoint={"version": 1, "iteration": 0, "rngSeed": int(config["seed"]),
                    "budget": config["evolution"]["budget"]},
        started_ts=int(time.time())))
    client = await _resolve_client(config)
    get_discovery_manager().start(RunSpec(
        run_id=int(row.id), inst_id=inst_id, bar=bar, symbols=(inst_id,), config=config,
        rest=rest, settings=get_settings(), resume=False, llm_client=client))
    return row


async def _resolve_client(config: dict):
    from ..db.session import get_sessionmaker
    from .llm_agents import resolve_llm_client

    if not bool((config.get("llm") or {}).get("enabled", True)):
        return None
    return await resolve_llm_client(get_sessionmaker(), get_settings())


# ============================ 暂停 / 恢复 / 停止 / 删除 ============================
async def pause_run(session: AsyncSession, run_id: int) -> dict:
    row = await _require_run(session, run_id)
    if row.status in repo.TERMINAL_STATUSES:
        return {"runId": run_id, "status": row.status}
    get_discovery_manager().pause(run_id, "user")
    await repo.set_run_status(session, run_id, status="paused", pause_reason="user")
    return {"runId": run_id, "status": "paused"}


async def resume_run(session: AsyncSession, rest, run_id: int) -> dict:
    """从检查点续跑：循环**还活着**就直接放行标志；否则重新装配 run（``resume=True``）。

    重装配路径会先跑 §2.10 的 kept 池重建（``rebuilding=true``），重建失败即停在 ``paused``，
    **绝不**带残缺池开跑。

    ⚠️ 判活必须看 ``is_alive`` 而非 ``is_terminal``：``_prepare`` 阶段取数失败 / 重建失败转的
    ``paused`` 会让循环协程直接返回，此时对象还在注册表里且非终态，只翻标志等于什么都没做——
    接口却回 ``running``。它还占着并发名额，故重装配前必须先 ``drop``。
    """
    settings = get_settings()
    if not bool(settings.discovery_enabled):
        raise _bad("DISCOVERY_DISABLED", "策略与因子挖掘已被管理员关闭", 403)
    row = await _require_run(session, run_id)
    if row.status in repo.TERMINAL_STATUSES:
        raise _bad("DISCOVERY_NOT_RESUMABLE", f"任务已处于终态（{row.status}），无法恢复", 409)
    manager = get_discovery_manager()
    existing = manager.get(run_id)
    if existing is not None and not existing.is_terminal and existing.is_alive:
        manager.resume(run_id)
        await repo.set_run_status(session, run_id, status="running", pause_reason="")
        return {"runId": run_id, "status": "running"}
    if existing is not None:
        manager.drop(run_id)      # 循环已退出的空壳不得再占并发名额
    if not manager.reserve():
        raise _bad("DISCOVERY_BUSY", "并发挖掘任务已达上限，请先停止其他任务", 409)
    try:
        await _relaunch(session, rest, row)
    except Exception:
        manager.release()
        raise
    return {"runId": run_id, "status": "running"}


async def _relaunch(session: AsyncSession, rest, row) -> None:
    config = json.loads(row.config_json or "{}") or normalize_config({})
    client = await _resolve_client(config)
    await repo.set_run_status(session, int(row.id), status="running", pause_reason="")
    get_discovery_manager().start(RunSpec(
        run_id=int(row.id), inst_id=row.inst_id, bar=row.bar,
        symbols=tuple(json.loads(row.symbols_json or "[]") or [row.inst_id]),
        config=config, rest=rest, settings=get_settings(), resume=True, llm_client=client))


async def stop_run(session: AsyncSession, run_id: int) -> dict:
    row = await _require_run(session, run_id)
    if row.status in repo.TERMINAL_STATUSES:
        return {"runId": run_id, "status": row.status}
    get_discovery_manager().stop(run_id)
    await repo.set_run_status(session, run_id, status="stopped", pause_reason="user",
                              finished_ts=int(time.time()))
    return {"runId": run_id, "status": "stopped"}


async def delete_run(session: AsyncSession, run_id: int) -> str:
    row = await repo.get_run(session, run_id)
    if row is None:
        return "not_found"
    manager = get_discovery_manager()
    if run_id in manager.active_run_ids() or row.status == "running":
        return "running"
    manager.drop(run_id)
    return await repo.delete_run(session, run_id)


async def _require_run(session: AsyncSession, run_id: int):
    row = await repo.get_run(session, run_id)
    if row is None:
        raise _bad("NOT_FOUND", "挖掘任务不存在", 404)
    return row


# ============================ 投影 ============================
def _loads(raw: str | None, fallback):
    try:
        value = json.loads(raw or "")
    except (json.JSONDecodeError, TypeError):
        return fallback
    return value if isinstance(value, type(fallback)) else fallback


def run_summary(row) -> dict:
    """任务列表投影（内存态优先：在跑的 run 以运行时快照为准，避免列表显示滞后）。"""
    live = get_discovery_manager().get(int(row.id))
    status = live.status if live is not None and not live.is_terminal else row.status
    pause_reason = live.pause_reason if live is not None and not live.is_terminal \
        else row.pause_reason
    return {
        "runId": int(row.id), "label": row.label, "instId": row.inst_id, "bar": row.bar,
        "status": status, "pauseReason": pause_reason, "iteration": int(row.iteration),
        "keptCount": int(row.kept_count), "testedCount": int(row.tested_count),
        "counters": _loads(row.counters_json, {}),
        "startedTs": int(row.started_ts),
        "lastIterTs": int(row.last_iter_ts) if row.last_iter_ts else None,
        "finishedTs": int(row.finished_ts) if row.finished_ts else None,
        "errorCode": row.error_code, "errorMessage": row.error_message,
    }


def run_detail(row, iterations: list) -> dict:
    live = get_discovery_manager().get(int(row.id))
    return {**run_summary(row),
            "config": _loads(row.config_json, {}),
            "checkpoint": _loads(row.checkpoint_json, {}),
            "state": live.state_snapshot() if live is not None else None,
            "iterations": [iteration_summary(item) for item in iterations]}


def iteration_summary(row) -> dict:
    return {"idx": int(row.idx), "startedTs": int(row.started_ts),
            "finishedTs": int(row.finished_ts), "budget": _loads(row.budget_json, {}),
            **_loads(row.stats_json, {}), "llm": _loads(row.llm_json, {}), "note": row.note}


def candidate_summary(row) -> dict:
    metrics = _loads(row.metrics_json, {})
    return {"id": int(row.id), "runId": int(row.run_id), "iterationIdx": int(row.iteration_idx),
            "kind": row.kind, "genomeHash": row.genome_hash, "exprText": row.expr_text,
            "describe": row.describe_text, "skeleton": row.skeleton,
            "mechanism": row.mechanism, "source": row.source, "verdict": row.verdict,
            "rejectReason": row.reject_reason, "demoted": bool(row.demoted),
            "trialsAtAccept": int(row.trials_at_accept),
            "promotedKind": row.promoted_kind,
            "promotedId": int(row.promoted_id) if row.promoted_id else None,
            "createdTs": int(row.created_ts), "metrics": metrics}


def candidate_detail(row) -> dict:
    return {**candidate_summary(row), "series": _loads(row.series_json, {}),
            "genome": _loads(row.genome_json, {})}


def config_defaults() -> dict:
    """「恢复默认」的唯一真源 + ``implementedAxes``（D-6：启动器只渲染这里回的轴）。"""
    settings = get_settings()
    return {
        "implementedAxes": list(IMPLEMENTED_AXES), "allAxes": list(ALL_AXES),
        "bars": list(BAR_ENUM),
        "windowBars": {"default": 4000, "min": MIN_WINDOW_BARS,
                       "max": int(settings.discovery_max_window_bars)},
        "candidatesPerIteration": {"default": 40, "min": 1,
                                   "max": int(settings.discovery_max_candidates_per_iter)},
        "iterationIntervalS": {"default": float(settings.discovery_default_interval_s),
                               "min": float(settings.discovery_min_interval_s),
                               "max": float(settings.discovery_max_interval_s)},
        "horizon": {"default": 6, "min": 1, "max": 100},
        "dataRefreshS": {"default": 21600.0, "min": 0.0},
        "maxKept": {"default": 200, "min": 1, "max": 2000},
        "maxKeptPerAxis": {"default": 200, "min": 1, "max": 2000},
        "axisInfo": {axis: {**AXIS_INFO[axis], "cost": AXIS_COST[axis]} for axis in ALL_AXES},
        "axisLabels": dict(AXIS_LABEL_CN),
        "templates": [spec.to_dict() for spec in
                      minable_templates(settings.default_bar, max_htf_frames=int(
                          settings.discovery_max_htf_frames)).templates],
        "strategyThresholds": dict(DEFAULT_STRATEGY_THRESHOLDS),
        "comboSize": {"default": dict(DEFAULT_COMBO_SIZE), "min": COMBO_SIZE_BOUNDS[0],
                      "max": COMBO_SIZE_BOUNDS[1]},
        "rotationSize": {"default": dict(DEFAULT_ROTATION_SIZE),
                         "min": ROTATION_SIZE_BOUNDS[0], "max": ROTATION_SIZE_BOUNDS[1]},
        "thresholds": dict(DEFAULT_THRESHOLDS),
        "evolution": {**DEFAULT_EVOLUTION, "budget": dict(DEFAULT_BUDGET)},
        "llm": {**DEFAULT_LLM, "enabled": bool(settings.discovery_llm_enabled),
                "maxCallsPerHour": int(settings.discovery_llm_max_calls_per_hour),
                "timeoutS": float(settings.discovery_llm_timeout_s)},
        "workUnitLimit": int(settings.discovery_work_unit_limit),
        "enabled": bool(settings.discovery_enabled),
        "maxRuns": int(settings.discovery_max_runs),
    }


def vocabulary_payload() -> dict:
    """字段 / 算子 / 机制族词表（UI 教学提示与 prompt 同源）。"""
    return {**vocabulary(), "mechanisms": mechanism_catalog(),
            "note": "报告中的收益数字不构成本功能的预期收益；所有指标均为历史样本统计。"}


def estimate_window(bar: str, window_bars: int) -> dict:
    """窗口时间跨度预估（启动器实时显示「大约覆盖 N 天」，避免用户凭感觉填根数）。"""
    seconds = bar_to_seconds(bar) * max(0, int(window_bars))
    return {"bars": int(window_bars), "seconds": int(seconds),
            "days": round(seconds / 86400.0, 2)}
