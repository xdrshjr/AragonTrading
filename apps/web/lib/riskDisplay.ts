import { fmtPrice } from "./format";

export type RiskKind = "stopLoss" | "takeProfit";

const RISK_LABELS: Record<RiskKind, string> = {
  stopLoss: "止损(SL)",
  takeProfit: "止盈(TP)",
};

function isFiniteNumber(value: number | null | undefined): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

export function riskColumnLabel(kind: RiskKind): string {
  return RISK_LABELS[kind];
}

export function riskInlineLabel(kind: RiskKind): string {
  return RISK_LABELS[kind];
}

export function formatRiskPct(value: number | null | undefined): string | null {
  if (!isFiniteNumber(value)) return null;
  return `${(value * 100).toFixed(2)}%`;
}

export function formatRiskValue(
  pct: number | null | undefined,
  price: number | null | undefined,
): string {
  const pctText = formatRiskPct(pct);
  const priceText = isFiniteNumber(price) ? fmtPrice(price) : null;
  if (pctText && priceText) return `${pctText} @ ${priceText}`;
  if (priceText) return priceText;
  if (pctText) return pctText;
  return "-";
}

export function formatRiskLine(
  kind: RiskKind,
  pct: number | null | undefined,
  price: number | null | undefined,
): string {
  return `${riskInlineLabel(kind)} ${formatRiskValue(pct, price)}`;
}

export function formatMaxHoldBars(value: number | null | undefined): string | null {
  if (!isFiniteNumber(value) || value <= 0) return null;
  return `最长持有 ${value} 根K线`;
}
