"use client";

import { useState } from "react";
import { Badge, Button, Modal } from "@/components/ui";
import { colorForReturn, fmtNum, fmtPrice, signed } from "@/lib/format";
import { tradingApi } from "@/lib/tradingApi";
import type { CloseAllResultRow, LivePosition } from "@/lib/tradingTypes";

/**
 * 一键市价全平二次确认弹窗（平仓 spec §2.6）：列出全部待平持仓，底部强制勾选 + **二次点击**才执行。
 *
 * 显式二次确认三道闸：勾选「我已知晓…不可撤销」→ 首次点击进入二次确认态（按钮变「再次确认 · 立即全平」）
 * → 再次点击才真正调 `tradingApi.closeAll`。逐仓结果诚实展示，失败行标红、绝不汇报「全部成功」（R10）。
 */
export default function MarketCloseAllConfirm({
  open,
  onClose,
  positions,
  accountId,
  onClosed,
}: {
  open: boolean;
  onClose: () => void;
  positions: LivePosition[];
  accountId?: number;
  onClosed: () => void;
}) {
  const [acknowledged, setAcknowledged] = useState(false);
  const [armed, setArmed] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [results, setResults] = useState<CloseAllResultRow[] | null>(null);

  const close = () => {
    setAcknowledged(false);
    setArmed(false);
    setError(null);
    setResults(null);
    onClose();
  };

  const handleConfirm = async () => {
    // 首次点击仅进入二次确认态；再次点击才真正执行（显式二次确认，§2.6）。
    if (!armed) {
      setArmed(true);
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const res = await tradingApi.closeAll(accountId);
      setResults(res.results);
      onClosed();
    } catch (e) {
      setError((e as Error).message || "一键全平失败");
    } finally {
      setSubmitting(false);
      setArmed(false);
    }
  };

  const resultByInst = new Map((results ?? []).map((r) => [`${r.instId}-${r.posSide}`, r]));

  return (
    <Modal open={open} onClose={close} labelledBy="close-all-title" className="max-w-lg">
      <div className="flex items-center justify-between border-b border-[var(--border)] px-5 py-3.5">
        <h2 id="close-all-title" className="text-sm font-semibold text-[var(--text)]">
          一键市价全平
        </h2>
        <Badge tone="short">{fmtNum(positions.length, 0)} 笔持仓</Badge>
      </div>

      <div className="flex min-h-0 flex-col gap-3 overflow-auto px-5 py-4">
        <p className="text-[11px] leading-relaxed text-[var(--muted)]">
          将对以下全部持仓按当前市价逐仓平掉，结果不可撤销，并自动撤销相关合约的未成交挂单。
        </p>

        <table className="w-full text-xs">
          <thead className="text-[var(--muted)]">
            <tr className="text-left">
              <th className="py-1 pr-2 font-medium">合约</th>
              <th className="py-1 pr-2 font-medium">方向</th>
              <th className="py-1 pr-2 text-right font-medium">张数</th>
              <th className="py-1 pr-2 text-right font-medium">标记价</th>
              <th className="py-1 text-right font-medium">浮盈 / 结果</th>
            </tr>
          </thead>
          <tbody>
            {positions.map((p, i) => {
              const isLong = p.posSide === "long" || (p.posSide === "net" && (p.pos ?? 0) > 0);
              const r = resultByInst.get(`${p.instId}-${p.posSide}`);
              return (
                <tr key={`${p.instId}-${p.posSide}-${i}`} className="border-t border-[var(--border)]">
                  <td className="py-1.5 pr-2 font-medium text-[var(--text)]">{p.instId}</td>
                  <td className="py-1.5 pr-2">
                    <Badge tone={isLong ? "long" : "short"}>{isLong ? "多" : "空"}</Badge>
                  </td>
                  <td className="py-1.5 pr-2 text-right tabular">{fmtNum(Math.abs(p.pos ?? 0), 0)}</td>
                  <td className="py-1.5 pr-2 text-right tabular">{fmtPrice(p.last)}</td>
                  <td className="py-1.5 text-right tabular">
                    {r ? (
                      <span className={r.ok ? "text-[var(--long-ink)]" : "text-[var(--short-ink)]"}>
                        {r.ok ? "已提交" : `失败：${r.code ?? "未知"}`}
                      </span>
                    ) : (
                      <span style={{ color: colorForReturn(p.upl ?? 0) }}>
                        {p.upl != null ? signed(p.upl) : "—"}
                      </span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>

        {!results && (
          <label className="flex items-start gap-2 text-xs leading-relaxed text-[var(--text)]">
            <input
              type="checkbox"
              checked={acknowledged}
              disabled={submitting}
              onChange={(e) => setAcknowledged(e.target.checked)}
              aria-label="我已知晓将以市价一键平掉以上全部持仓"
              className="mt-0.5 h-4 w-4 shrink-0 accent-[var(--short)]"
            />
            <span>
              我已知晓将以<strong className="text-[var(--short-ink)]">市价一键平掉以上全部持仓</strong>
              ，结果不可撤销。
            </span>
          </label>
        )}

        {error && (
          <p role="alert" className="text-[11px] font-medium text-[var(--short-ink)]">
            {error}
          </p>
        )}
        {results && (
          <p role="status" className="text-[11px] font-medium text-[var(--text)]">
            已提交 {fmtNum(results.filter((r) => r.ok).length, 0)} 笔，失败{" "}
            {fmtNum(results.filter((r) => !r.ok).length, 0)} 笔（持仓将于数秒内刷新）。
          </p>
        )}

        <div className="flex items-center justify-end gap-2">
          <Button variant="ghost" size="sm" onClick={close} disabled={submitting}>
            {results ? "完成" : "取消"}
          </Button>
          {!results && (
            <Button
              variant="primary"
              size="sm"
              onClick={handleConfirm}
              disabled={!acknowledged || submitting || positions.length === 0}
              className={armed ? "!bg-[var(--short)]" : undefined}
            >
              {submitting ? "提交中…" : armed ? "再次确认 · 立即全平" : "确认市价全平"}
            </Button>
          )}
        </div>
      </div>
    </Modal>
  );
}
