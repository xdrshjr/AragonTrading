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

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import factor_repositories as factor_repo
from ..db.models import Factor
from .expression import expr_hash, parse_expression
from .expression_text import describe_text
from .mechanisms import mechanism_cn
from . import repositories as repo

logger = logging.getLogger("aragon")

DISCOVERED_CATEGORY = "discovered"    # 与 FACTOR_LIBRARY["expr"].category 同值（单一口径）
FACTOR_NAME_MAX = 128
DESCRIBE_IN_NAME_MAX = 100


def _bad(code: str, message: str, status: int = 400) -> HTTPException:
    return HTTPException(status, detail={"code": code, "message": message})


def build_factor_name(hash8: str, describe: str) -> str:
    """入库因子名（总长 ≤128，``hash8`` 保证互异且可溯源）。"""
    name = f"挖掘因子 {hash8}：{describe[:DESCRIBE_IN_NAME_MAX]}"
    return name[:FACTOR_NAME_MAX]


def member_name(expr_text: str) -> str:
    """``expr`` 成员的稳定列名 ``expr:<hash8>``（``factors/specs.py`` 强制校验此格式）。"""
    return f"expr:{expr_hash(parse_expression(expr_text))[:8]}"


async def promote_candidate(session: AsyncSession, candidate_id: int) -> dict:
    """候选 → 实体的**幂等**入库：重复入库返回既有实体 id 并回 200（不重复建行）。"""
    row = await repo.get_candidate(session, candidate_id)
    if row is None:
        raise _bad("NOT_FOUND", "候选不存在", 404)
    if row.verdict != "kept":
        raise _bad("DISCOVERY_CANDIDATE_NOT_KEPT",
                   "只有通过全部过滤条件的候选才能入库", 409)
    if row.promoted_kind and row.promoted_id:
        return _promoted_payload(row.promoted_kind, int(row.promoted_id), reused=True)
    if row.kind != "factor":
        raise _bad("DISCOVERY_AXIS_UNSUPPORTED",
                   f"搜索轴「{row.kind}」的入库尚未实现", 400)
    entity = await _promote_factor(session, row)
    await repo.mark_promoted(session, candidate_id, kind="factor", entity_id=int(entity.id))
    return _promoted_payload("factor", int(entity.id), reused=False)


async def _promote_factor(session: AsyncSession, row) -> Factor:
    """建 ``factors`` 行（``factor_key="expr"``）；同表达式已存在即复用（跨 run 也幂等）。"""
    expr_text = row.expr_text or ""
    try:
        node = parse_expression(expr_text)
    except Exception as exc:  # noqa: BLE001 —— 落库前最后一道边界校验
        raise _bad("FACTOR_EXPR_INVALID", f"候选表达式无法解析：{exc}") from exc
    existing = await _find_existing_factor(session, expr_text)
    if existing is not None:
        return existing
    metrics = _loads(row.metrics_json)
    direction = str(metrics.get("direction") or "long")
    hash8 = expr_hash(node)[:8]
    mechanism = mechanism_cn(row.mechanism) if row.mechanism else "未分类"
    return await factor_repo.create_factor(session, values=factor_repo.FactorValues(
        factor_key="expr",
        name=build_factor_name(hash8, describe_text(node, limit=DESCRIBE_IN_NAME_MAX)),
        params={"expr": expr_text},
        direction=direction if direction in ("long", "short") else "long",
        category=DISCOVERED_CATEGORY, source="mined",
        notes=f"discovery #{int(row.run_id)} iter {int(row.iteration_idx)} · {mechanism}"[:256]))


async def _find_existing_factor(session: AsyncSession, expr_text: str) -> Factor | None:
    """同表达式的挖掘因子已存在即复用（幂等：``params_json`` 里的 expr 逐字相等）。"""
    rows = (await session.execute(
        select(Factor).where(Factor.factor_key == "expr",
                             Factor.source == "mined"))).scalars().all()
    for row in rows:
        if _loads(row.params_json).get("expr") == expr_text:
            return row
    return None


def _promoted_payload(kind: str, entity_id: int, *, reused: bool) -> dict:
    return {"promoted": {"kind": kind, "entityId": entity_id, "entityKind": kind,
                         "reused": reused}}


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
    if not row.expr_text:
        raise _bad("DISCOVERY_AXIS_UNSUPPORTED", "该候选没有可回测的表达式", 400)
    run = await repo.get_run(session, int(row.run_id))
    if run is None:
        raise _bad("NOT_FOUND", "候选所属任务不存在", 404)
    metrics = _loads(row.metrics_json)
    params = _factor_score_params(row.expr_text, str(metrics.get("direction") or "long"))
    _candles, result, costs = await run_factor_score_backtest(
        session, rest, params=params, inst_id=run.inst_id, bar=run.bar,
        start_ts=int(start_ts), end_ts=int(end_ts))
    return {"candidateId": candidate_id, "instId": run.inst_id, "bar": run.bar,
            "startTs": int(start_ts), "endTs": int(end_ts), "metrics": result.metrics,
            "trades": len(result.trades),
            "costs": {"initialCapital": costs[0], "takerFeeBps": costs[1],
                      "slippageBps": costs[2], "leverage": costs[3]}}


def _factor_score_params(expr_text: str, direction: str) -> dict:
    """单成员 ``factor_score`` 快照（等权，阈值取模板默认；成员必带 ``expr:<hash8>`` 列名）。"""
    from ..factors.specs import normalize_thresholds, validate_members

    members = validate_members([{
        "factorKey": "expr", "params": {"expr": expr_text}, "weight": 1.0,
        "direction": direction if direction in ("long", "short") else "long",
        "name": member_name(expr_text)}])
    return {"members": members, "thresholds": normalize_thresholds(None)}
