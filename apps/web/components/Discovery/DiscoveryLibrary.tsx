"use client";

import { useCallback, useEffect, useState } from "react";
import { Badge, Button, Card } from "@/components/ui";
import { discoveryApi } from "@/lib/discoveryApi";
import { AXIS_SHORT_CN, fmtNumber, isStrategyAxis } from "@/lib/discoveryStrategy";
import type { DiscoveryCandidateRow } from "@/lib/discoveryTypes";
import { useAppStore } from "@/lib/store";
import DiscoveryCandidateDetail from "./DiscoveryCandidateDetail";
import { useDiscovery } from "./DiscoveryLab";

function fmt(value: number | null | undefined, digits = 4): string {
  return value === null || value === undefined || !Number.isFinite(value)
    ? "—"
    : value.toFixed(digits);
}

/** 候选 → 它入库后会变成哪种实体（与后端 ``_PROMOTERS`` 的映射同源）。 */
function entityOf(kind: string): "factor" | "factor_set" | "strategy" {
  if (kind === "factor_combo") return "factor_set";
  if (kind === "factor") return "factor";
  return "strategy";
}

const ENTITY_TITLE: Record<string, string> = {
  factor: "因子",
  factor_set: "因子组合（含伴生得分策略）",
  strategy: "策略",
};

/**
 * 成果库：按**实体类型**分组（因子 / 因子组合 / 策略）+ 「本轮已淘汰」徽标 + 溯源 + 一键跳转。
 *
 * 「挖到 → 一眼看到 → 一键用起来」是这个分区的全部意义：只列出来而不给跳转，用户还得自己
 * 去别的页面找一遍。
 */
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

  /** 跳转到该实体所在的既有页面（复用 setActiveView + 分区跳转，不新造路由）。 */
  const jumpTo = (entity: "factor" | "factor_set" | "strategy") => {
    if (entity === "strategy") {
      setActiveView("trade");
      return;
    }
    // 因子组合落在因子实验室的「组合构建」分区（与 StrategyDetail 的跳转口径一致）。
    setFactorLabSection(entity === "factor_set" ? "builder" : "my-factors");
    setActiveView("factor");
  };

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
              <div className="flex items-start gap-2">
                <button
                  type="button"
                  onClick={() => row.id !== undefined && setDetailId(row.id)}
                  className="block flex-1 truncate text-left font-mono text-[11px] text-[var(--text)] underline-offset-2 hover:underline"
                  title={row.exprText || row.describe}
                >
                  {row.exprText || row.describe}
                </button>
                <span className="shrink-0 rounded-full bg-[var(--surface-2)] px-1.5 py-0.5 text-[10px] text-[var(--muted)]">
                  {AXIS_SHORT_CN[row.kind] ?? row.kind}
                </span>
                {row.promotedId && (
                  <button
                    type="button"
                    onClick={() => jumpTo(entityOf(row.kind))}
                    className="shrink-0 text-[10px] text-[var(--accent)] underline"
                  >
                    {entityOf(row.kind) === "strategy" ? "去策略面板" : "去因子实验室"}
                  </button>
                )}
              </div>
              <p className="truncate text-[11px] text-[var(--muted)]">{row.describe}</p>
              <p className="mt-0.5 flex flex-wrap gap-x-3 text-[10px] tabular text-[var(--muted)]">
                {isStrategyAxis(row.kind) ? (
                  <>
                    <span>IS 夏普 {fmtNumber(row.metrics.is?.sharpe)}</span>
                    <span>OOS 夏普 {fmtNumber(row.metrics.oos?.sharpe)}</span>
                  </>
                ) : (
                  <>
                    <span>IS RankIC {fmt(row.metrics.is?.rankIc)}</span>
                    <span>OOS RankIC {fmt(row.metrics.oos?.rankIc)}</span>
                  </>
                )}
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

  const byEntity = (entity: string, list: DiscoveryCandidateRow[]) =>
    list.filter((row) => entityOf(row.kind) === entity);

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
        <Button variant="ghost" size="sm" onClick={() => goSection("assets")}>
          跨任务查看 →
        </Button>
      </div>

      {error && (
        <p role="alert" className="text-xs text-[var(--short)]">
          {error}
        </p>
      )}

      {(["factor", "factor_set", "strategy"] as const).map((entity) =>
        byEntity(entity, promoted).length > 0 ? (
          <div key={entity}>
            {renderGroup(`已入库 · ${ENTITY_TITLE[entity]}`, byEntity(entity, promoted), "")}
          </div>
        ) : null,
      )}
      {promoted.length === 0 &&
        renderGroup("已入库", promoted,
                    "还没有入库的成果。在候选库里选中通过全部过滤条件的候选并点「入库」即可。")}
      {renderGroup("待入库（已通过全部过滤条件）", pending,
                   "暂无待入库候选。")}
      {/* 命名纪律（§2.5.4）：run 内的 demoted 与资产级的「已衰减」是**两个概念**，
          中文必须分开——混用会让用户以为系统前后矛盾。 */}
      <div title="「本轮已淘汰」= 本任务窗口右移后的轻量复评淘汰；「已衰减」= 成果资产分区里跨任务的持续样本外衰减。两者互不等价。">
        {renderGroup("本轮已淘汰", demoted,
                     "暂无淘汰记录。数据窗口右移后系统会对本任务已入库候选做轻量复评，连续不达标者会退出父本池。跨任务的长期衰减请看「成果资产」分区。")}
      </div>

      <DiscoveryCandidateDetail
        candidateId={detailId}
        onClose={() => setDetailId(null)}
        onPromoted={() => void load()}
      />
    </div>
  );
}
