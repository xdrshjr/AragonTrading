"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Badge, Button, Modal, Select, Switch } from "@/components/ui";
import { contextModeLabel, formatBytes, formatSeconds, skippedLabel } from "@/lib/journal";
import { journalApi } from "@/lib/journalApi";
import { buildSliceUrls, downloadSlices } from "@/lib/journalExport";
import type {
  ContextMode,
  ExportFormat,
  ExportPreview,
  JournalFilters,
  JournalStats,
  LinkConfidence,
  SftOptions,
} from "@/lib/journalTypes";

/**
 * 导出中心（ai-trade-audit-journal §2.6.4）。
 *
 * 三条形状硬约束：
 *
 * - **预检先行**：任一参数变化 → 防抖 400ms → `POST /export/preview` → 显示「将导出 N 条
 *   （约 X MB）；另有 M 条被排除：…」。一个不说「另外 2810 条为什么没导出」的导出器，
 *   会让人误以为系统只做了 37 次决策，故九个桶**逐项渲染**。
 * - **分片顺序下载**：`slices > 1` 时按钮写明片数与预估耗时，点击后**顺序**触发 N 次
 *   `<a download>`。并发会让 SQLite 上的几个长扫描互相抢读锁，而这条链上还挂着一个正在下单
 *   的会话。
 * - **`minimal` 档禁用后训练导出**：一个点了会导出空文件的按钮，比没有按钮更坏。
 *
 * `minLinkConfidence` 的实际生效值取自预检回执的 `resolvedMinLinkConfidence`，
 * 前端**照抄不重算**——那条判定在服务端只有一处。
 */
const FORMATS: { value: ExportFormat; label: string; hint: string }[] = [
  { value: "audit-csv", label: "审计 CSV", hint: "人看 / Excel / 合规留档，22 列扁平字段" },
  { value: "audit-jsonl", label: "审计 JSONL", hint: "全保真归档 / 离线复算，含门禁链与执行断面" },
  { value: "sft-jsonl", label: "后训练 JSONL", hint: "Messages 形态样本 + 结果标签，可直接拿去微调" },
];

const CONFIDENCE_OPTIONS: { value: LinkConfidence | "auto"; label: string }[] = [
  { value: "auto", label: "自动（由服务端按本次范围解出）" },
  { value: "exact", label: "仅精确关联（按交易所单号）" },
  { value: "heuristic", label: "含启发式关联" },
  { value: "none", label: "不限（含未关联到成交的轮次）" },
];

const DEFAULT_SFT: SftOptions = {
  excludeGateVetoed: true,
  excludeDegraded: true,
  onlySettled: true,
  includeHold: false,
  minLinkConfidence: "auto",
  contextMode: "full",
};

const SFT_HINTS: Record<keyof SftOptions, string> = {
  excludeGateVetoed: "把被确定性门禁改写的输出当正样本，训出来的就是会被否决的行为。",
  excludeDegraded: "降级轮里模型那一段是系统合成的观望，不是模型说的话。",
  onlySettled: "没有结果标签的样本对后训练没有价值；放开时盈亏字段整键缺席，绝不填 0。",
  includeHold: "观望轮占比极高，全量导出会把样本严重压偏；需要平衡负样本时再开。",
  minLinkConfidence: "纸面 / 无凭证会话在结构上没有交易所单号，故默认交给服务端自适应。",
  contextMode: "默认按上下文链重建整段多轮对话；重建不出来的样本一律排除，而不是凑合导出。",
};

export default function JournalExportDialog({
  open,
  onClose,
  scope,
  stats,
}: {
  open: boolean;
  onClose: () => void;
  scope: JournalFilters;
  stats: JournalStats | null;
}) {
  const [format, setFormat] = useState<ExportFormat>("audit-csv");
  const [sft, setSft] = useState<SftOptions>(DEFAULT_SFT);
  const [preview, setPreview] = useState<ExportPreview | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [error, setError] = useState("");
  const [downloaded, setDownloaded] = useState<number>(0);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const minimalTier = stats?.captureTier === "minimal";
  const sftDisabled = minimalTier;
  const chainOff = stats ? !stats.contextChainEnabled : false;

  // 总闸关时 `full` 根本重建不出来：把单选**钉在** lastTurnOnly 并说清楚，
  // 而不是让用户选一个会产出空文件的档。
  useEffect(() => {
    if (chainOff && sft.contextMode !== "lastTurnOnly") {
      setSft((prev) => ({ ...prev, contextMode: "lastTurnOnly" }));
    }
  }, [chainOff, sft.contextMode]);

  useEffect(() => {
    if (!open) return;
    if (timerRef.current) clearTimeout(timerRef.current);
    setPreviewing(true);
    setError("");
    timerRef.current = setTimeout(() => {
      journalApi
        .exportPreview(format, scope, format === "sft-jsonl" ? sft : {})
        .then(setPreview)
        .catch((err) => setError(err instanceof Error ? err.message : String(err)))
        .finally(() => setPreviewing(false));
    }, 400);
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [open, format, scope, sft]);

  const overLimit = preview?.overLimit ?? false;
  // 片数按**实际拿得到的游标数**收敛：服务端只保证 `sliceCursors.length <= slices - 1`
  // （行数不够切时更短）。多发一片没有游标的请求 = 又从头拉一遍全量，那正是分片要避免的。
  //
  // **按钮文案与真正会发生的事必须是同一个数**：写「分 3 个文件」却只下 2 个，
  // 是这个界面上最容易被信以为真的一句假话。
  const slices = useMemo(() => {
    if (!preview) return 1;
    return Math.min(preview.slices, (preview.sliceCursors ?? []).length + 1);
  }, [preview]);

  const buttonLabel = useMemo(() => {
    if (!preview) return "导出";
    if (slices > 1) {
      return `导出（分 ${slices} 个文件，约 ${formatSeconds(preview.estimatedSeconds)}）`;
    }
    return `导出 ${preview.rows} 条`;
  }, [preview, slices]);

  const start = async () => {
    if (!preview) return;
    const urls = buildSliceUrls(
      { format, scope, sft: format === "sft-jsonl" ? sft : undefined },
      slices,
      preview.sliceCursors ?? [],
    );
    setDownloaded(await downloadSlices(urls));
  };

  const setFlag = (key: keyof SftOptions, value: boolean) =>
    setSft((prev) => ({ ...prev, [key]: value }));

  return (
    <Modal open={open} onClose={onClose} labelledBy="journal-export-title" className="max-w-[720px]">
      <div className="space-y-3 p-4">
        <h2 id="journal-export-title" className="text-base font-medium">
          导出交易日志
        </h2>

        <section className="space-y-1.5">
          <h3 className="text-xs font-medium text-[var(--muted)]">格式</h3>
          {FORMATS.map((item) => {
            const disabled = item.value === "sft-jsonl" && sftDisabled;
            return (
              <label
                key={item.value}
                className="flex cursor-pointer items-start gap-2 rounded-[10px] border border-[var(--border)] p-2"
              >
                <input
                  type="radio"
                  name="journal-export-format"
                  value={item.value}
                  checked={format === item.value}
                  disabled={disabled}
                  onChange={() => setFormat(item.value)}
                  className="mt-0.5"
                />
                <span className="min-w-0">
                  <span className="text-xs font-medium">{item.label}</span>
                  <span className="block text-[11px] text-[var(--muted)]">{item.hint}</span>
                  {disabled && (
                    <span className="block text-[11px] text-[var(--short-ink)]">
                      当前采集档为「精简」，不含模型输入 / 输出，导出的会是空文件。
                      把 AI_TRADE_JOURNAL_CAPTURE_TIER 调到 standard 或 full 后重启后端再来。
                    </span>
                  )}
                </span>
              </label>
            );
          })}
        </section>

        {format === "sft-jsonl" && (
          <section className="space-y-1.5">
            <h3 className="text-xs font-medium text-[var(--muted)]">后训练筛选纪律</h3>
            {(
              [
                ["excludeGateVetoed", "排除被门禁改写的轮次"],
                ["excludeDegraded", "排除降级轮"],
                ["onlySettled", "只导出已结算的轮次"],
                ["includeHold", "包含观望轮"],
              ] as [keyof SftOptions, string][]
            ).map(([key, label]) => (
              <div key={key} className="rounded-[10px] border border-[var(--border)] p-2">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-xs">{label}</span>
                  <Switch
                    checked={Boolean(sft[key])}
                    onChange={(next: boolean) => setFlag(key, next)}
                    label={label}
                  />
                </div>
                <p className="mt-0.5 text-[11px] text-[var(--muted)]">{SFT_HINTS[key]}</p>
              </div>
            ))}

            <div className="rounded-[10px] border border-[var(--border)] p-2">
              <span className="text-xs">关联置信度下限</span>
              <Select
                aria-label="关联置信度下限"
                value={sft.minLinkConfidence}
                onChange={(e) =>
                  setSft((prev) => ({
                    ...prev,
                    minLinkConfidence: e.target.value as LinkConfidence | "auto",
                  }))
                }
                className="mt-1 w-full text-xs"
              >
                {CONFIDENCE_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </Select>
              <p className="mt-0.5 text-[11px] text-[var(--muted)]">
                {SFT_HINTS.minLinkConfidence}
              </p>
              {preview && (
                <p className="mt-0.5 text-[11px] text-[var(--accent-ink)]">
                  本次实际生效：{preview.resolvedMinLinkConfidence}
                  {preview.resolvedReason === "no_client_order_id_in_scope" &&
                    "（本次范围内未发现交易所单号，已自动放宽到启发式关联）"}
                  {preview.resolvedReason === "client_order_id_present" &&
                    "（本次范围内存在交易所单号，按精确关联）"}
                </p>
              )}
            </div>

            <div className="rounded-[10px] border border-[var(--border)] p-2">
              <span className="text-xs">上下文重建</span>
              {(["full", "lastTurnOnly"] as ContextMode[]).map((mode) => (
                <label key={mode} className="mt-1 flex cursor-pointer items-start gap-2">
                  <input
                    type="radio"
                    name="journal-context-mode"
                    value={mode}
                    checked={sft.contextMode === mode}
                    disabled={chainOff && mode === "full"}
                    onChange={() => setSft((prev) => ({ ...prev, contextMode: mode }))}
                    className="mt-0.5"
                  />
                  <span className="text-[11px]">{contextModeLabel(mode)}</span>
                </label>
              ))}
              <p className="mt-0.5 text-[11px] text-[var(--muted)]">{SFT_HINTS.contextMode}</p>
              {sft.contextMode === "lastTurnOnly" && (
                <p
                  role="alert"
                  className="mt-1 rounded-[8px] bg-[color-mix(in_srgb,var(--accent)_16%,transparent)] px-2 py-1 text-[11px] text-[var(--accent-ink)]"
                >
                  样本将只含最后一轮，与模型当时看到的整段对话不同。每条样本都会带
                  quality.contextTruncated=true，请在训练前确认这正是你要的。
                </p>
              )}
              {chainOff && (
                <p className="mt-1 text-[11px] text-[var(--muted)]">
                  上下文链总闸当前关闭，故只能导出单轮样本。
                </p>
              )}
            </div>
          </section>
        )}

        <section className="rounded-[10px] border border-[var(--border)] p-2">
          <h3 className="text-xs font-medium text-[var(--muted)]">预检</h3>
          {previewing && <p className="text-[11px] text-[var(--muted)]">正在核算…</p>}
          {error && <p className="text-[11px] text-[var(--short-ink)]">预检失败：{error}</p>}
          {preview && !previewing && (
            <div className="space-y-1">
              <p className="text-xs">
                将导出 <span className="tabular font-medium">{preview.rows}</span> 条（约{" "}
                {formatBytes(preview.approxBytes)}，预估 {formatSeconds(preview.estimatedSeconds)}）
                {slices > 1 && `，将分 ${slices} 个文件顺序下载`}
              </p>
              <div className="flex flex-wrap gap-1.5">
                {Object.entries(preview.skipped).map(([key, count]) => (
                  <Badge key={key} tone={count > 0 ? "accent" : "neutral"}>
                    {skippedLabel(key)} {count}
                  </Badge>
                ))}
              </div>
              {overLimit && (
                <p className="text-[11px] text-[var(--short-ink)]">
                  超过单次导出的行数上限（{stats?.limits.exportMaxRows ?? "—"}），
                  请收窄时间范围或指定单个会话后再导。
                </p>
              )}
            </div>
          )}
        </section>

        {downloaded > 0 && (
          <p className="text-[11px] text-[var(--muted)]">
            已顺序触发 {downloaded} / {slices} 片下载。每片都自带 manifest 与 eof 行；
            任何一片缺少 eof 行都表示该片不完整，请重新导出那一片。
          </p>
        )}

        <footer className="flex items-center justify-end gap-2 pt-1">
          <Button variant="ghost" onClick={onClose}>
            关闭
          </Button>
          <Button
            variant="primary"
            onClick={start}
            disabled={!preview || previewing || overLimit || preview.rows === 0}
          >
            {buttonLabel}
          </Button>
        </footer>
      </div>
    </Modal>
  );
}
