"""档位分辨率的**唯一真源**（ai-trade-fill-truth-and-effective-controls 轴 C-2 / 轴 D）。

无 IO、无状态、绝不抛。本模块回答两个问题，它们是同一件事的一体两面：

1. **启动之前**：你选的这个档位，会让哪几层风控在多少格上完全失效？（`describe_gear_resolution`）
2. **运行之中**：一次「强制降档」在离散档位上到底该怎么降？（`downgrade_one_gear`）

为什么必须住在同一个文件里：生产实测 `positionStepPct = 0.5` 占全库 147 条 ai-trade run 的
**88.4%**，此时阶梯只有 `[0.5, 1.0]` 两格，而 `quantize_size_fraction` 的 `max(1, …)` 保证任何
`>0` 的意图至少落到最小格。于是「×0.5 的强制降档」被吸附回原值，`shrink_requested and
size >= proposed` 成立 ⇒ 报否决。生产实测逆势通路 2373 个决策轮**一次都没放行过**。

⚠️ `downgrade_one_gear` 有**两个**调用点（`signals.resolve_consensus_gate` 与
`trend_verdict.apply_trend_bias_gate`），它们的降档段今天是**逐字同构的两份字节**，而
`session._run_decision_round` 先调后者、再调前者。只改一处，逆势开仓会在 `openFunnel` 的
`trendBias` 那一格被拦死，而共识那一侧的验收断言照样会绿——这正是评审 RV-P0-1 的形状。
**绝不在那两个文件里各写一份降档逻辑**：把谓词收进这里，是本轮唯一能防住「下次又只改一半」
的结构性手段。
"""
from __future__ import annotations

from dataclasses import dataclass

from .signals import position_ladder, quantize_size_fraction

_EPS = 1e-9

# 会被档位地板吸附回原值的三个机制（声明序即界面展示序）。
# **相关性缩减没有配置常数**：`corr_scale` 的值域是 `[0,1]`，由组合有效敞口现算；用 0.5 作代表值
# 是因为它恰是「缩到一半」这个最典型的断面——比它更狠的缩减只会更容易被地板吃掉，故判据保守。
_CORR_REPRESENTATIVE_MULT = 0.5

_MECHANISM_LABELS = {
    "brakeSlow": "回撤刹车第一档",
    "contrarian": "逆势通路强制降档",
    "corrScale": "相关性缩减",
}


@dataclass(frozen=True)
class GearResolution:
    """一个档位选择对「按比例缩减」这类机制的分辨率结论。

    Attributes:
        step: 档位步长（fraction）。
        gears: 阶梯格数（`len(position_ladder(step))`）；连续档位为 0。
        min_gear: 最小一格；连续档位为 0.0。
        erased: 在这套阶梯上**失效面过半**的机制 key，按 `_MECHANISM_LABELS` 的声明序。
        survives: 其余机制 key。
        dead_gears: 每个机制在阶梯上完全无效的格数（`{key: n}`），供界面说出具体比例。
    """

    step: float
    gears: int
    min_gear: float
    erased: tuple[str, ...]
    survives: tuple[str, ...]
    dead_gears: dict


def describe_gear_resolution(step: float, *, brake_rule=None,
                             consensus_rule=None) -> GearResolution:
    """这个档位会让哪几层风控失效（启动弹窗 / 纪律自检 / 启动回执共用同一份判据）。

    判据对每个机制的系数 `m`：**它在这套阶梯上有多少格是彻头彻尾的空转**——

        dead = #{g ∈ ladder | quantize_size_fraction(g × m, step) >= g − eps}
        erased = m < 1 且 dead × 2 >= gears        # 失效面过半

    ⚠️ **spec §4.C C-2 写的那条判据（「系数作用在最小一格上会不会被吸附回原值」）在数学上恒为
    真**，因此无法区分任何两个档位：`min_gear × m`（`m<1`）必然小于 `min_gear`，而
    `quantize_size_fraction` 的 `max(1, …)` 保证它一定被抬回 `min_gear`——每一个 `step` 都会命中，
    包括 spec 自己在门禁 `G-C4` 里要求返回空集的 `step=0.05`。这是实施期发现的方案缺陷，已登记
    在 spec 的《实施过程发现的方案缺陷》一节（`I-3`）。

    改用「失效面占比」的理由：真正决定这件事严重性的是**阶梯有多少格**。`m=0.5` 时只有
    `g >= 2×step` 的那些格降得下去，故失效格数恒为 1，而分母是 `gears`——

    - `step=0.5`：阶梯 `[0.5, 1.0]`，1/2 = 50% 失效 ⇒ 判 erased（生产实测占全库 88.4% 的运行态，
      `scaledRounds == quantizeErasedRounds` 逐行相等，命中率 100%）；
    - `step=0.25`：1/4 = 25% ⇒ 不判；
    - `step=0.05`：1/20 = 5% ⇒ 不判（与 `G-C4` 一致）；
    - `step=1.0`：阶梯只有 `[1.0]`，1/1 = 100% ⇒ 判（任何缩减都不可能发生）。

    Args:
        step: 档位步长；`<= 0`（连续档位）⇒ `erased` 恒空（没有阶梯就没有吸附）。
        brake_rule: `drawdown_brake.BrakeRule | None`（读 `slow_size_mult`）。
        consensus_rule: `signals.ConsensusRule | None`（读 `contrarian_size_mult`）。

    Returns:
        `GearResolution`；两条规则缺席时该机制按各自的默认系数判——`None` 不等于「不会失效」，
        它只意味着这一层此刻没装配，而档位是**启动之前**就要说清楚的事。
    """
    ladder = position_ladder(step)
    if not ladder:
        return GearResolution(step=float(step), gears=0, min_gear=0.0, erased=(),
                              survives=tuple(_MECHANISM_LABELS),
                              dead_gears={key: 0 for key in _MECHANISM_LABELS})
    mults = {
        "brakeSlow": _mult_of(brake_rule, "slow_size_mult", 0.5),
        "contrarian": _mult_of(consensus_rule, "contrarian_size_mult", 0.5),
        "corrScale": _CORR_REPRESENTATIVE_MULT,
    }
    erased, survives, dead_gears = [], [], {}
    for key in _MECHANISM_LABELS:
        mult = mults[key]
        dead = sum(1 for gear in ladder
                   if quantize_size_fraction(gear * mult, step) >= gear - _EPS)
        dead_gears[key] = dead
        (erased if mult < 1.0 and dead * 2 >= len(ladder) else survives).append(key)
    return GearResolution(step=float(step), gears=len(ladder), min_gear=ladder[0],
                          erased=tuple(erased), survives=tuple(survives),
                          dead_gears=dead_gears)


def _mult_of(rule, attr: str, fallback: float) -> float:
    """从规则对象读一个降档系数；缺席 / 非有限 ⇒ 回落默认（本层绝不因缺一个字段而抛）。"""
    try:
        value = float(getattr(rule, attr, fallback))
    except (TypeError, ValueError):
        return fallback
    return value if value == value and 0.0 <= value <= 1.0 else fallback


def gear_resolution_note(res: GearResolution) -> str | None:
    """`GearResolution` → 一句可以直接放进界面的中文说明；无失效机制 ⇒ `None`（整句不渲染）。

    ⚠️ **文案里不得出现字面 `**`**：本仓库的后端文案会被 JSX 当成纯文本节点渲染，Markdown 粗体
    标记会原样显示成星号——这是第 5 次登记同一条纪律（门禁 `G-X3` 用源码扫描冻结）。
    """
    if not res.erased:
        return None
    names = "、".join(_MECHANISM_LABELS[key] for key in res.erased)
    dead = max(res.dead_gears.get(key, 0) for key in res.erased)
    return (f"按当前档位（每档 {res.step * 100:g}%，阶梯共 {res.gears} 格），"
            f"{names} 在 {res.gears} 格里有 {dead} 格会被量化吸附回原值，即那些格上完全不生效"
            f"（含最常落到的最小一格 {res.min_gear * 100:g}%）。"
            "改用更细的档位（例如每档 5% / 10%）可让它们真的作用到下单量上。")


def _nearest_gear_index(ladder: list[float], value: float) -> int:
    """`value` 在阶梯上的序号；不在阶梯上（差值超过半格）⇒ `-1`。

    刻意**不**做「就近吸附」：本函数的调用方需要知道「这个仓位到底是不是一个合法档位」，把一个
    落在两格之间的值悄悄归到某一格，会让下游的「掉一格」变成一次幅度不确定的缩放。
    """
    if not ladder:
        return -1
    best, best_gap = -1, None
    for index, gear in enumerate(ladder):
        gap = abs(gear - value)
        if best_gap is None or gap < best_gap:
            best, best_gap = index, gap
    tol = max(_EPS, (ladder[0] if len(ladder) == 1 else ladder[1] - ladder[0]) / 2 + _EPS)
    return best if best_gap is not None and best_gap <= tol else -1


def downgrade_one_gear(proposed: float, *, step: float, size_mult: float,
                       gear_downgrade: bool) -> tuple[float, str]:
    """逆势通路的「强制降档」在离散档位上的**唯一**实现（两把闸共用，评审 RV-P0-1 / 放行条件 C-1）。

    Args:
        proposed: 降档前的仓位占比（模型自报，已过各层夹紧）。
        step: 档位步长；`<= 0`（连续档位）⇒ 退回乘性降档。
        size_mult: 配置的强制降档系数（如 0.5）。
        gear_downgrade: 总闸 `ai_trade_contrarian_gear_downgrade`；`False` ⇒ **逐字回退**乘性降档。

    Returns:
        `(size, lane)`。`lane ∈ {"gear", "mult", "floor_is_min"}`：

        - `"mult"`：走了今天的乘性降档（总闸关 / 连续档位 / 提议值不在阶梯上）；
        - `"gear"`：真的掉了一格；
        - `"floor_is_min"`：**已经在最低格**——此时 `size == proposed == min_gear`。

    `"floor_is_min"` 由调用方**放行**是本轴的核心判定。理由：逆势通路的不变式 2 说的是「只可能
    比原否决更宽，绝不比它更窄，也绝不放大仓位」。今天的写法在「已经是最小一格」这个断面上把它
    变成了**更窄**（直接退回否决），而此时既没有放大仓位，也确实是本通路能给出的最小注——它满足
    不变式 2 的两个条款，不满足的只是「必须严格小于」这个**实现细节**。生产实测这条实现细节让
    整条通路 2373 轮零放行。

    唯一的例外是 `step >= 1.0`（每次全仓）：那时最低格就是 100%，「已降档」等于「不降档」，故
    退回今天的否决（返回 `proposed` 与 `"mult"`，调用方的第三条判据会把它拦下）。
    """
    if not gear_downgrade or step <= 0:
        return quantize_size_fraction(proposed * size_mult, step), "mult"
    ladder = position_ladder(step)
    index = _nearest_gear_index(ladder, proposed)
    if index < 0:                       # 防御：提议值不在阶梯上（不该发生），退回今天的行为
        return quantize_size_fraction(proposed * size_mult, step), "mult"
    if index == 0:
        return (ladder[0], "floor_is_min") if step < 1.0 else (proposed, "mult")
    return ladder[index - 1], "gear"


# 两把闸的 wire 上各写一个 `downgradeLane`（**各写一份，绝不合成一份**——合成会让「到底是哪一把
# 闸放的行」重新不可回答，而那正是评审 RV-P0-1 能藏住的原因）。
#
# spec §4.D 原本要求三个加法键，另两个是降档前后在阶梯上的**格号**。终审删掉了它们：它们与
# 两把闸各自 wire 上早已有的 `sizeBefore` / `sizeAfter`（前端 `TrendGateRow` 真的在渲染那一对）
# 严格冗余，且全仓——后端、用例、`apps/web`——一处读点都没有。留着它们就是本轮自己在原则 6 里
# 明令禁止、并且专门做了机械化门禁 `G-X2` 去守的那个形状。
