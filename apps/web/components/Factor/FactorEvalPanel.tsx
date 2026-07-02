"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Badge, Button, Select } from "@/components/ui";
import type { PerSymbolEval } from "@/lib/factorTypes";
import {
  directionLabel,
  formatIc,
  formatPct,
  freezeDirection,
  icCurvePoints,
  icTone,
  pointsToPath,
  quantileBars,
} from "@/lib/factor";
import { factorApi } from "@/lib/factorApi";
import { factorPointsToLine } from "@/lib/factorSeries";
import type { FactorDirection, FactorEvalResult, FactorHorizonStat } from "@/lib/factorTypes";
import { useAppStore } from "@/lib/store";
import FactorScoreChart from "./FactorScoreChart";
import { useWorkbench } from "./FactorLab";

const HORIZONS = [1, 3, 6, 12];
// 日内限定因子用于非日内 bar 的统一空态文案（L5）。
const BAR_INCOMPATIBLE_NOTE = "该因子仅适用于日内周期，请切换到 1m/5m/15m/1H 等日内周期";

/** 因子评估：选因子 + 方向 → IC 曲线 + 分位收益条形 + 逐 horizon 前瞻表 + 加入组合 / 上图。 */
export default function FactorEvalPanel() {
  const wb = useWorkbench();
  const setSection = useAppStore((s) => s.setFactorLabSection);
  const setOverlay = useAppStore((s) => s.setFactorOverlay);
  const lib = useQuery({ queryKey: ["factor", "library"], queryFn: factorApi.library });

  const [factorKey, setFactorKey] = useState(wb.evalTarget?.factorKey ?? "mom_roc");
  const [direction, setDirection] = useState<FactorDirection>(wb.evalTarget?.direction ?? "auto");
  const [result, setResult] = useState<FactorEvalResult | null>(null);

  useEffect(() => {
    if (wb.evalTarget) {
      setFactorKey(wb.evalTarget.factorKey);
      setDirection(wb.evalTarget.direction);
      setResult(null);
    }
  }, [wb.evalTarget]);

  const range = { instId: wb.instId, bar: wb.bar, startTs: wb.startTs, endTs: wb.endTs };
  // 标的组（>1）时把组成员下发后端逐标的取数聚合；单标的省略 symbols 走原路径（factor-lab-symbol-groups §3.3）。
  const groupSymbols = wb.symbols.length > 1 ? wb.symbols : undefined;
  const evaluate = useMutation({
    mutationFn: () =>
      factorApi.evaluate({ ...range, factorKey, direction, horizons: HORIZONS,
        ...(groupSymbols ? { symbols: groupSymbols } : {}) }),
    onSuccess: setResult,
  });
  const compute = useMutation({ mutationFn: () => factorApi.compute({ ...range, factorKey }) });

  const series = useMemo(
    () => (compute.data ? factorPointsToLine(compute.data.points) : []),
    [compute.data],
  );

  return (
    <div className="flex flex-col gap-4 pb-6">
      <div className="card flex flex-wrap items-end gap-3 p-3">
        <label className="flex flex-col gap-1">
          <span className="text-[11px] text-[var(--muted)]">因子</span>
          <Select value={factorKey} onChange={(e) => setFactorKey(e.target.value)} className="py-1 text-xs">
            {(lib.data ?? []).map((f) => (
              <option key={f.key} value={f.key}>
                {f.cn}（{f.key}）
              </option>
            ))}
          </Select>
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-[11px] text-[var(--muted)]">方向</span>
          <Select
            value={direction}
            onChange={(e) => setDirection(e.target.value as FactorDirection)}
            className="py-1 text-xs"
          >
            <option value="auto">自动</option>
            <option value="long">看多</option>
            <option value="short">看空</option>
          </Select>
        </label>
        <Button variant="primary" disabled={evaluate.isPending} onClick={() => evaluate.mutate()}>
          {evaluate.isPending ? "评估中..." : "运行评估"}
        </Button>
        <Button variant="default" disabled={compute.isPending} onClick={() => { compute.mutate(); setOverlay({ kind: "factor", factorKey }); }}>
          {compute.isPending ? "计算中..." : "上图预览"}
        </Button>
      </div>

      {evaluate.isError && (
        <p className="text-[11px] text-[var(--short)]">评估失败：{(evaluate.error as Error)?.message}</p>
      )}

      {compute.data?.note === "bar_incompatible" ? (
        <FactorScoreChart points={[]} title={`${factorKey} 因子值`} emptyNote={BAR_INCOMPATIBLE_NOTE} />
      ) : (
        series.length > 0 && <FactorScoreChart points={series} title={`${factorKey} 因子值`} />
      )}

      {result && <EvalResult result={result} onAddToCombo={() => {
        wb.addPendingMember({
          factorKey: result.factorKey,
          params: wb.evalTarget?.params ?? {},
          weight: 0,
          direction: freezeDirection(result.direction),
        });
        setSection("builder");
      }} />}
    </div>
  );
}

function EvalResult({ result, onAddToCombo }: { result: FactorEvalResult; onAddToCombo: () => void }) {
  // 日内限定因子用于非日内 bar → 全 NaN：明确解释原因而非静默空白（L5）。
  if (result.note === "bar_incompatible") {
    return (
      <div className="card flex flex-col gap-2 p-4 text-center">
        <span className="text-[11px] font-medium text-[var(--accent)]">该因子仅适用于日内周期</span>
        <span className="text-[11px] text-[var(--muted)]">{BAR_INCOMPATIBLE_NOTE}。</span>
      </div>
    );
  }
  // 退化情形（无前瞻统计 / 零配对样本）不渲染空图，给出明确空态（H9 可靠性）。
  if (result.horizons.length === 0 || result.occurrences === 0) {
    return (
      <div className="card flex flex-col gap-2 p-4 text-center">
        <span className="text-[11px] font-medium text-[var(--muted)]">评估无有效结果</span>
        <span className="text-[11px] text-[var(--muted)]">
          样本不足或区间过短（配对样本 {result.occurrences}）：请扩大区间或更换因子后重试。
        </span>
      </div>
    );
  }
  const primary = result.horizons.find((h) => h.quantiles.length > 0) ?? result.horizons[0];
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2">
        {result.aggregated && (
          <Badge tone="accent">组内均值 · {result.symbols?.length ?? result.perSymbol?.length ?? 0} 标的</Badge>
        )}
        <Badge tone={result.direction === "long" ? "long" : "short"}>方向 {directionLabel(result.direction)}</Badge>
        <Badge tone="neutral">覆盖 {formatPct(result.coverage)}</Badge>
        <Badge tone="neutral">配对样本 {result.occurrences}</Badge>
        {result.note === "samples_sparse" && <Badge tone="short">样本偏少，结论谨慎</Badge>}
        <div className="ml-auto">
          <Button variant="primary" size="sm" onClick={onAddToCombo}>加入组合</Button>
        </div>
      </div>

      {result.aggregated && result.perSymbol && result.perSymbol.length > 0 && (
        <PerSymbolDrawer rows={result.perSymbol} />
      )}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div className="card flex flex-col gap-2 p-3">
          <span className="text-[11px] font-medium text-[var(--muted)]">RankIC 曲线（逐 horizon）</span>
          <IcCurve horizons={result.horizons} />
        </div>
        <div className="card flex flex-col gap-2 p-3">
          <span className="text-[11px] font-medium text-[var(--muted)]">
            分位前瞻收益（h={primary?.h ?? "—"}，单调性 {formatIc(primary?.monotonicity)}）
          </span>
          <QuantileBars horizon={primary} />
        </div>
      </div>

      <HorizonTable horizons={result.horizons} />
    </div>
  );
}

// 逐标的贡献抽屉（factor-lab-symbol-groups §3.3）：透明呈现哪些标的入了组内均值（配对样本 / 主 RankIC / 覆盖），
// 绝不隐藏聚合来源。默认展开首屏可见，可折叠收起。
function PerSymbolDrawer({ rows }: { rows: PerSymbolEval[] }) {
  const [open, setOpen] = useState(true);
  return (
    <div className="rounded-[12px] border border-[var(--border)]">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center justify-between px-3 py-2 text-left text-[11px] font-medium text-[var(--muted)]"
      >
        <span>逐标的贡献（{rows.length} 标的）</span>
        <span aria-hidden>{open ? "收起 ▲" : "展开 ▼"}</span>
      </button>
      {open && (
        <div className="overflow-x-auto border-t border-[var(--border)]">
          <table className="w-full min-w-[420px] text-left text-xs tabular">
            <thead className="bg-[var(--surface-2)] text-[var(--muted)]">
              <tr>
                {["标的", "配对样本", "主 RankIC", "覆盖"].map((c) => (
                  <th key={c} className="px-3 py-1.5 font-medium">{c}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.instId} className="border-t border-[var(--border)]">
                  <td className="px-3 py-1.5 font-medium text-[var(--text)]">{r.instId}</td>
                  <td className="px-3 py-1.5 text-[var(--muted)]">{r.occurrences}</td>
                  <td className={cellTone(r.primaryRankIc)}>{formatIc(r.primaryRankIc)}</td>
                  <td className="px-3 py-1.5 text-[var(--muted)]">{formatPct(r.coverage)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function IcCurve({ horizons }: { horizons: FactorHorizonStat[] }) {
  const width = 320;
  const height = 110;
  const pts = icCurvePoints(horizons, width, height);
  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="h-auto w-full" role="img" aria-label="RankIC 曲线">
      <line x1={0} x2={width} y1={height / 2} y2={height / 2} stroke="var(--border)" strokeDasharray="3 3" />
      {pts.length > 0 ? (
        <>
          <path d={pointsToPath(pts)} fill="none" stroke="var(--accent)" strokeWidth={1.8} strokeLinejoin="round" />
          {pts.map((p, i) => (
            <circle key={i} cx={p.x} cy={p.y} r={2.5} fill="var(--accent)" />
          ))}
        </>
      ) : (
        <text x={width / 2} y={height / 2} textAnchor="middle" className="fill-[var(--muted)] text-[11px]">
          样本不足
        </text>
      )}
    </svg>
  );
}

function QuantileBars({ horizon }: { horizon: FactorHorizonStat | undefined }) {
  const bars = quantileBars(horizon?.quantiles ?? []);
  if (bars.length === 0) {
    return <p className="py-6 text-center text-[11px] text-[var(--muted)]">无分位数据</p>;
  }
  return (
    <div className="flex h-[110px] items-end gap-1.5">
      {bars.map((b) => (
        <div key={b.q} className="flex flex-1 flex-col items-center gap-1" title={`Q${b.q + 1}: ${formatPct(b.meanRet)}（${b.count}）`}>
          <div className="flex w-full flex-1 items-end">
            <div
              className="w-full rounded-t-[3px]"
              style={{ height: `${Math.max(2, b.heightPct)}%`, background: b.positive ? "var(--long)" : "var(--short)" }}
            />
          </div>
          <span className="text-[10px] text-[var(--muted)]">Q{b.q + 1}</span>
        </div>
      ))}
    </div>
  );
}

function HorizonTable({ horizons }: { horizons: FactorHorizonStat[] }) {
  return (
    <div className="overflow-x-auto rounded-[12px] border border-[var(--border)]">
      <table className="w-full min-w-[560px] text-left text-xs tabular">
        <thead className="bg-[var(--surface-2)] text-[var(--muted)]">
          <tr>
            {["h", "IC", "RankIC", "IR", "t 值", "单调性", "top-bottom"].map((c) => (
              <th key={c} className="px-3 py-2 font-medium">{c}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {horizons.map((s) => (
            <tr key={s.h} className="border-t border-[var(--border)]">
              <td className="px-3 py-1.5 font-medium">{s.h}</td>
              <td className={cellTone(s.ic)}>{formatIc(s.ic)}</td>
              <td className={cellTone(s.rankIc)}>{formatIc(s.rankIc)}</td>
              <td className={cellTone(s.ir)}>{formatIc(s.ir)}</td>
              <td className="px-3 py-1.5 text-[var(--muted)]">{s.tStat == null ? "—" : s.tStat.toFixed(2)}</td>
              <td className={cellTone(s.monotonicity)}>{formatIc(s.monotonicity)}</td>
              <td className={cellTone(s.topBottomSpread)}>{formatPct(s.topBottomSpread)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function cellTone(value: number | null): string {
  const tone = icTone(value);
  return `px-3 py-1.5 ${tone === "long" ? "text-[var(--long)]" : tone === "short" ? "text-[var(--short)]" : "text-[var(--muted)]"}`;
}
