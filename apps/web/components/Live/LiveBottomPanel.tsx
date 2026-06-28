"use client";

import { useState } from "react";
import { Tabs } from "@/components/ui";
import LiveFillsTable from "./LiveFillsTable";
import LivePositionsTable from "./LivePositionsTable";
import LiveTradePanel from "./LiveTradePanel";

const RIGHT_TABS = [
  { key: "positions", label: "当前持仓" },
  { key: "trades", label: "成交记录" },
];

type RightTab = "positions" | "trades";

/**
 * 实盘下方面板（需求 · 切到实盘后下方左右分栏）：
 * - 左栏：下单面板 `<LiveTradePanel>`（输入价格 / 杠杆 / 数量 / 止盈止损，买多 / 买空、市价）。
 * - 右栏：成交记录 / 当前持仓页签，数据源为该实盘账户真实 OKX（轮询，C1 绝不假数据）。
 */
export default function LiveBottomPanel({
  instId,
  markPrice,
  accountId,
}: {
  instId: string;
  markPrice: number | null;
  accountId?: number;
}) {
  const [rightTab, setRightTab] = useState<RightTab>("positions");

  return (
    <div className="flex h-full min-h-0 min-w-0">
      <div className="flex w-2/5 min-w-[300px] max-w-[460px] shrink-0 flex-col overflow-y-auto border-r border-[var(--border)]">
        <LiveTradePanel instId={instId} markPrice={markPrice} accountId={accountId} />
      </div>

      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <div className="shrink-0 px-3 pt-2">
          <Tabs
            variant="underline"
            tabs={RIGHT_TABS}
            active={rightTab}
            onChange={(k) => setRightTab(k as RightTab)}
          />
        </div>
        <div className="min-h-0 flex-1 overflow-auto px-3 py-2">
          {rightTab === "trades" ? (
            <LiveFillsTable instId={instId} accountId={accountId} />
          ) : (
            <LivePositionsTable instId={instId} accountId={accountId} />
          )}
        </div>
      </div>
    </div>
  );
}
