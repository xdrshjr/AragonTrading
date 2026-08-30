# 证据 · R1–R14 无头浏览器人工验收（§9.3 的终审门）

> 终审节点（Subtask #3）产出。spec §9.3 把 R1–R14 定义为**终审门**：jsdom 没有布局引擎，
> §9.1 / §9.2 的 47 条用例只能证明「类名写对了」，证明不了「像素上真的没折行」。
> 本文件是那一遍真实渲染验收的记录 —— **判据不是类名，而是 `Range.getClientRects()` 的行盒数**。

## 一、复现方法（可原样重跑）

```bash
# 1) 起 dev server —— 必须 cd 进 apps/web 再起
#    （在仓库根用 `next dev apps/web` 起，Tailwind 的 content глоb 相对 cwd 解析不到任何文件，
#     产物只有 preflight、一条 utility 都没有，页面全裸而验收会全绿——假绿）
cd apps/web
NODE_ENV=development node node_modules/next/dist/bin/next dev -p 18763

# 2) 跑验收（harness 与本文件同目录）
cd docs/plans/workstation-text-nowrap-contract/evidence/harness
node run.js <输出目录>
```

`harness/` 三个文件：`fixtures.js`（后端桩，经 Playwright `route` 拦 `/api/py/**`）、
`audit.js`（注入页面的度量脚本）、`run.js`（档位与真实拖拽）。
浏览器用系统 Chrome（`executablePath`），本机 `ms-playwright` 未下载 headless shell。

**为什么用真实拖拽而不是预置 `localStorage`。** §9.3 建议注入 `aragon-ui` 键再 reload；实测这条路
**不通**：`store.ts:1489` 是 `skipHydration: true`，由 `app/page.tsx:536` 挂载后才 `rehydrate()`，
而在此之前已有一次 persist 写穿把默认值刷回 `localStorage`，注入值被覆盖（实测注入 200 / 640 / 0.3，
落地读回 240 / 380 / 0.15）。改为用 `page.mouse` 真拖 `[role="separator"]` 手柄 —— 这也更贴近
R4 / R14 的原意（它们的判据本来就是「跟手 / 不抖动」）。

## 二、度量口径

| 指标 | 取法 | 为什么这么取 |
|---|---|---|
| **是否真的折行** | 遍历 `<main>` 下每个文本节点，`Range.selectNodeContents` 后取 `getClientRects()`，按 `top` 去重得行盒数 > 1 | 唯一能区分「声明了 nowrap」与「像素上真没折」的判据 |
| 是否**允许**折行 | 该文本祖先链上是否有 `whitespace-normal` 类 | Type D 折行是**期望行为**，必须与缺陷分开计数 |
| 锚点自证 | 每个 `[data-nowrap]` 的 `getComputedStyle().whiteSpace` | 类名写了 ≠ CSS 生成了（K10） |
| 行是否溢出 | `scrollWidth > clientWidth + 1` | 吸收点没生效时表现为行溢出而非折行 |
| 表格退化方式 | 向上找第一个 `overflow-x: auto/scroll` 宿主，看它是否真的可滚 | Type C 承诺「横滚而不是折表头」（K2） |

## 三、结论：**通过**。工作台受管区域零非预期折行

九个档位（含 20 次连续拖拽）里，**唯一**出现的折行全部落在显式 `whitespace-normal` 的散文上：

| 档位 | 折行文本 | `whitespace-normal`？ |
|---|---|---|
| 全部九档 | 「把下方自选标的拖到这里，或点其「＋」加入；启动后将对所有成员同时自动交易」（`AutoTradePanel` 空态） | ✅ 是（Type D，**期望折行**） |
| R9 | `TradeExplainDetail` 的 `summary` 散文 | ✅ 是（Type D，**期望折行**，正是 R9 的判据） |

`components/Agent/**` 的两处提示文案（AI 助手空态说明与建议 chip）也会折行，未声明 `whitespace-normal` ——
它们属 N6 排除区（上一轮 `5dfbe62` 已单独收编，有自己的门禁 `agentToolbarNoWrap.source.test.ts`），
本轮不动。已在 R10 / R7 两档如实记录在 `r1-r14-audit.json` 里。

## 四、逐条判据

| # | 判据 | 实测 | 结论 |
|---|---|---|---|
| R1/R2/R3 | 左侧栏 200px 下自动交易三行完整 | 第 1 行 `min-h-[24px]` 实测高 **24px**、`scrollWidth == clientWidth`（184/184）；第 3 行同 | ✅ |
| R4 | 200↔460 连续拖 20 次 | 20 次全部精确落在 `200px` / `460px`，**零漂移**；侧栏内横向滚动量恒为 0；中栏全程 634 / 374 | ✅ |
| R5 | 右栏 640 + 左栏 460（1440 窗口） | 侧栏存值 460 / 实渲 **334**（轴 E 预期的「存值 > 渲染宽」）；中栏 **恰好 240**（未被挤破地板）；文档零横向溢出 | ✅ |
| R6 | 1920 → 860 → 1920 | 860 档中栏 240 / 盘口 160 / 侧栏 200，全部停在各自地板；1920 档中栏 1011、盘口 223 | ✅ |
| R7 | 数据表横滚而非折表头 | `min-w-[620px]` 的成交记录表与 `min-w-[420px]` 的运行历史表在窄档下 `hostScrollable = true`、`clipped = false` | ✅（实盘三张表见 §五残留） |
| R8/R9 | 展开行内散文仍换行 | R9 档（860 窗口）`TradeExplainDetail` 的 `summary` 折 2 行且带 `whitespace-normal`；同屏 `trade-explain-metrics-grid`（Type S）宽 578、零溢出 | ✅ |
| R10 | 右栏 320px 下五处 | 表格横滚正常、指标卡与表头一行完整、零非预期折行 | ✅ |
| R11 | 明 / 暗双主题 | 暗色 1024 档与明色同档判据逐条一致 | ✅ |
| R12 | 情报弹窗 7 个页签（K4） | 弹窗宽由 `max-w-*` 决定（N6 范围外）；`Tabs` 拿到 `whitespace-nowrap` 后页签不折行 | ✅ |
| R13 | 桌面端打包产物 | **未执行**，见 §五 | ⚠️ |
| **R14** | 过约束档拖拽手感（**C4 绑定**） | 侧栏「不跟手」（存值 460 / 实渲 334）但**稳定**：无抖动、无闪烁、中栏停在 240 未被挤破、文档零横向溢出 | ✅ **轴 E 无需回滚** |

## 五、本轮**在真实渲染下**发现并已修复的两个缺陷

两条都是 jsdom 察觉不到、且都由本轮改动引入的，已在源码修好并各配一条门禁。

### D-4（P1）`ChartHeaderBar` 的 OHLC 组被 `shrink-0` 钉死，窄档整组横向溢出

`shrink-0` 让该组按 max-content **固定在 626px**，它自己的 `flex-wrap` 便永远轮不到触发。
实测 `chart-header-bar` 的 `scrollWidth` 在任何容器宽下恒为 **642px**：中栏 634 时溢出 8px、
中栏 414 时溢出 228px、中栏 240 时溢出 **402px**，全部越过右侧边界。
这与 R5 判据「OHLC 字段**整块换行**」正好相反。

- **修法**：两个组各加 `max-w-full`（与同文件 `paperStrategies` 那组的既有写法同源）。
  修后 `cw == sw` 在九档全部成立（634/634、414/414、240/240 …）。
- **门禁**：契约新增**第 5 条铁律**「本身还会回流的条目，`shrink-0` 必须与 `max-w-full` 同行出现」，
  由源码门禁 **G7**（受管文件内 `flex-wrap` + `shrink-0` 同现即要求 `max-w-full`，带 `data-nowrap` 的行容器除外）
  与组件用例 **T6b** 各守一道。反向验证：删掉任一处 `max-w-full`，G7 与 T6b 同时变红。

### D-5（P1）轴 E 的 240px 地板没有配套裁切，盘口列**盖在**右侧策略面板上

K9 写的退化是「盘口列被 `<main>` 的 `overflow-hidden` 裁掉一部分」。实测**不成立**：
`<main>` 在本行**之外**且比本行宽（860 窗口下 main 宽 812、上区行宽仅 426），裁不到这一行。
E-2 把中栏地板提到 240 后，本行三列硬地板合计 **612px** > 426px ⇒ 溢出 186px，
盘口列右边缘落在 **x=660**，而右侧 `<aside>` 左边缘在 **x=480** —— **重叠 180px**，
「disconnected / 24h Vol」直接压在「交易策略 / 2 个内置」上。

- **修法**：上区三列行（`ref={topRef}`）加 `overflow-hidden`，让 K9 那句承诺成立。
  修后 860 档：行右边缘 474、`aside` 左边缘 480，**不再重叠**；盘口列如 N3 所述被裁掉。
- **门禁**：**G6 扩一条**——`ref={topRef}` 的开标签必须含 `overflow-hidden`（地板与裁切必须成对存在）。
- **代价（已实测、可接受）**：860 档打开「指标」下拉时，面板右边缘被这层裁切切掉约 76px。
  见 `menu-860.png`：七个指标条目与「恢复默认」**全部可读可点**，只切掉了右侧一条背景。
  改前该下拉是**盖在**右侧面板上（同样越界，只是没被裁）。

## 六、明确未覆盖的残留（如实登记，不算通过）

1. **R13 桌面端打包产物真机验收未执行。** 需 `desktop/` 出包 + 双进程起后端，超出终审节点的验证边界。
   Electron 的 WCO 安全区只影响顶栏、不影响工作台（spec §9.3 R13 原注），Web 档结论可迁移，但**这是推断不是实测**。
2. **实盘三张表（`LivePositionsTable` / `LiveOpenOrdersTable` / `LiveFillsTable`）未在真实渲染下走到。**
   它们只在实盘账户模式下渲染，需真实凭证 + 二次确认弹窗。三张表的 Type C 契约由 T11–T14 与 G4 冻结，
   且同一套写法已在本轮真实渲染中由 `TradesTable`（`min-w-[620px]`）与 `RunHistory`（`min-w-[420px]`）
   证明「宿主真的横滚、真的没被裁」。**同形而非同一处**，登记在此。
3. **`ChartToolbar` 在中栏 240px 地板处仍会被裁。** 七个周期段控是**一个不可分割的条目**（264px），
   容器只有 208px ⇒ 「1D」被裁。这是 Type B 声明的退化方式（「条目内部永不折」）与 N3 已接受的
   「860 档任何 CSS 排布都放不下」的交集，**不是新缺陷**：改前该档中栏实宽仅 13px，整条工具栏溢出得更远。
4. **左侧下单栏四个子组件（C5 / 评审 P1-5）的目视确认**同样受限于实盘模式，未走到；仍按 §12 留给下一轮。

## 七、原始数据

`r1-r14-audit.json` —— 九个档位各自的 `geom` / `wrapped` / `anchors` / `tables` / `sweep` 全量记录。
截图：`R1-R4-sidebar-min-200.png`、`R5-R14-overconstrained.png`、`R6-narrow-860.png`、`R6-wide-1920.png`、
`R10-rightpanel-320.png`、`R11-dark-1024.png`、`R7-R8-live-bottom.png`、`R9-trade-explain-expanded.png`、
`R4-drag-sweep.png`、`menu-860.png`。
