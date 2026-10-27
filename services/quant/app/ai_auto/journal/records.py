"""把会话侧的活对象裁剪 + 序列化成可入队的行（**纯函数**，ai-trade-audit-journal §2.2.3）。

两条硬纪律：

1. **入队即已序列化**。`writer.note_*` 收到的是**已经 `json.dumps` 过的字符串字段**的 dict，
   不是活的对象引用。理由具体：`session._round_trace` 是**原地累积**的（`_begin_round_trace`
   之后逐相位改写），把它的引用排进队列，落库时写下的是**下一轮**的内容。序列化在入队时完成，
   既冻结了内容，又把上限施加在了热路径上（可控、可测）。
2. **三档采集粒度的裁剪只在这一处生效**（`build_decision_row`）。散在多处的裁剪必然漂移，
   而漂移的表现（某一档少落一列）不报任何错。

本模块**不许出现 `raise`**（门禁 G-5 用 AST 冻结）：它跑在决策轮的 `try` 内，一次异常会把
一轮**已经成功成交**的决策翻成 `AI_TRADE_ROUND_FAILED`——审计需求恰好害了它声称绝不碰的东西。
所有 `json.dumps` 一律带 `default=str`（未知对象降级成它的 `repr`，而不是抛）。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from ..open_funnel import LAYER_LABELS, LAYERS, classify_block
from .redact import redact

SCHEMA_VERSION = 1

# `full` 档的文本下界：设置项低于它时按它取（§七 第 6 项「full 档实际取 max(本值, 20000)」）。
FULL_TEXT_FLOOR = 20000

@dataclass(frozen=True, slots=True)
class CaptureLimits:
    """一次采集的三档粒度与两个上限（由 `runtime.limits_from_settings` 从 `Settings` 派生）。"""

    tier: str = "standard"
    text_max_chars: int = 4000
    payload_max_chars: int = 24000
    context_chain: bool = True

    @property
    def keeps_context(self) -> bool:
        """本档是否落 `payload_json` / `output_json`（`minimal` 档不落 ⇒ 做不了后训练）。"""
        return self.tier in ("standard", "full")

    @property
    def keeps_full_text(self) -> bool:
        """本档是否落 `thinking_text` 与 `prompt_text`（仅 `full`）。"""
        return self.tier == "full"

    @property
    def effective_text_max(self) -> int:
        """单段文本的生效上限：`full` 档取 `max(设置值, 20000)`，其余取设置值。"""
        return max(self.text_max_chars, FULL_TEXT_FLOOR) if self.keeps_full_text \
            else self.text_max_chars


@dataclass(frozen=True, slots=True)
class JournalScope:
    """一条日志行的会话身份（group / run / inst / bar）与本次采集的档位。"""

    group_id: str = ""
    run_id: int = 0
    inst_id: str = ""
    bar: str = ""
    limits: CaptureLimits = field(default_factory=CaptureLimits)


@dataclass(frozen=True, slots=True)
class RoundJournalInput:
    """决策行写点的**冻结聚合入参**（`CLAUDE.md` §二：形参 > 5 一律改用聚合结构体）。

    **只承载引用、不做任何计算**：裁剪与序列化全部在 `build_decision_row()` 里做（单一真源）。

    `round_fills` 是本轮 `executor.trades` 上新增的**全部**腿——含 `_post_round` 里
    `_maybe_profit_exit` 平掉的那一笔。没有它，「利润保护在决策轮内平掉的那一笔」在日志台上会
    表现为「模型说开多、然后什么都没发生、然后不知从哪冒出来一笔平仓」，而利润保护正是本仓库
    实测唯一为正的确定性离场通道（评审 RP1-2）。
    `post_round_offset` 是那条分界线在 `round_fills` 里的下标：它之后的腿 `source="post_round"`。
    """

    round_no: int
    ts: int
    wake: dict
    payload: dict | None
    decision: object            # 门禁**之后**的最终 TradeDecision
    stage1_decision: object     # 任何门禁**之前**的模型原始决策
    executed: dict
    degrade: str | None = None
    gate_degrade: str | None = None
    llm_degrade: str | None = None
    reflection_verdict: str | None = None
    reflection_reason: str | None = None
    round_fills: tuple = ()
    post_round_offset: int = 0


@dataclass(frozen=True, slots=True)
class DeciderSnapshot:
    """决策器在本轮结束那一刻的只读断面（由 `session._journal_decider_snapshot()` 装配）。

    全部取自 `decider` 的只读出口，**一个字段都不在这里重算**——尤其是 `request_shape`：它必须
    是出站那一行现算的那一份，按 `payload` 重算的版本永远看不见重试后缀（评审 RP0-1）。
    """

    analysis: str = ""
    reflection: str = ""
    thinking: str = ""
    profile: dict | None = None          # {"sha256","systemPrompt","toolsJson","provider","model"}
    request_shape: dict | None = None
    context_turn: dict | None = None     # {"assistant": 门禁后的 decision_input, "toolResult": 原串}
    usage: dict | None = None
    reasoning: dict | None = None        # ReasoningMeta.wire()
    latency_ms: float | None = None
    retries: int = 0
    depth: str = ""


@dataclass(frozen=True, slots=True)
class EventJournalInput:
    """事件行写点的冻结聚合入参。`detail` 会经 `redact` 后序列化。"""

    category: str
    code: str
    level: str = "info"
    message: str = ""
    detail: dict | None = None
    ts: int = 0
    inst_id: str | None = None


def dumps(obj) -> str:
    """本包**唯一**的 JSON 序列化出口：`ensure_ascii=False` + `default=str`（绝不抛）。"""
    try:
        return json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001 —— 见模块 docstring：本模块的任何异常都会伤到真实交易
        return json.dumps({"_serializeFailed": True}, ensure_ascii=False)


def clip(text: str | None, limit: int) -> str | None:
    """把一段文本截到 `limit` 字符；空串 / None ⇒ `None`（**整键缺席**，绝不写空串）。

    `limit <= 0` 也返回 `None`——那是「这一档不采这段文本」的显式表达。
    """
    if not text or limit <= 0:
        return None
    value = str(text)
    return value if len(value) <= limit else value[:limit]


def _leg_wire(leg: dict, source: str) -> dict:
    """一条成交腿的有界断面。`px` / `sz` 取自 executor 的 `price` / `qty`（键名对齐 wire）。"""
    out = {"side": leg.get("side"), "px": leg.get("price"), "sz": leg.get("qty"),
           "fee": leg.get("fee"), "pnl": leg.get("pnl"), "source": source}
    for key in ("clOrdId", "ordId"):
        if leg.get(key):
            out[key] = leg.get(key)
    # `reason` 是这条腿的触发码（`profit_guard_*` / `agent` / …）。带上它，详情页第 6 段才说得出
    # 「本笔由利润保护在决策轮末触发，不是模型的动作」。
    if leg.get("reason"):
        out["reason"] = leg.get("reason")
    return out


def build_fill_ref(inp: RoundJournalInput) -> dict:
    """执行断面（`fill_ref_json` 的内容）。**含本轮全部成交腿**，含 `_post_round` 的那一笔。"""
    executed = inp.executed if isinstance(inp.executed, dict) else {}
    offset = max(0, int(inp.post_round_offset or 0))
    legs = [_leg_wire(leg, "round" if idx < offset else "post_round")
            for idx, leg in enumerate(inp.round_fills or ()) if isinstance(leg, dict)]
    out: dict = {
        "traded": bool(executed.get("traded")),
        "skipped": bool(executed.get("skipped")),
        "action": executed.get("action"),
        "side": executed.get("side"),
        "qty": executed.get("qty"),
        "positionAfter": executed.get("positionAfter"),
        "legs": legs,
    }
    for key in ("reason", "closeBlocked", "closedFilled"):
        if executed.get(key) is not None:
            out[key] = executed.get(key)
    return out


def _decision_attr(decision, name, default=None):
    """从 TradeDecision 取一个属性（**整段 getattr 兜底**：单测替身可能只给部分字段）。"""
    return getattr(decision, name, default)


def _cl_ord_id(fill_ref: dict) -> str:
    """本轮的锚点单号：优先最后一条腿（含 `_post_round` 的平仓腿），无腿即空串。

    取**最后**一条而不是第一条：`linking` 的第 1 步按它回查成交，而一轮里最靠后的那条腿才是
    「这一轮最终发生的事」。缺失即空串——`clOrdId` 在 `live_auto/executor` 里是 best-effort
    可选（纸面 / 无凭证会话在结构上根本没有交易所单号），**绝不**据此判空。
    """
    legs = fill_ref.get("legs") or []
    for leg in reversed(legs):
        if leg.get("clOrdId"):
            return str(leg["clOrdId"])[:32]
    return ""


def _ord_id(fill_ref: dict) -> str:
    legs = fill_ref.get("legs") or []
    for leg in reversed(legs):
        if leg.get("ordId"):
            return str(leg["ordId"])[:64]
    return ""


def build_telemetry(snap: DeciderSnapshot, limits: CaptureLimits) -> dict:
    """遥测断面：provider / model / token / 耗时 / 重试 / 研判深度 / 采集档。

    **取不到即整键缺席**，绝不补 0——一个填了 0 的 token 数会让「没记到」与「真的是 0」
    在数据上不可区分，而那正是本仓库在费率上付过代价的形状。
    """
    profile = snap.profile or {}
    usage = snap.usage or {}
    out: dict = {"captureTier": limits.tier}
    for key, value in (("provider", profile.get("provider")), ("model", profile.get("model")),
                       ("inputTokens", usage.get("inputTokens")),
                       ("outputTokens", usage.get("outputTokens")),
                       ("reasoningDepth", snap.depth or None)):
        if value not in (None, ""):
            out[key] = value
    if snap.latency_ms is not None:
        out["latencyMs"] = round(float(snap.latency_ms), 1)
    out["retries"] = int(snap.retries or 0)
    if snap.reasoning:
        out["reasoning"] = snap.reasoning
    return out


GATES_SCHEMA = 1


def build_gates(armed: dict, readings: dict, degrade: str | None) -> dict:
    """13 层门禁的三态断面（**层集与层名的唯一真源是 `open_funnel`**，评审 RP0-3）。

    `gate_veto.py` 的开篇纪律 1 逐字写着：「`vetoedBy` 一律取 `open_funnel.classify_block`，
    **绝不**在本模块另写一张层名表……两处各写一份必然漂移，而漂移的表现（模型与界面读到一个
    不存在的层名）**不报任何错**。」本函数逐字沿用同一条纪律：`key` 与顺序恒等于
    `open_funnel.LAYERS`，中文名恒取自 `LAYER_LABELS` 并**随 wire 下发**给前端
    （`lib/journal.ts` 里一个层名字面量都不许有，由前端源码扫描门禁冻结）。

    三条判据各自有真源、互不重算：

    - `armed` 取各层 runtime 已有的只读断面。**取不到 ⇒ `None`**，前端渲染成「未知」而不是
      「未装配」——把「问不出来」说成「没装」是本仓库反复禁止的那种谎。
    - `verdict`：`veto` = 该层就是 `vetoedBy`；`skip` = `armed` 明确为假；其余 `pass`。
      `armed is None` ⇒ 仍判 `pass`（我们只知道它没否决本轮，不知道它装没装）。
    - **13 行一条不少地渲染**：「没显示 = 通过」是被禁止的（`trace.py` 的 `base_rule` 相位注释
      原文：「**含放行轮**——静默的保守会被用户当成故障」）。

    Args:
        armed: `{层 key: bool | None}`；缺席的键等价于 `None`。
        readings: `{层 key: dict}`；**缺即省略**，绝不补 0。
        degrade: 本轮合成的降级码（门禁之后、执行注记之前的那一个）。

    Returns:
        `{"schema", "vetoedBy", "layers": [{key,label,armed,verdict,readings?}, …]}`。
    """
    vetoed_by = classify_block(degrade)
    layers = []
    for key in LAYERS:
        state = armed.get(key) if key in armed else None
        if key == vetoed_by:
            verdict = "veto"
        elif state is False:
            verdict = "skip"
        else:
            verdict = "pass"
        row = {"key": key, "label": LAYER_LABELS[key], "armed": state, "verdict": verdict}
        reading = readings.get(key)
        if reading:
            row["readings"] = reading
        layers.append(row)
    return {"schema": GATES_SCHEMA, "vetoedBy": vetoed_by, "layers": layers}


def build_decision_row(scope: JournalScope, inp: RoundJournalInput,
                       snap: DeciderSnapshot, gates: dict | None) -> dict:
    """把一轮决策裁剪 + 序列化成可入队的记录（**三档采集粒度在此一处生效**）。

    Args:
        scope: 会话身份与本次采集的档位。
        inp: 决策轮的冻结聚合入参（只承载引用）。
        snap: 决策器的只读断面（研判 / 反思 / 档案 / 上下文断面 / 用量）。
        gates: `session._journal_gates()` 的产物（13 层三态 + `vetoedBy`）；`None` ⇒ 该列不落。

    Returns:
        `{"kind": "decision", "cols": {...}, "profile": {...}|None, "summaryProfile": {...}|None}`
        —— `cols` 的键与 `AiTradeDecisionLog` 的列名一一对应，且值已全部是标量或字符串。
    """
    limits = scope.limits
    now = int(time.time())
    fill_ref = build_fill_ref(inp)
    text_max = limits.effective_text_max
    shape = snap.request_shape if (limits.context_chain and snap.request_shape) else None
    payload_json, truncated = _payload_columns(inp.payload, limits)
    cols: dict = {
        "schema_version": SCHEMA_VERSION,
        "group_id": scope.group_id[:48],
        "run_id": int(scope.run_id or 0),
        "inst_id": scope.inst_id[:32],
        "bar": (scope.bar or "")[:8],
        "round_no": int(inp.round_no or 0),
        "ts": int(inp.ts or 0),
        "wall_ts": now,
        "trigger": str((inp.wake or {}).get("trigger") or "")[:16],
        # 模型说的（任何门禁之前）vs 门禁后真正执行的。两者不同 ⇒ 详情页并排显示并标注被哪一层改写。
        "action": str(_decision_attr(inp.stage1_decision, "action", "") or "")[:20],
        "final_action": str(_decision_attr(inp.decision, "action", "") or "")[:20],
        "size_fraction": _finite(_decision_attr(inp.decision, "size_fraction")),
        "confidence": _finite(_decision_attr(inp.decision, "confidence")),
        "ord_type": _short(_decision_attr(inp.decision, "ord_type"), 8),
        "limit_px": _finite(_decision_attr(inp.decision, "limit_px")),
        "degrade_reason": _short(inp.degrade, 48),
        "reflection_verdict": _short(inp.reflection_verdict, 16),
        # 512 而不是 `trades.reason` 的 128：实测 221 笔 LLM 平仓的 reason **全部**被截断到 64。
        "reason": str(_decision_attr(inp.decision, "reason", "") or "")[:512],
        # 「`executed` 说没成交」不等于「这一轮没有成交」（评审 RP1-2）：`_post_round` 里的
        # 利润保护会在同一轮再平一笔，而那笔**不在** `executed` 里。这一列的语义是
        # 「本轮到底有没有发生成交」，故取两者的并——否则「成交与订单」页会整条漏掉
        # 那些「模型什么都没做、轮末被保护平了一笔」的轮次，而那正是本仓实测唯一为正的
        # 确定性离场通道。
        "executed": bool(fill_ref.get("traded")) or bool(fill_ref.get("legs")),
        "cl_ord_id": _cl_ord_id(fill_ref),
        "ord_id": _ord_id(fill_ref),
        "fill_ref_json": dumps(fill_ref),
        "profile_sha256": str((snap.profile or {}).get("sha256") or "")[:64],
        # 取自出站那一行现算的断面，**不是**按 payload 重算的那一条（评审 RP0-1）。
        "prompt_sha256": str((snap.request_shape or {}).get("finalUserSha256") or "")[:64],
        "payload_json": payload_json,
        "payload_truncated": truncated,
        "prompt_text": (clip((snap.request_shape or {}).get("finalUserText"), text_max)
                        if limits.keeps_full_text else None),
        "output_json": (dumps(redact(_decision_input_wire(inp.stage1_decision)))
                        if limits.keeps_context else None),
        "context_ref_json": dumps(_context_ref(shape)) if shape else None,
        "context_turn_json": (dumps(redact(snap.context_turn))
                              if (limits.context_chain and snap.context_turn) else None),
        "analysis_text": clip(snap.analysis, text_max) if limits.keeps_context else None,
        "reflection_text": (clip(snap.reflection or inp.reflection_reason, text_max)
                            if limits.keeps_context else None),
        "thinking_text": clip(snap.thinking, text_max) if limits.keeps_full_text else None,
        # 门禁链是**结论类**字段，三档全落（§2.2.3 的表）——`minimal` 档也要答得出「谁拦的」。
        "gates_json": dumps(gates) if gates else None,
        "telemetry_json": dumps(build_telemetry(snap, limits)),
        "capture_tier": limits.tier[:10],
        "created_ts": now,
    }
    return {"kind": "decision", "cols": cols,
            "profile": _profile_record(snap.profile, scope),
            "summaryProfile": _summary_record(shape, scope)}


def _payload_columns(payload: dict | None, limits: CaptureLimits) -> tuple[str | None, bool]:
    """`payload_json` 与 `payload_truncated` 的**唯一**产出点。

    被截断的行在 SFT 导出中一律排除（截断的上下文重建不出原提示词），故这个布尔必须准确：
    宁可少导出一条样本，也绝不导出一条「我们以为模型看到了这个」的假样本。
    """
    if not limits.keeps_context or payload is None:
        return None, False
    text = dumps(redact(payload))
    if limits.payload_max_chars > 0 and len(text) > limits.payload_max_chars:
        return text[:limits.payload_max_chars], True
    return text, False


def _context_ref(shape: dict) -> dict:
    """`context_ref_json` 的内容：只留结构性字段，**不含**两段大文本（它们各有自己的落点）。"""
    return {key: shape.get(key) for key in
            ("contextTurns", "contextFirstRound", "summarySha256", "finalUserSha256",
             "attempt", "hintApplied", "extraInstruction") if key in shape}


def _profile_record(profile: dict | None, scope: JournalScope) -> dict | None:
    """系统提示词 + 工具 schema 的内容寻址档案（`kind="profile"`）。"""
    if not profile or not profile.get("sha256"):
        return None
    return {"sha256": profile["sha256"], "kind": "profile",
            "systemPrompt": profile.get("systemPrompt") or "",
            "toolsJson": profile.get("toolsJson") or "[]",
            "provider": profile.get("provider") or "", "model": profile.get("model") or "",
            "firstGroupId": scope.group_id}


def _summary_record(shape: dict | None, scope: JournalScope) -> dict | None:
    """滚动摘要种子的内容寻址档案（`kind="summary"`）。

    没有它，上下文链的第一条消息重建不出来——而摘要重置之后**每一条**样本的第一条消息都是它。
    """
    if not shape:
        return None
    sha = shape.get("summarySha256") or ""
    text = shape.get("summaryText") or ""
    if not sha or not text:
        return None
    return {"sha256": sha, "kind": "summary", "systemPrompt": text, "toolsJson": "[]",
            "provider": "", "model": "", "firstGroupId": scope.group_id}


def _decision_input_wire(decision) -> dict:
    """模型**原始**工具入参的 wire（与 `decider.decision_input_wire` 同一个函数，绝不另写一份）。

    延迟导入是刻意的：`journal` 包不得被 `decider` 反向依赖，而在模块顶层 import 会在
    `ai_auto/__init__` 的装配顺序上制造一条不必要的环。
    """
    from ..decider import decision_input_wire
    return decision_input_wire(decision)


def build_event_row(scope: JournalScope, ev: EventJournalInput) -> dict:
    """把一条事件裁剪 + 序列化成可入队的记录。"""
    now = int(time.time())
    cols = {
        "group_id": scope.group_id[:48],
        "run_id": int(scope.run_id or 0) or None,
        "inst_id": (ev.inst_id if ev.inst_id is not None else scope.inst_id)[:32],
        "ts": int(ev.ts or now),
        "wall_ts": now,
        "level": (ev.level or "info")[:8],
        "category": (ev.category or "")[:16],
        "code": (ev.code or "")[:48],
        "message": (ev.message or "")[:512],
        "detail_json": dumps(redact(ev.detail)) if ev.detail else None,
        "created_ts": now,
    }
    return {"kind": "event", "cols": cols, "level": cols["level"]}


def veto_layer(degrade: str | None) -> str | None:
    """本轮被哪一层否决（**唯一真源** `open_funnel.classify_block`，绝不另算）。"""
    return classify_block(degrade)


def _finite(value):
    """把一个可能是 None / 非数 / inf 的读数收敛成 `float | None`（**绝不补 0**）。"""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out and abs(out) != float("inf") else None


def _short(value, limit: int) -> str | None:
    return str(value)[:limit] if value else None
