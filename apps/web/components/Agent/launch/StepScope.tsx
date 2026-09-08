"use client";

import { AI_TRADE_MAX_STRATEGIES, AI_TRADE_MAX_SYMBOLS } from "@/lib/aiTrade";
import { isAutoTradable, memberBudget } from "@/lib/autoTrade";
import { fmtPrice } from "@/lib/format";
import { risksForStep } from "@/lib/aiTradeLaunchRisk";
import { AllocModeTab, DateField, InlineRiskNote } from "./LaunchPrimitives";
import type { UseAiTradeLaunchForm } from "./useAiTradeLaunchForm";

/**
 * 第 1 步：**对谁交易、凭哪些信号**（ai-trade-guided-launch §4.2.1）。
 *
 * 承载今天弹窗里的：多标的分配器（实盘）/ 回测单标的 + 回测区间 / 策略多选。
 * 标题与副题一律取自 `AI_TRADE_LAUNCH_STEPS`（G-3），本文件不出现任何步骤标题字面量。
 */
/** 这两条各自由分配器下方的合计横幅与截断横幅就地说出口（见下方渲染纪律）。 */
const ALREADY_INLINE = new Set(["allocationOversubscribed", "membersTruncated"]);

export default function StepScope({
  form,
  submitting,
}: {
  form: UseAiTradeLaunchForm;
  submitting: boolean;
}) {
  const {
    mode, members, allocationMode, setAllocationMode, applyEvenSplit, updateAllocation,
    removeMember, addSymbolRaw, setAddSymbolRaw, addError, atCap, truncated,
    defaultMembersCount, previewBudget, maxBudgetPct, handleAddSymbol,
    instId, setInstId, selectedIds, toggleId, strategiesQ,
    startInput, setStartInput, endInput, setEndInput, risks,
  } = form;
  const stepRisks = risksForStep(risks, "scope");

  return (
    <div className="flex flex-col gap-3">
      {/* 多选策略清单（≥1、≤上限，两模式共用）。 */}
      <div className="flex flex-col gap-1 text-[11px] text-[var(--muted)]">
        <span>
          交易策略（可多选，AI 综合研判其信号）· 已选{" "}
          <span className="font-medium text-[var(--text)]">{selectedIds.length}</span>/
          {AI_TRADE_MAX_STRATEGIES}
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

      {mode === "live" ? (
        /* 实盘：多标的分配选择器（默认回填 autoTradeMembers）。 */
        <div className="flex flex-col gap-1.5 text-[11px] text-[var(--muted)]">
          <div className="flex items-center justify-between gap-2">
            <span>
              自动交易标的（默认取自左侧「自动交易」面板）· 已选{" "}
              <span className="font-medium text-[var(--text)]">{members.length}</span>/
              {AI_TRADE_MAX_SYMBOLS}
            </span>
            <div className="flex items-center gap-1">
              <AllocModeTab
                active={allocationMode === "even"}
                disabled={submitting}
                onClick={() => setAllocationMode("even")}
              >
                均分份额
              </AllocModeTab>
              <AllocModeTab
                active={allocationMode === "max_ratio"}
                disabled={submitting}
                onClick={() => setAllocationMode("max_ratio")}
              >
                独立上限
              </AllocModeTab>
              <button
                type="button"
                disabled={submitting || members.length === 0}
                onClick={applyEvenSplit}
                className="rounded border border-[var(--border)] px-2 py-0.5 text-[10px] text-[var(--text)] hover:bg-[var(--surface-2)] disabled:opacity-40"
              >
                均分
              </button>
            </div>
          </div>
          {truncated && (
            <p className="rounded border border-[var(--border)] bg-[var(--surface-2)] px-2 py-1 text-[10px] leading-relaxed text-[var(--muted)]">
              左侧已配置 {defaultMembersCount} 个标的，已达 AI 自动交易标的上限 {AI_TRADE_MAX_SYMBOLS}
              ，仅前 {AI_TRADE_MAX_SYMBOLS} 个入选（可在此手动增删）。
            </p>
          )}
          <div className="overflow-hidden rounded-md border border-[var(--border)]">
            <div className="flex items-center justify-between bg-[var(--surface-2)] px-3 py-1.5 text-[10px] font-semibold uppercase tracking-wide text-[var(--muted)]">
              <span>标的</span>
              <span>{allocationMode === "max_ratio" ? "上限% · 预算预览" : "百分比% · 预算预览"}</span>
            </div>
            {members.map((m) => (
              <div
                key={m.instId}
                className="flex items-center justify-between gap-2 border-t border-[var(--border)] px-3 py-1.5 text-xs"
              >
                <span className="flex items-center gap-1.5 truncate text-[var(--text)]">
                  {m.instId}
                  {!isAutoTradable(m.instId) && (
                    <span className="text-[10px] text-[var(--short-ink)]">不支持</span>
                  )}
                </span>
                <span className="flex items-center gap-2">
                  <input
                    type="text"
                    inputMode="decimal"
                    value={String(m.allocationPct)}
                    disabled={submitting}
                    onChange={(e) => updateAllocation(m.instId, e.target.value)}
                    aria-label={`${m.instId} 百分比`}
                    className="w-14 rounded border border-[var(--border)] bg-[var(--bg)] px-1.5 py-1 text-right text-xs text-[var(--text)] outline-none focus:border-[var(--accent)] disabled:opacity-50"
                  />
                  <span className="tabular text-[10px] text-[var(--muted)]">
                    {previewBudget != null
                      ? `${fmtPrice(memberBudget(previewBudget, m.allocationPct))} USDT`
                      : "—"}
                  </span>
                  <button
                    type="button"
                    disabled={submitting}
                    onClick={() => removeMember(m.instId)}
                    aria-label={`移除 ${m.instId}`}
                    className="rounded px-1 text-[var(--muted)] hover:text-[var(--short-ink)] disabled:opacity-40"
                  >
                    ✕
                  </button>
                </span>
              </div>
            ))}
            {/* 新增标的（达上限即禁用）。 */}
            <div className="flex items-center gap-2 border-t border-[var(--border)] px-3 py-1.5">
              <input
                type="text"
                value={addSymbolRaw}
                placeholder={atCap ? `已达上限 ${AI_TRADE_MAX_SYMBOLS} 个` : "新增标的（USDT 永续 / 交割）"}
                disabled={submitting || atCap}
                onChange={(e) => setAddSymbolRaw(e.target.value.trim().toUpperCase())}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    handleAddSymbol();
                  }
                }}
                aria-label="新增自动交易标的"
                className="flex-1 rounded border border-[var(--border)] bg-[var(--bg)] px-2 py-1 text-xs uppercase text-[var(--text)] outline-none focus:border-[var(--accent)] disabled:opacity-50"
              />
              <button
                type="button"
                disabled={submitting || atCap || !addSymbolRaw.trim()}
                onClick={handleAddSymbol}
                className="rounded border border-[var(--border)] px-2 py-1 text-[11px] text-[var(--text)] hover:bg-[var(--surface-2)] disabled:opacity-40"
              >
                添加
              </button>
            </div>
          </div>
          {addError && <span className="text-[10px] text-[var(--short-ink)]">{addError}</span>}
          {form.oversubscribed && (
            <p className="rounded border border-[var(--border)] bg-[var(--surface-2)] px-2 py-1 text-[10px] leading-relaxed text-[var(--muted)]">
              各标的上限合计 {Math.round(form.sumPct * 100) / 100}%（&gt; 100%）：多标的同时触发时组合敞口可能超过总预算，
              真实敞口仍受总预算上限（可用余额 × {maxBudgetPct}%）、单笔名义上限与单仓 OCO 约束。
            </p>
          )}
        </div>
      ) : (
        /* 回测：仍单标的（默认 members[0]），同享可编辑周期。 */
        <label className="flex flex-col gap-1 text-[11px] text-[var(--muted)]">
          <span>标的（回测单标的，默认首个自动交易标的，仅 USDT 本位永续 / 交割合约）</span>
          <input
            type="text"
            value={instId}
            disabled={submitting}
            onChange={(e) => setInstId(e.target.value.trim().toUpperCase())}
            className="rounded-md border border-[var(--border)] bg-[var(--bg)] px-2 py-1.5 text-xs uppercase text-[var(--text)] outline-none focus:border-[var(--accent)] disabled:opacity-50"
          />
        </label>
      )}

      {mode === "backtest" && (
        <div className="grid grid-cols-2 gap-3">
          <DateField label="回测起始" value={startInput} onChange={setStartInput} disabled={submitting} />
          <DateField label="回测结束" value={endInput} onChange={setEndInput} disabled={submitting} />
        </div>
      )}

      {/* 本步**已由自己的控件就地说出口**的风险（见 ALREADY_INLINE）不再走下面这条通用条目，
          否则同一句话会在同一屏上出现两次——§4.2.5 渲染规则 1 要的是「就地说一次」，
          不是「说两次」。清单外的风险由通用条目兜底：新加一条风险时二选一，别两边都不管。 */}
      {stepRisks
        .filter((risk) => !ALREADY_INLINE.has(risk.id))
        .map((risk) => (
          <InlineRiskNote key={risk.id} tone={risk.tone} title={risk.title} detail={risk.detail} />
        ))}
    </div>
  );
}
