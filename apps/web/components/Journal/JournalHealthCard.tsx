"use client";

import { Badge, Card } from "@/components/ui";
import { captureTierLabel, formatBytes } from "@/lib/journal";
import type { JournalStats } from "@/lib/journalTypes";

/**
 * 采集健康度卡（ai-trade-audit-journal §2.6.5）。
 *
 * **`dropped > 0` ⇒ 琥珀色横幅**：「有 N 条日志因写入积压被丢弃，本时段的审计记录存在缺口」。
 * 审计系统必须说出自己哪里不完整——一个看起来干干净净、实际有洞的日志台，比没有日志台更坏。
 *
 * 卡上的**每一个口径数字都来自 props**（`/journal/stats` 的回执），一个都不硬编码。
 * 教训来源：`_compute_ai_stats` 里那句注释——硬编码的「近 50 轮」一旦与常量分叉就开始说谎。
 */
function Row({ label, value, hint }: { label: string; value: React.ReactNode; hint?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-0.5">
      <span className="whitespace-nowrap text-[11px] text-[var(--muted)]">
        {label}
        {hint && <span className="ml-1 text-[10px]">{hint}</span>}
      </span>
      <span className="tabular text-xs">{value}</span>
    </div>
  );
}

export default function JournalHealthCard({ stats }: { stats: JournalStats | null }) {
  if (!stats) {
    return (
      <Card className="p-3 text-xs text-[var(--muted)]">采集健康度读数尚未载入。</Card>
    );
  }
  const counters = stats.counters;
  const gap = counters.dropped + counters.droppedOnShutdown;

  return (
    <Card className="space-y-2 p-3">
      <header className="flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-medium">采集健康度</h3>
        <Badge tone={stats.enabled ? "long" : "neutral"}>
          <span aria-hidden="true" className="mr-1">
            {stats.enabled ? "●" : "○"}
          </span>
          {stats.enabled ? "采集中" : "已关闭"}
        </Badge>
        <Badge tone="accent">{captureTierLabel(stats.captureTier)}</Badge>
        <Badge tone={stats.contextChainEnabled ? "long" : "neutral"}>
          <span aria-hidden="true" className="mr-1">
            {stats.contextChainEnabled ? "●" : "○"}
          </span>
          {stats.contextChainEnabled ? "上下文链已开启" : "上下文链已关闭"}
        </Badge>
      </header>

      {gap > 0 && (
        <p
          role="alert"
          className="rounded-[8px] bg-[color-mix(in_srgb,var(--accent)_16%,transparent)] px-2 py-1.5 text-[11px] text-[var(--accent-ink)]"
        >
          有 {gap} 条日志因写入积压或关停未排空而被丢弃，本时段的审计记录存在缺口。
          调大 AI_TRADE_JOURNAL_QUEUE_MAX 或降低采集档可以缓解。
        </p>
      )}
      {counters.serializeErrors > 0 && (
        <p className="rounded-[8px] bg-[var(--surface-2)] px-2 py-1.5 text-[11px] text-[var(--muted)]">
          有 {counters.serializeErrors} 轮的留痕在序列化时失败（那几轮的交易本身照常完成，
          只是没有留下日志行）。
        </p>
      )}

      <section>
        <Row label="入队总数" value={counters.enqueued} />
        <Row label="已落库" value={counters.written} />
        <Row label="被丢弃" value={counters.dropped} />
        <Row label="关停未排空" value={counters.droppedOnShutdown} />
        <Row label="卸载的 info 事件" value={counters.shedInfoEvents} />
        <Row label="写库错误" value={counters.writeErrors} />
        <Row
          label="当前队列深度"
          value={`${stats.queue.depth} / ${stats.queue.max}`}
        />
        <Row label="最老待写 (ms)" value={stats.queue.oldestPendingMs} />
      </section>

      <section className="border-t border-[var(--border)] pt-1.5">
        <Row label="决策行" value={stats.tables.decisions} />
        <Row label="事件行" value={stats.tables.events} />
        <Row label="提示词档案" value={stats.tables.promptProfiles} />
        <Row label="实占字节" value={formatBytes(stats.tables.approxBytes)} />
      </section>

      <section className="border-t border-[var(--border)] pt-1.5">
        <Row
          label="保留天数"
          value={stats.retention.days === 0 ? "不限（该闸未启用）" : stats.retention.days}
        />
        <Row
          label="保留行数"
          value={stats.retention.maxRows === 0 ? "不限（该闸未启用）" : stats.retention.maxRows}
        />
        <Row label="单段文本上限" value={stats.limits.textMaxChars} />
        <Row label="上下文上限" value={stats.limits.payloadMaxChars} />
        <Row label="导出行数上限" value={stats.limits.exportMaxRows} />
        <Row
          label="单片导出墙钟预算 (秒)"
          value={
            stats.limits.exportTimeBudgetS === 0
              ? "不限（该闸未启用）"
              : stats.limits.exportTimeBudgetS
          }
        />
        <Row label="事件采集下限" value={stats.limits.eventMinLevel} />
      </section>
    </Card>
  );
}
