"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Badge, Button, Input, Select, Switch } from "@/components/ui";
import { api } from "@/lib/api";
import {
  addMembersCapped,
  applyWeights,
  factorSetToBuilderState,
  formatIc,
  memberIdentity,
  overfitWarning,
  resolveThresholds,
  toggleMember,
  trainedFromSnapshot,
  validateMembers,
  weightsCoverMembers,
} from "@/lib/factor";
import { factorApi } from "@/lib/factorApi";
import {
  DEFAULT_THRESHOLDS,
  type FactorMember,
  type FactorScoreSnapshotParams,
  type FactorThresholds,
  type TrainMethod,
  type TrainResult,
} from "@/lib/factorTypes";
import { buildTrainTarget, methodLabel, weightResultToTrained } from "@/lib/weightModel";
import { buildBatchTarget } from "@/lib/weightBatch";
import { useAppStore } from "@/lib/store";
import MemberPalette from "./MemberPalette";
import WeightBars from "./WeightBars";
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

function nowTs(): number {
  return Math.floor(Date.now() / 1000);
}

/**
 * 权重训练与组合（新建 | 编辑双模）：选成员 + 方法 → 训练 → 权重条 + 复合 IC → 保存 / 更新组合 / 从组合
 * 新建策略。挂载时消费 store.factorEditTarget 一次性水合（set 失败回落策略内联快照，P1-1），顶部呈现
 * 编辑态横幅 +「新建组合」清空（P1-3）。
 */
export default function FactorSetBuilder() {
  const qc = useQueryClient();
  const wb = useWorkbench();
  const setActiveView = useAppStore((s) => s.setActiveView);
  const setStrategyId = useAppStore((s) => s.setStrategyId);
  const factorEditTarget = useAppStore((s) => s.factorEditTarget);
  const setFactorEditTarget = useAppStore((s) => s.setFactorEditTarget);
  // 权重模型工作区交接：训练权重跳转 + 回流水合（weight-model-workspace §3.4）。
  const setSection = useAppStore((s) => s.setFactorLabSection);
  const setWeightTrainTarget = useAppStore((s) => s.setWeightTrainTarget);
  const setWeightBatchTarget = useAppStore((s) => s.setWeightBatchTarget);
  const weightTrainResult = useAppStore((s) => s.weightTrainResult);
  const setWeightTrainResult = useAppStore((s) => s.setWeightTrainResult);
  const boundModelId = useAppStore((s) => s.boundModelId);
  const setBoundModelId = useAppStore((s) => s.setBoundModelId);

  const lib = useQuery({ queryKey: ["factor", "library"], queryFn: factorApi.library });
  const trainers = useQuery({ queryKey: ["factor", "trainers"], queryFn: factorApi.trainers });

  const [members, setMembers] = useState<FactorMember[]>([]);
  const [method, setMethod] = useState<TrainMethod>("linear");
  const [name, setName] = useState("我的因子组合");
  const [thresholds, setThresholds] = useState<FactorThresholds>({ ...DEFAULT_THRESHOLDS });
  const [trained, setTrained] = useState<TrainResult | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  // 编辑指针：editingSetId!=null → 更新组合态；editStrategyId!=null → 来自交易面板旧策略快照（可更新该策略）。
  const [editingSetId, setEditingSetId] = useState<number | null>(null);
  const [editStrategyId, setEditStrategyId] = useState<number | null>(null);
  // 仅跳过「水合那一帧」的 [members] 训练作废（H3）；retrained 标记本会话是否重训过（决定更新组合的 trainedAt
  // 是否重打戳，P2-1）；loadedTrainedAt 保留载入组合的原 trainedAt 以便无重训更新时原样回传（P2-1）。
  const skipTrainResetRef = useRef(false);
  const retrainedRef = useRef(false);
  const loadedTrainedAtRef = useRef<number | null>(null);
  // 已消费的交接目标引用（一次性 + 防重入，P2-2）：清空 store 后 effect 会以 null 重跑、被早返回挡下；
  // async set 拉取回来后据此判断是否被更新的目标取代（避免陈旧水合）。
  const consumedTargetRef = useRef<typeof factorEditTarget>(null);
  // 权重模型回流的一次性消费引用（防重入，模式同 consumedTargetRef）。
  const consumedResultRef = useRef<typeof weightTrainResult>(null);

  // 合并跨工作区「加入组合」的待入成员（按 memberIdentity 去重，含规范化 params，评审 P1-1）。
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

  // 成员集合变化即作废上一轮训练结果（H3）；唯独跳过「水合那一帧」（编辑态载入已训练组合时勿误清）。
  useEffect(() => {
    if (skipTrainResetRef.current) {
      skipTrainResetRef.current = false;
      return;
    }
    setTrained(null);
  }, [members]);

  // 跨视图编辑交接：挂载消费 factorEditTarget，第一时间清空（一次性 + 防重入，P2-2），再分支水合。
  // 用 consumedTargetRef 而非「清空触发的卸载 cancelled」做防重入：清空 store 会以 null 重跑本 effect，
  // 若用 cleanup 置 cancelled 会在 async set 拉取返回前就把它取消（静默丢水合）；改为只在「目标被更新的
  // 目标取代」时跳过陈旧结果。
  useEffect(() => {
    const target = factorEditTarget;
    if (!target || consumedTargetRef.current === target) return;
    consumedTargetRef.current = target;
    setFactorEditTarget(null);

    const applyHydration = (next: {
      members: FactorMember[];
      method?: TrainMethod;
      name: string;
      thresholds: FactorThresholds;
      trained: TrainResult | null;
      editingSetId: number | null;
      editStrategyId: number | null;
      loadedTrainedAt: number | null;
      notice: string | null;
    }) => {
      skipTrainResetRef.current = true; // 跳过本次水合触发的 [members] 训练作废（H3）
      retrainedRef.current = false; // 水合的 trained 视为「未在本会话重训」（更新时不重打戳，P2-1）
      loadedTrainedAtRef.current = next.loadedTrainedAt;
      setBoundModelId(null); // H-F2：载入不同组合即复位绑定模型，杜绝跨上下文污染下一次「训练权重」
      setMembers(next.members);
      if (next.method) setMethod(next.method);
      setName(next.name);
      setThresholds(next.thresholds);
      setTrained(next.trained);
      setEditingSetId(next.editingSetId);
      setEditStrategyId(next.editStrategyId);
      setNotice(next.notice);
    };

    const hydrateFromSnapshot = (
      snap: FactorScoreSnapshotParams,
      opts: { name: string; editStrategyId: number | null; notice: string | null },
    ) =>
      applyHydration({
        members: snap.members,
        name: opts.name,
        thresholds: resolveThresholds(snap.thresholds),
        trained: trainedFromSnapshot(snap, method),
        editingSetId: null,
        editStrategyId: opts.editStrategyId,
        loadedTrainedAt: null,
        notice: opts.notice,
      });

    if (target.source === "snapshot") {
      hydrateFromSnapshot(target.snapshot, {
        name: target.name,
        editStrategyId: target.strategyId,
        notice: null,
      });
      return;
    }

    // source === "set"：拉取组合；失败回落内联快照兜底（P1-1）。仅在该目标仍是最新被消费目标时落地。
    void (async () => {
      try {
        const set = await factorApi.sets.get(target.setId);
        if (consumedTargetRef.current !== target) return;
        const state = factorSetToBuilderState(set);
        applyHydration({
          members: state.members,
          method: state.method,
          name: state.name,
          thresholds: state.thresholds,
          trained: state.trained,
          editingSetId: set.id,
          editStrategyId: null,
          loadedTrainedAt: typeof set.model?.trainedAt === "number" ? set.model.trainedAt : null,
          notice: null,
        });
      } catch {
        if (consumedTargetRef.current !== target) return;
        if (target.snapshot) {
          hydrateFromSnapshot(target.snapshot, {
            name: target.name ?? "我的因子组合",
            editStrategyId: target.strategyId ?? null,
            notice: "来源组合已删除，已按策略快照载入。",
          });
        } else {
          setNotice("载入组合失败：该组合可能已被删除，请返回列表刷新。");
        }
      }
    })();
    // 其余 setter / method 为水合内部读取，故意不入依赖（仅交接载荷变化时消费）。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [factorEditTarget]);

  const range = { instId: wb.instId, bar: wb.bar, startTs: wb.startTs, endTs: wb.endTs };
  const memberError = validateMembers(members);

  // 「训练权重」改为跳转「权重模型」实时训练视图（不再原地阻塞）：写交接载荷（含完整 builder 状态供无损回流，
  // R-02 携 boundModelId/editingSetId 复用同模型）→ 切到 models section（weight-model-workspace §3.4-A）。
  const goTrain = () => {
    if (memberError) {
      setNotice(memberError);
      return;
    }
    setWeightTrainTarget(
      buildTrainTarget({
        members,
        method,
        thresholds,
        horizon: 6,
        instId: range.instId,
        bar: range.bar,
        startTs: range.startTs,
        endTs: range.endTs,
        name,
        modelId: boundModelId ?? undefined,
        factorSetId: editingSetId ?? undefined,
        // 标的组训练（factor-lab-symbol-groups §3.5）：wb.symbols 含焦点，group 目标随来源组 id。
        symbols: wb.symbols,
        symbolGroupId: wb.target.kind === "group" ? wb.target.groupId : undefined,
      }),
    );
    setSection("models");
  };

  // 「多模型训练」→ 跳转「权重模型」竞技场启动器：写批次交接载荷（共享成员/标的/周期/时间窗，origin=builder
  // 供最佳候选回流组合，weight-multi-model-training §3.4）→ 切 models section。
  const goBatch = () => {
    if (memberError) {
      setNotice(memberError);
      return;
    }
    setWeightBatchTarget(
      buildBatchTarget({
        members,
        thresholds,
        horizon: 6,
        instId: range.instId,
        bar: range.bar,
        startTs: range.startTs,
        endTs: range.endTs,
        name,
        factorSetId: editingSetId ?? undefined,
        origin: "builder",
      }),
    );
    setSection("models");
  };

  // 消费权重模型回流：完整水合 members/method/name/thresholds/trained + 编辑指针（builder 重挂载已丢本地态，
  // 故载荷自包含；一次性 + 防重入，模式同 factorEditTarget）。trainedFresh 由既有 weightsCoverMembers 守门。
  useEffect(() => {
    const result = weightTrainResult;
    if (!result || consumedResultRef.current === result) return;
    consumedResultRef.current = result;
    setWeightTrainResult(null);
    skipTrainResetRef.current = true; // 跳过本次水合触发的 [members] 训练作废（H3）
    retrainedRef.current = true; // 视为本会话已重训：保存时重打 trainedAt（P2-1）
    loadedTrainedAtRef.current = null;
    setBoundModelId(result.modelId); // 记忆复用同一模型（R-02）
    setMembers(result.members);
    setMethod(result.method);
    setName(result.name);
    setThresholds({ ...result.thresholds, normWindow: result.normStats.normWindow });
    setTrained(weightResultToTrained(result));
    setEditingSetId(result.factorSetId ?? null);
    setEditStrategyId(null);
    setNotice("已应用训练权重，可保存 / 更新组合并自动同步交易策略");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [weightTrainResult]);

  // 单源真相：训练新鲜（权重键集恰好覆盖当前成员）才可保存/更新/建策略，否则提示原因（H3）。
  const trainedFresh = !!trained && weightsCoverMembers(members, trained.weights);
  const blockReason = memberError
    ? memberError
    : !trainedFresh
      ? "请先训练权重（成员变化后需重新训练）"
      : null;

  // 组合 model_json：无重训更新时原样回传载入的 trainedAt（不重打戳，P2-1），否则盖当前时刻。
  const buildModelJson = (): Record<string, unknown> => {
    if (!trained) return {};
    const trainedAt = retrainedRef.current ? nowTs() : loadedTrainedAtRef.current ?? nowTs();
    return {
      weights: trained.weights,
      normStats: trained.normStats,
      trainerVersion: trained.trainerVersion,
      provenance: trained.provenance,
      trainedAt,
    };
  };
  const setBody = () => ({
    name,
    members,
    method,
    modelJson: buildModelJson(),
    metricsJson: trained?.metrics ?? null,
    config: { thresholds },
  });
  // 落库后进入该组合编辑态：刷新编辑指针并重置 trainedAt 来源（后续无重训更新即原样回传）。
  const enterEditingSet = (set: { id: number; model?: { trainedAt?: number } }) => {
    setEditingSetId(set.id);
    loadedTrainedAtRef.current = typeof set.model?.trainedAt === "number" ? set.model.trainedAt : null;
    retrainedRef.current = false;
    void qc.invalidateQueries({ queryKey: ["factor", "sets"] });
  };
  const strategyParams = (factorSetId?: number): Record<string, unknown> => ({
    // 训练权重烘焙进成员、不写顶层 weights；带 normStats（打分取同一归一化契约 H2/H3）、provenance（仅 UI 展示
    // 训练区间 L4）、可选 factorSetId（弱关联来源组合，回链稳健 set 分支）。
    members: applyWeights(members, trained?.weights),
    thresholds: { ...thresholds },
    normStats: trained?.normStats,
    provenance: trained?.provenance,
    ...(factorSetId != null ? { factorSetId } : {}),
  });

  const save = useMutation({
    mutationFn: () => factorApi.sets.create(setBody()),
    onSuccess: (created) => {
      enterEditingSet(created);
      // 保存组合即自动部署伴生交易策略（后端单事务），失效交易策略列表使其同步出现（无需手动新建）。
      void qc.invalidateQueries({ queryKey: ["strategies"] });
      setNotice("因子组合已保存并同步到交易策略");
    },
  });
  const update = useMutation({
    mutationFn: () => factorApi.sets.update(editingSetId as number, setBody()),
    onSuccess: (updated) => {
      loadedTrainedAtRef.current =
        typeof updated.model?.trainedAt === "number" ? updated.model.trainedAt : loadedTrainedAtRef.current;
      retrainedRef.current = false;
      setNotice("因子组合已更新并同步到交易策略");
      // 双失效：组合更新连带刷新伴生策略快照与名称，两视图须同步。
      void qc.invalidateQueries({ queryKey: ["factor", "sets"] });
      void qc.invalidateQueries({ queryKey: ["strategies"] });
    },
  });
  // 更新该策略（仅 snapshot 分支旧策略）：写回当前快照；若已「另存为组合」则顺带回链 factorSetId。
  const updateStrategy = useMutation({
    mutationFn: () =>
      api.updateStrategy(editStrategyId as number, {
        params: strategyParams(editingSetId ?? undefined),
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
  const handleAddMany = (candidates: FactorMember[]) => {
    const { next, skipped } = addMembersCapped(members, candidates, MAX_MEMBERS);
    setMembers(next);
    if (skipped > 0) {
      setNotice(`已加入候选；${skipped} 个因重复或超出上限（最多 ${MAX_MEMBERS} 个）未加入`);
    }
  };
  // 「新建组合」清空：从编辑态显式回到全新搭建（杜绝静默从「更新」回落「新建」，P1-3）。
  const resetToNew = () => {
    setEditingSetId(null);
    setEditStrategyId(null);
    loadedTrainedAtRef.current = null;
    retrainedRef.current = false;
    setBoundModelId(null); // H-F2：新建组合即复位绑定模型
    setMembers([]);
    setMethod("linear");
    setName("我的因子组合");
    setThresholds({ ...DEFAULT_THRESHOLDS });
    setTrained(null);
    setNotice(null);
  };

  // 清空成员即复位绑定模型（成员集变化=新上下文，杜绝陈旧 boundModelId 污染，H-F2）。
  const clearMembers = () => {
    setMembers([]);
    setBoundModelId(null);
  };

  return (
    <div className="flex flex-col gap-4 pb-6">
      <EditBanner
        editingSetId={editingSetId}
        editStrategyId={editStrategyId}
        name={name}
        onReset={resetToNew}
      />

      <MemberPalette
        library={lib.data ?? []}
        members={members}
        onToggle={handleToggle}
        onAddMany={handleAddMany}
        onClear={clearMembers}
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
        <Button variant="primary" disabled={!!memberError} onClick={goTrain}>
          训练权重
        </Button>
        <Button variant="default" disabled={!!memberError} onClick={goBatch}
          title="在同一份成员 / 标的 / 周期 / 时间窗下并行训练多个方法，完成后按样本外 IC 择优">
          多模型训练
        </Button>
        <span className="pb-1 text-[11px] text-[var(--muted)]">
          {memberError ?? "「训练权重」训单个方法；「多模型训练」并行训多个方法并择优，完成后一键回流"}
        </span>
      </div>

      {trained && (
        <div className="card flex flex-col gap-3 p-3">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-semibold text-[var(--text)]">训练结果</span>
            {/* G-F8：与直播 ResultSummary 同口径——方法本地化、「训练样本 / 验证样本」本地化（去原始 key / n_train）。 */}
            <Badge tone="accent">{methodLabel(method)}</Badge>
            {trained.trainerVersion && <Badge tone="neutral">{trained.trainerVersion}</Badge>}
            <Badge tone="neutral">样本内 IC {formatIc(trained.metrics.icIn)}</Badge>
            <Badge tone="neutral">样本外 IC {formatIc(trained.metrics.icOut)}</Badge>
            <span className="text-[11px] text-[var(--muted)]">
              训练样本 {trained.metrics.nTrain} · 验证样本 {trained.metrics.nValid}
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
        <SaveActions
          blockReason={blockReason}
          editingSetId={editingSetId}
          editStrategyId={editStrategyId}
          savePending={save.isPending}
          updatePending={update.isPending}
          updateStrategyPending={updateStrategy.isPending}
          onSave={() => save.mutate()}
          onUpdate={() => update.mutate()}
          onUpdateStrategy={() => updateStrategy.mutate()}
        />
      </div>
      <p className="text-[11px] text-[var(--muted)]">
        保存 / 更新组合后会自动同步到交易策略面板的「因子组合得分策略」，无需手动新建。
      </p>
      {blockReason && !memberError && (
        <p className="text-[11px] text-[var(--muted)]">{blockReason}</p>
      )}
      {notice && <p role="status" aria-live="polite" className="text-[11px] font-medium text-[var(--accent)]">{notice}</p>}
      {(save.isError || update.isError || updateStrategy.isError) && (
        <p className="text-[11px] text-[var(--short)]">
          操作失败：
          {((save.error || update.error || updateStrategy.error) as Error)?.message}
        </p>
      )}
    </div>
  );
}

// 编辑态横幅（P1-3）：显著呈现当前是「更新组合」还是「从交易策略载入快照」，并提供「新建组合」清空退路。
function EditBanner({
  editingSetId,
  editStrategyId,
  name,
  onReset,
}: {
  editingSetId: number | null;
  editStrategyId: number | null;
  name: string;
  onReset: () => void;
}) {
  if (editingSetId == null && editStrategyId == null) return null;
  const label =
    editingSetId != null
      ? `正在编辑组合「${name}」 — 保存将更新该组合（#${editingSetId}）`
      : `已从交易策略「${name}」载入内联快照 — 可「另存为组合」或「更新该策略」`;
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 rounded-[10px] border border-[var(--accent)] bg-[var(--accent-soft)]/50 px-3 py-2">
      <span className="text-[11px] font-medium text-[var(--accent)]">{label}</span>
      <Button variant="ghost" size="sm" onClick={onReset}>新建组合</Button>
    </div>
  );
}

// 保存区动作：按当前模式（新建 / 更新组合 / 旧策略快照）渲染按钮，全部复用同一 blockReason 禁用门（P1-3）。
// 组合保存即自动部署伴生交易策略，故不再有「从组合新建策略」手动按钮（factor-combo-strategy-sync）。
function SaveActions({
  blockReason,
  editingSetId,
  editStrategyId,
  savePending,
  updatePending,
  updateStrategyPending,
  onSave,
  onUpdate,
  onUpdateStrategy,
}: {
  blockReason: string | null;
  editingSetId: number | null;
  editStrategyId: number | null;
  savePending: boolean;
  updatePending: boolean;
  updateStrategyPending: boolean;
  onSave: () => void;
  onUpdate: () => void;
  onUpdateStrategy: () => void;
}) {
  const disabled = !!blockReason;
  // 新建态主按钮「保存组合」；来自交易面板旧策略快照时为「另存为组合」（流程B，§2.3）；编辑组合态为「更新组合」。
  const saveLabel = editStrategyId != null ? "另存为组合" : "保存组合";
  return (
    <>
      {editingSetId != null ? (
        <Button variant="primary" disabled={disabled || updatePending} title={blockReason ?? "更新该因子组合"} onClick={onUpdate}>
          {updatePending ? "更新中..." : "更新组合"}
        </Button>
      ) : (
        <Button variant="primary" disabled={disabled || savePending} title={blockReason ?? "保存因子组合"} onClick={onSave}>
          {savePending ? "保存中..." : saveLabel}
        </Button>
      )}
      {editingSetId != null && (
        <Button variant="default" disabled={disabled || savePending} title={blockReason ?? "另存为一个新的因子组合"} onClick={onSave}>
          {savePending ? "保存中..." : "另存为新组合"}
        </Button>
      )}
      {editStrategyId != null && (
        <Button
          variant="default"
          disabled={disabled || updateStrategyPending}
          title={blockReason ?? "把当前快照写回该 factor_score 策略"}
          onClick={onUpdateStrategy}
        >
          {updateStrategyPending ? "更新中..." : "更新该策略"}
        </Button>
      )}
    </>
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
