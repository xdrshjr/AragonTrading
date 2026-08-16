"use client";

import { useEffect, useRef, useState } from "react";
import { Badge, Button } from "@/components/ui";
import { accountApi } from "@/lib/accountApi";
import type { OkxAccount, OkxAccountDraft, OkxAccountTestResult } from "@/lib/accountTypes";
import { draftFromAccount, isDraftValid, validateAccountDraft } from "@/lib/accountSettings";
import ConnectivityResult from "./ConnectivityResult";
import OkxAccountDetailModal from "./OkxAccountDetailModal";
import OkxAccountEditForm from "./OkxAccountEditForm";

/** 单账户卡：展示态（掩码 / 默认徽标 / 测试 / 设默认 / 编辑 / 删除 / 查看详情）+ 内联编辑态。 */
export default function OkxAccountCard({
  account,
  onMutated,
}: {
  account: OkxAccount;
  onMutated: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<OkxAccountDraft>(() => draftFromAccount(account));
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<OkxAccountTestResult | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [showDetail, setShowDetail] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);
  const flashTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // 编辑态 secret/passphrase 可留空保留，故 requireSecrets=false。
  const errors = validateAccountDraft(draft, false);
  const canViewDetail = account.hasSecret && account.hasPassphrase;

  const flash = (msg: string) => {
    setFeedback(msg);
    if (flashTimer.current) clearTimeout(flashTimer.current);
    flashTimer.current = setTimeout(() => setFeedback(null), 1500);
  };
  useEffect(() => () => {
    if (flashTimer.current) clearTimeout(flashTimer.current);
  }, []);

  const beginEdit = () => {
    setDraft(draftFromAccount(account));
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
      const r =
        which === "stored"
          ? await accountApi.accounts.test(account.id)
          : await accountApi.accounts.testDraft(draft);
      setTestResult(r);
    } catch (e) {
      setTestResult({ ok: false, message: (e as Error).message });
    } finally {
      setTesting(false);
    }
  };

  const save = () =>
    guard(async () => {
      if (!isDraftValid(draft, false)) return;
      await accountApi.accounts.update(account.id, draft);
      setEditing(false);
      flash("已保存 ✓");
      onMutated();
    });

  const setDefault = () =>
    guard(async () => {
      await accountApi.accounts.setDefault(account.id);
      flash("已设为默认");
      onMutated();
    });

  const remove = () =>
    guard(async () => {
      await accountApi.accounts.remove(account.id);
      onMutated();
    });

  return (
    <div className="card flex flex-col gap-3 p-4">
      <div className="flex items-start justify-between gap-2">
        <div className="flex min-w-0 flex-col gap-1.5">
          <div className="flex items-center gap-2">
            <span className="truncate text-sm font-semibold text-[var(--text)]">{account.label}</span>
            {account.isDefault && <Badge tone="accent">默认</Badge>}
          </div>
          <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1 text-[11px] text-[var(--muted)]">
            <span className="tabular">API {account.apiKeyPreview || "未配置"}</span>
            <span className={account.hasSecret ? "text-[var(--long-ink)]" : "text-[var(--short-ink)]"}>
              {account.hasSecret ? "密钥已存" : "密钥未存"}
            </span>
            {account.ipWhitelist && (
              <span className="truncate" title={account.ipWhitelist}>
                IP {account.ipWhitelist}
              </span>
            )}
          </div>
        </div>
      </div>

      {editing && (
        <OkxAccountEditForm
          draft={draft}
          setDraft={setDraft}
          errors={errors}
          hasExistingSecret={account.hasSecret}
          hasExistingPassphrase={account.hasPassphrase}
          existingApiKeyPreview={account.apiKeyPreview}
          idPrefix={`acc${account.id}`}
        />
      )}

      <div className="flex items-center justify-between gap-2">
        <ConnectivityResult loading={testing} result={testResult} />
        <div className="flex flex-wrap items-center justify-end gap-1.5">
          {editing ? (
            <>
              <Button variant="ghost" size="sm" onClick={() => runTest("draft")} disabled={testing || busy || !isDraftValid(draft)}>
                测试
              </Button>
              <Button variant="ghost" size="sm" onClick={() => setEditing(false)} disabled={busy}>
                取消
              </Button>
              <Button variant="primary" size="sm" onClick={save} disabled={busy || !isDraftValid(draft, false)}>
                保存
              </Button>
            </>
          ) : (
            <>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => runTest("stored")}
                disabled={testing || !canViewDetail}
                title={canViewDetail ? "测试连通性" : "请先补全密钥"}
              >
                测试连通性
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setShowDetail(true)}
                disabled={!canViewDetail}
                title={canViewDetail ? "查看资产与持仓详情" : "请先补全密钥"}
              >
                查看详情
              </Button>
              {!account.isDefault && (
                <Button variant="ghost" size="sm" onClick={setDefault} disabled={busy}>
                  设为默认
                </Button>
              )}
              <Button variant="ghost" size="sm" onClick={beginEdit} disabled={busy}>
                编辑
              </Button>
              {confirmDelete ? (
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
              )}
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

      {showDetail && <OkxAccountDetailModal account={account} onClose={() => setShowDetail(false)} />}
    </div>
  );
}
