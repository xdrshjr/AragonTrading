"""轴 B 端点与查询层（真实 SQLite 集成，不 mock DB）：G-6 / G-6b / G-6c / G-21 / G-23。

与既有 quant 套件共享临时 DB，故每用例先清掉本套件自造的 `aiprof_` / ai-trade 行。
"""
from __future__ import annotations

import json

from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, or_, select

from app.ai_auto.trip_ledger import SettleContext, open_snapshot, settle
from app.config import get_settings
from app.db import repositories as repo
from app.db.models import Strategy, StrategyRun, Trade
from app.db.session import get_sessionmaker, init_db


async def _reset() -> int:
    await init_db()
    sm = get_sessionmaker()
    async with sm() as s:
        run_ids = (await s.execute(
            select(StrategyRun.id).where(
                or_(StrategyRun.mode == "ai-trade",
                    StrategyRun.group_id.like("aiprof_%"))))).scalars().all()
        if run_ids:
            await s.execute(delete(Trade).where(Trade.run_id.in_(run_ids)))
            await s.execute(delete(StrategyRun).where(StrategyRun.id.in_(run_ids)))
        await s.commit()
        return (await s.execute(
            select(Strategy.id).order_by(Strategy.id.asc()).limit(1))).scalar_one()


async def _add_run(session, *, strategy_id: int, group_id: str, inst_id: str = "BTC-USDT-SWAP",
                   bar: str = "15m", cost_basis: str | None = "gross",
                   preset: str = "balanced") -> int:
    config = {"budgetUsd": 100.0, "leverage": 3.0, "allocationPct": 100.0,
              "strategyNames": ["动量"], "groupId": group_id, "totalBudgetUsd": 100.0,
              "bar": bar}
    if cost_basis is not None:
        config["costBasis"] = cost_basis
    run = StrategyRun(
        strategy_id=strategy_id, inst_id=inst_id, bar=bar, mode="ai-trade", status="stopped",
        start_ts=1000, end_ts=2000,
        config_json=json.dumps(config, ensure_ascii=False),
        metrics_json=json.dumps({"aiStats": {"disciplinePreset": preset}}, ensure_ascii=False),
        group_id=group_id, group_started_ts=1000, group_total_budget_usd=100.0)
    session.add(run)
    await session.commit()
    await session.refresh(run)
    return run.id


async def _add_trip(session, run_id: int, *, entry: float, exit_px: float, pnl: float,
                    fee: float = 0.0, stop: float | None = None, take: float | None = None,
                    meta: dict | None = None, ts: int = 100, exit_ts: int | None = None,
                    entry_wall_ts: int | None = None, exit_wall_ts: int | None = None,
                    fee_known: bool = True) -> None:
    """造一笔往返。

    `entry_wall_ts` / `exit_wall_ts` / `exit_ts` 均为关键字且默认 `None`（既有调用点零改）：
    它们用来复刻 spec D3 的双时钟形状——`ts` 被吸附到 K 线开盘秒，真值另存
    `explain_json.meta.wallTs`；默认只带费用来源、不带结算派生键。
    """
    fee_meta = ({"feeBasis": "measured", "feeKnown": True, "feeCurrency": "USDT"}
                if fee_known else {})
    entry_meta = dict(fee_meta)
    if entry_wall_ts is not None:
        entry_meta["wallTs"] = entry_wall_ts
    entry_explain = {"kind": "atomic", "meta": entry_meta}
    session.add(Trade(run_id=run_id, ts=ts, side="long", price=entry, qty=10.0, fee=fee,
                      pnl=0.0, position_after=10.0, reason="", stop_loss_price=stop,
                      take_profit_price=take,
                      explain_json=(json.dumps(entry_explain, ensure_ascii=False)
                                    if entry_explain else None)))
    await session.commit()
    exit_meta = {**fee_meta, "exitSource": "agent", **(meta or {})}
    if exit_wall_ts is not None:
        exit_meta["wallTs"] = exit_wall_ts
    explain = {"kind": "atomic", "meta": exit_meta}
    session.add(Trade(run_id=run_id, ts=(ts + 100 if exit_ts is None else exit_ts),
                      side="close", price=exit_px, qty=10.0,
                      fee=fee, pnl=pnl, position_after=0.0, reason="",
                      explain_json=json.dumps(explain, ensure_ascii=False)))
    await session.commit()


def _load_app():
    from app.main import app
    return app


async def test_query_layer_recomputes_the_legacy_readings():
    """历史无结算派生键，只要原始口径和费用来源完整，仍复算净额、位移和 R。"""
    sid = await _reset()
    sm = get_sessionmaker()
    async with sm() as s:
        run = await _add_run(s, strategy_id=sid, group_id="aiprof_legacy")
        await _add_trip(s, run, entry=100.0, exit_px=101.0, pnl=0.1, stop=95.0, take=110.0)
        trips = await repo.load_profitability_trips(s, repo.TripQuery())
    assert len(trips) == 1
    row = trips[0]
    assert row["moveBps"] == 100.0 and row["plannedRr"] == 2.0
    assert row["feeKnown"] is True and row["realizedCostBps"] == 0
    assert row["rMultiple"] is not None and row["exitSource"] == "agent"
    assert row["bar"] == "15m" and row["preset"] == "balanced"


async def test_hold_seconds_uses_per_leg_wall_clock():
    """spec D3：两腿落在同一根 bar（`ts` 相同）时，持仓时长必须来自 `wallTs` 而不是被吸附的 `ts`。

    旧实现在这条形状上恒报 0——一笔 20 分钟的往返被 1H 的 bar 对齐整个量化掉。
    """
    sid = await _reset()
    sm = get_sessionmaker()
    async with sm() as s:
        run = await _add_run(s, strategy_id=sid, group_id="aiprof_hold", bar="1H")
        await _add_trip(s, run, entry=100.0, exit_px=101.0, pnl=0.1,
                        ts=3600, exit_ts=3600,                   # 同一根 1H bar 的开盘秒
                        entry_wall_ts=4020, exit_wall_ts=5220)   # 09:07 → 09:27
        trips = await repo.load_profitability_trips(s, repo.TripQuery())
    assert trips[0]["holdSeconds"] == 1200


async def test_hold_seconds_falls_back_to_bar_ts_for_legacy_trips():
    """老数据（两腿都无 `wallTs`）逐值保持修改前的行为：`exit.ts − entry.ts`。"""
    sid = await _reset()
    sm = get_sessionmaker()
    async with sm() as s:
        run = await _add_run(s, strategy_id=sid, group_id="aiprof_hold_legacy", bar="1H")
        await _add_trip(s, run, entry=100.0, exit_px=101.0, pnl=0.1, ts=3600, exit_ts=7200)
        trips = await repo.load_profitability_trips(s, repo.TripQuery())
    assert trips[0]["holdSeconds"] == 3600


async def test_hold_seconds_is_per_leg_when_exit_has_no_wall_ts():
    """`offline_exit` 形状：平仓腿自带 explain 却**永远没有** `wallTs`，其 `ts` 却是真实墙钟秒。

    故取值必须**逐腿** `wallTs ?? ts`（= `exit.ts − entry.wallTs`）。若按「双腿全有才用墙钟」，
    这笔往返会退回「墙钟 − bar 对齐」——正是本条要消灭的混合口径（spec 评审 P1-3）。
    """
    sid = await _reset()
    sm = get_sessionmaker()
    async with sm() as s:
        run = await _add_run(s, strategy_id=sid, group_id="aiprof_hold_offline", bar="1H")
        await _add_trip(s, run, entry=100.0, exit_px=101.0, pnl=0.1,
                        ts=3600, entry_wall_ts=4020,   # 开仓腿：ts 为 bar 开盘秒，真值在 wallTs
                        exit_ts=5220)                  # 平仓腿：无 wallTs，ts 即交易所 uTime（墙钟）
        trips = await repo.load_profitability_trips(s, repo.TripQuery())
    assert trips[0]["holdSeconds"] == 1200             # 5220 − 4020，绝不是 5220 − 3600


async def test_g6_live_settlement_and_retrospective_recompute_agree():
    """G-6（阻断级）双向对拍：同一笔往返走实盘结算与走追溯复算，四个键的相对误差 < 1e-6。

    构造的必须是一笔**自洽**的往返：`qty=10 × ctVal=0.01 ⇒ unit=0.1`，价 100→102 ⇒ 毛额 0.2，
    双腿费 0.1 ⇒ 净额 0.1。追溯侧的 `unitUsd = |毛额| / |Δ价|` 正是靠这条恒等式代数抵消掉 `ctVal`
    （它不在 `trades` 表里）——喂一笔算术上不成立的成交，对拍失败的是数据而不是代码。
    """
    sid = await _reset()
    sm = get_sessionmaker()
    async with sm() as s:
        run = await _add_run(s, strategy_id=sid, group_id="aiprof_xcheck", cost_basis="net")
        await _add_trip(s, run, entry=100.0, exit_px=102.0, pnl=0.1, fee=0.05,
                        stop=95.0, take=110.0)
        trips = await repo.load_profitability_trips(s, repo.TripQuery())
    row = trips[0]
    snap = open_snapshot({"price": 100.0, "qty": 10.0, "fee": 0.05, "ts": 100, "side": "long",
                          "feeKnown": True, "feeBasis": "measured",
                          "stopLossPrice": 95.0, "takeProfitPrice": 110.0},
                         seq=1, now_wall=0.0, atr_px=None, ct_val=0.01, estimate=None)
    live = settle(snap, {"price": 102.0, "qty": 10.0, "fee": 0.05, "pnl": 0.1, "ts": 200,
                        "feeKnown": True, "feeBasis": "measured"},
                  SettleContext(net_mode=True, ct_val=0.01, exit_source="agent"))
    for retro, direct in (("notionalUsd", live.notional_usd),
                          ("roundTripFeeUsd", live.fee_usd),
                          ("realizedCostBps", live.realized_cost_bps),
                          ("rMultiple", live.r_multiple)):
        assert abs(row[retro] - direct) / max(1e-9, abs(direct)) < 1e-6, retro


async def test_g6c_cost_basis_is_resolved_per_run_and_counted():
    """原 run 净毛口径各自复算；缺失口径的 run 明确标未知，不能进入收益学习样本。"""
    sid = await _reset()
    sm = get_sessionmaker()
    async with sm() as s:
        known = await _add_run(s, strategy_id=sid, group_id="aiprof_known", cost_basis="net")
        gross = await _add_run(s, strategy_id=sid, group_id="aiprof_gross", cost_basis="gross")
        legacy = await _add_run(s, strategy_id=sid, group_id="aiprof_legacy2", cost_basis=None)
        await _add_trip(s, known, entry=100.0, exit_px=101.0, pnl=0.1, fee=0.05)
        await _add_trip(s, gross, entry=100.0, exit_px=101.0, pnl=0.1, fee=0.05)
        await _add_trip(s, legacy, entry=100.0, exit_px=101.0, pnl=0.1, fee=0.05)
        trips = await repo.load_profitability_trips(s, repo.TripQuery())
        detail = await repo.get_ai_trade_group_detail(s, "aiprof_legacy2")
    assert len(trips) == 2
    assert sorted(row["netPnlUsd"] for row in trips) == [0.0, 0.1]
    assert not any(row["costBasisAssumed"] for row in trips)
    economics = detail["summary"]["economics"]
    assert economics["unknownTripCount"] == 1 and economics["coverage"] == "legacy_partial"
    assert economics["tradingNetUsd"] is None and economics["grossPnlUsd"] is None


async def test_legacy_zero_without_fee_source_is_excluded_and_stays_unknown():
    sid = await _reset()
    sm = get_sessionmaker()
    async with sm() as session:
        run = await _add_run(session, strategy_id=sid, group_id="aiprof_unknown_fee")
        await _add_trip(session, run, entry=100, exit_px=101, pnl=.1, fee_known=False)
        assert await repo.load_profitability_trips(session, repo.TripQuery()) == []
        detail = await repo.get_ai_trade_group_detail(session, "aiprof_unknown_fee")
    economics = detail["summary"]["economics"]
    assert economics["feeUsd"] is None and economics["tradingNetUsd"] is None
    assert economics["grossPnlUsd"] == .1 and economics["unknownTripCount"] == 1


async def test_g21_route_order_profitability_is_not_swallowed_by_group_id():
    """G-21（[P1-5]）：`/history/profitability` 返回 200 且**不是**详情的形状（没被路径参数吞掉）。"""
    sid = await _reset()
    sm = get_sessionmaker()
    async with sm() as s:
        run = await _add_run(s, strategy_id=sid, group_id="aiprof_route")
        await _add_trip(s, run, entry=100.0, exit_px=101.0, pnl=0.1, stop=95.0)
    async with AsyncClient(transport=ASGITransport(app=_load_app()),
                           base_url="http://test") as client:
        res = await client.get("/api/ai-trade/history/profitability")
    assert res.status_code == 200
    body = res.json()
    assert "pooled" in body and "verdict" in body
    assert "members" not in body and "groupId" not in body      # 详情的形状一个都不该出现


async def test_endpoint_returns_full_shape_even_with_no_history():
    """空历史 ⇒ 结构完整的回执（不 404、不 500）；「样本不足」不等于「藏起来」。"""
    await _reset()
    async with AsyncClient(transport=ASGITransport(app=_load_app()),
                           base_url="http://test") as client:
        res = await client.get("/api/ai-trade/history/profitability")
    body = res.json()
    assert res.status_code == 200 and body["sampleTrips"] == 0
    assert body["verdict"]["code"] == "insufficient_samples"
    assert set(body["calibration"]) == {"edgeNumerator", "costModel", "confidenceReliability"}


async def test_endpoint_filters_by_inst_and_bar():
    """过滤参数：`instId` / `bar` 各自生效（轴 D 靠它按 `(instId,bar)` 取战绩）。"""
    sid = await _reset()
    sm = get_sessionmaker()
    async with sm() as s:
        btc = await _add_run(s, strategy_id=sid, group_id="aiprof_btc", inst_id="BTC-USDT-SWAP")
        eth = await _add_run(s, strategy_id=sid, group_id="aiprof_eth", inst_id="ETH-USDT-SWAP")
        await _add_trip(s, btc, entry=100.0, exit_px=101.0, pnl=0.1)
        await _add_trip(s, eth, entry=100.0, exit_px=99.0, pnl=-0.1)
    async with AsyncClient(transport=ASGITransport(app=_load_app()),
                           base_url="http://test") as client:
        btc_res = await client.get("/api/ai-trade/history/profitability",
                                   params={"instId": "BTC-USDT-SWAP", "bar": "15m"})
        other_bar = await client.get("/api/ai-trade/history/profitability",
                                     params={"bar": "1D"})
    assert btc_res.json()["pooled"]["roundTrips"] == 1
    assert other_bar.json()["pooled"]["roundTrips"] == 0


async def test_g23_bracket_survives_the_response_model(monkeypatch, synthetic_candles):
    """G-23（[P0-6]）：`bracket` 穿透 `response_model`——手造 wire 的纯前端用例守不住这条。"""
    settings = get_settings()
    assert settings.ai_trade_bracket_check_enabled
    from types import SimpleNamespace
    from app.ai_auto import tradability_probe

    requested = []

    async def get_candles(inst_id, bar, limit):
        requested.append((inst_id, bar))
        return [SimpleNamespace(**row) for row in synthetic_candles[-limit:]]

    monkeypatch.setattr(tradability_probe, "get_rest_client",
                        lambda: SimpleNamespace(get_candles=get_candles))
    async with AsyncClient(transport=ASGITransport(app=_load_app()),
                           base_url="http://test") as client:
        res = await client.get("/api/ai-trade/tradability",
                               params={"instId": "BTC-USDT-SWAP", "bar": "15m"})
    assert res.status_code == 200
    # 使用脱网行情走真实探针与响应模型，确保真实非空 bracket 没被 FastAPI 裁掉。
    assert "bracket" in res.json()
    assert res.json()["bracket"] is not None
    assert ("BTC-USDT-SWAP", "15m") in requested
