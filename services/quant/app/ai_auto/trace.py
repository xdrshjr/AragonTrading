"""AI 自动交易「决策认知轨迹」纯逻辑（ai-auto-trading-agent-trace §3.5）。

集中承载轨迹相关的无副作用装配与流式合并，避免 `decider.py`/`session.py` 膨胀：
- `_TraceCoalescer`：把决策器高频 `text_delta`/`thinking_delta` 按 phase 合并后再 `emit`，
  避免撑爆 WS 队列（`_push` 满队列会丢帧）；`emit is None`（回测 / 单测无订阅）时全程空转，
  决策行为与今日逐字一致。
- `new_round_trace`：装配「在飞轮次」初值（thinking/analysis 空串、state="deciding"）。
- `trace_event`：构造 `agent_trace` WS 事件信封 `{type,data:{phase,round,ts,**data}}`。

阈值为模块级常量（与 `_DECISIONS_MAX`/`_SNAPSHOT_EQUITY_MAX` 同风格，规避「AI_TRADE_ 前缀 env
三处联动」的既知易踩点，§八）。
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable

# 轨迹阶段常量（前后端镜像；session 按此织入，前端 reducer 按此归约）。
PHASES = ("round_start", "thinking", "analysis", "tool_call_start",
          "tool_call", "result", "summary", "error")

# 合并冲刷阈值：累计 ≥48 字符 / 相变 / 工具起始 / 收尾 即冲刷一条 delta（轮间隔以分钟计，量级极低）。
_TRACE_FLUSH_CHARS = 48
# 整轮单 phase 累计文本硬上限（防滥用），超出后丢弃尾部。
_TRACE_MAX_CHARS = 4000
# 轮次轨迹环形容量（仅内存 / 快照回放，进程内随会话结束回收）。
_TRACES_MAX = 20

# 由 session 注入的 push 闭包：接收 `{phase, delta}` 或 `{phase:tool_call_start, name}`，补 round/ts 后扇出。
TraceEmit = Callable[[dict], Awaitable[None]]


class _TraceCoalescer:
    """按 phase 累积决策器流式增量，达阈值 / 相变 / 收尾时冲刷一条合并 delta（`emit is None` 空转）。"""

    def __init__(self, emit: TraceEmit | None, *,
                 flush_chars: int = _TRACE_FLUSH_CHARS,
                 max_chars: int = _TRACE_MAX_CHARS):
        self._emit = emit
        self._flush_chars = flush_chars
        self._max_chars = max_chars
        self._phase: str | None = None      # 当前缓冲的 phase（thinking / analysis）
        self._buf: list[str] = []
        self._buf_len = 0
        self._sent: dict[str, int] = {}     # 各 phase 已发字符数（供 max 截断）

    async def feed(self, phase: str, text: str) -> None:
        """喂入某 phase 的增量；phase 切换先冲刷旧段，超单 phase 硬上限则丢弃尾部。"""
        if self._emit is None or not text:
            return
        if self._phase is not None and phase != self._phase:
            await self.flush()
        self._phase = phase
        remaining = self._max_chars - self._sent.get(phase, 0) - self._buf_len
        if remaining <= 0:
            return
        chunk = text if len(text) <= remaining else text[:remaining]
        self._buf.append(chunk)
        self._buf_len += len(chunk)
        if self._buf_len >= self._flush_chars:
            await self.flush()

    async def flush(self) -> None:
        """把当前缓冲作为一条 `{phase, delta}` 发出（空缓冲则仅复位）。"""
        if self._emit is None or not self._buf or self._phase is None:
            self._buf = []
            self._buf_len = 0
            return
        delta = "".join(self._buf)
        phase = self._phase
        self._buf = []
        self._buf_len = 0
        self._sent[phase] = self._sent.get(phase, 0) + len(delta)
        await self._emit({"phase": phase, "delta": delta})

    async def mark(self, name: str) -> None:
        """模型开始调用工具：先由调用方 flush 文本段，再发一条 tool_call_start 占位。"""
        if self._emit is None:
            return
        await self._emit({"phase": "tool_call_start", "name": name})


def new_round_trace(round_no: int, ts: int, ref_px: float,
                    payload: dict, signal_board: list[dict]) -> dict:
    """装配「在飞轮次」初值：输入快照（价/持仓/信号板/体制/共识）+ 空 thinking/analysis + deciding 态。"""
    return {
        "round": round_no, "ts": ts, "lastPx": ref_px,
        "position": payload.get("position"),
        "signalBoard": signal_board,
        "marketContext": payload.get("marketContext"),
        "consensus": payload.get("consensus"),
        "thinking": "", "analysis": "",
        "toolCallPending": False, "toolCall": None,
        "executed": None, "degradeReason": None,
        "summary": None, "errorMessage": None,
        "state": "deciding",
    }


def trace_event(phase: str, round_no: int, ts: int, **data) -> dict:
    """构造 `agent_trace` WS 事件信封（沿用既有 `{type,data}`；老前端忽略未知 type，向后兼容）。"""
    return {"type": "agent_trace",
            "data": {"phase": phase, "round": round_no, "ts": ts, **data}}
