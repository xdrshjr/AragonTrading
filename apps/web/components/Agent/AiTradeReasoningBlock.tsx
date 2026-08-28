"use client";

// 分阶段研判视图（ai-trade-deep-reasoning-chain 轴 E / §3.5.2）。取代旧的 `AnalysisBlock`。
//
// 一段 ~9000 字符的英文思维链原样糊进右侧窄栏就是一次人机交互的倒退。本组件按三条原则重做：
// **渐进式披露**（流式期只显当前阶段 + 进度导轨；结算后默认折叠成一行中文摘要）、
// **稳定锚点**（`## S<k>` 是后端协议规定的、模型逐字输出的标记，不依赖模型措辞）、
// **母语索引**（中文阶段名来自前端静态映射，英文正文原样保留）。
//
// 安全铁律：模型输出即不可信输入——正文一律走 `renderMarkdown`（自动转义、绝不
// `dangerouslySetInnerHTML`、链接只放行 http(s)）。

import { useMemo, useState } from "react";
import {
  DEFAULT_OPEN_STAGES,
  STAGE_TOTAL,
  collapsedHeadline,
  missingStageTitles,
  parseReasoningStages,
  stageAt,
  stageProgress,
  tailLines,
  type ReasoningKind,
  type ReasoningStage,
} from "@/lib/aiTradeReasoning";
import type { AiTradeRoundTrace } from "@/lib/aiTradeTypes";
import { fmtNum } from "@/lib/format";
import { renderMarkdown } from "@/lib/markdown";

/** 流式期只渲染当前阶段的尾部若干行——DOM 节点数与轮次长度**解耦**（用例 F-7）。 */
const LIVE_TAIL_LINES = 5;

function fmtTokens(value?: number | null): string {
  if (value == null) return "—";
  return value >= 1000 ? `${fmtNum(value / 1000, 1)}k` : String(value);
}

function fmtChars(value: number): string {
  return value >= 1000 ? `${fmtNum(value / 1000, 1)}k 字` : `${value} 字`;
}

function fmtElapsed(ms?: number | null): string {
  if (ms == null) return "";
  return ms >= 60_000 ? `${fmtNum(ms / 60_000, 1)} 分钟` : `${Math.round(ms / 1000)}s`;
}

/** 12（或 7）格进度导轨：命中实心、缺失灰槽。`title` 给中文阶段名 + 字数（hover 可读）。 */
function StageRail({ stages, kind }: { stages: ReasoningStage[]; kind: ReasoningKind }) {
  const total = STAGE_TOTAL[kind];
  const cells = [];
  for (let i = 1; i <= total; i += 1) {
    const stage = stageAt(stages, i);
    cells.push(
      <span
        key={i}
        title={
          stage
            ? `${stage.id} ${stage.titleZh} · ${stage.body.length} 字`
            : `${kind === "review" ? "R" : "S"}${i} 未输出`
        }
        aria-hidden
        className="h-1.5 w-2.5 rounded-[2px]"
        style={{
          backgroundColor: stage ? "var(--accent)" : "var(--border)",
          opacity: stage ? 1 : 0.7,
        }}
      />,
    );
  }
  return <span className="flex items-center gap-[3px]">{cells}</span>;
}

/**
 * 整段正文：流式走 `pre-wrap` + 光标，结算后走安全 markdown。
 *
 * `tail`（默认 **false**）：只渲染尾部若干行。**仅用于按协议分段的那条路径**——一个阶段的正文
 * 可以有几千字符，DOM 节点数必须与它解耦（用例 F-7）。未分阶段的回退路径刻意 `tail=false`：
 * 那正是 `off` 档与旧模型走的路，它必须与本特性合入前**逐字一致**（§7.7 第 3 条人工验收）。
 */
function FlatBody({ text, streaming, tail = false }: {
  text: string;
  streaming: boolean;
  tail?: boolean;
}) {
  if (streaming) {
    return (
      <span className="whitespace-pre-wrap">
        {tail ? tailLines(text, LIVE_TAIL_LINES) : text}
        <span className="ml-0.5 inline-block h-3.5 w-[2px] translate-y-0.5 animate-pulse bg-current align-middle" />
      </span>
    );
  }
  return <div className="flex flex-col">{renderMarkdown(text)}</div>;
}

/** 展开态：每阶段一个 `<details>`；最有决策价值的两段（证伪 / 结论）默认展开。 */
function StageList({ stages, kind }: { stages: ReasoningStage[]; kind: ReasoningKind }) {
  const open = DEFAULT_OPEN_STAGES[kind];
  return (
    <div className="flex flex-col gap-1">
      {stages.map((stage) => (
        <details
          key={`${stage.id}-${stage.index}`}
          open={open.includes(stage.index)}
          className="rounded-[8px] border border-[var(--border)] bg-[var(--surface)] px-2 py-1"
        >
          <summary className="cursor-pointer select-none text-[11px] font-medium text-[var(--text)]">
            {stage.id} {stage.titleZh}
            {stage.code && (
              <span className="ml-1 text-[10px] font-normal text-[var(--muted)]">{stage.code}</span>
            )}
            <span className="ml-1 text-[10px] font-normal text-[var(--muted)]">
              · {stage.body.length} 字
            </span>
          </summary>
          <div className="mt-1 flex flex-col text-[11px] leading-relaxed text-[var(--muted)]">
            {renderMarkdown(stage.body)}
          </div>
        </details>
      ))}
    </div>
  );
}

/**
 * 可复用的分阶段正文视图：决策段（`kind="decision"`，12 格）与复核段（`kind="review"`，7 格）
 * 共用**同一套**解析、导轨与渐进披露策略——两处各写一套必然漂移，而漂移的表现是「反思段的
 * 导轨永远显示 0/7」这类不会报错的静默缺失。
 */
export function ReasoningStageView({
  text,
  kind,
  streaming,
}: {
  text: string;
  kind: ReasoningKind;
  streaming: boolean;
}) {
  // 解析结果 memo 化：轨迹面板一次渲染 20 轮，每轮最长 20000 字符——流式期每个 delta 帧都会
  // 触发整列表重渲染，不缓存就是每帧重解析 ~180KB（DOM 节点数解耦之外的第二笔成本）。
  const { preamble, stages } = useMemo(() => parseReasoningStages(text, kind), [text, kind]);
  const progress = stageProgress(stages, kind);
  if (stages.length === 0) {
    return (
      <div className="flex flex-col gap-1">
        <span className="text-[10px] text-[var(--muted)]">未分阶段</span>
        <FlatBody text={text} streaming={streaming} />
      </div>
    );
  }
  if (streaming) {
    const active = stages[stages.length - 1];
    return (
      <div className="flex flex-col gap-1.5">
        <div className="flex items-center gap-2 text-[11px] text-[var(--muted)]">
          <StageRail stages={stages} kind={kind} />
          <span className="tabular">
            阶段 {progress.done}/{progress.total}
          </span>
          <span className="truncate text-[var(--text)]">{active.titleZh}</span>
        </div>
        <FlatBody text={active.body} streaming tail />
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-1.5">
      {preamble && (
        <div className="flex flex-col text-[11px] leading-relaxed text-[var(--muted)]">
          {renderMarkdown(preamble)}
        </div>
      )}
      <StageList stages={stages} kind={kind} />
    </div>
  );
}

/** 扩展思考段（`thinking`）：默认折叠、按 `pre-wrap` 纯文本渲染（它不是 markdown）。 */
function ThinkingBlock({ text, streaming }: { text: string; streaming: boolean }) {
  if (!text) return null; // 未开启扩展思考（默认）⇒ 整块不渲染，与合入前逐字一致
  return (
    <details className="text-[11px] text-[var(--muted)]">
      <summary className="cursor-pointer select-none text-[var(--text)]">深度思考</summary>
      <p className="mt-1 whitespace-pre-wrap leading-relaxed">
        {streaming ? tailLines(text, LIVE_TAIL_LINES) : text}
      </p>
    </details>
  );
}

/** 深化追问段（P0-3）：两份研判**并列呈现、绝不互相覆盖**；未采纳的那一份不隐藏。 */
function ReaskBlock({ text, adopted }: { text: string; adopted: boolean }) {
  if (!text) return null;
  return (
    <details
      className="rounded-[8px] border border-[var(--border)] px-2 py-1 text-[11px]"
      style={{ opacity: adopted ? 1 : 0.75 }}
    >
      <summary className="cursor-pointer select-none font-medium text-[var(--text)]">
        深化追问（{adopted ? "已采纳" : "未采纳，保留首次研判"}）
      </summary>
      <div className="mt-1">
        <ReasoningStageView text={text} kind="decision" streaming={false} />
      </div>
    </details>
  );
}

/** 元信息行：token / 耗时 + 截断与墙钟收敛两枚 chip（诚实呈现，绝不美化）。 */
function MetaRow({ trace }: { trace: AiTradeRoundTrace }) {
  const meta = trace.reasoning;
  if (!meta) return null;
  const elapsed = fmtElapsed(meta.elapsedMs);
  return (
    <div className="flex flex-wrap items-center gap-1.5 text-[10px] text-[var(--muted)]">
      <span className="tabular">
        输入 {fmtTokens(meta.inputTokens)} tok · 输出 {fmtTokens(meta.outputTokens)} tok
        {elapsed ? ` · 耗时 ${elapsed}` : ""}
      </span>
      {meta.truncated && (
        <span className="rounded-full bg-[color-mix(in_srgb,var(--short)_16%,transparent)] px-1.5 py-0.5 text-[var(--short-ink)]">
          输出预算耗尽（已截断）
        </span>
      )}
      {meta.deadlineHit && (
        <span className="rounded-full bg-[var(--surface-2)] px-1.5 py-0.5">
          本轮超时预算用尽，已提前收敛
        </span>
      )}
    </div>
  );
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      onClick={() => {
        // 失败静默降级为一句提示（剪贴板在非安全上下文 / 权限被拒时会抛）。
        navigator.clipboard
          ?.writeText(text)
          .then(() => setCopied(true))
          .catch(() => setCopied(false));
      }}
      className="rounded px-1.5 py-0.5 text-[10px] text-[var(--muted)] transition-colors duration-150 ease-smooth hover:bg-[var(--surface-2)]"
    >
      {copied ? "已复制" : "复制全文"}
    </button>
  );
}

/**
 * 单轮研判段（决策侧）。四态：
 * ① 流式中 —— 进度导轨 + 当前阶段尾部若干行；
 * ② 结算 · 收起态（默认）—— 一行中文摘要标题（`evidenceDigest` → S12 首句 → `reason`）；
 * ③ 结算 · 展开态 —— 逐阶段 `<details>` + 元信息行 + 深化追问段；
 * ④ 扩展思考段 —— 本组件**接管**旧 `AnalysisBlock` 里的那个「深度思考」折叠块。
 */
export default function AiTradeReasoningBlock({ trace }: { trace: AiTradeRoundTrace }) {
  const [open, setOpen] = useState(false);
  const streaming = trace.state === "deciding";
  const analysis = trace.analysis ?? "";
  const reask = trace.reaskAnalysis ?? "";
  const thinking = trace.thinking ?? "";
  // ⚠️ 全部 Hook 必须在任何提前 return **之前**调用（React Hooks 规则）。一轮从 `deciding`
  // 走到 `done` 而研判与思考两段都空（降级轮）时，早退写在 `useMemo` 之前会让本次渲染比
  // 上一次少调一个 Hook，React 抛「Rendered fewer hooks than expected」把整个轨迹面板打崩。
  const { stages } = useMemo(() => parseReasoningStages(analysis, "decision"), [analysis]);
  if (!analysis && !thinking && !streaming) return null;

  const progress = stageProgress(stages, "decision");
  const missing = missingStageTitles(stages, "decision");
  const headline = collapsedHeadline(
    trace.toolCall?.evidenceDigest,
    stages,
    trace.toolCall?.reason,
  );
  const elapsed = fmtElapsed(trace.reasoning?.elapsedMs);

  if (streaming) {
    return (
      <div className="flex flex-col gap-1.5">
        <ThinkingBlock text={thinking} streaming />
        <div className="rounded-[10px] bg-[var(--surface-2)] px-2.5 py-2 text-[12px] leading-relaxed text-[var(--text)]">
          <div className="mb-1 flex items-center gap-1.5 text-[11px] font-medium">
            <span aria-hidden>🧠</span>
            <span>
              深度研判中 · 阶段 {progress.done}/{progress.total}
            </span>
          </div>
          {analysis ? (
            <ReasoningStageView text={analysis} kind="decision" streaming />
          ) : (
            <span className="text-[var(--muted)]">正在研判…</span>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-1.5">
      <ThinkingBlock text={thinking} streaming={false} />
      {analysis && (
        <div className="rounded-[10px] bg-[var(--surface-2)] px-2.5 py-2 text-[12px] leading-relaxed text-[var(--text)]">
          <button
            type="button"
            aria-expanded={open}
            onClick={() => setOpen((v) => !v)}
            className="flex w-full items-center gap-1.5 text-left text-[11px] font-medium"
          >
            <span aria-hidden>🧠</span>
            <span>
              深度研判 · {progress.done}/{progress.total} 阶段 · {fmtChars(analysis.length)}
              {elapsed ? ` · ${elapsed}` : ""}
            </span>
            {missing.length > 0 && (
              <span className="text-[10px] font-normal text-[var(--muted)]" title={missing.join("、")}>
                （缺 {missing.length} 段）
              </span>
            )}
            <span className="ml-auto text-[var(--muted)]">{open ? "⌃" : "⌄"}</span>
          </button>
          {!open && headline && (
            <p className="mt-1 whitespace-pre-wrap text-[11px] leading-relaxed text-[var(--muted)]">
              {headline}
            </p>
          )}
          {open && (
            <div className="mt-1.5 flex flex-col gap-1.5">
              <ReasoningStageView text={analysis} kind="decision" streaming={false} />
              <ReaskBlock text={reask} adopted={Boolean(trace.reasoning?.reaskAdopted)} />
              <div className="flex items-center justify-between gap-2">
                <MetaRow trace={trace} />
                <CopyButton text={analysis} />
              </div>
            </div>
          )}
          {!open && <MetaRow trace={trace} />}
        </div>
      )}
    </div>
  );
}
