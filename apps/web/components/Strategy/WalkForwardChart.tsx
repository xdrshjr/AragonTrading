"use client";

import { fmtPct } from "@/lib/format";
import { windowBarGeometry } from "@/lib/walkForward";
import type { WalkForwardWindow } from "@/lib/types";

// 每窗口收益率柱状图：横轴窗口序号，纵轴 totalReturn，正绿负红，最好/最差窗口高亮（内联 div，无依赖）。
// 以零基线为中轴：正收益向上、负收益向下，柱高按最大绝对值归一化（spec §7.3）。
export default function WalkForwardChart({
  windows,
  bestIndex,
  worstIndex,
}: {
  windows: WalkForwardWindow[];
  bestIndex: number;
  worstIndex: number;
}) {
  const geometry = windowBarGeometry(windows.map((w) => w.metrics.totalReturn));
  if (geometry.length === 0) {
    return <div className="py-6 text-center text-xs text-[var(--muted)]">无窗口数据</div>;
  }

  return (
    <div className="rounded-[10px] border border-[var(--border)] bg-[var(--surface-2)] p-3">
      <div className="mb-2 text-[11px] font-medium text-[var(--muted)]">各窗口收益率分布</div>
      <div className="flex h-[120px] items-stretch gap-0.5">
        {geometry.map((bar) => {
          const highlight =
            bar.index === bestIndex ? "best" : bar.index === worstIndex ? "worst" : null;
          const color = bar.positive ? "var(--long)" : "var(--short)";
          return (
            <div
              key={bar.index}
              className="group relative flex flex-1 flex-col justify-center"
              title={`窗口 #${bar.index + 1}：${fmtPct(bar.value)}${
                highlight === "best" ? "（最好）" : highlight === "worst" ? "（最差）" : ""
              }`}
            >
              {/* 上半区：正收益向上生长 */}
              <div className="flex flex-1 items-end">
                <div
                  className="w-full rounded-t-[2px]"
                  style={{
                    height: bar.positive ? `${bar.heightPct}%` : "0%",
                    background: color,
                    opacity: highlight ? 1 : 0.7,
                    outline: highlight ? `1px solid ${color}` : undefined,
                  }}
                />
              </div>
              {/* 零基线 */}
              <div className="h-px w-full bg-[var(--border)]" />
              {/* 下半区：负收益向下生长 */}
              <div className="flex flex-1 items-start">
                <div
                  className="w-full rounded-b-[2px]"
                  style={{
                    height: bar.positive ? "0%" : `${bar.heightPct}%`,
                    background: color,
                    opacity: highlight ? 1 : 0.7,
                    outline: highlight ? `1px solid ${color}` : undefined,
                  }}
                />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
