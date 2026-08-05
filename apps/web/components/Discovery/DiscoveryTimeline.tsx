"use client";

import clsx from "clsx";
import { useEffect, useRef, useState } from "react";
import { Badge } from "@/components/ui";
import { keptRate } from "@/lib/discovery";
import type { DiscoveryIterationRow } from "@/lib/discoveryTypes";

const IDLE_RESTICK_MS = 120_000; // 2 分钟空闲后自动恢复贴底（复用 AI-trade 轨迹面板的成熟交互）

function formatClock(ts: number): string {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleTimeString("zh-CN", { hour12: false });
}

/**
 * 迭代时间线：贴底自动跟随 + 用户上滚后暂停跟随 + 2 分钟空闲自动恢复贴底。
 *
 * 长跑友好：用户往上翻看历史时绝不把他拽回底部；但离开两分钟回来，又应当看到最新的一轮。
 */
export default function DiscoveryTimeline({ rows }: { rows: DiscoveryIterationRow[] }) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [stick, setStick] = useState(true);
  const idleTimer = useRef<number | null>(null);

  useEffect(() => {
    if (!stick) return;
    const node = scrollRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [rows, stick]);

  const onScroll = () => {
    const node = scrollRef.current;
    if (!node) return;
    const atBottom = node.scrollHeight - node.scrollTop - node.clientHeight < 24;
    setStick(atBottom);
    if (idleTimer.current) window.clearTimeout(idleTimer.current);
    if (!atBottom) {
      idleTimer.current = window.setTimeout(() => setStick(true), IDLE_RESTICK_MS);
    }
  };

  useEffect(() => () => {
    if (idleTimer.current) window.clearTimeout(idleTimer.current);
  }, []);

  if (rows.length === 0) {
    return <p className="text-xs text-[var(--muted)]">还没有迭代记录。</p>;
  }

  return (
    <div className="flex flex-col gap-1.5">
      <div
        ref={scrollRef}
        onScroll={onScroll}
        className="max-h-72 overflow-y-auto rounded-[10px] border border-[var(--border)] bg-[var(--surface)]/40"
      >
        <ul className="divide-y divide-[var(--border)]">
          {rows.map((row) => {
            const rate = keptRate(row.validated, row.kept, 1);
            return (
              <li key={row.index} className="px-3 py-2">
                <div className="flex items-center gap-2">
                  <span className="text-xs font-medium tabular text-[var(--text)]">
                    #{row.index}
                  </span>
                  <span className="text-[11px] tabular text-[var(--muted)]">
                    {formatClock(row.finishedTs)}
                  </span>
                  <span className="text-[11px] tabular text-[var(--muted)]">
                    {(row.durationMs / 1000).toFixed(1)}s
                  </span>
                  {row.kept > 0 && <Badge tone="long">入库 {row.kept}</Badge>}
                  {row.overrun && <Badge tone="short">超时长</Badge>}
                  {row.truncatedByTimeout > 0 && (
                    <Badge tone="short">丢弃 {row.truncatedByTimeout}</Badge>
                  )}
                  {row.llm.degraded && (
                    <Badge tone="neutral">LLM 降级：{row.llm.degradedReason || "未知"}</Badge>
                  )}
                </div>
                <p className="mt-0.5 text-[11px] leading-relaxed text-[var(--muted)]">
                  生成 {row.generated} · 规则拒 {row.ruleRejected} · 验证 {row.validated} ·
                  入库率 {rate === null ? "—" : `${(rate * 100).toFixed(1)}%`}
                  {row.note ? ` · ${row.note}` : ""}
                </p>
              </li>
            );
          })}
        </ul>
      </div>
      <div className="flex items-center justify-between text-[10px] text-[var(--muted)]">
        <span>共 {rows.length} 轮（内存保留最近 200 轮，完整历史见任务详情）</span>
        {!stick && (
          <button
            type="button"
            onClick={() => setStick(true)}
            className={clsx("underline transition-colors hover:text-[var(--text)]")}
          >
            回到最新
          </button>
        )}
      </div>
    </div>
  );
}
