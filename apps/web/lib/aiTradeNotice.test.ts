// 提示纪元纯核心的判活矩阵（ai-trade-notice-state-sync §8.1，U-1 ~ U-13）。
//
// 这一层是本轮全部结论的地基：只要谓词是对的，「继续之后暂停提示消失」就是一条**结构上成立**的
// 性质，而不依赖任何写点记得去清。U-10 / U-12 / U-13 三条各自守着一条在 v1 里**恒绿**的分支
// （评审的两条 P0 与 RP1-6 的共同形状：修法落在它以为的位置之外，门禁恰好只走那条已经好的路）。
import { beforeEach, describe, expect, it } from "vitest";
import { AI_TRADE_RECONCILE_FAST_MS } from "./aiTrade";
import {
  AI_TRADE_NOTICE_RULES,
  AI_TRADE_NOTICE_SETTLE_MS,
  currentAiTradeEpoch,
  isAiTradeNoticeBlockedBySticky,
  isAiTradeNoticeLive,
  mintAiTradeNotice,
} from "./aiTradeNotice";
import type {
  AiTradeLifecycle,
  AiTradeNotice,
  AiTradeNoticeEpoch,
  AiTradeNoticeKind,
} from "./aiTradeNotice";
import { useAppStore } from "./store";

const TS = 1_700_000_000_000;

function epoch(patch: Partial<AiTradeNoticeEpoch> = {}): AiTradeNoticeEpoch {
  return {
    groupId: "g1",
    lifecycle: "running" as AiTradeLifecycle,
    resumedCount: 0,
    reconciledAtMs: 0,
    ...patch,
  };
}

function mint(kind: AiTradeNoticeKind, at: AiTradeNoticeEpoch, ts = TS): AiTradeNotice {
  const notice = mintAiTradeNotice(kind, `${kind} 的文案`, at, ts);
  if (!notice) throw new Error("mint 返回了 null —— 用例前提不成立");
  return notice;
}

describe("isAiTradeNoticeLive · 判活矩阵（U-1 ~ U-9）", () => {
  it("U-1：paused 在同组同 resumedCount 的 paused 态下判活，lifecycle 变 running 后判死", () => {
    const notice = mint("paused", epoch({ lifecycle: "paused" }));
    expect(isAiTradeNoticeLive(notice, epoch({ lifecycle: "paused" }), TS)).toBe(true);
    // 用户报告的那条缺陷的**直接复现**：继续之后这一条必须判死。
    // `reconciledAtMs` 已越过 `notice.ts` ⇒「未落地宽限」失效，validIn 恢复为硬判据。
    expect(
      isAiTradeNoticeLive(notice, epoch({ lifecycle: "running", reconciledAtMs: TS + 1 }), TS),
    ).toBe(false);
  });

  it("U-2：paused 在 resumedCount 由 0→1 后判死，**即使 lifecycle 又回到 paused**", () => {
    const notice = mint("paused", epoch({ lifecycle: "paused" }));
    expect(
      isAiTradeNoticeLive(notice, epoch({ lifecycle: "paused", resumedCount: 1 }), TS),
    ).toBe(false);
  });

  it("U-3：ended 在 groupId 变 null 后仍判活（groupScoped:false）；lifecycle=running 下判死", () => {
    const notice = mint("ended", epoch({ lifecycle: "idle" }));
    // 结束那一刻 aiTradeGroup 被清成 null —— 按 groupScoped 判，这条真钱待办会在它最该出现的
    // 那一瞬间被自己判死。
    expect(isAiTradeNoticeLive(notice, epoch({ groupId: null, lifecycle: "idle" }), TS)).toBe(true);
    expect(
      isAiTradeNoticeLive(
        notice,
        epoch({ groupId: null, lifecycle: "running", reconciledAtMs: TS + 1 }),
        TS,
      ),
    ).toBe(false);
  });

  it("U-4：action_unknown 的 SETTLE 窗两侧各一条（reconciledAtMs 取的是请求**出发**时刻）", () => {
    const notice = mint("action_unknown", epoch());
    expect(isAiTradeNoticeLive(notice, epoch({ reconciledAtMs: TS + 1_400 }), TS)).toBe(true);
    expect(isAiTradeNoticeLive(notice, epoch({ reconciledAtMs: TS + 1_600 }), TS)).toBe(false);
  });

  it("U-4b：SETTLE 必须严格小于快对账窗口，否则「下一次必算」在数学上不成立", () => {
    expect(AI_TRADE_NOTICE_SETTLE_MS).toBeLessThan(AI_TRADE_RECONCILE_FAST_MS);
  });

  it("U-5：resumed 的 12 秒 TTL 两侧各一条；lifecycle=paused 时立即判死", () => {
    const notice = mint("resumed", epoch());
    expect(isAiTradeNoticeLive(notice, epoch(), TS + 11_900)).toBe(true);
    expect(isAiTradeNoticeLive(notice, epoch(), TS + 12_100)).toBe(false);
    expect(
      isAiTradeNoticeLive(notice, epoch({ lifecycle: "paused", reconciledAtMs: TS + 1 }), TS),
    ).toBe(false);
  });

  it("U-6：未登记的 kind 返回 false 且**不抛**（fail-closed，不是崩）", () => {
    const bogus = {
      kind: "no_such_kind",
      text: "x",
      epoch: epoch(),
      ts: TS,
    } as unknown as AiTradeNotice;
    expect(() => isAiTradeNoticeLive(bogus, epoch(), TS)).not.toThrow();
    expect(isAiTradeNoticeLive(bogus, epoch(), TS)).toBe(false);
    expect(isAiTradeNoticeLive(null, epoch(), TS)).toBe(false);
  });

  it("U-7：空串 / 全空白铸造出 null（不留一条空条）", () => {
    expect(mintAiTradeNotice("paused", "", epoch(), TS)).toBeNull();
    expect(mintAiTradeNotice("paused", "   ", epoch(), TS)).toBeNull();
    expect(mintAiTradeNotice("paused", null, epoch(), TS)).toBeNull();
    expect(mintAiTradeNotice("paused", " 有内容 ", epoch(), TS)?.text).toBe("有内容");
  });

  it("U-8：规则表完备性 —— 键集合与写死的七元组逐元素相等", () => {
    expect(Object.keys(AI_TRADE_NOTICE_RULES).sort()).toEqual(
      [
        "action_error",
        "action_unknown",
        "backend_ended",
        "ended",
        "member_status",
        "paused",
        "resumed",
      ].sort(),
    );
  });

  it("U-9：冷占位路径 —— currentAiTradeEpoch(null, resumable, ms) 是 paused 且 groupId 为 null", () => {
    const cold = { groupId: "g-cold" } as never;
    const now = currentAiTradeEpoch(null, cold, 123);
    expect(now.lifecycle).toBe("paused");
    // 冷占位卡没有「这一组的回执」这回事：拿它当组身份会让上一组的回执在冷卡出现时复活。
    expect(now.groupId).toBeNull();
    expect(now.resumedCount).toBeNull();
    expect(now.reconciledAtMs).toBe(123);
  });
});

describe("未落地宽限与纪元三元组（U-10 / U-11 / U-13）", () => {
  it("U-10：pause 回执铸于 running 态时判活；reconciledAtMs 越过 notice.ts 之后判死", () => {
    // `pause_group` 的回执不是 `group_status()` 形状 ⇒ `pauseM.onSuccess` 不置本地态 ⇒ 铸造这一刻
    // 本地 lifecycle 仍是 "running"。没有宽限，这条唯一会说出「平仓其实没成功」的话会被自己判死。
    const notice = mint("paused", epoch({ lifecycle: "running" }));
    expect(
      isAiTradeNoticeLive(notice, epoch({ lifecycle: "running", reconciledAtMs: TS }), TS),
    ).toBe(true);
    // **成对断言**：只写上一条等于把宽限做成了永久豁免（RC-1）。
    expect(
      isAiTradeNoticeLive(notice, epoch({ lifecycle: "running", reconciledAtMs: TS + 1 }), TS),
    ).toBe(false);
  });

  it("U-11：persist 位恰好是 {ended, backend_ended}，且跨进程旧时戳不参与判活", () => {
    const persisted = Object.entries(AI_TRADE_NOTICE_RULES)
      .filter(([, rule]) => rule.persist)
      .map(([kind]) => kind)
      .sort();
    expect(persisted).toEqual(["backend_ended", "ended"]);
    // 水合回来的那一条：ts 是一天前、epoch.reconciledAtMs 是上一个进程的旧戳。
    // 这两种 settledByReconcile:false + ttlMs:null，故两个旧值都不参与判活。
    const stale = mint(
      "ended",
      epoch({ groupId: "g-old", lifecycle: "idle", reconciledAtMs: TS - 5_000 }),
      TS - 86_400_000,
    );
    expect(isAiTradeNoticeLive(stale, epoch({ groupId: null, lifecycle: "idle" }), TS)).toBe(true);
  });

  it("U-13：action_error 靠 resumedCount 判死，而不是靠 lifecycle 的名字", () => {
    // E-13 唯一可达的那条路径是**外部**发起的继续：resumeM.onSuccess 根本不跑，
    // 没有任何写点会去清它，而 `paused → running → paused` 之后按 lifecycle 判它会原样复活。
    const notice = mint("action_error", epoch({ lifecycle: "paused", resumedCount: 0 }));
    expect(isAiTradeNoticeLive(notice, epoch({ lifecycle: "paused", resumedCount: 1 }), TS)).toBe(
      false,
    );
    // 反向：同一段 running/paused 里来回切换态**不影响**它的可见性（validIn:[] 的语义 ——
    // 一次动作的失败说明在同一段生命周期里应当保持可见）。
    expect(isAiTradeNoticeLive(notice, epoch({ lifecycle: "paused" }), TS)).toBe(true);
    expect(isAiTradeNoticeLive(notice, epoch({ lifecycle: "running" }), TS)).toBe(true);
    // 换组之后上一组的失败说明不再是事实。
    expect(isAiTradeNoticeLive(notice, epoch({ groupId: "g2", lifecycle: "paused" }), TS)).toBe(
      false,
    );
  });
});

describe("U-12：铸造闸 —— 非 sticky 不得驱逐仍判活的 sticky", () => {
  beforeEach(() => {
    localStorage.clear();
    useAppStore.setState({
      aiTradeNotice: null,
      aiTradeGroup: null,
      aiTradeResumable: null,
      aiTradeReconciledAtMs: 0,
    });
  });

  it("纯谓词层：action_unknown 撞仍判活的 paused ⇒ 被拦；paused 判死后 ⇒ 放行", () => {
    const paused = mint("paused", epoch({ lifecycle: "paused" }));
    expect(
      isAiTradeNoticeBlockedBySticky(paused, "action_unknown", epoch({ lifecycle: "paused" }), TS),
    ).toBe(true);
    expect(
      isAiTradeNoticeBlockedBySticky(
        paused,
        "action_unknown",
        epoch({ lifecycle: "running", reconciledAtMs: TS + 1 }),
        TS,
      ),
    ).toBe(false);
    // sticky 之间不设闸：ended 永远能覆盖。
    expect(isAiTradeNoticeBlockedBySticky(paused, "ended", epoch({ lifecycle: "paused" }), TS)).toBe(
      false,
    );
  });

  it("store 层：一条「请立即手动处理这些持仓」不会被一句「本次操作结果未知」顶掉", () => {
    useAppStore.setState({
      aiTradeGroup: { groupId: "g1", state: "paused", resumedCount: 0, members: {} } as never,
    });
    const store = useAppStore.getState();
    store.noteAiTradeNotice(
      "paused",
      "已暂停，但平仓没有完成：请立即到下方实盘面板手动处理这些持仓。",
    );
    expect(useAppStore.getState().aiTradeNotice?.kind).toBe("paused");

    store.noteAiTradeNotice("action_unknown", "本次操作结果未知…");
    // 两句话一起消失才是真正的坏结果：真钱待办被覆盖，随后「结果未知」被对账结算掉。
    expect(useAppStore.getState().aiTradeNotice?.kind).toBe("paused");

    // 那条 paused 判死之后（继续成功 ⇒ running + resumedCount+1），槽位可以被替换。
    useAppStore.setState({
      aiTradeGroup: { groupId: "g1", state: "running", resumedCount: 1, members: {} } as never,
      aiTradeReconciledAtMs: Date.now() + 1_000,
    });
    useAppStore.getState().noteAiTradeNotice("action_unknown", "本次操作结果未知…");
    expect(useAppStore.getState().aiTradeNotice?.kind).toBe("action_unknown");
  });

  it("sticky 永远能覆盖：ended 撞一条仍判活的 paused 也照样落位", () => {
    useAppStore.setState({
      aiTradeGroup: { groupId: "g1", state: "paused", resumedCount: 0, members: {} } as never,
    });
    useAppStore.getState().noteAiTradeNotice("paused", "已暂停…");
    useAppStore.getState().noteAiTradeNotice("ended", "已结束并归档。以下持仓仍留在交易所上…");
    expect(useAppStore.getState().aiTradeNotice?.kind).toBe("ended");
  });
});
