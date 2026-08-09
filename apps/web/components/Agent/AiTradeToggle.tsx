"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Switch } from "@/components/ui";
import { accountApi } from "@/lib/accountApi";
import { agentApi } from "@/lib/agentApi";
import { aiBacktestApi } from "@/lib/aiBacktestApi";
import { aiTradeApi } from "@/lib/aiTradeApi";
import {
  AI_TRADE_BASE_RULE_DEFAULTS,
  AI_TRADE_DEFAULT_CONTEXT_ROUNDS,
  AI_TRADE_DEFAULT_POSITION_STEP,
  AI_TRADE_DEFAULT_WAKE_INTERVAL_MIN,
  AI_TRADE_PROFIT_GUARD_DEFAULTS,
  resolveAiTradeGate,
} from "@/lib/aiTrade";
import { ApiRequestError } from "@/lib/api";
import { groupStateFromStatus } from "@/lib/useAiTradeGroupStream";
import { useAppStore } from "@/lib/store";
import AiTradeLaunchModal, { type AiTradeLaunchOpts } from "./AiTradeLaunchModal";

/**
 * AI 自动交易开关（ai-trade-multi-symbol-and-timeframe §4.2）：挂 AgentPanel 头部「新对话」右侧。
 * 只要 Agent 已配置模型即可点开弹窗（回测门禁独立于 resolveAiTradeGate）；弹窗内「实盘 / 回测」段控——
 * 实盘段走完整 resolveAiTradeGate 门禁并**自动填入左侧「自动交易」面板的 autoTradeMembers 多标的**（分组
 * 启动），回测段仅需 Agent 已配置。运行中开关用于停止整组；回测确认→启动 job（开关不 latch）。
 */
export default function AiTradeToggle() {
  const accountMode = useAppStore((s) => s.accountMode);
  const instId = useAppStore((s) => s.instId);
  const bar = useAppStore((s) => s.bar);
  const strategyId = useAppStore((s) => s.strategyId);
  const backtestStartTs = useAppStore((s) => s.backtestStartTs);
  const backtestEndTs = useAppStore((s) => s.backtestEndTs);
  const aiTradeGroup = useAppStore((s) => s.aiTradeGroup);
  const liveAutoGroup = useAppStore((s) => s.liveAutoGroup);
  const autoTradeMembers = useAppStore((s) => s.autoTradeMembers);
  const autoTradeAllocationMode = useAppStore((s) => s.autoTradeAllocationMode);
  const aiTradeAcknowledged = useAppStore((s) => s.aiTradeAcknowledged);
  const aiTradeConfig = useAppStore((s) => s.aiTradeConfig);
  const aiBacktestConfig = useAppStore((s) => s.aiBacktestConfig);
  const setAiTradeGroup = useAppStore((s) => s.setAiTradeGroup);
  const setAiTradeAcknowledged = useAppStore((s) => s.setAiTradeAcknowledged);
  const setAiTradeConfig = useAppStore((s) => s.setAiTradeConfig);
  const setAiBacktestJobId = useAppStore((s) => s.setAiBacktestJobId);
  const setAiBacktestConfig = useAppStore((s) => s.setAiBacktestConfig);
  const clearAiTradeRuntime = useAppStore((s) => s.clearAiTradeRuntime);

  const qc = useQueryClient();
  const [modalOpen, setModalOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const accountsQ = useQuery({ queryKey: ["okx", "accounts"], queryFn: accountApi.accounts.list });
  const defaultAccount =
    accountsQ.data?.accounts.find((a) => a.id === accountsQ.data?.defaultId) ?? null;
  const hasReadyDefault =
    !!defaultAccount && defaultAccount.hasSecret && defaultAccount.hasPassphrase;

  const agentConfigQ = useQuery({ queryKey: ["agent", "config"], queryFn: agentApi.config });
  const agentConfigured = agentConfigQ.data?.configured ?? false;

  const statusQ = useQuery({
    queryKey: ["aiTrade", "status"],
    queryFn: aiTradeApi.status,
    enabled: accountMode === "live",
    refetchInterval: 15_000,
  });
  const aiTradeEnabled = statusQ.data?.aiTradeEnabled ?? false;

  const running = !!aiTradeGroup;
  const groupId = aiTradeGroup?.groupId ?? null;
  // 实盘段门禁（传入弹窗，实盘 tab 据此禁用 + 悬浮说明）；回测段仅需 agentConfigured。
  const liveGateReason = resolveAiTradeGate({
    accountMode,
    hasReadyDefault,
    aiTradeEnabled,
    agentConfigured,
    liveAutoActive: !!liveAutoGroup,
    hasActiveSession: running,
  });
  // 打开弹窗的最小门禁（独立于 resolveAiTradeGate）：Agent 已配置即可（回测在模拟账户模式也可用，R6）。
  const openBlockedReason = running ? null : !agentConfigured ? "请先在设置中心配置 AI 模型" : null;

  const startM = useMutation({
    mutationFn: (opts: AiTradeLaunchOpts) =>
      aiTradeApi.start({
        members: opts.members,
        bar: opts.bar,                       // 可编辑周期真正生效（不再直读 store.bar）
        strategyIds: opts.strategyIds,
        allocationMode: opts.allocationMode,
        capitalUsd: opts.capitalUsd,
        leverage: opts.leverage,
        takeProfitUsd: opts.takeProfitUsd,
        stopLossUsd: opts.stopLossUsd,
        stopLossPct: opts.stopLossPct,       // E4：单仓 OCO 止损%（透传，服务端夹紧）
        takeProfitPct: opts.takeProfitPct,   // E4：单仓 OCO 止盈%
        positionStepPct: opts.positionStepPct,   // 仓位操作档位（透传，服务端默认回填/量化）
        wakeIntervalMinutes: opts.wakeIntervalMinutes,   // Agent 墙钟定时唤醒周期（透传，服务端默认回填/夹紧）
        baseRules: opts.baseRules,               // 【基础策略】铁律（透传；取消勾选即 enabled:false）
        profitGuard: opts.profitGuard,           // 利润保护层（透传；取消勾选即 enabled:false）
        tradingHorizon: opts.tradingHorizon,     // 交易周期模式（同上；缺省=服务端默认开启）
        contextRefreshRounds: opts.contextRefreshRounds,
      }),
    onSuccess: (res, opts) => {
      setAiTradeGroup(groupStateFromStatus(res));
      // 记忆偏好（策略集合 + 刷新轮数 + 仓位档位 + 唤醒周期），下次启动回填。[R-4] positionStepPct/
      // wakeIntervalMinutes 须取自 opts——setAiTradeConfig 是整对象替换且 res 不回显此二键，取 res 会让
      // 所选偏好每次重开都回落默认、永不持久。
      setAiTradeConfig({
        strategyIds: res.strategyIds,
        contextRefreshRounds: res.contextRefreshRounds,
        positionStepPct: opts.positionStepPct,
        wakeIntervalMinutes: opts.wakeIntervalMinutes ?? undefined,
        // [R-4] 同理：res 不回显完整规则参数，取 opts 才能让所选铁律参数真正持久（回执只镜像装配态）。
        baseRules: opts.baseRules.maBandEntry ?? undefined,
        // 同上取 opts：诊断根因 ③ 的根治点——单仓 OCO 与利润保护偏好从此被记住并回填，
        // 不再每次重开都清空、让用户在不知情下一直吃 10%/5% 的宽默认。
        profitGuard: opts.profitGuard,
        // [R-4] 同理取 opts：回执镜像的是**服务端解析后的生效值**（含两个派生量），拿它回填会把
        // 用户「留空=按预设」的意图固化成一组具体数字，下次改周期时不再联动。
        tradingHorizon: opts.tradingHorizon,
        stopLossPct: opts.stopLossPct ?? undefined,
        takeProfitPct: opts.takeProfitPct ?? undefined,
      });
      setModalOpen(false);
      setError(null);
      qc.invalidateQueries({ queryKey: ["aiTrade", "status"] });
    },
    onError: (e) => setError(e instanceof ApiRequestError ? describeStartError(e) : "启动失败，请稍后重试"),
  });

  const startBacktestM = useMutation({
    mutationFn: (opts: AiTradeLaunchOpts) =>
      aiBacktestApi.start({
        instId: opts.instId,
        bar: opts.bar,                       // 回测同享可编辑周期
        strategyIds: opts.strategyIds,
        startTs: opts.startTs ?? 0,
        endTs: opts.endTs ?? 0,
        decisionEveryBars: opts.decisionEveryBars ?? null,
        initialCapital: opts.capitalUsd,
        leverage: opts.leverage,
        stopLossPct: opts.stopLossPct,
        takeProfitPct: opts.takeProfitPct,
        takeProfitUsd: opts.takeProfitUsd,
        stopLossUsd: opts.stopLossUsd,
        contextRefreshRounds: opts.contextRefreshRounds,
        minConsensus: opts.minConsensus ?? null,
        positionStepPct: opts.positionStepPct,   // 回测同享档位约束（决策脑单点收口，实盘/回测同生效）
        baseRules: opts.baseRules,               // 回测同享铁律（P1-4：默认必须与实盘一致，否则调参失真）
        // 回测同享交易周期模式（同一 `_resolve_horizon` 解析 + 一层回测运维总闸）。同一条理由——
        // 两侧参数不同源，用户拿回测调出来的周期在实盘直接失真。
        tradingHorizon: opts.tradingHorizon,
        // F3：回测同享利润保护（bar 内极值 + 保守成交序）。同一条理由——两侧参数不同源，
        // 用户拿回测调出来的参数在实盘直接失真。
        profitGuard: opts.profitGuard,
      }),
    onSuccess: (res, opts) => {
      setAiBacktestJobId(res.jobId);
      setAiBacktestConfig({
        decisionEveryBars: res.decisionEveryBars,
        contextRefreshRounds: opts.contextRefreshRounds,
      });
      setModalOpen(false);
      setError(null);
    },
    onError: (e) =>
      setError(e instanceof ApiRequestError ? e.message : "回测启动失败，请稍后重试"),
  });

  const stopM = useMutation({
    mutationFn: (id: string) => aiTradeApi.stopGroup(id),
    onSettled: () => {
      clearAiTradeRuntime();
      qc.invalidateQueries({ queryKey: ["aiTrade", "status"] });
    },
  });

  const onToggle = (next: boolean) => {
    setError(null);
    if (next) {
      if (openBlockedReason) return;
      setModalOpen(true);
    } else if (groupId) {
      stopM.mutate(groupId);
    }
  };

  const defaultStrategyIds =
    aiTradeConfig?.strategyIds && aiTradeConfig.strategyIds.length > 0
      ? aiTradeConfig.strategyIds
      : strategyId != null
        ? [strategyId]
        : [];

  return (
    <div
      className="flex items-center gap-1.5"
      title={
        openBlockedReason ?? (running ? "点击停止 AI 自动交易并平掉自管持仓" : "开启 AI 自动交易 / 回测")
      }
    >
      <span
        className={
          running
            ? "text-[11px] font-medium text-[var(--accent)]"
            : "text-[11px] text-[var(--muted)]"
        }
      >
        AI 自动交易
      </span>
      <Switch
        checked={running}
        onChange={onToggle}
        label="AI 自动交易开关"
        className={openBlockedReason && !running ? "opacity-40" : undefined}
      />
      <AiTradeLaunchModal
        open={modalOpen}
        onClose={() => {
          setModalOpen(false);
          setError(null);
        }}
        onConfirm={(opts) => (opts.mode === "backtest" ? startBacktestM.mutate(opts) : startM.mutate(opts))}
        submitting={startM.isPending || startBacktestM.isPending}
        acknowledged={aiTradeAcknowledged}
        onAcknowledge={setAiTradeAcknowledged}
        bar={bar}
        defaultMembers={autoTradeMembers}
        defaultAllocationMode={autoTradeAllocationMode}
        defaultInstId={instId}
        defaultStrategyIds={defaultStrategyIds}
        defaultContextRounds={aiTradeConfig?.contextRefreshRounds ?? AI_TRADE_DEFAULT_CONTEXT_ROUNDS}
        defaultPositionStepPct={aiTradeConfig?.positionStepPct ?? AI_TRADE_DEFAULT_POSITION_STEP}
        defaultWakeIntervalMinutes={aiTradeConfig?.wakeIntervalMinutes ?? AI_TRADE_DEFAULT_WAKE_INTERVAL_MIN}
        defaultProfitGuard={aiTradeConfig?.profitGuard ?? AI_TRADE_PROFIT_GUARD_DEFAULTS}
        defaultStopLossPct={aiTradeConfig?.stopLossPct ?? undefined}
        defaultTakeProfitPct={aiTradeConfig?.takeProfitPct ?? undefined}
        defaultBaseRule={aiTradeConfig?.baseRules ?? AI_TRADE_BASE_RULE_DEFAULTS}
        // 无本地预设兜底：模式表由 `GET /ai-trade/horizon-profiles` 下发，未选过时留 undefined 即可
        // （弹窗采用接口返回的 defaultProfile）——前端复制一份默认模式必然与服务端漂移（R-3）。
        defaultTradingHorizon={aiTradeConfig?.tradingHorizon}
        error={error}
        liveGateReason={liveGateReason}
        defaultStartTs={backtestStartTs}
        defaultEndTs={backtestEndTs}
        defaultDecisionEveryBars={aiBacktestConfig?.decisionEveryBars}
      />
    </div>
  );
}

/** 整组前置失败（AI_TRADE_GROUP_PREFLIGHT）逐标的明细中文化；否则回落 message。 */
function describeStartError(err: ApiRequestError): string {
  if (err.code === "AI_TRADE_GROUP_PREFLIGHT" && err.members?.length) {
    return err.members.map((m) => `${m.instId}：${m.message}`).join("；");
  }
  return err.message;
}
