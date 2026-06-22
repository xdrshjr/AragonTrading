"use client";

import { Field, Input, Select } from "@/components/ui";
import type { ApiStandard, ProviderDraft } from "@/lib/agentTypes";
import type { ProviderDraftErrors } from "@/lib/settings";

/**
 * 服务商编辑表单（卡内）。official 项锁定 label / api_standard（`editableMeta=false`），
 * 仅可改 URL / MODEL / KEY；custom 项可改全部。APIKEY 用密码框，留空表示保留原值。
 */
export default function ProviderEditForm({
  draft,
  setDraft,
  errors,
  editableMeta,
  hasExistingKey,
  idPrefix,
}: {
  draft: ProviderDraft;
  setDraft: (next: ProviderDraft) => void;
  errors: ProviderDraftErrors;
  editableMeta: boolean;
  hasExistingKey: boolean;
  idPrefix: string;
}) {
  const patch = (p: Partial<ProviderDraft>) => setDraft({ ...draft, ...p });

  return (
    <div className="flex flex-col gap-3">
      {editableMeta && (
        <div className="grid grid-cols-2 gap-3">
          <Field label="服务名称" htmlFor={`${idPrefix}-label`} error={errors.label}>
            <Input
              id={`${idPrefix}-label`}
              value={draft.label}
              invalid={!!errors.label}
              onChange={(e) => patch({ label: e.target.value })}
              placeholder="如：我的网关"
            />
          </Field>
          <Field
            label="API 标准"
            htmlFor={`${idPrefix}-std`}
            hint="Anthropic Messages / OpenAI Chat"
          >
            <Select
              id={`${idPrefix}-std`}
              value={draft.apiStandard}
              onChange={(e) => patch({ apiStandard: e.target.value as ApiStandard })}
            >
              <option value="anthropic">Anthropic 标准</option>
              <option value="openai">OpenAI 标准</option>
            </Select>
          </Field>
        </div>
      )}
      <Field label="API URL" htmlFor={`${idPrefix}-url`} error={errors.baseUrl}>
        <Input
          id={`${idPrefix}-url`}
          value={draft.baseUrl}
          invalid={!!errors.baseUrl}
          onChange={(e) => patch({ baseUrl: e.target.value })}
          placeholder="https://api.example.com"
        />
      </Field>
      <Field label="模型名称" htmlFor={`${idPrefix}-model`} error={errors.model}>
        <Input
          id={`${idPrefix}-model`}
          value={draft.model}
          invalid={!!errors.model}
          onChange={(e) => patch({ model: e.target.value })}
          placeholder="如：gpt-4o"
        />
      </Field>
      <Field
        label="API Key"
        htmlFor={`${idPrefix}-key`}
        hint={hasExistingKey ? "已保存密钥；留空则保留原值" : "密钥仅保存在本地后端，绝不上传"}
      >
        <Input
          id={`${idPrefix}-key`}
          type="password"
          autoComplete="off"
          value={draft.apiKey}
          onChange={(e) => patch({ apiKey: e.target.value })}
          placeholder={hasExistingKey ? "•••• 保留原有密钥" : "粘贴 API Key"}
        />
      </Field>
    </div>
  );
}
