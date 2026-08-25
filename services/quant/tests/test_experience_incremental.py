"""增量物化的等价性与判据（ai-trade-experience-compounding W-B）。

**本文件里最重要的一条是 G-10**：增量算错了而无人察觉，是本轮唯一能悄悄毁掉整个经验资产的
失败形态（R-1）。它的判据不是「差不多」，是**逐值相等**——节点 key 集合、每个节点的
`label / signature / scope / support / weight / first_ts / last_seen_ts / muted / mute_reason`、
`stats` 的每一个键、边的**五**元组 `(src, dst, rel, weight, support)`。

两类明确的例外，各自单独钉死：
① `medianNetBps` 是 1bps 分箱的近似（误差 ≤ 一个箱宽），由 A-25 单独断言；
② `prevSign` / `signStreak` / `tier` **按定义就是路径依赖的**（`roll_sign` 每被调用一次
   streak 就 +1），故 G-10 把它们切出去、由 **G-10b**（一步增量）单独覆盖。

**等价的前提是「同一份成本分位」**：`costRegime` 决定桶身份，两条路径用不同的分位就不是在比
同一批桶——那种「红」说明不了任何问题。故预言机走 `materialize(..., cuts=<epoch 冻结的那份>)`。
"""
from __future__ import annotations

import random

import pytest
from sqlalchemy import delete

from app.ai_auto.experience import incremental
from app.ai_auto.experience import service as experience_service
from app.ai_auto.experience.aggregate import cost_regime_cuts
from app.ai_auto.experience.graph import materialize, node_from_row
from app.config import get_settings
from app.db import repositories as repo
from app.db.models import (
    AiExperienceBucket,
    AiExperienceContextCount,
    AiExperienceEdge,
    AiExperienceEvent,
    AiExperienceInjection,
    AiExperienceNode,
    AppSetting,
)
from app.db.session import dispose_engine, get_sessionmaker, init_db

_START = 1_700_000_000

_REGIMES = ("trend_up", "trend_down", "range")
_SIDES = ("long", "short")
_EXITS = ("agent", "profitGuard", "timeExit")
_INSTS = ("BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP")


async def _fresh() -> None:
    """清掉六张经验表与 `app_settings`（本仓库的用例共用同一个临时 DB）。"""
    await init_db()
    sm = get_sessionmaker()
    async with sm() as session:
        for table in (AiExperienceEdge, AiExperienceNode, AiExperienceEvent,
                      AiExperienceBucket, AiExperienceContextCount, AiExperienceInjection):
            await session.execute(delete(table))
        await session.execute(delete(AppSetting))
        await session.commit()


def _rows(count: int, *, seed: int, first_seq: int = 0) -> list[dict]:
    """一批**形态齐全**的事件：含 `unknown` 轴、缺席轴的 `origin="adopted"`、跨标的、
    `fee_known=False`、同 seq 反手腿。少了任何一种，G-10 都会在一条它本该抓住的路径上放行。"""
    rng = random.Random(seed)
    out: list[dict] = []
    for index in range(count):
        inst = _INSTS[index % len(_INSTS)]
        side = _SIDES[index % 2]
        net = rng.gauss(-4.0, 30.0)
        adopted = index % 11 == 0          # 降级事件：只写四条确定为真的轴
        context = {"symbol": inst, "bar": "15m", "side": side,
                   "exit": _EXITS[index % len(_EXITS)]}
        if not adopted:
            context.update({
                "regime": _REGIMES[index % len(_REGIMES)],
                # 每 7 条掺一个 `unknown` 轴：J-4 之后它不进桶身份，但**必须**照常入表。
                "volatility": "unknown" if index % 7 == 0 else "high_volatility",
                "event": "breakout", "wake": "candle_close",
                "horizon": "intraday_swing", "confidence": "high"})
        out.append({
            "group_id": f"grp-{index % 3}", "run_id": 1, "inst_id": inst, "bar": "15m",
            "seq": first_seq + index, "origin": "adopted" if adopted else "live",
            "entry_ts": _START + index * 900, "exit_ts": _START + index * 900 + 600,
            "entry_wall_ts": _START + index * 900,
            "exit_wall_ts": _START + index * 900 + 600,
            "side": side, "context": context, "outcome": {"netBps": net},
            "cost_basis": "net", "fee_known": index % 13 != 0,
            "net_bps": round(net, 4), "gross_bps": round(net + 12.0, 4),
            # 成本刻意**恒定**：`costRegime` 的三分位因此在任何子集上都相同，两条路径比的
            # 才是同一批桶。分位漂移本身由 A-20 单独覆盖。
            "cost_bps": 12.0, "edge_error_bps": 1.0, "hold_seconds": 600.0,
            "exit_source": _EXITS[index % len(_EXITS)], "created_ts": _START,
        })
    return out


async def _seed(rows: list[dict]) -> None:
    sm = get_sessionmaker()
    async with sm() as session:
        await repo.append_experience_events(session, rows)


async def _load_events():
    sm = get_sessionmaker()
    async with sm() as session:
        return experience_service._event_views(
            await repo.load_experience_events(session, repo.ExperienceEventQuery()))


async def _snapshot() -> dict:
    """把库里的图读成一个可逐值比对的普通结构。"""
    sm = get_sessionmaker()
    async with sm() as session:
        node_rows = await repo.load_experience_nodes(session)
        edge_rows = await repo.load_experience_edges(session)
    by_id = {row["id"]: row["key"] for row in node_rows}
    nodes = {row["key"]: node_from_row(row) for row in node_rows
             if row["kind"] == "lesson"}
    edges = {(by_id[e["src_id"]], by_id[e["dst_id"]], e["rel"],
              round(float(e["weight"] or 0.0), 6), int(e["support"] or 0))
             for e in edge_rows if e["src_id"] in by_id and e["dst_id"] in by_id}
    return {"nodes": nodes, "edges": edges}


def _oracle(events, cuts) -> dict:
    """预言机：同一批事件 + **同一份分位** 跑一次全量物化（纯内存，不落库）。"""
    plan = materialize(events, experience_service._options(get_settings()), None, cuts=cuts)
    return {node.key: node for node in plan.nodes if node.kind == "lesson"}


async def _prime_epoch(cuts: dict) -> None:
    """播一个「什么都还没折进来」的 epoch 底座，使下一次物化恰好走增量。

    这正是判据 B-4 的边界：`eventCount == 0` 时累加器表为空是**合法**的（从空底座上加 ==
    从零建），危险的只是「状态说有 N 条、累加器表却空了」——那一条由 B-5 的对账兜住。
    """
    sm = get_sessionmaker()
    async with sm() as session:
        await incremental.write_epoch(session, incremental.empty_epoch(epoch=1, cuts=cuts))


async def _run_increment(settings) -> dict:
    sm = get_sessionmaker()
    async with sm() as session:
        planned = await incremental.plan(session, settings)
        assert planned["mode"] == "incremental", planned["reason"]
        return await experience_service._run_materialize(session, settings, planned=planned)


# ---------------------------------------------------------------- G-10 / G-10b
@pytest.mark.parametrize("count", [500, 1500])
async def test_g10_incremental_equals_full_value_by_value(count: int) -> None:
    """**G-10** · 七个随机批次的增量 ≡ 一次全量（逐值，含 `muted` / `mute_reason` 与边的五元组）。"""
    await _fresh()
    try:
        settings = get_settings().model_copy(update={
            "ai_trade_experience_incremental_batch": max(20, count // 7)})
        rows = _rows(count, seed=count)
        await _seed(rows)
        events = await _load_events()
        cuts = cost_regime_cuts(
            events, min_samples=int(settings.ai_trade_experience_cost_regime_min_samples))
        await _prime_epoch(cuts)
        for _ in range(12):                       # 七批之外多跑几次，直到 up_to_date
            sm = get_sessionmaker()
            async with sm() as session:
                planned = await incremental.plan(session, settings)
            assert planned["mode"] == "incremental", planned["reason"]
            if planned["reason"] == incremental.REASON_UP_TO_DATE:
                break
            await _run_increment(settings)
        got = await _snapshot()
        want = _oracle(events, cuts)

        # 逐批物化会在中途留下一批「模板还没定下来」的旧 key（桶的统计量随样本增长会换模板，
        # 而模板进 `lesson_key` 的哈希）。**一串全量重建同样会留下它们**——那不是分叉，那是
        # 版本历史，正确的终局是它们被判为沉降而**不是**被删掉（删了就没人回答得了「这条经验
        # 去哪了」）。故：活着的那些必须与预言机逐值相等，其余每一条都必须是 `stale`。
        live = {key: node for key, node in got["nodes"].items() if not node.muted}
        assert set(live) == set(want), "活着的节点 key 集合不等 ⇒ 桶枚举分叉"
        for key, node in got["nodes"].items():
            if key in want:
                continue
            assert node.muted and node.mute_reason == "stale", (
                f"{key} 既不在本次产出里、又没有被判为沉降——那它是一条谁也解释不了的孤儿")
        for key, node in sorted(want.items()):
            mine = live[key]
            for field in ("label", "signature", "scope", "support", "weight",
                          "first_ts", "last_seen_ts", "muted", "mute_reason"):
                assert getattr(mine, field) == getattr(node, field), f"{key}.{field}"
            for stat_key, value in node.stats.items():
                if stat_key in ("medianNetBps", "prevSign", "signStreak"):
                    continue
                assert mine.stats.get(stat_key) == value, f"{key}.stats.{stat_key}"
            # A-25：`medianNetBps` 是 1bps 分箱的近似，误差不得超过一个箱宽。
            got_median = mine.stats.get("medianNetBps")
            want_median = node.stats.get("medianNetBps")
            if got_median is not None and want_median is not None:
                assert abs(got_median - want_median) <= 1.0, f"{key}.median"

        oracle_edges = {(e.src_key, e.dst_key, e.rel, round(float(e.weight), 6), int(e.support))
                        for e in materialize(events, experience_service._options(get_settings()),
                                             None, cuts=cuts).edges}
        # `replace_edges` 丢弃端点不在节点表里的悬空边；沉降节点的边同样还挂着（边是派生物、
        # 每次物化整体替换），故这里只比**活着的经验**那一侧——它才是检索真正会走到的图。
        live_edges = {edge for edge in got["edges"] if edge[0] in live}
        assert {edge for edge in oracle_edges if edge[0] in live} == live_edges
    finally:
        await dispose_engine()


async def test_g10b_single_step_increment_matches_full_on_sign_history() -> None:
    """**G-10b** · 一步增量 ⇒ 连 `prevSign` / `signStreak` / `tier` 都逐值相等。

    它与 G-10 的分工是「累加器算错了」与「批次切法不同」的**唯一分离判据**：
    G-10 红而本条绿 ⇒ 是切法；两条都红 ⇒ 是算错了。只留 G-10 会逼着实现方去放宽门禁。
    """
    await _fresh()
    try:
        settings = get_settings().model_copy(update={
            "ai_trade_experience_incremental_batch": 200000})
        rows = _rows(400, seed=7)
        await _seed(rows)
        events = await _load_events()
        cuts = cost_regime_cuts(
            events, min_samples=int(settings.ai_trade_experience_cost_regime_min_samples))
        await _prime_epoch(cuts)
        result = await _run_increment(settings)
        assert result["mode"] == "incremental" and result["events"] == len(rows)
        got = await _snapshot()
        want = _oracle(events, cuts)
        assert set(got["nodes"]) == set(want)
        for key, node in sorted(want.items()):
            mine = got["nodes"][key]
            assert mine.prev_sign == node.prev_sign, f"{key}.prevSign"
            assert mine.sign_streak == node.sign_streak, f"{key}.signStreak"
            assert mine.tier == node.tier, f"{key}.tier"
    finally:
        await dispose_engine()


# ---------------------------------------------------------------- 五条判据（A-17…A-24）
async def test_a17_a18_first_materialize_is_full_then_increments() -> None:
    """A-17 / A-18 · 首次物化 ⇒ 全量 + epoch=1 + 累加器写满；之后追加 ⇒ 增量、水位线前进。"""
    await _fresh()
    try:
        settings = get_settings()
        await _seed(_rows(60, seed=1))
        first = await experience_service.materialize_bounded(settings, force=True)
        assert first["ran"] and first["mode"] == "full"
        sm = get_sessionmaker()
        async with sm() as session:
            state = await incremental.read_epoch(session)
            assert state is not None and state["epoch"] == 1
            assert await repo.count_experience_buckets(session, 1) > 0
        watermark = state["watermarkEventId"]

        await _seed(_rows(20, seed=2, first_seq=1000))
        second = await experience_service.materialize_bounded(settings, force=True)
        assert second["ran"] and second["mode"] == "incremental"
        async with sm() as session:
            after = await incremental.read_epoch(session)
        assert after["watermarkEventId"] > watermark
        assert after["eventCount"] == 80
    finally:
        await dispose_engine()


async def test_a19_taxonomy_version_change_forces_full() -> None:
    """A-19 · 词表版本变更 ⇒ `fullReason="taxonomy"` 并走全量（否则图与代码语义永久错位）。"""
    await _fresh()
    try:
        settings = get_settings()
        await _seed(_rows(40, seed=3))
        await experience_service.materialize_bounded(settings, force=True)
        sm = get_sessionmaker()
        async with sm() as session:
            state = await incremental.read_epoch(session)
            state["taxonomyVersion"] = state["taxonomyVersion"] + 1
            await incremental.write_epoch(session, state)
            planned = await incremental.plan(session, settings)
        assert planned["mode"] == "full" and planned["reason"] == incremental.REASON_TAXONOMY
    finally:
        await dispose_engine()


async def test_a20_cuts_drift_forces_full() -> None:
    """A-20 · 成本分位漂移 > 容忍 ⇒ `fullReason="cuts_drift"`。

    分位漂了却继续用旧标签，等于「今天的 low 和半年前的 low 不是一回事」，而桶聚合把它们当
    同一格：一次缓慢的、完全不可察觉的失真。
    """
    await _fresh()
    try:
        settings = get_settings().model_copy(update={
            "ai_trade_experience_cost_regime_min_samples": 5})
        await _seed(_rows(40, seed=4))
        await experience_service.materialize_bounded(settings, force=True)
        shifted = _rows(40, seed=5, first_seq=2000)
        for row in shifted:                     # 成本量纲整体抬高一个数量级
            row["cost_bps"] = 250.0
        await _seed(shifted)
        sm = get_sessionmaker()
        async with sm() as session:
            planned = await incremental.plan(session, settings)
        assert planned["mode"] == "full"
        assert planned["reason"] == incremental.REASON_CUTS_DRIFT
    finally:
        await dispose_engine()


async def test_a21_a22_node_cap_and_missing_epoch_force_full() -> None:
    """A-21 / A-22 · 节点数越界、以及手工清库（epoch 缺席）都必须打回全量。"""
    await _fresh()
    try:
        settings = get_settings()
        await _seed(_rows(40, seed=6))
        await experience_service.materialize_bounded(settings, force=True)
        sm = get_sessionmaker()
        async with sm() as session:
            planned = await incremental.plan(
                session, settings.model_copy(update={"ai_trade_experience_graph_node_cap": 1}))
            assert planned["reason"] == incremental.REASON_NODE_CAP

            await session.execute(delete(AppSetting).where(
                AppSetting.key == incremental.EPOCH_KEY))
            await session.commit()
            planned = await incremental.plan(session, settings)
        assert planned["mode"] == "full" and planned["reason"] == incremental.REASON_NO_EPOCH
    finally:
        await dispose_engine()


async def test_a44_watermark_reconciliation_catches_interleaved_commits() -> None:
    """**A-44（★ E-11 / 判据 B-5）** · 「id 小的行后提交」必须被对账抓住，并自愈。

    id 在 `INSERT` 时分配、在 `COMMIT` 时可见。多成员会话各自 `persist_pending`，两条连接
    完全可能出现「id 100–105 比 106–110 后提交」。水位线一旦推到 110，100–105 就**永久不被
    折进累加器**，而且没有任何东西会发现——一条静默丢样本的路径。
    """
    await _fresh()
    try:
        settings = get_settings()
        await _seed(_rows(40, seed=8))
        await experience_service.materialize_bounded(settings, force=True)
        sm = get_sessionmaker()
        async with sm() as session:
            state = await incremental.read_epoch(session)
            # 模拟被跳过的那一段：水位线之下真实存在的行数比状态里记的多。
            state["eventCount"] = int(state["eventCount"]) - 5
            await incremental.write_epoch(session, state)
            planned = await incremental.plan(session, settings)
        assert planned["reason"] == incremental.REASON_COUNT_MISMATCH

        assert (await experience_service.materialize_bounded(settings, force=True))["ran"]
        async with sm() as session:
            healed = await incremental.read_epoch(session)
            settled = await repo.count_experience_events(
                session, up_to_id=healed["watermarkEventId"])
        assert settled == healed["eventCount"], "回全量之后水位线对账必须重新成立（自愈）"
    finally:
        await dispose_engine()


async def test_a23_batch_cap_reports_pending_and_keeps_the_dirty_flag() -> None:
    """A-23 · 满批 ⇒ 只吃一批、`pendingEvents` 如实回报、脏标志**仍为真**、下一次吃完。

    一个悄悄吃不完的队列在 UI 上与「已经跟上了」长得一模一样。
    """
    await _fresh()
    try:
        settings = get_settings().model_copy(update={
            "ai_trade_experience_incremental_batch": 100,
            # 本条测的是「批次上限」，不是分位漂移：把分组门槛调低，使 `(instId,bar)` 分位从
            # 第一批起就成立、此后不再有新分组跨过它（那条路由 A-20 单独覆盖）。
            "ai_trade_experience_cost_regime_min_samples": 3})
        await _seed(_rows(30, seed=9))
        await experience_service.materialize_bounded(settings, force=True)
        await _seed(_rows(130, seed=10, first_seq=5000))
        first = await experience_service.materialize_bounded(settings, force=True)
        assert first["mode"] == "incremental" and first["events"] == 100
        assert first["pendingEvents"] == 30
        sm = get_sessionmaker()
        async with sm() as session:
            assert bool(await repo.get_app_setting(
                session, experience_service.DIRTY_KEY, False)) is True
        second = await experience_service.materialize_bounded(settings, force=True)
        assert second["events"] == 30 and second["pendingEvents"] == 0
        async with sm() as session:
            assert bool(await repo.get_app_setting(
                session, experience_service.DIRTY_KEY, False)) is False
    finally:
        await dispose_engine()


async def test_a24_incremental_disabled_is_a_one_switch_rollback() -> None:
    """A-24 · 总闸关 ⇒ 恒走全量，行为与合入本轮之前逐值相同（一键回滚可用）。"""
    await _fresh()
    try:
        settings = get_settings().model_copy(update={
            "ai_trade_experience_incremental_enabled": False})
        await _seed(_rows(40, seed=11))
        assert (await experience_service.materialize_bounded(settings, force=True))["mode"] == "full"
        await _seed(_rows(10, seed=12, first_seq=7000))
        again = await experience_service.materialize_bounded(settings, force=True)
        assert again["mode"] == "full" and again["fullReason"] == incremental.REASON_DISABLED
    finally:
        await dispose_engine()


# ---------------------------------------------------------------- G-17 / A-41
async def test_g17_scale_wall_only_binds_the_full_path() -> None:
    """**G-17 / A-41** · 规模墙只约束全量——这是「规模墙被推开了」唯一的机械证据。

    大库 + 健康 epoch ⇒ 增量照跑、`overflow` 为 false；把增量关掉 ⇒ 同一个库上回
    `{"ran": False, "reason": "overflow"}`。
    """
    await _fresh()
    try:
        base = get_settings()
        await _seed(_rows(60, seed=13))
        assert (await experience_service.materialize_bounded(base, force=True))["ran"]
        await _seed(_rows(10, seed=14, first_seq=9000))
        # `model_copy` 绕过 `Field(ge=1000)` 的域，才能在几十条事件上构造出越界断面。
        tiny = base.model_copy(update={"ai_trade_experience_materialize_max_events": 5})
        ran = await experience_service.materialize_bounded(tiny, force=True)
        assert ran["ran"] and ran["mode"] == "incremental"
        sm = get_sessionmaker()
        async with sm() as session:
            health = incremental.health_wire(await incremental._read_health(session))
        assert health["overflow"] is False, "增量路径不该被规模墙挡住"

        await _seed(_rows(10, seed=15, first_seq=9500))
        no_inc = tiny.model_copy(update={"ai_trade_experience_incremental_enabled": False})
        blocked = await experience_service.materialize_bounded(no_inc, force=True)
        assert blocked["ran"] is False and blocked["reason"] == "overflow"
    finally:
        await dispose_engine()


# ---------------------------------------------------------------- A-39（E-1 的灾难复现）
async def test_a39_incremental_never_mutes_the_rest_of_the_library() -> None:
    """**A-39（★ E-1）** · 只碰几个桶的增量步，**绝不能**把其余经验判成沉降。

    `mark_stale_lessons` 是全表扫 + 反向标记：凡不在 `seen_keys` 里的一律静音。增量步若传给它
    的不是「本次算出的全部经验节点 key」（比如传了 `touched`、或传了**桶签名**那套 key），
    一个 tick 之内整库经验就会变成 `muted=stale` ⇒ `retrieval._collect` 过滤 muted ⇒
    L0/L1 当场变空，而 tsc / ruff / 全量用例全绿、日志一行都没有。

    ★ 这里同时跑一次 §十二 P7 的**十秒复现法**：增量之后立刻 `retrieve` 一颗与本批无关的种子，
    L0/L1 条数必须与增量前逐值相同。它比读一百行代码都快。
    """
    await _fresh()
    try:
        settings = get_settings()
        await _seed(_rows(240, seed=16))
        assert (await experience_service.materialize_bounded(settings, force=True))["ran"]
        before = await _snapshot()
        live_before = {key for key, node in before["nodes"].items() if not node.muted}
        assert len(live_before) > 50, "样本太小，这条用例证明不了什么"
        picked_before = await _retrieve_counts("ETH-USDT-SWAP", settings)

        # 只碰 BTC 那一支：ETH / SOL 的桶一条新事件都没拿到。
        await _seed([{**row, "inst_id": "BTC-USDT-SWAP",
                      "context": {**row["context"], "symbol": "BTC-USDT-SWAP"}}
                     for row in _rows(3, seed=17, first_seq=20000)])
        step = await experience_service.materialize_bounded(settings, force=True)
        assert step["mode"] == "incremental"

        after = await _snapshot()
        untouched = [key for key in live_before
                     if before["nodes"][key].signature.get("symbol")
                     in ("ETH-USDT-SWAP", "SOL-USDT-SWAP")]
        assert untouched, "样本里没有「未被碰过」的桶，这条用例证明不了什么"
        for key in untouched:
            node = after["nodes"].get(key)
            assert node is not None and not node.muted, (
                f"{key} 一条新事件都没拿到，却在一次增量之后被判成沉降——"
                "这正是 E-1 那场「一个 tick 静音整库」的灾难")
        picked_after = await _retrieve_counts("ETH-USDT-SWAP", settings)
        # 判据是**不许变少**而不是「逐值相同」：第二个 tick 里拿到新的同号证据的桶会正当地升上
        # `iron`，L0 因此可能变多——那是对的。E-1 那场灾难的签名是**塌成 0**。
        assert sum(picked_after) >= sum(picked_before) and sum(picked_after) > 0, (
            f"增量之后同一颗种子检索出来的条数从 {picked_before} 掉到 {picked_after}"
            " ⇒ 那一层已经悄悄变空了")
    finally:
        await dispose_engine()


async def _retrieve_counts(inst_id: str, settings) -> tuple[int, int]:
    """对一颗固定种子跑一次真检索，回 `(len(l0), len(l1))`（§十二 P7 的十秒复现法）。"""
    from app.ai_auto.experience.graph import build_graph
    from app.ai_auto.experience.retrieval import ContextSeed, RetrievalConfig, retrieve

    sm = get_sessionmaker()
    async with sm() as session:
        nodes = await repo.load_experience_nodes(session)
        edges = await repo.load_experience_edges(session, [row["id"] for row in nodes])
    graph = build_graph(nodes, edges)
    seed = ContextSeed(axes={"symbol": inst_id, "bar": "15m", "side": "long"},
                       now_wall=float(_START + 200000))
    result = retrieve(graph, seed, RetrievalConfig.from_settings(settings))
    return (len(result.l0), len(result.l1))


# ---------------------------------------------------------------- A-40（J-2b）
async def test_a40_untouched_buckets_freeze_their_sign_streak_and_iron_never_grows() -> None:
    """**A-40（★ E-4 / J-2b）** · 符号连续性只能变难，不能变容易。

    全量时代是「安静一轮也 +1」：一条经验哪怕一条新证据都没拿到，只要有人点了重建，它的
    `sign_streak` 就往前走一格——而那是 `iron`（L0 铁律）的第三道门槛。增量把「一次重算」
    切成「一个批次」之后，若照搬这条语义，门槛等于被悄悄放宽了 N 倍：一条**自动升档**路径，
    而 J-1 明令禁止这个方向。

    本轮的处置是**只对本批真被新事件碰过的桶前滚**。可检验的形态就是这一条：跑一次只碰一个
    标的的增量，其余经验的 `sign_streak` 必须**逐值不变**；且 `iron` 条数不增。
    """
    await _fresh()
    try:
        settings = get_settings()
        await _seed(_rows(120, seed=18))
        assert (await experience_service.materialize_bounded(settings, force=True))["ran"]
        before = await _snapshot()
        streaks_before = {k: n.sign_streak for k, n in before["nodes"].items()}
        iron_before = sum(1 for n in before["nodes"].values() if n.tier == "iron")

        # 只碰 BTC 那一支：其余标的的桶一条新事件都没拿到。
        await _seed([{**row, "inst_id": "BTC-USDT-SWAP",
                      "context": {**row["context"], "symbol": "BTC-USDT-SWAP"}}
                     for row in _rows(4, seed=19, first_seq=30000)])
        step = await experience_service.materialize_bounded(settings, force=True)
        assert step["mode"] == "incremental"

        after = await _snapshot()
        untouched = [key for key, node in after["nodes"].items()
                     if node.signature.get("symbol") not in (None, "BTC-USDT-SWAP")]
        assert untouched, "样本里没有「未被碰过」的桶，这条用例证明不了什么"
        for key in untouched:
            assert after["nodes"][key].sign_streak == streaks_before.get(key), (
                f"{key} 一条新证据都没拿到，`sign_streak` 却前滚了——"
                "那正是 J-1 明令禁止的自动升档方向")
        iron_after = sum(1 for n in after["nodes"].values() if n.tier == "iron")
        assert iron_before >= 0
    finally:
        await dispose_engine()

    # 「不增」的比较对象是**同一份数据、同样两个 tick 的全量路径**——不是「加事件之前」。
    # 一条经验在第二个 tick 里拿到新的同号证据而升上 `iron`，两条路径都会发生，那是正确的。
    # J-2b 要挡的是「增量比全量更容易升」。
    await _fresh()
    try:
        full_only = get_settings().model_copy(update={
            "ai_trade_experience_incremental_enabled": False})
        await _seed(_rows(120, seed=18))
        await experience_service.materialize_bounded(full_only, force=True)
        await _seed([{**row, "inst_id": "BTC-USDT-SWAP",
                      "context": {**row["context"], "symbol": "BTC-USDT-SWAP"}}
                     for row in _rows(4, seed=19, first_seq=30000)])
        await experience_service.materialize_bounded(full_only, force=True)
        iron_full = sum(1 for n in (await _snapshot())["nodes"].values() if n.tier == "iron")
    finally:
        await dispose_engine()
    assert iron_after <= iron_full, (
        f"同一份数据、同样两个 tick：增量得到 {iron_after} 条铁律、全量只有 {iron_full} 条"
        "——增量比全量更容易升 iron，J-1 的压舱石被 W-B 顶穿了")


# ---------------------------------------------------------------- G-15（规模承诺）
async def test_g15_an_increment_on_a_large_library_stays_far_under_the_budget() -> None:
    """**G-15** · 大库上新增 50 条事件的一次增量 **< 500ms**，并贴出同一库的全量耗时作对照。

    这条门禁守的是本轮的产品承诺本身：需求原文说「经验是累积的，随着交易的进行能够进行不断的
    更新」，而上一轮的实现是**每一次物化都从事件表全量重建**——库越过规模闸之后那句话就停下来了。
    增量的成本必须与库大小**近似无关**：它只读 `id > watermark` 的那一批，触碰的桶数由本批
    事件决定。达不到就回到设计，而不是放宽这个数字。
    """
    await _fresh()
    try:
        settings = get_settings()
        # 50000 条事件的库（分片写入，避免一次 INSERT 撞上 SQLite 的绑定参数上限）。
        for chunk in range(10):
            await _seed(_rows(5000, seed=100 + chunk, first_seq=chunk * 5000))
        full = await experience_service.materialize_bounded(settings, force=True)
        assert full["ran"] and full["mode"] == "full"

        await _seed(_rows(50, seed=999, first_seq=900000))
        step = await experience_service.materialize_bounded(settings, force=True)
        assert step["ran"] and step["mode"] == "incremental" and step["events"] == 50
        print(f"\n[G-15] 全量 {full['elapsedMs']}ms / 增量 {step['elapsedMs']}ms "
              f"（库 {full['events']} 条事件，本批 50 条）")
        assert step["elapsedMs"] < 500, (
            f"增量耗时 {step['elapsedMs']}ms ≥ 500ms —— 它已经与库大小相关了")
        assert step["elapsedMs"] < full["elapsedMs"], (
            "增量并不比全量快 ⇒ 「成本与库大小近似无关」这句承诺没有兑现")
    finally:
        await dispose_engine()


# ---------------------------------------------------------------- 回归（终审）：锁外计划过期
async def test_a_stale_plan_from_outside_the_lock_never_rebases_the_epoch() -> None:
    """**回归**（终审）· 锁外算出的计划进临界区之后必须**重新对表**。

    `materialize_bounded` 为了让规模闸看得见 `mode`，把 `plan()` 跑在物化锁**外**；而
    `POST /api/experience/rebuild`（「强制全量」）不受 `_MATERIALIZE_BUSY` 那个进程级单槽约束，
    完全可能在这中间抢到锁、`epoch+1` 并 purge 掉旧版本的累加器。照着过期计划往下走 =
    **在一个刚被清空的底座上折进本批** ⇒ 整张图被一批只含最后一批事件的节点替换、其余全部被
    判为沉降，而且一行日志都没有——本轮最危险的失败形态（R-1）恰好长这个样子。
    """
    await _fresh()
    try:
        # 分组门槛调高，使本轮**只**考察「计划过期」这一件事：否则第二批事件会让某个
        # `(instId, bar)` 分组跨过 `cost_regime_min_samples`，判据 B-2 先把这一次打回全量
        # （那条路径由 A-20 单独覆盖）。
        settings = get_settings().model_copy(update={
            "ai_trade_experience_cost_regime_min_samples": 100000})
        await _seed(_rows(60, seed=11))
        first = await experience_service.materialize_bounded(settings, force=True)
        assert first["ran"] and first["mode"] == "full"
        await _seed(_rows(30, seed=12, first_seq=5000))

        sm = get_sessionmaker()
        async with sm() as session:
            stale = await incremental.plan(session, settings)
            assert stale["mode"] == "incremental", stale["reason"]
            assert stale["rows"]
            # 另一条路径抢到锁：强制全量 ⇒ epoch+1，旧 epoch 的累加器被 purge 掉。
            await experience_service.rebuild_manual(
                session, settings, lock_timeout=5.0, scope="full")
            moved = await incremental.read_epoch(session)
            assert moved["epoch"] == int(stale["epoch"]["epoch"]) + 1
            # 现在照着那份**过期**的计划再跑一次。
            await experience_service._run_materialize(session, settings, planned=stale)
            after = await incremental.read_epoch(session)
            settled = await repo.count_experience_events(
                session, up_to_id=int(after["watermarkEventId"]))
        assert after["epoch"] == moved["epoch"], "过期计划把 epoch 拨回了已经被 purge 的旧版本"
        assert settled == int(after["eventCount"]), (
            "水位线与 eventCount 不自洽 —— 下一个 tick 的 B-5 会当场把它打回全量")

        got = await _snapshot()
        want = _oracle(await _load_events(), dict(after["cuts"] or {}))
        live = {key: node for key, node in got["nodes"].items() if not node.muted}
        assert set(live) == set(want), "图被重建在了一个空底座上（其余全部被判沉降）"
    finally:
        await dispose_engine()
