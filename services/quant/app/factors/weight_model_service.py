"""权重模型 / 训练记录 service 编排（weight-model-workspace §3.3）。

模型 CRUD 校验编排 + spec 组装 + 训练启动守门（**守门优先、全过后才持久化**，R-01）与模型去重解析
（R-02）。流式作业归 ``weight_train_manager``（manager 只持注册表，作业续跑与 socket 解耦）。取数 / 计算
全程真实 OKX，绝不假数据、绝不静默降级。
"""
from __future__ import annotations

import json

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import repositories as repo
from ..db.models import WeightModel
from ..engine.mtf import bar_to_seconds
from ..schemas import (
    WeightModelCreateReq,
    WeightModelUpdateReq,
    WeightTrainStartReq,
    weight_backtest_out,
    weight_model_out,
    weight_train_run_out,
)
from .service import FactorWindow, _effective_symbols, _load_df  # noqa: F401  —— 复用进程内 K 线缓存 + 标的规整
from .specs import apply_weights, assert_weights_cover, normalize_thresholds, validate_members
from .weight_train_manager import JobSpec, get_weight_train_manager


def _guard_train_symbols(symbols: list[str]) -> None:
    """训练标的数超 ``max_train_symbols`` → 400（**取数前**判定，绝不无界扇出 OKX 请求，factor-lab-symbol-groups C-1）。"""
    limit = get_settings().max_train_symbols
    if len(symbols) > limit:
        raise HTTPException(400, detail={
            "code": "FACTOR_TRAIN_TOO_MANY_SYMBOLS",
            "message": f"标的组过大：训练最多支持 {limit} 个标的，请缩减标的组后重试。"})


def _inject_symbols(config: dict | None, symbols: list[str], group_id: str | None) -> dict:
    """把标的组成员与来源组注入模型 ``config``（落 config_json，零 DB 迁移，§3.5）；新值覆盖原 config 同名键。"""
    return {**(config or {}), "symbols": list(symbols), "symbolGroupId": group_id}


def _load_json(raw: str | None, fallback):
    """JSON 字段安全解析（缺 / 空 / 坏 JSON 回落，与 schemas._load_json 同容错口径）。"""
    try:
        value = json.loads(raw) if raw else fallback
    except (json.JSONDecodeError, TypeError):
        return fallback
    return value


# ============================ 模型 CRUD ============================
async def _model_out(session: AsyncSession, row: WeightModel) -> dict:
    """模型行 → 前端 dict，并按 ``latest_run_id`` 附最近成功训练记录概要（无则 ``None``）。"""
    latest = None
    if row.latest_run_id is not None:
        latest = await repo.get_weight_run(session, row.id, row.latest_run_id)
    return weight_model_out(row, latest_run=latest)


async def list_models(session: AsyncSession, *, enabled: bool | None = None) -> list[dict]:
    rows = await repo.list_weight_models(session, enabled=enabled)
    return [await _model_out(session, row) for row in rows]


async def get_model(session: AsyncSession, model_id: int) -> dict | None:
    row = await repo.get_weight_model(session, model_id)
    return await _model_out(session, row) if row is not None else None


async def create_model(session: AsyncSession, req: WeightModelCreateReq) -> dict:
    """手动新建模型（编辑器）：成员边界校验（400）→ bar 校验（400，H-B5）→ 落库 → 序列化。

    标的组（``symbols``）注入 ``config_json`` 且 ``inst_id`` 取焦点（组内首个），零 DB 迁移（§3.5）；
    缺省回落 ``[instId]`` 单标的（行为不变）。
    """
    members = validate_members([m.model_dump() for m in req.members])
    _validate_bar(req.bar)
    _validate_train_config(req.config or {})   # G-B10：手动建模型亦校验 config，杜绝非法 config 落库
    symbols = _effective_symbols(req.symbols, req.instId)
    _guard_train_symbols(symbols)
    config = _inject_symbols(req.config, symbols, req.symbolGroupId)
    spec = repo.WeightModelSpec(
        name=req.name, inst_id=symbols[0], bar=req.bar, members=members, method=req.method,
        config=config, horizon=req.horizon, source="manual",
        factor_set_id=req.factorSetId, notes=req.notes, enabled=req.enabled)
    row = await repo.create_weight_model(session, spec=spec)
    return await _model_out(session, row)


async def update_model(session: AsyncSession, model_id: int, req: WeightModelUpdateReq) -> dict | None:
    members = validate_members([m.model_dump() for m in req.members]) if req.members is not None else None
    if req.bar is not None:
        _validate_bar(req.bar)   # H-B5：手动改模型亦校验 bar 白名单
    if req.config is not None:
        _validate_train_config(req.config)   # G-B10：改模型 config 亦校验
    inst_id = req.instId
    config = req.config
    # 标的组显式变更（§3.5）：把组成员注入 config、焦点作为 inst_id；未变更则保持既有 patch 语义（None=不改）。
    if req.symbols is not None or req.symbolGroupId is not None:
        symbols = _effective_symbols(req.symbols, req.instId or (req.symbols[0] if req.symbols else ""))
        if symbols and symbols[0]:
            _guard_train_symbols(symbols)
            config = _inject_symbols(req.config, symbols, req.symbolGroupId)
            inst_id = symbols[0]
    patch = repo.WeightModelPatch(
        name=req.name, inst_id=inst_id, bar=req.bar, members=members, method=req.method,
        config=config, horizon=req.horizon, notes=req.notes, enabled=req.enabled)
    row = await repo.update_weight_model(session, model_id, patch=patch)
    return await _model_out(session, row) if row is not None else None


async def delete_model(session: AsyncSession, model_id: int) -> str:
    """删模型（G-B1/R-01）：真·在跑（manager 有非终态 job）→ ``"running"``（路由 409）；孤儿 running
    （DB=running 但无在跑 job）→ 删除路径就地对账 ``failed`` + repoint 再删；否则直接既有级联删。

    返回 ``"not_found"`` | ``"running"`` | ``"ok"``。
    """
    model = await repo.get_weight_model(session, model_id)
    if model is None:
        return "not_found"
    running = await repo.list_weight_runs(session, model_id, status="running")
    if running:
        alive = get_weight_train_manager().active_run_ids()
        if any(run.id in alive for run in running):
            return "running"                                                # 真·在跑 → 409
        await repo.fail_orphan_running_runs(session, model_id, alive=alive)  # 孤儿 → 就地对账 failed
        await session.commit()
    return await repo.delete_weight_model(session, model_id)                 # 已无 running → 既有级联删


# ============================ 训练记录读 / 删 ============================
async def list_runs(session: AsyncSession, model_id: int, *, limit: int | None,
                    status: str | None) -> list[dict] | None:
    """某模型的训练记录列表（不含完整 curve，仅 ``curveCount``）；模型不存在 → ``None``（路由转 404）。"""
    if await repo.get_weight_model(session, model_id) is None:
        return None
    rows = await repo.list_weight_runs(session, model_id, limit=limit, status=status)
    return [weight_train_run_out(row, with_curve=False) for row in rows]


async def get_run(session: AsyncSession, model_id: int, run_id: int) -> dict | None:
    """某条训练记录详情（含完整 curve / weights / metrics / provenance）；不存在 → ``None``。"""
    row = await repo.get_weight_run(session, model_id, run_id)
    return weight_train_run_out(row, with_curve=True) if row is not None else None


async def delete_run(session: AsyncSession, model_id: int, run_id: int) -> str:
    return await repo.delete_weight_run(session, model_id, run_id)


# ============================ 一键回测（组装 factor_score 快照 → 复用既有回测链路，不落库，weight-model-backtest-replay §2.2） ============================
async def backtest_run(session: AsyncSession, rest, model_id: int, run_id: int, *,
                       start_ts: int | None = None, end_ts: int | None = None,
                       initial_capital: float | None = None, fees: float | None = None,
                       slippage: float | None = None, leverage: float | None = None,
                       max_bars: int | None = None) -> dict | None:
    """对某模型某条**成功训练记录**的权重做一次性回测（同步、不落库）。

    把训练记录组装成 factor_score 自包含快照（成员 + 烘焙权重 + 归一化契约 + 阈值），局部 import 调
    ``agent.services.run_factor_score_backtest`` 复用既有 ``run_backtest`` 链路，再 ``weight_backtest_out`` 打包。
    区间缺省回落训练区间（样本内、确定性可复现）；``max_bars`` 由取数层静默钳制到 ``settings.backtest_max_bars``。

    Returns:
        ``{candles, trades, equity, metrics, meta}`` 打包体；模型或训练记录不存在 → ``None``（路由转 404）。

    Raises:
        HTTPException: 409 ``WEIGHT_RUN_NOT_BACKTESTABLE``（非 succeeded / 空权重 / 缺 normStats）；
            400 ``FACTOR_WEIGHTS_STALE``（成员漂移，``assert_weights_cover``）；400 ``EMPTY_RANGE``（透传）。
    """
    model = await repo.get_weight_model(session, model_id)
    if model is None:
        return None
    run = await repo.get_weight_run(session, model_id, run_id)
    if run is None:
        return None
    weights = _load_json(run.weights_json, {})
    norm_stats = _load_json(run.norm_stats_json, {})
    if run.status != "succeeded" or not weights:
        raise HTTPException(409, detail={
            "code": "WEIGHT_RUN_NOT_BACKTESTABLE",
            "message": "该训练记录未成功或未产出权重，无法回测，请改用一条成功的训练记录。"})
    if not norm_stats:   # P2-9：极旧无 normStats 记录保守拒绝，杜绝归一化窗口与训练期漂移
        raise HTTPException(409, detail={
            "code": "WEIGHT_RUN_NOT_BACKTESTABLE",
            "message": "该训练记录缺少归一化契约（normStats），请重新训练后再回测。"})

    # 成员读取优先候选自有组合（factor-combo-exploration §2.5/C-3）：探索候选成员=池子集，须用其自身组合回测；
    # run.members_json 为空（既有单次训练 / 多方法批次）时回落父模型成员——既有行为字节不变。
    members = _load_json(run.members_json, None) or _load_json(model.members_json, [])
    config = _load_json(model.config_json, {})
    thresholds = normalize_thresholds(config.get("thresholds") if isinstance(config, dict) else None)
    # 令阈值窗口与训练 normStats 自洽（冗余保险，resolve_norm_contract 本就优先 normStats.normWindow，P2-4）。
    if norm_stats.get("normWindow") is not None:
        thresholds["normWindow"] = norm_stats["normWindow"]
    assert_weights_cover(members, {"weights": weights})   # 成员漂移 → 400 FACTOR_WEIGHTS_STALE
    baked = apply_weights(members, weights)               # 权重烘焙进各成员 weight（自包含新式快照）
    params = {"members": baked, "thresholds": thresholds, "normStats": norm_stats}

    range_start = start_ts if start_ts is not None else run.start_ts
    range_end = end_ts if end_ts is not None else run.end_ts

    from ..agent import services as bt_services   # 局部 import 破潜在装载期环（方向本单向，加固仿 factor_score 延迟 import）
    candles, result, costs = await bt_services.run_factor_score_backtest(
        session, rest, params=params, inst_id=run.inst_id, bar=run.bar,
        start_ts=range_start, end_ts=range_end, initial_capital=initial_capital, fees=fees,
        slippage=slippage, leverage=leverage, limit=max_bars)
    meta = {
        "instId": run.inst_id, "bar": run.bar, "startTs": range_start, "endTs": range_end,
        "horizon": run.horizon, "effectiveCandleCount": len(candles), "initialCapital": costs[0],
    }
    return weight_backtest_out(candles, result, meta=meta)


# ============================ 训练启动（守门优先 → 去重解析 → 落库 → 注册作业，R-01/R-02） ============================
def _validate_bar(bar: str) -> None:
    """非白名单周期 → 400（H-B5）：``bar_to_seconds`` 对未知 bar 抛裸 ``ValueError``，在请求边界映射为 400，
    避免漏到 ``_guard_range``（自身调 ``bar_to_seconds``）里抛出无处理器的 500。"""
    try:
        bar_to_seconds(bar)
    except ValueError:
        raise HTTPException(400, detail={
            "code": "FACTOR_INVALID_BAR", "message": f"不支持的周期：{bar}"})


def _config_num(config: dict, key: str) -> float | None:
    """取 config 中数值键（缺省返回 None=合法、引擎用默认）；非数值 → 400（H-B5/R-01）。"""
    if key not in config:
        return None
    try:
        return float(config[key])
    except (TypeError, ValueError):
        raise HTTPException(400, detail={
            "code": "FACTOR_TRAIN_INVALID_CONFIG",
            "message": f"参数 {key} 必须为数值，收到：{config[key]!r}"})


def _validate_train_config(config: dict) -> None:
    """边界校验 config 中**真实且出现**的 camelCase 键（H-B5/R-01/G-B5）：``split∈(0,1)`` / ``l2≥0`` /
    ``nEstimators``·``maxDepth``·``normWindow`` 正整数；``clip`` 仅断言可转数值（``clip≤0`` 是关闭裁剪的
    合法哨兵，不限范围）。全部可选、缺省合法；非数值 / 越界在请求边界即 400，不漏到 ``_run`` 抛 ValueError。"""
    split = _config_num(config, "split")
    if split is not None and not (0.0 < split < 1.0):
        raise HTTPException(400, detail={
            "code": "FACTOR_TRAIN_INVALID_CONFIG", "message": f"split 须在 (0,1) 内，收到：{split}"})
    l2 = _config_num(config, "l2")
    if l2 is not None and l2 < 0.0:
        raise HTTPException(400, detail={
            "code": "FACTOR_TRAIN_INVALID_CONFIG", "message": f"l2 须 ≥0，收到：{l2}"})
    for key in ("nEstimators", "maxDepth"):     # 可选：present 时须为正整数（越界仍由引擎钳制兜底）
        val = _config_num(config, key)
        if val is not None and (val != int(val) or val <= 0):
            raise HTTPException(400, detail={
                "code": "FACTOR_TRAIN_INVALID_CONFIG",
                "message": f"{key} 须为正整数，收到：{config[key]!r}"})
    nw = _config_num(config, "normWindow")       # G-B5：present 时须正整数（_run 对它做 int()，漏校验会落 FAILED）
    if nw is not None and (nw != int(nw) or nw <= 0):
        raise HTTPException(400, detail={
            "code": "FACTOR_TRAIN_INVALID_CONFIG",
            "message": f"normWindow 须为正整数，收到：{config['normWindow']!r}"})
    _config_num(config, "clip")                  # G-B5：present 时仅断言可转数值（clip≤0 是关闭裁剪合法哨兵，不限范围）


def _guard_range(window: FactorWindow) -> None:
    """区间有效根数超 ``factor_train_max_bars`` → 413（复用同步训练护栏；绝不让无界长请求拖垮服务）。"""
    settings = get_settings()
    requested = (window.end_ts - window.start_ts) // bar_to_seconds(window.bar) + 1
    ceiling = settings.backtest_max_bars
    if window.max_bars is not None:
        ceiling = min(int(window.max_bars), ceiling)
    effective = max(0, min(int(requested), int(ceiling)))
    if effective > settings.factor_train_max_bars:
        raise HTTPException(413, detail={
            "code": "FACTOR_TRAIN_TOO_LARGE",
            "message": "训练区间过长，请缩短区间（或调高上限）后重试。"})


def _members_summary(members: list[dict]) -> str:
    """缺省模型名 = 成员摘要（前若干因子 key 拼接），便于列表识别。"""
    keys = [str(m.get("factorKey", "")) for m in members if m.get("factorKey")]
    head = "·".join(keys[:3])
    suffix = f" 等{len(keys)}因子" if len(keys) > 3 else ""
    return f"{head}{suffix} 权重模型" if head else "权重模型"


async def _resolve_model(session: AsyncSession, req: WeightTrainStartReq,
                         members: list[dict], *, symbols: list[str]) -> WeightModel:
    """模型去重解析（R-02）：modelId 复用 → factorSetId 复用-或-建 → 否则按 name/成员摘要新建。

    标的组成员注入 ``config_json``（``symbols`` / ``symbolGroupId``），``inst_id`` 取焦点（组内首个）；
    零 DB 迁移（§3.5）。复用既有模型时 ``refresh_weight_model_spec`` 以本次组 spec 刷新。
    """
    spec = repo.WeightModelSpec(
        name=req.name or _members_summary(members), inst_id=symbols[0], bar=req.bar,
        members=members, method=req.method,
        config=_inject_symbols(req.config, symbols, req.symbolGroupId), horizon=req.horizon,
        source="builder", factor_set_id=req.factorSetId)
    if req.modelId is not None:
        existing = await repo.get_weight_model(session, req.modelId)
        if existing is not None:
            return await repo.refresh_weight_model_spec(session, existing, spec=spec)
    if req.factorSetId is not None:
        existing = await repo.find_weight_model_by_factor_set(session, req.factorSetId)
        if existing is not None:
            return await repo.refresh_weight_model_spec(session, existing, spec=spec)
    return await repo.create_weight_model(session, spec=spec)


async def start_training(session: AsyncSession, rest, req: WeightTrainStartReq) -> dict:
    """启动流式训练：**守门优先、全过后才落库 / 注册作业**（R-01，杜绝孤儿）。

    守门序固定（R-04）：① 成员 400 → ② bar 400 → ③ config 400 → ④ 区间 413 → ④.5 标的数 400 → ⑤ 并发 429（同步预占）。
    bar/config 的 400 **必须先于** 区间 413（``_guard_range`` 自身调 ``bar_to_seconds``，非法 bar 否则抛 500）。
    标的组（``symbols``）逐标的沿用 ``_guard_range``（各标的同区间，故等价单次校验）；焦点 = 组内首个。

    Returns:
        ``{jobId, runId, modelId}``。

    Raises:
        HTTPException: 400 成员 / bar / config / 标的数非法 ｜ 413 区间过长 ｜ 429 作业繁忙。
    """
    members = validate_members([m.model_dump() for m in req.members])   # ① 成员 400
    _validate_bar(req.bar)                                              # ② bar 400（先于 413）
    _validate_train_config(req.config)                                 # ③ config 400（先于 413）
    symbols = _effective_symbols(req.symbols, req.instId)               # 去空去重（[0]=焦点）
    focus = symbols[0]
    window = FactorWindow(inst_id=focus, bar=req.bar, start_ts=req.startTs,
                          end_ts=req.endTs, max_bars=req.maxBars)
    _guard_range(window)                                                # ④ 区间 413（各标的同区间）
    _guard_train_symbols(symbols)                                       # ④.5 标的数 400（取数前，C-1）
    settings = get_settings()
    manager = get_weight_train_manager()
    if not manager.reserve():                                           # ⑤ 并发 429（同步预占硬上限，H-B3）
        raise HTTPException(429, detail={
            "code": "WEIGHT_TRAIN_BUSY",
            "message": f"并发训练作业已达上限 {settings.max_weight_train_jobs}，请稍后重试。"})
    # 守门全过（已预占名额）→ 解析 / 建模型（R-02）→ 落 running 记录 → 注册作业（此刻起即开缓冲，R-06）。
    # 预占后任何失败路径都 release，杜绝名额泄漏致长期假 429。
    try:
        model = await _resolve_model(session, req, members, symbols=symbols)
        run = await repo.add_weight_run(session, start=repo.WeightRunStart(
            model_id=model.id, method=req.method, inst_id=focus, bar=req.bar,
            start_ts=req.startTs, end_ts=req.endTs, horizon=req.horizon, config=req.config,
            started_ts=_now()))
        job = manager.start_job(JobSpec(                                # 内部 release()（预占转 running 计数）
            run_id=run.id, model_id=model.id, rest=rest, members=members, method=req.method,
            config=req.config, window=window, horizon=req.horizon, symbols=symbols,
            run_cap=settings.weight_model_run_cap, curve_steps=settings.weight_train_curve_steps))
    except Exception:
        manager.release()
        raise
    return {"jobId": job.job_id, "runId": run.id, "modelId": model.id}


def cancel_training(job_id: str) -> dict:
    """协作式取消训练作业（R-03）；返回管理器解析状态供路由映射 202/200/404。"""
    return {"jobId": job_id, "result": get_weight_train_manager().cancel(job_id)}


def _now() -> int:
    import time
    return int(time.time())
