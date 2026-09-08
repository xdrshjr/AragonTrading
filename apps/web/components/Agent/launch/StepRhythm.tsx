"use client";

import { Select } from "@/components/ui";
import { AI_BACKTEST_MAX_ROUNDS } from "@/lib/aiBacktest";
import {
  AI_TRADE_DEFAULT_CONTEXT_ROUNDS,
  AI_TRADE_DEFAULT_WAKE_INTERVAL_MIN,
} from "@/lib/aiTrade";
import { risksForStep } from "@/lib/aiTradeLaunchRisk";
import type { Bar } from "@/lib/types";
import AiTradeHorizonFields, { type HorizonCustomKey } from "../AiTradeHorizonFields";
import { Field, InlineRiskNote, Row } from "./LaunchPrimitives";
import { AI_TRADE_DEFAULT_OCO_SL_PCT, type UseAiTradeLaunchForm } from "./useAiTradeLaunchForm";

/** 时间级别候选（镜像 ChartToolbar 的 BARS；弹窗「时间级别」下拉可编辑）。 */
const BARS: Bar[] = ["1m", "5m", "15m", "30m", "1H", "4H", "1D"];

/**
 * 第 2 步：**多久做一次决策、一笔拿多久**（ai-trade-guided-launch §4.2.1）。
 *
 * 承载今天弹窗里的：交易周期模式卡、时间级别、Agent 唤醒周期（实盘）、上下文刷新轮数、
 * 决策间隔 / 共识门槛（回测）。
 */
/**
 * 唤醒下限那一条已由 `AiTradeHorizonFields` 的一致性提示（`ai-trade-horizon-hints`）就地说出口
 * ——那句话的措辞与除数都由 `horizonCoherenceHints` 现算，前端在这里再抄一遍必然分叉。
 */
const ALREADY_INLINE = new Set(["wakeFloorRaised"]);

export default function StepRhythm({
  form,
  submitting,
}: {
  form: UseAiTradeLaunchForm;
  submitting: boolean;
}) {
  const {
    mode, bar, setBar, horizonQ, horizonEnabled, setHorizonEnabled, horizonProfileId,
    handleProfileSelect, horizonCustom, setHorizonCustom, leverage, stopLossPct,
    repairMode, setRepairMode, wakeMinutes, alignToProfile, activeHorizonProfile,
    wakeIntervalRaw, setWakeIntervalRaw, roundsRaw, setRoundsRaw,
    everyRaw, setEveryRaw, smartEvery, minConsensusRaw, setMinConsensusRaw,
    everyValue, plannedRounds, roundsCapError, risks,
  } = form;
  const stepRisks = risksForStep(risks, "rhythm");

  return (
    <div className="flex flex-col gap-3">
      {/* 交易周期模式：**挂在最顶部**——它决定了下面几乎所有字段的合理取值。 */}
      <AiTradeHorizonFields
        data={horizonQ.data ?? null}
        loading={horizonQ.isPending}
        enabled={horizonEnabled}
        onEnabledChange={setHorizonEnabled}
        profileId={horizonProfileId}
        onProfileChange={handleProfileSelect}
        custom={horizonCustom}
        onCustomChange={(key: HorizonCustomKey, value: string) =>
          setHorizonCustom((prev) => ({ ...prev, [key]: value }))
        }
        bar={bar}
        leverage={leverage.value}
        stopLossPct={stopLossPct.value}
        defaultStopLossPct={AI_TRADE_DEFAULT_OCO_SL_PCT}
        backstopRepairMode={repairMode}
        onBackstopRepairModeChange={setRepairMode}
        wakeMinutes={wakeMinutes}
        onAlign={() => alignToProfile(activeHorizonProfile ?? undefined)}
        disabled={submitting}
      />

      {/* 时间级别（默认 K 线所选周期，可修改，两模式共用）。 */}
      <label className="flex flex-col gap-1 text-[11px] text-[var(--muted)]">
        <span>时间级别（默认 K 线所选周期，可修改）</span>
        <Select
          value={bar}
          disabled={submitting}
          onChange={(e) => setBar(e.target.value as Bar)}
          className="text-xs"
          aria-label="时间级别"
        >
          {BARS.map((b) => (
            <option key={b} value={b}>
              {b}
            </option>
          ))}
        </Select>
      </label>

      {/* Agent 唤醒周期（仅实盘段）。回测走逐根决策、与墙钟无关，故不露出。 */}
      {mode === "live" && (
        <label className="flex flex-col gap-1 text-[11px] text-[var(--muted)]">
          <span>Agent 唤醒周期（分钟）</span>
          <input
            type="text"
            inputMode="decimal"
            value={wakeIntervalRaw}
            placeholder={String(AI_TRADE_DEFAULT_WAKE_INTERVAL_MIN)}
            disabled={submitting}
            onChange={(e) => setWakeIntervalRaw(e.target.value)}
            aria-label="Agent 唤醒周期（分钟）"
            className="rounded-md border border-[var(--border)] bg-[var(--bg)] px-2 py-1.5 text-xs text-[var(--text)] outline-none focus:border-[var(--accent)] disabled:opacity-50"
          />
          <span className="leading-relaxed text-[var(--muted)]">
            除按 K 线与策略触发外，每隔此分钟数 Agent 也会用
            <strong className="text-[var(--text)]">当前实时价</strong>重新研判；
            越小唤醒越频繁、LLM 调用越多；留空 / 0 = 仅按 K 线与策略触发。
          </span>
        </label>
      )}

      {mode === "backtest" && (
        <>
          <div className="flex flex-col gap-px overflow-hidden rounded-md bg-[var(--border)]">
            <Row
              label="计划决策轮数"
              value={
                <span className={roundsCapError ? "text-[var(--short-ink)]" : undefined}>
                  {plannedRounds} 轮 · 间隔 {everyValue} 根/次（上限 {AI_BACKTEST_MAX_ROUNDS}）
                </span>
              }
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <Field
              label="决策间隔（根/次）"
              placeholder={String(smartEvery)}
              value={everyRaw}
              onChange={setEveryRaw}
              disabled={submitting}
              hint="AI 每 K 根决策一次，越大越省 LLM（盯市/风控不降频）"
            />
            <Field
              label="共识门槛（可选）"
              placeholder="留空＝默认"
              value={minConsensusRaw}
              onChange={setMinConsensusRaw}
              disabled={submitting}
              hint="开仓需同向策略数 ≥ 此值，0=关闭"
            />
          </div>
        </>
      )}

      <Field
        label="上下文刷新轮数"
        placeholder={String(AI_TRADE_DEFAULT_CONTEXT_ROUNDS)}
        value={roundsRaw}
        onChange={setRoundsRaw}
        disabled={submitting}
        hint="每 N 轮把 AI 执行过程摘要为上下文、清空历史保持纯净（保留连续记忆的同时守住 token 预算）"
      />

      {/* 本步**已由自己的控件就地说出口**的风险（见 ALREADY_INLINE）不再走下面这条通用条目，
          否则同一句话会在同一屏上出现两次——§4.2.5 渲染规则 1 要的是「就地说一次」，
          不是「说两次」。清单外的风险由通用条目兜底：新加一条风险时二选一，别两边都不管。 */}
      {stepRisks
        .filter((risk) => !ALREADY_INLINE.has(risk.id))
        .map((risk) => (
          <InlineRiskNote
            key={risk.id}
            tone={risk.tone}
            title={risk.title}
            detail={risk.detail}
            testId={risk.id === "deepWakeTooShort" ? "ai-deep-wake-too-short" : undefined}
          />
        ))}
    </div>
  );
}
