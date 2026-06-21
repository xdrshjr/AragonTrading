"use client";

// 右侧多页签容器：AI 助手 / 交易策略（承载于 page.tsx 右侧 aside）(Agent Trading Copilot §3.2)。
// 两面板始终挂载（用 hidden 切换），切换页签不破坏各自状态（§6.2#1）。
import { Tabs } from "@/components/ui";
import AgentPanel from "@/components/Agent/AgentPanel";
import StrategyPanel from "@/components/Strategy/StrategyPanel";
import { useAppStore, type RightTab } from "@/lib/store";

const RIGHT_TABS = [
  { key: "agent", label: "AI 助手" },
  { key: "strategy", label: "交易策略" },
];

export default function RightPanel() {
  const rightTab = useAppStore((s) => s.rightTab);
  const setRightTab = useAppStore((s) => s.setRightTab);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="px-3 pt-3">
        <Tabs tabs={RIGHT_TABS} active={rightTab} onChange={(k) => setRightTab(k as RightTab)} />
      </div>
      <div className="mt-2 min-h-0 flex-1" hidden={rightTab !== "agent"}>
        <AgentPanel />
      </div>
      <div className="min-h-0 flex-1" hidden={rightTab !== "strategy"}>
        <StrategyPanel />
      </div>
    </div>
  );
}
