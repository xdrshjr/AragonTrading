"""检查点序列化与断点续跑（strategy-factor-discovery §2.10）—— 小而有界（目标 < 64 KB）。

D-3 的另一半：**已测哈希绝不进 JSON**（24/7 跑三天会攒十万级候选，JSON 大对象既写不动也不
原子），它们靠 ``discovery_candidates`` 的 ``UniqueConstraint(run_id, genome_hash)`` 物理去重，
续跑时一次 ``SELECT genome_hash WHERE run_id=?`` 载入内存 set。

本模块只负责「小状态」：迭代数 / 种子 / 预算 / 计数器 / 动量 / FSA 冻结 / 失败模式库 /
机制覆盖 / 数据窗口 / 池大小。所有可增长的字段都在此**强制裁剪**，绝不指望调用方自觉。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from .evolution import DEFAULT_BUDGET, MOMENTUM_CAP, normalize_budget
from .fsa import SKELETON_STATS_CAP, FsaBan, prune_skeleton_stats
from .genomes import ALL_AXES
from .mechanisms import MECHANISM_KEYS, empty_coverage

CHECKPOINT_VERSION = 1
FSA_BANS_CAP = 64            # 冻结记录保留条数（含已过期的历史，供 UI 回溯）
REJECT_REASON_CAP = 40       # 拒绝原因分布的键数上限（长尾归并进 other）


@dataclass
class Checkpoint:
    """一次挖掘任务的断点状态（**必须**保持有界；新增字段须同步裁剪策略）。"""

    version: int = CHECKPOINT_VERSION
    iteration: int = 0
    rng_seed: int = 0
    budget: dict = field(default_factory=lambda: dict(DEFAULT_BUDGET))
    counters: dict = field(default_factory=lambda: {"tested": 0, "kept": 0, "rejected": {}})
    momentum: list = field(default_factory=list)
    fsa_bans: list = field(default_factory=list)
    skeleton_stats: dict = field(default_factory=dict)
    mechanism_coverage: dict = field(default_factory=empty_coverage)
    data_window: dict = field(default_factory=lambda: {"startTs": 0, "endTs": 0,
                                                       "lastRefreshTs": 0})
    pool_sizes: dict = field(default_factory=lambda: {axis: 0 for axis in ALL_AXES})
    kept_rate_trend: list = field(default_factory=list)
    # discovery-strategy-axes §2.10 的七个可缺省新字段（老检查点直接续跑）。
    axis_shares: dict = field(default_factory=dict)
    member_catalog_hash: str = ""
    template_signature: str = ""
    tested_by_axis: dict = field(default_factory=dict)
    skeleton_stats_by_axis: dict = field(default_factory=dict)
    fsa_bans_by_axis: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """序列化为 camelCase dict（落 ``discovery_runs.checkpoint_json``，写前统一裁剪）。"""
        return {
            "version": CHECKPOINT_VERSION,
            "iteration": int(self.iteration),
            "rngSeed": int(self.rng_seed),
            "budget": normalize_budget(self.budget),
            "counters": _bounded_counters(self.counters),
            "momentum": list(self.momentum)[-MOMENTUM_CAP:],
            "fsaBans": list(self.fsa_bans)[:FSA_BANS_CAP],
            "skeletonStats": _stats_to_pairs(self.skeleton_stats),
            "mechanismCoverage": _bounded_coverage(self.mechanism_coverage),
            "dataWindow": _bounded_window(self.data_window),
            "poolSizes": {axis: int(self.pool_sizes.get(axis, 0)) for axis in ALL_AXES},
            "keptRateTrend": [round(float(v), 6) for v in list(self.kept_rate_trend)[-3:]],
            "axisShares": {axis: int(self.axis_shares.get(axis, 0)) for axis in ALL_AXES},
            "memberCatalogHash": str(self.member_catalog_hash or ""),
            "templateSignature": str(self.template_signature or ""),
            "testedByAxis": {axis: int(self.tested_by_axis.get(axis, 0)) for axis in ALL_AXES},
            "skeletonStatsByAxis": {
                axis: _stats_to_pairs(self.skeleton_stats_by_axis.get(axis) or {})
                for axis in ALL_AXES},
            "fsaBansByAxis": {axis: list(self.fsa_bans_by_axis.get(axis) or [])[:FSA_BANS_CAP]
                              for axis in ALL_AXES},
        }

    @staticmethod
    def from_dict(raw: dict | None) -> "Checkpoint":
        """反序列化（版本不识别 / 字段缺失一律回落默认，**绝不抛**——断点损坏不该让 run 崩）。

        **BL-14 的老检查点回落（承重）**：缺 ``testedByAxis`` / ``skeletonStatsByAxis`` /
        ``fsaBansByAxis`` 时，把既有的**全局**值整体归给 ``factor`` 轴。归零会让一个跑了两周的
        因子任务在升级后把 ``trials`` 清零，F3/F11 的多重检验惩罚整体塌回基准值，第二天就会
        收进一批本该被惩罚掉的噪声因子。
        """
        data = raw if isinstance(raw, dict) else {}
        legacy_stats = _pairs_to_stats(data.get("skeletonStats"))
        legacy_bans = list(data.get("fsaBans") or [])[:FSA_BANS_CAP]
        legacy_tested = int(_bounded_counters(data.get("counters")).get("tested", 0))
        return Checkpoint(
            axis_shares={axis: int((data.get("axisShares") or {}).get(axis, 0) or 0)
                         for axis in ALL_AXES},
            member_catalog_hash=str(data.get("memberCatalogHash") or ""),
            template_signature=str(data.get("templateSignature") or ""),
            tested_by_axis=_by_axis_ints(data.get("testedByAxis"), legacy_tested),
            skeleton_stats_by_axis=_by_axis_stats(data.get("skeletonStatsByAxis"),
                                                  legacy_stats),
            fsa_bans_by_axis=_by_axis_bans(data.get("fsaBansByAxis"), legacy_bans),
            version=int(data.get("version", CHECKPOINT_VERSION) or CHECKPOINT_VERSION),
            iteration=int(data.get("iteration", 0) or 0),
            rng_seed=int(data.get("rngSeed", 0) or 0),
            budget=normalize_budget(data.get("budget")),
            counters=_bounded_counters(data.get("counters")),
            momentum=list(data.get("momentum") or [])[-MOMENTUM_CAP:],
            fsa_bans=list(data.get("fsaBans") or [])[:FSA_BANS_CAP],
            skeleton_stats=_pairs_to_stats(data.get("skeletonStats")),
            mechanism_coverage=_bounded_coverage(data.get("mechanismCoverage")),
            data_window=_bounded_window(data.get("dataWindow")),
            pool_sizes={axis: int((data.get("poolSizes") or {}).get(axis, 0) or 0)
                        for axis in ALL_AXES},
            kept_rate_trend=[float(v) for v in (data.get("keptRateTrend") or [])][-3:])


def dumps(checkpoint: Checkpoint) -> str:
    """序列化为紧凑 JSON 字符串（落库入口，单一出口便于测尺寸上界）。"""
    return json.dumps(checkpoint.to_dict(), ensure_ascii=False, separators=(",", ":"))


def loads(raw: str | None) -> Checkpoint:
    """从库里读回检查点（非法 JSON 一律回落初始状态，绝不让 run 因断点损坏崩掉）。"""
    try:
        return Checkpoint.from_dict(json.loads(raw or "{}"))
    except (json.JSONDecodeError, TypeError, ValueError):
        return Checkpoint()


def iteration_seed(base_seed: int, iteration: int) -> int:
    """每轮实际种子 = ``sha256(seed, iteration)``——可复现且续跑安全（§2.10）。

    直接用 ``base_seed + iteration`` 会让相邻两轮的随机流高度相关；哈希派生既复现又去相关。
    """
    digest = hashlib.sha256(f"{int(base_seed)}:{int(iteration)}".encode()).hexdigest()
    return int(digest[:12], 16)


def bump_reject(counters: dict, reason: str) -> dict:
    """累计一条拒绝原因（就地更新；长尾键在序列化时归并进 ``other``，保证有界）。"""
    rejected = counters.setdefault("rejected", {})
    key = str(reason or "unknown")
    rejected[key] = int(rejected.get(key, 0)) + 1
    return counters


# ============================ 裁剪（全部在序列化边界统一收口） ============================
def _bounded_counters(raw: object) -> dict:
    data = raw if isinstance(raw, dict) else {}
    rejected = data.get("rejected") if isinstance(data.get("rejected"), dict) else {}
    ordered = sorted(rejected.items(), key=lambda item: -int(item[1] or 0))
    head = dict(ordered[:REJECT_REASON_CAP])
    overflow = sum(int(v or 0) for _k, v in ordered[REJECT_REASON_CAP:])
    if overflow:
        head["other"] = int(head.get("other", 0)) + overflow
    return {"tested": int(data.get("tested", 0) or 0),
            "kept": int(data.get("kept", 0) or 0),
            "llmCalls": int(data.get("llmCalls", 0) or 0),
            "prunedCandidates": int(data.get("prunedCandidates", 0) or 0),
            "prunedIterations": int(data.get("prunedIterations", 0) or 0),
            "rejected": head}


def _bounded_coverage(raw: object) -> dict:
    """机制覆盖恒为 13 族全集（绝不惰性建键，否则 UI 热力图会随机缺格）。"""
    data = raw if isinstance(raw, dict) else {}
    coverage = empty_coverage()
    for key in MECHANISM_KEYS:
        coverage[key] = int(data.get(key, 0) or 0)
    unclassified = int(data.get("unclassified", 0) or 0)
    if unclassified:
        coverage["unclassified"] = unclassified
    return coverage


def _bounded_window(raw: object) -> dict:
    data = raw if isinstance(raw, dict) else {}
    return {"startTs": int(data.get("startTs", 0) or 0),
            "endTs": int(data.get("endTs", 0) or 0),
            "lastRefreshTs": int(data.get("lastRefreshTs", 0) or 0)}


def _stats_to_pairs(stats: dict) -> list:
    """失败模式库落成 ``[[skeleton, {tested,kept}]]``（保持顺序稳定，diff 友好）。"""
    pruned = prune_skeleton_stats(dict(stats or {}), cap=SKELETON_STATS_CAP)
    return [[key, {"tested": int(entry.get("tested", 0)), "kept": int(entry.get("kept", 0))}]
            for key, entry in sorted(pruned.items())]


def _by_axis_ints(raw: object, legacy_factor: int) -> dict:
    """逐轴整数计数；字段缺失时把老的全局值整体归 ``factor`` 轴（BL-14 回落）。"""
    if not isinstance(raw, dict) or not raw:
        return {axis: (legacy_factor if axis == "factor" else 0) for axis in ALL_AXES}
    return {axis: int(raw.get(axis, 0) or 0) for axis in ALL_AXES}


def _by_axis_stats(raw: object, legacy_factor: dict) -> dict:
    if not isinstance(raw, dict) or not raw:
        return {axis: (dict(legacy_factor) if axis == "factor" else {}) for axis in ALL_AXES}
    return {axis: _pairs_to_stats(raw.get(axis)) for axis in ALL_AXES}


def _by_axis_bans(raw: object, legacy_factor: list) -> dict:
    if not isinstance(raw, dict) or not raw:
        return {axis: (list(legacy_factor) if axis == "factor" else []) for axis in ALL_AXES}
    return {axis: list(raw.get(axis) or [])[:FSA_BANS_CAP] for axis in ALL_AXES}


def bans_of(raw: list) -> list[FsaBan]:
    """一组冻结记录 dict → ``FsaBan`` 列表（非法条目静默跳过，绝不因脏数据崩）。"""
    return [FsaBan.from_dict(item) for item in (raw or [])
            if isinstance(item, dict) and item.get("skeleton")]


def _pairs_to_stats(raw: object) -> dict:
    out: dict[str, dict] = {}
    for item in (raw or []):
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            continue
        key, entry = item
        if not isinstance(entry, dict):
            continue
        out[str(key)] = {"tested": int(entry.get("tested", 0) or 0),
                         "kept": int(entry.get("kept", 0) or 0)}
    return prune_skeleton_stats(out, cap=SKELETON_STATS_CAP)


def bans_from_checkpoint(checkpoint: Checkpoint) -> list[FsaBan]:
    """检查点里的冻结记录 → ``FsaBan`` 列表（非法条目静默跳过，绝不因脏数据崩）。"""
    return bans_of(checkpoint.fsa_bans)
