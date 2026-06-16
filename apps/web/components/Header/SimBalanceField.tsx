"use client";

import clsx from "clsx";
import { useEffect, useRef, useState } from "react";
import { fmtMoney, parseMoneyInput } from "@/lib/format";
import { useAppStore } from "@/lib/store";

/**
 * 模拟资金内联可编辑字段（支柱 A / A3）。
 *
 * 显示态点击进入编辑；Enter / 失焦提交（解析 + 夹取由 store.setSimBalance 负责），Esc 取消。
 * 存在活跃会话时仍允许编辑（满足「随时调整」），但提示「下次启动生效」——不热改已锁定的 broker（R4）。
 */
export default function SimBalanceField() {
  const { simBalance, setSimBalance, paperSessionId } = useAppStore();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [rejected, setRejected] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (editing) inputRef.current?.select();
  }, [editing]);

  const begin = () => {
    setRejected(false);
    setDraft(String(simBalance));
    setEditing(true);
  };

  const commit = () => {
    const parsed = parseMoneyInput(draft);
    if (parsed === null) {
      // 非法输入：回退原值并以红框轻提示（不静默吞掉）。
      setRejected(true);
      setEditing(false);
      window.setTimeout(() => setRejected(false), 600);
      return;
    }
    setSimBalance(parsed);
    setEditing(false);
  };

  const cancel = () => setEditing(false);

  if (editing) {
    return (
      <input
        ref={inputRef}
        inputMode="decimal"
        aria-label="模拟资金"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") commit();
          else if (e.key === "Escape") cancel();
        }}
        className="w-28 rounded-[8px] border border-[var(--accent)] bg-[var(--surface)] px-2 py-1 text-xs tabular text-[var(--text)] outline-none focus:border-[var(--accent)]"
      />
    );
  }

  return (
    <button
      type="button"
      onClick={begin}
      title={
        paperSessionId
          ? "调整模拟资金（将于下次启动实时模拟时生效）"
          : "点击编辑模拟资金总额"
      }
      className={clsx(
        "group flex items-center gap-1.5 rounded-[8px] border bg-[var(--surface)] px-2 py-1 text-xs transition-colors duration-150 ease-smooth",
        rejected
          ? "border-[var(--short)]"
          : "border-[var(--border)] hover:border-[var(--accent)] focus:border-[var(--accent)]",
      )}
    >
      <span className="text-[var(--muted)]">模拟资金</span>
      <span className="tabular font-semibold text-[var(--text)]">{fmtMoney(simBalance)}</span>
      {paperSessionId && (
        <span className="text-[10px] text-[var(--muted)]">· 下次启动生效</span>
      )}
    </button>
  );
}
