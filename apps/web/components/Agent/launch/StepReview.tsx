"use client";

import { useState } from "react";
import {
  AI_TRADE_LAUNCH_STEPS,
  stepSummary,
  type AiTradeLaunchStepId,
} from "@/lib/aiTradeLaunchSteps";
import type { AiTradeLaunchRisk } from "@/lib/aiTradeLaunchRisk";
import type { UseAiTradeLaunchForm } from "./useAiTradeLaunchForm";

/**
 * 第 6 步：**我到底要启动一个什么东西**（ai-trade-guided-launch §4.2.1 / §4.2.5）。
 *
 * 三块内容，缺一不可：
 *  ① 逐步只读摘要 + 每组一个「修改」按钮（跳回产地那一步）；
 *  ② **风险清单**——不变式 I-1 的另一个渲染落点：`collectRisks()` 产出的每一条都**无条件**
 *     在这里再出现一次，带 `data-risk-id`。这是「一个从未翻到第 5 步的用户也看得见第 5 步的
 *     警示」的唯一保证；
 *  ③ 二次确认 + 真实资金知情 + 错误横幅 / remedies + 兜底行。
 */
export default function StepReview({
  form,
  submitting,
  errorRemedies,
  acknowledged,
  onAcknowledge,
}: {
  form: UseAiTradeLaunchForm;
  submitting: boolean;
  errorRemedies?: { id: string; label: string }[] | null;
  acknowledged: boolean;
  onAcknowledge: (value: boolean) => void;
}) {
  const {
    mode, gotoStep, summaryInput, risks, ready, firstError, blockingStep,
    tradabilityConfirmed, setTradabilityConfirmed,
    isUnguardedHorizon, unguardedConfirmed, setUnguardedConfirmed,
    setProfitGuardEnabled, setHorizonEnabled,
  } = form;
  /**
   * 上一次点过的 remedy 的**就地回执**（§4.2.6）。
   *
   * 那两个按钮把一个用户此刻**看不见的步骤**里的开关拨了过去；不说清拨的是哪一格，分步化就
   * 造出了一个新暗箱——用户只知道"点了一下、错误没了"，不知道自己的会话被改成了什么样。
   */
  const [remedyReceipt, setRemedyReceipt] = useState<{ text: string; step: AiTradeLaunchStepId } | null>(
    null,
  );

  return (
    <div className="flex flex-col gap-3">
      {/* ① 逐步只读摘要。 */}
      <div className="flex flex-col gap-px overflow-hidden rounded-md bg-[var(--border)]">
        {AI_TRADE_LAUNCH_STEPS.filter((step) => step.id !== "review").map((step) => (
          <div
            key={step.id}
            className="flex items-center justify-between gap-3 bg-[var(--bg)] px-3 py-2 text-xs"
            data-testid={`ai-launch-summary-${step.id}`}
          >
            <span className="shrink-0 text-[var(--muted)]">{step.title}</span>
            <span className="flex min-w-0 items-center gap-2">
              <span
                className="min-w-0 truncate text-right font-medium text-[var(--text)]"
                title={stepSummary(summaryInput, step.id)}
              >
                {stepSummary(summaryInput, step.id)}
              </span>
              <button
                type="button"
                disabled={submitting}
                onClick={() => gotoStep(step.id)}
                aria-label={`修改${step.title}`}
                className="shrink-0 rounded border border-[var(--border)] px-1.5 py-0.5 text-[10px] text-[var(--accent-ink)] hover:bg-[var(--surface-2)] disabled:opacity-40"
              >
                修改
              </button>
            </span>
          </div>
        ))}
      </div>

      {/* ② 风险清单（I-1 的可证伪落点：`data-risk-id` 集合 === `collectRisks()` 的 id 集合）。 */}
      <div className="flex flex-col gap-1.5" data-testid="ai-launch-risk-list">
        <span className="text-[11px] text-[var(--muted)]">
          风险与提示 · {risks.length === 0 ? "无" : `${risks.length} 条`}
        </span>
        {risks.length === 0 && (
          <p className="rounded border border-[var(--border)] bg-[var(--surface-2)] px-2 py-1 text-[10px] leading-relaxed text-[var(--muted)]">
            当前配置没有触发任何非阻断提示。这不代表这次交易没有风险，只代表启动前的确定性检查都通过了。
          </p>
        )}
        {risks.map((risk) => (
          <RiskItem
            key={risk.id}
            risk={risk}
            submitting={submitting}
            onGoto={gotoStep}
            tradabilityConfirmed={tradabilityConfirmed}
            onTradabilityConfirmedChange={setTradabilityConfirmed}
            unguardedConfirmed={unguardedConfirmed}
            onUnguardedConfirmedChange={setUnguardedConfirmed}
            showUnguardedAck={mode === "live" && isUnguardedHorizon}
          />
        ))}
      </div>

      {/* ③ 真实资金知情（仅实盘）。 */}
      {mode === "live" && (
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
            我已知晓这是<strong className="text-[var(--short-ink)]">真实资金 AI 自动交易</strong>
            ，AI 将对上述全部标的据所选策略信号自主决定开仓 / 平仓，盈亏由本人承担。
          </span>
        </label>
      )}

      {firstError && (
        <p role="alert" className="text-[11px] font-medium text-[var(--short-ink)]">
          {firstError}
        </p>
      )}

      {/*
        【v2 · P0-3】`ready === false` 而横幅为空时不留死路。今天唯一的成因是
        `directionError` / `trendError`（它们只进 `ready`、就地渲染在第 5 步的字段旁，从不进
        `firstError`），但判据**不写死在这两项上**：任何一个「进了 ready 却没进横幅」的校验量
        都会落在这里，而不是让启动按钮灰着且全屏没有一句理由。
      */}
      {!ready && !firstError && blockingStep && (
        <div
          className="flex items-center justify-between gap-2 rounded border border-[var(--short-ink)] bg-[var(--surface-2)] px-2 py-1.5 text-[11px] text-[var(--short-ink)]"
          data-testid="ai-launch-blocking-step"
        >
          <span>
            第 {AI_TRADE_LAUNCH_STEPS.findIndex((s) => s.id === blockingStep.id) + 1} 步（
            {blockingStep.title}）有未解决的输入错误
          </span>
          <button
            type="button"
            disabled={submitting}
            onClick={() => gotoStep(blockingStep.id)}
            className="shrink-0 rounded border border-[var(--short-ink)] px-1.5 py-0.5 text-[10px] font-medium hover:opacity-90 disabled:opacity-40"
          >
            去处理
          </button>
        </div>
      )}

      {errorRemedies && errorRemedies.length > 0 && (
        // ★ A-1：422 的三条**可执行**出路。文案逐条由后端下发（前端不自造），而每一条按钮
        // 真的把对应的开关拨过去——「知道坏了却不知道该点哪里」是本轮明确要消灭的形态。
        // `ops_override` 是运维侧动作（改 env 后重启后端），前端拨不动它，故只渲染成说明。
        <div className="flex flex-col gap-1.5" data-testid="ai-start-remedies">
          <div className="flex flex-wrap items-center gap-1.5">
            {errorRemedies.map((remedy) =>
              remedy.id === "ops_override" ? (
                <span
                  key={remedy.id}
                  className="rounded border border-dashed border-[var(--border)] px-2 py-0.5 text-[10px] text-[var(--muted)]"
                >
                  {remedy.label}
                </span>
              ) : (
                <button
                  key={remedy.id}
                  type="button"
                  disabled={submitting}
                  data-testid={`ai-start-remedy-${remedy.id}`}
                  onClick={() => {
                    if (remedy.id === "enable_profit_guard") {
                      setProfitGuardEnabled(true);
                      setRemedyReceipt({
                        text: `已为你开启利润保护（第 5 步 · ${stepTitleOf("guards")}）`,
                        step: "guards",
                      });
                    }
                    if (remedy.id === "disable_horizon") {
                      setHorizonEnabled(false);
                      setRemedyReceipt({
                        text: `已为你关闭持仓周期约束（第 2 步 · ${stepTitleOf("rhythm")}）`,
                        step: "rhythm",
                      });
                    }
                  }}
                  className="rounded border border-[var(--accent)] bg-[var(--accent-soft)] px-2 py-0.5 text-[10px] font-medium text-[var(--accent-ink)] hover:opacity-90 focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--accent)] disabled:opacity-50"
                >
                  {remedy.label}
                </button>
              ),
            )}
          </div>
          {remedyReceipt && (
            <p
              className="text-[10px] leading-relaxed text-[var(--accent-ink)]"
              data-testid="ai-start-remedy-receipt"
            >
              {remedyReceipt.text}
              <button
                type="button"
                disabled={submitting}
                onClick={() => gotoStep(remedyReceipt.step)}
                className="ml-1.5 rounded border border-[var(--border)] px-1.5 py-0.5 hover:bg-[var(--surface-2)] disabled:opacity-40"
              >
                去看看
              </button>
            </p>
          )}
          {/* 【v2 · P1-7】这一行的所有者是前端，不是 `remedies`——后端错误体一个字节不改。
              运维总闸在本产品里有三个落点，说不清是哪一个，用户就只能猜。 */}
          <p
            className="text-[10px] leading-relaxed text-[var(--muted)]"
            data-testid="ai-start-remedy-ops-hint"
          >
            运维总闸的三个落点：后端进程环境变量、services/quant/.env、桌面端的 agent.env
            （按 AI_TRADE_ 前缀整段透传给后端子进程）。改完需重启后端才生效。
          </p>
        </div>
      )}
    </div>
  );
}

/** 步骤标题的唯一真源仍是 `AI_TRADE_LAUNCH_STEPS`（G-3：本文件不出现字面量标题）。 */
function stepTitleOf(id: AiTradeLaunchStepId): string {
  return AI_TRADE_LAUNCH_STEPS.find((step) => step.id === id)?.title ?? "";
}

/** 风险清单里的一条。需要一次显式勾选的，勾选框就长在这条下面（不让用户去别处找）。 */
function RiskItem({
  risk,
  submitting,
  onGoto,
  tradabilityConfirmed,
  onTradabilityConfirmedChange,
  unguardedConfirmed,
  onUnguardedConfirmedChange,
  showUnguardedAck,
}: {
  risk: AiTradeLaunchRisk;
  submitting: boolean;
  onGoto: (id: AiTradeLaunchStepId) => void;
  tradabilityConfirmed: boolean;
  onTradabilityConfirmedChange: (value: boolean) => void;
  unguardedConfirmed: boolean;
  onUnguardedConfirmedChange: (value: boolean) => void;
  showUnguardedAck: boolean;
}) {
  const color = risk.tone === "danger" ? "var(--short-ink)" : "var(--warn)";
  const target = risk.steps[0];
  return (
    <div
      data-risk-id={risk.id}
      data-tone={risk.tone}
      className="flex flex-col gap-1 rounded border bg-[var(--surface-2)] px-2 py-1.5 text-[10px] leading-relaxed"
      style={{ borderColor: color }}
    >
      <div className="flex items-start justify-between gap-2">
        <span className="font-medium" style={{ color }}>
          {/* 形状 + 文字双通道，不靠颜色单独承载语义（仓库既有可达性纪律）。 */}
          {risk.tone === "danger" ? "⚠ " : "△ "}
          {risk.title}
        </span>
        <button
          type="button"
          disabled={submitting}
          onClick={() => onGoto(target)}
          className="shrink-0 rounded border border-[var(--border)] px-1.5 py-0.5 text-[10px] text-[var(--accent-ink)] hover:bg-[var(--bg)] disabled:opacity-40"
        >
          去处理
        </button>
      </div>
      <span className="text-[var(--text)]">{risk.detail}</span>
      {risk.id === "tradabilityInfeasible" && (
        <label className="flex cursor-pointer items-start gap-2 text-[var(--short-ink)]">
          <input
            type="checkbox"
            checked={tradabilityConfirmed}
            disabled={submitting}
            onChange={(e) => onTradabilityConfirmedChange(e.target.checked)}
            aria-label="仍要在该周期上启动"
            className="mt-0.5 h-3.5 w-3.5 shrink-0 accent-[var(--short)]"
          />
          <span>
            我已知晓当前周期上绝大多数开仓意图会被最小 edge 门禁否决，<strong>仍要启动</strong>。
          </span>
        </label>
      )}
      {risk.id === "profitGuardOff" && showUnguardedAck && (
        <label className="flex cursor-pointer items-start gap-2 text-[var(--short-ink)]">
          <input
            type="checkbox"
            checked={unguardedConfirmed}
            disabled={submitting}
            onChange={(e) => onUnguardedConfirmedChange(e.target.checked)}
            className="mt-0.5 h-3.5 w-3.5 shrink-0 accent-[var(--short)]"
            data-testid="ai-profit-guard-unguarded-confirm"
            aria-label="我已了解关闭利润保护的后果，仍要启动"
          />
          <span>
            我已了解上述后果，仍要在<strong>没有任何自动兑现通道</strong>的情况下启动。
          </span>
        </label>
      )}
    </div>
  );
}
