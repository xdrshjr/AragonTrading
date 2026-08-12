"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  describeSource,
  describeTradability,
  firstFeasibleAlternative,
  formatBps,
  formatRate,
} from "@/lib/aiTradeTradability";
import { aiTradeTradabilityApi } from "@/lib/aiTradeHorizonApi";
import type { AiTradeTradabilityResponse } from "@/lib/aiTradeTypes";

/**
 * 「可交易性预检」整块（ai-trade-tradability-and-open-funnel 轴 A · §3.1.3 落点一）。
 *
 * 它回答的是**按下启动键之前**就该知道的一件事：在这个标的 / 这个周期 / 这组纪律阈值下，模型的
 * 开仓意图会有多大比例被最小 edge 门禁否决。本轮用用户本机真实 K 线实测：BTC-USDT-SWAP、
 * `balanced` 预设、市价口径下，**1m 是 98.6%、5m 是 65.0%**——而 `1m` 正是周期下拉的第一项。
 * 用户看到的现象就是「开着、在跑、每轮都有分析、就是从不开单」。
 *
 * 抽成子组件的唯一理由与 `AiTradeHorizonFields` / `ProfitGuardFields` 完全相同——CLAUDE.md 的
 * 800 行红线：`AiTradeLaunchModal.tsx` 开工前已 1542 行，而这一块含取数 effect + debounce +
 * 四档渲染 + 二次确认 + 一键换周期。
 *
 * 三条不可动摇的纪律：
 * 1. **只提示、不阻断**：启动按钮**永不禁用**；`infeasible` 只降为一次显式的二次确认；
 *    `unknown` 一律不阻碍任何操作。本仓库四次「永不开单」事故全都源于把「不确定」翻译成「否决」。
 * 2. **判据与文案一个字都不重写**：`level` / `headline` / `advice` 全部服从后端下发的那一份。
 * 3. **确认态的生命周期**：确认一次后在本次弹窗会话内保持，但只要 `level` 发生
 *    **由非 `infeasible` 变为 `infeasible`** 的跃迁（用户换了标的/周期/预设又换回来），确认态
 *    **必须重置**——否则用户在 A 周期上确认过的风险会被静默地套用到 B 周期上。
 *
 * **本组件内不写任何「重置表单」的 effect**：弹窗的重置逻辑必须留在父组件那个依赖收敛为 `[open]`
 * 的 effect 里（`ai-trade-modal-revert-recurrence` 的复发根因）。这里唯一的 effect 是取数。
 */
const DEBOUNCE_MS = 350;

/**
 * 未完成二次确认时，启动路径上给出的那一句（父组件的错误横幅读它）。
 *
 * 与下面 `ConfirmRow` 的勾选文案住在同一个文件，是为了让「要求什么」与「怎么满足它」不会分头演进。
 */
export const TRADABILITY_CONFIRM_REQUIRED_TEXT =
  "可交易性预检判定当前周期「不可行」：请在上方勾选「仍要启动」以确认风险，或改用更长的时间级别。";

export interface AiTradeTradabilityFieldsProps {
  /** 弹窗是否打开（关着就不取数，避免后台空跑）。 */
  open: boolean;
  instId: string;
  bar: string;
  preset: string;
  /** 「一键换周期」：直接改本弹窗的时间级别下拉，**不发任何请求**、不改预设。 */
  onPickBar: (bar: string) => void;
  /** 已确认「仍要启动」——受控 state 由父组件持有（本组件不写重置 effect）。 */
  confirmed: boolean;
  onConfirmedChange: (value: boolean) => void;
  /**
   * 本块**是否要求一次显式确认**（当且仅当 `level === "infeasible"`）。
   *
   * 必须向上报：父组件据此在提交路径上拦一次（`confirmed` 若只被写、不被读，那一行勾选就纯粹
   * 是装饰——用户一次点击照样能在一个几乎开不出单的周期上启动真实资金会话）。
   */
  onNeedsConfirmChange?: (needsConfirm: boolean) => void;
  disabled?: boolean;
}

export default function AiTradeTradabilityFields({
  open,
  instId,
  bar,
  preset,
  onPickBar,
  confirmed,
  onConfirmedChange,
  onNeedsConfirmChange,
  disabled = false,
}: AiTradeTradabilityFieldsProps) {
  const [data, setData] = useState<AiTradeTradabilityResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  // 上一次的 level：用于「由非 infeasible 变为 infeasible」的跃迁检测（确认态重置）。
  const prevLevel = useRef<string | null>(null);

  useEffect(() => {
    if (!open || !instId || !bar) return;
    let alive = true;
    setLoading(true);
    const timer = setTimeout(() => {
      aiTradeTradabilityApi
        .probe(instId, bar, preset)
        .then((res) => {
          if (!alive) return;
          setData(res);
          setFailed(false);
        })
        .catch(() => {
          if (!alive) return;
          // 取数失败**不阻碍任何操作**：后端保证 200 + unknown，走到这里说明是网络/服务不可达。
          setData(null);
          setFailed(true);
        })
        .finally(() => {
          if (alive) setLoading(false);
        });
    }, DEBOUNCE_MS);
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [open, instId, bar, preset]);

  const view = useMemo(() => describeTradability(data), [data]);

  useEffect(() => {
    const level = view?.level ?? null;
    // 只在「由非 infeasible 跃迁到 infeasible」时重置确认态。相同 level 的重复取数不重置——
    // 那会让用户每次 debounce 都要重新勾一次，是对同一个风险收第二次税。
    if (level === "infeasible" && prevLevel.current !== "infeasible" && confirmed) {
      onConfirmedChange(false);
    }
    prevLevel.current = level;
    // 同一处上报「要不要确认」：判据（level）在这里，拦截点在父组件的提交路径上。两者分处两个
    // 文件是刻意的——本块只提示、不阻断，真正的放行判定必须与其它启动前校验待在同一个地方。
    onNeedsConfirmChange?.(level === "infeasible");
    // `confirmed` / 两个回调刻意不进依赖：本 effect 只对 level 的**跃迁**做反应，
    // 把它们放进来会在用户刚勾上确认的那一帧再跑一次并当场取消勾选。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view?.level]);

  if (!view && !loading && !failed) return null;

  return (
    <div
      className="flex flex-col gap-1.5 rounded-md border border-[var(--border)] bg-[var(--surface)] px-3 py-2"
      data-testid="ai-trade-tradability"
    >
      <div className="flex items-center justify-between gap-2">
        <span className="text-[11px] font-medium text-[var(--text)]">可交易性预检</span>
        {view ? (
          <span
            className="tabular text-[10px]"
            style={{ color: toneVar(view.tone) }}
            data-testid="ai-tradability-level"
          >
            ● {view.label}
            {view.vetoText ? ` · 预计否决 ${view.vetoText}` : ""}
          </span>
        ) : (
          <span className="text-[10px] text-[var(--muted)]">{loading ? "计算中…" : "算不出"}</span>
        )}
      </div>

      {failed && !loading && (
        <p className="text-[10px] leading-relaxed text-[var(--muted)]">
          预检服务暂时不可达，本次不做事前评估——
          <strong className="text-[var(--text)]">这不影响启动</strong>。
        </p>
      )}

      {view && (
        <>
          <p
            className="text-[10px] leading-relaxed text-[var(--muted)]"
            data-testid="ai-tradability-headline"
          >
            {view.headline}
          </p>
          {view.advice && (
            <p
              className="rounded border px-2 py-1 text-[10px] leading-relaxed"
              style={{ color: toneVar(view.tone), borderColor: toneVar(view.tone) }}
              data-testid="ai-tradability-advice"
            >
              {view.advice}
            </p>
          )}
          <Numbers data={data} />
          <PickBarButton data={data} disabled={disabled} onPickBar={onPickBar} />
          {view.needsConfirm && (
            <ConfirmRow
              confirmed={confirmed}
              onChange={onConfirmedChange}
              disabled={disabled}
            />
          )}
          <Footnote data={data} />
        </>
      )}
    </div>
  );
}

/** 三个关键读数：ATR 中位 / 往返成本 / 门禁要求。**算不出的一律省略该子句**，不写「未知」。 */
function Numbers({ data }: { data: AiTradeTradabilityResponse | null }) {
  if (!data) return null;
  const atr = formatBps(data.atrBps?.p50);
  const cost = formatBps(data.costBps?.market);
  const required = formatBps(data.requiredBps?.market);
  const parts = [
    atr ? `ATR 中位 ${atr}` : null,
    cost ? `市价往返成本 ${cost}` : null,
    required ? `门禁要求 ≥ ${required}` : null,
  ].filter(Boolean);
  if (parts.length === 0) return null;
  return (
    <p className="tabular text-[10px] text-[var(--muted)]" data-testid="ai-tradability-numbers">
      {parts.join(" · ")}
    </p>
  );
}

/**
 * 「一键换周期」：只改本弹窗的时间级别下拉，**不发任何请求**、不自动改预设。
 * 自动改用户选的参数是本仓库明令禁止的「静默夹紧」形状——探针给建议，用户拍板。
 */
function PickBarButton({
  data,
  disabled,
  onPickBar,
}: {
  data: AiTradeTradabilityResponse | null;
  disabled: boolean;
  onPickBar: (bar: string) => void;
}) {
  const better = firstFeasibleAlternative(data);
  if (!data || data.level !== "infeasible" || !better) return null;
  const rate = formatRate(better.vetoRateMarket);
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={() => onPickBar(better.bar)}
      data-testid="ai-tradability-pick-bar"
      className="self-start text-[10px] font-medium text-[var(--accent)] hover:underline disabled:opacity-50"
    >
      改用 {better.bar}
      {rate ? `（预计否决约 ${rate}）` : ""}
    </button>
  );
}

/**
 * 二次确认行（`window.confirm` 不可用——桌面端体验差，且它会打断弹窗内的表单态）。
 * 启动按钮**不禁用**：这一行只是让用户在真实资金上显式承认「我知道这个周期几乎开不出单」。
 */
function ConfirmRow({
  confirmed,
  onChange,
  disabled,
}: {
  confirmed: boolean;
  onChange: (value: boolean) => void;
  disabled: boolean;
}) {
  return (
    <label
      className="flex cursor-pointer items-start gap-2 text-[10px] leading-relaxed text-[var(--short)]"
      data-testid="ai-tradability-confirm"
    >
      <input
        type="checkbox"
        checked={confirmed}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
        aria-label="仍要在该周期上启动"
        className="mt-0.5 h-3.5 w-3.5 shrink-0 accent-[var(--short)]"
      />
      <span>
        我已知晓当前周期上绝大多数开仓意图会被最小 edge 门禁否决，
        <strong>仍要启动</strong>。
      </span>
    </label>
  );
}

/** 数据来源 + 免责。免责句由后端下发，前端**一个字都不重写**（文案纪律）。 */
function Footnote({ data }: { data: AiTradeTradabilityResponse | null }) {
  if (!data) return null;
  const source = describeSource(data.source, data.asOfTs);
  return (
    <p className="text-[9px] leading-relaxed text-[var(--muted)]" data-testid="ai-tradability-note">
      {source ? `${source}。` : ""}
      {data.disclaimer}
    </p>
  );
}

function toneVar(tone: string): string {
  if (tone === "long") return "var(--long)";
  if (tone === "short") return "var(--short)";
  if (tone === "accent") return "var(--accent)";
  return "var(--muted)";
}
