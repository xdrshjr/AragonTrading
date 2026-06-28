"""因子 / 组合 CRUD 数据访问层（自 ``repositories.py`` 拆出，清洁度合规，R5）。

纯机械迁移、零行为变更：所有符号经 ``repositories.py`` 顶部 re-export，故 ``repo.X`` 调用点全部不变
（``api/factors.py`` / ``agent/tools/factor_tools.py`` / ``factors/service.py`` / seed / 测试）。私有 ``_*``
随模块内引用走、不外露。原文件因子块（676–939 行）超 800 行清洁度上限，本次拆出后两侧均 < 800。
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Factor, FactorSet


# ============================ 因子（因子层 §5.1） ============================
@dataclass(frozen=True)
class FactorValues:
    """新增因子的可写字段集合（聚合传参，避免位置参数堆叠）。"""

    factor_key: str
    name: str
    params: dict
    direction: str
    category: str
    source: str
    notes: str = ""


@dataclass(frozen=True)
class FactorPatch:
    """编辑因子的部分字段（None = 不改）。"""

    name: str | None = None
    params: dict | None = None
    direction: str | None = None
    enabled: bool | None = None
    notes: str | None = None


_FACTOR_SOURCES = ("builtin", "mined", "custom")
_FACTOR_SEARCH_MAX_LEN = 100    # q 长度上界（避免超长子串扫描，评审 P2-6）


def _primary_rankic(row: Factor) -> float | None:
    """因子缓存 ``eval_json`` 里**最小 horizon 的 ``rankIc``**（camelCase），无缓存 / 空则 ``None``（L3）。

    必须与前端 ``primaryRankIc``（``factor.ts``：按 ``h`` 升序取首项 ``rankIc``）**逐字对齐**，否则
    ``sort=rankic`` 服务端顺序会与列内显示值静默错位（评审 P1-2）。缓存键即 camelCase（``evaluation.py``）。
    """
    try:
        cached = json.loads(row.eval_json or "")
    except (json.JSONDecodeError, TypeError):
        return None
    horizons = cached.get("horizons") if isinstance(cached, dict) else None
    if not isinstance(horizons, list) or not horizons:
        return None
    first = min(horizons, key=lambda h: h.get("h", 0) if isinstance(h, dict) else 0)
    value = first.get("rankIc") if isinstance(first, dict) else None
    return float(value) if isinstance(value, (int, float)) else None


async def list_factors(session: AsyncSession, *, enabled: bool | None = None,
                       category: str | None = None, q: str | None = None,
                       source: str | None = None, sort: str | None = None) -> list[Factor]:
    """已存因子列表，支持激活态 / 类别 / 名称·key 子串 / 来源过滤 + 排序（L3）。

    ``q`` 子串匹配 name/factor_key（``ilike``，长度上界）；``source`` 仅接受白名单否则忽略该过滤；
    ``sort`` ∈ {rankic,name,created}，``rankic`` 在 Python 层按 ``_primary_rankic`` 降序、``None`` 沉底
    （SQL 取不到 JSON 内 RankIC）；缺省 / 未知排序保持「builtin 在前、id 升序」。
    """
    stmt = select(Factor)
    if enabled is not None:
        stmt = stmt.where(Factor.enabled == enabled)
    if category:
        stmt = stmt.where(Factor.category == category)
    if source in _FACTOR_SOURCES:
        stmt = stmt.where(Factor.source == source)
    if q:
        needle = f"%{q.strip()[:_FACTOR_SEARCH_MAX_LEN]}%"
        stmt = stmt.where(Factor.name.ilike(needle) | Factor.factor_key.ilike(needle))
    if sort == "name":
        stmt = stmt.order_by(Factor.name.asc())
    elif sort == "created":
        stmt = stmt.order_by(Factor.created_at.desc())
    else:
        stmt = stmt.order_by((Factor.source != "builtin"), Factor.id.asc())
    rows = list((await session.execute(stmt)).scalars().all())
    if sort == "rankic":
        rows.sort(key=lambda r: (_primary_rankic(r) is None, -(_primary_rankic(r) or 0.0)))
    return rows


async def get_factor(session: AsyncSession, factor_id: int) -> Factor | None:
    return await session.get(Factor, factor_id)


async def create_factor(session: AsyncSession, *, values: FactorValues) -> Factor:
    row = Factor(
        factor_key=values.factor_key, name=values.name,
        params_json=json.dumps(values.params or {}, ensure_ascii=False),
        direction=values.direction, category=values.category, source=values.source,
        enabled=True, notes=values.notes or "")
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def update_factor(session: AsyncSession, factor_id: int, *,
                        patch: FactorPatch) -> Factor | None:
    """编辑因子（``enabled`` 即激活 / 取消）。"""
    row = await session.get(Factor, factor_id)
    if row is None:
        return None
    if patch.name is not None:
        row.name = patch.name
    if patch.params is not None:
        row.params_json = json.dumps(patch.params, ensure_ascii=False)
    if patch.direction is not None:
        row.direction = patch.direction
    if patch.enabled is not None:
        row.enabled = patch.enabled
    if patch.notes is not None:
        row.notes = patch.notes
    # 参数 / 方向变更使旧评估缓存失效（避免「我的因子」静默展示陈旧 / 符号错的 RankIC，L1）。
    # 保守失效：PUT 同值也会清缓存（安全偏向，绝不展示陈旧）；重算交由显式 evaluate。
    if patch.params is not None or patch.direction is not None:
        row.eval_json = None
    await session.commit()
    await session.refresh(row)
    return row


async def set_factor_eval(session: AsyncSession, factor_id: int, eval_summary: dict) -> None:
    """缓存某因子最近一次评估摘要（不存在则静默跳过，缓存非关键路径）。"""
    row = await session.get(Factor, factor_id)
    if row is None:
        return
    row.eval_json = json.dumps(eval_summary, ensure_ascii=False)
    await session.commit()


async def delete_factor(session: AsyncSession, factor_id: int) -> str:
    """返回 'ok' | 'not_found' | 'builtin'（内置因子只读，C6）。"""
    row = await session.get(Factor, factor_id)
    if row is None:
        return "not_found"
    if row.source == "builtin":
        return "builtin"
    await session.delete(row)
    await session.commit()
    return "ok"


def _factor_dedupe_key(factor_key: str, params: dict | None, direction: str) -> str:
    """落库去重键：``factor_key | 规范化(params) | direction``（键序/数值规范化，避免误判，H5）。"""
    return f"{factor_key}|{json.dumps(params or {}, sort_keys=True, ensure_ascii=False)}|{direction}"


def _params_of(row: Factor) -> dict:
    try:
        parsed = json.loads(row.params_json or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


async def bulk_create_factors(session: AsyncSession, values: list[FactorValues]) -> list[Factor]:
    """批量落挖掘因子并**落库前去重**：同 ``(factor_key, 规范化 params, direction)`` 已存即跳过（H5）。

    去重对照库内全部已存行（任何 source），并在批内自去重；返回**实际新写入**的行（计数透明）。
    """
    if not values:
        return []
    keys = {v.factor_key for v in values}
    existing = (await session.execute(select(Factor).where(Factor.factor_key.in_(keys)))).scalars().all()
    seen = {_factor_dedupe_key(r.factor_key, _params_of(r), r.direction) for r in existing}
    rows: list[Factor] = []
    for v in values:
        dedupe_key = _factor_dedupe_key(v.factor_key, v.params, v.direction)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        rows.append(Factor(
            factor_key=v.factor_key, name=v.name,
            params_json=json.dumps(v.params or {}, ensure_ascii=False), direction=v.direction,
            category=v.category, source=v.source, enabled=True, notes=v.notes or ""))
    if not rows:
        return []
    session.add_all(rows)
    await session.commit()
    for row in rows:
        await session.refresh(row)
    return rows


# ============================ 因子组合（factor_sets，因子层 §5.1） ============================
@dataclass(frozen=True)
class FactorSetValues:
    """新增因子组合的可写字段集合（聚合传参）。"""

    name: str
    members: list
    method: str
    config: dict
    model: dict
    metrics: dict | None
    enabled: bool = True


@dataclass(frozen=True)
class FactorSetPatch:
    """编辑因子组合的部分字段（None = 不改）。"""

    name: str | None = None
    members: list | None = None
    method: str | None = None
    config: dict | None = None
    model: dict | None = None
    metrics: dict | None = None
    enabled: bool | None = None


async def list_factor_sets(session: AsyncSession, *, enabled: bool | None = None) -> list[FactorSet]:
    stmt = select(FactorSet).order_by(FactorSet.id.asc())
    if enabled is not None:
        stmt = stmt.where(FactorSet.enabled == enabled)
    return list((await session.execute(stmt)).scalars().all())


async def get_factor_set(session: AsyncSession, set_id: int) -> FactorSet | None:
    return await session.get(FactorSet, set_id)


async def create_factor_set(session: AsyncSession, *, values: FactorSetValues) -> FactorSet:
    row = FactorSet(
        name=values.name,
        members_json=json.dumps(values.members or [], ensure_ascii=False),
        method=values.method, config_json=json.dumps(values.config or {}, ensure_ascii=False),
        model_json=json.dumps(values.model or {}, ensure_ascii=False),
        metrics_json=json.dumps(values.metrics, ensure_ascii=False) if values.metrics is not None else None,
        enabled=values.enabled)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def update_factor_set(session: AsyncSession, set_id: int, *,
                            patch: FactorSetPatch) -> FactorSet | None:
    row = await session.get(FactorSet, set_id)
    if row is None:
        return None
    if patch.name is not None:
        row.name = patch.name
    if patch.members is not None:
        row.members_json = json.dumps(patch.members, ensure_ascii=False)
    if patch.method is not None:
        row.method = patch.method
    if patch.config is not None:
        row.config_json = json.dumps(patch.config, ensure_ascii=False)
    if patch.model is not None:
        row.model_json = json.dumps(patch.model, ensure_ascii=False)
    if patch.metrics is not None:
        row.metrics_json = json.dumps(patch.metrics, ensure_ascii=False)
    if patch.enabled is not None:
        row.enabled = patch.enabled
    await session.commit()
    await session.refresh(row)
    return row


async def delete_factor_set(session: AsyncSession, set_id: int) -> str:
    """返回 'ok' | 'not_found'（组合自包含快照，删除不影响已创建的 factor_score 策略，R5）。"""
    row = await session.get(FactorSet, set_id)
    if row is None:
        return "not_found"
    await session.delete(row)
    await session.commit()
    return "ok"
