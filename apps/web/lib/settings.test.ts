import { describe, expect, it } from "vitest";
import type { AgentProvider } from "./agentTypes";
import {
  draftFromProvider,
  emptyDraft,
  groupProviders,
  isDraftValid,
  maskKey,
  validateProviderDraft,
} from "./settings";

function provider(over: Partial<AgentProvider> = {}): AgentProvider {
  return {
    id: 1,
    slug: "openai",
    kind: "official",
    label: "OpenAI",
    apiStandard: "openai",
    baseUrl: "https://api.openai.com",
    model: "gpt-4o",
    userAgent: "claude-code/1.0",
    hasKey: false,
    keyPreview: "",
    isDefault: false,
    deletable: false,
    createdTs: 0,
    updatedTs: 0,
    ...over,
  };
}

describe("settings — maskKey", () => {
  it("keeps only the last 4 chars with a dotted prefix", () => {
    expect(maskKey("sk-abcdwxyz")).toBe("····wxyz");
  });

  it("returns empty string for empty / whitespace key", () => {
    expect(maskKey("")).toBe("");
    expect(maskKey("   ")).toBe("");
  });
});

describe("settings — validateProviderDraft", () => {
  it("rejects empty label / url / model", () => {
    const errors = validateProviderDraft(emptyDraft());
    expect(errors.label).toBeTruthy();
    expect(errors.baseUrl).toBeTruthy();
    expect(errors.model).toBeTruthy();
  });

  it("rejects a non-http(s) scheme", () => {
    const errors = validateProviderDraft({
      label: "x",
      apiStandard: "openai",
      baseUrl: "ftp://nope",
      apiKey: "",
      model: "m",
    });
    expect(errors.baseUrl).toBeTruthy();
    expect(errors.label).toBeUndefined();
    expect(errors.model).toBeUndefined();
  });

  it("accepts a complete, valid draft", () => {
    const draft = {
      label: "网关",
      apiStandard: "openai" as const,
      baseUrl: "https://gw.test/v1/chat/completions",
      apiKey: "",
      model: "gpt-4o",
    };
    expect(validateProviderDraft(draft)).toEqual({});
    expect(isDraftValid(draft)).toBe(true);
  });
});

describe("settings — groupProviders", () => {
  it("splits official and custom", () => {
    const rows = [
      provider({ id: 1, kind: "official", slug: "openai" }),
      provider({ id: 2, kind: "custom", slug: "uuid-a", deletable: true }),
      provider({ id: 3, kind: "official", slug: "kimi" }),
    ];
    const { official, custom } = groupProviders(rows);
    expect(official.map((p) => p.id)).toEqual([1, 3]);
    expect(custom.map((p) => p.id)).toEqual([2]);
  });
});

describe("settings — draftFromProvider", () => {
  it("copies fields but never the raw key (apiKey blank)", () => {
    const draft = draftFromProvider(provider({ hasKey: true, keyPreview: "····1234", model: "m9" }));
    expect(draft.apiKey).toBe("");
    expect(draft.model).toBe("m9");
    expect(draft.baseUrl).toBe("https://api.openai.com");
  });
});
