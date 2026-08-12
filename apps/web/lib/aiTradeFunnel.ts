// 开仓漏斗的**几何与呈现助手**（ai-trade-tradability-and-open-funnel 轴 C · 前端侧）。
//
// 分工：后端负责**判据与层名**（`layers` / `topBlocker.label` 都随 wire 下发），前端只做布局。
// 两条硬纪律：
// 1. **零样本绝不造百分比**（G-12）：`intents == 0` ⇒ 整卡显示「样本不足，不下结论」，
//    **绝不**把 0/0 画成 0%。
// 2. **口径词必须写出来**：漏斗是**全程累计**，而归因账本是 `deque(maxlen=200)` 的滚动窗口。
//    两张卡并排时读者极易串口径，故 `scope` 随 wire 下发并原样渲染。
import type { AiTradeOpenFunnel } from "./aiTradeTypes";

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
  horizon: "持仓周期纪律",
  profitCooldown: "保护性平仓冷却",
  turnover: "换手预算",
  exchangeBackstop: "交易所侧兜底",
  drawdownBrake: "回撤刹车 / 连亏熔断",
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
  const ratio = (n: number) => (total > 0 ? clamp01(n / total) : null);
  return [
    { key: "intents", label: "开仓意愿", count: total, ratio: total > 0 ? 1 : null, tone: "muted" },
    { key: "blocked", label: "被纪律拦下", count: blocked, ratio: ratio(blocked), tone: "short" },
    {
      key: "sizingZeroed",
      label: "仓位解到 0",
      count: funnel.sizingZeroed,
      ratio: ratio(funnel.sizingZeroed),
      tone: "accent",
    },
    { key: "submitted", label: "发出委托", count: funnel.submitted, ratio: ratio(funnel.submitted), tone: "accent" },
    { key: "filled", label: "真的成交", count: funnel.filled, ratio: ratio(funnel.filled), tone: "long" },
  ];
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
  return sumBlocked(funnel) + funnel.submitted + funnel.sizingZeroed <= funnel.intents;
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
      ratio: funnel.intents > 0 ? clamp01(funnel.blocked[key] / funnel.intents) : null,
    }));
}

/** 转化率的展示串；`conversion == null`（零意愿）⇒ `null` ⇒ 不渲染任何百分比。 */
export function formatConversion(funnel: AiTradeOpenFunnel | null | undefined): string | null {
  const value = funnel?.conversion;
  if (value == null || !Number.isFinite(value)) return null;
  return `${(value * 100).toFixed(1)}%`;
}

function clamp01(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return value < 0 ? 0 : value > 1 ? 1 : value;
}
