"use client";

import { useId, useState } from "react";
import { Badge, Button, Modal } from "@/components/ui";
import type { AiTradeResumableMember } from "@/lib/aiTradeTypes";
import { fmtNum } from "@/lib/format";

/**
 * 结束确认框（ai-trade-pause-resume §3.9.3 / [P1-12]）。
 *
 * 「结束」是**不可逆**动作，且在**带仓**时会造出一个「本地已归档、交易所上仍在」的孤儿仓 ——
 * 而本轮的立论正是拿「今天已经存在孤儿持仓，只是没人告诉用户」来论证必要性，绝不能自己再造
 * 一个入口。故：
 * - **无仓** ⇒ 一次普通确认即可，`flatten` 恒 `false`；
 * - **带仓** ⇒ 弹窗**不给默认值**，强制二选一；选「保留持仓」时必须再勾一个确认，且**勾选态
 *   真的门控提交按钮**（同 RV-1）。
 *
 * `canFlatten=false`（冷续跑卡：软件重启后进程里只剩磁盘上的一份契约）⇒ **不提供「平仓」这一支**。
 * 那条路服务端做不到（没有 Executor 也没有暖机窗口，无从下单），给了它只会让用户点完之后拿到
 * 一句「已平仓」而仓位原样还在 —— 与其事后报错，不如一开始就把做不到的事说清楚。
 */
export default function AiTradeEndDialog({
  open,
  members,
  canFlatten = true,
  submitting,
  error,
  onClose,
  onConfirm,
}: {
  open: boolean;
  members: AiTradeResumableMember[];
  canFlatten?: boolean;
  submitting: boolean;
  error?: string | null;
  onClose: () => void;
  onConfirm: (opts: { flatten: boolean | null; acknowledgeOrphanPosition: boolean }) => void;
}) {
  const titleId = useId();
  // `null` = 尚未选择（带仓时**不给默认值**）。
  const [flatten, setFlatten] = useState<boolean | null>(null);
  const [acknowledged, setAcknowledged] = useState(false);
  // 「读不到」按带仓处理（保守极性，与服务端 S3 fail-closed 同源）。
  const holding = members.filter((m) => !!m.position || m.positionUnknown);
  const hasPosition = holding.length > 0;
  // 不能平仓时唯一的出路是「保留持仓」，故勾选确认是**必经**的一步（不是二选一之后的附加项）。
  const canSubmit = !submitting && (!hasPosition
    || (canFlatten ? flatten === true || (flatten === false && acknowledged) : acknowledged));

  return (
    <Modal open={open} onClose={onClose} labelledBy={titleId} className="max-w-lg">
      <div className="flex flex-col gap-3 p-5" data-testid="ai-trade-end-dialog">
        <h2 id={titleId} className="text-sm font-semibold text-[var(--text)]">
          结束这次 AI 自动交易
        </h2>

        {!hasPosition ? (
          <p className="text-[12px] leading-relaxed text-[var(--muted)]">
            该会话当前没有持仓，结束后将归档到历史会话，不可再继续。
          </p>
        ) : (
          <>
            <p className="text-[12px] leading-relaxed text-[var(--muted)]">
              该会话仍有持仓。结束是不可逆的，请明确选择这些持仓怎么处理：
            </p>
            <div className="flex flex-col gap-1.5" data-testid="ai-trade-end-positions">
              {holding.map((member) => (
                <span
                  key={member.instId}
                  className="flex flex-wrap items-center gap-2 rounded border border-[var(--border)] px-2 py-1.5 text-[11px]"
                >
                  <span className="font-medium text-[var(--text)]">{member.instId}</span>
                  <span className="tabular text-[var(--muted)]">
                    {member.positionUnknown
                      ? "持仓未能读取"
                      : `${member.position?.side === "long" ? "多头" : "空头"} ${fmtNum(
                          Number(member.position?.size ?? 0),
                        )} 张`}
                  </span>
                  <Badge tone={member.backstopAtPause?.covered ? "long" : "short"}>
                    {member.backstopAtPause?.covered
                      ? "关机前交易所侧止损已覆盖"
                      : "关机前未确认有兜底"}
                  </Badge>
                </span>
              ))}
            </div>
            {canFlatten ? (
              <fieldset className="flex flex-col gap-2">
                <legend className="sr-only">持仓处置</legend>
                <label className="flex cursor-pointer items-start gap-2 rounded border border-[var(--border)] px-2.5 py-2">
                  <input
                    type="radio"
                    name="ai-trade-end-flatten"
                    checked={flatten === true}
                    onChange={() => setFlatten(true)}
                    className="mt-0.5"
                  />
                  <span className="text-[12px] text-[var(--short)]">平掉这些持仓并结束</span>
                </label>
                <label className="flex cursor-pointer items-start gap-2 rounded border border-[var(--border)] px-2.5 py-2">
                  <input
                    type="radio"
                    name="ai-trade-end-flatten"
                    checked={flatten === false}
                    onChange={() => setFlatten(false)}
                    className="mt-0.5"
                  />
                  <span className="text-[12px] text-[var(--text)]">保留持仓，仅结束本地会话</span>
                </label>
              </fieldset>
            ) : (
              <p
                className="rounded border border-[var(--short)] px-2.5 py-2 text-[11px] leading-relaxed text-[var(--short)]"
                data-testid="ai-trade-end-cold-hint"
              >
                软件重启后这次会话已不在进程内，这里无法代你平仓：结束只会归档本地记录，持仓会
                原样留在交易所上。想平掉它们，请先点「继续」恢复会话再停止，或到实盘面板手动处理。
              </p>
            )}
            {(flatten === false || !canFlatten) && (
              <label className="flex cursor-pointer items-start gap-2 text-[11px] leading-relaxed text-[var(--text)]">
                <input
                  type="checkbox"
                  checked={acknowledged}
                  onChange={(e) => setAcknowledged(e.target.checked)}
                  className="mt-0.5"
                  aria-label="我知道这些持仓将不再由任何自动化管理"
                />
                <span>
                  我知道这些持仓将不再由任何自动化管理，我会自行在实盘面板处理。
                </span>
              </label>
            )}
          </>
        )}

        {error && (
          <p className="text-[11px] leading-relaxed text-[var(--short)]" role="alert">
            {error}
          </p>
        )}

        <div className="mt-1 flex items-center justify-end gap-2">
          <Button variant="ghost" size="sm" onClick={onClose} disabled={submitting}>
            取消
          </Button>
          <Button
            variant="danger"
            size="sm"
            disabled={!canSubmit}
            onClick={() =>
              onConfirm({
                // 无仓、或冷态下平仓这条路本就不存在 ⇒ 显式发 `false`：发 `null` 会被服务端的
                // 「带仓必须显式二选一」判成缺参数而 400，而用户面前根本没有那个选项可选。
                flatten: hasPosition && canFlatten ? flatten : false,
                acknowledgeOrphanPosition: acknowledged,
              })
            }
          >
            {submitting ? "正在结束…" : "结束并归档"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
