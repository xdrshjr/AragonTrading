// 数字 / 时间 / 百分比格式化 (spec §8.2)。

export function fmtPct(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
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

/**
 * 盈亏数字的**文字色**（正绿 / 负红 / 零灰）。
 *
 * 返回墨色而不是面色（D2）：这一个函数被 83 处引用，且**全部**是 `style={{ color }}`——
 * 它实际上是全应用盈亏数字的统一着色点。面色 `--long` / `--short` 是为 K 线蜡烛设计的，作为
 * 正文字号的文字在浅色主题下只有 3.74 / 4.13，不达 WCAG AA；而「亏损红与盈利绿」正是这个产品
 * 的主要信息载体。改这一行，83 处一起达标。
 *
 * ⚠️ 返回值只能用于文字。要给色块 / 圆点 / 边框上色，请直接用面色 `var(--long)` / `var(--short)`。
 */
export function colorForReturn(v: number): string {
  if (v > 0) return "var(--long-ink)";
  if (v < 0) return "var(--short-ink)";
  return "var(--muted)";
}

export function fmtQty(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "-";
  return v.toLocaleString("en-US", {
    minimumFractionDigits: 4,
    maximumFractionDigits: 4,
  });
}

// 模拟资金显示：¥ + 千分位（整数不带小数，否则至多两位）。非有限回落 ¥0。
export function fmtMoney(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "¥0";
  return `¥${v.toLocaleString("en-US", { minimumFractionDigits: 0, maximumFractionDigits: 2 })}`;
}

// 解析用户输入的金额：剥离 ¥ / 逗号 / 空白；空串或非有限数返回 null（由调用方回退原值）。
export function parseMoneyInput(raw: string): number | null {
  const cleaned = raw.replace(/[¥,\s]/g, "");
  if (cleaned === "") return null;
  const n = Number(cleaned);
  return Number.isFinite(n) ? n : null;
}
