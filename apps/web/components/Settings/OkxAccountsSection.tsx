"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Badge, Button, Skeleton } from "@/components/ui";
import { accountApi } from "@/lib/accountApi";
import type { OkxAccount, OkxAccountDraft, OkxAccountTestResult } from "@/lib/accountTypes";
import { emptyDraft, isDraftValid, validateAccountDraft } from "@/lib/accountSettings";
import ConnectivityResult from "./ConnectivityResult";
import OkxAccountCard from "./OkxAccountCard";
import OkxAccountEditForm from "./OkxAccountEditForm";

const ACCOUNTS_KEY = ["okx", "accounts"];

/** 右侧主面板：当前默认横幅 + 账户卡列表 + 「添加账户」（同 AiModelsSection 骨架）。 */
export default function OkxAccountsSection() {
  const qc = useQueryClient();
  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ACCOUNTS_KEY,
    queryFn: accountApi.accounts.list,
  });
  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ACCOUNTS_KEY });
  };

  const [feedback, setFeedback] = useState<string | null>(null);
  const flashTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const flash = (msg: string) => {
    setFeedback(msg);
    if (flashTimer.current) clearTimeout(flashTimer.current);
    flashTimer.current = setTimeout(() => setFeedback(null), 1500);
  };
  useEffect(() => () => {
    if (flashTimer.current) clearTimeout(flashTimer.current);
  }, []);

  if (isLoading) {
    return (
      <div className="flex flex-col gap-3">
        <Skeleton className="h-16" />
        <Skeleton className="h-28" />
        <Skeleton className="h-28" />
      </div>
    );
  }
  if (isError || !data) {
    return (
      <div className="card flex items-center justify-between gap-3 p-4 text-sm text-[var(--short)]">
        <span>加载账户失败：{(error as Error)?.message || "请稍后重试"}</span>
        <Button variant="ghost" size="sm" onClick={() => void refetch()}>
          重试
        </Button>
      </div>
    );
  }

  const defaultAccount = data.accounts.find((a) => a.id === data.defaultId) ?? null;

  return (
    <div className="flex flex-col gap-5 pb-6">
      <DefaultBanner account={defaultAccount} />
      {feedback && (
        <p role="status" aria-live="polite" className="text-[11px] font-medium text-[var(--long)]">
          {feedback}
        </p>
      )}

      <section className="flex flex-col gap-3">
        <div className="flex flex-col gap-0.5">
          <h2 className="text-sm font-semibold text-[var(--text)]">实盘账户凭证</h2>
          <p className="text-[11px] text-[var(--muted)]">
            管理真实欧易（OKX）API 凭证；密钥仅保存在本地后端，绝不上传，本期仅只读访问、不下任何订单。
          </p>
        </div>
        {data.accounts.map((a) => (
          <OkxAccountCard key={a.id} account={a} onMutated={invalidate} />
        ))}
        {data.accounts.length === 0 && (
          <p className="text-xs text-[var(--muted)]">还没有账户，点击下方按钮添加第一个。</p>
        )}
        <AddAccount
          onAdded={() => {
            invalidate();
            flash("已添加 ✓");
          }}
        />
      </section>
    </div>
  );
}

function DefaultBanner({ account }: { account: OkxAccount | null }) {
  const ready = !!account && account.hasSecret && account.hasPassphrase;
  return (
    <div className="card flex items-center justify-between gap-3 p-4">
      <div className="flex min-w-0 flex-col gap-0.5">
        <span className="text-[11px] font-medium uppercase tracking-wide text-[var(--muted)]">
          当前默认账户
        </span>
        {account ? (
          <span className="truncate text-sm font-semibold text-[var(--text)]">
            {account.label} · API {account.apiKeyPreview || "未配置"}
          </span>
        ) : (
          <span className="text-sm font-semibold text-[var(--short)]">未设置默认账户</span>
        )}
      </div>
      <Badge tone={ready ? "long" : "neutral"}>{ready ? "就绪" : "待配置"}</Badge>
    </div>
  );
}

function AddAccount({ onAdded }: { onAdded: () => void }) {
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState<OkxAccountDraft>(() => emptyDraft());
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<OkxAccountTestResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reset = () => {
    setAdding(false);
    setDraft(emptyDraft());
    setTestResult(null);
    setError(null);
  };

  const test = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      setTestResult(await accountApi.accounts.testDraft(draft));
    } catch (e) {
      setTestResult({ ok: false, message: (e as Error).message });
    } finally {
      setTesting(false);
    }
  };

  const save = async () => {
    if (!isDraftValid(draft)) return;
    setBusy(true);
    setError(null);
    try {
      await accountApi.accounts.create(draft);
      onAdded();
      reset();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  if (!adding) {
    return (
      <Button variant="default" size="sm" className="self-start" onClick={() => setAdding(true)}>
        + 添加账户
      </Button>
    );
  }

  return (
    <div className="card flex flex-col gap-3 p-4">
      <OkxAccountEditForm
        draft={draft}
        setDraft={setDraft}
        errors={validateAccountDraft(draft)}
        hasExistingSecret={false}
        hasExistingPassphrase={false}
        idPrefix="new-account"
      />
      <div className="flex items-center justify-between gap-2">
        <ConnectivityResult loading={testing} result={testResult} />
        <div className="flex items-center gap-1.5">
          <Button
            variant="ghost"
            size="sm"
            onClick={test}
            disabled={testing || busy || !isDraftValid(draft)}
            title={isDraftValid(draft) ? "测试连通性" : "请先填写完整凭证"}
          >
            测试
          </Button>
          <Button variant="ghost" size="sm" onClick={reset} disabled={busy}>
            取消
          </Button>
          <Button variant="primary" size="sm" onClick={save} disabled={busy || !isDraftValid(draft)}>
            添加
          </Button>
        </div>
      </div>
      {error && <p className="text-[11px] text-[var(--short)]">{error}</p>}
    </div>
  );
}
