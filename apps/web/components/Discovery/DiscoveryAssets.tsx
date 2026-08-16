"use client";

import { useCallback, useEffect, useState } from "react";
import { Badge, Button, Card } from "@/components/ui";
import { discoveryAssetApi } from "@/lib/discoveryAssetApi";
import type { DiscoveryAssetQuery, DiscoveryAssetRow } from "@/lib/discoveryAssetTypes";
import { formatRetention, relativeTime, retentionBarRatio } from "@/lib/discoveryHealth";
import { AXIS_SHORT_CN } from "@/lib/discoveryStrategy";
import { useAppStore } from "@/lib/store";
import DiscoveryAssetDetail from "./DiscoveryAssetDetail";
import DiscoveryAssetFilters from "./DiscoveryAssetFilters";
import DiscoveryHealthBadge from "./DiscoveryHealthBadge";
import { useDiscovery } from "./DiscoveryLab";

const PAGE_SIZE = 50;
const DEFAULT_QUERY: DiscoveryAssetQuery = {
  archived: "0",
  sort: "lastFound",
  order: "desc",
  limit: PAGE_SIZE,
  offset: 0,
};

function isFiltered(query: DiscoveryAssetQuery): boolean {
  return Boolean(query.q || query.kind || query.status || query.promoted
    || query.archived === "1");
}

/**
 * 成果资产分区：跨 run 的台账（第 6 个分区）。
 *
 * 与「成果库」的分工：成果库回答「这一轮挖到了什么」，台账回答「我手上这些东西现在还灵不灵」。
 * 两者语义不同，故并列而不是改造——`DiscoveryLibrary` 是已发布且已终审的组件。
 */
export default function DiscoveryAssets() {
  const { runs, goSection } = useDiscovery();
  const selectedId = useAppStore((s) => s.discoveryAssetId);
  const setSelectedId = useAppStore((s) => s.setDiscoveryAssetId);
  // 筛选是会话内状态（不进 partialize）：见 DiscoveryAssetFilters 的注释。
  const [query, setQuery] = useState<DiscoveryAssetQuery>(DEFAULT_QUERY);
  const [rows, setRows] = useState<DiscoveryAssetRow[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [checked, setChecked] = useState<number[]>([]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await discoveryAssetApi.list(query);
      // 翻页是**累加**：换成替换的话「加载更多」会把已看的那一页换掉，而按钮文案还写着
      // 「已显示 N / total」——用户会以为记录丢了。筛选变更一律把 offset 归零，故走替换分支。
      setRows((prev) => ((query.offset ?? 0) > 0 ? [...prev, ...res.items] : res.items));
      setTotal(res.total);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载成果资产失败");
    } finally {
      setLoading(false);
    }
  }, [query]);

  useEffect(() => {
    void load();
  }, [load]);

  const patchQuery = (patch: Partial<DiscoveryAssetQuery>) =>
    setQuery((prev) => ({ ...prev, ...patch, offset: 0 }));

  const toggle = (id: number) =>
    setChecked((prev) => (prev.includes(id) ? prev.filter((v) => v !== id) : [...prev, id]));

  const archiveChecked = async () => {
    for (const id of checked) {
      await discoveryAssetApi.archive(id, query.archived !== "1");
    }
    setChecked([]);
    await load();
  };

  const hasRuns = runs.length > 0;

  /** 空态三分——绝不用一句「暂无数据」糊过去，三种情况的下一步动作完全不同。 */
  const renderEmpty = () => {
    if (isFiltered(query)) {
      return (
        <Card className="p-6 text-center">
          <p className="text-sm text-[var(--text)]">当前筛选条件下没有匹配的成果资产</p>
          <button
            type="button"
            onClick={() => setQuery(DEFAULT_QUERY)}
            className="mt-2 text-xs text-[var(--accent-ink)] underline"
          >
            清空筛选
          </button>
        </Card>
      );
    }
    if (hasRuns) {
      return (
        <Card className="p-6 text-center">
          <p className="text-sm text-[var(--text)]">台账还是空的，但已经有挖掘任务在跑</p>
          <p className="mt-1 text-[11px] leading-relaxed text-[var(--muted)]">
            候选只有被判为「通过全部过滤条件」之后才会登记进台账。任务刚起步时这需要一些时间。
          </p>
        </Card>
      );
    }
    return (
      <Card className="p-6 text-center">
        <p className="text-sm text-[var(--text)]">还没有挖到任何成果</p>
        <p className="mt-1 text-[11px] leading-relaxed text-[var(--muted)]">
          本功能上线前的历史成果已尽力回填；随任务一并删除的那部分无法恢复。
        </p>
        <button
          type="button"
          onClick={() => goSection("runs")}
          className="mt-2 text-xs text-[var(--accent-ink)] underline"
        >
          去新建一个挖掘任务
        </button>
      </Card>
    );
  };

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-semibold text-[var(--text)]">成果资产</h3>
        <Badge tone="neutral">{total}</Badge>
        <Button variant="ghost" size="sm" onClick={() => void load()} disabled={loading}>
          刷新
        </Button>
        <a
          href={discoveryAssetApi.exportUrl(
            checked.length ? { ids: checked.join(",") } : query,
          )}
          className="text-xs text-[var(--accent-ink)] underline"
        >
          导出{checked.length ? `选中 ${checked.length} 条` : "当前筛选"}
        </a>
        {checked.length > 0 && (
          <Button variant="ghost" size="sm" onClick={() => void archiveChecked()}>
            {query.archived === "1" ? "取消归档" : "归档"}选中 {checked.length} 条
          </Button>
        )}
      </div>

      <p className="text-[11px] leading-relaxed text-[var(--muted)]">
        台账按语义身份跨任务归一：同一条成果在多个任务被挖到只占一行。健康状态来自
        <b>挖掘完成之后新产生的数据</b>——这是唯一的真样本外证据。所有指标均为历史样本统计，
        不构成收益预期。
      </p>

      <DiscoveryAssetFilters
        query={query}
        onChange={patchQuery}
        onReset={() => setQuery(DEFAULT_QUERY)}
      />

      {error && (
        <p role="alert" className="text-xs text-[var(--short-ink)]">
          {error}
        </p>
      )}

      {rows.length === 0 && !loading ? (
        renderEmpty()
      ) : (
        <Card className="p-0">
          <ul className="flex flex-col">
            {rows.map((row) => (
              <li
                key={row.id}
                className="flex items-start gap-2 border-b border-[var(--border)] px-3 py-2 last:border-0"
              >
                <input
                  type="checkbox"
                  aria-label={`选择 ${row.label}`}
                  checked={checked.includes(row.id)}
                  onChange={() => toggle(row.id)}
                  className="mt-1"
                />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <DiscoveryHealthBadge
                      status={row.health.status}
                      reasons={row.health.reasons}
                    />
                    <button
                      type="button"
                      onClick={() => setSelectedId(row.id)}
                      className="min-w-0 flex-1 truncate text-left font-mono text-[11px] text-[var(--text)] underline-offset-2 hover:underline"
                      title={row.exprText || row.describe}
                    >
                      {row.exprText || row.describe || row.label}
                    </button>
                    <span className="shrink-0 rounded-full bg-[var(--surface-2)] px-1.5 py-0.5 text-[10px] text-[var(--muted)]">
                      {AXIS_SHORT_CN[row.kind] ?? row.kind}
                    </span>
                    {row.promoted ? (
                      <Badge tone="accent">已入库</Badge>
                    ) : (
                      <span className="text-[10px] text-[var(--muted)]">未入库</span>
                    )}
                    {row.backfilled && <Badge tone="neutral">上线前回填</Badge>}
                  </div>
                  <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] tabular text-[var(--muted)]">
                    <span className="flex items-center gap-1">
                      留存度
                      <span className="inline-block h-1.5 w-16 rounded-full bg-[var(--surface-2)]">
                        <span
                          className="block h-1.5 rounded-full bg-[var(--accent)]"
                          style={{ width: `${retentionBarRatio(row.health.retention) * 100}%` }}
                        />
                      </span>
                      <span className="text-[var(--text)]">
                        {formatRetention(row.health.retention)}
                      </span>
                    </span>
                    <span>
                      {row.instId} · {row.bar}
                    </span>
                    <span>首次发现 {relativeTime(row.firstFoundTs)}</span>
                    <span>最近体检 {relativeTime(row.health.lastCheckTs)}</span>
                    <span>溯源 {row.sourceCount} 次</span>
                    {row.variantCount > 1 && <span>合并 {row.variantCount} 组基因</span>}
                  </div>
                </div>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {total > rows.length && (
        <Button
          variant="ghost"
          size="sm"
          disabled={loading}
          onClick={() => setQuery((prev) => ({
            ...prev,
            offset: (prev.offset ?? 0) + PAGE_SIZE,
          }))}
        >
          加载更多（已显示 {rows.length} / {total}）
        </Button>
      )}

      <DiscoveryAssetDetail
        assetId={selectedId}
        onClose={() => setSelectedId(null)}
        onChanged={() => void load()}
      />
    </div>
  );
}
