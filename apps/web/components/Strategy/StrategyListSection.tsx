"use client";

import { isCombo } from "@/lib/combo";
import { buildEditTargetFromStrategy } from "@/lib/factor";
import { STRATEGY_LIST_TABS, isFactorStrategy } from "@/lib/strategyCatalog";
import { useAppStore } from "@/lib/store";
import type { Mode, Strategy, StrategyListTab } from "@/lib/types";
import { Button, Tabs } from "@/components/ui";
import StrategyList from "./StrategyList";

/**
 * 交易策略列表区（页签 + 空态 + 列表 + 卡片编辑/删除薄分发），自 StrategyPanel 抽出以净降其行数并守
 * 800 行硬上限（R-P1-1）。外部依赖以单一 props 配置对象传入（满足「形参 ≤5」约定）。
 *
 * 卡片编辑/删除按 kind 分流（与编辑对称，R-P2-1）：组合走 useComboPanel 单一真源（openEditCombo /
 * openDeleteCombo），因子交接因子实验室，原子选中并在下方详情区改参；非组合删除走 useStrategyDelete。
 */
export default function StrategyListSection({
  mode,
  strategyListTab,
  setStrategyListTab,
  listStrategies,
  strategyId,
  returnsById,
  onSelect,
  multiSelectIds,
  multiSelectDisabledIds,
  onToggleMultiSelect,
  openNewCombo,
  openEditCombo,
  openDeleteCombo,
  requestDelete,
}: {
  mode: Mode;
  strategyListTab: StrategyListTab;
  setStrategyListTab: (tab: StrategyListTab) => void;
  listStrategies: Strategy[];
  strategyId: number | null;
  returnsById: Record<number, number | undefined>;
  onSelect: (id: number) => void;
  multiSelectIds?: number[];
  multiSelectDisabledIds?: number[];
  onToggleMultiSelect?: (id: number) => void;
  openNewCombo: () => void;
  openEditCombo: (combo: Strategy) => void;
  openDeleteCombo: (combo: Strategy) => void;
  requestDelete: (s: Strategy) => void;
}) {
  const setActiveView = useAppStore((s) => s.setActiveView);
  const setFactorLabSection = useAppStore((s) => s.setFactorLabSection);
  const setFactorEditTarget = useAppStore((s) => s.setFactorEditTarget);

  // 编辑分发：组合→编辑器；因子→因子实验室（同 FactorStrategyBanner 三连）；原子→选中改参。
  const editStrategy = (s: Strategy) => {
    if (isCombo(s)) {
      openEditCombo(s);
      return;
    }
    if (isFactorStrategy(s)) {
      setFactorEditTarget(buildEditTargetFromStrategy(s));
      setFactorLabSection("builder");
      setActiveView("factor");
      return;
    }
    onSelect(s.id);
  };

  // 删除分发：组合回 useComboPanel（单一真源，保留其编辑器/详情态收敛）；原子 / 因子走 useStrategyDelete。
  const requestStrategyDelete = (s: Strategy) => {
    if (isCombo(s)) {
      openDeleteCombo(s);
      return;
    }
    requestDelete(s);
  };

  const isListMode = mode === "backtest" || mode === "paper";
  const showEmptyComboTab = isListMode && strategyListTab === "combo" && listStrategies.length === 0;
  const showEmptyFactorTab = isListMode && strategyListTab === "factor" && listStrategies.length === 0;

  const goFactorLab = () => {
    setFactorLabSection("builder");
    setActiveView("factor");
  };

  return (
    <>
      {isListMode && (
        <div className="mb-3">
          <Tabs
            tabs={STRATEGY_LIST_TABS}
            active={strategyListTab}
            onChange={(key) => setStrategyListTab(key as StrategyListTab)}
            variant="underline"
          />
        </div>
      )}

      {showEmptyComboTab ? (
        <div className="flex flex-col gap-2 rounded-[8px] border border-[var(--border)] bg-[var(--surface)] px-3 py-4 text-xs text-[var(--muted)]">
          <span>暂无组合策略</span>
          <div>
            <Button size="sm" variant="default" onClick={openNewCombo}>
              新建组合
            </Button>
          </div>
        </div>
      ) : showEmptyFactorTab ? (
        <div className="flex flex-col gap-2 rounded-[8px] border border-[var(--border)] bg-[var(--surface)] px-3 py-4 text-xs text-[var(--muted)]">
          <span>暂无因子策略，去因子实验室创建因子组合（保存后自动出现在此）。</span>
          <div>
            <Button size="sm" variant="default" onClick={goFactorLab}>
              去因子实验室
            </Button>
          </div>
        </div>
      ) : (
        <StrategyList
          strategies={listStrategies}
          activeId={strategyId}
          returnsById={returnsById}
          onSelect={onSelect}
          multiSelectIds={multiSelectIds}
          multiSelectDisabledIds={multiSelectDisabledIds}
          onToggleMultiSelect={onToggleMultiSelect}
          onEditStrategy={editStrategy}
          onDeleteStrategy={requestStrategyDelete}
        />
      )}
    </>
  );
}
