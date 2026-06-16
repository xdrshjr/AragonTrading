import {
  INDICATORS,
  type IndicatorDef,
  type IndicatorKey,
} from "./indicators";

export interface LegendItem {
  key: string;
  label: string;
  compactLabel: string;
  tone: "overlay" | "pane";
  priority: number;
  color?: string;
}

export interface BuildIndicatorLegendItemsInput {
  indicators: Record<IndicatorKey, boolean>;
  activePaneKey: IndicatorKey | null;
  registry?: readonly IndicatorDef[];
}

const ACTIVE_PANE_PRIORITY = 1000;
const DEFAULT_OVERLAY_PRIORITY = 700;
const OPTIONAL_OVERLAY_PRIORITY = 600;

export function buildIndicatorLegendItems({
  indicators,
  activePaneKey,
  registry = INDICATORS,
}: BuildIndicatorLegendItemsInput): LegendItem[] {
  const items: LegendItem[] = [];

  for (const def of registry) {
    if (def.category !== "overlay" || !indicators[def.key]) continue;
    items.push(buildLegendItem(def, "overlay"));
  }

  const paneDef = registry.find(
    (def) =>
      def.category === "pane" &&
      def.key === activePaneKey &&
      indicators[def.key],
  );
  if (paneDef) items.push(buildLegendItem(paneDef, "pane"));

  return items;
}

function buildLegendItem(
  def: IndicatorDef,
  tone: "overlay" | "pane",
): LegendItem {
  const label = def.paramsHint
    ? `${def.label} ${def.paramsHint}`
    : def.label;

  return {
    key: `${tone}:${def.key}`,
    label,
    compactLabel: def.key.toUpperCase(),
    tone,
    priority: priorityFor(def, tone),
    color: tone === "overlay" ? "var(--accent)" : "var(--muted)",
  };
}

function priorityFor(def: IndicatorDef, tone: "overlay" | "pane"): number {
  if (tone === "pane") return ACTIVE_PANE_PRIORITY;
  return def.defaultOn ? DEFAULT_OVERLAY_PRIORITY : OPTIONAL_OVERLAY_PRIORITY;
}
