import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AiTradeIntelSummary, IntelItem } from "@/lib/intelTypes";
import IntelCard, { type IntelCardActions } from "./IntelCard";

const NOW_MS = 1_800_000_000_000;
const NOW_S = NOW_MS / 1000;

function item(over: Partial<IntelItem> = {}): IntelItem {
  return {
    id: 1,
    instId: "BTC-USDT-SWAP",
    tier: "ultra",
    category: "risk",
    headline: "某跨链桥今晨被盗约 3400 万美元",
    summary: "与本标的无直接资金关系，但同板块情绪承压，永续资金费率已转负。",
    stance: "bearish",
    impact: "high",
    confidence: 0.65,
    eventTs: NOW_S - 4 * 3600,
    capturedTs: NOW_S - 3600,
    updatedTs: NOW_S,
    expiresTs: NOW_S + 86400,
    status: "active",
    pinned: false,
    userEdited: false,
    undated: false,
    isScheduled: false,
    origin: "agent",
    sources: [
      { url: "https://theblock.co/a", title: "T", publisher: "theblock.co", domain: "theblock.co", publishedTs: NOW_S },
      { url: "javascript:alert(1)", title: "bad", publisher: "", domain: "", publishedTs: 0 },
    ],
    sourceCount: 2,
    domainCount: 2,
    dedupKey: "k",
    supersedesId: null,
    promoteCount: 0,
    demoteCount: 0,
    lastCuratedTs: 0,
    curationNote: "",
    citeCount: 0,
    citeEntryCount: 0,
    settledCount: 0,
    winCount: 0,
    netPnlUsd: 0,
    lastCitedTs: 0,
    verdict: "",
    verdictTs: 0,
    verdictNote: "",
    verifyCount: 0,
    quoted: false, quoteCount: 0, quoteState: "", scheduleResolvedTs: 0,
    createdTs: NOW_S,
    ...over,
  };
}

const summary: AiTradeIntelSummary = {
  instId: "BTC-USDT-SWAP",
  counts: { ultra: 1, mid: 0, long: 0, archived: 0 },
  unread: 1,
  lastSeenTs: NOW_S - 7200,
  lastCollectTs: NOW_S - 600,
  lastCurateTs: null,
  nextDueTs: null,
  inFlight: false,
  degrade: null,
  providerConfigured: true,
  dailyUsed: 1,
  dailyCap: 96,
  citations: { cited: 0, entryCited: 0, settledTrips: 0, winCount: 0, netPnlUsd: 0, neverCited: 0 },
  verify: { confirmed: 0, refuted: 0, unresolved: 0, pending: 0, lastVerifyTs: null },
  quoteHitRate: null,
  alerts24h: 0,
  lastAlertTs: null,
  efficacy: { enabled: false, kMin: 8, sampleN: 0, overall: { settled: 0, wins: 0, netPnlUsd: 0, confirmed: 0, refuted: 0, unresolved: 0, samples: 0, accuracy: null }, byCategory: [] },
};

function actions(): IntelCardActions {
  return {
    onTogglePin: vi.fn(),
    onMove: vi.fn(),
    onEdit: vi.fn(),
    onMute: vi.fn(),
    onDelete: vi.fn(),
    onClearVerdict: vi.fn(),
    onVerifyItem: vi.fn(),
    onBlockSource: vi.fn(),
    onRestore: vi.fn(),
  };
}

afterEach(cleanup);

describe("IntelCard", () => {
  it("方向 / 类别 / 影响力 / 相对时间 / 来源数 / 置信度都在同一行内可读", () => {
    render(<IntelCard item={item()} summary={summary} actions={actions()} nowMs={NOW_MS} />);
    expect(screen.getByText(/偏空 · 风险 · 高/)).toBeInTheDocument();
    expect(screen.getByText(/4 小时前 · 2 源 · 置信 0.65/)).toBeInTheDocument();
  });

  it("绝对时间挂在 title 上（核对新闻时需要准确时刻）", () => {
    render(<IntelCard item={item()} summary={summary} actions={actions()} nowMs={NOW_MS} />);
    expect(screen.getByText(/4 小时前/).getAttribute("title")).toContain("UTC");
  });

  it("摘要默认两行截断，点击展开（纯本地 state，不发请求）", () => {
    render(<IntelCard item={item()} summary={summary} actions={actions()} nowMs={NOW_MS} />);
    const summaryNode = screen.getByText(/同板块情绪承压/);
    expect(summaryNode.className).toContain("line-clamp-2");
    fireEvent.click(screen.getByText("某跨链桥今晨被盗约 3400 万美元"));
    expect(screen.getByText(/同板块情绪承压/).className).not.toContain("line-clamp-2");
  });

  it("http(s) 来源渲染成外链且带 rel；非 http(s) 只渲染成文字", () => {
    render(<IntelCard item={item()} summary={summary} actions={actions()} nowMs={NOW_MS} />);
    const link = screen.getByRole("link", { name: /theblock\.co/ });
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(screen.queryByRole("link", { name: /未知来源/ })).toBeNull();
  });

  it("未读条目高亮（新采集的那几条要一眼能找到）", () => {
    const { container } = render(
      <IntelCard item={item({ capturedTs: NOW_S })} summary={summary} actions={actions()}
                 nowMs={NOW_MS} />,
    );
    expect(container.querySelector("[data-testid='intel-card']")!.className)
      .toContain("border-[var(--accent)]");
  });

  it("动作的 aria-label 带标题前缀（否则读屏会听到一排一模一样的「置顶」）", () => {
    const acts = actions();
    render(<IntelCard item={item()} summary={summary} actions={acts} nowMs={NOW_MS} />);
    fireEvent.click(screen.getByRole("button", { name: /^置顶：某跨链桥今晨被盗约/ }));
    expect(acts.onTogglePin).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: /^删除：某跨链桥今晨被盗约/ }));
    expect(acts.onDelete).toHaveBeenCalledTimes(1);
  });

  it("移层下拉只列出**其它**层级", () => {
    const acts = actions();
    render(<IntelCard item={item()} summary={summary} actions={acts} nowMs={NOW_MS} />);
    fireEvent.click(screen.getByRole("button", { name: /^移层：/ }));
    expect(screen.getByText("移到中期")).toBeInTheDocument();
    expect(screen.getByText("移到长期")).toBeInTheDocument();
    expect(screen.queryByText("移到超短")).toBeNull();
    fireEvent.click(screen.getByText("移到中期"));
    expect(acts.onMove).toHaveBeenCalledWith(expect.objectContaining({ id: 1 }), "mid");
  });

  it("已归档 / 已静音的条目换成「恢复 / 删除」两个动作", () => {
    const acts = actions();
    render(
      <IntelCard item={item({ status: "muted" })} summary={summary} actions={acts}
                 nowMs={NOW_MS} />,
    );
    expect(screen.queryByRole("button", { name: /^置顶：/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /^移层：/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /^静音：/ })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /^恢复：/ }));
    expect(acts.onRestore).toHaveBeenCalledTimes(1);
  });

  it("置顶 / 用户编辑 / 已排期三个标记都露在卡片上", () => {
    render(
      <IntelCard item={item({ pinned: true, userEdited: true, isScheduled: true })}
                 summary={summary} actions={actions()} nowMs={NOW_MS} />,
    );
    expect(screen.getByText(/已置顶/)).toBeInTheDocument();
    expect(screen.getByText(/用户编辑/)).toBeInTheDocument();
    expect(screen.getByText(/已排期/)).toBeInTheDocument();
  });
});


// ---------------- closed-loop：归因轨 / 判定徽章 / 证伪警示 / 来源拉黑 ----------------
describe("IntelCard · 情报闭环", () => {
  it("零引用且无判定 ⇒ 卡片与合入前一样（两块都不渲染）", () => {
    render(<IntelCard item={item()} summary={summary} actions={actions()} nowMs={NOW_MS} />);
    expect(screen.queryByTestId("intel-attribution-rail")).toBeNull();
    expect(screen.queryByTestId("intel-verdict-badge")).toBeNull();
    expect(screen.queryByTestId("intel-refuted-warning")).toBeNull();
  });

  it("被引用过 ⇒ 归因轨在位，并说「关联净额」", () => {
    render(<IntelCard
      item={item({ citeCount: 12, citeEntryCount: 5, settledCount: 4, winCount: 3,
        netPnlUsd: 18.4 })}
      summary={summary} actions={actions()} nowMs={NOW_MS} />);
    expect(screen.getByTestId("intel-attribution-rail")).toHaveTextContent("关联净额");
  });

  it("被证伪 ⇒ 出现警示行且卡片带降透明度类名", () => {
    render(<IntelCard item={item({ verdict: "refuted" })} summary={summary}
      actions={actions()} nowMs={NOW_MS} />);

    expect(screen.getByTestId("intel-refuted-warning"))
      .toHaveTextContent("已停止喂给交易 Agent");
    expect(screen.getByTestId("intel-card").className).toContain("opacity-60");
  });

  it("已证实 ⇒ 只有徽章，没有警示行、也不降透明度", () => {
    render(<IntelCard item={item({ verdict: "confirmed" })} summary={summary}
      actions={actions()} nowMs={NOW_MS} />);

    expect(screen.getByTestId("intel-verdict-badge")).toHaveTextContent("已证实");
    expect(screen.queryByTestId("intel-refuted-warning")).toBeNull();
    expect(screen.getByTestId("intel-card").className).not.toContain("opacity-60");
  });

  it("「我不同意」经卡片动作透传出去", () => {
    const acts = actions();
    const target = item({ verdict: "refuted" });
    render(<IntelCard item={target} summary={summary} actions={acts} nowMs={NOW_MS} />);
    fireEvent.click(screen.getByTestId("intel-verdict-badge"));
    fireEvent.click(screen.getByRole("button", { name: "我不同意，恢复注入" }));

    expect(acts.onClearVerdict).toHaveBeenCalledWith(target);
  });

  it("来源 chip 上的 ✕ 是 `<a>` 的**兄弟**节点，不是子节点", () => {
    const acts = actions();
    render(<IntelCard item={item()} summary={summary} actions={acts} nowMs={NOW_MS} />);

    const remove = screen.getByRole("button", { name: "不再采纳 theblock.co" });
    expect(remove.closest("a")).toBeNull();
    fireEvent.click(remove);
    expect(acts.onBlockSource).toHaveBeenCalledWith("theblock.co");
  });

  it("✕ 键盘可达（只在 hover 时出现的破坏性操作对键盘用户等于不存在）", () => {
    render(<IntelCard item={item()} summary={summary} actions={actions()} nowMs={NOW_MS} />);
    expect(screen.getByRole("button", { name: "不再采纳 theblock.co" }).className)
      .toContain("focus-visible:opacity-100");
  });

  // ---------------- evidence-grounding 轴 C：证据抽屉 ----------------
  it("摘要展开与证据展开是**两个独立的 state**：想读原文时不该被迫先展开摘要", () => {
    const acts = { ...actions(), onLoadEvidence: vi.fn() };
    render(<IntelCard item={item({ quoted: true, quoteCount: 2 })} summary={summary}
                      actions={acts} nowMs={NOW_MS} />);
    // 摘要触发器是那颗 `aria-expanded` 且**没有** `aria-label` 的按钮（动作区那几颗都有）。
    const summaryToggle = screen
      .getAllByRole("button", { name: /某跨链桥今晨被盗/ })
      .find((node) => !node.getAttribute("aria-label"))!;
    const evidenceToggle = screen.getByTestId("intel-evidence-trigger");
    expect(summaryToggle).toHaveAttribute("aria-expanded", "false");

    fireEvent.click(evidenceToggle);
    expect(evidenceToggle).toHaveAttribute("aria-expanded", "true");
    expect(summaryToggle).toHaveAttribute("aria-expanded", "false");

    fireEvent.click(summaryToggle);
    expect(summaryToggle).toHaveAttribute("aria-expanded", "true");
    expect(evidenceToggle).toHaveAttribute("aria-expanded", "true");
  });

  it("展开才发那一次请求；收起再展开**不重发**（收起不清缓存）", () => {
    const onLoadEvidence = vi.fn();
    const acts = { ...actions(), onLoadEvidence };
    const { rerender } = render(
      <IntelCard item={item({ quoted: true, quoteCount: 1 })} summary={summary}
                 actions={acts} nowMs={NOW_MS} />);
    expect(onLoadEvidence).not.toHaveBeenCalled();

    fireEvent.click(screen.getByTestId("intel-evidence-trigger"));
    expect(onLoadEvidence).toHaveBeenCalledTimes(1);

    // 弹窗把结果缓存下来之后，收起再展开不该再发。
    const evidence = {
      itemId: 1, quoteMode: "soft" as const, docs: [],
      quotes: [{
        id: 5, itemId: 1, instId: "BTC-USDT-SWAP", roundId: 1, role: "admit" as const,
        ref: "D1", text: "原文一段", sourceField: "body" as const,
        docUrl: "https://reuters.com/a", docDomain: "reuters.com", docPublishedTs: 0,
        hasImperative: false, verifiedTs: 0,
      }],
    };
    rerender(<IntelCard item={item({ quoted: true, quoteCount: 1 })} summary={summary}
                        actions={acts} nowMs={NOW_MS} evidence={evidence} />);
    fireEvent.click(screen.getByTestId("intel-evidence-trigger"));
    fireEvent.click(screen.getByTestId("intel-evidence-trigger"));
    expect(onLoadEvidence).toHaveBeenCalledTimes(1);
  });

  it("没有 `onLoadEvidence`（只读场景）⇒ 卡片上不出现任何证据入口", () => {
    render(<IntelCard item={item({ quoted: true, quoteCount: 2 })} summary={summary}
                      actions={actions()} nowMs={NOW_MS} />);
    expect(screen.queryByTestId("intel-evidence-trigger")).toBeNull();
    expect(screen.queryByTestId("intel-no-evidence")).toBeNull();
  });

  it("无引文 ⇒ 一枚带原因的「无引证」标记，而不是一个点开永远是空的触发器", () => {
    const acts = { ...actions(), onLoadEvidence: vi.fn() };
    render(<IntelCard item={item({ quoteState: "noBody" })} summary={summary}
                      actions={acts} nowMs={NOW_MS} />);
    expect(screen.queryByTestId("intel-evidence-trigger")).toBeNull();
    expect(screen.getByTestId("intel-no-evidence"))
      .toHaveAttribute("title", "这几个来源都没能抓到正文");
  });
});
