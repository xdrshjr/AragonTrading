"use client";

import { useMemo } from "react";
import { Skeleton } from "@/components/ui";
import { collapseAlerts } from "@/lib/intelActionability";
import type { IntelAlert, IntelRound } from "@/lib/intelTypes";
import IntelAlertRow from "./IntelAlertRow";
import { RoundRow } from "./IntelRoundsTab";

/**
 * 「动态」页签（ai-trade-intel-actionability §3.4.1）——轮次与告警**按时间倒序合流**。
 *
 * 这两类东西本来就是同一件事：「情报层在什么时刻做了什么」。合流之后用户第一次能看到一条
 * 完整的链路：
 *
 *     10:03 采集轮收进 3 条 → 10:03 告警：高危风险 → 10:03 唤醒了第 47 轮决策
 *
 * **零新增页签**：`IntelModal` 已有 7 个，再加两个会同时越过 HCI 的合理上限与 CLAUDE.md 的
 * 800 行上限。本组件因此**吸收**既有「整理记录」页签的渲染（`RoundRow` 由 `IntelRoundsTab`
 * 导出并在这里复用，**不抄第二份**），只在时间轴上多插一类行。
 */
export default function IntelActivityTab({
  rounds,
  alerts,
  loading,
  nowMs,
  pendingTtlS,
  onOpenRound,
}: {
  rounds: IntelRound[];
  alerts: IntelAlert[];
  loading: boolean;
  nowMs: number;
  pendingTtlS: number;
  onOpenRound?: (runId: number, roundNo: number) => void;
}) {
  // 合流：两类行各带自己的时刻，按时刻倒序。**折叠只在告警内部做**，且只折叠**相邻**的同码
  // 未告警行——把两条中间隔着一次采集轮的抑制行折在一起，会让时间线说一件没发生过的事。
  const entries = useMemo(() => {
    const roundRows = rounds.map((round) => ({
      kind: "round" as const,
      ts: round.startedTs,
      key: `round-${round.id}`,
      round,
    }));
    const alertRows = collapseAlerts(alerts).map((group) => ({
      kind: "alert" as const,
      ts: group.head.createdTs,
      key: group.key,
      group,
    }));
    return [...roundRows, ...alertRows].sort((a, b) => b.ts - a.ts);
  }, [rounds, alerts]);

  if (loading) {
    return (
      <div className="flex flex-col gap-2 p-3">
        <Skeleton className="h-10 w-full rounded-[10px]" />
        <Skeleton className="h-10 w-full rounded-[10px]" />
      </div>
    );
  }
  if (!entries.length) {
    return (
      <div className="px-6 py-10 text-center text-[11px] text-[var(--muted)]">
        这个标的还没有任何采集、整理或告警记录。开启 AI 自动交易后，情报 Agent
        会按设定的周期自动跑；一条够格的高危情报会当场多问 AI 一次。
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-1.5 p-3" data-testid="intel-activity">
      {entries.map((entry) =>
        entry.kind === "round" ? (
          <RoundRow key={entry.key} round={entry.round} nowMs={nowMs} />
        ) : (
          <IntelAlertRow
            key={entry.key}
            group={entry.group}
            nowMs={nowMs}
            pendingTtlS={pendingTtlS}
            onOpenRound={onOpenRound}
          />
        ),
      )}
    </div>
  );
}
