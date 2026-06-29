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
    AppSetting,
    CandleCache,
    EquityPoint,
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


async def list_explained_trades(session: AsyncSession, *, since_ts: int | None = None,
                                limit: int = 1000) -> list[Trade]:
    """取「带解释」的 trade 行（explain_json 非空、可选 ts>=since_ts、按 ts 倒序限量）。

    供实盘成交记录富化用（live-fills-explainability §2.3）：只回带自动交易解释行，自然排除
    回测 / 无解释行；``since_ts`` 把扫描收敛到当前 fills 时间窗内，单查询、小结果集。
    """
    stmt = select(Trade).where(Trade.explain_json.is_not(None))
    if since_ts is not None:
        stmt = stmt.where(Trade.ts >= since_ts)
    stmt = stmt.order_by(Trade.ts.desc()).limit(limit)
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


async def append_live_trade(session: AsyncSession, run_id: int, trade: dict) -> None:
    """把实盘自动交易的 trade dict（含 explain）落 Trade 行 + explain_json（auto-trade-explainability §2.5）。

    trade 为 LiveExecutor 产出的 camelCase dict（与 WS 扇出同构）；explain 序列化进 explain_json，
    缺失则置 NULL。**镜像 append_trade 的「自带 commit」语义**——否则 best-effort 落库不持久、
    刷新后历史触发丢失（R12）。调用方（session._persist_trade）已 try/except 兜底，落库失败不阻塞交易。
    """
    explain = trade.get("explain")
    explain_json = json.dumps(explain, ensure_ascii=False) if explain is not None else None
    session.add(Trade(
        run_id=run_id, ts=int(trade.get("ts", 0)), side=str(trade.get("side", "")),
        price=float(trade.get("price", 0.0)), qty=float(trade.get("qty", 0.0)),
        fee=float(trade.get("fee", 0.0) or 0.0), pnl=float(trade.get("pnl", 0.0) or 0.0),
        position_after=float(trade.get("positionAfter", 0.0) or 0.0),
        reason=str(trade.get("reason", ""))[:64],
        stop_loss_pct=trade.get("stopLossPct"), take_profit_pct=trade.get("takeProfitPct"),
        max_hold_bars=trade.get("maxHoldBars"), stop_loss_price=trade.get("stopLossPrice"),
        take_profit_price=trade.get("takeProfitPrice"), explain_json=explain_json))
    await session.commit()


async def append_equity(session: AsyncSession, run_id: int, e: EquityRecord) -> None:
    session.add(EquityPoint(run_id=run_id, ts=e.ts, equity=e.equity, drawdown=e.drawdown))
    await session.commit()


async def finalize_run(session: AsyncSession, run_id: int, *, status: str,
                       metrics: dict | None = None, end_ts: int | None = None) -> StrategyRun | None:
    """更新 Run 终态 / 指标（仅 UPDATE，仿 append_* 提交模式，live-auto-trading R2）。

    `create_run` 已建 running 行；本函数在自动会话停止 / 会话级止盈止损触发时落终态指标，
    使运行历史可复现。Run 不存在返回 None（绝不静默新建）。
    """
    run = await session.get(StrategyRun, run_id)
    if run is None:
        return None
    run.status = status
    if metrics is not None:
        run.metrics_json = json.dumps(metrics, ensure_ascii=False)
    if end_ts is not None:
        run.end_ts = end_ts
    await session.commit()
    return run


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


# ============================ App settings（设置中心通用 KV） ============================
_AUTO_TRADE_MAX_BUDGET_KEY = "auto_trade.max_budget_pct"
_DEFAULT_AUTO_TRADE_MAX_BUDGET_PCT = 100.0


async def get_app_setting(session: AsyncSession, key: str, default: object = None) -> object:
    """读单个设置（JSON 解码）；无记录 / 坏 JSON → 回 ``default``（稀疏表，C6 风格容错）。"""
    row = await session.get(AppSetting, key)
    if row is None:
        return default
    try:
        return json.loads(row.value)
    except (json.JSONDecodeError, TypeError, ValueError):
        return default


async def set_app_setting(session: AsyncSession, key: str, value: object) -> None:
    """按 key upsert 单个设置（JSON 编码）后 ``commit``（select-then-update / 否则 insert）。"""
    encoded = json.dumps(value, ensure_ascii=False)
    row = await session.get(AppSetting, key)
    if row is None:
        session.add(AppSetting(key=key, value=encoded))
    else:
        row.value = encoded
    await session.commit()


async def get_auto_trade_settings(session: AsyncSession) -> dict:
    """读自动交易设置（缺记录 / 越界回默认 maxBudgetPct=100）。服务端为分组预算上限唯一权威。"""
    raw = await get_app_setting(session, _AUTO_TRADE_MAX_BUDGET_KEY, _DEFAULT_AUTO_TRADE_MAX_BUDGET_PCT)
    try:
        pct = float(raw)
    except (TypeError, ValueError):
        pct = _DEFAULT_AUTO_TRADE_MAX_BUDGET_PCT
    if not (0 < pct <= 100):
        pct = _DEFAULT_AUTO_TRADE_MAX_BUDGET_PCT
    return {"maxBudgetPct": pct}


async def set_auto_trade_settings(session: AsyncSession, *, max_budget_pct: float) -> dict:
    """写自动交易总预算上限百分比（边界已由 schema 在路由层校验 0<pct≤100），返回回写后值。"""
    await set_app_setting(session, _AUTO_TRADE_MAX_BUDGET_KEY, max_budget_pct)
    return {"maxBudgetPct": max_budget_pct}


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


# ============================ 因子（因子层 §5.1） —— 已拆至 factor_repositories.py（R5） ============================
# 显式 re-export 全部公开符号，保持所有现有 repo.X 调用点零改动（私有 _* 随模块内引用走，不外露）。
from .factor_repositories import (  # noqa: E402,F401
    FactorPatch,
    FactorSetPatch,
    FactorSetValues,
    FactorValues,
    bulk_create_factors,
    create_factor,
    create_factor_set,
    delete_factor,
    delete_factor_set,
    get_factor,
    get_factor_set,
    list_factor_sets,
    list_factors,
    set_factor_eval,
    update_factor,
    update_factor_set,
)
