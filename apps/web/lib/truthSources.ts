// 真值源的 wire 类型 + 状态/文案的**唯一**前端派生点
// （ai-trade-silent-degradation-repair 轴 E · E-2）。**零 React、零请求**，可单测。
//
// 纪律：`reading` / `reason` 一个字都不在这里拼——它们由后端 `ai_auto/truth_wire.py` 单点装配。
// 本文件只负责「状态 → 颜色 / 文字标签 / 排序」这类**纯呈现**的派生。两处各写一遍判定性文案，
// 必然与后端真正据以判定的那份分叉（`edgeSource` 与 `horizon-profiles` 已各付过一次学费）。

export type TruthSourceState = "ok" | "degraded" | "unknown" | "off";

export interface TruthSourceAction {
  kind: "retry" | "settings";
  label: string;
  /** `kind === "retry"` 时的真实端点路径。 */
  endpoint?: string;
  /** `kind === "settings"` 时的设置中心分区 key。 */
  target?: string;
}

export interface TruthSourceRow {
  key: string;
  label: string;
  state: TruthSourceState;
  reading: string;
  reason: string;
  since: number;
  action: TruthSourceAction | null;
}

export interface TruthSourcesWire {
  generatedTs: number;
  sources: TruthSourceRow[];
}

/**
 * 状态的**文字**标签。
 *
 * **状态绝不靠颜色单独承载**：色觉障碍与「截图发给同事」这两个场景都要能读，所以每一行都是
 * 「色点 + 文字标签 + 读数」。这一条是本轮「顶级设计 / 人机交互最佳实践」里最不该被省的一格。
 */
export const TRUTH_STATE_LABEL: Record<TruthSourceState, string> = {
  ok: "正常",
  degraded: "降级",
  unknown: "未知",
  off: "已关闭",
};

/** 状态 → 色点的 CSS 变量。四态四色，且 `unknown` 与 `off` 刻意不同——它们是两件事。 */
export const TRUTH_STATE_DOT: Record<TruthSourceState, string> = {
  ok: "var(--long)",
  degraded: "var(--accent)",
  unknown: "var(--muted)",
  off: "var(--border)",
};

/**
 * 折叠态那一行摘要：`真值源 4 项 · 2 项降级`。
 *
 * **默认折叠**是刻意的：一张常驻的四行诊断表会很快变成背景噪声，而「一切正常时几乎不占视觉
 * 预算」正是它能长期活下去的前提。只有 `degradedCount > 0` 时摘要才带颜色。
 *
 * **`unknown` / `off` 绝不被并进「全部正常」**：没在跑时四行全是 `unknown`，把那一屏摘要写成
 * 「全部正常」，就是这张卡片自己犯下它要指出的那个错误——一句在当时并不成立的安心话。三态因此
 * 分开说：有降级说降级（带颜色）、没降级但有读不到的说「暂无读数」（不带颜色，它不是告警）、
 * 四行全 `ok` 才说全部正常。
 */
export function summarizeTruthSources(rows: TruthSourceRow[] | null | undefined): {
  text: string;
  degraded: number;
  tone: "ok" | "warn";
} {
  const list = rows ?? [];
  const degraded = list.filter((row) => row.state === "degraded").length;
  const ok = list.filter((row) => row.state === "ok").length;
  const silent = list.length - degraded - ok;      // unknown + off：既不是正常，也不是坏了
  const head = `真值源 ${list.length} 项 · `;
  const text = degraded > 0
    ? `${head}${degraded} 项降级`
    : silent > 0
      ? `${head}${silent} 项暂无读数`
      : `${head}全部正常`;
  return { text, degraded, tone: degraded > 0 ? "warn" : "ok" };
}

/**
 * 顶栏芯片那句话——**分母是 3 不是 4**（后端 `truthSources.trackedCount` 说了算）。
 *
 * `intelReader` 只在 REST 上出现（它的真源是一次 DB 读，进不了每 5 秒一次的 status 事件），
 * 所以这句话必须写「3 项真值源中 2 项降级」而不是「真值源 2 项降级」——后者会让用户以为
 * 四行都在被实时监视（评审 Q5 的追加约束）。
 */
export function describeTruthDigest(
  digest: { trackedCount?: number; degradedCount?: number } | null | undefined,
): string | null {
  if (!digest || !digest.trackedCount) return null;
  const degraded = digest.degradedCount ?? 0;
  if (degraded <= 0) return null;
  return `${digest.trackedCount} 项真值源中 ${degraded} 项降级`;
}
