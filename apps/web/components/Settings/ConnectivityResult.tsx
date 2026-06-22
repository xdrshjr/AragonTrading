"use client";

import type { ProviderTestResult } from "@/lib/agentTypes";

// 错误码 → 友好中文（与后端 probe / retry 错误码对齐）。
const CODE_HINT: Record<string, string> = {
  AGENT_AUTH_ERROR: "密钥无效或无权限",
  AGENT_NOT_CONFIGURED: "未填写密钥",
  INVALID_BASE_URL: "API URL 不合法",
  CONNECTION_FAILED: "连接失败",
  LLM_HTTP_ERROR: "服务返回错误",
  LLM_NETWORK_ERROR: "网络错误",
};

/** 连通性结果行：loading（转圈）/ ✓ 连通+时延 / ✗ 错误码+消息（hover 看详情）。 */
export default function ConnectivityResult({
  loading,
  result,
}: {
  loading: boolean;
  result: ProviderTestResult | null;
}) {
  if (loading) {
    return (
      <span className="inline-flex items-center gap-1.5 text-xs text-[var(--muted)]">
        <span className="h-3 w-3 animate-spin rounded-full border-2 border-[var(--border)] border-t-[var(--accent)]" />
        测试中…
      </span>
    );
  }
  if (!result) return null;
  if (result.ok) {
    const latency = typeof result.latencyMs === "number" ? ` · ${Math.round(result.latencyMs)}ms` : "";
    return (
      <span className="inline-flex items-center gap-1.5 text-xs font-medium text-[var(--long)]">
        ✓ 连通{latency}
      </span>
    );
  }
  const hint = (result.code && CODE_HINT[result.code]) || result.message || "测试失败";
  return (
    <span
      className="inline-flex items-center gap-1.5 text-xs font-medium text-[var(--short)]"
      title={result.message || hint}
    >
      ✗ {hint}
    </span>
  );
}
