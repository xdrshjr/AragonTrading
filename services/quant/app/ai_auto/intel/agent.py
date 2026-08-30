"""情报 Agent 的流式 LLM 调用与工具解析（ai-trade-market-intel §6.1 #7）。

形状镜像 `decider._stream_decision`，但**只有一个工具**且**没有任何交易语义**——这不是简化，是
§3.9 提示注入防护的第 ② 层：采集 Agent 的 `tools` 恒为单元素且是 `submit_market_intel`，
结构上无法交易。

两条与 `decider` 逐字同源的收尾纪律（它们各自对应本仓库栽过的一个坑）：

1. **首条 `message_done` 不直接 break**，而是置 `done` 并继续消费到流自然结束，只为回填 usage。
   OpenAI 标准的 usage 走一个 `choices` 为空的收尾 chunk，因而会产生**第二条** `message_done`；
   见到即 break 会让 token 用量在半个 provider 家族上恒空。
2. **截断口径 provider 无关**：Anthropic 报 `max_tokens`、OpenAI 把 `finish_reason="length"` 塞进
   同一个字段。判据取 `reasoning_protocol.TRUNCATION_STOP_REASONS` 这一唯一真源——写死一家口径
   会让另外半个 provider 家族原样退回「模型就是没调工具」的假象。
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass

from ...agent.llm.base import LlmMessage, TextBlock, ToolSchema
from ...agent.llm.retry import normalize_usage
from ..reasoning_protocol import CONTENT_FILTER_STOP_REASONS, TRUNCATION_STOP_REASONS

logger = logging.getLogger(__name__)

# 本轮研判文字的留存上限（只为审计与排障；情报轮不把它喂回任何 prompt）。
_NOTES_MAX_CHARS = 4000


@dataclass(frozen=True)
class IntelAgentResult:
    """一次情报轮 LLM 调用的产物。

    `degrade` 非空即表示本轮没有可用的工具入参，值域与 `taxonomy.DEGRADE_TEXT` 的键**同源**：
    `llm_failed` / `no_tool_call` / `reasoning_truncated`。
    """

    raw: dict | None
    degrade: str | None
    notes: str = ""
    usage: dict | None = None
    stop_reason: str | None = None

    @property
    def input_tokens(self) -> int:
        return int((self.usage or {}).get("inputTokens") or 0)

    @property
    def output_tokens(self) -> int:
        return int((self.usage or {}).get("outputTokens") or 0)


async def _consume(llm, *, system: str, user: str, tool: ToolSchema
                   ) -> tuple[dict | None, str, str | None, dict | None]:
    """跑一次流，累积首个目标工具的 `partial_json` 并解析。"""
    accs: dict[str, dict] = {}
    order: list[str] = []
    notes: list[str] = []
    done = False
    stop_reason: str | None = None
    usage: dict | None = None
    async for ev in llm.stream(system=system,
                               messages=[LlmMessage("user", [TextBlock(user)])],
                               tools=[tool]):
        if ev.kind == "message_done":
            stop_reason = ev.stop_reason or stop_reason   # 非空者胜（第二条通常只带 usage）
            usage = ev.usage or usage                     # 只回填，绝不用 None 覆盖已有值
            done = True
            continue
        if done:
            continue
        if ev.kind in ("text_delta", "thinking_delta"):
            notes.append(ev.text or "")
        elif ev.kind == "tool_use_start":
            accs[ev.id] = {"name": ev.name or "", "parts": []}
            order.append(ev.id)
        elif ev.kind == "tool_use_args_delta":
            if ev.id in accs:
                accs[ev.id]["parts"].append(ev.partial_json or "")
        elif ev.kind == "error":
            raise RuntimeError(ev.message or ev.code or "LLM_ERROR")
    raw: dict | None = None
    for tid in order:
        if accs[tid]["name"] == tool.name:
            text = "".join(accs[tid]["parts"]).strip() or "{}"
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = None
            raw = parsed if isinstance(parsed, dict) else None
            break
    return raw, "".join(notes).strip()[:_NOTES_MAX_CHARS], stop_reason, usage


def _no_tool_reason(stop_reason: str | None) -> str:
    """把 `no_tool_call` 细分成「预算耗尽」与「内容被拦」两个诚实的码。

    一个把输出预算烧光在研判文字上、根本没走到工具调用的轮次，被报成「模型就是没调工具」是本
    仓库栽过的一个坑（deep-reasoning-chain 根因 C-4）——它会让运维去调错的旋钮。
    """
    if not stop_reason:
        return "no_tool_call"
    if stop_reason in TRUNCATION_STOP_REASONS:
        return "reasoning_truncated"
    if stop_reason in CONTENT_FILTER_STOP_REASONS:
        return "no_tool_call"
    return "no_tool_call"


async def run_round(llm, *, system: str, user: str, tool_name: str, tool_description: str,
                    tool_schema: dict, timeout_s: float) -> IntelAgentResult:
    """跑一次情报轮（采集或整理）。**绝不抛**：任何失败都翻译成一个降级码。

    Args:
        llm: 情报专用 client（`intel/llm.py` 造的第四份预算）。
        system: 协议 system prompt。
        user: 语料块 / 清单 + 收尾指令。
        tool_name / tool_description / tool_schema: **唯一**放行的工具。
        timeout_s: 单轮墙钟死线。

    Returns:
        `IntelAgentResult`。`raw is None` 时 `degrade` 必非空。
    """
    tool = ToolSchema(name=tool_name, description=tool_description, input_schema=tool_schema)
    try:
        raw, notes, stop_reason, usage = await asyncio.wait_for(
            _consume(llm, system=system, user=user, tool=tool), timeout=timeout_s)
    except asyncio.TimeoutError:
        return IntelAgentResult(None, "llm_failed", "", None, None)
    except asyncio.CancelledError:
        raise                    # 关停：绝不吞、绝不降级，向上抛（BaseException，不入下方 except）
    except Exception as exc:  # noqa: BLE001 —— 任何 LLM/网络失败翻译成降级码（情报绝不阻断交易）
        logger.warning("ai-trade intel round failed: %s", exc)
        return IntelAgentResult(None, "llm_failed", "", None, None)
    normalized = normalize_usage(getattr(llm, "provider", "anthropic"), usage)
    if raw is None:
        return IntelAgentResult(None, _no_tool_reason(stop_reason), notes, normalized,
                                stop_reason)
    return IntelAgentResult(raw, None, notes, normalized, stop_reason)
