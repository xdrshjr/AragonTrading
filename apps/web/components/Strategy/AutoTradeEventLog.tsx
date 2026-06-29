"use client";

import clsx from "clsx";
import type { LiveAutoEvent } from "@/lib/liveAutoTypes";
import { useAppStore } from "@/lib/store";

const VISIBLE_MAX = 6;

/**
 * 自动交易事件流（live-auto-trading-hardening H6/H7）：紧凑活动流承载下单 / 平仓失败的 error、
 * 连接降级、反手跳过、OCO 已平等关键诊断，使「bot 此刻在做什么、哪笔单失败」对用户透明。
 *
 * R-1：本仓无任何全局 toast / 通知原语，本组件**不依赖也不新建** —— 错误级条目置顶 + 红色高亮，
 * 警告级（降级）clay 色、信息级灰色，与 H7 终止横幅双重保证关键 error 可见。仅有事件时渲染。
 */
export default function AutoTradeEventLog() {
  const events = useAppStore((s) => s.liveAutoEvents);
  if (events.length === 0) return null;

  const ordered = orderEvents(events).slice(0, VISIBLE_MAX);
  return (
    <div className="flex flex-col gap-px border-b border-[var(--border)] bg-[var(--bg)] px-4 py-1.5">
      <span className="text-[10px] font-medium uppercase tracking-wide text-[var(--muted)]">
        自动交易事件
      </span>
      <ul className="flex flex-col gap-0.5" aria-label="自动交易事件流">
        {ordered.map((event, i) => (
          <EventRow key={`${event.ts}-${i}-${event.message}`} event={event} />
        ))}
      </ul>
    </div>
  );
}

// 置顶错误：错误级在前（按时间倒序），其余按时间倒序；调用方再裁到可见上限。
function orderEvents(events: LiveAutoEvent[]): LiveAutoEvent[] {
  const errors = events.filter((e) => e.level === "error").reverse();
  const rest = events.filter((e) => e.level !== "error").reverse();
  return [...errors, ...rest];
}

function EventRow({ event }: { event: LiveAutoEvent }) {
  return (
    <li
      role={event.level === "error" ? "alert" : undefined}
      className={clsx(
        "flex items-start gap-1.5 rounded px-1.5 py-0.5 text-[11px] leading-snug",
        event.level === "error" && "bg-[var(--short)]/12 font-medium",
      )}
    >
      <span className={clsx("mt-1 h-1 w-1 shrink-0 rounded-full", dotTone(event.level))} />
      <span className={clsx("min-w-0", textTone(event.level))}>{event.message}</span>
    </li>
  );
}

function textTone(level: LiveAutoEvent["level"]): string {
  if (level === "error") return "text-[var(--short)]";
  if (level === "warn") return "text-[var(--accent)]";
  return "text-[var(--muted)]";
}

function dotTone(level: LiveAutoEvent["level"]): string {
  if (level === "error") return "bg-[var(--short)]";
  if (level === "warn") return "bg-[var(--accent)]";
  return "bg-[var(--muted)]";
}
