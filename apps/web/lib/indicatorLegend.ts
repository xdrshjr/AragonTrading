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
  /**
   * 图例前面那颗**色点的填充色**（不是文字色），所以取的是面色而非墨色。
   *
   * 字段名不叫 `color`：`components/ui/contrastTokens.source.test.ts` 有一条门禁——任何内联
   * `color:` 都不得取面色（面色是为 K 线蜡烛设计的，作正文字号在浅色主题下不达 WCAG AA）。
   * 叫 `color` 就得给那条门禁开一个例外，而「被约定的例外」正是这类缺陷复发的入口。
   */
  swatchColor?: string;
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
    swatchColor: tone === "overlay" ? "var(--accent)" : "var(--muted)",
  };
}

function priorityFor(def: IndicatorDef, tone: "overlay" | "pane"): number {
  if (tone === "pane") return ACTIVE_PANE_PRIORITY;
  return def.defaultOn ? DEFAULT_OVERLAY_PRIORITY : OPTIONAL_OVERLAY_PRIORITY;
}
