"use client";

import { useCallback, useEffect, useState } from "react";
import { Badge, Button, Modal } from "@/components/ui";
import { discoveryAssetApi } from "@/lib/discoveryAssetApi";
import type {
  DiscoveryAssetDetailRow,
  DiscoveryAssetSource,
} from "@/lib/discoveryAssetTypes";
import {
  formatRetention,
  healthReasonsCn,
  relativeTime,
} from "@/lib/discoveryHealth";
import { AXIS_SHORT_CN } from "@/lib/discoveryStrategy";
import DiscoveryHealthBadge from "./DiscoveryHealthBadge";
import DiscoveryHealthTrend from "./DiscoveryHealthTrend";
import ExpressionTree from "./ExpressionTree";

/** 双栏并排的指标键；因子与策略两套口径**不可比**，故各用各的。 */
const FACTOR_KEYS: [string, string][] = [
  ["rankIc", "RankIC"],
  ["ir", "信息比 IR"],
  ["tStat", "t 值"],
  ["samples", "配对样本"],
];
const STRATEGY_KEYS: [string, string][] = [
  ["sharpe", "夏普"],
  ["totalReturn", "总收益"],
  ["maxDrawdown", "最大回撤"],
  ["calmar", "Calmar"],
  ["tradeCount", "成交笔数"],
];

function fmt(value: number | null | undefined, digits = 4): string {
  return value === null || value === undefined || !Number.isFinite(Number(value))
    ? "—"
    : Number(value).toFixed(digits);
}

function arrow(before: number | null | undefined, after: number | null | undefined): string {
  if (before === null || before === undefined || after === null || after === undefined) {
    return "";
  }
  if (!Number.isFinite(Number(before)) || !Number.isFinite(Number(after))) return "";
  if (Number(after) > Number(before)) return "↑";
  return Number(after) < Number(before) ? "↓" : "→";
}

function SourceLine({ source }: { source: DiscoveryAssetSource }) {
  return (
    <li className="flex flex-wrap items-center gap-2 text-[11px] text-[var(--muted)]">
      {source.runExists ? (
        <span className="text-[var(--text)]">任务 #{source.runId}</span>
      ) : (
        // 悬空溯源如实标注且**不渲染跳转链接**：点了跳过去只会 404。
        <span className="text-[var(--muted)]">任务已删除</span>
      )}
      <span>第 {source.iterationIdx} 轮</span>
      <span>来源 {source.source || "—"}</span>
      <span>{relativeTime(source.foundTs)}</span>
      <span className="font-mono text-[10px] opacity-70">{source.genomeHash.slice(0, 8)}</span>
    </li>
  );
}

/**
 * 成果资产详情抽屉（六段）：头部 / 体检趋势 / 指标双栏 / 溯源时间线 / 基因 / 动作。
 *
 * 「立即体检」是一个可能挂近一分钟的同步请求，且**不重试**（重试只会撞守护的 409）。
 */
export default function DiscoveryAssetDetail({
  assetId,
  onClose,
  onChanged,
}: {
  assetId: number | null;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [detail, setDetail] = useState<DiscoveryAssetDetailRow | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");
  const [note, setNote] = useState("");

  const load = useCallback(async () => {
    if (!assetId) {
      setDetail(null);
      return;
    }
    setLoading(true);
    try {
      const row = await discoveryAssetApi.get(assetId);
      setDetail(row);
      setNote(row.note);
      setMessage("");
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "加载成果资产详情失败");
    } finally {
      setLoading(false);
    }
  }, [assetId]);

  useEffect(() => {
    void load();
  }, [load]);

  const runAction = async (key: string, action: () => Promise<string>) => {
    setBusy(key);
    try {
      setMessage(await action());
      await load();
      onChanged();
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "操作失败");
    } finally {
      setBusy("");
    }
  };

  const checkNow = () =>
    runAction("check", async () => {
      const row = await discoveryAssetApi.checkNow(assetId as number);
      return `体检完成：${row.status}${row.reasons.length ? `（${healthReasonsCn(row.reasons)}）` : ""}`;
    });

  const isFactor = detail?.kind === "factor";
  const keys = isFactor ? FACTOR_KEYS : STRATEGY_KEYS;
  const latest = detail?.checks?.length ? detail.checks[detail.checks.length - 1] : null;
  const history = latest?.metrics?.history ?? {};
  const fresh = latest?.metrics?.fresh ?? {};

  return (
    <Modal
      open={assetId !== null}
      onClose={onClose}
      labelledBy="discovery-asset-title"
      className="w-[700px]"
    >
      <div className="flex max-h-[80vh] flex-col gap-3 overflow-y-auto p-4">
        <div className="flex flex-wrap items-center gap-2">
          <h3 id="discovery-asset-title" className="text-sm font-semibold text-[var(--text)]">
            成果资产详情
          </h3>
          {detail && (
            <>
              <DiscoveryHealthBadge
                status={detail.health.status}
                reasons={detail.health.reasons}
              />
              <Badge tone="neutral">{AXIS_SHORT_CN[detail.kind] ?? detail.kind}</Badge>
              <span className="text-[11px] text-[var(--muted)]">
                {detail.instId} · {detail.bar}
                {isFactor && detail.direction ? ` · 冻结方向 ${detail.direction}` : ""}
              </span>
              {detail.archived && <Badge tone="neutral">已归档</Badge>}
            </>
          )}
        </div>

        {loading && <p className="text-xs text-[var(--muted)]">加载中…</p>}

        {detail && (
          <>
            <p className="text-xs leading-relaxed text-[var(--text)]">{detail.describe}</p>
            {detail.exprText && (
              <p className="font-mono text-[11px] text-[var(--muted)]">{detail.exprText}</p>
            )}

            {detail.variantCount > 1 && (
              <p className="rounded-[8px] bg-[var(--surface-2)] px-2 py-1.5 text-[11px] leading-relaxed text-[var(--text)]">
                本资产合并了 {detail.variantCount} 组不同权重的成果，
                <b>体检口径取首次登记的那一组</b>；其余变体的指标见下方溯源。
              </p>
            )}
            {detail.backfilled && (
              <p className="rounded-[8px] bg-[var(--surface-2)] px-2 py-1.5 text-[11px] leading-relaxed text-[var(--muted)]">
                本条为功能上线前回填，挖掘窗口的切点未知（不拿今天的窗口冒充当时的窗口）。
              </p>
            )}

            {/* ① 体检趋势 */}
            <section>
              <h4 className="mb-1 text-xs font-medium text-[var(--text)]">体检趋势</h4>
              <DiscoveryHealthTrend checks={detail.checks} />
            </section>

            {/* ② 指标双栏 */}
            <section>
              <h4 className="mb-1 text-xs font-medium text-[var(--text)]">指标对照</h4>
              <p className="mb-1.5 text-[11px] leading-relaxed text-[var(--muted)]">
                右栏只统计挖掘完成后新产生的 {latest?.freshBars ?? 0} 根 K 线，是真正的样本外
                （挖掘时这些数据还不存在，任何形式的过拟合都无法触及它）。
              </p>
              <table className="w-full text-[11px] tabular">
                <thead>
                  <tr className="text-left text-[var(--muted)]">
                    <th className="py-1 font-normal">指标</th>
                    <th className="py-1 font-normal">登记时（挖掘窗口）</th>
                    <th className="py-1 font-normal">最近一次体检（新增区间）</th>
                  </tr>
                </thead>
                <tbody>
                  {keys.map(([key, label]) => (
                    <tr key={key} className="border-t border-[var(--border)]">
                      <td className="py-1 text-[var(--muted)]">{label}</td>
                      <td className="py-1 text-[var(--text)]">{fmt(history[key] as number)}</td>
                      <td className="py-1 text-[var(--text)]">
                        {fmt(fresh[key] as number)}{" "}
                        <span className="text-[var(--muted)]">
                          {arrow(history[key] as number, fresh[key] as number)}
                        </span>
                      </td>
                    </tr>
                  ))}
                  <tr className="border-t border-[var(--border)]">
                    <td className="py-1 text-[var(--muted)]">留存度</td>
                    <td className="py-1 text-[var(--muted)]">100%</td>
                    <td className="py-1 text-[var(--text)]">
                      {formatRetention(detail.health.retention)}
                    </td>
                  </tr>
                </tbody>
              </table>
            </section>

            {/* ③ 溯源时间线 */}
            <section>
              <h4 className="mb-1 text-xs font-medium text-[var(--text)]">
                溯源（共 {detail.sourceCount} 次被挖到）
              </h4>
              <ul className="flex flex-col gap-1">
                {detail.sources.map((source) => (
                  <SourceLine key={`${source.runId}-${source.candidateId}`} source={source} />
                ))}
              </ul>
            </section>

            {/* ④ 表达式树（因子轴） */}
            {isFactor && detail.exprText && (
              <section>
                <h4 className="mb-1 text-xs font-medium text-[var(--text)]">表达式结构</h4>
                <ExpressionTree expr={detail.exprText} />
              </section>
            )}

            {/* ⑤ 备注 */}
            <section>
              <label className="text-xs text-[var(--muted)]" htmlFor="asset-note">
                备注
              </label>
              <input
                id="asset-note"
                value={note}
                maxLength={256}
                onChange={(event) => setNote(event.target.value)}
                onBlur={() =>
                  note !== detail.note &&
                  void runAction("note", async () => {
                    await discoveryAssetApi.patch(detail.id, note);
                    return "备注已保存";
                  })
                }
                className="mt-1 w-full rounded-[10px] border border-[var(--border)] bg-[var(--surface)] px-2.5 py-1.5 text-xs text-[var(--text)] outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
              />
            </section>

            {message && <p className="text-xs text-[var(--accent-ink)]">{message}</p>}

            {/* ⑥ 动作区 */}
            <div className="flex flex-wrap justify-end gap-2">
              <Button variant="ghost" size="sm" onClick={onClose}>
                关闭
              </Button>
              <Button
                variant="ghost"
                size="sm"
                disabled={busy !== ""}
                onClick={() =>
                  void runAction("archive", async () => {
                    await discoveryAssetApi.archive(detail.id, !detail.archived);
                    return detail.archived ? "已取消归档" : "已归档（不影响任何已入库实体）";
                  })
                }
              >
                {detail.archived ? "取消归档" : "归档"}
              </Button>
              {!detail.archived && (
                <Button
                  variant="default"
                  size="sm"
                  disabled={busy !== ""}
                  onClick={() => void checkNow()}
                >
                  {busy === "check" ? "正在用最新数据重评，最长约 1 分钟…" : "立即体检"}
                </Button>
              )}
              {!detail.promoted && (
                <Button
                  variant="primary"
                  size="sm"
                  disabled={busy !== ""}
                  onClick={() =>
                    void runAction("promote", async () => {
                      const res = await discoveryAssetApi.promote(detail.id);
                      return res.promoted.reused
                        ? "该成果此前已入库，已复用既有记录。"
                        : "已入库。";
                    })
                  }
                >
                  入库
                </Button>
              )}
            </div>
          </>
        )}
      </div>
    </Modal>
  );
}
