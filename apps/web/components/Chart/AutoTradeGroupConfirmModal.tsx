"use client";

import { useQuery } from "@tanstack/react-query";
import { type ReactNode, useEffect, useMemo, useState } from "react";
import { Badge, Button, Modal, Select } from "@/components/ui";
import { api } from "@/lib/api";
import {
  clampGroupBudgetToCap,
  deriveGroupDefaultBudget,
  groupBudgetCap,
  isAutoTradable,
  isGroupBudgetClamped,
  memberBudget,
  totalAllocation,
} from "@/lib/autoTrade";
import { fmtPrice } from "@/lib/format";
import {
  LIVE_AUTO_DEFAULT_LEVERAGE,
  LIVE_MAX_ORDER_NOTIONAL_USD,
  parseOptionalLeverage,
  parseOptionalUsd,
} from "@/lib/liveAuto";
import type { AutoTradeMember } from "@/lib/liveAutoTypes";
import { tradingApi } from "@/lib/tradingApi";

export interface AutoTradeGroupConfirmOpts {
  strategyId: number;
  totalCapitalUsd: number | null;
  leverage: number | null;
  takeProfitUsd: number | null;
  stopLossUsd: number | null;
}

/**
 * 启动多标的自动交易确认弹窗（watchlist-auto-trade-group §2.7）：策略下拉（自带，解耦策略面板，P2-5）+
 * 周期 + 总预算 / 杠杆 / 总止盈 / 总止损 + 成员清单与各自百分比及每标的预算预览 + 真实资金风险勾选。
 */
export default function AutoTradeGroupConfirmModal({
  open,
  onClose,
  onConfirm,
  submitting,
  acknowledged,
  onAcknowledge,
  members,
  bar,
  defaultStrategyId,
  error,
}: {
  open: boolean;
  onClose: () => void;
  onConfirm: (opts: AutoTradeGroupConfirmOpts) => void;
  submitting: boolean;
  acknowledged: boolean;
  onAcknowledge: (value: boolean) => void;
  members: AutoTradeMember[];
  bar: string;
  defaultStrategyId: number | null;
  error?: string | null;
}) {
  const [strategyId, setStrategyId] = useState<number | null>(defaultStrategyId);
  const [capitalRaw, setCapitalRaw] = useState("");
  const [leverageRaw, setLeverageRaw] = useState("");
  const [takeProfitRaw, setTakeProfitRaw] = useState("");
  const [stopLossRaw, setStopLossRaw] = useState("");

  const strategiesQ = useQuery({ queryKey: ["strategies"], queryFn: api.strategies });
  const firstInst = members[0]?.instId;
  const configQ = useQuery({
    queryKey: ["trading", "config", firstInst, null],
    queryFn: () => tradingApi.config(firstInst as string),
    enabled: open && !!firstInst,
  });
  const availUsdt = configQ.data?.availUsdt ?? null;

  // 弹窗每次打开：清空金额输入并把策略选择回落默认（当前激活策略优先）。
  useEffect(() => {
    if (!open) return;
    setStrategyId(defaultStrategyId);
    setCapitalRaw("");
    setLeverageRaw("");
    setTakeProfitRaw("");
    setStopLossRaw("");
  }, [open, defaultStrategyId]);

  // 策略列表迟到解析（冷缓存）时仅补默认策略，**绝不**重置用户此间已输入的预算 / 杠杆 / 止盈止损。
  useEffect(() => {
    if (!open) return;
    setStrategyId((prev) => prev ?? strategiesQ.data?.[0]?.id ?? null);
  }, [open, strategiesQ.data]);

  const capital = parseOptionalUsd(capitalRaw);
  const leverage = parseOptionalLeverage(leverageRaw);
  const takeProfit = parseOptionalUsd(takeProfitRaw);
  const stopLoss = parseOptionalUsd(stopLossRaw);
  const inputError = capital.error || leverage.error || takeProfit.error || stopLoss.error || null;
  const sumPct = totalAllocation(members);
  const usingDefault = capital.value == null;

  // 生效总预算预览：填值按硬上限夹紧；留空展示随成员数缩放的默认（P1-1），最终以服务端回执为准。
  const previewTotal = useMemo(() => {
    if (availUsdt == null) return null;
    const raw = capital.value ?? deriveGroupDefaultBudget(availUsdt, members.length);
    return clampGroupBudgetToCap(raw, availUsdt);
  }, [availUsdt, capital.value, members.length]);

  // 填值超过可用余额 5% 硬上限时，previewTotal 会被静默夹回上限——显式回显「已夹紧」，避免用户误以为输入未生效。
  const budgetCap = groupBudgetCap(availUsdt);
  const isClamped = isGroupBudgetClamped(capital.value, availUsdt);

  const hasUnsupported = members.some((m) => !isAutoTradable(m.instId));
  const ready = !inputError && acknowledged && strategyId != null && members.length > 0 && !hasUnsupported;

  const handleConfirm = () => {
    if (!ready) return;
    onConfirm({
      strategyId: strategyId as number,
      totalCapitalUsd: capital.value,
      leverage: leverage.value,
      takeProfitUsd: takeProfit.value,
      stopLossUsd: stopLoss.value,
    });
  };

  return (
    <Modal open={open} onClose={onClose} labelledBy="auto-trade-group-title" className="max-w-lg">
      <div className="flex items-center justify-between border-b border-[var(--border)] px-5 py-3.5">
        <h2 id="auto-trade-group-title" className="text-sm font-semibold text-[var(--text)]">
          启动多标的自动交易（{members.length} 个标的）
        </h2>
        <Badge tone="short">真实资金</Badge>
      </div>

      <div className="flex max-h-[60vh] flex-col gap-3 overflow-auto px-5 py-4">
        <label className="flex flex-col gap-1 text-[11px] text-[var(--muted)]">
          <span>交易策略（对所有标的统一应用）</span>
          <Select
            value={strategyId ?? ""}
            disabled={submitting}
            onChange={(e) => setStrategyId(e.target.value ? Number(e.target.value) : null)}
            className="text-xs"
          >
            {(strategiesQ.data ?? []).map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </Select>
        </label>

        <div className="flex flex-col gap-px overflow-hidden rounded-md bg-[var(--border)]">
          <Row label="周期" value={bar} />
          <Row label="交易账户" value="设置中心 · 默认实盘账户" />
          <Row
            label="可用余额"
            value={
              availUsdt != null
                ? `${fmtPrice(availUsdt)} USDT`
                : configQ.isError
                  ? "读取失败"
                  : "读取中…"
            }
          />
          <Row
            label={
              usingDefault
                ? "默认总预算（随成员数缩放）"
                : isClamped
                  ? "生效总预算（预览 · 已按 5% 上限夹紧）"
                  : "生效总预算（预览）"
            }
            value={previewTotal != null ? `${fmtPrice(previewTotal)} USDT` : "—"}
          />
          <Row
            label="生效杠杆"
            value={leverage.value != null ? `${leverage.value}×` : `默认 ${LIVE_AUTO_DEFAULT_LEVERAGE}×`}
          />
          <Row label="单笔名义上限" value={`≤ ${LIVE_MAX_ORDER_NOTIONAL_USD} USDT/单`} />
        </div>

        {/* 成员清单：各自百分比 + 每标的预算预览。 */}
        <div className="overflow-hidden rounded-md border border-[var(--border)]">
          <div className="flex items-center justify-between bg-[var(--surface-2)] px-3 py-1.5 text-[10px] font-semibold uppercase tracking-wide text-[var(--muted)]">
            <span>标的</span>
            <span>百分比 · 预算预览</span>
          </div>
          {members.map((m) => (
            <div
              key={m.instId}
              className="flex items-center justify-between gap-2 border-t border-[var(--border)] px-3 py-1.5 text-xs"
            >
              <span className="flex items-center gap-1.5 truncate text-[var(--text)]">
                {m.instId}
                {!isAutoTradable(m.instId) && (
                  <span className="text-[10px] text-[var(--short)]">不支持</span>
                )}
              </span>
              <span className="tabular text-[var(--muted)]">
                {m.allocationPct}% ·{" "}
                <span className="font-medium text-[var(--text)]">
                  {previewTotal != null
                    ? `${fmtPrice(memberBudget(previewTotal, m.allocationPct))} USDT`
                    : "—"}
                </span>
              </span>
            </div>
          ))}
        </div>

        <p className="rounded-md border border-[var(--border)] bg-[var(--surface-2)] px-2.5 py-2 text-[11px] leading-relaxed text-[var(--muted)]">
          <strong className="text-[var(--text)]">总止盈 / 止损为分组累计金额</strong>
          ，按各标的百分比归一分摊到每个标的、各自独立触发（阈值之和恒等于所填总额）；留空则不按金额触发。
          每笔交易另自带策略止盈止损（交易所侧 OCO，进程退出后仍兜底）。
        </p>
        <p className="text-[11px] leading-relaxed text-[var(--muted)]">
          以下均可留空（留空即用保守内置默认）；总预算硬上限为可用余额的 5%，超出将被服务端自动夹紧。已分配{" "}
          <span className="font-medium text-[var(--text)]">{Math.round(sumPct * 100) / 100}%</span>/100%。
        </p>

        <div className="grid grid-cols-2 gap-3">
          <Field
            label="总预算（USDT）"
            placeholder="留空＝默认"
            value={capitalRaw}
            onChange={setCapitalRaw}
            disabled={submitting}
            hint={
              isClamped && capital.value != null
                ? `输入 ${fmtPrice(capital.value)} 超过可用余额 5% 上限（${fmtPrice(budgetCap)} USDT），已按上限 ${fmtPrice(budgetCap)} 计算`
                : undefined
            }
          />
          <Field label="杠杆（×）" placeholder="留空＝默认" value={leverageRaw} onChange={setLeverageRaw} disabled={submitting} />
          <Field label="总止盈（USDT）" placeholder="留空＝不触发" value={takeProfitRaw} onChange={setTakeProfitRaw} disabled={submitting} />
          <Field label="总止损（USDT）" placeholder="留空＝不触发" value={stopLossRaw} onChange={setStopLossRaw} disabled={submitting} />
        </div>

        <label className="flex items-start gap-2 text-xs leading-relaxed text-[var(--text)]">
          <input
            type="checkbox"
            checked={acknowledged}
            disabled={submitting}
            onChange={(e) => onAcknowledge(e.target.checked)}
            aria-label="我已知晓这是真实资金多标的自动交易"
            className="mt-0.5 h-4 w-4 shrink-0 accent-[var(--accent)]"
          />
          <span>
            我已知晓这是<strong className="text-[var(--short)]">真实资金多标的自动交易</strong>
            ，将对上述全部标的按所选策略同时自动下单 / 平仓，盈亏由本人承担。
          </span>
        </label>

        {(inputError || error || hasUnsupported) && (
          <p role="alert" className="text-[11px] font-medium text-[var(--short)]">
            {inputError || error || "含不支持自动交易的标的，请先移出（仅 USDT 本位永续 / 交割合约）"}
          </p>
        )}
      </div>

      <div className="flex items-center justify-end gap-2 border-t border-[var(--border)] px-5 py-3">
        <Button variant="ghost" size="sm" onClick={onClose} disabled={submitting}>
          取消
        </Button>
        <Button variant="primary" size="sm" onClick={handleConfirm} disabled={!ready || submitting}>
          {submitting ? "启动中…" : "确认并开始自动交易"}
        </Button>
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
  hint,
}: {
  label: string;
  placeholder: string;
  value: string;
  onChange: (v: string) => void;
  disabled?: boolean;
  hint?: string;
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
      {hint && <span className="leading-relaxed text-[var(--short)]">{hint}</span>}
    </label>
  );
}
