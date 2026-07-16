"""AI 自动交易统计历史后端单测（ai-trade-stats-dashboard §7.1，真实 SQLite 集成、不 mock DB）。

覆盖：分组列已填（create_run group_* 落库）/ 列表聚合口径 + 分页 + exclude / 详情逐成员（含
metrics/allocationPct/strategyNames、逐成员配对不跨 instId）/ finalize 富化纯函数 / 孤儿对账幂等不误伤 /
REST 端点（list/detail/404）。P2-7：与既有 quant 套件共享临时 DB，每用例先清 ``aihist_`` / ai-trade
自造行自清理，不污染其它套件。
"""
from __future__ import annotations

import json

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, or_, select

from app.ai_auto.session import _compute_ai_stats, _digest_decisions, _downsample_equity
from app.db import repositories as repo
from app.db.ai_auto_repositories import (
    get_ai_trade_group_detail,
    list_ai_trade_groups,
    reconcile_orphan_ai_trade_runs,
)
from app.db.models import Strategy, StrategyRun, Trade
from app.db.session import get_sessionmaker, init_db


async def _reset() -> int:
    """建库 + 清掉本套件自造的 ai-trade / aihist_ 行（跨文件共享临时库时保证隔离）；回一个可用 strategy_id。"""
    await init_db()
    sm = get_sessionmaker()
    async with sm() as s:
        run_ids = (await s.execute(
            select(StrategyRun.id).where(
                or_(StrategyRun.mode == "ai-trade",
                    StrategyRun.group_id.like("aihist_%"))))).scalars().all()
        if run_ids:
            await s.execute(delete(Trade).where(Trade.run_id.in_(run_ids)))
            await s.execute(delete(StrategyRun).where(StrategyRun.id.in_(run_ids)))
        await s.commit()
        return (await s.execute(
            select(Strategy.id).order_by(Strategy.id.asc()).limit(1))).scalar_one()


async def _add_run(session, *, strategy_id: int, group_id: str | None, inst_id: str,
                   budget: float, alloc_pct: float, strategy_names: list[str],
                   total_budget: float, group_started_ts: int, start_ts: int,
                   status: str = "stopped", end_ts: int | None = None,
                   leverage: float = 3.0, mode: str = "ai-trade",
                   metrics: dict | None = None) -> int:
    """直插一条 run（全字段可控，供构造历史 / 孤儿场景）。"""
    run = StrategyRun(
        strategy_id=strategy_id, inst_id=inst_id, bar="1H", mode=mode, status=status,
        start_ts=start_ts, end_ts=end_ts,
        config_json=json.dumps({"budgetUsd": budget, "leverage": leverage,
                                "allocationPct": alloc_pct, "strategyNames": strategy_names,
                                "groupId": group_id, "totalBudgetUsd": total_budget},
                               ensure_ascii=False),
        metrics_json=json.dumps(metrics, ensure_ascii=False) if metrics is not None else None,
        group_id=group_id, group_started_ts=group_started_ts,
        group_total_budget_usd=total_budget)
    session.add(run)
    await session.commit()
    await session.refresh(run)
    return run.id


async def _add_trade(session, run_id: int, ts: int, side: str, price: float,
                     pnl: float = 0.0) -> None:
    session.add(Trade(run_id=run_id, ts=ts, side=side, price=price, qty=1.0, fee=0.0,
                      pnl=pnl, position_after=0.0, reason=""))
    await session.commit()


async def _seed_two_groups(session, strategy_id: int) -> None:
    """组 A（单标的 BTC，realized=+8）起点 1000；组 B（BTC+ETH，realized=+15）起点 2000（更晚→列表居首）。"""
    a = await _add_run(session, strategy_id=strategy_id, group_id="aihist_A", inst_id="BTC-USDT-SWAP",
                       budget=100, alloc_pct=100, strategy_names=["动量", "均值回归"],
                       total_budget=100, group_started_ts=1000, start_ts=1000, end_ts=40)
    await _add_trade(session, a, 10, "long", 100.0)
    await _add_trade(session, a, 20, "close", 110.0, pnl=12.0)
    await _add_trade(session, a, 30, "long", 100.0)
    await _add_trade(session, a, 40, "close", 96.0, pnl=-4.0)

    b_btc = await _add_run(session, strategy_id=strategy_id, group_id="aihist_B", inst_id="BTC-USDT-SWAP",
                           budget=100, alloc_pct=50, strategy_names=["动量"],
                           total_budget=200, group_started_ts=2000, start_ts=2000, end_ts=120)
    await _add_trade(session, b_btc, 110, "long", 100.0)
    await _add_trade(session, b_btc, 120, "close", 105.0, pnl=5.0)
    b_eth = await _add_run(session, strategy_id=strategy_id, group_id="aihist_B", inst_id="ETH-USDT-SWAP",
                           budget=100, alloc_pct=50, strategy_names=["动量"],
                           total_budget=200, group_started_ts=2000, start_ts=2000, end_ts=125)
    await _add_trade(session, b_eth, 115, "short", 50.0)
    await _add_trade(session, b_eth, 125, "close", 40.0, pnl=10.0)


# ---------------- 分组列已填（create_run 路径，_create_group_run 即用此签名）----------------
async def test_create_run_fills_group_columns() -> None:
    sid = await _reset()
    sm = get_sessionmaker()
    async with sm() as s:
        run = await repo.create_run(
            s, strategy_id=sid, inst_id="BTC-USDT-SWAP", bar="1H", mode="ai-trade",
            config={"strategyNames": ["动量"], "budgetUsd": 100}, status="running",
            group_id="aihist_cols", group_started_ts=1234, group_total_budget_usd=100.0)
        fetched = await s.get(StrategyRun, run.id)
        assert fetched.group_id == "aihist_cols"
        assert fetched.group_started_ts == 1234
        assert fetched.group_total_budget_usd == 100.0


# ---------------- 列表聚合 + 排序 + 分页 + exclude ----------------
async def test_list_groups_aggregation_and_order() -> None:
    sid = await _reset()
    sm = get_sessionmaker()
    async with sm() as s:
        await _seed_two_groups(s, sid)
        out = await list_ai_trade_groups(s, limit=20, offset=0)
    assert out["total"] == 2
    ids = [it["groupId"] for it in out["items"]]
    assert ids == ["aihist_B", "aihist_A"]   # startedTs DESC（2000 → 1000）
    b, a = out["items"][0], out["items"][1]
    # 组 A：realized=12-4=8、returnPct=8/100、2 笔、winRate=0.5、best=12、worst=-4
    assert a["realizedPnlUsd"] == pytest.approx(8.0)
    assert a["returnPct"] == pytest.approx(0.08)
    assert a["tradeCount"] == 2 and a["winRate"] == pytest.approx(0.5)
    assert a["bestUsd"] == pytest.approx(12.0) and a["worstUsd"] == pytest.approx(-4.0)
    assert a["strategyName"] == "动量 / 均值回归" and a["instIds"] == ["BTC-USDT-SWAP"]
    # 组 B：realized=5+10=15、returnPct=15/200、2 笔、winRate=1.0、逐成员不跨 instId
    assert b["realizedPnlUsd"] == pytest.approx(15.0)
    assert b["returnPct"] == pytest.approx(0.075)
    assert b["tradeCount"] == 2 and b["winRate"] == pytest.approx(1.0)
    assert set(b["instIds"]) == {"BTC-USDT-SWAP", "ETH-USDT-SWAP"}


async def test_list_groups_pagination_and_exclude() -> None:
    sid = await _reset()
    sm = get_sessionmaker()
    async with sm() as s:
        await _seed_two_groups(s, sid)
        page0 = await list_ai_trade_groups(s, limit=1, offset=0)
        page1 = await list_ai_trade_groups(s, limit=1, offset=1)
        excluded = await list_ai_trade_groups(s, limit=20, offset=0, exclude_group_id="aihist_B")
    assert page0["total"] == 2 and [i["groupId"] for i in page0["items"]] == ["aihist_B"]
    assert page1["total"] == 2 and [i["groupId"] for i in page1["items"]] == ["aihist_A"]
    assert [i["groupId"] for i in excluded["items"]] == ["aihist_A"]


async def test_list_empty_history() -> None:
    await _reset()
    sm = get_sessionmaker()
    async with sm() as s:
        out = await list_ai_trade_groups(s, limit=20, offset=0)
    assert out == {"items": [], "total": 0, "limit": 20, "offset": 0}


# ---------------- 详情：逐成员 + metrics + 不跨 instId + 组不存在 ----------------
async def test_detail_members_and_summary() -> None:
    sid = await _reset()
    enriched = {"totalReturn": 0.05, "finalSummary": "稳健", "reconciled": False,
                "equityDigest": [{"ts": 110, "equity": 100.0, "drawdown": 0.0},
                                 {"ts": 120, "equity": 105.0, "drawdown": 0.0}],
                "aiStats": {"decisionRounds": 3, "actionCounts": {"open_long": 1, "hold": 2},
                            "holdRate": 0.667, "avgConfidence": 0.7, "degradedRounds": 0}}
    sm = get_sessionmaker()
    async with sm() as s:
        await _seed_two_groups(s, sid)
        # 给组 B 的 BTC 成员补富化 metrics（验证详情能透传解析后的 metrics）
        btc_run_id = (await s.execute(
            select(StrategyRun.id).where(StrategyRun.group_id == "aihist_B",
                                         StrategyRun.inst_id == "BTC-USDT-SWAP"))).scalar_one()
        await repo.finalize_run(s, btc_run_id, status="stopped", metrics=enriched, end_ts=120)
        detail = await get_ai_trade_group_detail(s, "aihist_B")
        missing = await get_ai_trade_group_detail(s, "aihist_missing")
    assert missing is None
    assert detail["groupId"] == "aihist_B" and detail["totalBudgetUsd"] == pytest.approx(200.0)
    assert detail["summary"]["realizedPnlUsd"] == pytest.approx(15.0)
    members = {m["instId"]: m for m in detail["members"]}
    btc, eth = members["BTC-USDT-SWAP"], members["ETH-USDT-SWAP"]
    assert btc["allocationPct"] == pytest.approx(50.0) and btc["strategyNames"] == ["动量"]
    assert btc["leverage"] == pytest.approx(3.0) and btc["budgetUsd"] == pytest.approx(100.0)
    assert btc["metrics"]["finalSummary"] == "稳健"
    assert btc["metrics"]["equityDigest"][-1]["equity"] == pytest.approx(105.0)
    assert len(btc["trades"]) == 2 and len(eth["trades"]) == 2   # ORM Trade（API 层再 trade_out）


# ---------------- finalize 富化纯函数 ----------------
def test_downsample_equity_keeps_endpoints_and_never_throws() -> None:
    assert _downsample_equity([], 500) == []
    small = [{"ts": i, "equity": 100.0 + i, "drawdown": 0.0} for i in range(3)]
    assert _downsample_equity(small, 500) == small
    large = [{"ts": i, "equity": 100.0 + i, "drawdown": 0.0} for i in range(1200)]
    ds = _downsample_equity(large, 500)
    assert len(ds) <= 500 and ds[0]["ts"] == 0 and ds[-1]["ts"] == 1199


def test_digest_decisions_strips_big_fields_and_truncates() -> None:
    assert _digest_decisions([], 50) == []
    dec = [{"ts": 1, "round": 1, "action": "open_long", "confidence": 0.8, "reason": "x" * 500,
            "executed": {"ok": True, "traded": True}, "degradeReason": None,
            "signalBoard": [1, 2], "analysis": "long"},
           {"ts": 2, "round": 2, "action": "hold", "confidence": None, "reason": "r",
            "executed": {"ok": True, "skipped": True}, "degradeReason": "llm_timeout"}]
    dg = _digest_decisions(dec, 50)
    assert "signalBoard" not in dg[0] and "analysis" not in dg[0]
    assert len(dg[0]["reason"]) == 160
    assert dg[0]["executed"] is True and dg[1]["executed"] is False


def test_compute_ai_stats_matches_backtest_shape() -> None:
    dec = [{"action": "open_long", "confidence": 0.8, "degradeReason": None},
           {"action": "hold", "confidence": None, "degradeReason": "llm_timeout"},
           {"action": "hold", "confidence": 0.6, "degradeReason": None}]
    st = _compute_ai_stats(dec, 3)
    assert st["decisionRounds"] == 3
    # cancel_order 为 ai-trade-limit-orders 新增动作（session._ACTION_KEYS 与 backtest.action_counts
    # 同步扩为 5 键）；本断言与 backtest 形状保持一致。
    assert st["actionCounts"] == {"open_long": 1, "open_short": 0, "close": 0, "hold": 2,
                                  "cancel_order": 0}
    assert st["holdRate"] == pytest.approx(0.6667, abs=1e-4)
    assert st["avgConfidence"] == pytest.approx(0.7)
    assert st["degradedRounds"] == 1
    assert _compute_ai_stats([], 0)["holdRate"] == 0.0   # 空样本不抛、不 NaN


# ---------------- 孤儿对账：置 stopped + end_ts + reconciled、幂等、不误伤 ----------------
async def test_reconcile_orphan_ai_trade_runs() -> None:
    sid = await _reset()
    sm = get_sessionmaker()
    async with sm() as s:
        orphan = await _add_run(s, strategy_id=sid, group_id="aihist_ghost", inst_id="BTC-USDT-SWAP",
                                budget=100, alloc_pct=100, strategy_names=["动量"], total_budget=100,
                                group_started_ts=100, start_ts=50, status="running", end_ts=None)
        await _add_trade(s, orphan, 500, "long", 100.0)
        # guard 行：live-auto running（异 mode）与 已终态 ai-trade —— 均不应被动
        live = await _add_run(s, strategy_id=sid, group_id="aihist_live_guard", inst_id="BTC-USDT-SWAP",
                              budget=100, alloc_pct=100, strategy_names=[], total_budget=100,
                              group_started_ts=100, start_ts=50, status="running", mode="live-auto")
        done = await _add_run(s, strategy_id=sid, group_id="aihist_done", inst_id="BTC-USDT-SWAP",
                              budget=100, alloc_pct=100, strategy_names=["动量"], total_budget=100,
                              group_started_ts=100, start_ts=50, status="stopped", end_ts=200)
        first = await reconcile_orphan_ai_trade_runs(s)
        second = await reconcile_orphan_ai_trade_runs(s)   # 幂等：二次无 running → 0
        fixed = await s.get(StrategyRun, orphan)
        live_after = await s.get(StrategyRun, live)
        done_after = await s.get(StrategyRun, done)
    assert first == 1 and second == 0
    assert fixed.status == "stopped"
    assert fixed.end_ts == 500   # max(最后成交 500, group_started 100, start 50)
    assert json.loads(fixed.metrics_json)["reconciled"] is True
    assert live_after.status == "running"          # 异 mode 不误伤
    assert done_after.status == "stopped" and done_after.metrics_json is None   # 已终态未被改写


# ---------------- REST 端点（httpx ASGI，无活跃组 → exclude=None）----------------
def _load_app():
    from app.main import app
    return app


async def test_history_endpoints() -> None:
    sid = await _reset()
    from app.ai_auto import get_ai_trade_manager
    get_ai_trade_manager()._group = None   # 确保无活跃组（exclude=None）
    sm = get_sessionmaker()
    async with sm() as s:
        await _seed_two_groups(s, sid)
    app = _load_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        listed = await client.get("/api/ai-trade/history", params={"limit": 20, "offset": 0})
        detail = await client.get("/api/ai-trade/history/aihist_A")
        not_found = await client.get("/api/ai-trade/history/aihist_missing")
    assert listed.status_code == 200
    body = listed.json()
    assert body["total"] == 2 and [i["groupId"] for i in body["items"]] == ["aihist_B", "aihist_A"]
    assert detail.status_code == 200
    dbody = detail.json()
    assert dbody["summary"]["realizedPnlUsd"] == pytest.approx(8.0)
    # trade_out 序列化：成交行为 wire dict（含 explain 键），非 ORM
    assert "explain" in dbody["members"][0]["trades"][0]
    assert not_found.status_code == 404
