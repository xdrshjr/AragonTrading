"use client";

import { useEffect, useMemo, useState } from "react";
import { Badge, Button, Input, Modal } from "@/components/ui";
import { fmtTime } from "@/lib/format";
import { inferInstType } from "@/lib/instruments";
import { MULTI_SAMPLE_MAX_SYMBOLS, splitInstId } from "@/lib/multiSample";

const TITLE_ID = "multi-sample-modal-title";

const INST_TYPE_LABEL: Record<string, string> = {
  SPOT: "现货",
  SWAP: "永续",
  FUTURES: "交割",
};

/**
 * 多样本回测勾选弹窗（multi-sample-backtest §6.1，复用 Modal 八项无障碍原语）。
 *
 * 罗列全部自选标的供勾选；默认勾选当前标的（仅当其在自选内，R-P2-2）；支持搜索 / 全选 / 清空 /
 * 计数 / 上限禁用 / 空态。「确定」时回调 `onConfirm([...selected])`，由调用方乐观关闭。
 */
export default function MultiSampleBacktestModal({
  open,
  onClose,
  watchlist,
  currentInstId,
  bar,
  startTs,
  endTs,
  strategyName,
  busy,
  onConfirm,
}: {
  open: boolean;
  onClose: () => void;
  watchlist: string[];
  currentInstId: string;
  bar: string;
  startTs: number;
  endTs: number;
  strategyName: string;
  busy?: boolean;
  onConfirm: (instIds: string[]) => void;
}) {
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [query, setQuery] = useState("");

  // 每次打开重置：默认勾选当前标的（仅当其在自选内，R-P2-2），并清空搜索。
  useEffect(() => {
    if (!open) return;
    setSelected(watchlist.includes(currentInstId) ? new Set([currentInstId]) : new Set());
    setQuery("");
  }, [open, currentInstId, watchlist]);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return watchlist;
    return watchlist.filter((id) => id.toLowerCase().includes(needle));
  }, [watchlist, query]);

  const count = selected.size;
  const overLimit = count > MULTI_SAMPLE_MAX_SYMBOLS;
  const isEmpty = watchlist.length === 0;
  const confirmDisabled = count === 0 || overLimit || Boolean(busy);
  const atCap = count >= MULTI_SAMPLE_MAX_SYMBOLS;

  const toggle = (instId: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(instId)) next.delete(instId);
      else next.add(instId);
      return next;
    });

  const selectAllVisible = () =>
    setSelected(new Set(filtered.slice(0, MULTI_SAMPLE_MAX_SYMBOLS)));

  const confirm = () => {
    if (confirmDisabled) return;
    onConfirm([...selected]);
  };

  return (
    <Modal open={open} onClose={onClose} labelledBy={TITLE_ID} className="max-w-lg">
      <header className="flex items-start justify-between gap-3 border-b border-[var(--border)] px-5 py-4">
        <div className="min-w-0">
          <h2 id={TITLE_ID} className="text-sm font-semibold text-[var(--text)]">
            多样本回测
          </h2>
          <p className="mt-0.5 truncate text-[11px] text-[var(--muted)]">
            {strategyName} · {bar} · {fmtTime(startTs)} → {fmtTime(endTs)}
          </p>
        </div>
        <Button variant="ghost" size="sm" aria-label="关闭" onClick={onClose}>
          ✕
        </Button>
      </header>

      {isEmpty ? (
        <div className="flex flex-col items-center gap-1 px-5 py-10 text-center">
          <p className="text-sm text-[var(--text)]">自选列表为空</p>
          <p className="text-[11px] text-[var(--muted)]">请先在左侧标的栏添加自选，再运行多样本回测。</p>
        </div>
      ) : (
        <>
          <div className="flex items-center gap-2 border-b border-[var(--border)] px-5 py-3">
            <Input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="搜索标的"
              className="h-8 flex-1 py-1 text-xs"
            />
            <Button variant="ghost" size="sm" onClick={selectAllVisible}>
              全选
            </Button>
            <Button variant="ghost" size="sm" onClick={() => setSelected(new Set())}>
              清空
            </Button>
            <Badge tone="accent">
              已选 {count} / {watchlist.length}
            </Badge>
          </div>

          <div className="max-h-[46vh] flex-1 overflow-y-auto px-3 py-2">
            {filtered.length === 0 ? (
              <p className="px-2 py-6 text-center text-[11px] text-[var(--muted)]">无匹配标的</p>
            ) : (
              <ul className="flex flex-col gap-0.5">
                {filtered.map((instId) => {
                  const checked = selected.has(instId);
                  const { base, quote } = splitInstId(instId);
                  const type = inferInstType(instId);
                  const isCurrent = instId === currentInstId;
                  return (
                    <li key={instId}>
                      <label
                        className="flex cursor-pointer items-center gap-2.5 rounded-[8px] px-2 py-2 hover:bg-[var(--surface-2)]"
                      >
                        <input
                          type="checkbox"
                          checked={checked}
                          disabled={!checked && atCap}
                          onChange={() => toggle(instId)}
                          className="h-4 w-4 accent-[var(--accent)] disabled:cursor-not-allowed disabled:opacity-40"
                        />
                        <span className="min-w-0 flex-1 text-xs">
                          <span className="font-medium text-[var(--text)]">{base}</span>
                          <span className="text-[var(--muted)]"> / {quote}</span>
                        </span>
                        <Badge tone="neutral">{INST_TYPE_LABEL[type] ?? type}</Badge>
                        {isCurrent && <Badge tone="accent">当前</Badge>}
                      </label>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>

          <footer className="flex items-center justify-between gap-3 border-t border-[var(--border)] px-5 py-3">
            <span className="text-[11px] text-[var(--muted)]">
              已选 {count} 个标的
              {overLimit && (
                <span className="ml-1 text-[var(--short)]">最多 {MULTI_SAMPLE_MAX_SYMBOLS} 个</span>
              )}
            </span>
            <div className="flex gap-2">
              <Button variant="ghost" size="sm" onClick={onClose}>
                取消
              </Button>
              <Button variant="primary" size="sm" disabled={confirmDisabled} onClick={confirm}>
                {busy ? "回测中…" : "确定（开始回测）"}
              </Button>
            </div>
          </footer>
        </>
      )}
    </Modal>
  );
}
