import { describe, expect, it } from "vitest";
import { LONG_MS, timeoutFor } from "./httpTimeout";
import { toQuery } from "./journalApi";
import { buildExportUrl, buildSliceUrls, downloadSlices } from "./journalExport";

/**
 * 交易日志的 REST 客户端与导出 URL 组装（ai-trade-audit-journal §3.3 / §3.4）。
 */
describe("querystring 组装", () => {
  it("null / undefined / 空串 / false 一律不进串", () => {
    const qs = toQuery({
      groupId: "grp-1",
      instId: undefined,
      bar: "",
      sinceTs: null,
      executedOnly: false,
      limit: 50,
    });
    expect(qs).toBe("?groupId=grp-1&limit=50");
  });

  it("全空即空串（不留一个孤零零的问号）", () => {
    expect(toQuery({ a: undefined, b: null })).toBe("");
  });
});

describe("超时档", () => {
  it("导出预检落长跑档：它要在全量候选上走一遍完整的样本装配", () => {
    expect(timeoutFor("/ai-trade/journal/export/preview")).toBe(LONG_MS);
  });

  it("列表 / 详情 / 统计走默认档（都是毫秒级）", () => {
    expect(timeoutFor("/ai-trade/journal/decisions")).not.toBe(LONG_MS);
    expect(timeoutFor("/ai-trade/journal/stats")).not.toBe(LONG_MS);
  });
});

describe("导出 URL", () => {
  it("审计两档不带任何后训练参数（无作用的参数是「看起来生效、其实没读」的温床）", () => {
    const url = buildExportUrl({
      format: "audit-csv",
      scope: { groupId: "grp-1" },
      sft: { excludeGateVetoed: false },
    });
    expect(url).toContain("format=audit-csv");
    expect(url).toContain("groupId=grp-1");
    expect(url).not.toContain("excludeGateVetoed");
  });

  it("后训练档把八条纪律逐条拼进去，含显式的 false", () => {
    const url = buildExportUrl({
      format: "sft-jsonl",
      scope: { groupId: "grp-1" },
      sft: {
        excludeGateVetoed: false,
        excludeDegraded: true,
        onlySettled: true,
        includeHold: false,
        minLinkConfidence: "auto",
        contextMode: "full",
      },
    });
    // `false` 是用户的**显式放开**，绝不能被 `toQuery` 的空值过滤吞掉。
    expect(url).toContain("excludeGateVetoed=false");
    expect(url).toContain("includeHold=false");
    expect(url).toContain("minLinkConfidence=auto");
    expect(url).toContain("contextMode=full");
  });

  it("分片：第 1 片不带游标，后续片带上前一片的 nextBeforeId 与 part 序号", () => {
    const urls = buildSliceUrls(
      { format: "audit-jsonl", scope: { groupId: "grp-1" } },
      3,
      [91733, 84120],
    );
    expect(urls).toHaveLength(3);
    expect(urls[0]).not.toContain("beforeId");
    expect(urls[0]).not.toContain("part=");
    expect(urls[1]).toContain("beforeId=91733");
    expect(urls[1]).toContain("part=2");
    expect(urls[2]).toContain("beforeId=84120");
    expect(urls[2]).toContain("part=3");
  });
});

describe("分片下载", () => {
  it("必须顺序触发，绝不并发", async () => {
    // 并发会让 SQLite 上的几个长扫描互相抢读锁，而这条链上还挂着一个正在下单的会话。
    const order: string[] = [];
    const fakeAnchor = () => ({
      href: "",
      style: {} as CSSStyleDeclaration,
      setAttribute: () => undefined,
      click() {
        order.push(`click:${(this as { href: string }).href}`);
      },
    });
    const doc = {
      createElement: () => fakeAnchor(),
      body: { appendChild: () => undefined, removeChild: () => undefined },
    } as unknown as Document;

    const sleep = async () => {
      order.push("gap");
    };
    const count = await downloadSlices(["/a", "/b", "/c"], { doc, sleep });
    expect(count).toBe(3);
    expect(order).toEqual(["click:/a", "gap", "click:/b", "gap", "click:/c"]);
  });
});
