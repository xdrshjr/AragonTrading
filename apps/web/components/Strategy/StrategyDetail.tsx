"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui";
import { fmtMoney } from "@/lib/format";
import type { Strategy } from "@/lib/types";

type Params = Record<string, unknown>;
type PaperOpts = { source?: "live" | "replay"; speed?: number; replayBars?: number };

const SPEEDS = [1, 4, 16];

export default function StrategyDetail({
  strategy,
  mode,
  busy,
  paperActive,
  simBalance,
  onApplyAndBacktest,
  onStartPaper,
  onStopPaper,
  onSaveAs,
}: {
  strategy: Strategy;
  mode: "backtest" | "paper";
  busy: boolean;
  paperActive: boolean;
  simBalance?: number;
  onApplyAndBacktest: (params: Params) => void;
  onStartPaper: (params: Params, opts?: PaperOpts) => void;
  onStopPaper: () => void;
  onSaveAs: (name: string, params: Params) => void;
}) {
  const [params, setParams] = useState<Params>(strategy.params);
  const [source, setSource] = useState<"live" | "replay">("live");
  const [speed, setSpeed] = useState(16);

  useEffect(() => {
    setParams(strategy.params);
  }, [strategy.id, strategy.params]);

  const schema = strategy.paramsSchema || {};
  const setValue = (key: string, value: unknown) =>
    setParams((prev) => ({ ...prev, [key]: value }));

  return (
    <div className="flex flex-col gap-3">
      <p className="text-xs leading-relaxed text-[var(--muted)]">{strategy.description}</p>

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
                      ? "bg-[var(--accent-soft)] text-[var(--accent)]"
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
                        ? "bg-[var(--accent-soft)] text-[var(--accent)]"
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
        {mode === "backtest" ? (
          <Button variant="primary" disabled={busy} onClick={() => onApplyAndBacktest(params)}>
            {busy ? "运行中..." : "运行回测"}
          </Button>
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
    </div>
  );
}
