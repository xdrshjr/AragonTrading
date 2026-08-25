import { describe, expect, it } from "vitest";
import {
  AI_TRADE_EXPERIENCE_DEFAULTS,
  budgetText,
  describeSignature,
  formatNetBps,
  formatPercent,
  formatTStat,
  honestyNotes,
  previewParityText,
  relativeTime,
  shortTime,
  tierLabel,
} from "./experience";

const HEALTH = {
  lastTs: 1_700_000_000, lastMs: 1234, events: 2043,
  slow: false, torn: false, overflow: false,
};

describe("经验模式默认值", () => {
  it("默认开启、只用本标的、写回经验库（需求明写「默认开启」）", () => {
    expect(AI_TRADE_EXPERIENCE_DEFAULTS).toEqual({
      enabled: true, scope: "symbol", write: true,
    });
  });
});

describe("诚实性注记（I-8 的渲染载体）", () => {
  it("正常状态只给一行「上次重建」，且不是警告色", () => {
    const notes = honestyNotes({ health: HEALTH, excludedByCostBasis: 0, harvestGapTotal: 0 });
    expect(notes).toHaveLength(1);
    expect(notes[0].warn).toBe(false);
    expect(notes[0].text).toContain("2043 条事件样本");
  });

  it("overflow 优先于 slow（都说话会让用户不知道先做哪一件）", () => {
    const notes = honestyNotes({
      health: { ...HEALTH, slow: true, overflow: true },
      excludedByCostBasis: 0, harvestGapTotal: 0,
    });
    const warnings = notes.filter((n) => n.warn).map((n) => n.text);
    expect(warnings).toHaveLength(1);
    expect(warnings[0]).toContain("自动重建已停止");
  });

  it("每一条警告都带**可执行**的建议，而不是一句「异常」", () => {
    const notes = honestyNotes({
      health: { ...HEALTH, slow: true, torn: true },
      excludedByCostBasis: 0, harvestGapTotal: 0,
    });
    for (const note of notes.filter((n) => n.warn)) {
      expect(note.text).toMatch(/手动重建|导出归档/);
    }
  });

  it("两个采集缺口各有一句人话（它们说的是「这张表偏乐观」）", () => {
    const notes = honestyNotes({
      health: HEALTH, excludedByCostBasis: 7, harvestGapTotal: 3,
    }).map((n) => n.text);
    expect(notes.some((t) => t.includes("7 笔往返因成本口径不可比"))).toBe(true);
    expect(notes.some((t) => t.includes("3 笔往返因续跑接管") && t.includes("偏乐观"))).toBe(true);
  });

  it("库是空的（从未重建过）⇒ 一条注记都不渲染", () => {
    expect(honestyNotes({ health: null, excludedByCostBasis: 0, harvestGapTotal: 0 }))
      .toEqual([]);
  });
});

describe("检索预演的状态行（少了它，这一屏在会话跑起来时说的是假话）", () => {
  it("无运行中会话 ⇒ 说的是「下一次启动」", () => {
    expect(previewParityText([])).toContain("下一次启动会话");
  });

  it("有会话且未分叉 ⇒ 指名那个标的", () => {
    const text = previewParityText([
      { instId: "BTC-USDT-SWAP", graphAsOfTs: 1_700_000_000, diverged: false }]);
    expect(text).toContain("BTC-USDT-SWAP");
    expect(text).not.toContain("不同");
  });

  it("分叉 ⇒ 明说「与下方预演不同」并给出那一刻的快照时间", () => {
    const text = previewParityText([
      { instId: "BTC-USDT-SWAP", graphAsOfTs: 1_700_000_000, diverged: true },
      { instId: "ETH-USDT-SWAP", graphAsOfTs: 1_700_000_000, diverged: false }]);
    expect(text).toContain("BTC-USDT-SWAP");
    expect(text).toContain("不同");
    expect(text).toContain("下次自动重建");
    expect(text).not.toContain("ETH-USDT-SWAP");   // 没分叉的那个不该被拉进警告里
  });
});

describe("格式化", () => {
  it("缺席的统计量显示「—」而不是 0（0 是一个会被读成事实的数字）", () => {
    expect(formatTStat(null)).toBe("—");
    expect(formatTStat(Number.NaN)).toBe("—");
    expect(formatNetBps(undefined)).toBe("—");
    expect(formatNetBps(-31.24)).toBe("-31.2bps");
    expect(formatNetBps(12)).toBe("+12.0bps");
  });

  it("从未更新过显示「尚未」，绝不显示 1970", () => {
    expect(relativeTime(0)).toBe("尚未");
    expect(relativeTime(null)).toBe("尚未");
    expect(shortTime(0)).toBe("");
  });

  it("相对时间按量级降级", () => {
    const now = 1_700_000_000_000;
    expect(relativeTime(1_700_000_000 - 10, now)).toBe("刚刚");
    expect(relativeTime(1_700_000_000 - 600, now)).toBe("10 分钟前");
    expect(relativeTime(1_700_000_000 - 7200, now)).toBe("2 小时前");
    expect(relativeTime(1_700_000_000 - 3 * 86400, now)).toBe("3 天前");
  });

  it("档位标签把用户意志排在自动档位之前", () => {
    expect(tierLabel({ tier: "iron", muted: true, pinned: true })).toBe("已沉降");
    expect(tierLabel({ tier: "candidate", muted: false, pinned: true })).toBe("已置顶");
    expect(tierLabel({ tier: "iron", muted: false, pinned: false })).toBe("铁律");
  });

  it("语境短语不含标的与周期（模板前缀已单独渲染它们）", () => {
    expect(describeSignature({
      symbol: "BTC-USDT-SWAP", bar: "15m", volatility: "high_volatility", side: "long",
    })).toBe("高波动+做多");
    expect(describeSignature({ symbol: "BTC-USDT-SWAP" })).toBe("全局");
    expect(describeSignature({ regime: "unknown" })).toBe("全局");   // unknown 不占位
  });

  it("字符预算文案", () => {
    expect(budgetText(312, 480)).toBe("312 / 480 字");
  });

  it("比率类统计量按 0~1 小数换算成百分比（wire 里就是小数，后端渲染文案时同样 ×100）", () => {
    // 直接 `toFixed(0) + "%"` 会把 38% 的胜率显示成「0%」——一个连自己的胜率都写错的看板，
    // 比没有这一栏更坏。
    expect(formatPercent(0.38)).toBe("38%");
    expect(formatPercent(0.615)).toBe("62%");
    expect(formatPercent(0.615, 1)).toBe("61.5%");
    expect(formatPercent(1)).toBe("100%");
    expect(formatPercent(null)).toBe("—");
    expect(formatPercent(undefined)).toBe("—");
  });
});
