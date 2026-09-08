"use client";

import { useMemo, useState } from "react";
import {
  AI_TRADE_BASE_RULE_DEFAULTS,
  AI_TRADE_DEFAULT_CONTEXT_ROUNDS,
  AI_TRADE_DEFAULT_POSITION_STEP,
  AI_TRADE_DEFAULT_REASONING_DEPTH,
  AI_TRADE_DEFAULT_WAKE_INTERVAL_MIN,
  AI_TRADE_MAX_SYMBOLS,
  AI_TRADE_PROFIT_GUARD_DEFAULTS,
} from "@/lib/aiTrade";
import { DEFAULT_DISCIPLINE_PRESET } from "@/lib/aiTradeDiscipline";
import {
  matchTierPreset,
  pctToRaw,
  pctToRaw100,
  seedHorizonCustom,
  toLocalInput,
} from "@/lib/aiTradeLaunchFormat";
import { AI_TRADE_EXPERIENCE_DEFAULTS } from "@/lib/experience";
import type { AiTradeExperienceRule } from "@/lib/experienceTypes";
import type { Bar } from "@/lib/types";
import type {
  AiTradeBackstopRepairMode,
  AiTradeHorizonProfileId,
  AiTradeIntelRule,
  AiTradeReasoningDepth,
} from "@/lib/aiTradeTypes";
import { AI_TRADE_INTEL_RULE_DEFAULT } from "@/lib/aiTradeTypes";
import type { AutoTradeAllocationMode, AutoTradeMember } from "@/lib/liveAutoTypes";
import type { AiTradeLaunchStepId } from "@/lib/aiTradeLaunchSteps";
import type { AiTradeLaunchMode } from "@/lib/aiTradeLaunchSteps";
import type { AiTradeLaunchModalProps } from "../AiTradeLaunchModal";

/**
 * 启动向导的**受控状态层**（ai-trade-guided-launch §4.2.4 的实现拆分）。
 *
 * 这里只有两样东西：全部 `useState` 声明，以及把它们**一次性**写回默认值的 `seed()`。
 *
 * ── 为什么单独一个文件 ────────────────────────────────────────────────
 * `useAiTradeLaunchForm.ts` 收敛了状态 + 取数 + 派生 + 提交四件事后超过了 CLAUDE.md 的 800 行
 * 红线（门禁 G-1）。状态声明与复位体是其中**最机械、最没有判断**的一段，切出来对判据零影响：
 * `seed()` 只有一个调用点，就是那个依赖收敛为 `[open]` 的 effect（G-2 的落点仍在 form 文件里）。
 *
 * ⚠️ **本文件里不得出现任何 `useEffect`**（由 G-2 的子判据扫描冻结）：复位必须只经由那**一个**
 * `[open]` effect 发生，另写第二个正是 `ai-trade-modal-revert-recurrence` 的复发根因
 * ——用户正在编辑的字段会在约 1 秒后被冲回默认。
 */
const DAY = 86_400;

export type UseAiTradeLaunchState = ReturnType<typeof useAiTradeLaunchState>;

export function useAiTradeLaunchState(props: AiTradeLaunchModalProps) {
  const {
    liveGateReason = null, bar: propBar, defaultMembers, defaultAllocationMode, defaultInstId,
    defaultStrategyIds, defaultContextRounds, defaultPositionStepPct, defaultWakeIntervalMinutes,
    defaultReasoningDepth, defaultBaseRule, defaultProfitGuard, defaultTradingHorizon,
    defaultExperience, defaultIntel, defaultStopLossPct, defaultTakeProfitPct,
    defaultStartTs, defaultEndTs, defaultDecisionEveryBars,
  } = props;
  const liveAvailable = liveGateReason == null;
  const [mode, setMode] = useState<AiTradeLaunchMode>(liveAvailable ? "live" : "backtest");
  // 向导位置：以 `StepId` 存储（N-9），**不以数字下标**——下标在两种模式下指向不同步骤。
  const [stepId, setStepId] = useState<AiTradeLaunchStepId>("scope");
  // 已访问过的步骤（N-4 状态点的「未访问 / 已访问」两态）。它**不是**判据、不参与任何
  // 放行决定，只决定轨上那个徽标画的是数字还是勾——用户凭它一眼看出自己还没翻过哪几格。
  const [visitedSteps, setVisitedSteps] = useState<AiTradeLaunchStepId[]>(["scope"]);
  const [members, setMembers] = useState<AutoTradeMember[]>([]);
  const [allocationMode, setAllocationMode] =
    useState<AutoTradeAllocationMode>(defaultAllocationMode);
  const [bar, setBar] = useState<Bar>(propBar as Bar);
  const [instId, setInstId] = useState(defaultInstId);
  const [addSymbolRaw, setAddSymbolRaw] = useState("");
  const [addError, setAddError] = useState<string | null>(null);
  const [experience, setExperience] = useState<AiTradeExperienceRule>(
    defaultExperience ?? AI_TRADE_EXPERIENCE_DEFAULTS,
  );
  const [intel, setIntel] = useState<AiTradeIntelRule>(defaultIntel ?? AI_TRADE_INTEL_RULE_DEFAULT);
  const [selectedIds, setSelectedIds] = useState<number[]>(defaultStrategyIds);
  const [capitalRaw, setCapitalRaw] = useState("");
  const [leverageRaw, setLeverageRaw] = useState("");
  const [takeProfitRaw, setTakeProfitRaw] = useState("");
  const [stopLossRaw, setStopLossRaw] = useState("");
  const [stopLossPctRaw, setStopLossPctRaw] = useState(pctToRaw(defaultStopLossPct));
  const [takeProfitPctRaw, setTakeProfitPctRaw] = useState(pctToRaw(defaultTakeProfitPct));
  const [roundsRaw, setRoundsRaw] = useState(
    String(defaultContextRounds || AI_TRADE_DEFAULT_CONTEXT_ROUNDS),
  );
  const [positionStep, setPositionStep] = useState(
    defaultPositionStepPct || AI_TRADE_DEFAULT_POSITION_STEP,
  );
  const [wakeIntervalRaw, setWakeIntervalRaw] = useState(
    String(defaultWakeIntervalMinutes ?? AI_TRADE_DEFAULT_WAKE_INTERVAL_MIN),
  );
  const [reasoningDepth, setReasoningDepth] = useState<AiTradeReasoningDepth>(
    defaultReasoningDepth ?? (AI_TRADE_DEFAULT_REASONING_DEPTH as AiTradeReasoningDepth),
  );
  const [baseRuleEnabled, setBaseRuleEnabled] = useState(
    defaultBaseRule.enabled ?? AI_TRADE_BASE_RULE_DEFAULTS.enabled,
  );
  const [baseRulePeriodRaw, setBaseRulePeriodRaw] = useState(
    String(defaultBaseRule.period ?? AI_TRADE_BASE_RULE_DEFAULTS.period),
  );
  const [baseRuleStdRaw, setBaseRuleStdRaw] = useState(
    String(defaultBaseRule.stdMult ?? AI_TRADE_BASE_RULE_DEFAULTS.stdMult),
  );
  const [baseRuleTolerance, setBaseRuleTolerance] = useState(
    defaultBaseRule.tolerance ?? AI_TRADE_BASE_RULE_DEFAULTS.tolerance,
  );
  const [baseRuleDirectional, setBaseRuleDirectional] = useState(
    defaultBaseRule.directional ?? AI_TRADE_BASE_RULE_DEFAULTS.directional,
  );
  const [disciplinePreset, setDisciplinePreset] = useState(DEFAULT_DISCIPLINE_PRESET);
  const [directionEnabled, setDirectionEnabled] = useState(true);
  const [directionConfidenceRaw, setDirectionConfidenceRaw] = useState("");
  const [directionQuotaRaw, setDirectionQuotaRaw] = useState("");
  const [directionSizeMultRaw, setDirectionSizeMultRaw] = useState("");
  const [trendEnabled, setTrendEnabled] = useState(true);
  const [trendCounterGate, setTrendCounterGate] = useState(true);
  const [trendVoteEnabled, setTrendVoteEnabled] = useState(true);
  const [trendAllowanceEnabled, setTrendAllowanceEnabled] = useState(true);
  const [trendConfidenceRaw, setTrendConfidenceRaw] = useState("");
  const [trendSizeMultRaw, setTrendSizeMultRaw] = useState("");
  // 可交易性预检的二次确认态：**用户输入**，故仍是 state。它的派生对手盘
  // （`tradabilityNeedsConfirm` / `tradabilityBlocked`）见下方——【v2 · P0-2】那两个绝不能是 state。
  const [tradabilityConfirmed, setTradabilityConfirmed] = useState(false);
  const [unguardedConfirmed, setUnguardedConfirmed] = useState(false);
  const [profitGuardEnabled, setProfitGuardEnabled] = useState(
    defaultProfitGuard.enabled ?? AI_TRADE_PROFIT_GUARD_DEFAULTS.enabled,
  );
  const [armReturnRaw, setArmReturnRaw] = useState(
    pctToRaw100(defaultProfitGuard.armReturnPct, AI_TRADE_PROFIT_GUARD_DEFAULTS.armReturnPct),
  );
  const [givebackRaw, setGivebackRaw] = useState(
    pctToRaw100(defaultProfitGuard.givebackPct, AI_TRADE_PROFIT_GUARD_DEFAULTS.givebackPct),
  );
  const [breakevenArmRaw, setBreakevenArmRaw] = useState(
    pctToRaw100(
      defaultProfitGuard.breakevenArmReturnPct,
      AI_TRADE_PROFIT_GUARD_DEFAULTS.breakevenArmReturnPct,
    ),
  );
  const [minHoldRaw, setMinHoldRaw] = useState(
    String(defaultProfitGuard.minHoldSeconds ?? AI_TRADE_PROFIT_GUARD_DEFAULTS.minHoldSeconds),
  );
  const [tierPreset, setTierPreset] = useState(() =>
    matchTierPreset(defaultProfitGuard.givebackTiers),
  );
  const [atrFloorRaw, setAtrFloorRaw] = useState(
    String(defaultProfitGuard.atrFloorMult ?? AI_TRADE_PROFIT_GUARD_DEFAULTS.atrFloorMult),
  );
  const [atrCapRaw, setAtrCapRaw] = useState(
    String(defaultProfitGuard.atrCapMult ?? AI_TRADE_PROFIT_GUARD_DEFAULTS.atrCapMult),
  );
  const [cooldownRaw, setCooldownRaw] = useState(
    String(defaultProfitGuard.cooldownSeconds ?? AI_TRADE_PROFIT_GUARD_DEFAULTS.cooldownSeconds),
  );
  const [horizonEnabled, setHorizonEnabled] = useState(defaultTradingHorizon?.enabled ?? true);
  const [horizonProfileRaw, setHorizonProfileRaw] = useState<AiTradeHorizonProfileId | null>(
    defaultTradingHorizon?.profile ?? null,
  );
  const [horizonCustom, setHorizonCustom] = useState(() => seedHorizonCustom(defaultTradingHorizon));
  const [repairMode, setRepairMode] = useState<AiTradeBackstopRepairMode | null>(null);
  const [startInput, setStartInput] = useState("");
  const [endInput, setEndInput] = useState("");
  const [everyRaw, setEveryRaw] = useState("");
  const [minConsensusRaw, setMinConsensusRaw] = useState("");

  const seededMembers = useMemo<AutoTradeMember[]>(() => {
    const src =
      defaultMembers.length > 0 ? defaultMembers : [{ instId: defaultInstId, allocationPct: 100 }];
    return src.slice(0, AI_TRADE_MAX_SYMBOLS).map((m) => ({ ...m }));
  }, [defaultMembers, defaultInstId]);

  /**
   * 把全部受控输入写回默认值。**唯一调用点**是 `useAiTradeLaunchForm` 里那个依赖收敛为
   * `[open]` 的 effect —— 弹窗 open false→true 的那一次转换执行一次，别处一律不许调。
   */
  const seed = () => {
    setMode(liveAvailable ? "live" : "backtest");
    setStepId("scope"); // 向导位置刻意不持久化：一次启动是一次完整的授权
    setVisitedSteps(["scope"]);
    setMembers(seededMembers);
    setAllocationMode(defaultAllocationMode);
    setBar(propBar as Bar);
    setInstId(seededMembers[0]?.instId ?? defaultInstId);
    setAddSymbolRaw("");
    setAddError(null);
    setSelectedIds(defaultStrategyIds);
    setRoundsRaw(String(defaultContextRounds || AI_TRADE_DEFAULT_CONTEXT_ROUNDS));
    setPositionStep(defaultPositionStepPct || AI_TRADE_DEFAULT_POSITION_STEP);
    setWakeIntervalRaw(String(defaultWakeIntervalMinutes ?? AI_TRADE_DEFAULT_WAKE_INTERVAL_MIN));
    setReasoningDepth(
      defaultReasoningDepth ?? (AI_TRADE_DEFAULT_REASONING_DEPTH as AiTradeReasoningDepth),
    );
    setBaseRuleEnabled(defaultBaseRule.enabled ?? AI_TRADE_BASE_RULE_DEFAULTS.enabled);
    setBaseRulePeriodRaw(String(defaultBaseRule.period ?? AI_TRADE_BASE_RULE_DEFAULTS.period));
    setBaseRuleStdRaw(String(defaultBaseRule.stdMult ?? AI_TRADE_BASE_RULE_DEFAULTS.stdMult));
    setBaseRuleTolerance(defaultBaseRule.tolerance ?? AI_TRADE_BASE_RULE_DEFAULTS.tolerance);
    setBaseRuleDirectional(defaultBaseRule.directional ?? AI_TRADE_BASE_RULE_DEFAULTS.directional);
    setTrendEnabled(true);
    setTrendCounterGate(true);
    setTrendVoteEnabled(true);
    setTrendAllowanceEnabled(true);
    setTrendConfidenceRaw("");
    setTrendSizeMultRaw("");
    setProfitGuardEnabled(defaultProfitGuard.enabled ?? AI_TRADE_PROFIT_GUARD_DEFAULTS.enabled);
    setUnguardedConfirmed(false);
    setArmReturnRaw(
      pctToRaw100(defaultProfitGuard.armReturnPct, AI_TRADE_PROFIT_GUARD_DEFAULTS.armReturnPct),
    );
    setGivebackRaw(
      pctToRaw100(defaultProfitGuard.givebackPct, AI_TRADE_PROFIT_GUARD_DEFAULTS.givebackPct),
    );
    setBreakevenArmRaw(
      pctToRaw100(
        defaultProfitGuard.breakevenArmReturnPct,
        AI_TRADE_PROFIT_GUARD_DEFAULTS.breakevenArmReturnPct,
      ),
    );
    setMinHoldRaw(
      String(defaultProfitGuard.minHoldSeconds ?? AI_TRADE_PROFIT_GUARD_DEFAULTS.minHoldSeconds),
    );
    setTierPreset(matchTierPreset(defaultProfitGuard.givebackTiers));
    setAtrFloorRaw(
      String(defaultProfitGuard.atrFloorMult ?? AI_TRADE_PROFIT_GUARD_DEFAULTS.atrFloorMult),
    );
    setAtrCapRaw(String(defaultProfitGuard.atrCapMult ?? AI_TRADE_PROFIT_GUARD_DEFAULTS.atrCapMult));
    setCooldownRaw(
      String(defaultProfitGuard.cooldownSeconds ?? AI_TRADE_PROFIT_GUARD_DEFAULTS.cooldownSeconds),
    );
    setHorizonEnabled(defaultTradingHorizon?.enabled ?? true);
    setHorizonProfileRaw(defaultTradingHorizon?.profile ?? null);
    setHorizonCustom(seedHorizonCustom(defaultTradingHorizon));
    setRepairMode(null);
    setDisciplinePreset(DEFAULT_DISCIPLINE_PRESET);
    setDirectionEnabled(true);
    setDirectionConfidenceRaw("");
    setDirectionQuotaRaw("");
    setDirectionSizeMultRaw("");
    setTradabilityConfirmed(false);
    setCapitalRaw("");
    setLeverageRaw("");
    setTakeProfitRaw("");
    setStopLossRaw("");
    setStopLossPctRaw(pctToRaw(defaultStopLossPct));
    setTakeProfitPctRaw(pctToRaw(defaultTakeProfitPct));
    const nowSec = Math.floor(Date.now() / 1000);
    setStartInput(toLocalInput(defaultStartTs ?? nowSec - 30 * DAY));
    setEndInput(toLocalInput(defaultEndTs ?? nowSec));
    setEveryRaw(defaultDecisionEveryBars ? String(defaultDecisionEveryBars) : "");
    setMinConsensusRaw("");
    // 默认引用把正在编辑的表单每秒冲回默认（inputs-revert 复发根因）。
  };

  return {
    mode, setMode, stepId, setStepId, visitedSteps, setVisitedSteps, members, setMembers, allocationMode, setAllocationMode,
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
    seededMembers, seed,
  };
}
