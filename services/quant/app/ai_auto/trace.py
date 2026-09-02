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
from typing import TYPE_CHECKING

if TYPE_CHECKING:                       # 仅类型标注用，运行期不导入（with future annotations 不触发环）
    from .decider import ReflectionVerdict, TradeDecision

# 轨迹阶段常量（前后端镜像；session 按此织入，前端 reducer 按此归约）。
# retry：可重试失败降级前的重试面包屑（ai-trade-decision-llm-retry §4.4），前端据此重置本轮累积。
# reflection_*：下单前二次反思三阶段（ai-trade-pre-trade-reflection §2.6），仅交易意愿轮出现。
# base_rule：【基础策略】铁律门禁段（ai-trade-base-strategy-ma-band §2.9），**仅在规则装配时**每轮一条。
# edge_gate：最小 edge 门禁段（ai-trade-cost-accounting-and-edge-gate §5.3），**仅 k_min>0 时**每轮一条。
# sizing：仓位求解 + ATR 止损 + 刹车段（vol-sizing-and-drawdown-brake §3.9），**仅规则装配时**每轮一条。
# 之所以值得新增一个相位（代价 = 8 个前端镜像点）：求解发生在**轮次末尾**（管线第 11 步），`round_start`
# 早已发出、搭不上；且本层新增了 5 个「看起来生效但可能没生效」的观测量（scaleErased / stopSource 的三个
# skip 态 / exposureCapUnreachable / volSource），它们必须每轮可见——静默的保守是本仓库的前科。
# horizon：持仓周期铁律段（ai-trade-holding-horizon-modes §4.4），**仅在契约装配时**每轮一条
# （**含放行轮**——静默的保守会被用户当成故障，这是 base_rule / edge_gate 已确立的理由）。
# summary_failed：滚动摘要失败段（tradability-and-open-funnel 轴 D / D-6），**仅在本轮真的失败过时**
# 一条。它与 `retry` 面包屑同款：一条灰色注记，说明「长期记忆这一次没刷新成、连续第几次了」。
# 值得新增一个相位的理由：摘要失败在合入前**没有任何呈现落点**（`_status_payload` 只有
# `roundsSinceSummary`），而实测出现过 4 条连续失败且诊断串全为空——那正是「后端算了但用户看不见」。
# reask：浅研判追问的流式文本（ai-trade-deep-reasoning-chain §3.4.2 / P0-3）。**必须是独立相位**
# ——前端对 `analysis` 是纯追加归约、全流程唯一清零点是 `retry` 相位，而追问跑在重试循环**之后**、
# 不经 `_emit_retry`。沿用 `analysis` 相位会把 S1–S12 追加成两遍（阶段导轨读出 24/12）；改用
# `emit=None` 则界面上留着的是被丢弃的浅研判——两种写法都让「界面上的研判」与「实际生效的决策」
# 不是同一份。两份并列呈现是刻意的：被拒收的那一份正是「模型第二次反而想得更浅」的现场证据。
PHASES = ("round_start", "thinking", "analysis", "tool_call_start",
          "tool_call", "result", "summary", "error", "retry",
          "reflection_start", "reflection", "reflection_verdict", "base_rule", "edge_gate",
          "sizing", "horizon", "summary_failed", "reask")

# `sizing` 事件与在飞轮次共用的键集合（**单一真源**：事件装配、轮次初值、前端镜像三处照它对齐）。
SIZING_TRACE_KEYS = (
    "brakeLevel", "brakeReason", "drawdownPct", "lossStreak", "peakEquityBasis",
    "unrealizedShare", "modelFraction", "fVol", "fKelly", "fCap", "kellyStatus",
    "fFinalRaw", "finalFraction", "scaleSource", "scaleErased", "volSource",
    "stopPct", "takePct", "stopSource", "effectiveExposure", "effectiveExposureCap",
    "corrScale", "exposureCapUnreachable")

# 合并冲刷阈值：累计 ≥160 字符 / 相变 / 工具起始 / 收尾 即冲刷一条 delta（轮间隔以分钟计，量级极低）。
# [deep-reasoning-chain §3.3.4] 48 → 160：5× 文本量下 48 字符一帧会把 1000 槽位的订阅队列压满而
# **静默丢帧**（`session._push` 的 `QueueFull: pass`）。160 使单轮 delta 帧数与今日同量级。
_TRACE_FLUSH_CHARS = 160
# 整轮单 phase 累计文本硬上限（防滥用），超出后丢弃尾部。[§3.3.4] 4000 → 20000（5×）。
_TRACE_MAX_CHARS = 20000
# 单轮**四槽位合计**上限（新增，§3.3.4 / P1-6）。`_TRACE_MAX_CHARS` 是**单 phase** 的，而一轮最多
# 有 analysis / thinking / reflection / reaskAnalysis 四个累积槽位：各 20000 ⇒ 80000/轮 ⇒
# ×`_TRACES_MAX`(20) ⇒ **1.6MB/成员**，一个 5 标的分组即 8MB 常驻，且 WS 快照重放会把它整份推给
# 每一个新订阅者（订阅队列只有 1000 槽、满了就静默丢）。合计闸把最坏量级压回 ~720KB/成员。
_TRACE_ROUND_TOTAL_MAX = 36000
# 轮次轨迹环形容量（仅内存 / 快照回放，进程内随会话结束回收）。
_TRACES_MAX = 20

# 在飞轮次里的四个文本累积槽位（**单一真源**：轮次初值、合计闸、session 的 emit 闭包三处照它对齐）。
# 键名与 phase 名的唯一分歧是 `reask` → `reaskAnalysis`：前者是 wire 相位名，后者是前端字段名。
ROUND_TEXT_KEYS = ("thinking", "analysis", "reflection", "reaskAnalysis")
# wire 相位名 → 在飞轮次的文本槽位键。
PHASE_TO_TEXT_KEY = {"thinking": "thinking", "analysis": "analysis",
                     "reflection": "reflection", "reask": "reaskAnalysis"}


def append_round_text(trace: dict | None, key: str, delta: str, *,
                      round_max: int = _TRACE_ROUND_TOTAL_MAX) -> str:
    """把一段增量并进在飞轮次的文本槽位，施加**单轮四槽合计**上限；返回真正被接受的那一段。

    返回值同时被 session 用作**外发 delta**——超出配额的尾部既不进内存也不进 WS，否则合计闸
    只压住了服务端而前端仍会收到全量（前端的 trace 对象是同一个形状的第二份内存）。

    Args:
        trace: 在飞轮次对象（`None` ⇒ 无轮次，原样放行，不做任何截断）。
        key: `ROUND_TEXT_KEYS` 之一。
        delta: 待追加的增量。
        round_max: 单轮四槽合计上限（keyword-only，默认 `_TRACE_ROUND_TOTAL_MAX`）。

    Returns:
        被接受的增量（可能被截断，配额耗尽时为空串）。
    """
    if trace is None or not delta:
        return delta
    used = sum(len(trace.get(k) or "") for k in ROUND_TEXT_KEYS)
    remaining = round_max - used
    if remaining <= 0:
        return ""
    accepted = delta if len(delta) <= remaining else delta[:remaining]
    trace[key] = (trace.get(key) or "") + accepted
    return accepted


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
    """装配「在飞轮次」初值：输入快照（价/持仓/信号板/体制/共识/证据摘要）+ 空 thinking/analysis + deciding 态。"""
    return {
        "round": round_no, "ts": ts, "lastPx": ref_px,
        "position": payload.get("position"),
        "signalBoard": signal_board,
        "marketContext": payload.get("marketContext"),
        "multiTimeframeContext": payload.get("multiTimeframeContext"),
        "consensus": payload.get("consensus"),
        "marketDigest": payload.get("marketDigest"),   # R1：round_start/trace 承载证据摘要（RC-9）
        "trigger": payload.get("trigger"),   # wake-interval：唤醒来源 candle/interval（round_start 轨迹带来源）
        # 唤醒感知上下文（wake-aware-context §3.1）：本函数是**显式取键**装配，漏抄这四键
        # `AiTradeRoundTrace` 永远拿不到唤醒原因与战术周期证据 ⇒ 后端发什么前端都收不到
        # （跑不出错、功能静默缺失）。三总闸全关的会话恒为 None ⇒ 前端整段回退旧文案。
        "wake": payload.get("wake"),
        "tacticalContext": payload.get("tacticalContext"),
        "tacticalTier": payload.get("tacticalTier"),
        "contextDisclosure": payload.get("contextDisclosure"),
        # 交易经验（experience-context-graph W-13）：本函数是**显式取键**装配，漏抄这一键
        # `AiTradeRoundTrace` 永远拿不到本轮实际喂入的经验 ⇒ 后端发什么前端都收不到，
        # **跑不出错、功能静默缺失**。未装配的会话恒为 None ⇒ 前端整枚徽标不渲染。
        "experience": payload.get("experience"),
        # 执行周期趋势基调（execution-trend-verdict §3.7.4）：同款**显式取键**装配，漏抄这两键
        # `AiTradeRoundTrace` 永远拿不到本层数据 ⇒ 后端发什么前端都收不到（跑不出错、功能静默
        # 缺失）。`trendVerdict` 随 `round_start` 一次装配；`trendGate` 是轮中由
        # `session._apply_trend_bias_gate` **原地写入**的（走 `tool_call` 相位下发），故初值 None。
        "trendVerdict": payload.get("trendVerdict"),
        "trendGate": None,
        "thinking": "", "analysis": "",
        # 深度思维链（ai-trade-deep-reasoning-chain §3.4.1 / §3.4.2）：**显式取键**装配，漏抄这
        # 两键 `AiTradeRoundTrace` 永远拿不到本层数据 ⇒ 后端发什么前端都收不到（跑不出错、功能
        # 静默缺失）。`reaskAnalysis` 是追问的**独立**累积槽位（绝不并进 `analysis`，P0-3）；
        # `reasoning` 是轮末定型的整份只读读数（沿用 `horizonWire` 整对象透传的先例）。
        "reaskAnalysis": "", "reasoning": None,
        "toolCallPending": False, "toolCall": None,
        "executed": None, "degradeReason": None,
        "summary": None, "errorMessage": None,
        "retries": 0, "lastRetryReason": None,   # §4.4：本轮重试计数 + 最近一次触发原因
        # 下单前二次反思（ai-trade-pre-trade-reflection §5.2）：仅交易意愿轮被写入，hold/关闭轮维持初值。
        "reflection": "", "reflectionPending": False,
        "reflectionVerdict": None, "reflectionReason": None, "reflectionAdjustedSize": None,
        # 【基础策略】铁律门禁（ai-trade-base-strategy-ma-band §2.9）：规则未装配的会话恒为初值。
        "baseRuleStatus": None, "baseRuleZone": None,
        "baseRulePctB": None, "baseRuleReason": None,
        # 最小 edge 门禁（ai-trade-cost-accounting-and-edge-gate §4.3）：k_min=0（默认）的会话恒为初值。
        "edgeGateStatus": None, "edgeBps": None,
        "edgeCostBps": None, "edgeGateReason": None,
        # 信号先验（signal-prior-and-ev-gate §3.10）：**加法键**，不新增 trace phase（新增一个相位
        # 的代价是前端 6 处镜像点）。三总闸全关的会话恒为初值 None ⇒ 前端整段回退旧文案。
        "signalPriors": payload.get("signalPriors"),
        "edgeSource": None, "priorP": None, "priorEvBps": None,
        # 利润保护快照（ai-trade-profit-protection §2.7）：**刻意不新增 trace phase**——基础策略是逐轮
        # 门禁故每轮一条事件，保护层跑在 5s 盯市上、不是轮级判定，只随 round_start 带一份断面。
        "profitGuard": payload.get("profitGuard"),
        # 冷却期（adaptive §4.2 三载体之二）：本函数是**显式取键**装配，不加这一行 `AiTradeRoundTrace`
        # 永远拿不到冷却数据 ⇒ 轨迹上「本轮为什么被降级」永远空白（跑不出错、功能静默缺失）。
        "profitGuardCooldown": payload.get("profitGuardCooldown"),
        # 仓位 / 刹车层（vol-sizing-and-drawdown-brake §3.9）：本函数是**显式取键**装配，漏抄这批键
        # `AiTradeRoundTrace` 永远拿不到本层数据 ⇒ 后端发什么前端都收不到（跑不出错、功能静默缺失）。
        # 键集合走 `SIZING_TRACE_KEYS` 单一真源，杜绝「事件加了键、初值忘了加」的漂移。
        **{key: None for key in SIZING_TRACE_KEYS},
        # 持仓周期铁律层（ai-trade-holding-horizon-modes §3.4）：本函数是**显式取键**装配，漏抄这
        # 三键 `AiTradeRoundTrace` 永远拿不到本层数据 ⇒ 后端发什么前端都收不到（跑不出错、功能静默
        # 缺失）。契约未装配的会话恒为初值 None ⇒ 前端整段不渲染。
        "horizonStatus": None, "horizonReason": None, "horizonWire": None,
        # 滚动摘要健康度（轴 D）：**显式取键**装配，漏抄这一行前端永远拿不到停摆事实。
        "summaryHealth": None,
        # 离场优先级的**最终**档位（exit-priority §6.3）：由 `reflection_verdict` 相位回填；
        # 非反思轮为 None，此时读 `horizonWire.exitPriority`（整对象透传的那一份）即可。
        "exitPriority": None,
        # `tool_call` 相位的三个加法键（§4.6.1）：**逐键手抄区**，漏抄即静默功能缺失——界面上
        # 会继续显示「决策工具 · 观望」，而模型提交的其实是 `close`。
        "proposedAction": None, "proposedReason": None, "vetoedBy": None,
        "state": "deciding",
    }


def trace_event(phase: str, round_no: int, ts: int, **data) -> dict:
    """构造 `agent_trace` WS 事件信封（沿用既有 `{type,data}`；老前端忽略未知 type，向后兼容）。"""
    return {"type": "agent_trace",
            "data": {"phase": phase, "round": round_no, "ts": ts, **data}}


# ---------- 下单前二次反思轨迹装配（RV-2：下沉 trace.py 控 session.py 增量）----------
def reflection_start_event(round_no: int, ts: int, decision: "TradeDecision") -> dict:
    """reflection_start：进入二次反思，携被复核决策快照（仅交易意愿轮出现）。"""
    return trace_event("reflection_start", round_no, ts, input={
        "action": decision.action, "sizeFraction": decision.size_fraction,
        "confidence": decision.confidence, "reason": decision.reason})


def reflection_verdict_event(round_no: int, ts: int, verdict: "ReflectionVerdict",
                             adjusted_size_fraction: float | None = None,
                             exit_priority: dict | None = None) -> dict:
    """reflection_verdict：反思裁决（confirm/abort）+ 实际收缩后仓位（若有）+ 理由。

    `exit_priority`（exit-priority §6.3）：反思**之后**才定型的最终档位（含 `adjudicated` 与
    `failOpenReason`）。**刻意不新开 `exit_priority` 相位**：那要付出 `PHASES` + 事件类型 +
    轮次初值 + `reduceRoundPhase` 四处逐键手抄的代价，而漏抄不会报错、只会静默功能缺失（R-9）。
    非反思轮的 `exitPriority` 随 `horizon` 相位的整对象透传自然到达，`None` ⇒ 键值为 null。
    """
    return trace_event("reflection_verdict", round_no, ts, verdict=verdict.verdict,
                       adjustedSizeFraction=adjusted_size_fraction, reason=verdict.reason,
                       exitPriority=exit_priority)


# ---------- 【基础策略】铁律门禁轨迹装配（ai-trade-base-strategy-ma-band §2.9）----------
def base_rule_event(round_no: int, ts: int, band: dict | None,
                    status: str, reason: str | None = None) -> dict:
    """base_rule：本轮铁律判定（pass/veto/unavailable）+ 通道断面 + 降级原因。

    静默的保守会被用户当成故障（`ai-trade-reflection-hold-inertia` 是本仓库的前科），故**每轮**都发一条
    ——通道位置与放行 / 否决必须在 UI 上自解释。`band is None` 时三条通道线为 None（不假数据）。
    """
    band = band or {}
    return trace_event("base_rule", round_no, ts, status=status,
                       zone=band.get("zone"), pctB=band.get("pctB"),
                       upper=band.get("upper"), mid=band.get("mid"),
                       lower=band.get("lower"), reason=reason)


# ---------- 最小 edge 门禁轨迹装配（ai-trade-cost-accounting-and-edge-gate §5.3）----------
def edge_gate_event(round_no: int, ts: int, wire: dict,
                    cost_snapshot: dict | None = None) -> dict:
    """edge_gate：本轮门禁判定（pass/veto/unavailable）+ edge/成本断面 + 会话内成本快照。

    与 `base_rule_event` 的理由逐字相同：**每轮都发一条**（`k_min > 0` 时）——静默的保守会被用户
    当成故障。`costSnapshot` 是纯加法只读键（§5.4 的会话内观测通道），前端未识别即忽略。

    cost-truth-and-net-pnl P1-1：`costSnapshot` 自 `CostRuntime.snapshot()` 直出，故轴 A 的
    `feeRates` 与轴 C 的 `reconcile` 两个**顶层**加法键随该快照一并到达，本函数无需逐键搬运
    ——`build_cost_stats` 是这两条通道（trace / 落库 aiStats）的同一个产物，形状必然一致。
    """
    return trace_event("edge_gate", round_no, ts, costSnapshot=cost_snapshot, **wire)


def edge_gate_unavailable_event(ts: int, streak: int) -> dict:
    """连续不可用告警（镜像 `AI_TRADE_BASE_RULE_UNAVAILABLE` 的既有写法）。

    极性与铁律层相反：本层 fail-open，**期间开仓一律放行**——静默的降级与静默的保守一样，都会被
    用户当成故障，故文案必须把「放行」这件事讲清楚，而不是让用户以为门禁在生效。
    """
    return {"type": "error", "data": {
        "ts": int(ts), "code": "AI_TRADE_EDGE_GATE_UNAVAILABLE",
        "message": (f"最小 edge 门禁已连续 {streak} 轮无法估算（ATR 数据不足），"
                    "期间开仓一律**放行**（本层 fail-open）；平仓与撤单不受影响")}}


def apply_edge_gate_trace(trace: dict | None, wire: dict, reason: str | None) -> None:
    """把本轮 edge 门禁判定写入在飞轮次（`trace is None` ⇒ 空转，与既有 emit 段同款垫片）。"""
    if trace is None:
        return
    trace["edgeGateStatus"] = wire.get("status")
    trace["edgeBps"] = wire.get("edgeBps")
    trace["edgeCostBps"] = wire.get("costBps")
    trace["edgeGateReason"] = reason
    trace["edgeSource"] = wire.get("edgeSource")   # 前端据此改判「分子是怎么来的」文案
    trace["priorP"] = wire.get("priorP")
    trace["priorEvBps"] = wire.get("priorEvBps")


# ---------- 仓位 / 刹车层轨迹装配（ai-trade-vol-sizing-and-drawdown-brake §3.9）----------
def sizing_event(round_no: int, ts: int, wire: dict) -> dict:
    """sizing：本轮仓位求解 + ATR 止损 + 刹车断面（规则装配时**每轮一条**）。

    与 `base_rule_event` / `edge_gate_event` 的理由逐字相同：静默的保守会被用户当成故障。本层尤其
    需要每轮可见——`fFinalRaw` 与 `finalFraction` 之差就是被档位地板吃掉的量，只看后者会让运维以为
    减速生效了（[P1-2]）。
    """
    return trace_event("sizing", round_no, ts,
                       **{key: wire.get(key) for key in SIZING_TRACE_KEYS})


def apply_sizing_trace(trace: dict | None, wire: dict) -> None:
    """把本轮仓位判定写入在飞轮次（`trace is None` ⇒ 空转，与既有 emit 段同款垫片）。"""
    if trace is None:
        return
    for key in SIZING_TRACE_KEYS:
        trace[key] = wire.get(key)


# ---------- 持仓周期铁律层轨迹装配（ai-trade-holding-horizon-modes §4.4）----------
def horizon_event(round_no: int, ts: int, wire: dict | None,
                  status: str, reason: str | None = None) -> dict:
    """horizon：本轮持仓周期判定（pass/veto/released/idle）+ 完整快照 + 降级原因。

    与 `base_rule_event` / `edge_gate_event` 的理由逐字相同：**每轮都发一条**（契约装配时）——静默
    的保守会被用户当成故障。整份 `wire` 随事件下发（而非逐键搬运）：本层的读数有 20 余个，逐键抄
    一遍必然在下一次加字段时漏掉，而前端 chip / 轨迹 / 统计三处都要读它。
    """
    return trace_event("horizon", round_no, ts, status=status, reason=reason, horizon=wire)


def apply_horizon_trace(trace: dict | None, wire: dict | None, status: str,
                        reason: str | None) -> None:
    """把本轮持仓周期判定写入在飞轮次（`trace is None` ⇒ 空转，与既有 emit 段同款垫片）。"""
    if trace is None:
        return
    trace["horizonStatus"] = status
    trace["horizonReason"] = reason
    trace["horizonWire"] = wire


# ---------- 滚动摘要健康度轨迹装配（tradability-and-open-funnel 轴 D · D-6）----------
async def maybe_emit_summary_failed(push, trace: dict | None, health: dict | None,
                                    round_no: int, ts: int) -> None:
    """本轮摘要失败过 ⇒ 发一条 `summary_failed` 面包屑并写入在飞轮次；否则**零动作**。

    判据是 `roundsSinceSummary == 0 且 failures > 0`——摘要无论成败都会把它清零，故「刚归零且
    连续失败数非零」恰好等价于「本轮跑过摘要且失败了」。放在纯装配层是为了让 `session.py` 只留
    一行薄封装（它已 1819 行，本轮净增预算 ≤ 22 行）。

    Args:
        push: 会话的事件扇出闭包。
        trace: 在飞轮次（`None` ⇒ 只发事件、不写轮次，与既有 `apply_*_trace` 同款垫片）。
        health: `decider.summary_health_wire()` 产物；`None` ⇒ 零动作。
        round_no: 轮次号。
        ts: 事件时间戳。
    """
    if not health or int(health.get("roundsSinceSummary") or 0) != 0:
        return
    if int(health.get("failures") or 0) <= 0:
        return
    if trace is not None:
        trace["summaryHealth"] = health
    await push(trace_event("summary_failed", round_no, ts, reason=health.get("lastError"),
                           failures=health.get("failures"), stalled=health.get("stalled"),
                           backoffRounds=health.get("backoffRounds")))


def horizon_released_event(ts: int, streak: int, reason: str | None) -> dict:
    """连续解除告警（§4.4）：本层 fail-open，故阈值比铁律层宽（镜像 `_EDGE_UNAVAILABLE_ALERT_ROUNDS`）。

    文案必须把「放行」这件事讲清楚，而不是让用户以为门禁在生效——静默的降级与静默的保守一样，都
    会被当成故障。
    """
    return {"type": "error", "data": {
        "ts": int(ts), "code": "AI_TRADE_HORIZON_RELEASED",
        "message": (f"持仓周期约束已连续 {streak} 轮解除（原因：{reason or '未知'}），"
                    "期间主动平仓一律**放行**（本层 fail-open）；开仓与撤单本就不受限制")}}
