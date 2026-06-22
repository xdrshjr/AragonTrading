"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Badge, Button, Skeleton } from "@/components/ui";
import { agentApi } from "@/lib/agentApi";
import type { ProviderActive, ProviderDraft, ProviderTestResult } from "@/lib/agentTypes";
import { emptyDraft, groupProviders, isDraftValid, validateProviderDraft } from "@/lib/settings";
import ConnectivityResult from "./ConnectivityResult";
import ProviderCard from "./ProviderCard";
import ProviderEditForm from "./ProviderEditForm";

const PROVIDERS_KEY = ["agent", "providers"];

/** 右侧主面板：当前默认横幅 + 官方组 + 自定义组 + 「添加自定义模型」。 */
export default function AiModelsSection() {
  const qc = useQueryClient();
  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: PROVIDERS_KEY,
    queryFn: agentApi.providers.list,
  });
  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: PROVIDERS_KEY });
  };

  // 区级轻量内联反馈（H9）：新增成功后短暂提示；卸载清理计时器。
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
        <span>加载服务商失败：{(error as Error)?.message || "请稍后重试"}</span>
        <Button variant="ghost" size="sm" onClick={() => void refetch()}>
          重试
        </Button>
      </div>
    );
  }

  const { official, custom } = groupProviders(data.providers);

  return (
    <div className="flex flex-col gap-5 pb-6">
      <ActiveBanner active={data.active} />
      {feedback && (
        <p role="status" aria-live="polite" className="text-[11px] font-medium text-[var(--long)]">
          {feedback}
        </p>
      )}

      <Group title="官方模型" desc="OpenAI / Anthropic / Gemini / Kimi（不可删除，可编辑密钥与模型）">
        {official.map((p) => (
          <ProviderCard key={p.id} provider={p} onMutated={invalidate} />
        ))}
      </Group>

      <Group title="自定义模型" desc="任意 OpenAI / Anthropic 兼容服务，可新增 / 编辑 / 删除">
        {custom.map((p) => (
          <ProviderCard key={p.id} provider={p} onMutated={invalidate} />
        ))}
        {custom.length === 0 && (
          <p className="text-xs text-[var(--muted)]">还没有自定义模型，点击下方按钮添加。</p>
        )}
        <AddCustom
          onAdded={() => {
            invalidate();
            flash("已添加 ✓");
          }}
        />
      </Group>
    </div>
  );
}

function ActiveBanner({ active }: { active: ProviderActive }) {
  return (
    <div className="card flex items-center justify-between gap-3 p-4">
      <div className="flex min-w-0 flex-col gap-0.5">
        <span className="text-[11px] font-medium uppercase tracking-wide text-[var(--muted)]">
          当前默认服务
        </span>
        {active.configured ? (
          <span className="truncate text-sm font-semibold text-[var(--text)]">
            {active.label || active.provider} · {active.model}
          </span>
        ) : (
          <span className="text-sm font-semibold text-[var(--short)]">未配置可用服务</span>
        )}
      </div>
      <Badge tone={active.configured ? "long" : "neutral"}>
        {active.configured ? "就绪" : "待配置"}
      </Badge>
    </div>
  );
}

function Group({
  title,
  desc,
  children,
}: {
  title: string;
  desc: string;
  children: React.ReactNode;
}) {
  return (
    <section className="flex flex-col gap-3">
      <div className="flex flex-col gap-0.5">
        <h2 className="text-sm font-semibold text-[var(--text)]">{title}</h2>
        <p className="text-[11px] text-[var(--muted)]">{desc}</p>
      </div>
      {children}
    </section>
  );
}

function AddCustom({ onAdded }: { onAdded: () => void }) {
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState<ProviderDraft>(() => emptyDraft());
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<ProviderTestResult | null>(null);
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
      setTestResult(await agentApi.providers.testDraft(draft));
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
      await agentApi.providers.create(draft);
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
        + 添加自定义模型
      </Button>
    );
  }

  return (
    <div className="card flex flex-col gap-3 p-4">
      <ProviderEditForm
        draft={draft}
        setDraft={setDraft}
        errors={validateProviderDraft(draft)}
        editableMeta
        hasExistingKey={false}
        idPrefix="new-custom"
      />
      <div className="flex items-center justify-between gap-2">
        <ConnectivityResult loading={testing} result={testResult} />
        <div className="flex items-center gap-1.5">
          <Button
            variant="ghost"
            size="sm"
            onClick={test}
            disabled={testing || busy || !draft.apiKey.trim() || !isDraftValid(draft)}
            title={!draft.apiKey.trim() ? "请先填写 API Key" : "测试连通性"}
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
