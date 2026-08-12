"use client";

// 顶栏 OKX 链路状态：一枚芯片 + 一屏可展开的连接详情（okx-link-resilience §2.5.3）。
//
// 它取代了旧的 `ConnDot`——那个把三条链路压成一个「已连接 / 不可达」、且只有 hover 才看得到
// 时延的小圆点。用户在断线时最想知道的三件事（**断的是哪一路、断了多久、系统正在做什么**），
// 旧展示一件也答不了，于是「K 线不动了」这类沉默故障只能靠人自己发现。
//
// 展示字符串全部来自 `lib/okxLink.ts`（唯一口径），本组件不另抄一份中文。

import { useEffect, useId, useRef, useState } from "react";

import { Badge } from "@/components/ui";
import {
  accountLine,
  activitySummary,
  badgeTone,
  channelRows,
  chipView,
  clockLine,
  lastError,
} from "@/lib/okxLink";
import type { OkxConnectivity } from "@/lib/okxLinkTypes";
import { fmtTime } from "@/lib/format";

export interface ConnectionStatusProps {
  /** 端点快照；`null`（老后端 / 尚未取到）时回落到 `okxUp` 的二元展示，绝不白屏。 */
  connectivity?: OkxConnectivity | null;
  okxUp: boolean | null;
  latencyMs?: number | null;
  lastOkTs?: number | null;
  /** 面板开合回调：页面据它把 `/api/okx/connectivity` 的轮询从 30s 提到 5s。 */
  onOpenChange?: (open: boolean) => void;
  /** 「重新探测」：强制一次公共探活 + 一次时钟同步（只读，不触碰任何私有写路径）。 */
  onProbe?: () => Promise<unknown> | void;
}

export default function ConnectionStatus({
  connectivity,
  okxUp,
  latencyMs,
  lastOkTs,
  onOpenChange,
  onProbe,
}: ConnectionStatusProps) {
  const [open, setOpen] = useState(false);
  const [probing, setProbing] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelId = useId();

  const chip = chipView(connectivity, okxUp);
  const rows = channelRows(connectivity);
  const error = lastError(connectivity);
  const account = accountLine(connectivity);
  const clock = clockLine(connectivity);

  function toggle(next: boolean) {
    setOpen(next);
    onOpenChange?.(next);
  }

  // Esc 与点击外部关闭，并把焦点**还给触发按钮**（键盘用户不该在关闭后丢失位置）。
  useEffect(() => {
    if (!open) return;
    function onKey(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      toggle(false);
      triggerRef.current?.focus();
    }
    function onPointer(event: MouseEvent) {
      if (wrapRef.current && !wrapRef.current.contains(event.target as Node)) toggle(false);
    }
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onPointer);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onPointer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  async function probe() {
    if (!onProbe || probing) return;
    setProbing(true);
    try {
      await onProbe();
    } finally {
      setProbing(false);
    }
  }

  const title = connectivity
    ? connectivity.summary
    : okxUp
      ? `OKX 已连接${latencyMs != null ? ` · 时延 ${latencyMs}ms` : ""}${
          lastOkTs ? ` · 最近取数 ${fmtTime(lastOkTs)}` : ""
        }`
      : okxUp === null
        ? "正在探测 OKX 可达性…"
        : "OKX 不可达：请检查代理 / 网络（不会以假数据冒充真实行情）";

  return (
    <div ref={wrapRef} className="relative">
      <button
        ref={triggerRef}
        type="button"
        title={title}
        aria-expanded={open}
        aria-haspopup="dialog"
        aria-controls={open ? panelId : undefined}
        onClick={() => toggle(!open)}
        className="rounded-full outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
      >
        <Badge tone={badgeTone(chip.tone)}>
          <span
            className="mr-1 inline-block h-1.5 w-1.5 rounded-full motion-safe:animate-[shimmer_1.4s_ease-in-out_infinite]"
            style={{ background: "currentColor" }}
          />
          {chip.label}
        </Badge>
      </button>

      {open ? (
        <div
          id={panelId}
          role="dialog"
          aria-label="OKX 连接状态"
          className="absolute right-0 top-[calc(100%+6px)] z-50 w-[280px] rounded-[12px] border border-[var(--border)] bg-[var(--surface)] p-3 text-xs shadow-soft"
        >
          <div className="mb-2 flex items-center justify-between gap-2">
            <span className="font-medium text-[var(--text)]">OKX 连接状态</span>
            <Badge tone={badgeTone(chip.tone)}>{chip.label}</Badge>
          </div>

          {rows.length ? (
            <ul className="flex flex-col gap-1.5 border-t border-[var(--border)] pt-2">
              {rows.map((row) => (
                <li key={row.key} className="flex items-center justify-between gap-2">
                  <span className="flex min-w-0 items-center gap-1.5">
                    <span
                      className="inline-block h-1.5 w-1.5 shrink-0 rounded-full"
                      style={{ background: DOT[row.tone] }}
                    />
                    <span className="truncate text-[var(--text)]">{row.label}</span>
                  </span>
                  <span className="shrink-0 tabular text-[var(--muted)]">
                    {row.stateText} · {row.detail}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="border-t border-[var(--border)] pt-2 text-[var(--muted)]">
              {connectivity ? "尚未采集到链路样本" : "连接详情暂不可用（后端未提供该端点）"}
            </p>
          )}

          {account ? (
            <p className="mt-1.5 border-t border-[var(--border)] pt-1.5 text-[var(--muted)]">
              {account}
            </p>
          ) : null}
          {clock ? <p className="mt-1 text-[var(--muted)]">{clock}</p> : null}

          {error ? (
            <div className="mt-2 border-t border-[var(--border)] pt-2">
              <p className="tabular text-[var(--muted)]">最近错误 · {fmtTime(error.ts)}</p>
              <p className="mt-0.5 break-words text-[var(--text)]">{error.message}</p>
            </div>
          ) : null}

          <div className="mt-2 flex items-center justify-between gap-2 border-t border-[var(--border)] pt-2">
            <span className="tabular text-[var(--muted)]">{activitySummary(connectivity)}</span>
            {onProbe ? (
              <button
                type="button"
                onClick={probe}
                disabled={probing}
                className="rounded-[7px] bg-[var(--surface-2)] px-2 py-1 font-medium text-[var(--text)] transition-colors hover:bg-[var(--surface-3)] disabled:opacity-60"
              >
                {probing ? "探测中…" : "重新探测"}
              </button>
            ) : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}

// 面板行的小圆点用色（芯片走 `Badge` 的 tone；这里是行内点，直接取设计 token）。
const DOT: Record<string, string> = {
  long: "var(--long)",
  short: "var(--short)",
  warn: "var(--accent)",
  neutral: "var(--muted)",
};
