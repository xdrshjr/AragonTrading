// 设置中心 · AI 模型服务的纯函数（脱敏 / 校验 / 分组 / 草稿工厂），便于单测与组件复用。
import type { AgentProvider, ApiStandard, ProviderDraft } from "./agentTypes";

// 官方预置的 UI 文案（与后端 OFFICIAL_PRESETS slug 对齐）。
export const OFFICIAL_PRESET_UI: Record<string, { hint: string }> = {
  openai: { hint: "OpenAI 官方 / 兼容端点（gpt-4o 等）" },
  anthropic: { hint: "Anthropic Claude 官方端点" },
  gemini: { hint: "Google Gemini（OpenAI 兼容端点）" },
  kimi: { hint: "Kimi · .env 内置默认服务" },
};

export const API_STANDARD_LABEL: Record<ApiStandard, string> = {
  anthropic: "Anthropic 标准",
  openai: "OpenAI 标准",
};

/** 密钥脱敏：仅保留末 4 位、前缀点号；空串返回空（前端从不持有完整明文）。 */
export function maskKey(key: string): string {
  const trimmed = (key || "").trim();
  return trimmed ? `····${trimmed.slice(-4)}` : "";
}

export interface ProviderDraftErrors {
  label?: string;
  baseUrl?: string;
  model?: string;
}

/** 校验草稿（边界一次性，与后端 INVALID_BASE_URL 对齐）；返回字段→错误信息，空对象即合法。 */
export function validateProviderDraft(draft: ProviderDraft): ProviderDraftErrors {
  const errors: ProviderDraftErrors = {};
  if (!draft.label.trim()) errors.label = "请填写服务名称";
  const url = draft.baseUrl.trim();
  if (!url) errors.baseUrl = "请填写 API URL";
  else if (!/^https?:\/\//i.test(url)) errors.baseUrl = "URL 需以 http:// 或 https:// 开头";
  if (!draft.model.trim()) errors.model = "请填写模型名称";
  return errors;
}

export function isDraftValid(draft: ProviderDraft): boolean {
  return Object.keys(validateProviderDraft(draft)).length === 0;
}

/** 按 kind 分组（官方在前、自定义在后，由后端列表顺序保证；本函数仅拆分）。 */
export function groupProviders(providers: AgentProvider[]): {
  official: AgentProvider[];
  custom: AgentProvider[];
} {
  const official: AgentProvider[] = [];
  const custom: AgentProvider[] = [];
  for (const p of providers) {
    if (p.kind === "custom") custom.push(p);
    else official.push(p);
  }
  return { official, custom };
}

/** 新增自定义服务的空草稿（默认 anthropic 标准，与 R-Standard 一致）。 */
export function emptyDraft(apiStandard: ApiStandard = "anthropic"): ProviderDraft {
  return { label: "", apiStandard, baseUrl: "", apiKey: "", model: "", userAgent: "" };
}

/** 由既有服务行构造编辑草稿（apiKey 留空 = 保留原 key，前端不回填明文）。 */
export function draftFromProvider(p: AgentProvider): ProviderDraft {
  return {
    label: p.label,
    apiStandard: p.apiStandard,
    baseUrl: p.baseUrl,
    apiKey: "",
    model: p.model,
    userAgent: p.userAgent,
  };
}
