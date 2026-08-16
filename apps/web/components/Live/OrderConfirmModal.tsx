"use client";

import type { ReactNode } from "react";
import { Badge, Button, Modal } from "@/components/ui";
import { fmtNum, fmtPrice } from "@/lib/format";
import type { OrderDirection, OrderType, TdMode } from "@/lib/tradingTypes";

export interface OrderConfirmDetails {
  instId: string;
  direction: OrderDirection;
  tdMode: TdMode;
  ordType: OrderType;
  px: number | null; // 市价单为 null
  sz: number; // 张
  coinQty: number;
  ctValCcy: string;
  lever: number;
  notional: number;
  margin: number;
  tpTriggerPx: number | null;
  slTriggerPx: number | null;
}

// 真实下单确认弹窗（C1-3）：**每一单**都弹出、逐项明细复核不可跳过；
// 「我已知晓这是真实下单」勾选首单强制，之后会话内记忆（仅记忆勾选，不跳过本弹窗）。
export default function OrderConfirmModal({
  open,
  onClose,
  onConfirm,
  submitting,
  acknowledged,
  onAcknowledge,
  details,
  error,
}: {
  open: boolean;
  onClose: () => void;
  onConfirm: () => void;
  submitting: boolean;
  acknowledged: boolean;
  onAcknowledge: (value: boolean) => void;
  details: OrderConfirmDetails;
  error?: string | null;
}) {
  const isLong = details.direction === "long";
  return (
    <Modal open={open} onClose={onClose} labelledBy="order-confirm-title" className="max-w-md">
      <div className="flex items-center justify-between border-b border-[var(--border)] px-5 py-3.5">
        <h2 id="order-confirm-title" className="text-sm font-semibold text-[var(--text)]">
          确认真实下单
        </h2>
        <Badge tone={isLong ? "long" : "short"}>{isLong ? "买多 · 开多" : "买空 · 开空"}</Badge>
      </div>

      <div className="flex min-h-0 flex-col gap-px overflow-auto bg-[var(--border)] px-5 py-px">
        <Row label="合约" value={details.instId} />
        <Row label="订单类型" value={details.ordType === "limit" ? "限价单" : "市价单"} />
        <Row label="保证金模式" value={details.tdMode === "cross" ? "全仓 (cross)" : "逐仓 (isolated)"} />
        <Row
          label="委托价格"
          value={
            details.ordType === "market"
              ? "市价（按最新价估算，含滑点）"
              : `${fmtPrice(details.px)} USDT`
          }
        />
        <Row label="数量" value={`${fmtNum(details.sz, 0)} 张 ≈ ${fmtNum(details.coinQty, 4)} ${details.ctValCcy}`} />
        <Row label="杠杆" value={`${details.lever}×`} />
        <Row label="名义价值" value={`${fmtPrice(details.notional)} USDT`} />
        <Row label="所需保证金" value={`${fmtPrice(details.margin)} USDT`} />
        <Row
          label="止盈触发价"
          value={details.tpTriggerPx != null ? `${fmtPrice(details.tpTriggerPx)} USDT` : "未设置"}
        />
        <Row
          label="止损触发价"
          value={details.slTriggerPx != null ? `${fmtPrice(details.slTriggerPx)} USDT` : "未设置"}
        />
      </div>

      <div className="flex flex-col gap-3 px-5 py-4">
        <label className="flex items-start gap-2 text-xs leading-relaxed text-[var(--text)]">
          <input
            type="checkbox"
            checked={acknowledged}
            disabled={submitting}
            onChange={(e) => onAcknowledge(e.target.checked)}
            aria-label="我已知晓这是真实下单"
            className="mt-0.5 h-4 w-4 shrink-0 accent-[var(--accent)]"
          />
          <span>
            我已知晓这是<strong className="text-[var(--short-ink)]">真实资金下单</strong>
            ，将立即提交至 OKX 实盘账户，盈亏由本人承担。
          </span>
        </label>

        {error && (
          <p role="alert" className="text-[11px] font-medium text-[var(--short-ink)]">
            {error}
          </p>
        )}

        <div className="flex items-center justify-end gap-2">
          <Button variant="ghost" size="sm" onClick={onClose} disabled={submitting}>
            取消
          </Button>
          <Button
            variant="primary"
            size="sm"
            onClick={onConfirm}
            disabled={!acknowledged || submitting}
          >
            {submitting ? "提交中…" : "确认下单"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}

function Row({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 bg-[var(--bg)] px-3 py-2 text-xs">
      <span className="text-[var(--muted)]">{label}</span>
      <span className="tabular text-right font-medium text-[var(--text)]">{value}</span>
    </div>
  );
}
