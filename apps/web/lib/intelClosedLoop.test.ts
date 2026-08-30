import { describe, expect, it } from "vitest";

import {
  IMPACT_CAVEAT,
  REFUTED_WARNING,
  attributionText,
  attributionTitle,
  formatNetPnl,
  impactEmptyText,
  netPnlTone,
  sourceStateLabel,
  verdictLabel,
  verdictNoRefNote,
  verdictTone,
  verifyRoundSummary,
} from "./intelClosedLoop";
import type { IntelItem } from "./intelTypes";

function item(over: Partial<IntelItem> = {}): IntelItem {
  return {
    id: 1, instId: "BTC-USDT-SWAP", tier: "ultra", category: "risk", headline: "h",
    summary: "s", stance: "neutral", impact: "high", confidence: 0.6, eventTs: 0,
    capturedTs: 0, updatedTs: 0, expiresTs: 0, status: "active", pinned: false,
    userEdited: false, undated: false, isScheduled: false, origin: "agent", sources: [],
    sourceCount: 0, domainCount: 0, dedupKey: "k", supersedesId: null, promoteCount: 0,
    demoteCount: 0, lastCuratedTs: 0, curationNote: "", createdTs: 0,
    citeCount: 0, citeEntryCount: 0, settledCount: 0, winCount: 0, netPnlUsd: 0,
    lastCitedTs: 0, verdict: "", verdictTs: 0, verdictNote: "", verifyCount: 0,
    quoted: false, quoteCount: 0, quoteState: "", scheduleResolvedTs: 0,
    ...over,
  };
}

describe("判定徽章的四态", () => {
  it("`\"\"` 回空串 ⇒ 整个徽章不渲染（而不是渲染一个「未知」）", () => {
    expect(verdictLabel("")).toBe("");
  });

  it("三个判定各有一段互不相同的中文", () => {
    const labels = [verdictLabel("confirmed"), verdictLabel("refuted"),
      verdictLabel("unresolved")];
    expect(labels).toEqual(["已证实", "已证伪", "待定"]);
    expect(new Set(labels).size).toBe(3);
  });

  it("四态四色，且证伪用危险色", () => {
    expect(verdictTone("confirmed")).toBe("var(--ok)");
    expect(verdictTone("refuted")).toBe("var(--danger)");
    expect(verdictTone("unresolved")).toBe("var(--muted)");
  });
});

describe("净额配色", () => {
  it("零值用 muted 而不是 up（一排零值不该看起来像一排小胜）", () => {
    expect(netPnlTone(0)).toBe("var(--muted)");
    expect(netPnlTone(0.01)).toBe("var(--up)");
    expect(netPnlTone(-0.01)).toBe("var(--down)");
  });

  it("正数带 + 号，两位小数", () => {
    expect(formatNetPnl(18.4)).toBe("+18.40 USDT");
    expect(formatNetPnl(-3)).toBe("-3.00 USDT");
    expect(formatNetPnl(0)).toBe("0.00 USDT");
  });
});

describe("归因轨文案", () => {
  it("零引用 ⇒ 空串 ⇒ 整行不渲染（不出现「引用 0 次」这种噪音）", () => {
    expect(attributionText(item())).toBe("");
  });

  it("只被引用过、还没结算 ⇒ 不显示净额（0.00 会被读成「打平」）", () => {
    const text = attributionText(item({ citeCount: 3, citeEntryCount: 1 }));
    expect(text).toContain("引用 3");
    expect(text).toContain("入场 1");
    expect(text).not.toContain("净额");
  });

  it("结算之后给出胜负与**关联**净额", () => {
    const text = attributionText(item({
      citeCount: 12, citeEntryCount: 5, settledCount: 4, winCount: 3, netPnlUsd: 18.4,
    }));
    expect(text).toBe("引用 12 · 入场 5 · 已结算 4 · 胜 3 · 关联净额 +18.40 USDT");
    expect(text).not.toContain("贡献");
    expect(text).not.toContain("带来");
  });

  it("title 里必须写清「这是共现，不是因果」（R-1 唯一的缓解手段）", () => {
    const title = attributionTitle(item({ citeEntryCount: 5, settledCount: 4, winCount: 3 }));
    expect(title).toContain("胜 3 负 1");
    expect(title).toContain("共现");
    expect(title).toContain("不是因果归因");
  });
});

describe("影响力页签的四种空态", () => {
  it("四段文案**两两互不相同**（防被复制成同一段）", () => {
    const kinds = ["cite-off", "no-citations", "attribution-off", "not-armed"] as const;
    const texts = kinds.map(impactEmptyText);
    expect(new Set(texts).size).toBe(4);
    texts.forEach((t) => expect(t.length).toBeGreaterThan(10));
  });

  it("常驻口径说明说的是共现而不是因果", () => {
    expect(IMPACT_CAVEAT).toContain("共现");
    expect(IMPACT_CAVEAT).toContain("不是因果归因");
  });
});

describe("信源状态标签", () => {
  it("没表态的域名回空串（不显示一个假的「正常」徽章）", () => {
    expect(sourceStateLabel("")).toBe("");
    expect(sourceStateLabel("blocked")).toBe("已拉黑");
    expect(sourceStateLabel("trusted")).toBe("更信任");
  });
});

describe("核查轮的审计行", () => {
  it("三个判定各自计数", () => {
    expect(verifyRoundSummary({ confirmed: 1, refuted: 1, unresolved: 1 }))
      .toBe("核查 3 条 → 证实 1 · 证伪 1 · 待定 1");
  });

  it("零判定说的是「没有待核查条目」而不是「核查 0 条」", () => {
    expect(verifyRoundSummary({})).toBe("本轮没有待核查条目");
  });

  it("`verdict_no_ref` 那一行是「模型不能空口断言」的可见证据", () => {
    expect(verdictNoRefNote({ verdict_no_ref: 1 })).toBe("1 条判定因未指出依据被降为待定");
    expect(verdictNoRefNote({})).toBe("");
  });
});

describe("证伪警示行", () => {
  it("必须明说「已停止喂给交易 Agent」", () => {
    expect(REFUTED_WARNING).toContain("已停止喂给交易 Agent");
  });
});
