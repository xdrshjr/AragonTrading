"""跨会话盈利体检的**装配层**（ai-trade-realized-edge-calibration 轴 B/C/D · §5.1）。

为什么另起一个模块而不是塞进 `api/ai_auto.py`：后者开工前已 719 行，只剩 81 行余量，而本层含
端点回执装配（`pooled` + `risk_profile` + 六切片 + 三问 + 结论表）与启动期战绩快照两条路径。
下沉顺带满足本仓库已成文的那条纪律（`discipline_runtime.py:7-8`）：**判据在纯核心、取数在运行时
层**——判据全在 `profitability.py` / `calibration.py`，本模块只取数与装配。
先例：第 2 轮的 `tradability_probe.py`、`prior_service.py`。
"""
from __future__ import annotations

import hashlib
import json
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from ..db import repositories as repo
from ..okx.types import BAR_SECONDS
from .calibration import calibration
from .cost_runtime import CostRuntime
from .profitability import VerdictGates, pooled, risk_profile, slices, verdict
from .profitability_sample import summarize_coverage

logger = logging.getLogger(__name__)


async def build_profitability(session: AsyncSession, settings,
                              query: repo.TripQuery) -> dict:
    """`GET /ai-trade/history/profitability` 的完整回执（空历史 ⇒ 全 0/None 但结构完整）。

    五条契约纪律：① 算不出的量一律 `null`，**绝不填 0**；② 每个比例类读数所在的对象必带它自己的
    分母（`trips` / `n` / `coverage` / `rSamples` / `feeTrips`）；③ `verdict.detail` 由后端生成；
    ④ 口径推定用 `costBasisAssumedTrips`（**计数**，不是布尔）；⑤ `medianCostBps` /
    `travelToCostRatio` 与 `feeCoverage` **必须在同一个对象里**下发。
    """
    dataset = await repo.load_profitability_dataset(session, query)
    coverage = summarize_coverage(dataset)
    trips = [row for row in dataset.samples if row["netEligible"]]
    stats = pooled(trips, total_trips=len(dataset.samples))
    gates = VerdictGates(min_trips=int(settings.ai_trade_profitability_min_trips),
                         t_threshold=float(settings.ai_trade_profitability_t_threshold))
    judgment = verdict(stats, gates, coverage)
    identity = {"samples": dataset.samples, "runs": dataset.run_dimensions,
                "coverage": coverage, "schemaVersion": 2}
    snapshot_id = hashlib.sha256(json.dumps(
        identity, sort_keys=True, ensure_ascii=False, allow_nan=False,
        separators=(",", ":"), default=str).encode("utf-8")).hexdigest()
    return {
        "schemaVersion": 2, "coverage": coverage,
        "asOfTs": dataset.as_of_ts, "snapshotId": snapshot_id,
        "scope": {"instId": query.inst_id, "bar": query.bar,
                  "sinceTs": query.since_ts, "sinceBasis": "run_started",
                  "sinceField": "group_started_ts_or_start_ts",
                  "preset": query.preset, "horizonProfile": query.horizon_profile,
                  "basis": "trading_net_ex_funding"},
        "sampleTrips": stats["roundTrips"], "minTrips": gates.min_trips,
        "conclusive": judgment["code"] not in ("insufficient_samples", "insufficient_evidence"),
        "costBasisAssumedTrips": stats["costBasisAssumedTrips"],
        "pooled": stats, "risk": risk_profile(trips),
        "slices": slices(list(dataset.samples), dataset=dataset),
        "calibration": calibration(
            list(dataset.samples),
            bucket_min_trips=int(settings.ai_trade_profitability_bucket_min_trips),
            model_constant_bps=_model_constant_bps(settings, query.bar),
            structural_issue_count=coverage["structuralIssueCount"]),
        "verdict": judgment,
    }


def _model_constant_bps(settings, bar: str | None) -> float | None:
    """成本模型的市价往返常数（轴 C 问二的对照项）；估不出 ⇒ `None`（**绝不编一个数**）。

    复用**同一对**成本纯核心（不发任何请求的只读 `CostRuntime`，镜像 `build_charter_text` 与
    `tradability_probe._preview_costs` 的姿态）——写给用户看的「模型按 N bps 估」必须与门禁真正
    据以判定的那个数同源。
    """
    seconds = BAR_SECONDS.get(bar or "") or BAR_SECONDS["1H"]
    preview = CostRuntime("", settings, None, None).estimate_preview_cost(
        ord_type="market", side="long", bar_seconds=seconds)
    return None if preview is None else preview.total_bps


async def resolve_track_record(session: AsyncSession, settings, *,
                               inst_ids: list[str], bar: str) -> dict[str, dict] | None:
    """按 `instId` 逐个取历史战绩快照（轴 D）；总闸关 ⇒ `None`（连查询都不发）。

    **决策轮零 IO 是硬约束**（沿用 `PriorRuntime` 的同款纪律）：本函数是这份数据的**唯一**取数点，
    在启动流程里查一次、会话内不刷新。刷新需要在盯市 tick 上开 DB 会话，那是一条今天不存在的
    IO 面，而收益（几小时内战绩几乎不变）远小于风险。

    **按 `(instId, bar)` 键**而非分组共享一份：BTC 15m 的胜率与 PEPE 1m 的胜率不是同一个东西
    （实测按标的切分：ETH −5.30 / SOL +1.17 / BTC +0.44 / DOGE +1.06 / PEPE −1.48 USDT）。
    查询失败一律降级为「无战绩」——一个取不到历史的体检读数绝不能挡住真实资金会话的启动。
    """
    if not getattr(settings, "ai_trade_track_record_enabled", False):
        return None
    out: dict[str, dict] = {}
    for inst_id in dict.fromkeys(inst_ids):
        try:
            wire = await build_profitability(
                session, settings, repo.TripQuery(inst_id=inst_id, bar=bar))
        except Exception as exc:  # noqa: BLE001 —— 历史取数失败只降级，绝不阻断启动
            logger.info("ai-trade track record skipped (%s %s): %s", inst_id, bar, exc)
            continue
        out[inst_id] = {**{key: wire[key] for key in
                          ("pooled", "risk", "verdict", "coverage", "schemaVersion",
                           "asOfTs", "snapshotId")},
                        "basis": "trading_net_ex_funding",
                        "instId": inst_id, "bar": bar}
    return out or None
