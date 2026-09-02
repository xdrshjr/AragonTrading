// 真值源 + 费率来源 + 抓取健康的前端纯函数门禁
// （ai-trade-silent-degradation-repair 轴 A-4 / B-2 / E）。
import { describe, expect, it } from "vitest";
import { describeFeeRateError, describeFeeRates, feeOriginLabel } from "./aiTradeCost";
import { describeEmptyBodyCache, describeReaderHealth } from "./intelEvidence";
import {
  TRUTH_STATE_DOT,
  TRUTH_STATE_LABEL,
  describeTruthDigest,
  summarizeTruthSources,
  type TruthSourceRow,
} from "./truthSources";

const RATES = {
  makerBps: 2, takerBps: 5, level: "Lv1", ruleType: "normal",
  source: "exchange:family", rebate: false, ageS: 1, stale: false,
  makerBpsRaw: 2, takerBpsRaw: 5,
};

describe("describeFeeRates 的值域从 2 个扩到 4 个（轴 A-2）", () => {
  it("阶梯两跳各有自己的档位语义，绝不压成同一句话", () => {
    expect(feeOriginLabel({ ...RATES, source: "exchange:family" })).toBe("交易所官方档位 Lv1");
    expect(feeOriginLabel({ ...RATES, source: "exchange:type" })).toBe("交易所账户通用档位 Lv1");
  });

  it("老值 `exchange` 必须继续被识别——生产库里已经落了带这个值的历史记录", () => {
    expect(feeOriginLabel({ ...RATES, source: "exchange" })).toBe("交易所官方档位 Lv1");
  });

  it("未知值一律保守地按「本地保守常数」处理", () => {
    expect(feeOriginLabel({ ...RATES, source: "config" })).toBe("本地保守常数");
    expect(feeOriginLabel({ ...RATES, source: "whatever" })).toBe("本地保守常数");
  });

  it("缺席 ⇒ null ⇒ 调用方整行不渲染", () => {
    expect(describeFeeRates(null)).toBeNull();
    expect(describeFeeRates(undefined)).toBeNull();
  });
});

describe("describeFeeRateError：本地常数不能只说「这是猜的」，还要说为什么（轴 A-5）", () => {
  it("交易所拒绝 ⇒ 一句人话 + 括号里的原始码（§14.4 第 1 步的硬判据）", () => {
    // `50016 / instId and instType don't match` **不是编出来的**：它是实施节点用
    // `scripts/probe_trade_fee.py` 在这台真机上拿到的、那 1,524 次 400 的真实回执
    // （spec 附录 C）。这条用例因此同时是一份「用户到底会读到什么」的冻结。
    const line = describeFeeRates({
      ...RATES, source: "config", level: "", lastError: {
        rung: "type", httpStatus: 400, code: "50016",
        msg: "instId and instType don't match",
      },
    });
    expect(line).toContain("本地保守常数：maker 2 / taker 5 bps");
    expect(line).toContain("交易所拒绝了费率查询（50016 instId and instType don't match）");
    expect(line).toContain("成本与净额按保守常数计算");
  });

  it("传输层失败（没有 HTTP 状态）说的是另一句话——它指向的方向不同", () => {
    const text = describeFeeRateError({
      rung: "type", httpStatus: null, code: "OKX_UNREACHABLE", msg: "无法连接",
    });
    expect(text).toContain("未能连上交易所查询费率");
  });

  it("没失败过 ⇒ null ⇒ 不追加尾注（绝不渲染一个空括号）", () => {
    expect(describeFeeRateError(null)).toBeNull();
    expect(describeFeeRateError({ rung: "type", httpStatus: 400, code: "", msg: "" })).toBeNull();
    expect(describeFeeRates({ ...RATES, lastError: null })).not.toContain("——");
  });
});

describe("describeReaderHealth 三态（轴 B-2）", () => {
  it("没尝试过 ⇒ 中性；抓到过 ⇒ 正常；尝试了全失败 ⇒ 危险 + 成因", () => {
    expect(describeReaderHealth({ fetchAttempt: 0, fetchOk: 0 })?.tone).toBe("neutral");
    expect(describeReaderHealth({ fetchAttempt: 6, fetchOk: 5 })).toEqual({
      tone: "ok", text: "正文抓取 5/6", hint: "",
    });
    const bad = describeReaderHealth({ fetchAttempt: 96, fetchOk: 0 });
    expect(bad?.tone).toBe("danger");
    expect(bad?.text).toBe("正文抓取 0/96 全部失败");
    expect(bad?.hint).toContain("198.18.0.0/15");
  });

  it("老回执（两个键缺席）⇒ null ⇒ 绝不把 undefined 当 0 渲染成假的「0/0 全部失败」", () => {
    expect(describeReaderHealth(null)).toBeNull();
    expect(describeReaderHealth({})).toBeNull();
    expect(describeReaderHealth({ fetchAttempt: 3 })).toBeNull();
  });
});

describe("describeEmptyBodyCache：定义上不可能的那对读数", () => {
  it("缓存 29 篇 / 正文 0 字节 ⇒ 说出结论", () => {
    expect(describeEmptyBodyCache({ cachedDocs: 29, bodyBytes: 0 }))
      .toBe("—— 这些文档一篇正文都没抓下来");
  });

  it("其余组合一律 null（包括「一篇都没缓存」这种正常空态）", () => {
    expect(describeEmptyBodyCache({ cachedDocs: 0, bodyBytes: 0 })).toBeNull();
    expect(describeEmptyBodyCache({ cachedDocs: 29, bodyBytes: 12 })).toBeNull();
    expect(describeEmptyBodyCache(null)).toBeNull();
  });
});

describe("真值源卡片的前端派生（轴 E）", () => {
  const row = (over: Partial<TruthSourceRow>): TruthSourceRow => ({
    key: "feeRates", label: "交易所费率", state: "ok", reading: "", reason: "",
    since: 0, action: null, ...over,
  });

  it("状态绝不靠颜色单独承载：四态各有文字标签与色点", () => {
    expect(Object.keys(TRUTH_STATE_LABEL).sort())
      .toEqual(["degraded", "off", "ok", "unknown"]);
    expect(Object.keys(TRUTH_STATE_DOT).sort())
      .toEqual(["degraded", "off", "ok", "unknown"]);
    // `unknown` 与 `off` 是两件事，色点也必须不同。
    expect(TRUTH_STATE_DOT.unknown).not.toBe(TRUTH_STATE_DOT.off);
  });

  it("折叠态摘要：一切正常时不带颜色，有降级时才带", () => {
    const ok = summarizeTruthSources([row({}), row({ key: "privateQuota" })]);
    expect(ok).toEqual({ text: "真值源 2 项 · 全部正常", degraded: 0, tone: "ok" });
    const warn = summarizeTruthSources([row({}), row({ state: "degraded" })]);
    expect(warn.tone).toBe("warn");
    expect(warn.text).toBe("真值源 2 项 · 1 项降级");
  });

  it("`unknown` / `off` 绝不被并进「全部正常」——那是这张卡片自己要指出的那种假话", () => {
    // 没在跑时四行全是 `unknown`（G-E2 的那一格）：摘要必须说「暂无读数」而不是「全部正常」，
    // 且**不带颜色**——「不知道」不是告警，把它染成琥珀色会让用户去修一件没坏的事。
    const idle = summarizeTruthSources([row({ state: "unknown" }), row({ state: "unknown" })]);
    expect(idle).toEqual({ text: "真值源 2 项 · 2 项暂无读数", degraded: 0, tone: "ok" });
    const mixed = summarizeTruthSources([row({}), row({ state: "off" })]);
    expect(mixed.text).toBe("真值源 2 项 · 1 项暂无读数");
    // 有降级时降级优先：它是这一行上唯一需要被立刻读到的事。
    const both = summarizeTruthSources([row({ state: "unknown" }), row({ state: "degraded" })]);
    expect(both.text).toBe("真值源 2 项 · 1 项降级");
  });

  it("顶栏芯片的**分母是 3 不是 4**，且一切正常时不出现", () => {
    expect(describeTruthDigest({ trackedCount: 3, degradedCount: 2 }))
      .toBe("3 项真值源中 2 项降级");
    expect(describeTruthDigest({ trackedCount: 3, degradedCount: 0 })).toBeNull();
    expect(describeTruthDigest(null)).toBeNull();
    expect(describeTruthDigest({})).toBeNull();
  });
});
