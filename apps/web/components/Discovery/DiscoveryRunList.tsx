"use client";

import clsx from "clsx";
import { useState } from "react";
import { Badge, Button, Card } from "@/components/ui";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import { STATUS_CN, formatDuration, keptRate, pauseReasonCn, runtimeSeconds } from "@/lib/discovery";
import { discoveryApi } from "@/lib/discoveryApi";
import type { DiscoveryRunSummary, DiscoveryStatus } from "@/lib/discoveryTypes";
import { useDiscovery } from "./DiscoveryLab";
import DiscoveryLauncher from "./DiscoveryLauncher";

function statusTone(status: DiscoveryStatus): "neutral" | "long" | "short" | "accent" {
  if (status === "running") return "accent";
  if (status === "succeeded") return "long";
  if (status === "failed") return "short";
  return "neutral";
}

/** 挖掘任务列表：状态徽标 / 配置摘要 / 计数 + 暂停·恢复·停止·删除。 */
export default function DiscoveryRunList() {
  const { runs, defaults, loading, activeRunId, selectRun, refresh, goSection } = useDiscovery();
  const [launcherOpen, setLauncherOpen] = useState(false);
  const [busy, setBusy] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [confirmStop, setConfirmStop] = useState<DiscoveryRunSummary | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<DiscoveryRunSummary | null>(null);

  const act = async (runId: number, fn: () => Promise<unknown>) => {
    setBusy(runId);
    setError("");
    try {
      await fn();
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "操作失败");
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-[var(--text)]">挖掘任务</h3>
        <div className="flex gap-2">
          <Button variant="ghost" size="sm" onClick={() => void refresh()} disabled={loading}>
            刷新
          </Button>
          <Button
            variant="primary"
            size="sm"
            onClick={() => setLauncherOpen(true)}
            disabled={!defaults?.enabled}
            title={defaults?.enabled ? undefined : "挖掘总闸已关闭"}
          >
            新建挖掘
          </Button>
        </div>
      </div>

      {error && (
        <p role="alert" className="text-xs text-[var(--short-ink)]">
          {error}
        </p>
      )}

      {runs.length === 0 ? (
        <Card className="p-6 text-center">
          <p className="text-sm text-[var(--text)]">还没有挖掘任务</p>
          <p className="mt-1 text-xs leading-relaxed text-[var(--muted)]">
            新建一个任务后，系统会按「生成 → 审查 → 验证 → 入库」四步持续迭代；
            第一轮通常在一个迭代间隔内产出结果。
          </p>
          <Button
            variant="primary"
            size="sm"
            className="mt-3"
            onClick={() => setLauncherOpen(true)}
            disabled={!defaults?.enabled}
          >
            新建挖掘任务
          </Button>
        </Card>
      ) : (
        <ul className="flex flex-col gap-2">
          {runs.map((row) => {
            const rate = keptRate(row.testedCount, row.keptCount);
            const isActive = row.runId === activeRunId;
            const live = row.status === "running";
            return (
              <li key={row.runId}>
                <Card
                  className={clsx(
                    "flex flex-col gap-2 p-3 transition-colors",
                    isActive && "border-[var(--accent)]",
                  )}
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <button
                      type="button"
                      onClick={() => {
                        selectRun(row.runId);
                        goSection("monitor");
                      }}
                      className="text-sm font-medium text-[var(--text)] underline-offset-2 hover:underline"
                    >
                      {row.label || `#${row.runId}`}
                    </button>
                    <Badge tone={statusTone(row.status)}>
                      {live && (
                        <span
                          aria-hidden="true"
                          className="mr-1 inline-block h-1.5 w-1.5 rounded-full bg-current motion-safe:animate-pulse"
                        />
                      )}
                      {STATUS_CN[row.status] ?? row.status}
                    </Badge>
                    {row.pauseReason && <Badge tone="neutral">{pauseReasonCn(row.pauseReason)}</Badge>}
                    <span className="text-[11px] tabular text-[var(--muted)]">
                      {row.instId} · {row.bar}
                    </span>
                  </div>

                  <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] tabular text-[var(--muted)]">
                    <span>迭代 {row.iteration}</span>
                    <span>已测 {row.testedCount}</span>
                    <span>入库 {row.keptCount}</span>
                    <span>入库率 {rate === null ? "—" : `${(rate * 100).toFixed(2)}%`}</span>
                    <span>运行 {formatDuration(runtimeSeconds(row.startedTs))}</span>
                  </div>

                  {row.errorMessage && (
                    <p className="text-[11px] text-[var(--short-ink)]">
                      {row.errorCode}：{row.errorMessage}
                    </p>
                  )}

                  <div className="flex flex-wrap gap-1.5">
                    {row.status === "running" && (
                      <Button
                        size="sm"
                        variant="ghost"
                        disabled={busy === row.runId}
                        onClick={() => void act(row.runId, () => discoveryApi.pause(row.runId))}
                      >
                        暂停
                      </Button>
                    )}
                    {row.status === "paused" && (
                      <Button
                        size="sm"
                        variant="ghost"
                        disabled={busy === row.runId}
                        onClick={() => void act(row.runId, () => discoveryApi.resume(row.runId))}
                      >
                        恢复
                      </Button>
                    )}
                    {(row.status === "running" || row.status === "paused") && (
                      <Button size="sm" variant="ghost" onClick={() => setConfirmStop(row)}>
                        停止
                      </Button>
                    )}
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => {
                        selectRun(row.runId);
                        goSection("candidates");
                      }}
                    >
                      查看候选
                    </Button>
                    <Button size="sm" variant="danger" onClick={() => setConfirmDelete(row)}>
                      删除
                    </Button>
                  </div>
                </Card>
              </li>
            );
          })}
        </ul>
      )}

      <DiscoveryLauncher
        open={launcherOpen}
        defaults={defaults}
        onClose={() => setLauncherOpen(false)}
        onStarted={(runId) => {
          selectRun(runId);
          void refresh();
          goSection("monitor");
        }}
      />

      <ConfirmDialog
        open={confirmStop !== null}
        title="停止挖掘任务"
        message="停止后该任务不可恢复（已入库的候选会保留）。确定要停止吗？"
        confirmText="停止"
        onCancel={() => setConfirmStop(null)}
        onConfirm={() => {
          const row = confirmStop;
          setConfirmStop(null);
          if (row) void act(row.runId, () => discoveryApi.stop(row.runId));
        }}
      />

      <ConfirmDialog
        open={confirmDelete !== null}
        title="删除挖掘任务"
        message="将同时删除该任务的全部迭代记录与候选（已入库到「我的因子」的不受影响）。确定删除吗？"
        confirmText="删除"
        onCancel={() => setConfirmDelete(null)}
        onConfirm={() => {
          const row = confirmDelete;
          setConfirmDelete(null);
          if (row) void act(row.runId, () => discoveryApi.remove(row.runId));
        }}
      />
    </div>
  );
}
