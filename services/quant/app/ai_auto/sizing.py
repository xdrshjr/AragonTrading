"""波动率目标化仓位 + ATR 自适应止损的**纯核心**（ai-trade-vol-sizing-and-drawdown-brake §3.3/§3.4）。

归因恒等式 `E = p·W − (1−p)·L − C` 里，阶段一把 `C` 记进了账、阶段二第一步把 `p` 建成了可测量的先验，
到此为止「值不值得做」已可判定，而「该下多大注」仍完全由 LLM 的一句 `sizeFraction` 决定。本模块就是
把下注规模从「模型叙事决定」改造为「确定性计算决定」的那一层：

    f_core = min(凯利项 × λ, 波动率目标项, f_max)
    f      = min(f_core, 模型提议) × 刹车系数 × 相关性系数，最后保守选择合法档位

**刹车与相关性的作用位置（实施期修正，见 spec §「实施过程发现的方案缺陷」D-1）**：两个乘性系数必须
作用在「与模型提议取小**之后**」的结果上，而不是只缩系统上限 `f_core`。原因是 `f_core` 在两项都不可算
时退化为 `f_max = 1.0`，而系统 prompt 把模型引导到 `sizeFraction ≤ 0.5`——只缩上限的话，最常见的断面
（模型给 0.25、上限 1.0）里 `1.0 × 0.5 = 0.5 > 0.25`，第一档减速与相关性缩减**一点作用都没有**，而
`brakeSlowRounds` 照样计数。spec §3.3 步骤 5/6 的字面顺序与它自己的 [P1-2]、§3.6 档位表、T-23、§八 L3
判据互相矛盾，此处按后者（也是唯一自洽的那个）实现。单调收敛不变式 S-1 不受影响：两个系数恒 ≤ 1。

四条不可动摇的不变式（改本文件前先读）：

1. **只收不放（S-1）**：任何路径下的输出都 ≤ 进入本层时的模型提议——本层与模型提议取 `min`，绝不放大。
2. **无可行正仓位必须拒绝**：纯函数返回零占比和原因，运行时在信号生成前转为 hold。
3. **凯利赔率的 `W`/`L` 恒取本轮实际要挂的那对括号（S-12）**：本模块**不 import `edge_gate`**，也绝不
   读门禁的 edge 读数——那个量的语义随来源变（ATR 时是「若走对能吃到多少」的条件幅度，先验时已是按
   胜率加权的期望），当成赔率再乘一次 `p` 就是对同一个胜率收两次税。`W`/`L` 只能来自 §3.4 解出的、
   马上就要挂到交易所侧的 `StopPlan`（ATR 止损未装配时退回会话构造期的固定 pct）。
4. **杠杆刻意不进波动率目标项的分母**：`f` 是「已含杠杆的满仓名义额」的比例，账户级敞口上限由既有的
   `budget × leverage` 与分组天花板负责。把 `leverage` 再乘进分母等于对同一条红线收两次税——3× 杠杆下
   `f_vol` 会被压到 0.06，永远落到最小格，本层退化为常数。

档位真源仍为 `position_ladder`；风险解低于最低档时拒绝开仓，不能抬高风险解。
模型提议的就近吸附语义由 `signals.quantize_size_fraction` 保留。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

from .decider import TradeDecision
from .profit_guard import sanitize_atr
from .signals import position_ladder

# 受本层约束的动作（豁免矩阵冻结，与 `edge_gate._GATED_ACTIONS` / `base_rules._GATED_ACTIONS` 逐字同构）。
_GATED_ACTIONS = ("open_long", "open_short")

# 一年的秒数（365 天；加密永续 7×24 无休市，故不用交易日口径）。
_SECONDS_PER_YEAR = 31_536_000.0

# 读数统一小数位（避免浮点尾巴污染 wire 与断言；不改变任何判定量级）。
_ROUND = 6
_EPS = 1e-12


@dataclass(frozen=True)
class SizingRule:
    """仓位求解规则（总闸① `ai_trade_sizing_enabled`；关 ⇒ `parse_sizing_rule` 返回 None）。

    Attributes:
        vol_target_annual: 年化波动率目标（fraction）；`0` ⇒ 波动率目标项不参与 `min`。
        vol_window: 已实现波动率回看根数（需要 `window+1` 根收盘价）。
        kelly_lambda: 凯利缩放（四分之一凯利 = 0.25）；`0` ⇒ 凯利项不参与 `min`。
        f_max: 占比上界（沿用 1.0，不新增红线）。
        exposure_cap_mult: 有效敞口上限倍数；`0` ⇒ 相关性预算不生效（§3.5）。
        corr_min_samples: 相关性最小重叠样本数；不足即按 `ρ=1`（最保守）。
    """

    vol_target_annual: float
    vol_window: int
    kelly_lambda: float
    f_max: float
    exposure_cap_mult: float
    corr_min_samples: int


@dataclass(frozen=True)
class StopRule:
    """ATR 止损规则（总闸② `ai_trade_stop_atr_mult > 0`；**与总闸①完全独立**，runbook L1 只开这一个）。

    [P1-1] 独立解析是硬约束：把 `stop_atr_mult` 塞进 `SizingRule` 会让「只开 ATR 止损、不开仓位层」这一
    级恒不可达，整个放量阶梯从第一级起就是空话。

    Attributes:
        stop_atr_mult: 止损距离的 ATR 倍数（`<=0` ⇒ `parse_stop_rule` 返回 None）。
        stop_rr: 盈亏比（止盈 = `stop_rr × 止损`）。
        max_sl_pct: 止损比例的既有用户可见红线（`ai_trade_max_sl_pct`），本层只能在其内活动。
        max_tp_pct: 止盈比例的既有用户可见红线（`ai_trade_max_tp_pct`）。
        cost_floor_mult: **成本感知的止损下界**倍数（tradability-and-open-funnel 轴 B）：
            止损距离不得落进 `cost_floor_mult × 往返成本` 以内。`0` ⇒ 逐字回退合入前行为。
            实测依据：BTC 1m 上 `2×ATR` 的止损距离中位 11.2 bps，而一笔市价往返成本 13.0 bps
            ——**62.3% 的止损被打掉时亏的价格幅度还不如手续费多**，那是一台结构性手续费机器。
    """

    stop_atr_mult: float
    stop_rr: float
    max_sl_pct: float
    max_tp_pct: float
    cost_floor_mult: float = 0.0


@dataclass(frozen=True)
class StopPlan:
    """本轮解出的、**马上就要挂到交易所侧**的那一对括号（同时也是凯利赔率的 `W`/`L`，[P0-2]）。"""

    stop_pct: float
    take_pct: float
    stop_bps: float
    take_bps: float
    # "atr" | "atr_floored"（后者 = 本轮止损被成本下界抬起来了，轴 B）。
    # 不可用时调用方拿到的是 `(None, skip_reason)`，而非一个假 plan。
    source: str


@dataclass(frozen=True)
class SizingInputs:
    """一次仓位求解的全部入参（全部显式，使本模块保持纯函数）。

    Attributes:
        action: 本轮动作（仅 `open_long`/`open_short` 需要求解）。
        model_fraction: 模型给出、已过反思与档位吸附的占比；本层与它取 `min`。
        p_win: 先验胜率；`None` ⇒ 凯利项不参与 `min`（**绝不**用 0.5 之类的默认值凭空造先验）。
        cost_bps: 往返成本（bps，毛口径）；`None` ⇒ 凯利项不参与 `min`。
        stop_bps: 本轮**实际要挂的**止损距离（bps）——凯利的 `L`。
        take_bps: 本轮**实际要挂的**止盈距离（bps）——凯利的 `W`（[P0-2] 的分子）。
        realized_vol_bar: 每根已实现波动率；`None` ⇒ 波动率目标项不参与 `min`。
        brake_mult: 回撤刹车系数 ∈ (0,1]。
        corr_scale: 相关性缩减系数 ∈ [0,1]。
        step: 档位步长（吸附用），取自 `decider.position_step`。
    """

    action: str
    model_fraction: float | None
    p_win: float | None
    cost_bps: float | None
    stop_bps: float | None
    take_bps: float | None
    realized_vol_bar: float | None
    brake_mult: float = 1.0
    corr_scale: float = 1.0
    step: float = 0.0


@dataclass(frozen=True)
class SizingOutcome:
    """一次求解的产物；`fraction is None` ⇒ 本层不作用（调用方原样直通，`decision` 对象 `is` 相等）。

    Attributes:
        fraction: 最终占比（保守取档，无合法正档时为零）。
        fraction_raw: **吸附前**的解——§7.3 的「仓位-波动率秩相关」只能用它（[P2-7]：吸附后默认只有
            2~3 个取值，秩相关在统计上不可评估）。
        f_vol: 波动率目标项；`None` = 不参与 `min`。
        f_kelly: 凯利项（已乘 λ）；`None` = 不参与 `min`。
        kelly_status: ok | no_prior | no_cost | no_bracket | disabled。
        scale_source: model | vol | kelly | brake | correlation——本层是否真的绑定过（§7.3 的对偶监控）。
        scale_erased: 缩减被档位地板整个吃掉了（[P1-2] 的观测量）。
        vol_source: realized | insufficient_samples | disabled（[P1-6]：暖机不足与配错了不是一回事）。
        floor_verdict: none | solved_zero | below_gear_floor | exposure_cap |
            invalid_quantity，表示最终风险判定。`none` 以外的取值一律伴随
            `fraction == 0.0`，且调用方**必须**把决策改成 hold（见 `sizing_runtime.resolve`）。
        wire: 本产物自身的 camelCase 只读读数（`sizing_wire` 再与 plan/brake/exposure 合成整段）。
    """

    fraction: float | None
    fraction_raw: float | None = None
    f_vol: float | None = None
    f_kelly: float | None = None
    kelly_status: str = "disabled"
    scale_source: str = "model"
    scale_erased: bool = False
    vol_source: str = "disabled"
    floor_verdict: str = "none"
    wire: dict = field(default_factory=dict)


@dataclass(frozen=True)
class SizingFloorPolicy:
    """保留旧地板设置的解析兼容性；关闭它们不能绕过最终风险上限。

    Attributes:
        zero_is_veto: 开启时把极小正解也视为零；非正风险量始终拒绝。
        floor_ratio: `0 < f_raw < step × floor_ratio` 也否决。**默认 0.0 ⇒ 该判据逐字不存在**。
    """

    zero_is_veto: bool = False
    floor_ratio: float = 0.0


# ============================ 规则解析（两条**各自独立**，[P1-1]）============================
def parse_sizing_rule(settings) -> SizingRule | None:
    """总闸① 关 ⇒ `None`（本层的仓位求解整体不装配，`f` 逐字回退模型提议）。"""
    if not getattr(settings, "ai_trade_sizing_enabled", False):
        return None
    return SizingRule(
        vol_target_annual=max(0.0, float(settings.ai_trade_vol_target_annual)),
        vol_window=max(1, int(settings.ai_trade_realized_vol_window)),
        kelly_lambda=max(0.0, float(settings.ai_trade_kelly_lambda)),
        f_max=1.0,
        exposure_cap_mult=max(0.0, float(settings.ai_trade_effective_exposure_cap_mult)),
        corr_min_samples=max(1, int(settings.ai_trade_correlation_min_samples)))


def parse_stop_rule(settings) -> StopRule | None:
    """总闸② `ai_trade_stop_atr_mult <= 0` ⇒ `None` ⇒ OCO 逐字回退固定 pct。

    **不受总闸①门控**（[P1-1]）：runbook 的 L1 是「只开 ATR 止损、不开仓位层」，若这里读了
    `sizing_enabled`，那一级恒不可达。
    """
    mult = float(getattr(settings, "ai_trade_stop_atr_mult", 0.0) or 0.0)
    if mult <= 0:
        return None
    return StopRule(stop_atr_mult=mult, stop_rr=max(0.0, float(settings.ai_trade_stop_rr)),
                    max_sl_pct=float(settings.ai_trade_max_sl_pct),
                    max_tp_pct=float(settings.ai_trade_max_tp_pct),
                    cost_floor_mult=max(0.0, float(
                        getattr(settings, "ai_trade_stop_cost_floor_mult", 0.0) or 0.0)))


def window_need(rule: SizingRule | None) -> int:
    """本层需要的收盘价根数 `max(vol_window, corr_min_samples) + 1`；未装配 ⇒ `0`。

    **这个式子在全仓只允许有这一份**（`manager.py:696`「绝不在此复制一份 `_window_max` 公式——
    复制即漂移」的同款纪律）。`check_window_capacity` 的 422 判据、`SizingRuntime.window_need` 的
    只读属性、以及纪律预设的 `primingBars` 抬升（discipline-preset-and-integrity R-2）三处都读它：
    运维把 `AI_TRADE_CORRELATION_MIN_SAMPLES` 从 96 调到 200 时，三处必须一起变——任何一处写死
    常数，都会让启动被自己的 422 打回，且现场看不出是谁算错了。
    """
    if rule is None:
        return 0
    return max(int(rule.vol_window), int(rule.corr_min_samples)) + 1


def check_window_capacity(rule: SizingRule | None, window_max: int) -> str | None:
    """启动期对账（[P1-6]）：窗口装不下回看根数 ⇒ 返回中文错误文案，调用方据此启动失败（422）。

    `MarketWindow._window_max` 最小可到 80，而默认回看是 96——不在启动期拦住，`realized_vol_bar` 会
    恒 `None`、相关性恒 `ρ=1`，两个旗舰机制**永久静默失效且不报任何错**。运行期的暖机不足是另一回事，
    由 `vol_source="insufficient_samples"` 如实标注。
    """
    if rule is None:
        return None
    need = window_need(rule)
    if need <= int(window_max):
        return None
    return (f"行情窗口容量不足：仓位层需要 {need} 根收盘价，当前窗口上限只有 {window_max} 根。"
            f"请把启动参数 primingBars 调大到 ≥ {need}，或把 AI_TRADE_REALIZED_VOL_WINDOW / "
            f"AI_TRADE_CORRELATION_MIN_SAMPLES 调小到 ≤ {int(window_max) - 1}")


# ============================ 量纲折算与已实现波动率 ============================
def annual_to_bar_vol(vol_target_annual: float, bar_seconds: int) -> float | None:
    """年化 ⇄ 单根波动率折算的**唯一真源**（禁止第二处）：`σ_bar = σ_annual / sqrt(bars_per_year)`。

    15m 周期下 `bars_per_year = 35040`、`sqrt ≈ 187.2`、`σ_target_bar ≈ 0.107%`。`≤0` 一律 `None`
    （= 该项不参与 `min`，而不是「目标为 0 ⇒ 仓位归零」）。
    """
    target = _finite(vol_target_annual)
    secs = _finite(bar_seconds)
    if target is None or target <= 0 or secs is None or secs <= 0:
        return None
    return target / math.sqrt(_SECONDS_PER_YEAR / secs)


def realized_vol_bar(closes, window: int) -> tuple[float | None, str]:
    """**每根**对数收益的样本标准差 + 取数来源（`realized` / `insufficient_samples`）。

    需要 `window+1` 根收盘价才能产出 `window` 个收益；不足、含非正价、或算出非有限值一律
    `(None, "insufficient_samples")`——暖机期样本没攒够是**正常**的，但必须看得见（[P1-6]）。
    """
    if window < 1:
        return None, "insufficient_samples"
    rows = [px for px in (_finite(c) for c in (closes or [])) if px is not None and px > 0]
    if len(rows) < window + 1:
        return None, "insufficient_samples"
    tail = rows[-(window + 1):]
    rets = [math.log(tail[i] / tail[i - 1]) for i in range(1, len(tail))]
    if len(rets) < 2:
        return None, "insufficient_samples"
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)      # 样本方差（n−1）
    sigma = math.sqrt(var) if var > 0 else 0.0
    if not math.isfinite(sigma) or sigma <= 0:
        return None, "insufficient_samples"
    return sigma, "realized"


# ============================ 凯利项（赔率与本层自己挂的括号同源，[P0-2]）============================
def kelly_fraction(*, p_win: float | None, take_bps: float | None, cost_bps: float | None,
                   stop_bps: float | None) -> tuple[float | None, str]:
    """未经 λ 缩放的凯利占比 + 降级码；不可算一律 `(None, code)`，**绝不翻译成「仓位归零」**。

    ```
    b     = (take_bps − cost_bps) / stop_bps      # 净期望赢距离 ÷ 止损距离
    kelly = (p·b − (1−p)) / b  ≡  p − (1−p)/b
    ```

    **诚实声明**：把 `take_bps` 当作 `W` 是一个**乐观上界**——真实持仓常被模型主动平仓或被利润保护层
    提前锁定，realized 的赢面小于挂出去的止盈距离。缓解有三层：λ 默认 0.25、波动率目标项的独立 `min`、
    以及 §7.3 的「名义 RR vs 实测 RR」验收指标（实测长期低于名义的一半 ⇒ **下调 λ**，而不是继续用
    乐观分子）。

    `b <= 0`（成本吃掉了整个止盈距离）同样归 `no_bracket` 且**不参与 min**：那是最小 edge 门禁的判定域
    （已在管线第 9 步），在这里再判一次就是双重计费——「双重计费导致开仓率骤降为 0」是明确的回滚触发条件。
    """
    p = _finite(p_win)
    if p is None or not (0.0 < p < 1.0):
        return None, "no_prior"
    cost = _finite(cost_bps)
    if cost is None:
        return None, "no_cost"
    take, stop = _positive(take_bps), _positive(stop_bps)
    if take is None or stop is None:
        return None, "no_bracket"
    odds = (take - cost) / stop
    if odds <= 0:
        return None, "no_bracket"
    return max(0.0, p - (1.0 - p) / odds), "ok"


# ============================ ATR 自适应止损止盈（§3.4）============================
def resolve_stop_distance(*, atr_px, ref_px, rule: StopRule | None,
                          max_sl_pct: float | None = None,
                          max_tp_pct: float | None = None,
                          cost_bps: float | None = None
                          ) -> tuple[StopPlan | None, str | None]:
    """本轮的 ATR 止损/止盈距离 + **没覆盖的原因**；`(None, None)` ⇒ 调用方一行都不改。

    `sanitize_atr` 直接复用利润保护层的那一条（与实盘 `safe_atr`、回测 `_atr_at` 同一条降级判据），
    **禁止另写**。夹紧上界取既有的用户可见红线（`ai_trade_max_sl_pct` / `ai_trade_max_tp_pct`）；
    形参留空即取 `rule` 自带的那一份（两者恒同源于 settings）。

    **返回二元组而不是裸 `None`（[P0-2]）**：裸 `None` 把「为什么没覆盖」这个信息擦掉了——
    `sizing_runtime._apply_stop` 对 `plan is None` 的两条路径都只能得到 `"fixed"`，调用方无从
    区分「ATR 不可用」与「下界高于用户红线」。那会让 `floor_above_cap` 这个状态在当前代码上
    **产生不出来**（有读点、没写点）。故：

    - 不可用（rule 缺席 / ATR 无效 / px 无效）⇒ `(None, None)` —— 与合入前语义**逐字相同**；
    - 成本下界高于用户可见红线 `sl_cap` ⇒ `(None, "floor_above_cap")`。

    三段顺序**不可交换**：下界 → 上界 → 冲突判定。冲突时**不落 `StopPlan` 而是整条覆盖回退**：
    `sl_cap`（`ai_trade_max_sl_pct`，默认 0.20）是用户可见红线，若成本下界比红线还大，说明这个
    标的/周期的成本结构已经彻底不可行，此时挂一个「刚好等于红线」的止损是**假装可行**。

    Args:
        atr_px: 确认根 ATR（价格单位）。
        ref_px: 判定价。
        rule: 止损规则；`None` / `stop_atr_mult<=0` ⇒ 本层未装配。
        max_sl_pct: 止损红线覆盖（留空取 `rule.max_sl_pct`）。
        max_tp_pct: 止盈红线覆盖（留空取 `rule.max_tp_pct`）。
        cost_bps: 本轮往返成本（bps）；`None` / `<=0` ⇒ 下界分支**整体不进**（轴 B 不生效）。

    Returns:
        `(plan, skip_reason)`。
    """
    if rule is None or rule.stop_atr_mult <= 0:
        return None, None
    atr = sanitize_atr(atr_px)
    px = _positive(ref_px)
    if atr is None or px is None:
        return None, None
    sl_cap = _positive(rule.max_sl_pct if max_sl_pct is None else max_sl_pct)
    tp_cap = _positive(rule.max_tp_pct if max_tp_pct is None else max_tp_pct)
    stop_pct = rule.stop_atr_mult * atr / px
    floor_pct = None
    floored = False
    cost = _positive(cost_bps)
    if rule.cost_floor_mult > 0 and cost is not None:
        floor_pct = rule.cost_floor_mult * cost / 1e4
        if floor_pct > stop_pct:
            stop_pct, floored = floor_pct, True
    if sl_cap is not None:
        if floor_pct is not None and floor_pct > sl_cap:
            return None, "floor_above_cap"
        stop_pct = min(stop_pct, sl_cap)
    take_pct = rule.stop_rr * stop_pct
    if tp_cap is not None:
        take_pct = min(take_pct, tp_cap)
    if stop_pct <= 0 or take_pct <= 0:
        return None, None
    return StopPlan(stop_pct=round(stop_pct, _ROUND), take_pct=round(take_pct, _ROUND),
                    stop_bps=round(stop_pct * 1e4, _ROUND), take_bps=round(take_pct * 1e4, _ROUND),
                    source="atr_floored" if floored else "atr"), None


# ============================ 求解（§3.3 的七步）============================
def resolve_size_fraction(inputs: SizingInputs, rule: SizingRule, bar_seconds: int,
                          policy: "SizingFloorPolicy | None" = None) -> SizingOutcome:
    """解出本轮下单占比；无合法正档返回否决，运行时负责生成 hold。

    步骤与 §3.3 逐条对应：动作白名单 → 波动率目标项 → 凯利项 → 取小夹紧 → 刹车与相关性 → 与模型提议
    取小 → 地板判据 → 保守取档。缺失的估计项不参与 min，非有限最终量拒绝。

    Args:
        inputs: 本轮断面。
        rule: 本会话的仓位规则。
        bar_seconds: 每根秒数（波动率年化 ↔ 每根的换算）。
        policy: 旧地板阈值；缺席或关闭不能绕过连续风险上限。
    """
    if inputs.action not in _GATED_ACTIONS:
        return SizingOutcome(fraction=None)
    f_vol, vol_source = _vol_term(inputs.realized_vol_bar, rule, bar_seconds)
    f_kelly, kelly_status = _kelly_term(inputs, rule)
    f_max = max(0.0, float(rule.f_max))
    terms = [(name, value) for name, value in (("vol", f_vol), ("kelly", f_kelly))
             if value is not None]
    f_core = min([value for _, value in terms] + [f_max])
    core_source = next((name for name, value in terms if value <= f_core + _EPS), None)
    brake = _clamp01(inputs.brake_mult)
    corr = _clamp01(inputs.corr_scale)
    # 刹车与相关性作用在**与模型提议取小之后**的结果上，而不是只作用于系统上限（见模块 docstring 的
    # 「刹车与相关性的作用位置」）：只缩上限时，模型给 0.25 而上限是 1.0 的最常见断面上，×0.5 的第一档
    # 减速一点作用都没有——那正是本层要治的东西。
    entry = inputs.model_fraction if inputs.model_fraction is not None else f_core
    valid = all(_finite(value) is not None for value in
                (entry, f_core, inputs.brake_mult, inputs.corr_scale))
    f_raw = min(entry, f_core) * brake * corr if valid else float("nan")
    # ★ 轴 C-1：在**吸附之前**先判「这个解算结果该不该变成一张单」。`quantize_size_fraction` 的
    # `max(1, …)` 保证任何 `>0` 的意图至少落到最小一格——它对「模型给了个很小的数」是对的，对
    # 「风控层解出了零」却是把否决改写成开仓。生产实测 DOGE 两轮解出 0.0，被抬成半仓。
    verdict = _floor_verdict(f_raw, policy, step=inputs.step)
    if verdict != "none":
        vetoed = SizingOutcome(
            fraction=0.0, fraction_raw=_finite(f_raw),
            f_vol=None if f_vol is None else round(f_vol, _ROUND),
            f_kelly=None if f_kelly is None else round(f_kelly, _ROUND),
            kelly_status=kelly_status,
            scale_source=_scale_source(inputs, f_core, core_source, brake=brake, corr=corr),
            scale_erased=False, vol_source=vol_source, floor_verdict=verdict)
        return replace(vetoed, wire=_floor_wire(vetoed, inputs, rule))
    fraction = risk_fraction_candidates(f_raw, inputs.step)[0]
    outcome = SizingOutcome(
        fraction=fraction, fraction_raw=f_raw,
        f_vol=None if f_vol is None else round(f_vol, _ROUND),
        f_kelly=None if f_kelly is None else round(f_kelly, _ROUND),
        kelly_status=kelly_status,
        scale_source=_scale_source(inputs, f_core, core_source, brake=brake, corr=corr),
        scale_erased=False, vol_source=vol_source)
    return replace(outcome, wire=_outcome_wire(outcome, inputs, rule))


def risk_fraction_candidates(raw: float, step: float) -> list[float]:
    """返回不超过未舍入风险上限的候选档，按从大到小排列。

    连续模式保留原上限；无正有限解时返回空列表，调用方必须拒绝开仓。
    """
    if _positive(raw) is None or _finite(step) is None:
        return []
    upper = min(raw, 1.0)
    if step <= 0:
        return [upper]
    return [gear for gear in reversed(position_ladder(step))
            if 0 < gear <= upper]


def _floor_verdict(f_raw: float, policy: "SizingFloorPolicy | None", *, step: float) -> str:
    """硬上限独立于旧地板开关；展示精度不能增加可交易额度。"""
    if _finite(f_raw) is None or _finite(step) is None:
        return "invalid_quantity"
    if f_raw <= 0 or (policy and policy.zero_is_veto and f_raw <= _EPS):
        return "solved_zero"
    if not risk_fraction_candidates(f_raw, step):
        return "below_gear_floor"
    if policy and policy.floor_ratio > 0 and step > 0 and f_raw < step * policy.floor_ratio:
        return "below_gear_floor"
    return "none"


def _floor_wire(outcome: SizingOutcome, inputs: SizingInputs, rule: SizingRule) -> dict:
    """地板否决轮的 wire：形状与放行轮同构，只是 `fraction` 为 0 且多一个 `floorVerdict`。

    ⚠️ 必须由**本轮真实的产物**构造，绝不另造一个默认 `SizingOutcome`：那样 `fVol` / `fKelly` /
    `volSource` / `scaleSource` 会集体回落到构造默认值，于是在一个「波动率目标项算出来了、正是
    它把 `f_raw` 压到零」的轮次上，面板会显示 `volSource="disabled"`、`fVol=null` 与
    `fCap=f_max`——与本产物自己的字段互相矛盾，正是本轮在治的那类假数字。
    """
    return {**_outcome_wire(outcome, inputs, rule), "floorVerdict": outcome.floor_verdict}


def parse_floor_policy(settings) -> SizingFloorPolicy:
    """settings → `SizingFloorPolicy` 的**唯一**解析点（与 `parse_sizing_rule` 同一处调用）。

    保留旧设置的解析兼容性；最终仓位的硬上限不受这些开关影响。
    """
    return SizingFloorPolicy(
        zero_is_veto=bool(getattr(settings, "ai_trade_sizing_zero_is_veto", False)),
        floor_ratio=max(0.0, float(getattr(settings, "ai_trade_sizing_floor_ratio", 0.0))))


def _vol_term(sigma_bar, rule: SizingRule, bar_seconds: int) -> tuple[float | None, str]:
    """波动率目标项 `f_vol = σ_target_bar / σ_bar` + 取数来源（三态如实标注）。"""
    target_bar = annual_to_bar_vol(rule.vol_target_annual, bar_seconds)
    if target_bar is None:
        return None, "disabled"
    sigma = _positive(sigma_bar)
    if sigma is None:
        return None, "insufficient_samples"
    return target_bar / sigma, "realized"


def _kelly_term(inputs: SizingInputs, rule: SizingRule) -> tuple[float | None, str]:
    """凯利项（已乘 λ）；`λ<=0` ⇒ `disabled`（该项整体不参与 `min`）。"""
    if rule.kelly_lambda <= 0:
        return None, "disabled"
    raw, status = kelly_fraction(p_win=inputs.p_win, take_bps=inputs.take_bps,
                                 cost_bps=inputs.cost_bps, stop_bps=inputs.stop_bps)
    if raw is None:
        return None, status
    return raw * rule.kelly_lambda, status


def _scale_source(inputs: SizingInputs, f_core: float, core_source: str | None, *,
                  brake: float, corr: float) -> str:
    """本轮**谁真的绑定了**结果（§7.3 的对偶监控：`model` 占比 ≥80% ⇒ 本层是装饰品）。

    两个乘性系数只要 <1 就一定改变了结果，故优先级最高；其次才是「上限 vs 模型提议」的取小。
    """
    if corr < 1.0 - _EPS or brake < 1.0 - _EPS:
        return "correlation" if corr < brake - _EPS else "brake"
    if inputs.model_fraction is not None and inputs.model_fraction <= f_core + _EPS:
        return "model"
    return core_source or "model"


def _outcome_wire(outcome: SizingOutcome, inputs: SizingInputs, rule: SizingRule) -> dict:
    """求解产物自身的 camelCase 读数（`fFinalRaw` 与 `finalFraction` 之差 = 被档位地板吃掉的量）。

    `fCap` 是**刹车与相关性之前**的系统上限 `min(存在的项…, f_max)`：prompt 段用它告诉模型「本轮上限
    大约在哪」，与 `finalFraction`（含刹车、相关性、模型提议与档位吸附）刻意分开呈现。
    """
    cap = min([value for value in (outcome.f_vol, outcome.f_kelly) if value is not None]
              + [rule.f_max])
    return {"modelFraction": inputs.model_fraction, "fVol": outcome.f_vol, "fCap": round(cap, _ROUND),
            "fKelly": outcome.f_kelly, "kellyStatus": outcome.kelly_status,
            "fFinalRaw": (None if outcome.fraction_raw is None
                          else round(outcome.fraction_raw, _ROUND)),
            "finalFraction": outcome.fraction,
            "scaleSource": outcome.scale_source, "scaleErased": outcome.scale_erased,
            "volSource": outcome.vol_source, "volTargetAnnual": rule.vol_target_annual,
            "realizedVolBar": (None if inputs.realized_vol_bar is None
                               else round(float(inputs.realized_vol_bar), 8))}


def sizing_wire(outcome: SizingOutcome, plan: StopPlan | None, stop_source: str,
                brake: dict | None, exposure: dict | None) -> dict:
    """把求解产物 + 括号 + 刹车 + 敞口合成一条 `sizing` 事件 / `sizingPlan` 的整段读数。

    键集合与 §3.9 的事件形状逐字对应；缺席项一律 `None`（不假数据）。
    """
    wire = dict(outcome.wire)
    wire.update({"stopPct": None if plan is None else plan.stop_pct,
                 "takePct": None if plan is None else plan.take_pct,
                 "stopSource": stop_source})
    wire.update(brake or {})
    wire.update(exposure or {})
    return wire


def apply_size_fraction(decision: TradeDecision, outcome: SizingOutcome) -> TradeDecision:
    """把解出的占比写回决策（S-3：**只改 `size_fraction`**，`action` 一字不动）。

    `fraction is None`（非开仓动作 / 本层未装配）或与原值相等 ⇒ 返回**同一个**对象（`is` 相等），
    这正是 S-4 零回归断言依赖的东西。
    """
    if outcome.fraction is None or outcome.fraction == decision.size_fraction:
        return decision
    return replace(decision, size_fraction=outcome.fraction)


# ============================ 小工具 ============================
def _finite(value) -> float | None:
    """有限数归一（保留负号）：非有限 / 不可转 ⇒ None。"""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _positive(value) -> float | None:
    """正有限数归一：非有限 / ≤0 / 不可转 ⇒ None。"""
    out = _finite(value)
    return out if out is not None and out > 0 else None


def _clamp01(value) -> float:
    """把系数夹到 [0,1]；不可转 ⇒ 1.0（= 不缩减，本层的安全默认恒是「不动手」）。"""
    out = _finite(value)
    if out is None:
        return 1.0
    return 0.0 if out < 0 else 1.0 if out > 1 else out
