"use client";

import { useState } from "react";
import { Button, Field, Input, Modal, Select } from "@/components/ui";
import { CATEGORY_LABEL, IMPACT_LABEL, STANCE_LABEL, TIER_LABEL, isSafeUrl } from "@/lib/intel";
import type {
  IntelCategory,
  IntelImpact,
  IntelItem,
  IntelItemInput,
  IntelSourceInput,
  IntelStance,
  IntelTier,
} from "@/lib/intelTypes";

const TIERS: IntelTier[] = ["ultra", "mid", "long"];
const CATEGORIES: IntelCategory[] = [
  "market",
  "sentiment",
  "macro",
  "onchain",
  "project",
  "risk",
];
const STANCES: IntelStance[] = ["bullish", "bearish", "neutral"];
const IMPACTS: IntelImpact[] = ["low", "medium", "high"];

const HEADLINE_MAX = 40;
const SUMMARY_MAX = 400;

/**
 * 新增 / 编辑情报的表单（ai-trade-market-intel §6.2 #31）。
 *
 * **字数上限在输入时就说清楚**：后端对摘要是「超长即拒收、绝不静默截断」，如果表单不提示，
 * 用户会写完一整段再被一个 422 打回来。
 *
 * 用户手改过的条目在后端落 `user_edited=True`——那是与「置顶」并列的第二把免死金牌，自动清理
 * 一律跳过它。这一点写在表单底部，因为它改变了用户对「我改这一条会不会白改」的预期。
 */
export default function IntelEditor({
  open,
  item,
  onClose,
  onSubmit,
}: {
  open: boolean;
  /** 传入即编辑模式；`null` 为新增。 */
  item: IntelItem | null;
  onClose: () => void;
  onSubmit: (draft: IntelItemInput) => Promise<void>;
}) {
  const [tier, setTier] = useState<IntelTier>(item?.tier ?? "ultra");
  const [category, setCategory] = useState<IntelCategory>(item?.category ?? "market");
  const [stance, setStance] = useState<IntelStance>(item?.stance ?? "neutral");
  const [impact, setImpact] = useState<IntelImpact>(item?.impact ?? "medium");
  const [headline, setHeadline] = useState(item?.headline ?? "");
  const [summary, setSummary] = useState(item?.summary ?? "");
  const [sources, setSources] = useState<IntelSourceInput[]>(
    item?.sources.map((s) => ({ url: s.url, title: s.title })) ?? [{ url: "", title: "" }],
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const headlineError =
    headline.length > HEADLINE_MAX ? `标题最多 ${HEADLINE_MAX} 字（当前 ${headline.length}）` : "";
  const summaryError =
    summary.length > SUMMARY_MAX ? `摘要最多 ${SUMMARY_MAX} 字（当前 ${summary.length}）` : "";
  const badSource = sources.some((s) => s.url.trim() && !isSafeUrl(s.url.trim()));
  const invalid =
    !headline.trim() || !summary.trim() || !!headlineError || !!summaryError || badSource;

  const submit = async () => {
    if (invalid || busy) return;
    setBusy(true);
    setError("");
    try {
      await onSubmit({
        tier,
        category,
        stance,
        impact,
        headline: headline.trim(),
        summary: summary.trim(),
        sources: sources
          .filter((s) => s.url.trim())
          .map((s) => ({ url: s.url.trim(), title: (s.title ?? "").trim() })),
      });
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存失败，请稍后重试");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal open={open} onClose={onClose} className="max-w-lg">
      <div className="flex flex-col gap-3 p-4" data-testid="intel-editor">
        <h2 className="text-sm font-semibold text-[var(--text)]">
          {item ? "编辑情报" : "新增情报"}
        </h2>

        <div className="grid grid-cols-2 gap-3">
          <Field label="层级">
            <Select value={tier} onChange={(e) => setTier(e.target.value as IntelTier)}>
              {TIERS.map((t) => (
                <option key={t} value={t}>
                  {TIER_LABEL[t]}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="类别">
            <Select
              value={category}
              onChange={(e) => setCategory(e.target.value as IntelCategory)}
            >
              {CATEGORIES.map((c) => (
                <option key={c} value={c}>
                  {CATEGORY_LABEL[c]}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="方向">
            <Select value={stance} onChange={(e) => setStance(e.target.value as IntelStance)}>
              {STANCES.map((s) => (
                <option key={s} value={s}>
                  {STANCE_LABEL[s]}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="影响力">
            <Select value={impact} onChange={(e) => setImpact(e.target.value as IntelImpact)}>
              {IMPACTS.map((i) => (
                <option key={i} value={i}>
                  {IMPACT_LABEL[i]}
                </option>
              ))}
            </Select>
          </Field>
        </div>

        <Field
          label="标题"
          htmlFor="intel-headline"
          error={headlineError}
          hint={`${headline.length} / ${HEADLINE_MAX}`}
        >
          <Input
            id="intel-headline"
            value={headline}
            onChange={(e) => setHeadline(e.target.value)}
            placeholder="一眼可读的一句话"
          />
        </Field>

        <Field
          label="摘要"
          htmlFor="intel-summary"
          error={summaryError}
          hint={`${summary.length} / ${SUMMARY_MAX}｜几句话说清「发生了什么 + 为什么与本标的有关」`}
        >
          <textarea
            id="intel-summary"
            value={summary}
            onChange={(e) => setSummary(e.target.value)}
            rows={4}
            className="rounded-[10px] border border-[var(--border)] bg-[var(--surface)] px-2.5 py-1.5 text-sm text-[var(--text)] outline-none focus:border-[var(--accent)]"
          />
        </Field>

        <div className="flex flex-col gap-1.5">
          <span className="text-xs font-medium text-[var(--muted)]">来源（可选，最多 6 条）</span>
          {sources.map((source, index) => (
            <div key={index} className="flex items-center gap-1.5">
              <Input
                value={source.url}
                onChange={(e) => {
                  const next = [...sources];
                  next[index] = { ...next[index], url: e.target.value };
                  setSources(next);
                }}
                placeholder="https://…"
                aria-label={`来源链接 ${index + 1}`}
                className="min-w-0 flex-1"
              />
              <Button
                variant="ghost"
                size="sm"
                aria-label={`删除来源 ${index + 1}`}
                onClick={() => setSources(sources.filter((_, i) => i !== index))}
              >
                删除
              </Button>
            </div>
          ))}
          {badSource && (
            <span className="text-[11px] text-[var(--short-ink)]">
              来源链接必须是 http(s) 绝对地址
            </span>
          )}
          {sources.length < 6 && (
            <Button
              variant="ghost"
              size="sm"
              className="self-start"
              onClick={() => setSources([...sources, { url: "", title: "" }])}
            >
              + 添加来源
            </Button>
          )}
        </div>

        <span className="text-[11px] leading-relaxed text-[var(--muted)]">
          手工新增或编辑过的条目会被标记为「用户编辑」，此后自动整理与自动清理都不会动它。
        </span>
        {error && <span className="text-[11px] text-[var(--short-ink)]">{error}</span>}

        <div className="mt-1 flex items-center justify-end gap-2">
          <Button variant="ghost" size="sm" onClick={onClose}>
            取消
          </Button>
          <Button variant="primary" size="sm" disabled={invalid || busy} onClick={submit}>
            {busy ? "保存中…" : "保存"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
