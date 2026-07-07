"use client";

import { Badge } from "@/components/ui";
import { colorForReturn, fmtMoney, fmtNum, fmtPct, fmtPrice } from "@/lib/format";
import { useAppStore } from "@/lib/store";
import type { AiTradeGroupMemberState } from "@/lib/aiTradeTypes";
import type { Instrument } from "@/lib/types";

// AI 自动交易「标的面板」（ai-trade-multi-symbol-and-timeframe §4.2）：泛化为「多标的」分组视图。
// Agent 的思考 / 分析 / 工具调用 / 决策日志由右侧「AI 助手 → 执行轨迹」承接；左侧此处只呈现标的：
// 分组头（标签 + 运行徽标 + 分组累计盈亏＝Σ 成员 PnL）+ 逐成员「自动交易标的」行（可点击切主图 +
// 持仓徽标 + 命中自选时富化行情）。单成员时视觉与第 5 轮单会话面板等价。
interface AiTradePanelProps {
  rows: Instrument[]; // 已 resolve 的自选行（用于富化成员标的行行情）
  active: string; // 当前选中 instId（判断是否用 livePrice 覆盖快照价）
  livePrice?: number | null;
  changePct?: number | null;
  onSelect: (instId: string) => void; // 点击成员标的 → setInst 切图
}

export default function AiTradePanel({
  rows,
  active,
  livePrice,
  changePct,
  onSelect,
}: AiTradePanelProps) {
  const group = useAppStore((s) => s.aiTradeGroup);

  if (!group) return null;

  const members = Object.values(group.members);
  const aggregatePnl = members.reduce((sum, m) => sum + (m.session?.sessionPnlUsd ?? 0), 0);
  const stopped = group.state === "stopped" || members.every((m) => m.session?.state === "stopped");

  return (
    <div className="flex flex-col gap-2 border-b border-[var(--border)] px-3 py-2.5">
      <div className="flex items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-xs font-semibold text-[var(--text)]">AI 自动交易</span>
          <Badge tone={stopped ? "neutral" : "long"}>{stopped ? "已停止" : "运行中"}</Badge>
          {members.length > 1 && (
            <span className="text-[10px] text-[var(--muted)]">{members.length} 个标的</span>
          )}
        </div>
        <span
          className={
            aggregatePnl >= 0
              ? "tabular text-xs font-medium text-[var(--long)]"
              : "tabular text-xs font-medium text-[var(--short)]"
          }
        >
          {aggregatePnl >= 0 ? "+" : ""}
          {fmtMoney(aggregatePnl)}
        </span>
      </div>

      <div className="flex flex-col gap-1">
        <span className="text-[10px] font-semibold uppercase tracking-wide text-[var(--muted)]">
          自动交易标的
        </span>
        {members.map((m) => (
          <MemberRow
            key={m.instId}
            member={m}
            rows={rows}
            active={active}
            livePrice={livePrice}
            changePct={changePct}
            onSelect={onSelect}
          />
        ))}
      </div>
    </div>
  );
}

function MemberRow({
  member,
  rows,
  active,
  livePrice,
  changePct,
  onSelect,
}: {
  member: AiTradeGroupMemberState;
  rows: Instrument[];
  active: string;
  livePrice?: number | null;
  changePct?: number | null;
  onSelect: (instId: string) => void;
}) {
  const { instId } = member;
  const session = member.session;
  const positionSide = session?.positionSide ?? "flat";
  // 标的行情解析：命中自选行则富化（且 active 标的用 WS 实时价覆盖快照）；未收藏则优雅降级不造价。
  const inst = rows.find((r) => r.instId === instId) ?? null;
  const isActive = instId === active;
  const price = inst ? (isActive ? (livePrice ?? inst.last ?? null) : (inst.last ?? null)) : null;
  const pct = inst ? (isActive ? (changePct ?? inst.changePct ?? null) : (inst.changePct ?? null)) : null;

  return (
    <button
      type="button"
      onClick={() => onSelect(instId)}
      aria-current={isActive}
      className="flex w-full items-center justify-between gap-2 rounded border border-[var(--border)] bg-[var(--bg)] px-2 py-1.5 text-left transition-colors hover:bg-[var(--surface-2)] aria-[current=true]:border-[var(--accent)]"
    >
      <span className="flex min-w-0 flex-col">
        <span className="flex items-center gap-1.5">
          <span className="truncate text-xs font-medium text-[var(--text)]">{instId}</span>
          {session?.bar && <span className="shrink-0 text-[10px] text-[var(--muted)]">{session.bar}</span>}
        </span>
        <span className="truncate text-[10px] text-[var(--muted)]">
          预算 {fmtNum(session?.budgetUsd ?? 0)} USDT · {session?.leverage ?? 0}× · {member.allocationPct}%
        </span>
      </span>
      <span className="flex shrink-0 items-center gap-2">
        <Badge tone={positionTone(positionSide)}>{describePosition(positionSide)}</Badge>
        {price != null && (
          <span className="flex flex-col items-end">
            <span className="text-[11px] tabular text-[var(--text)]">{fmtPrice(price)}</span>
            {pct != null && (
              <span className="text-[10px] tabular" style={{ color: colorForReturn(pct) }}>
                {fmtPct(pct)}
              </span>
            )}
          </span>
        )}
      </span>
    </button>
  );
}

function describePosition(side: string): string {
  if (side === "long") return "多";
  if (side === "short") return "空";
  return "空仓";
}

function positionTone(side: string): "long" | "short" | "neutral" {
  if (side === "long") return "long";
  if (side === "short") return "short";
  return "neutral";
}
