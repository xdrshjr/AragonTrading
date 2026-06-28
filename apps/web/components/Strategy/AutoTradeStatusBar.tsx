"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import clsx from "clsx";
import { Button } from "@/components/ui";
import { fmtPrice } from "@/lib/format";
import { liveAutoApi } from "@/lib/liveAutoApi";
import { useAppStore } from "@/lib/store";

/**
 * 自动交易运行中状态条（live-auto-trading §3）：策略 / 标的 / 已运行时长 / 自管持仓方向 /
 * 会话盈亏 + 「停止」。仅在有活跃自动会话时由 StrategyPanel 渲染；停止态隐藏。
 */
export default function AutoTradeStatusBar() {
  const qc = useQueryClient();
  const liveAutoSessionId = useAppStore((s) => s.liveAutoSessionId);
  const liveAutoStatus = useAppStore((s) => s.liveAutoStatus);
  const liveAutoPosition = useAppStore((s) => s.liveAutoPosition);
  const clearLiveAutoRuntime = useAppStore((s) => s.clearLiveAutoRuntime);

  const statusQ = useQuery({
    queryKey: ["liveAuto", "status"],
    queryFn: liveAutoApi.status,
    enabled: !!liveAutoSessionId,
    refetchInterval: 15_000,
  });
  const session = statusQ.data?.session ?? null;

  const [now, setNow] = useState(() => Math.floor(Date.now() / 1000));
  useEffect(() => {
    const timer = setInterval(() => setNow(Math.floor(Date.now() / 1000)), 1000);
    return () => clearInterval(timer);
  }, []);

  const stopM = useMutation({
    mutationFn: (sessionId: string) => liveAutoApi.stop(sessionId),
    onSettled: () => {
      clearLiveAutoRuntime();
      qc.invalidateQueries({ queryKey: ["liveAuto", "status"] });
    },
  });

  if (!liveAutoSessionId) return null;

  const positionSide = liveAutoPosition?.side ?? liveAutoStatus?.positionSide ?? "flat";
  const pnl = liveAutoStatus?.sessionPnlUsd ?? session?.sessionPnlUsd ?? 0;
  const elapsed = session?.startedTs ? Math.max(0, now - session.startedTs) : null;

  return (
    <div className="flex items-center justify-between gap-2 border-b border-[var(--border)] bg-[var(--short)]/8 px-4 py-2 text-[11px]">
      <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-0.5">
        <span className="inline-flex items-center gap-1 font-semibold text-[var(--short)]">
          <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-[var(--short)]" />
          自动交易运行中
        </span>
        <span className="text-[var(--muted)]">
          {session?.strategyName || "策略"} · {session?.instId || ""} {session?.bar || ""}
        </span>
        {elapsed != null && <span className="text-[var(--muted)]">已运行 {fmtDuration(elapsed)}</span>}
        <span className="text-[var(--muted)]">持仓 {fmtSide(positionSide)}</span>
        <span
          className={clsx(
            "tabular font-medium",
            pnl > 0 ? "text-[var(--long)]" : pnl < 0 ? "text-[var(--short)]" : "text-[var(--text)]",
          )}
        >
          盈亏 {pnl >= 0 ? "+" : ""}
          {fmtPrice(pnl)} USDT
        </span>
      </div>
      <Button
        variant="ghost"
        size="sm"
        disabled={stopM.isPending}
        onClick={() => liveAutoSessionId && stopM.mutate(liveAutoSessionId)}
      >
        {stopM.isPending ? "停止中…" : "停止"}
      </Button>
    </div>
  );
}

function fmtSide(side: string): string {
  if (side === "long") return "多";
  if (side === "short") return "空";
  return "空仓";
}

function fmtDuration(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  if (h > 0) return `${h}h${m}m`;
  if (m > 0) return `${m}m${s}s`;
  return `${s}s`;
}
