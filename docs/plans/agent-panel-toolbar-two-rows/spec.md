# AI 助手面板工具栏分为两横栏（窄栏零换行）开发方案 spec.md

> Feature: `agent-panel-toolbar-two-rows` · 版本 **v2**（v1 由方案设计节点 Subtask #0 产出；v2 为 Subtask #1 评审修订，逐条改动见下方「评审记录」）
> 关联需求（原文）：
> 「这个项目最右边，界面最右边的 Agent 自动交易的这样的一个 AI 交易助手容器，当它开启了 AI 自动交易了以后，如果右侧的这样的一个面板的宽度过窄的时候，它会导致上面的这些按钮的文字自动换行，极其不美观，需要把这个调整一下，把它分散到两个横栏中，其中一部分横栏可以放到下方的输入框区域的上面，紧贴着输入框。」
> 附加要求：Anthropic 顶级产品品质 —— 美观、优雅、顶级设计、符合人机交互最佳实践；稳健、可靠。

---

## 评审记录（Review Notes · Subtask #1）

评审基线不是读文档，而是逐条回到工作树核对：`AgentPanel.tsx`（455 行）、`AiTradeToggle.tsx`（497 行）、
`AiTradeToggle.test.tsx`（403 行）、`AgentComposer.tsx`、`components/ui/index.tsx` 的 `Button` / `Badge` /
`Switch` / `Modal` 基类、`tailwind.config.ts`、`lib/store.ts` 的宽度夹取、`app/page.tsx` 的 `<aside>`、
`RightPanel.tsx`，以及 `contrastTokens.source.test.ts` / `globals.source.test.ts` / `warnToken.source.test.ts`
三道既有门禁。

**v1 的主干设计成立**，下列六条事实性前提逐条属实、不需要改：`Button` 与 `Badge` 基类确实不含
`whitespace-nowrap`（`ui/index.tsx:27` / `:143`）；`Switch` 基类自带 `shrink-0`（`:184`）；`tailwind.config.ts`
的 `plugins: []`，确无容器查询插件；`rightPanelWidth` 夹取 `[320, 640]`、默认 380（`store.ts:107-109`）；
`<aside>` 无内边距，320px 档下 `px-3` 后可用宽正是 296px；`AgentPanel` 是 `AiTradeToggle` 的**全仓唯一**
调用方。§2.3 的宽度预算按 Tailwind 实际类值复算，逐格误差在 ±6px 内，「除续跑在途瞬态外 320px 全状态
不需要截断」这条结论成立。

以下是需要修的地方。**P0 / P1 已在 v2 正文中直接改掉**，P2 亦一并修订（均在正文标注 `RV-n`）。

| # | 严重度 | 位置 | 问题 | v2 处置 |
|---|---|---|---|---|
| RV-1 | **P0** | §9.1 T8 / §9.3 R4 | 「构造 `resumePreview` 失败 → `getByRole("alert")`」这条复现路径**根本到不了**那一行。`openResume()` 是**先** `setResumeOpen(true)` **再**发请求，失败时 `resumeOpen` 仍为真，`paused && error && !resumeOpen && !endOpen` 直接为假；错误改由 `AiTradeResumeDialog.tsx:125` 那段**同样是 `role="alert"`** 的段落渲染。照 v1 写出来的 T8 只会命中弹窗里的 alert（或因命中多个而抛错），R4 则会被验收人判成「复现不出来」。 | §4.4 新增「这一行什么时候才会出现」一表，逐个 `error` 产生点列出当时的弹窗状态，指明唯一到达路径是**「请求在途时关掉弹窗、随后失败」的竞态**；T8 与 R4 按该顺序重写 |
| RV-2 | **P1** | §4.1 / §8.7 | 「行高恒定 / 不跳动」是**假的**。idle 行内最高的是 20px 的 `Switch`；running / paused 多出 `Button size="sm"`（`text-xs` line-height 16px + `py-1`×2 = 24px）。开启 / 停止自动交易时控制条会抖 4px 并连带推动输入框——这正是 §4.1 拿来否决「条件渲染」的那类缺陷，只是幅度小一档。 | 行容器加 `min-h-[24px]` 把三态行高锁死；§2.3 补行高预算、§4.1 / §8 改成据实表述；新增人工验收 R9 |
| RV-3 | **P1** | §9.1 T7 / T8 | 断言无落点。左右两簇是裸 `<div>`，既无 `role` 也无可访问名，`toHaveClass` 拿不到元素；错误 `<span>` 又与四个弹窗里的错误段落共用 `role="alert"`，`getByRole("alert")` 天然有歧义。T7 / T8 按 v1 的写法**写不出来**。 | §4.4 补三个落点 `data-testid`（`ai-trade-toggle-row` / `-main` / `-actions`）与 `-error`，写进 §6 DOM 契约；T7 / T8 改用它们定位 |
| RV-4 | **P1** | §4.4 / §3 G4 | `title` 下沉到左簇会**丢掉右簇按钮的原生气泡**。改前 `title` 挂在根节点上，浏览器沿祖先链取气泡，悬停「⏸ 暂停」/「▶ 继续」/「结束」都能看到；而暂停态那段文案（「点『继续』核对持仓后接着跑，或『结束』归档」）恰恰就是在解释这两颗按钮。下沉后它们悬停无提示——G4 宣称的「零行为回归」在这一点上不成立。 | `title` 改挂**行容器**（覆盖面 ⊇ 改前，且仍是唯一一份，K1 / T9 不受影响）；G4 据实补一句说明取舍 |
| RV-5 | **P1** | §5 文件变更计划 | 漏了两处会立刻变成僵尸注释的落点：`AiTradeStatsButton.tsx:8`「挂在 AgentPanel 头部 AiTradeToggle 左侧」与 `AiTradeToggle.tsx:34`「挂 AgentPanel 头部『新对话』右侧」，改完全都不再成立（`AgentPanel.tsx:430` 的分段行注释亦需微调）。CLAUDE.md §四明令「删除一切僵尸注释」，而 `AiTradeStatsButton.tsx` 甚至不在变更清单里，实施者不会打开它。 | §5 补入该文件并把注释刷新写进第 1 / 2 行的意图列；§11 加一步 |
| RV-6 | **P1** | §9.1「12 条」/ §9.3 DoD / §4.4 K1 | 基数写错，而这两个数字都被写进了 DoD。`AiTradeToggle.test.tsx` 实有 **15** 条（`describe("AiTradeToggle")` 8 条 + `describe("AiTradeToggle 的三态")` 7 条），不是 12 条；`findByTitle("开启 AI 自动交易 / 回测")` 实有 **6** 处（`:112 / :121 / :148 / :174 / :204 / :398`），不是 5 处，另有 `:130` 一处查门禁文案。按错的数字对账，验收会在「少了 3 条」上空耗，或反过来默许有人删掉 3 条。 | 全文订正为 15 条 / 6 处，并给出行号 |
| RV-7 | P2 | §4.4 K3 归因 | 「`contrastTokens.source.test.ts` 有一条专门的看守」在本场景**不成立**：那条判据是**同一行**内同时出现 `var(--short-ink)` 与 `bg-[var(--accent-soft)]`，而改后控制条底色写在 `AgentPanel.tsx`、`--short-ink` 写在 `AiTradeToggle.tsx`，跨文件跨行，既有门禁抓不到。真正看住这一格的只有本轮新增的 S5——把防线归给一个抓不到的门禁，等于没有防线。 | §4.4 / §10 K3 改成据实归因 |
| RV-8 | P2 | §9.1 桩化说明 | 三处会让实施者卡住的空白：① 没说桩掉 `AiTradeToggle` 后整棵子树不再有 `useQuery`，因而**不需要** `QueryClientProvider`；② T5 没说 `handleEvent` 是 `agentWS(conversationId, handleEvent, onConnState)` 的**第 2 个位置参数**；③ T2 需要 `AiTradeGroupState` 夹具，而 `AiTradeAgentTrace.test.tsx` 的 `groupWith()`（第 43-70 行）并未导出。 | §9.1 补三条注 |
| RV-9 | P2 | §4.2 / §8.2 | 措辞不准：`AgentPanel` 的 `<header>` 是 `<aside>` 的后代，按 HTML-AAM **不**映射 `banner` 角色，只是一个通用 `header`；「散落在 banner 里」的说法会误导后续 a11y 走查。另：§4.2 未点明「继承沿 **DOM 树**而非 React 树传播，所以 `nowrap` 不会漏进 `createPortal` 出去的四个弹窗」——这恰恰是敢用继承式 `nowrap` 而不逐点补类的前提。 | 两处补正 |
| RV-10 | P2 | §2.3 结论 / §9.3 R5 | 两处小失真：续跑在途瞬态的缺口约 29px，标签会退到约 37px，实际显示是「AI 自…」而非文中的「AI 自动交…」；R5 的「两者之间无空隙」与实际不符——`AgentComposer` 自带 `border-t`，控制条与输入框之间必然有一条 1px 分隔线。 | 两处据实改写 |
| RV-11 | P2 | §10 K6 | 免 remount 的**理由**站不住：「React 只是换了父节点但仍在同一次 reconcile 中」并不成立（跨父节点的位置变化本来就会卸载重挂）。结论没错，但根据不对——这里根本不存在运行期的父节点切换，`AiTradeToggle` 是**源码级**搬到新位置，改完之后首次挂载即长期挂着。理由写错会在下次有人真的想做条件渲染时给出错误的安全感。 | K6 改写为正确根据 |
| RV-12 | P2 | §4.1 / §10 | 640px 档下 `justify-between` 会把「⏸ 暂停」甩到离开关约 400px 外，按 Fitts 与格式塔邻近律，控件与其状态源看起来不像一组。这不是缺陷，但需要一条明确理由，否则后续很容易被人「顺手改成左对齐」。 | §4.1 补理由（与顶栏 / 输入框右簇对齐成一条右侧列）；§10 补 K9 |
| RV-13 | P2 | §11 | 无回滚说明。虽是纯表现层，仍应写明回滚粒度。 | §11 补一句 |

**未采纳的意见（记录在案，避免下一轮重提）**：CLAUDE.md 的「单行 ≤ 80 列」在 `apps/web` 未被任何工具强制
（无 prettier / eslint 配置，`printWidth` / `max-len` 均未设），且被改动的 `<header>` 行现状就有 103 列。本轮
按「与周边一致」处理，不为此单独换行——若要收敛，应作为独立的仓库级格式化提案，而不是夹带在一个
排版缺陷修复里。

---

## 1. 概述（Overview）

工作台右侧那块可拖拽调宽的竖向容器（`apps/web/app/page.tsx` 的 `<aside>`，宽度由 `store.rightPanelWidth` 单点持有，取值区间 **320–640px**、默认 380px），在「AI 助手」页签下渲染 `AgentPanel`。`AgentPanel` 顶部只有**一条**横栏（`AgentPanel.tsx:315-333`），它同时承载了两类完全不同的东西：左侧是**身份与连接态**（连接指示点、「AI 助手」、当前模型名），右侧是**四组动作**——「新对话」按钮、「统计」按钮，以及 `AiTradeToggle` 整簇（「AI 自动交易」文字 + 开关 + 运行时的「⏸ 暂停」/ 暂停态的「已暂停」徽标 + 「▶ 继续」+ 「结束」）。这四组动作在 AI 自动交易**未开启**时勉强放得下；一旦开启（更别说暂停态），簇内元素数量翻倍，而这一整行的可用宽度在面板收窄到 320px 时只有 **296px**（扣掉 `px-3` 左右各 12px）。

Flex 行本身没有 `flex-wrap`，所以溢出不会体现为「按钮换行」，而是体现为**每个按钮被压缩到内容宽度以下、其中文文案在字与字之间断开**——中文没有词边界，`white-space` 默认 `normal` 时可以在任意两个汉字之间折行，于是出现「新对 / 话」「暂 / 停」「AI 自动 / 交易」这种把一个词劈成两半的排版。这正是需求里说的「极其不美观」。这不是宽度不够用的必然结果，而是**信息架构与排版契约同时缺位**：一行里塞了两类语义的四组动作，且全仓没有任何一处声明过「按钮文案永不折行」。

本轮修法分两步，且**只动表现层**：其一，把工具栏按语义拆成**两条横栏**——顶部横栏留给「这个面板是什么 + 会话级动作」（连接点 / 名称 / 模型名 / 新对话 / 统计），把整簇 `AiTradeToggle` 下沉为一条**常驻的「AI 自动交易控制条」，贴在底部输入框（`AgentComposer`）的正上方**；其二，给这两条横栏建立一条明确的排版契约——**行级 `whitespace-nowrap` + 单一可截断文本**，让「折行」这件事在结构上不可能发生，宽度压力一律由指定的那一处文本以省略号吸收。控制条**无条件常驻**（不随运行态出现/消失）且行高被 `min-h-[24px]` 钉死（RV-2：只常驻还不够，idle 与 running/paused 的行内最高控件相差 4px），因此不产生任何布局跳动；在「执行轨迹」视图下输入框不渲染时，它自然落到面板底缘，两个视图里位置一致，用户不必重新寻找同一个开关。

方案不新增任何 REST / WebSocket 接口，不改 store 形态，不动后端与桌面主进程，不引入新依赖，不改变任何一个按钮的可访问名、`title` 文案与点击行为——因此既有 `AiTradeToggle.test.tsx` 的 **15 条**用例（`describe("AiTradeToggle")` 8 条 + `describe("AiTradeToggle 的三态")` 7 条；已逐条核对，全部以 `role` / `name` / `title` / 文本定位，无一处 `container.querySelector` / `parentElement` 之类的结构断言）与 `AiTradeAgentTrace.test.tsx` 中「AgentPanel segmented view」的 **5 条**用例应逐条保持绿色（RV-6）。

---

## 2. 现状与根因（Current state & root cause）

### 2.1 现状结构（已逐行核对）

`AgentPanel` 的渲染骨架（`apps/web/components/Agent/AgentPanel.tsx:313-396`）自上而下是：

```
<div class="flex h-full min-h-0 flex-col">
  <header class="flex items-center justify-between border-b … px-3 py-2">   ← 315-333：唯一工具栏
      左：● + 「AI 助手」 + {model}
      右：<Button>新对话</Button> + <AiTradeStatsButton/> + <AiTradeToggle/>
  </header>
  <AiTradeLifecycleNotice/>                                                  ← 335：生命周期持久提示
  {aiTradeGroup && <div role="tablist">对话 | 执行轨迹</div>}                ← 337-355：视图分段
  <div class="min-h-0 flex-1 …">…内容区…</div>                              ← 357-367
  {error && !showTrace && <div class="border-t …">错误条 + 重试</div>}       ← 369-382
  {!unconfigured && !showTrace && <AgentComposer …/>}                        ← 384-394：输入框
</div>
```

`AiTradeToggle` 自身的根节点（`AiTradeToggle.tsx:290-299`）是 `<div className="flex items-center gap-1.5" title={…}>`，其子节点依次为：文字标签「AI 自动交易」（300-308）、`<Switch/>`（311-316）、`running && <Button>⏸ 暂停</Button>`（317-329）、`paused && <><Badge>已暂停</Badge><Button>▶ 继续</Button><Button>结束</Button></>`（330-353）、`paused && error && <span class="max-w-[220px] truncate …">`（355-358），其后是四个对话框（360+，均经 `Modal` 的 `createPortal` 挂到 `document.body`，不占布局）。

### 2.2 根因三条

1. **语义混装**：一条横栏同时承载「身份 / 状态」与「会话动作」与「交易生命周期动作」。第三类在开启自动交易后从 1 个控件膨胀到 4 个，横栏没有任何伸缩预算。
2. **无不折行契约**：`components/ui/index.tsx` 的 `Button` 基类是
   `inline-flex items-center justify-center gap-1.5 rounded-[10px] font-medium …`，**不含 `whitespace-nowrap`**；`Badge` 同理。Flex item 的默认 `min-width:auto` 只保证不小于**最小内容宽度**，而中文的最小内容宽度就是**一个字**——所以压缩的结果必然是逐字折行，而不是横向溢出。
3. **无指定的压力吸收点**：模型名（`AgentPanel.tsx:322-324` 的 `<span class="text-[10px] text-[var(--muted)]">{config.model}`）没有 `truncate`，父容器没有 `min-w-0`，因此它既不截断也不让位，只会跟着一起挤压别人。

### 2.3 宽度预算（320px 极窄档，估算值，字号按 Tailwind 默认换算）

| 行 | 元素 | 估算宽度 |
|---|---|---|
| 顶栏左 | 连接点 6 + gap 8 + 「AI 助手」≈42 | **56**（模型名另计，可截断） |
| 顶栏右 | 「新对话」≈56 + gap 6 + 「统计」(icon+2字)≈62 | **124** |
| 顶栏合计（不含模型名） | | **180** / 可用 296 → 模型名可用 ≈**108px** ✓ |
| 控制条左 | 「AI 自动交易」≈66 + gap 6 + Switch 36 | **108** |
| 控制条右（idle） | — | **0** → 合计 108 ✓ |
| 控制条右（running） | 「⏸ 暂停」≈62 | **62** → 合计 ≈178 ✓ |
| 控制条右（paused） | 「已暂停」49 + gap 6 + 「▶ 继续」62 + gap 6 + 「结束」44 | **167** → 合计 ≈283 ✓（余 13px） |
| 控制条右（paused + 续跑请求在途） | 「已暂停」49 + 「正在核对持仓…」≈104 + 「结束」44 + gaps 12 | **209** → 合计 ≈325 ✗ → 由左侧标签截断吸收 |

结论：**两栏拆分后，除「暂停态且续跑请求在途」这一瞬态外，320px 下所有状态都不需要截断**；那一个瞬态的缺口约 **29px**，由左侧标签的省略号吸收——标签从 ≈66px 退到 ≈37px，实际显示约「AI 自…」而不是更长的形态（RV-10），完整语义由该行的 `title` 气泡兜底。**任何状态下都不会再出现折行**。

**行高预算（RV-2）**：控制条的行高由行内最高的控件决定——idle 态只有 `Switch`（`h-5` = **20px**），running / paused 态多出 `Button size="sm"`（`text-xs` 的 line-height 16px + `py-1` 上下各 4px = **24px**）。两者相差 4px：不钉死行高，开启 / 停止自动交易时控制条就会上下抖一格并连带推动输入框，正是 §4.1 用来否决「条件渲染」的那类缺陷、只是幅度小一档。因此 §4.4 的行容器**必须**带 `min-h-[24px]`。

---

## 3. 目标与非目标（Goals / Non-goals）

**目标**

- G1：`AgentPanel` 工具栏拆为两条横栏；第二条紧贴输入框上沿。
- G2：在 320–640px 全区间、idle / running / paused 全状态下，两条横栏中**没有任何一个按钮或标签发生折行**。
- G3：宽度不足时的退化路径是**确定的、单点的**（指定文本省略号），不是「谁先被挤谁倒霉」。
- G4：零行为回归——所有按钮的可访问名、`title` 文案、点击语义、弹窗行为、启停链路逐字不变。**唯一一处刻意的交互位移**（RV-4）是原生气泡的悬停范围：改前 `title` 挂在紧凑簇的根节点上，改后挂在通栏的**行容器**上（§4.4），覆盖面仍是「标签 + 开关 + 生命周期按钮」这一整组，不丢任何一颗按钮的气泡；代价是行内空白处悬停也会弹气泡。
- G5：明暗双主题下配色仍走既有 token，且不触碰 `contrastTokens.source.test.ts` 的既有对比度门禁。

**非目标（明确不做）**

- N1：不改 `components/ui/index.tsx`（`Button` / `Badge` / `Switch` 是全仓共用 kit，给基类加 `whitespace-nowrap` 会影响 60+ 调用点，收益与风险不成比例；本轮用**行级继承**达成同一效果，见 §4.2）。
- N2：不引入容器查询（`@container`）或 `ResizeObserver` 自适应。项目 Tailwind 为 **3.4.13 且未装 `@tailwindcss/container-queries` 插件**（`apps/web/tailwind.config.ts` 的 `plugins: []`），而 `sm:`/`md:` 断点跟的是**视口宽**、与可独立拖拽的面板宽无关，用了就是错的。行级不折行 + 单点截断是纯 CSS 的确定性解，无需测量。
- N3：不改 `AiTradeToggle` 的任何业务逻辑（mutation、门禁 `resolveAiTradeGate`、生命周期 `resolveAiTradeLifecycle`、弹窗装配）；本轮对该文件的改动**只在 `return (` 之后的 JSX 容器与类名**。
- N4：不动「对话 / 执行轨迹」分段行（`AgentPanel.tsx:337-355`）——它切换的是内容区，属于顶部语义，且 320px 下自身不溢出。
- N5：暂停态下 `Switch` 仍保留（禁用观感由 `opacity-40` + `noop` 表达）。它是「自动交易此刻是关着的」这一事实的视觉锚点，删掉会让暂停态与结束态长得一样；宽度压力不由删控件解决。

---

## 4. 技术设计（Technical design）

### 4.1 目标信息架构

```
┌─ AgentPanel ────────────────────────────────────────────┐
│ ● AI 助手  claude-sonnet-4-5      [新对话] [▮统计]      │ ← 横栏 A：身份 + 会话动作（既有 <header>）
├─────────────────────────────────────────────────────────┤
│ 对话 | 执行轨迹 ●                                        │ ← 既有分段行（仅分组存在时；不改）
├─────────────────────────────────────────────────────────┤
│                                                          │
│                    内容区（消息流 / 执行轨迹）            │
│                                                          │
├─────────────────────────────────────────────────────────┤
│ 连接中断，正在重连…                            [重试]    │ ← 既有错误条（仅出错时）
├─────────────────────────────────────────────────────────┤
│ AI 自动交易 ( ◯ )            [已暂停] [▶ 继续] [结束]   │ ← 横栏 B：新增「AI 自动交易控制条」（常驻）
├─────────────────────────────────────────────────────────┤
│ ┌────────────────────────────────────────────────────┐  │
│ │ 问点什么…                          [清除] [发送]    │  │ ← AgentComposer（紧贴控制条下沿）
│ └────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
```

**为什么这样切**（人机交互依据，非随意划分）：

- 横栏 A 回答「**这是什么、连着没有、跟谁说话**」+ 两个**会话级**动作。「新对话」与「统计」都是低频、非破坏性的，放在顶部符合「次要动作靠边站」的惯例；它们与输入框里的「清除」不重复（清除是清空当前会话上下文，新对话是换一个会话 id）。
- 横栏 B 回答「**这台机器现在跑不跑、要不要停**」。这是本面板里唯一的**有资金后果**的控件簇，把它放在**用户视线终点（输入框）正上方**，符合「关键状态与关键动作靠近操作焦点」的原则；同时它离顶部的「新对话」远了，**降低了误触概率**——「关开关」在 running 态是**立即下发停止并平掉自管持仓**（`AiTradeToggle.tsx` 的 `onToggle(false) → stopM.mutate`，无二次确认），它本就不该和「新对话」并排。
- 控制条**无条件常驻**：状态切换（idle→running→paused）只改行内右簇内容，**行本身不出现/消失**，因此内容区高度不跳动。这是比「仅在运行时显示」更好的选择——后者会在开启瞬间把整条消息流向上顶 38px。但**「常驻」本身还不够**（RV-2）：idle 态行内最高的是 20px 的 `Switch`，running / paused 多出 24px 的 `Button size="sm"`，不钉行高照样抖 4px。因此 §4.4 的行容器带 `min-h-[24px]`，把三态行高锁成同一个值——**这才是「零跳动」的兑现点**，常驻只是前置条件。
- 行内用 `justify-between`（状态在左、动作在右）而不是整体左对齐（RV-12）：640px 档下这确实会把「⏸ 暂停」推到离开关约 400px 外，但它换来的是**右侧动作列在整块面板里对齐**——顶栏的「新对话 / 统计」、控制条的生命周期按钮、输入框的「清除 / 发送」三者都贴着同一条 `px-3` 右边界，读起来是一条列而不是三处各行其是的漂浮物。左对齐会让这一行在宽档下与上下两行都不齐。**不要顺手改成左对齐**。

### 4.2 不折行契约（本方案的机械核心）

CSS 的 `white-space` 是**可继承属性**。因此只需在**两条横栏的行容器**上各写一次 `whitespace-nowrap`，其下所有后代文本（含 `Button` / `Badge` 内的文案）即全部不折行，**无需修改共用 kit，也无需在 6-7 个调用点逐个补类**。

继承沿 **DOM 树**传播、不沿 React 树（RV-9），所以这条 `nowrap` **不会漏进四个弹窗**：`Modal`（`components/ui/index.tsx:289`）在 `open` 为假时 `return null`，为真时 `createPortal` 到 `document.body`，弹窗子树的继承源是 `body` 而不是控制条。这正是敢用「继承式 nowrap」而不是逐点补类的前提——弹窗里那些必须自然换行的长文案（复核说明、孤儿仓告知）一个都不受影响。

压力吸收点则由「唯一一处 `min-w-0 + truncate`」显式指定：

| 横栏 | 行级声明 | 可截断项（唯一） | 不可压缩项 |
|---|---|---|---|
| A（header） | `whitespace-nowrap` | 模型名 `<span class="truncate">`（父 `min-w-0`） | 连接点 / 「AI 助手」/ 右簇（`shrink-0`） |
| B（控制条） | `whitespace-nowrap` | 标签「AI 自动交易」`<span class="min-w-0 truncate">` | Switch（kit 自带 `shrink-0`）/ 右簇（`shrink-0`） |

三条配套规则：

1. 左簇容器必须带 `min-w-0`。Flex item 默认 `min-width:auto`，不显式清零则 `truncate` 永远不会触发（这是 flexbox 截断失效的头号原因）。
2. 右簇容器必须带 `shrink-0`，保证「压力永远流向左侧那一处文本」，而不是随机压某个按钮。
3. 唯一需要**换行**的文本（暂停态的错误文案）显式写 `whitespace-normal` 覆盖继承值，并移出该行、独占第二行（§4.4）。

### 4.3 `AgentPanel.tsx` 改法（逐段）

**(a) 顶栏（替换 315-333 行）**

```tsx
      <header className="flex items-center justify-between gap-2 whitespace-nowrap border-b border-[var(--border)] px-3 py-2">
        <div className="flex min-w-0 items-center gap-2">
          <span
            className={`h-1.5 w-1.5 shrink-0 rounded-full ${connected ? "bg-[var(--long)]" : "bg-[var(--muted)]"}`}
            title={connected ? "已连接" : "连接中…"}
          />
          <span className="shrink-0 text-xs font-semibold text-[var(--text)]">AI 助手</span>
          {config?.model && (
            <span className="truncate text-[10px] text-[var(--muted)]" title={config.model}>
              {config.model}
            </span>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          <Button variant="ghost" size="sm" onClick={newConversation} aria-label="新对话">
            新对话
          </Button>
          <AiTradeStatsButton />
        </div>
      </header>
```

差异点：① 行上新增 `gap-2 whitespace-nowrap`；② 左簇加 `min-w-0`，连接点与「AI 助手」加 `shrink-0`；③ 模型名加 `truncate` 与 `title`（截断后仍可悬浮读全名）；④ 右簇加 `shrink-0`、`gap-2 → gap-1.5`；⑤ **移除 `<AiTradeToggle />`**（下沉到横栏 B）。`AiTradeStatsButton` 保留在顶栏，位置与外观不变。

**(b) 新增控制条（插入到 382 行错误条之后、384 行 `AgentComposer` 之前）**

```tsx
      {/* 横栏 B：AI 自动交易控制条。常驻贴在输入框正上方；「执行轨迹」视图下输入框不渲染时它
          自然落到面板底缘——两个视图里位置一致，用户不必重新寻找同一个开关。刻意不做条件渲染：
          随运行态出现/消失会在开启瞬间把整条消息流顶动 38px。 */}
      <div
        role="group"
        aria-label="AI 自动交易控制"
        data-testid="ai-trade-control-bar"
        className="shrink-0 whitespace-nowrap border-t border-[var(--border)] bg-[var(--surface)] px-3 py-1.5"
      >
        <AiTradeToggle />
      </div>
```

顺序刻意定为「错误条 → 控制条 → 输入框」：错误条是**瞬态**通知（连接中断 / 重试），控制条是**常驻**外壳，常驻外壳才有资格占住「紧贴输入框」这个位置；否则每次瞬断，控制条都会被顶开一格。

**(c) 其余不动**：`AiTradeLifecycleNotice`、分段行、内容区、错误条、`AgentComposer` 调用参数逐字不变。

### 4.4 `AiTradeToggle.tsx` 改法（只动 290-358 的容器与类名）

根节点由「单层 flex 行」改为「一列（行 + 可选错误行）」，行内再分左右两簇：

```tsx
  return (
    <div className="flex w-full min-w-0 flex-col gap-1">
      {/* 行容器承接**唯一一份** title（RV-4）：既有 6 处 findByTitle 只要属性还在文档里就能命中；挂在
          「行」而不是「左簇」，是为了保住改前的悬停覆盖面 —— 暂停态那段 title 恰恰是在解释「继续 /
          结束」这两颗按钮，挂到左簇会让它们悬停无提示。代价是行内空白处也会弹气泡：可接受，这一整行
          本来就是一个被 aria-label 命名过的语义分组。
          min-h-[24px]（RV-2）把三态行高锁成同一个值：idle 行内最高的是 20px 的 Switch，running /
          paused 多出 24px 的 sm Button，不钉死就会在开启 / 停止时抖 4px 并推动输入框。 */}
      <div
        data-testid="ai-trade-toggle-row"
        className="flex min-h-[24px] w-full min-w-0 items-center justify-between gap-2"
        title={
          paused
            ? "已暂停：不再开新仓，但盯市与保护层照常运行。点「继续」核对持仓后接着跑，或「结束」归档"
            : (openBlockedReason
              ?? (running ? "点击停止 AI 自动交易并平掉自管持仓" : "开启 AI 自动交易 / 回测"))
        }
      >
        {/* 左簇：标签 + 开关。全行唯一的压力吸收点。 */}
        <div data-testid="ai-trade-toggle-main" className="flex min-w-0 items-center gap-1.5">
          <span
            className={
              running || paused
                ? "min-w-0 truncate text-[11px] font-medium text-[var(--accent-ink)]"
                : "min-w-0 truncate text-[11px] text-[var(--muted)]"
            }
          >
            AI 自动交易
          </span>
          {/* 三态控件（§3.9.2）。`paused` 时开关保持 off 且**禁用**——避免「关开关」与「结束」两个
              入口语义重叠：暂停可逆、结束不可逆，二者绝不能长得像同一个动作。 */}
          <Switch
            checked={running}
            onChange={paused ? noop : onToggle}
            label="AI 自动交易开关"
            className={(openBlockedReason && !running) || paused ? "opacity-40" : undefined}
          />
        </div>

        {/* 右簇：生命周期动作。shrink-0 ⇒ 宽度压力一律流向左簇标签的省略号，按钮永不被压折。
            idle 态它是个空 div —— 刻意**无条件渲染**，让 T7 在三态下都有同一个落点，也让
            「shrink-0」这条契约在三态下都可被冻结。 */}
        <div data-testid="ai-trade-toggle-actions" className="flex shrink-0 items-center gap-1.5">
          {running && (
            <Button variant="ghost" size="sm" aria-label="暂停 AI 自动交易" onClick={…原样…}>
              ⏸ 暂停
            </Button>
          )}
          {paused && (
            <>
              <Badge tone="accent">已暂停</Badge>
              <Button variant="primary" size="sm" aria-label="继续 AI 自动交易" disabled={resumeM.isPending} onClick={openResume}>
                {resumeM.isPending ? "正在核对持仓…" : "▶ 继续"}
              </Button>
              <Button variant="ghost" size="sm" aria-label="结束 AI 自动交易" onClick={…原样…}>
                结束
              </Button>
            </>
          )}
        </div>
      </div>

      {/* 错误文案独占第二行：通栏里再挤一段 220px 截断文本，等于把「继续 / 结束」按钮挤没。
          whitespace-normal 显式覆盖上游继承的 nowrap（这是本方案里唯一需要换行的文本）。
          data-testid 是必需的：四个弹窗里的错误段落同样是 role="alert"，只靠 role 定位有歧义（RV-3）。
          触发面很窄，见下方「这一行什么时候才会出现」（RV-1）。 */}
      {paused && error && !resumeOpen && !endOpen && (
        <span
          data-testid="ai-trade-toggle-error"
          className="whitespace-normal break-words text-[10px] leading-relaxed text-[var(--short-ink)]"
          role="alert"
        >
          {error}
        </span>
      )}

      <AiTradePauseDialog … />   {/* 四个弹窗原样，Modal 经 createPortal 挂 body，不占布局、不继承 nowrap */}
      <AiTradeResumeDialog … />
      <AiTradeEndDialog … />
      <AiTradeLaunchModal … />
    </div>
  );
```

关键约束（下游实现时必须逐条守住）：

- `title` **只能存在一份**，且挂在**行容器**（`ai-trade-toggle-row`）上——不是根节点，也不是左簇（RV-4）。
  根节点上不得再留同名 `title`，否则 `screen.findByTitle("开启 AI 自动交易 / 回测")` 会因命中多个元素而抛错：
  `AiTradeToggle.test.tsx` 中该查询实有 **6 处**（`:112` / `:121` / `:148` / `:174` / `:204` / `:398`），
  另有 `:130` 一处查门禁文案「请先在设置中心配置 AI 模型」（RV-6）。
- `aria-label`、按钮文案、`Badge` 文案、`disabled` 条件、`onClick` 回调**逐字不变**。
- 行容器必须带 `min-h-[24px]`（RV-2）。右簇即使在 idle 态没有任何按钮也**必须渲染**那个空 `div`：
  T7 需要三态同一落点，且 `shrink-0` 这条契约本身要在三态下都成立。
- 三个 `data-testid`（`ai-trade-toggle-row` / `-main` / `-actions`）与 `-error` 是**排版契约用例的落点**，
  不是装饰（RV-3）：左右两簇是裸 `div`，既无 `role` 也无可访问名，`toHaveClass` 无从下手。形制与仓库里
  既有的 29 处 `data-testid`（如 `ai-trade-lifecycle-notice`、`ai-trade-pause-dialog`）一致。
- 错误 `<span>` 去掉 `max-w-[220px] truncate`，改为 `whitespace-normal break-words`；`role="alert"` 保留。
- `--short-ink` 所在行的背景是控制条的 `bg-[var(--surface)]`——该配对是 `app/globals.source.test.ts`
  对比度矩阵里的**在册项**（`{ fg: "--short-ink", bgs: ["--bg", "--surface", "--surface-2"] }`，暗 5.31 / 浅 6.52，
  均过 AA）。**不得**把控制条底色改成 `--accent-soft`：那个配对暗色实测只有 4.41，是矩阵的排除项。
  ⚠️ 但**别把这道防线记在既有门禁头上**（RV-7）：`contrastTokens.source.test.ts` 那条看守是**同一行**判据，
  而改后控制条底色写在 `AgentPanel.tsx`、`--short-ink` 写在 `AiTradeToggle.tsx`，跨文件跨行它抓不到。
  真正看住这一格的**只有**本轮新增的 S5。

**这一行（错误第二行）什么时候才会出现**（RV-1 —— 实现、用例与人工验收都依赖这条）：
`paused && error && !resumeOpen && !endOpen` 四个合取项里，`error` 的每一个产生点都伴随着一个**开着的弹窗**：

| `error` 产生点 | 产生瞬间的状态 | 错误实际落在哪 |
|---|---|---|
| `openResume()` 内 `resumePreview` 失败 | `resumeOpen` 已在**发请求之前**置 `true` | 续跑弹窗内（`AiTradeResumeDialog.tsx:125`，同样是 `role="alert"`） |
| `resumeM.onError` | `resumeOpen` 仍为 `true`（`onError` 不关框） | 同上 |
| `endM.onError` | `endOpen` 仍为 `true` | 结束弹窗内（`AiTradeEndDialog.tsx:139`） |
| `pauseM.onError` | 此刻分组还是 `running`，`paused` 为假 | 暂停弹窗内（`AiTradePauseDialog.tsx:105`） |
| `startM` / `startBacktestM.onError` | 暂停态下开关是 `noop`，这条路走不到 | — |
| `stopM` | 没有 `onError`，从不设 `error` | — |

而四个弹窗的 `onClose` 一律执行 `setError(null)`。**所以这一行唯一的到达路径是一个竞态**：暂停态下点
「▶ 继续」→ 在 `resumePreview` 请求**在途**时把弹窗关掉（Esc / 点遮罩 / 取消）→ 请求随后失败 →
`setError(...)` 落到一个已经没有弹窗承接的状态上，第二行这才出现。

这条路径**真实且值得保留**（它说的是「你关掉了那个框，但那次核对确实失败了」），本轮不改它的触发条件；
但它决定了 T8 与 R4 必须**按这个顺序**构造，不能按「让 `resumePreview` 失败」一句话了事。

### 4.5 渲染时序（无新增副作用）

本轮不新增 state / effect / 订阅。渲染路径仍是：

```
store.rightPanelWidth ──► page.tsx grid 列宽 ──► aside 宽 ──► RightPanel ──► AgentPanel
                                                                        │
    (纯 CSS，无 JS 参与)  flex 布局在同一帧内完成两栏排布与截断判定 ◄──┘
```

`AiTradeToggle` 的挂载位置从 `<header>` 变为控制条 `<div>`，但**始终挂载**（无条件渲染），因此其内部的 `useQuery`（accounts / agent config / aiTrade status，含 15s 轮询）挂载与卸载时机与改前**完全一致**，不产生额外请求、不会重置查询缓存。

---

## 5. 文件 / 模块变更计划（File / module change plan）

| # | 文件 | 类型 | 一句话意图 |
|---|---|---|---|
| 1 | `apps/web/components/Agent/AgentPanel.tsx` | 修改 | 顶栏加 `whitespace-nowrap` + 左簇 `min-w-0` / 模型名 `truncate+title` / 右簇 `shrink-0`，移出 `<AiTradeToggle/>`；在错误条与 `AgentComposer` 之间插入常驻的「AI 自动交易控制条」（`role="group"` + `data-testid="ai-trade-control-bar"`）。**顺带刷新 `:430` 那条分段行注释**（它以「与既有 AiTradeToggle 启停开关区分」为由说明分段行的位置，改完后开关已不在标题行）。 |
| 2 | `apps/web/components/Agent/AiTradeToggle.tsx` | 修改 | 根节点改为「一行 + 可选错误行」的通栏布局：行容器（承接唯一一份 `title`、`min-h-[24px]`、`justify-between`）/ 左簇（标签 `min-w-0 truncate` + Switch）/ 右簇（生命周期按钮，`shrink-0`，idle 态空 div 仍渲染）；错误文案独占第二行并显式 `whitespace-normal`。业务逻辑零改动。**同时刷新 `:34` 的组件头注释**「挂 AgentPanel 头部『新对话』右侧」——改完即为僵尸注释（RV-5）。 |
| 3 | `apps/web/components/Agent/AiTradeStatsButton.tsx` | 修改 | **仅注释**：`:8`「挂在 AgentPanel 头部 AiTradeToggle 左侧」在开关下沉后不再成立，改为「挂在 AgentPanel 顶栏『新对话』右侧」。零代码、零样式、零行为改动（RV-5；CLAUDE.md §四「删除一切僵尸注释」）。 |
| 4 | `apps/web/components/Agent/AgentComposer.tsx` | 修改 | 根节点补 `data-testid="agent-composer"`，供「控制条是输入框的紧邻前兄弟」这条位置断言定位（唯一改动，无样式/行为变化）。 |
| 5 | `apps/web/components/Agent/AgentPanelToolbar.test.tsx` | 新增 | 组件用例 T1–T6：控制条存在且紧贴输入框、轨迹视图下仍在且落底、顶栏不再含开关、两条横栏均带 `whitespace-nowrap`、模型名带 `truncate` 与完整 `title`、控制条以具名分组播报。 |
| 6 | `apps/web/components/Agent/AiTradeToggle.test.tsx` | 修改 | 追加 T7–T9：三态下的排版契约（行 `min-h-[24px]` / 左簇 `min-w-0` / 右簇 `shrink-0` / 标签 `truncate`）；暂停态错误第二行（按 §4.4 的竞态路径构造）；`title` 唯一性。既有 **15 条**用例一条不改。 |
| 7 | `apps/web/components/Agent/agentToolbarNoWrap.source.test.ts` | 新增 | 源码冻结门禁 S1–S6：两条横栏的 `whitespace-nowrap`、`<header>` 内不得再出现 `AiTradeToggle`、`AiTradeToggle.tsx` 不得再出现 `max-w-[220px]`、控制条行不得使用 `bg-[var(--accent-soft)]`。跨行匹配前先把 `\r\n` 归一化（仓库 `core.autocrlf=true`，否则干净检出上恒红）。 |
| 8 | `docs/plans/agent-panel-toolbar-two-rows/spec.md` | 新增 | 本文档。 |

**不涉及**：`components/ui/index.tsx`（共用 kit，见 N1）、`lib/store.ts`、`app/page.tsx`、`components/RightPanel/RightPanel.tsx`、任何 `services/quant/**`、任何 `desktop/**`。

---

## 6. 接口设计（Interface design）

**REST / WebSocket / CLI：无任何新增或变更。** 本轮是纯表现层重排，不触碰 `lib/aiTradeApi.ts`、`lib/agentApi.ts`、`lib/agentWs.ts` 的任何签名，后端 `services/quant` 零改动。

**组件接口（React props）契约变化如下：**

| 组件 | 变更前 | 变更后 | 兼容性 |
|---|---|---|---|
| `AiTradeToggle` | `() => JSX`，无 props；根节点为紧凑内联簇（`flex items-center gap-1.5`），宽度由内容决定 | `() => JSX`，仍无 props；根节点为**通栏块**（`flex w-full min-w-0 flex-col`），宽度由父容器决定 | **签名不变**；但**渲染契约变了**——调用方必须提供一个有确定宽度的块级父容器。全仓唯一调用方是 `AgentPanel`，本轮同步改。 |
| `AgentComposer` | 现有 7 个 props | 不变 | 完全兼容；仅根节点新增 `data-testid`。 |
| `AgentPanel` | 无 props | 无 props | 不变。 |
| `AiTradeStatsButton` | 无 props | 无 props | 不变，位置仍在顶栏。 |

**DOM 契约（供测试与后续维护定位，属对外可依赖的稳定标识）：**

| 选择器 | 含义 |
|---|---|
| `[data-testid="ai-trade-control-bar"]` | 横栏 B 的行容器；同时具备 `role="group"` 与 `aria-label="AI 自动交易控制"`。 |
| `[data-testid="agent-composer"]` | 输入框区根节点；控制条必须是它的 `previousElementSibling`（当它存在时）。 |
| `[data-testid="ai-trade-toggle-row"]` | `AiTradeToggle` 的单行容器：承接**唯一一份** `title`，带 `min-h-[24px]` 与 `justify-between`（RV-2 / RV-4）。 |
| `[data-testid="ai-trade-toggle-main"]` | 左簇（标签 + 开关）：全行唯一的压力吸收点，带 `min-w-0`。 |
| `[data-testid="ai-trade-toggle-actions"]` | 右簇（生命周期按钮）：带 `shrink-0`；idle 态为空但**仍然渲染**。 |
| `[data-testid="ai-trade-toggle-error"]` | 暂停态错误第二行；与四个弹窗里同为 `role="alert"` 的错误段落区分开（RV-3）。 |
| `role="switch"` + `aria-label="AI 自动交易开关"` | 自动交易开关，位置改变但可访问名不变。 |
| `role="button"` + name `暂停/继续/结束 AI 自动交易` | 生命周期按钮，可访问名不变。 |

---

## 7. 数据模型（Data model）

**无新增持久化字段，无 DB 迁移，无新增 store 键，无新增组件 state。**

本轮涉及的全部状态都是**既有的派生态**，读取点与推导点一处不变：

| 名称 | 来源 | 类型 | 用途 |
|---|---|---|---|
| `rightPanelWidth` | `useAppStore`（zustand persist，`lib/store.ts:107-109` 夹取到 320–640，默认 380） | `number` | 决定 `<aside>` 宽度；本方案**只被动适应它，不读取它**（纯 CSS 自适应，无 JS 分支）。 |
| `lifecycle` | `resolveAiTradeLifecycle(aiTradeGroup, aiTradeResumable)`（`AiTradeToggle.tsx` 内既有单点推导） | `"idle" \| "running" \| "paused"` | 决定控制条右簇渲染哪一组按钮。**推导点不变，任何组件不得自己拼这个判断。** |
| `openBlockedReason` | `running ? null : !agentConfigured ? "请先在设置中心配置 AI 模型" : null` | `string \| null` | 决定左簇 `title` 文案与 Switch 的 `opacity-40`。不变。 |
| `error`（`AiTradeToggle` 内） | 四个 mutation 的 `onError` | `string \| null` | 暂停态下渲染到控制条第二行。渲染位置变，产生与清除时机不变。 |
| `config.model` | `AgentPanel` 的 `ready` 事件 | `string \| undefined` | 顶栏可截断文本；新增 `title` 承载完整值。 |

**布局常量（新增，均为 Tailwind 类，不落 TS 常量）**：控制条 `px-3 py-1.5`（与顶栏 `px-3` 左右对齐，行高约 38px）；两簇间距 `gap-1.5`；行间距 `gap-2`。刻意不抽成 TS 常量——它们只在一处使用，抽出去反而制造间接层。

---

## 8. 可访问性与交互细节（A11y & interaction）

1. **可访问名零变化**：所有 `aria-label` / 按钮文案 / `Badge` 文案逐字保留，屏幕阅读器读出的内容与改前完全一致。
2. **新增分组语义**：控制条以 `role="group"` + `aria-label="AI 自动交易控制"` 包裹，读屏进入该区域时会先播报分组名，比改前「一堆散落在面板标题行里的按钮」更清晰。
   （措辞更正 RV-9：`AgentPanel` 的 `<header>` 位于 `<aside>` 之内，按 HTML-AAM **不**映射 `banner` 角色，只是一个通用 `header`——原文的「banner」说法会误导后续 a11y 走查。）
3. **Tab 顺序 = 视觉顺序**：DOM 顺序为 顶栏(新对话→统计) → 分段 tab → 内容区 → 错误条重试 → 控制条(开关→生命周期按钮) → 输入框(textarea→清除→发送)。控制条在输入框之前，符合视觉自上而下。
   **需要如实记下**：这确实改变了键盘用户到达开关的路径（改前开关在顶栏、约第 3 跳可达；改后落在内容区之后）。这是「把有资金后果的控件从低频会话动作旁边挪开」的必然代价，取舍偏向「键盘顺序与视觉顺序一致」而不是「保住旧的跳数」。
4. **焦点环不受影响**：`Button` 基类自带 `focus-visible:ring-2`，`Switch` 为原生 `<button role="switch">`，均未改。
5. **截断可读性**：模型名截断后新增 `title` 悬浮读全名；「AI 自动交易」标签仅在 §2.3 那一个瞬态下截断（约剩「AI 自…」），此时该行的 `title` 已完整说明控件语义。
6. **原生气泡覆盖面不缩水**（RV-4）：`title` 挂在**行容器**上，覆盖「标签 + 开关 + 生命周期按钮」整组，与改前根节点的覆盖面一致。若下沉到左簇，暂停态那段解释「继续 / 结束」的文案就恰好悬停不到它所解释的两颗按钮。代价是行内空白处悬停也会弹气泡——可接受：这一整行本来就是一个被 `aria-label` 命名过的语义分组。
7. **误触防护**：把「关自动交易」这个**立即生效、无二次确认**的开关从「新对话」旁边移到底部独立横栏，物理距离拉开；同时它与输入框内的「发送 / 清除」之间隔着 1px 分隔线与 `py-1.5` 内边距，不构成新的误触相邻对。
8. **无布局跳动**：控制条常驻**且行高由 `min-h-[24px]` 钉死**（RV-2），idle↔running↔paused 三态行高完全一致。仅当暂停态错误第二行出现时整条控制条增高一行、把输入框推低——与既有错误条的行为一致。
9. **动效**：不新增任何过渡；既有按钮 `transition-colors duration-150 ease-smooth` 保留。

---

## 9. 测试与验收标准（Testing & acceptance criteria）

### 9.1 自动化用例

**A. `apps/web/components/Agent/AgentPanelToolbar.test.tsx`（新增）** —— 桩化方式沿用 `AiTradeAgentTrace.test.tsx` 第 9-24 行（`vi.mock("@/lib/agentWs")` / `vi.mock("@/lib/agentApi")` / `vi.mock("./AiTradeToggle", () => ({ default: () => <div data-testid="ai-trade-toggle" /> }))`，并补 `Element.prototype.scrollIntoView = vi.fn()`）。

> 三条实施注（RV-8，缺了会卡住）：
> ① 桩掉 `AiTradeToggle` 之后，`AgentPanel` 子树里**不再有任何 `useQuery`**（`AiTradeStatsButton` 只有本地 `useState`，`AiTradeAgentTrace` 不发查询），因此**不需要** `QueryClientProvider`——既有的「AgentPanel segmented view」5 条用例正是这么跑的。
> ② T2 需要一个 `AiTradeGroupState` 夹具，而 `AiTradeAgentTrace.test.tsx` 的 `groupWith()`（第 43-70 行）**没有导出**；照它抄一份本地最小夹具即可，不要为此去改那个文件。
> ③ T5 要触发 `handleEvent` 的 `ready` 分支：`AgentPanel.tsx:244` 的调用是 `agentWS(conversationId, handleEvent, onConnState)`，回调是**第 2 个位置参数**——取 `vi.mocked(agentWS).mock.calls[0][1]`，在 `act()` 中投喂 `{ type: "ready", data: { configured: true, provider: "p", model: "claude-sonnet-4-5-20250929" } }`。

| 用例 | 断言 |
|---|---|
| T1 `renders the control bar directly above the composer` | `getByTestId("ai-trade-control-bar").nextElementSibling` === `getByTestId("agent-composer")` |
| T2 `keeps the control bar mounted in the trace view where the composer is hidden` | 置入 `aiTradeGroup` 后自动切到轨迹视图 → `queryByTestId("agent-composer")` 为 null，`getByTestId("ai-trade-control-bar")` 仍在，且是其父节点的**最后一个子元素** |
| T3 `no longer renders the auto-trade toggle inside the header` | `container.querySelector("header")!.querySelector('[data-testid="ai-trade-toggle"]')` 为 null，而全局 `getByTestId("ai-trade-toggle")` 存在 |
| T4 `freezes the no-wrap contract on both toolbar rows` | `header` 与控制条均 `toHaveClass("whitespace-nowrap")` |
| T5 `truncates the model name instead of squeezing the buttons` | 以 `ready` 事件注入长模型名后，该 `<span>` `toHaveClass("truncate")` 且 `title` 等于完整模型名 |
| T6 `announces the control bar as a labelled group` | `getByRole("group", { name: "AI 自动交易控制" })` 存在 |

**B. `apps/web/components/Agent/AiTradeToggle.test.tsx`（追加）**

| 用例 | 断言 |
|---|---|
| T7 `lays the three states out on a single fixed-height row` | idle / running / paused 三态下：`getByTestId("ai-trade-toggle-row")` `toHaveClass("min-h-[24px]", "justify-between")`；`getByTestId("ai-trade-toggle-main")` `toHaveClass("min-w-0")`，其内标签 `<span>` `toHaveClass("min-w-0", "truncate")`；`getByTestId("ai-trade-toggle-actions")` `toHaveClass("shrink-0")`（idle 态该 `div` 为空但必须存在） |
| T8 `moves the paused-state error onto its own wrapping line` | **必须按 §4.4 表下那条竞态路径构造**（RV-1）：把 `resumePreview` 桩成一个手控 deferred → 暂停态点「▶ 继续」→ 先断言此刻错误**不在**工具栏（`queryByTestId("ai-trade-toggle-error")` 为 null，它在续跑弹窗里）→ 关掉续跑弹窗 → `reject` 那个 deferred → `getByTestId("ai-trade-toggle-error")` 出现、`toHaveClass("whitespace-normal")`、**不含** `truncate` / `max-w-[220px]`，且带 `role="alert"` |
| T9 `keeps exactly one element carrying the gate title` | `screen.getAllByTitle("开启 AI 自动交易 / 回测")` 长度为 1（守住「`title` 只能存在一份」，K1） |

原有 **15 条**用例（`describe("AiTradeToggle")` 8 条 + `describe("AiTradeToggle 的三态")` 7 条，RV-6）**一条不改**，全部作为回归护栏：已逐条核对，它们只用 `role` / `name` / `title` / 文本定位，与容器结构解耦。

**C. `apps/web/components/Agent/agentToolbarNoWrap.source.test.ts`（新增，源码冻结门禁）** —— 形制对齐既有 `components/ui/warnToken.source.test.ts`：

| 用例 | 断言 |
|---|---|
| S1 | `AgentPanel.tsx` 的 `<header … className="…">` 属性串中含 `whitespace-nowrap` |
| S2 | `AgentPanel.tsx` 中 `data-testid="ai-trade-control-bar"` 所属容器的 `className` 含 `whitespace-nowrap` 与 `border-t` |
| S3 | `AgentPanel.tsx` 的 `<header …>` 到 `</header>` 之间**不含** `AiTradeToggle`（防止有人「顺手加回去」） |
| S4 | `AiTradeToggle.tsx` 中**不再出现** `max-w-[220px]` |
| S5 | 控制条那一行**不得**出现 `bg-[var(--accent-soft)]`（守住 `--short-ink` 的对比度排除项） |
| S6 | 上述文件确实被读到且非空（防止路径写错导致门禁空转变绿） |

> ⚠️ 仓库 `core.autocrlf=true`：**先做 `const src = text.replace(/\r\n/g, "\n")` 归一化，再匹配**——这是唯一不用逐条推敲的写法（RV-10 附带更正：原文举的 `/<header[\s\S]*?<\/header>/` 恰恰是**不需要**特判的那种，`[\s\S]` 本就含 `\r`；真正会栽的是 S1/S2 那种「属性串跨了几行、正则里写了字面 `\n`」的形态）。参照 `warnToken.source.test.ts` 与 `globals.source.test.ts` 的既有形制。

### 9.2 回归与门禁

```bash
pnpm -C apps/web typecheck          # tsc --noEmit
pnpm -C apps/web test               # vitest run（vitest 自身注入 NODE_ENV=test）
pnpm -C apps/web build              # next build，确认无 lint/type 阻断
```

若在自定义 shell 中显式导出过 `NODE_ENV`（非 `test`），组件用例会因 React 18 的 act() 环境判定而报错——跑前确认 `NODE_ENV` 为空或等于 `test`。后端 `pytest` 与桌面端 node 用例本轮**无需**跑（零改动），但最终提交节点仍按仓库惯例跑全量。

### 9.3 人工验收清单（必须逐项在真机勾掉）

拖动右侧面板分隔条到 **320px（最窄）/ 380px（默认）/ 640px（最宽）** 三档，在**明暗两种主题**下分别验证：

| # | 场景 | 期望 |
|---|---|---|
| R1 | 未开启自动交易（idle） | 顶栏一行，控制条一行；「新对话」「统计」「AI 自动交易」**均无折行**；模型名过长时以省略号收尾，悬浮显示全名 |
| R2 | 开启自动交易（running） | 控制条右侧出现「⏸ 暂停」；三个中文按钮全部**单行**；内容区高度未发生跳动 |
| R3 | 暂停态（paused） | 「已暂停」徽标 +「▶ 继续」+「结束」在同一行且**不折行**；点「继续」进入「正在核对持仓…」瞬态时，左侧标签以省略号退让（约剩「AI 自…」），按钮仍完整 |
| R4 | 暂停态、**续跑复核在途时先关掉弹窗**、随后复核失败（§4.4 表下那条竞态路径；RV-1 —— **不是**「让复核失败」那么简单：弹窗开着时错误只会落在弹窗里，工具栏第二行不会出现） | 错误文案独占控制条第二行，**自然换行、不截断**；「继续 / 结束」按钮不被挤压 |
| R5 | 切到「执行轨迹」视图 | 输入框消失，控制条落到面板底缘、仍完整可用；切回「对话」后控制条回到输入框正上方，两者之间只有 `AgentComposer` 自带的那条 1px 分隔线，**没有任何额外留白**（RV-10：原文「无空隙」措辞不准，分隔线是既有样式，本轮不动） |
| R6 | 未配置模型（unconfigured） | 控制条仍在，开关呈 `opacity-40`，悬浮提示「请先在设置中心配置 AI 模型」，点击不弹窗 |
| R7 | 键盘走查 | Tab 依次经过 新对话 → 统计 →（分段 tab）→ 开关 → 生命周期按钮 → textarea → 清除 → 发送；每一跳都有可见焦点环 |
| R8 | 桌面客户端 | 在打包后的 Electron 窗口中重复 R1–R3；确认标题栏 / WCO 区域不受影响（本轮不碰 `page.tsx` 顶栏，属零风险复核） |
| R9 | idle ↔ running ↔ paused 反复切换（RV-2） | 控制条**行高恒定**，消息流底缘与输入框顶缘**不上下抖动**（`min-h-[24px]` 是否真的生效，只有这一步能证明——jsdom 无布局引擎） |

**验收判据（Definition of Done）**：R1–R9 全绿 + 9.1 全部用例（T1–T9、S1–S6）绿 + 9.2 三条命令零错误 + `AiTradeToggle.test.tsx` 既有 **15 条**用例一条未改且全绿（RV-6）。

---

## 10. 风险与缓解（Risks & mitigations）

| # | 风险 | 影响 | 概率 | 缓解 |
|---|---|---|---|---|
| K1 | `title` 被同时留在根节点与行容器 → `findByTitle` 命中多个元素 | `AiTradeToggle.test.tsx` **6 处**该查询直接抛错 | 中 | §4.4 显式列为硬约束（唯一一份，挂在行容器）；T9 用例以 `getAllByTitle(...).length === 1` 冻结 |
| K2 | 忘记 `min-w-0` → `truncate` 不生效，极端瞬态下仍被压折 | 需求未真正解决 | 中 | S1/S2 源码门禁 + T7 类名断言；§4.2 把「flex 截断三件套」写成规则而非注释 |
| K3 | 后续有人给控制条底色改成 `--accent-soft`「让它更醒目」 | 暗色下 `--short-ink` 对比度掉到 4.41，落进矩阵排除项 | 低 | **只有新增的 S5 看得住**（RV-7）：既有 `contrastTokens.source.test.ts` 那条是**同一行**判据，而底色与墨色改后分处两个文件，它抓不到——不要误以为已有防线 |
| K4 | `AiTradeToggle` 改为 `w-full` 后被复用到别处（如设置面板）时撑满容器 | 布局失真 | 低 | 全仓唯一调用方是 `AgentPanel`（已核）；§6 表中显式声明「渲染契约变更：需块级定宽父容器」 |
| K5 | 控制条常驻挤占竖向空间（运行态叠加分段行后共 3 条外壳） | 消息流可视高度减少约 38px | 中 | 行高压到 `py-1.5` + `min-h-[24px]`（≈38px 含边框）；控制条是唯一新增外壳，换来的是「开关永远在同一个位置」；不做条件渲染以免布局跳动（N5/§4.1 已论证） |
| K6 | 担心搬动 `AiTradeToggle` 会让它 remount、15s 轮询重来 | 瞬时闪状态 | 低 | **不会发生，但理由要写对**（RV-11）：这不是运行期的父节点切换，而是一次**源码级**搬移——改完之后组件在新位置首次挂载，此后在两种视图下都**始终挂载**、从不进条件分支。（原文「React 只是换了父节点但仍在同一次 reconcile 中」是错的：跨父节点的位置变化本来就会卸载重挂；这里根本不存在那种变化。）真正要守的是「永远不要把控制条改成条件渲染」——一旦条件化，remount 与布局跳动会同时回来。T2 用例覆盖「轨迹视图下仍在」 |
| K7 | 中文按钮文案未来被加长（如「⏸ 暂停会话」） | 320px 下重新逼近溢出 | 中 | 行级 `nowrap` 保证**永远不折行**，最坏是标签截断；S1/S2 门禁保证该保护不会被误删；宽度预算表（§2.3）留在文档中供后续评估 |
| K8 | jsdom 无布局引擎，自动化用例无法真正证明「不换行」「不抖动」 | 门禁只证明契约存在、不证明像素结果 | 高（固有） | 明确分工：**自动化冻结契约**（类名 + 结构 + 位置），**人工验收证明像素**（R1–R9 三档宽度 × 双主题）；两者缺一不可，写进 DoD |
| K9 | 640px 档下 `justify-between` 把「⏸ 暂停」推到离开关约 400px 外，被后人「顺手改成左对齐」（RV-12） | 右侧动作列与顶栏 / 输入框错位，整块面板的对齐语言破掉 | 中 | §4.1 写明理由：三行的右簇共用同一条 `px-3` 右边界，读起来是一条列；改成左对齐会让控制条在宽档下与上下两行都不齐 |

---

## 11. 实施顺序（建议给下游开发节点）

1. `AgentComposer.tsx` 加 `data-testid="agent-composer"`（1 行，先做，让位置断言有落点）。
2. `AiTradeToggle.tsx` 重排容器（§4.4）：先改结构 → 跑 `pnpm -C apps/web test -t AiTradeToggle` 确认既有 **15 条**全绿（这是本轮最容易踩 `title` 唯一性的地方，先验证再往下走）。
3. `AgentPanel.tsx` 顶栏改类名 + 移出 `<AiTradeToggle/>` + 插入控制条（§4.3）。
4. 刷新三处会变成僵尸注释的说明（RV-5）：`AiTradeStatsButton.tsx:8`、`AiTradeToggle.tsx:34`、`AgentPanel.tsx:430`。**这一步不要留到最后**——它是纯注释改动，越晚做越容易被跳过，而 CLAUDE.md §四把僵尸注释列为必须清理项。
5. 补 `AgentPanelToolbar.test.tsx`（T1–T6）与 `AiTradeToggle.test.tsx` 追加用例（T7–T9）。T8 必须按 §4.4 表下那条竞态路径写，不要照「让 `resumePreview` 失败」的直觉写（RV-1）。
6. 补源码门禁 `agentToolbarNoWrap.source.test.ts`（S1–S6），匹配前先归一化 `\r\n`。
7. 跑 §9.2 三条命令；再按 §9.3 做三档宽度 × 双主题的人工走查，**R9（行高不抖）不能省**——它是 `min-h-[24px]` 唯一的证明手段。
8. 若实施中发现本方案的判断有误（例如某处宽度估算与真机偏差过大），**在本文件追加「实施过程发现的方案缺陷」一节**记录，不要静默改设计。

**回滚（RV-13）**：本轮是纯表现层改动，无 DB 迁移、无接口变更、无 store 形态变化，回滚粒度就是**整轮 revert 这一个提交**——`git revert` 后 UI 立即回到单条横栏，不存在任何需要反向迁移的持久化状态。若只想临时退掉「下沉」而保留「不折行」，把 `<AiTradeToggle/>` 移回 `<header>` 右簇即可（两条改动彼此独立），但那会让 320px 档重新逼近 §2.3 的溢出边界，仅作为应急手段。

---

## 12. 附录：为什么不选另外三种做法

| 备选 | 为什么不选 |
|---|---|
| 只给 `Button` 基类加 `whitespace-nowrap` | 治标：不折行了，但四组动作仍挤在一行，会改为**横向溢出/裁切**，同样难看，且改的是 60+ 调用点共用的 kit，风险面远大于收益。用户要的是「分成两栏」，不是「不折行」这一半。 |
| 溢出收进「⋯」更多菜单 | 把有资金后果的「暂停 / 结束」藏进二级菜单，违反「关键动作可见」原则；且需新增浮层原语（仓库无通用 Menu 组件），规模明显超出需求。 |
| 按面板宽度做响应式（容器查询 / ResizeObserver） | Tailwind 3.4.13 未装容器查询插件，`sm:` 断点跟视口宽而非面板宽（用了就是错的）；`ResizeObserver` 在 jsdom 下需额外桩、给测试增负。纯 CSS 的「行级 nowrap + 单点截断」已能在 320–640 全区间给出确定结果，无需测量。 |

---

**文档版本**：**v2**（Subtask #1 评审修订产出；v1 为 Subtask #0 方案设计产出）
**下游节点**：Subtask #2 代码开发 → Subtask #3 Review 与提交

---

## 评审结论（Review Verdict）

### 结论：**有条件通过**

主干设计经工作树逐条核对后成立，可以进入开发：「两条横栏 + 控制条下沉贴输入框」正面回应了需求原文；
「行级继承 `whitespace-nowrap` + 唯一 `min-w-0/truncate` 吸收点 + 右簇 `shrink-0`」是纯 CSS 的确定性解，
在 Tailwind 3.4.13 无容器查询插件、面板宽与视口断点无关的约束下，是这个仓库能拿到的最省也最稳的解法；
N1–N5 五条非目标（不动共用 kit、不引容器查询、不动业务逻辑、不动分段行、不删暂停态开关）逐条有据。
1 个 P0 与 5 个 P1 已在 v2 正文中直接改掉，7 条 P2 亦一并修订，**无遗留未处置的 P0 / P1**。

「有条件」指下面五条——它们不是重新设计，而是**开发与验收时必须照做**，漏掉任何一条，这轮修复都会
「测试全绿但缺陷仍在」或「测试红得莫名其妙」：

1. **T8 与 R4 必须按 §4.4 的竞态路径构造**（原 P0 / RV-1）。`openResume()` 是先开框再发请求，复核失败时错误
   由续跑弹窗里那段**同样是 `role="alert"`** 的段落渲染；工具栏第二行唯一的到达路径是「请求在途 → 关框 →
   随后失败」。照直觉写的 T8 会命中弹窗的 alert 或因多命中而抛错，R4 会被判成复现不出来。
2. **行容器必须带 `min-h-[24px]`**（原 P1 / RV-2）。「常驻」只解决了「行不出现/消失」，没解决「行内最高控件
   在三态间从 20px 变 24px」；不钉死行高，开关一开一关输入框就抖一格。R9 是它唯一的证明手段，不可省。
3. **`title` 挂在行容器、且全组件只有一份**（原 P1 / RV-4 + K1）。挂根节点会让 6 处 `findByTitle` 多命中而抛错；
   挂左簇会让暂停态那段解释「继续 / 结束」的文案悬停不到它所解释的两颗按钮。
4. **四个 `data-testid` 是契约的一部分，不是装饰**（原 P1 / RV-3）。左右两簇是裸 `div`、错误行与四个弹窗共用
   `role="alert"`，没有它们 T7 / T8 写不出来，排版契约就只剩口头约定。
5. **三处僵尸注释必须同轮刷新**（原 P1 / RV-5），其中 `AiTradeStatsButton.tsx` 在 v1 里根本不在变更清单上。
   CLAUDE.md §四把这条列为必须清理项，而「说明控件挂在哪」的注释一旦失真，正是下一个人做错判断的起点。

### 附带确认（评审中已核实，开发时可直接引用，不必重新求证）

- `Button`（`ui/index.tsx:27`）与 `Badge`（`:143`）基类确实不含 `whitespace-nowrap`；`Switch`（`:184`）自带 `shrink-0`。
- `Modal`（`:289`）在 `open` 为假时 `return null`、为真时 `createPortal` 到 `document.body`；四个弹窗全部经由它，
  因而既不占布局，也**不继承**控制条的 `whitespace-nowrap`。
- `AgentPanel` 是 `AiTradeToggle` 的全仓唯一调用方；`<aside>` 无内边距，320px 档 `px-3` 后可用宽正是 296px。
- `data-testid` 在本仓库已有 29 处生产代码用法（含 `AgentPanel.tsx:414` 的 `ai-trade-lifecycle-notice`），本轮新增
  与既有形制一致，不是新发明。
- `--short-ink` × `--surface` 是 `globals.source.test.ts` 对比度矩阵的在册项；`--short-ink` × `--accent-soft` 是排除项。
- CLAUDE.md 的「单行 ≤ 80 列」在 `apps/web` 无任何工具强制（无 prettier / eslint 配置），被改动的 `<header>` 行现状
  即 103 列；本轮按「与周边一致」处理，不夹带格式化改动。

---

## 实施过程发现的方案缺陷（Issues Found During Implementation · Subtask #2）

主干设计逐条落地，无需返工；下面两条是实施中实测到的**文档与事实不符**，都会让下一个节点空耗，
故按 §11 第 8 条记录在案（已按更正后的做法执行，未静默改设计）。

### I-1（P1，影响 §9.2 与下游 DoD）：「vitest 自身注入 NODE_ENV=test」不成立

§9.2 写的是「`pnpm -C apps/web test`（vitest 自身注入 `NODE_ENV=test`）」，并把 `NODE_ENV` 问题
归因为「若在自定义 shell 中显式导出过」。实测：**本仓库的开发 shell 就带着 `NODE_ENV=production`**
（`env | grep NODE_ENV` → `NODE_ENV=production`），而 **vitest 2.1.9 不覆盖已存在的 `NODE_ENV`**。
后果是裸跑 `npx vitest run` 时**每一条组件用例**都红在同一句上：

```
act(...) is not supported in production builds of React.
```

本轮直接命中：`AiTradeToggle.test.tsx` + `AiTradeAgentTrace.test.tsx` 一次跑出 55 条全红，与代码
改动毫无关系。**正确跑法（本轮全部验证均以此为准）**：

```bash
cd apps/web
NODE_ENV=test npx vitest run                 # 全量：173 文件 / 1955 用例全绿
NODE_ENV=development npx next dev -p 18761   # dev 同理，否则 globals.css 走不进 postcss，页面恒 500
npx tsc --noEmit                             # typecheck 不受 NODE_ENV 影响
npx next build                               # build 自带 production，无需干预
```

`next dev` 那一行是同一根因的另一半：`NODE_ENV=production` 下 Next 不加载 PostCSS/Tailwind 管线，
`@tailwind base;` 直接 `Module parse failed`、`GET / 500`。§9.2 只提了测试没提 dev，下一个节点若要
做 §9.3 的真机走查会先撞上它。

### I-2（P2，影响 §9.1 C 的 S3）：S3 的原始判据可以被「整簇删掉」骗过

S3 只断言「`<header>` … `</header>` 之间不含 `AiTradeToggle`」。这条在**有人把整簇控件删掉**时同样
是绿的——门禁本该守「它搬到了别处」，写出来却只守住了「它不在这儿」。实现里给 S3 补了一条配对
断言：`AgentPanel.tsx` 中仍须存在 `<AiTradeToggle />`。这与 S6（防止门禁空转变绿）是同一条理由的
延伸，不改变 S1–S6 的编号与语义。

### 附：本轮的实测排版读数（jsdom 之外的证据，供 §9.3 人工验收对账）

以 Chromium 实跑开发服务器、按 §9.3 的档位拖拽**真实分隔条**后量得（`getBoundingClientRect`）：

| 档位 / 状态 | 右栏宽 | 控制条高 | 行高 | 右簇按钮实测宽 | 控制条是否紧邻输入框 |
|---|---|---|---|---|---|
| 320px · running（轨迹视图） | 323 | 37 | **24** | 「⏸ 暂停」60 | 输入框不渲染，控制条落面板底缘 ✓ |
| 320px · running（对话视图） | 323 | 37 | **24** | 「⏸ 暂停」60 | 是（`nextElementSibling` 即 composer）✓ |
| 320px · paused | 323 | 37 | **24** | 「▶ 继续」58 +「结束」44 | 是 ✓ |
| 640px · paused | 640 | 37 | **24** | 同上 | 是 ✓ |

三态行高恒为 24px（`min-h-[24px]` 生效，RV-2 的 R9 由此得到一份机器读数，但**不替代**真机双主题
走查）；按钮均为自然内容宽，无压缩、无折行；顶栏「新对话」56 /「统计」62 亦未被压缩。
