"use client";

import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { useMemo, useState } from "react";
import { Badge, Button, Input } from "@/components/ui";
import {
  candidateIdentity,
  factorRowToMember,
  filterLibraryItems,
  isCandidateSelected,
  libraryItemToMember,
} from "@/lib/factor";
import { factorApi } from "@/lib/factorApi";
import type { Factor, FactorLibraryItem, FactorMember, TrainMethod } from "@/lib/factorTypes";
import { ALL_METHODS, methodLabel } from "@/lib/weightBatch";
import { methodDescription } from "@/lib/weightModel";
import {
  MAX_EXPLORE_CANDIDATES,
  MAX_EXPLORE_POOL,
  MIN_EXPLORE_CANDIDATES,
  estimateComboCount,
  type ExploreParams,
  type ExploreStrategy,
} from "@/lib/weightExplore";
import ManualComboBuilder from "./ManualComboBuilder";

// 探索启动配置（config 态回填给弹窗构造请求体）。
export interface ExploreConfig {
  pool: FactorMember[];
  strategy: ExploreStrategy;
  params: ExploreParams;
  methods: TrainMethod[];
}

const STRATEGIES: { value: ExploreStrategy; label: string; hint: string }[] = [
  { value: "sizes", label: "指定子集大小", hint: "对每个所选大小 k，穷举池中全部 C(池,k) 子集组合" },
  { value: "topk_prefix", label: "IC 排序前缀", hint: "按当前池顺序取各大小的嵌套前缀子集（少而稳，非贪心搜索）" },
  { value: "leave_one_out", label: "留一法", hint: "全池 + 逐一去掉单因子，定位拖后腿的因子" },
  { value: "manual", label: "手动指定", hint: "亲手勾选要对比的具体组合" },
];

// 默认探索方法：覆盖线性 / 树集成 + 一款非线性示例；取较快的 extra_trees 而非 O(n³) 的 kernel_ridge，
// 以免默认探索体感变慢（评审 v2 P2-6）。
const DEFAULT_METHODS: TrainMethod[] = ["linear", "random_forest", "extra_trees"];

/**
 * 探索训练配置态（factor-combo-exploration §2.7）：编辑因子池 + 组合生成策略 + 训练方法 + 候选数实时预览。
 * 候选数（组合 × 方法）超上限 / 不足下限即显式阻断并禁用启动（绝不静默截断）。启动交由弹窗构造请求体。
 */
export default function ExploreConfigPanel({
  library,
  initialPool,
  onLaunch,
  launching,
  error,
}: {
  library: FactorLibraryItem[];
  initialPool: FactorMember[];
  onLaunch: (config: ExploreConfig) => void;
  launching: boolean;
  error?: string | null;
}) {
  const [pool, setPool] = useState<FactorMember[]>(initialPool);
  const [strategy, setStrategy] = useState<ExploreStrategy>("sizes");
  const [sizes, setSizes] = useState<number[]>([]);
  const [combos, setCombos] = useState<number[][]>([]); // 手动指定组合的池内下标（manual 策略）
  const labelOf = (m: FactorMember) => library.find((f) => f.key === m.factorKey)?.cn ?? m.factorKey;
  const trainers = useQuery({ queryKey: ["factor", "trainers"], queryFn: factorApi.trainers });
  const available = (m: TrainMethod) => trainers.data?.find((t) => t.method === m)?.available ?? true;
  const [methods, setMethods] = useState<TrainMethod[]>(DEFAULT_METHODS);

  // 有效大小：strategy 用 sizes 时取当前勾选与池大小的交集（默认全池的「去一 / 全池」两档，少候选起步）。
  const usesSizes = strategy === "sizes" || strategy === "topk_prefix";
  const effectiveSizes = useMemo(() => {
    const inRange = sizes.filter((k) => k >= 2 && k <= pool.length);
    if (inRange.length > 0) return inRange;
    return pool.length >= 2 ? [Math.max(2, pool.length - 1), pool.length].filter((v, i, a) => a.indexOf(v) === i) : [];
  }, [sizes, pool.length]);

  // P2-1：manual 是新增第三分支（非替代 usesSizes 分支）；漏成 {} 会让 planCombos("manual",{}) 抛错→永久阻断。
  const params: ExploreParams = strategy === "manual" ? { combos } : usesSizes ? { sizes: effectiveSizes } : {};
  const activeMethods = methods.filter(available);
  const comboCount = safeCount(pool, strategy, params);
  const candidateCount = comboCount * activeMethods.length;

  const tooMany = candidateCount > MAX_EXPLORE_CANDIDATES;
  const tooFew = candidateCount < MIN_EXPLORE_CANDIDATES;
  const poolTooSmall = pool.length < 2;
  const noMethod = activeMethods.length < 1;
  const blocked = tooMany || tooFew || poolTooSmall || noMethod || launching;

  const toggleMethod = (m: TrainMethod) =>
    setMethods((prev) => (prev.includes(m) ? prev.filter((x) => x !== m) : [...prev, m]));

  return (
    <div className="flex flex-col gap-4">
      <PoolEditor library={library} pool={pool} onChange={setPool} />

      <section className="card flex flex-col gap-2 p-3">
        <span className="text-[11px] font-medium text-[var(--muted)]">组合生成策略</span>
        <div className="flex flex-wrap gap-1.5">
          {STRATEGIES.map((s) => (
            <button
              key={s.value}
              type="button"
              aria-pressed={strategy === s.value}
              title={s.hint}
              onClick={() => setStrategy(s.value)}
              className={clsx(
                "rounded-[8px] border px-3 py-1.5 text-xs font-medium transition-colors",
                strategy === s.value
                  ? "border-[var(--accent)] bg-[var(--accent-soft)] text-[var(--accent-ink)]"
                  : "border-[var(--border)] text-[var(--muted)] hover:text-[var(--text)]",
              )}
            >
              {s.label}
            </button>
          ))}
        </div>
        {usesSizes ? (
          <SizePicker poolSize={pool.length} selected={effectiveSizes} onToggle={(k) => setSizes((prev) => togglePick(prev, k, effectiveSizes))} />
        ) : strategy === "manual" ? (
          <ManualComboBuilder pool={pool} labelOf={labelOf} value={combos} onChange={setCombos} />
        ) : (
          <p className="text-[11px] text-[var(--muted)]">
            将训练 全池（1 个）+ 逐一去 1（{pool.length} 个）= 共 {pool.length + 1} 个组合。
          </p>
        )}
      </section>

      <section className="card flex flex-col gap-2 p-3">
        <span className="text-[11px] font-medium text-[var(--muted)]">训练方法（至少 1 个，并行训练后按样本外 IC 择优）</span>
        <div className="flex flex-wrap gap-2">
          {ALL_METHODS.map((m) => {
            const disabled = !available(m);
            const checked = methods.includes(m);
            return (
              <label
                key={m}
                title={methodDescription(m)}
                className={clsx(
                  "flex cursor-pointer items-center gap-2 rounded-[10px] border px-3 py-1.5 text-xs transition-colors",
                  disabled
                    ? "cursor-not-allowed border-[var(--border)] bg-[var(--surface-2)] text-[var(--muted)]"
                    : checked
                      ? "border-[var(--accent)] bg-[var(--accent-soft)]/50 text-[var(--text)]"
                      : "border-[var(--border)] text-[var(--text)] hover:border-[var(--accent-soft)]",
                )}
              >
                <input type="checkbox" checked={checked} disabled={disabled} onChange={() => toggleMethod(m)} className="accent-[var(--accent)]" />
                <span>{methodLabel(m)}</span>
                {disabled && <Badge tone="neutral">未安装</Badge>}
              </label>
            );
          })}
        </div>
      </section>

      <CandidatePreview
        comboCount={comboCount}
        methodCount={activeMethods.length}
        candidateCount={candidateCount}
        tooMany={tooMany}
        tooFew={tooFew}
        poolTooSmall={poolTooSmall}
        noMethod={noMethod}
      />

      {error && (
        <p role="alert" className="rounded-[10px] border border-[var(--short)]/40 bg-[var(--short)]/10 px-3 py-2 text-[11px] font-medium text-[var(--short-ink)]">
          {error}
        </p>
      )}

      <Button
        variant="primary"
        disabled={blocked}
        onClick={() => onLaunch({ pool, strategy, params, methods: activeMethods })}
      >
        {launching ? "启动中…" : `开始探索训练（${candidateCount} 个候选）`}
      </Button>
    </div>
  );
}

function safeCount(pool: FactorMember[], strategy: ExploreStrategy, params: ExploreParams): number {
  try {
    return estimateComboCount(pool, strategy, params);
  } catch {
    return 0; // 非法计划（如 manual 越界）→ 0，由预览的下限提示禁用启动
  }
}

function togglePick(prev: number[], k: number, effective: number[]): number[] {
  // prev 为空时以 effective（默认档）为基准再切换，避免「默认档不可取消」的错觉。
  const base = prev.length > 0 ? prev : effective;
  return base.includes(k) ? base.filter((x) => x !== k) : [...base, k];
}

function SizePicker({ poolSize, selected, onToggle }: { poolSize: number; selected: number[]; onToggle: (k: number) => void }) {
  const options = Array.from({ length: Math.max(0, poolSize - 1) }, (_, i) => i + 2); // 2..poolSize
  if (options.length === 0) return <p className="text-[11px] text-[var(--muted)]">因子池至少需 2 个因子。</p>;
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="text-[11px] text-[var(--muted)]">子集大小：</span>
      {options.map((k) => (
        <button
          key={k}
          type="button"
          aria-pressed={selected.includes(k)}
          onClick={() => onToggle(k)}
          className={clsx(
            "rounded-full px-2.5 py-0.5 text-[11px] font-medium transition-colors",
            selected.includes(k)
              ? "bg-[var(--accent-soft)] text-[var(--accent-ink)]"
              : "bg-[var(--surface-2)] text-[var(--muted)] hover:text-[var(--text)]",
          )}
        >
          {k} 因子
        </button>
      ))}
    </div>
  );
}

function CandidatePreview({
  comboCount,
  methodCount,
  candidateCount,
  tooMany,
  tooFew,
  poolTooSmall,
  noMethod,
}: {
  comboCount: number;
  methodCount: number;
  candidateCount: number;
  tooMany: boolean;
  tooFew: boolean;
  poolTooSmall: boolean;
  noMethod: boolean;
}) {
  return (
    <div className="flex flex-col gap-1">
      <p className="text-xs text-[var(--text)]">
        预览：<span className="font-semibold tabular">{comboCount}</span> 组合 ×{" "}
        <span className="font-semibold tabular">{methodCount}</span> 方法 ={" "}
        <span className={clsx("font-semibold tabular", tooMany ? "text-[var(--short-ink)]" : "text-[var(--accent-ink)]")}>{candidateCount}</span> 候选
      </p>
      {poolTooSmall && <p className="text-[11px] text-[var(--short-ink)]">因子池至少需 2 个因子才能探索子集组合。</p>}
      {noMethod && !poolTooSmall && <p className="text-[11px] text-[var(--short-ink)]">请至少选择 1 个可用训练方法。</p>}
      {tooMany && (
        <p className="text-[11px] font-medium text-[var(--short-ink)]">
          候选数超过上限 {MAX_EXPLORE_CANDIDATES}，请收窄组合大小或减少训练方法后再启动。
        </p>
      )}
      {tooFew && !tooMany && !poolTooSmall && !noMethod && (
        <p className="text-[11px] text-[var(--muted)]">至少需 {MIN_EXPLORE_CANDIDATES} 个候选才能对比择优。</p>
      )}
    </div>
  );
}

// 因子池编辑器：池 chip（可移除）+ 紧凑候选选择器（库目录 / 我的因子，点击加入，去重 + 上限 12）。
function PoolEditor({ library, pool, onChange }: { library: FactorLibraryItem[]; pool: FactorMember[]; onChange: (next: FactorMember[]) => void }) {
  const cn = (key: string) => library.find((f) => f.key === key)?.cn ?? key;
  const [source, setSource] = useState<"library" | "mine">("library");
  const [q, setQ] = useState("");
  const mine = useQuery({
    queryKey: ["factor", "list", { enabled: true, sort: "rankic" }],
    queryFn: () => factorApi.list({ enabled: true, sort: "rankic" }),
    enabled: source === "mine",
  });
  const full = pool.length >= MAX_EXPLORE_POOL;

  const candidates: FactorMember[] =
    source === "library"
      ? filterLibraryItems(library, q, "").map(libraryItemToMember)
      : (mine.data ?? []).map((f: Factor) => factorRowToMember(f, library)).filter((m) => matches(m, cn(m.factorKey), q));

  const addable = candidates.filter((c) => !isCandidateSelected(pool, c));
  const add = (m: FactorMember) => {
    if (isCandidateSelected(pool, m) || full) return;
    onChange([...pool, m]);
  };
  const removeAt = (i: number) => onChange(pool.filter((_, idx) => idx !== i));

  return (
    <section className="card flex flex-col gap-2 p-3">
      <div className="flex items-center justify-between gap-2">
        <span className="text-[11px] font-medium text-[var(--muted)]">探索因子池（{pool.length} / {MAX_EXPLORE_POOL}）</span>
        <span className="text-[10px] text-[var(--muted)]">默认取当前已选成员，可增删</span>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {pool.length === 0 && <span className="text-[11px] text-[var(--muted)]">池为空，请从下方添加因子。</span>}
        {pool.map((m, i) => (
          <span key={`${m.factorKey}-${i}`} className="flex items-center gap-1 rounded-full bg-[var(--surface-2)] px-2 py-0.5 text-[11px] text-[var(--text)]">
            {cn(m.factorKey)}
            <button type="button" aria-label={`移除 ${cn(m.factorKey)}`} className="text-[var(--muted)] hover:text-[var(--short-ink)]" onClick={() => removeAt(i)}>
              ×
            </button>
          </span>
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex gap-0.5 rounded-[8px] bg-[var(--surface-2)] p-0.5">
          {(
            [
              ["library", "库目录"],
              ["mine", "我的因子"],
            ] as const
          ).map(([val, label]) => (
            <button
              key={val}
              type="button"
              onClick={() => setSource(val)}
              className={clsx(
                "rounded-[6px] px-2 py-0.5 text-[11px] font-medium transition-all",
                source === val ? "bg-[var(--accent-soft)] text-[var(--accent-ink)]" : "text-[var(--muted)] hover:text-[var(--text)]",
              )}
            >
              {label}
            </button>
          ))}
        </div>
        <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="搜索名称 / key…" className="h-7 flex-1 py-1 text-xs" style={{ minWidth: 120 }} />
      </div>
      <div className="flex max-h-32 flex-wrap gap-1.5 overflow-y-auto">
        {addable.slice(0, 40).map((m) => (
          <button
            key={candidateIdentity(m)}
            type="button"
            disabled={full}
            title={full ? "已达因子池上限" : `加入 ${cn(m.factorKey)}`}
            onClick={() => add(m)}
            className="rounded-[8px] border border-[var(--border)] bg-[var(--surface)] px-2 py-1 text-[11px] text-[var(--text)] transition-colors hover:border-[var(--accent)] disabled:cursor-not-allowed disabled:opacity-40"
          >
            + {cn(m.factorKey)}
          </button>
        ))}
        {addable.length === 0 && <span className="text-[11px] text-[var(--muted)]">无可添加候选。</span>}
      </div>
    </section>
  );
}

function matches(member: FactorMember, name: string, q: string): boolean {
  if (!q) return true;
  const needle = q.trim().toLowerCase();
  return member.factorKey.toLowerCase().includes(needle) || name.toLowerCase().includes(needle);
}
