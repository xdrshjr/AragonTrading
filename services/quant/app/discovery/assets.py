"""成果资产的身份、登记与入库（discovery-asset-ledger §2.2 / §2.8 / §2.11）。

三条承重条款落在本模块：

- **AL-4**：资产身份 = 语义键，且与 ``promote.py`` 的三个 ``_find_existing_*`` **同源**
  （调用同一批模块级纯函数）。两套键各写一份，就会出现「promote 认为是同一条、台账认为是
  两条」的分裂，其中一行永远显示「未入库」。
- **AL-5**：登记点是**候选被判 kept 并落库之后**，不是 ``promote`` 之后；失败 fail-open
  但**必须计数**（只计数不呈现等于没计数，计数落点见 ``iteration._commit``）。
- **AL-11**：同一资产在多个 run 被挖到合并为一行多溯源；台账**自持** genome 与指标快照，
  run 被删掉之后详情、入库与体检照常可用。
"""
from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import DiscoveryCandidate, DiscoveryRun
from ..db.session import get_sessionmaker
from ..engine.mtf import bar_to_seconds
from . import asset_repositories as asset_repo
from . import describe_strategy, promote
from . import repositories as repo
from .strategy_gates import normalize_strategy_thresholds
from .validation import normalize_thresholds, split_index

logger = logging.getLogger("aragon")

_PREFIX = {"factor": "f", "factor_combo": "c", "strategy_param": "s", "strategy_combo": "r"}
_KEY_HEX = 32

# ``DISCOVERY_ASSET_LIMIT`` 的计数落点（§4.10）：它**永不**作为 HTTP 错误抛出——没有任何
# 调用方能收到那个异常。触顶只停「新建」、不停「合并溯源」，并在 summary.guard 上如实呈现。
_LIMIT_HITS = 0


def limit_hits() -> int:
    """进程内累计的台账触顶次数（``GET /discovery/assets/summary`` 的 ``assetLimitHits``）。"""
    return _LIMIT_HITS


def _note_limit_hit() -> None:
    global _LIMIT_HITS
    _LIMIT_HITS += 1


@dataclass(frozen=True)
class MinedWindow:
    """挖掘窗口口径（判级的分母之一，AL-3）；**唯一**组装点是 ``MinedWindow.of``。"""

    start_ts: int
    end_ts: int
    is_ratio: float
    window_bars: int
    split_ts: int

    def payload(self) -> dict:
        return {"startTs": int(self.start_ts), "endTs": int(self.end_ts),
                "isRatio": float(self.is_ratio), "windowBars": int(self.window_bars),
                "splitTs": int(self.split_ts)}

    @staticmethod
    def of(run) -> "MinedWindow":
        """从运行时组装。

        ``RunSpec`` 是 frozen dataclass，既没有 checkpoint 也没有 df（评审 P1-1），故窗口
        只能在 ``_commit`` 侧由 run 对象组装后**显式传进** ``register_kept``。
        """
        window = dict(run._checkpoint.data_window)
        is_ratio = float(run.spec.config.get("isRatio", 0.7))
        ts = run._df["ts"] if run._df is not None else []
        cut = split_index(len(ts), is_ratio) if len(ts) else 0
        return MinedWindow(
            start_ts=int(window.get("startTs") or 0), end_ts=int(window.get("endTs") or 0),
            is_ratio=is_ratio, window_bars=int(run.spec.config.get("windowBars", 4000)),
            split_ts=int(ts.iloc[min(cut, len(ts) - 1)]) if len(ts) else 0)


@dataclass
class RegisterStats:
    """一次登记的产物；``skipped`` 是台账触顶（``DISCOVERY_ASSET_LIMIT``）的计数落点。"""

    created: int = 0
    merged: int = 0
    skipped: int = 0


# ============================ AL-4：身份键 ============================
def identity_key(kind: str, *, expr_text: str = "", genome: dict | None = None) -> str:
    """跨 run 的资产语义身份（≤48 字符，落 ``discovery_assets.asset_key`` 唯一索引）。

    四条轴各自复用 ``promote`` 的幂等键——``strategy_combo`` 复用 ``strategy_match_key`` 是
    刻意的：它入库后就是一条 ``regime_rotation`` 策略，身份必须与 ``_promote_rotation`` 的
    幂等键一致，否则「入库过的轮动组合」在台账里会显示成未入库。
    """
    payload = genome or {}
    if kind == "factor":
        raw = promote.factor_match_key(expr_text)
    elif kind == "strategy_param":
        raw = promote.strategy_match_key(payload.get("templateKey") or "",
                                         payload.get("params") or {})
    elif kind == "factor_combo":
        raw = promote.member_set_match_key(promote.combo_members(payload))
    elif kind == "strategy_combo":
        raw = promote.strategy_match_key(promote.ROTATION_TEMPLATE_KEY,
                                         promote.rotation_params(payload))
    else:
        raise ValueError(f"未知搜索轴：{kind}")
    return f"{_PREFIX[kind]}:{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:_KEY_HEX]}"


def asset_label(kind: str, *, genome_hash: str, describe: str, genome: dict) -> str:
    """展示名（与 ``promote`` 的入库命名同源，免得台账与实体页面各叫各的）。"""
    if kind == "factor":
        return promote.build_factor_name(str(genome_hash)[:8],
                                         describe[:promote.DESCRIBE_IN_NAME_MAX])
    if kind == "strategy_param":
        return promote.strategy_label(
            "挖掘策略", genome_hash,
            describe_strategy.template_name(str(genome.get("templateKey") or "")))
    if kind == "factor_combo":
        return promote.strategy_label("挖掘组合", genome_hash,
                                      f"{len(genome.get('members') or [])} 因子")
    return promote.strategy_label("挖掘轮动", genome_hash,
                                  f"{len(genome.get('members') or [])} 策略")


def accept_summary(kind: str, metrics: dict, trials: int) -> dict:
    """溯源里的 ≤4 个标量（被合并掉的权重变体只有在这里才留得下痕迹，评审 P1-7）。"""
    if kind == "factor":
        side = dict(metrics.get("is") or {})
        return {"isRankIc": side.get("rankIc"), "isIr": side.get("ir"), "trials": int(trials)}
    side = dict(metrics.get("oos") or {})
    return {"oosSharpe": side.get("sharpe"), "oosTotalReturn": side.get("totalReturn"),
            "trials": int(trials)}


def thresholds_of(kind: str, config: dict) -> dict:
    """登记时冻结的阈值表（AL-3 第二句）。

    ⚠️ **键名逐轴写死**（评审 P1-10）：归一化后的 run config 里因子阈值在 ``thresholds``、
    策略阈值在 ``strategyThresholds``。取错了 ``normalize_strategy_thresholds`` 会把不认识的
    键**全部丢掉、静默回落默认值**，AL-3 第二句整条空转，且不报任何错。
    """
    if kind == "factor":
        return normalize_thresholds(config.get("thresholds"))
    return normalize_strategy_thresholds(config.get("strategyThresholds"))


# ============================ AL-5 / AL-11：登记 ============================
def _values_of(row, *, spec, window: MinedWindow, source: dict,
               settings) -> asset_repo.AssetValues:
    """候选行 + run 配置 → 一条待建资产（**不依赖任何外部查询**）。"""
    metrics = dict(row.metrics or {})
    horizon = (int(metrics.get("horizon") or spec.config.get("horizon", 6) or 0)
               if row.kind == "factor" else 0)
    direction = str(metrics.get("direction") or "long") if row.kind == "factor" else ""
    return asset_repo.AssetValues(
        asset_key=identity_key(row.kind, expr_text=row.expr_text, genome=row.genome),
        kind=row.kind, inst_id=spec.inst_id, bar=spec.bar, horizon=horizon,
        direction=direction, expr_text=row.expr_text or "", genome=dict(row.genome or {}),
        describe_text=row.describe_text or "", mechanism=row.mechanism or "",
        label=asset_label(row.kind, genome_hash=row.genome_hash,
                          describe=row.describe_text or "", genome=dict(row.genome or {})),
        found_ts=int(row.created_ts), source=source, mined_window=window.payload(),
        accept_metrics=metrics, accept_thresholds=thresholds_of(row.kind, spec.config),
        trials_at_accept=int(row.trials_at_accept or 0),
        next_check_ts=int(window.end_ts) + int(settings.discovery_health_min_fresh_bars)
        * bar_to_seconds(spec.bar))


def _source_of(row, *, run_id: int, candidate_id: int, iteration_idx: int) -> dict:
    return {"runId": int(run_id), "candidateId": int(candidate_id),
            "iterationIdx": int(iteration_idx), "foundTs": int(row.created_ts),
            "source": str(row.source or ""), "genomeHash": str(row.genome_hash or ""),
            "acceptSummary": accept_summary(row.kind, dict(row.metrics or {}),
                                            int(row.trials_at_accept or 0))}


async def register_kept(spec, *, rows: list, window: MinedWindow,
                        iteration_idx: int = 0) -> RegisterStats:
    """把本轮判 kept 的候选登记进台账（新建或合并溯源）。

    自开一次 ``get_sessionmaker()`` 会话（与 ``_persist`` 同姿态），**不复用**已提交的那个。
    调用方以 fail-open 姿态包住它——台账是观测面，绝不拖垮迭代（AL-5）。
    """
    stats = RegisterStats()
    if not rows:
        return stats
    settings = spec.settings
    limit = int(settings.discovery_health_max_assets)
    sm = get_sessionmaker()
    async with sm() as session:
        ids = await asset_repo.candidate_ids_by_hash(
            session, int(spec.run_id), [str(row.genome_hash) for row in rows])
        total = await asset_repo.count_assets(session)
        for row in rows:
            source = _source_of(row, run_id=int(spec.run_id),
                                candidate_id=ids.get(str(row.genome_hash), 0),
                                iteration_idx=int(iteration_idx))
            values = _values_of(row, spec=spec, window=window, source=source,
                                settings=settings)
            existing = await asset_repo.get_by_key(session, values.asset_key)
            if existing is not None:
                await asset_repo.merge_source(session, existing,
                                              found_ts=int(row.created_ts), source=source)
                stats.merged += 1
                continue
            if total >= limit:      # DISCOVERY_ASSET_LIMIT：只停新建、不停合并、不报错
                stats.skipped += 1
                _note_limit_hit()
                logger.info("成果资产台账已达上限 %d，本条不再新建：%s", limit, values.asset_key)
                continue
            await asset_repo.create_asset(session, values)
            total += 1
            stats.created += 1
    return stats


# ============================ §2.8：从台账入库 ============================
async def promote_asset(session: AsyncSession, asset_id: int) -> dict:
    """从资产行直接入库（**不经候选行**——run 可能已被删除，AL-11）。

    实体级幂等由 ``promote`` 的三个 ``_find_existing_*`` 保证：两条路径重复入库拿到的是
    同一个实体 id。溯源里的候选行若仍存在，一并 ``mark_promoted`` 保持两边一致。
    """
    row = await asset_repo.get_asset(session, asset_id)
    if row is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "成果资产不存在"})
    source = promote.PromoteSource(
        kind=str(row.kind), genome=asset_repo.loads(row.genome_json, {}) or {},
        expr_text=row.expr_text or "",
        metrics=asset_repo.loads(row.accept_metrics_json, {}) or {},
        mechanism=str(row.mechanism or ""), genome_hash=_genome_hash_of(row),
        run_id=_first_run_id(row), iteration_idx=0)
    if row.promoted_kind and row.promoted_id:
        return await _replay_asset(session, row)
    kind, entity_id, companion = await promote.promote_source(session, source)
    await asset_repo.set_promoted(session, asset_id, kind=kind, entity_id=entity_id,
                                  companion_strategy_id=companion)
    await _sync_candidates(session, row, kind=kind, entity_id=entity_id)
    return promote.promoted_payload(kind, entity_id, reused=False,
                                     companion_strategy_id=companion)


async def _replay_asset(session: AsyncSession, row) -> dict:
    """幂等回放：``factor_set`` 分支必须带上 ``companionStrategyId``（跳转靠它）。"""
    kind, entity_id = str(row.promoted_kind), int(row.promoted_id)
    companion = row.companion_strategy_id
    if kind == "factor_set" and companion is None:
        from ..factors import service as factors_service

        companion = await factors_service.companion_strategy_id(session, entity_id)
    return promote.promoted_payload(kind, entity_id, reused=True,
                                     companion_strategy_id=companion)


def _sources_of(row) -> list:
    value = asset_repo.loads(row.sources_json, []) or []
    return value if isinstance(value, list) else []


def _genome_hash_of(row) -> str:
    sources = _sources_of(row)
    return str(sources[0].get("genomeHash") or "") if sources else str(row.asset_key or "")


def _first_run_id(row) -> int:
    sources = _sources_of(row)
    return int(sources[0].get("runId") or 0) if sources else 0


async def _sync_candidates(session: AsyncSession, row, *, kind: str, entity_id: int) -> None:
    """溯源里仍存在的候选行一并标记已入库（台账是资产视图的权威，两边保持一致）。"""
    ids = [int(item.get("candidateId") or 0) for item in _sources_of(row)]
    for candidate_id in [value for value in ids if value > 0]:
        await repo.mark_promoted(session, candidate_id, kind=kind, entity_id=entity_id)


# ============================ §2.11：存量成果一次性回填 ============================
def _window_of_run(run: DiscoveryRun) -> MinedWindow:
    """从 run 的检查点复原挖掘窗口；``splitTs`` **无法**重算（当时的 df 已经不在了）。

    一律填 0 并在资产上置 ``backfilled``——**绝不**拿今天的窗口冒充当时的窗口，那正是 AL-3
    要防的「换分母」。
    """
    checkpoint = asset_repo.loads(run.checkpoint_json, {}) or {}
    config = asset_repo.loads(run.config_json, {}) or {}
    window = dict(checkpoint.get("dataWindow") or {})
    return MinedWindow(start_ts=int(window.get("startTs") or 0),
                       end_ts=int(window.get("endTs") or 0),
                       is_ratio=float(config.get("isRatio", 0.7) or 0.7),
                       window_bars=int(config.get("windowBars", 4000) or 4000), split_ts=0)


@dataclass(frozen=True)
class _BackfillSpec:
    """回填时冒充 ``RunSpec`` 的最小形态（``_values_of`` 只用到这四个字段）。"""

    run_id: int
    inst_id: str
    bar: str
    config: dict
    settings: object


async def backfill_assets(session: AsyncSession, settings) -> RegisterStats:
    """把本功能上线前已 kept 的候选一次性补进台账（幂等 / 有界 / fail-open / 错峰）。

    没有它，本轮交付的是一个「对老用户永远空、对新用户要等几天才有内容」的分区（D-29）：
    登记只发生在 ``_commit``，此前两轮跑出来的 kept 行永远不会自己进来。
    """
    stats = RegisterStats()
    limit = int(settings.discovery_health_max_assets)
    rows = list((await session.execute(
        select(DiscoveryCandidate).where(DiscoveryCandidate.verdict == "kept")
        .order_by(DiscoveryCandidate.created_ts.asc(),
                  DiscoveryCandidate.id.asc()))).scalars().all())
    if not rows:
        return stats
    runs = {int(run.id): run for run in (await session.execute(
        select(DiscoveryRun).where(DiscoveryRun.id.in_(
            {int(row.run_id) for row in rows})))).scalars().all()}
    interval = float(settings.discovery_health_interval_s)
    now = _now()
    total = await asset_repo.count_assets(session)
    for index, row in enumerate(rows):
        run = runs.get(int(row.run_id))
        if run is None:
            continue
        if total >= limit:
            stats.skipped += 1          # 触顶只 log + 计数，绝不静默截断、绝不删旧
            _note_limit_hit()
            continue
        outcome = await _backfill_one(session, row, run=run, settings=settings,
                                      stagger=int(now + index * interval / len(rows)))
        if outcome is None:
            continue
        if outcome:
            stats.created += 1
            total += 1
        else:
            stats.merged += 1
    if stats.skipped:
        logger.warning("成果资产回填触顶（上限 %d），本次跳过 %d 条历史成果", limit, stats.skipped)
    return stats


async def _backfill_one(session: AsyncSession, row, *, run, settings,
                        stagger: int) -> bool | None:
    """单条回填；已存在即合并溯源（同一个 upsert 语义，跑一百次结果相同）。"""
    candidate = _candidate_row_of(row)
    spec = _BackfillSpec(run_id=int(run.id), inst_id=run.inst_id, bar=run.bar,
                         config=asset_repo.loads(run.config_json, {}) or {},
                         settings=settings)
    source = _source_of(candidate, run_id=int(run.id), candidate_id=int(row.id),
                        iteration_idx=int(row.iteration_idx or 0))
    try:
        values = _values_of(candidate, spec=spec, window=_window_of_run(run), source=source,
                            settings=settings)
    except Exception as exc:  # noqa: BLE001 —— 单条历史脏数据不得拖垮整次回填
        logger.debug("候选 #%s 无法回填进台账：%s", row.id, exc)
        return None
    existing = await asset_repo.get_by_key(session, values.asset_key)
    if existing is not None:
        if any(int(item.get("candidateId") or 0) == int(row.id)
               for item in (asset_repo.loads(existing.sources_json, []) or [])):
            return None                      # 幂等：同一条候选不重复追加溯源
        await asset_repo.merge_source(session, existing, found_ts=int(row.created_ts),
                                      source=source)
        return False
    values.backfilled = True
    values.next_check_ts = int(stagger)      # 错峰摊进一个 interval，避免上线瞬间冲 OKX
    await asset_repo.create_asset(session, values)
    return True


def _candidate_row_of(row: DiscoveryCandidate) -> repo.CandidateRow:
    """ORM 行 → ``CandidateRow``（登记路径只认后者，回填因此复用同一段装配）。"""
    return repo.CandidateRow(
        kind=str(row.kind), genome_hash=str(row.genome_hash or ""),
        genome=asset_repo.loads(row.genome_json, {}) or {}, expr_text=row.expr_text or "",
        describe_text=row.describe_text or "", skeleton=row.skeleton or "",
        mechanism=row.mechanism or "", source=str(row.source or ""), verdict="kept",
        metrics=asset_repo.loads(row.metrics_json, {}) or {},
        trials_at_accept=int(row.trials_at_accept or 0), created_ts=int(row.created_ts or 0))


def _now() -> int:
    return int(time.time())
