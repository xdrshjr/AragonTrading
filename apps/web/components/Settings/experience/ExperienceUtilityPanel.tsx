"use client";

import { Badge, Button } from "@/components/ui";
import { formatNetBps, formatPercent, formatTStat, shortTime } from "@/lib/experience";
import type { ExperienceUtilityPage, ExperienceUtilityRow } from "@/lib/experienceTypes";

/**
 * 效用面板：「这条经验被喂进去之后，那些单后来怎么样」。
 *
 * **这是共现，不是因果**（不变式 J-5）。我们记录的是「注入了这几条之后，那一单的净额是多少」，
 * 并不知道模型是否真的采纳了其中任何一条。所以这一屏从标题到每一格都只说「注入后表现」
 * 与「与事实一致率」，不说「贡献」「带来」——一个用户会据以删掉经验的界面，措辞就是它的正确性。
 *
 * 三态用**颜色 + 形状 + 文字**三重编码（沿用仓库既有的可读墨水层纪律）：已失效的行左侧带一条
 * 竖线，不靠颜色单独承载语义。
 */
export default function ExperienceUtilityPanel({
  page,
  loading,
  onOpen,
  onRestore,
}: {
  page: ExperienceUtilityPage | null;
  loading: boolean;
  onOpen: (row: ExperienceUtilityRow) => void;
  onRestore: (row: ExperienceUtilityRow) => void;
}) {
  if (loading && !page) {
    return (
      <p className="px-4 py-6 text-center text-xs text-[var(--muted)]">读取效用读数…</p>
    );
  }
  if (!page || page.items.length === 0) {
    return (
      <p
        className="rounded-md border border-dashed border-[var(--border)] px-4 py-8 text-center text-xs text-[var(--muted)]"
        data-testid="experience-utility-empty"
      >
        还没有任何一条经验被喂进过一次真正开了仓的决策轮。跑几轮自动交易之后，这里会出现
        「注入之后那些单表现如何」的读数。
      </p>
    );
  }
  return (
    <div className="flex flex-col gap-2" data-testid="experience-utility-panel">
      <p className="text-[11px] leading-relaxed text-[var(--muted)]">
        共 {page.total} 条有注入样本 · 读数截至 {shortTime(page.asOfTs)}。
        一条经验被注入 {page.minSamples} 次以上、注入后的净额均值与它自己的结论
        <strong className="text-[var(--text)]">系统性相反</strong>
        且 |t| ≥ {page.tThreshold} 时会被自动静音。
        这是共现关系，不是因果——我们无法确认模型是否真的采纳了它。
      </p>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[780px] border-collapse text-left text-xs">
          <caption className="sr-only">经验的注入后表现（共现，不是因果）</caption>
          <thead>
            <tr className="border-b border-[var(--border)] text-[11px] text-[var(--muted)]">
              <th scope="col" className="px-2 py-2 font-medium">陈述</th>
              <th scope="col" className="px-2 py-2 text-right font-medium">注入次数</th>
              <th scope="col" className="px-2 py-2 text-right font-medium">注入后净额均值</th>
              <th scope="col" className="px-2 py-2 text-right font-medium">t</th>
              <th scope="col" className="px-2 py-2 text-right font-medium">与事实一致率</th>
              <th scope="col" className="px-2 py-2 font-medium">状态</th>
            </tr>
          </thead>
          <tbody>
            {page.items.map((row) => (
              <tr
                key={row.key}
                className="border-b border-[var(--border)] align-top hover:bg-[var(--surface-2)]"
                data-testid="experience-utility-row"
                // 形状编码：失效的行左侧一条竖线，不让颜色单独承载语义。
                style={row.ineffective
                  ? { borderLeft: "2px solid var(--warn)" }
                  : undefined}
              >
                <td className="max-w-[360px] px-2 py-2">
                  <button
                    type="button"
                    onClick={() => onOpen(row)}
                    className="text-left text-[var(--text)] underline-offset-2 hover:underline"
                  >
                    {row.label}
                  </button>
                </td>
                <td className="tabular px-2 py-2 text-right">{row.injected}</td>
                <td className="tabular px-2 py-2 text-right">
                  {formatNetBps(row.meanNetAfter)}
                </td>
                <td className="tabular px-2 py-2 text-right">{formatTStat(row.tAfter)}</td>
                <td className="tabular px-2 py-2 text-right">{formatPercent(row.agreement)}</td>
                <td className="whitespace-nowrap px-2 py-2">
                  {row.pinned && <Badge tone="accent">已置顶 · 不自动静音</Badge>}
                  {!row.pinned && row.muteReason === "ineffective" && (
                    <>
                      <Badge tone="neutral">⚠ 已自动静音</Badge>
                      <Button variant="ghost" size="sm" onClick={() => onRestore(row)}>
                        恢复启用
                      </Button>
                    </>
                  )}
                  {!row.pinned && row.muteReason !== "ineffective" && (
                    <span className="text-[var(--muted)]">
                      {row.ineffective ? "待下次重建时静音" : "启用中"}
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
