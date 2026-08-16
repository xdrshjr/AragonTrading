"use client";

import clsx from "clsx";
import { useEffect, useRef, useState } from "react";
import { Badge, Button } from "@/components/ui";
import { agentApi } from "@/lib/agentApi";
import type { AgentProvider, ProviderDraft, ProviderTestResult } from "@/lib/agentTypes";
import { API_STANDARD_LABEL, draftFromProvider, isDraftValid, validateProviderDraft } from "@/lib/settings";
import ConnectivityResult from "./ConnectivityResult";
import ProviderEditForm from "./ProviderEditForm";

/** 单服务商卡：展示态（标签 / 标准 / 默认 / key 状态 / 测试 / 设默认 / 编辑 / 删除）+ 内联编辑态。 */
export default function ProviderCard({
  provider,
  onMutated,
}: {
  provider: AgentProvider;
  onMutated: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<ProviderDraft>(() => draftFromProvider(provider));
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<ProviderTestResult | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);
  const flashTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const errors = validateProviderDraft(draft);

  // 轻量内联反馈（H9）：1.5s 后淡出；卸载清理计时器，避免对已卸载组件 setState。
  const flash = (msg: string) => {
    setFeedback(msg);
    if (flashTimer.current) clearTimeout(flashTimer.current);
    flashTimer.current = setTimeout(() => setFeedback(null), 1500);
  };
  useEffect(() => () => {
    if (flashTimer.current) clearTimeout(flashTimer.current);
  }, []);

  const beginEdit = () => {
    setDraft(draftFromProvider(provider));
    setError(null);
    setTestResult(null);
    setEditing(true);
  };

  const guard = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const runTest = async (which: "stored" | "draft") => {
    setTesting(true);
    setTestResult(null);
    try {
      const r = which === "stored"
        ? await agentApi.providers.test(provider.id)
        : await agentApi.providers.testDraft(draft);
      setTestResult(r);
    } catch (e) {
      setTestResult({ ok: false, message: (e as Error).message });
    } finally {
      setTesting(false);
    }
  };

  const save = () =>
    guard(async () => {
      if (!isDraftValid(draft)) return;
      await agentApi.providers.update(provider.id, draft);
      setEditing(false);
      flash("已保存 ✓");
      onMutated();
    });

  const setDefault = () => guard(async () => {
    await agentApi.providers.setDefault(provider.id);
    flash("已设为默认");
    onMutated();
  });

  const remove = () => guard(async () => {
    await agentApi.providers.remove(provider.id);
    onMutated();
  });

  return (
    <div className="card flex flex-col gap-3 p-4">
      <div className="flex items-start justify-between gap-2">
        <div className="flex min-w-0 flex-col gap-1.5">
          <div className="flex items-center gap-2">
            <span className="truncate text-sm font-semibold text-[var(--text)]">{provider.label}</span>
            {provider.isDefault && <Badge tone="accent">默认</Badge>}
          </div>
          <div className="flex flex-wrap items-center gap-1.5">
            <Badge tone="neutral">{API_STANDARD_LABEL[provider.apiStandard]}</Badge>
            <span className="tabular text-[11px] text-[var(--muted)]">{provider.model || "—"}</span>
          </div>
        </div>
        <span
          className={clsx(
            "shrink-0 text-[11px] tabular",
            provider.hasKey ? "text-[var(--long-ink)]" : "text-[var(--muted)]",
          )}
        >
          {provider.hasKey ? `密钥 ${provider.keyPreview}` : "未配置密钥"}
        </span>
      </div>

      {provider.isDefault && !provider.hasKey && !editing && (
        <p className="text-[11px] font-medium text-[var(--short-ink)]">
          该默认服务未填写密钥，填写后生效
        </p>
      )}

      {editing ? (
        <ProviderEditForm
          draft={draft}
          setDraft={setDraft}
          errors={errors}
          editableMeta={provider.kind === "custom"}
          hasExistingKey={provider.hasKey}
          idPrefix={`p${provider.id}`}
        />
      ) : (
        <p className="truncate text-[11px] text-[var(--muted)]" title={provider.baseUrl}>
          {provider.baseUrl}
        </p>
      )}

      <div className="flex items-center justify-between gap-2">
        <ConnectivityResult loading={testing} result={testResult} />
        <div className="flex flex-wrap items-center justify-end gap-1.5">
          {editing ? (
            <>
              <Button variant="ghost" size="sm" onClick={() => runTest("draft")} disabled={testing || busy}>
                测试
              </Button>
              <Button variant="ghost" size="sm" onClick={() => setEditing(false)} disabled={busy}>
                取消
              </Button>
              <Button variant="primary" size="sm" onClick={save} disabled={busy || !isDraftValid(draft)}>
                保存
              </Button>
            </>
          ) : (
            <>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => runTest("stored")}
                disabled={testing || !provider.hasKey}
                title={provider.hasKey ? "测试连通性" : "请先填写密钥"}
              >
                测试连通性
              </Button>
              {!provider.isDefault && (
                <Button variant="ghost" size="sm" onClick={setDefault} disabled={busy}>
                  设为默认
                </Button>
              )}
              <Button variant="ghost" size="sm" onClick={beginEdit} disabled={busy}>
                编辑
              </Button>
              {provider.deletable &&
                (confirmDelete ? (
                  <>
                    <Button variant="ghost" size="sm" onClick={() => setConfirmDelete(false)} disabled={busy}>
                      取消
                    </Button>
                    <Button variant="danger" size="sm" onClick={remove} disabled={busy}>
                      确认删除
                    </Button>
                  </>
                ) : (
                  <Button variant="danger" size="sm" onClick={() => setConfirmDelete(true)} disabled={busy}>
                    删除
                  </Button>
                ))}
            </>
          )}
        </div>
      </div>
      {error && <p className="text-[11px] text-[var(--short-ink)]">{error}</p>}
      {feedback && (
        <p role="status" aria-live="polite" className="text-[11px] font-medium text-[var(--long-ink)]">
          {feedback}
        </p>
      )}
    </div>
  );
}
