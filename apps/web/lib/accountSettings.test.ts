import { describe, expect, it } from "vitest";
import type { OkxAccount } from "./accountTypes";
import {
  acctLevelLabel,
  draftFromAccount,
  emptyDraft,
  isDraftValid,
  maskKey,
  permissionLabel,
  validateAccountDraft,
} from "./accountSettings";

function account(over: Partial<OkxAccount> = {}): OkxAccount {
  return {
    id: 1,
    label: "Dev-JR-US",
    apiKeyPreview: "····c848",
    hasSecret: true,
    hasPassphrase: true,
    ipWhitelist: "203.0.113.10",
    permissions: "读取/交易",
    isDefault: false,
    createdTs: 0,
    updatedTs: 0,
    ...over,
  };
}

describe("accountSettings — maskKey", () => {
  it("keeps only the last 4 chars with a dotted prefix", () => {
    // 占位假凭证，绝不写入真实开发凭证（R-Secret）。
    expect(maskKey("demo-api-key-not-real-c848")).toBe("····c848");
  });

  it("returns empty string for empty / whitespace key", () => {
    expect(maskKey("")).toBe("");
    expect(maskKey("   ")).toBe("");
  });
});

describe("accountSettings — validateAccountDraft", () => {
  it("rejects missing label / apiKey / secretKey / passphrase in new mode", () => {
    const errors = validateAccountDraft(emptyDraft());
    expect(errors.label).toBeTruthy();
    expect(errors.apiKey).toBeTruthy();
    expect(errors.secretKey).toBeTruthy();
    expect(errors.passphrase).toBeTruthy();
  });

  it("accepts blank secret/passphrase in edit mode (preserve original)", () => {
    const draft = {
      label: "Dev",
      apiKey: "ak-123",
      secretKey: "",
      passphrase: "",
      ipWhitelist: "",
      permissions: "",
    };
    const errors = validateAccountDraft(draft, false);
    expect(errors).toEqual({});
    expect(isDraftValid(draft, false)).toBe(true);
    // 同一草稿在新增态（requireSecrets=true）应报 secret/passphrase 缺失
    expect(isDraftValid(draft, true)).toBe(false);
  });

  it("accepts a blank apiKey in edit mode (preserve original, spec §8)", () => {
    // 编辑态仅改 IP：凭证三件套（apiKey/secretKey/passphrase）留空 = 保留原值 → 合法、可保存。
    const draft = {
      label: "Dev",
      apiKey: "",
      secretKey: "",
      passphrase: "",
      ipWhitelist: "1.2.3.4",
      permissions: "",
    };
    expect(validateAccountDraft(draft, false)).toEqual({});
    expect(isDraftValid(draft, false)).toBe(true);
    // 新增态（requireSecrets=true）同一草稿应报 apiKey/secret/passphrase 缺失。
    expect(isDraftValid(draft, true)).toBe(false);
  });

  it("accepts a complete new draft", () => {
    const draft = {
      label: "Dev",
      apiKey: "ak-123",
      secretKey: "sk-456",
      passphrase: "pp-789",
      ipWhitelist: "1.2.3.4",
      permissions: "读取/交易",
    };
    expect(validateAccountDraft(draft)).toEqual({});
    expect(isDraftValid(draft)).toBe(true);
  });
});

describe("accountSettings — emptyDraft", () => {
  it("has all blank fields", () => {
    expect(emptyDraft()).toEqual({
      label: "",
      apiKey: "",
      secretKey: "",
      passphrase: "",
      ipWhitelist: "",
      permissions: "",
    });
  });
});

describe("accountSettings — draftFromAccount", () => {
  it("copies informational fields but never raw secret/passphrase/key", () => {
    const draft = draftFromAccount(account());
    expect(draft.apiKey).toBe("");
    expect(draft.secretKey).toBe("");
    expect(draft.passphrase).toBe("");
    expect(draft.label).toBe("Dev-JR-US");
    expect(draft.ipWhitelist).toBe("203.0.113.10");
    expect(draft.permissions).toBe("读取/交易");
  });
});

describe("accountSettings — label maps", () => {
  it("maps account level codes to Chinese labels with fallback", () => {
    expect(acctLevelLabel("2")).toBe("单币种保证金模式");
    expect(acctLevelLabel("9")).toBe("9");
    expect(acctLevelLabel("")).toBe("—");
  });

  it("maps known permission codes and passes through unknown ones", () => {
    expect(permissionLabel("trade")).toBe("交易");
    expect(permissionLabel("custom")).toBe("custom");
  });
});
