import { describe, expect, it } from "vitest";

import {
  QUOTE_STATE_LABEL,
  bytesLabel,
  docStale,
  evidenceTriggerLabel,
  hasEvidence,
  hasStaleSnapshot,
  quoteHitRateLabel,
  quoteStateLabel,
  splitQuotes,
  strictReadiness,
} from "./intelEvidence";
import type { IntelDocState, IntelEvidence, IntelQuote, IntelQuoteState } from "./intelTypes";

function quote(over: Partial<IntelQuote> = {}): IntelQuote {
  return {
    id: 1, itemId: 10, instId: "BTC-USDT-SWAP", roundId: 3, role: "admit", ref: "D1",
    text: "核心 CPI 同比 3.1%", sourceField: "body", docUrl: "https://reuters.com/a",
    docDomain: "reuters.com", docPublishedTs: 1_700_000_000, hasImperative: false,
    verifiedTs: 1_700_000_100, ...over,
  };
}

function doc(over: Partial<IntelDocState> = {}): IntelDocState {
  return {
    url: "https://reuters.com/a", finalUrl: "https://reuters.com/a", domain: "reuters.com",
    title: "t", status: "ok", httpStatus: 200, fetchedTs: 1_700_000_050, bodyChars: 900,
    truncated: false, ...over,
  };
}

describe("quoteStateLabel", () => {
  // R-11：五个取值**逐个**断言不等于兜底串——漏一个的症状是用户读到一句不对的话，而不是报错。
  it.each<[Exclude<IntelQuoteState, "">, string]>([
    ["legacy", "本条早于原文取证功能，没有留存原文"],
    ["modeOff", "原文引证要求已在设置中关闭"],
    ["noBody", "这几个来源都没能抓到正文"],
    ["modelOmitted", "抓到了正文，但情报 Agent 本轮没有附引文"],
    ["verifyFailed", "附了引文，但没有一段能在原文里逐字对上"],
  ])("%s ⇒ 「%s」", (state, label) => {
    expect(quoteStateLabel(state)).toBe(label);
    expect(quoteStateLabel(state)).not.toBe("没有可核对的原文引证");
  });

  it("五个取值互不相同（同一句话解释两种原因等于什么都没解释）", () => {
    const labels = Object.values(QUOTE_STATE_LABEL);
    expect(new Set(labels).size).toBe(labels.length);
  });

  it("有引证（空串）⇒ 空文案 ⇒ 调用方渲染触发器而不是标记", () => {
    expect(quoteStateLabel("")).toBe("");
  });
});

describe("hasEvidence / evidenceTriggerLabel", () => {
  it("quoted 为真且条数 > 0 才算有证据", () => {
    expect(hasEvidence({ quoted: true, quoteCount: 2 })).toBe(true);
    expect(hasEvidence({ quoted: true, quoteCount: 0 })).toBe(false);
    expect(hasEvidence({ quoted: false, quoteCount: 3 })).toBe(false);
  });

  it("触发器文案带条数", () => {
    expect(evidenceTriggerLabel(2)).toBe("原文引证 2");
    expect(evidenceTriggerLabel(-1)).toBe("原文引证 0");
  });
});

describe("splitQuotes", () => {
  it("按 role 分两组——混在一起会让用户读不出「这条被推翻，是因为后来出现了这段字」", () => {
    const { admit, verify } = splitQuotes([
      quote({ id: 1 }), quote({ id: 2, role: "verify" }), quote({ id: 3 }),
    ]);
    expect(admit.map((q) => q.id)).toEqual([1, 3]);
    expect(verify.map((q) => q.id)).toEqual([2]);
  });
});

describe("docStale / hasStaleSnapshot", () => {
  it("缓存里还有这篇 ⇒ 不算过期（finalUrl 也算命中）", () => {
    expect(docStale(quote(), [doc()])).toBe(false);
    expect(docStale(quote({ docUrl: "https://reuters.com/final" }),
                    [doc({ finalUrl: "https://reuters.com/final" })])).toBe(false);
  });

  it("缓存已被淘汰 ⇒ 抽屉必须说出那句「仍为当时逐字留存」", () => {
    const evidence: IntelEvidence = {
      itemId: 10, quoteMode: "soft", quotes: [quote()], docs: [],
    };
    expect(docStale(quote(), [])).toBe(true);
    expect(hasStaleSnapshot(evidence)).toBe(true);
  });

  it("一条引文都没有 ⇒ 不说那句话（它回答的是「原文呢」，而不是「有没有引文」）", () => {
    expect(hasStaleSnapshot({ itemId: 1, quoteMode: "soft", quotes: [], docs: [] }))
      .toBe(false);
    expect(hasStaleSnapshot(null)).toBe(false);
  });
});

describe("quoteHitRateLabel / strictReadiness", () => {
  it("null（没提过引文）⇒ 空串 ⇒ 该行不渲染，绝不显示一个假的 0%", () => {
    expect(quoteHitRateLabel(null)).toBe("");
    expect(quoteHitRateLabel(0)).toBe("近 20 轮引文命中率 0%");
  });

  it("命中率四舍五入到整数百分比", () => {
    expect(quoteHitRateLabel(0.784)).toBe("近 20 轮引文命中率 78%");
  });

  it("把翻档判据摆在用户眼前（只写在文档里的判据等于没有判据）", () => {
    expect(strictReadiness(null)).toContain("还没有");
    expect(strictReadiness(0.62)).toContain("不建议");
    expect(strictReadiness(0.7)).toContain("建议线");
  });
});

describe("bytesLabel", () => {
  it("后端下发的是真实字节，这里只做单位换算", () => {
    expect(bytesLabel(0)).toBe("0 MB");
    expect(bytesLabel(1024)).toBe("<0.1 MB");
    expect(bytesLabel(18 * 1024 * 1024)).toBe("18.0 MB");
  });
});
