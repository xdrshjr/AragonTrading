"use client";

import { useState } from "react";
import { Button } from "@/components/ui";

/**
 * 上下文分区渲染（ai-trade-audit-journal §2.6.2 第 3 段）。
 *
 * `payload` 按语义分区折叠展示，底部「查看原始 JSON」展开等宽代码块（`overflow-x-auto`）。
 * `minimal` 档时整段替换为一句说明 + 如何开启的指引——一个空白的「上下文」段会被读成
 * 「模型什么都没看到」，而事实是「我们没留」。
 *
 * 分区名取自 `payload` 自己的键：**不在前端另立一张分区表**，否则后端加一个新的 payload 段
 * 时这里会静默地把它藏起来。已知键给中文标题，未知键原样显示（比藏起来诚实）。
 */
const SECTION_TITLES: Record<string, string> = {
  marketContext: "行情",
  marketDigest: "行情摘要",
  signalBoard: "信号盘",
  multiTimeframeContext: "多周期",
  tacticalContext: "战术窗口",
  intel: "市场情报",
  prior: "信号先验",
  position: "持仓",
  wake: "唤醒",
  discipline: "交易纪律",
  consensus: "同向共识",
  profitGuard: "利润保护",
  baseRules: "基础策略",
  trendVerdict: "趋势基调",
  cost: "成本与 edge",
  experience: "交易经验",
  horizon: "持仓周期",
  contextDisclosure: "上下文披露",
};

export default function JournalContextSections({
  payload,
  truncated,
  captureTier,
}: {
  payload: Record<string, unknown> | null;
  truncated: boolean;
  captureTier: string;
}) {
  const [rawOpen, setRawOpen] = useState(false);
  const [open, setOpen] = useState<Record<string, boolean>>({});

  if (payload === null) {
    return (
      <div className="space-y-1 px-1 py-2 text-xs text-[var(--muted)]">
        <p>
          该轮以「{captureTier}」档采集，未留存喂给模型的上下文，故这里没有内容可显示。
        </p>
        <p>
          需要它的话，把设置项 AI_TRADE_JOURNAL_CAPTURE_TIER 调到 standard 或 full 后重启后端；
          改档只影响此后的轮次，已经落下的行不会被追溯补齐。
        </p>
      </div>
    );
  }

  const entries = Object.entries(payload);
  return (
    <div className="space-y-1.5">
      {truncated && (
        <p className="rounded-[8px] bg-[color-mix(in_srgb,var(--accent)_12%,transparent)] px-2 py-1.5 text-[11px] text-[var(--accent-ink)]">
          这一轮的上下文超过了单行上限、已被截断保存。它不会被用于后训练导出——
          截断的上下文重建不出当时的提示词原文。
        </p>
      )}
      {entries.map(([key, value]) => {
        const expanded = open[key] ?? false;
        return (
          <section key={key} className="rounded-[10px] border border-[var(--border)]">
            <button
              type="button"
              aria-expanded={expanded}
              onClick={() => setOpen((prev) => ({ ...prev, [key]: !expanded }))}
              className="flex w-full items-center justify-between px-2 py-1.5 text-left text-xs font-medium"
            >
              <span>{SECTION_TITLES[key] ?? key}</span>
              <span aria-hidden="true" className="text-[var(--muted)]">
                {expanded ? "−" : "+"}
              </span>
            </button>
            {expanded && (
              <div className="min-w-0 overflow-x-auto border-t border-[var(--border)] p-2">
                <pre className="whitespace-pre text-[11px] leading-4">
                  {typeof value === "object" ? JSON.stringify(value, null, 2) : String(value)}
                </pre>
              </div>
            )}
          </section>
        );
      })}
      <div>
        <Button size="sm" variant="ghost" onClick={() => setRawOpen((v) => !v)}>
          {rawOpen ? "收起原始 JSON" : "查看原始 JSON"}
        </Button>
      </div>
      {rawOpen && (
        <div className="min-w-0 overflow-x-auto rounded-[10px] border border-[var(--border)] p-2">
          <pre className="whitespace-pre text-[11px] leading-4">
            {JSON.stringify(payload, null, 2)}
          </pre>
        </div>
      )}
    </div>
  );
}
