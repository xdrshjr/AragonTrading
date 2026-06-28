"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import clsx from "clsx";
import { Switch } from "@/components/ui";
import { accountApi } from "@/lib/accountApi";
import { api, ApiRequestError } from "@/lib/api";
import { resolveAutoTradeGate } from "@/lib/liveAuto";
import { liveAutoApi } from "@/lib/liveAutoApi";
import { useAppStore } from "@/lib/store";
import AutoTradeConfirmModal, { type AutoTradeConfirmOpts } from "./AutoTradeConfirmModal";

/**
 * 「自动交易」开关（live-auto-trading §3）：策略面板标题栏右上。
 *
 * 独立组件直接订阅全局 store（accountMode/strategyId/instId/bar/liveAutoSessionId）+ react-query
 * （账户列表判 hasReadyDefault、/live-auto/status 判 liveAutoEnabled）。开→弹确认弹窗，关→停会话。
 * 仅 mode==="paper" 时由 StrategyPanel 渲染；本组件再以 accountMode==="live" 等条件守门禁用态。
 */
export default function AutoTradeToggle() {
  const qc = useQueryClient();
  const accountMode = useAppStore((s) => s.accountMode);
  const strategyId = useAppStore((s) => s.strategyId);
  const instId = useAppStore((s) => s.instId);
  const bar = useAppStore((s) => s.bar);
  const liveAutoSessionId = useAppStore((s) => s.liveAutoSessionId);
  const setLiveAutoSessionId = useAppStore((s) => s.setLiveAutoSessionId);
  const setLiveAutoStatus = useAppStore((s) => s.setLiveAutoStatus);
  const clearLiveAutoRuntime = useAppStore((s) => s.clearLiveAutoRuntime);
  const acknowledged = useAppStore((s) => s.liveAutoAcknowledged);
  const setAcknowledged = useAppStore((s) => s.setLiveAutoAcknowledged);

  const [modalOpen, setModalOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const accountsQ = useQuery({ queryKey: ["okx", "accounts"], queryFn: accountApi.accounts.list });
  const defaultAccount =
    accountsQ.data?.accounts.find((a) => a.id === accountsQ.data?.defaultId) ?? null;
  const hasReadyDefault =
    !!defaultAccount && defaultAccount.hasSecret && defaultAccount.hasPassphrase;

  const statusQ = useQuery({
    queryKey: ["liveAuto", "status"],
    queryFn: liveAutoApi.status,
    enabled: accountMode === "live",
    refetchInterval: 15_000,
  });
  const liveAutoEnabled = statusQ.data?.liveAutoTradingEnabled ?? false;

  const strategiesQ = useQuery({ queryKey: ["strategies"], queryFn: api.strategies });
  const strategyName =
    strategiesQ.data?.find((s) => s.id === strategyId)?.name ?? "";

  const active = !!liveAutoSessionId;
  const disabledReason = resolveAutoTradeGate({
    accountMode,
    hasReadyDefault,
    liveAutoEnabled,
    strategyId,
  });

  const startM = useMutation({
    mutationFn: (opts: AutoTradeConfirmOpts) =>
      liveAutoApi.start({
        strategyId: strategyId as number,
        instId,
        bar,
        capitalUsd: opts.capitalUsd,
        leverage: opts.leverage,
        takeProfitUsd: opts.takeProfitUsd,
        stopLossUsd: opts.stopLossUsd,
      }),
    onSuccess: (res) => {
      setLiveAutoSessionId(res.sessionId);
      setLiveAutoStatus({
        state: "running",
        sessionPnlUsd: 0,
        budgetUsd: res.budgetUsd,
        leverage: res.leverage,
      });
      setModalOpen(false);
      qc.invalidateQueries({ queryKey: ["liveAuto", "status"] });
    },
    onError: (e) => setError(e instanceof ApiRequestError ? e.message : "启动失败"),
  });

  const stopM = useMutation({
    mutationFn: (sessionId: string) => liveAutoApi.stop(sessionId),
    onSettled: () => {
      clearLiveAutoRuntime();
      qc.invalidateQueries({ queryKey: ["liveAuto", "status"] });
    },
  });

  const onToggle = (next: boolean) => {
    setError(null);
    if (next) {
      if (disabledReason) return;
      setModalOpen(true);
    } else if (liveAutoSessionId) {
      stopM.mutate(liveAutoSessionId);
    }
  };

  const blocked = !active && !!disabledReason;
  return (
    <div className="flex items-center gap-2">
      <span
        className={clsx(
          "text-[11px] font-medium",
          active ? "text-[var(--short)]" : "text-[var(--muted)]",
        )}
      >
        自动交易
      </span>
      <div
        title={disabledReason ?? (active ? "点击停止自动交易并平仓" : "启动策略驱动的实时自动交易")}
        className={clsx(blocked && "cursor-not-allowed opacity-40")}
      >
        <Switch
          checked={active}
          onChange={(next) => (blocked ? undefined : onToggle(next))}
          label="自动交易"
        />
      </div>

      <AutoTradeConfirmModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        onConfirm={(opts) => startM.mutate(opts)}
        submitting={startM.isPending}
        acknowledged={acknowledged}
        onAcknowledge={setAcknowledged}
        instId={instId}
        bar={bar}
        strategyName={strategyName}
        error={error}
      />
    </div>
  );
}
