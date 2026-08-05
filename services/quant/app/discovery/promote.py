"""候选入库与一键回测（strategy-factor-discovery §2.12）—— **幂等**、边界收窄。

硬性不变式（安全，D-2 / R-8）：入库**只**写 ``factors`` / ``factor_sets`` / ``strategies`` 三张
既有表，**严禁**触碰 ``strategy_runs``、watchlist、自动交易成员、live-auto / ai-trade 的任何配置。
``autoPromote`` 默认 ``false``；即便打开也只做本模块列出的动作。

两处落库字段边界（评审 P1-5），改动须回文档：
- ``Factor.name`` 是 ``String(128)``，而中文渲染上限 512。故入库名固定
  ``挖掘因子 <hash8>：<中文渲染截断至 100>``（总长 ≤128，``hash8`` 保证互异且可溯源），
  完整渲染留在候选表的 ``describe_text`` 与详情抽屉。
- ``category`` **一律写 ``"discovered"``**，机制族写进 ``notes``。``Factor.category`` 承载的是
  既有类别词表，而 ``MemberPalette`` 的类别 chip 派生自**库目录**——13 个机制族名不会出现在
  任何 chip；更糟的是 ``factorApi.list({category})`` 是服务端过滤，用户一点任何类别 chip，
  挖掘因子就会整体消失。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import factor_repositories as factor_repo
from ..db import strategy_repositories as strategy_repo
from ..db.models import Factor, Strategy
from . import describe_strategy
from .expression import expr_hash, parse_expression
from .expression_text import describe_text
from .genomes import canonical_json
from .mechanisms import mechanism_cn
from . import repositories as repo

logger = logging.getLogger("aragon")

DISCOVERED_CATEGORY = "discovered"    # 与 FACTOR_LIBRARY["expr"].category 同值（单一口径）
FACTOR_NAME_MAX = 128
DESCRIBE_IN_NAME_MAX = 100
ROTATION_TEMPLATE_KEY = "regime_rotation"


def _bad(code: str, message: str, status: int = 400) -> HTTPException:
    return HTTPException(status, detail={"code": code, "message": message})


def build_factor_name(hash8: str, describe: str) -> str:
    """入库因子名（总长 ≤128，``hash8`` 保证互异且可溯源）。"""
    name = f"挖掘因子 {hash8}：{describe[:DESCRIBE_IN_NAME_MAX]}"
    return name[:FACTOR_NAME_MAX]


def member_name(expr_text: str) -> str:
    """``expr`` 成员的稳定列名 ``expr:<hash8>``（``factors/specs.py`` 强制校验此格式）。"""
    return f"expr:{expr_hash(parse_expression(expr_text))[:8]}"


# ============================ 幂等比较键（与台账身份同源，AL-4） ============================
def factor_match_key(expr_text: str) -> str:
    """因子的幂等键 = **规范式哈希**（discovery-asset-ledger §2.2.1 / D-27）。

    ⚠️ 这是一次**有意的语义变更**：本函数出现之前，``_find_existing_factor`` 比的是
    ``params_json["expr"]`` 与候选表达式的**逐字符串相等**，而 ``expr_hash`` 对交换律算子
    给出同哈希——二者对 ``add(a,b)`` / ``add(b,a)`` 给出相反答案，不可能同时成立。采纳规范式
    的理由：不然同一个因子会在台账里占两行、其中一行永远显示「未入库」（AL-4 空转）。
    代价是「文本不同但规范式相同」的两条因子从此复用同一个 ``factors`` 行。

    Raises:
        ExpressionError: 表达式无法解析。调用方须自行决定回落策略（历史脏数据回落逐字比）。
    """
    return expr_hash(parse_expression(expr_text))


def strategy_match_key(template_key: str, params: dict) -> str:
    """策略的幂等键（**纯提取**：逐字复刻 ``_find_existing_strategy`` 的现状比较键）。"""
    return canonical_json([str(template_key), dict(params)])


def member_set_match_key(members: list[dict]) -> str:
    """因子组合的幂等键（**纯提取**）：成员 ``(factorKey, params, direction)`` 排序后的规范式。

    ⚠️ 已知且有意：**不含 ``weight``**。同成员不同权重的两条成果会拿到同一个身份键，
    由台账按 AL-11 合并成一行；被合并掉的变体靠溯源里的 ``genomeHash`` + ``acceptSummary``
    留痕（§2.2.3）。
    """
    return canonical_json(sorted(
        [[str(m.get("factorKey") or ""), dict(m.get("params") or {}),
          str(m.get("direction") or "long")] for m in members],
        key=canonical_json))


def combo_members(genome: dict) -> list[dict]:
    """基因 → 入库成员（**权重逐成员烘焙**，BL-12：缺 ``weight`` 会被填 0.0 且不报错）。"""
    members = list(genome.get("members") or [])
    weights = list(genome.get("weights") or [])
    return [{"factorKey": str(member.get("factorKey") or ""),
             "params": dict(member.get("params") or {}),
             "direction": str(member.get("direction") or "long"),
             "name": str(member.get("name") or ""),
             "weight": float(weights[i]) if i < len(weights) else 0.0}
            for i, member in enumerate(members)]


def rotation_params(genome: dict) -> dict:
    """轮动组合基因 → ``regime_rotation`` 的入库参数（**纯提取**，与幂等键逐字节相同）。

    ``combo_specs.validate_combo_params`` 只校验不改写（返回 ``None``、不动入参），故本函数
    可以是纯函数，产物与 ``_promote_rotation`` 里被送去比对的那份 ``params`` 完全一致。
    """
    members = [{"templateKey": str(item.get("templateKey") or ""),
                "params": dict(item.get("params") or {})}
               for item in (genome.get("members") or [])]
    return {**dict(genome.get("policy") or {}), "members": members}


@dataclass(frozen=True)
class PromoteSource:
    """入库所需的**全部**输入（discovery-asset-ledger §2.8）。

    把入库从「候选行」上解耦出来：台账里的资产自持这些字段，因此 run 被删掉之后
    「从台账入库」依然可用（AL-11）。字段与 ``DiscoveryCandidate`` 同名同义，纯提取。
    """

    kind: str
    genome: dict
    expr_text: str
    metrics: dict
    mechanism: str
    genome_hash: str
    run_id: int
    iteration_idx: int


async def promote_source(session: AsyncSession, src: PromoteSource) -> tuple[str, int, int | None]:
    """``PromoteSource`` → ``(entity_kind, entity_id, companion_strategy_id)``（幂等）。

    ``promote_candidate`` 与台账的 ``promote_asset`` **共用**本实现；实体级幂等由三个
    ``_find_existing_*`` 保证，两条路径重复入库拿到的是同一个实体 id。
    """
    promoter = _PROMOTERS.get(src.kind)
    if promoter is None:
        raise _bad("DISCOVERY_AXIS_UNSUPPORTED",
                   f"搜索轴「{src.kind}」的入库尚未实现", 400)
    return await promoter(session, src)


async def promote_candidate(session: AsyncSession, candidate_id: int) -> dict:
    """候选 → 实体的**幂等**入库：重复入库返回既有实体 id 并回 200（不重复建行）。"""
    row = await repo.get_candidate(session, candidate_id)
    if row is None:
        raise _bad("NOT_FOUND", "候选不存在", 404)
    if row.verdict != "kept":
        raise _bad("DISCOVERY_CANDIDATE_NOT_KEPT",
                   "只有通过全部过滤条件的候选才能入库", 409)
    if row.promoted_kind and row.promoted_id:
        return await _replay_payload(session, row)
    entity_kind, entity_id, companion = await promote_source(session, source_of_row(row))
    await repo.mark_promoted(session, candidate_id, kind=entity_kind, entity_id=entity_id)
    return promoted_payload(entity_kind, entity_id, reused=False,
                            companion_strategy_id=companion)


def source_of_row(row) -> PromoteSource:
    """候选行 → ``PromoteSource``（唯一装配点，避免第二份字段口径）。"""
    return PromoteSource(
        kind=str(row.kind), genome=_loads(row.genome_json), expr_text=row.expr_text or "",
        metrics=_loads(row.metrics_json), mechanism=str(row.mechanism or ""),
        genome_hash=str(row.genome_hash or ""), run_id=int(row.run_id or 0),
        iteration_idx=int(row.iteration_idx or 0))


async def _replay_payload(session: AsyncSession, row) -> dict:
    """幂等回放（评审 P1-9）：``factor_set`` 分支必须**按 id 反查伴生策略**。

    ``companionStrategyId`` 只有首次调 ``factors.service.create_set`` 才拿得到；不反查的话
    第二次点「入库」返回的 payload 里没有它，成果库的「去策略面板」跳转就断了。
    """
    kind, entity_id = str(row.promoted_kind), int(row.promoted_id)
    companion = None
    if kind == "factor_set":
        from ..factors import service as factors_service

        companion = await factors_service.companion_strategy_id(session, entity_id)
    return promoted_payload(kind, entity_id, reused=True, companion_strategy_id=companion)


async def _promote_factor_axis(session: AsyncSession, src: PromoteSource) -> tuple[str, int, None]:
    entity = await _promote_factor(session, src)
    return "factor", int(entity.id), None


async def _promote_strategy(session: AsyncSession, src: PromoteSource) -> tuple[str, int, None]:
    """``strategy_param`` → ``strategies`` 行（BL-7：``params_json`` 与被回测的那份逐键相等）。"""
    template_key = str(src.genome.get("templateKey") or "")
    params = dict(src.genome.get("params") or {})
    if not template_key or not params:
        raise _bad("DISCOVERY_GENOME_INVALID", "候选基因缺少模板或参数，无法入库")
    existing = await _find_existing_strategy(session, template_key, params)
    if existing is not None:
        return "strategy", int(existing.id), None
    name = strategy_label("挖掘策略", src.genome_hash,
                          describe_strategy.template_name(template_key))
    created = await strategy_repo.add_strategy(session, template_key, name, params)
    await session.commit()
    return "strategy", int(created.id), None


async def _promote_rotation(session: AsyncSession, src: PromoteSource) -> tuple[str, int, None]:
    """``strategy_combo`` → ``regime_rotation`` 策略行（先过入库期同一把边界尺子）。"""
    from ..strategies import combo_specs

    params = rotation_params(src.genome)
    combo_specs.validate_combo_params(ROTATION_TEMPLATE_KEY, params)
    existing = await _find_existing_strategy(session, ROTATION_TEMPLATE_KEY, params)
    if existing is not None:
        return "strategy", int(existing.id), None
    name = strategy_label("挖掘轮动", src.genome_hash, f"{len(params['members'])} 策略")
    created = await strategy_repo.add_strategy(session, ROTATION_TEMPLATE_KEY, name, params)
    await session.commit()
    return "strategy", int(created.id), None


async def _promote_factor_set(session: AsyncSession,
                              src: PromoteSource) -> tuple[str, int, int | None]:
    """``factor_combo`` → ``factor_sets`` + 伴生 ``factor_score`` 策略（**必须**走 create_set）。

    ``factors.service.create_set`` 是单事务 + 伴生策略 + 三道校验的唯一真源；自建行会绕开
    伴生策略与权重契约校验，挖出一批「入库了但策略面板里没有」的组合。
    """
    from ..factors import service as factors_service

    members = combo_members(src.genome)
    existing = await _find_existing_set(session, members)
    if existing is not None:
        companion = await factors_service.companion_strategy_id(session, int(existing.id))
        return "factor_set", int(existing.id), companion
    name = strategy_label("挖掘组合", src.genome_hash, f"{len(members)} 因子")
    payload = await factors_service.create_set(
        session, name=name, members_raw=members, method="equal",
        config={"thresholds": dict(src.genome.get("thresholds") or {})}, model=None,
        metrics=None, enabled=True)
    return "factor_set", int(payload["id"]), payload.get("strategyId")


_PROMOTERS = {"factor": _promote_factor_axis, "strategy_param": _promote_strategy,
              "factor_combo": _promote_factor_set, "strategy_combo": _promote_rotation}


def strategy_label(prefix: str, genome_hash: str, tail: str) -> str:
    """``挖掘策略 <hash8>：<模板中文名>``（``Strategy.name`` / ``FactorSet.name`` 均 128 上限）。"""
    return f"{prefix} {str(genome_hash)[:8]}：{tail}"[:FACTOR_NAME_MAX]


async def _find_existing_strategy(session: AsyncSession, template_key: str, params: dict):
    """幂等键 = ``strategy_match_key``——同一份参数已入库即复用（与台账身份同源，AL-4）。"""
    target = strategy_match_key(template_key, params)
    rows = (await session.execute(select(Strategy).where(
        Strategy.template_key == template_key))).scalars().all()
    for row in rows:
        if strategy_match_key(row.template_key, _loads(row.params_json)) == target:
            return row
    return None


async def _find_existing_set(session: AsyncSession, members: list[dict]):
    """幂等键 = ``member_set_match_key``（成员 + 参数 + 方向的规范式），与名字无关。"""
    from ..db import factor_repositories as factor_set_repo

    target = member_set_match_key(members)
    for row in await factor_set_repo.list_factor_sets(session):
        if member_set_match_key(_loads_list(row.members_json)) == target:
            return row
    return None


def _loads_list(raw: str | None) -> list:
    try:
        value = json.loads(raw or "[]")
    except (json.JSONDecodeError, TypeError):
        return []
    return value if isinstance(value, list) else []


async def _promote_factor(session: AsyncSession, src: PromoteSource) -> Factor:
    """建 ``factors`` 行（``factor_key="expr"``）；同表达式已存在即复用（跨 run 也幂等）。"""
    expr_text = src.expr_text or ""
    try:
        node = parse_expression(expr_text)
    except Exception as exc:  # noqa: BLE001 —— 落库前最后一道边界校验
        raise _bad("FACTOR_EXPR_INVALID", f"候选表达式无法解析：{exc}") from exc
    existing = await _find_existing_factor(session, expr_text)
    if existing is not None:
        return existing
    direction = str(src.metrics.get("direction") or "long")
    hash8 = expr_hash(node)[:8]
    mechanism = mechanism_cn(src.mechanism) if src.mechanism else "未分类"
    return await factor_repo.create_factor(session, values=factor_repo.FactorValues(
        factor_key="expr",
        name=build_factor_name(hash8, describe_text(node, limit=DESCRIBE_IN_NAME_MAX)),
        params={"expr": expr_text},
        direction=direction if direction in ("long", "short") else "long",
        category=DISCOVERED_CATEGORY, source="mined",
        notes=f"discovery #{int(src.run_id)} iter {int(src.iteration_idx)} · {mechanism}"[:256]))


async def _find_existing_factor(session: AsyncSession, expr_text: str) -> Factor | None:
    """同表达式的挖掘因子已存在即复用（幂等键 = ``factor_match_key`` **规范式哈希**，D-27）。

    ``add(a,b)`` 与 ``add(b,a)`` 从此复用同一行——这是 AL-4 在因子轴上成立的前提，也是一次
    有意的语义变更（旧口径是逐字文本相等）。**历史脏数据兜底**：任一侧解析不了就退回逐字
    比较并留痕，绝不让一条解析不了的老行把整次入库炸掉。
    """
    try:
        target = factor_match_key(expr_text)
    except Exception:  # noqa: BLE001 —— 入参解析失败由上游 _promote_factor 先行拦截
        target = ""
    rows = (await session.execute(
        select(Factor).where(Factor.factor_key == "expr",
                             Factor.source == "mined"))).scalars().all()
    for row in rows:
        existing = str(_loads(row.params_json).get("expr") or "")
        try:
            if target and factor_match_key(existing) == target:
                return row
        except Exception as exc:  # noqa: BLE001 —— 单条历史脏数据回落逐字比
            logger.debug("factor #%s 的 expr 无法解析，回落逐字比较：%s", row.id, exc)
            if existing == expr_text:
                return row
    return None


def promoted_payload(kind: str, entity_id: int, *, reused: bool,
                     companion_strategy_id: int | None = None) -> dict:
    payload = {"kind": kind, "entityId": entity_id, "entityKind": kind, "reused": reused}
    if companion_strategy_id is not None:
        payload["companionStrategyId"] = int(companion_strategy_id)
    return {"promoted": payload}


def _loads(raw: str | None) -> dict:
    try:
        value = json.loads(raw or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


# ============================ 一键回测 ============================
async def backtest_candidate(session: AsyncSession, rest, candidate_id: int, *,
                             start_ts: int, end_ts: int) -> dict:
    """把候选装成单成员 ``factor_score`` 快照跑一次真回测（复用既有编排，不新造引擎）。

    该回测**不落库**（复用 ``run_factor_score_backtest`` 的一次性语义）——「进运行历史并可回放
    K 线」由用户先「入库」再从策略面板发起常规回测完成，路径与既有产品一致，也避免发现层
    往 ``strategy_runs`` 写东西（安全不变式）。
    """
    from ..agent.services import run_factor_score_backtest

    row = await repo.get_candidate(session, candidate_id)
    if row is None:
        raise _bad("NOT_FOUND", "候选不存在", 404)
    run = await repo.get_run(session, int(row.run_id))
    if run is None:
        raise _bad("NOT_FOUND", "候选所属任务不存在", 404)
    if row.kind != "factor":
        return await _backtest_strategy_candidate(rest, row, run, start_ts=start_ts,
                                                  end_ts=end_ts)
    if not row.expr_text:
        raise _bad("DISCOVERY_AXIS_UNSUPPORTED", "该候选没有可回测的表达式", 400)
    metrics = _loads(row.metrics_json)
    params = _factor_score_params(row.expr_text, str(metrics.get("direction") or "long"))
    _candles, result, costs = await run_factor_score_backtest(
        session, rest, params=params, inst_id=run.inst_id, bar=run.bar,
        start_ts=int(start_ts), end_ts=int(end_ts))
    return {"candidateId": candidate_id, "instId": run.inst_id, "bar": run.bar,
            "startTs": int(start_ts), "endTs": int(end_ts), "metrics": result.metrics,
            "trades": len(result.trades), "clipMode": "full",
            "costs": {"initialCapital": costs[0], "takerFeeBps": costs[1],
                      "slippageBps": costs[2], "leverage": costs[3]}}


async def _backtest_strategy_candidate(rest, row, run, *, start_ts: int,
                                       end_ts: int) -> dict:
    """策略轴候选的一键回测：按**同一个 assembler**在给定区间跑一次真回测（不落库）。

    与因子轴的一次性语义一致——「进运行历史并可回放 K 线」由用户先入库再从策略面板发起，
    发现层绝不往 ``strategy_runs`` 写东西（安全不变式）。
    """
    from ..config import get_settings
    from ..engine.backtester import candles_to_df, run_backtest
    from ..engine.combo import run_combo_backtest
    from .backtest_lane import costs_of, metrics_of
    from .genomes import GENOME_LOADERS
    from .lanes import LaneContext
    from .lanes.strategy_lane import assembler_for

    settings = get_settings()
    genome_payload = _loads(row.genome_json)
    if not genome_payload:
        raise _bad("DISCOVERY_GENOME_INVALID", "候选基因为空，无法回测")
    assembler = assembler_for(str(row.kind), GENOME_LOADERS[str(row.kind)](genome_payload),
                              settings)
    candles, frames = await _load_backtest_window(rest, assembler, run, start_ts, end_ts,
                                                  settings)
    ctx = LaneContext(df_full=candles_to_df(candles), candles=candles, htf_frames=frames,
                      bar=run.bar, split=len(candles), split_ts=int(end_ts),
                      config={}, settings=settings, memo={})
    reason = assembler.preflight(ctx)
    if reason:
        raise _bad("DISCOVERY_BACKTEST_UNAVAILABLE", f"候选无法回测：{reason}")
    initial, fee_bps, slip_bps, leverage = costs_of(settings)
    kwargs = {"initial_capital": initial, "taker_fee_bps": fee_bps,
              "slippage_bps": slip_bps, "leverage": leverage}
    if assembler.is_combo:
        members = assembler.members()
        result = run_combo_backtest(
            members, candles, run.bar, policy=assembler.policy(),
            htf_frames_by_member=assembler.htf_frames_by_member(ctx, members), **kwargs)
        extra = {"segments": len(result.segments)}
    else:
        result = run_backtest(assembler.build(), candles, run.bar,
                              htf_frames=assembler.htf_frames(ctx) or None, **kwargs)
        extra = {}
    return {"candidateId": int(row.id), "instId": run.inst_id, "bar": run.bar,
            "startTs": int(start_ts), "endTs": int(end_ts),
            "metrics": metrics_of(result.equity_points, result.trades, run.bar, initial),
            "trades": len(result.trades), "equity": _equity_points(result.equity_points),
            "clipMode": "full", **extra,
            "costs": {"initialCapital": initial, "takerFeeBps": fee_bps,
                      "slippageBps": slip_bps, "leverage": leverage}}


async def _load_backtest_window(rest, assembler, run, start_ts: int, end_ts: int,
                                settings) -> tuple[list, dict]:
    """薄包装（§2.7）：取数实现只有 ``window_loader.load_window`` 一份，体检共用它。"""
    from . import window_loader

    return await window_loader.load_window(
        rest, inst_id=run.inst_id, bar=run.bar, start_ts=int(start_ts), end_ts=int(end_ts),
        timeframes=_required_timeframes(assembler), settings=settings)


def _required_timeframes(assembler) -> list[str]:
    if assembler.is_combo:
        return [tf for member in assembler.members() for tf in member.timeframes]
    return list(assembler.build().timeframes())


def _equity_points(points: list, cap: int = 400) -> list[dict]:
    """净值下采样到 ≤``cap`` 点（详情抽屉够用，响应体不因此膨胀）。"""
    if not points:
        return []
    picked = points if len(points) <= cap else [
        points[min(len(points) - 1, round(i * (len(points) - 1) / (cap - 1)))]
        for i in range(cap)]
    return [{"ts": int(p.ts), "equity": round(float(p.equity), 4)} for p in picked]


def _factor_score_params(expr_text: str, direction: str) -> dict:
    """单成员 ``factor_score`` 快照（等权，阈值取模板默认；成员必带 ``expr:<hash8>`` 列名）。"""
    from ..factors.specs import normalize_thresholds, validate_members

    members = validate_members([{
        "factorKey": "expr", "params": {"expr": expr_text}, "weight": 1.0,
        "direction": direction if direction in ("long", "short") else "long",
        "name": member_name(expr_text)}])
    return {"members": members, "thresholds": normalize_thresholds(None)}
