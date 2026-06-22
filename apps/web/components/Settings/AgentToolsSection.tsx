"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Badge, Button, Skeleton } from "@/components/ui";
import { TOOL_CATEGORIES } from "@/lib/agentToolMeta";
import { agentApi } from "@/lib/agentApi";
import { enabledSummary, groupToolsByCategory } from "@/lib/agentTools";
import type { AgentTool, AgentToolsResponse } from "@/lib/agentTypes";
import ToolToggleCard from "./ToolToggleCard";

const TOOLS_KEY = ["agent", "tools"];

/** 工具列表整列重算（启用计数 / 总数随启用态变更同步，乐观更新缓存用）。 */
function withCounts(tools: AgentTool[]): AgentToolsResponse {
  const { enabled, total } = enabledSummary(tools);
  return { tools, enabledCount: enabled, totalCount: total };
}

/**
 * 右侧主面板：概览（已启用 X / 共 Y）+ 批量启停 + 全禁用降级提示 + 按分类分组的工具开关卡片。
 * 切换走乐观更新（先改缓存再请求），失败回滚并以 aria-live 文案提示，成功后 invalidate 校正概览。
 */
export default function AgentToolsSection() {
  const qc = useQueryClient();
  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: TOOLS_KEY,
    queryFn: agentApi.tools.list,
  });

  // 失败回滚的内联反馈（H9）：短暂提示后自动清除；卸载清理计时器。
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

  const optimistic = (next: (prev: AgentToolsResponse) => AgentToolsResponse) => async () => {
    await qc.cancelQueries({ queryKey: TOOLS_KEY });
    const prev = qc.getQueryData<AgentToolsResponse>(TOOLS_KEY);
    if (prev) qc.setQueryData(TOOLS_KEY, next(prev));
    return { prev };
  };
  const rollback = (msg: string) => (_e: unknown, _v: unknown, ctx?: { prev?: AgentToolsResponse }) => {
    if (ctx?.prev) qc.setQueryData(TOOLS_KEY, ctx.prev);
    flash(msg);
  };
  const settle = () => {
    void qc.invalidateQueries({ queryKey: TOOLS_KEY });
  };

  const toggle = useMutation({
    mutationFn: ({ name, enabled }: { name: string; enabled: boolean }) =>
      agentApi.tools.setEnabled(name, enabled),
    onMutate: ({ name, enabled }) =>
      optimistic((prev) =>
        withCounts(prev.tools.map((t) => (t.name === name ? { ...t, enabled } : t))),
      )(),
    onError: rollback("切换失败，已恢复原状态，请稍后重试。"),
    onSettled: settle,
  });
  const bulk = useMutation({
    mutationFn: (enabled: boolean) => agentApi.tools.setAll(enabled),
    onMutate: (enabled: boolean) =>
      optimistic((prev) => withCounts(prev.tools.map((t) => ({ ...t, enabled }))))(),
    onError: rollback("批量操作失败，已恢复原状态，请稍后重试。"),
    onSettled: settle,
  });

  if (isLoading) {
    return (
      <div className="flex flex-col gap-3">
        <Skeleton className="h-16" />
        <Skeleton className="h-32" />
        <Skeleton className="h-32" />
      </div>
    );
  }
  if (isError || !data) {
    return (
      <div className="card flex items-center justify-between gap-3 p-4 text-sm text-[var(--short)]">
        <span>加载工具列表失败：{(error as Error)?.message || "请稍后重试"}</span>
        <Button variant="ghost" size="sm" onClick={() => void refetch()}>
          重试
        </Button>
      </div>
    );
  }

  const groups = groupToolsByCategory(data.tools);

  return (
    <div className="flex flex-col gap-5 pb-6">
      <ToolsHeader
        enabledCount={data.enabledCount}
        totalCount={data.totalCount}
        busy={bulk.isPending}
        onEnableAll={() => bulk.mutate(true)}
        onDisableAll={() => bulk.mutate(false)}
      />
      {notice && (
        <p role="status" aria-live="polite" className="text-[11px] font-medium text-[var(--short)]">
          {notice}
        </p>
      )}
      {data.enabledCount === 0 && (
        <div className="card border-[var(--short)] p-4 text-sm text-[var(--short)]">
          已禁用全部工具：AI 助手将只能进行文字对话，无法读取真实行情或运行回测。
        </div>
      )}

      {groups.map(({ category, tools }) => (
        <ToolGroup
          key={category}
          title={TOOL_CATEGORIES[category].title}
          desc={TOOL_CATEGORIES[category].desc}
        >
          {tools.map((tool) => (
            <ToolToggleCard
              key={tool.name}
              tool={tool}
              onToggle={(name, enabled) => toggle.mutate({ name, enabled })}
            />
          ))}
        </ToolGroup>
      ))}
    </div>
  );
}

function ToolsHeader({
  enabledCount,
  totalCount,
  busy,
  onEnableAll,
  onDisableAll,
}: {
  enabledCount: number;
  totalCount: number;
  busy: boolean;
  onEnableAll: () => void;
  onDisableAll: () => void;
}) {
  return (
    <div className="card flex items-center justify-between gap-3 p-4">
      <div className="flex min-w-0 flex-col gap-0.5">
        <span className="text-[11px] font-medium uppercase tracking-wide text-[var(--muted)]">
          可用工具
        </span>
        <span className="text-sm font-semibold text-[var(--text)]" aria-live="polite">
          已启用 {enabledCount} / 共 {totalCount}
        </span>
      </div>
      <div className="flex items-center gap-1.5">
        <Badge tone={enabledCount === 0 ? "short" : "long"}>
          {enabledCount === 0 ? "全部关闭" : enabledCount === totalCount ? "全部开启" : "部分开启"}
        </Badge>
        <Button
          variant="ghost"
          size="sm"
          onClick={onEnableAll}
          disabled={busy || enabledCount === totalCount}
        >
          全部启用
        </Button>
        <Button variant="ghost" size="sm" onClick={onDisableAll} disabled={busy || enabledCount === 0}>
          全部禁用
        </Button>
      </div>
    </div>
  );
}

function ToolGroup({
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
