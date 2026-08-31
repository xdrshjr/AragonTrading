import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { IntelSettings } from "@/lib/intelTypes";
import IntelReaderSettings from "./IntelReaderSettings";

const health = vi.fn();

vi.mock("@/lib/intelApi", () => ({
  intelApi: {
    documentHealth: () => health(),
  },
}));

function settings(over: Partial<IntelSettings> = {}): IntelSettings {
  return {
    enabled: true, injectEnabled: true, provider: "tavily", hasKey: true, keyPreview: "…1234",
    collectIntervalMin: 30, curateEveryNCollects: 4, dailyCap: 96,
    estimate: { roundsPerDay: 10, inputTokensPerDay: 1000 },
    citeEnabled: true, attributionEnabled: true, verifyEnabled: true,
    verifyEveryNCurates: 1, verifyMaxItems: 3, verifyDocsPerItem: 4,
    blockedSweepEnabled: true, citeMaxRefs: 8,
    readerEnabled: true, readerTopK: 6, quoteMode: "soft", quoteHitRate: 0.78,
    alertEnabled: true, alertMinImpact: "high", alertCooldownMin: 20, alertDailyCap: 6,
    efficacyEnabled: true, conflictEnabled: true, alertRequireQuote: true,
    alertPendingTtlS: 180,
    // ai-trade-intel-arm-state 轴 C：装配状态块**必填**（`tsc` 强制，不是可选）。
    arm: {
      providerConfigured: true, intelEnabled: true, armed: true, reason: "" as const,
      message: "情报采集正在运行：BTC-USDT-SWAP", detail: "", groupRunning: true,
      armedMembers: ["BTC-USDT-SWAP"], pending: false,
    },
    ...over,
  };
}

beforeEach(() => {
  health.mockResolvedValue({
    cachedDocs: 1842, bodyBytes: 18 * 1024 * 1024, oldestFetchedTs: 1_700_000_000,
    lastPrunedTs: Math.round(Date.now() / 1000) - 7200, readerEnabled: true,
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("IntelReaderSettings", () => {
  it("三选一往返：改「引文要求」立刻保存那一个键", () => {
    const onSave = vi.fn();
    render(<IntelReaderSettings settings={settings()} busy={false} onSave={onSave} />);
    fireEvent.change(screen.getByLabelText("引文要求"), { target: { value: "strict" } });
    expect(onSave).toHaveBeenCalledWith({ quoteMode: "strict" });
  });

  it("抓取原文的开关与功能同批交付（默认开启的出网能力必须当轮可关）", () => {
    const onSave = vi.fn();
    render(<IntelReaderSettings settings={settings()} busy={false} onSave={onSave} />);
    fireEvent.click(screen.getByRole("switch", { name: "抓取原文" }));
    expect(onSave).toHaveBeenCalledWith({ readerEnabled: false });
  });

  it("数字框**失焦才保存**：逐次敲键就 PUT 会发出一串中间态并让输入自己回退", () => {
    const onSave = vi.fn();
    render(<IntelReaderSettings settings={settings()} busy={false} onSave={onSave} />);
    const input = screen.getByLabelText("每轮抓取篇数");
    fireEvent.change(input, { target: { value: "1" } });
    fireEvent.change(input, { target: { value: "12" } });
    expect(onSave).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "保存篇数" }));
    expect(onSave).toHaveBeenCalledWith({ readerTopK: 12 });
  });

  it("没改过 ⇒ 保存按钮不可点（一个永远亮着的按钮说不清自己有没有事情可做）", () => {
    render(<IntelReaderSettings settings={settings()} busy={false} onSave={vi.fn()} />);
    expect(screen.getByRole("button", { name: "保存篇数" })).toBeDisabled();
  });

  it("命中率与那条 70% 判据常驻（只写在文档里的运维判据等于没有判据）", () => {
    render(<IntelReaderSettings settings={settings({ quoteHitRate: 0.62 })} busy={false}
                                onSave={vi.fn()} />);
    const hint = screen.getByTestId("intel-strict-hint");
    expect(hint).toHaveTextContent("近 20 轮引文命中率 62%");
    expect(hint).toHaveTextContent("命中率低于 70% 时不建议开启");
  });

  it("还没有命中率读数 ⇒ 说「先跑几轮」，绝不显示一个假的 0%", () => {
    render(<IntelReaderSettings settings={settings({ quoteHitRate: null })} busy={false}
                                onSave={vi.fn()} />);
    const hint = screen.getByTestId("intel-strict-hint");
    expect(hint).not.toHaveTextContent("0%");
    expect(hint).toHaveTextContent("还没有可用的命中率读数");
  });

  it("原文缓存那一行读的是后端算的真实字节", async () => {
    render(<IntelReaderSettings settings={settings()} busy={false} onSave={vi.fn()} />);
    await waitFor(() =>
      expect(screen.getByTestId("intel-doc-cache")).toHaveTextContent("1842 条 / 约 18.0 MB"));
    expect(screen.getByTestId("intel-doc-cache")).toHaveTextContent("最近清理 2 小时前");
  });

  it("读不到缓存读数 ⇒ 整行不渲染，不显示一个假的 0", async () => {
    health.mockRejectedValue(new Error("boom"));
    render(<IntelReaderSettings settings={settings()} busy={false} onSave={vi.fn()} />);
    await waitFor(() => expect(health).toHaveBeenCalled());
    expect(screen.queryByTestId("intel-doc-cache")).toBeNull();
  });

  it("关掉抓取 ⇒ 篇数输入框失效（一个改了也不生效的框比没有这个框更坏）", () => {
    render(<IntelReaderSettings settings={settings({ readerEnabled: false })} busy={false}
                                onSave={vi.fn()} />);
    expect(screen.getByLabelText("每轮抓取篇数")).toBeDisabled();
  });

  // 终审补：后端在生效值上把 strict + 抓取关闭降回 soft；这里不让用户先走进那一步，
  // 并把「为什么这一项现在不可选」说出来——一个无声变灰的选项等于一次没有解释的拒绝。
  it("关掉抓取 ⇒ 「必须附可核对引文」不可选，且说明原因", () => {
    render(<IntelReaderSettings settings={settings({ readerEnabled: false })} busy={false}
                                onSave={vi.fn()} />);
    const strict = screen.getByRole("option", { name: "必须附可核对引文" });
    expect(strict).toBeDisabled();
    expect(screen.getByTestId("intel-strict-hint")).toHaveTextContent("抓取原文已关闭");
  });

  it("开着抓取 ⇒ 那一项照常可选", () => {
    render(<IntelReaderSettings settings={settings()} busy={false} onSave={vi.fn()} />);
    expect(screen.getByRole("option", { name: "必须附可核对引文" })).not.toBeDisabled();
  });
});
