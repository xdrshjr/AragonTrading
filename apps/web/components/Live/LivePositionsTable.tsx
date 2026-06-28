"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Badge, Button } from "@/components/ui";
import { colorForReturn, fmtNum, fmtPct, fmtPrice, signed } from "@/lib/format";
import {
  groupOrdersByPosition,
  openOrdersKey,
  positionKey,
} from "@/lib/openOrders";
import { tradingApi } from "@/lib/tradingApi";
import type { LiveOpenOrder, LivePosition } from "@/lib/tradingTypes";
import ClosePositionModal from "./ClosePositionModal";
import MarketCloseAllConfirm from "./MarketCloseAllConfirm";
import PositionOrdersRow from "./PositionOrdersRow";

// 实盘持仓表（§4.4）：5s 轮询真实 OKX；鉴权失败显式错误态，绝不假数据（C1）。
// 行内「平仓」（弹窗挂单 / 市价整仓）与表头「一键市价全平」二次确认（平仓 spec §2.6）；受 liveTradingEnabled 门控。
export default function LivePositionsTable({
  instId,
  accountId,
}: {
  instId?: string;
  accountId?: number;
}) {
  const qc = useQueryClient();
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["trading", "positions", accountId ?? null],
    queryFn: () => tradingApi.positions(accountId),
    refetchInterval: 5_000,
    refetchOnWindowFocus: true,
  });

  // 复用 LiveTradePanel 的 config 查询键（P2-6）读全局 liveTradingEnabled（账户级布尔，与具体 instId 无关）。
  const configQ = useQuery({
    queryKey: ["trading", "config", instId, accountId ?? null],
    queryFn: () => tradingApi.config(instId!, accountId),
    enabled: !!instId,
    refetchInterval: 15_000,
  });
  const liveEnabled = configQ.data?.liveTradingEnabled ?? false;

  // 未结委托（与图表 ChartWorkspace 共用 openOrdersKey 同源去重，C2）：单独失败不阻断持仓表（C6）。
  const openOrdersQ = useQuery({
    queryKey: openOrdersKey(accountId),
    queryFn: () => tradingApi.openOrders(accountId),
    refetchInterval: 5_000,
    refetchOnWindowFocus: true,
  });

  const [closeTarget, setCloseTarget] = useState<LivePosition | null>(null);
  const [closeAllOpen, setCloseAllOpen] = useState(false);
  // 折叠的持仓键集合（默认全展开：不在集合内即展开，新持仓天然展开）。
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const toggleCollapsed = (key: string) =>
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  // 平仓 / 下单成功后刷新持仓 / 成交 / 可用额 / 未结委托四键（5s 轮询亦兜底，R7）。
  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ["trading", "positions"] });
    void qc.invalidateQueries({ queryKey: ["trading", "fills"] });
    void qc.invalidateQueries({ queryKey: ["trading", "config"] });
    void qc.invalidateQueries({ queryKey: ["trading", "openOrders"] });
  };

  if (isLoading) {
    return <div className="py-8 text-center text-xs text-[var(--muted)]">加载持仓…</div>;
  }
  if (isError) {
    return (
      <div className="py-8 text-center text-xs text-[var(--short)]">
        加载持仓失败：{(error as Error)?.message || "请稍后重试"}
      </div>
    );
  }
  const rows = data?.positions ?? [];
  const open = rows.filter((p) => (p.pos ?? 0) !== 0);
  if (open.length === 0) {
    return <div className="py-8 text-center text-xs text-[var(--muted)]">当前实盘账户无持仓。</div>;
  }

  // 委托查询失败（502）时退化为空 → 子行显示「暂无」，不阻断持仓主行（C6）。
  const grouped = groupOrdersByPosition(openOrdersQ.data?.orders ?? [], open);
  const gateHint = liveEnabled ? undefined : "实盘平仓未启用（需后端 LIVE_TRADING_ENABLED=true）";

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-end">
        <Button
          variant="danger"
          size="sm"
          onClick={() => setCloseAllOpen(true)}
          disabled={!liveEnabled}
          title={gateHint}
        >
          一键市价全平
        </Button>
      </div>

      <table className="w-full text-xs">
        <thead className="sticky top-0 bg-[var(--surface)] text-[var(--muted)]">
          <tr className="text-left">
            <th className="py-1.5 pr-2 font-medium">合约</th>
            <th className="py-1.5 pr-2 font-medium">方向</th>
            <th className="py-1.5 pr-2 text-right font-medium">持仓</th>
            <th className="py-1.5 pr-2 text-right font-medium">开仓均价</th>
            <th className="py-1.5 pr-2 text-right font-medium">标记价</th>
            <th className="py-1.5 pr-2 text-right font-medium">浮动盈亏</th>
            <th className="py-1.5 text-right font-medium">操作</th>
          </tr>
        </thead>
        <tbody>
          {open.map((p, i) => {
            const key = positionKey(p.instId, p.posSide);
            return (
              <PositionRow
                key={`${p.instId}-${p.posSide}-${i}`}
                pos={p}
                orders={grouped.get(key) ?? []}
                expanded={!collapsed.has(key)}
                onToggle={() => toggleCollapsed(key)}
                liveEnabled={liveEnabled}
                gateHint={gateHint}
                onClose={() => setCloseTarget(p)}
              />
            );
          })}
        </tbody>
      </table>

      {closeTarget && (
        <ClosePositionModal
          open
          pos={closeTarget}
          accountId={accountId}
          onClose={() => setCloseTarget(null)}
          onClosed={invalidate}
        />
      )}
      {closeAllOpen && (
        <MarketCloseAllConfirm
          open
          positions={open}
          accountId={accountId}
          onClose={() => setCloseAllOpen(false)}
          onClosed={invalidate}
        />
      )}
    </div>
  );
}

function PositionRow({
  pos,
  orders,
  expanded,
  onToggle,
  liveEnabled,
  gateHint,
  onClose,
}: {
  pos: LivePosition;
  orders: LiveOpenOrder[];
  expanded: boolean;
  onToggle: () => void;
  liveEnabled: boolean;
  gateHint?: string;
  onClose: () => void;
}) {
  const isLong = pos.posSide === "long" || (pos.posSide === "net" && (pos.pos ?? 0) > 0);
  return (
    <>
      <tr className="border-t border-[var(--border)]">
        <td className="py-1.5 pr-2 font-medium text-[var(--text)]">
          <span className="inline-flex items-center gap-1">
            <CollapseToggle expanded={expanded} onToggle={onToggle} instId={pos.instId} />
            {pos.instId}
          </span>
        </td>
        <td className="py-1.5 pr-2">
          <Badge tone={isLong ? "long" : "short"}>{isLong ? "多" : "空"}</Badge>
        </td>
        <td className="py-1.5 pr-2 text-right tabular">
          {fmtNum(pos.pos, 0)}
          {pos.lever != null ? (
            <span className="ml-1 text-[10px] text-[var(--muted)]">{pos.lever}×</span>
          ) : null}
        </td>
        <td className="py-1.5 pr-2 text-right tabular">{fmtPrice(pos.avgPx)}</td>
        <td className="py-1.5 pr-2 text-right tabular">{fmtPrice(pos.last)}</td>
        <td
          className="py-1.5 pr-2 text-right tabular font-medium"
          style={{ color: colorForReturn(pos.upl ?? 0) }}
        >
          {pos.upl != null ? signed(pos.upl) : "—"} / {fmtPct(pos.uplRatio)}
        </td>
        <td className="py-1.5 text-right">
          <Button
            variant="ghost"
            size="sm"
            onClick={onClose}
            disabled={!liveEnabled}
            title={gateHint}
            aria-label={`平仓 ${pos.instId}`}
          >
            平仓
          </Button>
        </td>
      </tr>
      {expanded && (
        <tr>
          <td colSpan={7} className="bg-[var(--surface-2)]/40 px-2 pb-2">
            <PositionOrdersRow orders={orders} refPx={pos.last ?? null} />
          </td>
        </tr>
      )}
    </>
  );
}

// 折叠箭头：展开时旋转 90°；可访问性以 aria-expanded 暴露状态（默认展开）。
function CollapseToggle({
  expanded,
  onToggle,
  instId,
}: {
  expanded: boolean;
  onToggle: () => void;
  instId: string;
}) {
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-expanded={expanded}
      aria-label={expanded ? `折叠 ${instId} 委托明细` : `展开 ${instId} 委托明细`}
      className="inline-flex h-4 w-4 items-center justify-center rounded text-[var(--muted)] transition-colors hover:text-[var(--text)]"
    >
      <svg
        width="8"
        height="8"
        viewBox="0 0 8 8"
        className="transition-transform duration-150 ease-smooth"
        style={{ transform: expanded ? "rotate(90deg)" : "none" }}
        aria-hidden="true"
      >
        <path d="M2 1 L6 4 L2 7 Z" fill="currentColor" />
      </svg>
    </button>
  );
}
