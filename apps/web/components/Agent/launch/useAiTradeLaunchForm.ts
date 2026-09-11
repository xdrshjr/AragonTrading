"use client";

import { useQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/lib/api";
import {
  AI_BACKTEST_DEFAULT_DECISION_EVERY,
  AI_BACKTEST_MAX_ROUNDS,
  defaultDecisionEvery,
  planRounds,
  validateBacktestRange,
} from "@/lib/aiBacktest";
import {
  DEFAULT_DISCIPLINE_PRESET,
  DISCIPLINE_COPY,
  DISCIPLINE_PRESETS,
} from "@/lib/aiTradeDiscipline";
import {
  AI_TRADE_BASE_RULE_DEFAULTS,
  AI_TRADE_DEFAULT_CONTEXT_ROUNDS,
  AI_TRADE_DEFAULT_POSITION_STEP,
  AI_TRADE_DEFAULT_REASONING_DEPTH,
  AI_TRADE_DEFAULT_WAKE_INTERVAL_MIN,
  AI_TRADE_MAX_SL_PCT,
  AI_TRADE_MAX_SYMBOLS,
  AI_TRADE_MAX_TP_PCT,
  AI_TRADE_PROFIT_GUARD_DEFAULTS,
  REASONING_DEPTH_LABELS,
  deriveDefaultBudget,
  marginReturnFromPricePct,
  priceMoveFromMarginPct,
  validateContextRounds,
  validateDirectionRule,
  validateProfitGuard,
  validateStrategySelection,
  validateTrendRule,
  validateWakeInterval,
} from "@/lib/aiTrade";
import { describeGearResolution, gearResolutionNote } from "@/lib/aiTradeGearResolution";
import { validateHorizon } from "@/lib/aiTradeHorizon";
import {
  aiTradeDisciplineApi,
  aiTradeHorizonApi,
  aiTradeTradabilityApi,
} from "@/lib/aiTradeHorizonApi";
import { describeTradability } from "@/lib/aiTradeTradability";
import {
  collectRisks,
  effectiveMinHoldSeconds as computeEffectiveMinHold,
  unguardedHorizon,
  type AiTradeLaunchRisk,
} from "@/lib/aiTradeLaunchRisk";
import {
  type AiTradeLaunchErrorState,
  type AiTradeLaunchStepId,
  type AiTradeLaunchSummaryInput,
  firstErroredStep,
  inputStepsClean as computeInputStepsClean,
  stepError,
  stepsFor,
} from "@/lib/aiTradeLaunchSteps";
import {
  fromLocalInput,
  horizonOverrides,
  matchTierPreset,
  pctToRaw,
  pctToRaw100,
  rawPctToFraction,
  rawToNumber,
  seedHorizonCustom,
  tiersForPreset,
  toLocalInput,
} from "@/lib/aiTradeLaunchFormat";
import {
  clampGroupBudgetToCap,
  DEFAULT_AUTO_TRADE_MAX_BUDGET_PCT,
  groupBudgetCap,
  isAutoTradable,
  isGroupBudgetClamped,
  totalAllocation,
} from "@/lib/autoTrade";
import { autoTradeSettingsApi } from "@/lib/autoTradeSettingsApi";
import { fmtPrice } from "@/lib/format";
import {
  LIVE_AUTO_DEFAULT_LEVERAGE,
  parseOptionalLeverage,
  parseOptionalPct,
  parseOptionalUsd,
} from "@/lib/liveAuto";
import { tradingApi } from "@/lib/tradingApi";
import type { Bar } from "@/lib/types";
import { AI_TRADE_EXPERIENCE_DEFAULTS } from "@/lib/experience";
import type { AiTradeExperienceRule } from "@/lib/experienceTypes";
import type {
  AiTradeBackstopRepairMode,
  AiTradeBaseRules,
  AiTradeDirectionRule,
  AiTradeHorizonProfile,
  AiTradeHorizonProfileId,
  AiTradeHorizonRule,
  AiTradeIntelRule,
  AiTradeProfitGuardRule,
  AiTradeReasoningDepth,
  AiTradeTrendRule,
} from "@/lib/aiTradeTypes";
import { AI_TRADE_INTEL_RULE_DEFAULT } from "@/lib/aiTradeTypes";
import type { AutoTradeAllocationMode, AutoTradeMember } from "@/lib/liveAutoTypes";
import { TRADABILITY_CONFIRM_REQUIRED_TEXT } from "../AiTradeTradabilityFields";
// **只取类型**（`import type` 在编译期被整句擦除）：壳组件 import 本 hook 是一条运行时依赖，
// 反向再来一条就成环了；类型侧的「环」不存在，故对外类型仍住在壳文件里（对外接口零破坏）。
import type { AiTradeLaunchModalProps, AiTradeLaunchOpts } from "../AiTradeLaunchModal";
import { useAiTradeLaunchState } from "./useAiTradeLaunchState";
import { useTrajectoryCapture } from "./useTrajectoryCapture";
import type { AiTradeLaunchMode } from "@/lib/aiTradeLaunchSteps";

/**
 * 启动向导的**全部受控状态与派生量**（ai-trade-guided-launch §4.2.4）。
 *
 * ── 为什么全部住在这里 ────────────────────────────────────────────────
 * 分步之后子组件会随步骤切换**真实卸载**。这对纯展示组件无害，但对「自带取数与内部 ref」的
 * 组件是致命的：探针不重跑、`useRef` 丢失、由子组件写入的父级 state 再也没有人写。故本轮把
 * 表单态、取数与判据全部收敛到这个 hook 里——父组件全程不卸载，所有 ref 与 query 因而真的能
 * 跨步骤活下来。
 *
 * ── 本文件只允许存在三个 `useEffect`（门禁 G-2 逐条具名）──────────────────
 *  (a) 依赖数组恰为 `[open]` 的那**一个** seed / reset effect；
 *  (b) 可交易性 level 的**跃迁重置**（依赖 `[tradabilityView?.level]`）；
 *  (c) 文件私有的 `useDebounced`（依赖 `[value, ms]`）。
 * 除 (a) 外任何 effect 的依赖数组里都不得出现 `open`。复发根因
 * （`ai-trade-modal-revert-recurrence`）的形状是「**又一个依赖不稳的 effect 把正在编辑的表单冲回
 * 默认**」——(a) 的唯一性 + (c) 的禁令才是它的判据，而 (b) 写的是「跃迁后取消一次风险确认」，
 * 不写回任何默认值。
 */

const DAY = 86_400;

/** 服务端单仓 OCO 默认值（与后端 ai_trade_default_sl_pct / ai_trade_default_tp_pct 对齐）。 */
export const AI_TRADE_DEFAULT_OCO_SL_PCT = 0.05;
export const AI_TRADE_DEFAULT_OCO_TP_PCT = 0.1;

/** ★ A-3：关掉利润保护后的二次确认提示（与既有 `TRADABILITY_CONFIRM_REQUIRED_TEXT` 同款位置）。 */
export const UNGUARDED_CONFIRM_REQUIRED_TEXT =
  "已关闭利润保护：请先阅读上方的后果清单并勾选确认，再启动。";

/** 可交易性探针的防抖（沿用子组件里那 350ms，逐字不改）。 */
const TRADABILITY_DEBOUNCE_MS = 350;

/**
 * 文件私有的防抖（【v2 · P1-4】：`useDebouncedValue` 在本仓**不存在**，故不引用、不新增文件、
 * 不引依赖）。**只喂 `queryKey`，不喂任何提交体字段**——提交体走防抖等于让「按下启动键那一刻
 * 屏幕上的值」与「真正发出去的值」在几百毫秒里不是同一个。
 */
function useDebounced<T>(value: T, ms: number): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), ms);
    return () => clearTimeout(timer);
  }, [value, ms]);
  return settled;
}

export type UseAiTradeLaunchForm = ReturnType<typeof useAiTradeLaunchForm>;

export function useAiTradeLaunchForm(props: AiTradeLaunchModalProps) {
  const {
    open,
    onConfirm,
    submitting,
    acknowledged,
    bar: propBar,
    defaultMembers,
    defaultAllocationMode,
    defaultInstId,
    defaultStrategyIds,
    defaultContextRounds,
    defaultPositionStepPct,
    defaultWakeIntervalMinutes,
    defaultReasoningDepth,
    defaultBaseRule,
    defaultProfitGuard,
    defaultTradingHorizon,
    defaultExperience,
    defaultIntel,
    defaultStopLossPct,
    defaultTakeProfitPct,
    error,
    liveGateReason = null,
    defaultStartTs,
    defaultEndTs,
    defaultDecisionEveryBars,
  } = props;

  // 受控状态层（全部 `useState` 与那份一次性的复位体）住在 `useAiTradeLaunchState.ts`：
  // 切分只为守住 CLAUDE.md 的 800 行红线，判据落点不变——`seed()` 的**唯一**调用点仍是下面
  // 那个依赖收敛为 `[open]` 的 effect。
  const state = useAiTradeLaunchState(props);
  const {
    mode, setMode, stepId, setStepId, visitedSteps, setVisitedSteps, members, setMembers,
    allocationMode, setAllocationMode,
    bar, setBar, instId, setInstId, addSymbolRaw, setAddSymbolRaw, addError, setAddError,
    experience, setExperience, intel, setIntel, selectedIds, setSelectedIds, capitalRaw,
    setCapitalRaw, leverageRaw, setLeverageRaw, takeProfitRaw, setTakeProfitRaw, stopLossRaw,
    setStopLossRaw, stopLossPctRaw, setStopLossPctRaw, takeProfitPctRaw, setTakeProfitPctRaw,
    roundsRaw, setRoundsRaw, positionStep, setPositionStep, wakeIntervalRaw,
    setWakeIntervalRaw, reasoningDepth, setReasoningDepth, baseRuleEnabled, setBaseRuleEnabled,
    baseRulePeriodRaw, setBaseRulePeriodRaw, baseRuleStdRaw, setBaseRuleStdRaw,
    baseRuleTolerance, setBaseRuleTolerance, baseRuleDirectional, setBaseRuleDirectional,
    disciplinePreset, setDisciplinePreset, directionEnabled, setDirectionEnabled,
    directionConfidenceRaw, setDirectionConfidenceRaw, directionQuotaRaw, setDirectionQuotaRaw,
    directionSizeMultRaw, setDirectionSizeMultRaw, trendEnabled, setTrendEnabled,
    trendCounterGate, setTrendCounterGate, trendVoteEnabled, setTrendVoteEnabled,
    trendAllowanceEnabled, setTrendAllowanceEnabled, trendConfidenceRaw, setTrendConfidenceRaw,
    trendSizeMultRaw, setTrendSizeMultRaw, tradabilityConfirmed, setTradabilityConfirmed,
    unguardedConfirmed, setUnguardedConfirmed, profitGuardEnabled, setProfitGuardEnabled,
    armReturnRaw, setArmReturnRaw, givebackRaw, setGivebackRaw, breakevenArmRaw,
    setBreakevenArmRaw, minHoldRaw, setMinHoldRaw, tierPreset, setTierPreset, atrFloorRaw,
    setAtrFloorRaw, atrCapRaw, setAtrCapRaw, cooldownRaw, setCooldownRaw, horizonEnabled,
    setHorizonEnabled, horizonProfileRaw, setHorizonProfileRaw, horizonCustom,
    setHorizonCustom, repairMode, setRepairMode, startInput, setStartInput, endInput,
    setEndInput, everyRaw, setEveryRaw, minConsensusRaw, setMinConsensusRaw, liveAvailable,
    seededMembers, trajectory, setTrajectory,
    trajectorySkipOnce, setTrajectorySkipOnce,
  } = state;
  const truncated = defaultMembers.length > AI_TRADE_MAX_SYMBOLS;
  const atCap = members.length >= AI_TRADE_MAX_SYMBOLS;

  const strategiesQ = useQuery({ queryKey: ["strategies"], queryFn: api.strategies });
  // 账户级总预算上限百分比：**仅实盘**用它当天花板，回测走 initialCapital，故 enabled 收敛到 live 段。
  const settingsQ = useQuery({
    queryKey: ["settings", "auto-trade"],
    queryFn: autoTradeSettingsApi.get,
    enabled: open && mode === "live",
  });
  const maxBudgetPct = settingsQ.data?.effectiveMaxBudgetPct ?? DEFAULT_AUTO_TRADE_MAX_BUDGET_PCT;
  const budgetInst = mode === "live" ? (members[0]?.instId ?? "") : instId;
  const configQ = useQuery({
    queryKey: ["trading", "config", budgetInst, null],
    queryFn: () => tradingApi.config(budgetInst),
    enabled: open && !!budgetInst && mode === "live",
  });
  const availUsdt = configQ.data?.availUsdt ?? null;
  // 模式预设表（只读、不守 AI 总闸）：**按 bar 取**——`estimatedCostBps` 的资金费期数随执行周期变，
  // 契约摘要那一行的「需 ≥N bps」必须与门禁真正判定的数同源；实盘与回测两段都要，故不收敛 mode。
  const horizonQ = useQuery({
    queryKey: ["aiTrade", "horizonProfiles", bar],
    queryFn: () => aiTradeHorizonApi.profiles(bar),
    enabled: open,
  });
  // 纪律预设的只读阈值表：与 `bar` 无关，故 queryKey 不带它；取不到 ⇒ 表格整段不渲染，
  // 四张卡与提交路径照常可用（数值与标签全由后端下发，前端不复制任何一个数）。
  const disciplineQ = useQuery({
    queryKey: ["aiTrade", "disciplinePresets"],
    queryFn: aiTradeDisciplineApi.presets,
    enabled: open,
  });
  // 后训练采集：取数 / 有效值谓词 / 提交体全在 hook 里（RV1），本文件只接线。
  const trajectoryCapture = useTrajectoryCapture({
    open, mode, rule: trajectory, skipOnce: trajectorySkipOnce,
  });

  const capital = parseOptionalUsd(capitalRaw);
  const leverage = parseOptionalLeverage(leverageRaw);
  const takeProfit = parseOptionalUsd(takeProfitRaw);
  const stopLoss = parseOptionalUsd(stopLossRaw);
  const stopLossPct = parseOptionalPct(stopLossPctRaw);
  const takeProfitPct = parseOptionalPct(takeProfitPctRaw);

  // ---------- 可交易性探针（【v2 · P0-2】：取数与跃迁检测**上提到 hook**）----------
  // 子组件在六步里有五步是卸载的：探针留在那里就意味着「用户在第 2 步改了周期，第 5 步的结论
  // 却是上一个周期的」，而 `prevLevel` 这个 ref 随卸载丢失还会让确认态跨周期泄漏。
  const probeKey = useMemo(
    () => JSON.stringify([budgetInst, bar, disciplinePreset, stopLossPct.value ?? null]),
    [budgetInst, bar, disciplinePreset, stopLossPct.value],
  );
  const debouncedProbeKey = useDebounced(probeKey, TRADABILITY_DEBOUNCE_MS);
  const probeArgs = useMemo(
    () => JSON.parse(debouncedProbeKey) as [string, string, string, number | null],
    [debouncedProbeKey],
  );
  const tradabilityQ = useQuery({
    queryKey: ["aiTrade", "tradability", ...probeArgs],
    queryFn: () => aiTradeTradabilityApi.probe(probeArgs[0], probeArgs[1], probeArgs[2], probeArgs[3]),
    enabled: open && !!probeArgs[0] && !!probeArgs[1],
    staleTime: 30_000, // 步骤间往返不重复打后端
    retry: false, // 失败 ⇒ 不阻碍任何操作（后端已保证 200 + unknown）
  });
  const tradabilityView = useMemo(
    () => describeTradability(tradabilityQ.data ?? null),
    [tradabilityQ.data],
  );
  // 三者全部是**派生值**，没有一个是 state（G-6 源码门禁盯着这一行）。它一旦是 state，唯一的
  // 写点就只能在某个组件的 effect 里；而那个组件在六步里有五步是卸载的 ⇒ `tradabilityBlocked`
  // 恒 false ⇒ 一次真实资金启动绕过本轮唯一一次 fail-open 确认。
  const tradabilityNeedsConfirm = tradabilityView?.level === "infeasible";
  const tradabilityBlocked = tradabilityNeedsConfirm && !tradabilityConfirmed;

  const prevTradabilityLevel = useRef<string | null>(null);
  useEffect(() => {
    const level = tradabilityView?.level ?? null;
    // 只在「由非 infeasible 跃迁到 infeasible」时重置确认态：用户在 A 周期上确认过的风险不得被
    // 静默套用到 B 周期。相同 level 的重复取数不重置——那是对同一个风险收第二次税。
    if (level === "infeasible" && prevTradabilityLevel.current !== "infeasible") {
      setTradabilityConfirmed(false);
    }
    prevTradabilityLevel.current = level;
  }, [tradabilityView?.level]);

  // ---------- 弹窗每次打开：唯一的 seed / reset effect（N-8；绝不另写第二个）----------
  useEffect(() => {
    if (!open) return;
    state.seed();
    // 依赖收敛为 [open]：seed 只在弹窗 open false→true 的那一次转换执行，避免父树重渲染/非稳定
    // 默认引用（seededMembers、defaultStrategyIds 等）把正在编辑的表单每秒冲回默认。
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  // ---------- 成员编辑（实盘多标的）----------
  const toggleId = (id: number) =>
    setSelectedIds((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  const updateAllocation = (target: string, raw: string) => {
    const n = Number(raw);
    setMembers((prev) =>
      prev.map((m) => (m.instId === target ? { ...m, allocationPct: Number.isFinite(n) ? n : 0 } : m)),
    );
  };
  const removeMember = (target: string) =>
    setMembers((prev) => prev.filter((m) => m.instId !== target));
  const applyEvenSplit = () =>
    setMembers((prev) => {
      if (prev.length === 0) return prev;
      const each = Math.floor((100 / prev.length) * 100) / 100;
      return prev.map((m) => ({ ...m, allocationPct: each }));
    });
  const handleAddSymbol = () => {
    const sym = addSymbolRaw.trim().toUpperCase();
    if (!sym || atCap) return;
    if (!isAutoTradable(sym)) {
      setAddError("仅支持 USDT 本位永续 / 交割合约自动交易");
      return;
    }
    if (members.some((m) => m.instId === sym)) {
      setAddError("标的已在列表中");
      return;
    }
    setAddError(null);
    const remaining = Math.max(1, Math.round(100 - totalAllocation(members)));
    const pct = allocationMode === "even" ? Math.min(100, remaining) : 100;
    setMembers((prev) => [...prev, { instId: sym, allocationPct: pct }]);
    setAddSymbolRaw("");
  };

  // ---------- 校验与派生 ----------
  const roundsValue = Number.parseInt(roundsRaw, 10);
  const inputError =
    capital.error ||
    leverage.error ||
    takeProfit.error ||
    stopLoss.error ||
    stopLossPct.error ||
    takeProfitPct.error ||
    null;
  const strategyError = validateStrategySelection(selectedIds);
  const roundsError = validateContextRounds(roundsValue);
  const wakeMinutes = wakeIntervalRaw.trim() === "" ? null : Number(wakeIntervalRaw);
  const wakeError = validateWakeInterval(wakeMinutes);

  const baseRulePeriod = Number.parseInt(baseRulePeriodRaw, 10);
  const baseRuleStd = Number(baseRuleStdRaw);
  const baseRuleError = !baseRuleEnabled
    ? null
    : !Number.isInteger(baseRulePeriod) || baseRulePeriod < 2 || baseRulePeriod > 200
      ? "基础策略均线周期需为 2~200 的整数"
      : !Number.isFinite(baseRuleStd) || baseRuleStd <= 0 || baseRuleStd > 5
        ? "基础策略标准差倍数需在 (0, 5] 之间"
        : null;
  const baseRulesPayload: AiTradeBaseRules = {
    maBandEntry: baseRuleEnabled
      ? {
          enabled: true,
          period: baseRulePeriod,
          stdMult: baseRuleStd,
          tolerance: baseRuleTolerance,
          directional: baseRuleDirectional,
        }
      : { enabled: false },
  };

  const profitGuardPayload: AiTradeProfitGuardRule = profitGuardEnabled
    ? {
        enabled: true,
        armReturnPct: rawPctToFraction(armReturnRaw),
        givebackPct: rawPctToFraction(givebackRaw),
        breakevenArmReturnPct: rawPctToFraction(breakevenArmRaw),
        minHoldSeconds: Number(minHoldRaw),
        // 阶梯预设 → 阶梯数组（未知 key 回落标准预设，绝不静默提交一个空阶梯）。
        givebackTiers: tiersForPreset(tierPreset),
        atrFloorMult: Number(atrFloorRaw),
        atrCapMult: Number(atrCapRaw),
        cooldownSeconds: Number(cooldownRaw),
      }
    : { enabled: false };
  const profitGuardError = validateProfitGuard(profitGuardPayload);
  const armPriceMovePct = priceMoveFromMarginPct(
    profitGuardPayload.armReturnPct ?? null,
    leverage.value,
  );
  const slMarginPct = marginReturnFromPricePct(
    stopLossPct.value ?? AI_TRADE_DEFAULT_OCO_SL_PCT,
    leverage.value,
  );
  const tpMarginPct = marginReturnFromPricePct(
    takeProfitPct.value ?? AI_TRADE_DEFAULT_OCO_TP_PCT,
    leverage.value,
  );

  const horizonProfile = horizonProfileRaw ?? horizonQ.data?.defaultProfile ?? null;
  const horizonProfileId = (horizonProfile ?? "custom") as AiTradeHorizonProfileId;
  const tradingHorizonPayload: AiTradeHorizonRule = horizonEnabled
    ? {
        enabled: true,
        ...(horizonProfile ? { profile: horizonProfile } : {}),
        ...(repairMode ? { backstopRepair: repairMode } : {}),
        ...horizonOverrides(horizonProfileId, horizonCustom),
      }
    : { enabled: false };
  const activeHorizonProfile =
    horizonQ.data?.profiles.find((p) => p.id === horizonProfileId) ?? null;
  const horizonError = validateHorizon(tradingHorizonPayload);

  const alignToProfile = (profile: AiTradeHorizonProfile | undefined) => {
    if (!profile || profile.bar == null) return;
    setBar(profile.bar as Bar);
    if (profile.wakeIntervalMinutes != null) setWakeIntervalRaw(String(profile.wakeIntervalMinutes));
  };
  const handleProfileSelect = (profile: AiTradeHorizonProfile) => {
    setHorizonProfileRaw(profile.id);
    alignToProfile(profile);
  };

  const sumPct = totalAllocation(members);
  const hasUnsupportedMember = members.some((m) => !isAutoTradable(m.instId));
  const membersError =
    members.length === 0
      ? "请至少保留一个自动交易标的"
      : hasUnsupportedMember
        ? "含不支持自动交易的标的（仅 USDT 本位永续 / 交割合约）"
        : members.some((m) => !(m.allocationPct > 0 && m.allocationPct <= 100))
          ? "每个标的百分比须在 (0, 100] 之间"
          : allocationMode === "even" && sumPct > 100 + 1e-9
            ? "各标的百分比之和不可超过 100%（或切换为「独立上限」）"
            : null;
  const oversubscribed = allocationMode === "max_ratio" && sumPct > 100 + 1e-9;
  const instSupported = isAutoTradable(instId);

  const startTs = fromLocalInput(startInput);
  const endTs = fromLocalInput(endInput);
  const rangeError =
    startTs == null || endTs == null
      ? "请选择回测区间起止时间"
      : validateBacktestRange(startTs, endTs);
  const smartEvery = useMemo(
    () =>
      startTs != null && endTs != null
        ? defaultDecisionEvery(
            startTs,
            endTs,
            bar,
            AI_BACKTEST_DEFAULT_DECISION_EVERY,
            AI_BACKTEST_MAX_ROUNDS,
          )
        : AI_BACKTEST_DEFAULT_DECISION_EVERY,
    [startTs, endTs, bar],
  );
  const everyOverride = everyRaw.trim() ? Number.parseInt(everyRaw, 10) : null;
  const everyError =
    everyRaw.trim() && (!Number.isInteger(everyOverride) || (everyOverride ?? 0) < 1)
      ? "决策间隔需为 ≥1 的整数"
      : null;
  const everyValue = everyOverride && everyOverride >= 1 ? everyOverride : smartEvery;
  const minConsensusValue = minConsensusRaw.trim() ? Number.parseInt(minConsensusRaw, 10) : null;
  const minConsensusError =
    minConsensusRaw.trim() && (!Number.isInteger(minConsensusValue) || (minConsensusValue ?? 0) < 0)
      ? "共识门槛需为 ≥0 的整数"
      : null;
  const plannedRounds =
    startTs != null && endTs != null && !rangeError ? planRounds(startTs, endTs, bar, everyValue) : 0;
  const roundsCapError =
    plannedRounds > AI_BACKTEST_MAX_ROUNDS
      ? `计划决策轮数 ${plannedRounds} 超上限 ${AI_BACKTEST_MAX_ROUNDS}，请缩短区间或增大决策间隔`
      : null;

  const previewBudget = useMemo(() => {
    if (availUsdt == null) return null;
    const raw = capital.value ?? deriveDefaultBudget(availUsdt);
    return clampGroupBudgetToCap(raw, availUsdt, maxBudgetPct);
  }, [availUsdt, capital.value, maxBudgetPct]);
  const isClamped = isGroupBudgetClamped(capital.value, availUsdt, maxBudgetPct);
  const budgetCap = groupBudgetCap(availUsdt, maxBudgetPct);
  const usingDefault = capital.value == null;

  const directionPayload: AiTradeDirectionRule = {
    contrarianEnabled: directionEnabled,
    contrarianMinConfidence: rawToNumber(directionConfidenceRaw),
    contrarianMaxPerSession: rawToNumber(directionQuotaRaw),
    contrarianSizeMult: rawToNumber(directionSizeMultRaw),
  };
  const directionError = validateDirectionRule(directionPayload);
  const trendPayload: AiTradeTrendRule = {
    enabled: trendEnabled,
    counterGateEnabled: trendCounterGate,
    counterMinConfidence: rawToNumber(trendConfidenceRaw),
    counterSizeMult: rawToNumber(trendSizeMultRaw),
    consensusVoteEnabled: trendVoteEnabled,
    contrarianAllowanceEnabled: trendAllowanceEnabled,
  };
  const trendError = validateTrendRule(trendPayload);

  const gearNote = useMemo(
    () => gearResolutionNote(describeGearResolution(positionStep)),
    [positionStep],
  );

  // ★ A-4：本次启动中会失效的阈值键（判据与后端 `discipline.inert_thresholds()` 逐字同构）。
  const inertThresholdKeys = profitGuardEnabled ? [] : ["ai_trade_profit_guard_hard_tp_return_pct"];
  const hardTakeProfitText = (() => {
    const rows = disciplineQ.data?.presets.find((p) => p.id === disciplinePreset)?.rows;
    const row = rows?.find((r) => r.key === "ai_trade_profit_guard_hard_tp_return_pct");
    return row && !row.off ? `${row.label} ${row.value}` : null;
  })();

  // ---------- 轴 A：互锁总闸（wire 上的只读回执，键缺席 ⇒ false ⇒ 不阻断）----------
  const interlockArmed = horizonQ.data?.requireProfitExitWithHorizon === true;
  const effectiveMinHoldSeconds = computeEffectiveMinHold({
    profilesReady: !horizonQ.isPending && !!horizonQ.data,
    profileId: horizonProfileId,
    profiles: horizonQ.data?.profiles ?? [],
    customMinHoldRaw: horizonCustom.minHoldRaw,
  });
  const isUnguardedHorizon = unguardedHorizon({
    profitGuardEnabled,
    horizonEnabled,
    effectiveMinHoldSeconds,
    interlockArmed,
  });
  // 与服务端判据**逐字同构**：默认（互锁关）⇒ 恒 false ⇒ 前端不阻断、只在风险清单里披露。
  const unguardedBlocked = mode === "live" && isUnguardedHorizon && !unguardedConfirmed;

  const baseValid =
    !inputError &&
    !strategyError &&
    !roundsError &&
    !baseRuleError &&
    !profitGuardError &&
    !horizonError &&
    !directionError &&
    !trendError;
  const ready =
    mode === "live"
      ? baseValid && !membersError && !wakeError && acknowledged
        && !trajectoryCapture.error
      : baseValid &&
        instSupported &&
        !!instId &&
        !rangeError &&
        !everyError &&
        !minConsensusError &&
        !roundsCapError;

  // `firstError` 的表达式**逐字节不改**（它守着 79 条既有用例的次序）。
  const firstError =
    strategyError ||
    roundsError ||
    inputError ||
    baseRuleError ||
    profitGuardError ||
    horizonError ||
    (mode === "live"
      ? membersError || wakeError
      : instSupported
        ? null
        : "标的不支持自动交易（仅 USDT 本位永续 / 交割合约）") ||
    (mode === "backtest" ? rangeError || everyError || minConsensusError || roundsCapError : null) ||
    (tradabilityBlocked ? TRADABILITY_CONFIRM_REQUIRED_TEXT : null) ||
    (unguardedBlocked ? UNGUARDED_CONFIRM_REQUIRED_TEXT : null) ||
    error;

  // ---------- 风险汇聚（不变式 I-1）----------
  const risks: AiTradeLaunchRisk[] = useMemo(
    () =>
      collectRisks({
        mode,
        gearNote,
        tradabilityLevel: tradabilityView?.level ?? null,
        tradabilityHeadline: tradabilityView?.headline || null,
        profitGuardEnabled,
        horizonEnabled,
        effectiveMinHoldSeconds,
        interlockArmed,
        inertHardTakeProfitText: hardTakeProfitText,
        allocationOversubscribedPct: oversubscribed ? Math.round(sumPct * 100) / 100 : null,
        budgetClampText:
          mode === "live" && isClamped && capital.value != null
            ? `输入 ${fmtPrice(capital.value)} 超过可用余额 ${maxBudgetPct}% 上限` +
              `（${fmtPrice(budgetCap)} USDT），已按上限 ${fmtPrice(budgetCap)} 计算`
            : null,
        truncatedFrom: mode === "live" && truncated ? defaultMembers.length : null,
        maxSymbols: AI_TRADE_MAX_SYMBOLS,
        reasoningDepth: mode === "live" ? reasoningDepth : "standard",
        wakeMinutes: mode === "live" ? wakeMinutes : null,
        experienceEnabled: experience.enabled !== false,
        wakeFloorDivisor: horizonQ.data?.wakeHorizonDivisor ?? 0,
        targetHoldMinSeconds: activeHorizonProfile?.targetHoldMinSeconds ?? 0,
      }),
    [
      mode, gearNote, tradabilityView?.level, tradabilityView?.headline, profitGuardEnabled,
      horizonEnabled, effectiveMinHoldSeconds, interlockArmed, hardTakeProfitText, oversubscribed,
      sumPct, isClamped, capital.value, maxBudgetPct, budgetCap, truncated, defaultMembers.length,
      reasoningDepth, wakeMinutes, experience.enabled, horizonQ.data?.wakeHorizonDivisor,
      activeHorizonProfile?.targetHoldMinSeconds,
    ],
  );

  // ---------- 步骤模型 ----------
  const errors: AiTradeLaunchErrorState = {
    strategyError,
    membersError,
    instSupported: instSupported ? null : "标的不支持自动交易（仅 USDT 本位永续 / 交割合约）",
    instId: instId ? null : "请填写回测标的",
    rangeError,
    horizonError,
    wakeError,
    roundsError,
    everyError,
    minConsensusError,
    roundsCapError,
    inputError,
    baseRuleError,
    profitGuardError,
    directionError,
    trendError,
    tradabilityBlocked: tradabilityBlocked ? TRADABILITY_CONFIRM_REQUIRED_TEXT : null,
    unguardedBlocked: unguardedBlocked ? UNGUARDED_CONFIRM_REQUIRED_TEXT : null,
    acknowledged: acknowledged ? null : "请先勾选「我已知晓这是真实资金 AI 自动交易」",
    serverError: error ?? null,
    trajectoryDirError: trajectoryCapture.error,
  };
  const stepModel = { mode, errors };
  const inputStepsClean = computeInputStepsClean(stepModel);
  const blockingStep = firstErroredStep(stepModel);

  const summaryInput: AiTradeLaunchSummaryInput = {
    mode,
    memberCount: members.length,
    instId,
    strategyCount: selectedIds.length,
    bar,
    horizonLabel: horizonEnabled ? (activeHorizonProfile?.title ?? "自定义") : "不限持仓周期",
    wakeText: wakeMinutes && wakeMinutes > 0 ? `唤醒 ${wakeMinutes} 分` : "仅 K 线触发",
    decisionEveryText: `每 ${everyValue} 根`,
    budgetText:
      capital.value != null
        ? `${fmtPrice(capital.value)} USDT`
        : mode === "live"
          ? "默认预算"
          : "默认资金",
    leverageText: leverage.value != null ? `${leverage.value}×` : `默认 ${LIVE_AUTO_DEFAULT_LEVERAGE}×`,
    positionStepText: `${Math.round(positionStep * 100)}%`,
    reasoningDepthLabel:
      mode === "live" ? REASONING_DEPTH_LABELS[reasoningDepth] : REASONING_DEPTH_LABELS.standard,
    experienceEnabled: experience.enabled !== false,
    intelEnabled: mode === "live" && intel.enabled === true,
    disciplineLabel: DISCIPLINE_COPY[disciplinePreset].label,
    baseRuleEnabled,
    profitGuardEnabled,
    riskCount: risks.length,
    trajectoryEnabled: trajectoryCapture.effectiveEnabled,
  };

  const visibleSteps = useMemo(() => stepsFor(mode), [mode]);
  const gotoStep = useCallback((next: AiTradeLaunchStepId) => {
    setStepId(next);
    setVisitedSteps((prev) => (prev.includes(next) ? prev : [...prev, next]));
  }, [setStepId, setVisitedSteps]);
  const changeMode = useCallback((next: AiTradeLaunchMode) => {
    setMode(next);
    // N-9：切换模式后若当前步在新模式下不可见，收敛到最近的可见步（本轮六步两模式皆可见）。
    setStepId((cur) => (stepsFor(next).some((s) => s.id === cur) ? cur : "scope"));
  }, []);

  // ---------- 提交 ----------
  const handleConfirm = () => {
    if (!ready || submitting || tradabilityBlocked || unguardedBlocked) return;
    const base = {
      strategyIds: selectedIds,
      capitalUsd: capital.value,
      leverage: leverage.value,
      takeProfitUsd: takeProfit.value,
      stopLossUsd: stopLoss.value,
      stopLossPct: stopLossPct.value,
      takeProfitPct: takeProfitPct.value,
      positionStepPct: positionStep,
      baseRules: baseRulesPayload,
      tradingHorizon: tradingHorizonPayload,
      experience,
      intel,
      discipline: { preset: disciplinePreset },
      direction: directionPayload,
      trend: trendPayload,
      contextRefreshRounds: roundsValue,
      bar,
    };
    if (mode === "live") {
      onConfirm({
        mode: "live",
        members,
        allocationMode,
        instId: members[0]?.instId ?? instId,
        ...base,
        wakeIntervalMinutes: wakeMinutes ?? 0,
        profitGuard: profitGuardPayload,
        reasoningDepth,
        trajectoryCapture: trajectoryCapture.requestRule,
        trajectoryPreference: trajectory,
      });
      return;
    }
    onConfirm({
      mode: "backtest",
      members,
      allocationMode,
      instId,
      ...base,
      profitGuard: profitGuardPayload,
      startTs: startTs ?? undefined,
      endTs: endTs ?? undefined,
      decisionEveryBars: everyValue,
      minConsensus: minConsensusValue,
    });
  };

  return {
    // 段控与导航
    mode, changeMode, liveAvailable, liveGateReason,
    stepId, visitedSteps, gotoStep, visibleSteps, stepModel, inputStepsClean, blockingStep,
    stepErrorOf: (id: AiTradeLaunchStepId) => stepError(stepModel, id),
    // 查询
    strategiesQ, horizonQ, disciplineQ, tradabilityQ, configQ,
    // 标的与策略
    members, setMembers, allocationMode, setAllocationMode, updateAllocation, removeMember,
    applyEvenSplit, handleAddSymbol, addSymbolRaw, setAddSymbolRaw, addError, atCap, truncated,
    defaultMembersCount: defaultMembers.length, oversubscribed, sumPct,
    instId, setInstId, instSupported, selectedIds, toggleId,
    // 周期与节奏
    bar, setBar, horizonEnabled, setHorizonEnabled, horizonProfileId, handleProfileSelect,
    horizonCustom, setHorizonCustom, activeHorizonProfile, alignToProfile, repairMode, setRepairMode,
    wakeIntervalRaw, setWakeIntervalRaw, wakeMinutes, roundsRaw, setRoundsRaw,
    startInput, setStartInput, endInput, setEndInput, everyRaw, setEveryRaw, smartEvery,
    everyValue, minConsensusRaw, setMinConsensusRaw, plannedRounds, roundsCapError,
    // 资金与仓位
    capitalRaw, setCapitalRaw, leverageRaw, setLeverageRaw, takeProfitRaw, setTakeProfitRaw,
    stopLossRaw, setStopLossRaw, stopLossPctRaw, setStopLossPctRaw, takeProfitPctRaw,
    setTakeProfitPctRaw, capital, leverage, stopLossPct, takeProfitPct, slMarginPct, tpMarginPct,
    availUsdt, previewBudget, isClamped, budgetCap, maxBudgetPct, usingDefault,
    positionStep, setPositionStep, gearNote,
    // Agent 认知
    reasoningDepth, setReasoningDepth, experience, setExperience, intel, setIntel,
    trajectory, setTrajectory, setTrajectorySkipOnce, trajectoryCapture,
    // 纪律与护栏
    disciplinePreset, setDisciplinePreset, inertThresholdKeys, hardTakeProfitText,
    baseRuleEnabled, setBaseRuleEnabled, baseRulePeriodRaw, setBaseRulePeriodRaw,
    baseRuleStdRaw, setBaseRuleStdRaw, baseRuleTolerance, setBaseRuleTolerance,
    baseRuleDirectional, setBaseRuleDirectional,
    directionPayload, setDirectionEnabled, directionConfidenceRaw, setDirectionConfidenceRaw,
    directionQuotaRaw, setDirectionQuotaRaw, directionSizeMultRaw, setDirectionSizeMultRaw,
    directionError, trendPayload, setTrendEnabled, setTrendCounterGate, setTrendVoteEnabled,
    setTrendAllowanceEnabled, trendConfidenceRaw, setTrendConfidenceRaw, trendSizeMultRaw,
    setTrendSizeMultRaw, trendError,
    profitGuardEnabled, setProfitGuardEnabled, armReturnRaw, setArmReturnRaw, givebackRaw,
    setGivebackRaw, breakevenArmRaw, setBreakevenArmRaw, minHoldRaw, setMinHoldRaw,
    tierPreset, setTierPreset, atrFloorRaw, setAtrFloorRaw, atrCapRaw, setAtrCapRaw,
    cooldownRaw, setCooldownRaw, armPriceMovePct,
    // 可交易性预检（探针已上提；子组件降为纯展示）
    tradabilityView, tradabilityConfirmed, setTradabilityConfirmed, tradabilityNeedsConfirm,
    tradabilityBlocked,
    // 轴 A
    interlockArmed, isUnguardedHorizon, unguardedConfirmed, setUnguardedConfirmed, unguardedBlocked,
    effectiveMinHoldSeconds,
    // 汇总
    risks, summaryInput, ready, firstError, handleConfirm,
    maxSlPct: AI_TRADE_MAX_SL_PCT, maxTpPct: AI_TRADE_MAX_TP_PCT,
    disciplinePresets: DISCIPLINE_PRESETS,
  };
}
