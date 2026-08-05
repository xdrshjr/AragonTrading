// 健康状态 / 判据码的中文**单一真源** + 徽标色板 + 留存度格式化 + 体检序列 → 折线点。
//
// 本仓库已经连续三次栽在「后端加了码、前端另有一张表、漏改就回显裸英文」上（迭代 1 的
// `category=discovered`、迭代 2 的 `S*` 闸码、迭代 2 终审的裸模板标识符）。因此：
// **H1–H6 与六种健康状态的中文只在本文件定义**，两端各配一条机器判据
// （后端 `HEALTH_CODES == grade() 返回码集合`，前端 `discoveryHealth.test.ts` 的键集合断言）。

import type {
  DiscoveryAssetCheckRow,
  DiscoveryHealthStatus,
} from "./discoveryAssetTypes";

export const HEALTH_CODES = ["H1", "H2", "H3", "H4", "H5", "H6"] as const;

/** 判据码中文（与后端 `health.HEALTH_CODES` 一一对应，缺一即测试红）。 */
export const HEALTH_CODE_CN: Record<string, string> = {
  H1: "新增样本不足",
  H2: "暖机数据不足或缺口",
  H3: "新增区间覆盖率不足",
  H4: "方向已反号",
  H5: "预测强度衰减",
  H6: "信息比/风险恶化",
};

export const HEALTH_STATUS_ORDER: DiscoveryHealthStatus[] = [
  "fresh",
  "healthy",
  "watch",
  "decayed",
  "unknown",
  "error",
];

/** 六态中文。`unknown` 一律「暂不评级」——它**不是**坏消息（D-26）。 */
export const HEALTH_STATUS_CN: Record<DiscoveryHealthStatus, string> = {
  fresh: "待首检",
  healthy: "健康",
  watch: "观察中",
  decayed: "已衰减",
  unknown: "暂不评级",
  error: "体检异常",
};

/** 一句话解释，进徽标 tooltip 与详情抽屉——状态词本身不足以说清「凭什么」。 */
export const HEALTH_STATUS_HINT: Record<DiscoveryHealthStatus, string> = {
  fresh: "已登记，正在等挖掘窗口之后攒够新数据才做第一次体检。",
  healthy: "在挖掘完成之后新产生的数据上，各项判据仍然达标。",
  watch: "最近一次体检未达标，但尚未连续失败，暂列观察。",
  decayed: "连续多次体检未达标。系统只做标注，是否停用由你决定。",
  unknown: "新增样本或暖机数据不足，本次不给结论——这不是坏消息。",
  error: "求值或回测过程出错，本次未产出有效结论（详见错误信息）。",
};

/** 徽标色板：`unknown` 走中性色，绝不用警告色（D-26）。 */
export const HEALTH_STATUS_TONE: Record<DiscoveryHealthStatus, "long" | "short" | "accent" | "neutral"> = {
  fresh: "neutral",
  healthy: "long",
  watch: "accent",
  decayed: "short",
  unknown: "neutral",
  error: "neutral",
};

/** `unknown` / `error` 的点不参与连线——不可判定的点连成线就是在编故事。 */
export const UNDECIDED_STATUSES: DiscoveryHealthStatus[] = ["unknown", "error"];

export function healthStatusCn(status: string): string {
  return HEALTH_STATUS_CN[status as DiscoveryHealthStatus] ?? status;
}

/** 判据码 → 中文；`S0:*`（策略装配失败）原样透出，可运维优先于漂亮。 */
export function healthCodeCn(code: string): string {
  if (code.startsWith("S0:")) return `装配失败（${code.slice(3)}）`;
  return HEALTH_CODE_CN[code] ?? code;
}

export function healthReasonsCn(reasons: string[]): string {
  return reasons.length ? reasons.map(healthCodeCn).join("、") : "无";
}

/**
 * 留存度文案。`null` 一律 `—`，**绝不**显示成 0——分母不可用与「跌到 0」是两件事。
 * `>1` 显示原值（这条资产比挖到时更强，是真实信息）。
 */
export function formatRetention(retention: number | null | undefined): string {
  if (retention === null || retention === undefined || !Number.isFinite(retention)) {
    return "—";
  }
  return `${(retention * 100).toFixed(0)}%`;
}

/** 条形图长度按 `min(1, r)` 夹取；数值仍显示原值（§2.5.3）。 */
export function retentionBarRatio(retention: number | null | undefined): number {
  if (retention === null || retention === undefined || !Number.isFinite(retention)) {
    return 0;
  }
  return Math.max(0, Math.min(1, retention));
}

export interface HealthTrendPoint {
  ts: number;
  retention: number | null;
  status: DiscoveryHealthStatus;
  /** 不可判定的点：画空心且不与相邻点连线。 */
  undecided: boolean;
}

/** 体检序列 → 折线点（按时间升序；不可判定的点如实标注，交给绘制层断线）。 */
export function toTrendPoints(checks: DiscoveryAssetCheckRow[]): HealthTrendPoint[] {
  return [...checks]
    .sort((a, b) => a.checkedTs - b.checkedTs)
    .map((check) => ({
      ts: check.checkedTs,
      retention: check.retention,
      status: check.status,
      undecided:
        UNDECIDED_STATUSES.includes(check.status) ||
        check.retention === null ||
        check.retention === undefined,
    }));
}

/** 相对时间（秒级 UTC）；0 / 未来一律给可读文案，不显示负数。 */
export function relativeTime(ts: number | null | undefined, now?: number): string {
  if (!ts) return "从未";
  const nowSec = now ?? Math.floor(Date.now() / 1000);
  const delta = nowSec - ts;
  if (delta < 0) return "即将";
  if (delta < 60) return "刚刚";
  if (delta < 3600) return `${Math.floor(delta / 60)} 分钟前`;
  if (delta < 86_400) return `${Math.floor(delta / 3600)} 小时前`;
  return `${Math.floor(delta / 86_400)} 天前`;
}

/** 守护看起来停了吗——`lastTickTs` 超过 3×tick 即提示（R-9）。 */
export function isGuardStalled(lastTickTs: number, tickS: number, now?: number): boolean {
  if (!lastTickTs || !tickS) return false;
  const nowSec = now ?? Math.floor(Date.now() / 1000);
  return nowSec - lastTickTs > tickS * 3;
}
