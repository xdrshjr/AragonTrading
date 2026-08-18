"use client";

import { useId, useState } from "react";
import { Badge, Button, Modal } from "@/components/ui";
import { AI_TRADE_ARMING_TIMEOUT_MS, formatCountdown } from "@/lib/aiTrade";
import type {
  AiTradeDriftVerdict,
  AiTradeGroupMemberState,
  AiTradeLifecycleWire,
  AiTradeResumePreview,
} from "@/lib/aiTradeTypes";
import { fmtMoney, fmtNum } from "@/lib/format";

/**
 * 续跑复核框（ai-trade-pause-resume §3.9.3）。
 *
 * 诚实不变式 I-4 的界面落点：暂停期间发生的一切（离线被 OCO 平掉 / 被外部加仓 / 挂单成交 /
 * 契约漂移）在续跑**之前**必须以差异清单呈现并要求用户确认；读不到就说读不到。
 *
 * **判定性文案一律由后端下发**（`member.message`），前端只渲染 —— 本仓库已有两次前后端各存
 * 一份口径导致静默漂移的事故。这里只保留装饰性文案（按钮名、标题、颜色）。
 *
 * `verdict === "needs_confirm"` 时勾选框**必须真的门控提交按钮**（守 RV-1 那类「只有写点没有
 * 读点」的成文缺陷）。
 *
 * **第三屏「正在武装」**（ai-trade-lifecycle-truth F-8）：提交之后本框不立刻关闭。从点下「继续」
 * 到 `lifecycle.armed === true` 之间可能有几十秒（冷续跑要暖机），而在本轮之前这段时间界面上
 * 什么都没有 —— 用户只能靠猜，这正是「貌似已经恢复了」那句话的来源。清单的每一行真值**全部取
 * 后端 `lifecycle`，前端一个判断都不下**。
 */
export default function AiTradeResumeDialog({
  open,
  preview,
  loading,
  submitting,
  error,
  arming = false,
  armingSince = null,
  lifecycle = null,
  members = [],
  nowTs = 0,
  onClose,
  onConfirm,
  onEnd,
}: {
  open: boolean;
  preview: AiTradeResumePreview | null;
  loading: boolean;
  submitting: boolean;
  error?: string | null;
  /** 已提交，进入第三屏。 */
  arming?: boolean;
  /** 进入第三屏的墙钟秒（超时判据的起点）。 */
  armingSince?: number | null;
  /** 分组级 lifecycle（后端的权威结论；缺席 ⇒ 还没对账回来）。 */
  lifecycle?: AiTradeLifecycleWire | null;
  members?: AiTradeGroupMemberState[];
  nowTs?: number;
  onClose: () => void;
  onConfirm: (opts: { acknowledge: boolean }) => void;
  onEnd: () => void;
}) {
  const titleId = useId();
  const [acknowledged, setAcknowledged] = useState(false);
  const blocked = preview?.verdict === "blocked";
  const needsConfirm = preview?.verdict === "needs_confirm";
  const canSubmit = !!preview && !blocked && !submitting && (!needsConfirm || acknowledged);
  const elapsedMs = arming && armingSince ? Math.max(0, (nowTs - armingSince) * 1000) : 0;
  const armingTimedOut = arming && elapsedMs > AI_TRADE_ARMING_TIMEOUT_MS;

  return (
    <Modal open={open} onClose={onClose} labelledBy={titleId} className="max-w-xl">
      <div className="flex flex-col gap-3 p-5" data-testid="ai-trade-resume-dialog">
        <h2 id={titleId} className="text-sm font-semibold text-[var(--text)]">
          {arming ? "正在恢复 AI 自动交易" : "继续 AI 自动交易"}
        </h2>

        {arming && (
          <ArmingChecklist
            lifecycle={lifecycle}
            members={members}
            submitting={submitting}
            timedOut={armingTimedOut}
            elapsedMs={elapsedMs}
          />
        )}

        {!arming && loading && <p className="text-[12px] text-[var(--muted)]">正在核对持仓…</p>}

        {!arming && preview && (
          <>
            <p className="text-[12px] leading-relaxed text-[var(--muted)]">
              已暂停 {preview.offlineText}
              {preview.pauseReasonText ? ` · ${preview.pauseReasonText}` : ""}
              {preview.resumedCount > 0 ? ` · 已续跑 ${preview.resumedCount} 次` : ""}
            </p>

            <ContractSummary contract={preview.contract} />

            {preview.drift.length > 0 && (
              <div className="flex flex-col gap-1" data-testid="ai-trade-resume-drift">
                <span className="text-[10px] font-semibold uppercase tracking-wide text-[var(--muted)]">
                  契约漂移
                </span>
                {preview.drift.map((row) => (
                  <span key={row.field} className="text-[11px] text-[var(--muted)]">
                    {row.field}：{String(row.from)} → {String(row.to)}（{row.reason}）
                  </span>
                ))}
              </div>
            )}

            <div className="flex flex-col gap-1.5" data-testid="ai-trade-resume-members">
              {preview.members.map((member) => (
                <div
                  key={member.instId}
                  className={`flex flex-col gap-1 rounded border px-2 py-1.5 ${borderFor(member.verdict)}`}
                >
                  <span className="flex items-center gap-2">
                    <span className="text-[11px] font-medium text-[var(--text)]">
                      {member.instId}
                    </span>
                    <Badge tone={toneFor(member.verdict)}>{VERDICT_LABEL[member.verdict]}</Badge>
                  </span>
                  <span className="text-[11px] leading-relaxed text-[var(--muted)]">
                    {member.message}
                  </span>
                  {member.blockedReason && (
                    <span className="text-[10px] text-[var(--short-ink)]">
                      {BLOCKED_REASON_LABEL[member.blockedReason] ?? member.blockedReason}
                    </span>
                  )}
                </div>
              ))}
            </div>

            {needsConfirm && (
              <label className="flex cursor-pointer items-start gap-2 text-[11px] leading-relaxed text-[var(--text)]">
                <input
                  type="checkbox"
                  checked={acknowledged}
                  onChange={(e) => setAcknowledged(e.target.checked)}
                  className="mt-0.5"
                  aria-label="我已了解离线期间的持仓变化"
                />
                <span>
                  我已了解上面列出的持仓变化，确认按交易所当前的真实持仓继续 AI 自动交易。
                </span>
              </label>
            )}

            {blocked && (
              <p className="text-[11px] leading-relaxed text-[var(--short-ink)]" role="alert">
                当前状态无法安全续跑。你可以稍后重试，或结束并归档这次会话（如仍有持仓，请到实盘
                面板自行处理 —— AI 绝不会替你平掉它没有开过的仓位）。
              </p>
            )}
          </>
        )}

        {error && (
          <p className="text-[11px] leading-relaxed text-[var(--short-ink)]" role="alert">
            {error}
          </p>
        )}

        {/* 武装超时后按钮组换成两条**真实可走的路**（风险 R-12：绝不给一句「请重试」了事）。 */}
        <div className="mt-1 flex items-center justify-end gap-2">
          <Button variant="ghost" size="sm" onClick={onClose} disabled={submitting}>
            {arming ? "收起" : "取消"}
          </Button>
          <Button variant="ghost" size="sm" onClick={onEnd} disabled={submitting}>
            结束并归档
          </Button>
          {(!arming || armingTimedOut) && (
            <Button
              variant="primary"
              size="sm"
              disabled={arming ? submitting : !canSubmit}
              onClick={() => onConfirm({ acknowledge: acknowledged })}
            >
              {submitting ? "正在核对持仓…" : arming ? "重试继续" : "继续"}
            </Button>
          )}
        </div>
      </div>
    </Modal>
  );
}

/**
 * 「正在武装」清单（F-8）：逐成员四行「它到底起来了没有」的**证据**。
 *
 * 四行的真值全部取后端 `member.session.lifecycle`（§5.2 的 wire），**前端一个判断都不下** ——
 * 这一屏存在的全部理由就是「不再让用户猜」，如果它自己去猜，那就只是把谎话换了个地方说。
 *
 * 超过 `AI_TRADE_ARMING_TIMEOUT_MS` 仍未 armed ⇒ 清单原地变成如实的失败说明（说明取后端的
 * `reasonText`，绝不拼一句「请重试」）+ 上方按钮组给出两条真实可走的路。
 */
function ArmingChecklist({
  lifecycle,
  members,
  submitting,
  timedOut,
  elapsedMs,
}: {
  lifecycle: AiTradeLifecycleWire | null;
  members: AiTradeGroupMemberState[];
  submitting: boolean;
  timedOut: boolean;
  elapsedMs: number;
}) {
  const armed = !!lifecycle?.armed;
  const rows = members.filter((m) => !!m.session);
  return (
    <div className="flex flex-col gap-2" data-testid="ai-trade-arming-checklist">
      <p
        className={
          armed
            ? "text-[12px] leading-relaxed text-[var(--long-ink)]"
            : timedOut
              ? "text-[12px] leading-relaxed text-[var(--short-ink)]"
              : "text-[12px] leading-relaxed text-[var(--muted)]"
        }
        role={timedOut ? "alert" : undefined}
      >
        {armed
          ? "全部就绪：Agent 已恢复自动交易。"
          : timedOut
            ? `已等待 ${formatCountdown(elapsedMs / 1000)}，仍有标的没有武装成功。下面是后端此刻给出的原因：`
            : submitting
              ? "正在核对持仓、重建会话、接上行情…（冷启动后需要重新暖机，可能要几十秒）"
              : "已提交，正在等待后端确认武装状态…"}
      </p>

      {rows.length === 0 && !armed && (
        <p className="text-[11px] text-[var(--muted)]">正在等待后端下发成员状态…</p>
      )}

      {rows.map((member) => {
        const wire = member.session?.lifecycle ?? null;
        return (
          <div
            key={member.instId}
            className="flex flex-col gap-1 rounded border border-[var(--border)] px-2 py-1.5"
          >
            <span className="flex items-center gap-2">
              <span className="text-[11px] font-medium text-[var(--text)]">{member.instId}</span>
              <Badge tone={wire?.armed ? "long" : timedOut ? "short" : "neutral"}>
                {wire?.armed ? "已武装" : timedOut ? "未武装" : "武装中"}
              </Badge>
            </span>
            <ArmingLine label="会话已建" done={wire?.state === "running"} />
            <ArmingLine label="行情已连" done={!!wire && wire.linkState !== "down"} />
            <ArmingLine label="已接管持仓" done={(wire?.warmedBars ?? 0) > 0} />
            <ArmingLine
              label="下一次决策"
              done={wire?.nextWakeInS != null || wire?.nextBarInS != null}
              detail={
                wire?.nextWakeInS != null
                  ? formatCountdown(wire.nextWakeInS)
                  : wire?.nextBarInS != null
                    ? `下一根 K 线 ${formatCountdown(wire.nextBarInS)}`
                    : undefined
              }
            />
            {/* 判定性说明**一律由后端下发**（`reasonText`），前端不得按 code 自己拼中文。 */}
            {wire && !wire.armed && wire.reasonText && (
              <span className="text-[10px] leading-relaxed text-[var(--short-ink)]">
                {wire.reasonText}
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
}

/** 清单单行：`✓` / `·` 两态 + 可选的右侧细节（绝不用「--」占位冒充「不适用」）。 */
function ArmingLine({
  label,
  done,
  detail,
}: {
  label: string;
  done: boolean;
  detail?: string;
}) {
  return (
    <span className="flex items-center gap-1.5 text-[11px] leading-relaxed">
      <span
        aria-hidden
        className={done ? "text-[var(--long-ink)]" : "text-[var(--muted)]"}
      >
        {done ? "✓" : "·"}
      </span>
      <span className={done ? "text-[var(--text)]" : "text-[var(--muted)]"}>{label}</span>
      {detail && <span className="tabular text-[var(--muted)]">{detail}</span>}
    </span>
  );
}

function ContractSummary({ contract }: { contract: AiTradeResumePreview["contract"] }) {
  return (
    <div className="flex flex-wrap gap-x-3 gap-y-1 rounded border border-[var(--border)] px-2 py-1.5 text-[11px] text-[var(--muted)]">
      <span>周期 {contract.bar}</span>
      <span>标的 {contract.instIds.join("、")}</span>
      {contract.totalBudgetUsd != null && <span>预算 {fmtMoney(contract.totalBudgetUsd)}</span>}
      {contract.leverage != null && <span>杠杆 {fmtNum(contract.leverage)}x</span>}
      {contract.stopLossPct != null && (
        <span>单仓止损 {fmtNum(contract.stopLossPct * 100)}%</span>
      )}
      {contract.takeProfitPct != null && (
        <span>单仓止盈 {fmtNum(contract.takeProfitPct * 100)}%</span>
      )}
      {contract.strategyNames.length > 0 && <span>策略 {contract.strategyNames.join("、")}</span>}
    </div>
  );
}

/** 装饰性标签（判定性说明走后端 `message`）。 */
const VERDICT_LABEL: Record<AiTradeDriftVerdict, string> = {
  matched: "与暂停时一致",
  flat_ok: "无持仓",
  partially_closed: "离线期间部分平仓",
  closed_offline: "离线期间已被平掉",
  size_increased: "持仓变大",
  side_flipped: "方向不符",
  foreign_position: "不是本会话的持仓",
  opened_offline: "离线期间被外部开仓",
  unverifiable_identity: "无法确认持仓身份",
  unknown_order: "存在来路不明的挂单",
  unreadable: "读不到持仓",
};

/**
 * 阻断码 → 中文（装饰性映射，同 `VERDICT_LABEL`）。
 *
 * 后端的 `blockedReason` 是一个**稳定语义码**，不是给人读的句子；不映射就会把
 * `AI_TRADE_RESUME_BACKSTOP_MISSING` 原样糊到用户脸上。未知码回落原文——宁可露出一个码，
 * 也不能因为映射缺一格就把阻断原因整条吞掉。
 */
const BLOCKED_REASON_LABEL: Record<string, string> = {
  AI_TRADE_RESUME_BACKSTOP_MISSING:
    "本机要求「带仓续跑必须已有交易所侧兜底」，而这个标的当前未确认覆盖：请先补挂交易所侧止损再继续。",
};

const READY: AiTradeDriftVerdict[] = ["matched", "flat_ok"];
const CONFIRM: AiTradeDriftVerdict[] = ["partially_closed", "closed_offline"];

function toneFor(verdict: AiTradeDriftVerdict): "long" | "accent" | "short" {
  if (READY.includes(verdict)) return "long";
  return CONFIRM.includes(verdict) ? "accent" : "short";
}

function borderFor(verdict: AiTradeDriftVerdict): string {
  if (READY.includes(verdict)) return "border-[var(--border)]";
  return CONFIRM.includes(verdict) ? "border-[var(--accent)]" : "border-[var(--short)]";
}
