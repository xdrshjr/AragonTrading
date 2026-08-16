"use client";

// 崩溃页的**唯一**一份展示（段级 `app/error.tsx` 与根级 `app/global-error.tsx` 共用）。
//
// 它的来历是一次真实事故：一帧非 K 线数据混进图表数据，令图表库在 effect 里抛异常，而彼时
// app/ 下没有任何 error boundary，Next 的根兜底把整棵 React 树换成一行英文黑字，打包桌面端
// 看上去就是整页白屏。详见 docs/diagnoses/desktop-white-screen-market-ws-link-frame/analysis.md
//
// 这里不试图"修好"错误，只负责三件事：说人话、给出唯一出路、把 digest 亮出来供对日志。
//
// ⚠️ 下面那个 `<pre>` 里的**两个并列子节点**是硬约束，不得合并成一个模板字符串：
// `app/error.test.tsx` 用 `getByText("未知错误")` 精确匹配，一合并文本节点就失配——而那条用例
// 是上述事故的回归护栏，只能被满足，不能被改。

interface CrashFallbackProps {
  title: string;
  description: string;
  error?: { message?: string; digest?: string };
  onRetry?: () => void;
  retryText?: string;
}

export default function CrashFallback({
  title,
  description,
  error,
  onRetry,
  retryText = "重试",
}: CrashFallbackProps) {
  return (
    <div className="flex h-screen w-full flex-col items-center justify-center gap-4 bg-[var(--bg)] px-6 text-center">
      <h2 className="text-base font-semibold text-[var(--text)]">{title}</h2>
      <p className="max-w-[42rem] text-sm leading-6 text-[var(--muted)]">{description}</p>
      <pre className="max-w-[42rem] overflow-x-auto rounded-[6px] border border-[var(--border)] bg-[var(--surface)] px-3 py-2 text-left text-[11px] leading-5 text-[var(--muted)]">
        {error?.message || "未知错误"}
        {error?.digest ? `\n错误编号：${error.digest}` : ""}
      </pre>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="rounded-[6px] border border-[var(--border)] bg-[var(--surface)] px-4 py-1.5 text-sm font-medium text-[var(--text)] hover:bg-[var(--surface-2)]"
        >
          {retryText}
        </button>
      )}
    </div>
  );
}
