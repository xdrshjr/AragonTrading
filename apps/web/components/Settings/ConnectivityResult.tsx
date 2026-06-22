"use client";

// 连通性测试结果的共享最小形：AI 模型服务（ProviderTestResult）与实盘账户（OkxAccountTestResult）
// 同形，故放宽 prop 为此最小形以复用同一组件（P2-TestResultType），勿复制组件。
export interface TestResultLike {
  ok: boolean;
  latencyMs?: number;
  code?: string;
  message?: string;
}

// 错误码 → 友好中文（与后端 probe / retry / OKX 私有签名错误码对齐）。
const CODE_HINT: Record<string, string> = {
  // —— AI 模型服务 ——
  AGENT_AUTH_ERROR: "密钥无效或无权限",
  AGENT_NOT_CONFIGURED: "未填写密钥",
  INVALID_BASE_URL: "API URL 不合法",
  CONNECTION_FAILED: "连接失败",
  LLM_HTTP_ERROR: "服务返回错误",
  LLM_NETWORK_ERROR: "网络错误",
  // —— 实盘账户（OKX V5 私有签名语义码，R-Reuse）——
  ACCOUNT_NOT_CONFIGURED: "请补全密钥",
  OKX_AUTH_KEY: "API Key 无效",
  OKX_AUTH_SIGN: "签名不匹配（Secret Key 错误）",
  OKX_AUTH_PASSPHRASE: "Passphrase 错误",
  OKX_IP_DENIED: "当前出口 IP 未加入白名单",
  OKX_TIME_SKEW: "本机时间与 OKX 偏差过大，请校准系统时间",
  OKX_PERMISSION: "API Key 权限不足（未开读取）",
  OKX_ERROR: "OKX 业务错误",
  OKX_HTTP_ERROR: "OKX 服务返回错误",
};

/** 连通性结果行：loading（转圈）/ ✓ 连通+时延 / ✗ 错误码+消息（hover 看详情）。
 * 根容器 role="status" aria-live="polite"，使状态变化对读屏用户即时朗读（H6）。 */
export default function ConnectivityResult({
  loading,
  result,
}: {
  loading: boolean;
  result: TestResultLike | null;
}) {
  return (
    <span role="status" aria-live="polite" className="inline-flex items-center">
      {renderState(loading, result)}
    </span>
  );
}

function renderState(loading: boolean, result: TestResultLike | null) {
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
