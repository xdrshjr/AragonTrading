import { describe, expect, it } from "vitest";
import {
  HEALTH_CODES,
  HEALTH_CODE_CN,
  HEALTH_STATUS_CN,
  HEALTH_STATUS_HINT,
  HEALTH_STATUS_ORDER,
  HEALTH_STATUS_TONE,
  formatRetention,
  healthCodeCn,
  healthReasonsCn,
  healthStatusCn,
  isGuardStalled,
  relativeTime,
  retentionBarRatio,
  toTrendPoints,
} from "./discoveryHealth";
import type { DiscoveryAssetCheckRow } from "./discoveryAssetTypes";

const check = (over: Partial<DiscoveryAssetCheckRow>): DiscoveryAssetCheckRow => ({
  id: 1,
  checkedTs: 1_700_000_000,
  status: "healthy",
  reasons: [],
  retention: 0.9,
  clipMode: "",
  error: "",
  windowStartTs: 0,
  windowEndTs: 0,
  freshStartTs: 0,
  freshBars: 200,
  warmupBars: 400,
  metrics: {},
  ...over,
});

// ============================ 中文映射门禁（后半） ============================
describe("中文映射的单一真源", () => {
  it("H 码中文表恰好覆盖 H1–H6", () => {
    // 后端 `health.HEALTH_CODES` 与 `grade()` 返回码集合相等（那边一条测试守）；
    // 这边守「前端这张表不漏项」。漏一项就是用户在界面上看到裸英文码。
    expect(Object.keys(HEALTH_CODE_CN).sort()).toEqual([...HEALTH_CODES].sort());
  });

  it("六种健康状态的中文、提示与色板都齐全", () => {
    const expected = ["fresh", "healthy", "watch", "decayed", "unknown", "error"];
    expect(HEALTH_STATUS_ORDER).toEqual(expected);
    for (const table of [HEALTH_STATUS_CN, HEALTH_STATUS_HINT, HEALTH_STATUS_TONE]) {
      expect(Object.keys(table).sort()).toEqual([...expected].sort());
    }
  });

  it("unknown 用中性文案与中性色，绝不当成坏消息（D-26）", () => {
    expect(HEALTH_STATUS_CN.unknown).toBe("暂不评级");
    expect(HEALTH_STATUS_TONE.unknown).toBe("neutral");
  });

  it("未知状态回落原值而不是崩掉", () => {
    expect(healthStatusCn("brand_new")).toBe("brand_new");
  });

  it("S0:* 装配失败码原样透出，可运维优先于漂亮", () => {
    expect(healthCodeCn("S0:missing_htf:4H")).toContain("missing_htf:4H");
    expect(healthReasonsCn([])).toBe("无");
    expect(healthReasonsCn(["H4", "H5"])).toBe("方向已反号、预测强度衰减");
  });
});

// ============================ 留存度 ============================
describe("留存度格式化", () => {
  it("null 显示 — 而不是 0（分母不可用 ≠ 跌到 0）", () => {
    expect(formatRetention(null)).toBe("—");
    expect(formatRetention(undefined)).toBe("—");
    expect(formatRetention(Number.NaN)).toBe("—");
    expect(retentionBarRatio(null)).toBe(0);
  });

  it(">1 显示原值，但条形长度夹到 1", () => {
    expect(formatRetention(1.42)).toBe("142%");
    expect(retentionBarRatio(1.42)).toBe(1);
    expect(retentionBarRatio(0.61)).toBeCloseTo(0.61);
  });
});

// ============================ 体检序列 ============================
describe("体检序列 → 折线点", () => {
  it("按时间升序，且把不可判定的点标出来", () => {
    const points = toTrendPoints([
      check({ id: 2, checkedTs: 200, status: "unknown", retention: null }),
      check({ id: 1, checkedTs: 100, retention: 0.8 }),
      check({ id: 3, checkedTs: 300, status: "error", retention: null }),
    ]);
    expect(points.map((p) => p.ts)).toEqual([100, 200, 300]);
    expect(points.map((p) => p.undecided)).toEqual([false, true, true]);
  });

  it("有状态但 retention 为 null 的点同样算不可判定（不能连线）", () => {
    const [point] = toTrendPoints([check({ status: "healthy", retention: null })]);
    expect(point.undecided).toBe(true);
  });
});

// ============================ 相对时间 / 守护存活 ============================
describe("相对时间与守护存活", () => {
  it("从未体检、刚刚、分钟、小时、天", () => {
    const now = 1_700_000_000;
    expect(relativeTime(0, now)).toBe("从未");
    expect(relativeTime(now - 10, now)).toBe("刚刚");
    expect(relativeTime(now - 600, now)).toBe("10 分钟前");
    expect(relativeTime(now - 7200, now)).toBe("2 小时前");
    expect(relativeTime(now - 3 * 86_400, now)).toBe("3 天前");
    expect(relativeTime(now + 60, now)).toBe("即将");
  });

  it("lastTickTs 超过 3×tick 即判定守护疑似停摆（R-9）", () => {
    const now = 1_700_000_000;
    expect(isGuardStalled(now - 100, 900, now)).toBe(false);
    expect(isGuardStalled(now - 3600, 900, now)).toBe(true);
    expect(isGuardStalled(0, 900, now)).toBe(false);
  });
});
