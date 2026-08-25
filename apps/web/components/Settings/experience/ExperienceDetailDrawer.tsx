"use client";

import { useId } from "react";
import { Badge, Button, Modal } from "@/components/ui";
import {
  SOURCE_LABEL_ZH,
  axisLabel,
  formatNetBps,
  formatPercent,
  formatTStat,
  relativeTime,
  shortTime,
  tierLabel,
  valueLabel,
} from "@/lib/experience";
import type { ExperienceNodeDetail } from "@/lib/experienceTypes";

function statNumber(stats: Record<string, unknown>, key: string, digits = 1): string {
  const value = stats[key];
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(digits) : "—";
}

/**
 * 经验详情抽屉：陈述 / 长文 / 统计 / 语境签名 / 版本链 / 冲突 / 溯源证据 / 操作。
 *
 * **版本链与冲突不是装饰**：`refines` 让用户看到「这条经验曾经是反过来的，在 X 月被 N 笔新样本
 * 推翻」，`contradicts` 让用户看到「有两条互相矛盾的结论，因此都没有进纲要」。把一个未解决的
 * 冲突当成事实讲给交易员是产品在撒谎；把它摆在这里，才是对的。
 */
export default function ExperienceDetailDrawer({
  detail,
  open,
  onClose,
  onPin,
  onMute,
  onEdit,
  onDelete,
}: {
  detail: ExperienceNodeDetail | null;
  open: boolean;
  onClose: () => void;
  onPin: () => void;
  onMute: () => void;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const titleId = useId();
  if (!detail) return null;
  const { node, stats, contexts, versions, contradicts, evidence } = detail;
  return (
    <Modal open={open} onClose={onClose} labelledBy={titleId} className="max-w-[460px]">
      <div className="flex max-h-[80vh] flex-col gap-3 overflow-y-auto p-5">
        <div className="flex items-start justify-between gap-3">
          <h2 id={titleId} className="text-sm font-semibold leading-relaxed text-[var(--text)]">
            {node.label}
          </h2>
          <Badge tone={node.pinned ? "accent" : "neutral"}>{tierLabel(node)}</Badge>
        </div>

        <p className="text-[11px] text-[var(--muted)]">
          来源 {SOURCE_LABEL_ZH[node.source] ?? node.source}
          {node.scope === "global" ? " · 跨标的通用经验" : ""}
          {node.muteReason ? ` · 沉降原因：${node.muteReason === "user" ? "用户静音" : node.muteReason === "stale" ? "长期无新证据" : "效应翻转"}` : ""}
          {" · "}最近证据 {relativeTime(node.lastSeenTs)}
        </p>

        {node.detail && (
          <p className="rounded-md bg-[var(--surface-2)] px-3 py-2 text-[12px] leading-relaxed text-[var(--text)]">
            {node.detail}
          </p>
        )}

        <section>
          <h3 className="mb-1 text-[11px] font-medium text-[var(--muted)]">统计</h3>
          <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-[11px]">
            <Stat label="样本 n" value={String(node.support)} />
            {/* `winRate` / `costShare` 在 wire 里是 0~1 的小数（后端渲染文案时同样 × 100）。 */}
            <Stat label="胜率" value={formatPercent(stats.winRate)} />
            <Stat label="净额均值" value={formatNetBps(stats.meanNetBps)} />
            <Stat label="净额中位" value={formatNetBps(stats.medianNetBps)} />
            <Stat label="t 统计量" value={formatTStat(stats.tStat)} />
            <Stat label="平均持仓" value={`${statNumber(stats, "meanHoldS", 0)}s`} />
            <Stat label="成本占比" value={formatPercent(stats.costShare)} />
            <Stat label="edge 误差" value={formatNetBps(stats.meanEdgeErrorBps)} />
          </dl>
          {(Number(stats.excluded) || 0) > 0 && (
            <p className="mt-1 text-[10px] text-[var(--muted)]">
              另有 {String(stats.excluded)} 笔因成本口径不可比未计入本表（毛额与净额不能混算）。
            </p>
          )}
          {stats.costRegimeFallback ? (
            <p className="mt-1 text-[10px] text-[var(--muted)]">
              成本档分位样本不足，本条用的是全库分位而不是该标的 / 周期的分位。
            </p>
          ) : null}
        </section>

        <section>
          <h3 className="mb-1 text-[11px] font-medium text-[var(--muted)]">语境签名</h3>
          <div className="flex flex-wrap gap-1">
            {Object.entries(node.signature).map(([axis, value]) => (
              <Badge key={axis} tone="neutral">
                {axisLabel(axis)}·{valueLabel(String(value))}
              </Badge>
            ))}
            {contexts.length === 0 && Object.keys(node.signature).length === 0 && (
              <span className="text-[11px] text-[var(--muted)]">（无）</span>
            )}
          </div>
        </section>

        {versions.length > 0 && (
          <section>
            <h3 className="mb-1 text-[11px] font-medium text-[var(--muted)]">
              版本链（被本条取代的旧版本）
            </h3>
            <ul className="flex flex-col gap-1">
              {versions.map((old) => (
                <li key={old.id} className="text-[11px] leading-relaxed text-[var(--muted)]">
                  · {old.label}（{shortTime(old.updatedTs)}）
                </li>
              ))}
            </ul>
          </section>
        )}

        {contradicts.length > 0 && (
          <section>
            <h3 className="mb-1 text-[11px] font-medium text-[var(--warn)]">冲突</h3>
            <ul className="flex flex-col gap-1">
              {contradicts.map((rival) => (
                <li key={rival.id} className="text-[11px] leading-relaxed text-[var(--muted)]">
                  · {rival.label}
                </li>
              ))}
            </ul>
            <p className="mt-1 text-[10px] leading-relaxed text-[var(--muted)]">
              两条结论势均力敌时<strong className="text-[var(--text)]">都不会</strong>进入 Agent 的常驻纲要——把一个未解决的冲突当成事实
              讲给交易员是产品在撒谎；它只会作为「争议」出现在下单前的反思环节。
            </p>
          </section>
        )}

        <section>
          <h3 className="mb-1 text-[11px] font-medium text-[var(--muted)]">
            溯源证据（最近 {evidence.length} 笔）
          </h3>
          {evidence.length === 0 ? (
            <p className="text-[11px] text-[var(--muted)]">
              这条经验暂无可展示的往返证据（手工 / 导入的经验尚未被事件佐证）。
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[380px] border-collapse text-left text-[11px]">
                <thead>
                  <tr className="border-b border-[var(--border)] text-[var(--muted)]">
                    <th scope="col" className="py-1 pr-2 font-medium">时间</th>
                    <th scope="col" className="py-1 pr-2 font-medium">方向</th>
                    <th scope="col" className="py-1 pr-2 text-right font-medium">净额</th>
                    <th scope="col" className="py-1 font-medium">退出</th>
                  </tr>
                </thead>
                <tbody>
                  {evidence.map((row) => (
                    <tr key={row.id} className="border-b border-[var(--border)]">
                      <td className="py-1 pr-2">{shortTime(row.exitTs)}</td>
                      <td className="py-1 pr-2">{valueLabel(row.side)}</td>
                      <td className="tabular py-1 pr-2 text-right">{formatNetBps(row.netBps)}</td>
                      <td className="py-1">{valueLabel(row.exitSource)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        <div className="mt-1 flex flex-wrap items-center justify-end gap-2">
          <Button variant="ghost" size="sm" onClick={onPin}>
            {node.pinned ? "取消置顶" : "置顶"}
          </Button>
          <Button variant="ghost" size="sm" onClick={onMute}>
            {node.muted ? "取消静音" : "静音"}
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={onEdit}
            disabled={node.source === "stat"}
            title={node.source === "stat" ? "统计经验的陈述由模板生成，重建会覆盖手工改动" : undefined}
          >
            编辑
          </Button>
          <Button
            variant="danger"
            size="sm"
            onClick={onDelete}
            disabled={node.source === "stat"}
            title={node.source === "stat"
              ? "统计经验不能删除（重建会按同一 key 复活它）；请改用「静音」"
              : undefined}
          >
            删除
          </Button>
          <Button variant="ghost" size="sm" onClick={onClose}>关闭</Button>
        </div>
        <p className="text-[10px] leading-relaxed text-[var(--muted)]">
          置顶的经验不受证据门槛约束，会直接进入 Agent 的常驻纲要——这是「叙事 / 手写经验进纲要」
          的<strong className="text-[var(--text)]">唯一</strong>路径，也是一次明确的人类背书。
        </p>
      </div>
    </Modal>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <>
      <dt className="text-[var(--muted)]">{label}</dt>
      <dd className="tabular text-right text-[var(--text)]">{value}</dd>
    </>
  );
}
