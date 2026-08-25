"""物化 / 重建的落库行为（ai-trade-experience-context-graph §4.5）。

覆盖 A-15（幂等）/ A-27（保留用户意志）/ A-43（自动静音可解除、用户静音不可覆盖）/
A-44（撕裂可见）/ A-46（置顶真的有用）/ A-52（进程级单槽 + 规模闸）/ A-45（脏标志跨进程）。

这批用例**必须在 G-8（符号齐备）绿了之后才有意义**：在 `upsert_lesson_nodes` 未被再导出之前
写的每一条物化用例，都会在一个恒抛且被 `except Exception` 吞掉的写路径上「通过」。
"""
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import delete

from app.ai_auto.experience import service as experience_service
from app.ai_auto.experience.aggregate import EventView
from app.config import get_settings
from app.db import repositories as repo
from app.db.models import AiExperienceEdge, AiExperienceEvent, AiExperienceNode, AppSetting
from app.db.session import dispose_engine, get_sessionmaker, init_db

_START = 1_700_000_000


async def _fresh() -> None:
    """清掉三张经验表与 `app_settings`（本仓库的用例共用同一个临时 DB）。"""
    await init_db()
    sm = get_sessionmaker()
    async with sm() as session:
        for table in (AiExperienceEdge, AiExperienceNode, AiExperienceEvent):
            await session.execute(delete(table))
        await session.execute(delete(AppSetting))
        await session.commit()


def _event_rows(count: int, *, net: float, side: str = "long", exit_source: str = "agent",
                first_seq: int = 0, inst: str = "BTC-USDT-SWAP") -> list[dict]:
    return [{
        "group_id": "grp-1", "run_id": 1, "inst_id": inst, "bar": "15m",
        "seq": first_seq + i, "origin": "live",
        "entry_ts": _START + i * 900, "exit_ts": _START + i * 900 + 600,
        "entry_wall_ts": _START + i * 900, "exit_wall_ts": _START + i * 900 + 600,
        "side": side,
        "context": {"symbol": inst, "bar": "15m", "regime": "range",
                    "volatility": "high_volatility", "event": "breakout", "side": side,
                    "wake": "candle_close", "exit": exit_source,
                    "horizon": "intraday_swing", "confidence": "high"},
        "outcome": {"netBps": net}, "cost_basis": "net", "fee_known": True,
        "net_bps": net + (i % 3) * 0.4, "gross_bps": net + 12.0, "cost_bps": 12.0,
        "edge_error_bps": 1.0, "hold_seconds": 600.0, "exit_source": exit_source,
        "created_ts": _START,
    } for i in range(count)]


async def _seed(rows: list[dict]) -> None:
    sm = get_sessionmaker()
    async with sm() as session:
        await repo.append_experience_events(session, rows)


async def _rebuild() -> dict:
    sm = get_sessionmaker()
    async with sm() as session:
        return await experience_service.rebuild(session, get_settings())


async def _lessons() -> list[dict]:
    sm = get_sessionmaker()
    async with sm() as session:
        return await repo.load_experience_nodes(session, kinds=("lesson",))


async def test_a15_rebuild_is_idempotent_and_a28_iron_really_promotes():
    """A-15（幂等）+ A-28（`iron` 真的能晋升）——两者**必须写在一条用例里**。

    「重建幂等」在本设计里有一个精确到刺人的边界（实施发现 I-4）：`iron` 的第三条门槛是
    **连续两次同号物化**（`sign_streak >= 2`），所以第一次与第二次重建之间 `tier` 必然从
    `normal` 变成 `iron`——那不是不幂等，那正是 §4.7 要的语义。把 A-15 写成「两次重建逐值相等」
    会与 A-28 直接矛盾，且两条用例只能有一条是绿的。

    真正的幂等判据因此是：**键集合与支撑数恒定，且档位在饱和之后不再变化**。
    """
    await _fresh()
    try:
        await _seed(_event_rows(24, net=-30.0))
        first = await _rebuild()
        keys_1 = {(n["key"], n["support"]) for n in await _lessons()}

        second = await _rebuild()
        keys_2 = {(n["key"], n["support"]) for n in await _lessons()}
        snapshot_2 = {(n["key"], n["support"], n["tier"]) for n in await _lessons()}

        third = await _rebuild()
        snapshot_3 = {(n["key"], n["support"], n["tier"]) for n in await _lessons()}

        assert keys_1 == keys_2                 # 同一批事件 ⇒ 同一批桶，永远
        assert snapshot_2 == snapshot_3         # 档位饱和后完全稳定
        assert first["nodes"] == second["nodes"] == third["nodes"]
        assert first["edges"] == second["edges"] == third["edges"]

        # A-28：`iron` 的第三条门槛（连续两次同号）真的会让档位前进——没有一个活得过物化的
        # 符号历史落点时，它在结构上永远判不成立 ⇒ L0 恒空 ⇒ 整个特性一个字都不喂。
        lessons = await _lessons()
        assert any(n["tier"] == "iron" for n in lessons), (
            "两次同号物化之后一条 iron 都没有 ⇒ sign_streak 没有被前滚（评审 P0-2 配套）")
        assert all(n["sign_streak"] >= 2 for n in lessons if n["tier"] == "iron")
    finally:
        await dispose_engine()


async def test_a27_and_a43_user_intent_survives_but_auto_mute_lifts():
    """A-27 / A-43（★ E-9 / 评审 P1-3）· 物化保留用户意志，但**自动静音必须能解除**。

    此前 `_apply_update` 从不触碰 `muted` / `mute_reason`，于是一条经验只要安静过一轮物化就被
    判为 `stale` 并**永久沉降**——即便后来拿到成百上千条新证据也再回不到 prompt，而 §4.7 的
    档位表写的是「未被新证据佐证」。那半句此前在代码里没有对应物。

    「绝不覆盖」的范围因此收窄为**用户意志**：`mute_reason == "user"` 的一律不动。
    """
    await _fresh()
    try:
        await _seed(_event_rows(24, net=-30.0))
        await _rebuild()
        lessons = await _lessons()
        assert lessons, "第一次物化一条经验都没产出——写路径可能又被吞了"

        sm = get_sessionmaker()
        target = lessons[0]
        async with sm() as session:
            await repo.patch_experience_node(session, target["id"],
                                             {"pinned": True, "muted": True})
        # 用户静音 + 置顶，连跑两次物化：两者都必须一字不变地活着。
        await _rebuild()
        await _rebuild()
        async with sm() as session:
            row = await repo.get_experience_node(session, target["id"])
        assert row["pinned"] is True
        assert row["muted"] is True
        assert row["mute_reason"] == "user"
        assert row["created_ts"] == target["created_ts"]
        assert row["first_ts"] == target["first_ts"]

        # 反向：一条被自动判 stale 的经验，带着新证据回来时**必须**解除静音。
        async with sm() as session:
            other = [n for n in await _lessons() if n["id"] != target["id"]][0]
            await repo.patch_experience_node(session, other["id"], {"muted": True})
            row = await repo.get_experience_node(session, other["id"])
            # 把它伪装成「自动沉降」而不是用户意志。
            await repo.upsert_lesson_nodes(session, [])      # 触一次写路径，确保它是活的
        async with sm() as session:
            node = await session.get(AiExperienceNode, other["id"])
            node.mute_reason = "stale"
            await session.commit()
        await _rebuild()
        async with sm() as session:
            revived = await repo.get_experience_node(session, other["id"])
        assert revived["muted"] is False and revived["mute_reason"] is None, (
            "自动静音没有随新证据解除 ⇒ 一条经验安静过一轮就永久沉降（评审 P1-3）")
    finally:
        await dispose_engine()


async def test_a46_pinned_manual_lesson_becomes_retrievable_after_materialize():
    """A-46（★ E-2 / 评审 P0-2）· 手工新增 → 置顶 → 一次物化 ⇒ 它出现在预演的 `l0` 里。

    三件事缺一，「置顶」就还是一个假按钮：① 物化要给它建 `applies_in` 边；② 证据要有下限；
    ③ 分层判据 `iron = (tier == "iron") or pinned`。这条用例把三者一起钉死。
    """
    await _fresh()
    try:
        await _seed(_event_rows(12, net=-30.0))
        sm = get_sessionmaker()
        clean = experience_service.sanitize_lesson(
            {"statement": "别在高波动突破里追多——成本吃掉大半毛额",
             "context": {"symbol": "BTC-USDT-SWAP", "volatility": "high_volatility"}},
            source="manual")
        assert clean is not None
        async with sm() as session:
            node = await repo.create_experience_node(session, clean)
        await _rebuild()

        axes = {"symbol": "BTC-USDT-SWAP", "bar": "15m", "volatility": "high_volatility"}
        async with sm() as session:
            before = await experience_service.preview(session, get_settings(), axes)
        assert clean["key"] not in [item["key"] for item in before["l0"]], (
            "未置顶的 candidate 不该进 L0（不变式 I-4）")

        async with sm() as session:
            await repo.patch_experience_node(session, node["id"], {"pinned": True})
        await _rebuild()
        async with sm() as session:
            after = await experience_service.preview(session, get_settings(), axes)
            edges = await repo.load_experience_edges(session)
            nodes = {n["id"]: n["key"] for n in await repo.load_experience_nodes(session)}
        assert clean["key"] in [item["key"] for item in after["l0"]], (
            "置顶后仍读不出来——「置顶的经验不受证据门槛约束」这句话就是假的（评审 P0-2）")
        assert any(nodes.get(e["src_id"]) == clean["key"] and e["rel"] == "applies_in"
                   for e in edges), "物化没有为手工经验建出 applies_in 边"
    finally:
        await dispose_engine()


async def test_a44_torn_rebuild_is_visible_and_does_not_raise():
    """A-44（★ E-14 / 评审 P1-8）· 重建是**四次提交**，撕裂必须可见。

    取消或异常落在两次提交之间会留下「新节点 + 旧边」的撕裂图，而 `match(l)` 对没有边的新节点
    恒为 0 ⇒ **L0 静默变空**。v1 不强行改成单事务（要动数据层四个函数的提交语义），
    改为**让撕裂可见**：`materialize_health.torn` + UI 明示 + 一键手动重建。
    """
    await _fresh()
    try:
        await _seed(_event_rows(24, net=-30.0))
        original = repo.replace_experience_edges

        async def _boom(*_args, **_kwargs):
            raise RuntimeError("replace_edges exploded")

        repo.replace_experience_edges = _boom
        try:
            result = await experience_service.materialize_bounded(get_settings(), force=True)
        finally:
            repo.replace_experience_edges = original
        assert result["ran"] is False and result["reason"] == "torn"

        sm = get_sessionmaker()
        async with sm() as session:
            wire = await experience_service.overview(session, get_settings())
        assert wire["materializeHealth"]["torn"] is True, (
            "撕裂没有被下发 ⇒ 用户看到的是「经验不再更新了，但哪里都没有报错」")
    finally:
        await dispose_engine()


async def test_a52_materialize_is_a_process_wide_single_slot_with_a_scale_gate():
    """A-52（★ E-6 / 评审 P0-6）· 进程级单槽 + 规模闸。

    `asyncio.timeout` 停不掉 `run_in_threadpool` 里已经开跑的 CPU 工作：20 秒后任务被判为「结束」
    ⇒ 在飞标志被清 ⇒ 60 秒后又起一个 ⇒ **每 60 秒叠一个永不完成的全库重建**，吃满全进程共用的
    anyio 线程池并饿死回测 / 挖掘。故在飞标志的生命周期必须覆盖线程里的真实工作全程。
    """
    await _fresh()
    try:
        await _seed(_event_rows(24, net=-30.0))
        calls = {"n": 0}
        original = experience_service.rebuild

        async def _slow(session, settings, **kwargs):
            calls["n"] += 1
            await asyncio.sleep(0.25)
            return await original(session, settings, **kwargs)

        experience_service.rebuild = _slow
        try:
            results = await asyncio.gather(*[
                experience_service.materialize_bounded(get_settings(), force=True)
                for _ in range(5)])
        finally:
            experience_service.rebuild = original
        assert calls["n"] == 1, "进程级单槽失效：并发触发叠了多个全库重建"
        assert sum(1 for r in results if r["ran"]) == 1
        assert all(r["reason"] == "busy" for r in results if not r["ran"])

        # 规模闸：事件数超上限 ⇒ **全量不跑**，只在 health 里落 overflow 并由 UI 明示。
        # ★ E-3（compounding）· 本轮把这道闸从「事件总数」判据下沉成「**本次真的要跑全量**」
        # 判据，故这里必须同时把增量关掉，否则大库上走的是增量、`overflow` 恒为 false
        # （那正是 G-17 断言的另一半：规模墙只约束全量）。
        # `model_copy` 绕过 `Field(ge=1000)` 的域，才能在 24 条事件上构造出越界断面。
        tiny = get_settings().model_copy(
            update={"ai_trade_experience_materialize_max_events": 5,
                    "ai_trade_experience_incremental_enabled": False})
        overflow = await experience_service.materialize_bounded(tiny, force=True)
        assert overflow["ran"] is False and overflow["reason"] == "overflow"
        sm = get_sessionmaker()
        async with sm() as session:
            wire = await experience_service.overview(session, get_settings())
        assert wire["materializeHealth"]["overflow"] is True, (
            "越界后没有在 UI 上说话 ⇒ 用户看到的是「经验不再更新了，但哪里都没有报错」")
    finally:
        await dispose_engine()


async def test_a45_dirty_flag_survives_the_process():
    """A-45（★ E-5 / 评审 P0-5）· 脏标志**落库**，冷启动补物化能消费它。

    桌面端正常退出走的是 `pause_group("app_quit")`（`ai_trade_shutdown_action` 默认 `"hold"`），
    manager 内存里的脏标志随进程消亡 ⇒ 最后一个去抖窗口内平掉的仓位**永远不会进图**。
    """
    await _fresh()
    try:
        await _seed(_event_rows(24, net=-30.0))
        sm = get_sessionmaker()
        async with sm() as session:
            await repo.set_app_setting(session, experience_service.DIRTY_KEY, True)

        result = await experience_service.consume_dirty_flag(get_settings())
        assert result["ran"] is True, "冷启动没有消费脏标志 ⇒ 关软件那一笔永远不进图"
        assert result.get("mode") == "full", "首次物化必须是全量（判据 B-4：还没有 epoch 底座）"

        async with sm() as session:
            still_dirty = await repo.get_app_setting(session, experience_service.DIRTY_KEY, False)
            wire = await experience_service.overview(session, get_settings())
        assert bool(still_dirty) is False, "补物化跑完没有清脏标志 ⇒ 每次启动都会重建一次"
        assert wire["materializeHealth"]["lastTs"] > 0

        # 标志为假时什么都不做（绝不在每次启动都白跑一次全库重建）。
        again = await experience_service.consume_dirty_flag(get_settings())
        assert again["ran"] is False and again["reason"] == "clean"
    finally:
        await dispose_engine()


async def test_debounce_only_skips_when_not_forced():
    """A-37（★ D-3）· 去抖：距上次成功物化不足窗口 ⇒ 只置脏标志；终局物化**豁免**去抖。"""
    await _fresh()
    try:
        await _seed(_event_rows(24, net=-30.0))
        assert (await experience_service.materialize_bounded(get_settings(), force=True))["ran"]
        second = await experience_service.materialize_bounded(get_settings())
        assert second["ran"] is False and second["reason"] == "debounced"

        sm = get_sessionmaker()
        async with sm() as session:
            dirty = await repo.get_app_setting(session, experience_service.DIRTY_KEY, False)
        assert bool(dirty) is True, "被去抖掉的那一次必须留下脏标志，否则那批事件永远不进图"

        # 终局物化（stop / pause / 冷启动补跑）豁免去抖——那是最后一次机会。
        # ★ W-B 之后它会先问一句「水位线之后还有新事件吗」：没有 ⇒ `up_to_date`（**不是**再跑
        # 一次全库重建），并顺手把脏标志清掉。这正是「一次物化的成本与库大小近似无关」的另一面。
        third = await experience_service.materialize_bounded(get_settings(), force=True)
        assert third["ran"] is False and third["reason"] == "up_to_date"
        async with sm() as session:
            assert bool(await repo.get_app_setting(
                session, experience_service.DIRTY_KEY, False)) is False, (
                "确认没有待处理事件之后仍留着脏标志 ⇒ 每 60 秒白问一次")

        # 真的又有新事件时，终局物化照常跑（豁免去抖这条语义没有变）。
        await _seed(_event_rows(6, net=-18.0, first_seq=900))
        assert (await experience_service.materialize_bounded(get_settings(), force=True))["ran"]
    finally:
        await dispose_engine()


async def test_e6_waiting_for_the_materialize_lock_is_bounded_and_never_leaks_it():
    """★ E-6②（实施发现 I-5）· 等锁**有界**，而且等不到时**绝不把锁泄漏出去**。

    E-6 只写了「`materialize_timeout_s` = 排队等锁最多等多久」这条语义，落点却漏了：`rebuild()`
    里原本是一个**裸** `async with materialize_lock()`。后果实测过——一个持锁者随事件循环消失
    之后，后续每一次物化**永久挂起**，全量 pytest 硬挂在同一个位置，而日志上什么都没有。

    实现刻意**不用** `asyncio.wait_for(lock.acquire())`：CPython 3.11 的 `Lock.acquire` 在
    「恰好被授予的同一刻被取消」时不回滚 `_locked`（3.12 才修），那等于把一个有界等待换成一个
    概率性的**永久锁泄漏**。本用例同时钉死两件事：超时会抛，且抛完之后那把锁仍然是可用的。
    """
    lock = repo.experience_materialize_lock()
    await lock.acquire()
    try:
        with pytest.raises(experience_service.MaterializeBusyError):
            async with experience_service._materialize_lock(0.1):
                raise AssertionError("锁被别人持着，不该拿得到")
    finally:
        lock.release()
    # 超时之后锁必须仍然可用——泄漏的形态就是这一行永远回不来。
    async with experience_service._materialize_lock(1.0):
        assert lock.locked() is True
    assert lock.locked() is False


def test_event_view_conversion_is_the_single_translation_point():
    """`_event_views` 是 DB 行 → 聚合视图的唯一转换点（聚合层不认识 ORM，也不认识 wire 键名）。"""
    views = experience_service._event_views([{
        "instId": "BTC-USDT-SWAP", "bar": "15m", "context": {"regime": "range"},
        "costBasis": "net", "feeKnown": True, "netBps": -3.0, "grossBps": 1.0,
        "costBps": 4.0, "edgeErrorBps": 0.5, "holdSeconds": 60.0,
        "exitWallTs": _START, "seq": 1, "groupId": "g"}])
    assert isinstance(views[0], EventView)
    assert views[0].eligible is True
