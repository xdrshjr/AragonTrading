"""开仓侧换手纪律纯核心（ai-trade-turnover-budget-and-backstop §2.2）。

**本轮的一句话安全不变式（放行条件 6）：本模块新增的任何一道闸，都不得让某笔已有持仓比不装
本轮时更晚离场。** 从空仓开仓被拦不涉及任何持仓；反手被拦只否掉再入场腿、平仓腿照常执行；
同向加仓恒直通。三种断面里没有一种会推迟一笔已有持仓的退出。

与 `hold_horizon`（只约束平仓）严格互补：本模块**只约束建立新敞口的那一腿**，
`close` / `hold` / `cancel_order` 永不被拦。三条不可动摇的不变式：

1. **绝不阻止任何一次退出**——把平仓能力交给一个换手预算去管，等于让一个统计口径获得持仓
   生杀权，那是另一个特性、需要单独的安全评审。
   ⚠ [P0-1] 这条不变式**不能**靠「动作名不是 `close`」来保证：在本系统里「持空头时
   `open_long`」就是**反手**，它含一条平仓腿；把它降级为 `hold` 会把持仓留在原地。故本模块
   的判定式**必须看当前持仓方向**（见 `apply_turnover_gate` 的三分支）。
2. **fail-open**——阈为 None / 契约未装配 / 时钟异常一律放行。本层拦的是「再开一单」，
   而 fail-closed 的代价是一台永不交易的机器（`hold-inertia` 那一轮的教训）。
3. **本模块不 import `hold_horizon`**（[P1-9]）：依赖方向恒为 `hold_horizon → turnover_discipline`
   （`resolve_horizon_rule` 要调这里的派生式）。本模块只 import `acceptance_text`（拿
   `TURNOVER_HARD_CAP`，该模块零 ai_auto 依赖）与 `decider.TradeDecision`（与 `profit_cooldown`
   同款，且为打破 `decider → prompts → *_text` 的潜在环而**延迟导入**）。

`TurnoverState` **绝不可挂进 `HorizonState`**：后者在每次平仓 / 空仓被整体重置，令牌桶挂进去
会在每笔平仓时被清空，G5 就变成一个永远满桶的摆设（R-5）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace

from .acceptance_text import TURNOVER_HARD_CAP

_SECONDS_PER_DAY = 86400.0

# 建立**新敞口**的两个动作。注意它们只是候选集：是否真的受约束，取决于当前持仓方向（见下）。
_OPEN_ACTIONS = ("open_long", "open_short")
_POSITION_SIDES = ("long", "short", "flat")
_OPEN_FOR_SIDE = {"long": "open_long", "short": "open_short"}

REASON_REENTRY = "horizon_reentry_cooldown"
REASON_BUDGET = "horizon_turnover_budget"

# 反手被降级时追加的后缀标记（轨迹里可数、可事后核对；R-9）。
REVERSE_MARKER = "reverse→close"
# `position_side` 落在值域外时的如实记号（fail-open，不静默）。
SKIP_UNKNOWN_SIDE = "unknown_side"

# 三种断面（[P0-1] 的判定式按它分支）。
OPEN_FLAT = "flat_open"
OPEN_REVERSE = "reverse"
OPEN_SCALE_IN = "scale_in"

# 令牌桶的作用域：会话内存态、启动即满桶 ⇒ 停会话重开即回满（[P1-5] / D-13）。
BUDGET_SCOPE_SESSION = "session"


@dataclass(frozen=True)
class TurnoverRule:
    """会话级不可变契约（由 `HorizonRule` 的四个新字段直接投影，不二次解析）。

    Attributes:
        reentry_cooldown_s: G4 再入场冷却时长（秒）；`0` ⇒ 本闸结构性关闭。
        reentry_scope: `same_side`（只拦被平掉的那一侧，默认）| `both`。
        per_day: G5 速率（次/日）；`None` ⇒ 本闸结构性关闭。
        capacity: 令牌桶容量（突发额度）；`<=0` ⇒ 本闸结构性关闭。
    """

    reentry_cooldown_s: float = 0.0
    reentry_scope: str = "same_side"
    per_day: float | None = None
    capacity: float = 0.0


@dataclass(frozen=True)
class TurnoverState:
    """跨 epoch 存活的运行态（**绝不可挂进 `HorizonState`**，见模块 docstring）。

    Attributes:
        tokens: 当前令牌（装配时初始化为 `capacity`，[P1-5] 冷启动不惩罚）。
        last_refill_wall: 上一次补令牌的时刻（**单调不回退**，[P1-8]）。
        cooldown_until_wall: G4 冷却结束时刻。
        cooldown_side: 被平掉的方向（`same_side` 作用域用；真源是 epoch，[P0-4]）。
        reentry_vetoes / budget_vetoes: 两道闸各自**真的拦下**的次数。
        opens_counted: 消耗过令牌的新 epoch 数（L0 runbook 的「闸在跑」判据，[P2-8]）。
    """

    tokens: float = 0.0
    last_refill_wall: float = 0.0
    cooldown_until_wall: float = 0.0
    cooldown_side: str = ""
    reentry_vetoes: int = 0
    budget_vetoes: int = 0
    opens_counted: int = 0


# ==================== §2.2.1 派生规则（唯一真源，不得手抄第二遍） ====================
def derive_turnover_per_day(
        target_hold_min_s: float | None) -> tuple[float | None, float | None]:
    """（[P1-4]）**一次调用同时给出未夹与已夹两个值**：`(capped, raw)`。

    `raw = 86400 / 目标下界`（下界未设 ⇒ `(None, None)`）；
    `capped = min(raw, TURNOVER_HARD_CAP)`。

    为什么必须返回两个：`horizon_acceptance.AcceptanceThresholds` 有**两个**字段——
    `max_turnover_per_day`（已夹，判定用）与 `derived_turnover_per_day`（未夹，只给
    `acceptance_text.turnover_note` 那句「本模式派生阈高达 240 次/日、实质恒真」的自认用）。
    只搬走已夹的那一个，`86400 / lo` 就必然仍留在 `horizon_acceptance` 里，放行条件 3
    的「grep 自证不存在第二处」永远过不了。

    这是**验收卡第 3 行的同一个阈值**：`resolve_thresholds` 与 `resolve_horizon_rule`
    都调本函数，使「弹窗里承诺的门槛」「运行期执行的预算」「事后验收的判据」三处恒为同一个数。
    """
    lo = float(target_hold_min_s or 0.0)
    if lo <= 0:
        return None, None
    raw = _SECONDS_PER_DAY / lo
    return min(raw, TURNOVER_HARD_CAP), raw


def derive_max_turnover_per_day(target_hold_min_s: float | None) -> float | None:
    """`derive_turnover_per_day(...)[0]` 的便捷别名（G5 速率的唯一读点）。"""
    return derive_turnover_per_day(target_hold_min_s)[0]


def derive_reentry_cooldown_s(min_hold_s: float | None, *, frac: float) -> float:
    """再入场冷却 = `frac × 最短主动平仓窗口`（frac 默认 0.5 ⇒ 目标下界的 1/4）。

    取 `min_hold_s` 而非目标下界：G4 与 G1 是同一件事的两半（一次往返的「不许太早平」与
    「不许太快重开」），用同一个基准才能让两者的量纲天然一致；`min_hold_s <= 0`（custom
    全默认）⇒ `0` ⇒ 本闸结构性关闭，与「不装配」逐字等价。
    """
    return max(0.0, float(frac) * float(min_hold_s or 0.0))


def derive_turnover_capacity(per_day: float | None, *, burst_frac: float,
                             min_capacity: float) -> float:
    """令牌桶容量 = `max(min_capacity, ceil(per_day × burst_frac))`；`per_day is None` ⇒ 0。

    `min_capacity` 默认 **2** 是一个刻意的安全阀，不是凑整：`trend_hold` 的速率是 1 次/日，
    若容量也取 1，则「开仓 → 2 小时后止损出场」之后，会话要**整整 22 小时**开不出下一单。
    那既不符合用户对「趋势持仓」的预期，也正是本仓库 `hold-inertia` 那一轮踩过的「永不开单」
    形态。容量只影响**突发**，不影响长期速率——留 2 枚突发额度，长期换手率仍恒 ≤ per_day。
    """
    if per_day is None or float(per_day) <= 0:
        return 0.0
    return float(max(float(min_capacity),
                     math.ceil(float(per_day) * float(burst_frac))))


def rule_from_horizon(rule, settings) -> TurnoverRule | None:
    """`HorizonRule` → `TurnoverRule` 投影（**鸭子类型**读四个字段，不 isinstance、不 import 类型）。

    四个字段的唯一回填点是 `hold_horizon.resolve_horizon_rule`（它调上面三条派生式），本函数
    只做投影与总闸判定，绝不二次派生——两处各派生一份就是本仓库反复出事的那类分叉。

    Returns:
        `TurnoverRule`；总闸关 / 未装配 / 两道闸都结构性关闭 ⇒ `None`（整层空转、wire 整键缺席）。
    """
    if rule is None or not getattr(rule, "enabled", False):
        return None
    if not bool(getattr(settings, "ai_trade_horizon_turnover_enabled", True)):
        return None
    cooldown = max(0.0, float(getattr(rule, "reentry_cooldown_s", 0.0) or 0.0))
    raw_per_day = getattr(rule, "turnover_per_day", None)
    per_day = None if raw_per_day is None or float(raw_per_day) <= 0 else float(raw_per_day)
    capacity = max(0.0, float(getattr(rule, "turnover_capacity", 0.0) or 0.0))
    if per_day is None or capacity <= 0:      # 半个令牌桶不是桶，两者缺一即整闸关闭
        per_day, capacity = None, 0.0
    if cooldown <= 0 and per_day is None:
        return None
    scope = str(getattr(rule, "reentry_scope", "same_side") or "same_side")
    return TurnoverRule(reentry_cooldown_s=cooldown,
                        reentry_scope="both" if scope == "both" else "same_side",
                        per_day=per_day, capacity=capacity)


# ==================== §2.2.2 状态推进（全部纯函数、幂等） ====================
def init_state(rule: TurnoverRule | None, *, now_wall: float) -> TurnoverState:
    """装配即满桶（[P1-5]）：冷启动惩罚会在最需要建立第一个持仓的时刻把会话自己拦住。"""
    if rule is None:
        return TurnoverState(last_refill_wall=float(now_wall))
    return TurnoverState(tokens=float(rule.capacity), last_refill_wall=float(now_wall))


def refill(state: TurnoverState, rule: TurnoverRule | None, *,
           now_wall: float) -> TurnoverState:
    """按经过时间补令牌并夹到容量；**只在读取点调用**，不跑后台定时器。

    [P1-8] `elapsed` 必须夹到非负、`last_refill_wall` 必须**单调**：回测的 `note_open` 传的是
    **开仓根 ts**，epoch 迟采纳时它会早于上一次 refill 的时刻。不夹就会出现「令牌不增反减且
    不报错」；只夹 `elapsed` 而回写一个更早的 `last_refill_wall`，则会在下一次读取时把同一段
    时间补第二遍。
    """
    if rule is None or rule.per_day is None or rule.capacity <= 0:
        return state
    elapsed = max(0.0, float(now_wall) - float(state.last_refill_wall))
    tokens = min(float(rule.capacity),
                 float(state.tokens) + elapsed * float(rule.per_day) / _SECONDS_PER_DAY)
    return replace(state, tokens=tokens,
                   last_refill_wall=max(float(state.last_refill_wall), float(now_wall)))


def on_open_fill(state: TurnoverState, rule: TurnoverRule | None, *,
                 now_wall: float) -> TurnoverState:
    """建立新 epoch 的开仓成交：先 refill 再扣一枚（**允许为 0，不允许为负**）。

    允许为负会让一次「令牌恰好 1.0 的开仓」把桶打到 −1 并额外多冻结一个补充周期，用户无法从
    任何一个数上推出这段多出来的等待是怎么来的。

    加仓（同 epoch 的后续成交）**不到这里**：调用方 `HorizonRuntime.note_open` 已经保证了
    「同 `(side, seq)` 不重复建 epoch」。预算的单位是「往返」，与验收卡第 3 行的分子
    `roundTrips` 严格同单位；换了单位，弹窗承诺的那个数就对不上运行期的行为了（D-4）。
    """
    if rule is None:
        return state
    filled = refill(state, rule, now_wall=now_wall)
    tokens = max(0.0, float(filled.tokens) - 1.0) if rule.per_day is not None else filled.tokens
    return replace(filled, tokens=tokens, opens_counted=filled.opens_counted + 1)


def on_close_fill(state: TurnoverState, rule: TurnoverRule | None, *,
                  closed_side: str, now_wall: float) -> TurnoverState:
    """平仓成交：开启 G4 冷却。

    [P0-4] `closed_side` 的真源是 **epoch**，不是成交字典——实盘 close 成交的 `trade["side"]`
    恒为字符串 `"close"`，回测干脆传空字典。方向取不到（`epoch is None` / 从未建立）⇒
    **不开启冷却**（宁可不拦，也不拿一个猜的方向去拦）。
    """
    if rule is None or rule.reentry_cooldown_s <= 0:
        return state
    if closed_side not in ("long", "short"):
        return state
    filled = refill(state, rule, now_wall=now_wall)
    return replace(filled,
                   cooldown_until_wall=float(now_wall) + float(rule.reentry_cooldown_s),
                   cooldown_side=closed_side)


def note_veto(state: TurnoverState, reason: str | None) -> TurnoverState:
    """把一次**真的拦下**记进本层自己的计数器。

    [P2-7] 绝不并进 `HorizonState.blocked_streak` / `HorizonRuntime.vetoes`——那两个是第 1 轮的
    冻结口径，混进来会让 G-2 的 AST 冻结门禁虽过、语义却已漂移。
    """
    if reason is None:
        return state
    if reason.startswith(REASON_REENTRY):
        return replace(state, reentry_vetoes=state.reentry_vetoes + 1)
    if reason.startswith(REASON_BUDGET):
        return replace(state, budget_vetoes=state.budget_vetoes + 1)
    return state


# ==================== §5.4 wire ====================
def build_wire(state: TurnoverState, rule: TurnoverRule | None, *,
               now_wall: float) -> dict | None:
    """`horizon.turnover` 的 wire；未装配 ⇒ `None` ⇒ 整键缺席 ⇒ prompt 逐字节回退。

    零样本口径：未激活的读数一律 `null`，**绝不用 `0` 冒充**（T-13 守它）。
    """
    if rule is None:
        return None
    cooldown_active = _is_cooldown_active(state, rule, now_wall=now_wall)
    budget_enabled = rule.per_day is not None and rule.capacity > 0
    exhausted = bool(budget_enabled and float(state.tokens) < 1.0)
    next_token = None
    if exhausted:
        next_token = int(math.ceil((1.0 - float(state.tokens))
                                   * _SECONDS_PER_DAY / float(rule.per_day)))
    return {
        "cooldownActive": cooldown_active,
        "cooldownRemainingSeconds": (
            round(max(0.0, float(state.cooldown_until_wall) - float(now_wall)), 3)
            if cooldown_active else None),
        "cooldownSide": state.cooldown_side if cooldown_active else None,
        "cooldownScope": rule.reentry_scope,
        "cooldownSeconds": rule.reentry_cooldown_s if rule.reentry_cooldown_s > 0 else None,
        "budgetEnabled": budget_enabled,
        # [P1-5] 恒 "session"：会话重启即回满，三处文案必须照此措辞（弹窗 / 卡片 / 验收自认）。
        "budgetScope": BUDGET_SCOPE_SESSION,
        "tokens": round(float(state.tokens), 4) if budget_enabled else None,
        "capacity": rule.capacity if budget_enabled else None,
        "perDay": rule.per_day,
        "nextTokenSeconds": next_token,
        # 「若本轮提议开新仓，**至少一个方向**会被拦」——`same_side` 作用域下具体是哪一侧，
        # 由 `cooldownSide` / `cooldownScope` 两键如实给出，本键刻意不替 UI 做方向推断。
        "blocked": bool(cooldown_active or exhausted),
        # [P0-1]「若本轮提议反手，再入场腿会被否、只执行平仓腿」。
        "reverseWouldDowngrade": bool(cooldown_active or exhausted),
        "reentryVetoes": state.reentry_vetoes,
        "budgetVetoes": state.budget_vetoes,
        "opensCounted": state.opens_counted,
    }


def bump_wire_vetoes(wire: dict | None, state: TurnoverState) -> None:
    """[P2-2] **原地** mutate 同一个快照 dict，使轨迹与 decision 事件不落后一轮。

    `apply_horizon_trace` 在 `_apply_horizon_gate` 内部就把快照挂进了本轮 trace，而换手闸排在
    它之后；由于 trace 存的是同一个 dict 引用，此处原地改写即可让两处同步（S-17 守它）。
    """
    if isinstance(wire, dict):
        wire["reentryVetoes"] = state.reentry_vetoes
        wire["budgetVetoes"] = state.budget_vetoes


# ==================== §2.2.2 判定 ====================
def gate_skip_reason(position_side: str) -> str | None:
    """`position_side` 的值域只有 `long` / `short` / `flat`；其余 ⇒ 恒放行并如实记号。"""
    return None if position_side in _POSITION_SIDES else SKIP_UNKNOWN_SIDE


def open_kind(position_side: str, action: str) -> str | None:
    """本轮开仓动作落在哪一格（[P0-1] 三分支的唯一判定点）；非开仓动作 ⇒ `None`。"""
    if action not in _OPEN_ACTIONS:
        return None
    if position_side == "flat":
        return OPEN_FLAT
    if position_side in ("long", "short"):
        return OPEN_SCALE_IN if action == _OPEN_FOR_SIDE[position_side] else OPEN_REVERSE
    return None


def apply_turnover_gate(decision, wire: dict | None, rule: TurnoverRule | None, *,
                        position_side: str) -> tuple[object, str | None]:
    """G4/G5 判定：按**当前持仓方向**三分支（[P0-1]），绝不阻止任何一次退出。

    | 断面 | 语义 | 本层动作 |
    |---|---|---|
    | `flat` + 开仓 | 新建敞口 | 命中 ⇒ 降级为 `hold` |
    | 反向开仓 | **反手**（含一条平仓腿） | 命中 ⇒ 降级为 **`close`**（保留退出腿） |
    | 同向开仓 | 加仓（不建新 epoch、不消耗令牌） | **恒直通**（同一对象），归 G6 管 |

    反手为什么是 `close` 而不是整类放行：若整类放行，「开多 → 持够 G1 → 反手做空 → 持够 G1 →
    反手做多」这条链的换手率仍是 `86400 / min_hold_s`，缺口 #1 只被堵掉一半。降级为 `close`
    同时满足两件事：**退出永远能执行**（不变式 1）与**再入场受预算约束**（本轮目标），且
    `flat` 是三种持仓态里风险最低的一种，故本降级严格单向更保守（D-11）。

    判定顺序 **先 G4 后 G5**：冷却是「刚刚发生了一件事」、预算是「一段时间的总量」，前者对
    用户更具体、更可解释（沿用「更具体的原因优先」这一既有取舍）。

    Args:
        decision: 本轮决策（已过铁律 / 周期 / 冷却三层门禁）。
        wire: `build_wire` 产物；`None` / 空 ⇒ 恒直通。
        rule: 已装配契约；`None` ⇒ 恒直通。
        position_side: 当前**投影立场**（实盘 `executor.projected`，回测 `pos.side`）。

    Returns:
        `(decision, gate_reason)`——未降级时返回**同一个** decision 对象（`is` 相等）。
    """
    if rule is None or not wire or gate_skip_reason(position_side) is not None:
        return decision, None
    kind = open_kind(position_side, decision.action)
    if kind is None or kind == OPEN_SCALE_IN:
        return decision, None
    parts = _reentry_parts(wire, rule, action=decision.action) or _budget_parts(wire)
    if parts is None:
        return decision, None
    prefix, params = parts
    if kind == OPEN_REVERSE:
        reason = _reason(prefix, [*params, REVERSE_MARKER])
        return _downgrade(decision, "close", reason), reason
    reason = _reason(prefix, params)
    return _downgrade(decision, "hold", reason), reason


def _is_cooldown_active(state: TurnoverState, rule: TurnoverRule, *,
                        now_wall: float) -> bool:
    """G4 是否仍在生效（纯时间驱动，无清理副作用；方向未知 ⇒ 视为未激活）。"""
    if rule.reentry_cooldown_s <= 0 or state.cooldown_side not in ("long", "short"):
        return False
    return float(now_wall) < float(state.cooldown_until_wall)


def _reentry_parts(wire: dict, rule: TurnoverRule,
                   *, action: str) -> tuple[str, list[str]] | None:
    """G4：冷却激活且作用域命中 ⇒ 降级码参数；否则 `None`。"""
    if not wire.get("cooldownActive"):
        return None
    side = str(wire.get("cooldownSide") or "")
    scope = str(wire.get("cooldownScope") or rule.reentry_scope)
    if scope != "both":
        if side not in _OPEN_FOR_SIDE or action != _OPEN_FOR_SIDE[side]:
            return None
    remaining = int(round(float(wire.get("cooldownRemainingSeconds") or 0.0)))
    return REASON_REENTRY, [f"{remaining}s", side or scope]


def _budget_parts(wire: dict) -> tuple[str, list[str]] | None:
    """G5：令牌不足 ⇒ 降级码参数（**必须带「下一枚何时到账」**）；否则 `None`。

    没有第二个参数，用户与模型都只能看到一句「被拦了」而无法判断该等还是该改配置——
    `format_profit_cooldown` 的既有教训（只说被拦会让模型每轮重复提议、每轮被拦）。
    """
    if not wire.get("budgetEnabled"):
        return None
    tokens = float(wire.get("tokens") or 0.0)
    if tokens >= 1.0:
        return None
    params = [f"{tokens:.1f}/{float(wire.get('capacity') or 0.0):g}"]
    next_s = wire.get("nextTokenSeconds")
    if next_s is not None:
        params.append(f"next={int(math.ceil(float(next_s) / 60.0))}m")
    return REASON_BUDGET, params


def _reason(prefix: str, params: list[str]) -> str:
    return f"{prefix}({','.join(params)})"


def _downgrade(decision, action: str, reason: str):
    """降级为 `hold`（空仓开仓）或 `close`（反手，只否再入场腿）。

    延迟导入 `TradeDecision` 打破 `decider → prompts → *_text` 的潜在环（与 `hold_horizon._veto`
    同款姿态）。
    """
    from .decider import TradeDecision

    return TradeDecision(action=action, size_fraction=None, confidence=None,
                         reason=reason)
