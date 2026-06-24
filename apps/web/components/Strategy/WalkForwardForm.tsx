"use client";

import { Button } from "@/components/ui";
import { useAppStore } from "@/lib/store";

// 滑动窗口三参数表单：windowBars / stepBars / resetCapital + 「运行高级回测」按钮（显式触发，
// 区别于普通回测的随参自动跑——滑动窗口计算较重，不自动触发，spec §7.2）。
const WINDOW_PRESETS = [500, 1000, 2000];
const STEP_PRESETS = [100, 250, 500];

export default function WalkForwardForm({
  busy,
  onRun,
}: {
  busy: boolean;
  onRun: () => void;
}) {
  const { wfParams, setWfParams } = useAppStore();

  return (
    <div className="flex flex-col gap-3 rounded-[10px] border border-[var(--border)] bg-[var(--surface-2)] px-3 py-3">
      <PresetRow
        label="窗口长度"
        hint="每个窗口的 K 线根数（须显著大于策略暖机根数）"
        presets={WINDOW_PRESETS}
        value={wfParams.windowBars}
        min={100}
        max={5000}
        onChange={(windowBars) => setWfParams({ windowBars })}
      />
      <PresetRow
        label="滑动步长"
        hint="相邻窗口起点的间隔根数（须 ≤ 窗口长度）"
        presets={STEP_PRESETS}
        value={wfParams.stepBars}
        min={10}
        max={5000}
        onChange={(stepBars) => setWfParams({ stepBars })}
      />
      <label className="flex items-center justify-between gap-2">
        <span className="flex flex-col">
          <span className="text-[11px] text-[var(--text)]">每个窗口重置本金</span>
          <span className="text-[10px] text-[var(--muted)]">
            默认开：各窗口独立重置本金与仓位（鲁棒性测试，非连续交易）
          </span>
        </span>
        <input
          type="checkbox"
          checked={wfParams.resetCapital}
          onChange={(e) => setWfParams({ resetCapital: e.target.checked })}
          className="h-4 w-4 accent-[var(--accent)]"
        />
      </label>

      <Button variant="primary" disabled={busy} onClick={onRun}>
        {busy ? "运行中..." : "运行高级回测"}
      </Button>
    </div>
  );
}

function PresetRow({
  label,
  hint,
  presets,
  value,
  min,
  max,
  onChange,
}: {
  label: string;
  hint: string;
  presets: number[];
  value: number;
  min: number;
  max: number;
  onChange: (value: number) => void;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center justify-between gap-2">
        <span className="text-[11px] text-[var(--text)]">{label}</span>
        <input
          type="number"
          value={value}
          min={min}
          max={max}
          step={1}
          onChange={(e) => onChange(parseInt(e.target.value || "0", 10))}
          className="w-24 rounded-[8px] border border-[var(--border)] bg-[var(--surface)] px-2 py-1 text-xs tabular"
        />
      </div>
      <div className="flex gap-0.5 rounded-[8px] bg-[var(--surface)] p-0.5">
        {presets.map((preset) => (
          <button
            key={preset}
            type="button"
            onClick={() => onChange(preset)}
            className={
              "flex-1 rounded-[6px] px-2 py-0.5 text-[11px] font-medium transition-all " +
              (value === preset
                ? "bg-[var(--accent-soft)] text-[var(--accent)]"
                : "text-[var(--muted)] hover:text-[var(--text)]")
            }
          >
            {preset}
          </button>
        ))}
      </div>
      <span className="text-[10px] text-[var(--muted)]">{hint}</span>
    </div>
  );
}
