// 交易纪律预设与生效自检的前端纯函数（ai-trade-discipline-preset-and-integrity 轴 A/B/C）。
//
// 全部无副作用、不依赖 React/store/时钟。三条纪律：
// 1. **阈值与 14 行的 label 一律由后端下发**，本文件只放弹窗要用的「一句话后果说明」——本仓库已有
//    前后端各存一份口径导致静默漂移的事故（`edgeSource` 逐键手抄吞新键）。
// 2. `isNakedPosition` **不重算**裸仓判据，只读后端已判好的那一行——`coverageRatio == null`
//    是「还不知道」、`0` 是「确认裸露」，这个三态判据在前后端各写一遍就是两份口径，而误报会把
//    真报一起废掉。判据的唯一真源是后端 `discipline_view.naked_backstop`。
// 3. 汇总只数「后端说 armed 的行」，绝不在前端重算 armed——那会造出第二份判据。

import type {
  AiTradeContrarianQuota,
  AiTradeDirectionRule,
  AiTradeDisciplineDigest,
  AiTradeDisciplinePreset,
  AiTradeDisciplineRow,
} from "./aiTradeTypes";

export const DISCIPLINE_PRESETS: AiTradeDisciplinePreset[] = [
  "steady",
  "balanced",
  "aggressive",
  "off",
];

/** 服务端默认预设。**只用于弹窗初值与「（服务端默认）」角标**，真正生效值恒以启动回执为准。 */
export const DEFAULT_DISCIPLINE_PRESET: AiTradeDisciplinePreset = "balanced";

export interface DisciplinePresetCopy {
  label: string;
  /** 一句话后果说明：用户按下去之后 AI 的行为会怎么变（不是形容词，是后果）。 */
  detail: string;
}

// 四张卡的文案。**不写任何具体阈值**——阈值随后端预设表演进，抄一份必漂移；用户想看数字时展开
// 只读阈值表（那份数据由启动回执 `discipline.thresholds` 下发）。
export const DISCIPLINE_COPY: Record<AiTradeDisciplinePreset, DisciplinePresetCopy> = {
  steady: {
    label: "稳健",
    detail: "开仓门槛最高、仓位最小、回撤刹车最早介入。开单会明显变少，适合先看它稳不稳。",
  },
  balanced: {
    label: "均衡",
    detail: "在开单频次与风险控制之间取中。多数用户从这里开始。",
  },
  aggressive: {
    label: "激进",
    detail: "门槛最低、仓位最大、刹车最晚介入。开单更频繁，单次亏损也更大。",
  },
  off: {
    label: "关闭",
    detail: "完全不装配纪律层：仓位 = 模型自报比例 × 全额预算，没有波动率定标、没有回撤刹车、"
      + "也没有「成本覆盖不住就不开」的门禁。仅在你确知自己要什么时使用。",
  },
};

export interface DisciplineSummary {
  armed: number;
  total: number;
  /** `已生效 9/14`。总数为 0（老后端 / 还没成员）⇒ `null` ⇒ 整条纪律条不渲染。 */
  label: string | null;
  /** 至少一行处于危险态（当前只有裸仓）——纪律条右侧升级为红色警示。 */
  danger: boolean;
  /** 需要提请注意但不到危险的行数（如「解算仓位可能低于最小下单量」）。 */
  warnings: number;
}

/** 14 行 → 紧凑徽标读数。**只数后端说 armed 的行**，绝不在前端重算判据。 */
export function summarizeDisciplines(rows: AiTradeDisciplineRow[] | null | undefined):
DisciplineSummary {
  const list = rows ?? [];
  const armed = list.filter((row) => row.armed).length;
  return {
    armed,
    total: list.length,
    label: list.length > 0 ? `已生效 ${armed}/${list.length}` : null,
    danger: list.some((row) => row.tone === "danger"),
    warnings: list.filter((row) => row.tone === "warn").length,
  };
}

/**
 * 裸仓判定（[P0-3]）：**唯一入口**，与后端 `naked_backstop` 逐字同构。
 *
 * 判据不落在前端自己算的覆盖率上，而是读后端已经判好的那一行的 `tone === "danger"`——把同一个
 * 三态判据在前后端各写一遍，就是本仓库反复出事的那种「两份口径」。
 */
export function isNakedPosition(rows: AiTradeDisciplineRow[] | null | undefined): boolean {
  return (rows ?? []).some((row) => row.key === "exchangeBackstop" && row.tone === "danger");
}

/** 取某一行（找不到 ⇒ null；老后端缺行时调用方一律优雅降级，不渲染）。 */
export function disciplineRow(
  rows: AiTradeDisciplineRow[] | null | undefined,
  key: string,
): AiTradeDisciplineRow | null {
  return (rows ?? []).find((row) => row.key === key) ?? null;
}

/**
 * 成员的纪律 digest：**成员 status 优先，分组回执兜底**。
 *
 * 顺序不能反：`exchangeBackstop` 是唯一会逐成员分叉的一行，它只在成员 status 上是准的；分组回执
 * 取的是「第一个成员」的那份，用它覆盖别的成员就是把 A 的裸仓事实安到 B 头上。
 */
export function memberDisciplines(
  memberDigest: AiTradeDisciplineDigest | null | undefined,
  groupDigest: AiTradeDisciplineDigest | null | undefined,
): AiTradeDisciplineDigest | null {
  if (memberDigest && memberDigest.rows?.length) return memberDigest;
  if (groupDigest && groupDigest.rows?.length) return groupDigest;
  return null;
}

/**
 * 「逆势通路」那一行的文案（ai-trade-directional-symmetry [P2-4]）——**唯一真源**，卡片只做布局。
 *
 * 两件事必须同时说清楚，少一件这一行就没有存在的必要：
 * 1. **本会话已用 N/M**。配额刻意不落库、冷续跑归零（A-4 / RK-10），那是一个有意的取舍；但
 *    不把它画出来，「重启会话就能刷新配额」就是一个既存在又不可见的行为。
 * 2. **生效阈值**（置信度下界 / 降档系数）取自**服务端解析后的回执**，不是弹窗里那份入参
 *    ——「我在弹窗里填的到底生效了没有」只能由生效值回答（`baseRules` / `tradingHorizon`
 *    两处回执的成文姿态）。前端一个阈值都不重算。
 *
 * 未装配（总闸关 / 用户取消勾选 / 该预设 `minConsensus<=0` 使共识门禁本就不触发）⇒ `null`
 * ⇒ 整行不渲染，绝不挂一行「—」。
 */
export function describeContrarianQuota(
  quota: AiTradeContrarianQuota | null | undefined,
  rule?: (AiTradeDirectionRule & { minConsensus?: number }) | null,
): string | null {
  if (!quota) return null;
  const parts = [`本会话已用 ${quota.used}/${quota.maxPerSession}`];
  if (rule?.contrarianMinConfidence != null) {
    parts.push(`置信度 ≥${rule.contrarianMinConfidence}`);
  }
  if (rule?.contrarianSizeMult != null) parts.push(`仓位 ×${rule.contrarianSizeMult}`);
  return `${parts.join(" · ")}。配额不落库，重启会话后重新计数。`;
}

/** 触发次数的读数：`null`＝算不出（显示「—」），`0`＝确实一次都没触发。两者不可互相冒充。 */
export function formatFiredCount(count: number | null | undefined): string {
  return count == null ? "—" : `${count} 次`;
}
