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
//
// 展示部分收在 `components/ui/CrashFallback`，与根级边界 `app/global-error.tsx` 共用同一张页面：
// 崩溃页也是产品的一部分，两级边界长得不一样只会让用户更慌。可见文案逐字未变（回归护栏见
// `app/error.test.tsx`）。

import { useEffect } from "react";
import CrashFallback from "@/components/ui/CrashFallback";

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
    <CrashFallback
      title="工作台渲染失败"
      description="界面遇到一个未处理的异常，已停止渲染以免显示错误的行情。这不会影响后端已落库的数据与运行中的策略。可先重试；若反复出现，请把下面的错误编号连同日志一并反馈。"
      error={error}
      onRetry={reset}
    />
  );
}
