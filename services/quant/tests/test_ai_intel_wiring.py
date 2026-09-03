"""装配门禁（§7.1 · test_ai_intel_wiring）—— 全套用例里最重要的一个。

本仓库反复栽的坑是「**写点有、读点无**」：判定写对了、模块编译得过、单测全绿，但没有任何一处
真的读它。这个文件是唯一会因此判红的用例，故它的断言必须落在**真实发出去的东西**上：

- 采集 Agent **真正发出的** `tools` 恰为 1 个且名为 `submit_market_intel`（I-2）；
- 决策链**真正发出的** `system` 与 `messages` 里，有情报时确实出现「外部市场情报」段，
  且 `system` **一个字都没变**（断言必须同时覆盖 system 与 messages——deep-reasoning-chain
  终审的成文教训）；
- `manager` 的五处薄转发真的接上了调度与注册表；
- `main._shutdown_steps()` 里 `ai_trade_intel` 排在 `ai_trade` 之后、`db_engine` 之前。
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.agent.llm.base import StreamEvent
from app.ai_auto.decider import AiTradeDecider
from app.ai_auto.intel import prompts as intel_prompts
from app.ai_auto.intel import runtime as intel_runtime
from app.ai_auto.intel import scheduler as intel_scheduler
from app.ai_auto.intel import service as intel_service
from app.ai_auto.intel.agent import run_round
from app.ai_auto.intel.disclosure import build_digest, format_intel
from app.ai_auto.intel.gate import IntelItem
from app.ai_auto.intel.taxonomy import IntelLimits
from app.config import Settings
from app.db.session import get_sessionmaker

NOW = 1_800_000_000
INST = "BTC-USDT-SWAP"


class CapturingLLM:
    """把**真正发出去的** system / messages / tools 原样留下来。"""

    provider = "anthropic"

    def __init__(self, tool_name: str, payload: dict):
        self.tool_name = tool_name
        self.payload = payload
        self.seen: dict = {}

    async def stream(self, *, system, messages, tools):
        self.seen = {"system": system, "messages": messages, "tools": tools}
        yield StreamEvent(kind="tool_use_start", id="t", name=self.tool_name)
        yield StreamEvent(kind="tool_use_args_delta", id="t",
                          partial_json=json.dumps(self.payload, ensure_ascii=False))
        yield StreamEvent(kind="message_done", stop_reason="tool_use")


@pytest.fixture(autouse=True)
def _clean_registry():
    yield
    for task in list(intel_scheduler.tasks()):
        task.cancel()
    intel_scheduler._TASKS.clear()
    intel_scheduler._LOOPS.clear()
    intel_runtime._RUNTIMES.clear()
    intel_runtime._BY_INST.clear()


# ---------------- 情报 Agent 的工具面（I-2） ----------------
@pytest.mark.asyncio
async def test_collect_agent_sends_exactly_one_tool():
    """§3.9 第 ② 层防护：采集 Agent 结构上无法交易。"""
    llm = CapturingLLM(intel_prompts.COLLECT_TOOL_NAME, {"items": []})
    limits = IntelLimits()
    await run_round(llm, system="sys", user="usr",
                    tool_name=intel_prompts.COLLECT_TOOL_NAME,
                    tool_description=intel_prompts.COLLECT_TOOL_DESCRIPTION,
                    tool_schema=intel_prompts.build_collect_tool_schema(limits),
                    timeout_s=5.0)
    assert [t.name for t in llm.seen["tools"]] == [intel_prompts.COLLECT_TOOL_NAME]


@pytest.mark.asyncio
async def test_curate_agent_sends_exactly_one_tool():
    llm = CapturingLLM(intel_prompts.CURATE_TOOL_NAME, {"actions": []})
    limits = IntelLimits()
    await run_round(llm, system="sys", user="usr",
                    tool_name=intel_prompts.CURATE_TOOL_NAME,
                    tool_description=intel_prompts.CURATE_TOOL_DESCRIPTION,
                    tool_schema=intel_prompts.build_curate_tool_schema(limits),
                    timeout_s=5.0)
    assert [t.name for t in llm.seen["tools"]] == [intel_prompts.CURATE_TOOL_NAME]


# ---------------- 决策链真的看得到情报 ----------------
def _payload(**over) -> dict:
    base = {"round": 1, "barTs": NOW, "lastPx": 100.0, "position": None,
            "sessionPnlUsd": 0.0, "budgetUsd": 100.0, "takeProfitUsd": None,
            "stopLossUsd": None, "signalBoard": [], "summary": ""}
    base.update(over)
    return base


def _digest() -> dict:
    item = IntelItem(
        item_id=1, inst_id=INST, tier="ultra", category="risk",
        headline="某跨链桥今晨被盗约 3400 万美元",
        summary="与本标的无直接资金关系，但同板块情绪承压。", stance="bearish", impact="high",
        confidence=0.65, event_ts=NOW - 3600, captured_ts=NOW - 3600,
        expires_ts=NOW + 86400, sources=(), domain_count=2, dedup_key="k")
    return build_digest([item], limits=IntelLimits(), now=NOW, last_collect_ts=NOW - 600)


@pytest.mark.asyncio
async def test_decision_call_carries_the_intel_block_in_messages_not_in_system():
    """**同时覆盖 system 与 messages**：只断言其一会漏掉「装到了错误的那一半」。"""
    from app.ai_auto.prompts import DECISION_TOOL_NAME

    llm = CapturingLLM(DECISION_TOOL_NAME,
                       {"action": "hold", "reason": "观望", "confidence": 0.5})
    decider = AiTradeDecider(llm, "SYSTEM-PROMPT", context_refresh_rounds=10,
                             settings=Settings())
    await decider.decide(_payload(intel=_digest()))
    block = format_intel(_digest())
    assert block, "前置条件：本用例的 digest 必须非空，否则下面两条断言无意义"
    user_text = "\n".join(
        b.text for msg in llm.seen["messages"] for b in msg.content
        if getattr(b, "text", None))
    assert "外部市场情报" in user_text, "情报必须真的进到发出去的 messages 里"
    assert block in user_text
    assert "外部市场情报" not in llm.seen["system"], (
        "I-1：系统 prompt 里不得新增任何要求模型服从情报的条款")


@pytest.mark.asyncio
async def test_decision_call_is_byte_identical_without_intel():
    """G-6 的装配侧对照：键缺席时发出去的 user 消息与本特性合入前逐字节相同。"""
    from app.ai_auto.prompts import DECISION_TOOL_NAME, build_decision_prompt

    llm = CapturingLLM(DECISION_TOOL_NAME,
                       {"action": "hold", "reason": "观望", "confidence": 0.5})
    decider = AiTradeDecider(llm, "SYSTEM-PROMPT", context_refresh_rounds=10,
                             settings=Settings())
    payload = _payload()
    await decider.decide(payload)
    sent = "\n".join(b.text for msg in llm.seen["messages"] for b in msg.content
                     if getattr(b, "text", None))
    assert sent == build_decision_prompt(payload)


# ---------------- manager 的薄转发 ----------------
def _fake_group(group_id="grp-1"):
    session = SimpleNamespace(change_24h_pct=lambda: -1.5,
                              push_intel=_noop_push)
    member = SimpleNamespace(inst_id=INST, session=session)
    return SimpleNamespace(group_id=group_id, bar="15m", members=[member])


async def _noop_push(summary):
    return None


@pytest.mark.asyncio
async def test_manager_wires_and_unwires_the_intel_runtime(monkeypatch):
    from app.ai_auto.manager import AiTradeManager

    manager = AiTradeManager()
    group = _fake_group()
    manager._group = group
    monkeypatch.setattr(manager, "_group_settings", lambda g: Settings())
    monkeypatch.setattr("app.ai_auto.manager.resolve_active_provider",
                        _async_return(object()))

    async def fake_build(**kwargs):
        assert kwargs["group_id"] == "grp-1"
        assert kwargs["members"] == [INST]
        # E-5：装配点必须把**本模块里的那一份** `make_llm_client_from_config` 传下去。
        from app.ai_auto.manager import make_llm_client_from_config

        assert kwargs["make_client"] is make_llm_client_from_config
        deps = intel_service.IntelDeps(settings=Settings(), sessionmaker=get_sessionmaker,
                                       search=object(), llm=object(), group_id="grp-1")
        return intel_runtime.IntelRuntime("grp-1", [INST], deps, enabled=True,
                                          notify=kwargs.get("notify"))

    monkeypatch.setattr(intel_runtime, "build_group_runtime", fake_build)
    monkeypatch.setattr(intel_scheduler, "_loop", _forever)

    await manager._wire_intel(group)
    assert intel_runtime.runtime_for(INST) is not None
    assert list(intel_scheduler.loops()) == ["grp-1"], "装配后必须真的有一个调度循环在跑"

    manager._unwire_intel("grp-1")
    assert intel_scheduler.loops() == {}
    assert intel_runtime.runtime_for(INST) is None


def test_build_group_runtime_keeps_its_public_signature():
    """G-19：`build_group_runtime` 的公开签名逐字段不变（11 个 keyword-only 形参）。

    arm-state 把 `IntelDeps` 的构造收敛进了 `_build_deps`——那是**内部**重构。签名一旦漂移，
    上面那条 `fake_build(**kwargs)` 替身会静默接受任何入参，于是 manager 与装配点之间的接线
    错了也不会有人知道。

    ★ ai-trade-intel-session-binding 轴 A：第 11 个形参 `session_rule` 是**刻意**加进公开签名
    的——会话选择**必须**作为一个独立的、显式的 keyword 形参一路传到 `_build_deps`，在
    `resolve_intel_settings` **之后**才与运维总闸合取。把它编码成
    `Settings.ai_trade_intel_inject` 会被 `INTEL_SETTING_BRIDGE` 里那一行库内行静默覆盖，
    而且不报错（spec §3.1 A-2 的陷阱）。默认 `None` ⇒ 既有调用点逐字节回退。
    """
    import inspect

    params = inspect.signature(intel_runtime.build_group_runtime).parameters
    assert list(params) == ["group_id", "members", "settings", "sessionmaker", "provider_cfg",
                            "make_client", "bar", "change_24h_pct", "notify", "notify_alert",
                            "session_rule"]
    assert all(p.kind is inspect.Parameter.KEYWORD_ONLY for p in params.values())
    assert params["bar"].default == "15m"
    assert params["change_24h_pct"].default is None
    assert params["session_rule"].default is None


@pytest.mark.asyncio
async def test_wire_intel_passes_the_sessionmaker_factory_not_a_session(monkeypatch):
    """实施发现 I-4：`sessionmaker` 必须是**取 sessionmaker 的那个函数**，不是它的返回值。

    全部 `deps.sessionmaker()()` 调用点都按两跳写（`IntelDeps.sessionmaker: Callable[[], object]`）。
    传进去一个 `async_sessionmaker` 实例，第二跳就是 `AsyncSession()` ⇒ `TypeError`，而
    `_wire_intel` 全程 fail-open ⇒ 异常被吞、`register` 从没被调到、`_RUNTIMES` 恒空 ⇒ 面板恒说
    「尚未配置情报检索源」。这正是本轮用户报告的那句假话的根因，且它对上层**完全静默**。
    """
    from app.ai_auto.manager import AiTradeManager

    manager = AiTradeManager()
    group = _fake_group()
    manager._group = group
    monkeypatch.setattr(manager, "_group_settings", lambda g: Settings())
    monkeypatch.setattr("app.ai_auto.manager.resolve_active_provider",
                        _async_return(object()))
    seen: dict = {}

    async def capture(**kwargs):
        seen.update(kwargs)
        deps = intel_service.IntelDeps(settings=Settings(), sessionmaker=kwargs["sessionmaker"],
                                       group_id="grp-1")
        return intel_runtime.IntelRuntime("grp-1", [INST], deps, enabled=False)

    monkeypatch.setattr(intel_runtime, "build_group_runtime", capture)
    await manager._wire_intel(group)
    assert seen["sessionmaker"] is get_sessionmaker
    # 两跳必须真的走得通——只断言身份还不够，那正是这个缺陷活了四轮的原因。
    async with seen["sessionmaker"]()() as session:
        assert session is not None


@pytest.mark.asyncio
async def test_manager_wiring_is_fail_open(monkeypatch):
    """情报是加法流水线：它的任何失败都不该让一次真实资金的启动失败。"""
    from app.ai_auto.manager import AiTradeManager

    manager = AiTradeManager()
    group = _fake_group()
    manager._group = group
    monkeypatch.setattr(manager, "_group_settings", lambda g: Settings())

    async def boom(*args, **kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr("app.ai_auto.manager.resolve_active_provider", boom)
    await manager._wire_intel(group)          # 不该抛
    assert intel_runtime.runtime_for(INST) is None


@pytest.mark.asyncio
async def test_manager_drain_routes_to_the_scheduler(monkeypatch):
    from app.ai_auto.manager import AiTradeManager

    seen = {}

    async def fake_drain(timeout):
        seen["timeout"] = timeout

    monkeypatch.setattr(intel_scheduler, "drain", fake_drain)
    manager = AiTradeManager()
    await manager.drain_intel_tasks()
    assert seen["timeout"] == Settings().ai_trade_intel_drain_timeout_s


def test_shutdown_step_order_is_ai_trade_then_intel_then_db_engine():
    """位置是硬性的：先停会话（它在返回前可能 spawn 最后一轮），再收后台任务，最后弃引擎。"""
    from app.main import _shutdown_steps

    names = [name for name, _ in _shutdown_steps()]
    assert "ai_trade_intel" in names, "漏这一步 ⇒ 情报后台任务会活着穿过 dispose_engine"
    assert names.index("ai_trade") < names.index("ai_trade_intel") < names.index("db_engine")
    assert names.index("ai_trade_experience") < names.index("ai_trade_intel")


def test_router_is_mounted_under_api():
    from app.main import app

    paths = {route.path for route in app.routes}
    assert "/api/intel/{instId}" in paths
    assert "/api/settings/intel" in paths


def test_session_snapshot_declares_the_intel_key():
    """E-9 的双装配教训：只挂 WS 事件的话，刷新页面后徽标与降级提示会凭空消失。"""
    import inspect

    from app.ai_auto.session import AiTradeSession

    source = inspect.getsource(AiTradeSession.snapshot)
    assert '"intel": intel_summary_for(self.inst_id)' in source


# ---------------- closed-loop：六处薄接线的读点都必须真的存在 ----------------
def _session_source(name: str) -> str:
    import inspect

    from app.ai_auto.session import AiTradeSession

    return inspect.getsource(getattr(AiTradeSession, name))


def test_session_wires_every_attribution_hook():
    """轴 B 的六个钩子在 `session` 里**确有调用点**（源码扫描 + 下方行为断言双保险）。

    这是本仓库「写点有、读点无」那类缺陷唯一会判红的地方：`IntelAttribution` 自己全绿，
    而 `session` 忘了调 `note_open`，症状是 `cite_entry_count` 恒 0 且不报任何错。
    """
    dispatch = _session_source("_dispatch")
    assert "self._intel_cite.note_open(trade, seq=self._position_seq)" in dispatch
    assert "self._intel_cite.note_close(" in dispatch
    assert "await self._intel_cite.persist_pending()" in dispatch

    round_src = _session_source("_run_decision_round")
    assert "self._intel_cite.note_decision(" in round_src
    # E-4：引用必须取自任何门禁**之前**的那一个决策。
    assert "stage1_decision" in round_src.split("note_decision(", 1)[1][:200]
    assert "self._intel_cite.note_executed(" in round_src
    assert "self._intel_cite.end_round()" in round_src


def test_close_hook_runs_after_inject_explain_meta():
    """E-5 的顺序约束：`exitSource` 与往返结算都在 `_inject_explain_meta` 那一步才写进 meta。"""
    dispatch = _session_source("_dispatch")
    assert dispatch.index("self._inject_explain_meta(trade)") < dispatch.index(
        "self._intel_cite.note_close(")


def test_attribution_persist_is_not_awaited_before_the_decision_event():
    """R-5④：落库排在决策事件推送**之后**——决策卡是用户最在意的那个延迟。"""
    round_src = _session_source("_run_decision_round")
    assert round_src.index("await self._emit_decision(") < round_src.index(
        "await self._intel_cite.persist_pending()")


def test_ai_stats_merges_intel_at_the_call_site():
    """P2-9：合并落在**调用点** `_build_run_metrics`，`_compute_ai_stats` 一字不动。"""
    import inspect

    from app.ai_auto import session as session_mod

    assert 'metrics["aiStats"]["intel"] = self._intel_cite.stats()' in _session_source(
        "_build_run_metrics")
    assert "intel" not in inspect.getsource(session_mod._compute_ai_stats)


def test_scheduler_runs_verify_before_curate():
    """§3.3.6：核查轮排在整理轮**之前**——整理轮的升降级复核要读 verdict。"""
    import inspect

    tick = inspect.getsource(intel_scheduler._tick)
    assert "runtime.run_verify(inst_id)" in tick
    assert tick.index("run_verify(inst_id)") < tick.index("run_curate(inst_id)")
    assert "service.verify_due(" in tick


def test_shutdown_steps_are_unchanged_by_the_closed_loop():
    """评审 P2-12：本轮**不新增任何关停步骤**——尚未落盘的引用行按 `dropped` 计并放弃。"""
    from app.main import _shutdown_steps

    names = [name for name, _ in _shutdown_steps()]
    assert names.count("ai_trade_intel") == 1
    assert names.index("ai_trade_intel") == 6, "第 7 步的位置被本文件冻结"


def test_intel_package_exports_the_new_surface():
    """包导出面是本包对外的全部契约（漏导出的症状是运行期 AttributeError 被裸 except 吞掉）。"""
    from app.ai_auto import intel as intel_pkg

    for name in ("IntelAttribution", "run_verify_round", "resolve_citations",
                 "classify_outcome"):
        assert name in intel_pkg.__all__ and hasattr(intel_pkg, name), name


def _async_return(value):
    async def _inner(*args, **kwargs):
        return value

    return _inner


async def _forever(_runtime):
    await asyncio.sleep(3600)


def test_no_degrade_code_originates_from_intel_layer():
    """C-6 / 承诺二：轴 A/B/C/D 的任何异常路径都**不产出**新的 `TradeDecision` 降级码。

    情报是加法流水线：它的任何失败都必须收敛成「情报段少几条」「统计列没更新」「verdict 停留在
    unresolved」，而不是收敛成一笔没做成或做错方向的真实交易。这条用例的判据是**源码扫描**——
    它要挡的正是「下一次有人在归因层里顺手 `return _hold(...)`」这种改动。
    """
    import inspect

    from app.ai_auto.decider import AiTradeDecider
    from app.ai_auto.intel import attribution as attr_mod
    from app.ai_auto.intel import citation as citation_mod
    from app.ai_auto.intel import verify as verify_mod

    for module in (citation_mod, attr_mod, verify_mod):
        source = inspect.getsource(module)
        # 判据落在**可执行的引用**上而不是散落的文字：这三个模块既不 import 决策器，也不构造
        # 或改写 `TradeDecision`，更不会走 `_hold(...)` 那条降级出口。
        assert "from ..decider" not in source, f"{module.__name__} import 了决策器"
        assert "TradeDecision(" not in source, f"{module.__name__} 构造了决策对象"
        assert "_hold(" not in source, f"{module.__name__} 产出了一个降级"

    resolver = inspect.getsource(AiTradeDecider._resolve_intel_refs)
    assert "return ()" in resolver, "总闸关 / 类型错一律回空元组"
    assert "_hold(" not in resolver, \
        "解析器**绝不降级**：一个写错的编号是排版问题，不是安全问题"


def test_intel_refs_never_reach_any_gate():
    """承诺二的另一半：`intel_refs` 不参与任何仓位 / 风控判定。

    八道确定性门禁与仓位求解全在 `ai_auto/` 的这几个模块里；它们**一个字都不该提到**这个字段。
    """
    import inspect

    from app.ai_auto import gate_veto, hold_horizon, signals, sizing

    for module in (signals, sizing, gate_veto, hold_horizon):
        assert "intel_refs" not in inspect.getsource(module), \
            f"{module.__name__} 读了 intel_refs ⇒ 归因变成了门禁"


@pytest.mark.asyncio
async def test_verify_client_assembly_failure_falls_back_instead_of_killing_the_group():
    """核查轮的第二个 client 造不出来 ⇒ `verify_llm=None` 回落，而不是整组情报层不装配。

    §十一 对 `ai_trade_intel_verify_max_tokens` 的定稿写着「装配失败 ⇒ 回落 `deps.llm`，
    **不报错**」。核查是加法，采集是本体：为了一个更大的输出预算把整个情报层拖下水，正好把
    这条特性的成本收益反过来。回落之后 `_run_verify_agent` 的 `deps.verify_llm or deps.llm`
    照常拿到采集轮那一个 client（该分支另有用例）。
    """
    from dataclasses import dataclass

    from app.db.session import init_db

    @dataclass
    class _Cfg:
        max_tokens: int = 2048
        thinking_budget_tokens: int = 0

    settings = Settings(ai_trade_intel_enabled=True,
                        ai_trade_intel_search_provider="tavily",
                        ai_trade_intel_search_api_key="k",
                        ai_trade_intel_verify_max_tokens=8192)

    def make_client(cfg):
        # 只有核查轮那一个预算会炸——采集轮那一个必须照常造出来。
        if int(getattr(cfg, "max_tokens", 0)) == 8192:
            raise RuntimeError("verify client boom")
        return SimpleNamespace(provider="anthropic")

    await init_db()
    runtime = await intel_runtime.build_group_runtime(
        group_id="g-verify-fallback", members=[INST], settings=settings,
        sessionmaker=get_sessionmaker, provider_cfg=_Cfg(), make_client=make_client)

    assert runtime.enabled, "核查预算装配失败绝不允许把整组情报层降成一个壳"
    assert runtime.deps.verify_llm is None
    assert runtime.deps.llm is not None


def test_every_intel_setting_is_registered_in_env_example():
    """`.env.example` 与 `Settings` 的 `ai_trade_intel_*` 家族**双向逐键对齐**。

    本仓库为这件事栽过两次，而两次的症状都一样：**漏登记是静默的**。prod 的 PyInstaller 后端
    不读 `.env`、只读进程 env，而桌面壳按 `AI_TRADE_` 前缀从 `agent.env` 透传 —— 一个没写进
    `.env.example` 的旋钮，用户在打包形态下**没有任何途径**知道它存在，更不会去调它；反过来，
    一个写在 `.env.example` 却已被删掉的键，是一句会被照抄进真实配置的废话。
    `.env.example` 此前**没有任何扫描用例**（那正是它两次漏登记的原因），本用例把这个家族补上。

    判红之后的唯一正确动作是**把缺的那一行补进 `.env.example`**（或删掉多余的那一行），
    不是放宽这里的正则。
    """
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[3]
    cfg = (root / "services" / "quant" / "app" / "config.py").read_text(encoding="utf-8")
    env = (root / ".env.example").read_text(encoding="utf-8")

    fields = {name.upper() for name in
              re.findall(r"^    (ai_trade_intel_[a-z0-9_]+)\s*:", cfg, re.M)}
    declared = set(re.findall(r"^(AI_TRADE_INTEL_[A-Z0-9_]+)=", env, re.M))
    assert fields, "正则没匹配到任何字段 ⇒ 本用例已经失效，先修它"
    assert fields - declared == set(), f"这些设置没写进 .env.example：{sorted(fields - declared)}"
    assert declared - fields == set(), f".env.example 里有已不存在的键：{sorted(declared - fields)}"


def test_reader_config_is_derived_from_the_effective_settings():
    """evidence-grounding：抓取配置的**唯一**读点，且 `proxy` 复用进程代理（零新增配置项）。"""
    from app.ai_auto.intel import evidence as intel_evidence
    from app.config import Settings

    settings = Settings(ai_trade_intel_reader_top_k=9, ai_trade_intel_reader_enabled=False)
    cfg = intel_evidence.reader_config(settings)
    assert cfg.top_k == 9 and cfg.enabled is False
    assert cfg.proxy == settings.proxy_url


def test_negative_cache_window_is_derived_not_configured():
    """负缓存窗是**派生量** `min(6h, 正窗/12)`：少一个键就少一行必须双向对齐的门禁。"""
    from app.ai_auto.intel import evidence as intel_evidence
    from app.config import Settings

    ttl, negative = intel_evidence._cache_windows(Settings())
    assert ttl == 72 * 3600.0 and negative == 6 * 3600.0
    ttl2, negative2 = intel_evidence._cache_windows(
        Settings(ai_trade_intel_reader_cache_ttl_h=24.0))
    assert ttl2 == 24 * 3600.0 and negative2 == 2 * 3600.0


# ---------- G-16（ai-trade-intel-actionability）：六把新键逐键往返 ----------
@pytest.mark.asyncio
async def test_every_actionability_key_round_trips_through_the_bridge():
    """判据是 `resolve_intel_settings` 的**产物**，不是「请求没报错」。

    不扩 `INTEL_SETTING_BRIDGE` 的症状是：请求 200、面板上开关照动、`Settings` 上的值**永远
    不变**，而且不报错——把安全开关做成假开关是最不可接受的一种失败（本仓库已两次前科）。
    """
    from app.ai_auto.intel import service as intel_service
    from app.config import Settings
    from app.db import repositories as repo
    from app.db.session import get_sessionmaker, init_db

    await init_db()
    cases = {
        "ai_intel.alert_enabled": ("ai_trade_intel_alert_enabled", False),
        "ai_intel.alert_min_impact": ("ai_trade_intel_alert_min_impact", "medium"),
        "ai_intel.alert_cooldown_min": ("ai_trade_intel_alert_cooldown_min", 45.0),
        "ai_intel.alert_daily_cap": ("ai_trade_intel_alert_daily_cap", 12),
        "ai_intel.efficacy_enabled": ("ai_trade_intel_efficacy_enabled", False),
        "ai_intel.conflict_enabled": ("ai_trade_intel_conflict_enabled", False),
    }
    async with get_sessionmaker()() as session:
        for app_key, (_field, value) in cases.items():
            await repo.set_app_setting(session, app_key, value)
        derived = await intel_service.resolve_intel_settings(session, Settings())
    for _app_key, (field, value) in cases.items():
        assert getattr(derived, field) == value, field


@pytest.mark.asyncio
async def test_an_illegal_min_impact_is_dropped_rather_than_passed_through():
    """`model_copy(update=…)` **不跑校验器**：一个从库内行读回来的坏值会一路穿到 `IntelLimits`，
    让「影响力门槛」变成一个恒不成立的判据。桥的循环里必须再收一次值域。"""
    from app.ai_auto.intel import service as intel_service
    from app.config import Settings
    from app.db import repositories as repo
    from app.db.session import get_sessionmaker, init_db

    await init_db()
    async with get_sessionmaker()() as session:
        await repo.set_app_setting(session, "ai_intel.alert_min_impact", "low")
        derived = await intel_service.resolve_intel_settings(session, Settings())
    assert derived.ai_trade_intel_alert_min_impact == "high", "坏值被丢弃，回落默认"


@pytest.mark.asyncio
async def test_the_settings_wire_carries_the_pending_ttl_so_the_ui_need_not_hardcode_it():
    """动态页签用它把「还没回」与「再也不会回」分开。

    不下发它，前端只能写死一个数——而它是一个 30–900 的配置项：把它调到 900 的用户会在第
    180 秒看到一句「会话未在时限内响应」，而后端那一侧还在正常等着。它是**只读**的
    （不在 `IntelSettingsIn` 里），故这里断言的是「回执上在位且等于生效值」。
    """
    from app.api.app_settings import _intel_wire
    from app.config import Settings
    from app.db.session import get_sessionmaker, init_db

    await init_db()
    settings = Settings(ai_trade_intel_alert_pending_ttl_s=600.0)
    async with get_sessionmaker()() as session:
        wire = await _intel_wire(session, settings)
    assert wire["alertPendingTtlS"] == 600.0
