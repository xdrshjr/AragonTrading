import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import IntelSourceSettings from "./IntelSourceSettings";

// 这块卡片此前**没有任何用例**（§7.2 说「改」，实读为「新建」，已登记在 spec 的实施发现里）。
// 本文件只钉一件事：evidence-grounding 的「原文取证」组**真的被挂载了**，且它的三把单键
// 逐个走到 `PUT /settings/intel` —— 一个只在代码里存在、界面上打不开的开关，与没有这个开关等价。
const settingsMock = vi.fn();
const saveMock = vi.fn();
const rearmMock = vi.fn();

vi.mock("@/lib/intelApi", () => ({
  intelApi: {
    settings: () => settingsMock(),
    saveSettings: (patch: unknown) => saveMock(patch),
    rearm: () => rearmMock(),
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
  // silent-degradation-repair 轴 B-1 / B-2：解析策略 + 两把只读旋钮的生效值回读。
  readerResolver: "auto" as const, readerHealthVisible: true, readerZeroStreakWarn: 3,
  alertEnabled: true, alertMinImpact: "high" as const, alertCooldownMin: 20, alertDailyCap: 6,
  efficacyEnabled: true, conflictEnabled: true, alertRequireQuote: true,
  alertPendingTtlS: 180,
  // ai-trade-intel-arm-state：装配状态块（`tsc` 强制必填）。
  arm: {
    providerConfigured: true, intelEnabled: true, armed: true, reason: "" as const,
    message: "情报采集正在运行：BTC-USDT-SWAP", detail: "", groupRunning: true,
    armedMembers: ["BTC-USDT-SWAP"], pending: false,
  },
};

const ARM_FAILED = {
  ...SETTINGS,
  arm: {
    providerConfigured: true, intelEnabled: true, armed: false,
    reason: "arm_failed" as const,
    message: "情报流水线装配失败（LLM provider 未配置），检索源配置本身没有问题",
    detail: "LLM provider 未配置", groupRunning: true, armedMembers: [], pending: false,
  },
};

beforeEach(() => {
  settingsMock.mockResolvedValue(SETTINGS);
  saveMock.mockResolvedValue(SETTINGS);
  rearmMock.mockResolvedValue(SETTINGS);
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

describe("IntelSourceSettings · 生效状态卡（ai-trade-intel-arm-state）", () => {
  it("已生效时说清楚在为哪些标的跑，且不给一个点了什么也不发生的按钮", async () => {
    render(<IntelSourceSettings />);
    await waitFor(() =>
      expect(screen.getByTestId("intel-arm-status")).toBeInTheDocument());
    expect(screen.getByTestId("intel-arm-message")).toHaveTextContent(
      "情报采集正在运行：BTC-USDT-SWAP");
    expect(screen.queryByTestId("intel-arm-rearm")).toBeNull();
  });

  it("装配失败 ⇒ 露出【重新装配】，点一次真的发出 POST 并用回执刷新这张卡", async () => {
    settingsMock.mockResolvedValue(ARM_FAILED);
    rearmMock.mockResolvedValue(SETTINGS);
    render(<IntelSourceSettings />);
    await waitFor(() =>
      expect(screen.getByTestId("intel-arm-rearm")).toBeInTheDocument());
    expect(screen.getByTestId("intel-arm-message")).toHaveTextContent("装配失败");

    fireEvent.click(screen.getByTestId("intel-arm-rearm"));
    await waitFor(() => expect(rearmMock).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(screen.getByTestId("intel-arm-message")).toHaveTextContent("正在运行"));
    expect(screen.queryByTestId("intel-arm-rearm")).toBeNull();
  });
});
