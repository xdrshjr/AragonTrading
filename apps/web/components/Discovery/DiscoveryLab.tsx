"use client";

import clsx from "clsx";
import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { discoveryApi } from "@/lib/discoveryApi";
import type { DiscoveryDefaults, DiscoveryRunSummary } from "@/lib/discoveryTypes";
import { useDiscoveryRunStream } from "@/lib/useDiscoveryRunStream";
import { useAppStore } from "@/lib/store";
import DiscoveryAssets from "./DiscoveryAssets";
import DiscoveryCandidates from "./DiscoveryCandidateTable";
import DiscoveryLibrary from "./DiscoveryLibrary";
import DiscoveryMonitor from "./DiscoveryMonitor";
import DiscoveryOverview from "./DiscoveryOverview";
import DiscoveryRunList from "./DiscoveryRunList";

/**
 * 策略与因子挖掘工作区（strategy-factor-discovery §2.13）。
 *
 * 外壳镜像 `FactorLab`：左侧分区导航 + 右侧内容区，常驻挂载、由 NavRail 的 `activeView` 切显隐。
 * 全局只保留**一份**任务列表与默认配置（`DiscoveryContext`），五个分区共享，避免各自重复拉取。
 *
 * @param active 该视图当前是否可见（= activeView==="discovery" && !showSettings）。
 *   仅在可见时才拉数据与建 WS——未打开过的用户对既有请求路径零额外开销（DoD #13）。
 */
export interface DiscoveryContextValue {
  runs: DiscoveryRunSummary[];
  defaults: DiscoveryDefaults | null;
  loading: boolean;
  error: string;
  activeRunId: number | null;
  selectRun: (runId: number | null) => void;
  refresh: () => Promise<void>;
  goSection: (section: string) => void;
}

const DiscoveryContext = createContext<DiscoveryContextValue | null>(null);

export function useDiscovery(): DiscoveryContextValue {
  const ctx = useContext(DiscoveryContext);
  if (!ctx) throw new Error("useDiscovery must be used within DiscoveryLab");
  return ctx;
}

const SECTIONS = [
  { key: "overview", label: "总览", desc: "运行状态与近况" },
  { key: "runs", label: "挖掘任务", desc: "新建 / 暂停 / 恢复 / 停止" },
  { key: "monitor", label: "实时监控", desc: "漏斗 / 来源 / 迭代时间线" },
  { key: "candidates", label: "候选库", desc: "筛选 / 详情 / 入库" },
  { key: "library", label: "成果库", desc: "已入库因子与溯源" },
  // 第 6 分区（discovery-asset-ledger §2.10）：「成果库」是**本轮**视图，台账是**全局**视图，
  // 用户在长跑中最常问的两个问题分别对应这两个，故并列而不是改造既有分区。
  { key: "assets", label: "成果资产", desc: "跨任务台账与持续体检" },
];

export default function DiscoveryLab({ active }: { active: boolean }) {
  const section = useAppStore((s) => s.discoverySection);
  const setSection = useAppStore((s) => s.setDiscoverySection);
  const activeRunId = useAppStore((s) => s.discoveryRunId);
  const setActiveRunId = useAppStore((s) => s.setDiscoveryRunId);
  const setActiveView = useAppStore((s) => s.setActiveView);
  const headingRef = useRef<HTMLHeadingElement>(null);

  const [runs, setRuns] = useState<DiscoveryRunSummary[]>([]);
  const [defaults, setDefaults] = useState<DiscoveryDefaults | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [loadedOnce, setLoadedOnce] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [list, config] = await Promise.all([
        discoveryApi.listRuns({ limit: 50 }),
        discoveryApi.defaults(),
      ]);
      setRuns(list.items);
      setDefaults(config);
      setError("");
      // 未选中任务时自动选中最近一条在跑的（长跑特性下这几乎总是用户想看的那个）。
      const live = list.items.find((row) => row.status === "running") ?? list.items[0];
      if (live) {
        const current = useAppStore.getState().discoveryRunId;
        if (!current || !list.items.some((row) => row.runId === current)) {
          setActiveRunId(live.runId);
        }
      } else {
        setActiveRunId(null);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载挖掘任务失败");
    } finally {
      setLoading(false);
      setLoadedOnce(true);
    }
  }, [setActiveRunId]);

  // 惰性首拉：只有真的打开过本视图才发请求（DoD #13）。
  useEffect(() => {
    if (active && !loadedOnce) void refresh();
  }, [active, loadedOnce, refresh]);

  // 可见时低频对齐一次任务列表（WS 负责实时细节，这里只兜底状态漂移）。
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => void refresh(), 30_000);
    return () => window.clearInterval(timer);
  }, [active, refresh]);

  useDiscoveryRunStream(active ? activeRunId : null);

  // Esc 返回交易工作台（与 FactorLab 同款交互）；仅在可见时响应，避免隐藏态抢按键。
  useEffect(() => {
    if (!active) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setActiveView("trade");
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [active, setActiveView]);

  const value: DiscoveryContextValue = {
    runs,
    defaults,
    loading,
    error,
    activeRunId,
    selectRun: setActiveRunId,
    refresh,
    goSection: setSection,
  };

  return (
    <DiscoveryContext.Provider value={value}>
      <div className="flex h-full min-h-0">
        <nav
          aria-label="挖掘工作区导航"
          className="flex w-44 shrink-0 flex-col gap-0.5 border-r border-[var(--border)] bg-[var(--surface)]/40 p-2"
        >
          <h2 ref={headingRef} tabIndex={-1} className="px-2 py-1.5 text-xs font-semibold text-[var(--muted)]">
            策略与因子挖掘
          </h2>
          {SECTIONS.map((item) => {
            const isActive = section === item.key;
            return (
              <button
                key={item.key}
                type="button"
                onClick={() => setSection(item.key)}
                aria-current={isActive ? "page" : undefined}
                className={clsx(
                  "rounded-[10px] px-2.5 py-2 text-left transition-colors duration-150 ease-smooth",
                  isActive
                    ? "bg-[var(--accent-soft)] text-[var(--accent)]"
                    : "text-[var(--muted)] hover:bg-[var(--surface-2)] hover:text-[var(--text)]",
                )}
              >
                <span className="block text-sm font-medium">{item.label}</span>
                <span className="block text-[11px] leading-tight opacity-70">{item.desc}</span>
              </button>
            );
          })}
          <p className="mt-auto px-2 pt-3 text-[10px] leading-relaxed text-[var(--muted)]">
            所有指标均为历史样本统计，不构成任何收益预期或投资建议。
          </p>
        </nav>

        <main className="min-h-0 flex-1 overflow-y-auto p-4">
          {error && (
            <div
              role="alert"
              className="mb-3 rounded-[10px] border border-[var(--short)] bg-[color-mix(in_srgb,var(--short)_10%,transparent)] px-3 py-2 text-xs text-[var(--short)]"
            >
              {error}
              <button type="button" onClick={() => void refresh()} className="ml-2 underline">
                重试
              </button>
            </div>
          )}
          {section === "overview" && <DiscoveryOverview />}
          {section === "runs" && <DiscoveryRunList />}
          {section === "monitor" && <DiscoveryMonitor />}
          {section === "candidates" && <DiscoveryCandidates />}
          {section === "library" && <DiscoveryLibrary />}
          {section === "assets" && <DiscoveryAssets />}
        </main>
      </div>
    </DiscoveryContext.Provider>
  );
}
