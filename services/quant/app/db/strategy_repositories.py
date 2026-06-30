"""策略 CRUD 的不提交原语 + 因子组合伴生策略反查（自 ``repositories.py`` 拆出，R5）。

为「因子组合 ↔ 交易策略」单事务同步服务：``factors/service.py`` 在同一 ``AsyncSession`` 内编排
组合与其伴生 ``factor_score`` 策略的增删改，故这些原语**只 add / flush / mutate，绝不 commit**
（提交权收口在 service 末尾，保证组合与策略要么都落、要么都不落，原子回滚）。反查
``list_factor_score_rows_for_set`` 与批量 ``map_companion_strategy_ids`` 按 ``params_json.factorSetId``
解析「正向链接」，零迁移地还原 set→strategy（R1/R2）。所有公开符号经 ``repositories.py`` 顶部 re-export，
故 ``repo.X`` 调用点不变（沿用 ``factor_repositories.py`` 拆分先例）。
"""
from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Strategy

# factor_score 策略模板 key（伴生策略均为该模板；template_key 已建索引，反查非热路径）。
FACTOR_SCORE_TEMPLATE_KEY = "factor_score"


async def add_strategy(session: AsyncSession, template_key: str, name: str,
                       params: dict) -> Strategy:
    """新增策略行（add + flush 取 ``id``，**不 commit**）：供 service 单事务连带建伴生策略。

    返回 ORM 行（非 ``merge_strategy`` 投影），便于编排层取 ``id`` 与后续 ``apply_strategy`` 刷新。
    新行恒 ``enabled=True``、``builtin=False``（伴生策略可改可删）。
    """
    row = Strategy(
        template_key=template_key,
        name=name,
        params_json=json.dumps(params or {}, ensure_ascii=False),
        enabled=True,
        builtin=False,
    )
    session.add(row)
    await session.flush()
    return row


async def apply_strategy(session: AsyncSession, row: Strategy, *, name: str | None = None,
                         params: dict | None = None) -> None:
    """就地刷新策略行的 ``name`` / ``params``（**不 commit、不动 ``enabled``**，R9）。

    组合更新时以快照覆盖伴生策略的名称与参数，但保留用户在交易面板对该策略的启停态。
    """
    if name is not None:
        row.name = name
    if params is not None:
        row.params_json = json.dumps(params, ensure_ascii=False)
    await session.flush()


def _factor_set_id_of(row: Strategy) -> int | None:
    """解析 ``factor_score`` 行 ``params_json.factorSetId``（缺失 / 非整数 → ``None``，绝不抛）。

    ``bool`` 是 ``int`` 子类，显式排除以免 ``True``/``False`` 被误当作 set id。
    """
    try:
        params = json.loads(row.params_json or "{}")
    except (json.JSONDecodeError, TypeError):
        return None
    value = params.get("factorSetId") if isinstance(params, dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) else None


async def _factor_score_rows(session: AsyncSession) -> list[Strategy]:
    """全部 ``factor_score`` 策略行（``id`` 升序）。

    ``template_key`` 已建索引、``factor_score`` 行数量级小，仅在组合 CRUD / 补建时调用（非热路径）。
    """
    stmt = (select(Strategy)
            .where(Strategy.template_key == FACTOR_SCORE_TEMPLATE_KEY)
            .order_by(Strategy.id.asc()))
    return list((await session.execute(stmt)).scalars().all())


async def list_factor_score_rows_for_set(session: AsyncSession, set_id: int) -> list[Strategy]:
    """按 ``factorSetId`` 反查某组合的全部伴生 ``factor_score`` 行（``id`` 升序）。

    返回顺序保证「首条 = 正典（min id）」，供 ``ensure_companion_strategy`` 刷新正典 + 删多余（R2）、
    ``delete_set`` 删全部（R3）。
    """
    return [r for r in await _factor_score_rows(session) if _factor_set_id_of(r) == set_id]


async def map_companion_strategy_ids(session: AsyncSession, set_ids: list[int]) -> dict[int, int]:
    """批量反查 ``{factorSetId: 正典(min id) strategyId}``（一次查询，避免读路径 N+1，R1）。

    仅返回 ``set_ids`` 中确有伴生的组合；同 ``factorSetId`` 多伴生取最小 ``id`` 为正典（与 R2 坍缩口径
    一致，故刷新页面后列表 ``strategyId`` 指向正典而非随机某条）。``set_ids`` 为空时短路返回空字典。
    """
    if not set_ids:
        return {}
    wanted = set(set_ids)
    mapping: dict[int, int] = {}
    for row in await _factor_score_rows(session):   # 已按 id 升序 → 首见某 set_id 即其 min id
        set_id = _factor_set_id_of(row)
        if set_id in wanted and set_id not in mapping:
            mapping[set_id] = row.id
    return mapping
