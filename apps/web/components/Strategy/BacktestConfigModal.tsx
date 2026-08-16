"use client";

import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Button, Modal, Skeleton } from "@/components/ui";
import { api } from "@/lib/api";
import {
  BARS,
  BAR_LABEL,
  PRESET_LABEL,
  RANGE_PRESETS,
  estimateBars,
  localInputToTs,
  presetToRange,
  refreshPresetRange,
  tsToLocalInput,
  validateConfig,
} from "@/lib/backtestConfig";
import { downloadCsv, equityToCsv } from "@/lib/export";
import { fmtTime } from "@/lib/format";
import type { BacktestConfig, BacktestResult, Bar, RangePreset } from "@/lib/types";
import BacktestMetricsGrid from "./BacktestMetricsGrid";
import EquitySparkline from "./EquitySparkline";

const TITLE_ID = "backtest-config-modal-title";

// 两步向导：① 配置 → ② 结果（结果独立成第二步，backtest-modal-two-step-results §2.3）。
type Step = "config" | "result";

interface ProvenanceHint {
  startTs: number;
  endTs: number;
  instId?: string;
  bar?: string;
}

// 成本输入行（数字 + 单位 + 提示），复用设计 token，明暗自适应。
function CostField({
  label,
  value,
  unit,
  step,
  min,
  onChange,
}: {
  label: string;
  value: number;
  unit?: string;
  step?: number;
  min?: number;
  onChange: (n: number) => void;
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[11px] text-[var(--muted)]">{label}</span>
      <div className="flex items-center gap-1.5">
        <input
          type="number"
          value={Number.isFinite(value) ? value : ""}
          min={min}
          step={step ?? "any"}
          onChange={(e) => onChange(parseFloat(e.target.value))}
          className="tabular w-full rounded-[8px] border border-[var(--border)] bg-[var(--surface)] px-2 py-1 text-xs outline-none focus:border-[var(--accent)]"
        />
        {unit && <span className="shrink-0 text-[11px] text-[var(--muted)]">{unit}</span>}
      </div>
    </label>
  );
}

// 可空百分比输入（止盈止损用）：空串 → null（而非 NaN），供「留空＝沿用策略自带」语义（§三 #8）。
function OptionalPctField({
  label,
  value,
  placeholder,
  onChange,
}: {
  label: string;
  value: number | null | undefined;
  placeholder?: string;
  onChange: (n: number | null) => void;
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[11px] text-[var(--muted)]">{label}</span>
      <div className="flex items-center gap-1.5">
        <input
          type="number"
          value={value == null || !Number.isFinite(value) ? "" : value}
          min={0}
          step="any"
          placeholder={placeholder}
          onChange={(e) => {
            const raw = e.target.value;
            onChange(raw === "" ? null : parseFloat(raw));
          }}
          className="tabular w-full rounded-[8px] border border-[var(--border)] bg-[var(--surface)] px-2 py-1 text-xs outline-none focus:border-[var(--accent)]"
        />
        <span className="shrink-0 text-[11px] text-[var(--muted)]">%</span>
      </div>
    </label>
  );
}

/**
 * 运行回测配置 + 运行 + 结果弹窗（backtest-modal-two-step-results §2）。
 *
 * 两步向导（wizard）：**第一步「配置」**承载回测区间（预设 + 自定义起止）、K 线周期、初始资金、手续费、
 * 滑点、杠杆及其即时校验；用户点「开始回测」后**推进到第二步「结果」**——一个独立、聚焦的结果面板，专门
 * 呈现运行骨架 / 覆盖度横幅 / 专业绩效指标网格 / 净值缩略图，并可随时「← 返回上一步」微调重跑。运行态 /
 * 结果 / 错误经 props 观察同一 mutation（单一数据源，不另起状态）；`step` 只决定展示哪一屏。完全复用
 * `Modal` 的焦点陷阱 / Esc / 遮罩关闭等无障碍原语。
 */
export default function BacktestConfigModal({
  open,
  onClose,
  strategyName,
  instId,
  warmupBars,
  provenanceHint,
  initialConfig,
  running,
  result,
  errorMessage,
  onRun,
  riskOverrideEnabled = true,
}: {
  open: boolean;
  onClose: () => void;
  strategyName: string;
  instId: string;
  warmupBars?: number;
  provenanceHint?: ProvenanceHint | null;
  initialConfig: BacktestConfig;
  running: boolean;
  result?: BacktestResult;
  errorMessage?: string | null;
  onRun: (cfg: BacktestConfig) => void;
  // 止盈止损分区门控（backtest-stop-loss-take-profit §2.4）：原子/因子默认 true（渲染 + 校验 SL/TP）；
  // 组合显式 false（不渲染、跳过校验）。wire 层隔离在 StrategyPanel.runBacktest（R-1）。
  riskOverrideEnabled?: boolean;
}) {
  const [cfg, setCfg] = useState<BacktestConfig>(initialConfig);
  // 当前展示的步骤（config=配置屏，result=结果屏）。
  const [step, setStep] = useState<Step>("config");
  // 本次「打开」内是否已从弹窗触发过运行：既避免跨策略陈旧结果误显，又门控「② 结果」的前进回看可点性（R-1）。
  const [ranThisSession, setRanThisSession] = useState(false);
  const seededRef = useRef(false);
  // 焦点落点：进入结果屏落到「回测结果」小标题；返回配置屏落到配置区首个可交互控件（R-2）。
  const resultHeadingRef = useRef<HTMLHeadingElement>(null);
  const configBodyRef = useRef<HTMLDivElement>(null);
  // 记住上一步骤：只在真正的步骤切换时移焦，挂载 / 开合不抢 Modal 的默认焦点。
  const prevStepRef = useRef<Step | null>(null);

  // 打开时一次性以 initialConfig 播种，并按 now 重派生预设区间（保证「近 30 天」始终新鲜）；恒回第一步、清空「已运行」。
  useEffect(() => {
    if (!open) {
      seededRef.current = false;
      return;
    }
    if (seededRef.current) return;
    seededRef.current = true;
    setCfg(refreshPresetRange(initialConfig, Math.floor(Date.now() / 1000)));
    setStep("config");
    setRanThisSession(false);
  }, [open, initialConfig]);

  // 步骤切换时同步移焦（useLayoutEffect：在同一提交阶段闭合「按钮卸载→焦点回落 body」的窗口，R-2）。
  useLayoutEffect(() => {
    if (!open) {
      prevStepRef.current = null;
      return;
    }
    const prev = prevStepRef.current;
    prevStepRef.current = step;
    if (prev === null || prev === step) return; // 挂载 / 无切换：不抢 Modal 默认焦点
    if (step === "result") {
      resultHeadingRef.current?.focus({ preventScroll: true });
    } else {
      const body = configBodyRef.current;
      const first = body?.querySelector<HTMLElement>(
        'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
      );
      (first ?? body)?.focus({ preventScroll: true });
    }
  }, [step, open]);

  // 净值 CSV 导出所需的净值数据（与 EquitySparkline / StrategyPanel 同 queryKey，共享缓存）。
  const runId = result?.runId;
  const equityQ = useQuery({
    queryKey: ["equity", runId],
    queryFn: () => api.equity(runId as number),
    enabled: !!runId && open,
  });

  const patch = (part: Partial<BacktestConfig>) => setCfg((prev) => ({ ...prev, ...part }));

  const pickPreset = (preset: RangePreset) => {
    if (preset === "custom") {
      patch({ rangePreset: "custom" });
      return;
    }
    patch({ rangePreset: preset, ...presetToRange(preset, Math.floor(Date.now() / 1000)) });
  };

  const setBound = (key: "startTs" | "endTs", raw: string) => {
    const ts = localInputToTs(raw);
    if (ts !== null) patch({ [key]: ts } as Partial<BacktestConfig>);
  };

  const isCustom = cfg.rangePreset === "custom";
  const bars = estimateBars(cfg.bar, cfg.startTs, cfg.endTs);
  const { ok, errors, warnings } = validateConfig(cfg, warmupBars, riskOverrideEnabled);
  const runDisabled = !ok || running;

  // 样本内 / 外自查（因子权重训练区间重叠即偏乐观，沿用 StrategyDetail 口径）。
  const inSample =
    provenanceHint != null && cfg.startTs < provenanceHint.endTs && cfg.endTs > provenanceHint.startTs;

  // 推进到结果屏：先切屏（显骨架）再触发运行；`ranThisSession` 让「② 结果」此后可前进回看。
  const triggerRun = () => {
    if (runDisabled) return;
    setRanThisSession(true);
    setStep("result");
    onRun(cfg);
  };
  const goBack = () => setStep("config"); // 返回配置屏：cfg 与 ranThisSession 均不重置，可微调 / 可回看
  const goForward = () => {
    if (ranThisSession) setStep("result"); // 前进回看现有结果，不触发重跑（R-1）
  };

  const hasEquity = !!equityQ.data?.length;
  const exportEquity = () => {
    if (runId && equityQ.data) {
      downloadCsv(`aragon-equity-run${runId}.csv`, equityToCsv(equityQ.data));
    }
  };

  return (
    <Modal open={open} onClose={onClose} labelledBy={TITLE_ID}>
      <header className="border-b border-[var(--border)] px-5 py-4">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 id={TITLE_ID} className="text-sm font-semibold text-[var(--text)]">
              运行回测
            </h2>
            <p className="mt-0.5 truncate text-[11px] text-[var(--muted)]">
              {strategyName} · {instId} · 标的只读
            </p>
          </div>
          <Button variant="ghost" size="sm" aria-label="关闭" onClick={onClose}>
            ✕
          </Button>
        </div>
        <StepIndicator step={step} ran={ranThisSession} onBack={goBack} onForward={goForward} />
      </header>

      <div className="flex-1 overflow-y-auto px-5 py-4">
        {step === "config" ? (
          <ConfigStep
            bodyRef={configBodyRef}
            cfg={cfg}
            patch={patch}
            pickPreset={pickPreset}
            setBound={setBound}
            isCustom={isCustom}
            bars={bars}
            errors={errors}
            warnings={warnings}
            provenanceHint={provenanceHint}
            inSample={inSample}
            instId={instId}
            riskOverrideEnabled={riskOverrideEnabled}
          />
        ) : (
          <ResultStep
            headingRef={resultHeadingRef}
            running={running}
            result={result}
            errorMessage={errorMessage}
          />
        )}
      </div>

      {/* 页脚按 step 切换：第一步「开始回测」，第二步「返回上一步」+ 结果态操作条 */}
      {step === "config" ? (
        <div className="flex items-center justify-between gap-3 border-t border-[var(--border)] px-5 py-3">
          <span className="text-[11px] text-[var(--muted)]">
            配置区间与参数后点「开始回测」，进入下一步查看结果
          </span>
          <Button
            variant="primary"
            size="sm"
            disabled={runDisabled}
            title={!ok ? errors.join("；") : undefined}
            onClick={triggerRun}
          >
            {running ? "回测中…" : "开始回测"}
          </Button>
        </div>
      ) : (
        <div className="flex items-center justify-between gap-3 border-t border-[var(--border)] px-5 py-3">
          <Button variant="ghost" size="sm" onClick={goBack}>
            ← 返回上一步
          </Button>
          <div className="flex items-center gap-2">
            {running ? null : errorMessage ? (
              <Button
                variant="primary"
                size="sm"
                disabled={runDisabled}
                title={!ok ? errors.join("；") : undefined}
                onClick={triggerRun}
              >
                重新回测
              </Button>
            ) : result ? (
              <>
                <Button variant="ghost" size="sm" disabled={!hasEquity} onClick={exportEquity}>
                  导出净值 CSV
                </Button>
                <Button variant="default" size="sm" onClick={onClose}>
                  在主图查看
                </Button>
              </>
            ) : null}
          </div>
        </div>
      )}
    </Modal>
  );
}

// 步骤指示器：① 配置 —— ② 结果，两段均为真正的 <button>（当前段 aria-current、已完成段可回退、
// 未达段 disabled；已运行后「② 结果」可前进回看），键盘可达（R-5）。
function StepIndicator({
  step,
  ran,
  onBack,
  onForward,
}: {
  step: Step;
  ran: boolean;
  onBack: () => void;
  onForward: () => void;
}) {
  const atResult = step === "result";
  return (
    <div className="mt-3 flex items-center gap-2" role="group" aria-label="回测步骤">
      <StepPill
        index={1}
        label="配置"
        state={atResult ? "done" : "current"}
        onSelect={atResult ? onBack : undefined}
      />
      <div className="h-px w-6 shrink-0 bg-[var(--border)]" aria-hidden />
      <StepPill
        index={2}
        label="结果"
        state={atResult ? "current" : ran ? "ready" : "locked"}
        onSelect={!atResult && ran ? onForward : undefined}
      />
    </div>
  );
}

// 单个步骤段：当前=高亮 + aria-current，已完成=✓ 可回退，可前进=accent 可点，未达=灰 disabled。
function StepPill({
  index,
  label,
  state,
  onSelect,
}: {
  index: number;
  label: string;
  state: "current" | "done" | "ready" | "locked";
  onSelect?: () => void;
}) {
  const isCurrent = state === "current";
  const isLocked = state === "locked";
  return (
    <button
      type="button"
      disabled={isLocked}
      aria-current={isCurrent ? "step" : undefined}
      onClick={onSelect}
      className={clsx(
        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-medium transition-colors duration-150",
        "outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]",
        isCurrent && "bg-[var(--accent-soft)] text-[var(--accent)]",
        state === "done" && "cursor-pointer text-[var(--text)] hover:bg-[var(--surface-2)]",
        state === "ready" && "cursor-pointer text-[var(--accent)] hover:bg-[var(--accent-soft)]",
        isLocked && "cursor-not-allowed text-[var(--muted)] opacity-60",
      )}
    >
      <span
        className={clsx(
          "flex h-4 w-4 items-center justify-center rounded-full text-[10px] leading-none",
          isCurrent
            ? "bg-[var(--accent)] text-white"
            : state === "done"
              ? "bg-[var(--long)] text-white"
              : "bg-[var(--surface-2)] text-[var(--muted)]",
        )}
      >
        {state === "done" ? "✓" : index}
      </span>
      {label}
    </button>
  );
}

// 区间 / 周期选择 chip（复用 accent-soft 高亮，键盘可达）。
function Chip({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={
        "rounded-[8px] px-2.5 py-1 text-[11px] font-medium transition-all duration-150 " +
        "outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] " +
        (active
          ? "bg-[var(--accent-soft)] text-[var(--accent)]"
          : "bg-[var(--surface)] text-[var(--muted)] hover:text-[var(--text)]")
      }
    >
      {children}
    </button>
  );
}

// 第一步「配置」：回测区间 + K 线周期 + 成本与杠杆 + 即时反馈（校验错误 / 暖机告警 / 样本内外提示）。
// 表单 JSX 与派生逻辑逐字沿用上轮，仅整体换到独立步骤外壳；根容器 tabIndex=-1 作返回焦点兜底（R-2）。
function ConfigStep({
  bodyRef,
  cfg,
  patch,
  pickPreset,
  setBound,
  isCustom,
  bars,
  errors,
  warnings,
  provenanceHint,
  inSample,
  instId,
  riskOverrideEnabled,
}: {
  bodyRef: React.RefObject<HTMLDivElement>;
  cfg: BacktestConfig;
  patch: (part: Partial<BacktestConfig>) => void;
  pickPreset: (preset: RangePreset) => void;
  setBound: (key: "startTs" | "endTs", raw: string) => void;
  isCustom: boolean;
  bars: number;
  errors: string[];
  warnings: string[];
  provenanceHint?: ProvenanceHint | null;
  inSample: boolean;
  instId: string;
  riskOverrideEnabled: boolean;
}) {
  return (
    <div ref={bodyRef} tabIndex={-1} className="flex flex-col gap-4 outline-none">
      {/* 回测区间 */}
      <section className="flex flex-col gap-2 rounded-[10px] border border-[var(--border)] bg-[var(--surface-2)] p-3">
        <div className="text-[11px] font-semibold text-[var(--muted)]">回测区间</div>
        <div className="flex flex-wrap gap-1.5">
          {RANGE_PRESETS.map((preset) => (
            <Chip
              key={preset}
              active={cfg.rangePreset === preset}
              onClick={() => pickPreset(preset)}
            >
              {PRESET_LABEL[preset]}
            </Chip>
          ))}
        </div>
        <div className="grid grid-cols-2 gap-2">
          <label className="flex flex-col gap-1">
            <span className="text-[11px] text-[var(--muted)]">起始</span>
            <input
              type="datetime-local"
              value={tsToLocalInput(cfg.startTs)}
              disabled={!isCustom}
              onChange={(e) => setBound("startTs", e.target.value)}
              className="tabular w-full rounded-[8px] border border-[var(--border)] bg-[var(--surface)] px-2 py-1 text-xs outline-none focus:border-[var(--accent)] disabled:opacity-60"
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[11px] text-[var(--muted)]">结束</span>
            <input
              type="datetime-local"
              value={tsToLocalInput(cfg.endTs)}
              disabled={!isCustom}
              onChange={(e) => setBound("endTs", e.target.value)}
              className="tabular w-full rounded-[8px] border border-[var(--border)] bg-[var(--surface)] px-2 py-1 text-xs outline-none focus:border-[var(--accent)] disabled:opacity-60"
            />
          </label>
        </div>
        <p className="text-[11px] text-[var(--muted)]">
          预计 ≈ <span className="tabular font-medium text-[var(--text)]">{bars}</span> 根 K 线 ·{" "}
          {fmtTime(cfg.startTs)} → {fmtTime(cfg.endTs)}（UTC 本地时间）
        </p>
      </section>

      {/* K 线周期 */}
      <section className="flex flex-col gap-2 rounded-[10px] border border-[var(--border)] bg-[var(--surface-2)] p-3">
        <div className="text-[11px] font-semibold text-[var(--muted)]">K 线周期</div>
        <div className="flex flex-wrap gap-1.5">
          {BARS.map((b: Bar) => (
            <Chip key={b} active={cfg.bar === b} onClick={() => patch({ bar: b })}>
              {BAR_LABEL[b]}
            </Chip>
          ))}
        </div>
      </section>

      {/* 成本与杠杆 */}
      <section className="flex flex-col gap-2 rounded-[10px] border border-[var(--border)] bg-[var(--surface-2)] p-3">
        <div className="text-[11px] font-semibold text-[var(--muted)]">成本与杠杆</div>
        <div className="grid grid-cols-2 gap-2.5">
          <CostField
            label="初始资金"
            value={cfg.initialCapital}
            unit="USDT"
            min={0}
            step={100}
            onChange={(n) => patch({ initialCapital: n })}
          />
          <CostField
            label="杠杆"
            value={cfg.leverage}
            unit="x"
            min={1}
            step={1}
            onChange={(n) => patch({ leverage: n })}
          />
          <CostField
            label="手续费（taker）"
            value={cfg.feesBps}
            unit="bps"
            min={0}
            step={0.5}
            onChange={(n) => patch({ feesBps: n })}
          />
          <CostField
            label="滑点"
            value={cfg.slippageBps}
            unit="bps"
            min={0}
            step={0.5}
            onChange={(n) => patch({ slippageBps: n })}
          />
        </div>
      </section>

      {/* 止盈止损（可选，backtest-stop-loss-take-profit §2.4）：仅原子/因子渲染；两输入默认留空＝沿用策略自带风控。
          组合（riskOverrideEnabled=false）不渲染——语义不清且 wire 已隔离，避免「显示却不生效」的 UX 陷阱。 */}
      {riskOverrideEnabled && (
        <section className="flex flex-col gap-2 rounded-[10px] border border-[var(--border)] bg-[var(--surface-2)] p-3">
          <div className="text-[11px] font-semibold text-[var(--muted)]">止盈止损（可选）</div>
          <div className="grid grid-cols-2 gap-2.5">
            <OptionalPctField
              label="止盈%"
              value={cfg.takeProfitPct}
              placeholder="留空＝用策略自带"
              onChange={(n) => patch({ takeProfitPct: n })}
            />
            <OptionalPctField
              label="止损%"
              value={cfg.stopLossPct}
              placeholder="留空＝用策略自带"
              onChange={(n) => patch({ stopLossPct: n })}
            />
          </div>
          <p className="text-[11px] text-[var(--muted)]">
            仅本次回测临时生效，不写回策略参数；留空则完全沿用策略自带止盈止损。
          </p>
        </section>
      )}

      {/* 即时反馈：校验错误 / 暖机告警 / 样本内外提示 */}
      {errors.length > 0 && (
        <div className="rounded-[8px] border border-[var(--short)] bg-[var(--surface)] px-3 py-2 text-[11px] text-[var(--short)]">
          {errors.join("；")}
        </div>
      )}
      {warnings.map((w) => (
        <div
          key={w}
          className="rounded-[8px] border border-[color-mix(in_srgb,var(--warn)_40%,transparent)] bg-[var(--surface)] px-3 py-2 text-[11px] text-[var(--warn)]"
        >
          ⚠ {w}
        </div>
      ))}
      {provenanceHint != null && (
        <div className="rounded-[8px] border border-[var(--border)] bg-[var(--surface)] px-3 py-2 text-[11px] text-[var(--muted)]">
          权重训练区间：{provenanceHint.instId ?? instId} · {provenanceHint.bar ?? "—"} ·{" "}
          {fmtTime(provenanceHint.startTs)} → {fmtTime(provenanceHint.endTs)}
          <span className={inSample ? "text-[var(--warn)]" : "text-[var(--long)]"}>
            {inSample
              ? " · 当前区间与训练区间重叠（样本内），结果可能偏乐观"
              : " · 当前区间为样本外"}
          </span>
        </div>
      )}
    </div>
  );
}

// 第二步「结果」：三态 running → error → result（分支序为实现级不变量，防旧指标闪回，R-3）。
// 空态已删除（进入本屏必已运行）；操作条上移到页脚。小标题 tabIndex=-1 作焦点落点、容器 aria-live 播报完成。
function ResultStep({
  headingRef,
  running,
  result,
  errorMessage,
}: {
  headingRef: React.RefObject<HTMLHeadingElement>;
  running: boolean;
  result?: BacktestResult;
  errorMessage?: string | null;
}) {
  return (
    <div className="flex flex-col gap-3">
      <h3
        ref={headingRef}
        tabIndex={-1}
        className="text-[11px] font-semibold text-[var(--muted)] outline-none"
      >
        回测结果
      </h3>
      <div aria-live="polite" className="flex flex-col gap-3">
        {running ? (
          <div className="grid grid-cols-3 gap-2">
            {Array.from({ length: 9 }).map((_, i) => (
              <Skeleton key={i} className="h-[52px]" />
            ))}
          </div>
        ) : errorMessage ? (
          <div className="rounded-[10px] border border-[var(--short)] bg-[var(--surface)] px-3 py-3 text-xs text-[var(--short)]">
            回测失败：{errorMessage}
          </div>
        ) : result ? (
          <>
            {result.truncated && (
              <div className="rounded-[8px] border border-[color-mix(in_srgb,var(--warn)_40%,transparent)] bg-[var(--surface)] px-3 py-2 text-[11px] text-[var(--warn)]">
                ⚠ 区间过长 / 历史不足，仅覆盖最近 {result.effectiveCandleCount} / {result.requestedBars} 根，收益率为部分区间结果。
              </div>
            )}
            <BacktestMetricsGrid metrics={result.metrics} extended={result.extendedMetrics} />
            <div className="flex flex-col gap-1.5">
              <span className="text-[11px] font-semibold text-[var(--muted)]">净值曲线</span>
              <EquitySparkline runId={result.runId} />
            </div>
          </>
        ) : null}
      </div>
    </div>
  );
}
