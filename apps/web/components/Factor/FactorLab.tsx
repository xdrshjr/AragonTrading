"use client";

import clsx from "clsx";
import { createContext, useContext, useEffect, useMemo, useRef, useState } from "react";
import { Badge, Button, Select } from "@/components/ui";
import { factorTargetFocus, factorTargetSymbols, resolveFactorTarget } from "@/lib/factor";
import { useAppStore } from "@/lib/store";
import FactorCatalog from "./FactorCatalog";
import FactorEvalPanel from "./FactorEvalPanel";
import FactorMiningPanel from "./FactorMiningPanel";
import FactorSetBuilder from "./FactorSetBuilder";
import FactorSetList from "./FactorSetList";
import FactorTable from "./FactorTable";
import SymbolGroupModal from "./SymbolGroupModal";
import WeightModelPanel from "./WeightModelPanel";
import type { FactorDirection, FactorMember, FactorTarget, FactorTargetRef } from "@/lib/factorTypes";

// 跨工作区交接：评估目标因子（表/目录 → 评估）、待加入组合成员（评估/挖掘 → 组合）。
export interface EvalTarget {
  factorKey: string;
  params: Record<string, unknown>;
  direction: FactorDirection;
}

// 因子实验室工作台上下文：目标（单标的 / 标的组）/ 周期 / 评估区间（lab 内本地态，不扰动主工作台选择）。
export interface Workbench {
  target: FactorTarget;          // 当前目标（单标的 / 标的组）
  symbols: string[];             // 派生：single→[instId]；group→去空去重成员（驱动评估/挖掘/训练统计）
  instId: string;                // 焦点标的：single→instId；group→symbols[0]（图表/打分/建模默认标的）
  bar: string;
  startTs: number;
  endTs: number;
  setTarget: (t: FactorTarget) => void;   // 取代 setInstId：双写本地态 + store 指针（记忆上次选择）
  setBar: (v: string) => void;
  setRange: (startTs: number, endTs: number) => void;
  evalTarget: EvalTarget | null;
  setEvalTarget: (target: EvalTarget | null) => void;
  pendingMembers: FactorMember[];
  addPendingMember: (member: FactorMember) => void;
  clearPendingMembers: () => void;
}

const WorkbenchContext = createContext<Workbench | null>(null);

export function useWorkbench(): Workbench {
  const ctx = useContext(WorkbenchContext);
  if (!ctx) throw new Error("useWorkbench must be used within FactorLab");
  return ctx;
}

const BARS = ["1m", "5m", "15m", "30m", "1H", "4H", "1D"];

const SECTIONS = [
  { key: "catalog", label: "因子目录", desc: "库浏览与新建因子" },
  { key: "my-factors", label: "我的因子", desc: "查看 / 编辑 / 激活 / 删除" },
  { key: "evaluate", label: "因子评估", desc: "IC 曲线 + 分位收益" },
  { key: "mining", label: "因子挖掘", desc: "区间扫描按 IR 排名" },
  { key: "builder", label: "权重训练与组合", desc: "选成员 → 训练 → 保存" },
  { key: "models", label: "权重模型", desc: "模型管理 + 实时训练" },
  { key: "sets", label: "因子组合策略", desc: "查看 / 编辑已建组合" },
];

// FactorTarget → 持久化指针（仅存 single instId / group id；跨会话经 resolveFactorTarget 重解析为合法目标）。
function targetToRef(target: FactorTarget): FactorTargetRef {
  return target.kind === "group"
    ? { kind: "group", groupId: target.groupId }
    : { kind: "single", instId: target.instId };
}

// 层叠图标（管理标的组按钮），内联 SVG 避免引入图标库依赖。
function LayersIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M12 2 2 7l10 5 10-5-10-5Z" />
      <path d="m2 17 10 5 10-5" />
      <path d="m2 12 10 5 10-5" />
    </svg>
  );
}

function tsToDate(ts: number): string {
  return new Date(ts * 1000).toISOString().slice(0, 10);
}
function dateToTs(value: string, endOfDay = false): number {
  const [y, m, d] = value.split("-").map(Number);
  if (!y || !m || !d) return 0;
  return Math.floor(Date.UTC(y, m - 1, d, endOfDay ? 23 : 0, endOfDay ? 59 : 0, endOfDay ? 59 : 0) / 1000);
}

/**
 * 因子实验室面板（左导航 + 右内容；Esc 返回交易）。常驻挂载、由左侧 NavRail 的 activeView 切显隐，
 * 顶部工作台条统一标的 / 周期 / 区间，五个工作区共享（因子层 §3.2）。
 *
 * @param active 该视图当前是否可见（= activeView==="factor" && !showSettings）。焦点 / Esc 仅在
 *   active 为真时响应，避免隐藏态抢焦点。
 */
export default function FactorLab({ active }: { active: boolean }) {
  const setActiveView = useAppStore((s) => s.setActiveView);
  const section = useAppStore((s) => s.factorLabSection);
  const setSection = useAppStore((s) => s.setFactorLabSection);
  const headingRef = useRef<HTMLHeadingElement>(null);

  const storeInst = useAppStore((s) => s.instId);
  const storeBar = useAppStore((s) => s.bar);
  const storeStart = useAppStore((s) => s.backtestStartTs);
  const storeEnd = useAppStore((s) => s.backtestEndTs);
  const watchlist = useAppStore((s) => s.watchlist);
  const symbolGroups = useAppStore((s) => s.symbolGroups);
  const storeTargetRef = useAppStore((s) => s.factorLabTarget);
  const setStoreTargetRef = useAppStore((s) => s.setFactorLabTarget);

  // 目标初值：优先持久化指针（记忆上次选择），缺省 seed 主工作台 instId；经 resolveFactorTarget 夹取到合法目标
  // （组已删 / 成员全失效回落焦点单标的，保证选择器 value 恒命中一个 option）。
  const [target, setTargetState] = useState<FactorTarget>(() =>
    resolveFactorTarget(storeTargetRef ?? { kind: "single", instId: storeInst }, watchlist, symbolGroups),
  );
  const [bar, setBar] = useState<string>(storeBar);
  const [startTs, setStartTs] = useState(storeStart);
  const [endTs, setEndTs] = useState(storeEnd);
  const [evalTarget, setEvalTarget] = useState<EvalTarget | null>(null);
  const [pendingMembers, setPendingMembers] = useState<FactorMember[]>([]);
  const [groupModalOpen, setGroupModalOpen] = useState(false);

  // setTarget 双写：本地态 + store 指针（仅存指针 single instId / group id，跨会话经 resolveFactorTarget 重解析）。
  const setTarget = (next: FactorTarget) => {
    setTargetState(next);
    setStoreTargetRef(targetToRef(next));
  };

  const symbols = useMemo(() => factorTargetSymbols(target), [target]);
  const instId = useMemo(() => factorTargetFocus(target), [target]);

  const workbench = useMemo<Workbench>(
    () => ({
      target,
      symbols,
      instId,
      bar,
      startTs,
      endTs,
      setTarget,
      setBar,
      setRange: (s, e) => {
        setStartTs(s);
        setEndTs(e);
      },
      evalTarget,
      setEvalTarget,
      pendingMembers,
      addPendingMember: (member) =>
        setPendingMembers((prev) =>
          prev.some((m) => m.factorKey === member.factorKey && m.direction === member.direction)
            ? prev
            : [...prev, member],
        ),
      clearPendingMembers: () => setPendingMembers([]),
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [target, symbols, instId, bar, startTs, endTs, evalTarget, pendingMembers],
  );

  // 重协调：watchlist / 标的组变化（取消收藏、删组、编辑组成员、persist 补水）时重解析目标，保证选择器 value
  // 恒命中一个 option、group 成员快照随组编辑刷新。有意只跟踪 watchlist/symbolGroups 而非 storeInst，保持
  // lab 本地态不回写主工作台的解耦。
  useEffect(() => {
    setTargetState((cur) => resolveFactorTarget(targetToRef(cur), watchlist, symbolGroups));
  }, [watchlist, symbolGroups]);

  // 焦点：从「挂载即聚焦」改为「active 变真时聚焦标题」，避免隐藏态抢焦点（入口按钮已移除，无须回焦）。
  useEffect(() => {
    if (active) headingRef.current?.focus();
  }, [active]);

  // Esc 仅在 active（可见）时响应，返回交易工作台。
  useEffect(() => {
    if (!active) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setActiveView("trade");
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [active, setActiveView]);

  const rangeValid = startTs < endTs;

  // 目标选择器：value=`group:<id>` | `single:<instId>`；无自选且无标的组时禁用（回退空态）。
  const targetValue = target.kind === "group" ? `group:${target.groupId}` : `single:${target.instId}`;
  const hasOptions = watchlist.length > 0 || symbolGroups.length > 0;
  const onSelectTarget = (value: string) => {
    const ref: FactorTargetRef = value.startsWith("group:")
      ? { kind: "group", groupId: value.slice("group:".length) }
      : { kind: "single", instId: value.slice("single:".length) };
    setTarget(resolveFactorTarget(ref, watchlist, symbolGroups));
  };

  return (
    <WorkbenchContext.Provider value={workbench}>
      <div role="region" aria-label="因子实验室" className="flex h-full min-h-0 flex-col bg-[var(--bg)]">
        <header className="flex h-12 shrink-0 items-center justify-between border-b border-[var(--border)] px-6">
          <div className="flex items-baseline gap-2">
            <h1
              ref={headingRef}
              tabIndex={-1}
              className="text-sm font-semibold tracking-tight text-[var(--text)] outline-none"
            >
              因子实验室
            </h1>
            <span className="hidden text-[11px] text-[var(--muted)] sm:inline">
              行情 → 因子计算 → 评估/挖掘 → 权重训练 → 组合得分 → 策略决策
            </span>
          </div>
          <Button variant="ghost" size="sm" onClick={() => setActiveView("trade")} aria-label="返回工作台">
            返回工作台
          </Button>
        </header>

        <div className="flex shrink-0 flex-wrap items-end gap-3 border-b border-[var(--border)] px-6 py-2.5">
          <label className="flex flex-col gap-1">
            <span className="text-[11px] text-[var(--muted)]">标的</span>
            <div className="flex items-center gap-1.5">
              <Select
                value={targetValue}
                onChange={(e) => onSelectTarget(e.target.value)}
                disabled={!hasOptions}
                aria-label="标的"
                className="w-52 py-1 text-xs"
              >
                {!hasOptions ? (
                  <option value="">无自选标的</option>
                ) : (
                  <>
                    {symbolGroups.length > 0 && (
                      <optgroup label="标的组">
                        {symbolGroups.map((g) => (
                          <option key={g.id} value={`group:${g.id}`}>
                            {g.name}（{g.symbols.length}标的）
                          </option>
                        ))}
                      </optgroup>
                    )}
                    <optgroup label="单标的">
                      {watchlist.map((id) => (
                        <option key={id} value={`single:${id}`}>{id}</option>
                      ))}
                    </optgroup>
                  </>
                )}
              </Select>
              {target.kind === "group" && <Badge tone="accent">{symbols.length} 标的</Badge>}
            </div>
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[11px] text-[var(--muted)]">周期</span>
            <Select value={bar} onChange={(e) => setBar(e.target.value)} className="py-1 text-xs">
              {BARS.map((b) => (
                <option key={b} value={b}>{b}</option>
              ))}
            </Select>
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[11px] text-[var(--muted)]">起始</span>
            <input
              type="date"
              value={tsToDate(startTs)}
              onChange={(e) => setStartTs(dateToTs(e.target.value))}
              className="rounded-[8px] border border-[var(--border)] bg-[var(--surface)] px-2 py-1 text-xs tabular"
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[11px] text-[var(--muted)]">结束</span>
            <input
              type="date"
              value={tsToDate(endTs)}
              onChange={(e) => setEndTs(dateToTs(e.target.value, true))}
              className="rounded-[8px] border border-[var(--border)] bg-[var(--surface)] px-2 py-1 text-xs tabular"
            />
          </label>
          {watchlist.length === 0 && (
            <span className="pb-1 text-[11px] text-[var(--muted)]">请先在交易工作台添加自选标的</span>
          )}
          {!rangeValid && (
            <span className="pb-1 text-[11px] text-[var(--short-ink)]">起始须早于结束</span>
          )}
          {/* 横栏最右侧：管理标的组（自选为空时禁用并提示，spec §六1）。 */}
          <div className="ml-auto pb-0.5">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setGroupModalOpen(true)}
              disabled={watchlist.length === 0}
              aria-haspopup="dialog"
              title={watchlist.length === 0 ? "请先在交易工作台添加自选标的" : "管理标的组"}
            >
              <LayersIcon />
              管理标的组
            </Button>
          </div>
        </div>

        <SymbolGroupModal open={groupModalOpen} onClose={() => setGroupModalOpen(false)} />

        <div className="flex min-h-0 flex-1 gap-6 px-6 py-5">
          <nav className="flex w-[15%] min-w-[176px] shrink-0 flex-col gap-1" aria-label="因子工作区">
            {SECTIONS.map((s) => (
              <button
                key={s.key}
                type="button"
                onClick={() => setSection(s.key)}
                aria-current={section === s.key ? "page" : undefined}
                className={clsx(
                  "flex flex-col gap-0.5 rounded-[10px] px-3 py-2 text-left transition-colors duration-150 ease-smooth",
                  section === s.key
                    ? "bg-[var(--accent-soft)] text-[var(--accent-ink)]"
                    : "text-[var(--muted)] hover:bg-[var(--surface-2)] hover:text-[var(--text)]",
                )}
              >
                <span className="text-sm font-medium">{s.label}</span>
                <span className="text-[11px] opacity-80">{s.desc}</span>
              </button>
            ))}
          </nav>
          <section className="min-w-0 flex-1 overflow-y-auto pr-1">
            {section === "my-factors" ? (
              <FactorTable />
            ) : section === "evaluate" ? (
              <FactorEvalPanel />
            ) : section === "mining" ? (
              <FactorMiningPanel />
            ) : section === "builder" ? (
              <FactorSetBuilder />
            ) : section === "models" ? (
              <WeightModelPanel />
            ) : section === "sets" ? (
              <FactorSetList />
            ) : (
              <FactorCatalog />
            )}
          </section>
        </div>
      </div>
    </WorkbenchContext.Provider>
  );
}
