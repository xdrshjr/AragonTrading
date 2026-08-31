"""就地热重装（ai-trade-intel-arm-state §7.1 · G-10…G-19 / G-27 / G-30 / G-32）。

这一层的命题只有一句：**换配置、不换实例**。换实例的那条最直观的修法会撕断在飞的那一轮
（token 与检索额度白花）、在新旧两把 `asyncio.Lock` 的重叠期里制造并发、把弹窗计数闪回 0，
并且要手工搬运 `paused` 标志——漏搬就等于在一个已暂停的分组上偷偷放开采集。
"""
from __future__ import annotations

import asyncio
import time

import pytest
from sqlalchemy import delete

from app.ai_auto.intel import arming
from app.ai_auto.intel import runtime as intel_runtime
from app.ai_auto.intel import scheduler as intel_scheduler
from app.ai_auto.intel import service as intel_service
from app.config import Settings
from app.db import repositories as repo
from app.db.models import AppSetting
from app.db.session import get_sessionmaker, init_db

GROUP = "grp-1"
INST = "BTC-USDT-SWAP"


@pytest.fixture(autouse=True)
async def _clean_registry():
    """调度与注册表都是**模块级**单例：不清干净会跨用例污染。

    这里是 `async` 而不是同步夹具：收尾要在**事件循环还活着的时候**做。同步夹具跑在循环关闭
    之后，`task.cancel()` 会抛 `RuntimeError: Event loop is closed`，于是它下面那几行清理
    一行都不执行——上一个用例起的循环就这样活到了下一个用例里（本文件第一次跑就撞上了）。
    """
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
    # 里写了 provider / key 行，留着会让后面按 env 值装配的用例（`test_ai_intel_wiring.py` 的
    # 核查预算回落那条）读到 `provider="none"` 而判红——症状离原因很远。
    try:
        async with get_sessionmaker()() as session:
            await session.execute(delete(AppSetting))
            await session.commit()
    except Exception:  # noqa: BLE001 —— 引擎还没起来时无事可清
        pass


async def _fresh(**settings) -> None:
    """清空 `app_settings` 并按入参写好库内行（**key 只写库、env 留空**）。"""
    await init_db()
    async with get_sessionmaker()() as session:
        await session.execute(delete(AppSetting))
        await session.commit()
        for wire_key, app_key, _field, _cast in intel_service.INTEL_SETTING_BRIDGE:
            if wire_key in settings:
                await repo.set_app_setting(session, app_key, settings[wire_key])
        if "apiKey" in settings:
            await repo.set_app_setting(session, intel_service.INTEL_API_KEY_APP_KEY,
                                       settings["apiKey"])


def _shell(*, group_id=GROUP, bar="15m", change_24h_pct=None, notify=None,
           paused=False) -> intel_runtime.IntelRuntime:
    """一个**壳**运行时：`provider is None` 那条分支造出来的那一种（本轮的起点场景）。"""
    deps = intel_service.IntelDeps(settings=Settings(), sessionmaker=get_sessionmaker,
                                   group_id=group_id, bar=bar,
                                   change_24h_pct=change_24h_pct, now=time.time)
    runtime = intel_runtime.IntelRuntime(group_id, [INST], deps, enabled=False, notify=notify)
    runtime.paused = paused
    intel_runtime.register(runtime)
    return runtime


async def _rearm(runtime, *, deadline_s=2.5) -> dict:
    return await intel_runtime.rearm_group(
        group_id=runtime.group_id, settings=Settings(), provider_cfg=object(),
        make_client=lambda *a, **k: object(), deadline_s=deadline_s)


@pytest.fixture
def _no_loop(monkeypatch):
    """替身 `_loop`：真循环会去查库并睡 20 秒，用例只关心「有没有起来」。"""
    async def forever(_runtime):
        await asyncio.sleep(3600)

    monkeypatch.setattr(intel_scheduler, "_loop", forever)


# ---------------- G-7：三个新键在四条通路上都在位且同值 ----------------
@pytest.mark.asyncio
async def test_the_arm_keys_are_present_on_every_wire(_no_loop):
    """G-7：`_static_summary` / `summary_for` / `read_summary` / REST `_summary` 四条通路。

    这里钉死前两条（后两条由 `test_ai_intel_settings_rearm.py` 的端到端用例覆盖）。
    `_static_summary` 那一条**必须真的断言键在位**：它是一个只知道降级码的模块级函数，历轮四次
    的坑都是「承诺恒在位，写点却只有两处」——前端读到 `undefined` 会把「没装上」画成「一切正常」。
    """
    await _fresh()
    static = intel_runtime._static_summary(INST, code="disabled")
    for key in ("providerConfigured", "intelEnabled", "armed", "armReason"):
        assert key in static, key
    assert static["armed"] is False
    assert static["armReason"] == "disabled"
    assert static["intelEnabled"] is False
    assert static["providerConfigured"] is True, "关了总闸不等于没配源"
    assert static["degrade"]["code"] == "disabled"
    # **签名一个字节不改**：轮内降级码走 `arm_of_code` 的诚实回落分支（G-6b），
    # `test_ai_intel_alert_shutdown.py` 的既有调用点因此继续绿。
    legacy = intel_runtime._static_summary(INST, code="no_provider")
    assert legacy["armReason"] == "no_provider"
    assert legacy["providerConfigured"] is False

    runtime = _shell()
    live = runtime.summary_for(INST)
    assert set(("providerConfigured", "intelEnabled", "armed", "armReason")) <= set(live)
    assert live["armReason"] == runtime.arm.reason


# ---------------- G-10 / G-27：本轮的验收原命题 ----------------
@pytest.mark.asyncio
async def test_shell_becomes_armed_in_place_after_settings_are_saved(_no_loop):
    """G-10：**同一个实例**从壳变成装配好的运行时，且调度循环真的起来了。"""
    await _fresh(provider="tavily", apiKey="k-1")
    runtime = _shell()
    assert runtime.enabled is False
    result = await _rearm(runtime)
    assert result == {"rearmed": True, "pending": False, "armed": True,
                      "reason": "", "detail": "", "changed": True}
    assert intel_runtime.get_runtime(GROUP) is runtime, "换实例即撕断在飞轮，绝不允许"
    assert runtime.enabled is True
    assert runtime.deps.search is not None
    assert list(intel_scheduler.loops()) == [GROUP]
    assert runtime.summary_for(INST)["armed"] is True


@pytest.mark.asyncio
async def test_rearm_carries_the_group_identity_across(_no_loop):
    """G-27（评审 P0-1）：**壳恰恰是没有分组身份的那一个**——重装必须把身份显式带过去。

    漏了它，修好之后采到的每一条情报都挂着空 `groupId`、三种轮存根同样落空、Agent 拿到的是
    写死的 15m 市场上下文、「近 24 小时涨跌」整行消失——**而且没有任何东西会报错**。
    """
    await _fresh(provider="tavily", apiKey="k-1")
    change = (lambda inst: 3.5)
    runtime = _shell(group_id="grp-x", bar="4H", change_24h_pct=change)
    assert runtime.deps.group_id == "grp-x", "壳自己也必须带齐身份（唯一构造点的直接后果）"
    assert await _rearm(runtime) == {"rearmed": True, "pending": False, "armed": True,
                                     "reason": "", "detail": "", "changed": True}
    assert runtime.deps.group_id == "grp-x"
    assert runtime.deps.bar == "4H"
    assert runtime.deps.change_24h_pct is change
    assert runtime.deps.sessionmaker is get_sessionmaker


# ---------------- G-11：反向（关总闸） ----------------
@pytest.mark.asyncio
async def test_disabling_the_master_switch_stops_the_loop_and_pushes_the_reason(_no_loop):
    """G-11：同一实例被关掉、循环被 cancel、每个成员各收到一次带 `armReason` 的推送。

    （这条同时覆盖 E-18 的修法：推出去的那一份**必须**带齐 arm 四键——前端读到 `undefined`
    会把「没装上」画成「一切正常」。）
    """
    await _fresh(provider="tavily", apiKey="k-1")
    pushed: list[dict] = []
    runtime = _shell(notify=lambda inst, summary: pushed.append(summary))
    await _rearm(runtime)
    assert list(intel_scheduler.loops()) == [GROUP]

    await _fresh(provider="tavily", apiKey="k-1", enabled=False)
    result = await _rearm(runtime)
    assert result["armed"] is False
    assert result["reason"] == "disabled"
    assert runtime.enabled is False
    assert intel_scheduler.loops() == {}
    await asyncio.sleep(0.05)                       # 停机分支排的那次 refresh 是后台任务
    assert pushed, "停下来这件事也必须立刻在界面上看得见"
    last = pushed[-1]
    for key in ("providerConfigured", "intelEnabled", "armed", "armReason"):
        assert key in last, "裸字典绝不允许被推出去（E-18）"
    assert last["armReason"] == "disabled"
    assert last["intelEnabled"] is False
    assert last["providerConfigured"] is True, "关了总闸不等于没配源——那是两句不同的话"


# ---------------- G-12 / G-13：缓存与暂停标志 ----------------
@pytest.mark.asyncio
async def test_rearm_keeps_the_caches_and_the_paused_flag(_no_loop):
    """G-12 / G-13：计数不闪回 0；`paused` 天然保留（漏搬 = 在暂停的分组上偷偷放开采集）。"""
    await _fresh(provider="tavily", apiKey="k-1")
    runtime = _shell(paused=True)
    runtime._summary[INST]["counts"] = {"ultra": 3, "mid": 1, "long": 0, "archived": 2}
    runtime._digest[INST] = {"lines": ["[I01] 旧的那一条"], "hidden": 0}
    await _rearm(runtime)
    assert runtime.summary_for(INST)["counts"] == {"ultra": 3, "mid": 1, "long": 0,
                                                   "archived": 2}
    assert runtime.digest_for(INST) == {"lines": ["[I01] 旧的那一条"], "hidden": 0}
    assert runtime.paused is True


# ---------------- G-14：在飞轮不被撕断 ----------------
@pytest.mark.asyncio
async def test_an_in_flight_round_is_never_torn_and_the_rearm_goes_background(_no_loop):
    """G-14：持锁模拟一轮在飞 ⇒ `pending=True` 且**不抛**；释放锁之后后台任务把配置应用上。"""
    await _fresh(provider="tavily", apiKey="k-1")
    runtime = _shell()
    async with runtime.lock:
        result = await _rearm(runtime, deadline_s=0.05)
        assert result["pending"] is True
        assert result["reason"] == "arm_pending"
        assert result["changed"] is True
        assert runtime.arm.reason == "arm_pending"
        assert runtime.arm_pending is True
        assert runtime.enabled is False, "`mark_arm_pending` 只改 `_arm`，不动 `enabled`/`deps`"
    for _ in range(50):
        await asyncio.sleep(0.01)
        if runtime.enabled:
            break
    assert runtime.enabled is True, "后台重装必须在锁释放之后自己完成"
    assert runtime.arm_pending is False


# ---------------- G-15：序号语义 ----------------
@pytest.mark.asyncio
async def test_the_later_rearm_always_wins_even_when_it_lands_first(_no_loop):
    """G-15：乱序完成时最终生效的恒是 `seq` 大的那一份（用户连点两个开关的常态）。"""
    await _fresh()
    runtime = _shell()
    deps_a = intel_service.IntelDeps(settings=Settings(), sessionmaker=get_sessionmaker,
                                     group_id=GROUP, bar="1H")
    deps_b = intel_service.IntelDeps(settings=Settings(), sessionmaker=get_sessionmaker,
                                     group_id=GROUP, bar="4H")
    off = arming.arm_of_code("disabled")
    assert await runtime.rearm(deps_b, off, 2, ("fp-b",)) is True
    assert await runtime.rearm(deps_a, off, 1, ("fp-a",)) is False
    assert runtime.deps is deps_b
    assert runtime.arm_fingerprint == ("fp-b",)


# ---------------- G-16：一次失败的重装不许弄停一条在跑的流水线 ----------------
@pytest.mark.asyncio
async def test_a_failed_build_leaves_the_running_pipeline_untouched(monkeypatch, _no_loop):
    """G-16：`_build_deps` 抛异常 ⇒ `reason="arm_failed"` 且旧运行时一个字段都没被改。"""
    await _fresh(provider="tavily", apiKey="k-1")
    runtime = _shell()
    await _rearm(runtime)
    before_deps, before_arm = runtime.deps, runtime.arm

    def boom(**kwargs):
        raise RuntimeError("LLM provider 未配置")

    monkeypatch.setattr(intel_runtime, "_build_deps", boom)
    await _fresh(provider="brave", apiKey="k-2")
    result = await _rearm(runtime)
    assert result["reason"] == "arm_failed"
    assert result["detail"] == "LLM provider 未配置"
    assert result["rearmed"] is False
    assert runtime.deps is before_deps
    assert runtime.arm is before_arm
    assert runtime.enabled is True
    assert arming.arm_error_for(GROUP) == "LLM provider 未配置"


# ---------------- G-17：拆卸之后一切都是 no-op ----------------
@pytest.mark.asyncio
async def test_retire_makes_rearm_and_refresh_noop(_no_loop):
    """G-17：`retire()` 之后 `rearm` 回 False、`refresh` 不再 notify；失败原因一并清除。"""
    await _fresh(provider="tavily", apiKey="k-1")
    pushed: list = []
    runtime = _shell(notify=lambda inst, summary: pushed.append(summary))
    arming.record_arm_error(GROUP, RuntimeError("旧的那一次"))
    intel_runtime.unregister(GROUP)
    assert arming.arm_error_for(GROUP) == "", "残留会让下一个分组读到上一个分组的失败原因"
    pushed.clear()
    assert await runtime.rearm(runtime.deps, arming.arm_of_code("disabled"), 9, ()) is False
    assert await runtime.refresh(INST) is not None
    assert pushed == []


# ---------------- G-18：指纹短路，连锁都不碰 ----------------
class _CountingLock:
    """一把会数 `acquire` 次数的替身锁（`asyncio.Lock` 的最小可用面）。"""

    def __init__(self):
        self.inner = asyncio.Lock()
        self.acquires = 0

    async def __aenter__(self):
        self.acquires += 1
        return await self.inner.__aenter__()

    async def __aexit__(self, *exc):
        return await self.inner.__aexit__(*exc)

    def locked(self) -> bool:
        return self.inner.locked()


@pytest.mark.asyncio
async def test_an_unchanged_save_never_touches_the_lock(_no_loop):
    """G-18：两次完全相同的设置连续重装 ⇒ 第二次 `changed=False` 且**没有获取过锁**。"""
    await _fresh(provider="tavily", apiKey="k-1")
    runtime = _shell()
    await _rearm(runtime)
    counting = _CountingLock()
    runtime._lock = counting
    second = await _rearm(runtime)
    assert second["changed"] is False
    assert second["rearmed"] is False
    assert second["armed"] is True
    assert counting.acquires == 0, "一次真正的空操作必须连锁都不碰"


# ---------------- G-30：指纹与配置同锁同刻 ----------------
@pytest.mark.asyncio
async def test_fingerprint_is_only_stamped_by_whoever_applied(_no_loop):
    """G-30（评审 P1-9）：指纹是「此刻真的在跑的那份配置」的标签，**谁应用谁贴**。

    先写指纹再等应用的那种写法，会在两个 PUT 交错时造出「跑着 B、指纹说 A」——此后任何一次把
    设置改回 A 的保存都被 `changed=False` 短路，**A 永远装不上，且不报错**。
    """
    await _fresh(provider="tavily", apiKey="k-a")
    runtime = _shell()
    async with get_sessionmaker()() as session:
        derived_a = await intel_service.resolve_intel_settings(session, Settings())
        fp_a = arming.settings_fingerprint(derived_a, "k-a")
    await _fresh(provider="brave", apiKey="k-b")
    async with get_sessionmaker()() as session:
        derived_b = await intel_service.resolve_intel_settings(session, Settings())
        fp_b = arming.settings_fingerprint(derived_b, "k-b")
    assert fp_a != fp_b

    off = arming.arm_of_code("disabled")
    deps = intel_service.IntelDeps(settings=derived_b, sessionmaker=get_sessionmaker,
                                   group_id=GROUP)
    assert await runtime.rearm(deps, off, 2, fp_b) is True       # B 先应用
    assert await runtime.rearm(deps, off, 1, fp_a) is False      # A 后到，被 seq 拒
    assert runtime.arm_fingerprint == fp_b, "被拒的那一次绝不许留下自己的指纹"

    # 把设置改回 A：必须还能装上（v1 的写法会在这里回 changed=False）。
    await _fresh(provider="tavily", apiKey="k-a")
    result = await _rearm(runtime)
    assert result["changed"] is True
    assert result["armed"] is True
    assert runtime.deps.search is not None
    assert runtime.arm_fingerprint == fp_a


# ---------------- G-32：停机分支先等收尾段 ----------------
@pytest.mark.asyncio
async def test_shutdown_branch_waits_for_the_round_tail_then_hard_cancels(monkeypatch):
    """G-32（评审 P1-10）：`_after_round` 还在跑时关总闸 ⇒ **先等**它，超时才硬 cancel。

    收尾段承载着这一轮的快照刷新与 `_alert` 告警交接。把它丢在一次开关拨动上，症状是
    「今天为什么一次都没告警」这个问题**恰好在这一次没有答案**。
    """
    await _fresh()
    runtime = _shell()
    settled: list[str] = []

    async def slow_tail(inst_id, outcome):
        await asyncio.sleep(0.05)
        settled.append(inst_id)

    monkeypatch.setattr(runtime, "_after_round", slow_tail)

    async def one_round():
        return intel_service.RoundOutcome(INST, "collect", "ok")

    runtime.enabled = True
    task = asyncio.ensure_future(runtime._run(INST, "collect", one_round()))
    await asyncio.sleep(0.01)
    assert runtime.busy is True, "`_in_flight` 已空而收尾段仍在跑——只看它等于没等"
    assert await intel_runtime._await_round_settled(runtime, 1.0) is True
    assert settled == [INST]
    await task


@pytest.mark.asyncio
async def test_the_settle_wait_is_bounded(monkeypatch, caplog):
    """G-32 的有界性：一个恒不结束的收尾段 ⇒ 超过死线即硬 cancel 并打一行 warning，
    **整个调用不超过死线的两倍**。"""
    await _fresh()
    runtime = _shell()
    runtime._tail = 1                       # 一个永远收不了尾的收尾段
    runtime.deps = intel_service.IntelDeps(
        settings=Settings(ai_trade_intel_rearm_deadline_s=0.2),
        sessionmaker=get_sessionmaker, group_id=GROUP)
    started = time.monotonic()
    with caplog.at_level("WARNING", logger="app.ai_auto.intel.runtime"):
        await intel_runtime._sync_scheduler(runtime)
    elapsed = time.monotonic() - started
    assert 0.2 <= elapsed < 0.4, "等待必须有界：关掉总闸不该和一轮情报绑在一起"
    assert any("收尾段" in record.message for record in caplog.records)
