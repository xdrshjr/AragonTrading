import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AiTradeIntelSummary } from "@/lib/intelTypes";
import IntelList from "./IntelList";

/**
 * 空态的三级分叉（ai-trade-intel-arm-state §7.1 · G-24）。
 *
 * 「未配源 / 总闸关 / 未装配」是仅有的三种**用户不动手就永远不会有情报**的状态，而它们的出路
 * 完全不同：第一种要去选源存 key，第二种只要打开一个开关，第三种连设置都不用碰。把三者压成
 * 一句「尚未配置情报检索源」，会让一个**已经配好了**的用户去设置里反复确认自己没配错，
 * 然后断定这个功能坏了——那正是本轮的立项理由。
 */
const NOW_MS = Date.now();

function summary(over: Partial<AiTradeIntelSummary> = {}): AiTradeIntelSummary {
  return {
    instId: "BTC-USDT-SWAP",
    counts: { ultra: 0, mid: 0, long: 0, archived: 0 },
    unread: 0,
    lastSeenTs: 0,
    lastCollectTs: null,
    lastCurateTs: null,
    nextDueTs: null,
    inFlight: false,
    degrade: null,
    providerConfigured: true,
    intelEnabled: true,
    armed: true,
    armReason: "",
    dailyUsed: 0,
    dailyCap: 96,
    citations: { cited: 0, entryCited: 0, settledTrips: 0, winCount: 0, netPnlUsd: 0,
                 neverCited: 0 },
    verify: { confirmed: 0, refuted: 0, unresolved: 0, pending: 0, lastVerifyTs: null },
    quoteHitRate: null,
    alerts24h: 0,
    lastAlertTs: null,
    efficacy: { enabled: false, kMin: 8, sampleN: 0,
                overall: { settled: 0, wins: 0, netPnlUsd: 0, confirmed: 0, refuted: 0,
                           unresolved: 0, samples: 0, accuracy: null },
                byCategory: [] },
    ...over,
  };
}

function renderList(next: AiTradeIntelSummary, onRearm = vi.fn()) {
  return render(
    <IntelList
      items={[]}
      tier="ultra"
      summary={next}
      loading={false}
      filtered={false}
      actions={{} as never}
      nowMs={NOW_MS}
      onOpenSettings={vi.fn()}
      onCollect={vi.fn()}
      onClearFilters={vi.fn()}
      onRearm={onRearm}
    />,
  );
}

afterEach(cleanup);

describe("IntelList 的装配空态", () => {
  it("没配源 ⇒ no-provider（这一句在它真正成立的时候本来就是对的）", () => {
    renderList(summary({ providerConfigured: false, intelEnabled: true, armed: false,
                         armReason: "provider_unset" }));
    expect(screen.getByTestId("intel-empty-no-provider")).toBeInTheDocument();
    expect(screen.getByText("尚未配置情报检索源")).toBeInTheDocument();
  });

  it("配好了源但总闸关着 ⇒ intel-off（出路是打开一个开关，不是去配源）", () => {
    renderList(summary({ intelEnabled: false, armed: false, armReason: "disabled" }));
    expect(screen.getByTestId("intel-empty-intel-off")).toBeInTheDocument();
    expect(screen.getByText("市场情报采集已关闭")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "去设置里开启" })).toBeInTheDocument();
  });

  it("配好了、开着、就是没装上 ⇒ not-armed，正文取后端下发的那一句", () => {
    renderList(summary({
      armed: false, armReason: "not_running",
      degrade: { code: "not_running", message: "AI 自动交易未在运行，该标的的情报不会自动采集",
                 retryInS: null },
    }));
    expect(screen.getByTestId("intel-empty-not-armed")).toBeInTheDocument();
    expect(screen.getByText("AI 自动交易未在运行，该标的的情报不会自动采集"))
      .toBeInTheDocument();
    // `not_running` 的出路是去开自动交易，**不是**点一个按钮：给一个点了什么也不发生的按钮，
    // 比不给按钮更坏。
    expect(screen.queryByRole("button", { name: "重新装配" })).toBeNull();
  });

  it("装配失败 ⇒ not-armed 且给出【重新装配】", () => {
    const onRearm = vi.fn();
    renderList(summary({
      armed: false, armReason: "arm_failed",
      degrade: { code: "arm_failed",
                 message: "情报流水线装配失败（LLM provider 未配置），检索源配置本身没有问题",
                 retryInS: null },
    }), onRearm);
    expect(screen.getByTestId("intel-empty-not-armed")).toBeInTheDocument();
    screen.getByRole("button", { name: "重新装配" }).click();
    expect(onRearm).toHaveBeenCalledTimes(1);
  });

  it("三种空态的 testid 与标题各不相同（防被复制成同一段）", () => {
    const ids = new Set<string>();
    const titles = new Set<string>();
    for (const next of [
      summary({ providerConfigured: false, armed: false, armReason: "provider_unset" }),
      summary({ intelEnabled: false, armed: false, armReason: "disabled" }),
      summary({ armed: false, armReason: "not_running" }),
    ]) {
      const view = renderList(next);
      const node = view.container.querySelector("[data-testid^='intel-empty-']");
      ids.add(node?.getAttribute("data-testid") ?? "");
      titles.add(node?.firstElementChild?.textContent ?? "");
      view.unmount();
    }
    expect(ids).toEqual(new Set(["intel-empty-no-provider", "intel-empty-intel-off",
                                 "intel-empty-not-armed"]));
    expect(titles.size).toBe(3);
  });
});
