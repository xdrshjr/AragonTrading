import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAppStore } from "@/lib/store";
import type { AiTradeIntelSummary } from "@/lib/intelTypes";
import { AiTradeIntelButton } from "./AiTradeIntelButton";

// 弹窗桩掉：本文件只验证按钮本身（可见性 / 徽标 / a11y / 降级染色），弹窗有自己的用例。
vi.mock("@/components/Intel/IntelModal", () => ({
  default: ({ instId }: { instId: string }) => <div data-testid="intel-modal">{instId}</div>,
}));

const INST = "BTC-USDT-SWAP";

function summary(over: Partial<AiTradeIntelSummary> = {}): AiTradeIntelSummary {
  return {
    instId: INST,
    counts: { ultra: 2, mid: 0, long: 0, archived: 0 },
    unread: 0,
    lastSeenTs: 0,
    lastCollectTs: 1,
    lastCurateTs: null,
    nextDueTs: null,
    inFlight: false,
    degrade: null,
    providerConfigured: true,
    dailyUsed: 0,
    dailyCap: 96,
    citations: { cited: 0, entryCited: 0, settledTrips: 0, winCount: 0, netPnlUsd: 0, neverCited: 0 },
    verify: { confirmed: 0, refuted: 0, unresolved: 0, pending: 0, lastVerifyTs: null },
    quoteHitRate: null,
    ...over,
  };
}

function seed(value: AiTradeIntelSummary | null) {
  useAppStore.setState({ aiTradeIntel: value ? { [INST]: value } : {} });
}

beforeEach(() => seed(null));
afterEach(cleanup);

describe("AiTradeIntelButton", () => {
  it("默认不可见、不可点，但**占位存在**（hover 时不会引起行内位移）", () => {
    render(<AiTradeIntelButton instId={INST} />);
    const button = screen.getByTestId("ai-trade-intel-button");
    expect(button.className).toContain("opacity-0");
    expect(button.className).toContain("pointer-events-none");
  });

  it("hover 行内任意处即淡入（具名 group/member）", () => {
    render(<AiTradeIntelButton instId={INST} />);
    const button = screen.getByTestId("ai-trade-intel-button");
    expect(button.className).toContain("group-hover/member:opacity-100");
    expect(button.className).toContain("group-hover/member:pointer-events-auto");
  });

  it("键盘聚焦即可见——只在 hover 时出现的功能对键盘用户等于不存在", () => {
    render(<AiTradeIntelButton instId={INST} />);
    const button = screen.getByTestId("ai-trade-intel-button");
    expect(button.className).toContain("focus-visible:opacity-100");
    expect(button.className).toContain("focus-visible:pointer-events-auto");
  });

  it("触摸设备（无 hover）常驻可见", () => {
    render(<AiTradeIntelButton instId={INST} />);
    expect(screen.getByTestId("ai-trade-intel-button").className).toContain(
      "[@media(hover:none)]:opacity-100",
    );
  });

  it("有未读时**不 hover 也可见**并带计数徽标", () => {
    seed(summary({ unread: 3 }));
    render(<AiTradeIntelButton instId={INST} />);
    const button = screen.getByTestId("ai-trade-intel-button");
    expect(button.className).toContain("opacity-100");
    expect(button.className).not.toContain("opacity-0");
    expect(screen.getByTestId("ai-trade-intel-unread")).toHaveTextContent("3");
  });

  it("未读 >9 收成 9+，且未读数进 aria-label", () => {
    seed(summary({ unread: 42 }));
    render(<AiTradeIntelButton instId={INST} />);
    expect(screen.getByTestId("ai-trade-intel-unread")).toHaveTextContent("9+");
    expect(screen.getByRole("button", { name: /42 条未读/ })).toBeInTheDocument();
  });

  it("降级时图标染警示色并在 title 里说清楚原因", () => {
    seed(summary({
      degrade: { code: "no_provider", message: "尚未配置情报检索源，情报采集未启动", retryInS: null },
    }));
    render(<AiTradeIntelButton instId={INST} />);
    const button = screen.getByTestId("ai-trade-intel-button");
    expect(button.className).toContain("text-[var(--warn)]");
    expect(button.getAttribute("title")).toContain("尚未配置情报检索源");
  });

  it("弱降级不染色（一个常亮的警告等于没有警告）", () => {
    seed(summary({ degrade: { code: "search_empty", message: "本轮检索没有返回任何结果", retryInS: null } }));
    render(<AiTradeIntelButton instId={INST} />);
    expect(screen.getByTestId("ai-trade-intel-button").className).not.toContain(
      "text-[var(--warn)]",
    );
  });

  it("点击才挂载弹窗（十个标的不该有十份常驻的弹窗状态）", () => {
    render(<AiTradeIntelButton instId={INST} />);
    expect(screen.queryByTestId("intel-modal")).toBeNull();
    fireEvent.click(screen.getByTestId("ai-trade-intel-button"));
    expect(screen.getByTestId("intel-modal")).toHaveTextContent(INST);
  });
});
