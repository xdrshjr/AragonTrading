"use client";

import { useState } from "react";
import { Button, Field, Input } from "@/components/ui";
import type { OkxAccountDraft } from "@/lib/accountTypes";
import type { AccountDraftErrors } from "@/lib/accountSettings";

/**
 * 实盘账户编辑表单（卡内）。字段顺序：备注名 → API Key → Secret Key → Passphrase → IP 白名单 → 权限备注。
 * Secret Key / Passphrase 用密码框 + 显隐切换；编辑态留空表示保留原值。
 * 无障碍（H6）：每个字段错误经 `${id}-err` 串到 Input 的 aria-describedby。
 */
export default function OkxAccountEditForm({
  draft,
  setDraft,
  errors,
  hasExistingSecret,
  hasExistingPassphrase,
  existingApiKeyPreview,
  idPrefix,
}: {
  draft: OkxAccountDraft;
  setDraft: (next: OkxAccountDraft) => void;
  errors: AccountDraftErrors;
  hasExistingSecret: boolean;
  hasExistingPassphrase: boolean;
  existingApiKeyPreview?: string; // 编辑态展示「留空则保留原值」；新增态不传
  idPrefix: string;
}) {
  const patch = (p: Partial<OkxAccountDraft>) => setDraft({ ...draft, ...p });
  const [revealSecret, setRevealSecret] = useState(false);
  const [revealPass, setRevealPass] = useState(false);
  const describedBy = (field: keyof AccountDraftErrors, suffix: string) =>
    errors[field] ? `${idPrefix}-${suffix}-err` : undefined;

  return (
    <div className="flex flex-col gap-3">
      <Field label="备注名" htmlFor={`${idPrefix}-label`} error={errors.label}>
        <Input
          id={`${idPrefix}-label`}
          value={draft.label}
          invalid={!!errors.label}
          aria-describedby={describedBy("label", "label")}
          onChange={(e) => patch({ label: e.target.value })}
          placeholder="如：Dev-JR-US"
        />
      </Field>

      <Field
        label="API Key"
        htmlFor={`${idPrefix}-key`}
        error={errors.apiKey}
        hint={
          errors.apiKey
            ? undefined
            : existingApiKeyPreview
              ? `已保存 ${existingApiKeyPreview}；留空则保留原值`
              : undefined
        }
      >
        <Input
          id={`${idPrefix}-key`}
          autoComplete="off"
          value={draft.apiKey}
          invalid={!!errors.apiKey}
          aria-describedby={describedBy("apiKey", "key")}
          onChange={(e) => patch({ apiKey: e.target.value })}
          placeholder={existingApiKeyPreview ? "•••• 保留原有 API Key" : "OKX API Key"}
        />
      </Field>

      <Field
        label="Secret Key"
        htmlFor={`${idPrefix}-secret`}
        error={errors.secretKey}
        hint={
          errors.secretKey
            ? undefined
            : hasExistingSecret
              ? "已保存；留空则保留原值"
              : "密钥仅保存在本地后端，绝不上传"
        }
      >
        <RevealInput
          id={`${idPrefix}-secret`}
          reveal={revealSecret}
          onToggle={() => setRevealSecret((v) => !v)}
          value={draft.secretKey}
          invalid={!!errors.secretKey}
          describedBy={describedBy("secretKey", "secret")}
          onChange={(v) => patch({ secretKey: v })}
          placeholder={hasExistingSecret ? "•••• 保留原有密钥" : "粘贴 Secret Key"}
        />
      </Field>

      <Field
        label="Passphrase"
        htmlFor={`${idPrefix}-pass`}
        error={errors.passphrase}
        hint={
          errors.passphrase
            ? undefined
            : hasExistingPassphrase
              ? "已保存；留空则保留原值"
              : "创建 API Key 时设置的 Passphrase（OKX V5 必填）"
        }
      >
        <RevealInput
          id={`${idPrefix}-pass`}
          reveal={revealPass}
          onToggle={() => setRevealPass((v) => !v)}
          value={draft.passphrase}
          invalid={!!errors.passphrase}
          describedBy={describedBy("passphrase", "pass")}
          onChange={(v) => patch({ passphrase: v })}
          placeholder={hasExistingPassphrase ? "•••• 保留原有 Passphrase" : "粘贴 Passphrase"}
        />
      </Field>

      <div className="grid grid-cols-2 gap-3">
        <Field label="IP 白名单" htmlFor={`${idPrefix}-ip`} hint="OKX 侧绑定的出口 IP，逗号分隔">
          <Input
            id={`${idPrefix}-ip`}
            value={draft.ipWhitelist}
            onChange={(e) => patch({ ipWhitelist: e.target.value })}
            placeholder="如：203.0.113.10"
          />
        </Field>
        <Field label="权限备注" htmlFor={`${idPrefix}-perm`} hint="仅备注；详情以 OKX 权威权限为准">
          <Input
            id={`${idPrefix}-perm`}
            value={draft.permissions}
            onChange={(e) => patch({ permissions: e.target.value })}
            placeholder="如：读取/交易"
          />
        </Field>
      </div>
    </div>
  );
}

/** 密码框 + 显隐切换（Secret / Passphrase 共用）。 */
function RevealInput({
  id,
  reveal,
  onToggle,
  value,
  invalid,
  describedBy,
  onChange,
  placeholder,
}: {
  id: string;
  reveal: boolean;
  onToggle: () => void;
  value: string;
  invalid: boolean;
  describedBy?: string;
  onChange: (next: string) => void;
  placeholder: string;
}) {
  return (
    <div className="relative">
      <Input
        id={id}
        type={reveal ? "text" : "password"}
        autoComplete="off"
        className="pr-10"
        value={value}
        invalid={invalid}
        aria-describedby={describedBy}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
      />
      <Button
        type="button"
        variant="ghost"
        size="sm"
        className="absolute inset-y-0 right-1 my-auto h-7 w-7 px-0"
        aria-label={reveal ? "隐藏" : "显示"}
        aria-pressed={reveal}
        title={reveal ? "隐藏" : "显示"}
        onClick={onToggle}
      >
        <EyeIcon off={reveal} />
      </Button>
    </div>
  );
}

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
