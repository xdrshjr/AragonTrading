"use client";

import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import clsx from "clsx";
import type { ReactNode } from "react";
import { Badge, Button, Input, Modal } from "@/components/ui";
import { colorForReturn, fmtNum, fmtPrice, signed } from "@/lib/format";
import { defaultCloseSize, estimateRealizedPnl, validateCloseSize } from "@/lib/closePosition";
import { snapPrice, snapSize } from "@/lib/liveOrder";
import { useAppStore } from "@/lib/store";
import { tradingApi } from "@/lib/tradingApi";
import type { ClosePositionReq, LivePosition, OrderType, TdMode } from "@/lib/tradingTypes";

/**
 * 单仓平仓弹窗（平仓 spec §2.6）：限价（挂单平仓）/ 市价（整仓）两段，实时预览实现盈亏 / 名义 / 剩余持仓。
 *
 * 该弹窗自身即为平仓确认（提交在途禁用防重）；复用既有实盘下单的确认范式（Modal + acknowledge 结构、
 * `useAppStore.liveAcknowledged` 心智模型，P2-5）。方向安全由后端二次校验（`sz≤|pos|`、posSide/reduceOnly）。
 * 尾仓 `|pos|<minSz`（dust）时禁用限价、引导市价整仓退出（P1-2）。元数据按**该行 instId** 取（R9）。
 */
export default function ClosePositionModal({
  open,
  onClose,
  pos,
  accountId,
  onClosed,
}: {
  open: boolean;
  onClose: () => void;
  pos: LivePosition;
  accountId?: number;
  onClosed: () => void;
}) {
  const liveAcknowledged = useAppStore((s) => s.liveAcknowledged);
  const setLiveAcknowledged = useAppStore((s) => s.setLiveAcknowledged);

  // 复用 LiveTradePanel 的 config 查询键（P2-6），命中 React Query 去重；按该行 instId 取元数据（R9）。
  const configQ = useQuery({
    queryKey: ["trading", "config", pos.instId, accountId ?? null],
    queryFn: () => tradingApi.config(pos.instId, accountId),
    enabled: open,
  });

  const [ordType, setOrdType] = useState<OrderType>("limit");
  const [pxInput, setPxInput] = useState("");
  const [szInput, setSzInput] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [okMsg, setOkMsg] = useState<string | null>(null);

  const config = configQ.data;
  const meta = config?.instMeta;
  const absPos = Math.abs(pos.pos ?? 0);
  const isLong = pos.posSide === "long" || (pos.posSide === "net" && (pos.pos ?? 0) > 0);
  const markPx = pos.last ?? config?.lastPx ?? null;
  const isDust = meta ? absPos < meta.minSz : false;

  // 限价首次以标记价预填价格（用户编辑后不覆盖；清空可再预填）。
  useEffect(() => {
    if (open && ordType === "limit" && pxInput.trim() === "" && markPx && meta) {
      setPxInput(String(snapPrice(markPx, meta.tickSz)));
    }
  }, [open, ordType, pxInput, markPx, meta]);

  // 数量默认全仓 |pos|（首次 / 清空时）。
  useEffect(() => {
    if (open && szInput.trim() === "" && meta && absPos > 0) {
      setSzInput(String(defaultCloseSize(absPos, meta.lotSz)));
    }
  }, [open, szInput, meta, absPos]);

  // 尾仓不足 minSz 时强制切到市价（限价必被 minSz 拦截，P1-2）。
  useEffect(() => {
    if (isDust && ordType === "limit") setOrdType("market");
  }, [isDust, ordType]);

  const pxNum = pxInput.trim() === "" ? null : Number(pxInput);
  const szNum = ordType === "market" ? absPos : szInput.trim() === "" ? 0 : Number(szInput);
  const closePx = (ordType === "limit" ? pxNum : markPx) ?? markPx ?? 0;

  const szValidation = useMemo(() => {
    if (ordType === "market") return { valid: true as const, error: undefined };
    return validateCloseSize(szNum, pos.pos ?? 0, meta?.minSz ?? 1, meta?.lotSz ?? 1);
  }, [ordType, szNum, pos.pos, meta]);

  const pxValid = ordType === "market" || (pxNum !== null && pxNum > 0);
  const realizedPnl = meta ? estimateRealizedPnl(isLong, pos.avgPx ?? 0, closePx, szNum, meta.ctVal) : 0;
  const notional = meta ? szNum * meta.ctVal * closePx : 0;
  const remaining = Math.max(0, absPos - szNum);

  const blockedReason =
    config && !config.liveTradingEnabled
      ? "实盘平仓未启用（需后端设置 LIVE_TRADING_ENABLED=true）。"
      : null;
  const controlsDisabled = blockedReason !== null || submitting || okMsg !== null;
  const canSubmit =
    blockedReason === null &&
    liveAcknowledged &&
    pxValid &&
    szValidation.valid &&
    szNum > 0 &&
    !!meta &&
    !submitting;

  const submit = async () => {
    if (!meta) return;
    setSubmitting(true);
    setError(null);
    setOkMsg(null);
    const req: ClosePositionReq = {
      accountId,
      instId: pos.instId,
      posSide: (pos.posSide || "net") as ClosePositionReq["posSide"],
      pos: pos.pos ?? 0,
      mgnMode: (pos.marginMode || "cross") as TdMode,
      ordType,
      px: ordType === "limit" && pxNum ? snapPrice(pxNum, meta.tickSz) : undefined,
      sz: ordType === "limit" ? snapSize(szNum, meta.lotSz) : undefined,
    };
    try {
      const result = await tradingApi.closePosition(req);
      const n = result.closedSz ?? snapSize(szNum, meta.lotSz);
      // 限价为「委托」平仓量（挂单未必即时成交），文案明确以免误读为已平（P2-2）。
      setOkMsg(
        ordType === "limit"
          ? `已提交挂单平仓 ${fmtNum(n, 0)} 张（挂单未必即时成交）`
          : `已提交市价平仓 ${fmtNum(n, 0)} 张`,
      );
      onClosed();
    } catch (e) {
      setError((e as Error).message || "平仓失败");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal open={open} onClose={onClose} labelledBy="close-position-title" className="max-w-md">
      <div className="flex items-center justify-between border-b border-[var(--border)] px-5 py-3.5">
        <h2 id="close-position-title" className="text-sm font-semibold text-[var(--text)]">
          平仓 · {pos.instId}
        </h2>
        <Badge tone={isLong ? "long" : "short"}>{isLong ? "平多" : "平空"}</Badge>
      </div>

      <div className="flex min-h-0 flex-col gap-3 overflow-auto px-5 py-4">
        {blockedReason && (
          <p className="rounded-[10px] border border-[var(--border)] bg-[var(--surface-2)] px-3 py-2 text-[11px] leading-relaxed text-[var(--muted)]">
            {blockedReason}
          </p>
        )}

        <div className="flex flex-1 gap-1 rounded-[10px] bg-[var(--surface-2)] p-1">
          {(
            [
              { key: "limit", label: "限价（挂单平仓）" },
              { key: "market", label: "市价（整仓）" },
            ] as const
          ).map((opt) => (
            <button
              key={opt.key}
              type="button"
              disabled={controlsDisabled || (opt.key === "limit" && isDust)}
              onClick={() => setOrdType(opt.key)}
              className={clsx(
                "flex-1 rounded-[7px] px-2 py-1 text-[11px] font-medium transition-all duration-150",
                "disabled:cursor-not-allowed disabled:opacity-40",
                ordType === opt.key
                  ? "bg-[var(--surface)] text-[var(--text)] shadow-soft"
                  : "text-[var(--muted)] hover:text-[var(--text)]",
              )}
            >
              {opt.label}
            </button>
          ))}
        </div>

        {isDust && (
          <p className="rounded-[10px] bg-[var(--surface-2)] px-3 py-2 text-[11px] leading-relaxed text-[var(--muted)]">
            当前剩余持仓 {fmtNum(absPos, 0)} 张不足最小下单量 {fmtNum(meta?.minSz ?? 0, 0)} 张，限价平仓会被拒；
            请使用「市价（整仓）」一次性平掉残量。
          </p>
        )}

        {ordType === "limit" && (
          <label className="flex flex-col gap-1.5">
            <span className="text-xs font-medium text-[var(--muted)]">委托价格（USDT）</span>
            <Input
              type="number"
              inputMode="decimal"
              min={0}
              value={pxInput}
              disabled={controlsDisabled}
              invalid={!pxValid}
              aria-label="委托价格"
              onChange={(e) => setPxInput(e.target.value)}
              className="text-right tabular"
            />
          </label>
        )}

        <label className="flex flex-col gap-1.5">
          <span className="text-xs font-medium text-[var(--muted)]">平仓数量（张）</span>
          <Input
            type="number"
            inputMode="decimal"
            min={0}
            value={ordType === "market" ? String(absPos) : szInput}
            disabled={controlsDisabled || ordType === "market"}
            invalid={ordType === "limit" && !szValidation.valid}
            aria-label="平仓数量（张）"
            onChange={(e) => setSzInput(e.target.value)}
            className="text-right tabular"
          />
          {ordType === "limit" && !szValidation.valid && szValidation.error && (
            <span className="text-[11px] text-[var(--short)]">{szValidation.error}</span>
          )}
          {ordType === "limit" && (
            <button
              type="button"
              disabled={controlsDisabled}
              onClick={() => meta && setSzInput(String(defaultCloseSize(absPos, meta.lotSz)))}
              className="self-end text-[11px] text-[var(--accent)] disabled:opacity-40"
            >
              全仓 {fmtNum(absPos, 0)} 张
            </button>
          )}
        </label>

        {ordType === "market" && (
          <p className="rounded-[10px] bg-[var(--surface-2)] px-3 py-2 text-[11px] leading-relaxed text-[var(--muted)]">
            市价平仓将平掉该持仓全部 {fmtNum(absPos, 0)} 张，按当前盘口成交、可能有滑点，并自动撤销该合约未成交挂单。
          </p>
        )}

        <div className="flex flex-col gap-px overflow-hidden rounded-[10px] bg-[var(--border)]">
          <Row label="开仓均价" value={`${fmtPrice(pos.avgPx)} USDT`} />
          <Row label="标记价" value={`${fmtPrice(markPx)} USDT`} />
          <Row
            label="预计实现盈亏"
            value={
              <span style={{ color: colorForReturn(realizedPnl) }}>{signed(realizedPnl)} USDT</span>
            }
          />
          <Row label="名义价值" value={`${fmtPrice(notional)} USDT`} />
          <Row label="剩余持仓" value={`${fmtNum(remaining, 0)} 张`} />
        </div>

        <label className="flex items-start gap-2 text-xs leading-relaxed text-[var(--text)]">
          <input
            type="checkbox"
            checked={liveAcknowledged}
            disabled={controlsDisabled}
            onChange={(e) => setLiveAcknowledged(e.target.checked)}
            aria-label="我已知晓这是真实平仓"
            className="mt-0.5 h-4 w-4 shrink-0 accent-[var(--accent)]"
          />
          <span>
            我已知晓这是<strong className="text-[var(--short)]">真实资金平仓</strong>
            ，将立即提交至 OKX 实盘账户，盈亏由本人承担。
          </span>
        </label>

        {error && (
          <p role="alert" className="text-[11px] font-medium text-[var(--short)]">
            {error}
          </p>
        )}
        {okMsg && (
          <p role="status" className="text-[11px] font-medium text-[var(--long)]">
            {okMsg}
          </p>
        )}

        <div className="flex items-center justify-end gap-2">
          <Button variant="ghost" size="sm" onClick={onClose} disabled={submitting}>
            {okMsg ? "完成" : "取消"}
          </Button>
          {!okMsg && (
            <Button variant="primary" size="sm" onClick={submit} disabled={!canSubmit}>
              {submitting ? "提交中…" : ordType === "market" ? "确认市价平仓" : "确认挂单平仓"}
            </Button>
          )}
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
