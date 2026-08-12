"""生效纪律自检（ai-trade-discipline-preset-and-integrity 轴 B）——纯核心，零 IO。

**后端算了、但用户看不见 = 死数据**，是本仓库反复出现的缺陷形状，故轴 A 的装配必须配一个呈现落点。
本模块把「哪些纪律正在生效、阈值是多少、本会话拦下过多少次」装配成**固定顺序的 13 行 wire**，
经两条通道下发：会话级 `status.disciplines`（`patchAiTradeMemberStatus` 是整体 spread，新键不会被吞）
与分组级 `group_status().discipline.rows`。**不新增任何 WS 事件类型。**

四条硬约束（每一条都对应本仓库踩过的一次坑）：

1. **算不出 ≠ 0**：`threshold` / `firedCount` 算不出一律 `None` + `note` 说明，绝不填 0 冒充读数。
2. **`armed=false` 必须带 `note`**（为什么没生效、怎么开），否则用户只看到一片灰。
3. **只报「装配了」不报「拦了多少」的面板是危险的**（[P1-9]）：本仓库已有四次「恒真判据 ⇒ 永不
   开单」前科，那种断面上「闸门把每一单都拦了」与「一切正常」长得一模一样。故每行带 `firedCount`，
   且整体带一条恒真自检（见 `strictness_hint`）。
4. **裸仓判据以 `AiTradeBackstopWire` 的实名字段为准**（[P0-3]）：`coverageRatio == null` 是
   「还不知道」，与 `0`「确认裸露」是两个结论；前者一律灰色、绝不报红——误报会把真报一起废掉。
"""
from __future__ import annotations

from dataclasses import dataclass

# 13 行的固定顺序（wire 契约的一部分：前端按序渲染，不重排）。
ROW_KEYS = ("baseRuleMaBand", "consensus", "confidence", "edgeGate", "volSizing", "atrStop",
            "drawdownBrake", "lossStreak", "exposureCap", "profitGuard", "horizon",
            "exchangeBackstop", "signalPrior")

_LABELS = {
    "baseRuleMaBand": "基础策略·MA 通道", "consensus": "同向共识门槛",
    "confidence": "置信度门槛", "edgeGate": "最小 edge 门禁", "volSizing": "波动率目标仓位",
    "atrStop": "ATR 止损覆盖", "drawdownBrake": "回撤刹车", "lossStreak": "连亏熔断",
    "exposureCap": "有效敞口上限", "profitGuard": "利润保护棘轮", "horizon": "持仓周期纪律",
    "exchangeBackstop": "交易所侧兜底", "signalPrior": "信号先验 EV",
}

# 恒真自检的最小样本轮数（[P1-9]）：低于它不提示——刚启动的会话本来就没开过仓，那时提示只会被无视。
_STRICTNESS_MIN_ROUNDS = 8


@dataclass(frozen=True)
class DisciplineFacts:
    """13 行 digest 的全部输入（聚合结构体，满足 CLAUDE.md「函数形参 ≤ 5」，[P1-1]）。

    刻意收**运行时对象**而不是一堆散装读数：阈值的唯一真源是那份会话私有 `settings` 副本，装配与否
    的唯一真源是三条 rule 对象本身——摊平成十几个标量就等于再造一份可能与运行期分叉的口径。
    """

    settings: object                  # 会话私有派生副本（阈值的唯一真源）
    sizing: object | None             # SizingRuntime：rule / stop_rule / brake_rule / 计数器
    horizon_rule: object | None
    base_rule: object | None
    profit_rule: object | None
    backstop_wire: dict | None        # AiTradeBackstopWire 原样（判据见 `naked_backstop`）
    gate_counts: dict                 # 各闸本会话累计触发次数（算不出的键**缺席**，不填 0）
    prior_armed: bool
    spec_count: int
    # [P0-4] 启动期复核结论：按本预设的保守仓位下界解算，单笔委托量可能低于交易所最小下单量。
    # 它与运行期的 `volSizingDustSkips` 计数是**同一件事的先后两态**——先示警，真发生了再计数。
    dust_risk: bool = False


def naked_backstop(wire: dict | None, min_coverage: float) -> bool:
    """裸仓的**唯一判据**（[P0-3]；前端 `aiTradeDiscipline.ts::isNakedPosition` 与本式逐字同构）。

    只有「向交易所核实过（`basis == "exchange"`）」且「本地声称有止损但交易所侧一条都没有
    （`mismatch`）或覆盖率确实低于阈」才算裸仓。`coverageRatio is None` = 还没核实 ⇒ **不是**裸仓。
    """
    if not isinstance(wire, dict) or wire.get("basis") != "exchange":
        return False
    if wire.get("mismatch") is True:
        return True
    ratio = wire.get("coverageRatio")
    return ratio is not None and float(ratio) < float(min_coverage)


def build_discipline_digest(facts: DisciplineFacts) -> list[dict]:
    """13 行固定顺序的纪律自检 wire。

    每行 `{key, label, armed, threshold, source, note, firedCount, tone}`；`source` ∈
    `preset`（阈值来自 settings）/ `rule`（会话级契约对象）/ `runtime`（运行期核实结果）。
    """
    return [dict(_BUILDERS[key](facts), label=_LABELS[key]) for key in ROW_KEYS]


def strictness_hint(rows: list[dict], *, rounds_completed: int, opens: int) -> str | None:
    """[P1-9] 恒真自检：一堆闸都武装着、却一单都没开过 ⇒ 给出显著提示；否则 `None`。

    本仓库已有四次「恒真判据 ⇒ 永不开单」前科，它们在只报 armed 的面板上与「一切正常」完全同形。
    样本不足（轮数 < `_STRICTNESS_MIN_ROUNDS`）时不提示——刚启动没开过仓是正常的。
    """
    if opens > 0 or rounds_completed < _STRICTNESS_MIN_ROUNDS:
        return None
    armed = [row for row in rows if row["armed"]]
    if not armed:
        return None
    fired = [row for row in armed if (row.get("firedCount") or 0) > 0]
    culprits = "、".join(row["label"] for row in fired) if fired else None
    tail = f"其中 {culprits} 确实拦下过开仓意图。" if culprits else ""
    return (f"{len(armed)} 条纪律可能过严：已完成 {rounds_completed} 轮决策，本会话尚未开过任何仓。"
            f"{tail}可在弹窗改选更宽松的预设（或「关闭」）后重启会话。")


# ============================ 逐行装配 ============================
def _base(key: str, armed: bool, *, threshold: str | None = None, source: str = "preset",
          note: str | None = None, fired: int | None = None, tone: str | None = None) -> dict:
    return {"key": key, "armed": armed, "threshold": threshold, "source": source,
            "note": note, "firedCount": fired, "tone": tone}


def _count(facts: DisciplineFacts, key: str) -> int | None:
    """本会话累计触发次数；调用方没提供该键 ⇒ `None`（**不造数**，见硬约束 1）。"""
    value = facts.gate_counts.get(key)
    return None if value is None else int(value)


def _pct(value: float) -> str:
    return f"{float(value) * 100:.4g}%"


def _base_rule_row(facts: DisciplineFacts) -> dict:
    rule = facts.base_rule
    if rule is None:
        return _base("baseRuleMaBand", False, source="rule",
                     note="未装配。在启动弹窗的「基础策略」分区开启 MA 通道开单铁律。")
    return _base("baseRuleMaBand", True, source="rule",
                 threshold=f"MA{rule.period} / {rule.std_mult}σ / 容差 {_pct(rule.tolerance)}",
                 fired=_count(facts, "baseRuleMaBand"))


def _consensus_row(facts: DisciplineFacts) -> dict:
    need = int(getattr(facts.settings, "ai_trade_min_consensus", 0) or 0)
    total = int(facts.spec_count)
    if need <= 0:
        return _base("consensus", False,
                     note="未启用（门槛 0）。选「稳健」或「均衡」预设即开启同向共识门槛。")
    # 措辞必须在**两种断面**下都为真：门槛被 R-1 真夹下来（steady + 单策略），以及预设值本来就
    # 等于策略数（balanced + 单策略，一次都没夹）。写成「已按策略数夹紧」在后者上是一句假话，
    # 而这恰是默认路径最常见的断面——本仓库对「文案宣称并不存在的事」已有成文教训。
    note = (f"门槛已到本会话策略数上限（共 {total} 个策略，须全部同向才开仓）："
            "服务端按策略数做过夹紧，绝不让它高于策略数——高于即开仓永久不可达。"
            if need >= total > 0 else None)
    return _base("consensus", True, threshold=f"≥{need} / 共 {total} 策略", note=note,
                 fired=_count(facts, "consensus"))


def _confidence_row(facts: DisciplineFacts) -> dict:
    floor = float(getattr(facts.settings, "ai_trade_min_confidence", 0.0) or 0.0)
    if floor <= 0:
        return _base("confidence", False,
                     note="未启用（门槛 0）。任一具名预设都会给它一个下限。")
    return _base("confidence", True, threshold=f"≥{floor:.2f}",
                 fired=_count(facts, "confidence"))


def _edge_gate_row(facts: DisciplineFacts) -> dict:
    k_min = float(getattr(facts.settings, "ai_trade_edge_gate_k_min", 0.0) or 0.0)
    if k_min <= 0:
        return _base("edgeGate", False,
                     note="未启用（k=0 ⇒ 纯核心第一句即直通）。成本仍逐轮算给模型看，"
                          "但没有任何确定性判据会因为「毛 edge 覆盖不住成本」而拦下这一单。")
    return _base("edgeGate", True, threshold=f"毛 edge ≥ {k_min:g} × 成本",
                 fired=_count(facts, "edgeGate"))


def _vol_sizing_row(facts: DisciplineFacts) -> dict:
    rule = getattr(facts.sizing, "rule", None)
    if rule is None:
        return _base("volSizing", False,
                     note="未装配 ⇒ 仓位 = 模型自报比例 × 全额预算，没有波动率定标。"
                          "选任一具名预设即开启。")
    # [P0-4] 两态同一件事：`dust_risk` 是启动期的**事前**风险，`dust` 是运行期**已经发生**的次数。
    # 只报后者等于「先静默不开单若干轮，再告诉你为什么」；只报前者又答不出「已经跳过了几次」。
    dust = _count(facts, "volSizingDustSkips") or 0
    if dust > 0:
        note = (f"本会话已有 {dust} 次因解算仓位低于交易所最小下单量而跳过开仓"
                "——提高总预算或该标的百分比可消除。")
    elif facts.dust_risk:
        note = ("预算偏小：按本预设的保守仓位下界解算，单笔委托量可能低于交易所最小下单量，"
                "届时该轮会静默跳过开仓——提高总预算或该标的百分比可消除。")
    else:
        note = None
    return _base("volSizing", True,
                 threshold=f"年化 {_pct(rule.vol_target_annual)} / λ={rule.kelly_lambda:g}"
                           f" / 窗口 {rule.vol_window} 根",
                 note=note, fired=_count(facts, "volSizing"),
                 tone="warn" if note else None)


def _atr_stop_row(facts: DisciplineFacts) -> dict:
    stop = getattr(facts.sizing, "stop_rule", None)
    if stop is None:
        return _base("atrStop", False,
                     note="未装配 ⇒ 交易所侧 OCO 逐字回退固定百分比，止损距离不随波动率伸缩。")
    return _base("atrStop", True,
                 threshold=f"{stop.stop_atr_mult:g}×ATR / RR {stop.stop_rr:g}",
                 fired=_count(facts, "atrStop"))


def _drawdown_row(facts: DisciplineFacts) -> dict:
    brake = getattr(facts.sizing, "brake_rule", None)
    if brake is None or (brake.slow_dd_pct <= 0 and brake.halt_dd_pct <= 0):
        return _base("drawdownBrake", False,
                     note="未装配 ⇒ 账户回撤到任何程度都不会自动减速或停开新仓。")
    parts = []
    if brake.slow_dd_pct > 0:
        parts.append(f"减速 {_pct(brake.slow_dd_pct)}（×{brake.slow_size_mult:g}）")
    if brake.halt_dd_pct > 0:
        parts.append(f"停开 {_pct(brake.halt_dd_pct)}")
    return _base("drawdownBrake", True, threshold=" / ".join(parts),
                 fired=_count(facts, "drawdownBrake"))


def _loss_streak_row(facts: DisciplineFacts) -> dict:
    brake = getattr(facts.sizing, "brake_rule", None)
    if brake is None or brake.loss_streak <= 0:
        return _base("lossStreak", False, note="未装配 ⇒ 连续亏损不会触发冷却，模型可以一直重试。")
    cooldown = brake.loss_cooldown_s
    tail = f" / 冷却 {cooldown / 60:.0f} 分钟" if cooldown > 0 else ""
    return _base("lossStreak", True, threshold=f"连亏 {brake.loss_streak} 笔{tail}",
                 fired=_count(facts, "lossStreak"))


def _exposure_row(facts: DisciplineFacts) -> dict:
    rule = getattr(facts.sizing, "rule", None)
    mult = 0.0 if rule is None else float(rule.exposure_cap_mult)
    if mult <= 0:
        return _base("exposureCap", False,
                     note="未装配 ⇒ 多标的分组的相关性敞口不受约束（同涨同跌时等于加杠杆）。")
    return _base("exposureCap", True, threshold=f"{mult:g}× 单标的满仓名义额",
                 fired=_count(facts, "exposureCap"))


def _profit_guard_row(facts: DisciplineFacts) -> dict:
    rule = facts.profit_rule
    if rule is None:
        return _base("profitGuard", False, source="rule",
                     note="未装配 ⇒ 浮盈回吐不会触发保护性平仓。在弹窗「利润保护」分区开启。")
    hard = rule.hard_tp_return_pct
    tail = f" / 硬止盈 {_pct(hard)}" if hard and hard > 0 else ""
    return _base("profitGuard", True, source="rule",
                 threshold=f"武装 {_pct(rule.arm_return_pct)} / 回吐 {_pct(rule.giveback_pct)}{tail}",
                 fired=_count(facts, "profitGuard"))


def _horizon_row(facts: DisciplineFacts) -> dict:
    rule = facts.horizon_rule
    if rule is None:
        return _base("horizon", False, source="rule",
                     note="未装配 ⇒ 不约束最短持仓与换手频次。在弹窗「交易周期」分区开启。")
    label = rule.target_label or rule.title or rule.profile_id
    tail = f" / 最短 {rule.min_hold_s / 60:.0f} 分钟" if rule.min_hold_s > 0 else ""
    return _base("horizon", True, source="rule", threshold=f"{label}{tail}",
                 fired=_count(facts, "horizon"))


def _backstop_row(facts: DisciplineFacts) -> dict:
    """三态（`naked` / `unknown` / `covered`），文案必须**精确**（沿用 profit-guard RV-1 教训）：
    未核实就说未核实，绝不宣称一个并不存在的兜底。"""
    min_coverage = float(getattr(facts.settings, "ai_trade_backstop_min_coverage", 0.999))
    wire = facts.backstop_wire if isinstance(facts.backstop_wire, dict) else None
    if not getattr(facts.settings, "ai_trade_backstop_check_enabled", True):
        return _base("exchangeBackstop", False, source="runtime",
                     note="核实通道未开启（AI_TRADE_BACKSTOP_CHECK_ENABLED=false），"
                         "本层不向交易所核实随单止损是否真的挂上。")
    if wire is None:
        return _base("exchangeBackstop", False, source="runtime",
                     note="当前空仓或尚未核实——空仓时没有需要兜底的持仓，这不是坏消息。")
    if naked_backstop(wire, min_coverage):
        covered, total = wire.get("coveredSz"), wire.get("positionSz")
        detail = (f"交易所侧仅覆盖 {covered}/{total} 张" if covered is not None and total
                  else "交易所侧一条止损都没有")
        return _base("exchangeBackstop", False, source="runtime",
                     threshold=f"要求覆盖 ≥ {_pct(min_coverage)}",
                     note=f"⚠️ 当前持仓在交易所侧没有兜底止损（{detail}）。客户端断网 / 崩溃时"
                          "亏损无上界，请立即补挂。", tone="danger")
    ratio = wire.get("coverageRatio")
    if ratio is None:
        return _base("exchangeBackstop", False, source="runtime",
                     note="尚未向交易所核实覆盖率（「还不知道」不等于「没有兜底」，故不报警）。")
    return _base("exchangeBackstop", True, source="runtime",
                 threshold=f"覆盖 {_pct(ratio)}（阈值 {_pct(min_coverage)}）",
                 fired=_count(facts, "exchangeBackstop"))


def _signal_prior_row(facts: DisciplineFacts) -> dict:
    """[P2-5] 本行与其余 12 行**不同源**（由三条先验总闸 + 快照新鲜度共同决定），note 必须写「怎么开」。"""
    if facts.prior_armed:
        return _base("signalPrior", True, source="runtime", threshold="EV 分子取历史先验胜率",
                     fired=_count(facts, "signalPrior"))
    if not getattr(facts.settings, "ai_trade_signal_prior_enabled", False):
        return _base("signalPrior", False, source="runtime",
                     note="总闸未开（AI_TRADE_SIGNAL_PRIOR_ENABLED=false）⇒ edge 门禁的分子逐字"
                          "回退 ATR 粗估。开启前须先用 POST /ai-trade/priors/rebuild 建好快照。")
    return _base("signalPrior", False, source="runtime",
                 note="总闸已开但本标的/周期没有可用快照（未建过或已过期）⇒ EV 分子回退 ATR 粗估。"
                      "运维可用 POST /ai-trade/priors/rebuild 重建。")


_BUILDERS = {
    "baseRuleMaBand": _base_rule_row, "consensus": _consensus_row, "confidence": _confidence_row,
    "edgeGate": _edge_gate_row, "volSizing": _vol_sizing_row, "atrStop": _atr_stop_row,
    "drawdownBrake": _drawdown_row, "lossStreak": _loss_streak_row, "exposureCap": _exposure_row,
    "profitGuard": _profit_guard_row, "horizon": _horizon_row,
    "exchangeBackstop": _backstop_row, "signalPrior": _signal_prior_row,
}
