"use client";

import { useCallback, useId, useState } from "react";
import { Badge, Button, Modal } from "@/components/ui";
import { aiTradeApi } from "@/lib/aiTradeApi";
import type { AiTradeGroupState } from "@/lib/aiTradeTypes";
import { fmtNum } from "@/lib/format";

/**
 * 暂停确认框（ai-trade-pause-resume §3.9.3）。
 *
 * HCI 三条（需求明确要求「符合人机交互最佳实践」）：
 * - **可逆性**：暂停可逆，故默认选项就是「保留持仓暂停」，一次点击即可完成；只有**不可逆**的
 *   「平仓后暂停」需要用户主动切过去（它会实现一次盈亏）。
 * - **风险前置**：带仓时把「关机期间谁在保护这个仓位」写在**确认之前**，不是回执里——用户正要
 *   据此决定关不关电脑。兜底未覆盖时给一条「立即补挂」直连（复用既有 `repairBackstop`，不新增端点）。
 * - **绝不用 `window.confirm`**：走既有 `Modal` 体系（focus-trap / Esc / portal）。
 */
export default function AiTradePauseDialog({
  open,
  group,
  submitting,
  error,
  onClose,
  onConfirm,
}: {
  open: boolean;
  group: AiTradeGroupState | null;
  submitting: boolean;
  error?: string | null;
  onClose: () => void;
  onConfirm: (opts: { holdPosition: boolean }) => void;
}) {
  const titleId = useId();
  const [holdPosition, setHoldPosition] = useState(true);
  const members = group ? Object.values(group.members) : [];
  const holding = members.filter((m) => !!m.position);

  return (
    <Modal open={open} onClose={onClose} labelledBy={titleId} className="max-w-lg">
      <div className="flex flex-col gap-3 p-5" data-testid="ai-trade-pause-dialog">
        <h2 id={titleId} className="text-sm font-semibold text-[var(--text)]">
          暂停 AI 自动交易
        </h2>
        <p className="text-[12px] leading-relaxed text-[var(--muted)]">
          暂停后不再开新仓，但盯市、保护性平仓、会话止盈止损与交易所侧兜底核实照常运行。
          关闭软件也没关系：重新打开并联网后，点「继续」即可用同一份契约接着跑。
        </p>

        <fieldset className="flex flex-col gap-2">
          <legend className="sr-only">持仓归属</legend>
          <label className="flex cursor-pointer items-start gap-2 rounded border border-[var(--border)] px-2.5 py-2">
            <input
              type="radio"
              name="ai-trade-pause-hold"
              checked={holdPosition}
              onChange={() => setHoldPosition(true)}
              className="mt-0.5"
            />
            <span className="flex flex-col gap-0.5">
              <span className="text-[12px] font-medium text-[var(--text)]">保留持仓暂停</span>
              <span className="text-[11px] text-[var(--muted)]">
                不平仓、不付这一趟的手续费与滑点；持仓由交易所侧止损与本地保护层继续看着。
              </span>
            </span>
          </label>
          <label className="flex cursor-pointer items-start gap-2 rounded border border-[var(--border)] px-2.5 py-2">
            <input
              type="radio"
              name="ai-trade-pause-hold"
              checked={!holdPosition}
              onChange={() => setHoldPosition(false)}
              className="mt-0.5"
            />
            <span className="flex flex-col gap-0.5">
              <span className="text-[12px] font-medium text-[var(--text)]">平仓后暂停</span>
              <span className="text-[11px] text-[var(--muted)]">
                先平掉本会话的自管持仓再暂停 —— 会当场实现一次盈亏，并付一次双边成本。
              </span>
            </span>
          </label>
        </fieldset>

        {holding.length > 0 && (
          <div className="flex flex-col gap-1.5" data-testid="ai-trade-pause-positions">
            <span className="text-[10px] font-semibold uppercase tracking-wide text-[var(--muted)]">
              当前持仓与兜底覆盖
            </span>
            {holding.map((member) => (
              <MemberBackstopRow
                key={member.instId}
                groupId={group?.groupId ?? ""}
                instId={member.instId}
                side={String(member.position?.side ?? "")}
                size={Number(member.position?.size ?? 0)}
                backstop={(member.session?.disciplines?.rows ?? []).find(
                  (row) => row.key === "exchangeBackstop",
                )}
              />
            ))}
          </div>
        )}

        {error && (
          <p className="text-[11px] leading-relaxed text-[var(--short-ink)]" role="alert">
            {error}
          </p>
        )}

        <div className="mt-1 flex items-center justify-end gap-2">
          <Button variant="ghost" size="sm" onClick={onClose} disabled={submitting}>
            取消
          </Button>
          <Button
            variant={holdPosition ? "primary" : "danger"}
            size="sm"
            disabled={submitting}
            onClick={() => onConfirm({ holdPosition })}
          >
            {submitting ? "正在暂停…" : holdPosition ? "保留持仓并暂停" : "平仓后暂停"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}

/**
 * 单成员的「仓位 + 兜底覆盖」行，兜底未覆盖时给「立即补挂」直连。
 *
 * 三态按钮沿用 `NakedBackstopAlert` 的既有范式；文案只复述后端那一行的 `threshold`/`hint`——
 * 两处各写一遍就是两份口径，而这一条是安全告警，措辞失准的代价最大。
 */
function MemberBackstopRow({
  groupId,
  instId,
  side,
  size,
  backstop,
}: {
  groupId: string;
  instId: string;
  side: string;
  size: number;
  backstop?: { armed?: boolean; threshold?: string | null } | null;
}) {
  const [pending, setPending] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const covered = !!backstop?.armed;
  const repair = useCallback(() => {
    setPending(true);
    setResult(null);
    aiTradeApi
      .repairBackstop(groupId, instId)
      .then(() => setResult("补挂请求已发出，正在向交易所核实"))
      .catch(() => setResult("补挂失败，请到实盘面板手动处理"))
      .finally(() => setPending(false));
  }, [groupId, instId]);

  return (
    <div className="flex flex-wrap items-center gap-2 rounded border border-[var(--border)] px-2 py-1.5">
      <span className="text-[11px] font-medium text-[var(--text)]">{instId}</span>
      <span className="tabular text-[11px] text-[var(--muted)]">
        {side === "long" ? "多头" : side === "short" ? "空头" : side} {fmtNum(size)} 张
      </span>
      <Badge tone={covered ? "long" : "short"}>
        {covered ? "交易所侧兜底已覆盖" : "无持久化兜底"}
      </Badge>
      {backstop?.threshold && (
        <span className="truncate text-[10px] text-[var(--muted)]">{backstop.threshold}</span>
      )}
      {!covered && (
        <button
          type="button"
          onClick={repair}
          disabled={pending}
          className="rounded border border-[var(--short)] px-1.5 py-px text-[10px] text-[var(--short-ink)] transition-colors hover:bg-[var(--short)] hover:text-[var(--bg)] disabled:opacity-50"
        >
          {pending ? "补挂中…" : "立即补挂"}
        </button>
      )}
      {result && <span className="truncate text-[10px] text-[var(--muted)]">{result}</span>}
    </div>
  );
}
