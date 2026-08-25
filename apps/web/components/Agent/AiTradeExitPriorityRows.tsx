"use client";

/**
 * 离场优先级的两块轨迹呈现（ai-trade-exit-priority-and-commitment 轴 D · §4.6.1 / §4.6.2）。
 *
 * **独立组件文件**是刻意的（【R:P1-7】）：`AiTradeTraceRound.tsx` 现有 841 行、已越过 CLAUDE.md 的
 * 800 行红线，本轮对它的净增被压到「一行 import + 两处挂载」。
 *
 * 两块的职责严格分开：
 *  · `ProposedVetoRow` —— 决策工具卡片上的**保真行**。用户截图里那句「决策工具 · 观望」是本轮最
 *    刺眼的一处不诚实：模型提交的是 `close`，界面上只剩 `hold`，唯一的线索是折叠在下面的一行
 *    降级码。这一行把「提议了什么 → 被谁拦下」摆回台面。
 *  · `ExitPriorityRow` —— 交易周期卡片上的**优先级行**。`exitPriority` 键缺席 ⇒ 整行省略。
 *
 * 文案与数字**全部来自 `lib/aiTradeExit.ts` 的映射与后端 wire**，本文件不硬编任何一个阈值（X-8）。
 */
import { describeExitPriority, describeVetoedProposal } from "@/lib/aiTradeExit";
import type { AiTraceTone } from "@/lib/aiTrade";
import type { AiTradeExitPriorityWire, AiTradeRoundTrace } from "@/lib/aiTradeTypes";

const TONE_VAR: Record<AiTraceTone, string> = {
  long: "var(--long)",
  short: "var(--short)",
  muted: "var(--muted)",
  accent: "var(--accent)",
};

function toneSoft(tone: AiTraceTone): string {
  return `color-mix(in srgb, ${TONE_VAR[tone]} 14%, transparent)`;
}

/**
 * 决策工具卡片的「提议 → 被拦」保真行（F-6）。
 *
 * 未被拦（`proposedAction` 为空）⇒ `null` ⇒ 卡片逐字节回退到本特性合入前的样子（老快照零影响）。
 */
export function ProposedVetoRow({ trace }: { trace: AiTradeRoundTrace }) {
  const text = describeVetoedProposal(trace.proposedAction, trace.vetoedBy);
  if (!text) return null;
  return (
    <div
      className="flex flex-col gap-0.5 border-t border-[var(--border)] px-2.5 py-1.5"
      data-testid="ai-exit-proposed-veto"
    >
      <span className="text-[11px] font-medium leading-relaxed text-[var(--text)]">{text}</span>
      {trace.proposedReason && (
        <span className="text-[10px] leading-relaxed text-[var(--muted)]">
          原提议理由：{trace.proposedReason}
        </span>
      )}
    </div>
  );
}

/**
 * 交易周期卡片的「离场优先级」行（§4.6.2）。
 *
 * 取数优先级：**反思之后回填的那一份**（`trace.exitPriority`）优先于随 `horizon` 相位整对象透传
 * 的那一份——后者算在反思之前，`adjudicated` / `failOpenReason` 还没产生，据它渲染会让界面显示一个
 * 比实际更低的档位（而这一轮其实是按更高优先级放行的）。
 *
 * 徽标一律 accent（**绝不用红色**）：本层放行不是拦截，用红色会让用户以为被拦住了。
 */
export function ExitPriorityRow({ trace }: { trace: AiTradeRoundTrace }) {
  const wire: AiTradeExitPriorityWire | null | undefined =
    trace.exitPriority ?? trace.horizonWire?.exitPriority;
  const view = describeExitPriority(wire);
  if (!view) return null;
  return (
    <div className="flex flex-col gap-0.5" data-testid="ai-exit-priority-row">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-[10px] text-[var(--muted)]">离场优先级</span>
        <span
          className="inline-flex items-center rounded-full px-1.5 py-0.5 text-[10px] font-medium"
          style={{ color: TONE_VAR[view.tone], backgroundColor: toneSoft(view.tone) }}
        >
          {view.label}
        </span>
        {view.detail && (
          <span className="tabular text-[10px] text-[var(--muted)]">{view.detail}</span>
        )}
      </div>
      {view.note && (
        <p className="text-[10px] leading-relaxed text-[var(--text)]">{view.note}</p>
      )}
    </div>
  );
}
