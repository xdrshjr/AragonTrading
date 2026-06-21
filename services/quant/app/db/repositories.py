"""CRUD 数据访问层 (spec §8.3)。

含注册表 ↔ DB 行的合并规则 (C6)：DB 只存实例覆盖项，meta 从注册表回查。
"""
from __future__ import annotations

import json

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..engine.simulator import EquityRecord, TradeRecord
from ..strategies import STRATEGY_REGISTRY
from .models import AgentMessage, CandleCache, EquityPoint, Strategy, StrategyRun, Trade


# ============================ Strategies ============================
def merge_strategy(row: Strategy) -> dict:
    """把 DB 行与注册表 meta 合并为前端 `Strategy` 对象 (C6)。"""
    cls = STRATEGY_REGISTRY.get(row.template_key)
    try:
        params = json.loads(row.params_json or "{}")
    except json.JSONDecodeError:
        params = {}
    if cls is not None:
        meta = cls.meta
        merged_params = {**meta.default_params, **params}
        return {
            "id": row.id,
            "templateKey": row.template_key,
            "name": row.name or meta.name,
            "description": meta.description,
            "params": merged_params,
            "paramsSchema": meta.params_schema,
            "enabled": bool(row.enabled),
            "builtin": bool(row.builtin),
            "unavailable": False,
        }
    # 失效保护：模板已不存在 -> 标记 unavailable，不 500 (C6)
    return {
        "id": row.id,
        "templateKey": row.template_key,
        "name": row.name,
        "description": "(模板已不存在)",
        "params": params,
        "paramsSchema": {},
        "enabled": False,
        "builtin": bool(row.builtin),
        "unavailable": True,
    }


async def list_strategies(session: AsyncSession, enabled: bool | None = None) -> list[dict]:
    stmt = select(Strategy).order_by(Strategy.builtin.desc(), Strategy.id.asc())
    if enabled is not None:
        stmt = stmt.where(Strategy.enabled == enabled)
    rows = (await session.execute(stmt)).scalars().all()
    return [merge_strategy(r) for r in rows]


async def get_strategy_row(session: AsyncSession, strategy_id: int) -> Strategy | None:
    return await session.get(Strategy, strategy_id)


async def get_strategy(session: AsyncSession, strategy_id: int) -> dict | None:
    row = await get_strategy_row(session, strategy_id)
    return merge_strategy(row) if row else None


async def create_strategy(session: AsyncSession, template_key: str, name: str,
                          params: dict) -> dict:
    row = Strategy(
        template_key=template_key,
        name=name,
        params_json=json.dumps(params or {}, ensure_ascii=False),
        enabled=True,
        builtin=False,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return merge_strategy(row)


async def update_strategy(session: AsyncSession, strategy_id: int, *, name: str | None = None,
                          params: dict | None = None, enabled: bool | None = None) -> dict | None:
    row = await get_strategy_row(session, strategy_id)
    if row is None:
        return None
    if name is not None:
        row.name = name
    if params is not None:
        row.params_json = json.dumps(params, ensure_ascii=False)
    if enabled is not None:
        row.enabled = enabled
    await session.commit()
    await session.refresh(row)
    return merge_strategy(row)


async def delete_strategy(session: AsyncSession, strategy_id: int) -> str:
    """返回 'ok' | 'not_found' | 'builtin'（内置不可删，C6）。"""
    row = await get_strategy_row(session, strategy_id)
    if row is None:
        return "not_found"
    if row.builtin:
        return "builtin"
    await session.delete(row)
    await session.commit()
    return "ok"


# ============================ Runs ============================
async def create_run(session: AsyncSession, *, strategy_id: int, inst_id: str, bar: str,
                     mode: str, config: dict, status: str = "running") -> StrategyRun:
    run = StrategyRun(
        strategy_id=strategy_id, inst_id=inst_id, bar=bar, mode=mode, status=status,
        config_json=json.dumps(config, ensure_ascii=False),
    )
    session.add(run)
    await session.commit()
    await session.refresh(run)
    return run


async def persist_backtest_result(session: AsyncSession, run: StrategyRun, *,
                                  trades: list[TradeRecord], equity: list[EquityRecord],
                                  metrics: dict, start_ts: int, end_ts: int,
                                  status: str = "done") -> None:
    for t in trades:
        session.add(Trade(
            run_id=run.id, ts=t.ts, side=t.side, price=t.price, qty=t.qty, fee=t.fee,
            pnl=t.pnl, position_after=t.position_after, reason=t.reason,
        ))
    for e in equity:
        session.add(EquityPoint(run_id=run.id, ts=e.ts, equity=e.equity, drawdown=e.drawdown))
    run.status = status
    run.start_ts = start_ts
    run.end_ts = end_ts
    run.metrics_json = json.dumps(metrics, ensure_ascii=False)
    await session.commit()


async def get_run(session: AsyncSession, run_id: int) -> StrategyRun | None:
    return await session.get(StrategyRun, run_id)


async def list_runs(session: AsyncSession, *, strategy_id: int | None = None,
                    inst_id: str | None = None, mode: str | None = None,
                    limit: int = 50, offset: int = 0) -> list[StrategyRun]:
    """运行历史列表：按 id 降序（最新在前），支持 strategy/inst/mode 过滤 + 分页（F4）。"""
    stmt = select(StrategyRun)
    if strategy_id is not None:
        stmt = stmt.where(StrategyRun.strategy_id == strategy_id)
    if inst_id is not None:
        stmt = stmt.where(StrategyRun.inst_id == inst_id)
    if mode is not None:
        stmt = stmt.where(StrategyRun.mode == mode)
    stmt = stmt.order_by(StrategyRun.id.desc()).limit(limit).offset(offset)
    return list((await session.execute(stmt)).scalars().all())


async def list_trades(session: AsyncSession, run_id: int, limit: int = 200,
                      offset: int = 0) -> list[Trade]:
    stmt = (select(Trade).where(Trade.run_id == run_id)
            .order_by(Trade.ts.asc(), Trade.id.asc()).limit(limit).offset(offset))
    return list((await session.execute(stmt)).scalars().all())


async def list_equity(session: AsyncSession, run_id: int) -> list[EquityPoint]:
    stmt = (select(EquityPoint).where(EquityPoint.run_id == run_id)
            .order_by(EquityPoint.ts.asc(), EquityPoint.id.asc()))
    return list((await session.execute(stmt)).scalars().all())


async def append_trade(session: AsyncSession, run_id: int, t: TradeRecord) -> None:
    session.add(Trade(run_id=run_id, ts=t.ts, side=t.side, price=t.price, qty=t.qty,
                      fee=t.fee, pnl=t.pnl, position_after=t.position_after, reason=t.reason))
    await session.commit()


async def append_equity(session: AsyncSession, run_id: int, e: EquityRecord) -> None:
    session.add(EquityPoint(run_id=run_id, ts=e.ts, equity=e.equity, drawdown=e.drawdown))
    await session.commit()


# ============================ Candle cache ============================
async def get_cached_candles(session: AsyncSession, inst_id: str, bar: str,
                             limit: int) -> list[dict]:
    stmt = (select(CandleCache).where(CandleCache.inst_id == inst_id, CandleCache.bar == bar)
            .order_by(CandleCache.ts.desc()).limit(limit))
    rows = (await session.execute(stmt)).scalars().all()
    rows = sorted(rows, key=lambda r: r.ts)
    return [{"ts": r.ts, "o": r.o, "h": r.h, "l": r.l, "c": r.c, "vol": r.vol} for r in rows]


async def upsert_candles(session: AsyncSession, inst_id: str, bar: str, candles: list) -> None:
    """把真实 K 线写入缓存（按 inst+bar+ts 去重）。"""
    if not candles:
        return
    existing_stmt = select(CandleCache.ts).where(
        CandleCache.inst_id == inst_id, CandleCache.bar == bar,
        CandleCache.ts.in_([c.ts for c in candles]),
    )
    existing = {row[0] for row in (await session.execute(existing_stmt)).all()}
    for c in candles:
        if c.ts in existing:
            continue
        session.add(CandleCache(inst_id=inst_id, bar=bar, ts=c.ts,
                                o=c.o, h=c.h, l=c.l, c=c.c, vol=c.vol))
    await session.commit()


# ============================ Agent messages ============================
async def append_agent_message(session: AsyncSession, conversation_id: str, role: str,
                               content_json: str, created_ts: int) -> AgentMessage:
    """追加一条对话消息（content_json 为归一化 block 序列，P1-B）。"""
    row = AgentMessage(conversation_id=conversation_id, role=role,
                       content_json=content_json, created_ts=created_ts)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def list_agent_messages(session: AsyncSession, conversation_id: str,
                              limit: int = 200) -> list[AgentMessage]:
    """按时间升序返回某会话的消息（取最近 limit 条后再升序，便于重放）。"""
    stmt = (select(AgentMessage).where(AgentMessage.conversation_id == conversation_id)
            .order_by(AgentMessage.id.desc()).limit(limit))
    rows = list((await session.execute(stmt)).scalars().all())
    return list(reversed(rows))


async def clear_agent_messages(session: AsyncSession, conversation_id: str) -> int:
    """清空某会话（"新对话"），返回删除条数。"""
    stmt = delete(AgentMessage).where(AgentMessage.conversation_id == conversation_id)
    result = await session.execute(stmt)
    await session.commit()
    return result.rowcount or 0
