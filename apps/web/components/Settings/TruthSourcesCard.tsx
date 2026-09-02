"use client";

import { useCallback, useEffect, useState } from "react";
import { Button, Card } from "@/components/ui";
import { aiTradeApi } from "@/lib/aiTradeApi";
import { useAppStore } from "@/lib/store";
import {
  TRUTH_STATE_DOT,
  TRUTH_STATE_LABEL,
  summarizeTruthSources,
  type TruthSourceRow,
} from "@/lib/truthSources";

/**
 * 「真值源」卡片（ai-trade-silent-degradation-repair 轴 E · E-2）。
 *
 * 它回答的是本轮那句一句话结论所引出的唯一产品问题：**这个系统里每一条「向外界要真话」的通路，
 * 现在到底在不在说真话？** 四条通路、四个 100% 失败、三个星期、零界面提示——轴 E 就是那个提示。
 *
 * 七条逐条可验收的设计要点（spec §九 E-2）：
 *
 * 1. **默认折叠为一行摘要**。一张常驻的四行诊断表会很快变成背景噪声；默认状态必须是「一切正常
 *    时几乎不占视觉预算」。
 * 2. **状态不靠颜色单独承载**：每行是「色点 + 文字标签 + 读数」。色觉障碍与截图分享两个场景
 *    都要能读。
 * 3. **`reason` 是一句人话，不是一个码**。`51000` 这样的原始码只出现在展开后的补充行里。
 * 4. **每一行的 `action` 都必须真的点得下去**；没有可行动作时 `action` 为 `null`，界面上就
 *    **不画按钮**——一个点了没反应的按钮比没有按钮更糟。
 * 5. **文字排版遵循既有的《工作台文字零折行契约》**：读数整块省略号，`reason` 这类必须读全的
 *    散文显式允许换行。
 * 6. 文案由后端单点装配，前端一个字都不拼（避免「同一句话两处装配、必然漂移」）。
 * 7. **`unknown` 与 `degraded` 分开**。「没在跑所以不知道」和「在跑但坏了」是两件事。
 */
export default function TruthSourcesCard() {
  // 三态而不是两态：`undefined` = 还在取、`null` = 取不到（总闸关 ⇒ 路由不注册 ⇒ 404，
  // 或后端更老）、数组 = 有读数。把前两者压成同一个 `null` 会让「正在加载」与「这个功能被
  // 关掉了」在界面上长得一模一样——那正是本轮在讲的那种失效。
  const [rows, setRows] = useState<TruthSourceRow[] | null | undefined>(undefined);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const setSection = useAppStore((s) => s.setSettingsSection);

  const load = useCallback(() => {
    aiTradeApi
      .truthSources()
      .then((wire) => setRows(wire.sources ?? []))
      // 端点不存在（总闸关 ⇒ 路由不注册 ⇒ 404）或后端更老 ⇒ 整卡不渲染。
      .catch(() => setRows(null));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const onAction = useCallback(
    async (row: TruthSourceRow) => {
      const action = row.action;
      if (!action || busy) return;
      if (action.kind === "settings") {
        setSection(action.target || "intel-source");
        return;
      }
      setBusy(true);
      setNotice(null);
      try {
        await aiTradeApi.refreshFeeRates();
        setNotice("已重新向交易所查询费率档位");
        load();
      } catch (err) {
        // 分组未在跑时后端回 409，那是一个**正确**的回答，如实转述而不是假装成功。
        setNotice(err instanceof Error ? err.message : "重取失败");
      } finally {
        setBusy(false);
      }
    },
    [busy, load, setSection],
  );

  if (rows === undefined) return null;                 // 还在取：不闪一个空状态
  if (rows === null || rows.length === 0) {
    // **卡片本身不渲染**（总闸关时逐字节回退的那一条），但这一屏是设置中心的一个独立分区
    // ——点进来什么都没有会被读成「界面坏了」。故留一句话说明它为什么是空的。
    return (
      <p
        data-testid="truth-sources-unavailable"
        className="text-[11px] leading-relaxed text-[var(--muted)]"
      >
        真值源面板当前不可用：它可能被运维开关关掉了（AI_TRADE_TRUTH_SOURCES_ENABLED=false），
        或者后端版本还没有这个端点。
      </p>
    );
  }
  const summary = summarizeTruthSources(rows);

  return (
    <Card className="flex flex-col gap-2 p-4" data-testid="truth-sources-card">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center justify-between gap-2 text-left"
      >
        <span className="flex min-w-0 items-center gap-2">
          <span className="text-sm font-semibold text-[var(--text)]">真值源</span>
          <span
            data-testid="truth-sources-summary"
            className={
              summary.tone === "warn"
                ? "truncate text-[11px] text-[var(--accent-ink)]"
                : "truncate text-[11px] text-[var(--muted)]"
            }
          >
            {summary.text}
          </span>
        </span>
        <span className="shrink-0 text-[11px] text-[var(--muted)]">{open ? "收起" : "展开"}</span>
      </button>

      {!open && (
        <span className="text-[11px] leading-relaxed text-[var(--muted)]">
          每一条「向外界要真话」的通路现在到底在不在说真话：交易所费率、情报正文、账户配额、
          决策模型。展开看四行明细。
        </span>
      )}

      {open && (
        <div className="flex flex-col gap-2">
          {rows.map((row) => (
            <div
              key={row.key}
              data-testid={`truth-source-row-${row.key}`}
              data-state={row.state}
              className="flex flex-col gap-1 rounded-md border border-[var(--border)] bg-[var(--surface)] px-3 py-2"
            >
              <div className="flex items-center justify-between gap-2">
                <span className="flex min-w-0 items-center gap-1.5">
                  <span
                    aria-hidden
                    className="inline-block h-1.5 w-1.5 shrink-0 rounded-full"
                    style={{ background: TRUTH_STATE_DOT[row.state] }}
                  />
                  <span className="shrink-0 text-[12px] font-medium text-[var(--text)]">
                    {row.label}
                  </span>
                  {/* 文字标签与色点并列：状态绝不靠颜色单独承载。 */}
                  <span className="shrink-0 text-[10px] text-[var(--muted)]">
                    {TRUTH_STATE_LABEL[row.state]}
                  </span>
                </span>
                {row.action && (
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={busy}
                    onClick={() => onAction(row)}
                  >
                    {row.action.label}
                  </Button>
                )}
              </div>
              {/* 读数：整块省略号（零折行契约）。 */}
              <span className="truncate text-[11px] tabular text-[var(--text)]">
                {row.reading}
              </span>
              {/* 原因：必须读全的散文 ⇒ 显式允许换行。 */}
              {row.reason && (
                <span className="text-[11px] leading-relaxed text-[var(--muted)]">
                  {row.reason}
                </span>
              )}
            </div>
          ))}
          {notice && (
            <span data-testid="truth-sources-notice" className="text-[11px] text-[var(--muted)]">
              {notice}
            </span>
          )}
        </div>
      )}
    </Card>
  );
}
