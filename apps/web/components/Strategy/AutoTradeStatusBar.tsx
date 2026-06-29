"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import clsx from "clsx";
import { Button } from "@/components/ui";
import { fmtPrice } from "@/lib/format";
import { describeStopReason } from "@/lib/liveAuto";
import { liveAutoApi } from "@/lib/liveAutoApi";
import { useAppStore } from "@/lib/store";

/**
 * 自动交易状态条（live-auto-trading §3 + hardening H7）：运行中显示策略 / 标的 / 时长 / 持仓 /
 * 会话盈亏 +「停止」；连接降级时加「连接异常」徽标；会话停止（含后端自动止盈止损 / 服务重启）后
 * 切为终止横幅（中文停止原因 + 会话盈亏 + 关闭），平仓失败时升红强提示人工平仓，停止后停计时。
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

  const stopped = (liveAutoStatus?.state ?? session?.state ?? "running") === "stopped";

  const [now, setNow] = useState(() => Math.floor(Date.now() / 1000));
  useEffect(() => {
    if (stopped) return; // H7：会话停止后停计时（终止横幅不再跳动）。
    const timer = setInterval(() => setNow(Math.floor(Date.now() / 1000)), 1000);
    return () => clearInterval(timer);
  }, [stopped]);

  const stopM = useMutation({
    mutationFn: (sessionId: string) => liveAutoApi.stop(sessionId),
    onSettled: () => {
      clearLiveAutoRuntime();
      qc.invalidateQueries({ queryKey: ["liveAuto", "status"] });
    },
  });

  if (!liveAutoSessionId) return null;

  const pnl = liveAutoStatus?.sessionPnlUsd ?? session?.sessionPnlUsd ?? 0;

  // H7：会话已停止（用户未点本条停止键，多为后端自动止盈止损 / 服务重启）→ 终止横幅，明确告知原因。
  if (stopped) {
    return (
      <TerminalBanner
        reason={liveAutoStatus?.stopReason}
        pnl={pnl}
        flattenFailed={!!liveAutoStatus?.flattenFailed}
        onClose={clearLiveAutoRuntime}
      />
    );
  }

  const positionSide = liveAutoPosition?.side ?? liveAutoStatus?.positionSide ?? "flat";
  const elapsed = session?.startedTs ? Math.max(0, now - session.startedTs) : null;
  const degraded = !!liveAutoStatus?.degraded;

  return (
    <div className="flex items-center justify-between gap-2 border-b border-[var(--border)] bg-[var(--short)]/8 px-4 py-2 text-[11px]">
      <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-0.5">
        <span className="inline-flex items-center gap-1 font-semibold text-[var(--short)]">
          <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-[var(--short)]" />
          自动交易运行中
        </span>
        {degraded && (
          <span className="rounded bg-[var(--accent)]/15 px-1.5 py-0.5 font-medium text-[var(--accent)]">
            连接异常
          </span>
        )}
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

function TerminalBanner({
  reason,
  pnl,
  flattenFailed,
  onClose,
}: {
  reason?: string | null;
  pnl: number;
  flattenFailed: boolean;
  onClose: () => void;
}) {
  return (
    <div
      className={clsx(
        "flex flex-col gap-1 border-b px-4 py-2 text-[11px]",
        flattenFailed
          ? "border-[var(--short)] bg-[var(--short)]/12"
          : "border-[var(--border)] bg-[var(--surface-2)]",
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-0.5">
          <span className="inline-flex items-center gap-1 font-semibold text-[var(--text)]">
            <span className="h-1.5 w-1.5 rounded-full bg-[var(--muted)]" />
            自动交易已停止
          </span>
          <span className="text-[var(--muted)]">{describeStopReason(reason)}</span>
          <span
            className={clsx(
              "tabular font-medium",
              pnl > 0 ? "text-[var(--long)]" : pnl < 0 ? "text-[var(--short)]" : "text-[var(--text)]",
            )}
          >
            会话盈亏 {pnl >= 0 ? "+" : ""}
            {fmtPrice(pnl)} USDT
          </span>
        </div>
        <Button variant="ghost" size="sm" onClick={onClose}>
          关闭
        </Button>
      </div>
      {flattenFailed && (
        <span role="alert" className="font-medium text-[var(--short)]">
          ⚠ 自动平仓失败，请到下方实盘面板手动平仓
        </span>
      )}
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
