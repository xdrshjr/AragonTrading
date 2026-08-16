"use client";

import { useEffect, useState } from "react";
import { Badge, Button } from "@/components/ui";
import { buildEditTargetFromStrategy, directionLabel } from "@/lib/factor";
import type { FactorScoreSnapshotParams } from "@/lib/factorTypes";
import { fmtMoney, fmtTime } from "@/lib/format";
import { useAppStore } from "@/lib/store";
import type { BacktestConfig, BacktestKind, BacktestResult, Strategy } from "@/lib/types";
import BacktestConfigModal from "./BacktestConfigModal";
import MultiSampleBacktestModal from "./MultiSampleBacktestModal";
import WalkForwardForm from "./WalkForwardForm";

interface FactorMemberSnapshot {
  factorKey: string;
  direction?: string;
}

// 因子组合得分策略（kind==="factor"）的内联快照摘要 + 编辑入口（评审 C-P1-5）。
function FactorStrategyBanner({ strategy }: { strategy: Strategy }) {
  const setActiveView = useAppStore((s) => s.setActiveView);
  const setFactorLabSection = useAppStore((s) => s.setFactorLabSection);
  const setFactorEditTarget = useAppStore((s) => s.setFactorEditTarget);
  const members = (strategy.params?.members as FactorMemberSnapshot[] | undefined) ?? [];
  // 短窗护栏（H6）：策略暖机=normWindow+60；区间过短将安静空仓，此处如实标注最小所需根数。
  const thresholds = (strategy.params?.thresholds as { normWindow?: number } | undefined) ?? {};
  const minBars = Number(thresholds.normWindow ?? 200) + 60;
  // 训练区间诚实提示（L4）：如实展示权重训练所用区间 + 样本内/外自查提示；旧快照无 provenance 时安全不展示。
  const provenance = (strategy.params as Partial<FactorScoreSnapshotParams>)?.provenance;
  const hasRange = provenance?.startTs != null && provenance?.endTs != null;
  return (
    <div className="flex flex-col gap-2 rounded-[10px] border border-[var(--accent-soft)] bg-[var(--accent-soft)]/40 px-3 py-2.5">
      <div className="flex items-center justify-between gap-2">
        <span className="text-[11px] font-semibold text-[var(--accent-ink)]">因子组合得分策略</span>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => {
            // 把当前策略映射为编辑交接目标（含 factorSetId → set 分支 + snapshot 兜底；旧策略 → snapshot
            // 分支），由 FactorSetBuilder 挂载时消费水合（P1-1）。
            setFactorEditTarget(buildEditTargetFromStrategy(strategy));
            setFactorLabSection("builder");
            setActiveView("factor");
          }}
        >
          在因子实验室编辑
        </Button>
      </div>
      <p className="text-[11px] text-[var(--muted)]">
        消费独立因子层的复合得分做决策（内联快照 {members.length} 个因子成员，回测路径零改动）。
      </p>
      <p className="text-[11px] text-[var(--muted)]">
        参数由因子实验室的因子组合管理，请在因子实验室编辑；此处临时改参仅用于即时回测，下次组合更新会以快照覆盖。
      </p>
      <p className="text-[11px] text-[var(--accent-ink)]">
        至少需约 {minBars} 根数据才会产出信号（区间过短将安静空仓，请确保回测/对比区间足够长）。
      </p>
      {hasRange && provenance && (
        <div className="flex flex-col gap-0.5">
          <p className="text-[11px] text-[var(--muted)]">
            训练于 {provenance.instId ?? "—"} · {provenance.bar ?? "—"} · [{fmtTime(provenance.startTs)},{" "}
            {fmtTime(provenance.endTs)}]{provenance.horizon != null ? ` · 前瞻 ${provenance.horizon} 根` : ""}
          </p>
          <p className="text-[11px] text-[var(--muted)]">
            若回测 / 对比区间落在上述训练区间内即为样本内，结果可能偏乐观，建议用训练区间之后的样本外区间验证。
          </p>
        </div>
      )}
      {members.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {members.map((m, i) => (
            <Badge key={`${m.factorKey}-${i}`} tone={m.direction === "short" ? "short" : "long"}>
              {m.factorKey}·{directionLabel(m.direction ?? "long")}
            </Badge>
          ))}
        </div>
      )}
    </div>
  );
}

type Params = Record<string, unknown>;
type PaperOpts = { source?: "live" | "replay"; speed?: number; replayBars?: number };

const SPEEDS = [1, 4, 16];

export default function StrategyDetail({
  strategy,
  mode,
  backtestKind = "simple",
  busy,
  paperActive,
  simBalance,
  watchlist = [],
  instId = "",
  bar = "1H",
  backtestStartTs = 0,
  backtestEndTs = 0,
  backtestConfig,
  onRunBacktest,
  backtestRunning = false,
  backtestResult,
  backtestError,
  onRunWalkForward,
  onStartPaper,
  onStopPaper,
  onSaveAs,
  onMultiBacktest,
}: {
  strategy: Strategy;
  mode: "backtest" | "paper";
  backtestKind?: BacktestKind;
  busy: boolean;
  paperActive: boolean;
  simBalance?: number;
  // 多样本回测上下文（评审 R-P1-4：由 StrategyPanel 注入，StrategyDetail 不直接读 store）。
  watchlist?: string[];
  instId?: string;
  bar?: string;
  backtestStartTs?: number;
  backtestEndTs?: number;
  // 运行回测配置弹窗（strategy-backtest-config-modal §4.4）：种子配置 + 运行回调 + 运行态/结果/错误观测。
  backtestConfig: BacktestConfig;
  onRunBacktest: (params: Params, cfg: BacktestConfig) => void;
  backtestRunning?: boolean;
  backtestResult?: BacktestResult;
  backtestError?: string | null;
  onRunWalkForward?: (params: Params) => void;
  onStartPaper: (params: Params, opts?: PaperOpts) => void;
  onStopPaper: () => void;
  onSaveAs: (name: string, params: Params) => void;
  onMultiBacktest?: (instIds: string[], params: Params) => void;
}) {
  const [params, setParams] = useState<Params>(strategy.params);
  const [source, setSource] = useState<"live" | "replay">("live");
  const [speed, setSpeed] = useState(16);
  const [multiOpen, setMultiOpen] = useState(false);
  const [configOpen, setConfigOpen] = useState(false);

  useEffect(() => {
    setParams(strategy.params);
  }, [strategy.id, strategy.params]);

  const schema = strategy.paramsSchema || {};
  const setValue = (key: string, value: unknown) =>
    setParams((prev) => ({ ...prev, [key]: value }));

  // 多周期：策略声明了高周期参考时展示信息性徽标（不改任何请求路径，仅帮助理解综合多周期）。
  const htfFilterOn = params.htf_trend_filter !== false;
  const htfBar = typeof params.htf_bar === "string" && htfFilterOn ? params.htf_bar : null;

  // 运行回测弹窗上下文：factor_score 暖机最小根数（normWindow+60，沿用横幅口径）+ 权重训练区间
  //（样本内/外自查），非因子策略省略。
  const isFactor = strategy.kind === "factor";
  const factorThresholds = (strategy.params?.thresholds as { normWindow?: number } | undefined) ?? {};
  const warmupBars = isFactor ? Number(factorThresholds.normWindow ?? 200) + 60 : undefined;
  const provenance = isFactor
    ? (strategy.params as Partial<FactorScoreSnapshotParams>)?.provenance
    : undefined;
  const provenanceHint =
    provenance?.startTs != null && provenance?.endTs != null
      ? {
          startTs: provenance.startTs,
          endTs: provenance.endTs,
          instId: provenance.instId,
          bar: provenance.bar,
        }
      : null;

  return (
    <div className="flex flex-col gap-3">
      {strategy.kind === "factor" && <FactorStrategyBanner strategy={strategy} />}
      <p className="text-xs leading-relaxed text-[var(--muted)]">{strategy.description}</p>

      {htfBar && (
        <span className="inline-flex w-fit items-center gap-1 rounded-[6px] bg-[var(--accent-soft)] px-2 py-0.5 text-[11px] font-medium text-[var(--accent-ink)]">
          参考周期：{htfBar}
        </span>
      )}

      <div className="grid grid-cols-2 gap-2">
        {Object.entries(schema).map(([key, def]) => {
          const value = params[key] ?? def.default;
          return (
            <label key={key} className="flex flex-col gap-1">
              <span className="text-[11px] text-[var(--muted)]">{def.label}</span>
              {def.type === "bool" ? (
                <input
                  type="checkbox"
                  checked={Boolean(value)}
                  onChange={(event) => setValue(key, event.target.checked)}
                  className="h-4 w-4 accent-[var(--accent)]"
                />
              ) : def.type === "enum" ? (
                <select
                  value={String(value)}
                  onChange={(event) => setValue(key, event.target.value)}
                  className="rounded-[8px] border border-[var(--border)] bg-[var(--surface)] px-2 py-1 text-xs"
                >
                  {(def.options || []).map((option) => (
                    <option key={option} value={option}>
                      {option}
                    </option>
                  ))}
                </select>
              ) : (
                <input
                  type="number"
                  value={Number(value)}
                  min={def.min}
                  max={def.max}
                  step={def.step ?? (def.type === "int" ? 1 : "any")}
                  onChange={(event) =>
                    setValue(
                      key,
                      def.type === "int"
                        ? parseInt(event.target.value || "0", 10)
                        : parseFloat(event.target.value),
                    )
                  }
                  className="rounded-[8px] border border-[var(--border)] bg-[var(--surface)] px-2 py-1 text-xs tabular"
                />
              )}
            </label>
          );
        })}
      </div>

      {mode === "paper" && !paperActive && (
        <div className="flex flex-col gap-2 rounded-[10px] border border-[var(--border)] bg-[var(--surface-2)] px-3 py-2">
          {simBalance != null && (
            <div className="flex items-center justify-between gap-2">
              <span className="text-[11px] text-[var(--muted)]">本金</span>
              <span className="text-[11px] text-[var(--muted)]">
                <span className="tabular font-medium text-[var(--text)]">
                  {fmtMoney(simBalance)}
                </span>
              </span>
            </div>
          )}
          <div className="flex items-center justify-between gap-2">
            <span className="text-[11px] text-[var(--muted)]">数据源</span>
            <div className="flex gap-0.5 rounded-[8px] bg-[var(--surface)] p-0.5">
              {(["live", "replay"] as const).map((item) => (
                <button
                  key={item}
                  type="button"
                  onClick={() => setSource(item)}
                  className={
                    "rounded-[6px] px-2 py-0.5 text-[11px] font-medium transition-all " +
                    (source === item
                      ? "bg-[var(--accent-soft)] text-[var(--accent-ink)]"
                      : "text-[var(--muted)] hover:text-[var(--text)]")
                  }
                >
                  {item === "live" ? "实时" : "历史回放"}
                </button>
              ))}
            </div>
          </div>
          {source === "replay" && (
            <div className="flex items-center justify-between gap-2">
              <span className="text-[11px] text-[var(--muted)]">倍速</span>
              <div className="flex gap-0.5 rounded-[8px] bg-[var(--surface)] p-0.5">
                {SPEEDS.map((item) => (
                  <button
                    key={item}
                    type="button"
                    onClick={() => setSpeed(item)}
                    className={
                      "rounded-[6px] px-2 py-0.5 text-[11px] font-medium transition-all " +
                      (speed === item
                        ? "bg-[var(--accent-soft)] text-[var(--accent-ink)]"
                        : "text-[var(--muted)] hover:text-[var(--text)]")
                    }
                  >
                    {item}x
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {mode === "backtest" && backtestKind === "walkforward" && onRunWalkForward && (
        <WalkForwardForm busy={busy} onRun={() => onRunWalkForward(params)} />
      )}

      <div className="flex flex-wrap gap-2">
        {mode === "backtest" ? (
          backtestKind === "walkforward" ? null : (
            <Button variant="primary" disabled={busy} onClick={() => setConfigOpen(true)}>
              {busy ? "运行中..." : "运行回测"}
            </Button>
          )
        ) : paperActive ? (
          <Button variant="danger" disabled={busy} onClick={onStopPaper}>
            停止模拟
          </Button>
        ) : (
          <Button
            variant="primary"
            disabled={busy}
            onClick={() => onStartPaper(params, { source, speed, replayBars: 300 })}
          >
            {busy ? "启动中..." : source === "replay" ? "启动历史回放" : "启动实时模拟"}
          </Button>
        )}

        {mode === "backtest" && backtestKind !== "walkforward" && onMultiBacktest && (
          <Button variant="default" disabled={busy} onClick={() => setMultiOpen(true)}>
            多样本回测
          </Button>
        )}

        <Button
          variant="default"
          disabled={busy}
          onClick={() => {
            const name = window.prompt("另存为新策略名称", `${strategy.name} 副本`);
            if (name) onSaveAs(name, params);
          }}
        >
          另存为
        </Button>
      </div>

      {onMultiBacktest && (
        <MultiSampleBacktestModal
          open={multiOpen}
          onClose={() => setMultiOpen(false)}
          watchlist={watchlist}
          currentInstId={instId}
          bar={bar}
          startTs={backtestStartTs}
          endTs={backtestEndTs}
          strategyName={strategy.name}
          busy={busy}
          onConfirm={(instIds) => {
            onMultiBacktest(instIds, params);
            setMultiOpen(false);
          }}
        />
      )}

      <BacktestConfigModal
        open={configOpen}
        onClose={() => setConfigOpen(false)}
        strategyName={strategy.name}
        instId={instId}
        warmupBars={warmupBars}
        provenanceHint={provenanceHint}
        initialConfig={backtestConfig}
        running={backtestRunning}
        result={backtestResult}
        errorMessage={backtestError}
        onRun={(cfg) => onRunBacktest(params, cfg)}
      />
    </div>
  );
}
