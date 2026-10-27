import { describe, expect, it } from "vitest";
import {
  EVIDENCE_FOOTNOTES,
  EVIDENCE_POLICY_OPTIONS,
  backfillSampleCount,
  bucketsForInst,
  daysToMinSamples,
  describeBackfill,
  describeLiveCovered,
  evidenceInterval,
  evidenceScale,
  evidenceSessionText,
  evidenceStateLabel,
  fmtEvidenceWinRate,
  fmtSignedBps,
  formatEvidenceReading,
  hasProvenBucket,
  sideSegments,
  type AiTradeEvidenceBackfillOut,
  type AiTradeEvidenceBucketOut,
  type AiTradeEvidenceResponse,
  type AiTradeEvidenceSide,
  type AiTradeEvidenceStatus,
} from "./aiTradeEvidence";

/**
 * 实证准入展示纯函数（ai-trade-evidence-gated-capital §7.5 F-1..F-10）。
 *
 * 三态与区间全部由后端判定；这里守的是「说成人话」这一步不说错——尤其是读数的符号、
 * 缺席方向不造段、速度拿不到时不猜天数。
 */

const LONG: AiTradeEvidenceSide = {
  state: "unproven", basis: "bucket", n: 7, meanBps: 1.3, lcbBps: -2.1, ucbBps: 4.7,
  winRate: 0.57, gating: "shadow", openShadow: true, sessionShadows: 9, sessionTraded: 0,
};

const SHORT: AiTradeEvidenceSide = {
  state: "disproven", basis: "pooled", n: 3, meanBps: -6.2, lcbBps: -9, ucbBps: -1.1,
  winRate: 0.33, gating: "shadow", openShadow: false, sessionShadows: 5, sessionTraded: 3,
};

function status(over: Partial<AiTradeEvidenceStatus> = {}): AiTradeEvidenceStatus {
  return {
    policy: "strict", minSamples: 10, refPolicy: "ref-v1:x",
    sides: { long: LONG, short: SHORT },
    session: { intents: 42, shadows: 14, repeats: 28, matured: 11, traded: 3, voided: 0, dropped: 0 },
    snapshotError: false,
    ...over,
  };
}

function bucket(over: Partial<AiTradeEvidenceBucketOut>): AiTradeEvidenceBucketOut {
  return {
    instId: "ETH-USDT-SWAP", side: "long", state: "unproven", basis: "bucket", n: 4,
    meanBps: 1, seBps: 2, lcbBps: -1, ucbBps: 3, winRate: 0.5, gating: "shadow",
    backfillN: 0, openN: 0, lastMaturedTs: null, ...over,
  };
}

function response(over: Partial<AiTradeEvidenceResponse> = {}): AiTradeEvidenceResponse {
  return {
    bar: "30m", refPolicy: "ref-v1:x", asOf: 1790300000,
    rule: { policy: "strict", minSamples: 10, window: 30, z: 1, maxAgeDays: 30 },
    buckets: [],
    recentShadowRatePerDay: {},
    ...over,
  };
}

describe("F-1 三态标签", () => {
  it("三态逐字，未知态原样回退", () => {
    expect(evidenceStateLabel("proven")).toBe("已证实");
    expect(evidenceStateLabel("unproven")).toBe("未证实");
    expect(evidenceStateLabel("disproven")).toBe("已证伪");
    expect(evidenceStateLabel("brandNew")).toBe("brandNew");
  });
});

describe("F-2 / F-3 读数格式", () => {
  it("正数带 +，负号是 U+2212，缺失是 —", () => {
    expect(fmtSignedBps(3.14)).toBe("+3.1");
    expect(fmtSignedBps(-6.2)).toBe("−6.2");
    expect(fmtSignedBps(-0.01)).toBe("0.0");
    expect(fmtSignedBps(null)).toBe("—");
    expect(fmtSignedBps(Number.NaN)).toBe("—");
  });

  it("未证实且样本不足 ⇒ 进度；否则均值 + 下界；已证伪报上界", () => {
    expect(formatEvidenceReading(LONG, 10)).toBe("7/10");
    expect(formatEvidenceReading({ ...LONG, n: 12 }, 10)).toBe("均值 +1.3 bps（下界 −2.1）");
    expect(formatEvidenceReading({ ...LONG, state: "proven", n: 12, lcbBps: 0.4 }, 10))
      .toBe("均值 +1.3 bps（下界 +0.4）");
    expect(formatEvidenceReading(SHORT, 10)).toBe("均值 −6.2 bps（上界 −1.1）");
  });
});

describe("F-4 / F-5 sideSegments", () => {
  it("两段按多、空顺序，影子与合并样本标记取自后端字段", () => {
    const segs = sideSegments(status());
    expect(segs.map((s) => s.side)).toEqual(["long", "short"]);
    expect(segs[0]).toMatchObject({ sideLabel: "多", stateLabel: "未证实", reading: "7/10" });
    expect(segs[0].isShadow).toBe(true);
    expect(segs[1].isPooled).toBe(true);
    expect(sideSegments(status({ sides: { long: { ...LONG, gating: "live" } } }))[0].isShadow)
      .toBe(false);
  });

  it("缺席的方向不出段；来源为空 ⇒ 空数组", () => {
    expect(sideSegments(status({ sides: { short: SHORT } })).map((s) => s.side)).toEqual(["short"]);
    expect(sideSegments(status({ sides: {} }))).toEqual([]);
    expect(sideSegments(null)).toEqual([]);
    expect(sideSegments(undefined)).toEqual([]);
  });

  it("查询结果经 bucketsForInst 进同一个渲染函数", () => {
    const data = response({
      buckets: [bucket({ side: "short", state: "disproven", n: 24, meanBps: -4.8, ucbBps: -2.7 }),
        bucket({ instId: "BTC-USDT-SWAP" })],
    });
    const segs = sideSegments(bucketsForInst(data, "ETH-USDT-SWAP"));
    expect(segs.map((s) => s.side)).toEqual(["short"]);
    expect(segs[0].reading).toBe("均值 −4.8 bps（上界 −2.7）");
    expect(Object.keys(bucketsForInst(data, "SOL-USDT-SWAP").sides)).toEqual([]);
  });
});

describe("F-6 / F-7 daysToMinSamples", () => {
  const ids = ["ETH-USDT-SWAP"];

  it("按方向分别估计，取最慢的那个方向，向上取整", () => {
    const data = response({
      buckets: [bucket({ side: "long", n: 4 }), bucket({ side: "short", n: 8 })],
      recentShadowRatePerDay: { "ETH-USDT-SWAP": { long: 2, short: 0.5 } },
    });
    // 多：缺 6 笔 / 2 = 3 天；空：缺 2 笔 / 0.5 = 4 天 ⇒ 4
    expect(daysToMinSamples(data, ids)).toBe(4);
  });

  it("还缺样本的方向拿不到速度 ⇒ null（不猜）", () => {
    const data = response({ recentShadowRatePerDay: { "ETH-USDT-SWAP": { long: 2 } } });
    expect(daysToMinSamples(data, ids)).toBeNull();
    expect(daysToMinSamples(response(), ids)).toBeNull();
    expect(daysToMinSamples(null, ids)).toBeNull();
    expect(daysToMinSamples(data, [])).toBeNull();
  });

  it("已攒满的方向不需要速度；全部攒满 ⇒ null（没有可估的等待）", () => {
    const partial = response({
      buckets: [bucket({ side: "short", n: 12 })],
      recentShadowRatePerDay: { "ETH-USDT-SWAP": { long: 5 } },
    });
    expect(daysToMinSamples(partial, ids)).toBe(2);
    const full = response({
      buckets: [bucket({ side: "long", n: 10 }), bucket({ side: "short", n: 11 })],
    });
    expect(daysToMinSamples(full, ids)).toBeNull();
  });
});

describe("F-8 口径常量逐字", () => {
  it("统计卡两行页脚", () => {
    expect(EVIDENCE_FOOTNOTES).toEqual([
      "样本按固定参考离场规则计算（收益锁 → 2×ATR 止损 / 4×ATR 止盈 → 24 根时间离场），" +
        "与真实离场无关；真实盈亏见上方已实现卡片。",
      "「最小 edge 门禁」衡量的是波动能否覆盖成本；「实证准入」衡量的是这个方向过去是否真的赚钱。",
    ]);
  });

  it("三档说明逐字，严格是唯一推荐档，文案不含 Markdown 强调符", () => {
    expect(EVIDENCE_POLICY_OPTIONS.map((o) => [o.value, o.label, o.hint])).toEqual([
      ["strict", "严格 · 先证后投",
        "某个方向在该标的上至少有 10 笔参考样本、且均值下界为正，才会下真实委托；在此之前只记影子。"],
      ["balanced", "均衡 · 证伪即停", "证据不足时照常下单，只有被证明亏钱的方向会停下。"],
      ["observe", "仅观察", "从不拦截，只记录证据。适合你想先看看数据的时候。"],
    ]);
    expect(EVIDENCE_POLICY_OPTIONS.filter((o) => o.recommended).map((o) => o.value))
      .toEqual(["strict"]);
    for (const text of [...EVIDENCE_FOOTNOTES, ...EVIDENCE_POLICY_OPTIONS.map((o) => o.hint)]) {
      expect(text).not.toContain("**");
    }
  });
});

describe("F-9 统计卡读数", () => {
  it("本会话列按方向；胜率 n<2 显示 —", () => {
    expect(evidenceSessionText(LONG)).toBe("真单 0 · 影子 9 · 进行中 1");
    expect(evidenceSessionText(SHORT)).toBe("真单 3 · 影子 5 · 进行中 0");
    expect(fmtEvidenceWinRate(LONG)).toBe("57%");
    expect(fmtEvidenceWinRate({ ...LONG, n: 1 })).toBe("—");
  });

  it("区间条零线恒居中，端点按共用半宽缩放", () => {
    const scale = evidenceScale([LONG, SHORT]);
    expect(scale).toBe(9);
    const geo = evidenceInterval(SHORT, scale);
    expect(geo?.zeroPct).toBe(50);
    expect(geo?.loPct).toBe(0);
    expect(geo?.hiPct).toBeLessThan(50);
    expect(evidenceInterval({ ...LONG, lcbBps: null }, scale)).toBeNull();
    expect(evidenceInterval(LONG, 0)).toBeNull();
  });
});

describe("F-10 预览与回填回执", () => {
  const out: AiTradeEvidenceBackfillOut = {
    inserted: 42,
    skipped: { candlesMissing: 3, atrUnavailable: 0, duplicate: 0, liveCovered: 0 },
    truncated: false, remaining: 0, notes: [],
  };

  it("完成：列出非零跳过原因；无跳过只报条数", () => {
    expect(describeBackfill(out)).toBe("回填 42 笔（跳过 3 笔：K 线缺失 3）");
    expect(describeBackfill({ ...out, skipped: { ...out.skipped, candlesMissing: 0 } }))
      .toBe("回填 42 笔");
  });

  it("截断 ⇒ 提示再点一次；liveCovered > 0 ⇒ 补一句不重复计入", () => {
    expect(describeBackfill({ ...out, truncated: true, remaining: 30 }))
      .toBe("已回填 42 笔，还有 30 笔没处理完，再点一次继续");
    expect(describeLiveCovered(out)).toBeNull();
    expect(describeLiveCovered({ ...out, skipped: { ...out.skipped, liveCovered: 5 } }))
      .toBe("另有 5 笔已由实时影子度量，不重复计入");
  });

  it("proven 桶判定与回填样本合计", () => {
    const data = response({
      buckets: [bucket({ backfillN: 20 }), bucket({ side: "short", state: "proven", backfillN: 22 })],
    });
    expect(hasProvenBucket(data)).toBe(true);
    expect(hasProvenBucket(response())).toBe(false);
    expect(hasProvenBucket(null)).toBe(false);
    expect(backfillSampleCount(data)).toBe(42);
  });
});
