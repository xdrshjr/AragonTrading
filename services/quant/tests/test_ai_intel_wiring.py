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


def _async_return(value):
    async def _inner(*args, **kwargs):
        return value

    return _inner


async def _forever(_runtime):
    await asyncio.sleep(3600)
