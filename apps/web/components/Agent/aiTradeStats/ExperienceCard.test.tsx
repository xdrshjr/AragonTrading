import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import ExperienceCard from "./ExperienceCard";
import type { AiTradeExperienceStatus } from "@/lib/experienceTypes";

// 运行中会话的经验卡（experience-context-graph §7.3 + experience-compounding W-A / W-F）。
// 它承载三个**诚实性键**（不变式 I-8：后端算了却没有渲染落点的观测量等于死码）：
// `harvest.adopted`（接管补采了几笔）、`harvest.gap` + `reasons`（还漏了几笔、为什么漏）、
// 以及 `graphAsOfTs`（这个会话用的是哪一刻的快照）。

const STATUS: AiTradeExperienceStatus = {
  enabled: true, scope: "symbol", write: true, tier: "full",
  l0: 3, l1: 2, injections: 41, chars: 312, eventsHarvested: 7,
  harvestGap: 0, graphAsOfTs: 1_700_000_000,
};

afterEach(cleanup);

describe("ExperienceCard", () => {
  it("未装配 ⇒ 整卡不渲染（绝不在没有数据的会话上挂一排「—」）", () => {
    expect(render(<ExperienceCard experience={null} />).container).toBeEmptyDOMElement();
    cleanup();
    expect(render(<ExperienceCard experience={undefined} />).container).toBeEmptyDOMElement();
    cleanup();
    expect(render(<ExperienceCard experience={{ ...STATUS, enabled: false }} />).container)
      .toBeEmptyDOMElement();
  });

  it("装配后说清楚本轮喂了什么、沉淀了多少、用的是哪一刻的快照", () => {
    render(<ExperienceCard experience={STATUS} />);
    const card = screen.getByTestId("ai-experience-card");
    expect(card).toHaveTextContent("本轮喂入 5 条");
    expect(card).toHaveTextContent("312 字");
    expect(card).toHaveTextContent("本次会话已沉淀 7 条经验事件");
    expect(card).toHaveTextContent("经验库快照");
  });

  it("只读模式在标题上就说出来（用户必须知道这次交易的收据没在攒）", () => {
    render(<ExperienceCard experience={{ ...STATUS, write: false }} />);
    expect(screen.getByTestId("ai-experience-card")).toHaveTextContent("只读（不沉淀）");
  });

  it("harvestGap 为 0 ⇒ 不渲染那一行（不制造一句无信息量的噪音）", () => {
    render(<ExperienceCard experience={STATUS} />);
    expect(screen.queryByTestId("ai-experience-card-gap")).toBeNull();
  });

  it("harvestGap > 0 ⇒ 说清楚漏了几笔、为什么漏、以及它意味着什么（I-8）", () => {
    render(<ExperienceCard experience={{ ...STATUS, harvestGap: 2 }} />);
    const note = screen.getByTestId("ai-experience-card-gap");
    expect(note).toHaveTextContent("2 笔往返");
    expect(note).toHaveTextContent("续跑接管");
    expect(note).toHaveTextContent("偏乐观");
  });

  it("新键 `harvest` 缺席时回落到旧标量（桌面端旧包 + 新后端并存时这张卡不许空掉）", () => {
    render(<ExperienceCard experience={{ ...STATUS, harvestGap: 2 }} />);
    expect(screen.getByTestId("ai-experience-card-gap")).toHaveTextContent("2 笔往返");
    expect(screen.queryByTestId("ai-experience-card-adopted")).toBeNull();
  });

  it("新键在场 ⇒ 补采条数与逐原因明细各有一个渲染落点（W-A / I-8）", () => {
    render(<ExperienceCard experience={{
      ...STATUS, harvestGap: 3,
      harvest: { gap: 3, adopted: 2,
                 reasons: { noOpenContext: 0, noTrip: 1, slotEvicted: 0, persistFailed: 0,
                            offlineNoReadback: 2, offlineInconsistent: 0 } },
    }} />);
    const adopted = screen.getByTestId("ai-experience-card-adopted");
    expect(adopted).toHaveTextContent("2 笔");
    expect(adopted).toHaveTextContent("降级语境");
    const gap = screen.getByTestId("ai-experience-card-gap");
    expect(gap).toHaveTextContent("3 笔往返");
    // 逐原因明细只列**真的发生过**的那几种；零值的原因不进这句话（那会变成一串噪音）。
    expect(gap).toHaveTextContent("离线平仓回读不到 2");
    expect(gap).toHaveTextContent("结算不出往返 1");
    expect(gap).not.toHaveTextContent("语境槽被淘汰");
    // J-5 / G-13：这张卡上的措辞一律不得暗示因果。
    expect(adopted.textContent ?? "").not.toMatch(/贡献|带来|因此|导致/);
  });

  it("补采为 0 ⇒ 那一行整段不渲染（绝不显示一句「补采 0 笔」）", () => {
    render(<ExperienceCard experience={{
      ...STATUS,
      harvest: { gap: 0, adopted: 0,
                 reasons: { noOpenContext: 0, noTrip: 0, slotEvicted: 0, persistFailed: 0,
                            offlineNoReadback: 0, offlineInconsistent: 0 } },
    }} />);
    expect(screen.queryByTestId("ai-experience-card-adopted")).toBeNull();
    expect(screen.queryByTestId("ai-experience-card-gap")).toBeNull();
  });

  it("档位被翻译成人话（`repeat` 不是给用户看的枚举）", () => {
    render(<ExperienceCard experience={{ ...STATUS, tier: "repeat" }} />);
    expect(screen.getByTestId("ai-experience-card")).toHaveTextContent("语境未变");
  });
});
