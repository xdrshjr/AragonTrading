"use client";

import { useEffect, useRef } from "react";
import { Badge, Button, Modal } from "@/components/ui";
import {
  AI_TRADE_LAUNCH_STEPS,
  stepSummary,
  type AiTradeLaunchStepId,
} from "@/lib/aiTradeLaunchSteps";
import { risksForStep } from "@/lib/aiTradeLaunchRisk";
import type {
  AiTradeBaseRules,
  AiTradeDirectionRule,
  AiTradeDisciplineIn,
  AiTradeHorizonRule,
  AiTradeIntelRule,
  AiTradeMaBandRule,
  AiTradeProfitGuardRule,
  AiTradeProfitLockRule,
  AiTradeReasoningDepth,
  AiTradeTrendRule,
} from "@/lib/aiTradeTypes";
import type { AiTradeExperienceRule } from "@/lib/experienceTypes";
import type { AutoTradeAllocationMode, AutoTradeMember } from "@/lib/liveAutoTypes";
import type { AiTradeTrajectoryRule } from "@/lib/trajectoryTypes";
import AiTradeLaunchStepRail, {
  type AiTradeLaunchStepView,
} from "./launch/AiTradeLaunchStepRail";
import { ModeTab } from "./launch/LaunchPrimitives";
import StepCapital from "./launch/StepCapital";
import StepCognition from "./launch/StepCognition";
import StepGuards from "./launch/StepGuards";
import StepReview from "./launch/StepReview";
import StepRhythm from "./launch/StepRhythm";
import StepScope from "./launch/StepScope";
import { useAiTradeLaunchForm } from "./launch/useAiTradeLaunchForm";

export type AiTradeLaunchMode = "live" | "backtest";

export interface AiTradeLaunchOpts {
  mode: AiTradeLaunchMode;
  members: { instId: string; allocationPct: number }[]; // 实盘多标的（回测取 members[0] 兜底）
  allocationMode: AutoTradeAllocationMode;               // even | max_ratio
  instId: string;                                        // 回测单标的（members[0] 兜底）
  bar: string;                                           // 可编辑周期（取代对 store.bar 的直读）
  strategyIds: number[];
  capitalUsd: number | null;       // live=分组总预算；backtest=初始资金（留空＝服务端默认）
  leverage: number | null;
  takeProfitUsd: number | null;
  stopLossUsd: number | null;
  stopLossPct: number | null;      // E4：单仓 OCO 止损%（fraction，留空＝config 默认）
  takeProfitPct: number | null;    // E4：单仓 OCO 止盈%
  positionStepPct: number;         // 仓位操作档位步长（fraction；AI 每次开仓规模只能落在该档位阶梯上）
  // 【基础策略】铁律（实盘/回测两 tab 同得，实盘与回测的默认必须一致，P1-4）：勾选 ⇒ 带完整参数；
  // 取消勾选 ⇒ `{maBandEntry:{enabled:false}}`（**显式关闭**，与「字段缺省=默认开启」语义严格区分）。
  baseRules: AiTradeBaseRules;
  // 利润保护层（**实盘与回测同源**，adaptive §7.2a）：回测改用 bar 内 OHLC 极值 + 保守成交序后，
  // 该字段两模式都提交。勾选 ⇒ 带完整参数；取消勾选 ⇒ `{enabled:false}`（显式关闭）。
  profitGuard?: AiTradeProfitGuardRule;
  // 收益锁（profit-lock C-2，**实盘与回测同源**）：开 ⇒ 只带用户改过的字段；关 ⇒ `{enabled:false}`。
  profitLock?: AiTradeProfitLockRule;
  // 交易周期模式（**实盘与回测同源**，holding-horizon §4.3）：选中模式 ⇒ 带 `{enabled,profile,…}`；
  // 取消勾选 ⇒ `{enabled:false}`（**显式关闭**，与「字段缺省=默认开启」语义严格区分）。
  tradingHorizon?: AiTradeHorizonRule;
  // 经验模式（experience-context-graph §7.2）：勾选 ⇒ 带 `{enabled,scope,write}`；取消勾选 ⇒
  // `{enabled:false}`（**显式关闭**——后者是老客户端没发这个字段，前者是用户亲手取消了勾选，
  // 合成一个判据用户就再也关不掉这个功能）。
  experience?: AiTradeExperienceRule;
  // 市场情报研判（intel-session-binding 轴 A）：**仅实盘下发**（情报层不进回测车道）。
  // ⚠️ 与上面六个字段的方向相反：这一项的**系统默认就是关闭**，「整键缺省 ⇒ 服务端不注入」。
  intel?: AiTradeIntelRule;
  // 后训练轨迹采集（trajectory-capture §2.13.2）：**只在有效开启时携带**——关闭 / 配置读不到 /
  // 运维关 ⇒ `undefined` ⇒ 启动体整键缺席。仅 live 分支下发。
  trajectoryCapture?: AiTradeTrajectoryRule;
  // 用户在卡片上的**原始**选择（与生效值无关），只供 `AiTradeToggle` 记偏好（【v2 · RV3】）。
  trajectoryPreference?: AiTradeTrajectoryRule;
  // 交易纪律预设（**实盘与回测同源**，discipline-preset R-3）：只带 `{preset}`，阈值一律由服务端
  // 的冻结表决定。整键不发 ⇒ 服务端按 `ai_trade_discipline_default_preset` 装配。
  discipline?: AiTradeDisciplineIn;
  // 方向对称性 / 逆势通路（**实盘与回测同源**，directional-symmetry §7.1）。
  direction?: AiTradeDirectionRule;
  // 执行周期趋势基调（**实盘与回测同源**，execution-trend-verdict §5.1）。
  trend?: AiTradeTrendRule;
  contextRefreshRounds: number;
  // Agent 墙钟定时唤醒周期（分钟）：[RN-1] 声明为**可选**，仅 live 分支透传、base 不含、回测省略。
  wakeIntervalMinutes?: number | null;
  // 研判深度档（deep-reasoning-chain §3.5.3 / P2-2）：与 `wakeIntervalMinutes` 同款——**仅 live
  // 分支透传、base 不含**。回测档只读展示，改档走设置页。
  reasoningDepth?: AiTradeReasoningDepth | null;
  // 回测专属（mode==="backtest" 时有效）
  startTs?: number;
  endTs?: number;
  decisionEveryBars?: number | null;   // UI 已解析为智能默认，永不因越界自拒（R5）
  minConsensus?: number | null;
}

/**
 * 启动弹窗的对外 props（**逐字段与拆分前相同**——`AiTradeToggle.tsx` 的 import 与传参零改动）。
 *
 * 抽成具名 interface 只为了让 `useAiTradeLaunchForm` 能以 `import type` 引用它；类型侧的 import
 * 在编译期被整句擦除，不构成运行时环。
 */
export interface AiTradeLaunchModalProps {
  open: boolean;
  onClose: () => void;
  onConfirm: (opts: AiTradeLaunchOpts) => void;
  submitting: boolean;
  acknowledged: boolean;
  onAcknowledge: (value: boolean) => void;
  bar: string;                        // K 线所选周期（回填「时间级别」下拉默认值）
  defaultMembers: AutoTradeMember[];  // 来自 autoTradeMembers（可 > 上限，弹窗内截断并提示，P1-2）
  defaultAllocationMode: AutoTradeAllocationMode;   // 来自 store.autoTradeAllocationMode（P1-2）
  defaultInstId: string;              // 当前图表标的（autoTradeMembers 空时回退单成员）
  defaultStrategyIds: number[];
  defaultContextRounds: number;
  defaultPositionStepPct: number;     // 上次所选仓位操作档位（回填弹窗）
  defaultWakeIntervalMinutes: number; // 上次所选 Agent 唤醒周期（回填弹窗）
  // 上次所选研判深度档（可选：老 localStorage 无此键 ⇒ 回落 AI_TRADE_DEFAULT_REASONING_DEPTH）。
  defaultReasoningDepth?: AiTradeReasoningDepth;
  defaultBaseRule: AiTradeMaBandRule; // 上次所选【基础策略】参数
  defaultProfitGuard: AiTradeProfitGuardRule; // 上次所选利润保护参数
  // 上次所选交易周期模式；**无本地预设兜底**——模式表由服务端下发，未选过时留 undefined 并采用
  // 接口返回的 defaultProfile（前端不复制任何一个预设数值，R-3）。
  defaultTradingHorizon?: AiTradeHorizonRule;
  defaultExperience?: AiTradeExperienceRule;   // 上次所选经验模式（缺省 ⇒ 本地默认全开）
  // 上次所选的情报研判偏好（缺省 ⇒ `AI_TRADE_INTEL_RULE_DEFAULT`，即**不勾**）。
  defaultIntel?: AiTradeIntelRule;
  // 上次所选的后训练数据采集偏好（缺省 ⇒ `AI_TRADE_TRAJECTORY_RULE_DEFAULT`，即**不勾**）。
  defaultTrajectoryCapture?: AiTradeTrajectoryRule;
  // 上次填过的单仓 OCO（诊断根因 ③：每次都被清空 ⇒ 用户不知不觉一直用 10%/5% 的宽默认）。
  defaultStopLossPct?: number;
  defaultTakeProfitPct?: number;
  error?: string | null;
  // ai-trade-loss-and-liveness-hardening A-1：422 `AI_TRADE_UNGUARDED_HORIZON` 随错误体带回的
  // **可执行补救**。文案由后端下发、前端只负责把它渲染成按钮并把对应的开关真的拨过去。
  errorRemedies?: { id: string; label: string }[] | null;
  liveGateReason?: string | null;     // 实盘不可用原因（非 null → 段控禁用实盘、默认落回测，R6）
  defaultStartTs?: number;
  defaultEndTs?: number;
  defaultDecisionEveryBars?: number;
}

/**
 * 启动 AI 自动交易弹窗 —— **六步引导式向导的壳**（ai-trade-guided-launch 轴 B）。
 *
 * 本文件只做四件事：调 `useAiTradeLaunchForm`、渲染步骤轨、渲染当前步、渲染底部操作条。
 * 全部受控状态、派生量、取数与提交判据都住在那个 hook 里；六个 `Step*.tsx` 是纯视图。
 *
 * 改造前它是 1937 行、一屏 20 个分区、一次性铺开的长滚动表单——**一次要求用户同时做 20 个决定，
 * 等于一个都没有真正被决定**。分步化唯一的危险是「藏起来的警示等于消失的警示」，那由
 * `lib/aiTradeLaunchRisk.ts` 的不变式 I-1 消灭，而不是靠实现者的记性。
 */
export default function AiTradeLaunchModal(props: AiTradeLaunchModalProps) {
  const { open, onClose, submitting, acknowledged, onAcknowledge, errorRemedies = null } = props;
  const form = useAiTradeLaunchForm(props);
  const {
    mode, changeMode, liveAvailable, liveGateReason, stepId, gotoStep, visibleSteps,
    stepErrorOf, inputStepsClean, summaryInput, risks, ready, handleConfirm,
    tradabilityNeedsConfirm, visitedSteps,
  } = form;

  const headingRef = useRef<HTMLHeadingElement>(null);
  const currentIndex = Math.max(0, visibleSteps.findIndex((s) => s.id === stepId));
  const current = visibleSteps[currentIndex] ?? visibleSteps[0];
  const total = visibleSteps.length;
  const isLast = current?.id === "review";
  const currentError = current ? stepErrorOf(current.id) : null;

  // 切步后把焦点移到内容区标题（N-6）。它**不写任何表单 state**，与
  // `ai-trade-modal-revert-recurrence` 那类「把正在编辑的表单冲回默认」的 effect 无关。
  useEffect(() => {
    if (!open) return;
    headingRef.current?.focus();
  }, [stepId, open]);

  // 全局 `Alt+←/→` 上/下一步（与轨是否有焦点无关）。`Esc` 仍然是「关闭弹窗」，**不**劫持为
  // 「上一步」——两义会造成误关或误退，而弹窗承载的是一次真实资金授权（N-7）。
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (!e.altKey) return;
      const delta = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
      if (delta === 0) return;
      const next = visibleSteps[currentIndex + delta];
      if (!next) return;
      e.preventDefault();
      gotoStep(next.id);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, currentIndex, visibleSteps, gotoStep]);

  // 状态点的定级顺序：**有错 > 有警示 > 已访问 > 未访问**（N-4）。「当前」不占一个 `data-state`
  // 取值——它已经由 `aria-selected` 与底色表达，再挤进来就会盖掉「这一步有错」这条更要紧的信息。
  const views: AiTradeLaunchStepView[] = visibleSteps.map((step, i) => {
    const error = stepErrorOf(step.id);
    const warn = risksForStep(risks, step.id).length > 0;
    const visited = visitedSteps.includes(step.id);
    return {
      id: step.id,
      title: step.title,
      index: i + 1,
      state: error ? "error" : warn ? "warn" : visited ? "edited" : "default",
      current: step.id === stepId,
      summary: stepSummary(summaryInput, step.id),
      error,
    };
  });

  const title = mode === "backtest" ? "AI 自动交易 · 回测" : "启动 AI 自动交易";
  const ctaLabel =
    mode === "backtest"
      ? submitting
        ? "回测启动中…"
        : "开始 AI 回测"
      : submitting
        ? "启动中…"
        : tradabilityNeedsConfirm
          ? "仍要启动 AI 自动交易"
          : "确认并开始 AI 自动交易";

  return (
    <Modal
      open={open}
      onClose={onClose}
      labelledBy="ai-trade-launch-title"
      className="max-w-3xl"
      initialFocusRef={headingRef}
    >
      <div className="flex items-center justify-between border-b border-[var(--border)] px-5 py-3.5">
        <h2 id="ai-trade-launch-title" className="text-sm font-semibold text-[var(--text)]">
          {title}
        </h2>
        {mode === "backtest" ? <Badge tone="accent">模拟回测</Badge> : <Badge tone="short">真实资金</Badge>}
      </div>

      {/* 模式段控（实盘 / 回测）：它决定整条向导的形状，故留在壳里、六步之外。 */}
      <div className="px-5 pt-3">
        <div className="flex gap-1 rounded-md bg-[var(--surface-2)] p-0.5">
          <ModeTab
            active={mode === "live"}
            disabled={!liveAvailable || submitting}
            title={liveGateReason ?? undefined}
            onClick={() => changeMode("live")}
          >
            实盘
          </ModeTab>
          <ModeTab active={mode === "backtest"} disabled={submitting} onClick={() => changeMode("backtest")}>
            回测
          </ModeTab>
        </div>
        {/* 这一段说的是「你正在授权的是什么」，与具体某一步无关，故与段控同住壳里、六步之外。
            拆分时把它丢掉过一次——一个上来就问「对谁交易」却从没说清自己是什么的向导，
            比一屏铺开的长表单更让人不安。 */}
        <p className="mt-2 text-[11px] leading-relaxed text-[var(--muted)]">
          {mode === "backtest" ? (
            <>
              AI 将在所选历史区间上<strong className="text-[var(--text)]">逐根决策</strong>（纯模拟撮合、
              不下真单、不动真实资金），完成后展示全套回测指标与 AI 决策过程，可先评估再决定是否投入真实资金。
            </>
          ) : (
            <>
              AI 助手将作为决策者，综合研判所选<strong className="text-[var(--text)]">多个策略</strong>的实时信号，
              对下方<strong className="text-[var(--text)]">全部标的</strong>同时在每根 K 线上决定开多 / 开空 /
              平仓 / 观望，真单经既有守护执行器落地并继承全部资金护栏。
            </>
          )}
        </p>
      </div>

      {/* 两栏：左轨固定、右内容独立滚动（≥md）；窄档折成单列、整块一起滚。
          轨若跟着内容一起滚，用户翻到第 6 步时就再也看不见自己走到哪了——那正是分步 UI 唯一
          比单屏更差的一种收场。 */}
      <div className="flex max-h-[62vh] flex-col gap-3 overflow-auto px-5 py-3 md:flex-row md:gap-4 md:overflow-hidden">
        <AiTradeLaunchStepRail
          steps={visibleSteps}
          views={views}
          currentId={stepId}
          onSelect={gotoStep}
        />

        <div
          role="tabpanel"
          id={`ai-launch-panel-${current?.id ?? "scope"}`}
          aria-labelledby={`ai-launch-tab-${current?.id ?? "scope"}`}
          className="ai-launch-panel flex min-w-0 flex-1 flex-col gap-3 md:min-h-0 md:overflow-y-auto md:pr-1"
          key={current?.id}
        >
          <div className="flex flex-col gap-0.5">
            <h3
              ref={headingRef}
              tabIndex={-1}
              className="text-xs font-semibold text-[var(--text)] outline-none"
            >
              {current?.title}
            </h3>
            <p className="text-[10px] leading-relaxed text-[var(--muted)]">{current?.question}</p>
          </div>

          {current?.id === "scope" && <StepScope form={form} submitting={submitting} />}
          {current?.id === "rhythm" && <StepRhythm form={form} submitting={submitting} />}
          {current?.id === "capital" && <StepCapital form={form} submitting={submitting} />}
          {current?.id === "cognition" && <StepCognition form={form} submitting={submitting} />}
          {current?.id === "guards" && <StepGuards form={form} submitting={submitting} />}
          {current?.id === "review" && (
            <StepReview
              form={form}
              submitting={submitting}
              errorRemedies={errorRemedies}
              acknowledged={acknowledged}
              onAcknowledge={onAcknowledge}
            />
          )}
        </div>
      </div>

      {/* 读屏播报：切步即播「第 N 步，共 M 步：<标题>」。 */}
      <p aria-live="polite" className="sr-only">
        第 {currentIndex + 1} 步，共 {total} 步：{current?.title}
      </p>

      <div className="flex items-center justify-between gap-2 border-t border-[var(--border)] px-5 py-3">
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="sm" onClick={onClose} disabled={submitting}>
            取消
          </Button>
          {currentIndex > 0 && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => gotoStep(visibleSteps[currentIndex - 1].id)}
              disabled={submitting}
            >
              上一步
            </Button>
          )}
        </div>
        <div className="flex items-center gap-2">
          {/*
            N-2：本步错误只**提示**、不拦截——N-1 允许任意跳转，用户点一下轨就绕过去了。
            同屏渲染该错误原文（不是一个笼统的「请检查输入」）。
          */}
          {!isLast && currentError && (
            <span className="max-w-[18rem] truncate text-[10px] text-[var(--short-ink)]" title={currentError}>
              {currentError}
            </span>
          )}
          {/*
            N-10「跳到确认」：判据是 `inputStepsClean`（第 1–5 步各自的 `stepError` 全为 null），
            **不是 `ready`**——live 段的 `ready` 与 `acknowledged` 合取，而那个勾选框本身就在第 6 步、
            且刷新即复位，用 `ready` 会让这个出口恰好对每一次刷新后的首次启动都不存在。
          */}
          {!isLast && inputStepsClean && (
            <Button variant="ghost" size="sm" onClick={() => gotoStep("review")} disabled={submitting}>
              跳到确认
            </Button>
          )}
          {isLast ? (
            // 按钮**永不因预检而禁用**（轴 A 的 fail-open 极性）：`infeasible` 只把文案改成
            // 「仍要启动」并在提交路径上要一次显式确认。
            <Button variant="primary" size="sm" onClick={handleConfirm} disabled={!ready || submitting}>
              {ctaLabel}
            </Button>
          ) : (
            <Button
              variant="primary"
              size="sm"
              onClick={() => gotoStep(visibleSteps[currentIndex + 1].id)}
              disabled={submitting || !!currentError}
            >
              下一步
            </Button>
          )}
        </div>
      </div>
    </Modal>
  );
}

/** 步骤 id 的对外别名（用例与 `AiTradeToggle` 可据它跳步；唯一真源仍是 `AI_TRADE_LAUNCH_STEPS`）。 */
export type { AiTradeLaunchStepId };
export { AI_TRADE_LAUNCH_STEPS };
