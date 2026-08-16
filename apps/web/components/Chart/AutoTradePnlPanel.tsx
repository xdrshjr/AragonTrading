"use client";

import { useEffect, useMemo, useState } from "react";
import { Modal } from "@/components/ui";
import { colorForReturn, fmtTime, signed } from "@/lib/format";
import {
  type AutoTradeRange,
  type RoundTrip,
  buildLedger,
  filterRoundTrips,
  resolveRangeBounds,
  summarizeRoundTrips,
} from "@/lib/autoTradePnl";
import { useAppStore } from "@/lib/store";
import { TRADES_MAX } from "@/lib/wireCaps";
import AutoTradePnlSummary from "./AutoTradePnlSummary";
import AutoTradeRoundTripRow from "./AutoTradeRoundTripRow";

const TITLE_ID = "auto-trade-pnl-title";

// 自动交易收益记录面板（auto-trade-pnl-records §3.4）：顶部时间维度筛选 + KPI 概览（总收益 / 总收益率）+
// 当前持仓浮盈条 + 完整交易往返明细列表 + 数据覆盖诚实提示 / 空态。瞬态局部 state，不改 store。
export default function AutoTradePnlPanel({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const liveAutoGroup = useAppStore((s) => s.liveAutoGroup);
  const [range, setRange] = useState<AutoTradeRange>("all");
  const [custom, setCustom] = useState<{ startTs?: number; endTs?: number }>({});
  const [openRowKey, setOpenRowKey] = useState<string | null>(null);
  // 短生命周期模态：渲染期取一次 nowSec，每次打开刷新；明确不加定时 tick（v2 P2-6）。
  const [nowSec, setNowSec] = useState(() => Math.floor(Date.now() / 1000));
  useEffect(() => {
    if (open) setNowSec(Math.floor(Date.now() / 1000));
  }, [open]);

  const capitalBaseUsd = liveAutoGroup?.totalBudgetUsd ?? 0;
  // v2（P1-3）当前浮盈：直接 Σ member.position.unrealizedPnl，与时间窗口完全解耦；不用 sessionPnlUsd。
  const unrealizedUsd = useMemo(() => {
    if (!liveAutoGroup) return 0;
    return Object.values(liveAutoGroup.members).reduce(
      (sum, m) => sum + (m.position?.unrealizedPnl ?? 0),
      0,
    );
  }, [liveAutoGroup]);

  const { visible, summary, bounds } = useMemo(() => {
    if (!liveAutoGroup) {
      return { visible: [], summary: summarizeRoundTrips([], 0), bounds: { startTs: 0, endTs: nowSec } };
    }
    const members = Object.values(liveAutoGroup.members).map((m) => ({
      instId: m.instId,
      trades: m.trades,
      leverage: liveAutoGroup.leverage,
    }));
    const b = resolveRangeBounds(range, nowSec, custom);
    const vis = filterRoundTrips(buildLedger(members), b).sort((a, c) => c.closedTs - a.closedTs);
    return { visible: vis, summary: summarizeRoundTrips(vis, capitalBaseUsd), bounds: b };
  }, [liveAutoGroup, range, custom, nowSec, capitalBaseUsd]);

  // D4：`buildLedger` 的入参是 `member.trades`，而那份缓冲有 wire 封顶。触到上限即改口径标注——
  // 早于窗口起点的回合会整段消失，不说出来就是在报一个分母悄悄变过的收益率。
  const tradesCapped = useMemo(
    () => Object.values(liveAutoGroup?.members ?? {}).some((m) => m.trades.length >= TRADES_MAX),
    [liveAutoGroup],
  );

  return (
    <Modal open={open} onClose={onClose} labelledBy={TITLE_ID} className="max-w-2xl">
      <div className="flex items-center justify-between border-b border-[var(--border)] px-5 py-3.5">
        <h2 id={TITLE_ID} className="text-sm font-semibold text-[var(--text)]">
          自动交易收益
        </h2>
        <button
          type="button"
          onClick={onClose}
          aria-label="关闭"
          className="rounded p-1 text-[var(--muted)] transition-colors hover:bg-[var(--surface-2)] hover:text-[var(--text)]"
        >
          ✕
        </button>
      </div>

      <div className="flex max-h-[72vh] flex-col gap-3 overflow-auto px-5 py-4">
        {liveAutoGroup == null ? (
          <EmptyHint text="尚未启动自动交易。启动后这里会按时间维度汇总每一次完整交易（开→平）的收益与总收益率。" />
        ) : (
          <>
            <p className="rounded-md border border-[var(--border)] bg-[var(--surface-2)] px-2.5 py-1.5 text-[10px] leading-relaxed text-[var(--muted)]">
              数据自 <span className="tabular text-[var(--text)]">{fmtTime(liveAutoGroup.startedTs)}</span>{" "}
              起记录（运行期态，整组停止后清空）；时间筛选覆盖本组启动以来，跨会话历史收益将在后续版本提供。
            </p>
            {tradesCapped && (
              <p
                data-testid="auto-trade-pnl-cap-notice"
                className="rounded-md border border-[var(--warn)] bg-[var(--warn-soft)] px-2.5 py-1.5 text-[10px] leading-relaxed text-[var(--warn)]"
              >
                口径：最近 {TRADES_MAX} 笔成交。实时缓冲已达上限，早于该窗口的往返不计入下方汇总。
              </p>
            )}
            <AutoTradePnlSummary
              range={range}
              onRangeChange={setRange}
              custom={custom}
              onCustomChange={setCustom}
              summary={summary}
            />
            <div className="flex items-center justify-between rounded-md border border-[var(--border)] bg-[var(--surface)] px-3 py-2 text-xs">
              <span className="text-[var(--muted)]">
                当前持仓浮盈（未实现，全局）
                <span className="ml-1 text-[10px]">· 与时间窗口无关，不计入上方已实现总收益</span>
              </span>
              <span className="tabular font-semibold" style={{ color: colorForReturn(unrealizedUsd) }}>
                {signed(unrealizedUsd)} USDT
              </span>
            </div>
            {visible.length === 0 ? (
              <WindowEmpty
                earlierThanStart={bounds.endTs < liveAutoGroup.startedTs}
                startedTs={liveAutoGroup.startedTs}
              />
            ) : (
              <div className="overflow-hidden rounded-md border border-[var(--border)] bg-[var(--surface)]">
                {visible.map((rt) => {
                  const key = rowKey(rt);
                  return (
                    <AutoTradeRoundTripRow
                      key={key}
                      rt={rt}
                      open={openRowKey === key}
                      onToggle={() => setOpenRowKey((prev) => (prev === key ? null : key))}
                    />
                  );
                })}
              </div>
            )}
          </>
        )}
      </div>
    </Modal>
  );
}

function WindowEmpty({
  earlierThanStart,
  startedTs,
}: {
  earlierThanStart: boolean;
  startedTs: number;
}) {
  if (earlierThanStart) {
    return (
      <EmptyHint
        text={`所选区间早于本组启动时间（${fmtTime(startedTs)}），暂无记录；跨会话历史收益将在后续版本（基于历史成交）提供。`}
      />
    );
  }
  return (
    <EmptyHint text="当前时间窗口内暂无完整交易（一次完整交易 = 一对开仓→平仓）；持仓平仓后将在此按收益率汇总。" />
  );
}

function EmptyHint({ text }: { text: string }) {
  return (
    <p className="px-3 py-8 text-center text-xs leading-relaxed text-[var(--muted)]">{text}</p>
  );
}

// 稳定行 key：优先成交 id，回退开 / 平时刻，永不跨标的混淆。
function rowKey(rt: RoundTrip): string {
  return `${rt.instId}-${rt.entry.id ?? rt.openedTs}-${rt.exit.id ?? rt.closedTs}`;
}
