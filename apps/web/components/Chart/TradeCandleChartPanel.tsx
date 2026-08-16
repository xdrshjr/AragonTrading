"use client";

import { useQuery } from "@tanstack/react-query";
import dynamic from "next/dynamic";
import { Button, Skeleton } from "@/components/ui";
import { api } from "@/lib/api";
import { barSeconds } from "@/lib/aiBacktest";
import { colorForReturn, fmtPrice, fmtTime, signed } from "@/lib/format";
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
    // E3-a：静态降级文案下补一个用户主动触发的「重试」（保留 retry:false 的自动不重试克制策略）。
    return (
      <div className="flex flex-col items-center gap-2 rounded-[10px] border border-[var(--border)] bg-[var(--surface-2)] px-3 py-6 text-center">
        <p className="text-[11px] leading-relaxed text-[var(--muted)]">
          该时段 K 线暂不可用（历史窗口超出行情保留期或行情源暂不可达）。
        </p>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => query.refetch()}
          disabled={query.isFetching}
        >
          {query.isFetching ? "重试中…" : "重试"}
        </Button>
      </div>
    );
  }

  const candles = query.data;
  // E3-b：极长窗口被行情条数上限（Math.min(1000, …)）截断时，首根 K 线晚于开仓 → 诚实提示只显示临近平仓的一段。
  const truncated = candles.length > 0 && candles[0].ts > entry.ts;
  // 轴 D：K 线窗口边界仍用已对齐的 `ts`（那正是根边界），但**时长与展示时刻**必须读真实墙钟——
  // 用对齐后的 ts 算，1H 周期下一笔 20 分钟往返会显示成「持仓 0 秒」。缺键回退 `ts`。
  const entryWallTs = entry.wallTs ?? entry.ts;
  const exitWallTs = exit.wallTs ?? exit.ts;
  const holdSec = Math.max(0, exitWallTs - entryWallTs);

  return (
    <div className="flex flex-col gap-1.5">
      <div className="rounded-[10px] border border-[var(--border)] bg-[var(--surface)] p-1">
        <TradeCandleChart candles={candles} trades={[entry, exit]} theme={theme} />
      </div>
      {/* E2-b 交易事实条：方向 · 开仓价@时间 · 平仓价@时间 · 持仓时长 · 盈亏（USDT + 收益率），即需求③的「相应的信息」。 */}
      <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 px-0.5 text-[10px] text-[var(--muted)]">
        <span
          className="tabular font-medium"
          style={{ color: direction === "long" ? "var(--long-ink)" : "var(--short-ink)" }}
        >
          {direction === "long" ? "开多" : "开空"}
        </span>
        <span className="tabular">
          开仓 {fmtPrice(entry.price)} @ {fmtTime(entryWallTs)}
        </span>
        <span className="tabular">
          平仓 {fmtPrice(exit.price)} @ {fmtTime(exitWallTs)}
        </span>
        <span className="tabular">持仓 {fmtHoldSpan(holdSec)}</span>
        <span className="tabular font-medium" style={{ color: colorForReturn(exit.pnl) }}>
          盈亏 {signed(exit.pnl)}
        </span>
        {truncated && (
          <span className="text-[var(--muted)]">· 窗口较长，仅显示临近平仓的一段</span>
        )}
      </div>
    </div>
  );
}

// 局部持仓时长格式化（分 / 时 / 天，至多两段）：有意镜像 AutoTradeRoundTripRow.formatHoldDuration，
// 不抽公共函数——集中化会改共享行组件的渲染路径引回归面，trivial 小工具局部复制成本更低（§3.3 R-5）。
function fmtHoldSpan(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  if (s < 60) return `${s}秒`;
  const minutes = Math.floor(s / 60);
  if (minutes < 60) return `${minutes}分`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) {
    const rem = minutes % 60;
    return rem > 0 ? `${hours}时${rem}分` : `${hours}时`;
  }
  const days = Math.floor(hours / 24);
  const remHours = hours % 24;
  return remHours > 0 ? `${days}天${remHours}时` : `${days}天`;
}
