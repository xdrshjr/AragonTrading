"""交易纪律预设（ai-trade-discipline-preset-and-integrity 轴 A）——纯核心，零 IO、零 pandas。

## 这一层解决什么

AI 自动交易在过去十余轮里已经把「盈利所需的机制」造齐了：波动率目标化仓位、ATR 止损覆盖、回撤刹车、
连亏熔断、有效敞口上限、最小 edge 门禁、同向共识与置信度门槛、利润保护棘轮。但它们在**生产默认路径
上全是暗的**——`ai_trade_sizing_enabled=False`、`ai_trade_edge_gate_k_min=0.0`（纯核心第一句就直通）、
`min_consensus=0`、`min_confidence=0.0`、`stop_atr_mult`/`brake_*`/`exposure_cap_mult` 一律 `0`。
于是一个用户点开弹窗启动 AI 自动交易，拿到的是「持有全额预算、没有波动率定标、没有回撤刹车、没有连亏
熔断、且明知手续费吃得掉预期收益也照开」的 LLM。本模块把这些层按一组冻结阈值真正装配起来。

## 机制：派生一份**会话私有的 Settings 副本**

本模块只产出一个 `updates` dict，喂给 `Settings.model_copy(update=...)`。**绝不 `setattr` 全局**：
`get_settings()` 是进程级缓存单例，而 `LiveExecutor` 被 `ai_auto` 与 `live_auto` **共用同一个
settings 对象**（历史坑：`ai_trade_*` 开关会静默改写非 AI 链路的 fee 与会话止盈时点）。用副本之后，
AI 侧的纪律阈值再也不会溢出到非 AI 链路。

`model_copy(update=)` **绕过 pydantic 校验**，故 `updates` 的每一个值只允许来自两个源：
① 本文件里冻结的常量表；② 已过 `AiTradeDisciplineIn` 的 `Field` 约束的用户覆盖字段。
`tests/test_ai_trade_discipline.py::test_every_preset_survives_the_real_validator`（G-11）
会对每个预设跑一遍真实校验器。

## 阈值选值依据（写在这里，便于后续拿验收卡回看校准）

- `k_min`（最小 edge 门禁倍数）：默认成本断面（taker 5bps 双边 + 半价差 1bps + 冲击 0.5bps
  ≈ 12~15bps）对 1H BTC 的 ATR 粗估毛 edge（≈ 40~60bps）而言，`k=1.5` 只在明显低波动断面拦单，
  不会把门禁做成恒真判据。
- `min_consensus`：**必须夹到本会话真实策略数**（见 `resolve_updates` 的 R-1），否则单策略会话下
  `2` 会让开仓永久不可达——这正是本仓库反复踩过的「恒真判据 ⇒ 永不开单」形状（已有四次前科）。
- 刹车阈值以「预算的回撤比例」为口径（`drawdown_brake.py` 既有语义）：第一档减速、第二档只平不开，
  两档之间留 5~10 个百分点，避免刚减速就停开。
- `f_floor`：该预设下「解算仓位」的保守下界，**只用于启动期的最小下单量可行性复核**（[P0-4]），
  绝不参与任何交易判定。波动率目标化 + 凯利缩放下 f 常落在 0.2~0.4。

**这些数字没有经过回测校准**，本轮只承诺「可观测、可回退、可校准」。任何 UI 文案与文档都不得出现
「已校准 / 经回测验证」这类措辞（[P1-10]）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

# 四个预设 id。`off` 是唯一的「关闭」出口，它的 `updates` 恒为空 dict ⇒ 调用方返回**原对象本身**。
PRESET_IDS = ("steady", "balanced", "aggressive", "off")

# 冻结预设表：settings 键 → 各预设取值。`off` 不出现在表里（空 updates 即关闭）。
_PRESETS: dict[str, dict[str, float | int | bool]] = {
    "steady": {
        "ai_trade_edge_gate_k_min": 2.0,
        "ai_trade_min_consensus": 2,
        "ai_trade_min_confidence": 0.60,
        "ai_trade_sizing_enabled": True,
        "ai_trade_vol_target_annual": 0.15,
        "ai_trade_kelly_lambda": 0.15,
        "ai_trade_realized_vol_window": 96,
        "ai_trade_effective_exposure_cap_mult": 1.5,
        "ai_trade_stop_atr_mult": 2.0,
        "ai_trade_stop_rr": 2.0,
        "ai_trade_brake_slow_dd_pct": 0.05,
        "ai_trade_brake_slow_size_mult": 0.50,
        "ai_trade_brake_halt_dd_pct": 0.10,
        "ai_trade_brake_loss_streak": 3,
        "ai_trade_brake_loss_cooldown_bars": 6.0,
        "ai_trade_profit_guard_hard_tp_return_pct": 0.30,
        # 逆势通路（directional-symmetry 轴 A）：稳健档**默认关**——它的 `min_consensus=2` 本就
        # 是「两条策略同向才许开」的强约束，在这一档开后门与这一档的定位矛盾。
        "ai_trade_consensus_contrarian_enabled": False,
    },
    "balanced": {
        "ai_trade_edge_gate_k_min": 1.5,
        "ai_trade_min_consensus": 1,
        "ai_trade_min_confidence": 0.50,
        "ai_trade_sizing_enabled": True,
        "ai_trade_vol_target_annual": 0.20,
        "ai_trade_kelly_lambda": 0.25,
        "ai_trade_realized_vol_window": 96,
        "ai_trade_effective_exposure_cap_mult": 2.0,
        "ai_trade_stop_atr_mult": 2.0,
        "ai_trade_stop_rr": 2.0,
        "ai_trade_brake_slow_dd_pct": 0.08,
        "ai_trade_brake_slow_size_mult": 0.50,
        "ai_trade_brake_halt_dd_pct": 0.15,
        "ai_trade_brake_loss_streak": 4,
        "ai_trade_brake_loss_cooldown_bars": 4.0,
        "ai_trade_profit_guard_hard_tp_return_pct": 0.50,
        # 逆势通路：均衡档**默认开**。生产回归窗口装的正是这一档（`min_consensus=1`），而
        # 1540 次开仓意愿里 832 次死在共识门禁——「同向票数 >= 1」在单边行情里对逆势方向恒不
        # 成立，这一档是本轴真正要救的那一档。
        "ai_trade_consensus_contrarian_enabled": True,
    },
    "aggressive": {
        "ai_trade_edge_gate_k_min": 1.0,
        "ai_trade_min_consensus": 0,
        "ai_trade_min_confidence": 0.35,
        "ai_trade_sizing_enabled": True,
        "ai_trade_vol_target_annual": 0.35,
        "ai_trade_kelly_lambda": 0.40,
        "ai_trade_realized_vol_window": 96,
        "ai_trade_effective_exposure_cap_mult": 3.0,
        "ai_trade_stop_atr_mult": 2.5,
        "ai_trade_stop_rr": 1.8,
        "ai_trade_brake_slow_dd_pct": 0.12,
        "ai_trade_brake_slow_size_mult": 0.60,
        "ai_trade_brake_halt_dd_pct": 0.22,
        "ai_trade_brake_loss_streak": 5,
        "ai_trade_brake_loss_cooldown_bars": 3.0,
        "ai_trade_profit_guard_hard_tp_return_pct": 0.0,   # 激进档不设硬止盈
    },
}

# ⚠️ **逆势通路的真实作用域只有 `steady`(2) 与 `balanced`(1)**（directional-symmetry 评审 P1-2）：
# `aggressive` 的 `ai_trade_min_consensus` 就是 `0`、`off` 走 `config.py` 的默认 `0`，共识门禁在
# 这两档**根本不触发**，逆势通路在那里是死配置。故上表只给这两档配了默认值，另两档一个键都不加
# ——加一个永远不会被读到的键，就是在阈值表里放一句假话。

# 各预设下「解算仓位」的保守下界（[P0-4] 启动期最小下单量复核专用，不参与任何交易判定）。
_F_FLOOR: dict[str, float] = {"steady": 0.15, "balanced": 0.20, "aggressive": 0.30}

# 用户覆盖字段（camelCase wire）→ settings 键。值域已由 `AiTradeDisciplineIn` 的 `Field` 在入口
# 校验，本层**只做映射与缺省回填，不复夹**（沿用 `_resolve_position_step` 的 [R-2] 惯例）。
OVERRIDE_TO_SETTING: dict[str, str] = {
    "edgeGateK": "ai_trade_edge_gate_k_min",
    "minConsensus": "ai_trade_min_consensus",
    "minConfidence": "ai_trade_min_confidence",
    "volTargetAnnual": "ai_trade_vol_target_annual",
    "stopAtrMult": "ai_trade_stop_atr_mult",
    "brakeSlowDdPct": "ai_trade_brake_slow_dd_pct",
    "brakeHaltDdPct": "ai_trade_brake_halt_dd_pct",
    "brakeLossStreak": "ai_trade_brake_loss_streak",
}

# 预设中文标签（回执与落库用；前端另有一份用于弹窗卡片的说明文案，不与本表交叉引用）。
PRESET_LABELS: dict[str, str] = {
    "steady": "稳健", "balanced": "均衡", "aggressive": "激进", "off": "关闭",
}

# 阈值键的中文标签与展示单位。**下发给前端渲染只读阈值表用**——沿用 `/ai-trade/horizon-profiles`
# 的既有纪律：「数值与中文标签全部由后端下发，前端不复制任何一个」。前端抄一份，这张表下次调参
# 时就会与真实生效值静默分叉，而它恰恰是用户按下启动键之前唯一能看到的依据。
_THRESHOLD_LABELS: dict[str, tuple[str, str]] = {
    "ai_trade_edge_gate_k_min": ("最小 edge 门禁", "×成本"),
    "ai_trade_min_consensus": ("同向共识门槛", "个策略"),
    "ai_trade_min_confidence": ("置信度门槛", ""),
    "ai_trade_vol_target_annual": ("年化波动率目标", "pct"),
    "ai_trade_kelly_lambda": ("凯利缩放 λ", ""),
    "ai_trade_realized_vol_window": ("波动率回看窗口", "根"),
    "ai_trade_effective_exposure_cap_mult": ("有效敞口上限", "×单标的满仓"),
    "ai_trade_stop_atr_mult": ("ATR 止损倍数", "×ATR"),
    "ai_trade_stop_rr": ("盈亏比 RR", ""),
    "ai_trade_brake_slow_dd_pct": ("刹车·减速回撤", "pct"),
    "ai_trade_brake_slow_size_mult": ("刹车·减速后仓位系数", ""),
    "ai_trade_brake_halt_dd_pct": ("刹车·停开回撤", "pct"),
    "ai_trade_brake_loss_streak": ("连亏熔断笔数", "笔"),
    "ai_trade_brake_loss_cooldown_bars": ("连亏冷却", "根"),
    "ai_trade_profit_guard_hard_tp_return_pct": ("利润保护·硬止盈", "pct"),
}


def preset_rows(preset_id: str) -> list[dict]:
    """某预设的只读阈值表（中文标签 + 已格式化的展示值），供启动弹窗在按下按钮**之前**展开核对。

    `sizing_enabled` 这类纯布尔总闸不进表——它不是「阈值」，且它开没开由其它行的存在本身回答。
    `ai_trade_consensus_contrarian_enabled`（directional-symmetry 轴 A）同属此类：它的开关态在
    启动弹窗里有专门的一块（`AiTradeDirectionFields`），不在这张**阈值**表里重复一遍。
    """
    table = _PRESETS.get(preset_id, {})
    rows = []
    for key, (label, unit) in _THRESHOLD_LABELS.items():
        if key not in table:
            continue
        value = table[key]
        if unit == "pct":
            text = f"{float(value) * 100:.4g}%"
        elif unit:
            text = f"{value:g} {unit}"
        else:
            text = f"{value:g}"
        rows.append({"key": key, "label": label, "value": text,
                     "off": float(value) == 0.0})
    return rows


@dataclass(frozen=True)
class DisciplinePreset:
    """一次解析的产物：喂 `Settings.model_copy(update=)` 的 updates + 如实披露的调整项。

    `adjustments` 是**可变列表**，允许下游（`manager._validate_sizing` 的 [P0-4] 最小下单量复核）
    在启动期继续追加——回执里必须逐条列出「服务端替你改了什么、为什么」，静默夹紧是本仓库明令
    禁止的形状。
    """

    preset_id: str
    updates: dict[str, float | int | bool]
    adjustments: list[dict] = field(default_factory=list)

    @property
    def armed(self) -> bool:
        """`off`（空 updates）⇒ 假 ⇒ 调用方必须返回**原 settings 对象本身**（`is` 相等）。"""
        return bool(self.updates)

    @property
    def f_floor(self) -> float:
        """该预设的保守仓位下界；未装配 ⇒ `1.0`（= 既有 `_budget_too_small` 的 f=1.0 口径）。"""
        return _F_FLOOR.get(self.preset_id, 1.0)


def preset_table(preset_id: str) -> dict[str, float | int | bool]:
    """某预设的冻结阈值表**只读副本**（供回执 / 前端只读阈值表 / 单测对拍）。未知 id ⇒ 空 dict。"""
    return dict(_PRESETS.get(preset_id, {}))


def resolve_updates(preset_id: str, overrides: dict | None,
                    spec_count: int) -> DisciplinePreset:
    """预设 + 用户覆盖 → `updates`（含 R-1 共识夹紧）。

    Args:
        preset_id: `steady` / `balanced` / `aggressive` / `off`；未知 id 一律按 `off` 处理
            （宁可不装配，也不猜一组阈值去开真实仓位）。
        overrides: 用户逐项覆盖（camelCase，键见 `OVERRIDE_TO_SETTING`）；`None` 值表示「按预设」。
        spec_count: **本会话真实的策略数**，R-1 夹紧的分母。

    Returns:
        `DisciplinePreset`。`off` ⇒ `updates` 为空 dict ⇒ 调用方返回原 settings 对象。

    R-1（共识夹紧）：`min_consensus` 一律夹到 `spec_count`。单策略会话选 `steady`（门槛 2）时，
    不夹就是「开仓永久不可达」——一条全 hold 的曲线看上去像「策略太保守」，实际是判据恒真。
    夹紧必须**如实回执**（`adjustments` + digest 的 `consensus.note`），绝不静默。
    """
    if preset_id not in _PRESETS:
        return DisciplinePreset(preset_id="off", updates={}, adjustments=[])
    updates: dict[str, float | int | bool] = dict(_PRESETS[preset_id])
    adjustments: list[dict] = []
    for wire_key, setting_key in OVERRIDE_TO_SETTING.items():
        value = (overrides or {}).get(wire_key)
        if value is None:
            continue
        updates[setting_key] = int(value) if isinstance(updates[setting_key], int) else value
    wanted = int(updates["ai_trade_min_consensus"])
    clamped = min(wanted, max(0, int(spec_count)))
    if clamped != wanted:
        updates["ai_trade_min_consensus"] = clamped
        adjustments.append({
            "field": "minConsensus", "from": wanted, "to": clamped,
            "reason": f"本会话只有 {int(spec_count)} 个策略，同向共识门槛已按策略数夹紧"
                      "（不夹紧会让开仓永久不可达）"})
    return DisciplinePreset(preset_id=preset_id, updates=updates, adjustments=adjustments)


# ============================ 轴 A（loss-and-liveness-hardening §4.1）============================
def unguarded_horizon(profit_rule, horizon_rule, *, enabled: bool) -> bool:
    """「装了持仓周期锁却没有利润侧钥匙」——本轮**唯一**的致命组合判据（唯一真源）。

    生产实证：`profitGuard` 回执为 `null`（= 利润保护层未装配）的 run 有 **193 / 211**；其中
    **45 条**同时带着 `discipline.preset="balanced"` —— 用户选了承诺「硬止盈 50%」的纪律预设，
    却因为另一个复选框而拿到了一个**没有任何利润侧离场**的会话。而持仓周期层的三道闸只有亏损侧
    豁免、没有利润侧对手盘：浮亏到半个止损距离 ⇒ 三闸全开、Agent 可以随时割肉；浮盈任意幅度 ⇒
    三闸全关、Agent 在 `min_hold_s`（默认 7200s）之内**没有任何通道**可以兑现利润。
    **锁被装上了，钥匙被拔走了。**

    ⚠️ **本判据只允许在 `ai_trade_start` 端点内求值**（评审 P0-3）。装配链
    `api/ai_auto.build_start_request` 被**冷续跑复用**（`resume.py` 延迟导入并用 `start_wire`
    原文重跑它），而 `_rebuild_request` 把任何 `HTTPException` 一律翻成
    `409 AI_TRADE_RESUME_CONTRACT_INVALID`「启动契约已失效，无法继续」——把 422 放进装配链
    ⇒ 生产里那 **50 条**「周期已装配 + 保护层未装配」的会话一旦暂停就再也续不回来，
    而续不回来的会话可能正持有真实仓位。互锁的职责是「不让新的致命会话被创建」，
    **不是「把已经存在的会话锁死」**。

    Args:
        profit_rule: 已解析的利润保护契约；`None` = 未装配。
        horizon_rule: 已解析的持仓周期契约；`None` = 未装配。
        enabled: 运维总闸 `ai_trade_require_profit_exit_with_horizon`；`False` ⇒ **恒 `False`**。

    Returns:
        真 ⇒ 这是致命组合，端点应当 422 并给出三条可执行的补救。
    """
    return bool(enabled) and profit_rule is None and horizon_rule is not None \
        and float(getattr(horizon_rule, "min_hold_s", 0.0) or 0.0) > 0


def inert_thresholds(thresholds: dict | None, *, profit_rule, sizing_rule,
                     horizon_rule) -> list[str]:
    """在本会话中**其消费层未装配**、因而不作数的阈值键（只读披露，不改任何行为）。

    这是 D2「显示了但不作数」的**通用修法**：启动弹窗把纪律预设的阈值表原样展示给用户，而其中
    一部分的唯一消费点可能整层没装配 —— `ai_trade_profit_guard_hard_tp_return_pct` 的唯一消费点
    在 `profit_guard.py`（`grep` 全仓确认），保护层未装配时它**逐字节不作数**，实测 45 条 run
    正处于这个状态。后续新增阈值只需往下面这张判据表里加一行。

    `sizing_rule` / `horizon_rule` 本轮**没有成员**，但它们是这张表的自然扩展维度，形参先立在
    这里以免下一轮为了加一个判据去改所有调用点的签名（`ai_trade_stop_atr_mult` 等在仓位层未装配
    时同属此类，但本轮不声称已核实，故不写进表里——**宁可少说一条，不说一句没核过的**）。

    Args:
        thresholds: `preset_table(preset_id)` 的产物；空 / `None` ⇒ 返回空列表。
        profit_rule: 已解析的利润保护契约（`None` = 未装配）。
        sizing_rule: 已解析的仓位 / 刹车契约（本轮不参与判定）。
        horizon_rule: 已解析的持仓周期契约（本轮不参与判定）。

    Returns:
        失效阈值键的列表（**保持 `thresholds` 的键序**，使回执可逐字对拍）。
    """
    del sizing_rule, horizon_rule          # 本轮无判据；形参见 docstring
    if not thresholds:
        return []
    inert: list[str] = []
    if profit_rule is None and "ai_trade_profit_guard_hard_tp_return_pct" in thresholds:
        inert.append("ai_trade_profit_guard_hard_tp_return_pct")
    return inert
