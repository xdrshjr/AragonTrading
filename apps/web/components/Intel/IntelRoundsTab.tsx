"use client";

import { useState } from "react";
import { Badge, Skeleton } from "@/components/ui";
import { absoluteTime, relativeTime } from "@/lib/intel";
import { verdictNoRefNote, verifyRoundSummary } from "@/lib/intelClosedLoop";
import type { IntelRound } from "@/lib/intelTypes";

// 拒收码 / 动作码 → 中文。**只做展示层翻译**，判据与计数全部来自后端的直方图。
const CODE_LABEL: Record<string, string> = {
  bad_ref: "引用了不存在的语料编号",
  unsourced_number: "数字在被引用文档中找不到",
  too_long: "标题或摘要超长",
  too_short: "摘要过短",
  stale_event: "事件时刻超出时间窗",
  bad_field: "类别/方向/影响力非法",
  imperative_text: "含指令或角色设定文本",
  over_cap: "超出本轮条目上限",
  promote: "升级",
  demote: "降级",
  archive: "归档",
  merge: "合并",
  keep: "保持",
  ttl: "到期归档",
  overflow: "溢出淘汰",
  lapsed_schedule: "已排期事件失效",
  promote_insufficient_evidence: "升级证据不足（被驳回）",
  demote_skips_tier: "降级跨层（被驳回）",
  archive_protected: "条目受保护（被驳回）",
  merge_bad_target: "合并目标非法（被驳回）",
  unknown_item: "条目不存在（被驳回）",
  bad_action: "动作非法（被驳回）",
  truncated_actions: "超出单轮动作上限被截断",
  // ---- closed-loop 轴 C / 轴 D 的新码 ----
  confirmed: "核查判定：已证实",
  refuted: "核查判定：已证伪",
  unresolved: "核查判定：待定",
  bad_item_ref: "条目编号不存在（整条丢弃）",
  bad_verdict: "判定值域外（整条丢弃）",
  verdict_no_ref: "判定未指出依据，已降为待定",
  verdict_no_reason: "判定没有写理由，已降为待定",
  dup_item_ref: "同一条目重复判定（取第一条）",
  archive_profitable: "归档有正战绩的条目，理由不足（被驳回）",
  blocked_sweep: "来源已拉黑，存量归档",
  citations_pruned: "引用明细到期裁剪",
  verify_skipped: "本轮没有待核查条目",
};

// 降级码 → 中文。轮存根里的 `degradeCode` 是一个**裸码**（summary 里那份才带后端下发的
// `message`），直接把 `search_failed` 贴在一个中文界面上等于没说。此表只服务审计时间线。
const DEGRADE_LABEL: Record<string, string> = {
  no_provider: "未配置检索源",
  verify_no_provider: "未配置检索源，核查未启动",
  verify_search_failed: "核查检索请求失败",
  verify_no_corpus: "核查没有检索到可用材料，相关条目记为待定",
  verify_llm_failed: "核查模型调用失败",
  disabled: "情报采集已在设置中关闭",
  search_failed: "检索请求失败",
  search_empty: "检索没有返回任何结果",
  llm_failed: "情报模型调用失败",
  no_tool_call: "模型本轮未提交结果",
  reasoning_truncated: "模型输出预算在提交前就用尽",
  all_rejected: "没有一条通过真实性校验",
  budget_exhausted: "今日采集额度已用尽",
  circuit_open: "检索源连续失败已暂停",
  paused: "AI 自动交易已暂停",
};

const KIND_LABEL: Record<string, string> = { collect: "采集", curate: "整理", verify: "核查" };

/** 一行摘要：三种轮各说各的话（一个「执行 N 个动作」放在核查轮上等于什么都没说）。 */
function roundSummary(round: IntelRound, entries: [string, number][]): string {
  if (round.kind === "collect") {
    return (
      `${round.docCount} 篇材料 / ${round.domainCount} 个来源 → ` +
      `采纳 ${round.accepted}、合并 ${round.merged}、拒收 ${round.rejected}`
    );
  }
  if (round.kind === "verify") return verifyRoundSummary(round.actions || {});
  return `执行 ${entries.reduce((sum, [, n]) => sum + n, 0)} 个动作`;
}

/**
 * 采集 / 整理 / 核查轮的审计时间线（ai-trade-market-intel §6.2 #32）。
 *
 * 这一屏回答的是「为什么这两小时一条情报都没有」——一个只在当时闪过的 banner 回答不了它。
 * 每一轮都可展开看拒收码直方图：**服务端裁决的每一条理由都留痕**，模型与服务端的分歧同样。
 */
export default function IntelRoundsTab({
  rounds,
  loading,
  nowMs,
}: {
  rounds: IntelRound[];
  loading: boolean;
  nowMs: number;
}) {
  if (loading) {
    return (
      <div className="flex flex-col gap-2 p-3">
        <Skeleton className="h-10 w-full rounded-[10px]" />
        <Skeleton className="h-10 w-full rounded-[10px]" />
      </div>
    );
  }
  if (!rounds.length) {
    return (
      <div className="px-6 py-10 text-center text-[11px] text-[var(--muted)]">
        这个标的还没有任何采集或整理记录。开启 AI 自动交易后，情报 Agent 会按设定的周期自动跑。
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-1.5 p-3" data-testid="intel-rounds">
      {rounds.map((round) => (
        <RoundRow key={round.id} round={round} nowMs={nowMs} />
      ))}
    </div>
  );
}

function RoundRow({ round, nowMs }: { round: IntelRound; nowMs: number }) {
  const [open, setOpen] = useState(false);
  const histogram = round.kind === "collect" ? round.rejects : round.actions;
  const entries = Object.entries(histogram || {}).filter(([, n]) => n > 0);
  const tone = round.status === "ok" ? "long" : round.status === "failed" ? "short" : "neutral";
  return (
    <div className="rounded-[10px] border border-[var(--border)] px-2.5 py-1.5">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center justify-between gap-2 text-left outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
      >
        <span className="flex min-w-0 items-center gap-1.5">
          <Badge tone={tone}>{KIND_LABEL[round.kind] ?? round.kind}</Badge>
          <span className="truncate text-[11px] text-[var(--text)]">
            {roundSummary(round, entries)}
          </span>
        </span>
        <span
          className="shrink-0 text-[10px] tabular text-[var(--muted)]"
          title={absoluteTime(round.startedTs)}
        >
          {relativeTime(round.startedTs, nowMs)}
        </span>
      </button>
      {open && (
        <div className="mt-1 flex flex-col gap-0.5 border-t border-[var(--border)] pt-1">
          {/* closed-loop §3.7.4：**这正是本特性最值得让用户看见的那一行**——它是「模型不能空口
              断言」这条承诺的可见证据。 */}
          {round.kind === "verify" && verdictNoRefNote(round.actions || {}) && (
            <span data-testid="intel-verdict-no-ref" className="text-[10px] text-[var(--muted)]">
              {verdictNoRefNote(round.actions || {})}
            </span>
          )}
          {round.degradeCode && (
            <span className="text-[10px] text-[var(--warn)]">
              降级：{DEGRADE_LABEL[round.degradeCode] ?? round.degradeCode}
              {round.errorText ? `（${round.errorText}）` : ""}
            </span>
          )}
          {entries.map(([code, n]) => (
            <span key={code} className="text-[10px] tabular text-[var(--muted)]">
              {CODE_LABEL[code] ?? code} × {n}
            </span>
          ))}
          {!!round.queries.length && (
            <span className="truncate text-[10px] text-[var(--muted)]" title={round.queries.join(" ｜ ")}>
              查询词：{round.queries.join(" ｜ ")}
            </span>
          )}
          <span className="text-[10px] tabular text-[var(--muted)]">
            token：输入 {round.inputTokens} / 输出 {round.outputTokens}
          </span>
        </div>
      )}
    </div>
  );
}
