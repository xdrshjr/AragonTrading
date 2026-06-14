// 数字 / 时间 / 百分比格式化 (spec §8.2)。

export function fmtPct(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return `${(v * 100).toFixed(digits)}%`;
}

export function fmtNum(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return v.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function fmtPrice(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const digits = v >= 1000 ? 1 : v >= 1 ? 2 : 4;
  return v.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

// 秒级 UTC -> 本地可读时间
export function fmtTime(tsSeconds: number, withDate = true): string {
  const d = new Date(tsSeconds * 1000);
  const date = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  const time = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  return withDate ? `${date} ${time}` : time;
}

function pad(n: number): string {
  return n < 10 ? `0${n}` : `${n}`;
}

export function signed(v: number, digits = 2): string {
  const s = v.toFixed(digits);
  return v > 0 ? `+${s}` : s;
}

export function colorForReturn(v: number): string {
  if (v > 0) return "var(--long)";
  if (v < 0) return "var(--short)";
  return "var(--muted)";
}

export function fmtQty(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "-";
  return v.toLocaleString("en-US", {
    minimumFractionDigits: 4,
    maximumFractionDigits: 4,
  });
}
