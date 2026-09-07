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
from app.config import Settings
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


# ---------------- 双时钟 ts 的因果配对回归（ai-trade-stats-missing-round-trips）----------------
async def test_pairs_by_causal_order_when_ts_is_written_by_two_clocks() -> None:
    """AI 自动交易的 trades.ts 由两套时钟写入（K 线轮=bar 开盘时刻 / 定时唤醒轮=墙钟）。

    按 ts 排序做单持仓配对会打乱真实的开→平交替：对内逆序整笔丢弃，跨对重叠则张冠李戴 + 丢一笔。
    配对必须按落库顺序（id 升序 = 因果序）。两组夹具均取自生产库真实成交。
    """
    sid = await _reset()
    sm = get_sessionmaker()
    async with sm() as s:
        # 形态一（生产库 run_id=1557）：平仓 ts 比开仓早 12 分钟 —— 整笔被丢弃
        inverted = await _add_run(s, strategy_id=sid, group_id="aihist_dualclock",
                                  inst_id="ETH-USDT-SWAP", budget=100, alloc_pct=50,
                                  strategy_names=["动量"], total_budget=200,
                                  group_started_ts=1784699234, start_ts=1784699234,
                                  end_ts=1784702000)
        await _add_trade(s, inverted, 1784701626, "long", 1912.94)          # 墙钟 06:27:06
        await _add_trade(s, inverted, 1784700900, "close", 1913.64, pnl=0.0574)  # bar 06:15:00

        # 形态二（生产库 run_id=1548）：两对对内均正序，但后一对开仓 ts 早于前一对平仓 —— 错配 + 丢弃
        overlap = await _add_run(s, strategy_id=sid, group_id="aihist_dualclock",
                                 inst_id="BTC-USDT-SWAP", budget=100, alloc_pct=50,
                                 strategy_names=["动量"], total_budget=200,
                                 group_started_ts=1784699234, start_ts=1784654000,
                                 end_ts=1784659083)
        await _add_trade(s, overlap, 1784654324, "short", 66409.2)
        await _add_trade(s, overlap, 1784656025, "close", 66200.0, pnl=0.3347)
        await _add_trade(s, overlap, 1784655900, "short", 66186.5)          # ts 倒退 2 分 05 秒
        await _add_trade(s, overlap, 1784659083, "close", 66265.0, pnl=-0.1256)

        listed = await list_ai_trade_groups(s, limit=20, offset=0)
        detail = await get_ai_trade_group_detail(s, "aihist_dualclock")

    item = next(i for i in listed["items"] if i["groupId"] == "aihist_dualclock")
    # 3 笔完整交易全部计入：0.0574 + 0.3347 - 0.1256
    assert item["tradeCount"] == 3
    assert item["realizedPnlUsd"] == pytest.approx(0.2665)
    assert item["bestUsd"] == pytest.approx(0.3347)
    assert item["worstUsd"] == pytest.approx(-0.1256)
    assert detail["summary"]["tradeCount"] == 3
    assert detail["summary"]["realizedPnlUsd"] == pytest.approx(0.2665)
    # startedTs / endedTs 取自 group_started_ts 与成交 ts 最大值，配对排序键变化不得波及
    assert detail["startedTs"] == 1784699234 and detail["endedTs"] == 1784702000


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


# ============ F4 利润捕获率聚合（ai-trade-profit-protection-adaptive §2.4.3）============
def test_capture_ratio_only_counts_pairs_with_a_positive_peak():
    """口径：`captureRatio = Σrealized / Σpeak`，仅 `peak > 0` 的往返入分母；缺失 / ≤0 单列计数。"""
    from app.db.ai_auto_repositories import _summarize_pairs, _Trip

    class _T:
        def __init__(self, side, pnl=0.0, peak_pnl=None, tid=0):
            self.side, self.pnl, self.peak_pnl, self.id = side, pnl, peak_pnl, tid

    # 零腿费 + basis="gross" ⇒ net_pnl / gross_pnl 皆等于 exit.pnl，故本组既有断言逐字不变。
    pairs = [
        _Trip(_T("long", tid=1), _T("close", pnl=0.6, peak_pnl=1.0, tid=2), 0.0, "gross"),
        _Trip(_T("long", tid=3), _T("close", pnl=0.9, peak_pnl=1.0, tid=4), 0.0, "gross"),
        _Trip(_T("long", tid=5), _T("close", pnl=-0.2, peak_pnl=None, tid=6), 0.0, "gross"),
        _Trip(_T("long", tid=7), _T("close", pnl=-0.3, peak_pnl=0.0, tid=8), 0.0, "gross"),
    ]

    summary = _summarize_pairs(pairs, 1000.0)

    assert summary["peakPnlUsd"] == 2.0
    assert summary["capturedPnlUsd"] == pytest.approx(1.5)
    assert summary["captureRatio"] == pytest.approx(0.75)
    assert summary["capturePairs"] == 2 and summary["noPeakCount"] == 2
    # 既有五键口径逐字不变（纯加法）。
    assert summary["tradeCount"] == 4 and summary["realizedPnlUsd"] == pytest.approx(1.0)


def test_capture_ratio_tolerates_legacy_rows_without_the_column():
    """G-6：老库缺 `peak_pnl` 列 ⇒ `getattr` 兜底为 None ⇒ 全部计入 noPeakCount，比率为 0（不炸）。"""
    from app.db.ai_auto_repositories import _summarize_pairs, _Trip

    class _Legacy:
        def __init__(self, side, pnl=0.0, tid=0):
            self.side, self.pnl, self.id = side, pnl, tid

    summary = _summarize_pairs(
        [_Trip(_Legacy("long", tid=1), _Legacy("close", pnl=0.5, tid=2), 0.0, "gross")], 100.0)

    assert summary["captureRatio"] == 0.0 and summary["capturePairs"] == 0
    assert summary["noPeakCount"] == 1


def test_capture_ratio_denominator_is_revalued_at_final_average_price():
    """[P1-7] 口径瑕疵留档：epoch 内加过仓的往返，`peak` 是**按最终均价重估**值，不是账户真实拥有过的浮盈。

    本用例把这一点钉成**已知且被接受**的行为（而不是某次实现走样）：同一笔往返里 peak 由保护层用
    当前 qty × (peak_px − 漂移后均价) 得到，聚合层原样采信、不做任何 rebase。
    """
    from app.db.ai_auto_repositories import _summarize_pairs, _Trip

    class _T:
        def __init__(self, side, pnl=0.0, peak_pnl=None, tid=0):
            self.side, self.pnl, self.peak_pnl, self.id = side, pnl, peak_pnl, tid

    # 加仓后均价上移、手数变大 ⇒ 重估峰值 2.4 大于任何单一时刻真实浮盈；聚合仍原样使用。
    summary = _summarize_pairs(
        [_Trip(_T("long", tid=1), _T("close", pnl=1.2, peak_pnl=2.4, tid=2), 0.0, "gross")], 1000.0)

    assert summary["peakPnlUsd"] == 2.4
    assert summary["captureRatio"] == pytest.approx(0.5)
    assert summary["capturePairs"] == 1


# ============ 成本口径聚合（ai-trade-cost-truth-and-net-pnl §3.2 B-4 / B-5，G-6 / G-14）============
class _CostTrade:
    """包含可信增量数量的成交行替身，费用来源仍按各代原始数据。"""

    def __init__(self, side, *, pnl=0.0, fee=0.0, peak_pnl=None, tid=0, qty=1):
        self.side, self.pnl, self.fee, self.peak_pnl, self.id = side, pnl, fee, peak_pnl, tid
        self.qty, self.price = qty, 100.0


def _round_trip_rows(*, fee: float, pnl: float, peak: float | None = None) -> list:
    """一笔往返：开仓腿 + 平仓腿，各收一半腿费。"""
    return [_CostTrade("long", fee=fee / 2, tid=1),
            _CostTrade("close", pnl=pnl, fee=fee / 2, peak_pnl=peak, tid=2)]


def test_leg_fees_are_summed_across_partially_filled_open_legs():
    """B-5：部分成交产生多条开仓腿，配对只留最后一条 ⇒ 按「配对那条腿的 fee」计会漏记先前分批。"""
    from app.db.ai_auto_repositories import _pair_round_trips

    trades = [_CostTrade("long", fee=1.0, tid=1, qty=3),
              _CostTrade("long", fee=2.0, tid=2, qty=7),
              _CostTrade("close", pnl=10.0, fee=3.0, tid=3, qty=10)]
    trips = _pair_round_trips(trades, "gross")

    assert len(trips) == 1
    assert trips[0].leg_fee_usd == pytest.approx(6.0)          # 三腿之和
    assert trips[0].leg_fee_usd != pytest.approx(5.0)          # ≠「仅最后一条开仓腿 + 平仓腿」
    assert trips[0].net_pnl == pytest.approx(4.0)


def test_four_generations_of_rows_aggregate_without_double_charging():
    """G-6：同一张 trades 表里的**四**代行各自还原正确，缺 costBasis 的第 ④ 代绝不被二次扣费。"""
    from app.db.ai_auto_repositories import _pair_round_trips, _summarize_pairs

    # ① 阶段一之前：fee 恒 0、pnl 毛额、缺 costBasis ⇒ 推定 gross ⇒ 与今天数值相同（诚实降级）。
    gen1 = _summarize_pairs(_pair_round_trips(_round_trip_rows(fee=0.0, pnl=10.0), "gross"), 100.0)
    assert gen1["realizedPnlUsd"] == pytest.approx(10.0) and gen1["feeUsd"] == 0.0
    # ② 阶段一之后、本轮之前：fee 已记、pnl 仍毛额 ⇒ 可还原为 Σ(pnl − legFee)。
    gen2 = _summarize_pairs(_pair_round_trips(_round_trip_rows(fee=2.0, pnl=10.0), "gross"), 100.0)
    assert gen2["realizedPnlUsd"] == pytest.approx(8.0) and gen2["grossPnlUsd"] == pytest.approx(10.0)
    # ③ 本轮之后：pnl 已是净额且 costBasis == "net" ⇒ 原样相加。
    gen3 = _summarize_pairs(_pair_round_trips(_round_trip_rows(fee=2.0, pnl=8.0), "net"), 100.0)
    assert gen3["realizedPnlUsd"] == pytest.approx(8.0) and gen3["grossPnlUsd"] == pytest.approx(10.0)
    # ④ 运维按 runbook 提前开过总闸：pnl 已是净额但**缺** costBasis ⇒ 推定为 "net" 才不二次扣费。
    gen4 = _summarize_pairs(_pair_round_trips(_round_trip_rows(fee=2.0, pnl=8.0), "net"), 100.0)
    assert gen4["realizedPnlUsd"] == pytest.approx(8.0)
    assert gen4["realizedPnlUsd"] != pytest.approx(6.0)         # 二次扣费的错数长这样


def test_legacy_cost_basis_is_a_declared_assumption_not_a_silent_guess(monkeypatch):
    """P0-3：缺键即**推定**，且推定这件事本身必须随回执下发（UI 要把「推定」与「已知」分开）。"""
    import app.db.ai_auto_repositories as mod

    assert mod._run_cost_basis({"costBasis": "net"}) == ("net", False)
    assert mod._run_cost_basis({"costBasis": "gross"}) == ("gross", False)

    monkeypatch.setattr(mod, "get_settings",
                        lambda: Settings(ai_trade_history_legacy_cost_basis="gross"))
    assert mod._run_cost_basis({}) == ("gross", True)
    monkeypatch.setattr(mod, "get_settings",
                        lambda: Settings(ai_trade_history_legacy_cost_basis="net"))
    assert mod._run_cost_basis({}) == ("net", True)


def test_mixed_basis_group_is_flagged():
    from app.db.ai_auto_repositories import _pair_round_trips, _summarize_pairs

    trips = (_pair_round_trips(_round_trip_rows(fee=2.0, pnl=10.0), "gross")
             + _pair_round_trips(_round_trip_rows(fee=2.0, pnl=8.0), "net"))
    summary = _summarize_pairs(trips, 100.0)
    assert summary["costBasisMixed"] is True
    assert summary["realizedPnlUsd"] == pytest.approx(16.0)     # 各按自己的口径还原后相加
    assert summary["feeUsd"] == pytest.approx(4.0)


def test_capture_ratio_is_identical_on_both_sides_of_the_net_switch():
    """G-14：捕获率分子分母**同为毛额** ⇒ 净额总闸翻转不改变它；只有 realizedPnlUsd 差一个 feeUsd。"""
    from app.db.ai_auto_repositories import _pair_round_trips, _summarize_pairs

    gross = _summarize_pairs(
        _pair_round_trips(_round_trip_rows(fee=2.0, pnl=10.0, peak=20.0), "gross"), 100.0)
    net = _summarize_pairs(
        _pair_round_trips(_round_trip_rows(fee=2.0, pnl=8.0, peak=20.0), "net"), 100.0)

    for key in ("peakPnlUsd", "capturedPnlUsd", "captureRatio", "capturePairs", "noPeakCount"):
        assert gross[key] == net[key], key                      # 逐位相同（老会话的历史 KPI 不被追溯改写）
    assert gross["capturedPnlUsd"] == pytest.approx(10.0)        # 分子是**毛额**，与毛额分母同维
    assert gross["realizedPnlUsd"] == net["realizedPnlUsd"] == pytest.approx(8.0)
    assert net["realizedPnlUsd"] == pytest.approx(net["grossPnlUsd"] - net["feeUsd"])
