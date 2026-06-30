"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Badge, Button, Input, Select, Switch } from "@/components/ui";
import { api } from "@/lib/api";
import {
  addMembersCapped,
  applyWeights,
  formatIc,
  memberIdentity,
  overfitWarning,
  toggleMember,
  validateMembers,
  weightBars,
  weightsCoverMembers,
} from "@/lib/factor";
import { factorApi } from "@/lib/factorApi";
import {
  DEFAULT_THRESHOLDS,
  type FactorMember,
  type FactorThresholds,
  type TrainMethod,
  type TrainResult,
} from "@/lib/factorTypes";
import { useAppStore } from "@/lib/store";
import MemberPalette from "./MemberPalette";
import { useWorkbench } from "./FactorLab";

// 组合成员数上限（与 validateMembers 同口径，作为批量并入 / 切换的护栏入参）。
const MAX_MEMBERS = 12;

const METHODS: { value: TrainMethod; label: string }[] = [
  { value: "linear", label: "线性岭回归" },
  { value: "equal", label: "等权" },
  { value: "ic_weight", label: "IC 加权" },
  { value: "random_forest", label: "随机森林" },
  { value: "xgboost", label: "XGBoost" },
];

/** 权重训练与组合：选成员 + 方法 → 训练 → 权重条 + 复合 IC → 保存组合 / 从组合新建策略。 */
export default function FactorSetBuilder() {
  const qc = useQueryClient();
  const wb = useWorkbench();
  const setActiveView = useAppStore((s) => s.setActiveView);
  const setStrategyId = useAppStore((s) => s.setStrategyId);

  const lib = useQuery({ queryKey: ["factor", "library"], queryFn: factorApi.library });
  const trainers = useQuery({ queryKey: ["factor", "trainers"], queryFn: factorApi.trainers });

  const [members, setMembers] = useState<FactorMember[]>([]);
  const [method, setMethod] = useState<TrainMethod>("linear");
  const [name, setName] = useState("我的因子组合");
  const [thresholds, setThresholds] = useState<FactorThresholds>({ ...DEFAULT_THRESHOLDS });
  const [trained, setTrained] = useState<TrainResult | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // 合并跨工作区「加入组合」的待入成员（按 memberIdentity 去重后并入，随即清空待入队列防重复并入）。
  // 去重键含规范化 params（评审 P1-1），避免静默丢弃同 key 不同参的挖掘变体。
  useEffect(() => {
    if (wb.pendingMembers.length === 0) return;
    setMembers((prev) => {
      const merged = [...prev];
      const seen = new Set(merged.map(memberIdentity));
      for (const m of wb.pendingMembers) {
        const id = memberIdentity(m);
        if (!seen.has(id)) {
          merged.push(m);
          seen.add(id);
        }
      }
      return merged;
    });
    wb.clearPendingMembers();
  }, [wb.pendingMembers, wb]);

  // 成员集合变化即作废上一轮训练结果（强制重训，杜绝「成员变了权重没变」的静默错配，H3）。
  useEffect(() => {
    setTrained(null);
  }, [members]);

  const range = { instId: wb.instId, bar: wb.bar, startTs: wb.startTs, endTs: wb.endTs };
  const memberError = validateMembers(members);

  const train = useMutation({
    mutationFn: () =>
      factorApi.train({ ...range, members, method, config: { normWindow: thresholds.normWindow } }),
    // 训练成功后把归一化窗口锁定为训练写入的 normStats.normWindow（改窗口=重训，归一化契约 H1/H2）。
    onSuccess: (result) => {
      setTrained(result);
      setThresholds((prev) => ({ ...prev, normWindow: result.normStats.normWindow }));
    },
  });
  // 单源真相：训练新鲜（权重键集恰好覆盖当前成员）才可保存/建策略，否则提示原因（H3）。
  const trainedFresh = !!trained && weightsCoverMembers(members, trained.weights);
  const blockReason = memberError
    ? memberError
    : !trainedFresh
      ? "请先训练权重（成员变化后需重新训练）"
      : null;
  const save = useMutation({
    mutationFn: () =>
      factorApi.sets.create({
        name,
        members,
        method,
        modelJson: trained
          ? {
              weights: trained.weights,
              normStats: trained.normStats,
              trainerVersion: trained.trainerVersion,
              provenance: trained.provenance,
              trainedAt: Math.floor(Date.now() / 1000),
            }
          : {},
        metricsJson: trained?.metrics ?? null,
        config: { thresholds },
      }),
    onSuccess: () => {
      setNotice("因子组合已保存");
      void qc.invalidateQueries({ queryKey: ["factor", "sets"] });
    },
  });
  const createStrategy = useMutation({
    mutationFn: () =>
      api.createStrategy({
        templateKey: "factor_score",
        name: `${name} · 得分策略`,
        // 新建快照（结构对齐 FactorScoreSnapshotParams）：训练权重烘焙进成员、不再写顶层 weights；带 normStats
        // 供打分取同一归一化契约（H2/H3）；带 provenance 供 StrategyDetail 如实展示训练区间（仅 UI 展示，L4）。
        params: {
          members: applyWeights(members, trained?.weights),
          thresholds: { ...thresholds },
          normStats: trained?.normStats,
          provenance: trained?.provenance,
        },
      }),
    onSuccess: (strategy) => {
      setStrategyId(strategy.id);
      setActiveView("trade");
    },
  });

  const overfit = overfitWarning(trained?.metrics);
  const trainerOf = (m: TrainMethod) => trainers.data?.find((t) => t.method === m)?.available ?? true;

  // 单击候选切换：命中则移除该因子，否则在未满时追加；已满拒绝并提示上限（不静默吞，评审 R1 / §3.4）。
  const handleToggle = (member: FactorMember) => {
    const { next, capped } = toggleMember(members, member, MAX_MEMBERS);
    if (capped) {
      setNotice(`最多 ${MAX_MEMBERS} 个因子成员`);
      return;
    }
    setMembers(next);
  };
  // 「全选当前结果」批量并入：去重 + 截断到上限，被丢弃条数显式提示（§3.4）。
  const handleAddMany = (candidates: FactorMember[]) => {
    const { next, skipped } = addMembersCapped(members, candidates, MAX_MEMBERS);
    setMembers(next);
    if (skipped > 0) {
      setNotice(`已加入候选；${skipped} 个因重复或超出上限（最多 ${MAX_MEMBERS} 个）未加入`);
    }
  };

  return (
    <div className="flex flex-col gap-4 pb-6">
      <MemberPalette
        library={lib.data ?? []}
        members={members}
        onToggle={handleToggle}
        onAddMany={handleAddMany}
        onClear={() => setMembers([])}
        onRemove={(i) => setMembers((prev) => prev.filter((_, idx) => idx !== i))}
        onDirection={(i, direction) =>
          setMembers((prev) => prev.map((m, idx) => (idx === i ? { ...m, direction } : m)))
        }
        maxMembers={MAX_MEMBERS}
      />

      <div className="card flex flex-wrap items-end gap-3 p-3">
        <label className="flex flex-col gap-1">
          <span className="text-[11px] text-[var(--muted)]">训练方法</span>
          <Select value={method} onChange={(e) => setMethod(e.target.value as TrainMethod)} className="py-1 text-xs">
            {METHODS.map((m) => (
              <option key={m.value} value={m.value} disabled={!trainerOf(m.value)}>
                {m.label}{trainerOf(m.value) ? "" : "（未安装）"}
              </option>
            ))}
          </Select>
        </label>
        <Button variant="primary" disabled={!!memberError || train.isPending} onClick={() => train.mutate()}>
          {train.isPending ? "训练中..." : "训练权重"}
        </Button>
        {memberError && <span className="pb-1 text-[11px] text-[var(--short)]">{memberError}</span>}
      </div>

      {train.isError && (
        <p className="text-[11px] text-[var(--short)]">训练失败：{(train.error as Error)?.message}</p>
      )}

      {trained && (
        <div className="card flex flex-col gap-3 p-3">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-semibold text-[var(--text)]">训练结果</span>
            <Badge tone="accent">{method}</Badge>
            {/* 生效后端徽标（评审 P2-6）：内置兜底显示 builtin-rf@1 等、库后端显示库版本；核心训练器 trainerVersion=null 时短路不渲染。 */}
            {trained.trainerVersion && <Badge tone="neutral">{trained.trainerVersion}</Badge>}
            <Badge tone="neutral">样本内 IC {formatIc(trained.metrics.icIn)}</Badge>
            <Badge tone="neutral">样本外 IC {formatIc(trained.metrics.icOut)}</Badge>
            <span className="text-[11px] text-[var(--muted)]">
              n_train {trained.metrics.nTrain} · n_valid {trained.metrics.nValid}
            </span>
          </div>
          {overfit && <p className="text-[11px] font-medium text-[var(--short)]">{overfit}</p>}
          {trained.provenance && (
            <p className="text-[11px] text-[var(--muted)]">
              训练于 {trained.provenance.instId} · {trained.provenance.bar} · 前瞻 {trained.provenance.horizon} 根
              ；归一化窗口 {trained.normStats.normWindow}（已锁定，改窗口需重新训练）
            </p>
          )}
          <WeightBars weights={trained.weights} />
        </div>
      )}

      <ThresholdsEditor thresholds={thresholds} onChange={setThresholds} normWindowLocked={trainedFresh} />

      <div className="card flex flex-wrap items-end gap-3 p-3">
        <label className="flex flex-1 flex-col gap-1">
          <span className="text-[11px] text-[var(--muted)]">组合名称</span>
          <Input value={name} onChange={(e) => setName(e.target.value)} className="py-1.5 text-xs" />
        </label>
        <Button
          variant="default"
          disabled={!!blockReason || save.isPending}
          title={blockReason ?? "保存因子组合"}
          onClick={() => save.mutate()}
        >
          {save.isPending ? "保存中..." : "保存组合"}
        </Button>
        <Button
          variant="primary"
          disabled={!!blockReason || createStrategy.isPending}
          title={blockReason ?? "以内联快照创建 factor_score 策略，自动出现在策略列表 / 回测 / 对比"}
          onClick={() => createStrategy.mutate()}
        >
          {createStrategy.isPending ? "创建中..." : "从组合新建策略"}
        </Button>
      </div>
      {blockReason && !memberError && (
        <p className="text-[11px] text-[var(--muted)]">{blockReason}</p>
      )}
      {notice && <p className="text-[11px] font-medium text-[var(--accent)]">{notice}</p>}
      {(save.isError || createStrategy.isError) && (
        <p className="text-[11px] text-[var(--short)]">
          操作失败：{((save.error || createStrategy.error) as Error)?.message}
        </p>
      )}
    </div>
  );
}

function WeightBars({ weights }: { weights: Record<string, number> }) {
  const bars = weightBars(weights);
  return (
    <div className="flex flex-col gap-1.5">
      {bars.map((b) => (
        <div key={b.name} className="flex items-center gap-2">
          <span className="w-32 shrink-0 truncate text-[11px] text-[var(--muted)]">{b.name}</span>
          <div className="relative h-3 flex-1 rounded-full bg-[var(--surface-2)]">
            <div
              className="absolute top-0 h-3 rounded-full"
              style={{
                width: `${b.widthPct / 2}%`,
                left: b.positive ? "50%" : undefined,
                right: b.positive ? undefined : "50%",
                background: b.positive ? "var(--long)" : "var(--short)",
              }}
            />
            <div className="absolute left-1/2 top-0 h-3 w-px bg-[var(--border)]" />
          </div>
          <span className="w-14 shrink-0 text-right text-[11px] tabular text-[var(--text)]">{b.weight.toFixed(3)}</span>
        </div>
      ))}
    </div>
  );
}

function ThresholdsEditor({
  thresholds,
  onChange,
  normWindowLocked = false,
}: {
  thresholds: FactorThresholds;
  onChange: (next: FactorThresholds) => void;
  // 训练后归一化窗口由 normStats 锁定（改窗口=重训，归一化契约 H1/H2）。
  normWindowLocked?: boolean;
}) {
  const set = (key: keyof FactorThresholds, value: number | boolean) => onChange({ ...thresholds, [key]: value });
  const numField = (
    key: "enterLong" | "enterShort" | "exitBand" | "normWindow",
    label: string,
    step: number,
    locked = false,
  ) => (
    <label className="flex flex-col gap-1">
      <span className="text-[11px] text-[var(--muted)]">{label}</span>
      <input
        type="number"
        step={step}
        value={thresholds[key]}
        readOnly={locked}
        disabled={locked}
        title={locked ? "归一化窗口由训练锁定，改窗口需重新训练" : undefined}
        onChange={(e) => set(key, Number(e.target.value))}
        className={
          "w-24 rounded-[8px] border border-[var(--border)] px-2 py-1 text-xs tabular " +
          (locked ? "cursor-not-allowed bg-[var(--surface-2)] text-[var(--muted)]" : "bg-[var(--surface)]")
        }
      />
    </label>
  );
  return (
    <div className="card flex flex-wrap items-end gap-3 p-3">
      <span className="w-full text-[11px] font-medium text-[var(--muted)]">交易阈值（factor_score 策略用）</span>
      {numField("enterLong", "进多阈值", 0.05)}
      {numField("enterShort", "进空阈值", 0.05)}
      {numField("exitBand", "退出带", 0.05)}
      {numField("normWindow", "归一化窗口", 10, normWindowLocked)}
      <label className="flex flex-col gap-1">
        <span className="text-[11px] text-[var(--muted)]">tanh 压缩</span>
        <Switch checked={thresholds.squash} onChange={(v) => set("squash", v)} label="tanh 压缩到[-1,1]" />
      </label>
      {normWindowLocked && (
        <span className="w-full text-[10px] text-[var(--muted)]">
          归一化窗口已由训练锁定；若要改窗口，请改后重新训练以保持权重与打分契约一致。
        </span>
      )}
    </div>
  );
}
