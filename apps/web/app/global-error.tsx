"use client";

// 根级错误边界（Next App Router 约定文件）。
//
// 与 `app/error.tsx` 的分工是硬契约、不是风格选择：`error.tsx` 只接**本段 children** 的渲染异常，
// **根 layout / 根 template 自身抛出的异常只有本文件能接**，否则落到 Next 内置兜底——也就是那次
// 白屏事故里「整棵 React 树换成一行英文黑字」的形状。
// 详见 docs/diagnoses/desktop-white-screen-market-ws-link-frame/analysis.md
//
// 两条本文件特有的约束：
// 1. 它替换的是根 layout，故必须自带 `<html><body>` 外壳，也**必须自己 import globals.css**——
//    `@tailwind` 三行与全部 CSS 变量的唯一导入点是 `app/layout.tsx`，而那个 layout 此刻不渲染；
//    不导入就等于没修：崩溃页会退化成一段无样式黑字。
// 2. 主题类 `.dark` 由 `lib/store.ts` 在客户端挂到 documentElement，根级边界下那段代码不会执行，
//    故本页**恒以浅色取值渲染**。这是有意接受的降级，不是 bug：一个只在根布局炸掉时才出现的
//    页面，「看得见、说人话、有出路」远比「跟随主题」重要。

import "./globals.css";
import CrashFallback from "@/components/ui/CrashFallback";

interface GlobalErrorProps {
  error: Error & { digest?: string };
  reset: () => void;
}

export default function GlobalError({ error, reset }: GlobalErrorProps) {
  return (
    <html lang="zh-CN">
      <body>
        <CrashFallback
          title="应用启动失败"
          description="界面在最外层就没能渲染起来，因此整页停在了这里。后端服务与已落库的数据不受影响，运行中的策略仍按原样执行。请先重新载入；若反复出现，请把下面的错误编号连同日志一并反馈。"
          error={error}
          onRetry={reset}
          retryText="重新载入"
        />
      </body>
    </html>
  );
}
