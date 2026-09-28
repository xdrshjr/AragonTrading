"use client";

import { environmentLabel, tradingTime,
  type AutoTradeContext } from "@/lib/agentTradingTypes";

interface ContextMember {
  instId?: string;
  state?: string;
  bar?: string;
  lastRound?: number | null;
  lastDecision?: { action?: string; reason?: string; gateReasons?: string[] } | null;
  positionSummary?: { size?: string; sz?: string; avgPx?: string } | null;
}
interface ContextGroup extends ContextMember {
  groupId?: string;
  contextUnavailableReason?: string | null;
  members?: ContextMember[];
}
const STATE_LABELS: Record<string, string> = {
  running: "运行中", paused: "已暂停", settling: "结算中", stopping: "停止中",
  recovering: "恢复中", idle: "空闲", unknown: "状态未知",
};

/** 自动交易快照只读，与交易所持仓分开，不导出模型原始思考。 */
export default function AutoTradeContextCard({ context }: {
  context: AutoTradeContext;
}) {
  const detail = Array.isArray(context.data) ? context.data[0] : context.data;
  const groups = Array.isArray(detail?.groups)
    ? detail.groups as ContextGroup[] : [detail as ContextGroup];
  return <section aria-label="自动交易研判"
    className="min-w-0 rounded-[10px] border border-[var(--border)] bg-[var(--surface)] p-3 text-xs">
    <h4 className="font-semibold">自动交易研判 · 只读</h4>
    <p className="mt-1">{context.accountLabel} · {environmentLabel(context.environment)}</p>
    <p className="text-[var(--muted)]">本地策略快照：{tradingTime(context.asOfTs)}</p>
    {groups.length === 0 && <p className="mt-2">当前没有自动交易托管快照。</p>}
    {groups.map((group, index) => <Group key={group?.groupId ?? index} group={group} />)}
    <p className="mt-2 text-[var(--muted)]">
      本地策略视图不等于交易所实时持仓；普通对话不会接管或停止托管。
    </p>
    {context.warnings.map((warning, index) => <p key={index}>{warning}</p>)}
  </section>;
}

function Group({ group }: { group: ContextGroup }) {
  if (!group) return <p>当前没有自动交易托管快照。</p>;
  const members = group.members?.length ? group.members : [group];
  return <div className="mt-2 space-y-2">
    <p>托管状态：{STATE_LABELS[group.state ?? "unknown"] ?? group.state}</p>
    {group.contextUnavailableReason
      ? <p>身份或历史证据不足，当前无法读取最近研判。</p>
      : members.map((member, index) => <div key={member.instId ?? index}
        className="rounded-md bg-[var(--surface-2)] p-2">
        <p>{member.instId ?? "托管会话"} · {member.bar ?? "周期未知"}
          {" · "}轮次 {member.lastRound ?? "—"}</p>
        <p>最近决策：{member.lastDecision?.action ?? "暂无"}</p>
        {member.lastDecision?.reason && <p>{member.lastDecision.reason}</p>}
        {!!member.lastDecision?.gateReasons?.length && <p>
          门禁原因：{member.lastDecision.gateReasons.join("；")}</p>}
        {member.positionSummary && <p>本地仓位：
          {member.positionSummary.sz ?? member.positionSummary.size ?? "—"} 张
          {" · "}均价 {member.positionSummary.avgPx ?? "—"}</p>}
      </div>)}
  </div>;
}
