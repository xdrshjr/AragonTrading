import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAppStore } from "@/lib/store";
import type { IntelItem, IntelReadResponse } from "@/lib/intelTypes";
import IntelModal from "./IntelModal";

const NOW_S = Math.round(Date.now() / 1000);
const INST = "BTC-USDT-SWAP";

let readResponse: IntelReadResponse;
// 归档 / 静音各自的回执：`GET /intel/{instId}` 的 `status` 是单值过滤器，取 `active` 的那一次
// 回执里恒不含任何非 active 行——用例必须跟着这条真实语义走，否则它会放过「归档页签恒空」。
let inactiveResponses: Record<string, IntelReadResponse> = {};
const readMock = vi.fn(
  async (_instId: string, params?: { status?: string }) =>
    (params?.status && params.status !== "active" && inactiveResponses[params.status]) ||
    readResponse,
);
const collectMock = vi.fn(async () => ({ queued: true }));
const curateMock = vi.fn(async () => ({ queued: true }));
const seenMock = vi.fn(async () => ({ summary: readResponse.summary }));
const patchMock = vi.fn(async () => ({ item: readResponse.tiers.ultra.items[0] }));
const deleteMock = vi.fn(async () => undefined);

vi.mock("@/lib/intelApi", () => ({
  intelApi: {
    read: (...a: unknown[]) => readMock(...(a as [string, { status?: string }?])),
    rounds: async () => ({ rounds: [] }),
    collect: (...a: unknown[]) => collectMock(...(a as [])),
    curate: (...a: unknown[]) => curateMock(...(a as [])),
    markSeen: (...a: unknown[]) => seenMock(...(a as [])),
    patchItem: (...a: unknown[]) => patchMock(...(a as [])),
    moveItem: vi.fn(async () => ({ item: {} })),
    deleteItem: (...a: unknown[]) => deleteMock(...(a as [])),
    createItem: vi.fn(async () => ({ item: {} })),
  },
}));

function item(over: Partial<IntelItem> = {}): IntelItem {
  return {
    id: 1, instId: INST, tier: "ultra", category: "risk",
    headline: "某跨链桥今晨被盗", summary: "同板块情绪承压。", stance: "bearish",
    impact: "high", confidence: 0.65, eventTs: NOW_S - 3600, capturedTs: NOW_S - 3600,
    updatedTs: NOW_S, expiresTs: NOW_S + 86400, status: "active", pinned: false,
    userEdited: false, undated: false, isScheduled: false, origin: "agent", sources: [],
    sourceCount: 0, domainCount: 2, dedupKey: "k", supersedesId: null, promoteCount: 0,
    demoteCount: 0, lastCuratedTs: 0, curationNote: "", createdTs: NOW_S, ...over,
  };
}

function response(over: Partial<IntelReadResponse> = {}): IntelReadResponse {
  return {
    instId: INST,
    summary: {
      instId: INST, counts: { ultra: 2, mid: 1, long: 0, archived: 3 }, unread: 0,
      lastSeenTs: NOW_S, lastCollectTs: NOW_S - 600, lastCurateTs: null,
      nextDueTs: NOW_S + 720, inFlight: false, degrade: null, providerConfigured: true,
      dailyUsed: 18, dailyCap: 96,
    },
    tiers: {
      ultra: { items: [item(), item({ id: 2, category: "macro", headline: "CPI 数据即将公布" })], total: 2 },
      mid: { items: [item({ id: 3, tier: "mid", headline: "主网升级定档" })], total: 1 },
      long: { items: [], total: 0 },
    },
    archivedTotal: 3,
    mutedTotal: 0,
    ...over,
  };
}

function emptyTiers(): IntelReadResponse["tiers"] {
  return {
    ultra: { items: [], total: 0 },
    mid: { items: [], total: 0 },
    long: { items: [], total: 0 },
  };
}

beforeEach(() => {
  readResponse = response();
  inactiveResponses = {};
  readMock.mockClear();
  patchMock.mockClear();
  collectMock.mockClear();
  curateMock.mockClear();
  seenMock.mockClear();
  useAppStore.setState({ aiTradeIntel: {} });
});
afterEach(cleanup);

async function open(res?: IntelReadResponse) {
  if (res) readResponse = res;
  render(<IntelModal instId={INST} open onClose={() => {}} />);
  await waitFor(() => expect(readMock).toHaveBeenCalled());
}

describe("IntelModal", () => {
  it("五个页签带计数（超短 / 中期 / 长期 / 已归档 / 整理记录）", async () => {
    await open();
    await waitFor(() => expect(screen.getByRole("tab", { name: "超短 2" })).toBeInTheDocument());
    expect(screen.getByRole("tab", { name: "中期 1" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "长期 0" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "已归档 3" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "整理记录" })).toBeInTheDocument();
  });

  it("打开即标记已读（真值落服务端，而不是前端 localStorage）", async () => {
    await open();
    await waitFor(() => expect(seenMock).toHaveBeenCalledWith(INST));
  });

  it("类别过滤只留下该类", async () => {
    await open();
    await waitFor(() => expect(screen.getByText("某跨链桥今晨被盗")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "宏观" }));
    expect(screen.getByText("CPI 数据即将公布")).toBeInTheDocument();
    expect(screen.queryByText("某跨链桥今晨被盗")).toBeNull();
  });

  it("搜索按标题与摘要过滤", async () => {
    await open();
    await waitFor(() => expect(screen.getByText("某跨链桥今晨被盗")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("搜索情报"), { target: { value: "CPI" } });
    expect(screen.queryByText("某跨链桥今晨被盗")).toBeNull();
    expect(screen.getByText("CPI 数据即将公布")).toBeInTheDocument();
  });

  it("`/` 聚焦搜索框（键盘用户不必用鼠标穿过整条过滤条）", async () => {
    await open();
    const search = screen.getByLabelText("搜索情报");
    fireEvent.keyDown(screen.getByTestId("intel-modal"), { key: "/" });
    expect(document.activeElement).toBe(search);
  });

  it("未配置检索源 ⇒ 说清楚并给设置入口，**绝不**只说「暂无情报」", async () => {
    await open(response({
      summary: { ...response().summary, providerConfigured: false, lastCollectTs: null },
      tiers: { ultra: { items: [], total: 0 }, mid: { items: [], total: 0 }, long: { items: [], total: 0 } },
    }));
    await waitFor(() =>
      expect(screen.getByTestId("intel-empty-no-provider")).toBeInTheDocument());
    expect(screen.queryByText("暂无情报")).toBeNull();
    expect(screen.getByRole("button", { name: "去设置检索源" })).toBeInTheDocument();
  });

  it("已配置但从未采集 ⇒ 给倒计时 + 立即采集", async () => {
    await open(response({
      summary: { ...response().summary, lastCollectTs: null },
      tiers: { ultra: { items: [], total: 0 }, mid: { items: [], total: 0 }, long: { items: [], total: 0 } },
    }));
    await waitFor(() =>
      expect(screen.getByTestId("intel-empty-not-collected")).toBeInTheDocument());
  });

  it("本层为空但其它层有 ⇒ 提示切层", async () => {
    await open();
    await waitFor(() => expect(screen.getByRole("tab", { name: "长期 0" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("tab", { name: "长期 0" }));
    expect(screen.getByTestId("intel-empty-other-tier")).toBeInTheDocument();
  });

  it("搜索无结果 ⇒ 给清除筛选", async () => {
    await open();
    await waitFor(() => expect(screen.getByText("某跨链桥今晨被盗")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("搜索情报"), { target: { value: "不存在的词" } });
    expect(screen.getByTestId("intel-empty-no-match")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "清除筛选" }));
    expect(screen.getByText("某跨链桥今晨被盗")).toBeInTheDocument();
  });

  it("降级 banner 显示后端下发的文案与倒计时", async () => {
    await open(response({
      summary: {
        ...response().summary,
        degrade: { code: "search_failed", message: "检索请求失败（连接超时）", retryInS: 112 },
      },
    }));
    await waitFor(() =>
      expect(screen.getByTestId("intel-degrade-banner")).toHaveTextContent(
        "检索请求失败（连接超时），112s 后自动重试"));
  });

  it("footer 给出下一次采集与今日额度，并能手动触发采集 / 整理", async () => {
    await open();
    await waitFor(() => expect(screen.getByText(/今日已用 18\/96/)).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "立即采集" }));
    await waitFor(() => expect(collectMock).toHaveBeenCalledWith(INST));
    fireEvent.click(screen.getByRole("button", { name: "立即整理" }));
    await waitFor(() => expect(curateMock).toHaveBeenCalledWith(INST));
  });

  it("删除走二次确认，取消即什么都不做", async () => {
    await open();
    await waitFor(() => expect(screen.getByText("某跨链桥今晨被盗")).toBeInTheDocument());
    fireEvent.click(screen.getAllByRole("button", { name: /^删除：/ })[0]);
    expect(screen.getByText("删除这条情报？")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(deleteMock).not.toHaveBeenCalled();
  });

  it("刷新用顶部 2px 进度条，**不清空列表**（否则每隔几秒丢一次阅读位置）", async () => {
    await open();
    await waitFor(() => expect(screen.getByText("某跨链桥今晨被盗")).toBeInTheDocument());
    expect(screen.getByTestId("intel-progress").className).toContain("opacity-0");
    act(() => {
      useAppStore.getState().setAiTradeIntelSummary(INST, {
        ...response().summary,
        inFlight: true,
      });
    });
    expect(screen.getByTestId("intel-progress").className).toContain("opacity-100");
    expect(screen.getByText("某跨链桥今晨被盗")).toBeInTheDocument();
    expect(screen.queryByTestId("intel-list-skeleton")).toBeNull();
  });

  it("已归档页签列出归档与静音两类条目（而不是一边报着 3 条一边空白）", async () => {
    await open();
    inactiveResponses = {
      archived: response({
        tiers: { ...emptyTiers(), ultra: { items: [item({ id: 8, status: "archived", headline: "已过期的旧闻" })], total: 1 } },
      }),
      muted: response({
        tiers: { ...emptyTiers(), mid: { items: [item({ id: 9, tier: "mid", status: "muted", headline: "被静音的传闻" })], total: 1 } },
      }),
    };
    fireEvent.click(screen.getByRole("tab", { name: "已归档 3" }));
    await waitFor(() => expect(screen.getByText("已过期的旧闻")).toBeInTheDocument());
    expect(screen.getByText("被静音的传闻")).toBeInTheDocument();
  });

  it("归档条目只给「恢复 / 删除」，恢复即写回 active（静音不能是单行道）", async () => {
    await open();
    inactiveResponses = {
      archived: response({
        tiers: { ...emptyTiers(), ultra: { items: [item({ id: 9, status: "muted", headline: "被静音的传闻" })], total: 1 } },
      }),
      muted: response({ tiers: emptyTiers() }),
    };
    fireEvent.click(screen.getByRole("tab", { name: "已归档 3" }));
    await waitFor(() => expect(screen.getByText("被静音的传闻")).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: /^置顶：/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /^静音：/ })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /^恢复：/ }));
    await waitFor(() => expect(patchMock).toHaveBeenCalledWith(9, { status: "active" }));
  });

  it("归档抽屉为空时说的是归档自己的话，而不是「还没采集过」", async () => {
    await open();
    inactiveResponses = { archived: response({ tiers: emptyTiers() }), muted: response({ tiers: emptyTiers() }) };
    fireEvent.click(screen.getByRole("tab", { name: "已归档 3" }));
    await waitFor(() =>
      expect(screen.getByTestId("intel-empty-none-archived")).toBeInTheDocument());
  });

  it("WS 推来的 summary 实时更新头部（弹窗打开期间不必等下一次轮询）", async () => {
    await open();
    await waitFor(() => expect(screen.getByRole("tab", { name: "超短 2" })).toBeInTheDocument());
    expect(screen.queryByTestId("intel-in-flight")).toBeNull();
    act(() => {
      useAppStore.getState().setAiTradeIntelSummary(INST, {
        ...response().summary,
        inFlight: true,
      });
    });
    expect(screen.getByTestId("intel-in-flight")).toBeInTheDocument();
  });
});
