"use client";

import clsx from "clsx";
import { createContext, useContext, useEffect, useMemo, useRef, useState } from "react";
import { Button, Select } from "@/components/ui";
import { resolveWatchlistInstId } from "@/lib/factor";
import { useAppStore } from "@/lib/store";
import FactorCatalog from "./FactorCatalog";
import FactorEvalPanel from "./FactorEvalPanel";
import FactorMiningPanel from "./FactorMiningPanel";
import FactorSetBuilder from "./FactorSetBuilder";
import FactorSetList from "./FactorSetList";
import FactorTable from "./FactorTable";
import WeightModelPanel from "./WeightModelPanel";
import type { FactorDirection, FactorMember } from "@/lib/factorTypes";

// 跨工作区交接：评估目标因子（表/目录 → 评估）、待加入组合成员（评估/挖掘 → 组合）。
export interface EvalTarget {
  factorKey: string;
  params: Record<string, unknown>;
  direction: FactorDirection;
}

// 因子实验室工作台上下文：标的 / 周期 / 评估区间（lab 内本地态，不扰动主工作台选择）。
export interface Workbench {
  instId: string;
  bar: string;
  startTs: number;
  endTs: number;
  setInstId: (v: string) => void;
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

  // 标的初值经纯函数协调到自选下拉的合法值（命中自选则用之，否则回落首项 / 空列表回落 ""）。
  const [instId, setInstId] = useState(() => resolveWatchlistInstId(storeInst, watchlist));
  const [bar, setBar] = useState<string>(storeBar);
  const [startTs, setStartTs] = useState(storeStart);
  const [endTs, setEndTs] = useState(storeEnd);
  const [evalTarget, setEvalTarget] = useState<EvalTarget | null>(null);
  const [pendingMembers, setPendingMembers] = useState<FactorMember[]>([]);

  const workbench = useMemo<Workbench>(
    () => ({
      instId,
      bar,
      startTs,
      endTs,
      setInstId,
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
    [instId, bar, startTs, endTs, evalTarget, pendingMembers],
  );

  // 重协调：watchlist 变化（如在别处取消收藏当前标的、或 persist 补水）致 instId 掉出列表时拉回合法值，
  // 保证原生 select 的 value 恒命中一个 option。有意只跟踪 watchlist 而非 storeInst，保持 lab 本地态不回写主工作台的解耦。
  useEffect(() => {
    setInstId((cur) => resolveWatchlistInstId(cur, watchlist));
  }, [watchlist]);

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
            <Select
              value={instId}
              onChange={(e) => setInstId(e.target.value)}
              disabled={watchlist.length === 0}
              className="w-44 py-1 text-xs"
            >
              {watchlist.length === 0 ? (
                <option value="">无自选标的</option>
              ) : (
                watchlist.map((id) => (
                  <option key={id} value={id}>{id}</option>
                ))
              )}
            </Select>
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
            <span className="pb-1 text-[11px] text-[var(--short)]">起始须早于结束</span>
          )}
        </div>

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
                    ? "bg-[var(--accent-soft)] text-[var(--accent)]"
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
