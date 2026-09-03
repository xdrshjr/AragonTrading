import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import AiTradeIntelFields from "./AiTradeIntelFields";
import { AI_TRADE_INTEL_RULE_DEFAULT } from "@/lib/aiTradeTypes";
import { intelApi } from "@/lib/intelApi";

// 启动弹窗的「市场情报研判」卡片（intel-session-binding §3.4 · W1 / W2 / W3 / W12）。
//
// 三条必须写在界面上的事实：
// ① **默认不勾**——与弹窗里其余六张卡的方向相反，这是需求的明文要求；
// ② 卡片上的两个字数取自接口回执，**源码里不许出现字面量 480 / 1200**；
// ③ 条数读不到就整句不渲染，勾选与档位单选**照常可用**——绝不先显示一个会被当真的 0。

const SETTINGS = {
  enabled: true, injectEnabled: true, provider: "tavily", hasKey: true, keyPreview: "…1234",
  collectIntervalMin: 30, curateEveryNCollects: 4, dailyCap: 96,
  estimate: { roundsPerDay: 10, inputTokensPerDay: 1000 },
  citeEnabled: true, attributionEnabled: true, verifyEnabled: true,
  verifyEveryNCurates: 1, verifyMaxItems: 3, verifyDocsPerItem: 4,
  blockedSweepEnabled: true, citeMaxRefs: 8,
  readerEnabled: true, readerTopK: 6, quoteMode: "soft" as const, quoteHitRate: 0.78,
  readerResolver: "auto" as const, readerHealthVisible: true, readerZeroStreakWarn: 3,
  alertEnabled: true, alertMinImpact: "high" as const, alertCooldownMin: 20, alertDailyCap: 6,
  efficacyEnabled: true, conflictEnabled: true, alertRequireQuote: true,
  alertPendingTtlS: 180,
  sessionOptInEnabled: true, expandEnabled: true, repeatCompressEnabled: true,
  indexMaxChars: 480, promptMaxChars: 1200,
  arm: {
    providerConfigured: true, intelEnabled: true, armed: true, reason: "" as const,
    message: "情报采集正在运行", detail: "", groupRunning: true,
    armedMembers: ["BTC-USDT-SWAP"], pending: false,
  },
};

function readResponse(over: { ultra?: number; lastCollectTs?: number | null } = {}) {
  return {
    summary: {
      counts: { ultra: over.ultra ?? 9, mid: 4, long: 2, archived: 30 },
      lastCollectTs: over.lastCollectTs === undefined ? 1_800_000_000 : over.lastCollectTs,
    },
  };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

beforeEach(() => {
  vi.spyOn(intelApi, "settings").mockResolvedValue(SETTINGS as never);
  vi.spyOn(intelApi, "read").mockResolvedValue(readResponse() as never);
  vi.setSystemTime(new Date(1_800_000_600 * 1000));
});

function renderCard(
  over: Partial<React.ComponentProps<typeof AiTradeIntelFields>> = {},
) {
  const onChange = vi.fn();
  render(
    <AiTradeIntelFields
      value={AI_TRADE_INTEL_RULE_DEFAULT}
      onChange={onChange}
      instIds={["BTC-USDT-SWAP", "ETH-USDT-SWAP"]}
      {...over}
    />,
  );
  return { onChange };
}

describe("AiTradeIntelFields", () => {
  it("W1：首渲染**未勾选**，档位单选只在勾选之后出现", async () => {
    renderCard();
    const box = screen.getByLabelText("让 Agent 参考市场情报") as HTMLInputElement;
    expect(box.checked).toBe(false);
    expect(screen.queryByText(/索引档（推荐/)).not.toBeInTheDocument();

    cleanup();
    renderCard({ value: { enabled: true, mode: "index" } });
    expect(await screen.findByText(/索引档（推荐/)).toBeInTheDocument();
    expect(screen.getByText("全文档")).toBeInTheDocument();   // 单选项本身，不是说明段里那一句
  });

  it("W2：运维总闸关 ⇒ 整卡禁用 + 「去设置」入口，勾选框不可点", async () => {
    vi.spyOn(intelApi, "settings").mockResolvedValue(
      { ...SETTINGS, sessionOptInEnabled: false } as never);
    renderCard();
    await waitFor(() =>
      expect(screen.getByTestId("ai-trade-intel-ops-off")).toBeInTheDocument());
    expect(screen.getByLabelText("让 Agent 参考市场情报")).toBeDisabled();
    expect(screen.getByRole("button", { name: "去设置 →" })).toBeInTheDocument();
  });

  it("W2b：未配置检索源 ⇒ 同样整卡禁用并说明原因", async () => {
    vi.spyOn(intelApi, "settings").mockResolvedValue(
      { ...SETTINGS, arm: { ...SETTINGS.arm, providerConfigured: false } } as never);
    renderCard();
    await waitFor(() =>
      expect(screen.getByTestId("ai-trade-intel-no-provider")).toBeInTheDocument());
    expect(screen.getByLabelText("让 Agent 参考市场情报")).toBeDisabled();
  });

  it("W3：两个字数与那个比值全部取自接口回执（改一次回执，卡片上的数就跟着变）", async () => {
    vi.spyOn(intelApi, "settings").mockResolvedValue(
      { ...SETTINGS, indexMaxChars: 300, promptMaxChars: 1500 } as never);
    renderCard({ value: { enabled: true, mode: "index" } });
    const budget = await screen.findByTestId("ai-trade-intel-budget");
    expect(budget).toHaveTextContent("约 300 字");
    expect(budget).toHaveTextContent("约 1500 字");
    expect(budget).toHaveTextContent("约 5 倍");     // 1500 / 300 现算，前端不硬编码
  });

  it("W12：任一标的读失败 ⇒ 条数整句不渲染，勾选与档位单选仍然可用", async () => {
    vi.spyOn(intelApi, "read")
      .mockResolvedValueOnce(readResponse() as never)
      .mockRejectedValueOnce(new Error("boom"));
    renderCard({ value: { enabled: true, mode: "index" } });
    await screen.findByTestId("ai-trade-intel-budget");
    expect(screen.queryByTestId("ai-trade-intel-stock")).not.toBeInTheDocument();
    expect(screen.getByLabelText("让 Agent 参考市场情报")).not.toBeDisabled();
    expect(screen.getByText(/索引档（推荐/)).toBeInTheDocument();
  });

  it("W12b：全部 lastCollectTs 为 null ⇒ 换成「尚未成功采集过」那一句", async () => {
    vi.spyOn(intelApi, "read").mockResolvedValue(
      readResponse({ lastCollectTs: null }) as never);
    renderCard();
    expect(await screen.findByTestId("ai-trade-intel-never")).toHaveTextContent(
      "尚未成功采集过情报");
  });

  it("条数口径 = 三层 active 之和，文案写「在库情报」而不是「有效情报」", async () => {
    renderCard();
    const line = await screen.findByTestId("ai-trade-intel-stock");
    expect(line).toHaveTextContent("30 条在库情报");   // (9+4+2) × 2 个标的
    expect(line).not.toHaveTextContent("有效情报");
  });

  it("回测 tab 恒不渲染这张卡（情报层不进回测车道）", () => {
    const { container } = render(
      <AiTradeIntelFields
        value={AI_TRADE_INTEL_RULE_DEFAULT}
        onChange={vi.fn()}
        instIds={["BTC-USDT-SWAP"]}
        backtest
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
