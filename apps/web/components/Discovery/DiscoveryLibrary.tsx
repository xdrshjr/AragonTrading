"use client";

import { useCallback, useEffect, useState } from "react";
import { Badge, Button, Card } from "@/components/ui";
import { discoveryApi } from "@/lib/discoveryApi";
import type { DiscoveryCandidateRow } from "@/lib/discoveryTypes";
import { useAppStore } from "@/lib/store";
import DiscoveryCandidateDetail from "./DiscoveryCandidateDetail";
import { useDiscovery } from "./DiscoveryLab";

function fmt(value: number | null | undefined, digits = 4): string {
  return value === null || value === undefined || !Number.isFinite(value)
    ? "—"
    : value.toFixed(digits);
}

/** 成果库：本任务已入库的因子 + 「已退化」徽标 + 溯源（run # / iter #）+ 跳转因子实验室。 */
export default function DiscoveryLibrary() {
  const { activeRunId, runs, goSection } = useDiscovery();
  const setActiveView = useAppStore((s) => s.setActiveView);
  const setFactorLabSection = useAppStore((s) => s.setFactorLabSection);
  const [rows, setRows] = useState<DiscoveryCandidateRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [detailId, setDetailId] = useState<number | null>(null);

  const load = useCallback(async () => {
    if (!activeRunId) return;
    setLoading(true);
    try {
      const res = await discoveryApi.listCandidates(activeRunId, { verdict: "kept", limit: 200 });
      setRows(res.items);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载成果库失败");
    } finally {
      setLoading(false);
    }
  }, [activeRunId]);

  useEffect(() => {
    void load();
  }, [load]);

  const summary = runs.find((row) => row.runId === activeRunId) ?? null;
  const promoted = rows.filter((row) => row.promotedId);
  const pending = rows.filter((row) => !row.promotedId && !row.demoted);
  const demoted = rows.filter((row) => row.demoted);

  if (!activeRunId) {
    return (
      <Card className="p-6 text-center">
        <p className="text-sm text-[var(--text)]">还没有选中挖掘任务</p>
        <button
          type="button"
          onClick={() => goSection("runs")}
          className="mt-2 text-xs text-[var(--accent)] underline"
        >
          去挖掘任务列表选择
        </button>
      </Card>
    );
  }

  const renderGroup = (title: string, list: DiscoveryCandidateRow[], hint: string) => (
    <Card className="p-3">
      <div className="mb-2 flex items-center gap-2">
        <h4 className="text-xs font-medium text-[var(--text)]">{title}</h4>
        <Badge tone="neutral">{list.length}</Badge>
      </div>
      {list.length === 0 ? (
        <p className="text-[11px] leading-relaxed text-[var(--muted)]">{hint}</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {list.map((row) => (
            <li key={row.id ?? row.genomeHash} className="border-b border-[var(--border)] pb-2 last:border-0 last:pb-0">
              <button
                type="button"
                onClick={() => row.id !== undefined && setDetailId(row.id)}
                className="block w-full truncate text-left font-mono text-[11px] text-[var(--text)] underline-offset-2 hover:underline"
                title={row.exprText}
              >
                {row.exprText}
              </button>
              <p className="truncate text-[11px] text-[var(--muted)]">{row.describe}</p>
              <p className="mt-0.5 flex flex-wrap gap-x-3 text-[10px] tabular text-[var(--muted)]">
                <span>IS RankIC {fmt(row.metrics.is?.rankIc)}</span>
                <span>OOS RankIC {fmt(row.metrics.oos?.rankIc)}</span>
                <span>试验数 {row.trialsAtAccept ?? "—"}</span>
                <span>
                  来源 #{summary?.runId ?? activeRunId} / 第 {row.iterationIdx} 轮
                </span>
              </p>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-2">
        <h3 className="text-sm font-semibold text-[var(--text)]">成果库</h3>
        <Button variant="ghost" size="sm" onClick={() => void load()} disabled={loading}>
          刷新
        </Button>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => {
            setFactorLabSection("my-factors");
            setActiveView("factor");
          }}
        >
          去因子实验室查看
        </Button>
      </div>

      {error && (
        <p role="alert" className="text-xs text-[var(--short)]">
          {error}
        </p>
      )}

      {renderGroup("已入库到「我的因子」", promoted,
                   "还没有入库的因子。在候选库里选中通过全部过滤条件的候选并点「入库」即可。")}
      {renderGroup("待入库（已通过全部过滤条件）", pending,
                   "暂无待入库候选。")}
      {renderGroup("已退化", demoted,
                   "暂无退化记录。数据窗口右移后系统会对已入库因子做轻量复评，连续不达标者会被标记为已退化并退出父本池。")}

      <DiscoveryCandidateDetail
        candidateId={detailId}
        onClose={() => setDetailId(null)}
        onPromoted={() => void load()}
      />
    </div>
  );
}
