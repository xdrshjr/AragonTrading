import { describe, expect, it } from "vitest";
import {
  actionLabel,
  captureTierLabel,
  contextModeLabel,
  degradeLabel,
  eventLabel,
  exitSourceLabel,
  formatBytes,
  formatLocal,
  formatNumber,
  formatSeconds,
  formatUtc,
  gateVerdictLabel,
  linkConfidenceGlyph,
  linkConfidenceLabel,
  linkConfidenceTone,
  skippedLabel,
  triggerLabel,
} from "./journal";

/**
 * 交易日志的中文化与格式化（ai-trade-audit-journal §六 第 6 条）。
 *
 * 最要紧的那条是「未知码回退到**原码**而不是空串」：一个显示成空白的降级原因，会让用户
 * 以为「这一轮没有降级」——那正是本轮要消灭的那种静默。
 */
describe("中文化表", () => {
  it("已知码给中文", () => {
    expect(degradeLabel("low_consensus")).toBe("同向共识不足");
    expect(eventLabel("ROUND_SKIPPED")).toBe("本轮被整轮跳过");
    expect(triggerLabel("interval")).toBe("定时唤醒");
    expect(exitSourceLabel("profit_guard")).toBe("利润保护");
    expect(actionLabel("open_long")).toBe("开多");
  });

  it("未知码回退到原码，绝不回退成空串", () => {
    expect(degradeLabel("brand_new_code")).toBe("brand_new_code");
    expect(eventLabel("BRAND_NEW_EVENT")).toBe("BRAND_NEW_EVENT");
    expect(exitSourceLabel("who_knows")).toBe("who_knows");
  });

  it("空值给空串（调用方据此决定显示 — 还是不显示）", () => {
    expect(degradeLabel(null)).toBe("");
    expect(degradeLabel(undefined)).toBe("");
    expect(eventLabel("")).toBe("");
  });

  it("九个 skipped 桶各有中文名，且 contextIncomplete 与别的桶说的不是一件事", () => {
    const keys = [
      "gateVetoed", "degraded", "unsettled", "hold", "lowConfidence",
      "promptDrift", "payloadTruncated", "minimalTier", "contextIncomplete",
    ];
    const labels = keys.map(skippedLabel);
    expect(new Set(labels).size).toBe(keys.length);
    expect(skippedLabel("contextIncomplete")).toContain("上下文链");
  });
});

describe("关联置信度的三重编码", () => {
  it("形状 + 文案 + 颜色三者齐全，语义绝不靠颜色单独承载", () => {
    expect(linkConfidenceGlyph("exact")).toBe("✓");
    expect(linkConfidenceGlyph("heuristic")).toBe("≈");
    expect(linkConfidenceGlyph("none")).toBe("—");
    expect(linkConfidenceGlyph(null)).toBe("—");
    expect(linkConfidenceLabel("exact")).toContain("交易所单号");
    expect(linkConfidenceLabel(null)).toBe(linkConfidenceLabel("none"));
    expect(linkConfidenceTone("exact")).toBe("long");
    expect(linkConfidenceTone("heuristic")).toBe("accent");
    expect(linkConfidenceTone(null)).toBe("neutral");
  });
});

describe("门禁三态 + 未知态", () => {
  it("armed 为 null 时说「未知」，绝不说「未装配」", () => {
    expect(gateVerdictLabel("pass", null)).toContain("未知");
    expect(gateVerdictLabel("veto", null)).toContain("未知");
  });

  it("三个确定态各有各的说法", () => {
    expect(gateVerdictLabel("veto", true)).toBe("否决");
    expect(gateVerdictLabel("skip", false)).toContain("未装配");
    expect(gateVerdictLabel("pass", true)).toBe("放行");
  });
});

describe("格式化", () => {
  it("体积按量级换单位", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(2048)).toBe("2.0 KB");
    expect(formatBytes(5 * 1024 * 1024)).toBe("5.0 MB");
    expect(formatBytes(null)).toBe("—");
    expect(formatBytes(-1)).toBe("—");
  });

  it("时长按秒 / 分说", () => {
    expect(formatSeconds(42)).toBe("42 秒");
    expect(formatSeconds(120)).toBe("2 分钟");
    expect(formatSeconds(125)).toBe("2 分 5 秒");
    expect(formatSeconds(null)).toBe("—");
  });

  it("时间双列：UTC 与本地各出一份（审计口径必须无歧义）", () => {
    expect(formatUtc(1788000900)).toMatch(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/);
    expect(formatLocal(1788000900)).toMatch(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/);
    expect(formatUtc(0)).toBe("—");
    expect(formatLocal(null)).toBe("—");
  });

  it("数字缺失一律 —，绝不显示 0", () => {
    expect(formatNumber(1.23456, 2)).toBe("1.23");
    expect(formatNumber(null)).toBe("—");
    expect(formatNumber(undefined)).toBe("—");
    expect(formatNumber(Number.NaN)).toBe("—");
    expect(formatNumber(0)).toBe("0.00");
  });

  it("采集档与上下文模式各有一句解释", () => {
    expect(captureTierLabel("minimal")).toContain("精简");
    expect(captureTierLabel("full")).toContain("完整");
    expect(contextModeLabel("full")).toContain("与模型当时看到的一致");
    expect(contextModeLabel("lastTurnOnly")).toContain("不同");
  });
});
