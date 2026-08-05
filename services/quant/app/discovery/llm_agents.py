"""生成 / 审查两个受约束 LLM 子代理（strategy-factor-discovery §2.11）—— **一律 fail-open**。

安全姿态（D-4，镜像 ``ai_auto/decider.py`` 的工具围栏与安全回落）：
- **唯一放行工具**：只把一个 ``ToolSchema`` 传给 ``llm.stream(tools=[...])``，模型物理上没有
  别的工具可调。
- **自行累积 partial_json**：真实事件是 ``tool_use_start`` → 若干 ``tool_use_args_delta``
  → ``message_done``；本模块自行拼接再 ``json.loads``（独立实现，不复用交互式会话）。
- **fail-open**：超时 / 报错 / 解析失败 / 未配置 ⇒ 生成子代理返回空列表（本轮 llm 份额转给
  random），审查子代理**视为全部 accept**。循环退化为纯演化搜索，而不是停摆。这与验证层的
  fail-closed 正好相反，是刻意的：LLM 负责**供给与边界精判**，达标与否绝不由它说了算。

成本闸：每轮至多 2 次调用（1 生成 + 1 审查），另有 ``llmMaxCallsPerHour`` 硬上限。
⚠️ 小时窗是**进程内**滑动窗（不进检查点），故进程重启后计数从 0 重新起算——这是有意选择
（跨重启保留计数需要落库墙钟，而墙钟不可信：用户可能关机三天）。UI 必须标注「本小时用量自
进程启动起算」，不许让用户误以为是跨重启累计。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field

from ..agent.llm.base import LlmMessage, TextBlock
from .prompts import (
    GENERATE_TOOL_NAME,
    REVIEW_TOOL_NAME,
    build_generate_prompt,
    build_generate_system_prompt,
    build_review_prompt,
    build_review_system_prompt,
    generate_tool,
    review_tool,
)

logger = logging.getLogger("aragon")

HOUR_SECONDS = 3600.0
MAX_PROMPT_CHARS = 12000     # 单次 prompt 硬上限（超出左裁，绝不无界膨胀上下文）


@dataclass
class LlmOutcome:
    """一次子代理调用的产物 + 如实的降级标注（进迭代记录，UI 可看「今天烧了多少次」）。"""

    payload: dict = field(default_factory=dict)
    calls: int = 0
    latency_ms: int = 0
    degraded: bool = False
    degraded_reason: str = ""
    prompt_chars: int = 0


class HourlyCallBudget:
    """进程内小时滑动窗计数器（``deque`` 存最近调用的单调时刻，逐出超过 3600s 的）。"""

    def __init__(self, max_per_hour: int, *, clock=time.monotonic):
        self._max = int(max_per_hour)
        self._clock = clock
        self._calls: deque[float] = deque()

    def _evict(self) -> None:
        now = self._clock()
        while self._calls and now - self._calls[0] > HOUR_SECONDS:
            self._calls.popleft()

    def used(self) -> int:
        self._evict()
        return len(self._calls)

    def allows(self) -> bool:
        return self.used() < self._max

    def record(self) -> None:
        self._calls.append(self._clock())

    @property
    def limit(self) -> int:
        return self._max


class DiscoveryLlmAgents:
    """挖掘循环的两个 LLM 子代理（生成 + 审查）；``client=None`` 即整体停用。"""

    def __init__(self, client, *, timeout_s: float = 60.0, max_retries: int = 1,
                 budget: HourlyCallBudget | None = None):
        self._client = client
        self._timeout_s = float(timeout_s)
        self._max_retries = max(0, int(max_retries))
        self._budget = budget or HourlyCallBudget(24)

    @property
    def enabled(self) -> bool:
        return self._client is not None

    def usage(self) -> dict:
        """本小时用量摘要（UI 的 LLM 轨迹卡读它；``sinceProcessStart`` 必须如实标注）。"""
        return {"callsThisHour": self._budget.used(), "limit": self._budget.limit,
                "sinceProcessStart": True}

    # ---------- 生成子代理 ----------
    async def generate(self, payload: dict) -> LlmOutcome:
        """请求 K 条新表达式；任何失败 ⇒ 空列表 + ``degraded``（本轮 llm 份额转给 random）。"""
        prompt = build_generate_prompt(payload)
        outcome = await self._call(system=build_generate_system_prompt(), prompt=prompt,
                                   tool=generate_tool(), tool_name=GENERATE_TOOL_NAME)
        if outcome.degraded:
            return outcome
        items = outcome.payload.get("candidates")
        if not isinstance(items, list):
            return _degraded("invalid_payload", outcome)
        outcome.payload = {"candidates": [x for x in items if isinstance(x, dict)]}
        return outcome

    # ---------- 审查子代理 ----------
    async def review(self, payload: dict) -> LlmOutcome:
        """请求逐条裁决；任何失败 ⇒ **视为全部 accept**（fail-open，绝不因 LLM 挂了卡住搜索）。"""
        prompt = build_review_prompt(payload)
        outcome = await self._call(system=build_review_system_prompt(), prompt=prompt,
                                   tool=review_tool(), tool_name=REVIEW_TOOL_NAME)
        if outcome.degraded:
            return outcome
        items = outcome.payload.get("verdicts")
        if not isinstance(items, list):
            return _degraded("invalid_payload", outcome)
        outcome.payload = {"verdicts": [x for x in items if isinstance(x, dict)]}
        return outcome

    # ---------- 受约束调用（镜像 decider） ----------
    async def _call(self, *, system: str, prompt: str, tool, tool_name: str) -> LlmOutcome:
        outcome = LlmOutcome(prompt_chars=len(prompt))
        if self._client is None:
            return _degraded("not_configured", outcome)
        if not self._budget.allows():
            return _degraded("hourly_cap_reached", outcome)
        message = LlmMessage("user", [TextBlock(prompt[:MAX_PROMPT_CHARS])])
        started = time.monotonic()
        last_reason = "llm_error"
        for attempt in range(self._max_retries + 1):
            self._budget.record()
            outcome.calls += 1
            try:
                raw = await asyncio.wait_for(
                    self._stream(system, message, tool, tool_name), timeout=self._timeout_s)
            except asyncio.TimeoutError:
                last_reason = "timeout"
            except asyncio.CancelledError:
                raise            # 关停：绝不吞、绝不降级，向上抛
            except Exception as exc:  # noqa: BLE001 —— LLM 层任何失败都降级，绝不拖垮挖掘循环
                logger.warning("discovery LLM 调用失败（第 %d 次）：%s", attempt + 1, exc)
                last_reason = "llm_error"
            else:
                outcome.latency_ms = int((time.monotonic() - started) * 1000)
                if raw is None:
                    return _degraded("no_tool_call", outcome)
                outcome.payload = raw
                return outcome
            if not self._budget.allows():
                break
        outcome.latency_ms = int((time.monotonic() - started) * 1000)
        return _degraded(last_reason, outcome)

    async def _stream(self, system: str, message: LlmMessage, tool,
                      tool_name: str) -> dict | None:
        """流式收集**首条**目标工具调用的入参（多次调用取首条；无调用返回 None）。"""
        accs: dict[str, dict] = {}
        order: list[str] = []
        async for event in self._client.stream(system=system, messages=[message],
                                               tools=[tool]):
            if event.kind == "tool_use_start":
                accs[event.id] = {"name": event.name or "", "parts": []}
                order.append(event.id)
            elif event.kind == "tool_use_args_delta":
                if event.id in accs:
                    accs[event.id]["parts"].append(event.partial_json or "")
            elif event.kind == "error":
                raise RuntimeError(event.message or event.code or "LLM_ERROR")
            elif event.kind == "message_done":
                break
        for tid in order:
            if accs[tid]["name"] != tool_name:
                continue
            raw = "".join(accs[tid]["parts"]).strip() or "{}"
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                return None
            return parsed if isinstance(parsed, dict) else None
        return None


def _degraded(reason: str, outcome: LlmOutcome) -> LlmOutcome:
    outcome.degraded = True
    outcome.degraded_reason = reason
    outcome.payload = {}
    return outcome


def accepted_hashes(outcome: LlmOutcome, all_hashes: list[str]) -> tuple[set[str], dict]:
    """把审查裁决折成「被否决的哈希集」；降级时**全部放行**（fail-open）。

    裁决语义**只减不增**：``accept=false`` ⇒ 拒；``accept=true`` 不代表放行，仍要过验证层。

    Returns:
        ``(vetoed_hashes, {hash: reason})``。
    """
    if outcome.degraded:
        return set(), {}
    known = set(all_hashes)
    vetoed: set[str] = set()
    reasons: dict[str, str] = {}
    for row in outcome.payload.get("verdicts") or []:
        digest = str(row.get("hash") or "")
        if digest not in known or bool(row.get("accept", True)):
            continue
        vetoed.add(digest)
        reasons[digest] = str(row.get("reason") or "")[:48]
    return vetoed, reasons


async def resolve_llm_client(session_factory, settings):
    """三级回落解析当前生效的 LLM client（DB 默认服务 → env → None），失败一律 None。

    未配置 ⇒ 生成 / 审查子代理整体停用，循环退化为纯演化（``llmDegraded="not_configured"``），
    这条 fail-open 姿态必须是真的：DoD #9 要求「LLM 未配置 / 报错时任务照常推进」。
    """
    if not bool(getattr(settings, "discovery_llm_enabled", True)):
        return None
    try:
        from ..agent.llm.factory import make_llm_client_from_config
        from ..agent.providers import resolve_active_provider

        config = await resolve_active_provider(session_factory, settings)
        return make_llm_client_from_config(config) if config is not None else None
    except Exception as exc:  # noqa: BLE001 —— 解析失败一律停用 LLM，绝不因此拖垮启动
        logger.warning("discovery LLM 客户端解析失败，本 run 退化为纯演化搜索：%s", exc)
        return None
