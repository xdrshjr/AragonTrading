import { describe, expect, it } from "vitest";
import {
  describeFeedRecovery,
  describeFeedRecoveryRows,
  isFeedRecoveryStuck,
} from "./aiTradeFeed";
import type { AiTradeFeedRecovery, AiTradeGroupMemberState } from "./aiTradeTypes";

/**
 * 行情自愈恢复卡的**视图模型**（ai-trade-feed-self-healing §7.5）。
 *
 * 本文件守的是一条纪律：**这里不算任何一个数、不拼任何一句中文**。重连次数、倒计时、行情年龄
 * 与那句判定性文案全部来自后端 wire。一张前后端各存一份的表必然漂移——那是本仓库反复出事的形态。
 */
const WIRE: AiTradeFeedRecovery = {
  state: "recovering",
  stage: "relink",
  attempts: { backfill: 17, relink: 2 },
  lastAttemptTs: 1_756_270_000,
  nextAttemptInS: 38,
  lastOutcome: "no_data",
  lastOutcomeText: "REST 未取到更新的 K 线",
  source: "ws",
  recoveryText: "正在自动恢复行情连接 · 已重连 2 次 · 下一次尝试 38 秒后",
};

function member(instId: string, feedRecovery?: AiTradeFeedRecovery | null) {
  return {
    instId,
    sessionId: `s-${instId}`,
    allocationPct: 50,
    session: feedRecovery === undefined ? {} : { lifecycle: { feedRecovery } },
    trades: [],
    position: null,
    decisions: [],
    traces: [],
    summary: null,
  } as unknown as AiTradeGroupMemberState;
}

describe("describeFeedRecovery", () => {
  it("键缺席与值为 null 表现一致：都不渲染（两档在 wire 里仍可区分）", () => {
    expect(describeFeedRecovery(undefined, "BTC-USDT-SWAP", 1)).toBeNull();
    expect(describeFeedRecovery(null, "BTC-USDT-SWAP", 1)).toBeNull();
  });

  it("`healthy` 同样不渲染（防御性：后端在那一档下发的是 null）", () => {
    expect(
      describeFeedRecovery({ ...WIRE, state: "healthy" }, "BTC-USDT-SWAP", 1),
    ).toBeNull();
  });

  it("单标的不带前缀，多标的**每行必须点名标的**", () => {
    expect(describeFeedRecovery(WIRE, "ETH-USDT-SWAP", 1)?.label).toBe(WIRE.recoveryText);
    expect(describeFeedRecovery(WIRE, "ETH-USDT-SWAP", 5)?.label).toBe(
      `ETH-USDT-SWAP：${WIRE.recoveryText}`,
    );
  });

  it("所有数字来自 wire —— 前端既不重算次数，也不把次数再抄一份进视图模型", () => {
    const view = describeFeedRecovery(
      {
        ...WIRE,
        attempts: { backfill: 0, relink: 7 },
        recoveryText: "正在自动恢复行情连接 · 已重连 7 次 · 下一次尝试 38 秒后",
      },
      "BTC-USDT-SWAP",
      1,
    );
    expect(view?.label).toBe("正在自动恢复行情连接 · 已重连 7 次 · 下一次尝试 38 秒后");
    expect(view?.nextAttemptInS).toBe(38);
    // 视图模型只留组件真的会读的字段：多抄一个 `attempts` 就是一个只写不读的字段。
    expect(Object.keys(view ?? {}).sort()).toEqual(
      ["instId", "label", "nextAttemptInS", "stuck"],
    );
  });

  it("`nextAttemptInS === null` 原样透传 ⇒ 组件据此整行不渲染倒计时", () => {
    const view = describeFeedRecovery({ ...WIRE, nextAttemptInS: null }, "BTC-USDT-SWAP", 1);
    expect(view?.nextAttemptInS).toBeNull();
  });

  it("REST 生命维持那一句原样透传（它由后端拼，前端绝不据 `source` 自己补一句）", () => {
    const onRest = "正在自动恢复行情连接 · 已重连 2 次。当前 K 线由 REST 补取维持，实时通道尚未恢复";
    const view = describeFeedRecovery(
      { ...WIRE, source: "rest", recoveryText: onRest },
      "BTC-USDT-SWAP",
      1,
    );
    expect(view?.label).toBe(onRest);
    // `source` 变了但文案没变 ⇒ 前端一个字都不许自己加。
    expect(describeFeedRecovery({ ...WIRE, source: "rest" }, "BTC-USDT-SWAP", 1)?.label).toBe(
      WIRE.recoveryText,
    );
  });

  it("`stuck` 是一个独立的结论，不由前端从次数推导", () => {
    const view = describeFeedRecovery(
      { ...WIRE, state: "stuck", stage: "escalated" },
      "BTC-USDT-SWAP",
      1,
    );
    expect(view?.stuck).toBe(true);
    expect(describeFeedRecovery(WIRE, "BTC-USDT-SWAP", 1)?.stuck).toBe(false);
  });

  it("后端没给文案 ⇒ 不渲染（绝不自己编一句）", () => {
    expect(
      describeFeedRecovery({ ...WIRE, recoveryText: "" }, "BTC-USDT-SWAP", 1),
    ).toBeNull();
  });

  it("文案里绝不出现字面星号（后端串直接进状态行，`**` 在 JSX 里会显示成星号）", () => {
    const view = describeFeedRecovery(WIRE, "BTC-USDT-SWAP", 3);
    expect(view?.label.includes("**")).toBe(false);
  });
});

describe("describeFeedRecoveryRows", () => {
  it("只留下需要自愈的成员；全部正常 ⇒ 空数组 ⇒ 整卡不渲染、不占位", () => {
    expect(describeFeedRecoveryRows([member("BTC-USDT-SWAP", null)])).toEqual([]);
    expect(describeFeedRecoveryRows([member("BTC-USDT-SWAP")])).toEqual([]);
  });

  it("5 个标的死 1 个 ⇒ 只出一行，且**点名**是哪一个（绝不整组抹掉）", () => {
    const members = [
      member("BTC-USDT-SWAP", null),
      member("ETH-USDT-SWAP", WIRE),
      member("SOL-USDT-SWAP", null),
      member("XRP-USDT-SWAP", null),
      member("DOGE-USDT-SWAP", null),
    ];
    const rows = describeFeedRecoveryRows(members);
    expect(rows).toHaveLength(1);
    expect(rows[0].instId).toBe("ETH-USDT-SWAP");
    expect(rows[0].label.startsWith("ETH-USDT-SWAP：")).toBe(true);
  });

  it("单标的分组 ⇒ 不出现 `1/1` 这类分数，也不带标的前缀", () => {
    const rows = describeFeedRecoveryRows([member("BTC-USDT-SWAP", WIRE)]);
    expect(rows[0].label).toBe(WIRE.recoveryText);
    expect(rows[0].label).not.toContain("1/1");
  });

  it("任一行 `stuck` ⇒ 整卡按 danger 取色（最坏的那一行决定整卡的语气）", () => {
    const rows = describeFeedRecoveryRows([
      member("BTC-USDT-SWAP", WIRE),
      member("ETH-USDT-SWAP", { ...WIRE, state: "stuck", stage: "escalated" }),
    ]);
    expect(isFeedRecoveryStuck(rows)).toBe(true);
    expect(isFeedRecoveryStuck(describeFeedRecoveryRows([member("BTC-USDT-SWAP", WIRE)]))).toBe(
      false,
    );
  });
});
