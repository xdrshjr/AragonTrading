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

# 14 行的固定顺序（wire 契约的一部分：前端按序渲染，不重排）。
# 第 14 行 `trendBias` 由 execution-trend-verdict 加入，且它**必须与 `discipline_runtime`
# 的 `("counter_trend_", "trendBias")` 前缀同轮加**：只加前缀不开这一行 = 有写点没读点，
# 计数写进 `_degrades` 后在 `build_discipline_digest` 这一步蒸发（评审 RP1-3）。
ROW_KEYS = ("baseRuleMaBand", "consensus", "confidence", "edgeGate", "volSizing", "atrStop",
            "drawdownBrake", "lossStreak", "exposureCap", "profitGuard", "horizon",
            "exchangeBackstop", "signalPrior", "trendBias")

_LABELS = {
    "baseRuleMaBand": "基础策略·MA 通道", "consensus": "同向共识门槛",
    "confidence": "置信度门槛", "edgeGate": "最小 edge 门禁", "volSizing": "波动率目标仓位",
    "atrStop": "ATR 止损覆盖", "drawdownBrake": "回撤刹车", "lossStreak": "连亏熔断",
    "exposureCap": "有效敞口上限", "profitGuard": "利润保护棘轮", "horizon": "持仓周期纪律",
    "exchangeBackstop": "交易所侧兜底", "signalPrior": "信号先验 EV",
    "trendBias": "趋势基调·逆势门槛",
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
    # execution-trend-verdict 第 14 行：**取会话那份 `TrendVerdictRule` 本身**而不是从 settings
    # 再读一次——`trend_rule_from_settings` 会做两处夹紧（阈值倒挂 / htf 权重封顶），读 settings
    # 原值会让面板报出一个「用户改了但运行期并没有生效」的数（本仓库对此已有成文教训）。
    # 追加在**末位**：本类的既有字段全部带默认值，新字段跟在最后既满足 dataclass「无默认值字段
    # 必须在前」的约束，也让既有的按位置构造点一个都不用改。
    trend_rule: object | None = None


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
    """14 行固定顺序的纪律自检 wire。

    每行 `{key, label, armed, threshold, source, note, firedCount, tone}`；`source` ∈
    `preset`（阈值来自 settings）/ `rule`（会话级契约对象）/ `runtime`（运行期核实结果）。
    """
    return [dict(_BUILDERS[key](facts), label=_LABELS[key]) for key in ROW_KEYS]


@dataclass(frozen=True)
class StrictnessFacts:
    """`strictness_hint` 的全部输入（聚合结构体，[P1-8]）。

    **刻意不是五个 keyword-only 形参**：那恰好踩到 CLAUDE.md 的「函数形参 ≤ 5」红线，而**同一个
    文件**的 `DisciplineFacts` docstring 里已经白纸黑字写了这条规则和它的解法。在同一文件里另立
    一套散装形参，就是本仓库反复记录的「制造新孤岛」。

    Attributes:
        rounds_completed: 本会话已完成决策轮数（样本量门禁）。
        opens: 本会话开仓成交笔数（>0 ⇒ 不提示）。
        funnel: `open_funnel.funnel_wire()` 产物或 `None`（轴 C 未装配 / 老会话）。
        tradability_level: 轴 A 的启动断面 level（`ok|marginal|infeasible|unknown`）或 `None`。
    """

    rounds_completed: int = 0
    opens: int = 0
    funnel: dict | None = None
    tradability_level: str | None = None


def strictness_hint(rows: list[dict], facts: StrictnessFacts) -> str | None:
    """[P1-9 / 轴 C] 恒真自检**升级为对症建议**：不只说「可能过严」，而是说**谁在拦、该动哪个旋钮**。

    合入前这句提示的建议固定为「改选更宽松的预设」。本轮实测证明那在短周期断面上是**错的**：
    同一批 BTC 1m K 线上，最宽松的 `aggressive`（k=1.0）仍有 93.0% 的否决率——门禁的分母是往返
    成本，短周期的单笔波动幅度本身就覆盖不了它，放宽 k 只能把曲线整体平移一点。真正的补救是
    **换时间级别**。

    有序规则链（第一条命中即返回）：

    1. 已开过仓 / 样本不足 ⇒ `None`（既有行为逐字不变）。
    2. `tradability_level == "infeasible"` ⇒ **首要建议是换周期**，且必须写明「换预设解决不了」。
       这一条**必须排在第 4 条之前**——D-1 的实测表就是它的依据。
    3. `funnel.intents == 0` ⇒ 模型一次都没提出过开仓：这是**模型侧**问题而非纪律侧，
       放宽一个从未触发的闸没有任何作用。
    4. 有首拦截层 ⇒ 点名那一层 + 它专属的对症建议。
    5. 兜底 ⇒ 保留合入前那句（作为最后一档，不删）。
    """
    if facts.opens > 0 or facts.rounds_completed < _STRICTNESS_MIN_ROUNDS:
        return None
    armed = [row for row in rows if row["armed"]]
    if not armed:
        return None
    if facts.tradability_level == "infeasible":
        return _infeasible_hint(facts)
    funnel = facts.funnel if isinstance(facts.funnel, dict) else None
    if funnel is not None and int(funnel.get("intents") or 0) == 0:
        return _no_intent_hint(facts, funnel)
    top = (funnel or {}).get("topBlocker") if funnel else None
    if isinstance(top, dict) and top.get("count"):
        return _top_blocker_hint(facts, funnel or {}, top)
    return _legacy_hint(armed, facts.rounds_completed)


def _infeasible_hint(facts: StrictnessFacts) -> str:
    """轴 A 判定不可行时的首要建议：换时间级别（并明说换预设解决不了）。"""
    return ("本周期的可交易性预检判定为「不可行」：这个标的在当前「时间级别」上的单笔波动幅度"
            f"覆盖不了一笔往返成本，已完成 {facts.rounds_completed} 轮决策而一单未开。"
            "首要动作是改用更长的时间级别——换预设解决不了（最宽松的档位仍会被同一条"
            "成本判据拦住绝大多数开仓）。详情见「可交易性预检」卡。")


def _no_intent_hint(facts: StrictnessFacts, funnel: dict) -> str:
    """分母为 0：模型压根没提出过开仓 ⇒ 这是模型侧问题，放宽纪律毫无作用。"""
    degraded = int(funnel.get("llmDegraded") or 0)
    tail = f"（其中 {degraded} 轮因模型调用失败而降级）" if degraded else ""
    return (f"模型在 {facts.rounds_completed} 轮里一次都没有提出开仓{tail}——"
            "这是模型侧（策略信号板 / 提示词 / 行情本身）的问题，不是纪律过严："
            "放宽一个从未被触发过的闸没有任何作用。建议先检查信号板是否长期无同向信号。")


def _top_blocker_hint(facts: StrictnessFacts, funnel: dict, top: dict) -> str:
    """点名首拦截层 + 该层专属的对症建议（口径词必须带：只归给**第一个**拦下它的闸）。"""
    intents = int(funnel.get("intents") or 0)
    label = top.get("label") or top.get("key")
    advice = _BLOCKER_ADVICE.get(str(top.get("key")), _BLOCKER_ADVICE_DEFAULT)
    return (f"{intents} 次开仓意图里有 {int(top['count'])} 次被「{label}」拦下"
            f"（按决策链顺序只归给第一个拦下它的闸）。{advice}")


def _legacy_hint(armed: list[dict], rounds_completed: int) -> str:
    """兜底档：漏斗不可用（老会话 / 轴 C 未装配）时保留合入前那句。"""
    fired = [row for row in armed if (row.get("firedCount") or 0) > 0]
    culprits = "、".join(row["label"] for row in fired) if fired else None
    tail = f"其中 {culprits} 确实拦下过开仓意图。" if culprits else ""
    return (f"{len(armed)} 条纪律可能过严：已完成 {rounds_completed} 轮决策，本会话尚未开过任何仓。"
            f"{tail}可在弹窗改选更宽松的预设（或「关闭」）后重启会话。")


# 逐层的对症建议：每条都要能自证「为什么是这个旋钮」，绝不给一句「改选更宽松的预设」了事。
# ⚠️ 本表与上面三个 `_*_hint` 的产出串**全部是纯文本**：它们经 `status.disciplines.hint` 下发，
# 由 `DisciplineCard` 直接放进 JSX 文本节点渲染，**不经任何 markdown 渲染器**——写 `**粗体**`
# 会原样显示成两个星号（本仓库已为这条栽过一次）。强调一律用「」与句式。
_BLOCKER_ADVICE_DEFAULT = "可在启动弹窗改选更宽松的纪律预设后重启会话。"
_BLOCKER_ADVICE = {
    "edgeGate": ("这一条比较的是「一笔的预期毛幅度 vs 一笔往返成本」：短周期上分子天然打不过"
                 "分母，首选换更长的时间级别；其次才是改用「激进」预设降低 k。"),
    "baseRuleMaBand": ("开仓被限制在 MA 通道的上/中/下轨附近。可在弹窗「基础策略」分区放宽容差，"
                       "或取消勾选该铁律（它是凌驾于所选策略之上的开单纪律）。"),
    "consensus": "同向策略数没达到门槛。可减少策略数量、或改用门槛更低的预设。",
    "confidence": "模型自报置信度低于门槛。这通常说明行情本身不明朗，放宽门槛会同时放进低质量开仓。",
    "horizon": "被持仓周期纪律拦下的是「平仓与反手」：若你要的是更高频的进出，请改选更短的周期模式。",
    "turnover": "换手预算已用尽（按会话计）。可改选换手预算更高的周期模式，或等预算恢复。",
    "profitCooldown": "处在保护性平仓后的冷却期内。可在弹窗「利润保护」分区调小冷却秒数。",
    "drawdownBrake": "回撤刹车 / 连亏熔断正在阻止开新仓——这通常是应该被拦住的断面，先复盘亏损原因。",
    "exchangeBackstop": "交易所侧兜底止损未覆盖当前持仓，系统据此暂停加仓。请先补挂止损。",
    "reflection": "下单前二次反思否决了这些开仓。可在设置中关闭反思，但那会去掉一层安全网。",
    "trendBias": ("这些开仓与本级别的趋势基调相反，且模型自报的置信度没达到逆势门槛。"
                  "若你认为基调本身判错了，请先看统计卡的「本会话基调分布」；"
                  "确要放宽，可在启动弹窗的「趋势基调」分区调低逆势置信度下界或整条关闭它。"),
    "other": _BLOCKER_ADVICE_DEFAULT,
}


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
        # ★ 轴 A-4（loss-and-liveness-hardening）：**「显示了但不作数」必须被说出来**。
        # `hard_tp_return_pct` 的唯一消费点在 `profit_guard.py` 里；保护层未装配时，纪律预设
        # 承诺的那个硬止盈逐字节不生效，而它照旧被渲染在启动弹窗的只读阈值表上——实测 45 条 run
        # 正处于这个状态。阈值取**会话私有派生 settings**（预设的唯一真源），绝不另抄一份。
        hard = float(getattr(facts.settings, "ai_trade_profit_guard_hard_tp_return_pct", 0.0) or 0.0)
        inert = (f"本会话纪律预设承诺的「利润保护·硬止盈 {_pct(hard)}」也随之失效——"
                 "它的唯一执行者就是本层。" if hard > 0 else "")
        return _base("profitGuard", False, source="rule",
                     note="未装配 ⇒ 浮盈回吐不会触发保护性平仓。在弹窗「利润保护」分区开启。"
                          + inert)
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


def _trend_bias_row(facts: DisciplineFacts) -> dict:
    """第 14 行：趋势基调·逆势门槛（execution-trend-verdict 轴 E / 评审 RP1-3）。

    两级未装配态各有各的话要说（`armed=false` 必须带 `note`，硬约束 2）：总闸关 = 整个特性没
    装配；只关子闸 = 基调仍进 prompt 与统计，只是不再拦任何一笔。
    """
    rule = facts.trend_rule
    if rule is None or not getattr(rule, "enabled", False):
        return _base("trendBias", False, source="rule",
                     note="未装配趋势基调层（AI_TRADE_TREND_VERDICT_ENABLED=false 或本会话显式"
                          "关闭）。开启后系统每轮会在你所选的执行周期上给出方向基调。")
    if not getattr(rule, "counter_gate_enabled", False):
        return _base("trendBias", False, source="rule",
                     note="基调仍进研判与统计，但逆势门槛已关闭：逆势开仓不再需要更高置信度、"
                          "也不再被强制降档。可在启动弹窗的「趋势基调」分区重新勾选。")
    floor = float(getattr(rule, "counter_min_confidence", 0.0) or 0.0)
    mult = float(getattr(rule, "counter_size_mult", 0.0) or 0.0)
    return _base("trendBias", True, source="preset",
                 threshold=f"逆势开仓需自报置信度 ≥{floor:.2f}，且仓位 ×{mult:g}",
                 fired=_count(facts, "trendBias"))


_BUILDERS = {
    "baseRuleMaBand": _base_rule_row, "consensus": _consensus_row, "confidence": _confidence_row,
    "edgeGate": _edge_gate_row, "volSizing": _vol_sizing_row, "atrStop": _atr_stop_row,
    "drawdownBrake": _drawdown_row, "lossStreak": _loss_streak_row, "exposureCap": _exposure_row,
    "profitGuard": _profit_guard_row, "horizon": _horizon_row,
    "exchangeBackstop": _backstop_row, "signalPrior": _signal_prior_row,
    "trendBias": _trend_bias_row,
}
