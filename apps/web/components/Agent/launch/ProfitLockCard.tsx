"use client";

import { AI_TRADE_PROFIT_LOCK_DEFAULTS, previewLockThresholds } from "@/lib/aiTradeProfitLock";
import type { AiTradeProfitLockRule, AiTradeTradabilityResponse } from "@/lib/aiTradeTypes";
import type { ProfitLockField, ProfitLockFieldsView } from "./useProfitLockFields";
import type { UseAiTradeLaunchState } from "./useAiTradeLaunchState";

/**
 * 第 5 步「收益锁」卡片（ai-trade-profit-lock-and-entry-unblock C-2）。排在利润保护**之前**：
 * 对多数单子来说它比利润保护更早生效——利润保护按保证金口径武装，5× 下要浮盈约 60 bps，
 * 而生产上曾浮盈后翻亏的单子，峰值中位只有约 20 bps。
 *
 * 受控组件：值与 setter 全部来自 `useAiTradeLaunchState`，**本组件不写任何 effect**。
 * 预估数字只取可交易性预检的真实读数；预检未就绪时只说公式，不编数字。
 */
interface Props {
  state: UseAiTradeLaunchState;
  view: ProfitLockFieldsView;
  probe: AiTradeTradabilityResponse | null;
  mode: "live" | "backtest";
  disabled: boolean;
}

const D = AI_TRADE_PROFIT_LOCK_DEFAULTS;

function fmtBps(value: number): string {
  return `${+value.toFixed(1)}`;
}

function Preview({ probe, rule }: {
  probe: AiTradeTradabilityResponse | null;
  rule: AiTradeProfitLockRule;
}) {
  const cost = probe?.costBps?.market ?? null;
  const atr = probe?.atrBps?.p50 ?? null;
  const preview = previewLockThresholds(cost, atr, rule);   // 高级区改过的倍数同步进预估
  if (!preview || cost == null) {
    return (
      <span data-testid="ai-profit-lock-formula">
        浮盈 ≥ max({D.armCostMult}× 往返成本, {D.armAtrMult}× ATR) 时保本；
        ≥ max({D.trailCostMult}× 往返成本, {D.trailAtrMult}× ATR) 时开始锁利。
      </span>
    );
  }
  return (
    <span data-testid="ai-profit-lock-preview">
      按当前成本约 {fmtBps(cost)} bps
      {atr != null ? `、ATR 约 ${fmtBps(atr)} bps` : ""}：浮盈到 {fmtBps(preview.armBps)} bps
      时保本，到 {fmtBps(preview.trailArmBps)} bps 时开始锁利。
    </span>
  );
}

function LockField(props: {
  field: ProfitLockField;
  label: string;
  placeholder: string;
  value: string;
  onChange: (value: string) => void;
  error?: string;
  disabled: boolean;
}) {
  return (
    <label className="flex min-w-0 flex-col gap-0.5">
      <span className="truncate whitespace-nowrap">{props.label}</span>
      <input
        inputMode="decimal"
        value={props.value}
        placeholder={props.placeholder}
        disabled={props.disabled}
        onChange={(e) => props.onChange(e.target.value)}
        aria-invalid={props.error ? true : undefined}
        data-testid={`ai-profit-lock-${props.field}`}
        className="h-7 rounded border border-[var(--border)] bg-[var(--bg)] px-2 text-xs tabular text-[var(--text)] aria-[invalid=true]:border-[var(--short)]"
      />
      {props.error && (
        <span className="text-[10px] text-[var(--short-ink)]" role="alert">{props.error}</span>
      )}
    </label>
  );
}

export default function ProfitLockCard({ state, view, probe, mode, disabled }: Props) {
  const enabled = state.profitLockEnabled;
  return (
    <div
      className="flex flex-col gap-1.5 rounded-md border border-[var(--accent-soft)] bg-[color-mix(in_srgb,var(--accent)_5%,transparent)] px-3 py-2"
      data-testid="ai-profit-lock-card"
    >
      <label className="flex cursor-pointer items-start gap-2 text-xs">
        <input
          type="checkbox"
          checked={enabled}
          disabled={disabled}
          onChange={(e) => state.setProfitLockEnabled(e.target.checked)}
          className="mt-0.5 h-3.5 w-3.5 shrink-0 accent-[var(--accent)]"
          aria-label="收益锁（推荐）"
        />
        <span className="flex flex-col gap-0.5">
          <span className="font-medium text-[var(--text)]">收益锁（推荐）</span>
          <span className="text-[11px] leading-relaxed text-[var(--muted)]">
            浮盈覆盖约 {D.armCostMult} 倍往返成本后，把离场线抬到
            <strong className="text-[var(--text)]">含费保本</strong>之上；浮盈继续扩大后
            <strong className="text-[var(--text)]">锁住一半</strong>。只会平仓，不会开仓或加仓。
            {mode === "backtest" && " 回测按 K 线极值近似，会低估收益锁的效果。"}
          </span>
          {enabled && (
            <span className="text-[11px] leading-relaxed text-[var(--text)]">
              <Preview probe={probe} rule={view.payload} />
            </span>
          )}
        </span>
      </label>
      {enabled && (
        <details className="text-[11px] text-[var(--muted)]">
          <summary className="cursor-pointer select-none">高级</summary>
          <div className="mt-1.5 grid grid-cols-3 gap-2">
            <LockField
              field="armCostMult"
              label="保本锁武装（× 成本）"
              placeholder={String(D.armCostMult)}
              value={state.lockArmCostRaw}
              onChange={state.setLockArmCostRaw}
              error={view.fieldErrors.armCostMult}
              disabled={disabled}
            />
            <LockField
              field="keepRatio"
              label="利润锁保留比例"
              placeholder={String(D.keepRatio)}
              value={state.lockKeepRatioRaw}
              onChange={state.setLockKeepRatioRaw}
              error={view.fieldErrors.keepRatio}
              disabled={disabled}
            />
            <LockField
              field="confirmSeconds"
              label="越线确认（秒）"
              placeholder={String(D.confirmSeconds)}
              value={state.lockConfirmRaw}
              onChange={state.setLockConfirmRaw}
              error={view.fieldErrors.confirmSeconds}
              disabled={disabled}
            />
          </div>
          <p className="mt-1 leading-relaxed">
            留空即用默认值。收益锁只在应用运行时生效；交易所侧止盈止损不受影响。
          </p>
        </details>
      )}
    </div>
  );
}
