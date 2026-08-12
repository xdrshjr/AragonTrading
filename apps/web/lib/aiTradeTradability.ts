// 可交易性预检的**只读渲染助手**（ai-trade-tradability-and-open-funnel 轴 A · 前端侧）。
//
// **本模块绝不重算任何判据**（G-5）：`level` / `headline` / `advice` 全部由后端下发，前端只做
// 颜色、图标与格式化。一张用户按下启动键**之前**看的表，如果与真正生效的那组数分叉，比不给这张表
// 更坏——本仓库已有过前后端各存一份口径导致静默漂移的事故（`edgeSource` 逐键手抄吞新键）。
//
// 它回答的是一个此前系统从不在事前回答的问题：**在这个标的 / 这个周期 / 这组纪律阈值下，模型的
// 开仓意图会有多大比例被最小 edge 门禁否决**。实测 BTC 1m 上是 98.6%——「开着、在跑、每轮都有
// 分析、就是从不开单」的真正原因。
import type {
  AiTradeTradabilityLevel,
  AiTradeTradabilityResponse,
  AiTradeTradabilitySnapshot,
} from "./aiTradeTypes";

export type TradabilityTone = "long" | "accent" | "short" | "muted";

export interface TradabilityView {
  level: AiTradeTradabilityLevel;
  /** 四档中文标签（后端只发 level 这个枚举，标签是纯呈现，故留在前端）。 */
  label: string;
  tone: TradabilityTone;
  /** 后端下发的一句话结论，**一个字都不重写**。 */
  headline: string;
  /** 后端下发的对症建议；`ok` / `unknown` ⇒ null。 */
  advice: string | null;
  /** 否决率的百分数串；算不出 ⇒ null（**绝不**把「算不出」写成 0%）。 */
  vetoText: string | null;
  /** 是否需要二次确认才允许启动（只有 `infeasible`；启动按钮**永不禁用**）。 */
  needsConfirm: boolean;
}

const LEVEL_LABEL: Record<AiTradeTradabilityLevel, string> = {
  ok: "可交易",
  marginal: "偏紧",
  infeasible: "不可行",
  unknown: "暂时算不出",
};

const LEVEL_TONE: Record<AiTradeTradabilityLevel, TradabilityTone> = {
  ok: "long",
  marginal: "accent",
  infeasible: "short",
  unknown: "muted",
};

/**
 * 后端 wire → 呈现视图。**服从后端的 `level`**：即便下发的数字与 level 看起来矛盾，也照 level 渲染
 * ——前端重算一遍就是造出第二份口径，那正是本轮 G-5 要钉死的东西。
 */
export function describeTradability(
  wire: AiTradeTradabilityResponse | AiTradeTradabilitySnapshot | null | undefined,
): TradabilityView | null {
  if (!wire || !wire.level) return null;
  const level = wire.level;
  return {
    level,
    label: LEVEL_LABEL[level] ?? LEVEL_LABEL.unknown,
    tone: LEVEL_TONE[level] ?? "muted",
    headline: wire.headline ?? "",
    advice: wire.advice ?? null,
    vetoText: formatRate(readVetoRate(wire)),
    needsConfirm: level === "infeasible",
  };
}

/** 两种 wire 形状（完整响应 / status 瘦身投影）里取同一个读数。 */
export function readVetoRate(
  wire: AiTradeTradabilityResponse | AiTradeTradabilitySnapshot | null | undefined,
): number | null {
  if (!wire) return null;
  if ("vetoRate" in wire && wire.vetoRate) return wire.vetoRate.market ?? null;
  if ("vetoRateMarket" in wire) return wire.vetoRateMarket ?? null;
  return null;
}

/** 比例 → 百分数串；`null` / 非有限 ⇒ `null`（调用方据此省略该子句，而不是写「未知 0%」）。 */
export function formatRate(value: number | null | undefined): string | null {
  if (value == null || !Number.isFinite(value)) return null;
  return `${(value * 100).toFixed(1)}%`;
}

/** bps 读数 → 展示串；算不出 ⇒ `null`。 */
export function formatBps(value: number | null | undefined): string | null {
  if (value == null || !Number.isFinite(value)) return null;
  return `${value.toFixed(1)} bps`;
}

/**
 * 数据来源的一句中文。`cache` 必须**说出来**：探针无条件读本地缓存是一次有意偏离
 * （见后端 `tradability_probe` 的 [P1-4]），用户有权知道这份统计不是实时行情算的。
 */
export function describeSource(
  source: string | null | undefined,
  asOfTs: number | null | undefined,
): string | null {
  if (!source) return null;
  const when = asOfTs ? `，最后一根 ${new Date(asOfTs * 1000).toLocaleString()}` : "";
  return source === "cache" ? `基于本地缓存的历史 K 线${when}` : `基于实时行情${when}`;
}

/**
 * 「一键换周期」的候选：第一个 `level` 非 `infeasible`/`unknown` 的更长周期。
 * 没有候选 ⇒ `null` ⇒ 不渲染那个按钮（**绝不**推荐一个同样不可行的周期）。
 */
export function firstFeasibleAlternative(
  wire: AiTradeTradabilityResponse | null | undefined,
): { bar: string; vetoRateMarket: number | null } | null {
  const hit = (wire?.alternatives ?? []).find(
    (item) => item.level === "ok" || item.level === "marginal",
  );
  return hit ? { bar: hit.bar, vetoRateMarket: hit.vetoRateMarket } : null;
}
