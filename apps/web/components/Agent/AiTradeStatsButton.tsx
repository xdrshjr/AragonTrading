"use client";

import { useState } from "react";
import { Button } from "@/components/ui";
import AiTradeStatsPanel from "./AiTradeStatsPanel";

// AI 自动交易统计入口按钮（ai-trade-stats-dashboard §3.9）：自封装（自持 open state + 自挂看板 Modal，
// 仿 AiTradeToggle，避免污染 AgentPanel 状态）。挂在 AgentPanel 头部 AiTradeToggle 左侧。
export default function AiTradeStatsButton() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button
        variant="ghost"
        size="sm"
        onClick={() => setOpen(true)}
        aria-label="AI 交易统计"
        title="查看 AI 自动交易的统计与历史回溯"
      >
        <BarsIcon />
        统计
      </Button>
      <AiTradeStatsPanel open={open} onClose={() => setOpen(false)} />
    </>
  );
}

// 迷你条形图 icon（复用统计面板触发按钮的 SVG 风格；currentColor 随 Button 文字色）。
function BarsIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden="true">
      <rect x="1" y="6" width="2.4" height="5" rx="0.6" fill="currentColor" />
      <rect x="4.8" y="3" width="2.4" height="8" rx="0.6" fill="currentColor" />
      <rect x="8.6" y="1" width="2.4" height="10" rx="0.6" fill="currentColor" />
    </svg>
  );
}
