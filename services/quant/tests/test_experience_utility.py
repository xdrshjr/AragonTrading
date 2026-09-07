"""效用闭环（W-C，A-26…A-32 / A-34 / A-45）。

`node.weight` 说的是「这个语境桶历史上赚不赚」，**不是**「把这句话讲给模型之后有没有变好」。
两者是完全不同的量，而后者此前系统从来没有记录过答案——用户因此回答不了「这条经验值不值得
留着」，`v1:R-1`（自我强化的幻觉回路）也没有任何检测手段。

**这批用例守的四条纪律**（J-1 / J-5 在本模块的具体化）：只静音绝不反向加权；`ineffective` 与
`user` 严格分离；置顶豁免；文案一律「注入后表现」。
"""
from __future__ import annotations

from sqlalchemy import delete

from app.ai_auto.experience import service as experience_service
from app.ai_auto.experience import utility
from app.ai_auto.experience.events import EventIdentity
from app.ai_auto.experience.rules import ExperienceRule
from app.ai_auto.experience.runtime import ExperienceRuntime
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
_KEY = "lesson:deadbeef0001"


async def _fresh() -> None:
    await init_db()
    sm = get_sessionmaker()
    async with sm() as session:
        for table in (AiExperienceEdge, AiExperienceNode, AiExperienceEvent,
                      AiExperienceBucket, AiExperienceContextCount, AiExperienceInjection):
            await session.execute(delete(table))
        await session.execute(delete(AppSetting))
        await session.commit()


def _records(count: int, *, net: float, claim: int, fee_known: bool = True) -> list[dict]:
    """`count` 条注入记录；`claim` 是它当时断言的净额符号（冻结值）。"""
    return [{"lessonKey": _KEY, "layer": "l0", "claimSign": claim,
             "netBps": net + (index % 3) * 0.5, "feeKnown": fee_known,
             "costBasis": "net", "exitTs": _START + index}
            for index in range(count)]


async def _seed_lesson(*, pinned: bool = False, muted: bool = False,
                       mute_reason: str | None = None) -> int:
    sm = get_sessionmaker()
    async with sm() as session:
        node = await repo.create_experience_node(session, {
            "key": _KEY, "label": "BTC-USDT-SWAP 15m · 方向·做多：30 笔中亏 20 笔。",
            "source": "manual", "scope": "symbol", "tier": "normal",
            "signature": {"symbol": "BTC-USDT-SWAP", "bar": "15m", "side": "long"}})
        if pinned or muted:
            await repo.set_experience_lesson_mute(
                session, node["id"], muted=muted, reason=mute_reason)
            if pinned:
                await repo.patch_experience_node(session, node["id"], {"pinned": True})
    return node["id"]


async def _inject(rows: list[dict]) -> None:
    sm = get_sessionmaker()
    async with sm() as session:
        await repo.append_experience_injections(session, [{
            "group_id": "grp-1", "inst_id": "BTC-USDT-SWAP", "seq": index,
            "exit_ts": row["exitTs"], "lesson_key": row["lessonKey"], "layer": row["layer"],
            "claim_sign": row["claimSign"], "injected_wall_ts": row["exitTs"] - 600,
            "net_bps": row["netBps"], "fee_known": row["feeKnown"],
            "cost_basis": row["costBasis"], "created_ts": row["exitTs"],
        } for index, row in enumerate(rows)])


# ---------------------------------------------------------------- 纯核心（判据本身）
def test_the_single_ineffectiveness_criterion_needs_all_three_thresholds() -> None:
    """A-28 的纯核心 · 三重门槛缺一不可（只看符号会误杀小样本，只看 |t| 会杀掉「说亏确实亏」）。"""
    # 说「亏」（claim=-1），实际系统性为正、|t| 足够 ⇒ 失效。
    strong = utility.score(_records(20, net=25.0, claim=-1), min_samples=12, t_threshold=2.0)
    assert strong["ineffective"] is True
    assert strong["meanNetAfter"] > 0 and strong["claimSign"] == -1

    # 样本不足 ⇒ 不失效（噪声不该杀掉一条经验）。
    assert utility.score(_records(5, net=25.0, claim=-1),
                         min_samples=12, t_threshold=2.0)["ineffective"] is False
    # 方向一致（说亏、确实亏）⇒ 绝不失效。
    agreeing = utility.score(_records(20, net=-25.0, claim=-1),
                             min_samples=12, t_threshold=2.0)
    assert agreeing["ineffective"] is False and agreeing["agreement"] == 1.0
    # 断言无方向（claim=0）⇒ 判据不成立（我们没有可反驳的断言）。
    assert utility.score(_records(20, net=25.0, claim=0),
                         min_samples=12, t_threshold=2.0)["ineffective"] is False
    # 口径不合格（毛额 / 费率未知）不进 bps 聚合（`v1:I-7`）。
    excluded = utility.score(_records(20, net=25.0, claim=-1, fee_known=False),
                             min_samples=12, t_threshold=2.0)
    assert excluded["injected"] == 20 and excluded["scored"] == 0
    assert excluded["meanNetAfter"] is None and excluded["ineffective"] is False


# ---------------------------------------------------------------- A-28 / A-29
async def test_a28_a29_auto_mute_is_reversible_and_never_overwrites_user_intent() -> None:
    """A-28 / A-29 · 判据成立 ⇒ 自动静音；样本增长后不再成立 ⇒ 自动解除；`user` 静音绝不被改写。"""
    await _fresh()
    try:
        settings = get_settings()
        node_id = await _seed_lesson()
        await _inject(_records(20, net=25.0, claim=-1))
        sm = get_sessionmaker()
        async with sm() as session:
            report = await utility.refresh(session, settings)
        assert report["ineffective"] == 1 and report["muted"] == 1
        async with sm() as session:
            row = await repo.get_experience_node(session, node_id)
        assert row["muted"] is True and row["mute_reason"] == utility.MUTE_INEFFECTIVE

        # 新样本把方向拉回来 ⇒ 判据不再成立 ⇒ 自动解除。
        await _inject([{**row_, "exitTs": _START + 1000 + i}
                       for i, row_ in enumerate(_records(40, net=-30.0, claim=-1))])
        async with sm() as session:
            again = await utility.refresh(session, settings)
            row = await repo.get_experience_node(session, node_id)
        assert again["unmuted"] == 1
        assert row["muted"] is False and row["mute_reason"] is None
    finally:
        await dispose_engine()


async def test_user_mute_is_never_touched_by_the_automation() -> None:
    """纪律 2 · `mute_reason == "user"` 是人类意志，自动化一律不动（正反两向）。"""
    await _fresh()
    try:
        node_id = await _seed_lesson()
        sm = get_sessionmaker()
        async with sm() as session:
            await repo.patch_experience_node(session, node_id, {"muted": True})
            row = await repo.get_experience_node(session, node_id)
        assert row["mute_reason"] == "user"
        await _inject(_records(40, net=-30.0, claim=-1))      # 判据**不**成立 ⇒ 本该解除
        async with sm() as session:
            await utility.refresh(session, get_settings())
            row = await repo.get_experience_node(session, node_id)
        assert row["muted"] is True and row["mute_reason"] == "user", (
            "自动解除碰了用户亲手按下的静音——那是一次对人类意志的改写")
    finally:
        await dispose_engine()


# ---------------------------------------------------------------- A-30
async def test_a30_pinned_lessons_are_exempt_but_still_scored() -> None:
    """A-30 · 置顶的经验**永不**被自动静音，但效用读数照常产出并展示。

    用户的显式意志优先于统计——但要让用户自己看见「你置顶的这条，注入 20 次之后的均值是
    +25bps，而它说的是会亏」。
    """
    await _fresh()
    try:
        node_id = await _seed_lesson(pinned=True)
        await _inject(_records(20, net=25.0, claim=-1))
        sm = get_sessionmaker()
        async with sm() as session:
            report = await utility.refresh(session, get_settings())
            row = await repo.get_experience_node(session, node_id)
            scores = await utility.by_keys(session, get_settings(), [_KEY])
        assert report["ineffective"] == 1, "读数照常算"
        assert report["muted"] == 0 and row["muted"] is False, "置顶的经验绝不被自动静音"
        assert scores[_KEY]["ineffective"] is True, "但界面上必须看得见这条判定"
    finally:
        await dispose_engine()


# ---------------------------------------------------------------- A-31
async def test_a31_utility_switch_off_means_no_ledger_no_score_no_mute() -> None:
    """A-31 · 总闸关 ⇒ 不算、不静音，且 `overview` **整键缺席**（不是一个恒为 0 的假读数）。"""
    await _fresh()
    try:
        off = get_settings().model_copy(update={"ai_trade_experience_utility_enabled": False})
        node_id = await _seed_lesson()
        await _inject(_records(20, net=25.0, claim=-1))
        sm = get_sessionmaker()
        async with sm() as session:
            report = await utility.refresh(session, off)
            row = await repo.get_experience_node(session, node_id)
            wire = await experience_service.overview(session, off)
        assert report == {"scored": 0, "ineffective": 0, "muted": 0, "unmuted": 0}
        assert row["muted"] is False
        assert "utility" not in wire
        async with sm() as session:
            assert "utility" in await experience_service.overview(session, get_settings())
    finally:
        await dispose_engine()


# ---------------------------------------------------------------- A-26 / A-27
async def test_a26_a27_the_ledger_shares_one_commit_with_the_event_row() -> None:
    """**A-26 / A-27** · 台账与事件行**同一次 commit**；开了仓没平 ⇒ 台账**零行**。

    分两次提交会在崩溃窗口里留下「有事件、没台账」或反过来的半成品；开仓时就写则会留下一批
    `net_bps` 永远为 NULL 的悬空行，而那些行会静默拉偏效用统计。
    """
    await _fresh()
    try:
        settings = get_settings().model_copy(update={"ai_trade_net_pnl_enabled": True})
        runtime = ExperienceRuntime(
            None, ExperienceRule(enabled=True, scope="symbol", write=True), settings,
            EventIdentity(group_id="grp-1", run_id=1, inst_id="BTC-USDT-SWAP", bar="15m"))
        # 冻一个「本轮注入了两条经验」的开仓槽（`_last_wire` 是它的唯一取数点）。
        runtime._last_wire = {"l0": [{"key": "lesson:aaa"}], "l1": [{"key": "lesson:bbb"}]}
        from app.ai_auto.experience.events import ContextFacts

        runtime.note_open({"side": "long", "explain": {"meta": {"confidence": 0.8}}},
                          seq=1, facts=ContextFacts(
                              inst_id="BTC-USDT-SWAP", bar="15m", side="long",
                              market_context={"primary": "range"}, wake_kind="candle_close",
                              horizon_profile_id="intraday_swing", confidence=0.8))
        sm = get_sessionmaker()
        async with sm() as session:
            assert await repo.load_experience_injections(session) == [], (
                "开了仓不一定平（进程崩溃 / 被接管）——开仓时写台账会留下永远为 NULL 的悬空行")

        # 平仓：事件与台账必须一起出现。
        from app.ai_auto.experience.adopted import synth_open_snapshot
        from app.ai_auto.trip_ledger import SettleContext, settle

        snapshot = synth_open_snapshot(seq=1, side="long",
                                       entry={"px": 60000.0, "qty": 3.0, "ts": _START},
                                       open_fee_usd=1.8)
        trip = settle(snapshot, {"ts": _START + 600, "price": 61000.0, "qty": 3.0,
                                 "fee": 1.8, "pnl": 30.0,
                                 "feeBasis": "measured", "feeKnown": True},
                      SettleContext(net_mode=True, ct_val=0.01, exit_source="agent"))
        runtime.note_close({"side": "close", "ts": _START + 600},
                           {"exitSource": "agent", "wallTs": _START + 600}, trip)
        await runtime.persist_pending()
        async with sm() as session:
            events = await repo.load_experience_events(session, repo.ExperienceEventQuery())
            ledger = await repo.load_experience_injections(session)
        assert len(events) == 1 and len(ledger) == 2, "两条注入 ⇒ 两行台账"
        for row in ledger:
            assert (row["groupId"], row["instId"], row["seq"], row["exitTs"]) == (
                events[0]["groupId"], events[0]["instId"], events[0]["seq"],
                events[0]["exitTs"]), "自然键必须与事件行**逐字一致**（幂等语义同源）"
            assert row["netBps"] == events[0]["netBps"]
    finally:
        await dispose_engine()


# ---------------------------------------------------------------- A-34
async def test_a34_prune_dry_run_matches_the_real_run_and_skips_user_intent() -> None:
    """**A-34** · `dryRun` 的条数与真删一致；置顶与用户静音一律跳过并如实回报。"""
    await _fresh()
    try:
        sm = get_sessionmaker()
        async with sm() as session:
            keep_pinned = await repo.create_experience_node(session, {
                "key": "lesson:pinned", "label": "置顶的一条。", "source": "stat"})
            keep_user = await repo.create_experience_node(session, {
                "key": "lesson:user", "label": "用户静音的一条。", "source": "stat"})
            drop = await repo.create_experience_node(session, {
                "key": "lesson:stale", "label": "沉降的一条。", "source": "stat"})
            manual = await repo.create_experience_node(session, {
                "key": "lesson:manual", "label": "手写且沉降的一条。", "source": "manual"})
            await repo.set_experience_lesson_mute(
                session, keep_pinned["id"], muted=True, reason="stale")
            await repo.patch_experience_node(session, keep_pinned["id"], {"pinned": True})
            await repo.patch_experience_node(session, keep_user["id"], {"muted": True})
            await repo.set_experience_lesson_mute(session, drop["id"], muted=True,
                                                  reason="stale")
            await repo.set_experience_lesson_mute(session, manual["id"], muted=True,
                                                  reason="stale")

            preview = await experience_service.prune_stale(session, dry_run=True)
            assert preview == {"matched": 1, "deleted": 0,
                               "skippedPinned": 1, "skippedUserMuted": 1}
            assert (await repo.get_experience_node(session, drop["id"])) is not None

            done = await experience_service.prune_stale(session, dry_run=False)
            assert done["matched"] == preview["matched"] and done["deleted"] == 1
            assert (await repo.get_experience_node(session, drop["id"])) is None
            assert (await repo.get_experience_node(session, keep_pinned["id"])) is not None
            assert (await repo.get_experience_node(session, keep_user["id"])) is not None
            assert (await repo.get_experience_node(session, manual["id"])) is not None, (
                "只清 `source=stat` 的沉降经验——手写 / 导入的那些是用户自己写下来的")
    finally:
        await dispose_engine()


# ---------------------------------------------------------------- A-45（★ E-13）
async def test_a45_refresh_runs_inside_the_same_materialize_lock() -> None:
    """**A-45（★ E-13）** · `utility.refresh` 与 `mark_stale_lessons` 处在**同一个临界区**。

    两者写的是同一批 `muted` / `mute_reason` 列。跑在锁外会丢更新（把 `ineffective` 覆盖成
    `stale`，或反过来），而且是那种「数字不对但不报错」的丢法。判据不是读代码——是让一次真实
    物化跑完，然后看那条被判失效的经验有没有被同一次物化改写成 `stale`。
    """
    await _fresh()
    try:
        settings = get_settings()
        node_id = await _seed_lesson()
        await _inject(_records(20, net=25.0, claim=-1))
        result = await experience_service.materialize_bounded(settings, force=True)
        assert result["ran"] and "utility" in result
        sm = get_sessionmaker()
        async with sm() as session:
            row = await repo.get_experience_node(session, node_id)
        assert row["mute_reason"] == utility.MUTE_INEFFECTIVE, (
            "同一次物化把 `ineffective` 覆盖成了别的原因 ⇒ 两处写在锁外互相丢更新")
    finally:
        await dispose_engine()
