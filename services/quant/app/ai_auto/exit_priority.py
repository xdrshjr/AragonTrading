"""离场优先级纯核心（ai-trade-exit-priority-and-commitment 轴 B · §4.3）。

用户的仓位不是被某一个参数卡死的，是被「离场这件事在整条决策链上没有优先级」卡死的：系统对开仓侧
建了六道闸、每一道都写明「平仓 `close` 不受影响」，而真正约束 `close` 的那一层（持仓周期三闸）把
「Agent 的离场确信度」当成一个不存在的量——无论模型是随口一提，还是在思维链里逐条否掉了持仓理由、
又经独立复核官背书，G2 数到的都只是「轮」。

本模块把确信度提升为一等公民，合成三方证据（**模型显式声明** × **跨轮意图账本** × **独立复核官的
第二意见**）得出五档优先级，再由 `hold_horizon.evaluate_horizon_gate` 的 `priority_gates` 并集解闸。

四条不可动摇的不变式（改本文件前先读）：

1. **X-1 · 离场永远优先于开仓。** 本层**只可能**让一次 `close` 更容易发生。作用域因此被钉死为
   `gated_kind(...) == "close"`——反手（持多时 `open_short`）同样受持仓周期三闸约束，但它携带一条
   **开仓腿**，给它解闸就是用「帮你离场」的名义解开 G1 去开一笔新仓。反手的处置是 X-1b：由上游
   `turnover_discipline` 式的「保留退出腿、丢弃开仓腿」降级为 `close` 后再进本层。
2. **X-4 · 确信度不由任何单方决定。** `override` 必须同时满足「模型显式 `final`」×「证据非空」×
   「跨轮账本达门槛」×「会话配额未尽」×「已过绝对地板」。**删掉其中任意一个合取项，这条通道就
   变成模型的自助放行按钮。** 复核官背书**不是**这四项中任何一项的替代品，它只替 `ledger_short`
   （账本还不够厚）背书——配额是会话级资源约束、证据缺失是根本没有证据可看、时间地板是墙钟事实
   而非判断题，这三样都不是复核官有资格替代的。
3. **X-4b · 裁决放行与 `override` 共用同一份配额。** 否则「配额用尽 ⇒ 走裁决 ⇒ 配额继续涨」会让
   `max_overrides_per_session` 变成一个只在文档里存在的数字。
4. **纯核心不依赖调用顺序**（X-10）：本模块无 IO、无 LLM、无 broker，`resolve_exit_priority` 是
   一次求值的纯函数，会话侧只读它的产物（含 `may_pend`）、**绝不重算**。
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from .hold_horizon import GATE_EXIT_CONFIRM, GATE_HYSTERESIS, GATE_MIN_HOLD, gated_kind

# 五档（wire 值，前后端镜像；顺序即优先级由低到高）。
LEVEL_NONE = "none"                # 本轮不是**平仓**（含 hold / 开仓 / 反手）—— 本层整体空转
LEVEL_NORMAL = "normal"            # P1：今天的行为，三闸照旧
LEVEL_EXPEDITE = "expedite"        # P2：**只解 G2**（【R:P1-4】不解 G3——那是成本闸）
LEVEL_ADJUDICATED = "adjudicated"  # P3a：复核官背书放行，解 G1/G2/G3
LEVEL_OVERRIDE = "override"        # P3b：模型终局判断 + 证据 + 配额 + 地板，解 G1/G2/G3

# 模型显式声明的离场确信度（决策工具 `exitConviction` 的值域）。
CONVICTION_NORMAL, CONVICTION_HIGH, CONVICTION_FINAL = "normal", "high", "final"
CONVICTIONS: tuple[str, ...] = (CONVICTION_NORMAL, CONVICTION_HIGH, CONVICTION_FINAL)

# 四种降档原因（`final` 声明未能兑现为 `override` 的原因；自上而下取第一条，顺序即合取项顺序）。
DOWNGRADE_EVIDENCE = "evidence_missing"
DOWNGRADE_LEDGER = "ledger_short"
DOWNGRADE_QUOTA = "quota_exhausted"
DOWNGRADE_MIN_HOLD = "min_hold_floor"

_ALL_GATES: tuple[str, ...] = (GATE_MIN_HOLD, GATE_EXIT_CONFIRM, GATE_HYSTERESIS)
# `expedite` 只解 G2：`_first_gate_reason` 的 G2 判据是 `streak < rounds and not windowElapsed`，
# 故本档由 `intent_streak >= exit_confirm_rounds` 命中时 G2 本就已放行——若同时解 G3，该析取支
# **唯一的净效果**就是解开换手迟滞，即「在浮盈不足以覆盖一次往返成本时离场」，确定性地做一笔净亏
# 的往返。G3 的解除只保留两条自带约束的通道：`override`（证据 + 配额 + 地板）与 `exit_pending`
# （墙钟上界）。
_EXPEDITE_GATES: tuple[str, ...] = (GATE_EXIT_CONFIRM,)


@dataclass(frozen=True)
class ExitPriorityRule:
    """会话级不可变契约；`None` ⇒ 本层不装配 ⇒ 判定恒返回 `normal` 且 wire 整键缺席。"""

    enabled: bool = True
    exit_confirm_rounds: int = 2          # 与 HorizonRule 同源，由装配点原样投影，**不复算**
    override_rounds: int = 2
    override_min_hold_s: float = 60.0     # G1 的不可解除内核：override 也不解这一段
    # 【X-4b】`override` 与 `adjudicated` **共用**这一份配额（裁决不是配额外的第二条通道）。
    max_overrides_per_session: int = 6
    evidence_min_chars: int = 10


@dataclass(frozen=True)
class ExitPriorityInput:
    """一次判定所需的断面（聚合结构体，守 CLAUDE.md「形参 ≤5」）。"""

    action: str
    position_side: str
    gate_reverse: bool
    conviction: str | None = None   # 模型声明；None / 非法值 ⇒ 视为 normal（**绝不据它降级**）
    evidence: str | None = None
    intent_streak: int = 0
    intent_lapses: int = 0
    hold_seconds: float = 0.0
    overrides_used: int = 0
    # 只由 §4.4.2 的复核官分支用 `replace(inp, adjudicated=True)` 回填后**重跑同一个纯函数**；
    # W-2 那次调用恒传 False（复核官此刻还没跑）。它命中的是**独立档位**（判定表第 4 条），不是
    # `override` 的合取项——写成合取项会让它在唯一的判定落点上恒为 False，`level` 长期说谎。
    adjudicated: bool = False


@dataclass(frozen=True)
class ExitPriority:
    """一次判定的产物（不可变）；`wire` 为空 dict ⇒ 调用方**整键缺席**，不是 `null`。"""

    level: str
    released_gates: tuple[str, ...]
    declared: str
    downgrade_reason: str | None
    # 本轮是否允许挂起待裁决：**恒等于** `downgrade_reason == "ledger_short"` 且配额未尽
    # （X-4b）。判定收在纯核心里，会话侧只读它、**不重算**。
    may_pend: bool
    reason: str | None                    # 稳定放行码，如 exit_priority_override(final,2/2)
    wire: dict


def normalize_conviction(value) -> str:
    """归一模型声明；值域外 / 类型错 ⇒ `normal`（**一个读不懂的确信度不该把最安全的动作降级**）。"""
    text = str(value or "").strip()
    return text if text in CONVICTIONS else CONVICTION_NORMAL


def resolve_exit_priority(inp: ExitPriorityInput,
                          rule: ExitPriorityRule | None) -> ExitPriority:
    """§4.3.1 **冻结判定表**（自上而下取第一条命中）；一次求值的纯函数。

    | # | 条件 | level | 解闸 |
    |---|---|---|---|
    | 1 | `gated_kind(...) != "close"`（非平仓，含 `None` 与 `reverse`） | `none` | — |
    | 2 | 未装配（`rule is None` / 总闸关） | `normal` | — |
    | 3 | `final` ∧ 证据 ∧ 账本 ∧ 配额 ∧ 地板 | `override` | G1,G2,G3 |
    | 4 | `adjudicated`（复核官已背书） | `adjudicated` | G1,G2,G3 |
    | 5 | `conviction in (high, final)` **或** `intent_streak >= exit_confirm_rounds` | `expedite` | **G2** |
    | 6 | 其余 | `normal` | — |

    Args:
        inp: 本轮判定断面。
        rule: 已装配契约；`None` / `enabled=False` ⇒ 第 2 条。

    Returns:
        `ExitPriority`（含 `may_pend` 与 wire；`level=none` / 未装配 ⇒ `wire` 为空 dict）。
    """
    if gated_kind(inp.action, inp.position_side, gate_reverse=inp.gate_reverse) != "close":
        return _build(LEVEL_NONE, (), CONVICTION_NORMAL, None, inp, None)
    declared = normalize_conviction(inp.conviction)
    if rule is None or not rule.enabled:
        return _build(LEVEL_NORMAL, (), declared, None, inp, None)
    downgrade = _downgrade_reason(inp, rule, declared)
    if declared == CONVICTION_FINAL and downgrade is None:
        return _build(LEVEL_OVERRIDE, _ALL_GATES, declared, None, inp, rule)
    if inp.adjudicated:
        return _build(LEVEL_ADJUDICATED, _ALL_GATES, declared, downgrade, inp, rule)
    if (declared in (CONVICTION_HIGH, CONVICTION_FINAL)
            or int(inp.intent_streak) >= int(rule.exit_confirm_rounds)):
        return _build(LEVEL_EXPEDITE, _EXPEDITE_GATES, declared, downgrade, inp, rule)
    return _build(LEVEL_NORMAL, (), declared, downgrade, inp, rule)


def adjudicate(inp: ExitPriorityInput, rule: ExitPriorityRule | None) -> ExitPriority:
    """复核官 `confirm` 之后的**档位重算**（§4.4.2）：`replace(inp, adjudicated=True)` 再跑一次。

    刻意**重跑同一个纯函数**而不是就地把 `level` 字符串改掉——这样 `released_gates` / `reason` /
    `wire` 三者恒自洽；wire、`aiStats.horizon.*`、退出归因一律取这一份重跑结果。
    """
    return resolve_exit_priority(replace(inp, adjudicated=True), rule)


def _downgrade_reason(inp: ExitPriorityInput, rule: ExitPriorityRule,
                      declared: str) -> str | None:
    """`final` 声明未能兑现为 `override` 的第一条原因；非 `final` 声明 ⇒ `None`（无降档可言）。

    **静默降档会让模型在下一轮重复完全相同的错误**，故本码必须如实进 prompt 与轨迹：
    「你声明了 final，但因为 X 本轮按 high 处理」是模型修正行为的唯一依据。
    """
    if declared != CONVICTION_FINAL:
        return None
    if not _evidence_ok(inp.evidence, rule.evidence_min_chars):
        return DOWNGRADE_EVIDENCE
    if int(inp.intent_streak) < int(rule.override_rounds):
        return DOWNGRADE_LEDGER
    if int(inp.overrides_used) >= int(rule.max_overrides_per_session):
        return DOWNGRADE_QUOTA
    if float(inp.hold_seconds) < float(rule.override_min_hold_s):
        return DOWNGRADE_MIN_HOLD
    return None


def _evidence_ok(evidence: str | None, min_chars: int) -> bool:
    """证据是否达标：去空白后长度 ≥ 门槛（门槛 0 ⇒ 只要非空）。"""
    text = str(evidence or "").strip()
    return len(text) >= max(1, int(min_chars or 0))


def _may_pend(downgrade: str | None, inp: ExitPriorityInput,
              rule: ExitPriorityRule | None) -> bool:
    """本轮是否允许挂起待裁决（【R:P0-1】）。

    **只对 `ledger_short` 开启**：四种降档原因里只有「跨轮账本还不够厚」是复核官看得见证据、有
    资格替代的判断。另外三种若也走裁决，等于一次删掉 X-4 四个合取项中的三个——会话配额、证据
    要求、60 秒绝对地板全部可被复核官一句 `confirm` 抹掉。配额还须**在自增之前**再判一次
    （X-4b），否则「配额用尽 ⇒ 走裁决 ⇒ 配额继续涨」会把上限变成一个只在文档里存在的数字。
    """
    if rule is None or downgrade != DOWNGRADE_LEDGER:
        return False
    return int(inp.overrides_used) < int(rule.max_overrides_per_session)


def _reason_code(level: str, declared: str, inp: ExitPriorityInput,
                 rule: ExitPriorityRule | None) -> str | None:
    """稳定放行码（进 `exitPriority.reason` 与 trace；**绝不进 `degrade`**，§5.5）。"""
    if rule is None or level in (LEVEL_NONE, LEVEL_NORMAL):
        return None
    progress = f"{int(inp.intent_streak)}/{int(rule.override_rounds)}"
    if level == LEVEL_OVERRIDE:
        return f"exit_priority_override({declared},{progress})"
    if level == LEVEL_ADJUDICATED:
        return f"exit_adjudicated({progress})"
    return f"exit_priority_expedite({declared})"


def _build(level: str, gates: tuple[str, ...], declared: str, downgrade: str | None,
           inp: ExitPriorityInput, rule: ExitPriorityRule | None) -> ExitPriority:
    """装配产物 + §5.3 wire；`level=none` 或未装配 ⇒ wire 为**空 dict**（调用方整键缺席）。"""
    may_pend = _may_pend(downgrade, inp, rule)
    reason = _reason_code(level, declared, inp, rule)
    wire: dict = {}
    if rule is not None and level != LEVEL_NONE:
        used, cap = int(inp.overrides_used), int(rule.max_overrides_per_session)
        wire = {
            "level": level, "declared": declared, "downgradeReason": downgrade,
            "releasedGates": list(gates),
            "intentStreak": int(inp.intent_streak), "intentLapses": int(inp.intent_lapses),
            "holdSeconds": round(float(inp.hold_seconds), 3),
            "overrideRounds": int(rule.override_rounds),
            "overrideMinHoldSeconds": float(rule.override_min_hold_s),
            "overridesUsed": used, "overridesRemaining": max(0, cap - used),
            "maxOverridesPerSession": cap,
            "adjudicated": level == LEVEL_ADJUDICATED,
            "mayPend": may_pend, "pending": False,
            # 非 null ⇒ 本笔离场**未经复核官复核**（基础设施失败下的 fail-open 放行，【R:P1-3】）。
            # 由会话侧在反思之后回填；它**绝不进 `degrade`**——放行不是降级。
            "failOpenReason": None,
            "reason": reason,
        }
    return ExitPriority(level=level, released_gates=gates, declared=declared,
                        downgrade_reason=downgrade, may_pend=may_pend, reason=reason,
                        wire=wire)


def rule_wire(rule: ExitPriorityRule | None) -> dict | None:
    """契约 → camelCase 回执镜像（落 `horizon.rule_wire.exitPriority`）；未装配 ⇒ `None`。"""
    if rule is None or not rule.enabled:
        return None
    return {"enabled": True, "overrideRounds": rule.override_rounds,
            "overrideMinHoldSeconds": rule.override_min_hold_s,
            "maxOverridesPerSession": rule.max_overrides_per_session,
            "evidenceMinChars": rule.evidence_min_chars}


def rule_from_settings(settings, *, exit_confirm_rounds: int) -> ExitPriorityRule | None:
    """运维总闸 → 契约（**唯一装配点**，沿用 `_resolve_turnover` 的既有姿态）；关 ⇒ `None`。

    `exit_confirm_rounds` 由调用方从 `HorizonRule` **原样投影**——同一个数在一个会话里只能有一份
    真源，本层绝不按目标区间再派生一次。
    """
    if not getattr(settings, "ai_trade_exit_priority_enabled", False):
        return None
    return ExitPriorityRule(
        enabled=True, exit_confirm_rounds=max(1, int(exit_confirm_rounds)),
        override_rounds=int(settings.ai_trade_exit_override_rounds),
        override_min_hold_s=float(settings.ai_trade_exit_override_min_hold_s),
        max_overrides_per_session=int(settings.ai_trade_exit_override_max_per_session),
        evidence_min_chars=int(settings.ai_trade_exit_evidence_min_chars))


def horizon_projection(settings, *, confirm_window_s: float,
                       exit_confirm_rounds: int) -> dict:
    """`HorizonRule` 三个投影字段的**唯一回填点**（§5.2）；总闸关 ⇒ `{}` ⇒ 三者全走默认（逐字回退）。

    由 `hold_horizon.resolve_horizon_rule` 经**延迟导入**调用（本模块 import `hold_horizon` 取
    `gated_kind`，反向必须延迟，沿用 `_acceptance_preview` 打破环的既有姿态）。

    Args:
        settings: 运行配置（提供总闸与 C-2 / C-3 两个派生入参）。
        confirm_window_s: 会话**派生后**的 G2 墙钟窗口——`exit_pending` 上界按它现算，绝不按预设
            的建议周期算（与 `expected_hold_bars` 同一条纪律）。
        exit_confirm_rounds: 会话实际生效的连续确认轮数（原样投影，本层不复算）。
    """
    from .exit_intent import derive_exit_pending_max_s

    if not getattr(settings, "ai_trade_exit_priority_enabled", False):
        return {}
    return {
        "exit_intent_decay_rounds": int(settings.ai_trade_exit_intent_decay_rounds),
        "exit_pending_max_s": derive_exit_pending_max_s(
            confirm_window_s, settings.ai_trade_exit_pending_release_mult),
        "exit_priority": rule_from_settings(settings,
                                            exit_confirm_rounds=exit_confirm_rounds),
    }


def profiles_block(settings) -> dict | None:
    """`GET /ai-trade/horizon-profiles` 的只读说明块（§6.2）；总闸关 ⇒ `None` ⇒ 前端整块不渲染。

    标签、阈值与那句安全承诺**全部由后端下发**，前端一个字、一个数都不硬编（X-8 / R-8：一张
    前后端各存一份的表必然漂移）。
    """
    rule = rule_from_settings(settings, exit_confirm_rounds=2)
    if rule is None:
        return None
    return {
        "enabled": True, "overrideRounds": rule.override_rounds,
        "overrideMinHoldSeconds": rule.override_min_hold_s,
        "maxOverridesPerSession": rule.max_overrides_per_session,
        "evidenceMinChars": rule.evidence_min_chars,
        "levels": [
            {"id": CONVICTION_HIGH, "label": "加速离场",
             "detail": "跳过「离场连续确认」这一条约束（换手迟滞仍生效）"},
            {"id": CONVICTION_FINAL, "label": "最高优先级离场",
             "detail": "跳过全部三条约束立即执行；须给出新证据，本会话配额有限"},
        ],
        "note": "本通道只放行平仓，永不放行开仓；交易所侧止损止盈不受任何影响。",
    }
