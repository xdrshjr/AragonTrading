"use client";

// 工作台段的错误边界（Next App Router 约定文件）。
//
// 它存在的理由是一次真实事故：一帧非 K 线数据混进图表数据，令 lightweight-charts 在 effect 里
// 抛异常；此前 app/ 下没有任何 error boundary，Next 的根兜底把整棵 React 树换成一行英文黑字，
// 打包桌面端看上去就是整页白屏——用户既看不出发生了什么，也没有任何自救手段。
// 详见 docs/diagnoses/desktop-white-screen-market-ws-link-frame/analysis.md
//
// 这里不试图"修好"错误，只负责三件事：说人话、给出重试、把 digest 亮出来供对日志。
// 重试走 Next 的 reset()（重挂本段），失败原因若是瞬时坏帧，一次重试即恢复。

import { useEffect } from "react";

interface WorkspaceErrorProps {
  error: Error & { digest?: string };
  reset: () => void;
}

export default function WorkspaceError({ error, reset }: WorkspaceErrorProps) {
  useEffect(() => {
    // 生产构建里错误信息会被 Next 抹成占位串，控制台这一条是排障时唯一能对上号的线索。
    console.error("[workspace] 渲染失败：", error);
  }, [error]);

  return (
    <div className="flex h-screen w-full flex-col items-center justify-center gap-4 bg-[var(--bg)] px-6 text-center">
      <h2 className="text-base font-semibold text-[var(--text)]">工作台渲染失败</h2>
      <p className="max-w-[42rem] text-sm leading-6 text-[var(--muted)]">
        界面遇到一个未处理的异常，已停止渲染以免显示错误的行情。这不会影响后端已落库的数据与
        运行中的策略。可先重试；若反复出现，请把下面的错误编号连同日志一并反馈。
      </p>
      <pre className="max-w-[42rem] overflow-x-auto rounded-[6px] border border-[var(--border)] bg-[var(--surface)] px-3 py-2 text-left text-[11px] leading-5 text-[var(--muted)]">
        {error.message || "未知错误"}
        {error.digest ? `\n错误编号：${error.digest}` : ""}
      </pre>
      <button
        type="button"
        onClick={reset}
        className="rounded-[6px] border border-[var(--border)] bg-[var(--surface)] px-4 py-1.5 text-sm font-medium text-[var(--text)] hover:bg-[var(--surface-2)]"
      >
        重试
      </button>
    </div>
  );
}
