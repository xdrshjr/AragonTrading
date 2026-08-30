"use client";

import type { ReactNode } from "react";
import { Badge } from "@/components/ui";
import { colorForReturn, fmtPct, fmtPrice, signed } from "@/lib/format";
import { deriveOpenPosition } from "@/lib/position";
import type { Trade } from "@/lib/types";

// 当前持仓卡片（chart-workspace-layout 需求 3）：合约 / 方向 / 持仓量 / 开仓均价 / 标记价 / 浮动盈亏。
// 持仓由真实 trades 纯前端推导（deriveOpenPosition）；空仓诚实显「当前无持仓」，绝不造假。
interface PositionPanelProps {
  trades?: Trade[];
  markPrice?: number | null;
  instId: string;
}

export default function PositionPanel({ trades, markPrice, instId }: PositionPanelProps) {
  const pos = deriveOpenPosition(trades ?? [], markPrice ?? null);

  if (!pos) {
    return (
      <div className="whitespace-normal break-words py-8 text-center text-xs text-[var(--muted)]">
        当前无持仓，运行回测或启动模拟后显示。
      </div>
    );
  }

  const pnlColor = pos.unrealizedPnl != null ? colorForReturn(pos.unrealizedPnl) : "var(--muted)";

  return (
    // 列数由**容器**宽决定，与视口无关（sm: / lg: 跟的是视口宽，而这块面板的宽度由
    // rightPanelWidth / chartSplitRatio / 下方左右分栏共同决定）。每格最少 120px，放不下就少一列。
    <div
      data-nowrap="scope"
      data-testid="position-panel-grid"
      className="grid grid-cols-[repeat(auto-fit,minmax(120px,1fr))] gap-x-6 gap-y-3 whitespace-nowrap text-xs"
    >
      <Cell label="合约">
        <span className="font-medium text-[var(--text)]">{instId}</span>
      </Cell>
      <Cell label="方向">
        <Badge tone={pos.side === "long" ? "long" : "short"}>
          {pos.side === "long" ? "多" : "空"}
        </Badge>
      </Cell>
      <Cell label="持仓量">
        <span className="tabular text-[var(--text)]">{pos.size.toFixed(4)}</span>
      </Cell>
      <Cell label="开仓均价">
        <span className="tabular text-[var(--text)]">{fmtPrice(pos.avgEntry)}</span>
      </Cell>
      <Cell label="标记价">
        <span className="tabular text-[var(--text)]">{fmtPrice(pos.markPrice)}</span>
      </Cell>
      <Cell label="浮动盈亏">
        <span className="tabular font-medium" style={{ color: pnlColor }}>
          {pos.unrealizedPnl != null ? signed(pos.unrealizedPnl) : "—"} / {fmtPct(pos.unrealizedPct)}
        </span>
      </Cell>
    </div>
  );
}

function Cell({ label, children }: { label: string; children: ReactNode }) {
  return (
    // min-w-0：grid item 的 min-width 同样默认 auto，不清零则下面两处 truncate 永不触发。
    <div className="flex min-w-0 flex-col gap-1">
      <span className="truncate text-[10px] text-[var(--muted)]" title={label}>
        {label}
      </span>
      {/* 值由调用方以 children 传入，截断只能在这里统一包一层（6 个调用点零改动）。 */}
      <span className="block min-w-0 truncate">{children}</span>
    </div>
  );
}
