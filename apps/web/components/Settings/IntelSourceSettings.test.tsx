import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import IntelSourceSettings from "./IntelSourceSettings";

// 这块卡片此前**没有任何用例**（§7.2 说「改」，实读为「新建」，已登记在 spec 的实施发现里）。
// 本文件只钉一件事：evidence-grounding 的「原文取证」组**真的被挂载了**，且它的三把单键
// 逐个走到 `PUT /settings/intel` —— 一个只在代码里存在、界面上打不开的开关，与没有这个开关等价。
const settingsMock = vi.fn();
const saveMock = vi.fn();

vi.mock("@/lib/intelApi", () => ({
  intelApi: {
    settings: () => settingsMock(),
    saveSettings: (patch: unknown) => saveMock(patch),
    providerHealth: async () => ({
      provider: "tavily", rounds: 0, ok: 0, failed: 0, degraded: 0, breakerRemainingS: 0,
    }),
    probe: async () => ({ ok: true, latencyMs: 12, sampleTitle: "t", message: "" }),
    documentHealth: async () => ({
      cachedDocs: 12, bodyBytes: 3 * 1024 * 1024, oldestFetchedTs: 0, lastPrunedTs: 0,
      readerEnabled: true,
    }),
  },
}));

const SETTINGS = {
  enabled: true, injectEnabled: true, provider: "tavily", hasKey: true, keyPreview: "…abcd",
  collectIntervalMin: 30, curateEveryNCollects: 4, dailyCap: 96,
  estimate: { roundsPerDay: 240, inputTokensPerDay: 1_000_000 },
  citeEnabled: true, attributionEnabled: true, verifyEnabled: true, verifyEveryNCurates: 1,
  verifyMaxItems: 3, verifyDocsPerItem: 4, blockedSweepEnabled: true, citeMaxRefs: 8,
  readerEnabled: true, readerTopK: 6, quoteMode: "soft" as const, quoteHitRate: 0.78,
};

beforeEach(() => {
  settingsMock.mockResolvedValue(SETTINGS);
  saveMock.mockResolvedValue(SETTINGS);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("IntelSourceSettings · 原文取证组", () => {
  it("「原文取证」组与其余卡片一起挂载", async () => {
    render(<IntelSourceSettings />);
    await waitFor(() =>
      expect(screen.getByTestId("intel-reader-settings")).toBeInTheDocument());
    expect(screen.getByTestId("intel-source-settings")).toBeInTheDocument();
    expect(screen.getByTestId("intel-closed-loop-switches")).toBeInTheDocument();
  });

  it("三把单键逐个透传到 PUT（不扩 bridge ⇒ 面板改得动、系统永远不读，而且不报错）", async () => {
    render(<IntelSourceSettings />);
    await waitFor(() =>
      expect(screen.getByTestId("intel-reader-settings")).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText("引文要求"), { target: { value: "strict" } });
    await waitFor(() => expect(saveMock).toHaveBeenCalled());
    expect(saveMock.mock.calls[0][0]).toMatchObject({ quoteMode: "strict" });

    fireEvent.click(screen.getByRole("switch", { name: "抓取原文" }));
    await waitFor(() => expect(saveMock).toHaveBeenCalledTimes(2));
    expect(saveMock.mock.calls[1][0]).toMatchObject({ readerEnabled: false });
  });

  it("保存时不把只读键（hasKey / estimate）一起 PUT 出去（那会 422）", async () => {
    render(<IntelSourceSettings />);
    await waitFor(() =>
      expect(screen.getByTestId("intel-reader-settings")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("引文要求"), { target: { value: "off" } });
    await waitFor(() => expect(saveMock).toHaveBeenCalled());
    const patch = saveMock.mock.calls[0][0] as Record<string, unknown>;
    expect(patch).not.toHaveProperty("hasKey");
    expect(patch).not.toHaveProperty("estimate");
    expect(patch).not.toHaveProperty("quoteHitRate");
  });
});
