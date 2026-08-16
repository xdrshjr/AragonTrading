"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Button, Skeleton } from "@/components/ui";
import { autoTradeSettingsApi } from "@/lib/autoTradeSettingsApi";
import type { AutoTradeSettings } from "@/lib/liveAutoTypes";

const SETTINGS_KEY = ["settings", "auto-trade"];

/** 解析总预算上限输入：空 / 非数 / 越界 (0,100] → 错误；否则返回数值（整规到 2 位小数）。 */
function parseMaxBudgetPct(raw: string): { value: number | null; error: string | null } {
  const text = raw.trim();
  if (text === "") return { value: null, error: "请输入 0~100 之间的百分比" };
  const n = Number(text);
  if (!Number.isFinite(n) || n <= 0 || n > 100) {
    return { value: null, error: "总预算上限须在 (0, 100] 之间" };
  }
  return { value: Math.round(n * 100) / 100, error: null };
}

/**
 * 设置中心 · 自动交易设置：配置「启动多标的自动交易」的总预算上限百分比（默认 100% = 全部可用余额，
 * 不随成员数缩放）。保存走乐观提示，成功后写入查询缓存供启动弹窗预览读取（auto-trade-budget-and-allocation-settings §4.1）。
 */
export default function AutoTradeSettingsSection() {
  const qc = useQueryClient();
  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: SETTINGS_KEY,
    queryFn: autoTradeSettingsApi.get,
  });

  const [raw, setRaw] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const flashTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const flash = (msg: string) => {
    setNotice(msg);
    if (flashTimer.current) clearTimeout(flashTimer.current);
    flashTimer.current = setTimeout(() => setNotice(null), 2500);
  };
  useEffect(() => () => {
    if (flashTimer.current) clearTimeout(flashTimer.current);
  }, []);

  // 数据到达 / 变更时回填输入框（以服务端最新值为准）。
  useEffect(() => {
    if (data) setRaw(String(data.maxBudgetPct));
  }, [data]);

  const save = useMutation({
    mutationFn: (pct: number) => autoTradeSettingsApi.update(pct),
    onSuccess: (res) => {
      qc.setQueryData(SETTINGS_KEY, res);
      flash("已保存");
    },
    onError: () => flash("保存失败，请稍后重试"),
  });

  if (isLoading) {
    return (
      <div className="flex flex-col gap-3">
        <Skeleton className="h-16" />
        <Skeleton className="h-32" />
      </div>
    );
  }
  if (isError || !data) {
    return (
      <div className="card flex items-center justify-between gap-3 p-4 text-sm text-[var(--short-ink)]">
        <span>加载自动交易设置失败：{(error as Error)?.message || "请稍后重试"}</span>
        <Button variant="ghost" size="sm" onClick={() => void refetch()}>
          重试
        </Button>
      </div>
    );
  }

  const parsed = parseMaxBudgetPct(raw);
  const dirty = parsed.value != null && parsed.value !== data.maxBudgetPct;
  const ceilingActive = data.effectiveMaxBudgetPct < data.maxBudgetPct;

  return (
    <div className="flex flex-col gap-5 pb-6">
      <MaxBudgetCard
        raw={raw}
        onChange={setRaw}
        error={parsed.error}
        busy={save.isPending}
        canSave={!parsed.error && dirty && !save.isPending}
        onSave={() => parsed.value != null && save.mutate(parsed.value)}
        effectivePct={data.effectiveMaxBudgetPct}
        ceilingActive={ceilingActive}
      />
      {notice && (
        <p role="status" aria-live="polite" className="text-[11px] font-medium text-[var(--accent-ink)]">
          {notice}
        </p>
      )}
    </div>
  );
}

function MaxBudgetCard({
  raw,
  onChange,
  error,
  busy,
  canSave,
  onSave,
  effectivePct,
  ceilingActive,
}: {
  raw: string;
  onChange: (v: string) => void;
  error: string | null;
  busy: boolean;
  canSave: boolean;
  onSave: () => void;
  effectivePct: number;
  ceilingActive: boolean;
}) {
  return (
    <section className="card flex flex-col gap-3 p-4">
      <div className="flex flex-col gap-0.5">
        <h2 className="text-sm font-semibold text-[var(--text)]">总预算上限</h2>
        <p className="text-[11px] leading-relaxed text-[var(--muted)]">
          「启动多标的自动交易」的生效总预算上限占可用余额的百分比。默认 100%（即默认最大总预算 = 全部可用
          余额），不随自动交易标的数缩放。仅治理多标的自动交易分组面板。
        </p>
      </div>
      <label className="flex flex-col gap-1 text-[11px] text-[var(--muted)]">
        <span>总预算上限（% 可用余额）</span>
        <div className="flex items-center gap-2">
          <input
            type="text"
            inputMode="decimal"
            value={raw}
            placeholder="100"
            disabled={busy}
            onChange={(e) => onChange(e.target.value)}
            aria-label="总预算上限百分比"
            className="w-32 rounded-md border border-[var(--border)] bg-[var(--bg)] px-2 py-1.5 text-xs text-[var(--text)] outline-none focus:border-[var(--accent)] disabled:opacity-50"
          />
          <span className="text-xs text-[var(--muted)]">%</span>
          <Button variant="primary" size="sm" onClick={onSave} disabled={!canSave}>
            {busy ? "保存中…" : "保存"}
          </Button>
        </div>
      </label>
      {error ? (
        <p role="alert" className="text-[11px] font-medium text-[var(--short-ink)]">
          {error}
        </p>
      ) : ceilingActive ? (
        <p className="text-[11px] leading-relaxed text-[var(--muted)]">
          运营方天花板已收紧：实际生效上限为可用余额的{" "}
          <span className="font-medium text-[var(--text)]">{effectivePct}%</span>。
        </p>
      ) : null}
    </section>
  );
}
