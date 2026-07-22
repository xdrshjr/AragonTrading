"use client";

import { useQuery } from "@tanstack/react-query";
import dynamic from "next/dynamic";
import { Skeleton } from "@/components/ui";
import { api } from "@/lib/api";
import { barSeconds } from "@/lib/aiBacktest";
import { fmtPrice } from "@/lib/format";
import { useAppStore } from "@/lib/store";
import type { Trade } from "@/lib/types";

// slim 蜡烛图动态引入（ssr:false，规避 lightweight-charts 的 SSR 问题，与 WeightBacktestPanel/ChartWorkspace 同范式）。
const TradeCandleChart = dynamic(() => import("./TradeCandleChart"), { ssr: false });

const PAD_BARS = 6; // 窗口两侧各留 6 根 K 线余量，让开 / 平点不贴边（P2-2）。
const GC_MS = 30 * 60_000;

/**
 * 单条完整交易往返展开后的行内 K 线数据层（ai-trade-stats-two-column-and-kline §3.4）：以 (instId,bar,往返窗口)
 * 拉取该时段 K 线，走 loading / error / empty / ok 四态。本 Panel **仅在 row 展开时被挂载**（天然懒加载），
 * 配合不可变 `queryKey` + `staleTime:Infinity` → 重复展开零重拉（P1-4）；拉空 / 出错优雅降级，绝不阻断该行其余信息。
 */
export default function TradeCandleChartPanel({
  instId,
  bar,
  entry,
  exit,
}: {
  instId: string;
  bar: string;
  entry: Trade;
  exit: Trade;
}) {
  const theme = useAppStore((s) => s.theme); // P2-1：主题与 ChartWorkspace/WeightBacktestPanel 同源
  const barSec = barSeconds(bar); // P2-2：未知周期回落 3600
  const span = Math.max(exit.ts - entry.ts, barSec * 4);
  const padSec = barSec * PAD_BARS;
  const startTs = entry.ts - padSec;
  const endTs = exit.ts + padSec;
  const limit = Math.min(1000, Math.ceil(span / barSec) + 2 * PAD_BARS + 4);

  const query = useQuery({
    // P1-4 不可变缓存键：同一往返（instId+bar+开/平 ts）唯一，重复展开命中缓存不重拉。
    queryKey: ["trade-window-candles", instId, bar, entry.ts, exit.ts],
    queryFn: async () => {
      const { candles } = await api.candlesWithMeta(instId, bar, limit, endTs, startTs);
      // 客户端裁剪到 [entry.ts-pad, exit.ts+pad]（history-candles 分页可能多回）。
      return candles.filter((c) => c.ts >= startTs && c.ts <= endTs);
    },
    staleTime: Infinity,
    gcTime: GC_MS,
    retry: false,
  });

  const direction = entry.side === "short" ? "short" : "long";

  if (query.isLoading) {
    return <Skeleton className="h-[240px] w-full rounded-[10px]" />;
  }
  if (query.isError || !query.data || query.data.length === 0) {
    return (
      <p className="rounded-[10px] border border-[var(--border)] bg-[var(--surface-2)] px-3 py-6 text-center text-[11px] leading-relaxed text-[var(--muted)]">
        该时段 K 线暂不可用（历史窗口超出行情保留期或行情源暂不可达）。
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-1.5">
      <div className="rounded-[10px] border border-[var(--border)] bg-[var(--surface)] p-1">
        <TradeCandleChart candles={query.data} trades={[entry, exit]} theme={theme} />
      </div>
      <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 px-0.5 text-[10px] text-[var(--muted)]">
        <span
          className="tabular font-medium"
          style={{ color: direction === "long" ? "var(--long)" : "var(--short)" }}
        >
          {direction === "long" ? "开多" : "开空"}
        </span>
        <span className="tabular">
          开仓 {fmtPrice(entry.price)} → 平仓 {fmtPrice(exit.price)}
        </span>
        <span>· BUY/SELL 为开仓、CLOSE 为平仓，连线示开→平点位</span>
      </div>
    </div>
  );
}
