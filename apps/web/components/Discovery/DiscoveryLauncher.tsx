"use client";

import { useEffect, useMemo, useState } from "react";
import { Badge, Button, Field, Input, Modal, Select } from "@/components/ui";
import { windowDays, workloadOf } from "@/lib/discovery";
import { discoveryApi } from "@/lib/discoveryApi";
import type { DiscoveryAxis, DiscoveryDefaults, DiscoveryStartBody } from "@/lib/discoveryTypes";
import { useAppStore } from "@/lib/store";

const AXIS_LABEL: Record<DiscoveryAxis, string> = {
  factor: "因子表达式",
  factor_combo: "因子组合",
  strategy_param: "常规策略参数",
  strategy_combo: "策略轮动组合",
};

interface FormState {
  instId: string;
  bar: string;
  windowBars: number;
  horizon: number;
  candidatesPerIteration: number;
  iterationIntervalS: number;
  dataRefreshS: number;
  maxKept: number;
  maxIterations: number;
  autoResume: boolean;
  llmEnabled: boolean;
  llmReviewSample: number;
  llmMaxCallsPerHour: number;
  thresholds: Record<string, number>;
}

function initialForm(defaults: DiscoveryDefaults, instId: string, bar: string): FormState {
  return {
    instId,
    bar: defaults.bars.includes(bar) ? bar : defaults.bars[0],
    windowBars: defaults.windowBars.default,
    horizon: defaults.horizon.default,
    candidatesPerIteration: defaults.candidatesPerIteration.default,
    iterationIntervalS: defaults.iterationIntervalS.default,
    dataRefreshS: defaults.dataRefreshS.default,
    maxKept: defaults.maxKept.default,
    maxIterations: 0,
    autoResume: true,
    llmEnabled: defaults.llm.enabled,
    llmReviewSample: defaults.llm.reviewSample,
    llmMaxCallsPerHour: defaults.llm.maxCallsPerHour,
    thresholds: { ...defaults.thresholds },
  };
}

function clamp(value: number, min: number, max: number): number {
  if (!Number.isFinite(value)) return min;
  return Math.max(min, Math.min(max, value));
}

/**
 * 两步式启动器（strategy-factor-discovery §2.13）：第一步标的·周期·窗口·轴，第二步阈值与 LLM 用量。
 *
 * **D-6 强制推论**：轴选择器只渲染 `GET /discovery/config/defaults` 回的 `implementedAxes`，
 * 绝不渲染灰掉的「敬请期待」占位——「能选、能启动、静默不产候选」正是本仓库反复吃过亏的失败模式。
 */
export default function DiscoveryLauncher({
  open,
  defaults,
  onClose,
  onStarted,
}: {
  open: boolean;
  defaults: DiscoveryDefaults | null;
  onClose: () => void;
  onStarted: (runId: number) => void;
}) {
  const instId = useAppStore((s) => s.instId);
  const bar = useAppStore((s) => s.bar);
  const [step, setStep] = useState(1);
  const [form, setForm] = useState<FormState | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  // 依赖收敛到 [open]：仅在**打开的那一刻**重置表单。若把 form 或其派生值放进依赖，
  // 每次输入都会被重置回默认值（本仓库出现过的复发性缺陷）。
  useEffect(() => {
    if (!open || !defaults) return;
    setForm(initialForm(defaults, instId, bar));
    setStep(1);
    setError("");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const axes = defaults?.implementedAxes ?? [];
  const workload = useMemo(
    () => (form ? workloadOf(form.candidatesPerIteration, form.windowBars) : 0),
    [form],
  );
  const overLimit = Boolean(defaults && workload > defaults.workUnitLimit);

  if (!open || !defaults || !form) return null;

  const patch = (next: Partial<FormState>) => setForm((prev) => (prev ? { ...prev, ...next } : prev));

  const submit = async () => {
    setSubmitting(true);
    setError("");
    try {
      const body: DiscoveryStartBody = {
        instId: form.instId.trim(),
        bar: form.bar,
        windowBars: form.windowBars,
        axes: axes.length ? [axes[0]] : ["factor"],
        horizon: form.horizon,
        candidatesPerIteration: form.candidatesPerIteration,
        iterationIntervalS: form.iterationIntervalS,
        dataRefreshS: form.dataRefreshS,
        windowMode: "rolling",
        maxIterations: form.maxIterations,
        maxRuntimeHours: 0,
        maxKept: form.maxKept,
        autoResume: form.autoResume,
        autoPromote: false,
        llm: {
          enabled: form.llmEnabled,
          reviewSample: form.llmReviewSample,
          maxCallsPerHour: form.llmMaxCallsPerHour,
          timeoutS: defaults.llm.timeoutS,
        },
        thresholds: form.thresholds,
        evolution: defaults.evolution,
      };
      const res = await discoveryApi.start(body);
      onStarted(res.runId);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "启动挖掘任务失败");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal open={open} onClose={onClose} labelledBy="discovery-launcher-title" className="w-[560px]">
      <div className="flex flex-col gap-3 p-4">
        <div className="flex items-center gap-2">
          <h3 id="discovery-launcher-title" className="text-sm font-semibold text-[var(--text)]">
            新建挖掘任务
          </h3>
          <Badge tone="accent">第 {step} / 2 步</Badge>
        </div>

        {step === 1 ? (
          <div className="grid grid-cols-2 gap-3">
            <Field label="标的" htmlFor="disc-inst">
              <Input
                id="disc-inst"
                value={form.instId}
                onChange={(e) => patch({ instId: e.target.value })}
              />
            </Field>
            <Field label="周期" htmlFor="disc-bar">
              <Select id="disc-bar" value={form.bar} onChange={(e) => patch({ bar: e.target.value })}>
                {defaults.bars.map((item) => (
                  <option key={item} value={item}>
                    {item}
                  </option>
                ))}
              </Select>
            </Field>
            <Field
              label="数据窗口（根）"
              htmlFor="disc-window"
              hint={`约覆盖 ${windowDays(form.bar, form.windowBars).toFixed(1)} 天`}
            >
              <Input
                id="disc-window"
                type="number"
                value={form.windowBars}
                onChange={(e) =>
                  patch({
                    windowBars: clamp(
                      Number(e.target.value),
                      defaults.windowBars.min,
                      defaults.windowBars.max,
                    ),
                  })
                }
              />
            </Field>
            <Field label="前瞻根数" htmlFor="disc-horizon" hint="因子预测的未来 K 线数">
              <Input
                id="disc-horizon"
                type="number"
                value={form.horizon}
                onChange={(e) =>
                  patch({
                    horizon: clamp(Number(e.target.value), defaults.horizon.min, defaults.horizon.max),
                  })
                }
              />
            </Field>
            <Field label="搜索轴" htmlFor="disc-axis" hint="当前版本仅实现因子表达式轴">
              <Select id="disc-axis" value={axes[0] ?? "factor"} disabled={axes.length <= 1}>
                {axes.map((axis) => (
                  <option key={axis} value={axis}>
                    {AXIS_LABEL[axis] ?? axis}
                  </option>
                ))}
              </Select>
            </Field>
            <Field
              label="每轮候选数"
              htmlFor="disc-count"
              error={overLimit ? "工作量超出上限，请调小候选数或窗口" : undefined}
              hint={overLimit ? undefined : `工作量预估 ${workload.toLocaleString()} / ${defaults.workUnitLimit.toLocaleString()}`}
            >
              <Input
                id="disc-count"
                type="number"
                value={form.candidatesPerIteration}
                onChange={(e) =>
                  patch({
                    candidatesPerIteration: clamp(
                      Number(e.target.value),
                      defaults.candidatesPerIteration.min,
                      defaults.candidatesPerIteration.max,
                    ),
                  })
                }
              />
            </Field>
          </div>
        ) : (
          <div className="grid grid-cols-2 gap-3">
            <Field label="迭代间隔（秒）" htmlFor="disc-interval" hint="报告口径为 5 分钟一轮">
              <Input
                id="disc-interval"
                type="number"
                value={form.iterationIntervalS}
                onChange={(e) =>
                  patch({
                    iterationIntervalS: clamp(
                      Number(e.target.value),
                      defaults.iterationIntervalS.min,
                      defaults.iterationIntervalS.max,
                    ),
                  })
                }
              />
            </Field>
            <Field label="数据刷新（秒）" htmlFor="disc-refresh" hint="0 表示固定窗口不刷新">
              <Input
                id="disc-refresh"
                type="number"
                value={form.dataRefreshS}
                onChange={(e) => patch({ dataRefreshS: Math.max(0, Number(e.target.value)) })}
              />
            </Field>
            <Field label="最小 |RankIC|" htmlFor="disc-ic">
              <Input
                id="disc-ic"
                type="number"
                step="0.01"
                value={form.thresholds.minAbsIc}
                onChange={(e) =>
                  patch({ thresholds: { ...form.thresholds, minAbsIc: Number(e.target.value) } })
                }
              />
            </Field>
            <Field label="最小 |IR|" htmlFor="disc-ir">
              <Input
                id="disc-ir"
                type="number"
                step="0.1"
                value={form.thresholds.minAbsIr}
                onChange={(e) =>
                  patch({ thresholds: { ...form.thresholds, minAbsIr: Number(e.target.value) } })
                }
              />
            </Field>
            <Field label="精英池上限" htmlFor="disc-kept">
              <Input
                id="disc-kept"
                type="number"
                value={form.maxKept}
                onChange={(e) =>
                  patch({
                    maxKept: clamp(Number(e.target.value), defaults.maxKept.min, defaults.maxKept.max),
                  })
                }
              />
            </Field>
            <Field label="LLM 每小时调用上限" htmlFor="disc-llm" hint="0 表示不调用 LLM（纯演化搜索）">
              <Input
                id="disc-llm"
                type="number"
                value={form.llmMaxCallsPerHour}
                onChange={(e) =>
                  patch({ llmMaxCallsPerHour: Math.max(0, Number(e.target.value)) })
                }
              />
            </Field>
            <label className="col-span-2 flex items-center gap-2 text-xs text-[var(--muted)]">
              <input
                type="checkbox"
                checked={form.autoResume}
                onChange={(e) => patch({ autoResume: e.target.checked })}
              />
              进程重启后自动续跑（受挖掘总闸一票否决）
            </label>
          </div>
        )}

        {error && (
          <p role="alert" className="text-xs text-[var(--short)]">
            {error}
          </p>
        )}

        <div className="flex items-center justify-between gap-2 pt-1">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => defaults && setForm(initialForm(defaults, instId, bar))}
          >
            恢复默认
          </Button>
          <div className="flex gap-2">
            {step === 2 && (
              <Button variant="ghost" size="sm" onClick={() => setStep(1)}>
                上一步
              </Button>
            )}
            <Button variant="ghost" size="sm" onClick={onClose}>
              取消
            </Button>
            {step === 1 ? (
              <Button variant="primary" size="sm" disabled={overLimit} onClick={() => setStep(2)}>
                下一步
              </Button>
            ) : (
              <Button variant="primary" size="sm" disabled={submitting || overLimit} onClick={submit}>
                {submitting ? "启动中…" : "开始挖掘"}
              </Button>
            )}
          </div>
        </div>
      </div>
    </Modal>
  );
}
