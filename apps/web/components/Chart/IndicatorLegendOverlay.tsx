"use client";

import type { LegendItem } from "@/lib/indicatorLegend";

interface IndicatorLegendOverlayProps {
  items: LegendItem[];
}

const OVERLAY_CLASS_NAME = [
  "pointer-events-none absolute left-3 right-20 top-2 z-10",
  "flex max-h-10 max-w-[calc(100%-96px)] flex-wrap items-center",
  "gap-x-3 gap-y-1 overflow-hidden text-[11px] font-medium leading-4",
  "text-[var(--text)] drop-shadow-[0_1px_1px_rgba(0,0,0,0.45)]",
].join(" ");

export default function IndicatorLegendOverlay({
  items,
}: IndicatorLegendOverlayProps) {
  if (items.length === 0) return null;

  const label = `Active indicators: ${items
    .map((item) => item.compactLabel)
    .join(", ")}`;

  return (
    <div
      className={OVERLAY_CLASS_NAME}
      aria-label={label}
    >
      {items.map((item) => (
        <LegendToken key={item.key} item={item} />
      ))}
    </div>
  );
}

function LegendToken({ item }: { item: LegendItem }) {
  const shouldHideOnTightScreens =
    item.tone !== "pane" && item.priority < 700;

  return (
    <span
      className={[
        "inline-flex min-w-0 items-center gap-1.5 whitespace-nowrap",
        shouldHideOnTightScreens ? "max-[420px]:hidden" : "",
      ].join(" ")}
    >
      <span
        className="h-1.5 w-1.5 shrink-0 rounded-full"
        style={{ backgroundColor: item.swatchColor ?? "var(--accent)" }}
      />
      <span className="truncate max-sm:hidden">{item.label}</span>
      <span className="hidden truncate max-sm:inline">
        {item.compactLabel}
      </span>
    </span>
  );
}
