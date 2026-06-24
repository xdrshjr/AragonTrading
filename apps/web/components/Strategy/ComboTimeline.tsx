"use client";

import { buildMemberColorMap, totalTimelineBars } from "@/lib/combo";
import { fmtPct, fmtTime } from "@/lib/format";
import { regimeLabel } from "@/lib/regimeAffinity";
import type { ComboTimelineSeg } from "@/lib/types";

// 轮动时间线色带：按成员配色分段（宽度 ∝ 根数），悬浮显示体制 / 区间 / 贡献。
export default function ComboTimeline({ segments }: { segments: ComboTimelineSeg[] }) {
  if (!segments.length) {
    return (
      <div className="rounded-[10px] border border-dashed border-[var(--border)] px-3 py-4 text-center text-[11px] text-[var(--muted)]">
        运行组合回测后显示轮动时间线
      </div>
    );
  }

  const colorMap = buildMemberColorMap(segments.map((s) => s.memberKey));
  const total = totalTimelineBars(segments);
  const legend = Array.from(colorMap, ([memberKey, color]) => {
    const name = segments.find((s) => s.memberKey === memberKey)?.memberName ?? memberKey;
    return { memberKey, color, name };
  });

  return (
    <div className="flex flex-col gap-2">
      <div className="flex h-6 w-full overflow-hidden rounded-[8px] border border-[var(--border)]">
        {segments.map((seg, index) => {
          const widthPct = (Math.max(0, seg.bars) / total) * 100;
          const color = colorMap.get(seg.memberKey) ?? "var(--muted)";
          return (
            <div
              key={`${seg.startTs}-${index}`}
              style={{ width: `${widthPct}%`, background: color }}
              title={
                `${seg.memberName}\n体制：${regimeLabel(seg.regime)}\n` +
                `区间：${fmtTime(seg.startTs)} → ${fmtTime(seg.endTs)}\n` +
                `根数：${seg.bars}　贡献：${fmtPct(seg.contribution)}`
              }
              className="h-full transition-opacity hover:opacity-80"
            />
          );
        })}
      </div>
      <div className="flex flex-wrap gap-x-3 gap-y-1">
        {legend.map((item) => (
          <span key={item.memberKey} className="flex items-center gap-1 text-[11px] text-[var(--muted)]">
            <span className="inline-block h-2.5 w-2.5 rounded-[3px]" style={{ background: item.color }} />
            <span className="truncate text-[var(--text)]">{item.name}</span>
          </span>
        ))}
      </div>
    </div>
  );
}
