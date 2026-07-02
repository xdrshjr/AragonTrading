"use client";

import { useQuery } from "@tanstack/react-query";
import { type ReactNode, useEffect, useMemo, useState } from "react";
import { Badge, Button, Modal } from "@/components/ui";
import { api } from "@/lib/api";
import {
  AI_TRADE_DEFAULT_CONTEXT_ROUNDS,
  AI_TRADE_MAX_SL_PCT,
  AI_TRADE_MAX_STRATEGIES,
  AI_TRADE_MAX_TP_PCT,
  clampBudgetToCap,
  deriveDefaultBudget,
  validateContextRounds,
  validateStrategySelection,
} from "@/lib/aiTrade";
import { isAutoTradable } from "@/lib/autoTrade";
import { fmtPrice } from "@/lib/format";
import {
  clampPct,
  LIVE_AUTO_DEFAULT_LEVERAGE,
  LIVE_MAX_ORDER_NOTIONAL_USD,
  parseOptionalLeverage,
  parseOptionalPct,
  parseOptionalUsd,
} from "@/lib/liveAuto";
import { tradingApi } from "@/lib/tradingApi";

export interface AiTradeLaunchOpts {
  instId: string;
  strategyIds: number[];
  capitalUsd: number | null;
  leverage: number | null;
  takeProfitUsd: number | null;
  stopLossUsd: number | null;
  stopLossPct: number | null;      // E4：单仓 OCO 止损%（fraction，留空＝config 默认）
  takeProfitPct: number | null;    // E4：单仓 OCO 止盈%
  contextRefreshRounds: number;
}

/**
 * 启动 AI 自动交易弹窗（ai-auto-trading §2.7）：多选策略（≥1、≤上限）+ 标的 / 周期 + 总预算 / 杠杆 /
 * 总止盈 / 总止损 + 上下文刷新轮数 + 只读预算预览 + 真实资金风险勾选。AI 综合研判所选策略的实时信号做决策。
 */
export default function AiTradeLaunchModal({
  open,
  onClose,
  onConfirm,
  submitting,
  acknowledged,
  onAcknowledge,
  bar,
  defaultInstId,
  defaultStrategyIds,
  defaultContextRounds,
  error,
}: {
  open: boolean;
  onClose: () => void;
  onConfirm: (opts: AiTradeLaunchOpts) => void;
  submitting: boolean;
  acknowledged: boolean;
  onAcknowledge: (value: boolean) => void;
  bar: string;
  defaultInstId: string;
  defaultStrategyIds: number[];
  defaultContextRounds: number;
  error?: string | null;
}) {
  const [instId, setInstId] = useState(defaultInstId);
  const [selectedIds, setSelectedIds] = useState<number[]>(defaultStrategyIds);
  const [capitalRaw, setCapitalRaw] = useState("");
  const [leverageRaw, setLeverageRaw] = useState("");
  const [takeProfitRaw, setTakeProfitRaw] = useState("");
  const [stopLossRaw, setStopLossRaw] = useState("");
  const [stopLossPctRaw, setStopLossPctRaw] = useState("");
  const [takeProfitPctRaw, setTakeProfitPctRaw] = useState("");
  const [roundsRaw, setRoundsRaw] = useState(String(defaultContextRounds || AI_TRADE_DEFAULT_CONTEXT_ROUNDS));

  const strategiesQ = useQuery({ queryKey: ["strategies"], queryFn: api.strategies });
  const configQ = useQuery({
    queryKey: ["trading", "config", instId, null],
    queryFn: () => tradingApi.config(instId),
    enabled: open && !!instId,
  });
  const availUsdt = configQ.data?.availUsdt ?? null;

  // 弹窗每次打开：回落默认标的 / 策略 / 轮数并清空金额输入。
  useEffect(() => {
    if (!open) return;
    setInstId(defaultInstId);
    setSelectedIds(defaultStrategyIds);
    setRoundsRaw(String(defaultContextRounds || AI_TRADE_DEFAULT_CONTEXT_ROUNDS));
    setCapitalRaw("");
    setLeverageRaw("");
    setTakeProfitRaw("");
    setStopLossRaw("");
    setStopLossPctRaw("");
    setTakeProfitPctRaw("");
  }, [open, defaultInstId, defaultContextRounds]); // eslint-disable-line react-hooks/exhaustive-deps

  const toggleId = (id: number) =>
    setSelectedIds((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));

  const capital = parseOptionalUsd(capitalRaw);
  const leverage = parseOptionalLeverage(leverageRaw);
  const takeProfit = parseOptionalUsd(takeProfitRaw);
  const stopLoss = parseOptionalUsd(stopLossRaw);
  const stopLossPct = parseOptionalPct(stopLossPctRaw);
  const takeProfitPct = parseOptionalPct(takeProfitPctRaw);
  const roundsValue = Number.parseInt(roundsRaw, 10);
  const inputError =
    capital.error || leverage.error || takeProfit.error || stopLoss.error ||
    stopLossPct.error || takeProfitPct.error || null;
  const strategyError = validateStrategySelection(selectedIds);
  const roundsError = validateContextRounds(roundsValue);
  const instSupported = isAutoTradable(instId);

  const previewBudget = useMemo(() => {
    if (availUsdt == null) return null;
    const raw = capital.value ?? deriveDefaultBudget(availUsdt);
    return clampBudgetToCap(raw, availUsdt);
  }, [availUsdt, capital.value]);

  const usingDefault = capital.value == null;
  const ready =
    !inputError && !strategyError && !roundsError && acknowledged && instSupported && !!instId;

  const handleConfirm = () => {
    if (!ready) return;
    onConfirm({
      instId,
      strategyIds: selectedIds,
      capitalUsd: capital.value,
      leverage: leverage.value,
      takeProfitUsd: takeProfit.value,
      stopLossUsd: stopLoss.value,
      stopLossPct: stopLossPct.value,
      takeProfitPct: takeProfitPct.value,
      contextRefreshRounds: roundsValue,
    });
  };

  const firstError = strategyError || roundsError || inputError || error || (
    !instSupported ? "标的不支持自动交易（仅 USDT 本位永续 / 交割合约）" : null
  );

  return (
    <Modal open={open} onClose={onClose} labelledBy="ai-trade-launch-title" className="max-w-lg">
      <div className="flex items-center justify-between border-b border-[var(--border)] px-5 py-3.5">
        <h2 id="ai-trade-launch-title" className="text-sm font-semibold text-[var(--text)]">
          启动 AI 自动交易
        </h2>
        <Badge tone="short">真实资金</Badge>
      </div>

      <div className="flex max-h-[62vh] flex-col gap-3 overflow-auto px-5 py-4">
        <p className="text-[11px] leading-relaxed text-[var(--muted)]">
          AI 助手将作为决策者，综合研判所选<strong className="text-[var(--text)]">多个策略</strong>的实时信号，
          在每根 K 线上决定开多 / 开空 / 平仓 / 观望，真单经既有守护执行器落地并继承全部资金护栏。
        </p>

        {/* 多选策略清单（≥1、≤上限）。 */}
        <div className="flex flex-col gap-1 text-[11px] text-[var(--muted)]">
          <span>
            交易策略（可多选，AI 综合研判其信号）· 已选{" "}
            <span className="font-medium text-[var(--text)]">{selectedIds.length}</span>/{AI_TRADE_MAX_STRATEGIES}
          </span>
          <div className="max-h-40 overflow-auto rounded-md border border-[var(--border)]">
            {(strategiesQ.data ?? []).length === 0 && (
              <div className="px-3 py-2 text-xs text-[var(--muted)]">
                {strategiesQ.isError ? "策略列表读取失败" : "加载中…"}
              </div>
            )}
            {(strategiesQ.data ?? []).map((s) => (
              <label
                key={s.id}
                className="flex cursor-pointer items-center gap-2 border-t border-[var(--border)] px-3 py-1.5 text-xs first:border-t-0 hover:bg-[var(--surface-2)]"
              >
                <input
                  type="checkbox"
                  checked={selectedIds.includes(s.id)}
                  disabled={submitting}
                  onChange={() => toggleId(s.id)}
                  className="h-3.5 w-3.5 shrink-0 accent-[var(--accent)]"
                />
                <span className="truncate text-[var(--text)]">{s.name}</span>
              </label>
            ))}
          </div>
        </div>

        {/* 标的（默认当前图表标的，可改）+ 只读预览。 */}
        <label className="flex flex-col gap-1 text-[11px] text-[var(--muted)]">
          <span>标的（默认当前图表标的，仅 USDT 本位永续 / 交割合约）</span>
          <input
            type="text"
            value={instId}
            disabled={submitting}
            onChange={(e) => setInstId(e.target.value.trim().toUpperCase())}
            className="rounded-md border border-[var(--border)] bg-[var(--bg)] px-2 py-1.5 text-xs uppercase text-[var(--text)] outline-none focus:border-[var(--accent)] disabled:opacity-50"
          />
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
            label={usingDefault ? "默认预算（可用余额 × 1%）" : "生效预算（预览 · 已按 5% 上限夹紧）"}
            value={previewBudget != null ? `${fmtPrice(previewBudget)} USDT` : "—"}
          />
          <Row
            label="生效杠杆"
            value={leverage.value != null ? `${leverage.value}×` : `默认 ${LIVE_AUTO_DEFAULT_LEVERAGE}×`}
          />
          <Row label="单笔名义上限" value={`≤ ${LIVE_MAX_ORDER_NOTIONAL_USD} USDT/单`} />
          <Row
            label="单仓兜底 OCO（交易所侧）"
            value={`SL ${fmtOcoPct(stopLossPct.value, AI_TRADE_MAX_SL_PCT)} / TP ${fmtOcoPct(takeProfitPct.value, AI_TRADE_MAX_TP_PCT)}`}
          />
        </div>

        <div className="grid grid-cols-2 gap-3">
          <Field label="总预算（USDT）" placeholder="留空＝默认" value={capitalRaw} onChange={setCapitalRaw} disabled={submitting} />
          <Field label="杠杆（×）" placeholder="留空＝默认" value={leverageRaw} onChange={setLeverageRaw} disabled={submitting} />
          <Field label="总止盈（USDT）" placeholder="留空＝不触发" value={takeProfitRaw} onChange={setTakeProfitRaw} disabled={submitting} />
          <Field label="总止损（USDT）" placeholder="留空＝不触发" value={stopLossRaw} onChange={setStopLossRaw} disabled={submitting} />
          <Field label="单仓止损%（如 0.05＝5%）" placeholder="留空＝默认" value={stopLossPctRaw} onChange={setStopLossPctRaw} disabled={submitting} />
          <Field label="单仓止盈%（如 0.10＝10%）" placeholder="留空＝默认" value={takeProfitPctRaw} onChange={setTakeProfitPctRaw} disabled={submitting} />
        </div>

        <Field
          label="上下文刷新轮数"
          placeholder={String(AI_TRADE_DEFAULT_CONTEXT_ROUNDS)}
          value={roundsRaw}
          onChange={setRoundsRaw}
          disabled={submitting}
          hint="每 N 轮把 AI 执行过程摘要为上下文、清空历史保持纯净（保留连续记忆的同时守住 token 预算）"
        />

        <label className="flex items-start gap-2 text-xs leading-relaxed text-[var(--text)]">
          <input
            type="checkbox"
            checked={acknowledged}
            disabled={submitting}
            onChange={(e) => onAcknowledge(e.target.checked)}
            aria-label="我已知晓这是真实资金 AI 自动交易"
            className="mt-0.5 h-4 w-4 shrink-0 accent-[var(--accent)]"
          />
          <span>
            我已知晓这是<strong className="text-[var(--short)]">真实资金 AI 自动交易</strong>
            ，AI 将据所选策略信号自主决定开仓 / 平仓，盈亏由本人承担。
          </span>
        </label>

        {firstError && (
          <p role="alert" className="text-[11px] font-medium text-[var(--short)]">
            {firstError}
          </p>
        )}
      </div>

      <div className="flex items-center justify-end gap-2 border-t border-[var(--border)] px-5 py-3">
        <Button variant="ghost" size="sm" onClick={onClose} disabled={submitting}>
          取消
        </Button>
        <Button variant="primary" size="sm" onClick={handleConfirm} disabled={!ready || submitting}>
          {submitting ? "启动中…" : "确认并开始 AI 自动交易"}
        </Button>
      </div>
    </Modal>
  );
}

/** 单仓 OCO 比例预览：留空→「默认」；有值→夹紧到 max 后按百分比展示（如 0.9→「20%」）。 */
function fmtOcoPct(value: number | null, max: number): string {
  if (value == null) return "默认";
  return `${+(clampPct(value, max) * 100).toFixed(2)}%`;
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
      {hint && <span className="leading-relaxed text-[var(--muted)]">{hint}</span>}
    </label>
  );
}
