"use client";

import { Badge, Button } from "@/components/ui";
import {
  SOURCE_LABEL_ZH,
  describeSignature,
  formatNetBps,
  formatTStat,
  relativeTime,
  tierLabel,
} from "@/lib/experience";
import type { ExperienceNode } from "@/lib/experienceTypes";

/**
 * 列表视图 —— 图谱的**无障碍等价形态**。
 *
 * 只有图的界面对读屏用户不可用，而需求写的是「符合人机交互最佳实践」。两者**同数据源同筛选**
 * （后端 `lessons_view` 与 `graph_view` 共用 `_match`），故同一筛选下的条数在结构上不可能不一致。
 */
export default function ExperienceList({
  items,
  total,
  loading,
  onOpen,
  onPin,
  onMute,
  onDelete,
}: {
  items: ExperienceNode[];
  total: number;
  loading: boolean;
  onOpen: (node: ExperienceNode) => void;
  onPin: (node: ExperienceNode) => void;
  onMute: (node: ExperienceNode) => void;
  onDelete: (node: ExperienceNode) => void;
}) {
  if (!loading && items.length === 0) {
    return (
      <p
        className="rounded-md border border-dashed border-[var(--border)] px-4 py-8 text-center text-xs text-[var(--muted)]"
        data-testid="experience-list-empty"
      >
        当前筛选下没有经验。完成第一次自动交易后，这里会出现你的第一条。
      </p>
    );
  }
  return (
    <div className="flex flex-col gap-2" data-testid="experience-list">
      <p className="text-[11px] text-[var(--muted)]">
        共 {total} 条{items.length < total ? `，已显示前 ${items.length} 条` : ""}
      </p>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[860px] border-collapse text-left text-xs">
          <caption className="sr-only">交易经验列表（与图谱同数据源同筛选）</caption>
          <thead>
            <tr className="border-b border-[var(--border)] text-[11px] text-[var(--muted)]">
              <th scope="col" className="px-2 py-2 font-medium">陈述</th>
              <th scope="col" className="px-2 py-2 font-medium">档位</th>
              <th scope="col" className="px-2 py-2 font-medium">来源</th>
              <th scope="col" className="px-2 py-2 text-right font-medium">样本 n</th>
              <th scope="col" className="px-2 py-2 text-right font-medium">净额均值</th>
              <th scope="col" className="px-2 py-2 text-right font-medium">t</th>
              <th scope="col" className="px-2 py-2 font-medium">最近证据</th>
              <th scope="col" className="px-2 py-2 font-medium">操作</th>
            </tr>
          </thead>
          <tbody>
            {items.map((node) => (
              <tr
                key={node.id}
                className="border-b border-[var(--border)] align-top hover:bg-[var(--surface-2)]"
                data-testid="experience-list-row"
              >
                <td className="max-w-[380px] px-2 py-2">
                  <button
                    type="button"
                    onClick={() => onOpen(node)}
                    className="text-left text-[var(--text)] underline-offset-2 hover:underline"
                  >
                    {node.label}
                  </button>
                  <p className="mt-0.5 text-[10px] text-[var(--muted)]">
                    {describeSignature(node.signature as Record<string, string>)}
                    {node.scope === "global" ? " · 跨标的通用" : ""}
                  </p>
                </td>
                <td className="px-2 py-2">
                  <Badge tone={node.pinned ? "accent" : node.muted ? "neutral" : "neutral"}>
                    {tierLabel(node)}
                  </Badge>
                </td>
                <td className="px-2 py-2 text-[var(--muted)]">
                  {SOURCE_LABEL_ZH[node.source] ?? node.source}
                </td>
                <td className="tabular px-2 py-2 text-right">{node.support}</td>
                <td className="tabular px-2 py-2 text-right">
                  {formatNetBps((node.stats as Record<string, unknown>).meanNetBps)}
                </td>
                <td className="tabular px-2 py-2 text-right">
                  {formatTStat((node.stats as Record<string, unknown>).tStat)}
                </td>
                <td className="px-2 py-2 text-[var(--muted)]">{relativeTime(node.lastSeenTs)}</td>
                <td className="whitespace-nowrap px-2 py-2">
                  <Button variant="ghost" size="sm" onClick={() => onPin(node)}>
                    {node.pinned ? "取消置顶" : "置顶"}
                  </Button>
                  <Button variant="ghost" size="sm" onClick={() => onMute(node)}>
                    {node.muted ? "取消静音" : "静音"}
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => onDelete(node)}
                    // 统计经验删了会被下一次重建按 key 复活——那是个假动作，故直接禁用并说明。
                    disabled={node.source === "stat"}
                    title={node.source === "stat"
                      ? "统计经验不能删除（重建会复活它）；请改用「静音」"
                      : undefined}
                  >
                    删除
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
