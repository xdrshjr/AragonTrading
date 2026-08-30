import { describe, expect, it } from "vitest";
import {
  accuracyLabel,
  alertRowState,
  alertRowText,
  alertRowTone,
  collapseAlerts,
  conflictIds,
  conflictPartners,
  conflictTitle,
  efficacyReadyText,
  sortSourcesByAccuracy,
  statText,
  weightLabel,
} from "./intelActionability";
import type { IntelAlert, IntelConflict, IntelSourceRow, IntelStat } from "./intelTypes";

const NOW_S = 1_788_110_000;
const NOW_MS = NOW_S * 1000;

function alert(over: Partial<IntelAlert> = {}): IntelAlert {
  return {
    id: 1,
    instId: "BTC-USDT-SWAP",
    groupId: "g",
    itemId: 41,
    roundId: 7,
    createdTs: NOW_S - 30,
    severity: 0.82,
    fired: true,
    suppressedCode: "",
    suppressedText: "",
    outcome: "",
    outcomeText: "",
    outcomeTs: 0,
    runId: 0,
    roundNo: 0,
    headline: "某交易所热钱包异常出金",
    category: "risk",
    tier: "ultra",
    stance: "bearish",
    impact: "high",
    domainCount: 3,
    quoted: true,
    confidence: 0.78,
    ref: "I02",
    inDigest: true,
    reasons: {},
    ...over,
  };
}

function stat(over: Partial<IntelStat> = {}): IntelStat {
  return {
    settled: 0,
    wins: 0,
    netPnlUsd: 0,
    confirmed: 0,
    refuted: 0,
    unresolved: 0,
    samples: 0,
    accuracy: null,
    ...over,
  };
}

describe("告警行的三态（§3.4.1）", () => {
  it("未告警 → 灰行 + 后端下发的中文抑制文案", () => {
    const row = alert({ fired: false, suppressedCode: "daily_cap", suppressedText: "今日告警额度已用尽" });
    expect(alertRowState(row, NOW_MS, 180)).toBe("suppressed");
    expect(alertRowText(row, "suppressed")).toBe("未告警 · 今日告警额度已用尽");
    expect(alertRowTone(row, "suppressed")).toBe("var(--muted)");
  });

  it("后端没给文案时退回裸码，绝不显示一个空句子", () => {
    const row = alert({ fired: false, suppressedCode: "weird", suppressedText: "" });
    expect(alertRowText(row, "suppressed")).toBe("未告警 · weird");
  });

  it("已告警但还没结果 → 等待响应；超过时限 → 未在时限内响应（渲染期判定，不回写）", () => {
    const row = alert();
    expect(alertRowState(row, NOW_MS, 180)).toBe("waiting");
    expect(alertRowText(row, "waiting")).toBe("已告警 · 等待会话响应");
    const stale = alert({ createdTs: NOW_S - 10_000 });
    expect(alertRowState(stale, NOW_MS, 180)).toBe("expired");
    expect(alertRowText(stale, "expired")).toBe("已告警 · 会话未在时限内响应");
    expect(alertRowTone(stale, "expired")).toBe("var(--warn)");
  });

  it("已有结果 → 用后端下发的结果文案，`woke` 用强调墨色", () => {
    const row = alert({ outcome: "woke", outcomeText: "已唤醒一轮决策", runId: 3, roundNo: 9 });
    expect(alertRowState(row, NOW_MS, 180)).toBe("settled");
    expect(alertRowText(row, "settled")).toBe("已告警 · 已唤醒一轮决策");
    // 面色不得用作文字色（D2 的成文纪律 + 源码扫描门禁）。
    expect(alertRowTone(row, "settled")).toBe("var(--accent-ink)");
  });
});

describe("连续同码折叠（否则真正的事件会被淹掉）", () => {
  it("相邻同码的未告警压成一行 + 计数", () => {
    const rows = [
      alert({ id: 5, fired: false, suppressedCode: "impact_below_bar" }),
      alert({ id: 4, fired: false, suppressedCode: "impact_below_bar" }),
      alert({ id: 3, fired: false, suppressedCode: "impact_below_bar" }),
    ];
    const groups = collapseAlerts(rows);
    expect(groups).toHaveLength(1);
    expect(groups[0].count).toBe(3);
    expect(groups[0].head.id).toBe(5);
  });

  it("已告警的行**永不折叠**，且它会打断折叠链", () => {
    const groups = collapseAlerts([
      alert({ id: 5, fired: false, suppressedCode: "cooldown" }),
      alert({ id: 4, outcome: "woke" }),
      alert({ id: 3, fired: false, suppressedCode: "cooldown" }),
      alert({ id: 2, outcome: "woke" }),
    ]);
    expect(groups.map((g) => [g.head.id, g.count])).toEqual([[5, 1], [4, 1], [3, 1], [2, 1]]);
  });

  it("不同抑制码不折叠（它们说的是不同的事）", () => {
    const groups = collapseAlerts([
      alert({ id: 5, fired: false, suppressedCode: "cooldown" }),
      alert({ id: 4, fired: false, suppressedCode: "few_domains" }),
    ]);
    expect(groups).toHaveLength(2);
  });
});

describe("战绩与信誉的诚实文案（R-6）", () => {
  it("准确率为 null ⇒ 「样本不足」而不是 0%", () => {
    expect(accuracyLabel(null)).toBe("样本不足");
    expect(accuracyLabel(undefined)).toBe("样本不足");
    expect(accuracyLabel(0)).toBe("0%");
    expect(accuracyLabel(0.833)).toBe("83%");
  });

  it("样本不足时如实写「样本 3/8，先验未生效」而不是画一个假的权重条", () => {
    const text = efficacyReadyText({ enabled: true, kMin: 8, sampleN: 3, overall: stat(), byCategory: [] });
    expect(text).toContain("样本 3/8");
    expect(text).toContain("未生效");
    const ready = efficacyReadyText({ enabled: true, kMin: 8, sampleN: 9, overall: stat(), byCategory: [] });
    expect(ready).toContain("已生效");
    expect(efficacyReadyText({ enabled: false, kMin: 8, sampleN: 0, overall: stat(), byCategory: [] }))
      .toBe("战绩统计已在设置中关闭");
    expect(efficacyReadyText(null)).toBe("");
  });

  it("零样本的桶 ⇒ 空串 ⇒ 整行不渲染（不出现「0 胜 0 负」的噪音）", () => {
    expect(statText(stat())).toBe("");
    expect(statText(stat({ settled: 2, wins: 1, samples: 2 }))).toBe("已结算 2 · 胜 1");
    expect(statText(stat({ confirmed: 3, refuted: 1, samples: 4 })))
      .toBe("证实 3 · 证伪 1 · 待定 0");
  });

  it("权重的方向被写成人话，零值说「无影响」", () => {
    expect(weightLabel(0.12)).toContain("更靠前");
    expect(weightLabel(-0.2)).toContain("更靠后");
    expect(weightLabel(0)).toBe("0（无影响）");
  });

  it("按准确率排序时样本不足的行恒排最后，绝不与真实读数混排", () => {
    const rows: IntelSourceRow[] = [
      { domain: "a.com", itemCount: 9, lastTs: 1, state: "", stateInstId: "", confirmed: 1, refuted: 0, unresolved: 0, accuracy: null },
      { domain: "b.com", itemCount: 2, lastTs: 1, state: "", stateInstId: "", confirmed: 4, refuted: 1, unresolved: 0, accuracy: 0.8 },
      { domain: "c.com", itemCount: 3, lastTs: 1, state: "", stateInstId: "", confirmed: 9, refuted: 0, unresolved: 0, accuracy: 1 },
    ];
    expect(sortSourcesByAccuracy(rows).map((r) => r.domain)).toEqual(["c.com", "b.com", "a.com"]);
    expect(rows[0].domain).toBe("a.com"); // 不就地改原数组
  });
});

describe("矛盾对的索引与文案（轴 C）", () => {
  const pair: IntelConflict = { aId: 4, bId: 9, overlap: 0.62, windowH: 24, category: "market", openRounds: 3 };

  it("双向索引，重叠度最高的那一对先到先得", () => {
    expect(conflictPartners([pair])).toEqual({ 4: 9, 9: 4 });
    const second: IntelConflict = { ...pair, bId: 11 };
    expect(conflictPartners([pair, second])[4]).toBe(9);
    expect(conflictIds([pair])).toEqual(new Set([4, 9]));
  });

  it("悬浮说明带上对方标题与悬置轮数，且明说系统不会自动删除任何一方", () => {
    const partner = { id: 9, headline: "ETF 单日净流出创年内新高" } as never;
    const text = conflictTitle(pair, partner);
    expect(text).toContain("ETF 单日净流出创年内新高");
    expect(text).toContain("已悬置 3 轮整理");
    expect(text).toContain("不会自动删除");
    // ⚠️ 这段文案会原样进 `title=` 属性：带 Markdown 星号会被原样显示成两颗星。
    expect(text).not.toContain("**");
  });

  it("没有矛盾对 ⇒ 空串 ⇒ 整枚徽标不渲染", () => {
    expect(conflictTitle(undefined, undefined)).toBe("");
  });
});
