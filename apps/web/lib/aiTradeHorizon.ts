// 交易周期模式的前端纯逻辑（ai-trade-holding-horizon-modes §3.3）：校验 / 中文化 / 一致性提示 /
// 契约摘要装配。
//
// **本文件不含任何预设数值常量**（R-3 / G-10）：模式表的 11 列 × 5 行数字与中文标签一律来自
// `GET /ai-trade/horizon-profiles`。本仓库已经有过前后端各存一份口径导致静默漂移的事故
// （`edgeSource` 逐键手抄吞新键），一张这么宽的表手抄一遍必然漂移。此处只保留**校验边界**
// （与后端 schema 的 Field 约束一一对应）与**换算因子**（秒/分/小时/天），它们不是模式预设。
import type {
  AiTradeHorizonGateStatus,
  AiTradeHorizonProfile,
  AiTradeHorizonRule,
  AiTradeHorizonSnapshot,
  AiTradeHorizonStats,
} from "./aiTradeTypes";

// 校验边界：与后端 `AiTradeHorizonIn` 的 Field 约束逐条对应（前端即时报错，后端越界 422）。
export const HORIZON_MAX_MIN_HOLD_SECONDS = 604_800; // 7 天硬顶
export const HORIZON_MAX_CONFIRM_ROUNDS = 10;
export const HORIZON_MAX_HYSTERESIS_K = 10;
export const HORIZON_MAX_TARGET_SECONDS = 2_592_000; // 30 天

/** 一根 K 线的秒数（与后端 `okx.types.BAR_SECONDS` 镜像；一致性提示要按周期折算，别处无此表）。 */
export const HORIZON_BAR_SECONDS: Record<string, number> = {
  "1m": 60,
  "5m": 300,
  "15m": 900,
  "30m": 1800,
  "1H": 3600,
  "4H": 14400,
  "12H": 43200,
  "1D": 86400,
};

/**
 * 校验周期契约入参（区间与后端 schema 一致），返回中文错误文案或 null。
 *
 * `enabled=false` 时其余输入被忽略、一律合法——关闭态的残留值不该阻塞提交（沿用
 * `validateProfitGuard` 的既有惯例）。
 */
export function validateHorizon(rule: AiTradeHorizonRule): string | null {
  if (!rule.enabled) return null;
  const minHold = rule.minHoldSeconds;
  if (minHold != null && (!Number.isFinite(minHold) || minHold < 0 || minHold > HORIZON_MAX_MIN_HOLD_SECONDS)) {
    return `最短持仓窗口需在 0 ~ ${HORIZON_MAX_MIN_HOLD_SECONDS} 秒之间`;
  }
  const rounds = rule.exitConfirmRounds;
  if (rounds != null && (!Number.isInteger(rounds) || rounds < 1 || rounds > HORIZON_MAX_CONFIRM_ROUNDS)) {
    return `离场确认轮数需为 1 ~ ${HORIZON_MAX_CONFIRM_ROUNDS} 的整数`;
  }
  const k = rule.turnoverHysteresisK;
  if (k != null && (!Number.isFinite(k) || k < 0 || k > HORIZON_MAX_HYSTERESIS_K)) {
    return `换手迟滞倍数需在 0 ~ ${HORIZON_MAX_HYSTERESIS_K} 之间`;
  }
  return validateHorizonTargets(rule);
}

/** 目标持仓区间校验（拆出以守住「单方法 ≤50 行 / 嵌套 ≤4」）。 */
function validateHorizonTargets(rule: AiTradeHorizonRule): string | null {
  const lo = rule.targetHoldMinSeconds;
  const hi = rule.targetHoldMaxSeconds;
  for (const value of [lo, hi]) {
    if (value != null && (!Number.isFinite(value) || value <= 0 || value > HORIZON_MAX_TARGET_SECONDS)) {
      return `目标持仓需在 0 ~ ${HORIZON_MAX_TARGET_SECONDS} 秒（30 天）之间`;
    }
  }
  if (lo != null && hi != null && lo > hi) return "目标持仓下界不可大于上界";
  return null;
}

/** 秒 → 人话时长（`5400` ⇒ `1.5 小时`）；非正 / 不可数 ⇒ `—`。与后端 `_fmt_duration` 同口径。 */
export function formatHoldDuration(seconds?: number | null): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds <= 0) return "—";
  if (seconds < 3600) return `${Math.round(seconds / 60)} 分钟`;
  if (seconds < 86400) return `${trimNum(seconds / 3600)} 小时`;
  return `${trimNum(seconds / 86400)} 天`;
}

function trimNum(value: number): string {
  return String(Math.round(value * 100) / 100);
}

/**
 * 解除阈（保证金收益率，正数=幅度）——**按当前表单值现算**（[P1-10]）。
 *
 * 这一行是用户判断「这把锁安不安全」的唯一依据：`stopLossPct` 与 `leverage` 都在同一张表单里、
 * 用户随时会改，任何静态算例都会说谎。与后端 `loss_release_return_pct` 逐字同式。
 *
 * @param stopLossPct 单仓交易所侧止损（fraction）；`null` = 走服务端默认，视为「有止损」由调用方
 *   传入默认值；显式 `0` = 用户明确不挂止损 ⇒ 回落兜底常数。
 */
export function horizonLossRelease(
  stopLossPct: number | null,
  leverage: number | null,
  frac: number,
  fallbackPct: number,
): { pct: number; basis: "oco" | "fallback" } {
  if (stopLossPct != null && stopLossPct > 0 && leverage != null && leverage > 0) {
    return { pct: stopLossPct * leverage * frac, basis: "oco" };
  }
  return { pct: fallbackPct, basis: "fallback" };
}

/** 按用户**实际所选周期**现算 G2 的墙钟上界（与后端 `derive_confirm_window_s` 逐字同式）。 */
export function horizonConfirmWindow(targetHoldMinSeconds: number | null, bar: string): number {
  if (!targetHoldMinSeconds || targetHoldMinSeconds <= 0) return 0;
  const barSeconds = HORIZON_BAR_SECONDS[bar] ?? 0;
  const quarter = 0.25 * targetHoldMinSeconds;
  return barSeconds > 0 ? Math.min(quarter, 4 * barSeconds) : quarter;
}

export interface HorizonContractLine {
  key: string;
  text: string;
}

export interface HorizonContractInput {
  profile: AiTradeHorizonProfile | null;
  bar: string;
  leverage: number | null;
  stopLossPct: number | null; // 用户填写值（null=留空走服务端默认）
  defaultStopLossPct: number; // 服务端默认止损（弹窗已知常量，用于「留空」路径的现算）
  estimatedCostBps: number | null;
  lossReleaseFrac: number;
  lossReleaseFallbackPct: number;
  // 运维总闸（turnover-budget §4.1）：关 ⇒ 换手纪律两行整体省略（缺省视为开，与后端默认一致）。
  turnoverGateEnabled?: boolean;
}

/**
 * 契约摘要（§2.11）——**这是本设计最重要的一块信息**：用户必须在不展开任何高级参数的情况下就能
 * 理解「我选的这个模式到底会让系统做什么」。
 *
 * 四行全部按当前表单值现算：确认窗口随所选周期变、所需 bps 随接口下发的费率变、解除阈随止损与
 * 杠杆变。`profile.bar == null`（自定义）⇒ 退化为一行说明，不编造任何数字。
 */
export function horizonContractLines(input: HorizonContractInput): HorizonContractLine[] {
  const { profile } = input;
  if (!profile) return [];
  if (profile.bar == null) {
    return [{ key: "custom", text: "本模式下的约束由你自己设定；全部留空即不施加任何换手约束。" }];
  }
  const lines: HorizonContractLine[] = [];
  const minHold = profile.minHoldSeconds ?? 0;
  if (minHold > 0) {
    lines.push({
      key: "minHold",
      text: `开仓后 ${formatHoldDuration(minHold)}内不接受 AI 主动平仓（交易所止损/止盈、利润保护层、会话止损不受本层限制）`,
    });
  }
  const rounds = profile.exitConfirmRounds ?? 1;
  if (rounds > 1) {
    const window = horizonConfirmWindow(profile.targetHoldMinSeconds ?? 0, input.bar);
    const tail = window > 0 ? `，最多等待 ${formatHoldDuration(window)}（到时即按你的意愿放行）` : "";
    lines.push({ key: "confirm", text: `离场理由需连续 ${rounds} 轮成立才执行${tail}` });
  }
  const k = profile.turnoverHysteresisK ?? 0;
  if (k > 0) lines.push({ key: "hysteresis", text: hysteresisText(k, input.estimatedCostBps) });
  lines.push(...turnoverContractLines(profile, input.turnoverGateEnabled !== false));
  lines.push({ key: "release", text: releaseText(input) });
  return lines;
}

/**
 * 开仓侧换手纪律的两行契约摘要（turnover-budget §2.4.1）。
 *
 * 三条必须写在这一行里的事（缺一就会变成投诉）：
 * 1. **「本会话」口径**（[P1-5]）——令牌桶是会话内存态、重启即回满，写一句做不到的「每日」就是
 *    本轮自己最反对的那种「没核实过的话」；
 * 2. **平仓与反手的平仓腿永不受限**（[P0-1]）——用户看到「暂停开仓」时最怕的是仓位平不掉；
 * 3. **长周期模式的代价必须在选模式那一刻就说**（[P2-1]）——事后再解释就是投诉。
 *
 * 数值全部来自服务端下发的预览值，前端一个都不算（R-3 / G-10）；运维总闸关 ⇒ 两行整体省略。
 */
function turnoverContractLines(
  profile: AiTradeHorizonProfile,
  gateEnabled: boolean,
): HorizonContractLine[] {
  if (!gateEnabled) return [];
  const lines: HorizonContractLine[] = [];
  const cooldown = profile.reentryCooldownSeconds ?? 0;
  if (cooldown > 0) {
    lines.push({
      key: "reentry",
      text: `平仓后 ${formatHoldDuration(cooldown)}内不再开同向仓（反向不受限）`,
    });
  }
  const perDay = profile.turnoverBudgetPerDay ?? 0;
  const capacity = profile.turnoverBudgetCapacity ?? 0;
  if (perDay > 0) {
    lines.push({
      key: "budget",
      text:
        `本会话每日最多 ${trimNum(perDay)} 次完整往返，突发额度 ${trimNum(capacity)} 次` +
        "（会话重启后预算重新计算；超出即暂停开新仓，平仓与反手的平仓腿永不受限）",
    });
    if (perDay <= 2 && capacity > 0) {
      // 再次开仓只需**一枚**令牌，故等待上限是补一枚的时间 `24 / perDay` 小时，而不是把整桶补满的
      // `capacity / perDay × 24`（`trend_hold` 下后者是 48h，足足夸大一倍）。本轮的全部论点都建立在
      // 「不许说没核实过的话」上，弹窗里这个数尤其不能自己先说错。
      const worstHours = Math.round(24 / perDay);
      lines.push({
        key: "budgetCost",
        text:
          `本模式每日只补 ${trimNum(perDay)} 枚令牌：若连续用光突发额度，最长需等约 ` +
          `${worstHours} 小时补回一枚令牌才能再次开仓（平仓不受影响）。` +
          "这是长周期契约的代价，不是故障。",
      });
    }
  }
  return lines;
}

function hysteresisText(k: number, estimatedCostBps: number | null): string {
  if (estimatedCostBps == null) {
    return `获利了结需浮盈覆盖 ${k}× 往返成本（当前费率暂不可用，实际所需 bps 以每轮成本读数为准）`;
  }
  const required = Math.round(k * estimatedCostBps * 10) / 10;
  const cost = Math.round(estimatedCostBps * 10) / 10;
  return `获利了结需浮盈覆盖 ${k}× 往返成本（当前费率约 ${cost}bps ⇒ 需 ≥${required}bps）`;
}

/** 解除阈一行（[P1-10]）：显式填 0 ⇒ 兜底口径改述；否则按「止损 × 杠杆 × frac」现算。 */
function releaseText(input: HorizonContractInput): string {
  const explicitZero = input.stopLossPct != null && input.stopLossPct <= 0;
  const sl = explicitZero ? 0 : (input.stopLossPct ?? input.defaultStopLossPct);
  const { pct, basis } = horizonLossRelease(
    sl,
    input.leverage,
    input.lossReleaseFrac,
    input.lossReleaseFallbackPct,
  );
  const amount = `−${(pct * 100).toFixed(2)}%`;
  if (basis === "fallback") {
    return `浮亏达 ${amount}（保证金，兜底口径：本会话未挂交易所止损或未填杠杆）时以上限制全部解除`;
  }
  return `浮亏达 ${amount}（保证金，按你当前的止损 ${(sl * 100).toFixed(2)}% × 杠杆 ${input.leverage}× 估算）时以上限制全部解除`;
}

export interface HorizonCoherenceInput {
  profile: AiTradeHorizonProfile | null;
  bar: string;
  wakeMinutes: number | null;
  leverage: number | null;
}

/**
 * 一致性提示（§2.11 联动规则 3）——**非阻断**：用户手改 bar / 唤醒 / 杠杆后只提示、不回写 profile、
 * 不弹窗警告。静默夹紧比不夹更危险（杠杆是用户的资金决策）。
 */
export function horizonCoherenceHints(input: HorizonCoherenceInput): string[] {
  const { profile } = input;
  if (!profile || profile.bar == null) return [];
  const hints: string[] = [];
  const barSeconds = HORIZON_BAR_SECONDS[input.bar] ?? 0;
  const targetMin = profile.targetHoldMinSeconds ?? 0;
  if (barSeconds > 0 && targetMin > 0 && barSeconds * 2 > targetMin) {
    hints.push("执行周期偏粗：目标持仓下界不足 2 根 K 线，模型几乎没有可用于确认的中间状态。");
  }
  const minHold = profile.minHoldSeconds ?? 0;
  const wakeSeconds = (input.wakeMinutes ?? 0) * 60;
  if (minHold > 0 && wakeSeconds > minHold) {
    hints.push("唤醒周期长于最短持仓窗口，该窗口几乎不会被触发（等于本层的第一道闸空转）。");
  }
  if (wakeSeconds > 0 && barSeconds > 0 && wakeSeconds < barSeconds / 10) {
    hints.push("唤醒远快于执行周期：每根 K 线会被问 10 次以上，LLM 成本与换手压力都会显著上升。");
  }
  const cap = profile.leverageMaxHint;
  if (cap != null && input.leverage != null && input.leverage > cap) {
    hints.push(`杠杆高于本模式建议上限 ${cap}×，手续费对预算的侵蚀会成比例放大。`);
  }
  return hints;
}

export interface AiHorizonView {
  label: string;
  tone: "muted" | "accent" | "long" | "short";
}

const HORIZON_RELEASE_LABEL: Record<string, string> = {
  loss_release: "浮亏已达解除阈",
  stale_px: "判定价不可信",
  epoch_estimated: "持仓起点为估算值",
  max_hold_bars: "已达强制平仓上限",
};

/**
 * 门禁判定中文化（轨迹段徽标单点）：放行 → muted（常态不抢眼）；否决 → short；**解除 → accent 而非
 * short**——本层 fail-open，此时主动平仓一律放行，用红色会让用户以为被拦住了。
 * 未装配（`horizonStatus` 为空）→ null ⇒ 组件整段不渲染（老会话与老快照零影响）。
 */
export function describeHorizonGate(
  status?: AiTradeHorizonGateStatus | null,
  wire?: AiTradeHorizonSnapshot | null,
): AiHorizonView | null {
  if (!status) return null;
  const title = wire?.title ?? "交易周期";
  if (status === "idle") return { label: `${title} · 空仓（不约束开仓）`, tone: "muted" };
  if (status === "veto") return { label: `${title}否决 · 观望`, tone: "short" };
  if (status === "released") {
    const why = HORIZON_RELEASE_LABEL[wire?.releaseReason ?? ""] ?? "限制已解除";
    return { label: `${title} · 限制已解除（${why}）`, tone: "accent" };
  }
  return { label: `${title} · 放行`, tone: "muted" };
}

/** 豁免原因中文化（统计看板的拦截计数行 title 悬浮用）；未知码原样回退。 */
export function describeHorizonRelease(reason?: string | null): string {
  if (!reason) return "";
  return HORIZON_RELEASE_LABEL[reason] ?? reason;
}

/**
 * 成员面板 chip 文案（§2.11 三个展示落点之一）：持仓中显示倒计时，`released` 时点名原因，空仓时
 * 明说「本层不约束开仓」——**新增边缘态必须验渲染门控没把它整段吞掉**（`ai-trade-profit-protection`
 * 终审 RV-2 的成文教训）。未装配 ⇒ null ⇒ 整行不渲染。
 */
export function describeHorizonStatus(wire?: AiTradeHorizonSnapshot | null): string | null {
  if (!wire) return null;
  const title = wire.title || "交易周期";
  if (wire.status === "flat") return `${title} · 待入场`;
  const held = formatHoldDuration(wire.holdSeconds);
  if (wire.released) {
    return `${title} · 已持仓 ${held} · 限制已解除（${describeHorizonRelease(wire.releaseReason)}）`;
  }
  if (wire.minHoldSeconds > 0 && wire.minHoldRemainingSeconds > 0) {
    return `${title} · 已持仓 ${held} / 最短 ${formatHoldDuration(wire.minHoldSeconds)}（还差 ${formatHoldDuration(wire.minHoldRemainingSeconds)}）`;
  }
  return `${title} · 已持仓 ${held}`;
}

/**
 * 统计看板的三格 KPI（§2.11 第三个落点）。
 *
 * [P2-3] 零样本一律呈现「—」而不是 0%——「目标区间内占比 0%」与「还没有样本」在看板上是两个截然
 * 不同的结论，用 0 表示后者会把一个正常运行的会话读成彻底失效。
 */
export function horizonKpis(stats?: AiTradeHorizonStats | null): { label: string; value: string }[] {
  if (!stats) return [];
  return [
    { label: "平均持仓时长", value: formatHoldDuration(stats.avgHoldSeconds) },
    {
      label: "日均换手次数",
      value: stats.turnoverPerDay == null ? "—" : `${stats.turnoverPerDay.toFixed(2)} 次`,
    },
    {
      label: "目标区间内占比",
      value: stats.withinTargetRate == null ? "—" : `${(stats.withinTargetRate * 100).toFixed(0)}%`,
    },
  ];
}

/**
 * 运行期的三格 KPI（`AiTradeStatsLive` 用）：由**已完成往返的持仓秒数**现算。
 *
 * 与落库口径的差别必须说清楚（调用方负责在卡片上写出来）：后端样本取 `note_close` 的墙钟差，本函数
 * 取成交时间戳配对的差；且本函数只看已配对完成的往返，未平仓的持仓不计入。两者量级一致但不保证
 * 逐笔相等——把它当成运行期估算，会话停止后以「历史」标签的落库值为准。
 *
 * 零样本时三项一律 `null` ⇒ 呈现「—」（[P2-3]：绝不用 0 冒充「还没有样本」）。
 */
export function horizonLiveKpis(
  holdSeconds: number[],
  rule?: AiTradeHorizonRule | null,
): { label: string; value: string }[] {
  const samples = holdSeconds.filter((s) => Number.isFinite(s) && s > 0);
  const total = samples.reduce((sum, s) => sum + s, 0);
  const days = total / 86400;
  const lo = rule?.targetHoldMinSeconds ?? 0;
  const hi = rule?.targetHoldMaxSeconds ?? 0;
  const within =
    samples.length > 0 && hi > 0
      ? samples.filter((s) => s >= lo && s <= hi).length / samples.length
      : null;
  return [
    {
      label: "平均持仓时长",
      value: samples.length ? formatHoldDuration(total / samples.length) : "—",
    },
    { label: "日均换手次数", value: days > 0 ? `${(samples.length / days).toFixed(2)} 次` : "—" },
    { label: "目标区间内占比", value: within == null ? "—" : `${(within * 100).toFixed(0)}%` },
  ];
}

/** 本层拦截计数一行（四个 veto 计数不可互相换算，故并列呈现而非求和）。 */
export function horizonVetoLine(stats?: AiTradeHorizonStats | null): string | null {
  if (!stats) return null;
  return (
    `本层拦截：最短窗口 ${stats.minHoldVetoes} / 未确认 ${stats.unconfirmedVetoes} / ` +
    `迟滞 ${stats.hysteresisVetoes} / 反手 ${stats.reverseVetoes}`
  );
}

/**
 * 开仓侧换手纪律的拦截计数一行（turnover-budget §2.4.4）；两个计数皆为 0 且未记开仓 ⇒ null。
 *
 * `budgetVetoes` 是本轮最有信息量的新读数：它是**「模型想换手而被制度拦住」的次数**，比换手率
 * 本身更能说明周期模式是否与该标的、该行情匹配。**不并入上面四个第 1 轮的冻结计数**（[P2-7]）：
 * 那会让冻结门禁虽过、语义却已漂移。
 */
export function turnoverVetoLine(stats?: AiTradeHorizonStats | null): string | null {
  if (!stats) return null;
  const reentry = stats.reentryVetoes ?? 0;
  const budget = stats.budgetVetoes ?? 0;
  const opens = stats.opensCounted ?? 0;
  if (reentry + budget + opens <= 0) return null;
  return `换手纪律：再入场冷却拦下 ${reentry} / 预算拦下 ${budget}（消耗令牌 ${opens} 次）`;
}

/** 拦截计数行的 title 悬浮：豁免明细 + 窗口放行次数（[P2-4] 要求这两项必须有展示归宿）。 */
export function horizonReleaseHint(stats?: AiTradeHorizonStats | null): string {
  if (!stats) return "";
  const detail = Object.entries(stats.releaseReasons ?? {})
    .map(([reason, n]) => `${describeHorizonRelease(reason)} ${n}`)
    .join(" · ");
  const parts = [`限制解除 ${stats.releasedRounds} 轮${detail ? `（${detail}）` : ""}`];
  parts.push(`确认窗口到期放行 ${stats.confirmWindowReleases} 次`);
  return parts.join("；");
}

/** 估算起点脚注（[P2-4]）：这些 epoch 未计入均值，不写出来均值就是一个来路不明的数。 */
export function horizonEstimatedNote(stats?: AiTradeHorizonStats | null): string | null {
  if (!stats || !stats.estimatedEpochs) return null;
  return `其中 ${stats.estimatedEpochs} 个持仓周期的起点为估算值，未计入上述均值。`;
}
