"use client";

import { useState } from "react";
import { Modal, Tabs } from "@/components/ui";
import AiTradeStatsDetail from "./aiTradeStats/AiTradeStatsDetail";
import AiTradeStatsHistoryList from "./aiTradeStats/AiTradeStatsHistoryList";
import AiTradeStatsLive from "./aiTradeStats/AiTradeStatsLive";

const TITLE_ID = "ai-trade-stats-title";
const TABS = [
  { key: "live", label: "当前" },
  { key: "history", label: "历史" },
];

// AI 自动交易统计看板（ai-trade-stats-dashboard §3.10）：Modal 壳 + 数据驱动 Tabs[当前｜历史] + 历史内下钻。
// 「当前」读瞬态 store.aiTradeGroup；「历史」拉持久化端点，点卡进单会话详情、返回回列表。面板本地持 active
// 与 selectedGroupId（瞬态、不持久化），按 active 条件渲染两页内容（P2-6：Tabs 非 children）。
export default function AiTradeStatsPanel({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [active, setActive] = useState("live");
  const [selectedGroupId, setSelectedGroupId] = useState<string | null>(null);

  return (
    <Modal open={open} onClose={onClose} labelledBy={TITLE_ID} className="max-w-3xl">
      <div className="flex items-center justify-between border-b border-[var(--border)] px-5 py-3.5">
        <h2 id={TITLE_ID} className="text-sm font-semibold text-[var(--text)]">
          AI 自动交易统计
        </h2>
        <button
          type="button"
          onClick={onClose}
          aria-label="关闭"
          className="rounded p-1 text-[var(--muted)] transition-colors hover:bg-[var(--surface-2)] hover:text-[var(--text)]"
        >
          ✕
        </button>
      </div>

      <div className="px-5 pt-3">
        <Tabs tabs={TABS} active={active} onChange={setActive} variant="underline" />
      </div>

      <div className="flex max-h-[72vh] flex-col overflow-auto px-5 py-4">
        {active === "live" ? (
          <AiTradeStatsLive />
        ) : selectedGroupId ? (
          <AiTradeStatsDetail groupId={selectedGroupId} onBack={() => setSelectedGroupId(null)} />
        ) : (
          <AiTradeStatsHistoryList onSelect={setSelectedGroupId} />
        )}
      </div>
    </Modal>
  );
}
