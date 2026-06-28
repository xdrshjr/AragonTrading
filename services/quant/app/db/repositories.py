"""CRUD 数据访问层 (spec §8.3)。

含注册表 ↔ DB 行的合并规则 (C6)：DB 只存实例覆盖项，meta 从注册表回查。
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass

from sqlalchemy import delete, insert, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..engine.simulator import EquityRecord, TradeRecord
from ..strategies import STRATEGY_REGISTRY
from .models import (
    AgentMessage,
    AgentProvider,
    AgentToolState,
    CandleCache,
    EquityPoint,
    Factor,
    FactorSet,
    LiveOrderAudit,
    OkxAccount,
    Strategy,
    StrategyRun,
    Trade,
)


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
            "kind": getattr(meta, "kind", "atomic"),
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
        "kind": "atomic",
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
    trade_rows = [
        {
            "run_id": run.id,
            "ts": t.ts,
            "side": t.side,
            "price": t.price,
            "qty": t.qty,
            "fee": t.fee,
            "pnl": t.pnl,
            "position_after": t.position_after,
            "reason": t.reason,
            "stop_loss_pct": t.stop_loss_pct,
            "take_profit_pct": t.take_profit_pct,
            "max_hold_bars": t.max_hold_bars,
            "stop_loss_price": t.stop_loss_price,
            "take_profit_price": t.take_profit_price,
        }
        for t in trades
    ]
    equity_rows = [
        {"run_id": run.id, "ts": e.ts, "equity": e.equity, "drawdown": e.drawdown}
        for e in equity
    ]
    if trade_rows:
        await session.execute(insert(Trade), trade_rows)
    if equity_rows:
        await session.execute(insert(EquityPoint), equity_rows)
    run.status = status
    run.start_ts = start_ts
    run.end_ts = end_ts
    run.metrics_json = json.dumps(metrics, ensure_ascii=False)
    await session.commit()


async def persist_walk_forward_result(session: AsyncSession, run: StrategyRun, *,
                                      summary: dict, windows: list[dict], start_ts: int,
                                      end_ts: int, status: str = "done") -> None:
    """合并写回高级回测结果（v2·P1-5 契约钉死，spec §4 / §6.1）。

    读取 `run.config_json` 并**合并**（叠加 `summary`/`windows`，**不覆盖**既有窗口参数键）后写回；
    构造 `metrics_json` 展示投影（Metrics 形状，仅供通用 run 列表卡片渲染，非真实单次回测指标）；
    写 `start_ts/end_ts/status` 后 commit。**不**落 `Trade`/`EquityPoint`（窗口各自独立、无单条连续净值）。
    """
    try:
        cfg = json.loads(run.config_json or "{}")
    except json.JSONDecodeError:
        cfg = {}
    cfg["summary"] = summary
    cfg["windows"] = windows
    run.config_json = json.dumps(cfg, ensure_ascii=False)
    total_trades = sum(int(w.get("tradeCount", 0)) for w in windows)
    run.metrics_json = json.dumps({
        "totalReturn": summary.get("avgReturn", 0.0),
        "winRate": summary.get("profitableRate", 0.0),
        "profitFactor": 0.0,
        "maxDrawdown": summary.get("avgMaxDrawdown", 0.0),
        "sharpe": summary.get("stabilityScore", 0.0),
        "tradeCount": total_trades,
        "avgPnl": summary.get("medianReturn", 0.0),
    }, ensure_ascii=False)
    run.status = status
    run.start_ts = start_ts
    run.end_ts = end_ts
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
                      fee=t.fee, pnl=t.pnl, position_after=t.position_after, reason=t.reason,
                      stop_loss_pct=t.stop_loss_pct, take_profit_pct=t.take_profit_pct,
                      max_hold_bars=t.max_hold_bars, stop_loss_price=t.stop_loss_price,
                      take_profit_price=t.take_profit_price))
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
    by_ts = {c.ts: c for c in candles}
    values = [
        {
            "inst_id": inst_id,
            "bar": bar,
            "ts": c.ts,
            "o": c.o,
            "h": c.h,
            "l": c.l,
            "c": c.c,
            "vol": c.vol,
        }
        for c in by_ts.values()
    ]
    stmt = sqlite_insert(CandleCache).values(values)
    stmt = stmt.on_conflict_do_update(
        index_elements=["inst_id", "bar", "ts"],
        set_={
            "o": stmt.excluded.o,
            "h": stmt.excluded.h,
            "l": stmt.excluded.l,
            "c": stmt.excluded.c,
            "vol": stmt.excluded.vol,
        },
    )
    await session.execute(stmt)
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


# ============================ Agent providers（设置中心 · AI 模型服务） ============================
@dataclass(frozen=True)
class ProviderValues:
    """新增服务商的可写字段集合（聚合传参，避免位置参数堆叠）。"""

    label: str
    api_standard: str
    base_url: str
    api_key: str
    model: str
    user_agent: str


@dataclass(frozen=True)
class ProviderPatch:
    """编辑服务商的部分字段（None = 不改；`api_key` 空/None = 保留原 key，前端不持有明文）。"""

    label: str | None = None
    api_standard: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    user_agent: str | None = None


async def list_agent_providers(session: AsyncSession) -> list[AgentProvider]:
    """全量服务商，官方在前、同类按 id 升序（展示顺序稳定）。"""
    stmt = select(AgentProvider).order_by(
        (AgentProvider.kind == "custom"), AgentProvider.id.asc())
    return list((await session.execute(stmt)).scalars().all())


async def get_agent_provider(session: AsyncSession, provider_id: int) -> AgentProvider | None:
    return await session.get(AgentProvider, provider_id)


async def get_agent_provider_by_slug(session: AsyncSession, slug: str) -> AgentProvider | None:
    stmt = select(AgentProvider).where(AgentProvider.slug == slug)
    return (await session.execute(stmt)).scalars().first()


async def get_default_agent_provider(session: AsyncSession) -> AgentProvider | None:
    stmt = select(AgentProvider).where(AgentProvider.is_default == True)  # noqa: E712
    return (await session.execute(stmt)).scalars().first()


async def create_agent_provider(session: AsyncSession, *, kind: str, values: ProviderValues,
                                slug: str | None = None,
                                is_default: bool = False) -> AgentProvider:
    """新增服务商（自定义默认 `kind="custom"` + 随机 slug）。"""
    row = AgentProvider(
        slug=slug or uuid.uuid4().hex,
        kind=kind,
        label=values.label,
        api_standard=values.api_standard,
        base_url=values.base_url,
        api_key=values.api_key or "",
        model=values.model,
        user_agent=values.user_agent or "claude-code/1.0",
        is_default=is_default,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def update_agent_provider(session: AsyncSession, provider_id: int, *,
                                patch: ProviderPatch) -> AgentProvider | None:
    """编辑服务商；官方项锁定 `slug/kind/api_standard`，`api_key` 空则保留原值。"""
    row = await session.get(AgentProvider, provider_id)
    if row is None:
        return None
    if patch.label is not None:
        row.label = patch.label
    if patch.base_url is not None:
        row.base_url = patch.base_url
    if patch.model is not None:
        row.model = patch.model
    if patch.user_agent is not None:
        row.user_agent = patch.user_agent
    if patch.api_key:  # 非空才更新（空/None = 保留原 key，R-Sec）
        row.api_key = patch.api_key
    if patch.api_standard is not None and row.kind == "custom":  # 官方项标准锁定
        row.api_standard = patch.api_standard
    await session.commit()
    await session.refresh(row)
    return row


async def delete_agent_provider(session: AsyncSession, provider_id: int) -> str:
    """返回 'ok' | 'not_found' | 'not_deletable'（官方项不可删）。删默认后自动回落（P2-Fallback）。"""
    row = await session.get(AgentProvider, provider_id)
    if row is None:
        return "not_found"
    if row.kind != "custom":
        return "not_deletable"
    was_default = bool(row.is_default)
    await session.delete(row)
    await session.commit()
    if was_default:
        await _reassign_default_after_delete(session)
    return "ok"


async def set_default_agent_provider(session: AsyncSession, provider_id: int) -> bool:
    """事务内清其余 `is_default` 再置本行，杜绝多默认（R-Default）。不存在返回 False。

    用 ORM 逐行置位（至多一行原默认）而非批量 UPDATE：批量 UPDATE + `expire_on_commit=False`
    会令同 session 内已加载对象的 `is_default` 滞留旧值，导致 set 后立即重查仍读到脏值。
    """
    target = await session.get(AgentProvider, provider_id)
    if target is None:
        return False
    stmt = select(AgentProvider).where(AgentProvider.is_default == True)  # noqa: E712
    for row in (await session.execute(stmt)).scalars().all():
        row.is_default = False
    target.is_default = True
    await session.commit()
    return True


async def _reassign_default_after_delete(session: AsyncSession) -> None:
    """删默认后选「含 key 且 id 最小」的行为新默认（确定可复现）；无含 key 行则不设默认。"""
    stmt = (select(AgentProvider)
            .where(AgentProvider.api_key != "")  # noqa: E711
            .order_by(AgentProvider.id.asc()).limit(1))
    row = (await session.execute(stmt)).scalars().first()
    if row is not None:
        await set_default_agent_provider(session, row.id)


async def count_agent_providers(session: AsyncSession) -> int:
    rows = (await session.execute(select(AgentProvider.id))).all()
    return len(rows)


# ============================ Agent 工具启用态（设置中心 · Agent 工具管理） ============================
async def get_tool_states(session: AsyncSession) -> dict[str, bool]:
    """读全部启用态覆盖项为 `{name: enabled}`（只读，不提交）。

    稀疏表：仅含用户显式改过的工具；调用方按「无记录即启用」合并（`states.get(name, True)`，C6）。
    """
    rows = (await session.execute(select(AgentToolState))).scalars().all()
    return {row.name: bool(row.enabled) for row in rows}


async def set_tool_enabled(session: AsyncSession, name: str, enabled: bool) -> None:
    """按 name upsert 单个工具启用态（select-then-update / 否则 insert）后 `commit`，否则不落库。"""
    stmt = select(AgentToolState).where(AgentToolState.name == name)
    row = (await session.execute(stmt)).scalars().first()
    if row is None:
        session.add(AgentToolState(name=name, enabled=enabled))
    else:
        row.enabled = enabled
    await session.commit()


async def set_all_tool_states(session: AsyncSession, names: list[str], enabled: bool) -> None:
    """对给定名集合批量 upsert 启用态后 `commit`（「全部启用 / 全部禁用」用）。"""
    stmt = select(AgentToolState).where(AgentToolState.name.in_(names))
    existing = {row.name: row for row in (await session.execute(stmt)).scalars().all()}
    for name in names:
        row = existing.get(name)
        if row is None:
            session.add(AgentToolState(name=name, enabled=enabled))
        else:
            row.enabled = enabled
    await session.commit()


# ============================ OKX 实盘账户（设置中心 · 实盘账户） ============================
@dataclass(frozen=True)
class OkxAccountValues:
    """新增账户的可写字段集合（聚合传参，避免位置参数堆叠）。"""

    label: str
    api_key: str
    secret_key: str
    passphrase: str
    ip_whitelist: str
    permissions: str


@dataclass(frozen=True)
class OkxAccountPatch:
    """编辑账户的部分字段（None = 不改；`secret_key`/`passphrase` 空/None = 保留原值）。"""

    label: str | None = None
    api_key: str | None = None
    secret_key: str | None = None
    passphrase: str | None = None
    ip_whitelist: str | None = None
    permissions: str | None = None


async def list_okx_accounts(session: AsyncSession) -> list[OkxAccount]:
    """全量账户，按 id 升序（展示顺序稳定）。"""
    stmt = select(OkxAccount).order_by(OkxAccount.id.asc())
    return list((await session.execute(stmt)).scalars().all())


async def get_okx_account(session: AsyncSession, account_id: int) -> OkxAccount | None:
    return await session.get(OkxAccount, account_id)


async def get_default_okx_account(session: AsyncSession) -> OkxAccount | None:
    stmt = select(OkxAccount).where(OkxAccount.is_default == True)  # noqa: E712
    return (await session.execute(stmt)).scalars().first()


async def create_okx_account(session: AsyncSession, *, values: OkxAccountValues,
                             is_default: bool = False) -> OkxAccount:
    """新增账户。secret_key/passphrase 原样列存（仅后端，绝不出接口 / 不进日志，C7）。"""
    row = OkxAccount(
        label=values.label,
        api_key=values.api_key or "",
        secret_key=values.secret_key or "",
        passphrase=values.passphrase or "",
        ip_whitelist=values.ip_whitelist or "",
        permissions=values.permissions or "",
        is_default=is_default,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def update_okx_account(session: AsyncSession, account_id: int, *,
                             patch: OkxAccountPatch) -> OkxAccount | None:
    """编辑账户；`secret_key`/`passphrase` 空则保留原值（前端不持有明文，R-Secret）。"""
    row = await session.get(OkxAccount, account_id)
    if row is None:
        return None
    if patch.label is not None:
        row.label = patch.label
    if patch.api_key is not None:
        row.api_key = patch.api_key
    if patch.secret_key:  # 非空才更新（空/None = 保留原值）
        row.secret_key = patch.secret_key
    if patch.passphrase:  # 非空才更新（空/None = 保留原值）
        row.passphrase = patch.passphrase
    if patch.ip_whitelist is not None:
        row.ip_whitelist = patch.ip_whitelist
    if patch.permissions is not None:
        row.permissions = patch.permissions
    await session.commit()
    await session.refresh(row)
    return row


async def delete_okx_account(session: AsyncSession, account_id: int) -> str:
    """返回 'ok' | 'not_found'。删默认后自动回落（id 最小者，P2-Fallback）。"""
    row = await session.get(OkxAccount, account_id)
    if row is None:
        return "not_found"
    was_default = bool(row.is_default)
    await session.delete(row)
    await session.commit()
    if was_default:
        await _reassign_default_okx_after_delete(session)
    return "ok"


async def set_default_okx_account(session: AsyncSession, account_id: int) -> bool:
    """事务内清其余 `is_default` 再置本行，杜绝多默认（R-Default）。不存在返回 False。

    用 ORM 逐行置位（至多一行原默认）而非批量 UPDATE：批量 UPDATE + `expire_on_commit=False`
    会令同 session 内已加载对象的 `is_default` 滞留旧值（延续 provider 经验）。
    """
    target = await session.get(OkxAccount, account_id)
    if target is None:
        return False
    stmt = select(OkxAccount).where(OkxAccount.is_default == True)  # noqa: E712
    for row in (await session.execute(stmt)).scalars().all():
        row.is_default = False
    target.is_default = True
    await session.commit()
    return True


async def _reassign_default_okx_after_delete(session: AsyncSession) -> None:
    """删默认后选 id 最小者为新默认（确定可复现）；空表则不设默认。"""
    stmt = select(OkxAccount).order_by(OkxAccount.id.asc()).limit(1)
    row = (await session.execute(stmt)).scalars().first()
    if row is not None:
        await set_default_okx_account(session, row.id)


async def count_okx_accounts(session: AsyncSession) -> int:
    rows = (await session.execute(select(OkxAccount.id))).all()
    return len(rows)


# ============================ 实盘下单审计（实盘交易模式 §5.1） ============================
@dataclass(frozen=True)
class LiveOrderAuditValues:
    """一条下单审计留痕的字段集合（聚合传参；绝不含任何密钥，C7）。"""

    account_id: int
    inst_id: str
    direction: str
    ord_type: str
    td_mode: str
    px: float | None
    sz: float
    lever: float | None
    tp_trigger_px: float | None
    sl_trigger_px: float | None
    status: str
    cl_ord_id: str
    ord_id: str
    error_code: str
    created_ts: int


async def create_live_order_audit(session: AsyncSession, *,
                                  values: LiveOrderAuditValues) -> LiveOrderAudit:
    """落一行下单审计（accepted/rejected/error 均留痕；订单真源仍在 OKX）。"""
    row = LiveOrderAudit(
        account_id=values.account_id, inst_id=values.inst_id, direction=values.direction,
        ord_type=values.ord_type, td_mode=values.td_mode, px=values.px, sz=values.sz,
        lever=values.lever, tp_trigger_px=values.tp_trigger_px, sl_trigger_px=values.sl_trigger_px,
        status=values.status, cl_ord_id=values.cl_ord_id, ord_id=values.ord_id,
        error_code=values.error_code, created_ts=values.created_ts)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def list_live_order_audit(session: AsyncSession, *, account_id: int | None = None,
                                limit: int = 100) -> list[LiveOrderAudit]:
    """按 id 降序（最新在前）列审计行，可按账户过滤。"""
    stmt = select(LiveOrderAudit)
    if account_id is not None:
        stmt = stmt.where(LiveOrderAudit.account_id == account_id)
    stmt = stmt.order_by(LiveOrderAudit.id.desc()).limit(limit)
    return list((await session.execute(stmt)).scalars().all())


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


async def list_factors(session: AsyncSession, *, enabled: bool | None = None,
                       category: str | None = None) -> list[Factor]:
    """已存因子列表（builtin 在前、同类按 id 升序），支持激活态 / 类别过滤。"""
    stmt = select(Factor).order_by((Factor.source != "builtin"), Factor.id.asc())
    if enabled is not None:
        stmt = stmt.where(Factor.enabled == enabled)
    if category:
        stmt = stmt.where(Factor.category == category)
    return list((await session.execute(stmt)).scalars().all())


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
