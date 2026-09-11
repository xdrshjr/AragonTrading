"use client";

import { useState } from "react";
import { getBridge } from "@/lib/desktop";
import {
  TRAJECTORY_TONE_COLOR,
  describeOpenPathFailure,
  describeTrajectoryReceipt,
} from "@/lib/trajectory";
import type { AiTradeTrajectoryReceipt } from "@/lib/trajectoryTypes";

/**
 * AI 自动交易开关行下方的**采集状态条**（ai-trade-trajectory-capture §2.14.2）。
 *
 * - 渲染判据在调用方：回执 `requested`（【v2 · RV2】），**绝不**按 `enabled`——打开失败 /
 *   写线程崩溃时 `enabled=false`，而那恰是最需要被看见的状态。
 * - **单行不折行**：文案 `min-w-0 truncate`、全文进 `title`，不改变开关行的 24px 行高契约。
 * - 形状 + 文字双通道（● / △ / ⚠ / —），颜色只是第三条通道。
 */
export interface AiTradeTrajectoryStatusProps {
  receipt: AiTradeTrajectoryReceipt;
  /** 分组已暂停：`recording` 在暂停期说「待命」而不是「采集中」。 */
  paused: boolean;
}

export default function AiTradeTrajectoryStatus({
  receipt,
  paused,
}: AiTradeTrajectoryStatusProps) {
  const view = describeTrajectoryReceipt(receipt, paused);
  const color = TRAJECTORY_TONE_COLOR[view.tone];
  return (
    <div
      data-testid="ai-trade-trajectory-status"
      data-tone={view.tone}
      data-state={receipt.state}
      className="flex min-w-0 items-center gap-1.5 text-[10px]"
    >
      <span aria-hidden="true" className="shrink-0" style={{ color }}>
        {view.glyph}
      </span>
      <span
        className="min-w-0 truncate tabular"
        style={{ color }}
        title={view.text}
        data-testid="ai-trade-trajectory-status-text"
      >
        {view.text}
      </span>
      <OpenDirectoryButton directory={receipt.directory} />
    </div>
  );
}

const OPEN_BUTTON =
  "shrink-0 rounded border border-[var(--border)] px-1 py-px text-[9px] " +
  "text-[var(--accent-ink)] hover:bg-[var(--surface-2)]";

/**
 * 桌面端「打开目录」。只在桥存在且回执给出了分组目录时渲染。
 *
 * 失败时按钮原地改为「无法打开」、原因进 `title`：在一条单行状态条里再挤一行错误文字，
 * 会把开关行下方的排版撑开。
 */
function OpenDirectoryButton({ directory }: { directory: string | null }) {
  const [failure, setFailure] = useState<string | null>(null);
  if (!directory || !getBridge()?.openPath) return null;
  const onClick = async () => {
    try {
      const reason = await getBridge()?.openPath?.(directory);
      setFailure(reason || null);
    } catch {
      setFailure("系统拒绝了这次请求");
    }
  };
  // 可访问名必须包含可见文字（WCAG 2.5.3 Label in Name）。
  const visible = failure ? "无法打开" : "打开目录";
  return (
    <button
      type="button"
      onClick={() => void onClick()}
      aria-label={`${visible}（后训练数据）`}
      title={failure ? `无法打开：${describeOpenPathFailure(failure)}` : directory}
      data-testid="ai-trade-trajectory-open-dir"
      className={OPEN_BUTTON}
    >
      {visible}
    </button>
  );
}
