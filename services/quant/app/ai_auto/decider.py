"""AI 决策器：多策略信号板 → 单一受约束交易决策 + 轮次滚动摘要式上下文压缩。

安全姿态（ai-auto-trading §2.3 / P1-5）：
- **唯一放行工具**：只把 `ToolSchema(submit_trade_decision)` 传给 `llm.stream(tools=[...])`，模型物理上
  没有别的工具可调（比 registry 围栏更强的边界）。
- **LLM 层无工具强制**：`stream` 无 `tool_choice`，「本轮必须且只能调用一次」只能由 prompt 引导；
  正确性由**安全回落**兜底——LLM 超时 / 只回文本无工具 / 产生多次（取首条有效）/ 入参非法 /
  `open_*` 缺 `sizeFraction` → 一律 `hold`，绝不在不确定时下单。
- **流事件契约（P1-5 校正）**：真实事件是 `tool_use_start`（name）→ 若干 `tool_use_args_delta`
  （partial_json 片段）→ `message_done`；决策器**自行**累积 partial_json 再 `json.loads`（参照
  `agent/loop._ToolAcc`，但独立实现、不复用交互式会话）。

上下文管理（§2.4 / P1-3）：每轮向 `_context` 追加 user+assistant(tool_use)+tool_result；每
`context_refresh_rounds` 轮以**完整 `_journal`（非可能被裁剪的 `_context`）**为素材 LLM 摘要 → 重置
`_context` 为「摘要种子」、清空 `_journal`、滚动累积上一段摘要。两次摘要间 `_context` 超硬上限则左裁。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import random
import re
import time
from copy import deepcopy
import uuid
from dataclasses import dataclass, replace

from ..agent.llm.base import (
    LLMClient,
    LlmMessage,
    TextBlock,
    ToolResultBlock,
    ToolSchema,
    ToolUseBlock,
)
from ..agent.llm.retry import backoff_delay_ms, normalize_usage
from ..config import Settings
from ..live_auto.resting import validate_limit_intent
from .hold_horizon_text import format_reflection_note
from .prompts import (
    DECISION_TOOL_NAME,
    REFLECTION_TOOL_DESCRIPTION,
    REFLECTION_TOOL_NAME,
    REFLECTION_TOOL_SCHEMA,
    ContrarianSchemaOptions,
    build_decision_prompt,
    build_advisory_evidence,
    build_reflection_evidence,
    build_decision_tool_description,
    build_decision_tool_schema,
    build_reflection_prompt,
    build_reflection_system_prompt,
    build_summary_prompt,
    build_summary_system_prompt,
)
from .reasoning_protocol import (
    CONTENT_FILTER_STOP_REASONS,
    TRUNCATION_STOP_REASONS,
    DepthProfile,
    build_review_protocol,
    count_stages,
)
from .prompts_en import RETRY_HINT_GENERIC_EN, RETRY_HINT_TRUNCATED_EN, build_reask_instruction
from .summary_health import SummaryHealth, health_wire
from .decision_memory import EVIDENCE_VERSION, RoundDecisionRecord
from .advisory_evidence import (
    attach_snapshot, bound_summary_evidence, evidence_hashes,
    evidence_identity, has_complete_evidence, unavailable_evidence,
)
from .trace import TraceEmit, _TraceCoalescer

logger = logging.getLogger(__name__)

_ACTIONS = ("open_long", "open_short", "close", "hold")
# 限价总闸开启时额外放行的动作（ai-trade-limit-orders §2.2/M-3）。`_ACTIONS` 与决策工具 schema 的
# `action.enum` 是**两个真源**，必须同改——否则新动作会被 `_map_decision` 判为 `invalid_action` 而
# 静默降级 hold，症状是「Agent 说撤单但什么也没发生」（§八.5）。总闸关闭时不放行，逐字回退 4 值。
_LIMIT_ACTIONS = ("cancel_order",)
# 会产生真实委托的动作（ai-trade-limit-orders-fix F-2）：限价解析/价带门禁**只**对它们执行——
# hold/cancel_order 无委托语义，LLM 残留的 orderType 字段绝不能把撤单/观望整轮降级吞掉。
# 单一真源：order_plan.decision_to_order_plan 同引本常量判「是否产生 OrderPlan」。
ORDERING_ACTIONS = ("open_long", "open_short", "close")

# 本轮研判思维链全文的硬上限（ai-trade-reasoning-chain §2.5）：`_last_analysis` 只经 agent_trace 流与
# _traces(≤20) 承载给 UI，超出即截断；进 journal / decision 事件的是调用方派生的有界摘录（≤400，RC-2/3）。
# [deep-reasoning-chain §3.3.4] 4000 → 20000（5×）：12 阶段协议下一份完整研判约 6~9k 字符，
# 4000 会在 S7 附近把后半截思维链整段丢掉——而丢掉的恰好是证伪（S11）与结论（S12）两段。
_ANALYSIS_MAX_CHARS = 20000
# 滚动摘要种子的字符兜底（新增，§3.3.4）：摘要客户端预算 1536 token，本常量是第二道闸，
# 防一次异常长的摘要撑爆 `_context`（摘要是每轮都会被重读的那一段）。
_SUMMARY_MAX_CHARS = 3000
# 中文证据摘要的落痕上限（与工具 schema 的 `maxLength: 400` 同数；schema 是软引导、本常量是硬约束）。
_EVIDENCE_DIGEST_MAX_CHARS = 400
# 逆势证据串的截断上限（与 schema 的 `maxLength` 同源，见 `prompts` 的同名常量）。
_CONTRARIAN_EVIDENCE_MAX_CHARS = 600
# 离场证据的落痕上限（与工具 schema 的 `maxLength: 300` 同数；schema 是软引导、本常量是硬约束）。
_EXIT_EVIDENCE_MAX_CHARS = 300
# 情报引用编号的形态（intel-closed-loop 轴 A）。**两位定长**，与语料的 `D[0-9]{1,2}` 和核查候选的
# `C[0-9]{1,2}` 互斥；判据的唯一真源在 `intel/citation.REF_RE`，此处只是同一条正则的解析侧复刻
# （`decider` 不 import `intel` 包：那会把一个可选特性变成决策器的模块级依赖）。
_INTEL_REF_RE = re.compile(r"^I[0-9]{2}$")

# 「可重试」degrade 原因白名单（瞬时 LLM 失败 / 未产出有效决策）；其余（low_confidence 等）为终态决策，
# 绝不重试（ai-trade-decision-llm-retry §4.1）。
# ai-trade-limit-orders §2.6：三个限价类降级原因同属「LLM 的瞬时失误」——重试一次极可能给出合理价，
# 比直接降级 hold 更有价值，故一并归入可重试集合（R-15：门禁必须在 _map_decision 内，重试循环只认
# 它返回的 degrade）。
# ⚠️ ai-trade-base-strategy-ma-band P1-1：`base_rule_*` 三个码（`base_rule_ma_band(...)` /
# `base_rule_ma_band_direction(...)` / `base_rule_band_unavailable`）为**确定性终态**，严禁加入本集合
# ——同一轮内价格与 MA 通道都是常量，重试必然被同样否决，每次重试白烧一次 LLM 调用与最多 60s 超时
# 预算。基础策略门禁因此刻意落在 `decide()` **之后**（session/backtest 调 base_rules 纯核心），
# 而不是 `_map_decision` 内。冻结断言见 tests/test_ai_trade_decider.py。
# ⚠️ deep-reasoning-chain §3.3.3：`reasoning_truncated`（输出预算耗尽在工具调用之前）**入**本
# 集合——带压缩提示重试一次极可能成功；而 `content_filtered`（内容安全拦截）**绝不入**：同一份
# prompt 重试只会再被拦一次，白烧一次预算与一次超时。
_RETRYABLE_DEGRADE_REASONS = frozenset(
    {"decision_timeout", "llm_error", "no_tool_call", "invalid_action", "missing_size",
     "missing_limit_price", "limit_price_out_of_band", "invalid_order_type",
     "reasoning_truncated"})
# 每次重试前退避的抖动上限（ms），镜像 agent/llm/retry.py 的 _JITTER_MS 语义（§4.3）。
_RETRY_JITTER_MS = 250

# 反思专属「可重试」原因白名单（瞬时反思失败）；`reflection_abort` **不在集内**——它是模型的真实终态
# 裁决，零重试（对齐 decider 对 low_confidence 的处理，ai-trade-pre-trade-reflection §2.4/RV-5）。
_RETRYABLE_REFLECTION_REASONS = frozenset(
    {"reflection_timeout", "reflection_llm_error", "reflection_no_tool", "reflection_invalid"})

# error 可由连接建立前的传输失败产生；只有模型响应能证明收到本轮材料。
_EVIDENCE_RESPONSE_KINDS = frozenset({
    "text_delta", "thinking_delta", "tool_use_start", "tool_use_args_delta",
    "tool_use_end", "message_done",
})


def _is_retryable_degrade(reason: str | None) -> bool:
    """该 degrade 原因是否属于瞬时/无有效产出（可重试）；None 与 low_confidence 返回 False。"""
    return reason in _RETRYABLE_DEGRADE_REASONS


def _is_retryable_reflection(reason: str | None) -> bool:
    """该反思 fail-closed 原因是否可重试；None 与 reflection_abort（终态裁决）返回 False（§2.4/RV-5）。"""
    return reason in _RETRYABLE_REFLECTION_REASONS


def _retry_hint_suffix(reason: str, *, english: bool = False) -> str:
    """重试时追加进瞬时 user_msg 的一次性纠偏提示（不落 _context/_journal，§4.5）。

    `english`（keyword-only，默认 False ⇒ 与本特性合入前**逐字节相同**）：深度档生效时整段换
    英文，与英文指令层同语言。截断码有**专属**文案——它要求压缩 S1–S9 而不是「再调一次工具」，
    因为上一次失败的原因恰恰是话说太长（§3.3.3）。
    """
    if not english:
        return (f"\n\n【系统提示】上一次未产出有效决策（{reason}）。"
                f"请务必**只调用一次** submit_trade_decision 并给出完整合法入参。")
    if reason == "reasoning_truncated":
        return RETRY_HINT_TRUNCATED_EN
    return RETRY_HINT_GENERIC_EN.format(reason=reason)


@dataclass(frozen=True)
class _StreamOutcome:
    """一次决策流的全部产出：工具入参 + provider 归一的收尾读数（§3.3.3）。

    合入前 `_stream_decision` 只返回 `dict | None`，`message_done` 携带的 `stop_reason` 与
    `usage` 被解析出来又原地丢掉——于是「输出预算耗尽在工具调用之前」表现为 `no_tool_call`
    → 重试 → 再次耗尽 → 降级 `hold`，日志与界面上**看不出**真实原因（根因 C-4）。
    """

    raw: dict | None
    stop_reason: str | None = None
    usage: dict | None = None


def _sha256(text: str) -> str:
    """计算实际模型请求的内容哈希。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ReasoningMeta:
    """逐轮研判元数据（§3.4.1）：深度、篇幅、阶段数、截断、追问、墙钟闸与 token 用量。

    经 `AiTradeDecider.last_reasoning` 只读取用（与 `last_analysis` 同款范式），使 `decide()`
    的签名契约零改动。`off` 档 / 未装配深度档 ⇒ 恒 `None` ⇒ wire 上整键缺席。
    """

    depth: str
    chars: int
    stages: int
    truncated: bool
    reasked: bool
    reask_adopted: bool
    deadline_hit: bool
    input_tokens: int | None = None
    output_tokens: int | None = None
    reflection_chars: int | None = None
    reflection_stages: int | None = None

    def wire(self) -> dict:
        """`result` 轨迹事件与 `aiStats` 共用的**唯一**一份 camelCase 装配（杜绝逐键手抄漂移）。"""
        return {"depth": self.depth, "chars": self.chars, "stages": self.stages,
                "truncated": self.truncated, "reasked": self.reasked,
                "reaskAdopted": self.reask_adopted, "deadlineHit": self.deadline_hit,
                "inputTokens": self.input_tokens, "outputTokens": self.output_tokens,
                "reflectionChars": self.reflection_chars,
                "reflectionStages": self.reflection_stages}


@dataclass
class TradeDecision:
    """AI 本轮决策（受约束）：action + 可选仓位占比 / 置信度 + 理由 + 委托方式。

    `ord_type` / `limit_px` 为末位带默认值的字段（ai-trade-limit-orders §2.2）——全部既有的
    `TradeDecision(action, size, conf, reason)` 位置构造与四元组解包**零改动**，默认即市价。
    """

    action: str                    # open_long | open_short | close | hold | cancel_order
    size_fraction: float | None    # 预算名义占比（open_* 用；close/hold/cancel_order 为 None）
    confidence: float | None
    reason: str
    ord_type: str = "market"       # market | limit（仅 open_* / close 有意义）
    limit_px: float | None = None  # 已过价带门禁的委托价；market 恒 None
    # 承诺退出线（ai-trade-profit-protection-commitment C1）：仅 `action=hold` 携带的合法值有意义
    # （非法值 / 非 hold 恒 None，绝不因此降级 hold）。末位带默认值 ⇒ 既有位置/六元构造零改动。
    hold_exit_px: float | None = None
    # 离场确信度与证据（exit-priority §6.1）：仅 `action=close` 携带的合法值有意义。同款末位带
    # 默认值 ⇒ 既有全部位置构造零改动；值域外 / 类型错**一律忽略、绝不据它降级**（沿用 orderType
    # 的 F-2 先例：一个读不懂的确信度不该把最安全的动作降级）。
    exit_conviction: str | None = None
    exit_evidence: str | None = None
    # 面向操作员的中文证据摘要（deep-reasoning-chain §3.1.7）：同款末位带默认值 ⇒ 既有全部位置
    # 构造与解包零改动。**纯呈现物**——缺席 / 非字符串 / 超长 / 写成英文四种情形一律不产生
    # degrade、不改任何交易语义（用例 D-11 / D-12）。
    evidence_digest: str | None = None
    # 逆势开仓的证据（directional-symmetry A-3）：仅 `open_long` / `open_short` 携带的合法值有
    # 意义。同款末位带默认值 ⇒ 既有全部位置构造与元组解包零改动。
    #
    # ⚠️ **刻意不复用 `evidence_digest`**：那个字段上面白纸黑字冻结着「纯呈现物、四种情形一律
    # 不产生 degrade、不改任何交易语义」，把它变成承重字段是一次静默的契约破坏。本字段镜像
    # `exit_evidence` 的既有先例另开一个专用字段。
    #
    # 值域外 / 类型错**一律忽略、绝不据它降级**（沿用 `orderType` 的 F-2 先例）：它的缺席只会
    # 让逆势通路不可用，而那本就是旧行为。
    contrarian_evidence: str | None = None
    # 本轮实际采信的外部情报编号（intel-closed-loop 轴 A）。同款末位带默认值 ⇒ 既有全部位置构造
    # 与元组解包零改动。**纯观测量**：它不产生任何 degrade、不改写任何其它字段、不参与任何仓位
    # 或风控判定（承诺二）。编号是否真实存在由服务端的 `intel/citation.resolve_citations` 裁决。
    intel_refs: tuple[str, ...] = ()


def _add_tokens(prev, current):
    """两个可空 token 读数相加；两者皆缺 ⇒ `None`（「没被告知」不该被写成 0）。"""
    if prev is None and current is None:
        return None
    return int(prev or 0) + int(current or 0)


def _hold(reason: str) -> TradeDecision:
    return TradeDecision(action="hold", size_fraction=None, confidence=None, reason=reason)


def _fail_closed(reason: str) -> "ReflectionVerdict":
    """反思瞬时失败/非法裁决的安全回落：等价 abort（本轮不下单），`degrade_reason` 为可重试原因（§2.2）。"""
    return ReflectionVerdict(verdict="abort", adjusted_size_fraction=None, confidence=None,
                             reason=reason, degrade_reason=reason, ran=True)


def _clamp01(value) -> float | None:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(n, 1.0))


def _finite_px(value) -> float | None:
    """把工具入参里的报价解析为**正有限**浮点，否则 None（C1 承诺退出线解析用）。

    与 `orderType/limitPrice` 对非法值「一律忽略、绝不据它降级」同理（`decider.py` F-2 先例）：
    一条读不懂的退出线不该把最安全的 hold 动作降级。
    """
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if math.isfinite(n) and n > 0 else None


@dataclass
class ReflectionVerdict:
    """下单前二次反思裁决（ai-trade-pre-trade-reflection §5.1）：confirm/abort + 可选收缩仓位 + 理由。"""

    verdict: str                          # "confirm" | "abort"
    adjusted_size_fraction: float | None  # 仅 confirm 且模型给了收缩值时有值（原始 [0,1] 提议，量化在 apply）
    confidence: float | None
    reason: str
    degrade_reason: str | None            # 非空 = fail-closed（等价 abort），如 reflection_timeout；abort 为 None
    ran: bool                             # True=真跑过反思；False=关闭/未触发（供 apply_reflection 直通 + 轨迹判定）


class AiTradeDecider:
    """持 LLMClient + 滚动上下文的决策器；每轮 `decide` 出决策，`commit_round` 记录并按需摘要。"""

    def __init__(self, llm: LLMClient, system_prompt: str, *,
                 context_refresh_rounds: int, settings: Settings,
                 position_step_pct: float = 0.0, reflection_enabled: bool = False,
                 allow_limit: bool = False, allow_hold_exit: bool = False,
                 allow_exit_priority: bool = False,
                 reflection_llm: LLMClient | None = None,
                 summary_llm: LLMClient | None = None,
                 allow_reasoning_digest: bool = False,
                 contrarian_schema: ContrarianSchemaOptions | None = None,
                 depth_profile: DepthProfile | None = None,
                 allow_intel_refs: bool = False, intel_max_refs: int = 8):
        self._llm = llm
        # 三份预算（deep-reasoning-chain §3.3.1）：默认 `None` ⇒ 三者同为 `llm` ⇒ 现有全部直接
        # 构造（回测早期路径、32 个假客户端单测、`decider.llm` 蒸馏出口）逐字零回归（用例 G-5）。
        self._reflection_llm = reflection_llm or llm
        self._summary_llm = summary_llm or llm
        # 本特性在决策器上的**唯一**开关：超时、轮级墙钟闸、尝试次数上限、追问阈、以及反思侧的
        # 复核协议文本全部由它派生。`None`（既有直接构造）⇒ 全部逐字回退到今日的 settings 读法。
        self._depth = depth_profile
        self.system_prompt = system_prompt
        self.context_refresh_rounds = max(1, int(context_refresh_rounds))
        self.settings = settings
        # 仓位操作档位步长（fraction；0=连续，保证所有既有直接构造 AiTradeDecider(...) 的位置零回归）。
        self._position_step = max(0.0, float(position_step_pct))
        # 限价委托总闸（默认 **False**，P1-3）：默认 True 会让既有恒等断言
        # `build_decision_tool_schema(0.0) is DECISION_TOOL_SCHEMA` 当场失败。回测恒为 False（§2.10：
        # SimBroker 每单必成，放任限价进回测会系统性高估）；生产装配从 settings 显式打开。
        self.allow_limit = bool(allow_limit)
        # 承诺退出线总闸（默认 **False**，镜像 allow_limit）：默认 False 是硬约束——它让
        # `build_decision_tool_schema(0.0) is DECISION_TOOL_SCHEMA` 恒等断言原样通过（§2.1.2）。生产
        # 装配点从 `profit_rule.commit_enabled` 派生（manager/backtest 各自决定）。
        self.allow_hold_exit = bool(allow_hold_exit)
        # 离场优先通道总闸（默认 **False**，镜像 allow_limit / allow_hold_exit）：默认 False 是硬
        # 约束——它让 `build_decision_tool_schema(0.0) is DECISION_TOOL_SCHEMA` 恒等断言原样通过。
        self.allow_exit_priority = bool(allow_exit_priority)
        # 中文证据摘要总闸（默认 **False**，镜像上面三个总闸）：默认 False 是硬约束——它让
        # `build_decision_tool_schema(0.0) is DECISION_TOOL_SCHEMA` 恒等断言原样通过（G-2）。
        self.allow_reasoning_digest = bool(allow_reasoning_digest)
        # 逆势证据总闸（默认 **None**，镜像上面四个总闸）：默认缺席是硬约束——它让
        # `build_decision_tool_schema(0.0) is DECISION_TOOL_SCHEMA` 恒等断言原样通过。
        # 生产装配点从会话的 `ConsensusRule` 派生（manager / backtest 各自决定）。
        self._contrarian_schema = contrarian_schema
        self.allow_contrarian_evidence = contrarian_schema is not None
        # 情报引用总闸（默认 **False**，镜像上面五个总闸）：默认 False 是硬约束——它让
        # `build_decision_tool_schema(0.0) is DECISION_TOOL_SCHEMA` 恒等断言原样通过（G-1）。
        # 生产装配点从 `settings.ai_trade_intel_cite_enabled` 派生（manager 决定；回测恒关，R-12）。
        self.allow_intel_refs = bool(allow_intel_refs)
        self.intel_max_refs = max(1, int(intel_max_refs))
        # 下单前二次反思总闸（默认关；直接构造该决策器的既有测试/回测 → reflect 无副作用直通、逐字零回归。
        # 生产装配 manager/backtest_manager 从 req.settings.ai_trade_reflection_enabled 显式打开，§2.3）。
        self.reflection_enabled = bool(reflection_enabled)
        # F-3：价带比例文案由 settings 派生（运行时唯一真源），prompt 与本地门禁恒同一口径。
        band_pct = settings.ai_trade_limit_price_max_deviation_pct
        # 工具描述的语言与指令层同源：深度档生效 ⇒ 整体英文；否则恒等返回今日中文常量（G-3）。
        tool_lang = "en" if self._depth_on else "zh"
        self._tool = ToolSchema(
            name=DECISION_TOOL_NAME,
            description=build_decision_tool_description(self.allow_limit,
                                                        limit_band_pct=band_pct,
                                                        allow_hold_exit=self.allow_hold_exit,
                                                        allow_exit_priority=self.allow_exit_priority,
                                                        lang=tool_lang),
            input_schema=build_decision_tool_schema(
                self._position_step,
                allow_limit=self.allow_limit,
                limit_band_pct=band_pct,
                allow_hold_exit=self.allow_hold_exit,
                allow_exit_priority=self.allow_exit_priority,
                allow_reasoning_digest=self.allow_reasoning_digest,
                contrarian=self._contrarian_schema,
                allow_intel_refs=self.allow_intel_refs,
                intel_max_refs=self.intel_max_refs))
        # 反思唯一放行工具（镜像决策工具的「唯一放行」范式，安全由回落兜底，§2.2）。
        self._reflection_tool = ToolSchema(name=REFLECTION_TOOL_NAME,
                                           description=REFLECTION_TOOL_DESCRIPTION,
                                           input_schema=REFLECTION_TOOL_SCHEMA)
        self._context: list[LlmMessage] = []
        self._summary: str = ""
        self._legacy_narrative = False
        self._summary_as_of_ts: float | None = None
        self._summary_evidence_version: int | None = None
        self._round_decision_facts: dict | None = None
        self._round_advisory_snapshot: dict | None = None
        self._round_reflection_snapshot: dict | None = None
        self._round_identity: dict | None = None
        self._round_evidence_sent = False
        self._journal: list[dict] = []
        self._rounds_since_summary = 0
        self._last_round = 0
        # 滚动摘要健康度（tradability-and-open-funnel 轴 D）：连续失败 / 累计失败 / 非空诊断串 /
        # 退避轮数。判据与文案在纯核心 `summary_health`，本类只持状态并在两个分支上喂数。
        self._summary_health = SummaryHealth()
        self._summary_skip_rounds = 0     # 退避：本次成功前额外要跳过的到期点轮数
        self._summary_due = False         # D-5：到期了但被 `defer_summary` 推迟到轮末再跑
        self._last_analysis: str = ""      # 本轮研判思维链全文（R3；每次尝试入口清零，RC-1，§4.2/R-6）
        # 反思复核推理全文（补根因 C-3）：合入前完全不留存（只作为 trace delta 扇出），
        # 事后无法度量、无法沉淀。入口清零、流正常结束才落、上限同 `_ANALYSIS_MAX_CHARS`。
        self._last_reflection_analysis: str = ""
        # 本轮研判元数据（§3.4.1）；`off` 档 / 未装配深度档恒 None ⇒ wire 整键缺席。
        self._last_reasoning: ReasoningMeta | None = None
        self._last_stop_reason: str | None = None   # 本轮最后一次尝试的收尾码（provider 原样）
        self._last_usage: dict | None = None        # 归一后的 {inputTokens, outputTokens}

    # ---------- 只读状态（供 status / snapshot）----------
    @property
    def llm(self):
        """LLM 客户端的**只读**出口（experience-context-graph §4.9 的会话末蒸馏用）。

        `self._llm` 是私有的，「从外部持一个引用」拿不到——而蒸馏必须跑在 manager 侧的后台任务里
        （绝不在 `stop()` 的 await 链上，评审 P0-1），那里只拿得到会话与决策器对象。纯读，
        不改变决策器的任何状态。
        """
        return self._llm

    @property
    def summary(self) -> str | None:
        return self._summary or None

    @property
    def rounds_since_summary(self) -> int:
        return self._rounds_since_summary

    @property
    def last_analysis(self) -> str:
        """本轮研判思维链全文（供 session 派生有界摘录入 journal/decision 事件）；降级轮为空串。"""
        return self._last_analysis

    @property
    def last_reflection_analysis(self) -> str:
        """本轮反思复核推理全文（补 C-3）；未反思 / 反思失败的轮次为空串。"""
        return self._last_reflection_analysis

    @property
    def last_reasoning(self) -> ReasoningMeta | None:
        """本轮研判元数据（§3.4.1）；`off` 档 / 未装配深度档恒 `None`（wire 整键缺席）。"""
        return self._last_reasoning

    @property
    def depth_name(self) -> str:
        """本会话生效的研判深度档名；未装配 / `off` 档恒空串。

        payload 的 `reasoningDepth` **加法键**取自这里（单一真源）：prompts 的收尾任务指令、
        session 的 wire 与 `aiStats` 三处读的都是同一个数，杜绝「决策器走英文档、prompt 走中文
        档」这类不会报错的漂移。
        """
        return self._depth.name if self._depth_on else ""

    @property
    def _depth_on(self) -> bool:
        """深度档是否真的生效（未装配 / `off` 档一律 False ⇒ 全链路逐字节回退）。"""
        return self._depth is not None and self._depth.enabled

    @property
    def _decision_timeout_s(self) -> float:
        """单次决策尝试超时：深度档生效取档位值，否则**逐字回退**到 settings（今日行为）。"""
        return (self._depth.decision_timeout_s if self._depth_on
                else float(self.settings.ai_trade_decision_timeout_s))

    @property
    def _reflection_timeout_s(self) -> float:
        """单次反思尝试超时：同款——深度档取档位值，否则逐字回退到 settings。"""
        return (self._depth.reflection_timeout_s if self._depth_on
                else float(self.settings.ai_trade_reflection_timeout_s))

    @property
    def position_step(self) -> float:
        """开仓档位步长（fraction，只读）；供 session 反思收缩量化吸附到合法档位（§2.3/RV-4）。"""
        return self._position_step

    # ---------- 决策 ----------
    async def decide(self, payload: dict, *, emit: TraceEmit | None = None
                     ) -> tuple[TradeDecision, str | None]:
        """出本轮决策；可重试失败先带退避重试，穷尽后才降级 hold。签名不变（RC/RV 契约，§4.2）。

        返回 `(decision, degrade_reason)`——degrade_reason 非 None 表示安全回落 hold。可选 `emit`
        （由 session 注入）承接决策器流式认知（分析 / 思考 / 工具起始 / 重试面包屑）经合并后扇出；
        默认 `None` 保证回测 / 单测等既有位置调用零改动、零回归（合并器全程空转）。

        瞬时失败（超时 / LLM 错误 / 未产出有效决策）先带有界指数退避重试至多
        `ai_trade_decision_max_retries` 次，全部失败才降级；模型真实终态决策（合法动作 / 刻意 hold /
        low_confidence 门禁）零重试、行为逐字不变；`asyncio.CancelledError` 立即向上抛（绝不吞、绝不降级）。
        `max_retries=0` 逐字回退旧行为「首次失败即降级」。

        深度档生效时**额外**叠加两道只收敛尝试次数、**绝不改变任何安全回落语义**的闸（§3.3.5）：
        尝试次数闸 `max_attempts_cap` 与轮级墙钟闸 `round_deadline_s`（全部尝试 + 追问共享）。
        闸耗尽**不是新的降级码**：沿用当前已有的 `degrade`（多半是 `decision_timeout`），只是不再
        发起新尝试；读数经 `last_reasoning.deadline_hit` 在界面上明示，绝不静默。
        """
        self._reset_round_reasoning()
        payload = deepcopy(payload)
        payload = self._freeze_round_evidence(payload)
        try:
            return await self._decide_frozen(payload, emit)
        except asyncio.CancelledError:
            self._round_evidence_sent = False
            raise

    async def _decide_frozen(self, payload: dict, emit: TraceEmit | None
                             ) -> tuple[TradeDecision, str | None]:
        """沿用已冻结输入执行原有重试和深度追问控制流。"""
        deadline = self._round_deadline()
        max_retries = self._effective_max_retries()
        decision, degrade = await self._attempt_decision(payload, emit)   # 首次：hint=None
        attempt = 0
        deadline_hit = False
        while (degrade is not None and _is_retryable_degrade(degrade)
               and attempt < max_retries):
            if self._deadline_exhausted(deadline):
                deadline_hit = True                # 只停止追加尝试，绝不中断在飞的那一次
                break
            attempt += 1
            await self._emit_retry(emit, attempt, max_retries, degrade)   # 面包屑 + 前端本轮累积重置
            await self._retry_backoff(attempt)                            # 可被 cancel 中断
            decision, degrade = await self._attempt_decision(payload, emit, hint=degrade)
        decision, degrade, reask = await self._maybe_reask(
            payload, emit, decision=decision, degrade=degrade, deadline=deadline)
        self._last_reasoning = self._build_reasoning_meta(
            reasked=reask[0], reask_adopted=reask[1],
            deadline_hit=deadline_hit or reask[2])
        return decision, degrade

    def _reset_round_reasoning(self) -> None:
        """轮入口清零本轮遥测（与 `_last_analysis` 的入口清零同款范式，RC-1）。"""
        self._last_reasoning = None
        self._last_stop_reason = None
        self._last_usage = None
        self._last_reflection_analysis = ""
        self._round_advisory_snapshot = None
        self._round_reflection_snapshot = None
        self._round_decision_facts = None
        self._round_identity = None
        self._round_evidence_sent = False

    def _effective_max_retries(self) -> int:
        """本轮**有效**重试次数 = min(settings 的重试数, 档位尝试上限 − 1)；未封顶即逐字回退。"""
        max_retries = max(0, int(self.settings.ai_trade_decision_max_retries))
        cap = self._depth.max_attempts_cap if self._depth_on else 0
        return max_retries if cap <= 0 else max(0, min(max_retries, cap - 1))

    def _round_deadline(self) -> float | None:
        """轮级墙钟闸的到期时刻（单调钟）；未设闸 ⇒ `None`。

        必须用 `monotonic()` 而非 `time.time()`：本仓库已经为「双时钟」栽过一次
        （docs/plans/ai-trade-wake-aware-context），墙钟闸尤其不能被系统时钟跳变打翻。
        """
        if not self._depth_on or self._depth.round_deadline_s <= 0:
            return None
        return time.monotonic() + float(self._depth.round_deadline_s)

    @staticmethod
    def _deadline_exhausted(deadline: float | None) -> bool:
        return deadline is not None and time.monotonic() >= deadline

    def _build_reasoning_meta(self, *, reasked: bool, reask_adopted: bool,
                              deadline_hit: bool) -> ReasoningMeta | None:
        """装配本轮 `ReasoningMeta`；深度档未生效 ⇒ `None`（wire 整键缺席，逐字回退）。"""
        if not self._depth_on:
            return None
        usage = self._last_usage or {}
        return ReasoningMeta(
            depth=self._depth.name,
            chars=len(self._last_analysis),
            stages=count_stages(self._last_analysis, "S"),
            truncated=self._last_stop_reason in TRUNCATION_STOP_REASONS,
            reasked=reasked, reask_adopted=reask_adopted, deadline_hit=deadline_hit,
            input_tokens=usage.get("inputTokens"), output_tokens=usage.get("outputTokens"))

    def _wants_reask(self, degrade: str | None) -> bool:
        """本轮**是否需要**追问（§3.4.2）：已经拿到一个真实决策，但研判确实过浅。

        刻意与「墙钟预算还够不够」分开判：两者合成一个谓词会让「想追问但没预算」变成一条
        静默路径，而那正是 `deadline_hit` 这个读数存在的理由。
        """
        if not self._depth_on or degrade is not None:
            return False
        if self._depth.min_analysis_chars <= 0:
            return False
        if int(self.settings.ai_trade_reasoning_reask_max) <= 0:
            return False
        return len(self._last_analysis) < self._depth.min_analysis_chars

    async def _maybe_reask(self, payload: dict, emit: TraceEmit | None, *,
                           decision: TradeDecision, degrade: str | None,
                           deadline: float | None
                           ) -> tuple[TradeDecision, str | None, tuple[bool, bool, bool]]:
        """至多一次的深度追问：**只提质量，永不降级**（§3.4.2 / 用例 D-4）。

        采纳规则（安全关键）：追问成功且更深 ⇒ 采纳；追问失败 / 更浅 / 产生 degrade ⇒ **原样
        保留第一次的决策与第一次的研判全文**。任何情况下都不会因为「研判太浅」而把决策改成
        `hold`——浅研判是质量问题，不是安全问题；把它接到降级链上会造出「模型话少 ⇒ 全部降级
        观望」的新吸收态（本仓库为「反思致永不开单」已经付过一次学费，R-4）。

        Returns:
            `(decision, degrade, (reasked, reask_adopted, deadline_hit))`。
        """
        if not self._wants_reask(degrade):
            return decision, degrade, (False, False, False)
        if self._deadline_exhausted(deadline):
            # 想追问、但轮级墙钟预算已经耗尽 ⇒ 不再发起，并**在界面上明示**（`deadlineHit`）。
            # 不明示就成了新的静默行为——而本特性的另一半正是「诚实」。
            return decision, degrade, (False, False, True)
        first_analysis = self._last_analysis
        first_stop_reason = self._last_stop_reason
        instruction = build_reask_instruction(
            stages=count_stages(first_analysis, "S"), chars=len(first_analysis),
            words_per_stage=self._depth.words_per_stage)
        # 流式产出走**独立相位** `reask`：前端对 `analysis` 是纯追加归约、唯一清零点是 `retry`
        # 相位，沿用同一相位会把 S1–S12 追加成两遍（导轨读出 24/12）；改用 emit=None 则界面上
        # 留着的是被丢弃的浅研判。两种写法都让「界面上的研判」与「实际生效的决策」不是同一份。
        deeper, deeper_degrade = await self._attempt_decision(
            payload, emit, extra_instruction=instruction, text_phase="reask")
        if deeper_degrade is None and len(self._last_analysis) > len(first_analysis):
            return deeper, None, (True, True, False)
        # 未采纳：研判全文与收尾码原样回到第一次那一份（`truncated` 必须描述**被采纳**的那次
        # 尝试；描述一次被丢弃的尝试是另一种形态的不诚实）。usage **不**回滚——那两次调用是
        # 真的都花了钱。
        self._last_analysis = first_analysis
        self._last_stop_reason = first_stop_reason
        # 第三元恒 False：追问**跑过了**，墙钟没有阻止任何事——把它报成 deadline_hit 会让界面
        # 上的「本轮超时预算用尽」在一次纯质量拒收上误报。
        return decision, degrade, (True, False, False)

    async def _attempt_decision(self, payload: dict, emit: TraceEmit | None = None,
                                *, hint: str | None = None,
                                extra_instruction: str | None = None,
                                text_phase: str = "analysis"
                                ) -> tuple[TradeDecision, str | None]:
        """单次决策尝试（= 今日 decide 主体）：入口清零 last_analysis → 流式研判 → 映射。

        `hint`（keyword-only，默认 None → 与今日首次尝试逐字一致）：上一次失败的 degrade 原因，
        仅追加进**当次瞬时** user_msg（§4.5），绝不落 `_context/_journal`——上下文零污染。

        `extra_instruction`（keyword-only，默认 None → 逐字一致）：浅研判追问提示（§3.4.2）。
        **刻意不复用 `hint`**：`hint` 的值域是 degrade 码、是 `_retry_hint_suffix` 的查表键，
        塞一段自由文本进去会让那张表多出一条永远不匹配的分支（用例 D-5c）。

        `text_phase`（keyword-only，默认 `"analysis"`）：本次尝试的文本增量落在哪个轨迹相位。
        追问走 `"reask"`，使首次研判在轨迹流上既不被覆盖也不被追加污染（用例 D-5b）。
        """
        self._last_analysis = ""     # RC-1（跨尝试版）：每次尝试入口清零，重试不残留上一次尝试的研判
        prompt_text = build_decision_prompt(payload)
        if hint:
            # §4.5：仅当次重试的一次性纠偏提示（首次不触发）。深度档生效时整段英文，与指令层同语言。
            prompt_text += _retry_hint_suffix(hint, english=self._depth_on)
        if extra_instruction:
            prompt_text += extra_instruction
        user_msg = LlmMessage("user", [TextBlock(prompt_text)])
        try:
            outcome = await asyncio.wait_for(
                self._stream_decision(user_msg, emit, text_phase=text_phase),
                timeout=self._decision_timeout_s)
        except asyncio.TimeoutError:
            return _hold("decision_timeout"), "decision_timeout"
        except asyncio.CancelledError:
            self._round_evidence_sent = False
            raise                    # 会话停止：绝不吞、绝不降级，向上抛（BaseException，不入下方 except）
        except Exception as exc:  # noqa: BLE001 —— 任何 LLM/网络失败回落 hold（绝不在不确定时下单）
            logger.warning("ai-trade decide attempt failed: %s", exc)
            return _hold("llm_error"), "llm_error"
        self._note_stream_outcome(outcome)
        # §2.6：last_px 取自 payload（`_build_decision_payload` 已含 lastPx）——与喂给 prompt 的价同源。
        decision, degrade = self._map_decision(outcome.raw, last_px=payload.get("lastPx"))
        return decision, self._honest_no_tool_reason(degrade, outcome.stop_reason)

    def _note_stream_outcome(self, outcome: _StreamOutcome) -> None:
        """把本次尝试的收尾读数记进轮级遥测（`usage` 归一复用 agent 层的同一份口径）。

        **usage 在一轮内累加**：一轮可能有重试与一次追问，每一次都真的花了钱。只留最后一次
        等于系统性低估 token 成本，而成本可见（G-8）正是本特性对 R-3（token 成本 5 倍）的
        主要对价——一个少报的数字比没有数字更坏。
        """
        self._last_stop_reason = outcome.stop_reason
        usage = normalize_usage(getattr(self._llm, "provider", "anthropic"), outcome.usage)
        if usage is None:
            return
        prev = self._last_usage or {}
        self._last_usage = {
            "inputTokens": _add_tokens(prev.get("inputTokens"), usage.get("inputTokens")),
            "outputTokens": _add_tokens(prev.get("outputTokens"), usage.get("outputTokens"))}

    @staticmethod
    def _honest_no_tool_reason(degrade: str | None, stop_reason: str | None) -> str | None:
        """把 `no_tool_call` 细分成「预算耗尽」与「内容被拦」两个诚实的码（§3.3.3 / 根因 C-4）。

        判据取 `reasoning_protocol.TRUNCATION_STOP_REASONS` 这一**唯一真源**：Anthropic 报
        `max_tokens`、OpenAI 标准把 `finish_reason="length"` 塞进同一个字段。任何写死一家口径
        的判据都会让另外半个 provider 家族原样退回「看不出原因」的形态（用例 D-1b）。
        """
        if degrade != "no_tool_call" or not stop_reason:
            return degrade
        if stop_reason in TRUNCATION_STOP_REASONS:
            return "reasoning_truncated"
        if stop_reason in CONTENT_FILTER_STOP_REASONS:
            return "content_filtered"
        return degrade

    async def _retry_backoff(self, attempt: int) -> None:
        """第 `attempt` 次（1-based）重试前的有界指数退避 + 抖动；可被 cancel / 会话停止中断（§4.3）。"""
        base = backoff_delay_ms(attempt - 1, self.settings.ai_trade_retry_base_ms,
                                self.settings.ai_trade_retry_max_ms)
        await asyncio.sleep((base + random.uniform(0, _RETRY_JITTER_MS)) / 1000.0)

    async def _emit_retry(self, emit: TraceEmit | None, attempt: int, max_retries: int,
                          reason: str) -> None:
        """重试前发一条 retry 轨迹（emit=None 空转，回测/单测零副作用，§4.4）。"""
        if emit is None:
            return
        await emit({"phase": "retry", "attempt": attempt,
                    "maxAttempts": max_retries, "reason": reason})

    async def _stream_decision(self, user_msg: LlmMessage, emit: TraceEmit | None = None,
                               *, text_phase: str = "analysis") -> _StreamOutcome:
        """流式累积首个 `submit_trade_decision` 的 partial_json 并解析（P1-5）；无工具调用→raw=None。

        旁路：text_delta（分析）/ thinking_delta（深度思考）经 `_TraceCoalescer` 合并后 `emit`；
        `emit is None` 时合并器空转，取工具入参逻辑与今日逐字不变（安全契约不受影响）。

        收尾语义（§3.3.3 / P0-2）：**首条 `message_done` 不再直接 `break`**，而是置 `done` 标志
        并继续消费到流自然结束，只为**回填 usage**。理由：OpenAI 的 usage 走
        `stream_options.include_usage`，它在一个独立的、`choices` 为空的收尾 chunk 里到达，
        因而会产生**第二条** `message_done`——首条那一条的 `usage` 恒为 `None`。合入前「见到即
        break」使 token 用量在半个 provider 家族上恒空，G-8 与探针判据一并落空。

        三条铁律：① `done` 之后**只接受** `message_done` 做回填，`text_delta` 一律丢弃，使
        `last_analysis` 与今日 `break` 的结果逐字相同（用例 B-8）；② 外层 `asyncio.wait_for` 仍
        兜住「provider 不关流」的极端情形，故这里不需要第二个超时；③ `error` 事件在 `done` 之后
        到达也一并丢弃——工具入参已经拿到，此时抛异常会把一轮已经成功的决策变成 `llm_error`。
        """
        accs: dict[str, dict] = {}
        order: list[str] = []
        analysis_parts: list[str] = []           # R3：累积研判全文（与 emit 解耦，回测 emit=None 也留痕）
        pump = _TraceCoalescer(emit)             # emit=None → 全程空转（回测 / 单测无订阅）
        done = False
        stop_reason: str | None = None
        usage: dict | None = None
        async for ev in self._llm.stream(system=self.system_prompt,
                                          messages=self._context + [user_msg],
                                          tools=[self._tool]):
            if ev.kind in _EVIDENCE_RESPONSE_KINDS:
                self._round_evidence_sent = True
            if ev.kind == "message_done":
                stop_reason = ev.stop_reason or stop_reason   # 非空者胜（第二条通常只带 usage）
                usage = ev.usage or usage                     # 只回填，绝不用 None 覆盖已有值
                if not done:
                    await pump.flush()
                    done = True
                continue
            if done:
                continue                         # 收尾之后到达的任何事件一律丢弃（语义等价今日 break）
            if ev.kind == "text_delta":
                analysis_parts.append(ev.text or "")
                await pump.feed(text_phase, ev.text or "")
            elif ev.kind == "thinking_delta":
                await pump.feed("thinking", ev.text or "")
            elif ev.kind == "tool_use_start":
                await pump.flush()               # 文本段结束，先冲刷再发工具起始占位
                accs[ev.id] = {"name": ev.name or "", "parts": []}
                order.append(ev.id)
                await pump.mark(ev.name or "")
            elif ev.kind == "tool_use_args_delta":
                if ev.id in accs:
                    accs[ev.id]["parts"].append(ev.partial_json or "")
            elif ev.kind == "error":
                await pump.flush()
                raise RuntimeError(ev.message or ev.code or "LLM_ERROR")
        # R3：流正常结束才落全文（超时/取消/error 提前退出则不赋值，_last_analysis 维持入口清零的空串）。
        self._last_analysis = "".join(analysis_parts).strip()[:_ANALYSIS_MAX_CHARS]
        for tid in order:                       # 取首条有效 submit_trade_decision（多调用取其一）
            if accs[tid]["name"] == DECISION_TOOL_NAME:
                raw = "".join(accs[tid]["parts"]).strip() or "{}"
                try:
                    return _StreamOutcome(json.loads(raw), stop_reason, usage)
                except json.JSONDecodeError:
                    return _StreamOutcome(None, stop_reason, usage)   # 入参非法 → hold
        return _StreamOutcome(None, stop_reason, usage)               # 未产生工具调用 → hold

    @property
    def _allowed_actions(self) -> tuple[str, ...]:
        """本决策器放行的动作枚举；总闸关闭时**不含** `cancel_order`，逐字回退 4 值（§一 (2)）。"""
        return _ACTIONS + _LIMIT_ACTIONS if self.allow_limit else _ACTIONS

    def _resolve_limit(self, raw: dict,
                       last_px: float | None) -> tuple[str, float | None, str | None]:
        """解析 + 校验委托方式（含价带门禁）；总闸关闭时恒直通市价（零回归，§2.6/P1-5）。"""
        if not self.allow_limit:
            return "market", None, None
        return validate_limit_intent(
            raw, last_px, self.settings.ai_trade_limit_price_max_deviation_pct)

    def _map_decision(self, raw: dict | None, *,
                      last_px: float | None = None) -> tuple[TradeDecision, str | None]:
        """把工具入参映射为受约束 `TradeDecision`；任一不合法一律回落 hold（安全关键）。

        `last_px`（keyword-only，默认 None → 既有直接调用零改动）：本轮最新价，**取自决策 payload 的
        `lastPx`**（`_attempt_decision` 传入）——与喂给 prompt 的价天然同源，无需任何新的取价通路，
        也保证 Agent 看到的价与被校验的价逐字一致（§2.6）。价带门禁**必须在本方法内**：重试循环在
        `decide` 里且只认本方法返回的 degrade，门禁若落在下游 `order_plan.py`，「可重试」承诺会静默
        失效（P1-5/R-15）。
        """
        if not isinstance(raw, dict):
            return _hold("no_tool_call"), "no_tool_call"
        action = raw.get("action")
        reason = str(raw.get("reason") or "")
        if action not in self._allowed_actions:
            return _hold("invalid_action"), "invalid_action"
        confidence = _clamp01(raw.get("confidence"))
        # deep-reasoning-chain §3.1.7：中文证据摘要是**纯呈现物**——总闸关 / 缺席 / 非字符串 /
        # 超长一律安全归一为 None 或截断，**绝不据它降级**（沿用 orderType 的 F-2 先例）。
        digest = self._resolve_evidence_digest(raw)
        # C1（[P2-3]）：`_map_decision` 无字面 `action == "hold"` 分支（hold/close/cancel_order 同走
        # 下方 fall-through），故承诺退出线**必须显式以 `action == "hold"` 为条件**解析——否则残留的
        # holdExitPx 会泄漏到 close/cancel_order。非法值 ⇒ None，绝不降级（hold 已是最安全动作）。
        hold_exit_px = _finite_px(raw.get("holdExitPx")) if action == "hold" else None
        # F-2：限价解析/价带门禁只对下单动作执行——hold/cancel_order 无委托语义，LLM 残留的
        # orderType/limitPrice 一律忽略（否则撤单意图会被 missing_limit_price 整轮降级吞掉）。
        if action in ORDERING_ACTIONS:
            ord_type, limit_px, limit_degrade = self._resolve_limit(raw, last_px)
            if limit_degrade is not None:  # 缺价 / 离谱价 / 非法 orderType → 降级 hold（可重试）
                return _hold(limit_degrade), limit_degrade
        else:
            ord_type, limit_px = "market", None
        if action in ("open_long", "open_short"):
            size = _clamp01(raw.get("sizeFraction"))
            if size is None or size <= 0:
                return _hold("missing_size"), "missing_size"
            if confidence is not None and confidence < self.settings.ai_trade_min_confidence:
                return _hold("low_confidence"), "low_confidence"
            # [R-5/R-9] 唯一硬约束点：过完 low_confidence 门禁后把开仓规模吸附到用户所选档位（实盘/回测同生效）。
            # 延迟导入打破 signals→decider 模块级循环；quantize 的 max(1,…) 保证 >0 输入不被吸附回 0（不新触发
            # missing_size）；step=0 时逐字等价旧行为（连续直通）。
            from .signals import quantize_size_fraction
            size = quantize_size_fraction(size, self._position_step)
            # directional-symmetry A-3：`contrarianEvidence` **只在开仓分支解析**（与 `holdExitPx`
            # 只在 hold 分支、`exitConviction` 只在 close 分支解析的成文纪律逐字同构）——本方法
            # 没有字面 `action == "open_*"` 分支，不显式限定作用域，残留值会泄漏到别的动作上。
            # 落点必须是这条**提前 return**：开仓走的是它，不是文件末尾那条六位置参的 return，
            # 改错行的表现是字段恒为 None 且不报任何错。
            return (TradeDecision(action, size, confidence, reason, ord_type, limit_px,
                                  evidence_digest=digest,
                                  contrarian_evidence=self._resolve_contrarian_evidence(raw),
                                  intel_refs=self._resolve_intel_refs(raw)),
                    None)
        # exit-priority §6.1【R:P1-9b】：**只改这一条返回**。`open_long`/`open_short` 走的是上面
        # 那条 6 位置参的提前 return（连 hold_exit_px 都不带），按 X-1b 反手一律不进本层（先降级
        # 为 close 再判）——去动它会把一个开仓决策变成潜在的解闸入口，直接违反 X-1。
        # 显式以 `action == "close"` 为条件解析（与 C1 给 holdExitPx 立的 [P2-3] 纪律逐字同构：
        # 本函数没有字面 `action == "close"` 分支，不显式判条件残留值会泄漏到 hold/cancel_order）。
        conviction, evidence = self._resolve_exit_intent(raw, action)
        # intel-closed-loop 轴 A：**两条返回路径都要带上它**——`contrarian_evidence` 的注释已经
        # 把「改错行的表现是字段恒为 None 且不报任何错」写死在上面那条提前 return 旁边。
        return (TradeDecision(action, None, confidence, reason, ord_type, limit_px,
                              hold_exit_px, conviction, evidence,
                              evidence_digest=digest,
                              intel_refs=self._resolve_intel_refs(raw)), None)

    def _resolve_evidence_digest(self, raw: dict) -> str | None:
        """解析可选的中文证据摘要（总闸关 / 缺席 / 非字符串 ⇒ `None`；超长按 400 截断）。

        **绝不降级**（用例 D-11 / D-12）：这个字段只喂三处中文界面（journal 摘录、收起态标题、
        左栏决策卡首段），一个写错的摘要是排版问题，不是安全问题。语言校验刻意**不放在这里**
        ——它是呈现层的取值回落（`session` 侧的 `is_probably_chinese`），放进决策映射会让一段
        英文摘要改变 `TradeDecision`，而那正是 R-4 的教训。
        """
        if not self.allow_reasoning_digest:
            return None
        text = str(raw.get("evidenceDigest") or "").strip()[:_EVIDENCE_DIGEST_MAX_CHARS]
        return text or None

    def _resolve_contrarian_evidence(self, raw: dict) -> str | None:
        """解析逆势开仓证据（总闸关 / 缺席 / 非字符串 ⇒ `None`；超长按上限截断）。

        **绝不降级**（沿用 `orderType` 的 F-2 先例）：一个读不懂的证据串只会让逆势通路不可用，
        而那正是本特性合入前的行为。字数是否达标由**纯核心** `signals._contrarian_block` 判定
        并产出 `low_consensus_evidence` 码——判据只有一处，本方法不复制它。
        """
        if not self.allow_contrarian_evidence:
            return None
        text = str(raw.get("contrarianEvidence") or "").strip()[:_CONTRARIAN_EVIDENCE_MAX_CHARS]
        return text or None

    def _resolve_intel_refs(self, raw: dict) -> tuple[str, ...]:
        """解析可选的情报引用编号（总闸关 / 缺席 / 类型错 ⇒ 空元组）。**绝不降级**。

        **刻意不限定 action 作用域**（与 `holdExitPx` / `exitConviction` / `contrarianEvidence`
        三个先例不同，理由写在这里而不是留给读者猜）：hold 轮的引用同样要记，那正是归因层区分
        `entry` / `hold` 的前提；而「残留值泄漏到别的 action 上」在这里不成立——它是一个纯观测
        字段，泄漏只会让统计多一条 `role="hold"` 的行，不会改变任何交易语义。

        编号是否**真实存在**不在这里判：编号表在 session 侧（随注入快照一起来），服务端裁决在
        纯核心 `intel/citation.resolve_citations`。本方法只做正则、去重与截断。
        """
        if not self.allow_intel_refs:
            return ()
        items = raw.get("intelRefs")
        if not isinstance(items, list):
            return ()
        seen: list[str] = []
        for entry in items:
            token = str(entry or "").strip().upper()
            if _INTEL_REF_RE.match(token) and token not in seen:
                seen.append(token)
            if len(seen) >= self.intel_max_refs:
                break
        return tuple(seen)

    def _resolve_exit_intent(self, raw: dict,
                             action: str) -> tuple[str | None, str | None]:
        """解析 `exitConviction` / `exitEvidence`（总闸关 / 非 close ⇒ 恒 `(None, None)`）。

        值域外 / 类型错**一律忽略、绝不降级**（沿用 `orderType` 的 F-2 先例）。
        """
        if not self.allow_exit_priority or action != "close":
            return None, None
        from .exit_priority import CONVICTIONS       # 延迟导入打破 decider→prompts→hold_horizon 环

        raw_conviction = str(raw.get("exitConviction") or "").strip()
        conviction = raw_conviction if raw_conviction in CONVICTIONS else None
        evidence = str(raw.get("exitEvidence") or "").strip()[:_EXIT_EVIDENCE_MAX_CHARS]
        return conviction, (evidence or None)

    # ---------- 下单前二次反思（ai-trade-pre-trade-reflection §2.2/§2.4）----------
    async def reflect(self, payload: dict, decision: TradeDecision, *,
                      emit: TraceEmit | None = None) -> ReflectionVerdict:
        """对本轮**交易意愿**决策做一次独立的下单前复核；返回 `ReflectionVerdict`（只会更保守）。

        无状态子调用：用独立 system prompt + 单轮 messages（**不拼 `_context/_journal`**），只传
        `submit_trade_reflection` 一个工具、无 `tool_choice`；安全由回落兜底（超时/LLM 错误/无工具/非法
        裁决 → fail-closed，等价 abort）。瞬时失败先带有界退避重试至多 `ai_trade_reflection_max_retries`
        次（复用 decide 同款退避），穷尽才 fail-closed；`abort` 是终态裁决、零重试；`asyncio.CancelledError`
        立即上抛（绝不吞、绝不降级，使 stop() 能干净取消在飞反思）。

        `reflection_enabled=False`（直接构造决策器的既有测试/回测）→ 无副作用直通（不调 LLM），返回
        `ran=False` 的确认裁决，`apply_reflection` 据此原样透传（逐字零回归，§2.3）。
        """
        if not self.reflection_enabled:
            return ReflectionVerdict("confirm", None, None, "", None, ran=False)
        payload = deepcopy(self._with_round_facts(payload))
        snapshot = build_reflection_evidence(payload)
        snapshot["sent"] = False
        self._round_reflection_snapshot = snapshot
        max_retries = max(0, int(self.settings.ai_trade_reflection_max_retries))
        try:
            verdict = await self._attempt_reflection(payload, decision, emit)
            attempt = 0
            while (verdict.degrade_reason is not None
                   and _is_retryable_reflection(verdict.degrade_reason)
                   and attempt < max_retries):
                attempt += 1
                await self._retry_backoff(attempt)
                verdict = await self._attempt_reflection(payload, decision, emit)
        except asyncio.CancelledError:
            snapshot["sent"] = False
            raise
        self._note_reflection_depth()
        return verdict

    def _note_reflection_depth(self) -> None:
        """把本轮复核的篇幅与阶段数回填进 `ReasoningMeta`（G-2 的可证伪判据）。

        `_last_reasoning` 是 frozen dataclass，故用 `replace` 生成新实例；深度档未生效
        （`_last_reasoning is None`）⇒ 整段空转，wire 上依旧整键缺席。
        """
        if self._last_reasoning is None:
            return
        text = self._last_reflection_analysis
        self._last_reasoning = replace(self._last_reasoning,
                                       reflection_chars=len(text),
                                       reflection_stages=count_stages(text, "R"))

    async def _attempt_reflection(self, payload: dict, decision: TradeDecision,
                                  emit: TraceEmit | None) -> ReflectionVerdict:
        """单次反思尝试：流式复核 → 映射裁决；任一瞬时失败回 fail-closed（可重试原因）。"""
        try:
            raw = await asyncio.wait_for(
                self._stream_reflection(payload, decision, emit),
                timeout=self._reflection_timeout_s)
        except asyncio.TimeoutError:
            return _fail_closed("reflection_timeout")
        except asyncio.CancelledError:
            raise                    # 会话停止：绝不吞、绝不降级，向上抛（先于下方 except Exception）
        except Exception as exc:  # noqa: BLE001 —— 任何 LLM/网络失败 fail-closed（校验不可用时不下单）
            logger.warning("ai-trade reflect attempt failed: %s", exc)
            return _fail_closed("reflection_llm_error")
        return self._map_reflection(raw)

    async def _stream_reflection(self, payload: dict, decision: TradeDecision,
                                 emit: TraceEmit | None = None) -> dict | None:
        """流式累积首个 `submit_trade_reflection` 的 partial_json 并解析（镜像 `_stream_decision`）。

        复核推理经 `phase="reflection"` 合并后 `emit`（`emit is None` 时空转，回测/单测零副作用）；反思
        工具起始**不**发 tool_call_start（裁决经 reflection_verdict 呈现，非工具卡）。无有效工具调用 → None。

        **复核协议的唯一注入点就在这里**（deep-reasoning-chain §3.2 / P0-5）：
        `build_reflection_system_prompt` 全仓只有本处一个调用点，且每轮用当轮 payload 现造
        `system`。manager / backtest **从未**引用过它——在那两处注入 R1–R7 是一条不报错、不判红、
        轴 B 整条死掉的死路。`self._depth is None`（既有直接构造 / 全部离线用例）⇒ 传 `""` ⇒
        反思系统 prompt **逐字节回退**，`test_ai_trade_exit_freeze.py` 那条 sha256 冻结断言原样成立。
        """
        # holding-horizon §2.9 c：持仓周期补充段随本轮 payload 现取（payload 就在手上，零额外管线）；
        # 契约未装配 ⇒ 键缺席 ⇒ `""` ⇒ 反思系统 prompt 逐字节回退（G-1 的 sha256 冻结门禁据此成立）。
        # directional-symmetry C-3：R7 追加「对做空提议适用与做多**完全相同**的证据标准」。
        # 复核官「只能收紧、不能改方向」的既有安全语义一个字节都不改；总闸关 ⇒ 逐字节回退。
        # execution-trend-verdict 轴 C′（评审 RP0-4）：`trend_bar` 从**当轮 payload** 现取，
        # 绝不从 settings 再读一次（两处各读一次必漂移；且总闸关时 payload 本就无键，判据天然
        # 同源）。它同时喂 R4 的条件化子句与复核官系统 prompt 的评级标准 (b)——两处**必须同源**，
        # 否则「R4 说顺着基调不算缺陷、评级标准仍说与高周期冲突就 abort」会互相打架。
        # 键缺席 ⇒ 空串 ⇒ 两处逐字节回退（用例 P-8 / P-9 冻结）。
        trend_bar = str((payload.get("trendVerdict") or {}).get("bar") or "")
        reasoning_text = ("" if self._depth is None else build_review_protocol(
            self._depth,
            symmetry=self.settings.ai_trade_directional_symmetry_enabled,
            trend_bar=trend_bar))
        system = build_reflection_system_prompt(
            format_reflection_note(payload.get("tradingHorizon")), reasoning_text, trend_bar)
        user_msg = LlmMessage("user", [TextBlock(build_reflection_prompt(payload, decision))])
        accs: dict[str, dict] = {}
        order: list[str] = []
        reflection_parts: list[str] = []         # 补根因 C-3：复核推理服务端留存（只收 text_delta）
        pump = _TraceCoalescer(emit)             # emit=None → 全程空转
        done = False
        self._last_reflection_analysis = ""      # 每次尝试入口清零（镜像 `_last_analysis` 的 RC-1）
        async for ev in self._reflection_llm.stream(system=system, messages=[user_msg],
                                                    tools=[self._reflection_tool]):
            if (ev.kind in _EVIDENCE_RESPONSE_KINDS
                    and self._round_reflection_snapshot is not None
                    and not self._round_reflection_snapshot.get("sent")):
                self._round_reflection_snapshot["sent"] = True
                text = user_msg.content[0].text
                self._round_reflection_snapshot["request"] = {
                    "finalUserText": text, "finalUserSha256": _sha256(text),
                }
            if ev.kind == "message_done":
                if not done:
                    await pump.flush()
                    done = True
                continue                         # 与决策侧同款：收尾后只等流自然结束，不再消费
            if done:
                continue
            if ev.kind in ("text_delta", "thinking_delta"):
                # [P2-3] 累积**只挂在 `text_delta` 上**：本方法把两种增量合喂同一个 phase（那是
                # 呈现层的合并，与服务端留存是两件事）。照抄那一行的位置累积，扩展思考一旦开启，
                # 模型的思考文本会混进 `_last_reflection_analysis`，令 R1–R7 计数虚高、篇幅失真。
                if ev.kind == "text_delta":
                    reflection_parts.append(ev.text or "")
                await pump.feed("reflection", ev.text or "")
            elif ev.kind == "tool_use_start":
                await pump.flush()               # 复核文本段结束，先冲刷（反思无 tool_call_start 占位）
                accs[ev.id] = {"name": ev.name or "", "parts": []}
                order.append(ev.id)
            elif ev.kind == "tool_use_args_delta":
                if ev.id in accs:
                    accs[ev.id]["parts"].append(ev.partial_json or "")
            elif ev.kind == "error":
                await pump.flush()
                raise RuntimeError(ev.message or ev.code or "LLM_ERROR")
        # 与决策侧同款：流正常结束才落全文（超时/取消/error 提前退出 ⇒ 维持入口清零的空串）。
        self._last_reflection_analysis = "".join(reflection_parts).strip()[:_ANALYSIS_MAX_CHARS]
        for tid in order:                        # 取首条有效 submit_trade_reflection
            if accs[tid]["name"] == REFLECTION_TOOL_NAME:
                raw = "".join(accs[tid]["parts"]).strip() or "{}"
                try:
                    return json.loads(raw)
                except json.JSONDecodeError:
                    return None                  # 入参非法 → fail-closed（invalid）
        return None                              # 未产生工具调用 → fail-closed（no_tool）

    def _map_reflection(self, raw: dict | None) -> ReflectionVerdict:
        """把反思工具入参映射为 `ReflectionVerdict`；任一不合法一律 fail-closed（安全关键，只会更保守）。"""
        if not isinstance(raw, dict):
            return _fail_closed("reflection_no_tool")
        verdict = raw.get("verdict")
        reason = str(raw.get("reason") or "")
        if verdict not in ("confirm", "abort"):
            return _fail_closed("reflection_invalid")
        confidence = _clamp01(raw.get("confidence"))
        if verdict == "abort":                   # 模型真实终态否决（degrade_reason=None → 零重试）
            return ReflectionVerdict("abort", None, confidence, reason or "reflection_abort",
                                     None, ran=True)
        # confirm：adjustedSizeFraction 只作「收缩」提议（apply_reflection 量化后对原值取 min，只减不增）。
        adjusted = _clamp01(raw.get("adjustedSizeFraction"))
        return ReflectionVerdict("confirm", adjusted, confidence, reason, None, ran=True)

    # ---------- 轮次记录 + 滚动摘要 ----------
    async def commit_round(self, payload: dict, decision: TradeDecision,
                           executed: dict, *, analysis: str | None = None,
                           reflection: dict | None = None,
                           gate_veto: dict | None = None,
                           defer_summary: bool = False,
                           record: RoundDecisionRecord | None = None) -> str | None:
        """记录本轮（追加 _context + _journal），到期触发摘要重置；返回新摘要文本或 None。

        `defer_summary`（keyword-only，默认 **False** ⇒ 回测 / 单测**逐字回退**；D-5）：为真时到期
        只记账（置 `_summary_due`）并返回 `None`，真正的 LLM 调用由调用方在推完决策事件之后经
        `flush_summary()` 触发。理由：摘要走 30s 超时，排在 `_emit_decision` **之前** await 会让
        「成交已经发生、决策已经完成」之后界面上的决策事件再等最多半分钟才出现（成交先到、解释后到）。
        **刻意不是 `create_task` 甩到后台**：`_deciding` 标志在 `finally` 里清零，后台摘要会与下一轮
        `decide` 并发写同一个 `_context` / `_journal`——那是一个真实的数据竞争。保持串行、只调整顺序，
        是这里唯一安全的改法（下一个人请勿"顺手优化"）。

        `analysis`（keyword-only，默认 None → 回测/单测既有调用零回归）：本轮研判的**有界摘录**
        （由调用方从 `last_analysis` 截到 ≤`_ANALYSIS_EXCERPT_CHARS`，RC-2/RC-3），入 journal 供滚动摘要
        沉淀「哪些信号被采纳/推翻」；完整思维链只走 agent_trace 流与 `_traces`，绝不入 journal（防膨胀）。

        `reflection`（keyword-only，默认 None → 既有调用零回归；hold-inertia-fix F2）：本轮反思注记
        （`build_reflection_note` 产物）。恒入 journal（未触发轮为 None，形状稳定，R-06）；
        **仅否决轮**（`vetoed=True`）把注记合并进 `_context` 的 tool_result——短窗口内模型自己也能
        看到「这轮 hold 是被否决的开仓」；非否决轮 tool_result 内容逐字不变（R-05 冻结契约）。

        `gate_veto`（keyword-only，默认 None → 既有调用零回归；exit-priority F-2）：本轮**门禁**
        否决注记（`horizon_runtime.build_gate_veto_note` 产物）。与 `reflection` 逐字同款处理：
        恒入 journal（未命中轮为 None，形状稳定），**仅命中轮**合并进 `_context` 的 tool_result。
        没有它，被 G2 拦下的那一轮模型下一轮读到的自己的历史是「上轮我决定观望」——F-2 断的正是
        模型侧的论证连续性，与 F-1 断的系统侧计数互为因果，共同构成那条没有出口的活锁。
        """
        self._last_round = int(payload.get("round") or self._last_round + 1)
        payload = self._with_round_facts(payload)
        tool_id = uuid.uuid4().hex
        # M-9：委托方式随 journal 落痕，否则滚动摘要会丢失挂单史（Agent 记不得自己挂过什么价）。
        decision_input = {"action": decision.action, "sizeFraction": decision.size_fraction,
                          "confidence": decision.confidence, "reason": decision.reason,
                          "orderType": decision.ord_type, "limitPrice": decision.limit_px,
                          # C1：承诺退出线随 journal 落痕，否则滚动摘要重置后问责回路断裂（模型记不得
                          # 自己许过什么退出线）——与 orderType/limitPrice 当初落 journal 同理（M-9）。
                          "holdExitPx": decision.hold_exit_px,
                          # exit-priority §6.1：两者随 journal 落痕，否则滚动摘要重置后问责回路
                          # 断裂（模型记不得自己声明过什么确信度）——与 orderType/holdExitPx 同理。
                          "exitConviction": decision.exit_conviction,
                          "exitEvidence": decision.exit_evidence}
        # 浅拷贝合并，绝不就地改 `executed` 入参对象；两者都未命中时走空字典分支、tool_result
        # 内容逐字不变（R-05）。两条注记**不假设互斥**（形状稳定优先于省字符）：门禁否决轮的
        # decision 此刻已是 hold ⇒ 反思对 hold 直通、`reflection` 恒 None，故实际不会同时出现。
        extra: dict = {}
        if reflection and reflection.get("vetoed"):
            extra["reflection"] = reflection
        if gate_veto:
            extra["gateVeto"] = gate_veto
        record_wire = record.to_wire() if record else {"legacy": True}
        record_wire.update(self._summary_round_evidence(payload))
        if record:
            facts = payload.get("decisionFacts") or record_wire["facts"]
            record_wire.update(facts=deepcopy(facts),
                               factsHash=facts.get("factsHash"),
                               evidenceFingerprint=facts.get("evidenceFingerprint"))
            record_wire["gateVeto"] = gate_veto or record_wire["gateVeto"]
            record_wire["executionReceipt"] = (
                executed.get("executionReceipt")
                or record_wire["executionReceipt"])
            extra.update(record_wire)
        tool_result_text = json.dumps({**executed, **extra} if extra else executed,
                                      ensure_ascii=False)
        self._context.append(LlmMessage("user", [TextBlock(build_decision_prompt(payload))]))
        self._context.append(LlmMessage(
            "assistant", [ToolUseBlock(id=tool_id, name=DECISION_TOOL_NAME, input=decision_input)]))
        self._context.append(LlmMessage(
            "tool", [ToolResultBlock(tool_use_id=tool_id, content=tool_result_text)]))
        # journal 每轮恒带 trigger/reflection 两键（含 None 值）：全轮形状稳定、F4b 折叠判定对
        # legacy 条目兼容（R-06 有意设计，勿按「省字符」回改为条件写入）。
        self._journal.append({
            "round": self._last_round, "signalBoard": payload.get("signalBoard"),
            "decision": decision_input, "executed": executed,
            "positionAfter": executed.get("positionAfter"),
            "sessionPnl": payload.get("sessionPnlUsd"), "analysis": analysis,
            "trigger": payload.get("trigger"), "reflection": reflection,
            "gateVeto": gate_veto, **record_wire})
        self._rounds_since_summary += 1
        self._trim_context()
        # [P1-11] 到期判定的**唯一最终形态**：退避（D-3 第二半）与 `defer_summary`（D-5）改的是
        # 同一个 `if`。分两处各改一处的表现是**静默的**（要么摘要再也不触发，要么退避恒不生效，
        # 两种都不会报错），故全仓只允许存在这一份。
        due = self._rounds_since_summary >= (self.context_refresh_rounds
                                             + self._summary_skip_rounds)
        if not due:
            return None
        if defer_summary:
            self._summary_due = True      # 只记账，真正的 LLM 调用挪到 `flush_summary`
            return None
        return await self.summarize()     # 回测 / 单测的逐字回退路径（默认 False）

    async def flush_summary(self) -> str | None:
        """把被 `defer_summary` 推迟的那一次摘要真正跑掉（D-5）；未欠着 ⇒ 直接 `None`。

        `_summary_due` 必须在 `summarize()` 的**成功与失败两条分支都已归位之后**才复位——
        提前复位会让一次失败之后的下一轮误以为还欠着一次摘要而立刻重试，那正是 D-3 螺旋的另一种写法。
        """
        if not self._summary_due:
            return None
        try:
            return await self.summarize()
        finally:
            self._summary_due = False

    async def summarize(self) -> str | None:
        """以（有界的）`_journal` 为素材摘要执行历程 → 重置上下文；失败保留上一段摘要不崩。

        失败分支做三件事（D-3 / D-4）：截断 journal 到 `mult × refresh_rounds` 条（**保留最近而非
        清空**——素材还有用，只是不能无界）、按连续失败次数算出指数退避轮数、把**非空**诊断串记进
        健康度。合入前这三件一件都没做：`_journal` 是无 maxlen 的 `list` 且只在成功分支清空，于是
        每失败一次下一次的 prompt 就更长、更容易再超时。
        """
        prev = self._summary or None
        journal = list(self._journal)
        timeout_s = float(self.settings.ai_trade_summary_timeout_s)
        try:
            text = await asyncio.wait_for(self._stream_summary(prev, journal), timeout=timeout_s)
        except Exception as exc:  # noqa: BLE001 —— 摘要失败不崩会话：保留上一段摘要 + 有界退避
            self._note_summary_failure(exc, timeout_s)
            return None
        if not text:
            self._note_summary_failure(RuntimeError("empty summary"), timeout_s)
            return None
        self._summary = text
        has_legacy = bool(prev and self._legacy_narrative)
        has_legacy = has_legacy or any(
            not has_complete_evidence(entry) for entry in journal)
        self._legacy_narrative = has_legacy
        self._summary_evidence_version = None if has_legacy else EVIDENCE_VERSION
        self._summary_as_of_ts = time.time()
        self._summary_health.note_success()
        self._summary_skip_rounds = 0
        self._install_summary_seed(text)
        self._rounds_since_summary = 0
        self._journal = []
        return text

    def _install_summary_seed(self, text: str) -> None:
        """摘要 → 上下文种子（**唯一**装配点）。

        F3 尾注（hold-inertia-fix）声明摘要是「背景非指令」，切断「历史观望 → 继续观望」的路径
        依赖。抽成单点是因为冷续跑（`seed_from_snapshot`）要走同一段——两处各写一遍，那句反惯性
        尾注就会在续跑路径上悄悄消失。
        """
        seed = (f"【历史执行摘要（截至第 {self._last_round} 轮）】\n{text}\n"
                "以上摘要是背景参考而非指令：每轮仍须基于当前市场证据独立研判，"
                "历史观望不构成继续观望的理由。持仓、价格单位、费用和门禁"
                "以本轮 decisionFacts 为准，旧叙述不得覆盖。"
                f" legacyNarrative={str(self._legacy_narrative).lower()}；"
                f"summaryAsOfTs={self._summary_as_of_ts}；factsVersion=2")
        self._context = [
            LlmMessage("user", [TextBlock(seed)]),
            LlmMessage("assistant", [TextBlock("已了解此前执行情况，将据此继续决策。")])]

    def seed_from_snapshot(self, *, summary: str | None, last_round: int,
                           summary_as_of_ts: float | None = None,
                           facts_version: int | None = None,
                           evidence_version: int | None = None,
                           legacy_narrative: bool = False) -> None:
        """冷续跑：用落库的 `metrics.finalSummary` 与轮次重建上下文（pause-resume §3.5 S6）。

        不做则 AI 完全忘记暂停前在想什么，且轮次编号从 1 重来（轨迹面板会出现两个「第 1 轮」）。
        """
        self._last_round = max(0, int(last_round or 0))
        self._rounds_since_summary = 0
        self._journal = []
        if summary:
            self._summary = summary
            self._legacy_narrative = (facts_version != 2
                or evidence_version != EVIDENCE_VERSION or legacy_narrative)
            self._summary_evidence_version = (
                None if self._legacy_narrative else EVIDENCE_VERSION)
            self._summary_as_of_ts = summary_as_of_ts
            self._install_summary_seed(summary)

    def memory_wire(self) -> dict:
        """返回摘要时间与兼容标记，不暴露历史原文。"""
        return {"factsVersion": 2, "legacyNarrative": self._legacy_narrative,
                "evidenceVersion": EVIDENCE_VERSION,
                "summaryEvidenceVersion": self._summary_evidence_version,
                "summaryAsOfTs": self._summary_as_of_ts}

    def _freeze_round_evidence(self, payload: dict) -> dict:
        """发送前一次冻结；会话已算好的 factsHash 原样沿用。"""
        snapshot = payload.get("advisorySnapshot")
        if snapshot is None:
            snapshot = build_advisory_evidence(payload)
            facts = payload.get("decisionFacts")
            hashes = evidence_hashes(snapshot)
            if (facts and not facts.get("factsUnavailable")
                    and facts.get("evidenceHashes") != hashes):
                from .decision_memory import build_decision_facts
                payload["decisionFacts"] = build_decision_facts({
                    **payload, **facts, "evidenceHashes": hashes})
        self._round_identity = evidence_identity(payload)
        self._round_advisory_snapshot = deepcopy(snapshot)
        self._round_decision_facts = deepcopy(payload.get("decisionFacts"))
        return attach_snapshot(payload, snapshot, self._round_decision_facts)

    def _summary_round_evidence(self, payload: dict) -> dict:
        """提交只读本轮真正发出的证据，不从可变 payload 重建。"""
        snapshot = self._round_advisory_snapshot
        is_current = self._round_identity == evidence_identity(payload)
        if not snapshot or not self._round_evidence_sent or not is_current:
            snapshot = unavailable_evidence("round_not_sent_or_mismatched")
            reflection = None
        else:
            snapshot = {**snapshot, "sent": True}
            reflection = self._round_reflection_snapshot
            if reflection is not None and not reflection.get("sent"):
                reflection = unavailable_evidence("reflection_not_sent")
            elif (reflection is not None
                    and reflection.get("identity") != self._round_identity):
                reflection = unavailable_evidence("reflection_mismatched")
        return {"evidenceVersion": EVIDENCE_VERSION,
                **bound_summary_evidence(snapshot, reflection)}

    def frozen_round_payload(self, payload: dict) -> dict:
        """返回供 journal 重建的独立快照；不匹配轮次不补上一轮材料。

        Args:
            payload: 当前轮最终组装的请求输入。
        Returns:
            保留原字段并冻结初次及反思 wire、实际反思 user 文本与 hash。
        """
        result = deepcopy(self._with_round_facts(payload))
        snapshot = self._round_reflection_snapshot
        is_current = self._round_identity == evidence_identity(payload)
        if (not is_current or not self._round_evidence_sent
                or not snapshot or not snapshot.get("sent")):
            return result
        if snapshot.get("identity") != self._round_identity:
            return result
        result.update(deepcopy(snapshot["wire"]))
        result["reflectionRequest"] = deepcopy(snapshot.get("request"))
        return result

    def _with_round_facts(self, payload: dict) -> dict:
        """同轮反思与上下文沿用真实发送的事实，不回写调用方状态。"""
        snapshot = self._round_advisory_snapshot
        if (snapshot and self._round_evidence_sent
                and self._round_identity == evidence_identity(payload)):
            return attach_snapshot(payload, snapshot,
                                   self._round_decision_facts)
        return payload

    def _note_summary_failure(self, exc: BaseException, timeout_s: float) -> None:
        """摘要失败的**唯一**归位点：截断 journal（D-3）+ 指数退避 + 非空诊断串（D-4）。

        `_rounds_since_summary` 照旧清零（避免每轮重试），退避则通过 `_summary_skip_rounds` 抬高
        下一次的到期门槛——两者相加才是「连续失败时不再每 N 轮就白烧一次超时预算」。
        """
        refresh = max(1, int(self.context_refresh_rounds))
        self._summary_health.note_failure(exc, refresh_rounds=refresh, timeout_s=timeout_s)
        # 退避轮数的**唯一真源**是健康度自己算的那一份（`SummaryHealth.note_failure` 内已按
        # `next_backoff` 落定）：在这里再算一遍就是第二份口径，两份一旦漂移，wire 上显示的
        # `backoffRounds` 与真正生效的到期门槛就会说两套话。
        self._summary_skip_rounds = self._summary_health.backoff_rounds
        cap = refresh * max(1, int(self.settings.ai_trade_summary_journal_cap_mult))
        self._journal = self._journal[-cap:]
        logger.warning("ai-trade summarize failed: %s", self._summary_health.last_error)
        self._rounds_since_summary = 0

    def summary_health_wire(self) -> dict | None:
        """`status.summaryHealth` / `info()` 共用的只读 wire（两处装配、同一次计算）。"""
        return health_wire(self._summary_health,
                           rounds_since_summary=self._rounds_since_summary,
                           refresh_rounds=self.context_refresh_rounds,
                           max_failures=int(self.settings.ai_trade_summary_max_failures))

    async def _stream_summary(self, prev: str | None, journal: list[dict]) -> str:
        parts: list[str] = []
        async for ev in self._summary_llm.stream(
                system=build_summary_system_prompt(),
                messages=[LlmMessage("user", [TextBlock(build_summary_prompt(prev, journal))])],
                tools=[]):
            if ev.kind == "text_delta":
                parts.append(ev.text or "")
            elif ev.kind == "error":
                raise RuntimeError(ev.message or ev.code or "LLM_ERROR")
            elif ev.kind == "message_done":
                break
        # §3.3.4：摘要种子是**每轮都会被重读**的那一段，故除了 1536 token 的模型侧预算之外
        # 再加一道字符兜底——一次异常长的摘要会把 `_context` 一路撑到窗口上限。
        return "".join(parts).strip()[:_SUMMARY_MAX_CHARS]

    def _trim_context(self) -> None:
        """两次摘要间硬上限兜底：裁到近 N 条且首条为 user 轮（避免孤儿 tool_result，参照 _trimmed_messages）。"""
        cap = self.settings.ai_trade_max_context_messages
        if len(self._context) <= cap:
            return
        msgs = self._context[-cap:]
        while msgs and msgs[0].role != "user":
            msgs = msgs[1:]
        self._context = msgs
