"""FSA 与失败模式库测试（strategy-factor-discovery §7.1）：超阈值冻结、TTL 解冻并可再冻结、
变体上限、失败模式黑名单触发条件、状态有界。"""
from __future__ import annotations

from app.discovery.fsa import (
    DEFAULT_BAN_TTL_ITERS,
    SKELETON_STATS_CAP,
    FsaBan,
    active_bans,
    blacklisted_skeletons,
    merge_bans,
    prune_expired,
    prune_skeleton_stats,
    scan_skeletons,
    update_skeleton_stats,
)

_HOT = "sub(ts_mean(?,_),delta(?,_))"
_COLD = "div(ts_std(?,_),ts_std(?,_))"


def _pool(hot: int, total: int = 100) -> list[str]:
    """``hot`` 条集中骨架 + 其余各自互异的稀疏骨架（后者占比 1%，恒在阈值之下）。"""
    return [_HOT] * hot + [f"{_COLD}#{i}" for i in range(total - hot)]


def test_scan_freezes_only_over_threshold_skeletons():
    bans = scan_skeletons(_pool(19), iteration=100, threshold=0.15)
    assert [b.skeleton for b in bans] == [_HOT]
    assert bans[0].share == 0.19 and bans[0].banned_at_iter == 100
    # 恰好等于阈值不冻结（判据是「超过」，边界不误伤）。
    assert scan_skeletons(_pool(15), iteration=100, threshold=0.15) == []


def test_scan_ignores_empty_pool():
    assert scan_skeletons([], iteration=5) == []
    assert scan_skeletons(["", ""], iteration=5) == []


def test_ttl_thaws_and_allows_refreeze():
    """冻结带 TTL（与报告的永久冻结**有意偏离**，理由见 R-6：长跑下永久冻结会饿死搜索）。"""
    ban = FsaBan(skeleton=_HOT, share=0.2, banned_at_iter=100)
    assert active_bans([ban], iteration=100, ttl_iters=DEFAULT_BAN_TTL_ITERS) == {_HOT}
    assert active_bans([ban], iteration=249, ttl_iters=DEFAULT_BAN_TTL_ITERS) == {_HOT}
    assert active_bans([ban], iteration=250, ttl_iters=DEFAULT_BAN_TTL_ITERS) == set()
    refrozen = merge_bans([ban], scan_skeletons(_pool(19), iteration=260))
    assert len(refrozen) == 1 and refrozen[0].banned_at_iter == 260
    assert active_bans(refrozen, iteration=260, ttl_iters=DEFAULT_BAN_TTL_ITERS) == {_HOT}


def test_ttl_zero_means_permanent_freeze():
    """``ttl_iters <= 0`` 回退报告的永久冻结口径（可配置，不是写死的偏离）。"""
    ban = FsaBan(skeleton=_HOT, share=0.2, banned_at_iter=1)
    assert active_bans([ban], iteration=10_000, ttl_iters=0) == {_HOT}


def test_merge_keeps_latest_and_history_is_retained():
    old = FsaBan(skeleton=_HOT, share=0.2, banned_at_iter=100)
    new = FsaBan(skeleton=_HOT, share=0.3, banned_at_iter=300)
    merged = merge_bans([old], [new])
    assert merged == [new]
    kept = prune_expired([old, new], iteration=1000, ttl_iters=150, keep_history=64)
    assert old in kept and new in kept      # 过期记录仍留作回溯，绝不清空即忘


def test_prune_expired_respects_history_cap():
    bans = [FsaBan(skeleton=f"s{i}", share=0.2, banned_at_iter=i) for i in range(100)]
    assert len(prune_expired(bans, iteration=10_000, ttl_iters=150, keep_history=64)) == 64


def test_skeleton_stats_accumulate_and_blacklist():
    stats: dict = {}
    for _ in range(19):
        update_skeleton_stats(stats, _COLD, kept=False)
    assert blacklisted_skeletons(stats, min_tested=20) == set()
    update_skeleton_stats(stats, _COLD, kept=False)
    assert blacklisted_skeletons(stats, min_tested=20) == {_COLD}
    # 入库过就不算失败模式，哪怕测了很多次。
    for _ in range(50):
        update_skeleton_stats(stats, _HOT, kept=False)
    update_skeleton_stats(stats, _HOT, kept=True)
    assert _HOT not in blacklisted_skeletons(stats, min_tested=20)
    assert stats[_HOT] == {"tested": 51, "kept": 1}


def test_skeleton_stats_ignore_empty_key():
    stats: dict = {}
    update_skeleton_stats(stats, "", kept=True)
    assert stats == {}


def test_skeleton_stats_are_bounded_for_the_checkpoint():
    """失败模式库进检查点，必须有界（§2.10 的 64 KB 目标）；裁掉的是最低频、信息量最小的。"""
    stats = {f"s{i}": {"tested": i, "kept": 0} for i in range(SKELETON_STATS_CAP + 50)}
    pruned = prune_skeleton_stats(stats)
    assert len(pruned) == SKELETON_STATS_CAP
    assert f"s{SKELETON_STATS_CAP + 49}" in pruned    # 高频保留
    assert "s0" not in pruned                          # 低频裁掉


def test_ban_serialization_roundtrip():
    ban = FsaBan(skeleton=_HOT, share=0.1875, banned_at_iter=42)
    assert FsaBan.from_dict(ban.to_dict()) == ban
    assert FsaBan.from_dict({}) == FsaBan(skeleton="", share=0.0, banned_at_iter=0)
