"use client";

import { useCallback, useEffect, useState } from "react";
import clsx from "clsx";
import { absoluteTime, relativeTime } from "@/lib/intel";
import { sourceStateLabel } from "@/lib/intelClosedLoop";
import { intelApi } from "@/lib/intelApi";
import type { IntelFeedbackRow, IntelSourceRow } from "@/lib/intelTypes";
import IntelBlockSourceDialog from "./IntelBlockSourceDialog";

/**
 * 「信源」页签（ai-trade-intel-closed-loop §3.7.3）——回答「这些消息都是谁说的」。
 *
 * 上半是**当前库存**的域名聚合（域名 / 条数 / 最近一次 / 状态），每行可 block / trust / 取消；
 * 下半是全局与本标的的黑白名单清单，可逐条撤销。
 *
 * 两条纪律：
 * - **拉黑走确认框**（含受影响条数与作用域勾选）；trust 与撤销是可逆动作，一次点击即可。
 * - **撤销拉黑不复活任何条目**：归档是一次带理由的状态迁移，自动复活会让用户在两次点击之间
 *   看到条目数跳来跳去。恢复路径是「已归档」页签里那颗按钮，文案里明说。
 */
export default function IntelSourcesTab({
  instId,
  onChanged,
}: {
  instId: string;
  onChanged: () => void;
}) {
  const [rows, setRows] = useState<IntelSourceRow[]>([]);
  const [feedback, setFeedback] = useState<IntelFeedbackRow[]>([]);
  const [notice, setNotice] = useState("");
  const [blocking, setBlocking] = useState<string>("");
  const [nowMs, setNowMs] = useState(() => Date.now());

  const load = useCallback(async () => {
    try {
      const [sources, list] = await Promise.all([
        intelApi.sources(instId),
        intelApi.feedback(instId),
      ]);
      setRows(sources.rows);
      setFeedback(list.rows);
      setNowMs(Date.now());
    } catch (err) {
      setNotice(err instanceof Error ? err.message : "读取信源清单失败");
    }
  }, [instId]);

  useEffect(() => {
    void load();
  }, [load]);

  /**
   * 跑一个写动作并重取两张清单。
   *
   * `task` **可以返回自己的回执文案**，返回了就用它；`done` 只是回落。这不是可有可无的灵活性：
   * 拉黑的回执里有「N 条情报已归档」这个只有服务端算得出来的数，而它正是那个确认框承诺过要给
   * 用户的后果——先前这里无条件写 `done`，把那句话在同一个 tick 里覆盖掉了。
   */
  const run = async (task: () => Promise<unknown>, done: string) => {
    setNotice("");
    try {
      const said = await task();
      setNotice(typeof said === "string" && said ? said : done);
      await load();
      onChanged();
    } catch (err) {
      setNotice(err instanceof Error ? err.message : "操作失败，请稍后重试");
    }
  };

  return (
    <div className="flex flex-col gap-3 px-4 py-3" data-testid="intel-sources-tab">
      <section className="flex flex-col gap-1">
        <h3 className="text-[11px] font-medium text-[var(--text)]">
          当前库存的来源
          <span className="ml-1.5 text-[10px] font-normal text-[var(--muted)]">
            拉黑之后的采集与核查都会跳过它
          </span>
        </h3>
        {rows.length === 0 ? (
          <p className="text-[11px] text-[var(--muted)]">本标的的情报库里还没有任何来源。</p>
        ) : (
          <ul className="flex flex-col gap-1">
            {rows.map((row) => (
              <li
                key={row.domain}
                className="flex items-center justify-between gap-2 rounded-[10px] border border-[var(--border)] bg-[var(--bg)] px-2.5 py-1.5"
              >
                <span className="min-w-0 flex-1 truncate text-[11px] text-[var(--text)]">
                  {row.domain}
                  <span className="ml-1.5 text-[10px] text-[var(--muted)]" title={absoluteTime(row.lastTs)}>
                    {row.itemCount} 条 · 最近 {relativeTime(row.lastTs, nowMs)}
                  </span>
                  {row.state && (
                    <span
                      className={clsx(
                        "ml-1.5 rounded-full border px-1 py-px text-[10px]",
                        row.state === "blocked"
                          ? "border-[var(--danger)] text-[var(--danger)]"
                          : "border-[var(--ok)] text-[var(--ok)]",
                      )}
                    >
                      {sourceStateLabel(row.state)}
                    </span>
                  )}
                  {/* 作用域是徽章**旁边**的一句话而不是徽章的一部分：一条全局记录与一条只对
                      本标的生效的记录，撤销之后的后果完全不同，而它们此前长得一模一样。
                      （徽章本身保持只写状态词，那是它被读取与断言的口径。） */}
                  {row.state && !row.stateInstId && (
                    <span className="ml-1 text-[10px] text-[var(--muted)]">（全局）</span>
                  )}
                </span>
                <span className="flex shrink-0 items-center gap-1">
                  {row.state === "" && (
                    <>
                      <RowAction text="不再采纳" danger onClick={() => setBlocking(row.domain)} />
                      <RowAction
                        text="更信任"
                        onClick={() =>
                          void run(
                            () =>
                              intelApi.writeFeedback({
                                key: row.domain,
                                action: "trust",
                                instId,
                              }),
                            `已把 ${row.domain} 标记为更信任的来源`,
                          )
                        }
                      />
                    </>
                  )}
                  {row.state !== "" && (
                    <RowAction
                      text="撤销"
                      onClick={() =>
                        void run(
                          // ★ 必须按 `row.stateInstId` 发：`clear_intel_feedback` 按
                          // `(key, instId)` 精确删，硬写 `instId` 会让一条全局记录撤销不掉，
                          // 而那次点击不会报任何错。
                          () => intelApi.clearFeedback(row.domain, row.stateInstId),
                          `已撤销对 ${row.domain} 的取舍（已归档的条目不会自动复活）`,
                        )
                      }
                    />
                  )}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="flex flex-col gap-1">
        <h3 className="text-[11px] font-medium text-[var(--text)]">黑白名单</h3>
        {feedback.length === 0 ? (
          <p className="text-[11px] text-[var(--muted)]">还没有任何来源取舍记录。</p>
        ) : (
          <ul className="flex flex-col gap-1">
            {feedback.map((row) => (
              <li
                key={row.id}
                className="flex items-center justify-between gap-2 rounded-[10px] border border-[var(--border)] bg-[var(--bg)] px-2.5 py-1.5"
              >
                <span className="min-w-0 flex-1 truncate text-[11px] text-[var(--text)]">
                  {row.key}
                  <span className="ml-1.5 text-[10px] text-[var(--muted)]">
                    {row.action === "block" ? "已拉黑" : "更信任"} ·{" "}
                    {row.instId ? row.instId : "全局生效"}
                  </span>
                </span>
                <RowAction
                  text="撤销"
                  onClick={() =>
                    void run(
                      () => intelApi.clearFeedback(row.key, row.instId),
                      `已撤销对 ${row.key} 的取舍（已归档的条目不会自动复活）`,
                    )
                  }
                />
              </li>
            ))}
          </ul>
        )}
      </section>

      {notice && <p className="text-[10px] text-[var(--muted)]">{notice}</p>}

      <IntelBlockSourceDialog
        open={blocking !== ""}
        domain={blocking}
        instId={instId}
        onCancel={() => setBlocking("")}
        onConfirm={(scopeInstId) => {
          const domain = blocking;
          setBlocking("");
          void run(async () => {
            const res = await intelApi.writeFeedback({
              key: domain,
              action: "block",
              instId: scopeInstId,
            });
            return res.archived > 0
              ? `已不再采纳 ${domain}；${res.archived} 条情报已归档，可在「已归档」中恢复`
              : `已不再采纳 ${domain}`;
          }, `已不再采纳 ${domain}`);
        }}
      />
    </div>
  );
}

function RowAction({
  text,
  danger,
  onClick,
}: {
  text: string;
  danger?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={clsx(
        "rounded px-1.5 py-0.5 text-[10px] outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]",
        danger
          ? "text-[var(--short-ink)] hover:bg-[var(--surface-2)]"
          : "text-[var(--muted)] hover:bg-[var(--surface-2)] hover:text-[var(--text)]",
      )}
    >
      {text}
    </button>
  );
}
