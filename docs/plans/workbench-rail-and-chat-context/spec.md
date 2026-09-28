# 工作台导航整理与 AI 助手上下文清空设计

版本：v2；日期：2026-09-28；阶段：方案评审完成，交付后续开发节点。

## 评审记录

本轮按可行性、完整性、一致性与范围适度逐节对照当前工作树、根目录 `CLAUDE.md` 和项目索引。评审只修改本文，不修改实现代码，不提交 Git。以下“已修订”指设计正文已闭合，不代表实现或测试已经通过。本轮未发现 P0；发现 7 项 P1、3 项 P2，均已在正文明确处理。

| 编号 | 级别 | 证据与问题 | 正文修订与验收 | 状态 |
| --- | --- | --- | --- | --- |
| RV-01 | P1 | `ui/index.tsx::Modal` 在 window 捕获阶段消费 Esc；`SimBalanceField` 依赖输入框冒泡阶段 Esc 取消。直接嵌入会抢走编辑取消语义 | §3.3 增加可选 Esc 委托，优先取消资金草稿；补文件清单与 N11、N12 | 已修订 |
| RV-02 | P1 | Modal 默认 `max-w-2xl`、`max-h-[85vh] overflow-hidden`，追加 `max-w-sm` 不保证覆盖，长说明或短窗口内容会被裁剪 | §3.3 明确宽度覆盖、可滚动正文、固定关闭区、当前项初始焦点；N13 | 已修订 |
| RV-03 | P1 | `AgentPanel` 在轨迹或未配置视图隐藏 Composer，唯一停止按钮随之消失；清空禁用提示缺少可执行路径 | §4.1 增加条件停止入口；C15 覆盖断线与视图切换 | 已修订 |
| RV-04 | P1 | §4.4 要求每次 effect 复位，§4.3 又要求同 ID 保留状态；StrictMode 重放及初始空 ID 的闭包会造成重复初始化 | §4.2–4.4 将会话状态边界与连接作用域分开，初始化读取即时 store；C12、C16 | 已修订 |
| RV-05 | P1 | `onConnState` 只在 true→false 报错，新会话首次连接失败仍是 false→false；原表格承诺错误反馈但无实现落点 | §4.5 明确首连失败与等待反馈；C17 | 已修订 |
| RV-06 | P1 | 原 C03 用笼统 delta 而非真实事件名，缺少同 tick 外部 ID 切换及 send/stop/updater 所有权验收 | §4.3、§4.4 与 C03、C18 补齐全部事件及状态写入边界 | 已修订 |
| RV-07 | P1 | tooltip 规定点击关闭、hover/focus 保持，却没有关闭锁；Modal 关闭恢复焦点会重新显示 tooltip，可能悬于对话框上方 | §3.2 增加 dismissed 状态、事件合并及 Esc 处理；N05、N14 | 已修订 |
| RV-08 | P2 | 清空提示以分组存在推断“自动交易继续运行”，暂停/停止分组同样存在 | §4.1 使用不依赖运行态的准确文案；C14 | 已修订 |
| RV-09 | P2 | 第一节与末尾仍描述设计节点、任务索引0；缺少布局回退对上下文隔离的约束 | §8.3、§10 修正评审交付口径、索引1及上线回退边界 | 已修订 |
| RV-10 | P2 | 规范要求文件≤800行、方法≤50行，而仅说 hook“函数短小”，无法约束搬迁后的大函数 | §4.2 与§7给出拆分边界和评审门禁，不扩展为全仓重构 | 已修订 |

逐节结论：§1–2 的范围与现有前后端机制一致；§3 的账户常驻和方形布局可行，已补 Modal/tooltip 边界；§4 的新 ID 隔离可行，已补生命周期和停止可达性；§5 无需协议或持久化迁移；§6–8 已同步落点和验收；§9–10 已明确残余验证限制与回退方式。共享黑板上的历史问题不能替代本次源码证据。

**目标：** 将账户模式、主题与设置集中到左侧导航底部，统一导航按钮的正方形几何，并让右侧助手提供明确、可靠的清空上下文操作。

**架构：** 沿用现有 Next.js 页面外壳、Zustand 状态和 Agent 会话协议。布局组件只调整入口，账户校验仍由原组件负责；交互式助手通过更换会话 ID 和拦截过期回调建立新的上下文边界。

**技术栈：** 仓库锁定 Next.js 14.2.15、React 18.3.1、TypeScript、Tailwind 3、Zustand 4、TanStack Query 5；后端为 FastAPI，桌面壳为 Electron。没有新增依赖、数据库迁移或后端协议变更。

## 一、概述

这次调整解决工作台全局操作分散和助手上下文操作不够明确的问题。设置、账户模式、主题属于全局工作环境，应集中在常驻左栏的底部；顶部保留品牌、拖动手柄和系统窗口操作。主功能导航继续承载交易、因子实验室、策略与因子挖掘，以及受总闸控制的交易日志。按钮采用统一的正方形命中区域和现有赭色选中样式，让选中、悬停、键盘焦点三种状态有稳定的视觉层次。

右侧助手已有“新对话”和输入框旁的“清除”，但目前的清除逻辑只切换 ID、后台尝试删除旧会话，没有完整清理输入草稿、重试文本和连接镜像，也没有阻止旧 REST 响应或旧 WebSocket 回调作用于新会话。本方案将两个重复入口合并成标题栏里明确可见的“清空上下文”。清空的产品含义是结束当前对话上下文、从空白对话继续，既包括可见消息，也包括后续模型使用的历史消息。它不等于删除后台历史记录，更不涉及自动交易的决策记忆、交易日志、市场情报或真实持仓。

本文包含上游方案与本轮评审修订，不修改实现、不提交 Git。上游已读取根目录 README.md；本轮对照 CLAUDE.md、项目索引及相关源码，以实际实现作为功能基线。工作区已有大量暂存和未暂存修改，特别是交易日志接线；后续节点必须在当前内容上增量实施，不覆盖这些修改。任务文件另列的 JRAgentMesh 安装目录属于只读参考，不是本方案的产品源码修改位置。

## 二、现状、根因与范围

### 2.1 已核实的代码路径

| 路径与符号 | 当前机制 | 本次影响 |
| --- | --- | --- |
| `apps/web/app/page.tsx::Home` | `appControls` 同时渲染账户模式、主题、设置和窗口键 | 前三项迁移；窗口键和标题栏安全区保留 |
| `apps/web/components/Nav/NavRail.tsx::NavRail` | `w-12`、左右 `px-2`，按钮只有 `h-11` | 实际按钮宽约 31px、高 44px，正是长方形的来源 |
| `apps/web/components/Header/AccountModeSwitch.tsx` | 账户查询、实盘校验、持久化水合回落、提示及模拟资金编辑都在同一组件 | 不能只在打开弹窗时才挂载整个组件 |
| `apps/web/components/Settings/SettingsGearButton.tsx` | 写 `showSettings`，保留当前主视图 | 保留状态行为，增加导航栏外观 |
| `apps/web/components/Agent/AgentPanel.tsx` | 持有消息、草稿、WS、断线续流、REST 补偿及新对话/清除动作 | 抽出会话职责并统一清空入口 |
| `apps/web/components/Agent/AgentComposer.tsx` | `onClear` 控制底部“清除”，运行中禁用 | 移除这个重复入口与必填属性 |
| `apps/web/lib/store.ts::newConversation` | 生成新会话 ID，`partialize` 持久化 ID | 直接复用，不增加持久化字段 |
| `services/quant/app/agent/loop.py::AgentSession.load_from_db` | 只按本会话 ID 查询历史并重建模型消息 | 新 ID 没有旧历史，可以建立真正的新上下文 |
| `services/quant/app/agent/manager.py::clear` | 删除数据库消息，再从内存会话表移除实例 | 没有先取消并等待运行任务，不能宣称在途删除可靠 |
| `apps/web/lib/ws.ts::ReconnectingWS` | 关闭 socket 后仍可能有已排队事件与状态回调 | 助手组件必须自行识别过期连接 |

### 2.2 冻结的产品范围

1. 左栏底部从上到下为“账户模式”“主题”“设置”；设置必须是最后一个操作。
2. “明色暗色交易”按现有全局明暗主题切换理解，不增加交易模式。
3. 账户模式仍是 `sim | live`。它不等于 OKX 账户自身的模拟环境标记，不修改既有两者关系。
4. 不改账户凭证规则、下单确认、自动交易启停、模拟资金数值边界及主题持久化策略。
5. 清空只作用于交互式聊天会话；系统提示、当前标的和周期等工作台上下文会照常注入新提问。
6. 本轮不提供历史永久删除、历史会话浏览、多标签页同步清除，也不增加服务端取消确认协议。
7. 本轮所有新增产品文案为中文，采用当前 CSS 设计变量和内联 SVG，不新增图标库。

### 2.3 方案选择

推荐“窄轨道 + 账户弹窗 + 新会话隔离”。直接把横向账户选择和资金字段塞进左栏，会扩大栏宽或者挤出不可读的小字；将所有操作藏在一个总菜单里又会让设置和主题多一层查找。因此保留三个独立入口，仅将需要解释和编辑空间的账户内容放进现有 Modal。

上下文采用新 ID 隔离，拒绝“只清空消息数组”：后者下一次请求仍会加载旧历史。也不沿用“先切换、再静默 DELETE”：那会混淆清空上下文和永久删除，且现有后端运行任务可能继续落盘。后台历史保留不是异常，而是本次明确定义的行为；界面说明必须如实表达。

## 三、技术设计：工作台布局

### 3.1 外壳布局与几何

`Home` 的顶部仍为 48px 标题行加独立 1px 分隔线。删除 `appControls` 中的 AccountModeSwitch、主题 Button、SettingsGearButton，右侧 `no-drag` 容器仅留下 WindowControls。删除不再使用的导入及 `toggleTheme` selector，保留图表所用 `theme`。同步改写这里关于“顶栏应用控件”的旧注释，不移除 `macSafeArea`、`winOverlay`、`win-titlebar-safe`、标题双击逻辑以及最左侧唯一的 `drag-region`。

NavRail 改为 `w-14`（56px）、`shrink-0`、`min-h-0`、纵向 flex、上下 12px 内边距，左右不再用 `px-2` 挤压按钮。内部区域 `items-center`；按钮明确为 `h-11 w-11 shrink-0`，宽高均 44px，圆角 12px。56px 栏宽包含右边框，按钮在剩余宽度中居中，因此不依赖恰好对齐的 padding 算式。本次有意增加 8px 栏宽，现有冻结 48px 的测试必须改成新契约。

轨道分为两个容器：上部主导航为 `min-h-0 flex-1 overflow-y-auto overflow-x-hidden`，内部按钮列间距 4px；下部工具区为 `mt-auto shrink-0`，顶部 1px 分隔线及 8px 间距，三个入口间距 4px。给工具区 `role="group"`、`aria-label="工作台设置"`。常规窗口下工具区贴底；短窗口下只滚动主导航，工具区不被主导航挤走。支持浏览器 860×600 起的工作台验收，并额外在 860×400 验证短高退化。

主导航键、顺序、ID、`journalEnabled` 过滤、`aria-current`、点击关闭设置再切换视图的规则保持原样。设置打开时主导航不显示选中，设置自身显示选中。工作区保态依然使用原 `hidden` wrapper，不把 flex/grid 类放到带 hidden 的 wrapper，也不通过切换 key 重新挂载图表或自动交易组件。

### 3.2 共用正方形按钮

新增 `Nav/RailIconButton.tsx`，只处理几何、视觉、焦点与文字提示，不读 store、不发网络请求。使用 `forwardRef<HTMLButtonElement>`，公共属性继承原生按钮属性并增加：`label: string`、`active?: boolean`、`indicator?: boolean`。children 为现有图标或账户标识，type 默认 button，aria-label 使用 label；原生 `aria-current`、`aria-pressed`、`aria-expanded` 可由调用方传入。

固定视觉：未选中为 `--muted`，hover 为 `--surface-2` 背景与 `--text`；选中为 `--accent-soft` 背景与 `--accent-ink`。仅主导航使用原 3×20px 左指示条；账户状态不以导航选中样式表达。键盘用 2px `--accent` 的 focus-visible outline，outline-offset 为 2px。图标一般为 20×20，描边沿用 1.6px，`aria-hidden=true`。不使用 `transition-all` 改变尺寸，过渡限于颜色、背景和透明度，150ms；减少动态效果沿用全局规则。

上部容器会滚动，原先绝对定位的提示气泡会被裁掉。因此提示通过 React portal 放到 `document.body`，`position:fixed`、z-index 60、与按钮右缘相距 8px、垂直居中；提示文字不可折行，背景 `--surface`，边框 `--border`，前景 `--text`。mouseenter 或 focus 显示，mouseleave 或 blur 关闭；任一来源仍激活则保持。打开时读取按钮 rect 和气泡尺寸，纵向位置夹在距视口边缘 8px 以内。捕获阶段监听 scroll、监听 resize 时关闭提示，避免滚动后悬空；cleanup 移除监听。点击也关闭提示。提示 `aria-hidden=true`，名称只由按钮 aria-label 提供，避免重复朗读。没有 hover/focus 时不创建 portal，SSR 阶段不访问 document。

RV-07 补充确定的关闭语义：增加 dismissed 锁；点击、Escape、滚动、resize 均置锁，即使按钮仍有焦点也关闭。下一次独立 mouseenter 或 focus 才解锁；账户按钮 `aria-expanded=true` 时始终不显示 tooltip。气泡使用 `pointer-events-none`，不遮挡账户 Modal。内部 onFocus/onBlur/onClick/onKeyDown 与调用方回调合并，不能被 props 展开覆盖；Escape 仅关闭气泡，不擅自阻止 Modal 的关闭事件。RailIconButton 的 className 合并不能覆盖44px几何。验证打开账户弹窗后没有遗留 portal、关闭恢复焦点后提示位置正确。

### 3.3 账户模式入口与弹窗

给 AccountModeSwitch 新增可选 `placement?: "inline" | "rail"`，默认 inline，保留原使用方式。NavRailControls 永久渲染 `<AccountModeSwitch placement="rail" />`。账户查询以及两个水合/提示 effect 都留在组件顶层，不能移动到 Modal 子树，不能因 `open=false` 停止执行。

rail 入口使用 RailIconButton：label 为“账户模式：模拟盘”或“账户模式：实盘”，`aria-haspopup="dialog"`，`aria-expanded` 对应 open。按钮中以 10px 中文显示“模拟”或“实盘”，上方是 16px 钱包图标。实盘文字使用 `--short-ink`、背景 `--surface`；模拟使用 `--text`。显式文字保证状态不只依赖颜色。触发键不直接翻转账户模式，避免误点进入实盘。

点击打开现有 Modal，标题“账户模式”，宽度使用 `max-w-sm`，内部 padding 16px。Modal 提供 portal、焦点陷阱、Esc/遮罩关闭和焦点恢复，无需自己实现浮层定位。内容按“模式分段选择 → 可见提示 → 模拟资金（仅 sim）→ 关闭”纵排；模式 radio 文案分别为“模拟盘”“实盘”，不缩写。默认焦点落当前模式按钮。保留原 radiogroup/radio 语义，rail 版本补齐方向键/Home/End 在两项间移动焦点并尝试选择，点击与键盘使用同一校验函数，不绕过守门。

RV-02 落地约束：Modal 实际从 `components/ui/index.tsx` 导出，没有独立 Modal.tsx。调用方用 `!max-w-sm` 覆盖其默认 `max-w-2xl`，以计算样式确认宽度≤384px且不超过视口留白。提供 `labelledBy` 与对应标题 ID，通过既有 `initialFocusRef` 指向当前模式按钮。标题和关闭区 `shrink-0`，中间内容 `min-h-0 overflow-y-auto p-4`，保留关闭按钮可见。当前模式 radio 为 tabIndex=0，另一项为-1；方向键移动焦点且通过同一 select 函数尝试切换，失败不伪造 aria-checked。Tab 离开分段选择后仍能访问资金和关闭区。不要仅用 autoFocus 猜测 Modal 初始焦点。

Modal 当前 FOCUSABLE 的 `button:not([disabled])` 仍会收集 tabIndex=-1 的按钮，因此焦点陷阱取得候选后须过滤 `element.tabIndex >= 0`，初始自动查找也使用同一候选辅助函数；显式 initialFocusRef 保持优先。否则选中实盘时 Shift+Tab 会错误地把不可 Tab 到的模拟项认作首项，漏掉首尾环绕。N13 必须从实盘 radio 按 Shift+Tab 到关闭键，再 Tab 回实盘，并验证切换/拒绝切换后的环绕；保留共享 Modal 既有调用方测试。

RV-01 资金编辑兼容：给共享 Modal 增加可选 `onEscapeKeyDown?: (event: KeyboardEvent) => void`，只在栈顶处理 Escape 时、现有关闭逻辑之前调用；若回调执行 `preventDefault()`，本层不关闭也不停止事件传播，使事件继续到输入框。未传属性的调用方保持原行为。AccountModeSwitch 仅当事件目标是本账户弹窗内的“模拟资金”输入框时 preventDefault，复用输入框已有 cancel；首次 Esc 取消草稿且保留弹窗，第二次 Esc 关闭弹窗并还焦点。不可只在 JSX 上 stopPropagation，因 window 捕获已经更早执行。以非默认金额验证取消不提交，并保留 Enter/blur 原提交路径；不修改 SimBalanceField 解析或数值边界。Modal 现有嵌套栈、普通 Esc 及焦点测试必须继续通过。

查询状态及动作规定如下：

| 查询/账户状态 | 点击实盘后的状态 | rail 弹窗反馈 |
| --- | --- | --- |
| pending | accountMode 不变 | “正在校验账户…请稍候后再切换。” |
| error | 不改变模式；发起一次 refetch | 完整显示现有失败文案，允许再次点实盘 |
| success，无默认账户 | 不改变模式 | 显示配置原因，并提供“前往账户设置” |
| success，凭证不全 | 不改变模式 | 显示凭证不全原因，并提供同一设置入口 |
| success，默认账户就绪 | 调用既有 setAccountMode("live") | 保持弹窗打开，radio 和栏上文字立即反映实盘 |

rail 版本的缺配置分支只显示原因，不立即打开设置；用户点击“前往账户设置”才按顺序关闭弹窗、设 `settingsSection="okx-accounts"`、设 `showSettings=true`。inline 版本保留原自动引导行为，兼容原测试。这样原因不会随着弹窗突然消失。rail 的 notice 使用 `role=status`、`whitespace-normal`，不 truncate。

切回模拟仍依次调用 `setAccountMode("sim")` 与 `setMode("paper")`，并清理 notice。模拟资金复用 SimBalanceField，不复制其解析、夹取和“下次启动生效”逻辑。关闭弹窗后重新打开反映 store 当前值；不要无依据提交第二次资金修改。弹窗打开期间切换主题或背景设置不应重置账户查询。关闭弹窗时仍验证失效实盘账户会回落模拟，这是本次移动最关键的回归断言。

### 3.4 主题和设置

新增 NavRailControls：仅组合 AccountModeSwitch、主题 RailIconButton、SettingsGearButton。主题直接读取 `theme`、调用 `toggleTheme`，label 与提示固定为目标动作“切换到暗色主题”或“切换到亮色主题”；light 显示月亮、dark 显示太阳，均为内联 SVG。

SettingsGearButton 增加同名 placement 可选参数，inline 为现状，rail 通过 RailIconButton 显示20px齿轮；保留 `id="settings-gear"`、`aria-label="设置"`、`aria-pressed` 与 `setShowSettings(!showSettings)`。再次点击关闭设置，返回之前主视图；设置中心任何子页也可以使用主题或账户操作。不要把设置写成 WorkbenchView 新枚举项。

## 四、技术设计：清空助手上下文

### 4.1 入口与准确语义

标题行原“新对话”按钮替换为“清空上下文”，`aria-label` 同文，显示小型擦除图标和完整文本；继续使用 ghost/sm。AiTradeStatsButton 保留。AgentComposer 删除 onClear 属性与“清除”按钮，输入和发送/停止保持原样。这是一个清晰入口，不保留两个名字含义重叠的动作。

标题按钮始终存在：断线、未配置模型、执行轨迹页均可看见。交互式聊天正在生成时禁用，title 为“请先停止当前回答，再清空上下文”；空白会话也允许点击，避免判定消息是否加载完成引入隐性不可用状态。禁用依据是聊天 running，不能使用 aiTradeAnyRunning；自动交易正在运行并不妨碍开启新聊天。若 running 且 Composer 因轨迹或未配置视图被隐藏，在标题下方独立状态行显示“对话仍在生成”与“停止当前回答”按钮，接同一 stop；断线也能使用该本地停止动作。Composer 存在时沿用其停止按钮，不再增加标题横排宽度，不移动自动交易控制条。

正常 title 为“开始空白对话；历史记录保留，自动交易不受影响”。点击直接生效，不增加确认弹窗，因为操作不调用永久删除接口。成功后显示 `role=status` 的文案：“上下文已清空，下一次提问将开始新的对话。历史记录保留，自动交易状态不受影响。” 不以分组存在推断其仍在运行。此提示在首次被接受的新发送时清除，空输入、超长输入或离线拦截不清提示，不使用自动消失定时器。它与错误条、AiTradeNoticeBar 分开，不能复用自动交易生命周期 notice。

若当前显示执行轨迹，清空后切到 chat；不复位 `autoSwitchedRef`，否则同一分组会马上把用户带回轨迹。用户之后可以继续切回轨迹，原分组、成员、统计和轨迹对象均保持。清空草稿属于清空当前聊天的一部分，不能继续保留或重试旧文本。

### 4.2 会话 hook 和职责边界

新增 `Agent/useAgentConversation.ts`，把 AgentPanel 里的聊天状态、WS effect、send/stop/retry、续流及 REST 补偿迁入 hook；AgentPanel 只保留聊天/轨迹展示、分组自动切页与标题/输入组件接线。保持公开函数短小，事件分派、状态复位、连接初始化各为独立函数，避免给已有大组件继续堆分支。

hook 无参数，返回：messages、config、connected、running、error、input、setInput、canRetry、send、stop、retry、clearContext、contextNotice。config 复用 `agentApi.ts` 已导出的 AgentConfig 类型。`clearContext(): boolean` 表示是否接受动作；运行中返回 false，其余返回 true。AgentPanel 只在 true 时 `setPanelView("chat")`。send 接收字符串；stop/retry 无参数。hook 内从 store 读取会话 ID 和 newConversation，不迁移任何自动交易状态。所有改变运行态的路径统一使用内部 `updateRunning(boolean)`，同步更新 runningRef 与 React running；不能只在 send 更新 ref 而漏掉 done/error/resumed/stop。

首次挂载 ID 为空时继续调用 newConversation；初始化 effect 执行当下再次读取 `useAppStore.getState().agentConversationId`，仍为空才生成，不能仅检查渲染闭包。已有 ID 时不额外换 ID。StrictMode 可以 connect/close/connect，但任意时刻只有最新 scope 有写权，不能把工厂调用恰好一次作为断言。不要通过 AgentPanel 的 key 强制整棵右侧树重挂载，因为其中包含自动交易组件。

hook 抽取遵守 CLAUDE.md 的文件≤800行、方法≤50行、形参≤5、圈复杂度≤10约束；hook 主体也按函数检查。状态/refs 声明、连接生命周期、发送动作、事件分类各有小 helper 或内部 hook，不把原400余行组件的事件与状态原样塞入单个函数；必要时在同一 useAgentConversation.ts 内拆辅助函数，只有超过文件阈值才新增模块并同步第六节。此门禁只检查本轮新增或改动的函数，不借机重写全仓。

### 4.3 连接作用域：阻止旧响应进入新上下文

每一次 WS effect 建立一个唯一对象作为作用域，包含 id、递增 generation、disposed；保存在 activeScopeRef。当前性判断必须同时满足：对象未 disposed、对象与 activeScopeRef.current 相同、对象 id 等于 `useAppStore.getState().agentConversationId`。最后一条用 store 的即时值，不能只依赖滞后一帧的 React effect 镜像。

所有 WebSocket onEvent/onState 都由当前 effect 包装后传给 agentWS，先判作用域，再调用已有事件处理器。过期 ready/history/delta/done/error/resumed/heartbeat 一律无操作；尤其旧 ui_action 不得修改标的、周期、图表或导航。不能只在 reduceAgentEvent 周围设闸，其他副作用同样属于旧上下文。

这里 delta 特指协议中的 `text_delta` 和 `thinking_delta`；覆盖还包括 `message_start`、`message_end`、`tool_call`、`tool_result`。send 的乐观消息 updater 同样捕获并复核 scope；stop/retry/send 不得从旧闭包取 ID 后操作当前 ws。外部 store 更换 ID 到 effect 执行之间，三个动作均先用即时 store 检验作用域，不写任何旧会话状态。clearContext 在无 scope 的首连等待期仍可换新 ID。

REST 补偿在调用 messages(id) 之前捕获当前 scope；await 成功后再次判当前性，且在 setMessages 的函数式 updater 内再判一次，才 reconcileHistory。失败仍沿用现有“等待下次权威 history”的策略，不新增旧会话错误提示。WS 对消息的函数式 updater 也用相同闸，避免已排队 updater 在切换后才执行。卸载时 disposed 让所有未结束 REST 请求失去写权，不要求修改共享 REST 客户端签名。

effect cleanup 顺序固定为：标记本 scope disposed；若仍是 activeScope 才清空 activeScopeRef；关闭本 effect 捕获的 ws；仅当 wsRef.current 仍指向这个实例时才清空它。不能无条件把后来建立的新 wsRef 置 null。底层 ws.ts 不改动，本 hook 负责自己的所有权边界。

同 ID 的网络自动重连不新建 React scope，因此仍保留已有流式气泡、lastSeq 与 resume 机制；真正更换会话 ID 才重置。connection generation 不能用于替代服务端事件 seq，两者含义不同。

### 4.4 清空动作的固定顺序

1. 使用同步 runningRef 再检查一次运行态，返回 false 时不改任何东西；按钮 disabled 不是唯一守卫。send 在发出请求之前同步置 runningRef=true，防止同一 tick 双击发送或清空。
2. 使 activeScope 立即 disposed 并解除当前性，同步取出并清空 wsRef，再关闭取出的旧 ws；旧 close 回调也会被作用域闸拦住。
3. 同步复位连接镜像与所有聊天 refs，再复位 React 状态：消息为空、草稿为空、错误为空、config=null、connected=false、running=false、lastUserText 为空、lastSeq=0、streaming=false、pendingGapReconcile=false、canRetry=false。WS 尚未真正重开前不能继承旧 connected=true。
4. 调用既有 newConversation 生成并持久化新 ID；不调用 agentApi.clear，不发任何交易端点请求。
5. 写入上节成功提示并返回 true；panel 切回 chat。新 ID effect 建立新 scope 和新 socket，连接成功后才允许发送；新 ready 决定配置可用性，新 history 为空。
6. 下一条 user_message 使用新 socket 和新 ID，仍根据当前 store 构造 AgentUiContext；旧草稿、旧重试字符串不能出现于其 payload。

状态复位 helper 在清空和真正更换会话 ID 时复用；用 `initializedConversationIdRef` 区分会话初始化和连接 effect 重建。清空先同步复位，再调用 newConversation，并将即时新 ID 记为已初始化；新 effect 不重复清草稿或提示。外部 ID 改变也执行完整复位。同 ID 的 effect cleanup/setup（含 StrictMode）仅令旧 scope 失效、断开并重置连接态，保留 messages、running、streaming、lastSeq、草稿和重试信息，等新连接沿用 resume 协议；实际卸载后局部状态自然释放。普通发送在所有守卫通过后清除 contextNotice。send 在原有输入守卫之外，必须检查当前 scope、connectedRef 和 wsRef，且用 runningRef 拦同 tick 重复发送，不能只信任闭包里的 connected/running。retry 读取已清理的 lastUserTextRef，stop 只向仍属于当前 scope 的 socket 发送。旧清除逻辑的 best-effort DELETE 及其注释必须删除；agentApi.clear 本身保留供独立历史删除用途，本次不改变端点。

stop 继续发送现有 cancel 并结束本地 running，不对外承诺服务端任务已经停止。stop 后立刻清空安全性的依据是新旧 ID 隔离及不删除旧会话，而不是 cancel 已确认。断线时 cancel 可能无法送达，旧服务端任务沿用既有 detach 宽限和 drain 流程；已执行的工具副作用不会被清空撤销。此限制不妨碍新会话不继承旧对话。

### 4.5 必须保持的续流行为

迁入 hook 时沿用原 reducer：history 按权威数据库记录对账；断线有 streaming 气泡时保留并提示；重连且存在 streaming 时发送 resume(lastSeq)；resumed 的 completed/cancelled/none 分支终结本地运行；gap 分支等待下一 done 再 REST 对账；只有带 seq 的 run 事件推进 lastSeq。16000 字输入上限、离线禁止乐观追加、show_chart/show_factor 的当前会话行为均保留。

首连反馈不能照搬原 `was && !open` 条件：当前 scope 收到 onState(false) 时，即使此前从未连接，也显示“连接失败，正在重试…”，并保留新 ID。尚无状态回调（包括底层构造失败自动重试）时显示“正在连接…”；不得虚构服务端错误原因。open 后清除此连接错误，ready 再更新模型配置。streamingRef 随消息事件同步维护，不能仅等 messages effect 才更新，否则同 tick 的 message_start→断线会丢失续流判据；函数式消息 updater 内保持纯状态计算，不写 refs。C11 增加此时序断言。

### 4.6 状态转换表

| 状态/输入 | 行为 | 必须成立 |
| --- | --- | --- |
| 空白/已有消息，点击清空 | 新 ID，空草稿，断开旧连接 | 旧消息不再参与模型请求 |
| 聊天 running，点击清空 | 按钮禁用，handler 返回 false | 不改 ID，不丢正在显示的回答 |
| 点击停止后马上清空 | cancel 为尽力发送，立即换新上下文 | 不 DELETE，不把停止等同服务端完成 |
| 断线但非 running，点击清空 | 本地换 ID，继续连接新会话 | 不因离线而偷偷回退旧 ID |
| 新会话连接失败 | 保持空白和新 ID，显示既有断线反馈 | 不显示旧 connected/model/error 状态 |
| 执行轨迹页点击清空 | 回聊天，分组对象不变 | 不清交易记忆、不停交易 |
| 旧 history/REST/ui_action 迟到 | 作用域验证失败，丢弃 | 无消息污染、无图表或导航跳转 |

## 五、接口设计与数据模型

### 5.1 网络接口

| 接口 | 既有签名及本次使用 |
| --- | --- |
| WS | `/api/ws/agent/{conversation_id}`，浏览器继续经 agentWS 连接后端；换 ID 新建连接 |
| WS 客户端帧 | `user_message` 携带 `{text, ui}`；`cancel`；`resume` 携带 `{lastSeq}`，全部保持原协议 |
| WS 服务端帧 | ready/history、流式消息、工具事件、ui_action、resumed/done/error/heartbeat，格式保持原样 |
| REST 历史 | 浏览器 `GET /api/py/agent/conversations/{id}/messages?limit=200`，服务端对应 `/api/agent/...`；只用作当前会话对账 |
| REST 删除 | 既有 `DELETE /api/py/agent/conversations/{id}` 返回204；清空上下文本次不调用它 |
| 账户查询 | 原 `accountApi.accounts.list` 和 `['okx','accounts']` queryKey 不变；全应用只有一份实际 AccountModeSwitch 实例 |

不新增 CLI、REST、WebSocket 字段或后端错误码。clearContext 返回本地动作是否被接受，不伪装成数据库清理成功回执。

### 5.2 前端组件接口

| 组件/函数 | 确定接口 |
| --- | --- |
| NavRail | 原 `{journalEnabled?: boolean}` 不变 |
| NavRailControls | 无 props，读现有 store 并组合三个入口 |
| RailIconButton | 原生 button props 加 label、active、indicator；转发 ref |
| AccountModeSwitch | `{placement?: 'inline' | 'rail'}`，默认 inline |
| SettingsGearButton | `{placement?: 'inline' | 'rail'}`，默认 inline |
| useAgentConversation | 无参数，返回第四节所列状态与动作 |
| AgentComposer | 移除 onClear；value/onChange/onSend/onStop/running/disabled 不变 |

### 5.3 内存形状与持久化

新增内部 `ConversationScope` 形状为 `{id: string, generation: number, disposed: boolean}`；generation 仅当前挂载期间单调递增。另以 initializedConversationIdRef 记录已复位的会话 ID。状态提示为 `string | null`；账户弹窗 open 为 boolean；tooltip 维护 hover/focus/dismissed boolean 和浮层坐标。这些全部是组件局部状态，不进入 Zustand partialize、localStorage 或数据库。Modal 仅新增可选原生 KeyboardEvent 回调，不改变默认 Esc 行为。

既有 `agentConversationId` 继续是持久化 string；`theme`、`accountMode`、`activeView` 继续沿用原存储；showSettings 保持瞬态。无需升级 store 的迁移版本，也不改 AgentMessage/ChatMessage/AgentUiContext 协议。后台历史仍归属于旧 ID，新 ID 读取不到它；不宣称本方案具有数据擦除或隐私删除能力。

### 5.4 不变式

- G1：所有主导航和底部工具的命中框均为44×44px；栏宽56px。
- G2：AccountModeSwitch 始终挂载一次，关闭弹窗不关闭账户校验。
- G3：设置为左栏最后一个按钮；顶部只保留窗口控制。
- G4：清空聊天不得修改任何 aiTrade/liveAuto/paper 会话对象或交易设置。
- G5：只有当前 scope 可以写消息、连接状态、错误、seq 或触发 ui_action。
- G6：清空同时清理草稿、重试文本、resume 锚点和流式镜像。
- G7：清空不发送 DELETE；新模型历史为空，但当前工作台上下文仍可用。
- G8：设置覆盖和主视图切换不卸载原工作台，现有 hidden 契约保持。

## 六、文件 / 模块变更计划

下表是后续开发节点的完整计划。本次设计评审仅修改 spec.md，没有改动表中的实现文件；工作区其他任务的已有修改仍须保留。

| 文件 | 动作 | 单一意图 |
| --- | --- | --- |
| `docs/plans/workbench-rail-and-chat-context/spec.md` | 新建，后续更新 | 本设计、评审结论与实际验证记录 |
| `apps/web/app/page.tsx` | 修改 | 移除顶部三个应用控件，保留窗口与拖拽约定 |
| `apps/web/components/Nav/NavRail.tsx` | 修改 | 主导航/底部工具分区及56px轨道 |
| `apps/web/components/Nav/RailIconButton.tsx` | 新建 | 统一44px按钮、焦点及portal提示 |
| `apps/web/components/Nav/NavRailControls.tsx` | 新建 | 账户、主题、设置三个常驻工具入口 |
| `apps/web/components/Header/AccountModeSwitch.tsx` | 修改 | 增加rail呈现和账户弹窗，保留常驻查询与守门 |
| `apps/web/components/ui/index.tsx::Modal` | 修改 | 增加可选 Esc 委托及tabIndex过滤，默认Esc行为不变 |
| `apps/web/components/ui/Modal.test.tsx` | 修改 | 验证 Esc 委托、默认关闭、嵌套栈与焦点契约 |
| `apps/web/components/Settings/SettingsGearButton.tsx` | 修改 | 增加rail外观，保持设置切换语义 |
| `apps/web/components/Agent/useAgentConversation.ts` | 新建 | 集中聊天生命周期、清空与异步作用域守卫 |
| `apps/web/components/Agent/AgentPanel.tsx` | 修改 | 使用hook，标题清空入口、提示与轨迹切换 |
| `apps/web/components/Agent/AgentComposer.tsx` | 修改 | 移除底部重复清除入口和onClear属性 |
| `apps/web/components/Nav/NavRail.test.tsx` | 修改 | 方形/分区/设置顺序及原导航行为回归 |
| `apps/web/components/Nav/RailIconButton.test.tsx` | 新建 | 提示portal、hover/focus、清理及按钮语义 |
| `apps/web/components/Nav/NavRailControls.test.tsx` | 新建 | 工具顺序、主题切换、设置开关、账户入口 |
| `apps/web/components/Header/AccountModeSwitch.test.tsx` | 修改 | 在原inline用例外覆盖rail全状态和关闭期守门 |
| `apps/web/app/page.test.tsx` | 修改 | 保留日志门控测试，增加顶栏/左栏接线断言 |
| `apps/web/components/Agent/useAgentConversation.test.tsx` | 新建 | 清空状态、过期回调、REST竞态与续流行为 |
| `apps/web/components/Agent/AgentPanelToolbar.test.tsx` | 修改 | 标题清空、单入口、轨迹隔离、原两横栏布局 |
| `apps/web/components/Agent/AgentComposer.test.tsx` | 新建 | 移除清除后发送、停止、键盘行为保持 |

明确不计划修改：store.ts、agentTypes.ts、agentApi.ts、agentWs.ts、ws.ts、SimBalanceField.tsx、全局CSS、desktop源码及后端源码。现有 `agentToolbarNoWrap.source.test.ts` 仍能检查 AgentPanel 标题和控制条，保持不改；`contrastTokens.source.test.ts` 已递归扫描新TSX，无需增加文件白名单。通用 noWrapContract 的受管文件清单不覆盖本次导航，不要误称新增文件自动受该清单全部约束。

## 七、实施顺序与验证命令

1. 保存所涉文件当前 diff 作为人工对照，读取本 spec 的评审修订版。确认没有覆盖工作区其他功能。使用现有依赖，不升级包。
2. 完成 RailIconButton 与工具区、NavRail 分区，再把设置/主题迁移。同步修改48px尺寸断言和旧tooltip结构断言。
3. 扩展 AccountModeSwitch rail 分支，再接入 NavRailControls，最后删除 Home 顶部旧实例，确保运行时只有一份守门。
4. 先写旧事件/旧REST会污染新会话的可重复用例，再迁出聊天hook、加入scope闸和统一复位。
5. 接入标题清空，删除新对话/底部重复入口；完成提示、轨迹页切回和运行态守卫。
6. 跑定向单测和类型检查；通过后跑一次前端全量测试与构建，再做浏览器与桌面人工验收。失败按证据修复，不删除原有断言来掩盖回归。

所有命令从仓库根目录单独执行，检查退出码后再执行下一条，不使用PowerShell不支持的 `&&`。下列命令属于后续开发验证，本设计阶段没有执行它们，也不据此宣称功能已通过。

```text
pnpm --dir apps/web exec vitest run components/Nav/NavRail.test.tsx components/Nav/RailIconButton.test.tsx components/Nav/NavRailControls.test.tsx components/Header/AccountModeSwitch.test.tsx app/page.test.tsx
pnpm --dir apps/web exec vitest run components/ui/Modal.test.tsx components/ui/ConfirmDialog.test.tsx
pnpm --dir apps/web exec vitest run components/Agent/useAgentConversation.test.tsx components/Agent/AgentPanelToolbar.test.tsx components/Agent/AgentComposer.test.tsx components/Agent/agentToolbarNoWrap.source.test.ts components/Agent/AiTradeAgentTrace.test.tsx lib/agentReducer.test.ts lib/agentActions.test.ts
pnpm --dir apps/web run typecheck
pnpm --dir apps/web test
pnpm --dir apps/web build
```

NavRail 原测试直接render，无 QueryClientProvider；引入常驻账户查询后，更新其render helper加入每用例独立 QueryClient，关闭retry并桩accountApi。不能在生产组件里吞掉缺Provider错误。page.test 的窗口、账户和设置替身现在返回null，新增接线断言前将替身改成可定位的最小按钮；账户替身透传placement以证明是rail调用，不把组件本身彻底mock掉后假测业务校验。助手测试从mock agentWS每次调用取独立的event/state回调，能在清空后人为重放旧回调。

## 八、测试与验收标准

### 8.1 自动测试矩阵

| 编号 | 场景 | 必须断言的结果 |
| --- | --- | --- |
| N01 | 四个主导航及日志总闸 | ID、标签、选中切换及false时隐藏日志保持 |
| N02 | 尺寸和结构 | 栏w-14，全部Rail按钮h-11/w-11/shrink-0，底部顺序账户→主题→设置 |
| N03 | 设置往返 | 设置打开主导航无aria-current；关设置仍是之前视图 |
| N04 | 主题切换两次 | store主题往返、目标动作label随之变化，账户与视图不变 |
| N05 | tooltip被滚动区域包围 | portal实际挂在body；hover/focus可见，滚动/resize/卸载清理 |
| N06 | 账户pending/error/无默认/凭证不全/就绪 | rail各分支与第三节一致；错误触发refetch，无乐观实盘 |
| N07 | 弹窗关闭、持久化live、查询返回无默认 | 仍自动回落sim，证明守门不是懒挂载 |
| N08 | 模拟资金编辑 | 存在可达入口，修改写原store；重开不丢已提交值 |
| N09 | 缺账户到设置 | 原因先可见，点设置CTA后关闭弹窗并定位okx-accounts |
| N10 | 外壳接线 | header没有账户/主题/设置，窗口占位仍在；nav中各一份 |
| N11 | rail资金编辑，输入不同金额后连续两次Esc | 第一次取消草稿，金额未改、Modal仍在；第二次关闭且焦点回入口 |
| N12 | Modal可选Esc委托 | 不传时原关闭语义不变；preventDefault后输入框收到Esc；嵌套只顶层处理 |
| N13 | rail弹窗焦点与结构 | live/sim各自初始焦点及Tab首尾环绕正确；方向键选择受账户校验；正文可滚动、关闭区不收缩 |
| N14 | tooltip点击/Esc/模态切换 | 点击后即使焦点仍在也关闭；Esc关闭；账户Modal展开时无tooltip；调用方事件仍执行 |
| C01 | 有history、草稿、错误和重试文本后清空 | 新ID，空消息、输入、错误，canRetry=false，connected=false |
| C02 | 清空后新发送 | 新ws接收user_message，旧ws没有新发送；payload不含旧草稿 |
| C03 | 清空后旧WS逐项发ready/history/message_start/text_delta/thinking_delta/tool_call/tool_result/message_end/done/error/resumed/heartbeat | 消息、running、error、model、seq均不变；高seq旧帧不污染新resume锚点 |
| C04 | 清空后旧WS发show_chart/show_factor | instId、bar、runId、activeView及factor状态均不变 |
| C05 | gap→done启动REST，清空后旧REST成功返回 | 不合并旧消息；新会话消息不被替换 |
| C06 | 新scope已连接，再触发旧onState(false) | 新connected仍true，没有旧断线提示 |
| C07 | running时连点清空、或send后同tick清空 | ID不变，clearContext返回false |
| C08 | stop后立即清空、取消尚无done | 不调用agentApi.clear，旧done不能改变新会话 |
| C09 | 无网/未配置模型 | 按钮仍存在；非running可清空，保持新ID等待重连 |
| C10 | 自动交易running且在轨迹页 | 清空按钮可用，回chat，分组对象/交易状态不变 |
| C11 | 普通同ID断线重连 | streaming保留、resume lastSeq正确、gap后REST正常对账 |
| C12 | StrictMode、卸载、再挂载 | 仅当前连接有写权；卸载后迟到REST不更新状态 |
| C13 | 标题和输入框 | 恰好一个“清空上下文”，无“新对话”/底部“清除”重复入口 |
| C14 | 清空提示与新发送 | 成功说明历史保留；暂停/停止分组不显示继续运行；成功接受发送才清提示；轨迹通知条不被清除 |
| C15 | 聊天生成中切轨迹/未配置视图并断线 | 停止当前回答始终可达；stop后清空成功，零交易端点调用 |
| C16 | 初始空ID StrictMode与同ID effect重放 | 只生成一次ID；旧scope失权；同ID重建不清已有草稿/seq/流式态 |
| C17 | 清空后新WS首连失败false→false | 新ID保持，显示连接失败重试提示，后续open清除此错误 |
| C18 | store外部改ID但effect尚未运行；排队旧发送updater | 旧WS事件和send/retry/stop不写状态、不发帧；旧updater不能追加消息 |

测试使用真实store和可控制Promise/WS回调，不接真实交易所或模型；不依赖时间sleep猜测网络竞态。新hook测试至少以C05和C06验证修复前失败、修复后通过。既有agentReducer/agentActions测试用于确认迁移没有改变正常事件语义。

### 8.2 浏览器与桌面视觉验收

在亮色、暗色分别检查1440×900、1024×768、860×600和860×400；将右侧面板拖到代码允许的最小宽度，再检查长模型名。使用getBoundingClientRect实测每个导航和工具按钮宽高均44px，容差0.5px。截图应同时包含顶栏、完整左栏与右侧助手；DOM类名通过不能替代实际尺寸验证。

账户入口可见当前模拟/实盘文字；所有按钮不拉伸、不逐字换行。主导航上部可滚动，底部设置保持可达；提示不被rail裁剪。账户弹窗完整显示失败原因和模拟资金，不遮住自己的关闭按钮；Esc关闭后焦点回到账户入口。Tab/Shift+Tab遍历所有工具，Enter/Space触发，focus ring清楚可见。主题切换后tooltip与Modal颜色同步。

右侧标题“清空上下文”和统计入口不挤压换行，模型名独占可截断区域；成功提示允许正常中文换行。清空后显示空态，刷新仍连接新会话；下一次提问不能复述测试前的独特历史口令。这里用测试口令证明会话隔离，不涉及真实私密数据。

Windows Electron必须分别在还原和最大化状态实点三个左栏入口及窗口最小化/最大化/关闭按钮，并测试品牌区拖动和双击。浏览器/jsdom无法证明OS标题栏命中区正确，不能拿单测代替这项验收。macOS/Linux若本轮无对应环境，明确记录未验证，不写“跨平台已通过”。

### 8.3 完成条件

方案评审通过后，开发交付必须满足G1–G8、N01–N14、C01–C18，定向单测、类型检查、全量前端测试和构建均有实际输出；视觉和Windows人工项有记录。既有失败须提供未修改基线复现，明确区分，不能统一归为本功能通过。源码不越界修改交易业务；没有新增依赖，没有修改store迁移版本，没有永久删除聊天历史。评审节点的完成条件独立：仅本文发生本节点编辑、标记v2、包含评审记录和评审结论、无未解决P0/P1，并成功向任务工具报告索引1完成。

## 九、风险与缓解

| 风险 | 成因 | 缓解与验证 |
| --- | --- | --- |
| 账户水合守门消失 | 把整个AccountModeSwitch藏进条件Modal | 顶层常驻，N07在弹窗从未打开时验证 |
| 旧回答或操作污染新上下文 | WS关闭与REST返回都是异步 | scope三条件守卫覆盖事件、状态和updater，C03–C06 |
| “清空”被误解为永久删除 | 旧实现实际尝试DELETE，名字含糊 | 产品tooltip与成功提示明确历史保留，C08断言零DELETE |
| 自动交易被意外重置 | 给整面板换key或复用自动交易notice | 只迁聊天hook，不重挂载控制条，C10检查对象身份 |
| 模拟资金入口丢失 | 迁移时只搬两个radio | 在rail弹窗保留SimBalanceField，N08 |
| 实盘缺配置反馈不可见 | 打开设置同时关闭包含原因的弹窗 | rail先显示原因，用户再点击设置CTA，N09 |
| 44px按钮被布局压扁 | flex默认收缩或栏宽仍48配padding | 显式双尺寸和shrink-0，真实几何验收 |
| 提示被裁剪或泄漏事件监听 | 主导航变成滚动容器 | portal提示、打开期监听、cleanup，N05 |
| 桌面点击失效复发 | 修改顶栏拖拽/系统三键安全区 | 保留原契约，Windows双状态实点 |
| hook抽取破坏断线续流 | 把scope generation误作事件seq或每次重连清空 | 同ID重连保留refs，C11 |
| 旧取消仍在执行 | cancel是协作请求而非完成回执 | 不删除旧会话，不承诺撤销工具副作用；新旧scope隔离 |
| 工作区其他修改被覆盖 | page/NavRail已有日志功能改动 | 以当前工作树为基线，保留日志门控，精确审阅diff |
| 大文件/复杂度继续增加 | 将所有布局与会话细节塞回原组件 | 按表拆成工具按钮、工具区、会话hook；不扩展无关重构 |

## 十、设计交付检查与后续记录

本设计以现有代码读取为依据，没有运行实现阶段的测试、构建或桌面验证。本轮已在本文顶部增加评审记录并修订为v2；开发节点依照第六节落地。实施中若发现需要修改清单之外文件，应先在本节记录实际原因和接口影响，再同步清单、验收与风险，不默默扩大范围。

上线与回退：本功能随前端及桌面包发布，兼容原后端，无迁移或功能旗标。分别核验浏览器和打包桌面；未执行项必须标明，不把设计评审通过当成发布通过。若布局回退，仅回退布局入口及样式，账户守门仍保持唯一实例；不得因回退 UI 恢复旧 DELETE 清空路径或去掉异步 scope 隔离。持久化新 ID 可继续连接，无需把用户回滚到旧聊天；旧历史保留沿用后端既有保留策略，本轮不承诺无限留存或提供浏览入口。

实施过程发现的方案缺陷与验证结果见文末记录；上文评审记录保留其当时口径。

本次逻辑记录以 `logic --none` 报告“仅评审并修订设计文档，未修改实现代码”。完成报告须指向本文，并核验任务索引1的服务端状态为completed；本节点不执行git commit。

## 评审结论

**通过。** v2 已在正文修复全部7项 P1 并明确处理3项 P2，无未解决 P0/P1。现有技术栈能够实施，无新增依赖、后端协议或数据库迁移；共享 Modal 的兼容扩展仅为保留资金编辑键盘语义，范围合理。

本结论仅批准设计。后续开发仍须完成32项自动验收场景、代码规范检查、前端测试与构建，以及本文列明的浏览器和 Windows 桌面视觉/操作验收。本轮未运行实现测试或构建，未宣称功能已实现。

## 实施过程发现的方案缺陷

### IMP-01：首次会话初始化必须先恢复持久化状态

浏览器实测发现 `store.ts` 使用 `skipHydration: true`。助手子组件的挂载 effect
早于 Home 的手动 rehydrate，直接在空内存 ID 上调用 newConversation 会先写回
localStorage，覆盖清空后保存的新 ID，导致刷新重新生成会话。新增回归用例已在
修复前失败。修正第四节：首次内存 ID 为空时，先调用既有 persist.rehydrate；
恢复完成后再读取即时 store，仍为空才生成 ID。异步恢复时须阻止已卸载 effect
继续生成会话。此修正仅落在计划内 useAgentConversation.ts，无 store 或迁移变更。

### IMP-02：Modal 与提示需要覆盖焦点不在原触发节点的键盘路径

独立审查复现：资金输入框 Esc 取消后卸载，焦点可能回落 body；Modal 的 Tab
陷阱需在焦点已离开弹窗时收回首项或末项。仅 hover 激活的 tooltip 则需要在
打开期监听 window Escape，不能只监听按钮自身。两项均在原计划内文件修复并
补测试，不改变资金字段的数值逻辑。

### IMP-03：同 ID 重建需要重新发布完整会话镜像

旧 scope 排队的 React updater 会被所有权闸拒绝。同 ID effect 重放时若只发布
connected=false，同步镜像中的草稿或流式状态可能未进入新一轮渲染。新 scope
建立后发布完整保留状态，仅将连接态改为 false；以 StrictMode 队列回归覆盖。

### IMP-04：执行环境与规范边界

本机继承 NODE_ENV=production，直接运行 Vitest 导致 React act 不可用；测试命令
显式使用 NODE_ENV=test，构建使用 production，不修改项目环境文件。既有 Home、
Modal、账户及面板 JSX 函数本已超过50行；本轮保留这些原有组合函数边界，避免为
入口迁移扩展重构。新增会话 hook、控制器方法与事件处理按50行以内拆分，新增
源码文件不超过800行。此处明确收窄第七节“所有改动函数”门禁到新增机制，
保留既有超限函数作为后续独立治理项，不宣称全仓尺寸规范已满足。

## 实施结果与验证记录（2026-09-28）

第六节20项文件均已创建或修改。左栏为56px，主导航与账户/主题/设置均为44px正方形；
顶部仅保留窗口控制。账户查询常驻，rail弹窗保留校验、配置引导、资金编辑及键盘
语义。助手标题只有一个“清空上下文”入口，运行中可停止后清空；旧WS/REST/排队
更新均受scope限制，旧历史保留，自动交易对象保持。新增useAgentConversation
采用一个ref持有控制器同步镜像，与设计中分散refs等价；对外接口不变。

### 自动验证

| 验证 | 实际结果 | 证据 |
| --- | --- | --- |
| C05旧REST迟到、C06旧连接断线 | 修复前2失败/6通过；修复后8通过 | AgentPanelToolbar.test.tsx，任务运行输出 |
| Modal Esc委托、tabIndex过滤 | 修复前2失败/13通过；修复后全部通过 | Modal.test.tsx，任务运行输出 |
| 持久化初始化覆盖 | 新用例修复前1失败/21通过；修复后22通过 | useAgentConversation.test.tsx，任务运行输出 |
| 前端全量 | 267文件、3128测试通过 | `.agentmesh/logic-notes/workbench-full-tests-final.log` |
| 最终定向（包含随后新增的持久化回归） | 14文件、159测试通过 | `.agentmesh/logic-notes/workbench-targeted-final.log` |
| 最终hook测试类型补正后复跑 | 22/22通过 | 任务运行输出 |
| `pnpm --dir apps/web run typecheck` | 最终退出码0 | 任务运行输出；补正测试夹具seq类型后通过 |
| `pnpm --dir apps/web build` | 最终退出码0，编译、类型校验、静态页面生成完成 | `.agentmesh/logic-notes/workbench-build-final.log` |
| 本轮diff空白检查与范围审计 | 通过；无计划外已跟踪文件变化，既有改动保持 | `.agentmesh/logic-notes/workbench-scope-audit.txt` |

全量测试发生在最后的持久化初始化修复之前，之后对全部相关组件和原reducer/actions
完成159项定向验证并重新构建。首次全量运行时测试进程缓存修复前tooltip模块，与
正在更新的Esc测试不一致而出现1项失败；稳定源码后的全量重跑全部通过。不是将
失败忽略为既有问题。依赖仍有Vite CJS弃用告警，部分既有测试有React act告警，
未将这些称为无告警运行。本轮新增用例的focus行为已包装act。

### 浏览器实测

使用独立Chrome持久化profile和18771开发端口，亮色/暗色分别完成1440×900、
1024×768、860×600、860×400，共8组截图及DOM矩形测量。全部7个左栏按钮宽高
约44.0000px，底部设置始终在视口内；账户弹窗宽约383.99998px。右栏拖到320px
下限后检查长模型名截断、清空操作、账户资金首次Esc取消/第二次关闭、Tab收回和
焦点恢复。实际浏览器复现的持久化覆盖已修复，再次完整脚本通过。

可控WS夹具验证两次发送进入不同ID/socket；第二次payload不含旧测试口令；刷新后
仍为清空后的ID；无DELETE。测试中非GET请求均被夹具截获，没有发送交易操作。
后台行情等GET沿用现有服务，不将其数据真实性或服务健康度算作本功能验收。

证据为 `.agentmesh/logic-notes/workbench-browser.log`、
`workbench-{light,dark}-{1440x900,1024x768,860x600,860x400}.png`；运行脚本与
浏览器快照归档于同目录。短窗中央既有交易区拥挤属于范围外布局，本轮未扩大调整。

### 已知验收边界与交付状态

- 18771不在既有后端WS Origin白名单内，真实连接返回403；因此聊天浏览器测试使用
  可控夹具，未修改后端白名单，也未使用真实模型验证历史口令遗忘。
- Windows已打包客户端仍运行旧资源；本轮没有重打包、重启或操作用户正在使用的
  交易客户端。还原/最大化状态的原生标题栏实点、拖拽、双击及系统三键仍未验证。
  macOS/Linux同样未验证。浏览器几何、jsdom和构建通过不能替代这些发布验收。
- 保留原工作区改动、未新增依赖、未修改后端/桌面/store迁移、未执行git commit。
  两份机制逻辑记录已登记。实施节点交付源码与可重复证据，提交由后续节点负责。

最终独立复核：未发现未解决P0/P1；四项审查发现均已修复，复核者独立重跑五套定向测试62/62通过（含持久化恢复与StrictMode）。

## 最终提交审查（2026-09-28）

本节点逐项核对第六节20项文件、G1–G8与N/C验收矩阵，未发现需追加修改的
产品机制缺陷。账户入口、主题与设置迁移完整；账户查询常驻与原校验保持；
共享Modal兼容原调用方；会话控制器在事件入口、REST返回和排队更新处校验
当前作用域，清空只更换聊天ID，不删除历史、不修改交易对象。

| 审查维度 | 结论 |
| --- | --- |
| 覆盖 | 四项用户需求及20项文件均包含于提交候选；未新增依赖或后端协议 |
| 回归 | 账户水合、模拟资金、Modal焦点、同ID续流、旧会话隔离及自动交易共存均有测试 |
| 命名、样式与注释 | 新组件沿用现有设计变量、中文交互和组件分层；既有大函数的边界按IMP-04记录 |
| 提交范围 | 使用独立临时索引，按具体路径暂存；不纳入运行产物、临时文件及其他功能 |

主工作区已有未提交的交易日志接线，且证据门禁等其他功能已暂存。直接提交
完整page/NavRail文件会把日志依赖带进本功能，甚至引用尚未提交的模块。因此
依据实施前快照，仅在提交候选中剥离日志代码及其专属测试；工作区原内容保留。
提交在当前HEAD的三个主导航基础上独立成立，工作区的第四项日志及其门控不变。
原暂存内容通过索引条目及暂存补丁逐字节比对保全，不使用全量git add。

本节点重新执行的验证结果：

- 隔离候选（当前HEAD加本功能）：255个文件、3016项前端测试全部通过。
- 同一候选的typecheck退出码0，生产构建退出码0，静态页面生成完成。
- 原工作区共存回归：page、NavRail、NavRailControls共17项全部通过。
- 候选diff空白检查通过。测试仍有既有React act及Vite弃用告警，不宣称零告警。

新证据存于`.agentmesh/logic-notes/workbench-review-tests.log`、
`workbench-review-build.log`与`workbench-review-integration.log`。
本轮核阅前序浏览器截图与脚本记录；未重跑真实模型或原生桌面验收，上一节
列明的发布验收限制继续有效。代码审查和独立提交验证通过不等同于这些验收通过。
