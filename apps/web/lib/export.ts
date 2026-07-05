// 成交 / 净值导出 CSV（纯函数，对齐 format.ts 约定）(spec §4.4 / F4)。
// 用户原始诉求："查看不同策略实际的交易情况以及收益"——导出便于离线 Excel / Pandas 分析。
import type { AiBacktestResult } from "./aiBacktestTypes";
import type { EquityPoint, Trade } from "./types";

type Column = { key: string; label: string };

// UTF-8 BOM：前置后 Excel 才会按 UTF-8 解析中文表头（U+FEFF）。
const BOM = "﻿";

// RFC4180 风格转义：字段含 , " \r \n 时用双引号包裹，内部 " 转义为 ""；null/undefined → 空串。
function escapeField(value: unknown): string {
  const s = value === null || value === undefined ? "" : String(value);
  return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

// 输出含表头的 CSV；行分隔 \r\n。
export function toCsv(rows: Record<string, unknown>[], columns: Column[]): string {
  const header = columns.map((c) => escapeField(c.label)).join(",");
  const body = rows.map((row) => columns.map((c) => escapeField(row[c.key])).join(","));
  return [header, ...body].join("\r\n");
}

// 秒级 UTC ts -> ISO8601 UTC 字符串（全系统 ts 为秒级，C2）。
function isoUtc(tsSeconds: number): string {
  return new Date(tsSeconds * 1000).toISOString();
}

export function tradesToCsv(trades: Trade[]): string {
  const columns: Column[] = [
    { key: "time", label: "时间(UTC)" },
    { key: "side", label: "方向" },
    { key: "price", label: "价格" },
    { key: "qty", label: "数量" },
    { key: "fee", label: "手续费" },
    { key: "pnl", label: "盈亏" },
    { key: "stopLossPct", label: "止损比例(SL%)" },
    { key: "stopLossPrice", label: "止损价格(SL)" },
    { key: "takeProfitPct", label: "止盈比例(TP%)" },
    { key: "takeProfitPrice", label: "止盈价格(TP)" },
    { key: "maxHoldBars", label: "最长持有K线" },
    { key: "positionAfter", label: "持仓后" },
    { key: "reason", label: "原因" },
  ];
  const rows = trades.map((t) => ({
    time: isoUtc(t.ts),
    side: t.side,
    price: t.price,
    qty: t.qty,
    fee: t.fee,
    pnl: t.pnl,
    stopLossPct: t.stopLossPct,
    stopLossPrice: t.stopLossPrice,
    takeProfitPct: t.takeProfitPct,
    takeProfitPrice: t.takeProfitPrice,
    maxHoldBars: t.maxHoldBars,
    positionAfter: t.positionAfter,
    reason: t.reason,
  }));
  return toCsv(rows, columns);
}

export function equityToCsv(equity: EquityPoint[]): string {
  const columns: Column[] = [
    { key: "time", label: "时间(UTC)" },
    { key: "equity", label: "净值" },
    { key: "drawdown", label: "回撤" },
  ];
  const rows = equity.map((e) => ({
    time: isoUtc(e.ts),
    equity: e.equity,
    drawdown: e.drawdown,
  }));
  return toCsv(rows, columns);
}

// 触发浏览器下载：前置 BOM 便于 Excel 正确识别 UTF-8；SSR / 测试无 document 时直接返回。
export function downloadCsv(filename: string, content: string): void {
  triggerDownload(filename, BOM + content, "text/csv;charset=utf-8");
}

// 完整回测结果序列化为 JSON（缩进 2，便于离线 Pandas / jq 复算）。
export function resultToJson(result: AiBacktestResult): string {
  return JSON.stringify(result, null, 2);
}

// 下载 JSON：MIME application/json、**不前置 BOM**（BOM 会破坏严格 JSON.parse 回读，§P1-4）。
export function downloadJson(filename: string, obj: unknown): void {
  const content = typeof obj === "string" ? obj : JSON.stringify(obj, null, 2);
  triggerDownload(filename, content, "application/json;charset=utf-8");
}

// 统一的 Blob 下载触发器（SSR / 测试无 document 时安全返回）。
function triggerDownload(filename: string, content: string, mime: string): void {
  if (typeof document === "undefined") return;
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}
