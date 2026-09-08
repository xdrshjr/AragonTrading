"use client";

import { useMemo } from "react";
import {
  describeSource,
  describeTradability,
  firstFeasibleAlternative,
  formatBps,
  formatRate,
} from "@/lib/aiTradeTradability";
import type { AiTradeTradabilityResponse } from "@/lib/aiTradeTypes";

/**
 * 「可交易性预检」整块（ai-trade-tradability-and-open-funnel 轴 A · §3.1.3 落点一）。
 *
 * 它回答的是**按下启动键之前**就该知道的一件事：在这个标的 / 这个周期 / 这组纪律阈值下，模型的
 * 开仓意图会有多大比例被最小 edge 门禁否决。本轮用用户本机真实 K 线实测：BTC-USDT-SWAP、
 * `balanced` 预设、市价口径下，**1m 是 98.6%、5m 是 65.0%**——而 `1m` 正是周期下拉的第一项。
 * 用户看到的现象就是「开着、在跑、每轮都有分析、就是从不开单」。
 *
 * ── 本组件是**纯展示**（ai-trade-guided-launch §4.2.4 / 【v2 · P0-2】）────────
 * 取数、350ms 防抖、`prevLevel` 跃迁检测与「要不要二次确认」的判定**全部住在**
 * `launch/useAiTradeLaunchForm.ts`。理由是分步化：本块住在第 5 步，而六步里有五步它是**卸载**的。
 * 取数留在这里 ⇒ 用户在第 2 步改了周期、直达第 6 步启动时探针根本没重跑；`prevLevel` 随卸载丢失
 * ⇒ 用户在 A 周期上确认过的风险被静默套用到 B 周期；而「要不要确认」若由本组件上报给父级 state，
 * 那个 state 在五步里**没有任何人写**，`tradabilityBlocked` 恒 `false`——一次真实资金启动就绕过了
 * 本轮唯一一次 fail-open 确认。**判据取自不携带该信息的入参**，正是本仓库反复付学费的形状。
 *
 * 三条不可动摇的纪律（逐字不变）：
 * 1. **只提示、不阻断**：启动按钮**永不禁用**；`infeasible` 只降为一次显式的二次确认；
 *    `unknown` 一律不阻碍任何操作。本仓库四次「永不开单」事故全都源于把「不确定」翻译成「否决」。
 * 2. **判据与文案一个字都不重写**：`level` / `headline` / `advice` 全部服从后端下发的那一份。
 * 3. **确认态的生命周期**：确认一次后在本次弹窗会话内保持，但只要 `level` 发生
 *    **由非 `infeasible` 变为 `infeasible`** 的跃迁，确认态必须重置（现由 hook 里那个跨步骤存活的
 *    `useRef` 负责——父组件全程不卸载，它才真的能活下来）。
 *
 * **本组件内不写任何 effect**：既没有取数，也没有「重置表单」的——弹窗的重置逻辑必须留在
 * hook 那个依赖收敛为 `[open]` 的 effect 里（`ai-trade-modal-revert-recurrence` 的复发根因）。
 */

/**
 * 未完成二次确认时，启动路径上给出的那一句（`useAiTradeLaunchForm` 的错误横幅读它）。
 *
 * 与下面 `ConfirmRow` 的勾选文案住在同一个文件，是为了让「要求什么」与「怎么满足它」不会分头演进。
 */
export const TRADABILITY_CONFIRM_REQUIRED_TEXT =
  "可交易性预检判定当前周期「不可行」：请在上方勾选「仍要启动」以确认风险，或改用更长的时间级别。";

export interface AiTradeTradabilityFieldsProps {
  /** 探针回执（`null` = 尚未落地 / 取数失败）。**取数由 hook 负责**，本组件不发任何请求。 */
  data: AiTradeTradabilityResponse | null;
  loading: boolean;
  /** 取数失败 ⇒ 只说一句「这不影响启动」，绝不阻碍任何操作。 */
  failed: boolean;
  /** 「一键换周期」：直接改本弹窗的时间级别下拉，**不发任何请求**、不改预设。 */
  onPickBar: (bar: string) => void;
  /** 已确认「仍要启动」——受控 state 由 hook 持有（本组件不写任何 effect）。 */
  confirmed: boolean;
  onConfirmedChange: (value: boolean) => void;
  disabled?: boolean;
}

export default function AiTradeTradabilityFields({
  data,
  loading,
  failed,
  onPickBar,
  confirmed,
  onConfirmedChange,
  disabled = false,
}: AiTradeTradabilityFieldsProps) {
  const view = useMemo(() => describeTradability(data), [data]);

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
          <BracketRow data={data} />
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
 * 风险单位体检行（realized-edge-calibration 轴 E-0）：**与既有可交易性结论共用同一个折叠区**。
 *
 * 它回答的是启动前最该说而至今没说的一句话：你设的止损离现价 500 bps，而这个周期一根 K 线的 ATR
 * 只有 28 bps —— 风险框架和交易尺度不在一个量级上。实测 193 笔历史往返里 100% 的 |R| < 0.2、
 * 无一笔触及过 ±1R，整套止损 / 止盈 / 利润保护武装线从未被触碰过一次。
 *
 * 三条纪律：① **只提示、不阻断**（启动按钮永不禁用，这一块也不参与二次确认）；② 文案与建议值
 * 全部由后端下发，前端一个字都不重写、一次换算都不做；③ 建议值**绝不自动填进输入框**。
 */
function BracketRow({ data }: { data: AiTradeTradabilityResponse | null }) {
  const bracket = data?.bracket;
  if (!bracket) return null;          // 算不出 / 总闸关 ⇒ 整块缺席（fail-open，与探针同极性）
  const alarming = bracket.level === "oversized";
  return (
    <p
      className="rounded border px-2 py-1 text-[10px] leading-relaxed"
      style={{
        color: alarming ? "var(--warn)" : "var(--muted)",
        borderColor: alarming ? "var(--warn)" : "var(--border)",
      }}
      data-testid="ai-bracket-verdict"
      data-level={bracket.level}
    >
      {bracket.text}
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
      className="self-start text-[10px] font-medium text-[var(--accent-ink)] hover:underline disabled:opacity-50"
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
      className="flex cursor-pointer items-start gap-2 text-[10px] leading-relaxed text-[var(--short-ink)]"
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
