"use client";

import { useState } from "react";
import { Button, Field, Input, Select } from "@/components/ui";
import type { ApiStandard, ProviderDraft } from "@/lib/agentTypes";
import type { ProviderDraftErrors } from "@/lib/settings";

/**
 * 服务商编辑表单（卡内）。official 项锁定 label / api_standard（`editableMeta=false`），
 * 仅可改 URL / MODEL / KEY；custom 项可改全部。APIKEY 用密码框，留空表示保留原值。
 * 无障碍（H6）：每个字段错误经 `${id}-err` 串到 Input 的 aria-describedby；密钥框支持显示/隐藏（H8）。
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
  const [reveal, setReveal] = useState(false);
  const describedBy = (field: keyof ProviderDraftErrors) =>
    errors[field] ? `${idPrefix}-${describeKey[field]}-err` : undefined;

  return (
    <div className="flex flex-col gap-3">
      {editableMeta && (
        <div className="grid grid-cols-2 gap-3">
          <Field label="服务名称" htmlFor={`${idPrefix}-label`} error={errors.label}>
            <Input
              id={`${idPrefix}-label`}
              value={draft.label}
              invalid={!!errors.label}
              aria-describedby={describedBy("label")}
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
          aria-describedby={describedBy("baseUrl")}
          onChange={(e) => patch({ baseUrl: e.target.value })}
          placeholder="https://api.example.com"
        />
      </Field>
      <Field label="模型名称" htmlFor={`${idPrefix}-model`} error={errors.model}>
        <Input
          id={`${idPrefix}-model`}
          value={draft.model}
          invalid={!!errors.model}
          aria-describedby={describedBy("model")}
          onChange={(e) => patch({ model: e.target.value })}
          placeholder="如：gpt-4o"
        />
      </Field>
      <Field
        label="API Key"
        htmlFor={`${idPrefix}-key`}
        hint={hasExistingKey ? "已保存密钥；留空则保留原值" : "密钥仅保存在本地后端，绝不上传"}
      >
        <div className="relative">
          <Input
            id={`${idPrefix}-key`}
            type={reveal ? "text" : "password"}
            autoComplete="off"
            className="pr-10"
            value={draft.apiKey}
            onChange={(e) => patch({ apiKey: e.target.value })}
            placeholder={hasExistingKey ? "•••• 保留原有密钥" : "粘贴 API Key"}
          />
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="absolute inset-y-0 right-1 my-auto h-7 w-7 px-0"
            aria-label={reveal ? "隐藏密钥" : "显示密钥"}
            aria-pressed={reveal}
            title={reveal ? "隐藏密钥" : "显示密钥"}
            onClick={() => setReveal((v) => !v)}
          >
            <EyeIcon off={reveal} />
          </Button>
        </div>
      </Field>
    </div>
  );
}

// 字段 → 控件 id 后缀（与各 Field 的 htmlFor 末段一致，用于拼 `${id}-err`）。
const describeKey: Record<keyof ProviderDraftErrors, string> = {
  label: "label",
  baseUrl: "url",
  model: "model",
};

/** 眼睛图标（内联 SVG，沿用「无三方图标」约定）：off=true 时画一道斜杠表示「点击隐藏」。 */
function EyeIcon({ off }: { off: boolean }) {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7z"
        stroke="currentColor"
        strokeWidth="1.6"
      />
      <circle cx="12" cy="12" r="3" stroke="currentColor" strokeWidth="1.6" />
      {off && <path d="M4 4l16 16" stroke="currentColor" strokeWidth="1.6" />}
    </svg>
  );
}
