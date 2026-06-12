"use client";

import { useEffect, useState } from "react";
import type { Strategy } from "@/lib/types";
import { Button } from "@/components/ui";

type Params = Record<string, unknown>;

export default function StrategyDetail({
  strategy,
  mode,
  busy,
  paperActive,
  onApplyAndBacktest,
  onStartPaper,
  onStopPaper,
  onSaveAs,
}: {
  strategy: Strategy;
  mode: "backtest" | "paper";
  busy: boolean;
  paperActive: boolean;
  onApplyAndBacktest: (params: Params) => void;
  onStartPaper: (params: Params) => void;
  onStopPaper: () => void;
  onSaveAs: (name: string, params: Params) => void;
}) {
  const [params, setParams] = useState<Params>(strategy.params);

  useEffect(() => {
    setParams(strategy.params);
  }, [strategy.id, strategy.params]);

  const schema = strategy.paramsSchema || {};
  const setVal = (k: string, v: unknown) => setParams((p) => ({ ...p, [k]: v }));

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
                  onChange={(e) => setVal(key, e.target.checked)}
                  className="h-4 w-4 accent-[var(--accent)]"
                />
              ) : def.type === "enum" ? (
                <select
                  value={String(value)}
                  onChange={(e) => setVal(key, e.target.value)}
                  className="rounded-[8px] border border-[var(--border)] bg-[var(--surface)] px-2 py-1 text-xs"
                >
                  {(def.options || []).map((o) => (
                    <option key={o} value={o}>
                      {o}
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
                  onChange={(e) =>
                    setVal(key, def.type === "int" ? parseInt(e.target.value || "0", 10) : parseFloat(e.target.value))
                  }
                  className="rounded-[8px] border border-[var(--border)] bg-[var(--surface)] px-2 py-1 text-xs tabular"
                />
              )}
            </label>
          );
        })}
      </div>

      <div className="flex flex-wrap gap-2">
        {mode === "backtest" ? (
          <Button variant="primary" disabled={busy} onClick={() => onApplyAndBacktest(params)}>
            {busy ? "运行中…" : "运行回测"}
          </Button>
        ) : paperActive ? (
          <Button variant="danger" disabled={busy} onClick={onStopPaper}>
            停止模拟
          </Button>
        ) : (
          <Button variant="primary" disabled={busy} onClick={() => onStartPaper(params)}>
            {busy ? "启动中…" : "启动实时模拟"}
          </Button>
        )}

        <Button
          variant="default"
          disabled={busy}
          onClick={() => {
            const name = window.prompt("另存为新策略名称：", `${strategy.name} 副本`);
            if (name) onSaveAs(name, params);
          }}
        >
          另存为
        </Button>
      </div>
    </div>
  );
}
