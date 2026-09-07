// 开仓漏斗的**几何与呈现助手**（ai-trade-tradability-and-open-funnel 轴 C · 前端侧）。
//
// 分工：后端负责**判据与层名**（`layers` / `topBlocker.label` 都随 wire 下发），前端只做布局。
// 两条硬纪律：
// 1. **零样本绝不造百分比**（G-12）：`intents == 0` ⇒ 整卡显示「样本不足，不下结论」，
//    **绝不**把 0/0 画成 0%。
// 2. **口径词必须写出来**：漏斗是**全程累计**，而归因账本是 `deque(maxlen=200)` 的滚动窗口。
//    两张卡并排时读者极易串口径，故 `scope` 随 wire 下发并原样渲染。
import type {
  AiTradeDirectionBalance,
  AiTradeIntentSide,
  AiTradeOpenFunnel,
  AiTradeSideFunnel,
} from "./aiTradeTypes";

export interface FunnelStage {
  key: string;
  label: string;
  count: number;
  /** 相对 `intents` 的宽度比例 ∈ [0,1]；`intents == 0` ⇒ null（不画条）。 */
  ratio: number | null;
  tone: "muted" | "short" | "accent" | "long";
}

// 层名的中文兜底：后端 `topBlocker.label` 已经中文化，但 `blocked` 的键是层 key，
// 逐层明细要自己译一次。**这份表与后端 `open_funnel.LAYER_LABELS` 同源**，改一处必须改两处。
const LAYER_LABEL: Record<string, string> = {
  baseRuleMaBand: "基础策略·MA 通道",
  // 轴 B：排在 `baseRuleMaBand` 之后、`horizon` 之前（与后端 `open_funnel.LAYERS` 层序同构）。
  orphanPosition: "交易所侧存在未接管持仓",
  horizon: "持仓周期纪律",
  profitCooldown: "保护性平仓冷却",
  turnover: "换手预算",
  exchangeBackstop: "交易所侧兜底",
  drawdownBrake: "回撤刹车 / 连亏熔断",
  trendBias: "趋势基调·逆势门槛",
  consensus: "同向共识门槛",
  confidence: "置信度门槛",
  edgeGate: "最小 edge 门禁",
  reflection: "下单前二次反思",
  other: "其它",
};

export function layerLabel(key: string): string {
  return LAYER_LABEL[key] ?? key;
}

/** 样本是否足够下结论：一次开仓意愿都没有 ⇒ 整卡只写「样本不足」。 */
export function hasFunnelSamples(funnel: AiTradeOpenFunnel | null | undefined): boolean {
  return !!funnel && funnel.intents > 0;
}

/**
 * 漏斗的四大格：意愿 → 被拦 → 委托 → 成交。
 *
 * `sizingZeroed` **不进「被拦」**（它不是一道闸，是求解结果），而是单列在「委托」之前。
 * `intents == 0` 时全部 `ratio` 为 null——调用方据此不画任何条、不写任何百分比。
 */
export function funnelStages(funnel: AiTradeOpenFunnel | null | undefined): FunnelStage[] {
  if (!funnel) return [];
  const total = funnel.intents;
  const blocked = sumBlocked(funnel);
  const complete = hasCompleteFunnel(funnel) && isFunnelClosed(funnel);
  const ratio = (n: number) => (complete && total > 0 ? clamp01(n / total) : null);
  const stages: FunnelStage[] = [
    { key: "intents", label: "开仓意愿", count: total, ratio: ratio(total), tone: "muted" },
    { key: "blocked", label: "被纪律拦下", count: blocked, ratio: ratio(blocked), tone: "short" },
    {
      key: "sizingZeroed",
      label: "仓位解到 0",
      count: funnel.sizingZeroed,
      ratio: ratio(funnel.sizingZeroed),
      tone: "accent",
    },
    { key: "submitted", label: "已接收订单", count: funnel.submitted, ratio: ratio(funnel.submitted), tone: "accent" },
    { key: "filled", label: "真的成交", count: funnel.filled, ratio: ratio(funnel.filled), tone: "long" },
    // ai-trade-loss-and-liveness-hardening E-7：与「真的成交」并列的**互斥末格**。合入前限价
    // 开仓单 602 笔里 223 笔（37%）从未成交而被撤销——模型决定开、闸全放行、单也发出去了，
    // 却在漏斗上不留痕，于是这张图系统性**高估**了「意愿 → 成交」的转化率。
    // 后端未上线该键 ⇒ `undefined` ⇒ 本格 count 为 0 且不渲染（见下方 `filter`）。
    {
      key: "expired",
      label: "挂单未成交即撤",
      count: funnel.expired ?? 0,
      ratio: ratio(funnel.expired ?? 0),
      tone: "short",
    },
  ];
  if (funnel.schemaVersion === 2) {
    const blockedExecution = Object.values(funnel.executionBlocked ?? {})
      .reduce((sum, count) => sum + count, 0);
    stages.splice(3, 0,
      { key: "executionBlocked", label: "执行未提交", count: blockedExecution,
        ratio: ratio(blockedExecution), tone: "short" },
      { key: "outcomeUnknown", label: "回执待核实", count: funnel.outcomeUnknown ?? 0,
        ratio: ratio(funnel.outcomeUnknown ?? 0), tone: "accent" },
    );
  }
  return stages.filter((stage) => stage.key !== "expired" || funnel.expired != null);
}

export function sumBlocked(funnel: AiTradeOpenFunnel | null | undefined): number {
  return Object.values(funnel?.blocked ?? {}).reduce((sum, n) => sum + (n || 0), 0);
}

/**
 * 闭合不变式：`Σblocked + submitted + sizingZeroed ≤ intents`。
 *
 * 这张图**全部**的价值就是它闭合。合入前 v1 的归类只看 `degrade`，一轮 `close` 被持仓周期铁律层
 * 拦下时 `intents` 没有 +1 而 `blocked` 会 +1 ⇒ 和超过分母。呈现层保留这个自检，是为了万一后端
 * 哪天回退，用户看到的是「数据异常」而不是一张悄悄不闭合的图。
 */
export function isFunnelClosed(funnel: AiTradeOpenFunnel | null | undefined): boolean {
  if (!funnel) return true;
  const known = sumBlocked(funnel) + funnel.submitted + funnel.sizingZeroed;
  if (!hasCompleteFunnel(funnel)) return known <= funnel.intents;
  const execution = Object.values(funnel.executionBlocked ?? {})
    .reduce((sum, count) => sum + count, 0);
  return known + execution + (funnel.outcomeUnknown ?? 0) === funnel.intents;
}

/** 旧服务端不能提供闭合终态分母，只展示计数与覆盖说明。 */
export function hasCompleteFunnel(funnel: AiTradeOpenFunnel): boolean {
  return funnel.schemaVersion === 2 && funnel.coverage === "complete"
    && funnel.executionBlocked != null && funnel.outcomeUnknown != null;
}

/** 逐层拦截明细（按 `layers` 的决策链顺序，只列真的拦过的层）。 */
export function blockedRows(
  funnel: AiTradeOpenFunnel | null | undefined,
): { key: string; label: string; count: number; ratio: number | null }[] {
  if (!funnel) return [];
  const order = funnel.layers?.length ? funnel.layers : Object.keys(funnel.blocked ?? {});
  return order
    .filter((key) => (funnel.blocked?.[key] ?? 0) > 0)
    .map((key) => ({
      key,
      label: layerLabel(key),
      count: funnel.blocked[key],
      ratio: hasCompleteFunnel(funnel) && isFunnelClosed(funnel) && funnel.intents > 0
        ? clamp01(funnel.blocked[key] / funnel.intents) : null,
    }));
}

/** 转化率的展示串；`conversion == null`（零意愿）⇒ `null` ⇒ 不渲染任何百分比。 */
export function formatConversion(funnel: AiTradeOpenFunnel | null | undefined): string | null {
  if (!funnel || !hasCompleteFunnel(funnel) || !isFunnelClosed(funnel)) return null;
  const value = funnel?.conversion;
  if (value == null || !Number.isFinite(value)) return null;
  return `${(value * 100).toFixed(1)}%`;
}

function clamp01(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return value < 0 ? 0 : value > 1 ? 1 : value;
}

// ============================ 方向轴（ai-trade-directional-symmetry 轴 D） ============================
//
// 分工不变：后端负责**判据**（`verdict` 由 `open_funnel.direction_verdict` 单点判定并随 wire
// 下发），前端只做布局与文案。三条硬纪律：
//
// 1. **零样本绝不造百分比**：`shortIntentShare` 为 `null` 时一个百分号都不渲染。
// 2. **`unknown` 不进分母、也不进左右两侧的长度归一**，但**必须在卡片上有可见落点**。实测它占
//    开仓意愿的 21%（置信度门槛在方向可见之前就把 action 洗成了 hold）。不披露它，
//    「模型从未提出过做空」就是一句系统证不出来的话——而它恰好会把运维引向完全错误的补救方向。
// 3. **两种病因的文案不可互换**：「提过做空但被门禁拦下」要动纪律旋钮，「从未提过做空」要查
//    信号板与研判协议。给错一句，用户会去调一个根本没在拦它的闸。

export interface DirectionRow {
  side: AiTradeIntentSide;
  label: string;
  intents: number;
  filled: number;
  /** 相对「两侧意愿的较大者」的长度比例 ∈ [0,1]；`unknown` 恒 0（它压在 0 轴上，不属于任何一侧）。 */
  ratio: number;
  /** 成交部分在本行内的占比 ∈ [0,1]（实心段）；`intents == 0` ⇒ 0。 */
  filledRatio: number;
  /** 被纪律拦下的次数（条纹段的读数来源）。 */
  blocked: number;
  tone: "long" | "short" | "muted";
}

const SIDE_LABEL: Record<AiTradeIntentSide, string> = {
  long: "做多",
  short: "做空",
  unknown: "方向不可知",
};

/** 卡片有没有资格渲染：后端没下发方向轴（老会话 / 总闸关）⇒ 整卡不渲染。 */
export function hasDirectionAxis(funnel: AiTradeOpenFunnel | null | undefined): boolean {
  return !!funnel?.directionBalance && !!funnel?.bySide;
}

function sumSideBlocked(side: AiTradeSideFunnel | undefined): number {
  return Object.values(side?.blocked ?? {}).reduce((sum, n) => sum + (n || 0), 0);
}

/**
 * 三行读数（做多 / 做空 / 方向不可知）。
 *
 * 长度归一的分母是**两个有方向的桶里较大的那个**，`unknown` 刻意不参与——它压在 0 轴上、
 * 是一段中性色，不属于左右任何一侧。
 */
export function directionRows(funnel: AiTradeOpenFunnel | null | undefined): DirectionRow[] {
  const bySide = funnel?.bySide;
  if (!bySide) return [];
  const scale = Math.max(bySide.long?.intents ?? 0, bySide.short?.intents ?? 0, 1);
  return (["long", "short", "unknown"] as AiTradeIntentSide[]).map((side) => {
    const row = bySide[side];
    const intents = row?.intents ?? 0;
    const filled = row?.filled ?? 0;
    return {
      side,
      label: SIDE_LABEL[side],
      intents,
      filled,
      blocked: sumSideBlocked(row),
      ratio: side === "unknown" ? 0 : clamp01(intents / scale),
      filledRatio: intents > 0 ? clamp01(filled / intents) : 0,
      tone: side === "long" ? "long" : side === "short" ? "short" : "muted",
    };
  });
}

/** 占比的展示串；`null`（分母为 0）⇒ `null` ⇒ 调用方一个百分号都不渲染。 */
export function formatDirectionShare(share: number | null | undefined): string | null {
  if (share == null || !Number.isFinite(share)) return null;
  return `${(share * 100).toFixed(1)}%`;
}

/** 拦掉某一侧意愿最多的那一层（用于「被谁拦下」那句结论）；一次都没拦过 ⇒ `null`。 */
export function topSideBlocker(
  funnel: AiTradeOpenFunnel | null | undefined,
  side: AiTradeIntentSide,
): { key: string; label: string; count: number } | null {
  const blocked = funnel?.bySide?.[side]?.blocked ?? {};
  const order = funnel?.layers?.length ? funnel.layers : Object.keys(blocked);
  let best: { key: string; label: string; count: number } | null = null;
  for (const key of order) {
    const count = blocked[key] ?? 0;
    if (count > 0 && (best === null || count > best.count)) {
      best = { key, label: layerLabel(key), count };
    }
  }
  return best;
}

/**
 * 一句结论文案 —— 本卡**唯一**的判断落点，按 `verdict` 分支且必须区分两种完全不同的病因。
 *
 * `unknownIntents > 0` 时**强制追加披露句**：没有它，「从未提出过做空意愿」是一句系统证不出来
 * 的话（那些轮在方向可见之前就被置信度门槛拦掉了，方向根本没进过系统）。
 */
export function directionVerdictText(
  funnel: AiTradeOpenFunnel | null | undefined,
): string | null {
  const balance = funnel?.directionBalance;
  if (!balance) return null;
  return [directionMainVerdict(balance, funnel), directionUnknownNote(balance)]
    .filter(Boolean)
    .join("");
}

function directionMainVerdict(
  balance: AiTradeDirectionBalance,
  funnel: AiTradeOpenFunnel | null | undefined,
): string {
  const directed = balance.longIntents + balance.shortIntents;
  if (balance.verdict === "insufficient") {
    return `样本不足（方向可辨的开仓意愿 ${directed} 次 < ${balance.minSamples}），不下结论。`;
  }
  if (balance.verdict === "balanced") {
    const share = formatDirectionShare(balance.shortIntentShare);
    return `本会话两个方向都被提出过（做空占方向可辨意愿的 ${share ?? "—"}），方向没有塌缩。`;
  }
  // 单边塌缩：弱侧到底是「提过但被拦」还是「从未提过」，对应完全相反的补救动作。
  const weakSide: AiTradeIntentSide = balance.verdict === "long_skewed" ? "short" : "long";
  const weakLabel = SIDE_LABEL[weakSide];
  const weakIntents = weakSide === "short" ? balance.shortIntents : balance.longIntents;
  if (weakIntents <= 0) {
    return `模型本会话从未提出过${weakLabel}意愿 —— 这不是门禁问题。放宽任何纪律都不会有效果，请检查信号板方向与研判协议。`;
  }
  const top = topSideBlocker(funnel, weakSide);
  const weakFilled = weakSide === "short" ? balance.shortFilled : balance.longFilled;
  const blockedText = top
    ? `其中 ${top.count} 次被「${top.label}」拦下 —— 这是门禁问题，可在启动弹窗放宽「逆势通路」。`
    : `但真正成交的只有 ${weakFilled} 次。`;
  return `本会话模型提出过 ${weakIntents} 次${weakLabel}意愿，${blockedText}`;
}

function directionUnknownNote(balance: AiTradeDirectionBalance): string {
  if (balance.unknownIntents <= 0) return "";
  const directed = balance.longIntents + balance.shortIntents;
  return `另有 ${balance.unknownIntents} 轮在方向可见之前就被「置信度门槛」拦下，方向不可知——本结论只覆盖其余 ${directed} 轮。`;
}
