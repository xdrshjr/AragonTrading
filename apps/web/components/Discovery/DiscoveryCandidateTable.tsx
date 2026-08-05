"use client";

import { useCallback, useEffect, useState } from "react";
import { Badge, Button, Card, Select } from "@/components/ui";
import { rejectReasonCn } from "@/lib/discovery";
import { discoveryApi } from "@/lib/discoveryApi";
import { AXIS_SHORT_CN, columnsForKind } from "@/lib/discoveryStrategy";
import type { DiscoveryCandidateRow } from "@/lib/discoveryTypes";
import DiscoveryCandidateDetail from "./DiscoveryCandidateDetail";
import { useDiscovery } from "./DiscoveryLab";

const PAGE_SIZE = 25;
const KIND_CHIPS = ["", "factor", "factor_combo", "strategy_param", "strategy_combo"];
const STRATEGY_REJECT_CODES = ["S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8", "S9",
                               "degenerate_params", "degenerate_members"];

/** 候选库：筛选（状态 / 机制族 / 拒绝原因）+ 分页 + 详情抽屉 + 批量入库。 */
export default function DiscoveryCandidates() {
  const { activeRunId, goSection } = useDiscovery();
  const [rows, setRows] = useState<DiscoveryCandidateRow[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [verdict, setVerdict] = useState("kept");
  const [mechanism, setMechanism] = useState("");
  const [reason, setReason] = useState("");
  const [kind, setKind] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [detailId, setDetailId] = useState<number | null>(null);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [bulkBusy, setBulkBusy] = useState(false);
  const [bulkMessage, setBulkMessage] = useState("");

  const load = useCallback(async () => {
    if (!activeRunId) return;
    setLoading(true);
    try {
      const res = await discoveryApi.listCandidates(activeRunId, {
        verdict,
        mechanism,
        reason,
        kind,
        limit: PAGE_SIZE,
        offset,
      });
      setRows(res.items);
      setTotal(res.total);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载候选失败");
    } finally {
      setLoading(false);
    }
  }, [activeRunId, verdict, mechanism, reason, kind, offset]);

  useEffect(() => {
    void load();
  }, [load]);

  // 切换筛选即回到第一页（否则会停在一个空白的尾页上，看起来像没数据）。
  useEffect(() => {
    setOffset(0);
    setSelected(new Set());
  }, [verdict, mechanism, reason, kind, activeRunId]);

  const toggle = (id: number) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const promoteSelected = async () => {
    setBulkBusy(true);
    setBulkMessage("");
    let ok = 0;
    let failed = 0;
    for (const id of selected) {
      try {
        await discoveryApi.promote(id);
        ok += 1;
      } catch {
        failed += 1;
      }
    }
    setBulkMessage(
      failed === 0 ? `已入库 ${ok} 条` : `已入库 ${ok} 条，${failed} 条失败（可逐条查看详情重试）`,
    );
    setSelected(new Set());
    setBulkBusy(false);
    void load();
  };

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

  const canPromote = verdict === "kept" && selected.size > 0;
  const columns = columnsForKind(kind);

  return (
    <div className="flex flex-col gap-3">
      <ul className="flex flex-wrap items-center gap-1.5" aria-label="按搜索轴筛选">
        {KIND_CHIPS.map((item) => (
          <li key={item || "all"}>
            <button
              type="button"
              onClick={() => setKind(item)}
              aria-pressed={kind === item}
              className={`rounded-full px-2.5 py-0.5 text-[11px] transition-colors ${
                kind === item
                  ? "bg-[var(--accent)] text-white"
                  : "bg-[var(--surface-2)] text-[var(--muted)] hover:text-[var(--text)]"
              }`}
            >
              {item ? AXIS_SHORT_CN[item] ?? item : "全部轴"}
            </button>
          </li>
        ))}
      </ul>

      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-semibold text-[var(--text)]">候选库</h3>
        <Select
          aria-label="按状态筛选"
          value={verdict}
          onChange={(e) => setVerdict(e.target.value)}
          className="text-xs"
        >
          <option value="kept">已入库候选</option>
          <option value="rejected">被拒绝</option>
          <option value="">全部</option>
        </Select>
        <Select
          aria-label="按机制族筛选"
          value={mechanism}
          onChange={(e) => setMechanism(e.target.value)}
          className="text-xs"
        >
          <option value="">全部机制族</option>
          {["trend_persistence", "momentum_reversal", "volatility_regime", "range_compression",
            "volume_price_divergence", "liquidity_impact", "breakout_structure",
            "shadow_pressure", "mean_reversion_band", "cross_scale_confirm",
            "intraday_seasonality", "gap_premium", "orderflow_proxy"].map((key) => (
            <option key={key} value={key}>
              {key}
            </option>
          ))}
        </Select>
        {verdict === "rejected" && (
          <Select
            aria-label="按拒绝原因筛选"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            className="text-xs"
          >
            <option value="">全部原因</option>
            {["F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8", "F9", "F10", "F11",
              ...STRATEGY_REJECT_CODES,
              "duplicate", "fsa_banned", "constant_or_empty"].map((code) => (
              <option key={code} value={code}>
                {code} · {rejectReasonCn(code)}
              </option>
            ))}
          </Select>
        )}
        <span className="text-[11px] tabular text-[var(--muted)]">共 {total} 条</span>
        <div className="ml-auto flex items-center gap-2">
          {bulkMessage && <span className="text-[11px] text-[var(--accent)]">{bulkMessage}</span>}
          <Button
            variant="primary"
            size="sm"
            disabled={!canPromote || bulkBusy}
            onClick={() => void promoteSelected()}
            title={canPromote ? undefined : "只有「已入库候选」可以批量入库"}
          >
            批量入库{selected.size > 0 ? `（${selected.size}）` : ""}
          </Button>
        </div>
      </div>

      {error && (
        <p role="alert" className="text-xs text-[var(--short)]">
          {error}
        </p>
      )}

      {rows.length === 0 && !loading ? (
        <Card className="p-6 text-center text-xs text-[var(--muted)]">
          当前筛选下没有候选。挖掘任务跑满第一轮后，这里会开始出现记录。
        </Card>
      ) : (
        <div className="overflow-x-auto rounded-[10px] border border-[var(--border)]">
          <table className="w-full text-left text-[11px]">
            <thead className="bg-[var(--surface-2)] text-[var(--muted)]">
              <tr>
                <th scope="col" className="w-8 px-2 py-1.5" />
                <th scope="col" className="px-2 py-1.5">候选</th>
                <th scope="col" className="px-2 py-1.5">轴 / 机制族</th>
                {columns.map((column) => (
                  <th
                    key={column.key}
                    scope="col"
                    className={`px-2 py-1.5 ${column.align === "right" ? "text-right" : ""}`}
                  >
                    {column.label}
                  </th>
                ))}
                <th scope="col" className="px-2 py-1.5">状态</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[var(--border)]">
              {rows.map((row) => (
                <tr key={row.id ?? row.genomeHash} className="hover:bg-[var(--surface-2)]/60">
                  <td className="px-2 py-1.5">
                    {row.verdict === "kept" && row.id !== undefined && (
                      <input
                        type="checkbox"
                        aria-label={`选择 ${row.exprText || row.describe}`}
                        checked={selected.has(row.id)}
                        onChange={() => toggle(row.id as number)}
                      />
                    )}
                  </td>
                  <td className="max-w-[260px] px-2 py-1.5">
                    <button
                      type="button"
                      onClick={() => row.id !== undefined && setDetailId(row.id)}
                      className="block max-w-full truncate text-left font-mono text-[var(--text)] underline-offset-2 hover:underline"
                      title={row.exprText || row.describe}
                    >
                      {row.exprText || row.describe}
                    </button>
                    <span className="block max-w-full truncate text-[var(--muted)]">
                      {row.describe}
                    </span>
                  </td>
                  <td className="px-2 py-1.5 text-[var(--muted)]">
                    <span className="mr-1 rounded-full bg-[var(--surface-2)] px-1.5 py-0.5 text-[10px]">
                      {AXIS_SHORT_CN[row.kind] ?? row.kind}
                    </span>
                    {row.mechanism || "—"}
                  </td>
                  {columns.map((column) => (
                    <td
                      key={column.key}
                      className={`px-2 py-1.5 tabular ${column.align === "right" ? "text-right" : ""}`}
                    >
                      {column.value(row)}
                    </td>
                  ))}
                  <td className="px-2 py-1.5">
                    {row.verdict === "kept" ? (
                      <Badge tone={row.demoted ? "short" : "long"}>
                        {row.demoted ? "已退化" : row.promotedId ? "已入库" : "待入库"}
                      </Badge>
                    ) : (
                      <span className="text-[var(--muted)]" title={rejectReasonCn(row.rejectReason)}>
                        {row.rejectReason}
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {total > PAGE_SIZE && (
        <div className="flex items-center justify-end gap-2 text-[11px]">
          <Button
            variant="ghost"
            size="sm"
            disabled={offset === 0}
            onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
          >
            上一页
          </Button>
          <span className="tabular text-[var(--muted)]">
            {Math.floor(offset / PAGE_SIZE) + 1} / {Math.ceil(total / PAGE_SIZE)}
          </span>
          <Button
            variant="ghost"
            size="sm"
            disabled={offset + PAGE_SIZE >= total}
            onClick={() => setOffset(offset + PAGE_SIZE)}
          >
            下一页
          </Button>
        </div>
      )}

      <DiscoveryCandidateDetail
        candidateId={detailId}
        onClose={() => setDetailId(null)}
        onPromoted={() => void load()}
      />
    </div>
  );
}
