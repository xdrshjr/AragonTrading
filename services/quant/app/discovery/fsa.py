"""FSA 频繁子树规避与失败模式库（strategy-factor-discovery §2.8）—— 纯函数、状态有界。

报告的 FSA：定期抽象因子结构并统计频次，超过 15% 阈值的骨架被禁止复用，迫使搜索转向新方向。

**与报告的有意偏离（承重，理由见 R-6）**：本实现的冻结**带 TTL**。报告只跑了 3 天，永久冻结
不会出问题；24/7 长跑下永久冻结会把搜索空间越切越小直至饿死。TTL 到期后该骨架重新参与频次
统计，若仍超阈值则立即再冻结。TTL 与冻结历史全部落迭代记录，UI 可回溯。
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

DEFAULT_FSA_EVERY = 25          # 每 N 轮扫一次骨架频次
DEFAULT_FSA_THRESHOLD = 0.15    # 占比超此值即冻结（报告口径）
DEFAULT_BAN_TTL_ITERS = 150     # 冻结时长（轮）；到期解冻并重新参与统计
SKELETON_STATS_CAP = 256        # 失败模式库条目上限（进检查点，须有界）


@dataclass(frozen=True)
class FsaBan:
    """一条冻结记录（进检查点与迭代记录，UI 据此展示「被冻结的骨架」chips）。"""

    skeleton: str
    share: float
    banned_at_iter: int

    def to_dict(self) -> dict:
        return {"skeleton": self.skeleton, "share": round(self.share, 4),
                "bannedAtIter": int(self.banned_at_iter)}

    @staticmethod
    def from_dict(raw: dict) -> "FsaBan":
        return FsaBan(skeleton=str(raw.get("skeleton", "")),
                      share=float(raw.get("share", 0.0) or 0.0),
                      banned_at_iter=int(raw.get("bannedAtIter", 0) or 0))


def scan_skeletons(kept_skeletons: list[str], *, iteration: int,
                   threshold: float = DEFAULT_FSA_THRESHOLD) -> list[FsaBan]:
    """统计**已入库**候选的骨架频次，返回本次应冻结的骨架（占比 > threshold）。

    只统计已入库者：被拒候选的骨架分布反映的是「搜索去过哪」，而 FSA 要抑制的是
    「产出集中在哪」——用错分母会把还没产出过任何东西的方向也冻掉。
    """
    if not kept_skeletons:
        return []
    counts = Counter(s for s in kept_skeletons if s)
    total = sum(counts.values())
    if total <= 0:
        return []
    return [FsaBan(skeleton=key, share=count / total, banned_at_iter=int(iteration))
            for key, count in counts.most_common()
            if count / total > float(threshold)]


def merge_bans(existing: list[FsaBan], fresh: list[FsaBan]) -> list[FsaBan]:
    """合并冻结名单：同骨架以**更晚**的一次为准（重新冻结即刷新 TTL 起点）。"""
    by_key: dict[str, FsaBan] = {ban.skeleton: ban for ban in existing}
    for ban in fresh:
        current = by_key.get(ban.skeleton)
        if current is None or ban.banned_at_iter >= current.banned_at_iter:
            by_key[ban.skeleton] = ban
    return sorted(by_key.values(), key=lambda b: (-b.banned_at_iter, b.skeleton))


def active_bans(bans: list[FsaBan], *, iteration: int,
                ttl_iters: int = DEFAULT_BAN_TTL_ITERS) -> set[str]:
    """当前仍在冻结期内的骨架集合（``ttl_iters <= 0`` 即永久冻结，回退报告口径）。"""
    if ttl_iters <= 0:
        return {ban.skeleton for ban in bans}
    return {ban.skeleton for ban in bans
            if iteration - ban.banned_at_iter < int(ttl_iters)}


def prune_expired(bans: list[FsaBan], *, iteration: int, ttl_iters: int,
                  keep_history: int = 64) -> list[FsaBan]:
    """裁剪过期冻结记录，但**保留最近 ``keep_history`` 条历史**供 UI 回溯（绝不清空即忘）。"""
    if ttl_iters <= 0:
        return bans[:keep_history]
    live = [b for b in bans if iteration - b.banned_at_iter < int(ttl_iters)]
    expired = [b for b in bans if iteration - b.banned_at_iter >= int(ttl_iters)]
    return (live + expired)[:keep_history]


# ============================ 失败模式库 ============================
def update_skeleton_stats(stats: dict[str, dict], skeleton: str, *, kept: bool) -> dict:
    """累计单个骨架的 ``{tested, kept}``（就地更新并返回同一 dict，调用方持有真源）。"""
    if not skeleton:
        return stats
    entry = stats.setdefault(skeleton, {"tested": 0, "kept": 0})
    entry["tested"] = int(entry.get("tested", 0)) + 1
    if kept:
        entry["kept"] = int(entry.get("kept", 0)) + 1
    return stats


def prune_skeleton_stats(stats: dict[str, dict], *, cap: int = SKELETON_STATS_CAP) -> dict:
    """按 ``tested`` 降序保留前 ``cap`` 条（检查点有界；被裁掉的都是低频、信息量最小的）。"""
    if len(stats) <= cap:
        return stats
    ordered = sorted(stats.items(), key=lambda item: -int(item[1].get("tested", 0)))
    return dict(ordered[:cap])


def blacklisted_skeletons(stats: dict[str, dict], *,
                          min_tested: int = 20) -> set[str]:
    """失败模式库：已测 ≥ ``min_tested`` 次且入库 0 次的骨架（生成阶段直接跳过）。"""
    return {key for key, entry in stats.items()
            if int(entry.get("tested", 0)) >= int(min_tested)
            and int(entry.get("kept", 0)) == 0}
