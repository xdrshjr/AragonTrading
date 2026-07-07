"use client";

import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
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

/**
 * 运行回测配置 + 运行 + 结果弹窗（strategy-backtest-config-modal §2.4）。
 *
 * 「上配置、下结果」单面板：用户显式修改回测区间（预设 + 自定义起止）、K 线周期、初始资金、手续费、
 * 滑点、杠杆，弹窗内运行并就地呈现专业绩效指标 + 覆盖度横幅 + 净值缩略图。运行态/结果/错误经 props
 * 观察同一 mutation（单一数据源，不另起状态）。完全复用 `Modal` 的焦点陷阱 / Esc / 遮罩关闭等无障碍原语。
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
}) {
  const [cfg, setCfg] = useState<BacktestConfig>(initialConfig);
  // 只在本次「打开」内是否已从弹窗触发过运行：决定结果区展示空态还是结果（避免跨策略陈旧结果误显）。
  const [hasRun, setHasRun] = useState(false);
  const seededRef = useRef(false);

  // 打开时一次性以 initialConfig 播种，并按 now 重派生预设区间（保证「近 30 天」始终新鲜）。
  useEffect(() => {
    if (!open) {
      seededRef.current = false;
      return;
    }
    if (seededRef.current) return;
    seededRef.current = true;
    setCfg(refreshPresetRange(initialConfig, Math.floor(Date.now() / 1000)));
    setHasRun(false);
  }, [open, initialConfig]);

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
  const { ok, errors, warnings } = validateConfig(cfg, warmupBars);
  const runDisabled = !ok || running;

  // 样本内 / 外自查（因子权重训练区间重叠即偏乐观，沿用 StrategyDetail 口径）。
  const inSample =
    provenanceHint != null && cfg.startTs < provenanceHint.endTs && cfg.endTs > provenanceHint.startTs;

  const triggerRun = () => {
    if (runDisabled) return;
    setHasRun(true);
    onRun(cfg);
  };

  return (
    <Modal open={open} onClose={onClose} labelledBy={TITLE_ID}>
      <header className="flex items-start justify-between gap-3 border-b border-[var(--border)] px-5 py-4">
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
      </header>

      <div className="flex-1 overflow-y-auto px-5 py-4">
        <div className="flex flex-col gap-4">
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

          {/* 即时反馈：校验错误 / 暖机告警 / 样本内外提示 */}
          {errors.length > 0 && (
            <div className="rounded-[8px] border border-[var(--short)] bg-[var(--surface)] px-3 py-2 text-[11px] text-[var(--short)]">
              {errors.join("；")}
            </div>
          )}
          {warnings.map((w) => (
            <div
              key={w}
              className="rounded-[8px] border border-[color-mix(in_srgb,var(--warn,#d97706)_40%,transparent)] bg-[var(--surface)] px-3 py-2 text-[11px] text-[var(--warn,#d97706)]"
            >
              ⚠ {w}
            </div>
          ))}
          {provenanceHint != null && (
            <div className="rounded-[8px] border border-[var(--border)] bg-[var(--surface)] px-3 py-2 text-[11px] text-[var(--muted)]">
              权重训练区间：{provenanceHint.instId ?? instId} · {provenanceHint.bar ?? "—"} ·{" "}
              {fmtTime(provenanceHint.startTs)} → {fmtTime(provenanceHint.endTs)}
              <span className={inSample ? "text-[var(--warn,#d97706)]" : "text-[var(--long)]"}>
                {inSample
                  ? " · 当前区间与训练区间重叠（样本内），结果可能偏乐观"
                  : " · 当前区间为样本外"}
              </span>
            </div>
          )}
        </div>
      </div>

      {/* 运行按钮 */}
      <div className="flex items-center justify-between gap-3 border-t border-[var(--border)] px-5 py-3">
        <span className="text-[11px] text-[var(--muted)]">
          配置区间与参数后点运行，指标将在下方就地呈现
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

      {/* 结果区 */}
      <div className="border-t border-[var(--border)] px-5 py-4">
        <ResultRegion
          running={running}
          hasRun={hasRun}
          result={result}
          errorMessage={errorMessage}
          hasEquity={!!equityQ.data?.length}
          onExport={() => {
            if (runId && equityQ.data) {
              downloadCsv(`aragon-equity-run${runId}.csv`, equityToCsv(equityQ.data));
            }
          }}
          onClose={onClose}
        />
      </div>
    </Modal>
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

// 结果区四态：运行中骨架 / 成功（覆盖横幅 + 指标网格 + 净值缩略图 + 操作条）/ 错误 / 空态。
function ResultRegion({
  running,
  hasRun,
  result,
  errorMessage,
  hasEquity,
  onExport,
  onClose,
}: {
  running: boolean;
  hasRun: boolean;
  result?: BacktestResult;
  errorMessage?: string | null;
  hasEquity: boolean;
  onExport: () => void;
  onClose: () => void;
}) {
  if (running) {
    return (
      <div className="grid grid-cols-3 gap-2">
        {Array.from({ length: 9 }).map((_, i) => (
          <Skeleton key={i} className="h-[52px]" />
        ))}
      </div>
    );
  }
  if (hasRun && errorMessage) {
    return (
      <div className="rounded-[10px] border border-[var(--short)] bg-[var(--surface)] px-3 py-3 text-xs text-[var(--short)]">
        回测失败：{errorMessage}
      </div>
    );
  }
  if (hasRun && result) {
    const covered = result.effectiveCandleCount;
    const requested = result.requestedBars;
    return (
      <div className="flex flex-col gap-3">
        {result.truncated && (
          <div className="rounded-[8px] border border-[color-mix(in_srgb,var(--warn,#d97706)_40%,transparent)] bg-[var(--surface)] px-3 py-2 text-[11px] text-[var(--warn,#d97706)]">
            ⚠ 区间过长 / 历史不足，仅覆盖最近 {covered} / {requested} 根，收益率为部分区间结果。
          </div>
        )}
        <BacktestMetricsGrid metrics={result.metrics} extended={result.extendedMetrics} />
        <div className="flex flex-col gap-1.5">
          <span className="text-[11px] font-semibold text-[var(--muted)]">净值曲线</span>
          <EquitySparkline runId={result.runId} />
        </div>
        <div className="flex items-center justify-end gap-2">
          <Button variant="ghost" size="sm" disabled={!hasEquity} onClick={onExport}>
            导出净值 CSV
          </Button>
          <Button variant="default" size="sm" onClick={onClose}>
            在主图查看
          </Button>
        </div>
      </div>
    );
  }
  return (
    <div className="flex items-center justify-center rounded-[10px] border border-dashed border-[var(--border)] px-3 py-6 text-center">
      <span className="text-[11px] text-[var(--muted)]">配置区间与参数后点「开始回测」，指标将在此呈现</span>
    </div>
  );
}
