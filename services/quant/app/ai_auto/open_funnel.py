"""开仓漏斗的**纯核心**（ai-trade-tradability-and-open-funnel 轴 C）。

全仓此前**没有分母**：`discipline_runtime._gate_counts()` 提供 11 个拦截计数，却没有任何一处统计
「模型这一轮到底想不想开仓」。后果是 `strictness_hint` 只能在「成交数 == 0 且轮数 ≥ 8」时给一句
概括性提示，而它给出的补救方向（改选更宽松的预设）在短周期断面上是**错的**。本模块补上那个分母，
把「想开 N 次 → 各层拦掉多少 → 真发出多少委托 → 真成交多少」做成一张能闭合的图。

三条不可动摇的纪律（改本文件前先读）：

1. **只统计开仓意愿轮**（[P0-3]）。持仓周期铁律层的作用域是**平仓与反手**
   （`hold_horizon.gated_kind:558-573` 第一条就是 `action == "close"`），一轮 `close` 被
   `horizon_min_hold` 拦下时 `degrade` 非空、而 `intents` 没有 +1。若只看 `degrade` 归类，
   `sum(blocked)` 会超过 `intents`，而这张图的**全部**价值就是它闭合。判据只能来自
   「本轮记没记过 intent」这个事实（`_intent_pending`），**不能**来自层名——**反手**
   （`open_long` 遇空头仓）被 horizon 拦下时确实是一次开仓意图、**应当**计入。
2. **`sizing_zeroed` 不是一道闸**（[P0-4a]）。`_apply_sizing` 不返回 `degrade`，把 `sizingZero`
   留在 `LAYERS` 里就是一条永远点不亮的死枚举。它由独立计数承载，画在「委托」前的最后一格，
   **不进 `blocked`**。
3. **`low_confidence` 只计一次**（[P0-4b]）。它的唯一落点是 `blocked["confidence"]`；
   `llm_degraded` 的口径收窄为**仅** LLM 故障（`llm_error` / `decision_timeout` / 空产出），
   中文标签用「模型调用失败」而不是「降级」——`config.py:157` 明确把 `low_confidence` 与
   「合法动作 / 刻意 hold」并列为 genuine 决策、不计入重试。
4. **方向切分是对同一批事件的重新分组，不是第二次计数**（directional-symmetry 轴 D）。
   `*_by_side` 三侧求和必须逐字等于既有的四个标量；`unknown` **单列一格、绝不猜方向**
   ——`low_confidence` 轮此刻 `action` 已被 `_map_decision` 洗成 `hold`，方向在系统里从来
   没有出现过（实测该桶占开仓意愿的 21%）。而 `filled_by_side` 的方向来自**成交腿自己的
   `side`**，不是 `_intent_side`：`note_filled` 排在 `note_executed`（已清 `_intent_pending`）
   之后、还可能跨轮到达，读轮级标志会让两个计数恒为 0 且不报任何错。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4

_OPEN_ACTIONS = ("open_long", "open_short")

# 方向轴的三格（directional-symmetry 轴 D）：**固定三格**，`unknown` 单列、绝不并进任何一侧。
SIDES = ("long", "short", "unknown")
UNKNOWN_SIDE = "unknown"

# `direction_verdict` 判「样本不足」的下界；生产调用点传 `ai_trade_direction_balance_min_samples`。
DEFAULT_DIRECTION_MIN_SAMPLES = 8
# 弱侧占比低于此值即判方向塌缩（两侧都有样本时的判据）。
_SKEW_SHARE = 0.15

# 决策链顺序（与 `session._run_decision_round` 的调用序逐字同构；顺序即漏斗层序）。
# 末位 `other` 承接未知前缀——**未知前缀归 other 而不是静默丢弃**（G-11）：静默丢弃会让
# `sum(blocked)` 小于真实拦截数，而这张图唯一的价值就是它闭合。
LAYERS = ("baseRuleMaBand", "orphanPosition", "horizon", "profitCooldown", "turnover",
          "exchangeBackstop", "drawdownBrake", "trendBias", "consensus", "confidence",
          "edgeGate", "evidenceGate", "reflection", "other")

LAYER_LABELS = {
    "baseRuleMaBand": "基础策略·MA 通道",
    # 孤儿持仓（fill-truth-and-effective-controls 轴 B）：它是比任何纪律都更靠前的**事实性**阻断
    # ——交易所侧存在一笔本会话不知道的持仓，此时再叠一笔自动仓位是本轮能想到的最坏结果。
    "orphanPosition": "交易所侧存在未接管持仓",
    "horizon": "持仓周期纪律",
    "profitCooldown": "保护性平仓冷却", "turnover": "换手预算",
    "exchangeBackstop": "交易所侧兜底", "drawdownBrake": "回撤刹车 / 连亏熔断",
    "trendBias": "趋势基调·逆势门槛",
    "consensus": "同向共识门槛", "confidence": "置信度门槛",
    "edgeGate": "最小 edge 门禁",
    # 实证准入（evidence-gated-capital §3.3.3）：edge 门禁问「波动够不够付成本」，本层问「这个方向
    # 过去有没有真的赚钱」。层序即调用序：排在 edge 门禁之后、二次反思之前。
    "evidenceGate": "实证准入",
    "reflection": "下单前二次反思", "other": "其它",
}

# 降级理由前缀 → 层 key（单点归类，沿用 `discipline_runtime._DEGRADE_PREFIXES` 的成文范式）。
# **顺序即优先级**：`horizon_turnover_*` / `horizon_reentry_*` / `horizon_backstop_*` 必须排在
# 裸 `horizon_` 之前，否则换手与兜底会被吞进持仓周期那一格。
_DEGRADE_PREFIXES: tuple[tuple[str, str], ...] = (
    ("base_rule_", "baseRuleMaBand"),
    # 与既有任何前缀都不撞；位置与 `LAYERS` 的层序同构（「顺序即优先级」要求新增条目显式声明）。
    ("orphan_position", "orphanPosition"),
    ("horizon_turnover_budget", "turnover"),
    ("horizon_turnover_hysteresis", "horizon"),
    ("horizon_reentry_cooldown", "turnover"),
    ("horizon_backstop_", "exchangeBackstop"),
    ("horizon_", "horizon"),
    ("profit_cooldown", "profitCooldown"),
    ("drawdown_brake", "drawdownBrake"),
    ("loss_streak", "drawdownBrake"),
    # 趋势基调·逆势门槛（execution-trend-verdict 轴 E）：`counter_trend_` 与既有任何前缀都不撞，
    # 位置只影响可读性——但「顺序即优先级」的纪律要求新增条目显式声明自己的位置，故与 `LAYERS`
    # 的层序同构地排在 `low_consensus` 之前。
    ("counter_trend_", "trendBias"),
    ("low_consensus", "consensus"),
    ("low_confidence", "confidence"),
    ("edge_below_cost", "edgeGate"),
    ("evidence_", "evidenceGate"),   # 与既有任何前缀都不撞（已 grep）
    ("reflection_", "reflection"),
)

# **只可能由开仓分支产出**的第一阶段降级码：见 `decider._map_decision`（`decider.py:393-398`），
# `low_confidence` 只在 `action in ("open_long","open_short")` 的分支里返回。它是模型的**真实
# 开仓意愿**被置信度门槛拦下，故必须计进漏斗分母——只按 `decision.action` 判会把它整条漏掉
# （此刻 action 已被洗成 hold）。`missing_size` 等码刻意**不在**集内：它们是 LLM 产出格式问题
# （在可重试白名单里），归 `llmDegraded` 才是实情。
_OPEN_ONLY_DEGRADES = ("low_confidence",)

# **仅** LLM 故障类降级码（[P0-4b] 收窄后的 `llmDegraded` 口径）。`low_confidence` 刻意不在集内。
# ⚠️ ai-trade-deep-reasoning-chain §3.3.3 把 `no_tool_call` 细分成了两个更诚实的码
# （`reasoning_truncated` = 输出预算耗尽在工具调用之前；`content_filtered` = 模型侧内容拦截）。
# 两者**必须一并登记**：同一个物理事件换了个码就掉出 `llmDegraded` 计数，是一次静默的观测回归。
_LLM_FAILURE_PREFIXES = ("llm_error", "decision_timeout", "no_tool_call", "invalid_action",
                         "missing_size", "missing_limit_price", "limit_price_out_of_band",
                         "invalid_order_type", "reasoning_truncated", "content_filtered")

# 两个口径词的共享常量（[P2-6]）：漏斗是**全程累计**，而归因账本 `AttributionLedger._trips` 是
# `deque(maxlen=200)` 的滚动窗口。两张卡并排时读者极易串口径，故两处文案各引用一个常量。
LIFETIME_SCOPE_TEXT = "当前运行段累计"

EXECUTION_REASONS = frozenset({
    "pre_submit_error", "canceled_before_send", "below_min_size",
    "existing_order_kept", "position_not_flat", "same_side_position",
    "exchange_rejected", "insufficient_balance", "attempt_persist_failed",
    "acceptance_unknown", "pre_submit_context_changed", "facts_unavailable",
    "pre_submit_price_drift",
    "quota_exhausted", "session_not_running", "other",
})


def wants_open(action: str, degrade: str | None) -> bool:
    """本轮是不是一次**真实的开仓意愿**（漏斗分母的**唯一**判据）。

    `low_confidence` 轮此刻 `action` 已被 `_map_decision` 洗成 `"hold"`
    （`decider.py:393-398` 只在开仓分支里返回这个码，实测占开仓意愿的 21%），故必须按码显式
    接住它——只按 `action` 判，置信度门禁在漏斗里将**恒不可见**。

    ⚠️ 本函数是这条判据的唯一落点：`OpenFunnelCounters.note_intent` 与
    `trend_runtime.TrendRuntime.note_intent`（execution-trend-verdict 评审 RP1-1）**共用它**。
    在第二处另写一遍，两个分母就会在下一次改动时静默分叉——而它们并排出现在同一张卡上。
    """
    return (action in _OPEN_ACTIONS and degrade is None) or degrade in _OPEN_ONLY_DEGRADES


def classify_block(degrade: str | None) -> str | None:
    """降级理由 → 首个拦下它的层 key；`degrade` 为空 ⇒ `None`；未知前缀 ⇒ `"other"`。"""
    if not degrade:
        return None
    for prefix, key in _DEGRADE_PREFIXES:
        if degrade.startswith(prefix):
            return key
    return "other"


def is_llm_failure(degrade: str | None) -> bool:
    """该降级码是否属于 **LLM 故障**（而不是模型的真实终态决策）。

    口径收窄的理由见模块 docstring 第 3 条：`low_confidence` 是 genuine 决策，把它算进
    「模型调用失败」既不准确，又会让同一轮在漏斗的两个格子里各出现一次。
    """
    return bool(degrade) and degrade.startswith(_LLM_FAILURE_PREFIXES)


def _bump(bucket: dict, key: str) -> None:
    """`bucket[key] += 1`（缺键即 0 起算）——三个 `*_by_side` 记点共用的唯一累加写法。"""
    bucket[key] = int(bucket.get(key, 0)) + 1


@dataclass
class OpenFunnelCounters:
    """会话级累计的开仓漏斗计数（**不进任何 deque**，口径 = `LIFETIME_SCOPE_TEXT`）。

    Attributes:
        intents: 第一阶段动作 ∈ `open_*` 且 `degrade is None` 的轮数（模型的真实开仓意愿）。
        blocked: 首个拦截层 → 次数（**只统计开仓意愿轮**）。
        llm_degraded: 第一阶段因 **LLM 故障**降级的轮数（不含 `low_confidence`）。
        submitted: 真的发出了开仓委托的轮数。
        filled: 真的成交了开仓腿的次数。
        sizing_zeroed: 过了全部门禁但仓位解到 0/None 的轮数（**不是一道闸**，见纪律 2）。
        intents_by_side: 方向 → 开仓意愿数（`long` / `short` / `unknown` 三格）。
        blocked_by_side: 方向 → {首个拦截层: 次数}。
        submitted_by_side: 方向 → 真的发出开仓委托的轮数。
        sizing_zeroed_by_side: 方向 → 仓位解到 0 的轮数。
        filled_by_side: 方向 → 开仓腿成交次数（**事件级**，方向取成交腿自己的 `side`）。
        expired: 限价开仓委托未成交即被撤销的次数（E-7；实测 602 笔限价开仓里 223 笔如此收场）。
        expired_by_side: 方向 → 同上（方向取**委托腿自己的** `side`，见 `note_expired`）。
        insufficient_balance: 开仓委托被交易所以 `INSUFFICIENT_BALANCE` 拒绝的次数（E-8 观测）。
        contrarian_passed: 走逆势通路放行的轮数。它**不是降级**，故不进 `blocked`
            （镜像 `sizing_zeroed` 不是一道闸的成文纪律）。
        _intent_pending: 轮级标志——本轮记过 intent 吗。它是本类**唯一**的跨调用状态，
            必须在轮末 `finally` 兜底清零（R-12：决策轮抛异常会让它带进下一轮，把下一轮的
            平仓侧降级错记成开仓拦截）。
        _intent_side: 轮级标志——本轮开仓意愿的方向。与 `_intent_pending` **同生同灭**，
            同一处清零（R-12 同款兜底）。
    """

    intents: int = 0
    blocked: dict = field(default_factory=dict)
    llm_degraded: int = 0
    submitted: int = 0
    filled: int = 0
    sizing_zeroed: int = 0
    _intent_pending: bool = False
    # ---- 方向轴（directional-symmetry 轴 D）：对同一批事件的**重新分组**，不是第二次计数 ----
    intents_by_side: dict = field(default_factory=dict)         # {side: n}
    blocked_by_side: dict = field(default_factory=dict)         # {side: {layer: n}}
    submitted_by_side: dict = field(default_factory=dict)       # {side: n}
    sizing_zeroed_by_side: dict = field(default_factory=dict)   # {side: n}
    filled_by_side: dict = field(default_factory=dict)          # {side: n}（**成交腿自己的 side**）
    contrarian_passed: int = 0                                  # 走逆势通路放行的轮数（不进 blocked）
    _intent_side: str | None = None                             # 轮级：本轮开仓意愿的方向
    # ---- E-7 / E-8（loss-and-liveness-hardening 轴 E）：委托发出之后的两格 ----
    expired: int = 0                                            # 限价开仓单未成交即撤（事件级）
    expired_by_side: dict = field(default_factory=dict)         # {side: n}（**委托腿自己的 side**）
    insufficient_balance: int = 0                               # 下单被交易所判可用保证金不足
    model_hold: int = 0
    execution_blocked: dict = field(default_factory=dict)
    execution_blocked_by_side: dict = field(default_factory=dict)
    outcome_unknown: int = 0
    outcome_unknown_by_side: dict = field(default_factory=dict)
    runtime_epoch_id: str = field(default_factory=lambda: uuid4().hex)
    _round: int = 0
    _outcomes: dict = field(default_factory=dict)

    def begin_round(self, round_no: int) -> None:
        """绑定运行段内轮号；跨进程回执不迁移本段计数。"""
        self.end_round()
        self._round = round_no

    def note_receipt(self, receipt: dict | None) -> None:
        """按真实开仓回执收口，一次意图只消费一次。"""
        if not self._intent_pending or not receipt:
            return
        status = receipt.get("status")
        side = self._take_intent_side()
        if status == "accepted":
            self.submitted += 1
            _bump(self.submitted_by_side, side)
        elif status == "unknown":
            self.outcome_unknown += 1
            _bump(self.outcome_unknown_by_side, side)
            attempt_id = receipt.get("attemptId")
            if attempt_id:
                self._outcomes[attempt_id] = (
                    side, self.runtime_epoch_id, self._round)
        else:
            self._note_execution_block(side, receipt.get("reason"))

    def resolve_outcome(self, attempt_id: str, status: str,
                        reason: str = "exchange_rejected") -> None:
        """将本段未知回执移到确认桶；重复及旧段回调均无副作用。"""
        if status not in ("accepted", "rejected", "not_submitted"):
            return
        outcome = self._outcomes.pop(attempt_id, None)
        if outcome is None or outcome[1] != self.runtime_epoch_id:
            return
        side = outcome[0]
        self.outcome_unknown -= 1
        self.outcome_unknown_by_side[side] -= 1
        if status == "accepted":
            self.submitted += 1
            _bump(self.submitted_by_side, side)
        else:
            self._note_execution_block(side, reason)

    def _note_execution_block(self, side: str, reason: str | None) -> None:
        if reason and reason.startswith("sizing_"):
            self.sizing_zeroed += 1
            _bump(self.sizing_zeroed_by_side, side)
            return
        key = reason if reason in EXECUTION_REASONS else "other"
        _bump(self.execution_blocked, key)
        _bump(self.execution_blocked_by_side.setdefault(side, {}), key)

    # ---------- 喂数（三个记点 + 一个轮末兜底）----------
    def note_intent(self, action: str, degrade: str | None) -> None:
        """第一阶段决策的收口：记分母 + 置位轮级标志。

        落点必须与 `HorizonRuntime.note_intent`（`horizon_runtime.py:197`）完全一致——`decide`
        返回后、**任何门禁之前**：门禁后的动作已被洗成 hold，在那里数分母永远是 0。

        **`low_confidence` 同样是一次真实的开仓意愿**（实施期发现，见 spec §实施过程发现的方案缺陷
        I-1）：`_map_decision` 只在 `action in ("open_long","open_short")` 分支里产出这个码
        （`decider.py:393-398`），此刻 `decision.action` 已被洗成 `hold`。若只按 `action` 判，
        置信度门禁在漏斗里将**恒不可见**（`_intent_pending` 为假 ⇒ `note_block` 第一行就返回），
        而 spec 的 G-10c 明确要求它的唯一落点是 `blocked["confidence"]`。故按码显式接住它。
        """
        opening = wants_open(action, degrade)
        self._intent_pending = opening
        # 轴 D：**绝不写 `action.removeprefix("open_")`**——`low_confidence` 那条路径此刻 action
        # 已被 `_map_decision` 洗成 `"hold"`，`removeprefix` 会原样返回它，凭空造出第四个桶。
        self._intent_side = (action[len("open_"):] if action in _OPEN_ACTIONS
                             else UNKNOWN_SIDE) if opening else None
        if opening:
            self.intents += 1
            _bump(self.intents_by_side, self._intent_side)
        elif is_llm_failure(degrade):
            self.llm_degraded += 1
        elif action == "hold" and degrade is None:
            self.model_hold += 1

    def note_block(self, degrade: str | None) -> None:
        """轮末归类：把本轮的首个拦截层记进 `blocked`；**非开仓意愿轮直接返回**（[P0-3]）。

        消费后立即清零 `_intent_pending`——它只服务本轮。
        """
        if not self._intent_pending:
            return
        key = classify_block(degrade)
        if key is None:
            return
        side = self._take_intent_side()
        self.blocked[key] = self.blocked.get(key, 0) + 1
        _bump(self.blocked_by_side.setdefault(side, {}), key)

    def note_sizing(self, action: str, size_fraction) -> None:
        """仓位求解之后的记点：过了全部门禁但解到 0/None ⇒ `sizing_zeroed`（[P0-4a]）。

        **必须带 `action`**：被门禁拦下的轮次此刻 `action` 已是 `hold`，只看 `size_fraction`
        会把每一轮观望都记成「仓位解到 0」。求解结果**不进 `blocked`**——它不是一道闸。
        """
        if not self._intent_pending or action not in _OPEN_ACTIONS:
            return
        if size_fraction is None or float(size_fraction) <= 0:
            side = self._take_intent_side()
            self.sizing_zeroed += 1
            _bump(self.sizing_zeroed_by_side, side)

    def note_executed(self, action: str, skipped: bool) -> None:
        """真的发出了开仓委托（`_execute_decision` 回执）。

        同样**必须带 `action`**：本记点排在轮末归类（`note_block`）**之前**，被门禁拦下的轮次
        此刻 `action` 已是 `hold`，不判就会把每一次拦截都记成一次委托，漏斗当场不闭合。
        """
        if self._intent_pending and action in _OPEN_ACTIONS and not skipped:
            side = self._take_intent_side()
            self.submitted += 1
            _bump(self.submitted_by_side, side)

    def note_filled(self, side: str) -> None:
        """开仓腿成交（`_dispatch` 里 `side ∈ ("long","short")` 的 trade 事件）。

        与 `discipline_runtime.hint` 数开仓成交的既有口径（`discipline_runtime.py:66`）**共用
        同一判据**，不得另写。它是事件级口径：一轮部分成交会产生多条腿。

        **方向必须取成交腿自己的 `side`**（directional-symmetry 轴 D / 评审 P0-1）：本记点排在
        `note_executed`（已把 `_intent_pending` 置 `False`）之后，一次部分成交还会产生多条腿、
        甚至跨轮到达，此刻 `_intent_side` 早已被清。照轮级标志去读，`filled_by_side` 恒为 0
        且**不报任何错**。调用点本来就有这个值，零新取数。

        Args:
            side: 成交腿的方向（`"long"` / `"short"`）；其它值一律归 `unknown`，绝不猜。
        """
        self.filled += 1
        _bump(self.filled_by_side, side if side in ("long", "short") else UNKNOWN_SIDE)

    def note_expired(self, side: str) -> None:
        """限价开仓委托未成交即被撤销（E-7；`filled` 的兄弟格，两者互斥）。

        **必须显式带方向**（评审 P2-4）：`expired` 是**跨轮事件级**读数——限价开仓单常在数轮之后
        才被撤，那时 `_intent_pending` / `_intent_side` 早已被 `note_executed` 清零。照轮级标志去读，
        `expired_by_side` 会**恒为 0 且不报任何错**，正是 `note_filled` 文档注释里写下的那个坑。
        调用点（撤单回执）本来就持有委托腿的 `side`，零新取数。

        **不进 `blocked`**：委托已经发出去了，这不是任何一道闸拦的——它画在漏斗的「委托」之后、
        与 `filled` 并列，故 `sum(blocked) + submitted + sizingZeroed <= intents` 的闭合不变式不受影响。

        Args:
            side: 委托腿的方向（`"long"` / `"short"`）；其它值一律归 `unknown`，绝不猜。
        """
        self.expired += 1
        _bump(self.expired_by_side, side if side in ("long", "short") else UNKNOWN_SIDE)

    def note_insufficient_balance(self) -> None:
        """开仓委托被交易所判「可用余额 / 保证金不足」（E-8 **纯观测**）。

        本轮**不改任何一次真实提交张数**：仓位求解结果超过可用保证金时今天以报错收场（实测 53 次），
        而 `LiveExecutor` 全文没有任何可用保证金读数，要预检就得在下单热路径上多打一发 REST。
        先把它变成一个看得见的数，下一轮才有数据回答「降一档到底够不够」。
        """
        self.insufficient_balance += 1

    def note_contrarian(self) -> None:
        """逆势通路放行了一轮（directional-symmetry 轴 A）。

        **它不是降级**，故独立计数、不进 `blocked`（镜像 `sizing_zeroed` 不是一道闸的纪律）。
        本轮随后仍会走 `note_executed` / `note_block`，方向轴的闭合不变式不受影响。
        """
        self.contrarian_passed += 1

    def end_round(self, receipt: dict | None = None,
                  reason: str = "pre_submit_error") -> None:
        """幂等收口：已确认回执优先，未发送异常不能冒充未知委托。"""
        self.note_receipt(receipt)
        if self._intent_pending:
            self._note_execution_block(self._take_intent_side(), reason)
        self._intent_pending = False
        self._intent_side = None

    def _take_intent_side(self) -> str:
        """消费本轮的方向标志（**与 `_intent_pending` 同生同灭**），返回它；未置位 ⇒ `unknown`。"""
        side = self._intent_side or UNKNOWN_SIDE
        self._intent_pending = False
        self._intent_side = None
        return side


def top_blocker(counters: OpenFunnelCounters) -> tuple[str, int] | None:
    """拦得最多的那一层 `(key, count)`；一次都没拦过 ⇒ `None`。

    并列时按 `LAYERS` 的决策链顺序取**更靠前**的那个——漏斗的语义是「第一个拦下它的闸」，
    并列时归给更早的闸与该语义一致，且使结果对字典插入序稳定（可断言）。
    """
    ranked = [(key, int(counters.blocked.get(key, 0))) for key in LAYERS
              if counters.blocked.get(key)]
    if not ranked:
        return None
    return max(ranked, key=lambda row: row[1])


def conversion(counters: OpenFunnelCounters) -> float | None:
    """开仓意愿 → 成交的转化率；`intents == 0` ⇒ `None`（**绝不**把 0/0 画成 0%）。"""
    if counters.intents <= 0:
        return None
    return round(counters.filled / counters.intents, 6)


def direction_verdict(counters: OpenFunnelCounters,
                      *, min_samples: int = DEFAULT_DIRECTION_MIN_SAMPLES) -> str:
    """方向分布判定的**唯一真源**（前端只做布局，一个判据都不复制）。

    **分母口径钉死**：`directed = intents_by_side["long"] + intents_by_side["short"]`，
    `unknown` 一律不进分母、也不进任何占比。理由是实测该桶占开仓意愿的 21%——`low_confidence`
    轮在 `_map_decision` 里就被洗成 hold，方向在系统里从来没有出现过。把它算进分母，
    「模型从未提出过做空」与「模型提过做空但在方向可见之前就被置信度门槛拦掉了」这两个
    **对应完全相反补救动作**的结论会被混成同一个数。

    Args:
        counters: 会话级漏斗计数。
        min_samples: 判「样本不足」的下界（生产传 `ai_trade_direction_balance_min_samples`）。

    Returns:
        `insufficient` | `long_skewed` | `short_skewed` | `balanced`。
    """
    long_n = int(counters.intents_by_side.get("long", 0))
    short_n = int(counters.intents_by_side.get("short", 0))
    directed = long_n + short_n
    if directed < max(1, int(min_samples)):
        return "insufficient"
    weak = min(long_n, short_n)
    if weak == 0 or weak / directed < _SKEW_SHARE:
        return "long_skewed" if long_n > short_n else "short_skewed"
    return "balanced"


def _share(numerator: int, denominator: int) -> float | None:
    """占比；分母为 0 ⇒ `None`（**绝不**把 0/0 写成 0.0——那是另一个结论）。"""
    if denominator <= 0:
        return None
    return round(numerator / denominator, 6)


def by_side_wire(counters: OpenFunnelCounters) -> dict:
    """三格方向切分的只读 wire（固定三格恒存在，缺席的一侧是一排真实的 0，不是「没有这个数」）。"""
    return {side: {
        "intents": int(counters.intents_by_side.get(side, 0)),
        "blocked": {key: int(value)
                    for key, value in (counters.blocked_by_side.get(side) or {}).items() if value},
        "sizingZeroed": int(counters.sizing_zeroed_by_side.get(side, 0)),
        "submitted": int(counters.submitted_by_side.get(side, 0)),
        "executionBlocked": dict(
            counters.execution_blocked_by_side.get(side, {})),
        "outcomeUnknown": int(counters.outcome_unknown_by_side.get(side, 0)),
        "filled": int(counters.filled_by_side.get(side, 0)),
        "expired": int(counters.expired_by_side.get(side, 0)),
    } for side in SIDES}


def direction_balance_wire(counters: OpenFunnelCounters,
                           *, min_samples: int = DEFAULT_DIRECTION_MIN_SAMPLES) -> dict:
    """方向分布卡片的只读 wire（结论文案由前端按 `verdict` 分支，判据只有这一份）。

    `unknownIntents` **必须下发**：不披露它，`verdict` 就是一句数据不支持的断言（RK-11）。
    """
    long_n = int(counters.intents_by_side.get("long", 0))
    short_n = int(counters.intents_by_side.get("short", 0))
    directed = long_n + short_n
    long_filled = int(counters.filled_by_side.get("long", 0))
    short_filled = int(counters.filled_by_side.get("short", 0))
    return {
        "longIntents": long_n, "shortIntents": short_n,
        "longFilled": long_filled, "shortFilled": short_filled,
        "shortIntentShare": _share(short_n, directed),
        "shortFillShare": _share(short_filled, long_filled + short_filled),
        "unknownIntents": int(counters.intents_by_side.get(UNKNOWN_SIDE, 0)),
        "unknownFilled": int(counters.filled_by_side.get(UNKNOWN_SIDE, 0)),
        "contrarianPassed": int(counters.contrarian_passed),
        "minSamples": max(1, int(min_samples)),
        "verdict": direction_verdict(counters, min_samples=min_samples),
    }


def funnel_wire(counters: OpenFunnelCounters | None, *, directional: bool = False,
                min_samples: int = DEFAULT_DIRECTION_MIN_SAMPLES) -> dict | None:
    """`status.openFunnel` / `aiStats.openFunnel` 共用的 camelCase 只读 wire；`None` ⇒ `None`。

    三条 wire 级不变式（G-10b 逐条守）：
    1. `sum(blocked.values()) + submitted + sizingZeroed <= intents`——漏斗必须闭合。
    2. `llmDegraded` 只计 LLM 故障；`low_confidence` 的唯一落点是 `blocked["confidence"]`。
    3. `sizingZeroed` 不进 `blocked`（它不是一道闸，是求解结果）。
    4. `bySide` 三侧求和逐字等于上面四个标量——方向切分是**重新分组**，不是第二次计数。

    Args:
        counters: 会话级漏斗计数；`None` ⇒ `None`。
        directional: 方向对称性总闸。`False`（默认）⇒ `bySide` / `directionBalance` **整键缺席**
            ⇒ wire 逐字节等于本特性合入前（前端渲染门控据此整卡不渲染）。
        min_samples: `verdict` 判「样本不足」的下界。
    """
    if counters is None:
        return None
    top = top_blocker(counters)
    directional_keys = ({"bySide": by_side_wire(counters),
                         "directionBalance": direction_balance_wire(
                             counters, min_samples=min_samples)} if directional else {})
    return {
        "intents": counters.intents,
        "blocked": {key: int(value) for key, value in counters.blocked.items() if value},
        "topBlocker": (None if top is None
                       else {"key": top[0], "label": LAYER_LABELS.get(top[0], top[0]),
                             "count": top[1]}),
        "llmDegraded": counters.llm_degraded,
        "modelHold": counters.model_hold,
        "sizingZeroed": counters.sizing_zeroed,
        "submitted": counters.submitted,
        "executionBlocked": dict(counters.execution_blocked),
        "outcomeUnknown": counters.outcome_unknown,
        "schemaVersion": 2,
        "runtimeEpochId": counters.runtime_epoch_id,
        "coverage": "partial" if counters._intent_pending else "complete",
        "filled": counters.filled,
        # E-7 / E-8：委托之后的两格。`expired` 与 `filled` 互斥且都在「委托」之后，故闭合不变式
        # （`sum(blocked) + submitted + sizingZeroed <= intents`）逐字不变。
        "expired": counters.expired,
        "insufficientBalance": counters.insufficient_balance,
        "conversion": conversion(counters),
        "layers": list(LAYERS),
        "scope": LIFETIME_SCOPE_TEXT,
        **directional_keys,
    }


def funnel_advice(counters: OpenFunnelCounters | None,
                  verdict_level: str | None = None) -> str | None:
    """漏斗视角的对症建议；样本不足 / 已经开过仓 ⇒ `None`（不下无据的结论）。

    这条建议**只回答「谁在拦」**，「换周期还是换预设」由 `discipline_view.strictness_hint`
    在更高一层按 `tradability_level` 决定——两处口径必须分工明确，否则会给出两句互相矛盾的建议。
    """
    if counters is None or counters.filled > 0:
        return None
    if counters.intents <= 0:
        return None
    top = top_blocker(counters)
    if top is None:
        return None
    label = LAYER_LABELS.get(top[0], top[0])
    tail = ("；而本周期上的可交易性预检已判定为「不可行」，首要动作是换更长的时间级别"
            if verdict_level == "infeasible" else "")
    return (f"{LIFETIME_SCOPE_TEXT}：模型提出过 {counters.intents} 次开仓意图，"
            f"其中 {top[1]} 次被「{label}」拦下{tail}。")
