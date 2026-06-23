"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import clsx from "clsx";
import { Button, Input, Skeleton } from "@/components/ui";
import { fmtPrice } from "@/lib/format";
import {
  computeCoinQty,
  computeMargin,
  computeNotional,
  maxSizeForBudget,
  snapPrice,
  snapSize,
  tpSlTriggerFromAmount,
  validateOrder,
} from "@/lib/liveOrder";
import { useAppStore } from "@/lib/store";
import { tradingApi } from "@/lib/tradingApi";
import type { OrderDirection, OrderType, PlaceOrderReq, TdMode } from "@/lib/tradingTypes";
import DirectionToggle from "./DirectionToggle";
import LeverageControl from "./LeverageControl";
import OrderConfirmModal, { type OrderConfirmDetails } from "./OrderConfirmModal";
import QuantitySlider from "./QuantitySlider";
import TpSlFields, { type TpSlMode } from "./TpSlFields";

const DEFAULT_LEVER = 3;

// 单侧触发价换算：按价格直取输入；按金额经 liveOrder 换算（多空对称）。无效输入回 null（不下发，§2.4）。
function computeTrigger(
  mode: TpSlMode,
  kind: "tp" | "sl",
  input: string,
  entryPx: number | null,
  sz: number,
  ctVal: number,
  direction: OrderDirection,
): number | null {
  const raw = input.trim();
  if (raw === "") return null;
  const value = Number(raw);
  if (!Number.isFinite(value) || value <= 0) return null;
  if (mode === "price") return value;
  return tpSlTriggerFromAmount(direction, kind, entryPx ?? 0, value, sz, ctVal);
}

/**
 * 实盘下单面板（§2.2）：方向 / 类型 / 价格 / 杠杆 / 数量（滑块＋输入）/ 止盈止损 → 确认弹窗 → 提交。
 *
 * 安全护栏：仅在 `liveTradingEnabled && instrumentSupported && 校验通过` 时可提交；**每单**弹明细确认
 * （C1-3）；提交在途禁用防重（C1-2）；不支持标的 / 开关关闭显式禁用并提示（C1-4）。
 */
export default function LiveTradePanel({
  instId,
  markPrice,
  accountId,
}: {
  instId: string;
  markPrice: number | null;
  accountId?: number;
}) {
  const qc = useQueryClient();
  const liveAcknowledged = useAppStore((s) => s.liveAcknowledged);
  const setLiveAcknowledged = useAppStore((s) => s.setLiveAcknowledged);

  const configQ = useQuery({
    queryKey: ["trading", "config", instId, accountId ?? null],
    queryFn: () => tradingApi.config(instId, accountId),
    refetchInterval: 15_000,
  });

  const [direction, setDirection] = useState<OrderDirection>("long");
  const [ordType, setOrdType] = useState<OrderType>("limit");
  const [tdMode, setTdMode] = useState<TdMode>("cross");
  const [pxInput, setPxInput] = useState("");
  const [lever, setLever] = useState(DEFAULT_LEVER);
  const [sz, setSz] = useState(0);
  const [tpExpanded, setTpExpanded] = useState(false);
  const [tpMode, setTpMode] = useState<TpSlMode>("price");
  const [tpInput, setTpInput] = useState("");
  const [slInput, setSlInput] = useState("");
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitOk, setSubmitOk] = useState<string | null>(null);

  const config = configQ.data;
  const meta = config?.instMeta;
  const lastPx = markPrice ?? config?.lastPx ?? null;
  const pxNum = pxInput.trim() === "" ? null : Number(pxInput);
  const entryPx = ordType === "limit" ? pxNum : lastPx;
  const sizingPx = entryPx ?? lastPx ?? null;

  // 限价单首次自动以最新价预填价格框（用户编辑后不再覆盖；清空可再次预填）。
  useEffect(() => {
    if (ordType === "limit" && pxInput.trim() === "" && lastPx && meta) {
      setPxInput(String(snapPrice(lastPx, meta.tickSz)));
    }
  }, [ordType, pxInput, lastPx, meta]);

  const maxSz = meta ? maxSizeForBudget(config!.availUsdt, lever, meta.ctVal, sizingPx, meta.lotSz) : 0;
  // 预算 / 杠杆收紧导致超额时回夹 sz，避免名义越界（收敛：maxSz 不依赖 sz）。
  useEffect(() => {
    if (maxSz > 0 && sz > maxSz) setSz(maxSz);
  }, [maxSz, sz]);

  const coinQty = meta ? computeCoinQty(sz, meta.ctVal) : 0;
  const notional = meta ? computeNotional(sz, meta.ctVal, sizingPx ?? 0) : 0;
  const margin = computeMargin(notional, lever);
  const tpTriggerPx = meta
    ? computeTrigger(tpMode, "tp", tpInput, entryPx, sz, meta.ctVal, direction)
    : null;
  const slTriggerPx = meta
    ? computeTrigger(tpMode, "sl", slInput, entryPx, sz, meta.ctVal, direction)
    : null;

  const validation = useMemo(
    () =>
      validateOrder({
        ordType,
        px: ordType === "limit" ? pxNum : null,
        sz,
        lever,
        minSz: meta?.minSz ?? 1,
        maxLever: meta?.maxLever ?? 1,
        instrumentSupported: config?.instrumentSupported ?? false,
      }),
    [ordType, pxNum, sz, lever, meta, config?.instrumentSupported],
  );

  if (configQ.isLoading) {
    return (
      <div className="flex flex-col gap-3 p-4">
        <Skeleton className="h-9" />
        <Skeleton className="h-16" />
        <Skeleton className="h-24" />
      </div>
    );
  }
  if (configQ.isError || !config || !meta) {
    return (
      <div className="flex flex-col items-start gap-2 p-4 text-xs text-[var(--short)]">
        <span>加载下单上下文失败：{(configQ.error as Error)?.message || "请稍后重试"}</span>
        <Button variant="ghost" size="sm" onClick={() => void configQ.refetch()}>
          重试
        </Button>
      </div>
    );
  }

  const blockedReason = !config.liveTradingEnabled
    ? "实盘下单未启用（需后端设置 LIVE_TRADING_ENABLED=true），当前仅可查看真实持仓 / 成交。"
    : !config.instrumentSupported
      ? "当前标的非 USDT 本位永续 / 交割合约，暂不支持实盘下单。"
      : null;
  const controlsDisabled = blockedReason !== null || submitting;
  const canSubmit = blockedReason === null && validation.valid && !submitting;

  const confirmDetails: OrderConfirmDetails = {
    instId,
    direction,
    tdMode,
    ordType,
    px: ordType === "limit" ? pxNum : null,
    sz: snapSize(sz, meta.lotSz),
    coinQty,
    ctValCcy: meta.ctValCcy,
    lever,
    notional,
    margin,
    tpTriggerPx,
    slTriggerPx,
  };

  const submit = async () => {
    setSubmitting(true);
    setSubmitError(null);
    setSubmitOk(null);
    const req: PlaceOrderReq = {
      accountId,
      instId,
      direction,
      ordType,
      tdMode,
      px: ordType === "limit" && pxNum ? snapPrice(pxNum, meta.tickSz) : undefined,
      sz: snapSize(sz, meta.lotSz),
      lever,
      tpTriggerPx: tpTriggerPx ?? undefined,
      slTriggerPx: slTriggerPx ?? undefined,
    };
    try {
      const result = await tradingApi.placeOrder(req);
      setSubmitOk(`下单成功 · ordId=${result.ordId ?? "—"}`);
      setConfirmOpen(false);
      void qc.invalidateQueries({ queryKey: ["trading", "positions"] });
      void qc.invalidateQueries({ queryKey: ["trading", "fills"] });
      void qc.invalidateQueries({ queryKey: ["trading", "config"] });
    } catch (e) {
      setSubmitError((e as Error).message || "下单失败");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="flex flex-col gap-3 p-3">
      {blockedReason && (
        <p className="rounded-[10px] border border-[var(--border)] bg-[var(--surface-2)] px-3 py-2 text-[11px] leading-relaxed text-[var(--muted)]">
          {blockedReason}
        </p>
      )}

      <DirectionToggle value={direction} onChange={setDirection} disabled={controlsDisabled} />

      <div className="flex items-center gap-2">
        <SegToggle
          options={[
            { key: "limit", label: "限价" },
            { key: "market", label: "市价" },
          ]}
          value={ordType}
          onChange={(k) => setOrdType(k as OrderType)}
          disabled={controlsDisabled}
        />
        <SegToggle
          options={[
            { key: "cross", label: "全仓" },
            { key: "isolated", label: "逐仓" },
          ]}
          value={tdMode}
          onChange={(k) => setTdMode(k as TdMode)}
          disabled={controlsDisabled}
        />
      </div>

      {ordType === "limit" ? (
        <label className="flex flex-col gap-1.5">
          <span className="text-xs font-medium text-[var(--muted)]">委托价格（USDT）</span>
          <Input
            type="number"
            inputMode="decimal"
            min={0}
            value={pxInput}
            disabled={controlsDisabled}
            invalid={!!validation.errors.px}
            aria-label="委托价格"
            onChange={(e) => setPxInput(e.target.value)}
            className="text-right tabular"
          />
          {validation.errors.px && (
            <span className="text-[11px] text-[var(--short)]">{validation.errors.px}</span>
          )}
        </label>
      ) : (
        <p className="rounded-[10px] bg-[var(--surface-2)] px-3 py-2 text-[11px] text-[var(--muted)]">
          市价单按最新价 {lastPx != null ? fmtPrice(lastPx) : "—"} 估算名义 / 保证金，实际成交含滑点。
        </p>
      )}

      <LeverageControl
        value={lever}
        maxLever={meta.maxLever}
        onChange={setLever}
        disabled={controlsDisabled}
      />

      <QuantitySlider
        sz={sz}
        maxSz={maxSz}
        lotSz={meta.lotSz}
        ctValCcy={meta.ctValCcy}
        coinQty={coinQty}
        notional={notional}
        margin={margin}
        onChange={setSz}
        disabled={controlsDisabled}
      />
      {validation.errors.sz && (
        <span className="text-[11px] text-[var(--short)]">{validation.errors.sz}</span>
      )}

      <TpSlFields
        expanded={tpExpanded}
        onToggleExpanded={() => setTpExpanded((v) => !v)}
        mode={tpMode}
        onModeChange={setTpMode}
        tpInput={tpInput}
        slInput={slInput}
        onTpInput={setTpInput}
        onSlInput={setSlInput}
        tpTriggerPx={tpTriggerPx}
        slTriggerPx={slTriggerPx}
        disabled={controlsDisabled}
      />

      <Button
        variant="primary"
        onClick={() => {
          // 开启新一单的确认流时清除上一单的成功 / 失败行内反馈，避免真实下单时
          // 残留「下单成功」横幅与新单状态并存而误导（real-money UI 安全）。
          setSubmitError(null);
          setSubmitOk(null);
          setConfirmOpen(true);
        }}
        disabled={!canSubmit}
        className={clsx(
          "w-full",
          direction === "long"
            ? "!bg-[var(--long)]"
            : "!bg-[var(--short)]",
          !canSubmit && "!bg-[var(--surface-2)]",
        )}
      >
        {direction === "long" ? "买入开多" : "卖出开空"}
      </Button>

      {submitOk && (
        <p role="status" className="text-[11px] font-medium text-[var(--long)]">
          {submitOk}
        </p>
      )}

      <OrderConfirmModal
        open={confirmOpen}
        onClose={() => setConfirmOpen(false)}
        onConfirm={submit}
        submitting={submitting}
        acknowledged={liveAcknowledged}
        onAcknowledge={setLiveAcknowledged}
        details={confirmDetails}
        error={submitError}
      />
    </div>
  );
}

function SegToggle({
  options,
  value,
  onChange,
  disabled,
}: {
  options: { key: string; label: string }[];
  value: string;
  onChange: (key: string) => void;
  disabled?: boolean;
}) {
  return (
    <div className="flex flex-1 gap-1 rounded-[10px] bg-[var(--surface-2)] p-1">
      {options.map((opt) => (
        <button
          key={opt.key}
          type="button"
          disabled={disabled}
          onClick={() => onChange(opt.key)}
          className={clsx(
            "flex-1 rounded-[7px] px-2 py-1 text-[11px] font-medium transition-all duration-150",
            "disabled:cursor-not-allowed disabled:opacity-40",
            value === opt.key
              ? "bg-[var(--surface)] text-[var(--text)] shadow-soft"
              : "text-[var(--muted)] hover:text-[var(--text)]",
          )}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}
