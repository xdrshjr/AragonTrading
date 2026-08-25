import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import ExperienceCard from "./ExperienceCard";
import type { AiTradeExperienceStatus } from "@/lib/experienceTypes";

// 运行中会话的经验卡（ai-trade-experience-context-graph §7.3）。
// 它承载两个**诚实性键**（不变式 I-8：后端算了却没有渲染落点的观测量等于死码）：
// `harvestGap`（这张表偏乐观多少）与 `graphAsOfTs`（这个会话用的是哪一刻的快照）。

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

  it("档位被翻译成人话（`repeat` 不是给用户看的枚举）", () => {
    render(<ExperienceCard experience={{ ...STATUS, tier: "repeat" }} />);
    expect(screen.getByTestId("ai-experience-card")).toHaveTextContent("语境未变");
  });
});
