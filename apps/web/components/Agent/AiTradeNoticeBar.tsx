"use client";

import { useEffect, useState } from "react";
import {
  AI_TRADE_NOTICE_RULES,
  currentAiTradeEpoch,
  isAiTradeNoticeLive,
} from "@/lib/aiTradeNotice";
import type { AiTradeNoticeRule } from "@/lib/aiTradeNotice";
import { useAppStore } from "@/lib/store";

/**
 * AI 自动交易生命周期回执的判定性提示条（ai-trade-notice-state-sync §4.8）。
 *
 * 从 `AgentPanel` 里的内联函数抽出，为的是两件事：① 它可以被单测**直接渲染**，不必拖起整个
 * AgentPanel；② 判活谓词有了一个明确的落点。**这条提示的消失不再依赖任何写点记得去清它** ——
 * 渲染点先过 `isAiTradeNoticeLive`，纪元不匹配就整条不渲染（G-2 的结构性质）。
 *
 * 挂在 AgentPanel 头部之下，是因为「结束并归档」之后 AI 面板整块会消失，只有这里在两种态下都还在。
 * 刻意**不做成 toast**：sticky 的那两条说的是真钱（「以下持仓从此不再由任何自动化管理」），
 * 三秒消失等于没说。
 */
export default function AiTradeNoticeBar() {
  // ---- §4.8-6：四个订阅源，少一个就会出现「事实变了但这一条没有重算」----
  // 谓词的入参没有一个是 props，组件必须自己订阅齐。尤其 `aiTradeReconciledAtMs`：少了它，
  // 一次「除了时戳什么都没变」的对账不会触发重渲染 ⇒ `action_unknown` 永远退不了场，
  // `paused` 的「未落地宽限」也永远退不出去。`syncAiTradeNotice()` 是 store 卫生，替代不了订阅。
  const notice = useAppStore((s) => s.aiTradeNotice);
  const group = useAppStore((s) => s.aiTradeGroup);
  const resumable = useAppStore((s) => s.aiTradeResumable);
  const reconciledAtMs = useAppStore((s) => s.aiTradeReconciledAtMs);
  const dismiss = useAppStore((s) => s.dismissAiTradeNotice);
  const [nowMs, setNowMs] = useState(() => Date.now());

  // ⚠️ 全部 hooks 必须声明在任何早退**之前**：本仓库有过一次「条件调用 hook 直接把面板打崩」的
  // 成文事故（深度思维链那一轮的 P0）。下面的早退因此一律排在最后。
  const kind = notice?.kind ?? null;
  const ttlMs = kind ? (AI_TRADE_NOTICE_RULES[kind]?.ttlMs ?? null) : null;
  const entered = useNoticeEntrance(notice?.ts ?? null);
  useEffect(() => {
    // 只在「当前提示的 kind 带 ttl」时才起表：常驻一个每秒定时器纯属浪费（风险 R-7）。
    if (ttlMs == null) return;
    const timer = setInterval(() => setNowMs(Date.now()), 1_000);
    return () => clearInterval(timer);
  }, [ttlMs]);

  if (!notice) return null;
  const rule = AI_TRADE_NOTICE_RULES[notice.kind] as AiTradeNoticeRule | undefined;
  if (!rule) return null;
  const epoch = currentAiTradeEpoch(group, resumable, reconciledAtMs);
  // 有 ttl 的种类要用走表的 `nowMs`，其余用一次性取值即可（那两条判据都不看它）。
  if (!isAiTradeNoticeLive(notice, epoch, ttlMs == null ? notice.ts : nowMs)) return null;

  const tone = TONE_CLASSES[rule.tone];
  return (
    <div
      data-testid="ai-trade-notice-bar"
      // `role="alert"` **只给需要用户行动的两种**（孤儿持仓 / 会话在后端自行终止）：把每一条提示
      // 都做成 alert 会让屏幕阅读器在每次暂停时打断用户，那不是「更安全」，是噪音。
      role={rule.urgent ? "alert" : "status"}
      aria-live={rule.urgent ? "assertive" : "polite"}
      // 分色靠「左缘 3px 面色竖线 + 既有底色」，**不**为每种态新造一个底色 token：全仓只有
      // accent 与 warn 两个 soft 面，多头 / 空头**没有** soft 面；给它们写一个想当然的 soft
      // token 名字是**静默**无底色（既有的内联回退扫描只看带回退的写法，看不见裸 var）。
      // 面色只进 border / background，绝不进任何 `color:`（配色纪律，源码扫描看守）。
      // 进出场只动 opacity / transform：这里做高度动画会把整段对话推位（`min-h-[24px]` 那条教训）。
      className={`flex items-start gap-2 border-b border-l-[3px] px-3 py-1.5 ${tone.border} ${tone.bg} ${
        entered ? "translate-y-0 opacity-100" : "translate-y-[-2px] opacity-0"
      } transition-[opacity,transform] duration-150 ease-smooth motion-reduce:transition-none`}
    >
      {/* `key` 让每一条新提示重新挂载 ⇒ 折叠态自动回到初值，不必在父组件里再养一个 effect。 */}
      <NoticeBody key={notice.ts} glyph={rule.glyph} text={notice.text} tone={tone} />
      <button
        type="button"
        // 可访问名**必须以可见文案开头**（WCAG 2.5.3 Label in Name，A 级）：可见的是「知道了」
        // 而可访问名只写「关闭提示」时，语音控制用户念屏幕上那三个字是点不动它的。可见文案随
        // 种类变（sticky 三种是「知道了」），故只能拼，不能写死一个串。
        aria-label={`${rule.dismissLabel}，关闭这条提示`}
        onClick={dismiss}
        className="shrink-0 text-[10px] text-[var(--muted)] transition-colors hover:text-[var(--text)]"
      >
        {rule.dismissLabel}
      </button>
    </div>
  );
}

type ToneClasses = { border: string; bg: string; text: string };

/**
 * 进场动效的那一位状态（§4.8-3）。
 *
 * 只挂 `transition-[opacity,transform]` 是**不会动**的：类名写死成终态，浏览器自始至终只看到
 * 一个值，那几个工具类就成了摆设。故先落在 `opacity-0 translate-y-[-2px]`，下一帧再翻到位；
 * 用 `requestAnimationFrame` 而非 `setTimeout(0)`（后者可能与同帧样式计算合并，依旧只见终态）。
 * `prefers-reduced-motion` 下由 `motion-reduce:transition-none` 瞬时到位 —— 关的是动效，
 * 不是任何一个字（WCAG 2.3.3 的极性）。依赖取 `ts`：换一句话要重播，每秒走的 TTL 表不该重播。
 */
function useNoticeEntrance(noticeTs: number | null): boolean {
  const [entered, setEntered] = useState(false);
  useEffect(() => {
    if (noticeTs == null) return;
    setEntered(false);
    const raf = requestAnimationFrame(() => setEntered(true));
    return () => cancelAnimationFrame(raf);
  }, [noticeTs]);
  return entered;
}

/**
 * 「展开 / 收起」只在这条文案**真的可能被裁掉**时才给（§4.8-2 说的是「**长文**两行折叠」）。
 *
 * 判据取字数而不是量 `scrollHeight > clientHeight`：jsdom 里两个高度恒为 0，量出来的结论永远是
 * 「裁不到」，于是 C-4 会在真实浏览器里失效的同时恒绿 ——「用例绿着但分支从没被走到」。
 * 阈值按**最窄**断面取（右栏下界 320px，扣内边距 / 前缀 / 关闭按钮后正文约 23 个全角字一行，
 * 两行下界 46 字，留余量取 40）。极性偏向「多给一个按钮」：漏给等于后半句被无声吞掉。
 */
const NOTICE_CLAMP_MIN_CHARS = 40;

/**
 * 分形前缀 + 两行折叠正文（§4.8-1 / §4.8-2）。
 *
 * 拆成子组件是 CLAUDE.md 的 50 行阈值要求的（§4.8-7 明写「拆到同文件内，不新开第三个文件」）。
 * 顺带一个好处：`expanded` 这个纯呈现态住在它真正属于的地方。
 */
function NoticeBody({ glyph, text, tone }: { glyph: string; text: string; tone: ToneClasses }) {
  const [expanded, setExpanded] = useState(false);
  const clampable = text.length > NOTICE_CLAMP_MIN_CHARS;
  return (
    <>
      {/* 分形前缀：强制单色模式 / 色觉障碍下仍能区分种类（G-5「绝不只靠颜色」）。
          **刻意不 `aria-hidden`**：C-5 断的是它出现在可访问名里 —— 藏起来就等于这条「不只靠颜色」
          只落在了视觉通道上，而屏幕阅读器用户拿到的仍是一句无差别的话。 */}
      <span className={`shrink-0 select-none text-[10px] leading-relaxed ${tone.text}`}>
        {glyph}
      </span>
      <span className={`min-w-0 flex-1 text-[10px] leading-relaxed ${tone.text}`}>
        {/* 折叠是**纯呈现**：`title` 仍带全文，复制 / 朗读不受影响。用户给的那条提示有 5 个标的、
            约 90 个汉字，在 AgentPanel 这个窄栏里要占 6 行、把对话区顶下去。 */}
        <span className={expanded || !clampable ? "block" : "line-clamp-2"} title={text}>
          {text}
        </span>
        {clampable && (
          <button
            type="button"
            aria-expanded={expanded}
            onClick={() => setExpanded((v) => !v)}
            className="mt-0.5 text-[10px] text-[var(--muted)] underline-offset-2 transition-colors hover:text-[var(--text)] hover:underline"
          >
            {expanded ? "收起" : "展开"}
          </button>
        )}
      </span>
    </>
  );
}

/**
 * 色板（§4.8-1）：**只许用 `globals.css` 里已定义的 token**，对比度取自那里已复算好的注释，
 * 不新增任何需要重算 WCAG 的配对。三条逐字遵守的纪律：
 * ① 面色（`--accent` / `--long` / `--short`）只能进 border / background，文字一律用墨色；
 * ② `--short-ink` 不得与 `bg-[var(--accent-soft)]` 同行（暗色仅 4.41，是配对矩阵里的显式排除项）
 *    —— 下表用 `--surface-2` 打底，天然避开；
 * ③ 警告档用 `--warn-soft` 打底 + 图标（`globals.css:47` 的成文口径）。
 *
 * Tailwind 只扫**字面量**，所以这里必须是写死的完整类名，不能拼接。
 */
const TONE_CLASSES: Record<AiTradeNoticeRule["tone"], ToneClasses> = {
  // 行末是该「墨色 on 底色」配对在 globals.css 里已复算好的对比度（浅 / 暗），本轮零新增配对。
  accent: { border: "border-l-[var(--accent)]", bg: "border-b-[var(--accent)] bg-[var(--accent-soft)]",
            text: "text-[var(--accent-ink)]" },                                  // 4.61 / 4.98
  long: { border: "border-l-[var(--long)]", bg: "border-b-[var(--border)] bg-[var(--surface-2)]",
          text: "text-[var(--long-ink)]" },                                      // 5.49 / 5.70
  short: { border: "border-l-[var(--short)]", bg: "border-b-[var(--border)] bg-[var(--surface-2)]",
           text: "text-[var(--short-ink)]" },                                    // 5.73 / 4.74
  warn: { border: "border-l-[var(--warn)]", bg: "border-b-[var(--warn)] bg-[var(--warn-soft)]",
          text: "text-[var(--warn)]" },                                          // 5.07 / 6.14
};
