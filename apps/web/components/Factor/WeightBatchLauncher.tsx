"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Badge, Button } from "@/components/ui";
import { ApiRequestError } from "@/lib/api";
import { fmtTime } from "@/lib/format";
import { factorApi } from "@/lib/factorApi";
import type { TrainMethod } from "@/lib/factorTypes";
import { ALL_METHODS, DEFAULT_BATCH_METHODS, batchStartBody, methodLabel } from "@/lib/weightBatch";
import { codeLabel } from "@/lib/weightModel";
import { weightBatchApi } from "@/lib/weightBatchApi";
import type { WeightBatchStartResult, WeightBatchTarget } from "@/lib/weightBatchTypes";

const MAX_BATCH_CANDIDATES = 8;   // 与后端默认 max_weight_batch_candidates 对齐（前端护栏）
const MIN_BATCH_CANDIDATES = 2;   // 多模型对比至少 2 个候选

/**
 * 多模型训练竞技场启动器：共享规格摘要 + 方法多选（不可用方法禁用）+「开始批量训练」。
 * 无交接载荷（从列表直接进入）时呈引导态，指回「权重训练与组合」搭建成员（HCI：绝不空手启动）。
 *
 * 每个勾选方法 = 一个候选（字节级等价于独立训练该方法）；候选数受上下限约束（≥2 才可对比、≤上限封资源）。
 */
export default function WeightBatchLauncher({
  target,
  onLaunched,
  onBack,
  onGoBuilder,
}: {
  target: WeightBatchTarget | null;
  onLaunched: (result: WeightBatchStartResult, target: WeightBatchTarget) => void;
  onBack: () => void;
  onGoBuilder: () => void;
}) {
  const trainers = useQuery({ queryKey: ["factor", "trainers"], queryFn: factorApi.trainers });
  const available = (m: TrainMethod) => trainers.data?.find((t) => t.method === m)?.available ?? true;

  const initial = useMemo<TrainMethod[]>(() => {
    const preset = target?.methods && target.methods.length > 0 ? target.methods : DEFAULT_BATCH_METHODS;
    return preset.filter((m) => ALL_METHODS.includes(m));
  }, [target]);
  const [selected, setSelected] = useState<TrainMethod[]>(initial);

  // 预选方法可能不可用（如未装 xgboost）：trainers 解析后剔除不可用项——禁用的 checkbox 无法取消勾选，
  // 若不剔除会把注定失败的候选一并提交（禁用应同时阻止提交）。
  useEffect(() => {
    const list = trainers.data;
    if (!list) return;
    setSelected((prev) => {
      const pruned = prev.filter((m) => list.find((t) => t.method === m)?.available ?? true);
      return pruned.length === prev.length ? prev : pruned;
    });
  }, [trainers.data]);

  const launch = useMutation({
    mutationFn: () => weightBatchApi.start(batchStartBody(target as WeightBatchTarget, selected.map((m) => ({ method: m })))),
    onSuccess: (res) => target && onLaunched(res, target),
  });

  if (!target) {
    return (
      <div className="flex flex-col gap-4 pb-6">
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="sm" onClick={onBack}>← 返回列表</Button>
          <span className="text-sm font-semibold text-[var(--text)]">多模型训练</span>
        </div>
        <div className="card flex flex-col items-center gap-3 p-8 text-center">
          <span className="text-sm font-medium text-[var(--text)]">先在「权重训练与组合」选好成员</span>
          <span className="text-[11px] text-[var(--muted)]">
            多模型训练在同一份成员 / 标的 / 周期 / 时间窗下并行训练多个方法，请先在搭建台选好因子成员，再点「多模型训练」。
          </span>
          <Button variant="primary" size="sm" onClick={onGoBuilder}>去权重训练与组合</Button>
        </div>
      </div>
    );
  }

  const toggle = (m: TrainMethod) => {
    setSelected((prev) => {
      if (prev.includes(m)) return prev.filter((x) => x !== m);
      if (prev.length >= MAX_BATCH_CANDIDATES) return prev;   // 候选数封顶（前端护栏）
      return [...prev, m];
    });
  };

  const atCap = selected.length >= MAX_BATCH_CANDIDATES;
  const tooFew = selected.length < MIN_BATCH_CANDIDATES;

  return (
    <div className="flex flex-col gap-4 pb-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="sm" onClick={onBack}>← 返回列表</Button>
          <span className="text-sm font-semibold text-[var(--text)]">多模型训练竞技场</span>
          <span className="text-[11px] text-[var(--muted)]">
            {target.instId} · {target.bar} · {target.members.length} 成员 · 前瞻 {target.horizon} 根
          </span>
          <span className="text-[11px] text-[var(--muted)]">
            区间 {fmtTime(target.startTs)} – {fmtTime(target.endTs)}
          </span>
        </div>
      </div>

      <div className="card flex flex-col gap-3 p-3">
        <div className="flex flex-col gap-0.5">
          <span className="text-sm font-medium text-[var(--text)]">选择候选方法（并行训练，完成后按样本外 IC 择优）</span>
          <span className="text-[11px] text-[var(--muted)]">
            共享同一份成员 / 标的 / 周期 / 时间窗，仅方法不同；已选 {selected.length} 个（{MIN_BATCH_CANDIDATES}–{MAX_BATCH_CANDIDATES} 个）
          </span>
        </div>
        <div className="flex flex-wrap gap-2">
          {ALL_METHODS.map((m) => {
            const disabled = !available(m);
            const checked = selected.includes(m);
            const blockAdd = !checked && atCap;
            return (
              <label
                key={m}
                className={
                  "flex cursor-pointer items-center gap-2 rounded-[10px] border px-3 py-2 text-xs transition-colors " +
                  (disabled || blockAdd
                    ? "cursor-not-allowed border-[var(--border)] bg-[var(--surface-2)] text-[var(--muted)]"
                    : checked
                      ? "border-[var(--accent)] bg-[var(--accent-soft)]/50 text-[var(--text)]"
                      : "border-[var(--border)] hover:border-[var(--accent-soft)] text-[var(--text)]")
                }
              >
                <input
                  type="checkbox"
                  checked={checked}
                  disabled={disabled || blockAdd}
                  onChange={() => toggle(m)}
                  className="accent-[var(--accent)]"
                />
                <span>{methodLabel(m)}</span>
                {disabled && <Badge tone="neutral">未安装</Badge>}
              </label>
            );
          })}
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <Button
          variant="primary"
          disabled={tooFew || launch.isPending}
          title={tooFew ? `至少选择 ${MIN_BATCH_CANDIDATES} 个方法` : "并行训练所选候选"}
          onClick={() => launch.mutate()}
        >
          {launch.isPending ? "启动中…" : `开始批量训练（${selected.length} 个候选）`}
        </Button>
        <span className="text-[11px] text-[var(--muted)]">
          {tooFew
            ? `至少选择 ${MIN_BATCH_CANDIDATES} 个方法才能对比择优`
            : "候选并行训练；超出并发上限的候选自动排队，名额释放即开跑"}
        </span>
      </div>

      {launch.isError && (
        <p role="alert" className="rounded-[10px] border border-[var(--short)]/40 bg-[var(--short)]/10 px-3 py-2 text-[11px] font-medium text-[var(--short-ink)]">
          启动失败（{codeLabel((launch.error as ApiRequestError)?.code)}）：{(launch.error as Error)?.message}
        </p>
      )}
    </div>
  );
}
