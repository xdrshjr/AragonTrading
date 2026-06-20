// 标的纯逻辑（instrument-sidebar-tabs §6.3）：类型推断 / 搜索排序 / 自选解析。
// 全部为可测纯函数（无 IO、无 React），由 InstrumentSidebar 容器调用。
import type { Instrument } from "./types";

export type InstType = "SPOT" | "SWAP" | "FUTURES";
export type QuoteFilter = "ALL" | "USDT" | "USDC" | "USD";

// 过滤后默认渲染上限：列表已按成交额降序，主流币必在前 200，不引入虚拟列表依赖（N2）。
export const MAX_VISIBLE_ROWS = 200;

// 全新安装的自选种子（也作回退默认标的）。
export const FALLBACK_INSTRUMENTS = ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP"];

// 交割合约 instId 末段为到期日（6~8 位数字，如 250328 / 20250328）。
const FUTURES_EXPIRY_SUFFIX = /-\d{6,8}$/;

/** 由 instId 末段推断标的类型：`-SWAP`→永续；`-{到期日}`→交割；否则现货。 */
export function inferInstType(instId: string): InstType {
  if (instId.endsWith("-SWAP")) return "SWAP";
  if (FUTURES_EXPIRY_SUFFIX.test(instId)) return "FUTURES";
  return "SPOT";
}

export interface InstFilterOpts {
  query: string;
  quote?: QuoteFilter; // 省略 / "ALL" 不按计价过滤
  instTypes?: InstType[]; // 省略 / 空数组=不限（合约页签「全部」）；["SWAP"]=永续；["FUTURES"]=交割
  limit?: number; // 渲染上限，默认 MAX_VISIBLE_ROWS
}

export interface FilteredInstruments {
  rows: Instrument[]; // 截断后用于渲染
  total: number; // 截断前匹配总数
  truncated: boolean; // total 是否超过 limit
}

function matchesType(instId: string, instTypes: InstType[] | undefined): boolean {
  if (!instTypes || instTypes.length === 0) return true;
  return instTypes.includes(inferInstType(instId));
}

function matchesQuery(inst: Instrument, needle: string): boolean {
  if (needle === "") return true;
  return inst.instId.toLowerCase().includes(needle) || inst.base.toLowerCase().includes(needle);
}

/** 按 query / quote / instTypes 过滤，保持入参（成交额降序）顺序，截断到 limit。 */
export function filterAndRankInstruments(
  list: Instrument[] | undefined,
  opts: InstFilterOpts,
): FilteredInstruments {
  const limit = opts.limit ?? MAX_VISIBLE_ROWS;
  const needle = opts.query.trim().toLowerCase();
  const quote = opts.quote && opts.quote !== "ALL" ? opts.quote : null;
  const matched = (list ?? []).filter(
    (inst) =>
      matchesType(inst.instId, opts.instTypes) &&
      (quote === null || inst.quote === quote) &&
      matchesQuery(inst, needle),
  );
  return {
    rows: matched.slice(0, limit),
    total: matched.length,
    truncated: matched.length > limit,
  };
}

function placeholderInstrument(instId: string): Instrument {
  const parts = instId.split("-");
  return {
    instId,
    instType: inferInstType(instId),
    base: parts[0] ?? instId,
    quote: parts[1] ?? "",
    last: null,
    open24h: null,
    vol24h: null,
    volCcy24h: null,
    changePct: null,
  };
}

/**
 * 按收藏顺序把 watchlist 解析为带行情的行；缺失行情则降级为占位行（仍可见、可移除）。
 * `byInstId` 由调用方归并涉及类型的 query 结果而成的单一映射（RV-2 / C-4）。
 */
export function resolveWatchlistRows(
  watchlist: string[],
  byInstId: Map<string, Instrument>,
): Instrument[] {
  return watchlist.map((instId) => byInstId.get(instId) ?? placeholderInstrument(instId));
}
