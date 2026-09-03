import { describe, expect, it } from "vitest";

import {
  CATEGORY_LABEL,
  TIER_LABEL,
  absoluteTime,
  confidenceBand,
  degradeAction,
  degradeText,
  intelButtonLabel,
  intelButtonTitle,
  intelReceiptText,
  isSafeUrl,
  isSevereDegrade,
  isUnread,
  itemScore,
  nextDueText,
  relativeTime,
  sortItems,
  stanceColor,
  unreadBadge,
} from "./intel";
import type { AiTradeIntelSummary, IntelItem } from "./intelTypes";

const NOW_MS = 1_800_000_000_000;
const NOW_S = NOW_MS / 1000;

function item(over: Partial<IntelItem> = {}): IntelItem {
  return {
    id: 1,
    instId: "BTC-USDT-SWAP",
    tier: "ultra",
    category: "risk",
    headline: "h",
    summary: "s",
    stance: "bearish",
    impact: "high",
    confidence: 0.6,
    eventTs: NOW_S - 3600,
    capturedTs: NOW_S - 3600,
    updatedTs: NOW_S,
    expiresTs: NOW_S + 86400,
    status: "active",
    pinned: false,
    userEdited: false,
    undated: false,
    isScheduled: false,
    origin: "agent",
    sources: [],
    sourceCount: 0,
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

function summary(over: Partial<AiTradeIntelSummary> = {}): AiTradeIntelSummary {
  return {
    instId: "BTC-USDT-SWAP",
    counts: { ultra: 3, mid: 1, long: 0, archived: 2 },
    unread: 0,
    lastSeenTs: NOW_S,
    lastCollectTs: NOW_S - 600,
    lastCurateTs: null,
    nextDueTs: NOW_S + 720,
    inFlight: false,
    degrade: null,
    providerConfigured: true,
    // ai-trade-intel-arm-state 轴 A：三个新键**必填**（TS 侧刻意不加 `?`——`tsc` 是这里唯一
    // 可靠的门禁）。前端读到 `undefined` 会把「没装上」画成「一切正常」。
    intelEnabled: true,
    armed: true,
    armReason: "" as const,
    dailyUsed: 18,
    dailyCap: 96,
    citations: { cited: 0, entryCited: 0, settledTrips: 0, winCount: 0, netPnlUsd: 0, neverCited: 0 },
    verify: { confirmed: 0, refuted: 0, unresolved: 0, pending: 0, lastVerifyTs: null },
    quoteHitRate: null,
    alerts24h: 0,
    lastAlertTs: null,
    efficacy: { enabled: false, kMin: 8, sampleN: 0, overall: { settled: 0, wins: 0, netPnlUsd: 0, confirmed: 0, refuted: 0, unresolved: 0, samples: 0, accuracy: null }, byCategory: [] },
    ...over,
  };
}

describe("标签表", () => {
  it("三层与六类都有中文标签", () => {
    expect(Object.keys(TIER_LABEL)).toEqual(["ultra", "mid", "long"]);
    expect(Object.keys(CATEGORY_LABEL)).toHaveLength(6);
  });

  it("方向色点用全站的多空色", () => {
    expect(stanceColor("bullish")).toBe("var(--long)");
    expect(stanceColor("bearish")).toBe("var(--short)");
    expect(stanceColor("neutral")).toBe("var(--muted)");
  });
});

describe("时间", () => {
  it("相对时间区分过去与未来", () => {
    expect(relativeTime(NOW_S - 4 * 3600, NOW_MS)).toBe("4 小时前");
    expect(relativeTime(NOW_S + 3 * 86400, NOW_MS)).toBe("3 天后");
    expect(relativeTime(NOW_S - 30, NOW_MS)).toBe("1 分钟前");
  });

  it("无时间说「时间未知」而不是伪造一个时刻", () => {
    expect(relativeTime(0, NOW_MS)).toBe("时间未知");
    expect(absoluteTime(0)).toBe("时间未知");
  });

  it("绝对时间同时给本地与 UTC（跨时区核对新闻时这是刚需）", () => {
    const text = absoluteTime(NOW_S);
    expect(text).toContain("本地");
    expect(text).toContain("UTC");
  });
});

describe("排序", () => {
  it("置顶恒在最前，即便它的 score 更低", () => {
    const pinned = item({ id: 2, pinned: true, confidence: 0.05 });
    const strong = item({ id: 3, confidence: 0.95 });
    expect(sortItems([strong, pinned], NOW_MS).map((i) => i.id)).toEqual([2, 3]);
  });

  it("未来事件不衰减（还没发生的事不会因为「早就知道」而变得不重要）", () => {
    const future = item({ eventTs: NOW_S + 5 * 86400 });
    const old = item({ eventTs: NOW_S - 5 * 86400 });
    expect(itemScore(future, NOW_MS)).toBeGreaterThan(itemScore(old, NOW_MS));
  });
});

describe("未读", () => {
  it("徽标 >9 收成 9+", () => {
    expect(unreadBadge(0)).toBe("");
    expect(unreadBadge(3)).toBe("3");
    expect(unreadBadge(12)).toBe("9+");
  });

  it("未读数进 aria-label：只有视觉红点的徽标对读屏用户等于不存在", () => {
    expect(intelButtonLabel("BTC-USDT-SWAP", summary({ unread: 4 }))).toContain("4 条未读");
    expect(intelButtonLabel("BTC-USDT-SWAP", summary())).not.toContain("未读");
  });

  it("按 capturedTs 与 lastSeenTs 判定单条是否未读", () => {
    expect(isUnread(item({ capturedTs: NOW_S + 10 }), summary())).toBe(true);
    expect(isUnread(item({ capturedTs: NOW_S - 10 }), summary())).toBe(false);
  });
});

describe("降级", () => {
  it("文案以后端下发的 message 为准，前端不二次编", () => {
    expect(degradeText({ code: "search_failed", message: "检索请求失败（连接超时）", retryInS: 112 }))
      .toBe("检索请求失败（连接超时），112s 后自动重试");
    expect(degradeText(null)).toBe("");
  });

  it("弱提示不染警示色（常亮的警告会失去意义）", () => {
    expect(isSevereDegrade({ code: "search_empty", message: "m", retryInS: null })).toBe(false);
    expect(isSevereDegrade({ code: "no_provider", message: "m", retryInS: null })).toBe(true);
  });

  it("核查轮的四个降级码逐个分级：只有「没检索到可核对的材料」是弱提示", () => {
    // 空核查轮是例行结果（相关条目已被诚实记成待定），漏登记它会让按钮长期挂着警示色。
    expect(isSevereDegrade({ code: "verify_no_corpus", message: "m", retryInS: null }))
      .toBe(false);
    for (const code of ["verify_no_provider", "verify_search_failed", "verify_llm_failed"]) {
      expect(isSevereDegrade({ code, message: "m", retryInS: null })).toBe(true);
    }
  });

  it("装配码不染警示色的只有两个：给不出出路的那两个（arm-state）", () => {
    // `not_running` 在自动交易没开的整段时间里恒成立，染色等于把警示色常亮；`arm_pending`
    // 最多持续一轮。反过来那四个「用户不动手就永远不会有情报」的码必须染色。
    for (const code of ["not_running", "arm_pending"]) {
      expect(isSevereDegrade({ code, message: "m", retryInS: null })).toBe(false);
    }
    for (const code of ["provider_unset", "key_missing", "provider_unknown", "arm_failed"]) {
      expect(isSevereDegrade({ code, message: "m", retryInS: null })).toBe(true);
    }
  });

  it("按钮按码四分叉：去设置 / 重新装配 / 立即重试 / 什么都不给（arm-state I-5）", () => {
    // ★ 这条闸守的是「三分叉不许顺手拿走一个正常工作的重试入口」：`search_failed` /
    // `circuit_open` / `budget_exhausted` 这些**轮内**降级码在本轮之前就归【立即重试】管，
    // 把它们一并并进「无按钮」，症状是用户点不动一个本来能自愈的降级，而且不报错。
    for (const code of ["no_provider", "disabled", "provider_unset", "key_missing",
                        "provider_unknown"]) {
      expect(degradeAction({ code, message: "m", retryInS: null })).toBe("settings");
    }
    expect(degradeAction({ code: "arm_failed", message: "m", retryInS: null })).toBe("rearm");
    for (const code of ["not_running", "arm_pending"]) {
      expect(degradeAction({ code, message: "m", retryInS: null })).toBe("none");
    }
    for (const code of ["search_failed", "circuit_open", "budget_exhausted", "llm_failed"]) {
      expect(degradeAction({ code, message: "m", retryInS: null })).toBe("retry");
    }
    expect(degradeAction(null)).toBe("none");
  });

  it("按钮 title 在降级时追加降级文案（不打开弹窗也知道出问题了）", () => {
    const text = intelButtonTitle(summary({
      degrade: { code: "no_provider", message: "尚未配置情报检索源", retryInS: null },
    }));
    expect(text).toContain("尚未配置情报检索源");
  });
});

describe("其它纯函数", () => {
  it("置信度分档", () => {
    expect(confidenceBand(0.8)).toBe("较高");
    expect(confidenceBand(0.5)).toBe("中等");
    expect(confidenceBand(0.2)).toBe("偏低");
  });

  it("倒计时；无排期即空串（该行不渲染）", () => {
    expect(nextDueText(summary(), NOW_MS)).toContain("12 分钟后");
    expect(nextDueText(summary({ nextDueTs: null }), NOW_MS)).toBe("");
    expect(nextDueText(summary({ nextDueTs: NOW_S - 10 }), NOW_MS)).toContain("即将开始");
  });

  it("显示层的协议白名单（纵深防御）", () => {
    expect(isSafeUrl("https://a.com")).toBe(true);
    expect(isSafeUrl("javascript:alert(1)")).toBe(false);
    expect(isSafeUrl("file:///etc/passwd")).toBe(false);
  });
});

describe("分组卡上的情报采信回执（intel-session-binding 轴 A-4）", () => {
  it("老后端不下发 ⇒ 空串 ⇒ 整段不渲染（绝不在没有读数时编一句「未采信」）", () => {
    expect(intelReceiptText(null)).toBe("");
  });

  it("用户自己选的 ⇒ 只说档位，不附任何解释", () => {
    expect(intelReceiptText({ enabled: true, mode: "index", source: "request" }))
      .toBe("索引档");
    expect(intelReceiptText({ enabled: false, mode: "off", source: "request" }))
      .toBe("未采信");
  });

  it("服务端改写了用户选择 ⇒ **必须**把改写如实附在后面", () => {
    // `legacy_fallback` 那一格只有「刷新页面后重发现一个暂停中的老会话」才走得到，
    // 而它恰恰是数十条持仓中的暂停会话冷续跑时唯一会走的那一格。
    const legacy = intelReceiptText({
      enabled: true, mode: "full", source: "legacy_fallback",
    });
    expect(legacy).toContain("全文档");
    expect(legacy).toContain("该开关上线之前");

    expect(intelReceiptText({ enabled: true, mode: "full", source: "ops_off" }))
      .toContain("运维");
    expect(intelReceiptText({ enabled: false, mode: "off", source: "resolve_failed" }))
      .toContain("未能解析");
  });
});
