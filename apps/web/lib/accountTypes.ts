// 设置中心 · 实盘账户类型（与后端 schemas account_out / OkxAccount* 对齐，camelCase）。
// 脱敏形：绝不含 raw secret / passphrase / 完整 api_key（R-Secret）。

// 账户（脱敏形）：列表与卡片展示用。
export interface OkxAccount {
  id: number;
  label: string;
  apiKeyPreview: string; // 末 4 位掩码，如 ····c848
  hasSecret: boolean;
  hasPassphrase: boolean;
  ipWhitelist: string;
  permissions: string;
  isDefault: boolean;
  createdTs: number;
  updatedTs: number;
}

// 表单草稿（新增 / 编辑共用）：secret/passphrase 留空 = 保留原值（编辑）或必填（新增）。
export interface OkxAccountDraft {
  label: string;
  apiKey: string;
  secretKey: string;
  passphrase: string;
  ipWhitelist: string;
  permissions: string;
}

// 连通性测试结果（与后端 OkxAccountTestResult 对齐）。
export interface OkxAccountTestResult {
  ok: boolean;
  latencyMs?: number;
  code?: string;
  message?: string;
}

// 详情 · 分币种余额行。
export interface OkxAccountBalanceRow {
  ccy: string;
  eq: number | null;
  availBal: number | null;
  frozenBal: number | null;
  eqUsd: number | null;
}

// 详情 · 单持仓。
export interface OkxAccountPosition {
  instId: string;
  instType: string;
  posSide: string;
  pos: number | null;
  avgPx: number | null;
  last: number | null;
  upl: number | null;
  uplRatio: number | null;
  lever: number | null;
  marginMode: string;
}

// 详情聚合（GET /accounts/{id}/detail）：信息 + 资产 + 持仓。
export interface OkxAccountDetail {
  info: { uid: string; acctLevel: string; acctLevelLabel: string; permissions: string[] };
  balance: { totalEqUsd: number | null; details: OkxAccountBalanceRow[] };
  positions: OkxAccountPosition[];
}

// GET /accounts 响应。
export interface OkxAccountsListResponse {
  accounts: OkxAccount[];
  defaultId: number | null;
}
