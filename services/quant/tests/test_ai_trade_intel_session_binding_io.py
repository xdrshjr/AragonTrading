"""会话级情报绑定与渐进式披露 —— 会话 / API / 运行时编排（spec §7.1 的 io 一半）。

纯核心命题在 `test_ai_trade_intel_session_binding_core.py`；分文件的理由是 800 行红线。

本文件里有两条用例的**跑法被 spec 的放行条件钉死**，照抄别的写法会得到一条恒绿的假门禁：

- `A4` 必须经一次**真实的** `rearm_group`（它内部走 `identity_of` 那段逐字段手抄的构造），
  **不许**直接构造 `GroupIdentity` 再断言字段还在；
- `D5` 必须显式断言「进程级 `Settings` 与运行时的派生副本**不相等**」——那正是「面板上关了、
  会话这侧照旧开着」这个缺陷唯一会显形的地方。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from app.ai_auto.intel import arming
from app.ai_auto.intel import binding as binding_mod
from app.ai_auto.intel import runtime as intel_runtime
from app.ai_auto.intel import scheduler as intel_scheduler
from app.ai_auto.intel import service as intel_service
from app.ai_auto.intel.session_rule import IntelSessionRule
from app.ai_auto.session import AiTradeSession
from app.config import Settings
from app.db import repositories as repo
from app.db.models import AppSetting
from app.db.session import get_sessionmaker, init_db

GROUP = "grp-intel-binding"
INST = "BTC-USDT-SWAP"
_ORIGIN = {"Origin": "http://localhost:18761"}


def _client() -> AsyncClient:
    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers=_ORIGIN)


@pytest.fixture(autouse=True)
async def _clean_registry():
    yield
    for task in list(intel_scheduler.tasks()):
        task.cancel()
    if intel_scheduler.tasks():
        await asyncio.wait(set(intel_scheduler.tasks()), timeout=0.5)
    intel_scheduler._TASKS.clear()
    intel_scheduler._LOOPS.clear()
    intel_runtime._RUNTIMES.clear()
    intel_runtime._BY_INST.clear()
    arming._ARM_ERRORS.clear()
    # `services/quant/tests` **共用同一个临时 DB**（本仓库成文纪律）：本文件往 `app_settings`
    # 写了 provider / key / 情报开关行，留着会让后面按 env 值装配的用例读到错的值。
    try:
        async with get_sessionmaker()() as session:
            await session.execute(delete(AppSetting))
            await session.commit()
    except Exception:  # noqa: BLE001 —— 引擎还没起来时无事可清
        pass


async def _fresh() -> None:
    await init_db()
    async with get_sessionmaker()() as session:
        await session.execute(delete(AppSetting))
        await session.commit()
        await repo.set_workspace_watchlist(session, watchlist=[INST], members=[],
                                           allocation_mode="even", symbol_groups=[])


async def _store(**rows) -> None:
    async with get_sessionmaker()() as session:
        for key, value in rows.items():
            await repo.set_app_setting(session, key, value)


async def _armed_runtime(*, session_rule: IntelSessionRule,
                         settings: Settings | None = None) -> intel_runtime.IntelRuntime:
    """装一个**真的**（armed）运行时：provider / key 走库内行，与生产路径同一条。"""
    await _store(**{"ai_intel.provider": "tavily",
                    intel_service.INTEL_API_KEY_APP_KEY: "k-live"})
    runtime = await intel_runtime.build_group_runtime(
        group_id=GROUP, members=[INST], settings=settings or Settings(),
        sessionmaker=get_sessionmaker, provider_cfg=object(),
        make_client=lambda *a, **k: object(), bar="15m",
        session_rule=session_rule)
    intel_runtime.register(runtime)
    return runtime


# ==================== 轴 A ====================
@pytest.mark.asyncio
async def test_a3_session_choice_is_not_overridden_by_the_db_row():
    """`A3`：`app_settings["ai_intel.inject"]=true` + 会话 `enabled=false` ⇒ `inject_on` 为假。

    这条正是 spec §3.1 A-2 那个陷阱的守卫：把会话选择编码成 `Settings.ai_trade_intel_inject`
    的实现会在这里判红——`build_group_runtime` 的第一件事就是叠加库内行。
    """
    await _fresh()
    await _store(**{"ai_intel.inject": True})
    runtime = await _armed_runtime(session_rule=IntelSessionRule(enabled=False))
    assert runtime.deps.settings.ai_trade_intel_inject is True       # 库内行真的生效了
    assert runtime.deps.inject_on is False                           # 会话选择仍然赢
    assert runtime.deps.intel_mode == "off"


@pytest.mark.asyncio
async def test_a4_session_rule_survives_a_real_rearm(monkeypatch):
    """`A4`（★ 评审 P0-4）：`enabled=False` 的会话经一次**真实 `rearm_group`** 之后仍然是关的。

    **跑法被钉死**：必须走 `rearm_group`（它内部经 `identity_of` 那段逐字段手抄的构造），
    不许直接构造 `GroupIdentity` 再断言字段还在——后者对本缺陷恒绿。

    漏抄那一行的表现是静默的，而且方向最坏：用户在设置中心保存一次**与情报无关**的参数就会
    触发重装，把启动时选的「不采信」翻成 `IntelSessionRule()` = 全文采信。
    """
    await _fresh()
    runtime = await _armed_runtime(session_rule=IntelSessionRule(enabled=False))
    monkeypatch.setattr(intel_scheduler, "start", lambda _rt: None)
    monkeypatch.setattr(intel_scheduler, "cancel", lambda _gid: None)

    # 改一个**与情报采信无关**的库内行，再重装（生产里就是「保存一次设置」这个动作）。
    await _store(**{"ai_intel.collect_interval_min": 45.0})
    result = await intel_runtime.rearm_group(
        group_id=GROUP, settings=Settings(), provider_cfg=object(),
        make_client=lambda *a, **k: object())

    assert result["rearmed"] is True
    assert runtime.deps.settings.ai_trade_intel_collect_interval_min == 45.0  # 真的重装了
    assert runtime.deps.session_rule == IntelSessionRule(enabled=False)
    assert runtime.deps.inject_on is False


def test_a7_alert_is_muted_when_the_session_did_not_opt_in():
    """`A7`（★ 评审 P0-3）：会话未采信 ⇒ 告警**不进决策通路**，只记一个数。

    注入与告警是两条独立通路：`wake_text._wake_intel_alert` 在 `inDigest=False` 时会把标题与
    摘要**就地内联**进唤醒句，而会话选择 `off` 恰恰使 `digest_ids` 恒空 ⇒ `inDigest` 恒 False。
    用户勾的是「不让 Agent 参考市场情报」，得到的会是「情报只从另一个门进来」。
    """
    from app.ai_auto.manager import AiTradeManager

    handed: list = []
    muted = {"n": 0}
    session = SimpleNamespace(
        note_intel_alert=lambda handoff: handed.append(handoff),
        note_intel_alert_muted=lambda: muted.__setitem__("n", muted["n"] + 1))
    member = SimpleNamespace(inst_id=INST, session=session)
    group = SimpleNamespace(group_id=GROUP, members=[member],
                            intel_rule=IntelSessionRule(enabled=False))
    manager = AiTradeManager()
    manager._group = group

    handoff = SimpleNamespace(alert_id=7, candidate=SimpleNamespace(item_id=42),
                              severity=1.0, in_digest=False)
    manager._on_intel_alert(GROUP, INST, handoff)
    assert handed == [] and muted["n"] == 1

    # 总闸关 ⇒ `intel_rule` 恒为默认规则（enabled=True）⇒ 告警照常唤醒（逐字节回退）。
    group.intel_rule = IntelSessionRule()
    manager._on_intel_alert(GROUP, INST, handoff)
    assert handed == [handoff] and muted["n"] == 1


def test_a6_resolve_intel_never_fails_the_start_request():
    """`A6`：情报层的任何异常都不让 `/ai-trade/start` 失败，回退方向是**关闭**。"""
    from app.api.ai_auto import _resolve_intel

    class _Boom:
        model_fields_set = {"intel"}

        @property
        def intel(self):
            raise RuntimeError("解析链上游炸了")

    rule, source = _resolve_intel(_Boom(), Settings())
    assert rule == IntelSessionRule(enabled=False)
    assert source == "resolve_failed"


def test_receipt_source_codes_cover_the_three_deterministic_paths():
    """回执 `source` 的三条确定性路径：`request` / `legacy_fallback` / `ops_off`。"""
    from app.api.ai_auto import _resolve_intel
    from app.schemas import AiTradeGroupStartIn

    body = AiTradeGroupStartIn(members=[{"instId": INST, "allocationPct": 10}],
                               strategyIds=[1], intel={"enabled": True, "mode": "full"})
    rule, source = _resolve_intel(body, Settings())
    assert (rule.enabled, rule.mode, source) == (True, "full", "request")

    legacy = AiTradeGroupStartIn(members=[{"instId": INST, "allocationPct": 10}],
                                 strategyIds=[1])
    rule, source = _resolve_intel(legacy, Settings())
    assert (rule, source) == (IntelSessionRule(), "legacy_fallback")

    rule, source = _resolve_intel(body, Settings(ai_trade_intel_session_opt_in_enabled=False))
    assert (rule, source) == (IntelSessionRule(), "ops_off")


def test_start_wire_always_carries_the_intel_key_after_this_feature():
    """`legacy` 判据的前提：本特性合入后每一次新启动的 `start_wire` 都**必然**含 `intel` 键。

    没有这条，`"intel" not in model_fields_set` 会把新会话也误判成老会话。
    """
    from app.schemas import AiTradeGroupStartIn

    body = AiTradeGroupStartIn(members=[{"instId": INST, "allocationPct": 10}],
                               strategyIds=[1], intel={"enabled": False, "mode": "index"})
    wire = body.model_dump(mode="json")
    assert "intel" in wire
    assert "intel" in AiTradeGroupStartIn(**wire).model_fields_set


# ==================== 轴 C / D ====================
@pytest.mark.asyncio
async def test_c1_binding_is_identical_on_all_three_read_paths(monkeypatch):
    """`C1` / `G-C2`：WS `intel` 事件、`snapshot.intel`、`GET /intel/{instId}` 三条通路逐键相同。"""
    await _fresh()
    runtime = await _armed_runtime(session_rule=IntelSessionRule(enabled=True, mode="index"))
    await runtime.refresh(INST)

    ws = (runtime.summary_for(INST) or {})["binding"]
    snapshot = (intel_runtime.intel_summary_for(INST) or {})["binding"]
    assert ws == snapshot

    monkeypatch.setattr("app.api.ai_intel.get_ai_trade_manager",
                        lambda: SimpleNamespace(group_status=lambda: {
                            "groupId": GROUP, "members": [{"instId": INST}]}))
    async with _client() as client:
        res = await client.get(f"/api/intel/{INST}")
    assert res.status_code == 200, res.text
    assert res.json()["summary"]["binding"] == ws


@pytest.mark.asyncio
async def test_c2_rest_keeps_the_binding_key_even_without_a_runtime(monkeypatch):
    """`C2`：没有运行时时 REST 仍然给出**形状恒定**的未绑定块（绝不缺键）。"""
    await _fresh()
    monkeypatch.setattr("app.api.ai_intel.get_ai_trade_manager",
                        lambda: SimpleNamespace(group_status=lambda: None))
    async with _client() as client:
        res = await client.get(f"/api/intel/{INST}")
    assert res.status_code == 200, res.text
    assert res.json()["summary"]["binding"] == binding_mod.unbound_wire()


@pytest.mark.asyncio
async def test_c4_note_disclosure_lands_on_the_next_summary_and_no_ops_when_unwired():
    """`C4`：`note_disclosure` 之后下一次 `summary_for` 带上 `lastRound`；未装配时 no-op 不抛。"""
    await _fresh()
    runtime = await _armed_runtime(session_rule=IntelSessionRule(enabled=True, mode="index"))
    await runtime.refresh(INST)
    wire = binding_mod.last_round_wire(
        round_no=7, ts=1_800_000_000,
        plan={"tier": "repeat", "show": [0], "same": 2, "hidden": 1, "refsKept": ["I02"]},
        chars=118)
    binding_mod.note_disclosure(INST, wire)
    assert (runtime.summary_for(INST) or {})["binding"]["lastRound"] == wire
    binding_mod.note_disclosure("ETH-USDT-SWAP", wire)          # 未装配 ⇒ no-op，绝不抛


@pytest.mark.asyncio
async def test_d5_session_side_limits_come_from_the_derived_copy_not_the_process_settings():
    """`D5`（★ 评审 P0-1 的假面板另一半）：面板改的是**库内行**，会话侧读到的必须是派生副本。

    本用例显式断言「进程级 `Settings` 与派生副本**不相等**」——两者不等**正是**要证明的那件
    事：读错一份的实现会在这里判红，而它在生产里的症状是「面板上关了、会话这侧照旧展开」，
    请求 200、开关照动、行为不变。
    """
    await _fresh()
    await _store(**{"ai_intel.expand_enabled": False})
    await _armed_runtime(session_rule=IntelSessionRule(enabled=True, mode="index"))

    assert Settings().ai_trade_intel_expand_enabled is True       # 进程级：仍然是开的
    limits = binding_mod.intel_limits_for(INST)
    assert limits is not None and limits.expand_enabled is False  # 派生副本：真的关了
    # 未装配 / 回测车道 ⇒ None ⇒ 会话侧整条情报链缺席（逐字节回退）。
    assert binding_mod.intel_limits_for("DOGE-USDT-SWAP") is None


@pytest.mark.asyncio
async def test_d1_three_new_gates_round_trip_through_the_settings_endpoint():
    """`D1`：三把新开关经 `PUT /settings/intel` 往返之后 `Settings` 上的值真的变了（假面板守卫）。"""
    await _fresh()
    async with _client() as client:
        res = await client.put("/api/settings/intel",
                               json={"sessionOptInEnabled": False, "expandEnabled": False,
                                     "repeatCompressEnabled": False})
        assert res.status_code == 200, res.text
        wire = res.json()
        assert wire["sessionOptInEnabled"] is False
        assert wire["expandEnabled"] is False
        assert wire["repeatCompressEnabled"] is False
        # 两个只读回显键必须下发（启动弹窗上那两个字数与那个比值全靠它们）。
        assert wire["indexMaxChars"] == 480 and wire["promptMaxChars"] == 1200

    async with get_sessionmaker()() as session:
        derived = await intel_service.resolve_intel_settings(session, Settings())
    assert derived.ai_trade_intel_session_opt_in_enabled is False
    assert derived.ai_trade_intel_expand_enabled is False
    assert derived.ai_trade_intel_repeat_compress_enabled is False


@pytest.mark.asyncio
async def test_b18_pin_is_state_not_a_parameter_of_refresh():
    """`B18`（★ 评审 P1-1）：告警落地之后、决策轮之前的任何一次 `refresh` 都不许抹掉置顶。

    `refresh` 有**四个**调用点（`_after_round` / `invalidate`（REST 的每一次写，含「标记
    已读」）/ 停调度收尾 / `scheduler._loop`）。只在其中一个传 `pin_item_id` 的实现会让
    「用户在弹窗里点一次标记已读」把置顶抹掉——而 `_alert` 早已把 `in_digest=True` 戳进台账
    与唤醒句，于是 prompt 说「该情报已在下方情报段中列出」而它并不在。
    """
    import time as _time

    await _fresh()
    runtime = await _armed_runtime(session_rule=IntelSessionRule(enabled=True, mode="index"))
    runtime._set_pin(INST, 42)
    assert runtime._live_pin(INST) == 42

    await runtime.refresh(INST)          # ← 模拟「标记已读」触发的那一次 invalidate → refresh
    assert runtime._live_pin(INST) == 42, "置顶必须是状态，不是一次调用的形参"

    # 越过 `alert_pending_ttl_s`（这条告警自己的唤醒窗口）⇒ 自动解除，不新造第二个时间常数。
    ttl = float(runtime.deps.settings.ai_trade_intel_alert_pending_ttl_s)
    runtime.deps.now = lambda: _time.time() + ttl + 1
    assert runtime._live_pin(INST) == 0

    # 一轮没有告警 ⇒ 解除上一轮的置顶（用旧新闻挤掉新证据是另一种说谎）。
    runtime.deps.now = _time.time
    runtime._set_pin(INST, 42)
    runtime._set_pin(INST, 0)
    assert runtime._live_pin(INST) == 0


@pytest.mark.asyncio
async def test_pin_gate_off_keeps_the_pinned_map_empty():
    """`G-4` 的运行时一半：L3 总闸关 ⇒ `_pinned` 恒空 ⇒ 注入排序逐字节回退。"""
    await _fresh()
    runtime = await _armed_runtime(
        session_rule=IntelSessionRule(enabled=True, mode="index"),
        settings=Settings(ai_trade_intel_alert_pin_enabled=False))
    assert runtime._pin_enabled is False
    handoff = SimpleNamespace(alert_id=1, candidate=SimpleNamespace(item_id=42))
    from app.ai_auto.intel.binding import pin_item_id

    runtime._set_pin(INST, pin_item_id(handoff, enabled=runtime._pin_enabled))
    assert runtime._pinned == {} and runtime._live_pin(INST) == 0


def test_b7_expand_is_injected_after_the_hold_early_return():
    """`B7`：L2 只在 `open_long` / `open_short` / `close` 上出现。

    **源码序门禁**：`hold` / `cancel_order` 的早返回必须排在 `inject_intel_expand` **之前**。
    这条判据取自源码位置而不是行为——`_reflect_before_execute` 要跑起来得有 executor /
    decider / WS，而这里要守的其实是「那一行别被挪到早返回上面去」这件结构性的事。
    """
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / "app" / "ai_auto"
            / "session.py").read_text(encoding="utf-8")
    early = text.index('decision.action in ("hold", "cancel_order")')
    inject = text.index("inject_intel_expand(payload, decision")
    assert early < inject, "L2 注入必须排在 hold / cancel_order 的早返回之后"


# ==================== 轴 B：会话内的四个重置点与置位时机 ====================
class _SessionSurrogate:
    """`AiTradeSession` 的四个情报字段 + 一个假 wake 层。

    **刻意不建一个真的会话**：`AiTradeSession.__init__` 要 executor / decider / WS / LLM 客户端，
    而本组用例要验的是四个重置点与置位时机这几条**纯状态**语义。方法用未绑定形式调用
    （`AiTradeSession._commit_intel_seen(self, …)`），故被验的是**生产代码那一份**，不是替身。
    """

    def __init__(self, *, inst_id: str = INST, wake_kind: str = "interval"):
        self.inst_id = inst_id
        self._wake = SimpleNamespace(last_round_kind=wake_kind)
        self._intel_seen_ids = frozenset()
        self._intel_round_ids = ()
        self._intel_repeat_streak = 0
        self._intel_force_full = False
        self._intel_disclosure = binding_mod.new_disclosure_counters()
        self._intel_disclosure_mode = "off"

    def _note_intel_round(self, wire, plan, *, round_no, ts):
        """观测与回填走生产代码那一份（未绑定调用），故 streak 的递增语义也被本组用例覆盖。"""
        AiTradeSession._note_intel_round(self, wire, plan, round_no=round_no, ts=ts)


def _commit(surrogate, context_reset):
    AiTradeSession._commit_intel_seen(surrogate, context_reset)


def test_b4c_seen_ids_do_not_move_when_commit_round_never_ran():
    """`B4c`：`commit_round` 没被调到（decide 抛异常 / 降级早退）⇒ `_intel_seen_ids` 不变。"""
    s = _SessionSurrogate()
    s._intel_round_ids = (1, 2)
    assert s._intel_seen_ids == frozenset()      # 装配期只暂存，不置位
    _commit(s, False)
    assert s._intel_seen_ids == frozenset({1, 2})
    # 下一轮装配了、但 commit 之前就崩了 ⇒ 暂存槽被清、已看过集合不动。
    s._intel_round_ids = (3,)
    s._intel_round_ids = ()                      # 模拟「这一轮的 payload 从未送达模型」
    _commit(s, False)
    assert s._intel_seen_ids == frozenset({1, 2})


def test_b13_context_summary_reset_forces_the_next_round_to_be_full(monkeypatch):
    """`B13`（★ 评审 P0-2a）：上下文被摘要吃掉之后，**紧接着那一轮是全量**。

    上一轮列出的那几条已经不在模型的上下文里了，再按编号引用它们就是一句确定错误的话，
    而且会让 `intelRefs` 静默塌成空集（`_intel_cite` 的分母不变、分子归零）。

    ⚠️ **只清空 `_intel_seen_ids` 是不够的**：清空之后那一轮若仍走 `repeat`，全部条目都会被
    当成新条目、再用 `repeat_max_chars`（只有纵向预算的一半）去裁——净效果是「上下文刚被
    摘要吃掉、情报段却只铺了半页」。故重置点置一面一次性旗，由 `_intel_payload` 消费。
    """
    from app.ai_auto.intel.taxonomy import IntelLimits

    monkeypatch.setattr("app.ai_auto.session.intel_limits_for",
                        lambda _inst: IntelLimits(repeat_max_chars=10))
    monkeypatch.setattr("app.ai_auto.session.note_disclosure", lambda *a, **k: None)

    s = _SessionSurrogate()
    s._intel_seen_ids = frozenset({1, 2, 3})
    s._intel_repeat_streak = 4
    s._intel_round_ids = (4,)
    _commit(s, True)
    assert s._intel_seen_ids == frozenset()
    assert s._intel_repeat_streak == 0
    assert s._intel_force_full is True
    assert s._intel_disclosure["seenReset"]["summary"] == 1

    intel = {"lines": ["[I01] · 一行足够长的情报行" * 3], "hidden": 0, "itemIds": [1],
             "refs": {"I01": 1}, "citeEnabled": True}
    wire = AiTradeSession._intel_payload(s, intel, 9, 0, {"contextDisclosure": "repeat"})
    assert wire["plan"]["tier"] == "full" and wire["plan"]["same"] == 0
    assert wire["plan"]["show"] == [0], "全量重铺 ⇒ 那一行必须真的渲染出来"
    assert s._intel_force_full is False, "一次性旗，消费即清"


def test_pause_and_resume_reset_the_seen_set_under_their_own_cause():
    """四个重置点里的两个：`pause()` / `resume()` 各记一次 `seenReset.pause`。"""
    s = _SessionSurrogate()
    s._intel_seen_ids = frozenset({9})
    AiTradeSession._reset_intel_seen(s, "pause")
    assert s._intel_seen_ids == frozenset()
    assert s._intel_force_full is True        # 跨越一次暂停 ⇒ 下一轮同样重铺全量
    assert s._intel_disclosure["seenReset"] == {"pause": 1, "summary": 0, "streak": 0}


def test_b14_repeat_streak_ceiling_forces_one_full_round(monkeypatch):
    """`B14`（★ 评审 P0-2a）：连续 `repeat` 撞 `repeat_max_rounds` ⇒ 该轮强制 `full` 并清零。

    存在的理由是 `_trim_context` 的左裁是**静默**的、会话拿不到任何回执；一个有界的重铺是
    唯一守得住那句「上一轮列出的 N 条仍然有效」的兜底。
    """
    from app.ai_auto.intel.taxonomy import IntelLimits

    limits = IntelLimits(repeat_max_rounds=2, repeat_compress_enabled=True)
    monkeypatch.setattr("app.ai_auto.session.intel_limits_for", lambda _inst: limits)
    monkeypatch.setattr("app.ai_auto.session.note_disclosure", lambda *a, **k: None)

    s = _SessionSurrogate()
    s._intel_seen_ids = frozenset({1})
    intel = {"lines": ["[I01] · 行"], "hidden": 0, "itemIds": [1],
             "refs": {"I01": 1}, "citeEnabled": True}
    payload = {"contextDisclosure": "repeat"}

    for _ in range(2):
        wire = AiTradeSession._intel_payload(s, intel, 1, 0, payload)
        assert wire["plan"]["tier"] == "repeat"
    # 第 3 轮撞上界 ⇒ 强制 full、streak 清零、`seenReset.streak` 加一。
    wire = AiTradeSession._intel_payload(s, intel, 3, 0, payload)
    assert wire["plan"]["tier"] == "full"
    assert s._intel_repeat_streak == 0
    assert s._intel_disclosure["seenReset"]["streak"] == 1


def test_b6_plan_is_written_on_a_shallow_copy_not_the_runtime_cache(monkeypatch):
    """`B6`：`plan` 写在 payload 的**拷贝**上 —— 多标的分组下改 A 不影响 B。

    运行时缓存被多个会话共享读，就地改会造成串扰，而串扰不报错。
    """
    from app.ai_auto.intel.taxonomy import IntelLimits

    monkeypatch.setattr("app.ai_auto.session.intel_limits_for", lambda _inst: IntelLimits())
    monkeypatch.setattr("app.ai_auto.session.note_disclosure", lambda *a, **k: None)
    cached = {"lines": ["[I01] · 行"], "hidden": 0, "itemIds": [1],
              "refs": {"I01": 1}, "citeEnabled": True}
    wire = AiTradeSession._intel_payload(_SessionSurrogate(), cached, 1, 0,
                                         {"contextDisclosure": "full"})
    assert "plan" in wire and "plan" not in cached      # 运行时缓存一个字节没被改
    assert wire["lines"] is cached["lines"]             # 浅拷贝：行本身不复制


def test_disclosure_mode_survives_the_runtime_being_unwired(monkeypatch):
    """终态读数的档位取**本会话真正渲染过的那一档**，不在拆卸之后现算。

    `stop_group` 今天的顺序是「先落 metrics、再 `_unwire_intel`」，但那是一条顺序约束而不是
    结构保证——现算一旦落在拆卸之后，一整份 `intelDisclosure` 会被悄悄丢掉（`binding_for`
    在注册表里查不到 ⇒ `mode == "off"` ⇒ `disclosure_stats` 返回 None）。
    """
    from app.ai_auto.intel.taxonomy import IntelLimits

    monkeypatch.setattr("app.ai_auto.session.intel_limits_for",
                        lambda _inst: IntelLimits(index_max_chars=3000))
    monkeypatch.setattr("app.ai_auto.session.note_disclosure", lambda *a, **k: None)
    s = _SessionSurrogate()
    intel = {"lines": ["[I01] · 行"], "hidden": 0, "itemIds": [1], "mode": "index",
             "refs": {"I01": 1}, "citeEnabled": True}
    AiTradeSession._intel_payload(s, intel, 1, 0, {"contextDisclosure": "full"})

    # 注册表里什么都没有（模拟已经拆卸），读数照样说得出自己是索引档。
    assert AiTradeSession._intel_mode(s) == "index"
    stats = binding_mod.disclosure_stats(s._intel_disclosure,
                                         mode=AiTradeSession._intel_mode(s))
    assert stats is not None and stats["mode"] == "index" and stats["roundsInjected"] == 1


def test_unwired_session_falls_back_byte_for_byte(monkeypatch):
    """`intel_limits_for` 回 `None`（未装配 / 回测车道）⇒ `plan` 键缺席 ⇒ 逐字节回退。"""
    monkeypatch.setattr("app.ai_auto.session.intel_limits_for", lambda _inst: None)
    s = _SessionSurrogate()
    cached = {"lines": ["[I01] · 行"], "hidden": 0, "itemIds": [1]}
    wire = AiTradeSession._intel_payload(s, cached, 1, 0, {"contextDisclosure": "repeat"})
    assert wire is cached and "plan" not in wire
    assert s._intel_round_ids == (1,)                  # 但「本轮看过哪些」照常暂存


def test_repeat_budget_dropouts_are_not_recorded_as_seen(monkeypatch):
    """`B4b` 的**第二层**（终审补）：被横向预算挤掉的新条目不许被记成「模型看过了」。

    `wire["itemIds"]` 是「digest 预算收进来的那些」，而 `plan.show` 才是「本轮真的渲染进
    prompt 的那些」。`repeat` 轮的 `repeat_max_chars` 同样会挤掉一部分**没见过**的条目
    （实施发现 I-4 新增的那一层 `hidden`），照 `itemIds` 记就等于把它们记成已看过——此后
    每一轮都被当作旧条目折叠，**永远进不了 prompt**，而且不报任何错。
    """
    from app.ai_auto.intel.taxonomy import IntelLimits

    # 预算只装得下第一行；第二、三行是没见过、却渲染不出来的那两条。
    monkeypatch.setattr("app.ai_auto.session.intel_limits_for",
                        lambda _inst: IntelLimits(repeat_max_chars=12))
    monkeypatch.setattr("app.ai_auto.session.note_disclosure", lambda *a, **k: None)

    s = _SessionSurrogate()
    intel = {"lines": ["[I01] · 一行", "[I02] · 一行", "[I03] · 一行"], "hidden": 0,
             "itemIds": [1, 2, 3],
             "refs": {"I01": 1, "I02": 2, "I03": 3}, "citeEnabled": True}
    wire = AiTradeSession._intel_payload(s, intel, 1, 0, {"contextDisclosure": "repeat"})

    assert wire["plan"]["tier"] == "repeat" and wire["plan"]["show"] == [0]
    assert wire["plan"]["hidden"] == 2, "被横向预算挤掉的两条如实计进 hidden"
    assert s._intel_round_ids == (1,), "只有真的渲染出来的那一条能进「本轮看过」"
    _commit(s, False)
    assert s._intel_seen_ids == frozenset({1})

    # 下一轮：那两条仍然是**新条目**（否则它们永远出不来）。
    wire = AiTradeSession._intel_payload(s, intel, 2, 0, {"contextDisclosure": "repeat"})
    assert wire["plan"]["same"] == 1, "只有第一条算「上一轮列过」"
    assert wire["plan"]["show"] == [1], "第二条这一轮终于排到了预算里"


def test_muted_alerts_are_the_only_exception_to_the_mode_off_absence():
    """未采信的会话上，`alertMuted` 是 `mode="off" ⇒ 整键缺席` 的**唯一**例外。

    这个数只在会话未采信情报时才可能非零（`manager._on_intel_alert` 的那处短路），而未采信
    恰好使 `mode == "off"` ∧ `roundsInjected == 0`——不开这个口，它就是一个永远到不了读点的
    写点，而「我关掉了情报，这一次会话因此有 N 条高危告警没有唤醒决策」正是用户唯一需要为
    这个选择付的账。
    """
    counters = binding_mod.new_disclosure_counters()
    assert binding_mod.disclosure_stats(counters, mode="off") is None   # D4 原样成立
    counters["alertMuted"] = 2
    stats = binding_mod.disclosure_stats(counters, mode="off")
    assert stats is not None and stats["alertMuted"] == 2
    assert stats["roundsInjected"] == 0, "其余读数在这一格全是 0，前端据它只渲染那一句"
