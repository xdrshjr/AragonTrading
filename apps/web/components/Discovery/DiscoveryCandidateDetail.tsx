"use client";

import { useEffect, useState } from "react";
import { Badge, Button, Modal } from "@/components/ui";
import { rejectReasonCn } from "@/lib/discovery";
import { discoveryApi } from "@/lib/discoveryApi";
import type { DiscoveryCandidateDetail as Detail } from "@/lib/discoveryTypes";
import ExpressionTree from "./ExpressionTree";

function fmt(value: number | null | undefined, digits = 4): string {
  return value === null || value === undefined || !Number.isFinite(value)
    ? "—"
    : value.toFixed(digits);
}

/** 分段 IC 柱（自绘 SVG）：正负分色，零轴居中；空序列显示「样本不足」。 */
function IcBars({ values, label }: { values: number[]; label: string }) {
  if (!values.length) {
    return <p className="text-[11px] text-[var(--muted)]">{label}：样本不足</p>;
  }
  const max = Math.max(...values.map(Math.abs), 0.0001);
  const width = 220;
  const height = 44;
  const barW = width / values.length;
  return (
    <div>
      <p className="mb-1 text-[11px] text-[var(--muted)]">{label}</p>
      <svg width={width} height={height} role="img" aria-label={`${label} 分段 IC`}>
        <line x1="0" y1={height / 2} x2={width} y2={height / 2} stroke="var(--border)" strokeWidth="1" />
        {values.map((value, index) => {
          const h = (Math.abs(value) / max) * (height / 2 - 2);
          return (
            <rect
              key={index}
              x={index * barW + 1}
              y={value >= 0 ? height / 2 - h : height / 2}
              width={Math.max(1, barW - 2)}
              height={Math.max(1, h)}
              fill={value >= 0 ? "var(--long)" : "var(--short)"}
            />
          );
        })}
      </svg>
    </div>
  );
}

/** 分位收益柱（自绘 SVG）。 */
function QuantileBars({ buckets }: { buckets: { q: number; meanRet: number; count: number }[] }) {
  if (!buckets.length) {
    return <p className="text-[11px] text-[var(--muted)]">分位收益：样本不足</p>;
  }
  const max = Math.max(...buckets.map((b) => Math.abs(b.meanRet)), 0.0001);
  return (
    <div>
      <p className="mb-1 text-[11px] text-[var(--muted)]">分位前瞻收益（方向调整后）</p>
      <ul className="flex items-end gap-1" style={{ height: 48 }}>
        {buckets.map((bucket) => (
          <li key={bucket.q} className="flex flex-1 flex-col items-center justify-end gap-0.5">
            <div
              className="w-full rounded-t-[3px]"
              style={{
                height: `${(Math.abs(bucket.meanRet) / max) * 34}px`,
                background: bucket.meanRet >= 0 ? "var(--long)" : "var(--short)",
              }}
              title={`Q${bucket.q + 1}：${(bucket.meanRet * 100).toFixed(3)}%（${bucket.count} 样本）`}
            />
            <span className="text-[9px] text-[var(--muted)]">Q{bucket.q + 1}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** 候选详情抽屉：表达式树 + 中文解释 + IS/OOS 双份指标 + 分段 IC + 分位收益 + 入库 / 回测。 */
export default function DiscoveryCandidateDetail({
  candidateId,
  onClose,
  onPromoted,
}: {
  candidateId: number | null;
  onClose: () => void;
  onPromoted: () => void;
}) {
  const [detail, setDetail] = useState<Detail | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");

  useEffect(() => {
    if (!candidateId) {
      setDetail(null);
      return;
    }
    setLoading(true);
    setMessage("");
    discoveryApi
      .getCandidate(candidateId)
      .then(setDetail)
      .catch((err) => setMessage(err instanceof Error ? err.message : "加载候选详情失败"))
      .finally(() => setLoading(false));
  }, [candidateId]);

  const promote = async () => {
    if (!candidateId) return;
    setBusy("promote");
    try {
      const res = await discoveryApi.promote(candidateId);
      setMessage(
        res.promoted.reused
          ? "该因子此前已入库，已复用既有记录。"
          : "已入库到「我的因子」，可在因子实验室评估、训练与组合。",
      );
      onPromoted();
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "入库失败");
    } finally {
      setBusy("");
    }
  };

  const metrics = detail?.metrics ?? {};
  const isSide = metrics.is;
  const oosSide = metrics.oos;

  return (
    <Modal
      open={candidateId !== null}
      onClose={onClose}
      labelledBy="discovery-candidate-title"
      className="w-[680px]"
    >
      <div className="flex max-h-[80vh] flex-col gap-3 overflow-y-auto p-4">
        <div className="flex items-center gap-2">
          <h3 id="discovery-candidate-title" className="text-sm font-semibold text-[var(--text)]">
            候选详情
          </h3>
          {detail && (
            <Badge tone={detail.verdict === "kept" ? "long" : "neutral"}>
              {detail.verdict === "kept" ? "已入库候选" : `已拒绝：${rejectReasonCn(detail.rejectReason)}`}
            </Badge>
          )}
          {detail?.demoted && <Badge tone="short">已退化</Badge>}
        </div>

        {loading && <p className="text-xs text-[var(--muted)]">加载中…</p>}

        {detail && (
          <>
            <div>
              <pre className="overflow-x-auto rounded-[10px] bg-[var(--surface-2)] px-3 py-2 font-mono text-[11px] text-[var(--text)]">
                {detail.exprText}
              </pre>
              <p className="mt-1 text-xs leading-relaxed text-[var(--text)]">{detail.describe}</p>
              <p className="mt-0.5 text-[11px] text-[var(--muted)]">
                机制族 {detail.mechanism || "未分类"} · 来源 {detail.source} · 第 {detail.iterationIdx} 轮
              </p>
            </div>

            <ExpressionTree expr={detail.exprText} />

            <div className="grid grid-cols-2 gap-3">
              {[
                { title: "样本内（IS）", side: isSide },
                { title: "样本外（OOS）", side: oosSide },
              ].map(({ title, side }) => (
                <div key={title} className="rounded-[10px] border border-[var(--border)] p-2.5">
                  <h4 className="mb-1 text-xs font-medium text-[var(--text)]">{title}</h4>
                  <dl className="grid grid-cols-2 gap-x-3 gap-y-0.5 text-[11px] tabular">
                    {[
                      ["RankIC", fmt(side?.rankIc)],
                      ["IR", fmt(side?.ir, 3)],
                      ["t 值", fmt(side?.tStat, 2)],
                      ["单调性", fmt(side?.monotonicity, 3)],
                      ["同号占比", fmt(side?.signStability, 3)],
                      ["配对样本", String(side?.samples ?? "—")],
                    ].map(([label, value]) => (
                      <div key={label} className="flex justify-between gap-2">
                        <dt className="text-[var(--muted)]">{label}</dt>
                        <dd className="text-[var(--text)]">{value}</dd>
                      </div>
                    ))}
                  </dl>
                </div>
              ))}
            </div>

            <dl className="grid grid-cols-4 gap-2 text-[11px] tabular">
              {[
                ["方向", metrics.direction === "short" ? "看空" : "看多"],
                ["换手代理", fmt(metrics.turnoverProxy, 3)],
                ["入库时试验数", String(metrics.trials ?? "—")],
                ["IR 门槛", fmt(metrics.requiredIr, 3)],
              ].map(([label, value]) => (
                <div key={label} className="rounded-[8px] bg-[var(--surface-2)] px-2 py-1.5">
                  <dt className="text-[var(--muted)]">{label}</dt>
                  <dd className="text-[var(--text)]">{value}</dd>
                </div>
              ))}
            </dl>

            <div className="grid grid-cols-2 gap-3">
              <IcBars values={detail.series.isIc ?? []} label="样本内分段 RankIC" />
              <IcBars values={detail.series.oosIc ?? []} label="样本外分段 RankIC" />
            </div>
            <QuantileBars buckets={detail.series.quantiles ?? []} />

            <p className="text-[10px] leading-relaxed text-[var(--muted)]">
              「入库时试验数」越大，说明该因子是在越多次尝试中被选出的，统计上越可能沾了多重检验的光；
              系统已按 log10(试验数) 抬高 IR 门槛，但这个数字仍值得你亲自看一眼。以上均为历史样本统计，
              不构成收益预期。
            </p>

            {message && <p className="text-xs text-[var(--accent)]">{message}</p>}

            <div className="flex justify-end gap-2">
              <Button variant="ghost" size="sm" onClick={onClose}>
                关闭
              </Button>
              {detail.verdict === "kept" && (
                <Button
                  variant="primary"
                  size="sm"
                  disabled={busy === "promote"}
                  onClick={() => void promote()}
                >
                  {detail.promotedId ? "已入库（再次入库幂等）" : "入库到我的因子"}
                </Button>
              )}
            </div>
          </>
        )}
      </div>
    </Modal>
  );
}
