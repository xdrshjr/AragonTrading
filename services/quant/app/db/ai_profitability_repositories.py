"""收益总体的单 SELECT 只读断面；调用者保有事务所有权。"""

from __future__ import annotations

import time
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai_auto.accounting_replay import TradeAccountingAccumulator
from ..ai_auto.profitability_sample import (
    ProfitabilityDataset,
    build_profitability_sample,
    number,
    text_dimension,
)
from .ai_auto_repositories import (
    TripQuery,
    _accounting_trade,
    _load_config,
    _row_matches,
    _run_matches,
)
from .models import StrategyRun, Trade

_RUN_FIELDS = (
    "id",
    "inst_id",
    "start_ts",
    "group_started_ts",
    "config_json",
    "metrics_json",
)
_TRADE_FIELDS = (
    "id",
    "run_id",
    "side",
    "ts",
    "price",
    "qty",
    "fee",
    "pnl",
    "reason",
    "position_after",
    "explain_json",
    "stop_loss_price",
    "take_profit_price",
    "peak_pnl",
)


async def load_profitability_dataset(
    session: AsyncSession,
    query: TripQuery,
) -> ProfitabilityDataset:
    """物化同一SQL中的run/trade，匹配空run也保留；无DML/提交。"""
    statement = (
        select(
            *(
                getattr(StrategyRun, key).label(f"r_{key}")
                for key in _RUN_FIELDS
            ),
            *(getattr(Trade, key).label(f"t_{key}") for key in _TRADE_FIELDS),
        )
        .select_from(StrategyRun)
        .outerjoin(
            Trade,
            Trade.run_id == StrategyRun.id,
        )
        .where(StrategyRun.mode == "ai-trade")
        .order_by(StrategyRun.id, Trade.id)
    )
    if query.inst_id:
        statement = statement.where(StrategyRun.inst_id == query.inst_id)
    as_of = int(time.time())
    with session.no_autoflush:
        rows = (await session.execute(statement)).mappings().all()
    runs = {}
    for row in rows:
        identifier = row["r_id"]
        if identifier not in runs:
            run = SimpleNamespace(
                **{key: row[f"r_{key}"] for key in _RUN_FIELDS}
            )
            runs[identifier] = (run, [])
        if row["t_id"] is not None:
            trade = SimpleNamespace(
                **{key: row[f"t_{key}"] for key in _TRADE_FIELDS}
            )
            runs[identifier][1].append(_accounting_trade(trade))
    return _project_runs(runs.values(), query, as_of)


def _project_runs(runs, query, as_of):
    samples, dimensions, issues = [], [], []
    open_count = 0
    for run, trades in runs:
        config = _load_config(run.config_json)
        dims = _dimensions(run, config)
        if not _run_matches(run, query) or not _row_matches(dims, query):
            continue
        accumulator = TradeAccountingAccumulator(dims)
        for trade in trades:
            before = len(accumulator.issues)
            result = accumulator.record_trade(trade)
            if not result["duplicate"]:
                issues.extend(
                    {
                        "runId": run.id,
                        "eventKey": result["eventKey"],
                        "reason": reason,
                    }
                    for reason in set(accumulator.issues[before:])
                )
        opened = int(accumulator.remaining_qty > 0)
        open_count += opened
        dimensions.append({**dims, "openEpochCount": opened})
        samples.extend(
            build_profitability_sample(trip, dims)
            for trip in accumulator.completed_trips
        )
    return ProfitabilityDataset(
        tuple(samples), tuple(dimensions), tuple(issues), open_count, as_of
    )


def _dimensions(run, config):
    stats = _load_config(run.metrics_json).get("aiStats")
    stats = stats if isinstance(stats, dict) else {}
    horizon = config.get("tradingHorizon")
    horizon = horizon if isinstance(horizon, dict) else {}
    basis = text_dimension(config.get("costBasis"))
    return {
        "runId": run.id, "instId": text_dimension(run.inst_id, "unknown"),
        "bar": text_dimension(config.get("bar")),
        "preset": text_dimension(stats.get("disciplinePreset"), "unknown"),
        "horizonProfile": text_dimension(horizon.get("profile")),
        "costBasis": basis, "costBasisAssumed": basis not in ("net", "gross"),
        "ctVal": number(config.get("ctVal")),
    }
