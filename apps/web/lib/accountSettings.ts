// 设置中心 · 实盘账户纯函数（脱敏 / 校验 / 草稿工厂 / 文案映射），便于单测与组件复用。
import type { OkxAccount, OkxAccountDraft } from "./accountTypes";

/** 密钥脱敏：仅保留末 4 位、前缀点号；空串返回空（前端从不持有完整明文）。 */
export function maskKey(key: string): string {
  const trimmed = (key || "").trim();
  return trimmed ? `····${trimmed.slice(-4)}` : "";
}

export interface AccountDraftErrors {
  label?: string;
  apiKey?: string;
  secretKey?: string;
  passphrase?: string;
}

/** 校验草稿（边界一次性）；`requireSecrets=false` 时（编辑态）凭证三件套留空合法（保留原值）。 */
export function validateAccountDraft(
  draft: OkxAccountDraft,
  requireSecrets = true,
): AccountDraftErrors {
  const errors: AccountDraftErrors = {};
  if (!draft.label.trim()) errors.label = "请填写备注名";
  // apiKey/secretKey/passphrase 后端均不回前端：新增态必填，编辑态留空 = 保留原值
  // （requireSecrets=false），与后端「显式给出才改」契约（spec §8）一致。
  if (requireSecrets) {
    if (!draft.apiKey.trim()) errors.apiKey = "请填写 API Key";
    if (!draft.secretKey.trim()) errors.secretKey = "请填写 Secret Key";
    if (!draft.passphrase.trim()) errors.passphrase = "请填写 Passphrase";
  }
  return errors;
}

export function isDraftValid(draft: OkxAccountDraft, requireSecrets = true): boolean {
  return Object.keys(validateAccountDraft(draft, requireSecrets)).length === 0;
}

/** 新增账户的空草稿。 */
export function emptyDraft(): OkxAccountDraft {
  return { label: "", apiKey: "", secretKey: "", passphrase: "", ipWhitelist: "", permissions: "" };
}

/** 由既有账户行构造编辑草稿（secret/passphrase 留空 = 保留原值，前端不回填明文）。 */
export function draftFromAccount(a: OkxAccount): OkxAccountDraft {
  return {
    label: a.label,
    apiKey: "",
    secretKey: "",
    passphrase: "",
    ipWhitelist: a.ipWhitelist,
    permissions: a.permissions,
  };
}

// OKX 账户层级（acctLv）→ 中文文案（后端已下发 acctLevelLabel；此为前端兜底）。
const ACCT_LEVEL_LABEL: Record<string, string> = {
  "1": "简单交易模式",
  "2": "单币种保证金模式",
  "3": "跨币种保证金模式",
  "4": "组合保证金模式",
};

export function acctLevelLabel(level: string): string {
  return ACCT_LEVEL_LABEL[level] || level || "—";
}

// OKX 权限码 → 中文文案（account/config.perm 取到的权威权限）。
const PERMISSION_LABEL: Record<string, string> = {
  read_only: "只读",
  trade: "交易",
  withdraw: "提现",
};

export function permissionLabel(perm: string): string {
  return PERMISSION_LABEL[perm] || perm;
}
