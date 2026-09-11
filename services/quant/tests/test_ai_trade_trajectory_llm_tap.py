"""后训练轨迹采集 · 采集点一 `RecordingLlmClient`（ai-trade-trajectory-capture §6.1 T-1…T-11）。

包装器跑在真实资金决策轮的热路径上，故本文件守的第一件事是**透明**：消费方看到的事件对象身份、
顺序、异常与决策结果和不包装时逐个相同；第二件事才是**完整**：思考 / 研判 / 工具入参逐字节留存，
重试、超时、提前关流、error 事件各自落成正确的状态。用例全部走真实 recorder 写线程并读回段文件。
"""
from __future__ import annotations

import ast
import asyncio
import copy
import gzip
import json
import pickle
import shutil
import tempfile
import uuid
from pathlib import Path

import pytest

from app.agent.llm.anthropic_client import AnthropicClient
from app.agent.llm.base import LlmMessage, StreamEvent, TextBlock, ToolSchema
from app.agent.llm.openai_client import OpenAIClient
from app.ai_auto.decider import AiTradeDecider, ReasoningMeta
from app.ai_auto.experience.distill import DISTILL_TOOL_NAME
from app.ai_auto.llm_budget import AiTradeLlmClients
from app.ai_auto.prompts import DECISION_TOOL_NAME, REFLECTION_TOOL_NAME
from app.ai_auto.trajectory import (EpisodeFacts, GroupRecorderRequest, RecordingLlmClient,
                                    SessionBinding, TrajectoryRule, open_group_recorder,
                                    wrap_llm_clients)
from app.ai_auto.trajectory import wire as wire_mod
from app.ai_auto.trajectory.llm_tap import classify_site, llm_params_of
from app.ai_auto.trajectory.records import adopted_decision_call_id
from app.config import Settings

_INST = "BTC-USDT-SWAP"
_LLM_TAP = Path(__file__).resolve().parents[1] / "app" / "ai_auto" / "trajectory" / "llm_tap.py"
_API_KEY = "sk-ant-secret-0123456789"


# ---------------- 夹具 ----------------
class _FakeExecutor:
    def __init__(self) -> None:
        self.trade_count = 0
        self.trades: list[dict] = []


class _ListLLM:
    """逐个产出**同一批**事件对象；可在某个下标之后抛异常，或在某个下标之前挂起。"""

    def __init__(self, events, *, provider="anthropic", raise_after=None, hang_before=None):
        self.events = events
        self.provider = provider
        self.raise_after = raise_after
        self.hang_before = hang_before

    async def stream(self, *, system, messages, tools):
        for index, event in enumerate(self.events):
            if self.hang_before is not None and index == self.hang_before:
                await asyncio.sleep(3600)
            yield event
            if self.raise_after is not None and index == self.raise_after:
                raise RuntimeError("inner boom")


class _ScriptedLLM:
    """与 `test_ai_trade_decider.py` 同款：按脚本逐次响应，脚本耗尽后回一个空的 message_done。"""

    def __init__(self, scripts):
        self._scripts = list(scripts)

    async def stream(self, *, system, messages, tools):
        script = self._scripts.pop(0) if self._scripts else [
            StreamEvent("message_done", stop_reason="end")]
        for event in script:
            yield event


class _OfflineAnthropic(AnthropicClient):
    """真实 `_body` 装配 + 脱网的流（参数真源用例只关心请求体，不打网络）。"""

    async def stream(self, *, system, messages, tools):
        for event in _events_normal():
            yield event


class _OfflineOpenAI(OpenAIClient):
    async def stream(self, *, system, messages, tools):
        for event in _events_normal():
            yield event


class _PicklableInner:
    provider = "anthropic"
    _model = "m"


class _PicklableChannel:
    pass


def _events_normal() -> list[StreamEvent]:
    return [StreamEvent("thinking_delta", text="想一想"), StreamEvent("text_delta", text="研判"),
            StreamEvent("tool_use_start", id="t1", name=DECISION_TOOL_NAME),
            StreamEvent("tool_use_args_delta", id="t1", partial_json='{"action":"hold"}'),
            StreamEvent("tool_use_end", id="t1"),
            StreamEvent("message_done", stop_reason="tool_use", usage={"input_tokens": 3})]


def _tools(name: str = DECISION_TOOL_NAME) -> list[ToolSchema]:
    return [ToolSchema(name, "d", {"type": "object"})]


def _messages() -> list[LlmMessage]:
    return [LlmMessage("user", [TextBlock("行情摘要")])]


def _payload(round_no: int) -> dict:
    return {"round": round_no, "barTs": 1_700_000_000 + round_no, "lastPx": 30000.0,
            "position": None, "sessionPnlUsd": 0.0, "budgetUsd": 100.0,
            "takeProfitUsd": None, "stopLossUsd": None,
            "signalBoard": [{"strategyId": 1, "name": "S", "stance": "flat",
                             "latestSignal": "open_long", "strength": 1.0, "reason": "r"}]}


async def _open_channel():
    """真实 recorder + 已进入第 1 轮的通道。根目录取短路径（Windows 根目录上限 100 字符）。"""
    root = tempfile.mkdtemp(prefix="trj")
    result = await open_group_recorder(GroupRecorderRequest(
        rule=TrajectoryRule(True, root), settings=Settings(ai_trade_trajectory_min_free_bytes=0),
        group_id=f"aitradegrp_{uuid.uuid4().hex}", created_ts=1_788_000_000, bar="15m"))
    assert result.recorder is not None, result.error
    recorder = result.recorder
    channel = recorder.channel(_INST)
    channel.bind(SessionBinding("s1", 7, _INST, "15m"), executor=_FakeExecutor())
    channel.open_episode(EpisodeFacts("s1", 7, _INST, "15m", [1], ["S"], 10, 0.0, None, None,
                                      1_788_000_000, None))
    channel.begin_round(1, 1_788_000_001, {"trigger": "candle"})
    return recorder, channel, root


async def _close_and_read(recorder, root: str) -> list[dict]:
    try:
        await recorder.aclose("test", compress_active=False, timeout_s=5)
        assert not recorder.is_alive()
        rows: list[dict] = []
        for segment in sorted((recorder.group_dir / "members").rglob("seg-*")):
            opener = gzip.open if segment.suffix == ".gz" else open
            with opener(segment, "rt", encoding="utf-8") as handle:
                rows.extend(json.loads(line) for line in handle if line.strip())
        return rows
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _calls(rows: list[dict]) -> list[dict]:
    return [row for row in rows if row["kind"] == "llm_call"]


async def _consume(client, tools, messages=None):
    seen, error = [], None
    try:
        async for event in client.stream(system="S", messages=messages or _messages(),
                                         tools=tools):
            seen.append(event)
    except Exception as exc:  # noqa: BLE001 —— 用例要比较的正是异常本身
        error = exc
    return seen, error


def _scenario(name: str) -> tuple[list[StreamEvent], dict]:
    if name == "error_event":
        return [StreamEvent("text_delta", text="x"),
                StreamEvent("error", code="LLM_ERROR", message="boom"),
                StreamEvent("text_delta", text="after")], {}
    if name == "inner_raises":
        return _events_normal(), {"raise_after": 2}
    if name == "usage_after_done":
        return [*_events_normal(), StreamEvent("message_done", stop_reason=None,
                                               usage={"output_tokens": 9})], {}
    return _events_normal(), {}


# ---------------- T-1 透明性 ----------------
@pytest.mark.parametrize("scenario", ["normal", "error_event", "inner_raises", "usage_after_done"])
async def test_wrapper_forwards_the_same_event_objects_in_the_same_order(scenario):
    events, kwargs = _scenario(scenario)
    recorder, channel, root = await _open_channel()
    try:
        bare_seen, bare_error = await _consume(_ListLLM(events, **kwargs), _tools())
        seen, error = await _consume(RecordingLlmClient(_ListLLM(events, **kwargs), channel),
                                     _tools())
    finally:
        rows = await _close_and_read(recorder, root)
    assert len(seen) == len(bare_seen)
    assert all(left is right for left, right in zip(seen, bare_seen))
    assert type(error) is type(bare_error)
    assert str(error) == str(bare_error)
    assert len(_calls(rows)) == 1


def _decision_script() -> list[StreamEvent]:
    args = {"action": "open_long", "sizeFraction": 0.4, "confidence": 0.8, "reason": "trend"}
    return [StreamEvent("text_delta", text="研判：趋势向上。" * 40),
            StreamEvent("tool_use_start", id="t1", name=DECISION_TOOL_NAME),
            StreamEvent("tool_use_args_delta", id="t1", partial_json=json.dumps(args)),
            StreamEvent("message_done", stop_reason="tool_use")]


async def test_decider_returns_the_same_decision_and_analysis_with_the_wrapper():
    settings = Settings(ai_trade_decision_max_retries=0)
    bare = AiTradeDecider(_ScriptedLLM([_decision_script()]), "sys", context_refresh_rounds=10,
                          settings=settings)
    recorder, channel, root = await _open_channel()
    try:
        wrapped = AiTradeDecider(RecordingLlmClient(_ScriptedLLM([_decision_script()]), channel),
                                 "sys", context_refresh_rounds=10, settings=settings)
        expected = await bare.decide(_payload(1))
        actual = await wrapped.decide(_payload(1))
    finally:
        rows = await _close_and_read(recorder, root)
    assert actual == expected
    assert wrapped.last_analysis == bare.last_analysis
    assert [call["site"] for call in _calls(rows)] == ["decision"]


# ---------------- T-2 AST 门禁 ----------------
def test_stream_has_one_async_for_no_await_one_yield_and_every_handler_reraises():
    tree = ast.parse(_LLM_TAP.read_text(encoding="utf-8"))
    cls = next(node for node in tree.body
               if isinstance(node, ast.ClassDef) and node.name == "RecordingLlmClient")
    stream = next(node for node in cls.body
                  if isinstance(node, ast.AsyncFunctionDef) and node.name == "stream")
    nodes = list(ast.walk(stream))
    assert sum(isinstance(node, ast.AsyncFor) for node in nodes) == 1
    assert sum(isinstance(node, ast.Await) for node in nodes) == 0
    assert sum(isinstance(node, (ast.Yield, ast.YieldFrom)) for node in nodes) == 1
    handlers = [node for node in nodes if isinstance(node, ast.ExceptHandler)]
    assert handlers and all(isinstance(handler.body[-1], ast.Raise) for handler in handlers)


# ---------------- T-3 完整性 ----------------
async def test_long_thinking_analysis_and_split_arguments_are_captured_byte_for_byte():
    """30000 字研判 + 25000 字思考：记录与拼接原文逐字节相等（不受 `_ANALYSIS_MAX_CHARS` 影响）。"""
    analysis, thinking = "研" * 30000, "思" * 25000
    head = '{"action":"open_long","sizeFraction":0.5,'
    tail = '"confidence":0.8,"reason":"' + "理由" * 100 + '"}'
    events = [StreamEvent("thinking_delta", text=thinking[:10000]),
              StreamEvent("thinking_delta", text=thinking[10000:]),
              StreamEvent("text_delta", text=analysis[:12345]),
              StreamEvent("text_delta", text=analysis[12345:]),
              StreamEvent("tool_use_start", id="t1", name=DECISION_TOOL_NAME),
              StreamEvent("tool_use_args_delta", id="t1", partial_json=head),
              StreamEvent("tool_use_args_delta", id="t1", partial_json=tail),
              StreamEvent("tool_use_end", id="t1"),
              StreamEvent("message_done", stop_reason="tool_use")]
    recorder, channel, root = await _open_channel()
    try:
        await _consume(RecordingLlmClient(_ListLLM(events), channel), _tools())
    finally:
        rows = await _close_and_read(recorder, root)
    call = _calls(rows)[0]
    assert call["response"]["text"] == analysis
    assert call["response"]["thinking"] == thinking
    tool = call["response"]["toolUses"][0]
    assert tool["argumentsJson"] == head + tail
    assert tool["input"]["reason"] == "理由" * 100 and tool["parseError"] is None
    assert call["request"]["messageCount"] == 1
    assert call["scope"] == {"phase": "round", "roundNo": 1, "callIndex": 1}


# ---------------- T-4 状态分类 ----------------
async def test_natural_end_is_completed():
    recorder, channel, root = await _open_channel()
    try:
        await _consume(RecordingLlmClient(_ListLLM(_events_normal()), channel), _tools())
    finally:
        rows = await _close_and_read(recorder, root)
    call = _calls(rows)[0]
    assert call["status"] == "completed" and call["responseComplete"] is True
    assert call["response"]["stopReason"] == "tool_use"
    assert call["response"]["normalizedUsage"] == {"inputTokens": 3, "outputTokens": None}


async def test_wait_for_timeout_is_recorded_as_cancelled():
    recorder, channel, root = await _open_channel()
    wrapped = RecordingLlmClient(_ListLLM(_events_normal(), hang_before=2), channel)

    async def consume():
        async for _ in wrapped.stream(system="S", messages=_messages(), tools=_tools()):
            pass

    try:
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(consume(), timeout=0.05)
    finally:
        rows = await _close_and_read(recorder, root)
    call = _calls(rows)[0]
    assert call["status"] == "cancelled" and call["responseComplete"] is False
    assert call["response"]["text"] == "研判"


@pytest.mark.parametrize("stop_kind,complete", [("message_done", True), ("text_delta", False)])
async def test_consumer_break_is_closed_early(stop_kind, complete):
    """摘要消费方见到 message_done 即 break：那是一次完整响应（RV-P2-7），更早的 break 不是。"""
    recorder, channel, root = await _open_channel()
    try:
        stream = RecordingLlmClient(_ListLLM(_events_normal()), channel).stream(
            system="S", messages=_messages(), tools=[])
        async for event in stream:
            if event.kind == stop_kind:
                break
        await stream.aclose()
    finally:
        rows = await _close_and_read(recorder, root)
    call = _calls(rows)[0]
    assert call["status"] == "closed_early" and call["site"] == "summary"
    assert call["responseComplete"] is complete


async def test_error_event_is_final_and_enqueued_before_the_generator_ends(monkeypatch):
    recorder, channel, root = await _open_channel()
    offered: list = []
    original = recorder.offer

    def spy(item):
        offered.append(item)
        return original(item)

    monkeypatch.setattr(recorder, "offer", spy)
    events = [StreamEvent("text_delta", text="x"),
              StreamEvent("error", code="LLM_ERROR", message="boom"),
              StreamEvent("text_delta", text="after")]
    try:
        stream = RecordingLlmClient(_ListLLM(events), channel).stream(
            system="S", messages=_messages(), tools=_tools())
        async for event in stream:
            if event.kind == "error":
                assert [getattr(item, "kind", None) for item in offered] == ["llm_call"]
                break
        await stream.aclose()
    finally:
        rows = await _close_and_read(recorder, root)
    call = _calls(rows)[0]
    assert call["status"] == "error"
    assert call["error"] == {"code": "LLM_ERROR", "message": "boom"}
    assert call["eventCounts"] == {"text_delta": 1, "error": 1}


async def test_inner_exception_is_recorded_and_reraised():
    recorder, channel, root = await _open_channel()
    try:
        _, error = await _consume(
            RecordingLlmClient(_ListLLM(_events_normal(), raise_after=2), channel), _tools())
    finally:
        rows = await _close_and_read(recorder, root)
    assert isinstance(error, RuntimeError)
    call = _calls(rows)[0]
    assert call["status"] == "exception"
    assert call["error"]["type"] == "RuntimeError" and call["error"]["message"] == "inner boom"


# ---------------- T-5 调用点分类 ----------------
@pytest.mark.parametrize("tools,site", [
    ([ToolSchema(DECISION_TOOL_NAME, "d", {})], "decision"),
    ([ToolSchema(REFLECTION_TOOL_NAME, "r", {})], "reflection"),
    ([ToolSchema(DISTILL_TOOL_NAME, "x", {})], "experience_distill"),
    ([], "summary"),
    ([ToolSchema("some_other_tool", "o", {})], "other"),
])
def test_call_sites_are_classified_by_tool_name(tools, site):
    assert classify_site(tools) == site


def test_tool_names_are_imported_from_their_source_modules_not_spelled_as_literals():
    text = _LLM_TAP.read_text(encoding="utf-8")
    assert '"submit_trade_' not in text and "'submit_trade_" not in text


# ---------------- T-6 绝不抛 ----------------
async def test_serialization_failure_never_breaks_the_stream(monkeypatch):
    def boom(_obj):
        raise TypeError("not serializable")

    events = _events_normal()
    recorder, channel, root = await _open_channel()
    try:
        monkeypatch.setattr(wire_mod, "canonical_json", boom)
        seen, error = await _consume(RecordingLlmClient(_ListLLM(events), channel), _tools())
        counters = recorder.counters_snapshot()
    finally:
        monkeypatch.undo()
        rows = await _close_and_read(recorder, root)
    assert error is None
    assert len(seen) == len(events) and all(a is b for a, b in zip(seen, events))
    assert counters["serializeErrors"] == 1
    assert _calls(rows) == []


# ---------------- T-7 OpenAI 流式形态 ----------------
async def test_openai_argument_chunks_without_id_attach_to_the_latest_tool():
    events = [StreamEvent("tool_use_start", id="call_1", name=DECISION_TOOL_NAME),
              StreamEvent("tool_use_args_delta", id="call_1", partial_json='{"action":'),
              StreamEvent("tool_use_args_delta", id=None, partial_json='"hold",'),
              StreamEvent("tool_use_args_delta", id=None, partial_json='"reason":"r"}'),
              StreamEvent("message_done", stop_reason="tool_calls",
                          usage={"prompt_tokens": 5, "completion_tokens": 6})]
    recorder, channel, root = await _open_channel()
    try:
        await _consume(RecordingLlmClient(_ListLLM(events, provider="openai"), channel), _tools())
    finally:
        rows = await _close_and_read(recorder, root)
    call = _calls(rows)[0]
    assert call["argsDeltaWithoutId"] == 2
    tool = call["response"]["toolUses"][0]
    assert tool["argumentsJson"] == '{"action":"hold","reason":"r"}'
    assert tool["input"] == {"action": "hold", "reason": "r"}
    assert call["capabilities"]["thinkingCaptured"] is False
    assert call["response"]["normalizedUsage"] == {"inputTokens": 5, "outputTokens": 6}


# ---------------- T-8 / T-10 采样参数真源 ----------------
def _anthropic(thinking: int = 0) -> _OfflineAnthropic:
    return _OfflineAnthropic(base_url="https://example.invalid", api_key=_API_KEY,
                             model="claude-test", anthropic_version="2023-06-01",
                             user_agent="ua", max_tokens=8192, temperature=0.3, top_p=0.9,
                             timeout=5.0, proxy=None, thinking_budget_tokens=thinking)


async def test_anthropic_parameters_are_read_and_the_api_key_never_reaches_a_record():
    recorder, channel, root = await _open_channel()
    try:
        await _consume(RecordingLlmClient(_anthropic(), channel), _tools())
    finally:
        rows = await _close_and_read(recorder, root)
    call = _calls(rows)[0]
    params = call["params"]
    assert params["paramsSource"] == "provider_body" and params["provider"] == "anthropic"
    assert params["thinkingBudgetTokens"] == 0
    assert {key: params["requestParams"][key] for key in
            ("model", "max_tokens", "temperature", "top_p")} == {
        "model": "claude-test", "max_tokens": 8192, "temperature": 0.3, "top_p": 0.9}
    assert _API_KEY not in json.dumps(rows, ensure_ascii=False)


def test_extended_thinking_records_the_temperature_that_was_actually_sent():
    """RV11：开扩展思考时 `_body` 强制 temperature=1.0 并删 top_p——记录必须是发出去的那一组。"""
    params = llm_params_of(_anthropic(thinking=2048), _tools())
    assert params["requestParams"]["temperature"] == 1.0
    assert "top_p" not in params["requestParams"]
    assert params["requestParams"]["thinking"] == {"type": "enabled", "budget_tokens": 2048}
    assert params["thinkingBudgetTokens"] == 2048
    assert _API_KEY not in json.dumps(params)


def test_openai_parameters_include_tool_choice_and_stream_options():
    client = _OfflineOpenAI(base_url="https://example.invalid", api_key=_API_KEY, model="gpt-test",
                            max_tokens=2048, temperature=0.2, top_p=1.0, timeout=5.0, proxy=None)
    params = llm_params_of(client, _tools())
    assert params["paramsSource"] == "provider_body" and params["provider"] == "openai"
    assert params["requestParams"]["tool_choice"] == "auto"
    assert params["requestParams"]["stream_options"] == {"include_usage": True}
    assert "messages" not in params["requestParams"] and "tools" not in params["requestParams"]
    assert _API_KEY not in json.dumps(params)


def test_a_stub_without_body_falls_back_to_attributes():
    class _Stub:
        provider = "anthropic"
        _model, _max_tokens, _temperature, _top_p = "m", 10, 0.5, 0.8

    params = llm_params_of(_Stub(), [])
    assert params["paramsSource"] == "attributes"
    assert params["requestParams"] == {"model": "m", "max_tokens": 10, "temperature": 0.5,
                                       "top_p": 0.8}


# ---------------- T-9 被采纳的决策调用 ----------------
def _meta(reasked: bool, adopted: bool) -> ReasoningMeta:
    return ReasoningMeta(depth="deep", chars=10, stages=3, truncated=False, reasked=reasked,
                         reask_adopted=adopted, deadline_hit=False)


_CALLS = [{"callId": "d1", "site": "decision"}, {"callId": "d2", "site": "decision"},
          {"callId": "r1", "site": "reflection"}, {"callId": "s1", "site": "summary"}]


@pytest.mark.parametrize("calls,reasoning,expected", [
    (_CALLS, None, "d2"),                       # 深度档未生效 ⇒ 最后一次
    (_CALLS, _meta(False, False), "d2"),        # 未追问 ⇒ 最后一次
    (_CALLS, _meta(True, True), "d2"),          # 追问被采纳 ⇒ 最后一次（追问那次）
    (_CALLS, _meta(True, False), "d1"),         # 追问被拒收 ⇒ 倒数第二次
    (_CALLS[2:], _meta(False, False), None),    # 本轮没有任何决策调用
])
def test_adopted_decision_call_follows_the_five_row_table(calls, reasoning, expected):
    assert adopted_decision_call_id(calls, reasoning) == expected


# ---------------- T-11 递归守卫与蒸馏直通 ----------------
def test_copy_and_pickle_do_not_recurse_into_getattr():
    wrapper = RecordingLlmClient(_PicklableInner(), _PicklableChannel())
    assert copy.copy(wrapper)._model == "m"
    restored = pickle.loads(pickle.dumps(wrapper))
    assert restored._model == "m" and restored.provider == "anthropic"


async def test_experience_distill_calls_pass_through_and_only_count_as_late():
    events = _events_normal()
    recorder, channel, root = await _open_channel()
    try:
        seen, error = await _consume(RecordingLlmClient(_ListLLM(events), channel),
                                     _tools(DISTILL_TOOL_NAME))
        counters = recorder.counters_snapshot()
    finally:
        rows = await _close_and_read(recorder, root)
    assert error is None and len(seen) == len(events)
    assert counters["lateCalls"] == 1 and counters["dropped"] == 0
    assert _calls(rows) == []


async def test_wrap_llm_clients_wraps_each_site_and_registers_the_api_key():
    recorder, channel, root = await _open_channel()
    try:
        inner = _anthropic()
        clients = wrap_llm_clients(AiTradeLlmClients(inner, inner, inner), channel)
        assert all(isinstance(client, RecordingLlmClient)
                   for client in (clients.decision, clients.reflection, clients.summary))
        assert clients.decision._inner is inner and clients.decision.provider == "anthropic"
        assert _API_KEY in recorder.secret_forms
    finally:
        await _close_and_read(recorder, root)
