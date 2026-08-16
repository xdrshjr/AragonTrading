"use client";

import { useQuery } from "@tanstack/react-query";
import { type ReactNode, useMemo, useState } from "react";
import { Badge, Button, Modal } from "@/components/ui";
import { fmtPrice } from "@/lib/format";
import {
  LIVE_AUTO_DEFAULT_LEVERAGE,
  LIVE_MAX_ORDER_NOTIONAL_USD,
  clampBudgetToCap,
  deriveDefaultBudget,
  parseOptionalLeverage,
  parseOptionalUsd,
} from "@/lib/liveAuto";
import { tradingApi } from "@/lib/tradingApi";

export interface AutoTradeConfirmOpts {
  capitalUsd: number | null;
  leverage: number | null;
  takeProfitUsd: number | null;
  stopLossUsd: number | null;
}

/**
 * 启动实时自动交易确认弹窗（live-auto-trading §3）：顶级设计、知情同意闸。
 *
 * 全部金额字段可选（留空＝保守默认，对用户可见可核）：展示可用余额 availUsdt 与据此派生的默认
 * 预算（availUsdt×1%）；可填预算 / 止盈金额 / 止损金额；真实资金风险勾选后方可确认（R10）。
 */
export default function AutoTradeConfirmModal({
  open,
  onClose,
  onConfirm,
  submitting,
  acknowledged,
  onAcknowledge,
  instId,
  bar,
  strategyName,
  error,
}: {
  open: boolean;
  onClose: () => void;
  onConfirm: (opts: AutoTradeConfirmOpts) => void;
  submitting: boolean;
  acknowledged: boolean;
  onAcknowledge: (value: boolean) => void;
  instId: string;
  bar: string;
  strategyName: string;
  error?: string | null;
}) {
  const [capitalRaw, setCapitalRaw] = useState("");
  const [leverageRaw, setLeverageRaw] = useState("");
  const [takeProfitRaw, setTakeProfitRaw] = useState("");
  const [stopLossRaw, setStopLossRaw] = useState("");

  const configQ = useQuery({
    queryKey: ["trading", "config", instId, null],
    queryFn: () => tradingApi.config(instId),
    enabled: open,
  });
  const availUsdt = configQ.data?.availUsdt ?? null;
  const defaultBudget = availUsdt != null ? deriveDefaultBudget(availUsdt) : null;

  const capital = parseOptionalUsd(capitalRaw);
  const leverage = parseOptionalLeverage(leverageRaw);
  const takeProfit = parseOptionalUsd(takeProfitRaw);
  const stopLoss = parseOptionalUsd(stopLossRaw);
  const inputError =
    capital.error || leverage.error || takeProfit.error || stopLoss.error || null;

  // 预算预览：填值则按硬上限夹紧预览；留空则展示默认（1%）。
  const previewBudget = useMemo(() => {
    if (availUsdt == null) return null;
    const raw = capital.value ?? deriveDefaultBudget(availUsdt);
    return clampBudgetToCap(raw, availUsdt);
  }, [availUsdt, capital.value]);

  const handleConfirm = () => {
    if (inputError || !acknowledged) return;
    onConfirm({
      capitalUsd: capital.value,
      leverage: leverage.value,
      takeProfitUsd: takeProfit.value,
      stopLossUsd: stopLoss.value,
    });
  };

  return (
    <Modal open={open} onClose={onClose} labelledBy="auto-trade-confirm-title" className="max-w-md">
      <div className="flex items-center justify-between border-b border-[var(--border)] px-5 py-3.5">
        <h2 id="auto-trade-confirm-title" className="text-sm font-semibold text-[var(--text)]">
          启动实时自动交易
        </h2>
        <Badge tone="short">真实资金</Badge>
      </div>

      <div className="flex min-h-0 flex-col gap-px overflow-auto bg-[var(--border)] px-5 py-px">
        <Row label="交易账户" value="设置中心 · 默认实盘账户" />
        <Row label="合约" value={instId} />
        <Row label="周期" value={bar} />
        <Row label="策略" value={strategyName || "—"} />
        <Row
          label="可用余额"
          value={availUsdt != null ? `${fmtPrice(availUsdt)} USDT` : configQ.isError ? "读取失败" : "读取中…"}
        />
        <Row
          label="默认预算（1%）"
          value={defaultBudget != null ? `${fmtPrice(defaultBudget)} USDT` : "—"}
        />
        <Row
          label="生效预算（预览）"
          value={previewBudget != null ? `${fmtPrice(previewBudget)} USDT` : "—"}
        />
        <Row
          label="生效杠杆"
          value={leverage.value != null ? `${leverage.value}×` : `默认 ${LIVE_AUTO_DEFAULT_LEVERAGE}×`}
        />
        <Row label="单笔名义上限" value={`≤ ${LIVE_MAX_ORDER_NOTIONAL_USD} USDT/单`} />
      </div>

      <div className="flex flex-col gap-3 px-5 py-4">
        {/* H8：点明止盈止损为会话累计金额阈值，并说明每笔自带交易所侧 OCO，避免风险敞口误解。 */}
        <p className="rounded-md border border-[var(--border)] bg-[var(--surface-2)] px-2.5 py-2 text-[11px] leading-relaxed text-[var(--muted)]">
          <strong className="text-[var(--text)]">止盈 / 止损为会话累计金额阈值</strong>
          （达到即自动平仓并停止），留空则不按金额触发；每笔交易另自带策略止盈止损（交易所侧 OCO，进程退出后仍兜底）。
        </p>
        {configQ.isError && (
          <p className="text-[11px] leading-relaxed text-[var(--muted)]">
            可用余额读取失败，预算将由服务端按上限夹紧后生效，不影响启动。
          </p>
        )}
        <p className="text-[11px] leading-relaxed text-[var(--muted)]">
          以下均可留空，留空即用保守内置默认；硬上限为可用余额的 5%，超出将被服务端自动夹紧。
        </p>
        <div className="grid grid-cols-2 gap-3">
          <Field label="预算（USDT）" placeholder="留空＝默认 1%" value={capitalRaw}
                 onChange={setCapitalRaw} disabled={submitting} />
          <Field label="杠杆（×）" placeholder="留空＝默认" value={leverageRaw}
                 onChange={setLeverageRaw} disabled={submitting} />
          <Field label="止盈金额（USDT）" placeholder="留空＝不触发" value={takeProfitRaw}
                 onChange={setTakeProfitRaw} disabled={submitting} />
          <Field label="止损金额（USDT）" placeholder="留空＝不触发" value={stopLossRaw}
                 onChange={setStopLossRaw} disabled={submitting} />
        </div>

        <label className="flex items-start gap-2 text-xs leading-relaxed text-[var(--text)]">
          <input
            type="checkbox"
            checked={acknowledged}
            disabled={submitting}
            onChange={(e) => onAcknowledge(e.target.checked)}
            aria-label="我已知晓这是真实资金自动交易"
            className="mt-0.5 h-4 w-4 shrink-0 accent-[var(--accent)]"
          />
          <span>
            我已知晓这是<strong className="text-[var(--short-ink)]">真实资金自动交易</strong>
            ，将按所选策略自动下单 / 平仓，盈亏由本人承担。
          </span>
        </label>

        {(inputError || error) && (
          <p role="alert" className="text-[11px] font-medium text-[var(--short-ink)]">
            {inputError || error}
          </p>
        )}

        <div className="flex items-center justify-end gap-2">
          <Button variant="ghost" size="sm" onClick={onClose} disabled={submitting}>
            取消
          </Button>
          <Button
            variant="primary"
            size="sm"
            onClick={handleConfirm}
            disabled={!acknowledged || !!inputError || submitting}
          >
            {submitting ? "启动中…" : "确认并开始自动交易"}
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

function Field({
  label,
  placeholder,
  value,
  onChange,
  disabled,
}: {
  label: string;
  placeholder: string;
  value: string;
  onChange: (v: string) => void;
  disabled?: boolean;
}) {
  return (
    <label className="flex flex-col gap-1 text-[11px] text-[var(--muted)]">
      <span>{label}</span>
      <input
        type="text"
        inputMode="decimal"
        value={value}
        placeholder={placeholder}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
        className="rounded-md border border-[var(--border)] bg-[var(--bg)] px-2 py-1.5 text-xs text-[var(--text)] outline-none focus:border-[var(--accent)] disabled:opacity-50"
      />
    </label>
  );
}
