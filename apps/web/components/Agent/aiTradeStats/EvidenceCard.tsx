"use client";

import {
  EVIDENCE_FOOTNOTES,
  EVIDENCE_POOLED_NOTE,
  EVIDENCE_SNAPSHOT_ERROR_TEXT,
  evidenceInterval,
  evidencePolicyOption,
  evidenceScale,
  evidenceSessionText,
  evidenceSideLabel,
  evidenceStateLabel,
  fmtEvidenceWinRate,
  fmtSignedBps,
  type AiTradeEvidenceSide,
  type AiTradeEvidenceSideKey,
  type AiTradeEvidenceState,
  type AiTradeEvidenceStatus,
} from "@/lib/aiTradeEvidence";

/**
 * 统计页「实证准入」卡（ai-trade-evidence-gated-capital §3.5.2）。每个成员一组、每个方向一行。
 *
 * 数据源是**成员级实时快照** `m.session.evidence`（`info()` 不含本键，刷新后等下一次 status，
 * spec P2-3 的成文取舍）。所有成员都未装配 ⇒ 整卡不渲染。
 *
 * 区间条用内联 SVG，全卡共用一个半宽让各行可比；窄屏（< 860px）隐藏区间条只留数值
 * （类名 `max-[860px]:hidden` 由用例冻结）。
 */
export interface EvidenceCardMember {
  instId: string;
  evidence: AiTradeEvidenceStatus | null | undefined;
}

const SIDES: readonly AiTradeEvidenceSideKey[] = ["long", "short"];

const PILL_INK: Record<AiTradeEvidenceState, string> = {
  proven: "text-[var(--long-ink)]",
  unproven: "text-[var(--muted)]",
  disproven: "text-[var(--short-ink)]",
};

interface Row {
  key: string;
  label: string;
  side: AiTradeEvidenceSide;
  minSamples: number;
}

function collectRows(members: readonly EvidenceCardMember[]): Row[] {
  const rows: Row[] = [];
  for (const m of members) {
    if (!m.evidence) continue;
    const symbol = m.instId.split("-")[0] || m.instId;
    for (const sideKey of SIDES) {
      const side = m.evidence.sides[sideKey];
      if (!side) continue;
      rows.push({
        key: `${m.instId}-${sideKey}`,
        label: `${symbol} ${evidenceSideLabel(sideKey)}`,
        side,
        minSamples: m.evidence.minSamples,
      });
    }
  }
  return rows;
}

function IntervalBar({ side, scale }: { side: AiTradeEvidenceSide; scale: number }) {
  const geo = evidenceInterval(side, scale);
  if (!geo) return null;
  return (
    <svg
      data-testid="ai-evidence-interval"
      className="h-2.5 w-full max-[860px]:hidden"
      viewBox="0 0 100 10"
      preserveAspectRatio="none"
      aria-hidden
    >
      <line
        data-testid="ai-evidence-zero"
        x1={geo.zeroPct} x2={geo.zeroPct} y1={0} y2={10}
        stroke="var(--border)" strokeWidth={0.8}
      />
      <rect
        x={geo.loPct} y={3} width={Math.max(0.5, geo.hiPct - geo.loPct)} height={4}
        fill={side.state === "disproven" ? "var(--short)" : side.state === "proven" ? "var(--long)" : "var(--muted)"}
        opacity={0.45}
      />
      <circle cx={geo.meanPct} cy={5} r={1.8} fill="var(--text)" />
    </svg>
  );
}

function SampleCell({ side, minSamples }: { side: AiTradeEvidenceSide; minSamples: number }) {
  const ratio = minSamples > 0 ? Math.min(1, side.n / minSamples) : 1;
  return (
    <span className="flex min-w-0 flex-col gap-0.5">
      <span className="tabular">{side.n} / {minSamples}</span>
      {side.n < minSamples && (
        <span className="h-1 w-full overflow-hidden rounded-full bg-[var(--surface-2)]" data-testid="ai-evidence-progress">
          <span
            className="block h-full bg-[var(--accent)]"
            style={{ width: `${(ratio * 100).toFixed(1)}%` }}
          />
        </span>
      )}
    </span>
  );
}

function EvidenceRow({ row, scale }: { row: Row; scale: number }) {
  const { side } = row;
  return (
    <tr data-testid={`ai-evidence-row-${row.key}`} className="align-top">
      <td className="whitespace-nowrap py-0.5 pr-2 text-[var(--text)]">{row.label}</td>
      <td className="py-0.5 pr-2">
        <span className={`whitespace-nowrap ${PILL_INK[side.state]}`}>
          {evidenceStateLabel(side.state)}
        </span>
        {side.basis === "pooled" && (
          <span className="block text-[9px] text-[var(--muted)]" data-testid="ai-evidence-pooled">
            {EVIDENCE_POOLED_NOTE}
          </span>
        )}
      </td>
      <td className="py-0.5 pr-2"><SampleCell side={side} minSamples={row.minSamples} /></td>
      <td className="min-w-[96px] py-0.5 pr-2">
        <IntervalBar side={side} scale={scale} />
        <span className="tabular whitespace-nowrap text-[var(--text)]">
          {fmtSignedBps(side.meanBps)} bps [{fmtSignedBps(side.lcbBps)}, {fmtSignedBps(side.ucbBps)}]
        </span>
      </td>
      <td className="tabular py-0.5 pr-2 text-[var(--text)]">{fmtEvidenceWinRate(side)}</td>
      <td className="whitespace-nowrap py-0.5 text-[var(--muted)]" data-testid="ai-evidence-session">
        {evidenceSessionText(side)}
      </td>
    </tr>
  );
}

export default function EvidenceCard({ members }: { members: readonly EvidenceCardMember[] }) {
  const armed = members.filter((m) => m.evidence);
  if (armed.length === 0) return null;
  const rows = collectRows(armed);
  const scale = evidenceScale(rows.map((r) => r.side));
  const head = armed[0].evidence as AiTradeEvidenceStatus;
  const failed = armed.filter((m) => m.evidence?.snapshotError).map((m) => m.instId);
  return (
    <div
      className="flex min-w-0 flex-col gap-1.5 rounded-md border border-[var(--border)] bg-[var(--surface)] px-3 py-2"
      data-testid="ai-evidence-card"
    >
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-xs text-[var(--muted)]">实证准入</span>
        <span className="tabular truncate text-[10px] text-[var(--muted)]" title={head.refPolicy}>
          {evidencePolicyOption(head.policy).short} · 至少 {head.minSamples} 笔
        </span>
      </div>
      {failed.length > 0 && (
        <p className="text-[10px] text-[var(--warn)]">
          {failed.join("、")}：{EVIDENCE_SNAPSHOT_ERROR_TEXT}
        </p>
      )}
      {rows.length === 0 ? (
        <p className="text-[10px] text-[var(--muted)]">还没有样本</p>
      ) : (
        <div className="min-w-0 overflow-x-auto">
          <table className="w-full border-collapse text-left text-[10px]">
            <thead className="text-[var(--muted)]">
              <tr>
                {["标的 · 方向", "状态", "样本", "均值与区间", "胜率", "本会话"].map((h) => (
                  <th key={h} className="whitespace-nowrap pb-0.5 pr-2 font-normal">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => <EvidenceRow key={row.key} row={row} scale={scale} />)}
            </tbody>
          </table>
        </div>
      )}
      {EVIDENCE_FOOTNOTES.map((note) => (
        <p key={note} className="text-[10px] leading-relaxed text-[var(--muted)]">{note}</p>
      ))}
    </div>
  );
}
