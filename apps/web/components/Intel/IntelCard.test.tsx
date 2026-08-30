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
};

function actions(): IntelCardActions {
  return {
    onTogglePin: vi.fn(),
    onMove: vi.fn(),
    onEdit: vi.fn(),
    onMute: vi.fn(),
    onDelete: vi.fn(),
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
