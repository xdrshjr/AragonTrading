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
const settingsMock = vi.fn(async () => ({
  enabled: true, injectEnabled: true, provider: "tavily", hasKey: true, keyPreview: "…abcd",
  collectIntervalMin: 30, curateEveryNCollects: 4, dailyCap: 96,
  estimate: { roundsPerDay: 240, inputTokensPerDay: 1_000_000 },
  citeEnabled: true, attributionEnabled: true, verifyEnabled: true, verifyEveryNCurates: 1,
  verifyMaxItems: 3, verifyDocsPerItem: 4, blockedSweepEnabled: true, citeMaxRefs: 8,
  readerEnabled: true, readerTopK: 6, quoteMode: "soft" as const, quoteHitRate: 0.78,
}));
const verifyMock = vi.fn(async () => ({ queued: true }));
const verifyItemMock = vi.fn(async () => ({ queued: true }));
// evidence-grounding：证据抽屉的懒加载点。返回值固定，用例只关心**它被调了几次**。
const evidenceMock = vi.fn(async (itemId: number) => ({
  itemId, quoteMode: "soft" as const, docs: [],
  quotes: [{
    id: 5, itemId, instId: INST, roundId: 1, role: "admit" as const, ref: "D1",
    text: "准入时看到的那一段原文", sourceField: "body" as const,
    docUrl: "https://reuters.com/a", docDomain: "reuters.com", docPublishedTs: 0,
    hasImperative: false, verifiedTs: 0,
  }],
}));

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
    // closed-loop：弹窗新增的六个取数点。**`read` mock 必须按 `status` 分发**（v1 终审的成文
    // 教训：不分发的 mock 会让用例空跑通过），新加的这些同理——一个返回 `undefined` 的
    // `settings()` 会让整个弹窗在 effect 里抛，而症状是十几条不相干的用例一起变红。
    settings: (...a: unknown[]) => settingsMock(...(a as [])),
    verify: (...a: unknown[]) => verifyMock(...(a as [])),
    verifyItem: (...a: unknown[]) => verifyItemMock(...(a as [])),
    impact: async () => ({
      instId: INST,
      totals: { citeCount: 0, citeEntryCount: 0, settledCount: 0, winCount: 0, netPnlUsd: 0 },
      top: [],
      worst: [],
      neverCited: 0,
    }),
    sources: async () => ({ rows: [] }),
    feedback: async () => ({ rows: [] }),
    writeFeedback: async () => ({ ok: true, archived: 0 }),
    previewFeedback: async () => ({ affected: 0 }),
    clearFeedback: async () => undefined,
    evidence: (...a: unknown[]) => evidenceMock(...(a as [number])),
    documentHealth: async () => ({
      cachedDocs: 0, bodyBytes: 0, oldestFetchedTs: 0, lastPrunedTs: 0, readerEnabled: true,
    }),
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
    demoteCount: 0, lastCuratedTs: 0, curationNote: "", createdTs: NOW_S,
    citeCount: 0, citeEntryCount: 0, settledCount: 0, winCount: 0, netPnlUsd: 0,
    lastCitedTs: 0, verdict: "", verdictTs: 0, verdictNote: "", verifyCount: 0,
    quoted: false, quoteCount: 0, quoteState: "", scheduleResolvedTs: 0,
    ...over,
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
      citations: { cited: 0, entryCited: 0, settledTrips: 0, winCount: 0,
        netPnlUsd: 0, neverCited: 0 },
      verify: { confirmed: 0, refuted: 0, unresolved: 0, pending: 0, lastVerifyTs: null },
      quoteHitRate: null,
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
  verifyMock.mockClear();
  verifyItemMock.mockClear();
  settingsMock.mockClear();
  useAppStore.setState({ aiTradeIntel: {} });
});
afterEach(cleanup);

async function open(res?: IntelReadResponse) {
  if (res) readResponse = res;
  render(<IntelModal instId={INST} open onClose={() => {}} />);
  await waitFor(() => expect(readMock).toHaveBeenCalled());
}

describe("IntelModal", () => {
  it("七个页签都可达（超短 / 中期 / 长期 / 影响力 / 信源 / 已归档 / 整理记录）", async () => {
    await open();
    await waitFor(() => expect(screen.getByRole("tab", { name: "超短 2" })).toBeInTheDocument());
    expect(screen.getByRole("tab", { name: "中期 1" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "长期 0" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "影响力" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "信源" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "已归档 3" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "整理记录" })).toBeInTheDocument();
  });

  it("影响力与信源两个页签各自能被打开（closed-loop §3.7.3）", async () => {
    await open();
    fireEvent.click(await screen.findByRole("tab", { name: "影响力" }));
    expect(await screen.findByTestId("intel-impact-tab")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "信源" }));
    expect(await screen.findByTestId("intel-sources-tab")).toBeInTheDocument();
  });

  it("footer 的「立即核查」触发 POST /intel/{instId}/verify", async () => {
    await open();
    fireEvent.click(await screen.findByRole("button", { name: "立即核查" }));
    await waitFor(() => expect(verifyMock).toHaveBeenCalled());
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

  // ---------------- evidence-grounding 轴 C：懒加载与命中率徽标 ----------------
  it("证据只在展开时取一次；收起再展开**不重发**", async () => {
    readResponse = response({
      tiers: {
        ultra: { items: [item({ quoted: true, quoteCount: 1 })], total: 1 },
        mid: { items: [], total: 0 },
        long: { items: [], total: 0 },
      },
    });
    render(<IntelModal instId={INST} open onClose={vi.fn()} />);
    await waitFor(() => expect(screen.getByTestId("intel-list")).toBeInTheDocument());
    expect(evidenceMock).not.toHaveBeenCalled();

    fireEvent.click(screen.getByTestId("intel-evidence-trigger"));
    await waitFor(() => expect(evidenceMock).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(screen.getByText("准入时看到的那一段原文")).toBeInTheDocument());

    fireEvent.click(screen.getByTestId("intel-evidence-trigger"));   // 收起
    fireEvent.click(screen.getByTestId("intel-evidence-trigger"));   // 再展开
    await waitFor(() =>
      expect(screen.getByText("准入时看到的那一段原文")).toBeInTheDocument());
    expect(evidenceMock).toHaveBeenCalledTimes(1);
  });

  it("顶部命中率徽标是把引文要求收紧的判据在界面上的落点", async () => {
    readResponse = response({
      summary: { ...response().summary, quoteHitRate: 0.78 },
    });
    render(<IntelModal instId={INST} open onClose={vi.fn()} />);
    await waitFor(() =>
      expect(screen.getByTestId("intel-hit-rate")).toHaveTextContent("近 20 轮引文命中率 78%"));
  });

  it("还没提过引文（`null`）⇒ 徽标整块不渲染，绝不显示一个假的 0%", async () => {
    readResponse = response();
    render(<IntelModal instId={INST} open onClose={vi.fn()} />);
    await waitFor(() => expect(screen.getByTestId("intel-list")).toBeInTheDocument());
    expect(screen.queryByTestId("intel-hit-rate")).toBeNull();
  });

  // 终审补：错误必须**按 itemId 存**。弹窗级的一个字符串会让 A 的失败在 B 展开时被清掉，
  // 而 A 那个还开着的抽屉于是从「读取失败」变成「这一条没有留存任何原文引证」——把一次请求
  // 失败说成一句关于数据的断言，是四句话里最不该出现的那一句。
  it("一条取证失败，不会把另一条的抽屉染成失败，也不会自己变成「没有引证」", async () => {
    readResponse = response({
      tiers: {
        ultra: {
          items: [item({ id: 1, quoted: true, quoteCount: 1 }),
                  item({ id: 2, headline: "CPI 数据即将公布", quoted: true, quoteCount: 1 })],
          total: 2,
        },
        mid: { items: [], total: 0 },
        long: { items: [], total: 0 },
      },
    });
    evidenceMock.mockImplementation(async (itemId: number) => {
      if (itemId === 1) throw new Error("读取原文引证失败：网络错误");
      return { itemId, quoteMode: "soft" as const, docs: [], quotes: [{
        id: 9, itemId, instId: INST, roundId: 1, role: "admit" as const, ref: "D1",
        text: "第二条的那一段原文", sourceField: "body" as const,
        docUrl: "https://reuters.com/b", docDomain: "reuters.com", docPublishedTs: 0,
        hasImperative: false, verifiedTs: 0,
      }] };
    });
    render(<IntelModal instId={INST} open onClose={vi.fn()} />);
    await waitFor(() => expect(screen.getByTestId("intel-list")).toBeInTheDocument());
    const triggers = screen.getAllByTestId("intel-evidence-trigger");
    fireEvent.click(triggers[0]);
    await waitFor(() =>
      expect(screen.getByText("读取原文引证失败：网络错误")).toBeInTheDocument());
    fireEvent.click(screen.getAllByTestId("intel-evidence-trigger")[1]);
    await waitFor(() =>
      expect(screen.getByText("第二条的那一段原文")).toBeInTheDocument());
    // 第一条仍然在说「读取失败」，而不是改口说这一条没有引证。
    expect(screen.getByText("读取原文引证失败：网络错误")).toBeInTheDocument();
    expect(screen.queryByText("这一条没有留存任何原文引证。")).toBeNull();
  });
});
