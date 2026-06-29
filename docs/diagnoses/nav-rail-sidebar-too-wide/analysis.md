# 诊断报告：左侧功能侧边栏（NavRail）过宽

> Subtask #0（问题定位与分析）产出。本节点不写修复代码、不执行 `git commit`，仅定位根因并给出候选修复方案。

## 一、问题描述（Problem description）

第 3 轮用户需求：

> 1 左侧的功能侧边栏，宽度修改为现在的 3/4 即可，现在太宽了。

当前左侧常驻功能导航栏（NavRail，承载「交易 / 因子实验室」两个一级入口）默认仅显示图标（第 2 轮已改为 icon-only + hover 气泡），但栏体宽度偏大，相对一排 20px 图标显得空旷。用户希望把该栏宽度收窄为当前的 **3/4**。

- 现状宽度：`w-16` = `4rem` = **64px**
- 目标宽度：`64px × 3/4` = **48px**（对应 Tailwind `w-12` = `3rem`）

这不是功能性崩溃，而是一处布局尺寸调整（以「Bug 修复」流程承载的视觉需求）。

## 二、复现步骤（Reproduction steps）

1. 启动 Web 端（仓库根目录下）：
   ```bash
   cd apps/web
   pnpm dev
   ```
   桌面端等价入口：在 `desktop/` 下运行打包/启动脚本后打开主窗口。
2. 打开主界面，观察 header 横线之下、内容区最左侧的竖向导航栏（含「交易」「因子实验室」两个图标按钮）。
3. 用浏览器 DevTools 选中该 `<nav aria-label="主功能导航">` 元素，查看计算样式：`width: 64px`（即 `w-16`）。
4. 结论：栏宽 64px，内部图标 20px、按钮 hover 命中区充裕但留白偏多，整体偏宽——与「太宽」的主观反馈一致。

期望：栏宽变为 **48px**（3/4），图标与按钮仍居中、可点击，hover 文字气泡仍正常右侧弹出。

## 三、根因分析（Root cause analysis）

栏宽由 NavRail 根元素 `<nav>` 上一个**硬编码的 Tailwind 宽度类 `w-16`** 决定，且为该栏宽度的唯一来源。

证据 1 — 宽度类位置（`apps/web/components/Nav/NavRail.tsx:69`）：

```tsx
<nav
  aria-label="主功能导航"
  className="flex w-16 shrink-0 flex-col gap-1 border-r border-[var(--border)] bg-[var(--surface)]/60 px-2 py-3"
>
```

- `w-16` 固定栏宽 = 64px；`shrink-0` 保证它在父级 flex 容器中不被压缩；`px-2`（左右各 8px）为内边距。

证据 2 — 布局为 flex，内容区自适应，无第二处宽度耦合（`apps/web/app/page.tsx:591-593`）：

```tsx
<div className="flex min-h-0 flex-1">
  <NavRail />
  <div className="flex min-h-0 flex-1 flex-col">
```

- 父容器 `flex`，右侧内容区用 `flex-1` 占据剩余空间。NavRail 缩窄后，内容区会自动补齐多出的 16px，**无需在 page.tsx 或别处调整偏移/占位**。栏宽改动是局部、自洽的。

证据 3 — Tailwind 间距为默认刻度，未被自定义（`apps/web/tailwind.config.ts:14-43` 的 `theme.extend` 仅扩展 colors/fontFamily/borderRadius/boxShadow/transitionTimingFunction，**未覆盖 spacing**）。因此默认换算成立：`w-16`=64px、`w-12`=48px、`w-14`=56px。`48px` 恰好等于 `64px` 的 3/4，对应 `w-12`。

证据 4 — 存在一处会因改宽而过期的注释（`apps/web/components/Nav/NavRail.tsx:98`）：

```tsx
{/* …pointer-events-none + 绝对定位：不撑开 64px 栏宽、不拦截命中。 */}
```

该注释写死了「64px 栏宽」，若改宽未同步更新会产生与代码不符的僵尸注释（CLAUDE.md 第四节：注释须真实）。

证据 5 — 测试不锁定宽度类（`apps/web/components/Nav/NavRail.test.tsx`）：用例只断言两项可访问名、`aria-current` 高亮、点击切换视图、icon-only 气泡契约（`opacity-0` / `group-hover:opacity-100`），**未对 `w-16` 做断言**。故改宽不会打破现有单测；如需可加一条宽度护栏（属修复/开发节点职责）。

**根因结论**：栏宽「太宽」源于 `NavRail.tsx:69` 硬编码 `w-16`(64px)。把它改为 `w-12`(48px) 即满足 3/4 需求，且因布局是 flex 自适应，改动范围最小、无外溢副作用。

## 四、影响面（Impact surface）

- **受影响组件**：仅 `apps/web/components/Nav/NavRail.tsx` 渲染的左侧导航栏。
- **受影响视图**：交易、因子实验室——两者共用同一外壳，导航栏全程常驻，故视觉变化在所有一级视图一致呈现。
- **平台**：Web 与桌面端（Electron）共用 `apps/web`，两端表现一致。
- **下游布局**：内容区 `flex-1` 自动多得 16px 宽度；右侧面板/图表区有微小可用宽度增益，无破坏性回流。
- **可访问性 / 交互**：按钮高度 `h-11`(44px) 与命中逻辑不变；缩窄到 48px 后内边距 `px-2`(共 16px) 仍余 32px 容纳 20px 图标居中，点击与键盘可达性不受影响；hover 文字气泡为 `position:absolute` + `left-full`，不占栏宽，缩窄反而更贴合。

## 五、候选修复方案对比（Candidate fixes）

| 方案 | 改动 | 是否精确等于 3/4 | 侵入性 | 风险 | 工作量 |
|---|---|---|---|---|---|
| **A（推荐）** `w-16` → `w-12` | `NavRail.tsx:69` 改一个类；同步把第 98 行注释「64px」改为「48px」 | 是（48px = 64×3/4） | 极小（1 个类 + 1 处注释） | 极低（flex 自适应、单测不锁宽） | 极小 |
| B `w-16` → `w-[48px]` | 用任意值类写死 48px | 是 | 极小 | 极低，但偏离设计刻度、不如 `w-12` 惯用 | 极小 |
| C `w-16` → `w-14` | 改为 56px | 否（56px = 64×7/8，非 3/4） | 极小 | 不满足需求（宽度不对） | 极小 |

> 备注：三方案都仅触及栏自身宽度，均不需要改 `page.tsx`（flex `flex-1` 自动补宽）。

## 六、推荐方案（Recommended fix）

**采用方案 A：将 `apps/web/components/Nav/NavRail.tsx:69` 的 `w-16` 改为 `w-12`，并把第 98 行注释中的「64px 栏宽」同步更新为「48px 栏宽」。**

理由：

1. **精确命中需求**：`w-12`=`3rem`=48px，正是 64px 的 3/4，无近似误差（方案 C 的 `w-14` 是 7/8，不符）。
2. **使用设计系统刻度**：`w-12` 是 Tailwind 标准刻度类，与全仓库其余尺寸保持一致（优于方案 B 的任意值 `w-[48px]`，符合 CLAUDE.md「与现有命名风格一致」）。
3. **改动最小、无外溢**：布局为 flex 自适应，仅一处类即可，`page.tsx` 等无需联动；现有单测不锁宽，不会引入回归。
4. **杜绝僵尸注释**：同步更新第 98 行「64px」表述，保证注释与代码一致（CLAUDE.md 第四节）。

可选增强（交由修复/开发节点判断，非必需）：在 `NavRail.test.tsx` 增加一条宽度护栏，断言根 `<nav>` 类含 `w-12`，把本次尺寸契约固化为未来回归护栏（CLAUDE.md 第七节）。

---

### 关键文件引用速查

- 宽度来源：`apps/web/components/Nav/NavRail.tsx:69`（`w-16` → 目标 `w-12`）
- 过期注释：`apps/web/components/Nav/NavRail.tsx:98`（「64px 栏宽」→「48px 栏宽」）
- 布局自适应证据：`apps/web/app/page.tsx:591-593`（`flex` + `flex-1`）
- 间距刻度未自定义：`apps/web/tailwind.config.ts:14-43`
- 现有单测（不锁宽）：`apps/web/components/Nav/NavRail.test.tsx`

---

## 评审结论（Review Verdict）

> 高级评审节点（Subtask #1）产出。已对照真实源码逐条复核根因证据与候选修复，
> 仅修改本 `analysis.md`，不写修复代码、不执行 `git commit`。

### 1. 根因证据复核：成立，证据链闭合

逐条对照仓库当前代码，五条证据全部属实：

| 证据 | 引用 | 复核结果 |
|---|---|---|
| 1 宽度来源 | `NavRail.tsx:69` `className="flex w-16 shrink-0 …"` | ✅ 行号与类名一致；`w-16` 确为 `<nav>` 唯一宽度类。 |
| 2 布局自适应 | `page.tsx:591-593` `flex min-h-0 flex-1` → `<NavRail/>` → `flex-1 flex-col` | ✅ 原文一字不差；右侧内容区 `flex-1` 吸收缩窄出的 16px。 |
| 3 间距未自定义 | `tailwind.config.ts:14-43` `theme.extend` 无 `spacing` | ✅ 仅扩展 colors/fontFamily/borderRadius/boxShadow/transitionTimingFunction，默认刻度成立。 |
| 4 过期注释 | `NavRail.tsx:98` 「不撑开 64px 栏宽」 | ✅ 该「64px」硬编码于行内注释，改宽后必须同步，否则成僵尸注释。 |
| 5 单测不锁宽 | `NavRail.test.tsx` | ✅ 用例仅断言可访问名 / `aria-current` / 点击切换 / icon-only 气泡契约，无 `w-16` 断言；改宽不破坏现有单测。 |

**补强证据（评审追加）——栏宽无第二来源**：对 `apps/web` 全量检索 `w-16` / `w-14` / `64px` / `NavRail`，
NavRail 之外仅 `LeverageControl.tsx:52`（`w-16` 杠杆输入标签宽）与 `FactorSetBuilder.tsx:447`
（`w-14` 权重数字宽）命中，二者均属无关组件、与导航栏布局无耦合。故「`NavRail.tsx:69` 是导航栏
宽度唯一来源」成立，不存在被遗漏的第二处宽度耦合或父级偏移占位。

**符号 → 症状 因果闭合**：症状是「栏体相对 20px 图标显空旷、太宽」；`w-16`(64px) 减去 `px-2`(16px)
得 48px 按钮内容宽，承载 20px 图标确有约 28px 富余留白——证据直接解释观察到的现象，无需别的假设。

未发现反例或更可能的替代解释。证据链闭合。

### 2. 候选修复复核：方案 A 为最小且无外溢的正确修复

- **是否精确 3/4**：Tailwind 默认 `spacing.12 = 3rem = 48px`，`64 × 3/4 = 48` 精确相等（无近似）。
  方案 C 的 `w-14`=56px=7/8 不符需求；方案 B 的 `w-[48px]` 数值正确但偏离设计刻度，逊于惯用的 `w-12`。
- **是否最小变更**：业务上的最小变更是 `NavRail.tsx:69` 单类 `w-16`→`w-12`。同步更新行 98 注释
  「64px→48px」并非「多做的事」，而是 CLAUDE.md 第四节（注释须真实、禁僵尸注释）要求的随附修正，
  不计入功能改动面。两处合计仍是最小封闭改动。
- **边界与回归审计**（本修复为纯 CSS 布局变更，无运行时逻辑）：
  - *并发 / 错误路径 / 异常输入*：不适用——无状态、无 IO、无分支逻辑，不存在并发或错误路径风险。
  - *命中区*：按钮宽随内容盒由 48px 收至 32px（48-16 内边距），高度 `h-11`(44px) 不变；32×44 对桌面
    鼠标目标仍充裕，图标 20px 居中可点。此为用户显式要求 3/4 宽度的预期取舍，非缺陷，记录在案。
  - *选中指示条 / hover 气泡*：指示条 `absolute left-0 w-[3px]`、气泡 `absolute left-full ml-2` 均为
    绝对定位、不占栏宽；缩窄后气泡仍自右边界外弹出，行为不变，无回流或遮挡回归。
  - *跨平台*：Web 与桌面端（Electron）共用 `apps/web`，单一类改动两端一致。
- **回归风险**：极低。`page.tsx` 等上层无需联动（`flex-1` 自动补宽）；现有单测不锁宽，不会失败。

### 3. 最终推荐修复（Final Recommended Fix）

**采用方案 A——将 `apps/web/components/Nav/NavRail.tsx:69` 的 `w-16` 改为 `w-12`（64px→48px，精确 3/4），
并同步把第 98 行注释中的「64px 栏宽」改为「48px 栏宽」。**

理由：精确命中 3/4 需求、使用 Tailwind 标准设计刻度、布局 flex 自适应使改动局部自洽无外溢、并消除随之
过期的注释。这是解决「栏太宽」的最小且充分的修复，不引入新回归。

**给下游修复/开发节点的执行提示**：
1. 仅此两处改动即满足验收，不要顺手改 `page.tsx` 或其它 `w-16`（那些属无关组件）。
2. 建议（非强制）在 `NavRail.test.tsx` 增加一条护栏：断言根 `<nav>` 的 `className` 含 `w-12`，
   把本次尺寸契约固化为回归护栏（CLAUDE.md 第七节）。

**Definition of Done 核对**：根因证据链闭合（含补强的「无第二宽度来源」检索）；本节明确给出单一推荐修复
（方案 A）及其理由。评审通过。
