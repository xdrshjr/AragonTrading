/**
 * AI 自动交易「引导式启动」的**步骤模型**（ai-trade-guided-launch §4.2.1 / §4.2.3）。
 *
 * 本文件是三件事的**唯一真源**，六个 `Step*.tsx` 与步骤轨一概不得另写一份：
 *  1. 步骤序列与标题（`AI_TRADE_LAUNCH_STEPS`）——轨上写「资金与仓位」、内容区写「资金设置」，
 *     是分步 UI 最常见的漂移，且没有任何断言会红，故由门禁 G-3 冻结。
 *  2. **校验归属表**（`STEP_ERROR_OWNERS`）——分步之后最容易犯的错是「某个校验没有归属」：
 *     它照旧拦住启动按钮，但没有任何一步的「下一步」会被它挡住，用户走到第 6 步才被告知第 3 步
 *     填错了。归属表必须是**全覆盖且无重叠**的划分（U-2）。
 *  3. 步骤摘要（`stepSummary`）——分步之后仍能一眼看全的唯一载体。
 *
 * 全部是纯函数：不读 state、不发请求、不 import 任何组件。
 */

export type AiTradeLaunchMode = "live" | "backtest";

export type AiTradeLaunchStepId =
  | "scope" // 1 标的与策略
  | "rhythm" // 2 周期与节奏
  | "capital" // 3 资金与仓位
  | "cognition" // 4 Agent 认知
  | "guards" // 5 纪律与护栏
  | "review"; // 6 确认并启动

export interface AiTradeLaunchStepDescriptor {
  id: AiTradeLaunchStepId;
  /** 步骤条与内容区标题的**唯一**来源（G-3：`Step*.tsx` 里不得出现字面量标题）。 */
  title: string;
  /** 一句话说明「这一步在回答什么问题」，渲染在内容区标题下方。 */
  question: string;
  /** 该步在给定模式下是否存在（本轮六步两模式皆存在，形参为将来留口）。 */
  visibleIn: readonly AiTradeLaunchMode[];
}

const BOTH: readonly AiTradeLaunchMode[] = ["live", "backtest"];

export const AI_TRADE_LAUNCH_STEPS: readonly AiTradeLaunchStepDescriptor[] = [
  {
    id: "scope",
    title: "标的与策略",
    question: "对谁交易、凭哪些信号？",
    visibleIn: BOTH,
  },
  {
    id: "rhythm",
    title: "周期与节奏",
    question: "多久做一次决策、一笔拿多久？",
    visibleIn: BOTH,
  },
  {
    id: "capital",
    title: "资金与仓位",
    question: "拿多少钱、每次下多大？",
    visibleIn: BOTH,
  },
  {
    id: "cognition",
    title: "Agent 认知",
    question: "让 Agent 想多深、拿什么当证据？",
    visibleIn: BOTH,
  },
  {
    id: "guards",
    title: "纪律与护栏",
    question: "什么时候不开单、浮盈怎么兑现？",
    visibleIn: BOTH,
  },
  {
    id: "review",
    title: "确认并启动",
    question: "我到底要启动一个什么东西？",
    visibleIn: BOTH,
  },
] as const;

/** 步骤 id → 标题的查表（用例的 `gotoStep` 与轨、内容区共用同一份文案）。 */
export const AI_TRADE_LAUNCH_STEP_TITLE: Record<AiTradeLaunchStepId, string> =
  AI_TRADE_LAUNCH_STEPS.reduce(
    (acc, step) => {
      acc[step.id] = step.title;
      return acc;
    },
    {} as Record<AiTradeLaunchStepId, string>,
  );

export function stepsFor(mode: AiTradeLaunchMode): AiTradeLaunchStepDescriptor[] {
  return AI_TRADE_LAUNCH_STEPS.filter((step) => step.visibleIn.includes(mode));
}

/**
 * 校验量的稳定标识（**与今天弹窗里的变量名逐字相同**，以便对账）。
 *
 * 集合 = `READY_INPUTS ∪ BANNER_INPUTS`（【v2 · P0-3】）：
 *  · `READY_INPUTS` = `ready` 表达式（含 `baseValid`）读到的全部校验量；
 *  · `BANNER_INPUTS` = `firstError` 读到的全部。
 * 两者**不是**包含关系——`directionError` / `trendError` 只进 `ready`（各自就地渲染在字段旁，
 * 从来不进错误横幅），照「参照集 = firstError」施工会让它们没有任何步骤归属。
 */
export const AI_TRADE_LAUNCH_ERROR_KEYS = [
  "strategyError",
  "membersError",
  "instSupported",
  "instId",
  "rangeError",
  "horizonError",
  "wakeError",
  "roundsError",
  "everyError",
  "minConsensusError",
  "roundsCapError",
  "inputError",
  "baseRuleError",
  "profitGuardError",
  "profitLockError",
  "directionError",
  "trendError",
  "tradabilityBlocked",
  "unguardedBlocked",
  "acknowledged",
  "serverError",
  "trajectoryDirError",
] as const;

export type AiTradeLaunchErrorKey = (typeof AI_TRADE_LAUNCH_ERROR_KEYS)[number];

/** `ready`（含 `baseValid`）读到的校验量。U-2 的参照集之一。 */
export const READY_INPUTS: readonly AiTradeLaunchErrorKey[] = [
  "inputError",
  "strategyError",
  "roundsError",
  "baseRuleError",
  "profitGuardError",
  "horizonError",
  "directionError",
  "trendError",
  // 收益锁（profit-lock C-2）：与 `directionError` 同形——只进 `ready`、就地渲染在卡片字段旁，
  // 不进 `firstError`（该表达式逐字节冻结，守着既有用例的次序）；第 6 步兜底行自动覆盖它。
  "profitLockError",
  "membersError",
  "wakeError",
  "acknowledged",
  "instSupported",
  "instId",
  "rangeError",
  "everyError",
  "minConsensusError",
  "roundsCapError",
  // trajectory-capture §2.13.2：只进 `ready`、不进横幅（与 `directionError` 同形）。
  "trajectoryDirError",
] as const;

/** `firstError`（错误横幅）读到的校验量。U-2 的参照集之二。 */
export const BANNER_INPUTS: readonly AiTradeLaunchErrorKey[] = [
  "strategyError",
  "roundsError",
  "inputError",
  "baseRuleError",
  "profitGuardError",
  "horizonError",
  "membersError",
  "wakeError",
  "instSupported",
  "rangeError",
  "everyError",
  "minConsensusError",
  "roundsCapError",
  "tradabilityBlocked",
  "unguardedBlocked",
  "serverError",
] as const;

export interface AiTradeLaunchErrorOwner {
  step: AiTradeLaunchStepId;
  /** 该校验量在哪些模式下有效（live 段不看 `everyError`，回测段不看 `membersError`）。 */
  modes: readonly AiTradeLaunchMode[];
}

/**
 * 归属表（**冻结**）：每一个校验量恰好归属一步，无遗漏、无重叠（U-2）。
 *
 * `cognition` 一步**只产生一种阻断错误**：用户勾选了后训练数据采集、而目录被服务端判定不可用
 * （`trajectoryDirError`，ai-trade-trajectory-capture §2.13.2）。研判深度 / 经验 / 情报三张卡
 * 仍只有档位选择、没有可填错的数值。它与 `directionError` / `trendError` 同形：只进 `ready`、
 * 就地渲染在卡片里，**不进** `firstError`（第 6 步的兜底行自动覆盖它）。
 */
export const STEP_ERROR_OWNERS: Record<AiTradeLaunchErrorKey, AiTradeLaunchErrorOwner> = {
  strategyError: { step: "scope", modes: BOTH },
  membersError: { step: "scope", modes: ["live"] },
  instSupported: { step: "scope", modes: ["backtest"] },
  instId: { step: "scope", modes: ["backtest"] },
  rangeError: { step: "scope", modes: ["backtest"] },
  horizonError: { step: "rhythm", modes: BOTH },
  wakeError: { step: "rhythm", modes: ["live"] },
  roundsError: { step: "rhythm", modes: BOTH },
  everyError: { step: "rhythm", modes: ["backtest"] },
  minConsensusError: { step: "rhythm", modes: ["backtest"] },
  roundsCapError: { step: "rhythm", modes: ["backtest"] },
  inputError: { step: "capital", modes: BOTH },
  // 只在实盘：回测车道不采集（trajectory-capture §1.3 N1），回测段的卡片整卡替换为一行说明。
  trajectoryDirError: { step: "cognition", modes: ["live"] },
  baseRuleError: { step: "guards", modes: BOTH },
  profitGuardError: { step: "guards", modes: BOTH },
  profitLockError: { step: "guards", modes: BOTH },
  // 【v2 · P0-3】这两项**不在** `firstError` 里，只进 `ready`。没有归属 ⇒ 第 5 步的「下一步」
  // 不被它们挡、第 6 步横幅也不提它们，而 `ready === false` 让启动按钮灰着且全屏没有一句理由。
  directionError: { step: "guards", modes: BOTH },
  trendError: { step: "guards", modes: BOTH },
  tradabilityBlocked: { step: "review", modes: BOTH },
  unguardedBlocked: { step: "review", modes: ["live"] },
  acknowledged: { step: "review", modes: ["live"] },
  serverError: { step: "review", modes: BOTH },
};

/** 步骤模型：每个校验量的**现值**（`null` = 通过）。由 `useAiTradeLaunchForm` 逐项填。 */
export type AiTradeLaunchErrorState = Partial<Record<AiTradeLaunchErrorKey, string | null>>;

export interface AiTradeLaunchStepModel {
  mode: AiTradeLaunchMode;
  errors: AiTradeLaunchErrorState;
}

/**
 * 该步在当前模式下**自有的**第一条阻断错误（`null` = 本步通过）。
 *
 * ⚠️ 它是「下一步」按钮的提示判据，**不是**拦截：N-1 允许任意跳转，用户点一下轨就绕过去了；
 * 全流程唯一的真拦截仍然只有 `review` 步的 `ready` + `handleConfirm` 那三个判据。
 */
export function stepError(
  model: AiTradeLaunchStepModel,
  id: AiTradeLaunchStepId,
): string | null {
  for (const key of AI_TRADE_LAUNCH_ERROR_KEYS) {
    const owner = STEP_ERROR_OWNERS[key];
    if (owner.step !== id) continue;
    if (!owner.modes.includes(model.mode)) continue;
    const value = model.errors[key];
    if (value) return value;
  }
  return null;
}

/** 第 1–5 步（即除 `review` 外的输入步）是否全部无自有错误——N-10「跳到确认」的判据。 */
export function inputStepsClean(model: AiTradeLaunchStepModel): boolean {
  return stepsFor(model.mode)
    .filter((step) => step.id !== "review")
    .every((step) => stepError(model, step.id) == null);
}

/** 归属表里第一个有阻断错误的步骤（`review` 步兜底行的跳转目标）。 */
export function firstErroredStep(
  model: AiTradeLaunchStepModel,
): AiTradeLaunchStepDescriptor | null {
  return stepsFor(model.mode).find((step) => stepError(model, step.id) != null) ?? null;
}

/** 步骤摘要的入参（全部已格式化为展示串 / 计数，本文件不做任何单位换算）。 */
export interface AiTradeLaunchSummaryInput {
  mode: AiTradeLaunchMode;
  memberCount: number;
  instId: string;
  strategyCount: number;
  bar: string;
  horizonLabel: string;
  wakeText: string;
  decisionEveryText: string;
  budgetText: string;
  leverageText: string;
  positionStepText: string;
  reasoningDepthLabel: string;
  experienceEnabled: boolean;
  intelEnabled: boolean;
  disciplineLabel: string;
  baseRuleEnabled: boolean;
  profitGuardEnabled: boolean;
  profitLockEnabled: boolean;
  riskCount: number;
  /** 后训练数据采集是否**有效**开启（可选：缺省 / false ⇒ cognition 摘要逐字节不变）。 */
  trajectoryEnabled?: boolean;
}

const DOT = " · ";

function onOff(value: boolean, on: string, off: string): string {
  return value ? on : off;
}

/** 展示串的安全阀：空 / `undefined` / `NaN` 一律回落成一个短横（U-7 断言不出现这两个字面量）。 */
function safe(text: string | number | null | undefined, fallback = "—"): string {
  if (text == null) return fallback;
  const s = String(text);
  if (!s.trim() || s.includes("undefined") || s.includes("NaN")) return fallback;
  return s;
}

/**
 * 每一步在轨上的**现值摘要**（N-5）。这是「分步之后仍能一眼看全」的唯一载体，也是把「一步一步」
 * 做成引导而不是幻灯片的关键。
 */
export function stepSummary(
  input: AiTradeLaunchSummaryInput,
  id: AiTradeLaunchStepId,
): string {
  switch (id) {
    case "scope":
      return input.mode === "live"
        ? `${input.memberCount} 个标的${DOT}${input.strategyCount} 策略`
        : `${safe(input.instId)}${DOT}${input.strategyCount} 策略`;
    case "rhythm":
      return input.mode === "live"
        ? `${safe(input.horizonLabel)}${DOT}${safe(input.bar)}${DOT}${safe(input.wakeText)}`
        : `${safe(input.horizonLabel)}${DOT}${safe(input.bar)}${DOT}${safe(input.decisionEveryText)}`;
    case "capital":
      return `${safe(input.budgetText)}${DOT}${safe(input.leverageText)}${DOT}档位 ${safe(input.positionStepText)}`;
    case "cognition":
      return (
        `${safe(input.reasoningDepthLabel)}${DOT}` +
        `经验${onOff(input.experienceEnabled, "开", "关")}${DOT}` +
        `情报${onOff(input.intelEnabled, "开", "关")}` +
        // 未开启时一个字节都不追加（U-7 冻结「改前逐字相等」）。
        (input.trajectoryEnabled ? `${DOT}采集开` : "")
      );
    case "guards":
      return (
        `${safe(input.disciplineLabel)}${DOT}` +
        `铁律${onOff(input.baseRuleEnabled, "开", "关")}${DOT}` +
        `收益锁${onOff(input.profitLockEnabled, "开", "关")}${DOT}` +
        `利润保护${onOff(input.profitGuardEnabled, "开", "关")}`
      );
    case "review":
      return input.riskCount > 0 ? `${input.riskCount} 条风险提示` : "无风险提示";
    default:
      return "—";
  }
}
