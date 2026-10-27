"use client";

import { useState } from "react";
import { Button } from "@/components/ui";
import { journalApi } from "@/lib/journalApi";
import type { JournalPromptBlock as PromptBlock, PromptRebuild } from "@/lib/journalTypes";

/**
 * 提示词段（ai-trade-audit-journal §2.6.2 第 4 段）。
 *
 * `full` 档直接显示原文（含重试后缀，不受渲染代码演进影响）；`standard` 档给一个「按上下文
 * 即时重建」按钮，点击后由服务端重建并与存档 sha 比对。
 *
 * `shaMatch=false` 时**必须**用醒目条说明「提示词渲染代码在此之后已变更 / 该轮带过重试后缀，
 * 下方为按当前代码重建的版本，与当时喂给模型的不完全相同」——这句话直接决定用户会不会拿一批
 * 假样本去训练。
 */
export default function JournalPromptBlock({
  decisionId,
  block,
}: {
  decisionId: number;
  block: PromptBlock;
}) {
  const [rebuilt, setRebuilt] = useState<PromptRebuild | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  if (block.source === "none") {
    return (
      <p className="px-1 py-2 text-xs text-[var(--muted)]">
        该轮以 minimal 档采集，未留存上下文，无法重建提示词。
      </p>
    );
  }

  if (block.source === "stored") {
    return (
      <div className="space-y-1.5">
        <p className="text-[11px] text-[var(--muted)]">
          存档原文（含当时真正送出去的重试 / 追问后缀），不经任何重建。
        </p>
        <div className="min-w-0 overflow-x-auto rounded-[10px] border border-[var(--border)] p-2">
          <pre className="whitespace-pre-wrap break-words text-[11px] leading-4">{block.text}</pre>
        </div>
      </div>
    );
  }

  const rebuild = async () => {
    setLoading(true);
    setError("");
    try {
      setRebuilt(await journalApi.prompt(decisionId));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-1.5">
      <p className="text-[11px] text-[var(--muted)]">
        本轮以 standard 档采集：只留了上下文与提示词的内容指纹，原文可按当前渲染代码即时重建。
      </p>
      {!rebuilt && (
        <Button size="sm" variant="ghost" onClick={rebuild} disabled={loading}>
          {loading ? "重建中…" : "按上下文即时重建"}
        </Button>
      )}
      {error && <p className="text-[11px] text-[var(--short-ink)]">重建失败：{error}</p>}
      {rebuilt && !rebuilt.shaMatch && (
        <p
          role="alert"
          className="rounded-[8px] bg-[color-mix(in_srgb,var(--short)_14%,transparent)] px-2 py-1.5 text-[11px] text-[var(--short-ink)]"
        >
          提示词渲染代码在此之后已变更，或该轮带过重试 / 追问后缀。下方是按当前代码重建的版本，
          与当时喂给模型的不完全相同——请不要把这一轮当作后训练样本使用（导出器也已自动排除它）。
        </p>
      )}
      {rebuilt && rebuilt.shaMatch && (
        <p className="text-[11px] text-[var(--long-ink)]">
          内容指纹一致：下方与当时喂给模型的那一条逐字相同。
        </p>
      )}
      {rebuilt && (
        <div className="min-w-0 overflow-x-auto rounded-[10px] border border-[var(--border)] p-2">
          <pre className="whitespace-pre-wrap break-words text-[11px] leading-4">
            {rebuilt.text}
          </pre>
        </div>
      )}
      <p className="break-all text-[10px] text-[var(--muted)]">存档指纹 {block.storedSha}</p>
    </div>
  );
}
