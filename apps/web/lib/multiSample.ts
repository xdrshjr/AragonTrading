// 多样本回测纯逻辑（multi-sample-backtest §3）：去重 / 拆分 / 汇总 / 页签着色。
// 全部为可测纯函数（无 IO、无 React），由 MultiSampleBacktestModal / MultiSampleResults 调用。
import type { MultiSampleItem, MultiSampleResult } from "./types";

// 多样本回测标的硬上限（与后端 schemas.MultiSampleBacktestReq 的 Field(max_length=20) 对齐）。
export const MULTI_SAMPLE_MAX_SYMBOLS = 20;

/** 去重保序：首次出现位置稳定（与后端 _dedupe_strs 同口径）。 */
export function dedupeInstIds(instIds: string[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const id of instIds) {
    if (!seen.has(id)) {
      seen.add(id);
      out.push(id);
    }
  }
  return out;
}

/** 拆 instId 为 base / quote（形如 "BTC-USDT-SWAP" → {base:"BTC", quote:"USDT"}）。 */
export function splitInstId(instId: string): { base: string; quote: string } {
  const parts = instId.split("-");
  return { base: parts[0] ?? instId, quote: parts[1] ?? "" };
}

export interface MultiSampleSummary {
  okCount: number;
  errorCount: number;
  best: MultiSampleItem | null; // 按 totalReturn 最高（仅成功且含 metrics 的标的）
  worst: MultiSampleItem | null; // 按 totalReturn 最低
}

/** 汇总成功 / 失败计数 + 按 totalReturn 的最佳 / 最差标的；无成功项时 best/worst 为 null（空 / 全失败安全）。 */
export function summarizeMultiSample(result: MultiSampleResult | null): MultiSampleSummary {
  const items = result?.items ?? [];
  const okCount = items.filter((it) => it.status === "ok").length;
  const scored = items.filter((it) => it.status === "ok" && it.metrics);
  let best: MultiSampleItem | null = null;
  let worst: MultiSampleItem | null = null;
  for (const it of scored) {
    const r = it.metrics!.totalReturn;
    if (best === null || r > best.metrics!.totalReturn) best = it;
    if (worst === null || r < worst.metrics!.totalReturn) worst = it;
  }
  return { okCount, errorCount: items.length - okCount, best, worst };
}

/** 页签微缩状态着色：error → short；ok 按 totalReturn 涨绿（long）跌红（short），零为 neutral。 */
export function multiSampleTabTone(item: MultiSampleItem): "long" | "short" | "neutral" {
  if (item.status === "error") return "short";
  const r = item.metrics?.totalReturn ?? 0;
  if (r > 0) return "long";
  if (r < 0) return "short";
  return "neutral";
}
