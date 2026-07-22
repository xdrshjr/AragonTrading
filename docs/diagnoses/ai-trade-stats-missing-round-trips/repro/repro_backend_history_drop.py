"""Backend repro: history aggregation drops a completed AI-trade round trip whose close.ts < open.ts.

Run from services/quant:  python <this file>
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
import uuid

os.environ["DB_PATH"] = os.path.join(tempfile.gettempdir(), f"aragon_repro_{uuid.uuid4().hex}.db")

from app.db.ai_auto_repositories import get_ai_trade_group_detail, list_ai_trade_groups  # noqa: E402
from app.db.models import Strategy, StrategyRun, Trade  # noqa: E402
from app.db.session import get_sessionmaker, init_db  # noqa: E402

GROUP = "aitradegrp_repro"


async def main() -> None:
    await init_db()
    sm = get_sessionmaker()
    async with sm() as s:
        strat = Strategy(template_key="ma_cross", name="repro", params_json="{}")
        s.add(strat)
        await s.commit()
        await s.refresh(strat)

        run = StrategyRun(
            strategy_id=strat.id, inst_id="ETH-USDT-SWAP", bar="15m", mode="ai-trade",
            status="stopped", start_ts=1784699234, end_ts=1784702000,
            config_json=json.dumps({"budgetUsd": 62.75, "leverage": 5.0,
                                    "allocationPct": 100.0, "strategyNames": ["repro"],
                                    "groupId": GROUP, "totalBudgetUsd": 62.75}),
            metrics_json=json.dumps({"tradeCount": 2, "totalReturn": 0.0574 / 62.75}),
            group_id=GROUP, group_started_ts=1784699234, group_total_budget_usd=62.75)
        s.add(run)
        await s.commit()
        await s.refresh(run)

        # Real rows copied from production aragon.db (run_id=1557):
        #   id=50914 open  ts=1784701626  <- interval-wake round  -> wall clock 06:27:06
        #   id=50915 close ts=1784700900  <- candle round         -> bar open   06:15:00
        s.add(Trade(run_id=run.id, ts=1784701626, side="long", price=1912.94, qty=0.82,
                    fee=0.0, pnl=0.0, position_after=0.82, reason="interval wake open"))
        s.add(Trade(run_id=run.id, ts=1784700900, side="close", price=1913.64, qty=0.82,
                    fee=0.0, pnl=0.0574, position_after=0.0, reason="candle round close"))
        await s.commit()

        rows = await list_ai_trade_groups(s, limit=10, offset=0)
        item = rows["items"][0]
        detail = await get_ai_trade_group_detail(s, GROUP)
        print("trades persisted in DB      :", len(detail["members"][0]["trades"]))
        print("list  tradeCount            :", item["tradeCount"], "(expected 1)")
        print("list  realizedPnlUsd        :", item["realizedPnlUsd"], "(expected 0.0574)")
        print("detail summary.tradeCount   :", detail["summary"]["tradeCount"], "(expected 1)")
        print("detail summary.realizedPnl  :", detail["summary"]["realizedPnlUsd"], "(expected 0.0574)")
        print("member metrics.totalReturn  :", detail["members"][0]["metrics"]["totalReturn"],
              "(finalize-time truth, still non-zero -> KPI self-contradiction)")


asyncio.run(main())
