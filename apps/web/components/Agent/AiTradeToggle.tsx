"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Switch } from "@/components/ui";
import { accountApi } from "@/lib/accountApi";
import { agentApi } from "@/lib/agentApi";
import { aiTradeApi } from "@/lib/aiTradeApi";
import {
  AI_TRADE_DEFAULT_CONTEXT_ROUNDS,
  resolveAiTradeGate,
} from "@/lib/aiTrade";
import { ApiRequestError } from "@/lib/api";
import { useAppStore } from "@/lib/store";
import AiTradeLaunchModal, { type AiTradeLaunchOpts } from "./AiTradeLaunchModal";

/**
 * AI 自动交易开关（ai-auto-trading §5.3）：挂 AgentPanel 头部「新对话」右侧。守门文案矩阵通过则开启
 * 弹窗多选策略并启动；运行中开关用于停止（确认平掉自管持仓）。与策略自动交易分组互斥（守门 + 后端准入锁）。
 */
export default function AiTradeToggle() {
  const accountMode = useAppStore((s) => s.accountMode);
  const instId = useAppStore((s) => s.instId);
  const bar = useAppStore((s) => s.bar);
  const strategyId = useAppStore((s) => s.strategyId);
  const aiTradeSessionId = useAppStore((s) => s.aiTradeSessionId);
  const liveAutoGroup = useAppStore((s) => s.liveAutoGroup);
  const aiTradeAcknowledged = useAppStore((s) => s.aiTradeAcknowledged);
  const aiTradeConfig = useAppStore((s) => s.aiTradeConfig);
  const setAiTradeSessionId = useAppStore((s) => s.setAiTradeSessionId);
  const setAiTradeSession = useAppStore((s) => s.setAiTradeSession);
  const setAiTradeAcknowledged = useAppStore((s) => s.setAiTradeAcknowledged);
  const setAiTradeConfig = useAppStore((s) => s.setAiTradeConfig);
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

  const running = !!aiTradeSessionId;
  const gateReason = resolveAiTradeGate({
    accountMode,
    hasReadyDefault,
    aiTradeEnabled,
    agentConfigured,
    liveAutoActive: !!liveAutoGroup,
    hasActiveSession: running,
  });

  const startM = useMutation({
    mutationFn: (opts: AiTradeLaunchOpts) =>
      aiTradeApi.start({
        instId: opts.instId,
        bar,
        strategyIds: opts.strategyIds,
        capitalUsd: opts.capitalUsd,
        leverage: opts.leverage,
        takeProfitUsd: opts.takeProfitUsd,
        stopLossUsd: opts.stopLossUsd,
        contextRefreshRounds: opts.contextRefreshRounds,
      }),
    onSuccess: (res, opts) => {
      const now = Math.floor(Date.now() / 1000);
      setAiTradeSessionId(res.sessionId);
      setAiTradeSession({
        sessionId: res.sessionId,
        runId: res.runId,
        state: "running",
        instId: res.instId,
        bar: res.bar,
        strategyIds: res.strategyIds,
        strategyNames: res.strategyNames,
        budgetUsd: res.budgetUsd,
        leverage: res.leverage,
        takeProfitUsd: opts.takeProfitUsd,
        stopLossUsd: opts.stopLossUsd,
        contextRefreshRounds: res.contextRefreshRounds,
        roundsCompleted: 0,
        roundsSinceSummary: 0,
        positionSide: "flat",
        sessionPnlUsd: 0,
        startedTs: now,
        lastDecision: null,
        contextSummary: null,
        degraded: false,
      });
      // 记忆偏好（策略集合 + 刷新轮数），下次启动回填。
      setAiTradeConfig({ strategyIds: res.strategyIds, contextRefreshRounds: res.contextRefreshRounds });
      setModalOpen(false);
      setError(null);
      qc.invalidateQueries({ queryKey: ["aiTrade", "status"] });
    },
    onError: (e) =>
      setError(e instanceof ApiRequestError ? e.message : "启动失败，请稍后重试"),
  });

  const stopM = useMutation({
    mutationFn: (id: string) => aiTradeApi.stop(id),
    onSettled: () => {
      clearAiTradeRuntime();
      qc.invalidateQueries({ queryKey: ["aiTrade", "status"] });
    },
  });

  const onToggle = (next: boolean) => {
    setError(null);
    if (next) {
      if (gateReason) return;
      setModalOpen(true);
    } else if (aiTradeSessionId) {
      stopM.mutate(aiTradeSessionId);
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
      title={gateReason ?? (running ? "点击停止 AI 自动交易并平掉自管持仓" : "开启 AI 自动交易")}
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
        className={gateReason && !running ? "opacity-40" : undefined}
      />
      <AiTradeLaunchModal
        open={modalOpen}
        onClose={() => {
          setModalOpen(false);
          setError(null);
        }}
        onConfirm={(opts) => startM.mutate(opts)}
        submitting={startM.isPending}
        acknowledged={aiTradeAcknowledged}
        onAcknowledge={setAiTradeAcknowledged}
        bar={bar}
        defaultInstId={instId}
        defaultStrategyIds={defaultStrategyIds}
        defaultContextRounds={aiTradeConfig?.contextRefreshRounds ?? AI_TRADE_DEFAULT_CONTEXT_ROUNDS}
        error={error}
      />
    </div>
  );
}
