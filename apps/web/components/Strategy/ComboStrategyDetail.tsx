"use client";

import { useState } from "react";
import { summarizeComboMembers } from "@/lib/combo";
import type { BacktestConfig, BacktestKind, BacktestResult, Strategy } from "@/lib/types";
import { Badge, Button } from "@/components/ui";
import BacktestConfigModal from "./BacktestConfigModal";
import WalkForwardForm from "./WalkForwardForm";

type PaperOpts = { source?: "live" | "replay"; speed?: number; replayBars?: number };

const SPEEDS = [1, 4, 16];

/**
 * 列表选中「组合」行时的右栏紧凑详情：组合描述 + 成员/轮动摘要 + 运行回测 / 查看 / 编辑 / 删除。
 * 不含原子参数网格、不含「另存为」、不含 paper 启停（组合只走回测 / 对比，不接 paper）。
 */
export default function ComboStrategyDetail({
  combo,
  mode = "backtest",
  backtestKind = "simple",
  busy,
  paperActive = false,
  instId = "",
  backtestConfig,
  backtestRunning = false,
  backtestResult,
  backtestError,
  onRunBacktest,
  onRunWalkForward,
  onStartPaper,
  onStopPaper,
  onView,
  onEdit,
  onDelete,
}: {
  combo: Strategy;
  mode?: "backtest" | "paper";
  backtestKind?: BacktestKind;
  busy: boolean;
  paperActive?: boolean;
  instId?: string;
  // 运行回测配置弹窗（strategy-backtest-config-modal §4.4）：组合无策略参数表单，仅区间/周期/成本。
  backtestConfig: BacktestConfig;
  backtestRunning?: boolean;
  backtestResult?: BacktestResult;
  backtestError?: string | null;
  onRunBacktest: (cfg: BacktestConfig) => void;
  onRunWalkForward?: (combo: Strategy) => void;
  onStartPaper?: (combo: Strategy, opts?: PaperOpts) => void;
  onStopPaper?: () => void;
  onView: (combo: Strategy) => void;
  onEdit: (combo: Strategy) => void;
  onDelete: (combo: Strategy) => void;
}) {
  const isWalkForward = backtestKind === "walkforward";
  const [source, setSource] = useState<"live" | "replay">("live");
  const [speed, setSpeed] = useState(16);
  const [configOpen, setConfigOpen] = useState(false);
  const isPaper = mode === "paper";

  return (
    <div className="flex flex-col gap-3">
      <div className="flex min-w-0 items-center gap-2">
        <span className="truncate text-sm font-semibold text-[var(--text)]">{combo.name}</span>
        <Badge tone="accent">组合</Badge>
      </div>

      {combo.description && (
        <p className="text-xs leading-relaxed text-[var(--muted)]">{combo.description}</p>
      )}

      <p className="text-[11px] text-[var(--muted)]">{summarizeComboMembers(combo)}</p>

      {!isPaper && isWalkForward && (
        <WalkForwardForm busy={busy} onRun={() => onRunWalkForward?.(combo)} />
      )}

      {isPaper && !paperActive && (
        <div className="flex flex-col gap-2 rounded-[10px] border border-[var(--border)] bg-[var(--surface-2)] px-3 py-2">
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

      <div className="flex flex-wrap gap-2">
        {isPaper ? (
          paperActive ? (
            <Button variant="danger" disabled={busy} onClick={onStopPaper}>
              停止模拟
            </Button>
          ) : (
            <Button
              variant="primary"
              disabled={busy}
              onClick={() => onStartPaper?.(combo, { source, speed, replayBars: 300 })}
            >
              {busy ? "启动中..." : source === "replay" ? "启动历史回放" : "启动实时模拟"}
            </Button>
          )
        ) : !isWalkForward ? (
          <Button variant="primary" disabled={busy} onClick={() => setConfigOpen(true)}>
            {busy ? "运行中..." : "运行回测"}
          </Button>
        ) : null}
        <Button variant="default" disabled={busy} onClick={() => onView(combo)}>
          查看详情
        </Button>
        <Button variant="default" disabled={busy} onClick={() => onEdit(combo)}>
          编辑
        </Button>
        <Button variant="danger" disabled={busy} onClick={() => onDelete(combo)}>
          删除
        </Button>
      </div>

      <BacktestConfigModal
        open={configOpen}
        onClose={() => setConfigOpen(false)}
        strategyName={combo.name}
        instId={instId}
        initialConfig={backtestConfig}
        running={backtestRunning}
        result={backtestResult}
        errorMessage={backtestError}
        onRun={(cfg) => onRunBacktest(cfg)}
        // 组合不支持止盈止损覆盖（backtest-stop-loss-take-profit §2.4）：不渲染分区 + 跳过 SL/TP 校验；
        // wire 层隔离（请求体不含 SL/TP）在 StrategyPanel 的组合 mutate 调用点（riskOverrideEnabled=false）。
        riskOverrideEnabled={false}
      />
    </div>
  );
}
