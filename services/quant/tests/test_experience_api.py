"""交易经验库端点单测（ai-trade-experience-context-graph §五）。

覆盖 A-16（不可信导入）/ A-17（`stat` 不可删）/ A-25（预演与注入同源）/ A-34（流式导出）/
A-40（`PUT` 不得绕过下限）/ A-41 与门禁 G-9（阈值真的会生效）/ A-48（两个诚实性读数有取数来源）/
A-49（导入体量闸 + `replace` 原子性）。

全程离线（不出网、不起会话）。
"""
from __future__ import annotations

import json

from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from app.ai_auto.experience import service as experience_service
from app.ai_auto.experience.disclosure import budget_floor
from app.config import get_settings
from app.db import repositories as repo
from app.db.models import AiExperienceEdge, AiExperienceEvent, AiExperienceNode, AppSetting
from app.db.session import dispose_engine, get_sessionmaker, init_db

_START = 1_700_000_000
# 端口同步点：18761 是 `security/origins.DEFAULT_ORIGINS` 里的 Next 前端端口。写别的会让
# 每一条写类断言都变成 403，而 403 与「端点判据坏了」在断言里长得一模一样。
_ORIGIN = {"Origin": "http://localhost:18761"}


def _app():
    from app.main import app
    return app


async def _fresh() -> None:
    await init_db()
    sm = get_sessionmaker()
    async with sm() as session:
        for table in (AiExperienceEdge, AiExperienceNode, AiExperienceEvent):
            await session.execute(delete(table))
        await session.execute(delete(AppSetting))
        await session.commit()


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=_app()), base_url="http://test",
                       headers=_ORIGIN)


def _rows(count: int, *, net: float = -30.0, group: str = "grp-1") -> list[dict]:
    return [{
        "group_id": group, "run_id": 1, "inst_id": "BTC-USDT-SWAP", "bar": "15m",
        "seq": i, "origin": "live",
        "entry_ts": _START + i * 900, "exit_ts": _START + i * 900 + 600,
        "entry_wall_ts": _START + i * 900, "exit_wall_ts": _START + i * 900 + 600,
        "side": "long",
        "context": {"symbol": "BTC-USDT-SWAP", "bar": "15m", "regime": "range",
                    "volatility": "high_volatility", "event": "breakout", "side": "long",
                    "wake": "candle_close", "exit": "agent", "horizon": "intraday_swing",
                    "confidence": "high"},
        "outcome": {"netBps": net}, "cost_basis": "net", "fee_known": True,
        "net_bps": net + (i % 3) * 0.4, "gross_bps": net + 12.0, "cost_bps": 12.0,
        "edge_error_bps": 1.0, "hold_seconds": 600.0, "exit_source": "agent",
        "created_ts": _START,
    } for i in range(count)]


async def _seed(count: int = 24) -> None:
    sm = get_sessionmaker()
    async with sm() as session:
        await repo.append_experience_events(session, _rows(count))


async def test_a17_stat_node_delete_is_refused_with_an_actionable_hint():
    """A-17 · `stat` 节点 DELETE ⇒ 409，且提示**可执行**（「改用静音」）。

    删了会被下一次物化按 `key` 原样复活——那是一个假动作，比拒绝更坏。而这句提示只有在
    「静音真的不会被物化覆盖」时才成立（A-27 钉那一半）。
    """
    await _fresh()
    try:
        await _seed()
        async with _client() as client:
            assert (await client.post("/api/experience/rebuild",
                                      json={"scope": "stat"})).status_code == 200
            lessons = (await client.get("/api/experience/lessons")).json()["items"]
            stat = next(n for n in lessons if n["source"] == "stat")
            res = await client.delete(f"/api/experience/nodes/{stat['id']}")
        assert res.status_code == 409
        body = res.json()["error"]
        assert body["code"] == "EXPERIENCE_STAT_NODE_UNDELETABLE"
        assert "静音" in body["message"]
    finally:
        await dispose_engine()


async def test_a16_and_a49_import_is_untrusted_input_with_a_size_gate():
    """A-16 / A-49（★ E-10 / 评审 P1-4）· 越界值被夹紧 / 整条丢弃；条数越界 ⇒ 422 且**一条不写**。

    R-8 列了六条防线却漏了**体量**：它是一次性 JSON body（导出是 NDJSON 流，导入不是），
    `nodes` 条数此前无上限。越界必须**拒绝**而不是静默截断——静默截断读起来像「全都导进去了」。
    """
    await _fresh()
    try:
        async with _client() as client:
            payload = {
                "version": 1, "mode": "merge", "edges": [{"x": 1}], "events": [{"y": 2}],
                "nodes": [
                    {"statement": "合法的一条", "context": {"regime": "range"},
                     "tier": "iron", "source": "stat"},                       # tier/source 被强制
                    {"statement": "越界轴", "context": {"心情": "好"}},         # 整条丢弃
                    {"statement": "", "context": {"regime": "range"}},        # 空陈述 ⇒ 丢弃
                ]}
            res = await client.post("/api/experience/import", json=payload)
        assert res.status_code == 200
        body = res.json()
        assert body["imported"]["nodes"] == 1
        assert body["rejected"]["nodes"] == 2
        assert body["rejected"]["edges"] == 1 and body["rejected"]["events"] == 1

        sm = get_sessionmaker()
        async with sm() as session:
            rows = await repo.load_experience_nodes(session, kinds=("lesson",))
        assert [r["source"] for r in rows] == ["imported"]      # source 强制
        assert [r["tier"] for r in rows] == ["candidate"]       # 一进门绝不是铁律

        limit = get_settings().ai_trade_experience_import_max_nodes
        async with _client() as client:
            too_big = await client.post("/api/experience/import", json={
                "version": 1, "mode": "merge",
                "nodes": [{"statement": f"第 {i} 条", "context": {"regime": "range"}}
                          for i in range(limit + 1)]})
        assert too_big.status_code == 422
        assert too_big.json()["error"]["code"] == "EXPERIENCE_IMPORT_TOO_LARGE"
        async with sm() as session:
            after = await repo.load_experience_nodes(session, kinds=("lesson",))
        assert len(after) == 1, "越界的导入写进去了一部分 ⇒ 静默截断（E-10 明令禁止）"
    finally:
        await dispose_engine()


async def test_a49_replace_import_is_atomic():
    """A-49 后半 · `mode="replace"` 中途失败 ⇒ **整体回滚**，既有手工节点一条不少。

    此前是「先逐条 delete 再逐条 create」，各自 commit；中途失败留下一个「删了一半」的经验库，
    而用户看到的只是「导入失败」。
    """
    await _fresh()
    try:
        sm = get_sessionmaker()
        clean = experience_service.sanitize_lesson(
            {"statement": "既有的手工经验", "context": {"regime": "range"}}, source="manual")
        async with sm() as session:
            await repo.create_experience_node(session, clean)

        # 打桩必须扎在**事务内部**（写第 3 条时），扎在 `_write_import` 外面等于让它自己先
        # commit 一次再抛——那样测的是「commit 之后再抛会不会回滚」，恒为否，与本条无关。
        original_dumps = experience_service.json.dumps
        calls = {"n": 0}

        def _boom(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] >= 5:            # 前两条各 2 次（stats/signature），第 3 条时炸
                raise RuntimeError("import exploded halfway")
            return original_dumps(*args, **kwargs)

        experience_service.json.dumps = _boom
        try:
            async with sm() as session:
                try:
                    await experience_service.import_graph(
                        session, {"nodes": [{"statement": f"新 {i}",
                                             "context": {"regime": "range"}}
                                            for i in range(3)]}, mode="replace")
                except RuntimeError:
                    pass
        finally:
            experience_service.json.dumps = original_dumps

        async with sm() as session:
            rows = await repo.load_experience_nodes(session, kinds=("lesson",))
        assert [r["label"] for r in rows] == ["既有的手工经验"], (
            "replace 中途失败留下了一个「删了一半」的库（评审 P1-4）")
    finally:
        await dispose_engine()


async def test_a48_overview_has_a_data_source_for_both_honesty_readings():
    """A-48（★ E-8 / 评审 P1-2）· `harvestGapTotal` 与 `materializeHealth` 必须**有取数来源**。

    两者在 §7.1 有渲染落点却没有读点时，概览条上那两个数字**恒为 0**——而它们恰恰是用来说
    「这张表偏乐观」与「自动重建已经跟不上了」的。这是 I-8 的镜像形态。
    """
    await _fresh()
    try:
        await _seed()
        sm = get_sessionmaker()
        async with sm() as session:
            await repo.set_app_setting(session, experience_service.HARVEST_GAP_KEY, 2)
        await experience_service.materialize_bounded(get_settings(), force=True)

        async with _client() as client:
            wire = (await client.get("/api/experience/overview")).json()
        assert wire["harvestGapTotal"] == 2
        health = wire["materializeHealth"]
        for key in ("lastTs", "lastMs", "events", "slow", "torn", "overflow"):
            assert key in health, f"materializeHealth 缺 {key} ⇒ §7.1 有一处渲染落点拿不到数字"
        assert health["lastTs"] > 0 and health["events"] == 24

        # 累计而非覆盖：第二次会话又漏一笔 ⇒ 变成 3。
        async with sm() as session:
            prior = int(await repo.get_app_setting(
                session, experience_service.HARVEST_GAP_KEY, 0))
            await repo.set_app_setting(session, experience_service.HARVEST_GAP_KEY, prior + 1)
        async with _client() as client:
            assert (await client.get("/api/experience/overview")).json()["harvestGapTotal"] == 3
    finally:
        await dispose_engine()


async def test_a40_put_settings_cannot_bypass_the_budget_floor():
    """A-40（★ E-7 / 评审 P1-1）· `PUT` 必须复用与启动期校验器**同一个**下限判据。

    `_check_experience_budgets` 只在 `Settings` 构造时跑，`PUT` 会完全绕过它——不在这里再判
    一次，用户就能从界面上把 L0 配成一个「永远渲染不出任何东西」的值而毫无提示。
    """
    await _fresh()
    try:
        floor = budget_floor("l0")
        async with _client() as client:
            bad = await client.put("/api/settings/experience", json={"l0MaxChars": floor - 1})
            good = await client.put("/api/settings/experience", json={"l0MaxChars": 480})
        assert bad.status_code == 422
        assert bad.json()["error"]["code"] == "EXPERIENCE_BUDGET_TOO_SMALL"
        assert str(floor) in bad.json()["error"]["message"]      # 错误体指明下限
        assert good.status_code == 200 and good.json()["l0MaxChars"] == 480
        assert good.json()["l0MinChars"] == floor                # 下限如实下发给 UI
    finally:
        await dispose_engine()


async def test_a41_g9_every_writable_threshold_has_a_read_path():
    """A-41 / 门禁 G-9（★ E-3 / 评审 P0-3 / 不变式 I-10）· **逐项**验证阈值真的会生效。

    `RetrievalConfig.from_settings` / `PromoteRule.from_settings` / `_options(settings)` 读的全是
    pydantic `Settings`，而 `PUT` 写的是 `app_settings` 稀疏 KV——两者之间必须有
    `resolve_experience_settings` 这座桥。**逐项**是关键：漏一项就是一个「改得动、永远不生效」
    的假控件，而它不会报任何错。
    """
    await _fresh()
    try:
        base = get_settings()
        # 每一项都给一个与默认值不同的合法值。
        patch = {
            "defaultOn": not base.ai_trade_experience_default_on,
            "writeEnabled": not base.ai_trade_experience_write_enabled,
            "minSamples": 3, "ironSamples": 6, "tThreshold": 1.25,
            "halfLifeDays": 12.5, "staleDays": 45.0,
            "l0Max": 2, "l0MaxChars": 520, "l1Max": 1, "l1MaxChars": 700,
            "distillEnabled": not base.ai_trade_experience_distill_enabled,
        }
        async with _client() as client:
            res = await client.put("/api/settings/experience", json=patch)
        assert res.status_code == 200

        sm = get_sessionmaker()
        async with sm() as session:
            derived = await experience_service.resolve_experience_settings(session, base)
        for wire_key, _app_key, field, _caster in experience_service.setting_bridge():
            assert getattr(derived, field) == patch[wire_key], (
                f"{wire_key} 写得进但读不到——`{field}` 没有被并回运行时真正读的那个对象"
                "（不变式 I-10：那就是一个用户改得动、系统永远不读、还不报错的假控件）")

        # ② 阈值真的改变行为：`minSamples=3` ⇒ n=3 的桶被晋升为 normal（默认 8 时不会）。
        await _seed(4)
        async with sm() as session:
            await experience_service.rebuild(session, derived)
            promoted = await repo.load_experience_nodes(session, kinds=("lesson",))
        assert any(n["tier"] != "candidate" for n in promoted), (
            "阈值改了但重建行为没变 ⇒ `_options(settings)` 读的还是旧对象")

        # ③ `enabled` 是运维总闸，本端点上只读：写它不生效（P2-4）。
        async with _client() as client:
            res = await client.put("/api/settings/experience", json={"enabled": False})
        assert res.status_code == 200
        assert res.json()["enabled"] is base.ai_trade_experience_enabled
    finally:
        await dispose_engine()


async def test_a25_preview_and_injection_share_one_snapshot():
    """A-25 · **同一份图快照下**，同一 seed 的 `preview.l0` 与 `runtime.inject` 的 `l0` 逐值相等。

    断言必须收窄到「同一份快照」：跨快照不成立（预演读当前 DB、会话用启动那一刻的内存快照），
    写成跨快照的断言会变成一条时红时绿的谎言。
    """
    await _fresh()
    try:
        await _seed()
        settings = get_settings()
        sm = get_sessionmaker()
        await experience_service.materialize_bounded(settings, force=True)

        from app.ai_auto.experience.retrieval import ContextSeed
        from app.ai_auto.experience.rules import ExperienceRule
        from app.ai_auto.experience.runtime import ExperienceRuntime

        rule = ExperienceRule(enabled=True, scope="symbol", write=True)
        async with sm() as session:
            snapshot = await experience_service.load_session_graph(
                session, settings=settings, rule=rule)
        assert snapshot is not None, "库里有事件却载不出图 ⇒ 物化写路径又被吞了"

        axes = {"symbol": "BTC-USDT-SWAP", "bar": "15m", "regime": "range"}
        runtime = ExperienceRuntime(snapshot, rule, settings)
        payload: dict = {}
        runtime.inject(payload, seed=ContextSeed(axes=axes, now_wall=float(_START)))

        async with sm() as session:
            preview = await experience_service.preview(session, settings, axes)
        assert [item["key"] for item in payload["experience"]["l0"]] == \
               [item["key"] for item in preview["l0"]]
        # I-9：页眉的两个整数与同一时刻的 overview 一致（首轮也一样）。
        async with _client() as client:
            wire = (await client.get("/api/experience/overview")).json()
        assert payload["experience"]["totals"]["events"] == wire["events"]
        assert payload["experience"]["totals"]["sessions"] == wire["sessions"]
        assert runtime.events_harvested == 0, "首轮就该是 0——而页眉里的那两个数不能是它"
    finally:
        await dispose_engine()


async def test_a34_export_is_streamed_with_a_counts_envelope():
    """A-34 · 导出是 NDJSON 流，首行 `counts` 与实际行数一致。

    一条被网络截断的 NDJSON 与一条完整的在语法上没有任何区别——这是导出格式必须自带体检项的
    理由。
    """
    await _fresh()
    try:
        await _seed(50)
        await experience_service.materialize_bounded(get_settings(), force=True)
        async with _client() as client:
            res = await client.get("/api/experience/export")
        assert res.status_code == 200
        assert res.headers["content-type"].startswith("application/x-ndjson")
        lines = [line for line in res.text.splitlines() if line.strip()]
        head = json.loads(lines[0])
        assert head["version"] == 1 and head["counts"]["events"] == 50
        kinds = [json.loads(line)["type"] for line in lines[1:]]
        assert kinds.count("event") == head["counts"]["events"]
        assert kinds.count("node") == head["counts"]["nodes"]
    finally:
        await dispose_engine()


async def test_graph_and_list_share_one_filter():
    """A-22 的后端一半：同一筛选下图谱与列表的经验条数**必然**一致（同数据源同判据）。"""
    await _fresh()
    try:
        await _seed()
        await experience_service.materialize_bounded(get_settings(), force=True)
        async with _client() as client:
            graph = (await client.get("/api/experience/graph?tier=iron&limit=2000")).json()
            listed = (await client.get("/api/experience/lessons?tier=iron&limit=500")).json()
        assert graph["total"] == listed["total"]
        # 边的 wire 统一 camelCase（评审 P2-3：同一个回执里两套命名会逼前端照抄一份不一致）。
        if graph["edges"]:
            assert set(graph["edges"][0]) >= {"srcId", "dstId", "rel", "weight"}
    finally:
        await dispose_engine()


async def test_manual_node_rejects_out_of_vocabulary_context():
    """手工新增：全部越界轴 ⇒ 422（不变式 I-5）；`tier` 升 `iron` 必须先置顶（I-4）。"""
    await _fresh()
    try:
        async with _client() as client:
            bad = await client.post("/api/experience/nodes", json={
                "statement": "编出来的轴", "detail": "", "context": {"心情": "好"},
                "polarity": "negative"})
            assert bad.status_code == 422
            assert bad.json()["error"]["code"] == "EXPERIENCE_CONTEXT_INVALID"

            created = await client.post("/api/experience/nodes", json={
                "statement": "别在急跌里接刀", "detail": "",
                "context": {"regime": "trend_down", "event": "crash"},
                "polarity": "negative"})
            assert created.status_code == 201
            node = created.json()["node"]
            assert node["tier"] == "candidate" and node["source"] == "manual"

            refused = await client.patch(f"/api/experience/nodes/{node['id']}",
                                         json={"tier": "iron"})
            assert refused.status_code == 422
            assert refused.json()["error"]["code"] == "EXPERIENCE_IRON_REQUIRES_PIN"

            allowed = await client.patch(f"/api/experience/nodes/{node['id']}",
                                         json={"pinned": True, "tier": "iron"})
            assert allowed.status_code == 200 and allowed.json()["node"]["tier"] == "iron"
    finally:
        await dispose_engine()


async def test_manual_rebuild_records_its_own_health_and_clears_the_torn_warning():
    """手动重建必须与后台物化**共用同一处健康写回**（`service.rebuild_manual`）。

    `materializeHealth` 是 §7.1 那三句注记的唯一取数来源，其中两句写的正是「……建议手动重建」。
    手动重建若不落健康读数：① 概览条的「上次重建」永远停在上一次**自动**物化的时刻；
    ② `torn` / `overflow` 两条警告在用户照做之后**永远不会消失**——产品告诉用户的做法，产品
    自己不兑现。这是本特性反复在守的那条线的镜像形态。
    """
    await _fresh()
    try:
        await _seed()
        sm = get_sessionmaker()
        # 先伪造一次「上次重建撕裂了」的现场：概览条此时会打出那条 `--warn` 注记。
        async with sm() as session:
            await repo.set_app_setting(session, experience_service.HEALTH_KEY, {
                "lastTs": 1, "lastMs": 0.0, "events": 0,
                "slow": False, "torn": True, "overflow": True})
            await repo.set_app_setting(session, experience_service.DIRTY_KEY, True)

        async with _client() as client:
            res = await client.post("/api/experience/rebuild", json={"scope": "stat"})
            wire = (await client.get("/api/experience/overview")).json()
        assert res.status_code == 200 and res.json()["torn"] is False

        health = wire["materializeHealth"]
        assert health["torn"] is False and health["overflow"] is False, (
            "用户照着提示点了「重建图谱」，那两条警告却还在——推荐的补救动作必须真的补救")
        assert health["lastTs"] > 1, "「上次重建」没有前进 ⇒ 手动重建没有落健康读数"
        assert health["events"] == 24

        async with sm() as session:
            dirty = await repo.get_app_setting(session, experience_service.DIRTY_KEY, False)
        assert dirty is False, "手动重建已经把库重建完了，脏标志还留着 ⇒ 冷启动会再白跑一次"
    finally:
        await dispose_engine()


async def test_import_collapses_duplicates_inside_the_file_and_says_so():
    """导入体**内部**的重复必须被夹紧，且如实报数。

    `AiExperienceNode.key` 是 UNIQUE 列，而 `manual_key` 只认「陈述 + 语境」——把两份导出拼在
    一起、或手改文件时复制一行，都会产出同 key 的两条。不去重就是在同一个事务里撞唯一约束
    ⇒ **整批回滚** ⇒ 用户看到的是「导入失败」而不是「其中一条是重复的」。
    """
    await _fresh()
    try:
        payload = {"version": 1, "mode": "merge", "nodes": [
            {"statement": "震荡里追突破亏钱", "context": {"regime": "range"}},
            {"statement": "震荡里追突破亏钱", "context": {"regime": "range"}},   # 逐字重复
            {"statement": "高波动要缩仓", "context": {"volatility": "high_volatility"}},
        ]}
        async with _client() as client:
            res = await client.post("/api/experience/import", json=payload)
        assert res.status_code == 200, "文件里有一条重复就整批失败 ⇒ 不可信输入没有被夹紧"
        body = res.json()
        assert body["imported"]["nodes"] == 2
        assert body["rejected"]["nodes"] == 0
        assert body["deduped"] == 1, "合并掉的重复必须说出来——静默合并读起来像「全都导进去了」"

        sm = get_sessionmaker()
        async with sm() as session:
            rows = await repo.load_experience_nodes(session, kinds=("lesson",))
        assert len({row["key"] for row in rows}) == 2
    finally:
        await dispose_engine()


async def test_muted_tier_filter_still_honours_the_search_box():
    """「档位 = 已沉降」与搜索词可以同时存在，判据不得在前者上早退。

    工具栏把两者做成两个独立控件，而 `_match` 一旦在 `tier == "muted"` 上 `return`，搜索框就
    **静默失效**：用户敲了字、结果一条没少，而界面上没有任何东西告诉他搜索没生效。
    """
    await _fresh()
    try:
        async with _client() as client:
            created = []
            for statement in ("震荡里追突破亏钱", "高波动要缩仓"):
                res = await client.post("/api/experience/nodes", json={
                    "statement": statement, "detail": "",
                    "context": {"regime": "range"}, "polarity": "negative"})
                assert res.status_code == 201
                created.append(res.json()["node"]["id"])
            for node_id in created:
                assert (await client.patch(f"/api/experience/nodes/{node_id}",
                                           json={"muted": True})).status_code == 200

            both = (await client.get("/api/experience/lessons?tier=muted")).json()
            filtered = (await client.get("/api/experience/lessons?tier=muted&q=缩仓")).json()
            # 图谱与列表同判据，故同一筛选下条数必须一致（A-22）。
            graph = (await client.get("/api/experience/graph?tier=muted&q=缩仓")).json()
        assert both["total"] == 2
        assert filtered["total"] == 1, "搜索词在「已沉降」档位下被静默忽略了"
        assert filtered["items"][0]["label"] == "高波动要缩仓"
        assert graph["total"] == filtered["total"]
    finally:
        await dispose_engine()
