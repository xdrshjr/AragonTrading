import { describe, expect, it } from "vitest";
import { FULL_REASON_ZH, HARVEST_REASON_ZH, honestyNotes, utilityCellText } from "./experience";

// 效用与采集完整性的渲染判据（ai-trade-experience-compounding W-C / W-A / W-F）。
//
// 这三组文案都是 I-8 的载体：后端算了却没有渲染落点的观测量等于死码。而 J-5 在这里的具体
// 形态是**措辞**——「注入后表现」是共现，说成「贡献」就是把一个我们无法确认的因果讲给用户。

describe("utilityCellText（列表页那一格的三态）", () => {
  it("无样本 ⇒ 一个破折号，而不是「注入 0 次」这种假读数", () => {
    expect(utilityCellText(undefined)).toBe("—");
    expect(utilityCellText({ injected: 0, meanNetAfter: null, agreement: null,
                             ineffective: false })).toBe("—");
  });

  it("有样本 ⇒ 注入次数 / 注入后净额 / 与事实一致率三个数", () => {
    const text = utilityCellText({ injected: 21, meanNetAfter: -8.4, agreement: 0.71,
                                   ineffective: false });
    expect(text).toContain("注入 21");
    expect(text).toContain("-8.4bps");
    expect(text).toContain("一致 71%");
  });

  it("有注入、但没有一条口径合格 ⇒ 明说「样本不足」，绝不填一个 0bps", () => {
    expect(utilityCellText({ injected: 5, meanNetAfter: null, agreement: null,
                             ineffective: false })).toContain("样本不足");
  });

  it("这一格的措辞里不许出现任何因果词（J-5 / G-13）", () => {
    const text = utilityCellText({ injected: 21, meanNetAfter: -8.4, agreement: 0.71,
                                   ineffective: true });
    expect(text).not.toMatch(/贡献|带来|因此|导致/);
  });
});

describe("闭合词表（键集与后端逐字对齐，多一个就是假分类）", () => {
  it("采集缺口的六种原因一个不多、一个不少", () => {
    expect(Object.keys(HARVEST_REASON_ZH).sort()).toEqual([
      "noOpenContext", "noTrip", "offlineInconsistent", "offlineNoReadback",
      "persistFailed", "slotEvicted",
    ]);
  });

  it("「为什么走了全量」的六个原因都被翻译成人话（枚举不是给用户看的）", () => {
    expect(Object.keys(FULL_REASON_ZH).sort()).toEqual([
      "count_mismatch", "cuts_drift", "disabled", "no_epoch", "node_cap", "taxonomy",
    ]);
    for (const label of Object.values(FULL_REASON_ZH)) {
      expect(label).not.toMatch(/[a-z_]{4,}/);
    }
  });
});

describe("概览注记里的 W-B 可见性键", () => {
  const HEALTH = {
    lastTs: 1_700_000_000, lastMs: 1234, events: 2043,
    slow: false, torn: false, overflow: false,
    mode: "incremental" as const, fullReason: null, pendingEvents: 0,
  };

  it("待处理为 0 时不渲染那半句（不制造一句无信息量的噪音）", () => {
    const notes = honestyNotes({ health: HEALTH, excludedByCostBasis: 0, harvestGapTotal: 0 });
    expect(notes[0].text).toContain("增量");
    expect(notes[0].text).not.toContain("待处理");
  });

  it("一个悄悄吃不完的队列必须能被看见", () => {
    const notes = honestyNotes({
      health: { ...HEALTH, pendingEvents: 8421 },
      excludedByCostBasis: 0, harvestGapTotal: 0,
    });
    expect(notes[0].text).toContain("待处理 8421 条");
  });

  it("补采是正面数字、静音是共现——两句话都必须在，且都不许暗示因果", () => {
    const texts = honestyNotes({
      health: HEALTH, excludedByCostBasis: 0, harvestGapTotal: 9,
      adoptedTotal: 4, utilityMuted: 2,
    }).map((n) => n.text);
    expect(texts.some((t) => t.includes("4 笔已由「接管补采」"))).toBe(true);
    const muted = texts.find((t) => t.includes("2 条经验"));
    expect(muted).toContain("共现关系，不是因果");
    for (const text of texts) expect(text).not.toMatch(/贡献|带来了|导致盈利/);
  });
});
