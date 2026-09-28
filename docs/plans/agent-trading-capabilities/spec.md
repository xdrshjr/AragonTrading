# Agent 账户分析与指令交易设计及实施计划

版本：v2；日期：2026-09-28；阶段：设计评审完成，有条件通过。

目标：让普通对话 Agent 查询真实账户、持仓、委托和成交，复用自动交易的
分析上下文，并将用户的买卖指令转成可核对、可执行、可追踪的交易。

架构：保留现有普通对话与自动交易两条编排链。新增账户读取适配层和持久化
交易意图服务，两者复用 OKX 服务、订单组装和既有自动交易状态。

技术栈：Next.js 14、React 18、TypeScript、Tailwind、Zustand、FastAPI、
Python 3.11、SQLAlchemy async、SQLite、现有 httpx OKX 客户端。

实施指引：下游按本文第九节逐项实施，可使用
`superpowers:executing-plans` 的分步验证方式。本节点仅写设计，不提交代码。

## 评审记录

评审依据：当前工作树源码、根目录 CLAUDE.md、项目索引及 OKX 官方接口
文档。按可行性、完整性、一致性和范围适配逐节审查；下表“已修订”指设计
正文已闭环，不表示代码已实现或测试已通过。P0 为可能错误交易/解除互斥，
P1 为实现缺口、恢复失真或重要契约冲突，P2 为可接受的产品限制。

| 编号 | 级别 | 问题及源码证据 | 正文处置 / 验收 |
|---|---|---|---|
| R01 | P0 | §3.5 外层复核后，place_live_order 再读取配置并重建 payload；限流等待后可发送不同或过期订单，现有 risk_guard 不校验完整预览 | 已修订：发送边界 guard、实际 payload 比对，§3.5/3.6；A29 |
| R02 | P0 | main.lifespan 用 create_task 加载 AI 冷暂停；仅恢复聊天 submitting 不能证明自动交易空闲 | 已修订：共同就绪屏障和全状态保守准入，§3.5；A30 |
| R03 | P0 | 恢复核对未规定环境检查；private_client._headers 使用当前全局 demo，重启换环境可能查询错误账户 | 已修订：核对身份与环境闸，§3.5/5.2；A31 |
| R04 | P0 | ToolContext 若逐工具读取新 binding，同一轮可能先分析 A 再为 B 预览；live 预览后切 sim 也无服务端失效机制 | 已修订：轮次冻结绑定及持久模式版本，§3.2；A32 |
| R05 | P1 | 撤单未知态套用下单 GET 映射，会把目标 live/部分成交误当撤单已受理/部分完成 | 已修订：按 action 分状态机，§3.5/5.2；A33 |
| R06 | P1 | CAS 与后台任务接管间取消可留下永久 submitting；同态成交增量与审计失败的 DB 真值未定义 | 已修订：任务所有权、孤儿恢复、同态 CAS，§3.5/5.2/5.3；A34 |
| R07 | P1 | requestHash 未区分用户输入和动态行情；同消息重试可因行情/all 数量变化另建单，过期去重又可能永远返回旧卡 | 已修订：静态请求散列和显式新消息重建，§3.4；A35 |
| R08 | P1 | 无持仓时 config 不含当前杠杆；旧 _avail_usdt 将缺失转 0，availBal 也不等于所有账户模式的可用保证金 | 已修订：leverage-info/max-size 只读适配、账户模式边界，§3.4/7；A36 |
| R09 | P1 | 只扩展 close builder 不足：validate_close 无条件要求 px；positionKey 不识别同向重开，部分平仓的“减少即失效”也与 frozenSz 上界冲突 | 已修订：市价校验分支、仓位观测指纹及明确比较规则，§3.4/3.6；A37 |
| R10 | P1 | fetch_live_open_orders 只读普通及 oco/conditional 各一页；不能把本地切片 total 当账户全部委托 | 已修订：覆盖范围与有界上游分页，§3.3；A38 |
| R11 | P1 | conversation.block_to_dict 把 content 存为 summary；加入模型 JSON 后会让恢复历史卡显示整段 JSON | 已修订：content/summary 分离与旧记录兼容，§3.7；A39 |
| R12 | P1 | clearContext 实际更换 conversationId；无全局未结入口会遗失唯一占用准入的卡片；后端 clear 亦未使预览失效 | 已修订：全局未结只读入口与清空事务，§3.7/4.2/6；A40 |
| R13 | P1 | 自动交易投影只有 accountId，冷暂停/环境和身份不足时可能误归因；状态列表未覆盖 settling/stopping | 已修订：证据身份过滤，所有非空托管占位拒绝写，§3.3/3.5；A41 |
| R14 | P1 | 两开关默认开启且实施第4步先开放 execute、第5步才补恢复，形成可实际发单的中间版本 | 已修订：开发装配开关及发布门禁，§5.3/9；A42 |
| R15 | P1 | 附带止损参数不等于保护已生效，部分成交/附单失败无说明；新 close/cancel 也遗漏共享仓位缓存失效 | 已修订：保护状态分离与缓存失效，§3.4/3.6；A43 |
| R16 | P1 | 新错误码未穷尽，旧 _map_trade_code 会拍平；接口框架校验可能返回英文 422 | 已修订：统一错误边界和补充契约，§4.2/7；A44 |
| R17 | P2 | 全局串行、限价长期占用及未知单可能永久保留，是一期可用性代价 | 接受并显式提示，保留人工交易所核对；§6/10，不增加自动重发或人工清零接口 |

逐节结论：§一/二的增量架构及单用户边界可行，保留；§三至六按 R01–R13、
R15–R16 补齐关键时序、契约和交互；§七至九同步文件计划、测试与装配门禁；
§十/十一保留外部交易竞态和未知态残余限制。共 4 项 P0、12 项 P1 已在正文
修订，1 项 P2 明确接受；无悬置的 P0/P1 设计决策。

## 一、概述

本次建设的是交易工作台的对话交易能力。用户在普通对话中可以询问“目前有
哪些持仓”“这笔订单成交了吗”“自动交易为什么没开仓”，也可以输入明确的
开仓、减仓、平仓或撤单指令。回答应来自账户与交易所证据，展示账户、环境、
时间及数量单位；模型负责解释和提取意图，确定性服务负责校验和执行。

当前源码并非缺少整个交易系统：手工交易 REST 已具备真实持仓、成交、下单
和平仓服务，AI 自动交易也已有持仓观测、风控、专用决策工具和执行器。真正
缺口是普通 Agent 的工具注册、账户上下文以及结构化交易回执没有连接这些
能力。本方案补齐这段链路，避免另造一套行情、风险计算或交易所签名实现。

交互采用对话内的交易预览卡：模型生成具体订单，用户在卡片上核对并提交，
提交动作直接调用后端，不再次依赖模型。查询无需确认，已提交订单的查询、
断线恢复也无需确认。自动交易继续使用现有启动授权及决策链，不因本方案
增加逐笔弹窗。产品文案使用“Aragon Trading”，不把需求中的合作背景描述
写成未经核验的品牌合作声明。

## 二、现状、范围与设计取舍

### 2.1 已核实的源码事实

本工作目录含真实项目源码。已阅读根目录 `README.md`、`CLAUDE.md` 和
`.claude-index/index.md`；索引仅用作导航，以当前源码为准。任务提示中的
另一个只读项目路径无需复制，也不得写入。工作树已有大量暂存及未暂存改动，
包含交易日志与证据门禁；下游须保留这些变更，禁止全量覆盖或清理工作树。

| 位置 / 符号 | 当前事实及本次影响 |
|---|---|
| `agent/tools/registry.py::_ALL_TOOLS` | 22 个工具覆盖行情、策略、因子、历史及 UI，没有账户持仓和下单工具 |
| `agent/tools/base.py::ToolContext` | 仅有 session 工厂、UI 上下文、取消探针，缺会话交易绑定和调用身份 |
| `agent/loop.py::_execute_tool_calls` | 普通工具只把 summary 传给模型及前端；payload 仅用于 UI action，直接返回交易 payload 会丢失 |
| `agent/manager.py::resolve_enabled_tools` | 工具状态采用无记录默认开启；新增工具不能靠工具开关充当交易授权 |
| `api/trading.py` | 已有 config、positions、fills、open-orders、order、close、close-all、leverage |
| `okx/trading.py::place_live_order` | 已支持固定 client_order_id、SubmissionTrace 和禁止自动重试的分支，必须复用 |
| `okx/trading.py::close_live_position` | 市价分支是整仓 close-position；不能拿它实现“平一半” |
| `okx/trading.py::fetch_live_order` | 按 clOrdId 查询订单权威状态，不能用未结订单列表推断已成交 |
| `okx/private_client.py::_headers` | demo 环境来自全局 okx_account_demo，不是每个账户独立字段 |
| `ai_auto/decider.py::AiTradeDecider` | 常规决策只暴露 submit_trade_decision；不要接入通用交易工具注册表 |
| `ai_auto/manager.py::status` | 包含冷启动暂停占位及 hydration unknown；非活跃进程不等于无托管 |
| `ai_auto/admission.py::get_admission_lock` | 自动交易两类 manager 已共用准入锁，本次复用同一把锁 |
| `ai_auto/session.py::snapshot` | 自动交易状态已有只读快照，可做白名单投影，无需另一次模型研判 |
| `db/session.py::init_db` | 新表通过 Base.metadata.create_all 注册；SQLite 外键未开启，不可依赖级联删除 |
| `apps/web/components/Agent/AgentPanel.tsx` | 普通对话与 AI 执行轨迹已经并存，可在普通对话内增加账户条和交易卡 |

表中后端相对路径均相对于 `services/quant/app/`。

### 2.2 本期能力与边界

交付：账户目录、余额摘要、当前持仓、普通及策略挂单、近期成交、指定订单
状态、AI 自动交易当前状态及最近决策摘要；对指定账户的 USDT 本位线性
SWAP/FUTURES，支持市价/限价开多开空、指定张数的市价/限价减仓及全仓平仓，
支持撤销该账户可识别的普通订单。

“买入”不能自动等同于开多，“卖出”不能自动等同于开空。缺少开平含义、
标的、数量单位、保证金模式或必要价格时，Agent 先澄清。用户要求现货交易、
币本位合约、算法撤单、改杠杆、改仓位模式、转账或提现时，明确说明本期
不支持；不通过另一个工具绕过范围。本期不新增一键批量全平。

本地 SimBroker 模拟会话和 OKX demo 是不同系统。新增账户工具只读取 OKX
账户，界面明确标为“OKX 模拟盘”或“OKX 实盘”；本地模拟模式仅显示
“当前为本地模拟，切换到账户交易后可生成订单”，不静默操作实盘。

自动交易已有的共识、收益保护、持仓周期、实证门禁和资金分配保持现状。
手工指令订单采用手工下单的确定性约束，不能被描述成“已通过自动策略门禁”。
不允许普通 Agent 修改自动交易策略、自动启动/暂停/结束会话或接管托管仓位。

### 2.3 方案比较与结论

| 方案 | 代价与结论 |
|---|---|
| 给 Agent 直接包装现有交易 REST | 开发量小，但没有持久意图、对话重试去重、未确认提交恢复；不采用 |
| 让普通对话调用 AI 自动交易 decider / executor | 混淆手工指令与周期策略，依赖完整会话、资金及托管状态；不采用 |
| 共享服务 + 对话交易意图 + 只读自动交易适配器 | 保留既有边界，补齐可靠提交所需状态；采用 |

本期沿用单机、单用户、单后端工作进程的部署约束。OriginGuard 是跨源边界，
conversationId 是数据定位键，二者都不是多租户鉴权。不能宣称本方案支持
互联网多用户部署；这需要独立的认证和账户归属项目。

## 三、技术设计与关键时序

### 3.1 模块与依赖方向

```text
普通对话 / 账户条 / 交易卡
  -> Agent WS、/agent/trading REST
  -> agent/tools/account_tools.py、trade_tools.py
  -> agent_trading/read_service.py、intent_service.py、execution.py
     -> db/agent_trade_repositories.py
     -> okx/accounts.py、trading.py、order_builder.py、trade_client.py
     -> agent_trading/auto_context.py -> 自动交易 manager 的只读出口

AI 自动交易原链
  -> snapshot / decider / 既有门禁 / LiveExecutor / okx/trading.py
  -> 仅在启动与恢复准入时检查对话交易的未结意图
```

REST 和工具调用同一个应用服务，不在进程内部发 HTTP 调自己的 API。
新服务不依赖 FastAPI Request，输入使用小型 dataclass/Pydantic DTO。
数据库 session 只覆盖短读写事务；等待 OKX、等待锁时不持有 SQLite 写事务。
公开函数参数不超过 5 个，复杂上下文合并成值对象。

### 3.2 会话绑定与调用身份

1. 账户条读取 `/api/agent/trading/capabilities`，显示实际环境和可用账户。
2. 用户选账户后 PUT 当前 conversation 的 binding，服务器查账户存在且凭据
   完整，保存 account_id、环境和递增 version。可以建议默认账户，但写操作
   不自动选默认账户。旧对话未绑定时，查询账户目录正常，其余账户工具返回
   `ACCOUNT_REQUIRED` 及“选择交易账户”的界面动作提示。
3. WS 的 user_message 加可选 `clientMessageId`（UUID）及
   `ui.accountMode=sim|live`。旧客户端缺 messageId 时后端生成，但缺
   accountMode 时仅允许查询、不允许生成交易意图。前端重试原消息复用 UUID。
4. ToolContext 新增可选 `call: AgentCallContext`，含 conversation_id、
   client_message_id、tool_use_id、账户/环境、绑定版本与账户模式。manager
   接收消息时冻结本轮绑定；每次工具仅 replace tool_use_id。取数前和创建
   意图落库前均比较当前绑定版本；轮内变更即 BINDING_CHANGED，不改读新账户。
   无绑定启动的轮次不能在运行中自动获得后来添加的绑定。
5. 新工具参数不接受 accountId、env、credential 或授权字段；账户来自当前
   binding。创建意图时冻结身份，执行时重读绑定、账户和环境。模型不能
   通过改参数换账户。UiContext 只是用户界面状态，不是执行授权。
6. 修改 binding 必须携带 expectedVersion；CAS 失败返回 409。更换账户或
   显式取消绑定使旧预览卡失效；已提交订单继续按原账户查状态。

binding 同时持久化 accountMode，初始缺省 sim。PUT 同时提交 accountId、
accountMode 与 expectedVersion；切换本地 sim/live 也递增版本。前端先禁用
提交按钮，完成 PUT 后才允许新模式创建预览；WS 模式必须与冻结 binding
相符，不能用一个 ui.live 覆盖服务端 sim。进入 submitting 后模式/账户
更新返回 409，UI 显示“提交处理中，暂不能切换”，不假装已切换。其他页签
仍在旧版本时提交由后端拒绝。重复 PUT 无变化不增加版本。

凭据指纹采用后端 SHA-256(api_key + 分隔符 + secret + 分隔符 + passphrase)，
只落内部字段、不发给模型或浏览器。换凭据导致预览失效；对旧未结订单不拿
新凭据冒充旧账户继续归因，而显示 `ACCOUNT_CREDENTIALS_CHANGED` 待人工核对。
不修改现有凭据存储方式，不在本期做密钥迁移。

### 3.3 查询链路与分析复用

读工具每次获取实时数据；同一次调用复用已取得的账户响应，不跨轮缓存持仓。
尤其不能把 `api/trading.py::trading_positions` 改接 positions_cache：源码
明确将用户主动刷新定义为真实查询。前台请求按会话取消，但已提交意图的
核对任务独立存活。共享现有 OKX 连接池、限流及 GET 重试，单调用总期限 15 秒。

每个结果含 schemaVersion=1、source、accountId、accountLabel、environment、
asOfTs、coverage、warnings 与有界 data。余额空值用 null，不用 0；无持仓
仅在成功读取且确实为空时返回空数组。读取失败返回错误，不返回空仓成功。
取数开始和结束时间可分别记 observedStartTs/asOfTs，不声称多个 REST 是原子快照。

持仓/委托工具可按 instId 筛选，列表 offset 默认 0、limit 默认 20、最大 50，
按稳定身份排序后切片，返回 total、hasMore；total 仅在覆盖完整时为数字，
不完整时为 null 并附 observedCount；多次读取的分页不保证同一快照，
界面标记各自时间。近期成交 limit 默认 20、最大 100，明确“近期可查范围”，
不声称是完整历史账本。发送给模型的 JSON 最大 24 KiB；超过时减少展示条数，
设置 truncated=true 和 returnedCount，不能截断 JSON，也不能用截断集合做风控。

委托覆盖范围明确为普通订单、oco/conditional，其他算法类型列入
coverage.excludedKinds；不称“全部策略委托”。不能直接把旧
fetch_live_open_orders 返回值当完整集合：新适配器复用客户端签名和连接池，
对三个端点分别用官方分页游标读取，按 ordId/algoId 去重；单调用各最多
5 页且共同服从 15 秒期限。旧方法缺省行为不变，新 keyword-only 分页
选项集中为查询 DTO。达到页数/期限或上游页失败时，已取数据可作为
coverage=partial 显示，warnings 指明端点和原因，hasMore=true；一页未
成功则返回读取失败。oco 展开后 rowKey 含 algoId 与 tp/sl 腿别。部分列表
只用于展示，撤单确认必须 get_order 精确查目标；需要完整委托覆盖的
开仓校验在未完成分页时返回 ACCOUNT_SNAPSHOT_INCOMPLETE。余额和仓位
保留原始十进制串构造 DTO，不把已有 float 归一结果用于执行精度比较。

`auto_context.py` 仅投影相同绑定账户的 group/session 公共状态。新增 manager
方法 `conversation_context(binding_identity, inst_id)`：在 manager 内访问自己持有
的 group、paused ref 和 session.snapshot；普通 Agent 不访问 `_executor`。
返回 groupId、sessionId、runId、state、bar、lastRound、lastDecision、
gateReasons、positionSummary、snapshotTs。字段缺失为 null，冷暂停为
contextUnavailableReason，不编造最近研判，不主动调用 LLM。

binding_identity 包含 accountId/environment/凭据指纹。只导出能够从启动
快照或持久运行记录证明属于同一身份的明细；现有记录若缺环境或凭据身份，
返回 CONTEXT_IDENTITY_UNAVAILABLE 和通用托管状态，不用当前全局配置
倒填历史身份。新增会话把非秘密的身份指纹保留于内部上下文，指纹不出 wire。
冷暂停可以只返回有证据的 accountId/groupId/state，最近研判为 null。
此过滤只限制读取明细；写入互斥仍是全局，不能因明细不可读而放行。

最近决策只保留动作、有限理由和门禁码，不导出原始模型思考全文、trajectory、
完整 prompt 或凭据。交易所持仓是当前事实；自动交易 positionSummary 是
该会话的本地视图，两者不一致时并列展示，不拼成一个“正确数字”。普通
Agent 可继续调用已有行情、体制、策略、因子和回测工具分析，但须区别历史
统计与实时观测，不把任何模型结论当成交易所事实。

### 3.4 创建预览意图

`prepare_trade` 只做读取和数据库写入，绝不调用交易所写端点。流程固定为：
校验 schema -> 读取绑定和来源模式 -> 读取账户/环境 -> 读取新鲜配置、持仓、
委托与标的元数据 -> 检查托管冲突 -> 规范化数量和价格 -> 计算成本摘要 ->
保存 intent + 首条 event -> 返回卡片及模型摘要。

用户数量支持两种开仓输入：`contracts`（张）与 `notional_usdt`（名义 USDT）。
禁止模糊“100 USDT”同时表示保证金和名义金额；工具 schema 明写含义，界面
同时展示张数、合约面值、估计名义和保证金。notional 转张数使用
floor((notional / (ctVal * referencePx)) / lotSz) * lotSz。
referencePx 市价预览取最新 ticker，限价取规范化限价；实际市价名义会变动，
不得把请求名义金额称为严格成交上限。

数量以十进制字符串输入，Pydantic 拒绝 NaN、Infinity、bool、指数溢出和
非正数；用 Decimal 完成意图层运算。复用现有 snap_size/snap_price 的真实
订单规则，并以最终序列化 px/sz 回读后形成卡片，确保预览值与提交值一致。
张数只向下取整，低于 minSz 就拒绝，不向上抬到最小单。限价按当前 builder
规则取整并显式显示调整后价格。进入旧 float 服务前验证有限且重序列化一致，
不能保证时拒绝 `PRECISION_UNSUPPORTED`。

开仓必须给 tdMode，不设置杠杆；读取现有杠杆计算保证金，无法读取则预览
失败，不拿默认值代替。执行时 OrderInput.lever=None，避免意外 set-leverage。
可选止盈止损沿用既有附单 builder；同时校验相对于方向和参考价的有效区间，
取整后再校验。不提供止损时卡片写“未设置止损”，不擅自补设。

杠杆来源补齐：在 OKXPrivateClient 新增只读 get_leverage_info 查询
`/account/leverage-info`，按 instId/mgnMode 及适用 posSide 匹配唯一条目；
不是从 account/config、maxLever 或不存在的持仓猜测。开仓再读取
`/account/max-size`，按相同 instId/tdMode/限价（若有）取得当前方向可开张数；
实际 frozenSz 不得超过 maxBuy/maxSell，不传 lever、不自动借币。本期写能力
仅支持能明确取得这些字段的单币种/多币种保证金账户；组合保证金及未知
acctLv 读能力保留、写返回 ACCOUNT_MODE_UNSUPPORTED。availableMarginUsdt
来自该模式适用的 USDT availEq，availBal 单独表示可用余额，字段无适用值
时为 null；禁止套用旧 _avail_usdt 的缺失转零逻辑。保证金展示为名义/当前
杠杆的估算，不能等同可提款额或完整组合风险评估；执行必须重查 max-size
和余额，字段缺失、模式不支持或保证金不足明确拒绝。

附单界面写“已请求随单止盈/止损”，protectionStatus 初始为 unverified；
普通订单 accepted/filled 都不自动改为 active。只在读到可匹配的算法委托
后显示 observed_active；无证据保持 unverified/unknown，不宣称受保护。
部分成交尤其不能承诺保护已激活，回执/卡片显式提示；本期不自动补挂或
维护附单。依据 [OKX 附单参数说明](https://www.okx.com/docs-v5/en/#order-book-trading-trade-post-place-order)，
附单关联在主单完全成交后产生，具体保护须另核对；这里采用保守展示，
不增加第二套保护执行器。

平仓通过最新 positionKey 精确选择 instId、posSide、mgnMode 及净仓方向。
`closeSize=all` 在预览时冻结为当时张数，卡片写“当前全部 X 张”；之后新增
持仓不扩大该数量。执行前仓位减少、消失或 net 方向翻转均返回
`POSITION_CHANGED`，旧卡片失效；不自动缩量后仍用旧确认。

冻结 positionObservedSz 与 positionIdentity（posId、exchangeCTimeMs、
净仓符号和保证金模式），缺少可比较身份时拒绝生成平仓预览。执行比较
currentAbsPos >= positionObservedSz 且 frozenSz <= currentAbsPos，因此
部分平仓时即便剩余仓位仍足以覆盖 frozenSz，只要比预览观测少也拒绝。
uTime 仅作观测，不作身份主键，以免行情更新误杀。身份或创建时间变化时
拒绝同向同量重开仓；交易所未改变这些标识的外部平开循环无法完全识别，
作为外部并发残余限制展示，不承诺仓位锁定。

撤单读取目标订单并冻结 ordId、instId、kind、状态与侧别，只允许普通委托。
不依赖用户输入的 clOrdId 作为凭证；属于别的账户、算法订单、未知身份均拒绝。
普通订单已终结时直接返回真实状态，不把“没有挂单”推断为撤单成功。

意图有效期固定 60 秒，预览不保留余额、不下单、不设杠杆。每个会话最多
20 个有效预览；超过返回 429。相同 conversationId、clientMessageId 与
规范化 requestHash 返回同一个 intent，工具调用 ID 变化也不会另造订单。
不同参数可形成不同预览，但不会自动执行；过期卡片需重新生成。

requestHash 只取 action 和规范化用户输入（含 all 字面量），不含 ticker、
换算张数、observedTs 或当前余额；预览事实仅进入 previewHash。先查去重，
再取行情并计算预览；并发 UNIQUE 冲突读取胜者，不能重算胜者的 TTL。
返回既有意图前校验本轮冻结账户/环境/绑定版本；不匹配即 BINDING_CHANGED，
不能以旧消息在新账户另造同请求。相同消息不同参数产生独立、需分别提交
的卡片；“重新生成”必须发新 clientMessageId 的明确新消息，旧 intent
及其终态永久保留，禁止更新原卡重置有效期。20 条额度在写事务内先惰性
过期再检查及插入，同请求去重优先于额度拒绝。

### 3.5 提交、互斥与恢复

交易卡 POST execute 携带 intentId、version、previewHash。后端不接受来自
浏览器的数量、价格、账户或交易所 payload 覆盖。浏览器提交动作是本次具体
订单的执行授权；模型工具注册表中不存在 execute/confirm 工具。显示预览、
切换视图、重放 WS、模型输出“已确认”均不能触发执行。

执行顺序如下：

1. 先校验总闸、对应 prepare 工具当前仍启用、会话归属、绑定版本、环境、
   凭据指纹、expiresTs 和 previewHash。重复执行先返回该 intent 的当前
   状态；只对仍为 prepared 的记录进行首次提交校验。
2. 取得现有 get_admission_lock，检查恢复就绪屏障后，在锁内查 AI
   manager.status 与 live-auto group_status。任一非空托管占位都阻止聊天
   写操作（包含 running/paused/recovering/settling/stopping/unknown），
   返回 `AUTO_TRADE_OWNS_ACCOUNT`；未知枚举默认阻止，不能只枚举几个状态。
   本期故意沿用全局单活跃自动化
   的保守粒度，不试图用可能缺失的 accountId 或不同 API key 分辨同一账户。
   提示跳转既有托管面板处理，普通对话不代为暂停或拆止损。
3. 同一锁内以短 DB 事务 CAS prepared -> submitting，并写 event，分配
   `clOrdId = 'agt' + uuid4.hex[:29]`，全表唯一，终态后永不重用。并检查
   全局最多一条未结交易链（submitting/unknown/accepted/partially_filled）；
   其他新增敞口或平仓提交返回 `TRADE_INTENT_IN_FLIGHT`。同一已知订单的
   撤单作为该链的取消请求允许进入，具体例外见下文。commit 成功才释放准入锁。
4. 两类自动交易 manager 的启动和 AI 恢复路径在同一准入锁内检查上述
   未结意图集合，存在则拒绝启动。数据库不可读也拒绝准入。这样提交后的
   网络等待期间无需长持共享锁，同时不会让自动交易抢入。
5. 释放锁后重取绑定、凭据及环境、新鲜持仓/配置/余额/可开张数/杠杆/
   元数据/行情并复核。最早一项关键观测取得后至实际发送超过 5 秒即停止。
   市价相对预览 ticker 绝对
   变化超过 50 bps、数量或精度变化、模式/杠杆变化使预览 stale；限价不按
   ticker 漂移否决，只复核实际价格精度、资金和风控。失败落 rejected。
6. 开仓复用 place_live_order，传已存 client_order_id、SubmissionTrace
   和内部 dispatch_guard，不传 lever。开仓按 live_max_order_notional_usd、
   余额、可开张数及实际 payload 复核；市价两方向的名义估计都取
   `ticker * 1.005` 作聊天入口额外校验。
   这只是发送前上界估计，不保证成交价。无法取得余额/持仓时拒绝。
7. 平仓使用新增 `okx/intent_close.py::place_intent_close`，调用现有
   build_close_order_payload 的兼容扩展（第 3.6 节），发送指定数量的
   `/trade/order`，而非带自动撤单副作用的 `/trade/close-position`。
8. 撤单调用 cancel_order 的兼容发送选项 `allow_retry=False`，随后查询订单
   真实状态。撤销回执仅表示请求受理，不能直接写 canceled。
9. 所有交易所写操作至多发送一次。发送前确定性失败为 rejected；发送后
   网络异常、HTTP 5xx、取消、空 ordId 或审计落库失败均为 unknown。
   仅收到可确定的交易所业务拒单才为 rejected。不要把 broad exception
   一律映射成“失败，可以重试”。不确定是否发送也按 unknown 处理。
10. 交易所受理先落 accepted；核对器只发 GET。仅开/平仓 intent 的订单
    live -> accepted、
    partially_filled -> partially_filled、filled -> filled、canceled 或
    mmp_canceled -> canceled；累计成交和成交均价单独记录。

R01 的发送边界是必要的兼容扩展，不可只在 execution.py 外层检查：
内部 IntentOrderInput 子类增加 dispatch_guard；旧 OrderInput 字段与
序列化契约保持不变，place_live_order 兼容读取内部 guard。
place_live_order 在重新取数及构造 payload 后，把实际 payload、元数据、
posMode/持仓/行情及各观测时间组成 DispatchEvidence 交给 guard。guard
比较冻结 preview 的 instId/side/posSide/tdMode/ordType/sz/px/reduceOnly/
附单，允许的唯一生成字段是已持久化 clOrdId；净仓省略 posSide 与 net
显式归一后比较。发现精度、模式或附单取整变化就拒绝，不重新适配旧确认。
posMode 缺失/未知也拒绝，不沿用旧服务的 long_short_mode 默认值。

将完整校验组合进现有 before_send：在 `_acquire_slot`、连接池等待之后，
紧贴 client.post 前同步检查单调时钟期限、预览到期、冻结 payload、当前内存总闸和
shutdown 标志；guard 与原 risk_guard 是 AND，不能相互覆盖。通过才设置
trace.phase=sent；不能在限流等待之前就标 sent。市价漂移用底层实际取得
的最新 ticker 再判定，外层余额/max-size/杠杆证据也仍须在共同 5 秒窗口内。
限价保留新鲜度与身份检查，只免 ticker 漂移；撤单无需资金/行情/杠杆读取。
旧服务的 2% 名义校验保持兼容，聊天检查是附加条件，不能承诺 0.5% 就是
唯一交易上限；两层均通过才发送。已知 guard 本地否决可 rejected；对无法
确认 before_send 是否完成的异常保留 unknown。

复核按 action 分支：open 需要完整资金/可开张数/杠杆证据；close 只需
身份、仓位、账户模式、合约规则及适用价格，不因缺少开仓余额/max-size
阻塞风险降低；cancel 只需目标普通订单、账户身份和状态新鲜度。cancel
guard 比较实际 instId/ordId，不要求不存在的 sz/px。目标已终态时只读
返回现状且零 POST；prepared cancel 先 rejected（无需撤销）并在 result
展示目标终态，不把未发送的请求标成“撤销成功”。

binding 的更新也取得同一准入锁；有 submitting 时返回 409，保证校验至
发送期间账户绑定不变。修改全局 demo 配置需要重启；凭据被其他设置入口
修改不影响已冻结的一次客户端凭据，但发送前发现指纹变化必须拒绝。正在
使用的凭据快照只存在局部内存，不落在 intent 或日志。

撤单例外必须可直接实现：在准入锁内取全部未结意图；只有当它们全是当前
绑定账户、环境与 targetOrdId 相同的原开/平仓 intent，且状态为 accepted
或 partially_filled，才允许新增一个 cancel intent 进入 submitting。
存在其他 cancel 的 submitting/unknown/accepted、原单 ordId 未知、其他
目标订单或其他账户的未结记录时一律拒绝。没有聊天未结记录时可撤该账户
的外部普通单。cancel 的 cl_ord_id 保持 null，target_ord_id 才是查单真源，
不能给取消请求新建一个可被误认为订单的 clOrdId。

撤单受理后，cancel intent 的 accepted 表示“撤销请求已受理，等待订单
终态”，持续 GET targetOrdId。目标 canceled 时其变 canceled；目标 filled
时其变 filled 并显示“撤销前已成交”；同一原开/平仓 intent 按同一权威
结果更新。两个更新各自 CAS，允许下一次核对补齐，不可提前释放仍未结的
另一条记录。撤销明确拒绝只终结 cancel intent，原订单继续占用准入。
因此用户可以在对话内撤销自己刚挂出的限价单，无需先等它终结。

撤单查单的非终态不能套用第10步：unknown cancel 查到目标 live 或部分
成交时仍为 unknown，只更新 target exchangeState/accFillSz；accepted
cancel 查到这些状态保持 accepted，表示仍等待撤销结果，不变成
partially_filled。只有原 cancel POST 的有效成功回执能证明 accepted。
目标终态才使 unknown/accepted cancel 转 filled/canceled；空查询和
ORDER_NOT_FOUND 保持 unknown。原单终态已经确认而 cancel 仍未知时，
原单可以先落终态，但 cancel 继续占用准入直到自己的同源核对完成。
撤单超时后原单仍 live 可能长期阻塞，不能因此补发撤单。

启动时先扫描数据库 submitting，转 unknown，并完成 AI 冷暂停占位加载，
再允许聊天执行和两类自动交易准入。main.lifespan 不能把既有
_hydrate_paused_ai_trade 继续 create_task 后立即视为已就绪；改为有界等待，
失败沿用 note_hydration_failed 置盲区，同时保持写屏障关闭。初始化屏障
默认 false，只有新意图恢复和托管身份检查均成功才打开；总开关关闭也必须
恢复已有意图并向自动交易提供准入闸。数据库不可用不开放写能力。AI 暂停
功能被配置关闭时不能直接跳过遗留暂停表检查；读到遗留占位仍拒绝聊天。
运行中失败可在后续只读恢复完整成功后打开屏障，不靠计时或一次空 status。
核对器单例每 5 秒批量读取最多 10 条
到期未结记录、并发最多 2 个 GET，单笔 10 秒期限；连续 12 次未获确定状态
后改每 60 秒核对，并提示长期未确认。禁用新交易不停止已有订单的只读核对。
读不到订单不能认为未下单，也不能自动重发；未知状态持续占用准入。

每次核对先比较 intent 的环境、accountId、凭据指纹与当前客户端身份；
换 demo/live、凭据被改/删除均只记录阻塞原因、退避并保持非终态，禁止
拿当前环境查旧订单，更不通过清空绑定释放准入。重新选择别的聊天账户不
影响原 intent 使用它被冻结且仍有效的账户凭据；不能要求旧订单跟随新
binding。恢复原环境/原凭据后可继续只读核对，模型/浏览器不接触凭据。

执行任务必须由 execution 服务强引用的 registry 持有：先注册受管协程，
再在该协程中获取锁、CAS 和发送；HTTP/WS 不持有它的取消权。CAS 之后的
异常及取消在 finally 以短事务落 unknown；DB 不可写时仍保留 submitting
及内存阻塞，不能仅返回 unknown 而丢掉持久占用。核对器看到 submitting
且没有活跃 owner 时先 CAS unknown，再 GET；活跃 owner 的 submitting
不抢查。进程重启按全部 submitting 处理，绝不重新创建发送任务。任务
完成回调取出异常并释放内存句柄，不能让未观察异常破坏这条恢复链。

重复 execute、浏览器超时或断线只 GET 同一 intent，按钮文案为“查询状态”。
需要紧急处理时引导使用已有手工交易面板/交易所核对，不能提供“清空未知态
并重发”的快捷入口。本期不承诺交易所与 SQLite 之间的分布式恰好一次；
目标是单个 intent 至多一次发送，并用保守未知态防止自动重复。

### 3.6 平仓与撤单的兼容扩展

`build_close_order_payload` 保持旧默认限价行为，增加 keyword-only
`order_type='limit'`；为 market 时不带 px，其余规则共用。净仓必须
reduceOnly=true，双向仓用 side + posSide 锁定平仓方向。新增 close 服务
从最新真实仓位推导方向，不信任浏览器 pos；比较净仓符号与预览快照，
再验证 frozenSz <= abs(currentPos)。market 数量与 limit 数量均校验步进，
平仓不应用新增敞口名义上限，也不设置杠杆或自动撤销其他保护单。

`validate_close` 同步增加 keyword-only order_type='limit'，market 分支
禁止 px，仍检查正数/minSz/步进/最新仓位上界；不能仅改 builder 后调用
旧的无条件限价校验。聊天适配层额外比较 positionObservedSz 与身份，旧
调用方默认行为逐字保留。close 的 IntentExecutionContext 带同一
dispatch_guard，实际 payload 与 before_send 使用第3.5节的发送边界；
市场单不设置虚构价格来绕过校验。

新 close 服务以现有 ClosePositionInput 加内部 IntentExecutionContext
组合输入，调用 OKXTradeClient.place_order(..., allow_retry=False)，使用
相同 live_order_audit 写入格式。旧 close_live_position 路径不改变。
尾仓不满足普通订单 minSz 时明确拒绝并引导既有整仓平仓入口，不偷偷换端点。

`OKXTradeClient.cancel_order` 增加可选发送选项 DTO（allow_retry 与
before_send），缺省保留旧行为及调用方式；聊天显式 false。支持按 ordId
撤销没有 clOrdId 的手工普通单，核对器使用
intent 存下的 ordId；不为了复用只按 clOrdId 的旧帮助函数伪造标识。

cancel 同样在发送边界检查目标身份、期限、总闸及 shutdown，发送选项
必须透传到 _post。新 close/cancel 在写受理及不确定发送后均作废对应
positions_cache；核对首次发现成交、撤销或累计成交增长也作废。复用既有
按账户身份失效的方法，不改用户主动刷新路径，不让延迟中的旧缓存请求
重新成为最新快照。缓存失败不得把已发送归为 rejected 或引发重发。

官方接口依据：clOrdId 只保证当前未结订单范围的唯一性，历史不能依赖其
自动去重；净仓与双向仓的 posSide/reduceOnly 使用规则不同；撤单受理需要
后续订单状态确认。上述约束已于设计日核对
[OKX API 文档](https://www.okx.com/docs-v5/zh/) 与
[订单处理说明](https://www.okx.com/docs-v5/trick_zh/)。因此应用层必须保留
自己的永久意图身份和提交记录，不能把网络重试交给交易所承担。

### 3.7 结构化结果与历史恢复

ToolResult 增加可选 code 与 model_payload，现有 payload 继续保留旧 UI
action 语义。新交易工具的 model_payload 是白名单 JSON；summary 是中文
短摘要，禁止包含任何密钥。loop 将摘要与合法有界 JSON 共同放入
ToolResultBlock.content，模型由此读取字段而非猜测摘要数字。

ToolResultBlock 增加可选 display_payload、display_summary、code，
conversation 序列化为 payload、summary、code；另存 content 作为模型
文本（摘要加有界 JSON）。新记录不能再直接令 summary=content；读取旧
记录时 content 缺失回退 summary，新记录分别恢复两者。两个 LLM provider
仍只发送 content，不额外发送 display_payload/display_summary。
WS tool_result 和历史 blocks 都增加可选 payload/code，旧消息无字段仍正常。
工具级失败保留稳定 code，registry 不把新服务的结构化错误压成一段字符串。
未预期异常只给通用错误及关联 ID，不把 str(exc) 的原文送入模型。

卡片 payload 分三类：account_snapshot、trade_intent、auto_trade_context。
trade_intent 的历史 payload 只是当时快照。每次恢复会话通过 intent REST
取得权威当前状态；每个响应带 version，只能覆盖低版本，跨 conversation
响应丢弃。不能从历史“prepared”卡片直接推断仍可提交。
交易执行和核对不新增依赖 WS 的事件协议；REST 轮询负责后续变更，避免
已结束的 Agent run 仍占用 its event buffer。用户再次询问订单时工具查询
当前状态后回喂模型。

清空上下文的现有前端行为是新建 conversationId，订单不会因此消失：账户
条始终显示全局未结入口（本项目单用户，跨会话只读），关闭总开关也保留
这个恢复入口。全局列表项含原 conversationId/intentId；打开后显示原
订单及回执，跳回原会话才能创建撤单预览，不能改写订单会话归属。历史会话
REST 仍可查意图，即使消息表已清空。后端 DELETE 清空消息在同一准入锁下
使 binding.version 递增、prepared 变 dismissed；submitting 存在则返回
409，不影响已提交单。取消并 drain 当时 Agent run 后执行清空；任何迟到
prepare 必须因旧绑定版本拒绝。单纯停止模型只取消生成，不使已提交单撤销。

## 四、接口设计

### 4.1 Agent 工具清单

全部新 schema 使用 additionalProperties=false，具体字段通过 Pydantic
extra='forbid' 同源生成及校验。未知工具名、禁用工具和任意 raw HTTP 不可执行。

| 工具名 | 输入 | 输出与行为 |
|---|---|---|
| list_trading_accounts | `{}` | id、label、isDefault、configured；不返回 keyPreview/权限原文 |
| get_account_balance | `{}` | 绑定账户 totalEqUsd、USDT 可用额与币种明细；无 uid |
| get_account_positions | `{instId?, offset?:0, limit?:20}` | 真实持仓和 source/asOfTs，附 positionKey |
| get_open_orders | `{instId?, offset?:0, limit?:20}` | 普通/算法委托，kind 明确，算法只读 |
| get_recent_fills | `{instId?, limit?:20}` | 成交 ID、订单 ID、方向、成交价量、费用、时间 |
| get_order_status | `{intentId}` 或 `{instId, ordId}` | 严格互斥；intent 必须属于当前会话，使用其冻结账户；ordId 分支用当前绑定账户 |
| get_ai_trade_context | `{instId?}` | 白名单自动交易快照；非绑定账户不返回明细 |
| prepare_trade | open/close 判别联合，见下文 | 持久化预览，无交易所写入 |
| prepare_cancel_order | `{instId, ordId}` | 普通订单撤销预览，无交易所写入 |

prepare_trade 的 open 分支必填 action='open'、instId、direction=long|short、
ordType=market|limit、tdMode=cross|isolated、size={unit,value}。
unit=contracts|notional_usdt，value 是正十进制字符串；px 在 limit 必填、
market 禁止；tpTriggerPx/slTriggerPx 可选正十进制字符串。禁止 lever。

close 分支必填 action='close'、positionKey、ordType、closeSize；closeSize
是字符串 'all' 或 `{contracts:正十进制字符串}`；limit 必填 px，market
禁止 px。instId/posSide/mgnMode 从 positionKey 及当前真实持仓解析。
positionKey 为服务端按 `[instId,posSide,mgnMode]` 的 JSON 进行 base64url
编码，既不是秘密也不是授权；每次都须在绑定账户重新匹配，方向独立校验。

工具 category 使用 account 与 trading；设置中心和 TOOL_CATEGORIES 同步
添加中文分类。工具可用性取决于注册表、工具开关及能力配置三者，不硬编码
“9 个永远可用”。自动交易 decider/intel/discovery 不引入这些新工具。

### 4.2 REST 契约

后端统一 `/api` 前缀，表内路径相对此；浏览器使用 `/api/py` 代理。
响应时间为 UTC 秒，金额/价格/数量为十进制字符串或 null，ID 为字符串，
accountId 保持项目既有正整数。只读不得通过 GET 执行交易。

| Method / path | 请求 | 响应 |
|---|---|---|
| GET `/agent/trading/capabilities` | 无 | enabled、executionEnabled、recoveryReady、environment、supportedActions、accounts；executionEnabled 为配置及恢复门禁合成值 |
| GET `/agent/trading/unsettled-intents` | `cursor?,limit=20`，最大50 | 单用户全局非终态已提交意图，只读；返回原 conversationId，开关关闭仍可读 |
| GET `/agent/conversations/{cid}/trading-binding` | 无 | binding 或 null，含 version |
| PUT `/agent/conversations/{cid}/trading-binding` | `{accountId:null或正整数,accountMode:sim或live,expectedVersion}` | 新 binding；初次 expectedVersion=0 |
| GET `/agent/conversations/{cid}/trade-intents` | `cursor?,limit=20`，最大50 | items、nextCursor，createdTs/id 倒序 |
| GET `/agent/conversations/{cid}/trade-intents/{id}` | 无 | TradeIntentView，读取存储状态，不直接触发交易 |
| POST `/agent/conversations/{cid}/trade-intents/{id}/execute` | `{version,previewHash}` | 202 提交中/未知/非终态；200 已有终态；重复不重发 |
| POST `/agent/conversations/{cid}/trade-intents/{id}/dismiss` | `{version}` | 200 dismissed；已提交409；不等于撤交易所订单 |

新 router 定义在 api/agent_trading.py，prefix='/agent'，在 main 按现有
router 方式注册；继续受 OriginGuardMiddleware 保护。这里的 POST 不提供
面向 LLM 的对应工具。capabilities 可以在模型未配置时读取，但产生 intent
需要正常对话服务；列表/状态/提交既有 intent 不依赖模型可用。

TradeIntentView 固定形状：schemaVersion、id、conversationId、version、
action、status、account{id,label,environment}、bindingVersion、createdTs、
expiresTs、previewHash、preview、result、error、canExecute。
preview 包含 instId、direction、posSide、tdMode、ordType、sizeContracts、
price、referencePrice、notionalEstimateUsdt、marginEstimateUsdt、
leverageObserved、tpTriggerPx、slTriggerPx、warnings、observedTs。
还需 side、reduceOnly、positionObservedSz、positionIdentity、accountMode、
acctLv、posMode、instrumentConstraints（ctVal/ctValCcy/lotSz/minSz/tickSz）、
maxOpenContracts 和 protectionStatus。开仓/撤单无关的仓位字段为 null；
金额字段保持字符串。所有影响订单的字段都进入 previewHash。保护状态的
后续观测在 result 中，不能修改冻结 preview 导致 hash 漂移。
撤单 preview 额外 targetOrdId/kind，不相关金额字段为 null。
result 包含 ordId、clOrdId、exchangeState、accFillSz、avgPx、lastCheckedTs；
无回执时 result=null。previewHash 是冻结 preview 加绑定身份的规范 JSON
SHA-256，排序键、无多余空白、金额标准十进制表示，不包含易变状态。

错误采用既有 `{error:{code,message}}`，可加 details，前端用 code 分支。
400：AMBIGUOUS_TRADE、INVALID_SIZE、UNSUPPORTED_ACTION；404：ACCOUNT_NOT_FOUND、
INTENT_NOT_FOUND、ORDER_NOT_FOUND；403：AGENT_TRADING_DISABLED、
LIVE_TRADING_DISABLED、TOOL_DISABLED、TRADING_MODE_REQUIRED；409：
BINDING_CHANGED、ENVIRONMENT_CHANGED、ACCOUNT_CREDENTIALS_CHANGED、
PREVIEW_EXPIRED、PREVIEW_CHANGED、POSITION_CHANGED、AUTO_TRADE_OWNS_ACCOUNT、
TRADE_INTENT_IN_FLIGHT；429：TOO_MANY_TRADE_PREVIEWS；502：上游读取失败；
503：STATE_STORE_UNAVAILABLE。未知提交返回 intent 状态 unknown，不伪装成
可安全重试的通用 502。所有错误信息中文，保持机器码稳定。

补充固定码：400 PRECISION_UNSUPPORTED、POSITION_IDENTITY_UNAVAILABLE、
ACCOUNT_MODE_UNSUPPORTED；409 PREVIEW_STALE、IDENTITY_MISMATCH、
INTENT_VERSION_CONFLICT、INTENT_ALREADY_SUBMITTED；502 ACCOUNT_READ_FAILED、
ACCOUNT_SNAPSHOT_INCOMPLETE、LEVERAGE_UNAVAILABLE、ORDER_CAPACITY_UNAVAILABLE；
503 TRADING_RECOVERY_PENDING。CONTEXT_IDENTITY_UNAVAILABLE 是只读快照的
缺失原因，不触发写权限回退。开仓余额不足沿用 INSUFFICIENT_BALANCE。
Pydantic/JSON 格式校验在此 router 转中文 400 INVALID_TRADING_REQUEST，
details 只列字段名和规则码，不能把含凭据的 rejected input 原样返回。
intent 服务错误在新 errors.py 统一映射；共享 OKX 原始拒绝码先按明确
白名单分类，再保留稳定码或归上游错误，不以 str(exc) 解析，也不让旧
_map_trade_code 吞掉 guard 的本地否决。旧 API 全局错误格式不更改。
同一 intent 已提交的重复 execute 返回现状态前仍须校验 cid/id 归属；
仅可跳过首次执行资格，不跳过资源定位与 Origin 检查。
读 intent 的 REST 返回存储真值及 lastCheckedTs/核对错误，不要求当前
binding 仍相同；工具 intent 分支若需要刷新，遵守原账户环境指纹闸。

### 4.3 WebSocket 与 CLI

沿用 `/api/ws/agent/{conversation_id}`，user_message 的增量字段见第 3.2 节，
tool_result 增量字段见第 3.7 节。ready/history/resume/heartbeat 的原语义不变；
不得增加“收到 confirm 文本就执行”的 WS 分支。客户端停止生成只取消 Agent
run，不能取消已提交交易或删除意图。无新增交易 CLI。

## 五、数据模型、事务与配置

### 5.1 新表

| 表 | 字段及约束 |
|---|---|
| agent_trade_bindings | conversation_id VARCHAR(64) PK；account_id INTEGER nullable；environment VARCHAR(8)；account_mode VARCHAR(8) NOT NULL default sim；version INTEGER NOT NULL；updated_ts INTEGER |
| agent_trade_intents | id VARCHAR(36) PK；conversation_id VARCHAR(64) index；client_message_id VARCHAR(36)；tool_use_id TEXT；account_id INTEGER；environment VARCHAR(8)；binding_version INTEGER；credential_fingerprint CHAR(64)；action VARCHAR(16)；status VARCHAR(32) index；version INTEGER；request_hash CHAR(64)；preview_hash CHAR(64)；request_json TEXT；preview_json TEXT；result_json TEXT nullable；cl_ord_id VARCHAR(32) unique nullable；target_ord_id TEXT nullable；error_code VARCHAR(64) nullable；created_ts/expires_ts/updated_ts INTEGER；next_check_ts INTEGER nullable；check_attempts INTEGER default 0 |
| agent_trade_events | id INTEGER PK autoincrement；intent_id VARCHAR(36) index；intent_version INTEGER；kind VARCHAR(32)；detail_json TEXT；created_ts INTEGER；UNIQUE(intent_id,intent_version) |

intents 额外 UNIQUE(conversation_id,client_message_id,request_hash)，索引
(conversation_id,created_ts,id) 与 (status,next_check_ts)。JSON 均只含白名单
字段，输入数值经规范化后才落盘，不存整个工具上下文、账户 ORM 或原始异常。
action/status/environment 加 CHECK；schemaVersion=1 放在 JSON 内。
外键关系由 repository 显式校验；不顺手开启全库 foreign_keys。

新增模型放 db/agent_trade_models.py，db/session.py 在 create_all 前导入。
只新增表，不改存量表、无历史回填；初次绑定和意图首次使用时惰性产生。
init_db 连续运行两次必须成功。回滚只禁用新入口，保留审计和未结记录；
有未结订单时不可退回完全不识别新表的旧二进制后再启动自动交易。

### 5.2 状态机与原子性

```text
开仓/平仓：
prepared -> dismissed | expired | rejected | submitting
submitting -> rejected | unknown | accepted
unknown -> accepted | partially_filled | filled | canceled
accepted -> partially_filled | filled | canceled
partially_filled -> filled | canceled

撤单：
prepared -> dismissed | expired | rejected | submitting
submitting -> rejected | unknown | accepted
unknown -> filled | canceled
accepted -> filled | canceled
```

撤单 unknown 可在仍存活的原 POST 成功回执到达时变 accepted，普通 GET
不能触发此转换。首次回执在提交 owner 内落 accepted，之后才允许核对；
重启只剩 unknown 时没有 owner 回执。已终态记录不被迟到回执倒退。

expired 在读取、提交、清扫时惰性转换；expiresTs 只影响 prepared，不能
让 accepted 的限价单自动过期或撤销。所有转换使用
UPDATE ... WHERE id=:id AND version=:expected AND status IN (...)
并检查 rowcount=1，同事务插 event，commit 后才发起外部副作用。
terminal = dismissed/expired/rejected/filled/canceled；终态不可重新提交。
canceled 可能有部分成交，必须保留 accFillSz 和 avgPx，不标成“零成交”。

CAS submitting 成功即意味着本次执行可能发送；进程崩溃的提交即便实际尚未
出网也恢复成 unknown。以少执行为代价避免多执行。核对只会增加 version，
accFillSz 不减少；过期的 GET 结果不得覆盖更新版本。
核对器先把 submitting 转为 unknown 的启动恢复必须完成后才对外就绪，
不可以用后台 task 延时恢复造成写入空窗。

同意图重复请求的数据库去重是真实 SQLite 唯一约束，不以 asyncio.Lock
代替持久去重。跨不同 intent 的全局互斥依赖项目既有单进程部署；不新增
多 worker 支持。DB busy 或审计失败在出网前阻止提交，出网后保留 unknown。
events 记录 requested/dispatch_started/accepted/rejected/reconciled/dismissed/
expired 等有限事件，不复制每次读取产生的相同状态，也不写模型推理全文。

同状态但 accFillSz/avgPx/关键回执变化必须 CAS 增 version 并写 event；
纯 lastCheckedTs、重试计数和 next_check_ts 更新也使用版本条件，增加
version 但不插无意义事件。events 的唯一约束表示每版本至多一事件，不
要求连续编号；前端以 version 排序这些元数据更新。累计成交不减少，未知
数量保持 null；部分成交缺均价仍能显示“均价待核实”，不能补造成交价。
身份/成交量异常的 GET 不降级已受理原单为 unknown：保留旧状态和锁占用，
记录 reconcileError；本来为 unknown 的维持 unknown。后续可信观测清除
错误，永远不能用身份不匹配的响应推进终态。

代码在启动恢复和状态转换时必须区别 action=cancel：只查 target_ord_id，
普通下单先以 ordId（已知时）查询，否则按自己永久唯一的 clOrdId 查询。
核对响应的账户由客户端固定，instId/ordId/clOrdId/side/posSide/原始 sz
与预览可对应字段不一致时保持原非终态并记录 IDENTITY_MISMATCH，不采纳
错误订单。对从 prepared 到提交前本地拒绝的记录，cl_ord_id 可以为空。

### 5.3 配置

新增 `AGENT_TRADING_ENABLED=true`（账户及意图功能总开关）和
`AGENT_TRADING_EXECUTION_ENABLED=true`（新入口执行开关）。这两个默认值
不会自行下单：仍必须有效绑定、具体预览卡用户提交、工具启用及既有
LIVE_TRADING_ENABLED=true。升级时不创建 binding、不执行历史文字指令。
上述为最终通过发布门禁后的默认值。开发/未通过全套验收的装配环境必须
显式配置 EXECUTION_ENABLED=false；即使人为开 true，recoveryReady=false
也拒绝 execute。不能用配置默认值取代启动恢复屏障。
关闭执行开关保留查询、预览和已提交单核对；关闭总开关隐藏新工具、拒绝
新预览和提交，保留历史与只读核对。关闭期间不自动撤单、平仓或停自动交易。

60 秒预览期限、50 bps 市价预览漂移、5 秒复核新鲜度、15 秒读请求期限、
10 秒单次写调用期限、5/60 秒核对间隔和 24 KiB 模型结果上限放新模块命名
常量，测试可注入时钟；本期不扩张设置中心成一组高级参数表。
执行任务总期限 25 秒（含准入、复核、单次写最多 10 秒），UI 请求超时
30 秒。受管任务完成准入 CAS 后尽快返回 202；重复请求查询同一记录，
不能不断创建新 owner。写请求超过期限或被取消只转 unknown，绝不补发。
HTTP 客户端断开不取消发送/核对任务。

关停先关闭进程内接单闸，禁止新 owner；新增 drain 步骤放在现有
weight_train 之后、okx_rest/okx_pool/db_engine 之前，保留既有索引断言的
关停步骤相对顺序。内部 drain 最多 min(5秒, shutdown_step_timeout_s/2)，
有界取消与收尾也计入该预算；未发送且可证明的拒绝，已发送/不明的保留
unknown 或持久 submitting。所有任务在最后发送边界再次检查 closing，
超时遗留任务不能稍后出网；核对任务也必须取消并收束，DB 关闭前回调完成。

桌面端须核查既有 AGENT_ 前缀透传；不新增依赖或新服务进程。配置例子写入
.env.example，并在 README 说明“界面本地模拟”和“OKX demo”的差别。

## 六、界面与交互设计

普通对话工具栏下增加单行账户条：账户名称、环境文字标签、切换入口和数据
时间。保持现有主图与 Agent 面板布局；右侧窄屏不挤压原有“清空上下文”和
自动交易控件，账户条独立成行。账户切换明确提示“旧预览已失效”，不更改
已有订单归属；新会话不继承旧 binding 的执行资格。

账户条还显示“未结交易 N”入口，由全局未结 REST 提供，清空上下文/新会话/
重启后均能找回原单。恢复列表支持只读详情和跳回原会话；功能总闸关闭时
仍可查询，不能以空的新会话列表冒充全账户无未结单。全局模式切换与账户
绑定 PUT 由同一前端控制器协调，事务未完成时禁用交易卡；失败保留原模式
并显示原因，不能仅更新 Zustand 显示而未更新 binding。

查询工具卡默认显示关键信息：账户/环境、时间、持仓方向、张数、均价、
未实现盈亏；明细可展开，算法订单带“保护委托，只读”文字。加载、真实
空仓、未配置、权限失败、取数失败各有不同文案。不用空表覆盖网络错误，
保留历史数据时醒目标注“上次查询”。

预览卡固定顺序为账户与环境、动作与标的、张数与订单类型、价格、估计名义/
保证金、现有杠杆、止盈止损、有效期、实际约束提示、操作。主按钮写具体
“提交买多”“提交平仓”“提交撤单”；次按钮“放弃预览”。不得只写“确定”。
金额区分估计值和实际成交值，不用绿色“完成”表示 accepted。

状态文案固定为：prepared=待提交；submitting=提交中；unknown=结果待核实；
accepted=委托已受理；partially_filled=部分成交；filled=已成交；
canceled=已撤销（如有成交仍显示）；rejected=未执行/已拒绝；expired=预览
已过期；dismissed=已放弃。按钮禁用依赖服务端 canExecute，客户端倒计时
只作提前提示，不作为有效性真源。unknown 无“重试下单”按钮。

卡片通过 REST 每 2 秒读取非终态 intent，组件不可见时停轮询，重新可见先
刷新；后端核对器独立运行。查询缓存键包含 conversationId/intentId，
binding 含 version；同页多个卡片轮询可合并为列表读取。版本倒退和会话
切换的迟到响应丢弃，延续现有 useAgentConversation 的 generation 守卫。

采用已有 surface/border/text/long-ink/short-ink 变量；状态必须有文字，
不能只靠红绿。使用原生 button、明确 aria-label、aria-live='polite'；
键盘 Enter 仅发送输入框文字，不提交交易卡。放弃后焦点回卡片标题，错误
聚焦卡内说明，不弹无限 toast。验证 360/768/1280 像素宽、明暗主题和键盘
操作；无新增第三方 UI 库、品牌图或大面积动画。

## 七、文件 / 模块变更计划

以下为下游实现的完整计划；本节点实际仅创建本 spec。M 为修改，A 为新增。
单文件超过 800 行的既有文件只做接线，新增函数按项目规范拆入新模块；
若现存豁免仍不能覆盖新增接线，在 `.claude-index/config.md` 明确登记理由。
以下已同步 v2 评审必需的兼容扩展；新 guard 的判定逻辑放独立模块，
旧 trading.py 仅增加证据传递及发送回调接线，不复制整个订单服务。

| 操作 | 文件 | 意图 |
|---|---|---|
| A | `services/quant/app/agent_trading/__init__.py` | 公开服务入口，不在 import 时启动任务 |
| A | `services/quant/app/agent_trading/schemas.py` | 工具/REST DTO、严格联合输入与视图 |
| A | `services/quant/app/agent_trading/binding.py` | 绑定 CAS、账户解析、凭据指纹 |
| A | `services/quant/app/agent_trading/read_service.py` | 真实账户读取与有界结果 |
| A | `services/quant/app/agent_trading/auto_context.py` | 自动交易快照白名单投影 |
| A | `services/quant/app/agent_trading/preview.py` | 数量/价格规范化、预览散列与前置验证 |
| A | `services/quant/app/agent_trading/intent_service.py` | 创建、去重、放弃、读取意图 |
| A | `services/quant/app/agent_trading/admission.py` | 未结意图准入检查，避免 manager 循环依赖 |
| A | `services/quant/app/agent_trading/execution.py` | 提交 CAS、复核、后台提交任务及结果归因 |
| A | `services/quant/app/agent_trading/dispatch_guard.py` | 冻结 payload 比较、证据新鲜度、实际发送边界 |
| A | `services/quant/app/agent_trading/errors.py` | 新服务稳定中文错误及原始拒绝码分类 |
| A | `services/quant/app/agent_trading/reconcile.py` | 启动恢复、只读核对、退避、关闭清理 |
| A | `services/quant/app/agent_trading/result_view.py` | 模型/前端安全白名单、截断与中文摘要 |
| A | `services/quant/app/db/agent_trade_models.py` | 三张新表、约束和索引 |
| A | `services/quant/app/db/agent_trade_repositories.py` | 短事务、CAS、去重和事件原子写入 |
| M | `services/quant/app/db/session.py` | create_all 前注册新表 |
| A | `services/quant/app/api/agent_trading.py` | REST 薄路由与稳定错误映射 |
| M | `services/quant/app/main.py` | 注册路由、就绪前恢复、核对任务生命周期 |
| M | `services/quant/app/config.py` | 两个 AGENT_TRADING 配置布尔项 |
| A | `services/quant/app/agent/tools/account_tools.py` | 七个只读工具包装 |
| A | `services/quant/app/agent/tools/trade_tools.py` | 两个只创建预览的工具 |
| M | `services/quant/app/agent/tools/base.py` | 增量调用上下文及结果字段 |
| M | `services/quant/app/agent/tools/registry.py` | 注册新工具、结构化错误和能力过滤 |
| M | `services/quant/app/agent/manager.py` | 准确计算新工具启用集，传入消息身份 |
| M | `services/quant/app/agent/loop.py` | 每调用上下文、有界模型结果及可恢复 payload |
| M | `services/quant/app/agent/prompts.py` | 去掉仅模拟身份；注入已绑定账户、能力及交易纪律 |
| M | `services/quant/app/agent/llm/base.py` | ToolResultBlock 增量 display_payload/code |
| M | `services/quant/app/agent/conversation.py` | 新字段序列化并兼容老历史 |
| M | `services/quant/app/api/agent.py` | user_message 身份校验、新字段传递及 clear 失效协作 |
| M | `services/quant/app/ai_auto/manager.py` | 只读 context 出口及 start/resume 未结意图门禁 |
| M | `services/quant/app/live_auto/manager.py` | 启动准入对话意图门禁 |
| M | `services/quant/app/okx/order_builder.py` | close builder/validator 可选 market、内部 guard 字段，保持旧默认 |
| M | `services/quant/app/okx/trading.py` | place_live_order 实际证据与 guard 接线，旧 risk_guard 仍生效 |
| M | `services/quant/app/okx/private_client.py` | 杠杆及可开数量只读方法，复用签名/连接池 |
| M | `services/quant/app/okx/trade_client.py` | cancel 发送选项与普通/算法委托分页 DTO，保留旧缺省 |
| A | `services/quant/app/okx/intent_close.py` | 固定身份、指定数量、仅减仓的服务适配 |
| A | `apps/web/lib/agentTradingTypes.ts` | 绑定、快照和意图公开 interfaces |
| A | `apps/web/lib/agentTradingApi.ts` | REST 方法、超时与明确的只查询恢复 |
| M | `apps/web/lib/agentTypes.ts` | 工具分类、上下文和 payload 可辨识联合 |
| M | `apps/web/lib/agentToolMeta.ts` | 中文工具名及账户/交易分类 |
| M | `apps/web/lib/agentReducer.ts` | 事件和历史 payload 归一，不重复卡片 |
| M | `apps/web/components/Agent/useAgentConversation.ts` | clientMessageId 重试复用、accountMode 传递 |
| M | `apps/web/components/Agent/AgentPanel.tsx` | 装配独立账户条与卡片上下文 provider |
| M | `apps/web/lib/store.ts` | 模式切换与 binding 控制器协调，失败保留原模式 |
| M | `apps/web/components/Header/AccountModeSwitch.tsx` | I01：等待 binding CAS 成功后切换图表模式，展示等待和失败 |
| M | `apps/web/components/Header/AccountModeSwitch.test.tsx` | I01：失败保留原模式、提交期间阻止重复切换 |
| M | `apps/web/components/ui/contrastTokens.source.test.ts` | I03：新增交易 HTTP 客户端后更新 fetch 调用数量契约，保留逐调用超时检查 |
| M | `apps/web/components/Agent/ToolCallCard.tsx` | 类型安全分派三类新卡片，保留旧摘要卡 |
| A | `apps/web/components/Agent/trading/TradingContext.tsx` | 会话作用域绑定/intent 缓存，避免层层传参 |
| A | `apps/web/components/Agent/trading/TradingAccountBar.tsx` | 账户选择、真实环境和模式提示 |
| A | `apps/web/components/Agent/trading/AccountSnapshotCard.tsx` | 账户、持仓和订单事实展示 |
| A | `apps/web/components/Agent/trading/AutoTradeContextCard.tsx` | 最近决策/门禁/托管状态只读展示 |
| A | `apps/web/components/Agent/trading/TradeIntentCard.tsx` | 预览、提交与状态反馈 |
| A | `apps/web/components/Agent/trading/UnsettledTrades.tsx` | 跨会话未结只读列表、详情与返回原会话 |
| A | `apps/web/components/Agent/trading/useTradeIntent.ts` | 版本守卫、轮询、只读恢复 |
| A | `services/quant/tests/test_agent_trading_reads.py` | 真实数据错误极性、账户隔离、截断 |
| A | `services/quant/tests/test_agent_trading_intents.py` | schema、规范化、去重、期限、状态机 |
| A | `services/quant/tests/test_agent_trading_execution.py` | 一次发送、平仓锁侧、超时归因 |
| A | `services/quant/tests/test_agent_trading_recovery.py` | 真实 SQLite 重启恢复、故障注入 |
| A | `services/quant/tests/test_agent_trading_admission.py` | 聊天提交与 AI/live-auto 启动/恢复竞态 |
| A | `services/quant/tests/test_agent_trading_api.py` | REST、Origin、账户绑定与会话归属 |
| A | `services/quant/tests/test_agent_trading_wire.py` | WS/历史/provider round-trip、敏感字段扫描 |
| A | `services/quant/tests/test_agent_trading_context.py` | 自动交易投影和工具能力隔离 |
| A | `services/quant/tests/test_agent_trading_review_contracts.py` | A29–A44 的发送、恢复、精度及兼容交叉回归 |
| M | `services/quant/tests/test_main_shutdown.py` | 新任务关闭在 DB/OKX 资源释放前 |
| M | `services/quant/tests/conftest.py` | I02：直接调用管理器的旧单元测试声明启动完成并隔离准入状态；启动屏障测试保留冷启动状态 |
| M | `services/quant/tests/test_ai_trade_pause_manager.py` | I02：将聊天准入模块纳入托管状态消费者白名单，保留非空互斥断言 |
| M | `services/quant/tests/test_agent_tool_registry.py` | 新工具与执行工具不存在的契约 |
| M | `services/quant/tests/test_agent_tools_api.py` | 设置工具枚举及禁用语义 |
| M | `apps/web/lib/agentReducer.test.ts` | 新旧 payload/history 兼容 |
| M | `apps/web/components/Agent/useAgentConversation.test.tsx` | 重试消息 UUID 与会话切换迟到响应 |
| A | `apps/web/lib/agentTradingApi.test.ts` | 超时不重发、版本查询 |
| A | `apps/web/components/Agent/trading/TradingAccountBar.test.tsx` | 绑定切换、模式与环境 |
| A | `apps/web/components/Agent/trading/TradeIntentCard.test.tsx` | 双击、过期、未知、部分成交、键盘 |
| A | `apps/web/components/Agent/trading/AccountSnapshotCard.test.tsx` | 空仓与失败、单位、截断及无障碍 |
| A | `apps/web/components/Agent/trading/UnsettledTrades.test.tsx` | 清空会话/关闭总闸后的未结单可达性 |
| M | `.env.example` | 新开关及准确含义 |
| M | `README.md` | 对话交易用法、模式差异和状态恢复 |
| A | `.claude-index/config.md` | HEAD 未跟踪；仅提交本次必要的大文件接线豁免，保留工作树其他历史记录 |

不修改 api/trading.py 的持仓读取，不扩张 decider 的工具集合，不修改
positions_cache 的现有语义。若 provider 适配器通过显式字段构造工具结果，
无需改它们；第八节测试必须证明 display_payload 不泄漏到 provider wire。

## 八、测试与验收标准

本节点只验证设计文档；以下测试由实现节点编写和运行，不能把它们标成已通过。
交易所使用可记录请求的 httpx MockTransport，账户/意图/CAS 用临时真实
SQLite 文件及真实 init_db，不 mock 数据库约束、事务或签名逻辑。
测试不向真实账户发单，不从开发者 .env 读取凭据。

| 编号 | 情景与通过条件 |
|---|---|
| A01 | 注册 9 个新工具；普通对话可查询；decider/intel/discovery 工具集合不增加交易写能力 |
| A02 | 无绑定、坏凭据、未知账户、跨会话 intent、绑定版本变化均有稳定错误，无交易所写请求 |
| A03 | 真实空仓与 429/超时/鉴权失败不同；null 余额不变零；主动刷新确实出网 |
| A04 | 所有 query 明示账户、demo/live、source、秒级时间和数量单位，算法订单只读 |
| A05 | bool/NaN/Infinity/额外字段/未知 action/低于 minSz/越限全部拒绝；取整不向上增风险 |
| A06 | notional 和 contracts 各自换算正确；价量最终序列化与预览相同；不自动设杠杆 |
| A07 | 净多平仓 sell+reduceOnly，净空 buy+reduceOnly；双向 long/sell、short/buy 锁侧正确 |
| A08 | “全仓”预览后持仓增大只平原量；减少、消失、翻方向则拒绝；市价半仓不会调用 close-position |
| A09 | 预览、放弃、模型输出“确认”、WS 重播、UI action 均无交易所写请求 |
| A10 | 同消息同参数重复工具调用仅一 intent；20 个并发 execute 对同 intent 仅一次交易所 POST |
| A11 | 两个不同 intent 同时执行仅一个获准；启动 AI、live-auto 与 execute 并发也仅一个取得准入 |
| A12 | running/paused/冷暂停/hydration unknown 都阻止聊天写；未结聊天意图阻止 AI resume/start |
| A13 | 发单前 DB commit 失败不出网；发送后断线、空回执、审计失败保持 unknown，绝不重发 |
| A14 | 重启在 CAS 后、发送中、成功回执后落库前三个故障点均恢复核对，未完成恢复不开放写入口 |
| A15 | 交易所已成交但首次查询不存在：仍只 GET，不补单；长期 unknown 仍保留准入占用 |
| A16 | 撤单响应成功但订单 filled：展示成交；partially_filled 后 canceled 保留真实成交量价 |
| A17 | 显式业务拒单和未知网络失败状态不同；accepted 不显示成交价或“已成交” |
| A18 | 执行前工具禁用、总闸关、demo 切换、账户凭据变化、预览过期、价格漂移均阻止提交 |
| A19 | 关闭新功能仍核对已有订单；关闭程序先 drain 执行/核对再关 DB/OKX，无孤儿任务 |
| A20 | WS、DB 历史、模型工具结果、UI 轮询恢复一致；旧历史不报错；低 version 不覆盖新状态 |
| A21 | accountMode=sim 或缺失无法生成交易意图；后端 demo 环境与前端本地模拟不混用 |
| A22 | 密钥/API key/passphrase/uid/完整提示/原始异常种入哨兵，模型 wire、REST、WS、日志均不含哨兵 |
| A23 | Origin 不允许时新 POST/WS 无 DB 副作用；保持现有无 Origin 客户端的边界行为 |
| A24 | 360/768/1280 宽、明暗主题、Tab/Enter、屏幕阅读状态可用；Enter 发送文字不提交交易卡 |
| A25 | 开新会话不继承执行绑定；清空/断线/停止模型不撤销真实订单；重连先拉当前 intent |
| A26 | 自动交易原门禁、轨迹、journal/evidence、旧手工交易 API 的定向回归保持通过 |
| A27 | 自己的限价单 accepted 时可生成并提交该单撤销；第二个撤销不能并发发送；原单与撤单两记录终态一致 |
| A28 | 外部普通单撤销按 ordId 查单；未知 ordId 不放行；错误 instId/side/sz 的查单响应不能解除 unknown |
| A29 | 外层复核后切 posMode/tickSz/附单参数或行情、阻塞限流超过5秒，实际 POST 数为0；guard 与 risk_guard 任一否决均拒绝，旧调用不受影响 |
| A30 | 用可控 barrier 阻塞 AI 冷暂停加载，同时 start/resume/execute 全部拒绝；hydration 失败及暂停功能关闭但有遗留记录仍不放行 |
| A31 | 重启把 demo 换 live 或换/删凭据，核对无错误身份 GET、未结继续占用；绑定切到另一账户不会改旧单身份；恢复原配置后正常核对 |
| A32 | 一轮先读A后切B，迟到 prepare 无法为B生成卡；live预览后切sim及跨页签旧version提交失败；缺mode的老客户端仍只读 |
| A33 | cancel unknown 查目标 live/部分成交仍 unknown，已受理 cancel 保持 accepted；只有目标 filled/canceled 才终结；始终至多一次 cancel POST |
| A34 | CAS后HTTP断开、任务异常、回执后DB不可写分别保留占用；同进程无owner的submitting能转unknown；同态部分成交增长正确递增version且不回退 |
| A35 | 同消息重试时行情/all数量已变仍同intent；过期原消息不延寿，新消息可新建；21个并发不同预览最多20个有效；账户版本变化无错误去重回显 |
| A36 | 无持仓首次开仓读取真实leverage-info/max-size；错误posSide、缺availEq、组合保证金、余额不足和可开上限不足均无POST；全链无set-leverage |
| A37 | market平半仓无需px且校验minSz；同向同量但仓位身份变化拒绝；预览10张平2张、现仓9张也拒绝；净仓和双向仓旧限价测试保持通过 |
| A38 | 两页普通单及oco双腿无遗漏/重复；达到上限或第二页失败显示partial且total=null，不声称全账户；截断集合无法用于完整风险判断 |
| A39 | 新content/summary/payload经WS、DB、两provider往返，UI摘要无JSON污染，模型仍含结构化事实；旧history兼容 |
| A40 | 清空后新cid仍看见旧accepted/unknown并可返回原会话；后端clear使旧preview失效、迟到prepare拒绝，已提交意图/event不删除 |
| A41 | 相同accountId但环境/指纹缺失或不同不导出自动交易明细；settling/stopping/未知状态仍占用聊天写准入 |
| A42 | 未就绪即使配置true仍无交易POST；开发默认装配false；全部恢复就绪才开放能力；关停后滞留发送任务不得越过closing guard |
| A43 | 附止损的主单accepted/部分成交不显示已保护；close/cancel受理、未知或后续成交变化均使共享仓位缓存失效，无自动补挂 |
| A44 | schema英文/含敏感输入异常统一中文稳定码；guard拒绝不被旧映射拍平；跨cid重复execute仍404；capability关闭仍可查未结状态 |

后端定向命令（仓库根 PowerShell，每条分别执行）：

```powershell
services/quant/.venv/Scripts/python.exe -m pytest services/quant/tests/test_agent_trading_reads.py services/quant/tests/test_agent_trading_intents.py services/quant/tests/test_agent_trading_execution.py services/quant/tests/test_agent_trading_recovery.py services/quant/tests/test_agent_trading_admission.py services/quant/tests/test_agent_trading_api.py services/quant/tests/test_agent_trading_wire.py services/quant/tests/test_agent_trading_context.py -q
services/quant/.venv/Scripts/python.exe -m pytest services/quant/tests/test_agent_trading_review_contracts.py -q
services/quant/.venv/Scripts/python.exe -m pytest services/quant/tests/test_agent_loop.py services/quant/tests/test_agent_ws.py services/quant/tests/test_agent_tool_registry.py services/quant/tests/test_agent_tools_api.py services/quant/tests/test_trading_api.py services/quant/tests/test_trading_close.py services/quant/tests/test_trading_open_orders.py services/quant/tests/test_main_shutdown.py -q
pnpm --dir apps/web test lib/agentReducer.test.ts lib/agentTradingApi.test.ts components/Agent/useAgentConversation.test.tsx components/Agent/trading
pnpm --dir apps/web typecheck
pnpm --dir apps/web build
```

全项目集成门禁由实现/最终评审节点运行 `pnpm test`；如果现有脏工作树引起
不相关失败，记录失败用例、复现命令和基线证据，不通过删除测试或重置用户
改动“修绿”。新增 UI 在本机用隔离假交易所后端做人工/浏览器冒烟：查询
持仓 -> 生成具体预览 -> 提交 -> 部分成交 -> 刷新恢复 -> 最终成交；另做
unknown 和自动交易暂停冲突各一条。记录截图与请求计数作为实施证据。

验收核心：用户能从普通对话取得真实账户证据、复用自动交易研判并提交
具体指令；每笔委托可追溯到会话、预览和交易所订单；没有伪造持仓、越权
切换账户、重复出单、把受理当成交或绕过托管门禁的路径。

## 九、实施顺序与交付门禁

1. 建 schemas、模型与 repository；先完成真实 SQLite create_all、CAS、唯一
   约束和重启用例，再接任何写端点。定义 fixture，冻结 A02/A10/A14。
2. 实现 binding 与 read_service，注册七个读工具，完成有界模型 JSON、
   持久化 payload 与前端只读卡；验证 A01–A04/A20–A23。
3. 实现 preview/intent_service 和两个 prepare 工具；在禁止执行条件下
   完成字段歧义、数量精度、过期及 UI 卡片。验证 A05/A06/A09/A18/A21。
4. 实现 admission、execution、dispatch_guard、intent_close 与撤单兼容
   参数；通过 A07/A08/A10–A13/A16/A17/A29/A33/A34/A37，execute 仍禁用。
5. 装配启动恢复和核对器，接 AI/live-auto 准入与只读 context；执行崩溃、
   竞态、暂停和关闭用例 A11–A15/A19/A26/A30/A31/A41/A42，证明不改变
   原决策输出；此时只在隔离假交易所环境开启 execute 完成端到端验证。
6. 完成 UI 账户条、REST 轮询和会话生命周期，验证 A24/A25，运行前后端
   定向与集成门禁，记录兼容性和截图。更新配置说明及 README。

第6步还须通过 A32/A35/A36/A38–A40/A43/A44。只有 A01–A44、旧 API
定向回归、完整 pnpm test、类型检查与构建都有结果后，才将发布配置默认值
恢复为 true。门禁失败不得发布可执行版本，不能把“已接路由”当发布完成。

每步先写所需的行为测试，再实现该机制；不要先铺一个可实际发单但尚无
恢复保障的工具。每步只修改计划中的文件，遇到确需新增文件或修改范围
时在本文“实施过程发现的方案缺陷”节写明具体原因并修订清单。设计节点
不运行真实账户测试、不提交 git；最终提交由任务图的最后节点处理。

## 十、风险与缓解

| 风险 | 缓解与残余限制 |
|---|---|
| 提示注入或模型捏造确认 | 模型只创建预览；无 execute 工具；后端按冻结意图提交；不把新闻/网页文字当用户授权 |
| 发单成功但本机未记账 | 出网前持久提交身份；发送后未知态仅查单；保留全局准入，不能保证即时恢复 |
| 自动交易与聊天同时管理仓位 | 同一准入锁和持久未结集合；暂停/未知也拒绝；本期不支持无缝接管托管仓 |
| 用户在交易所或旧手工面板同时交易 | 最新复核、只减仓、冻结数量；外部交易仍可能改变余额/成交，回执如实报告；不宣称全账户串行 |
| 相同交易所账户配置成多条 key | 全局写入互斥不依赖 accountId 去猜交易所身份；读取仍按绑定凭据隔离 |
| 数据来源不一致或过期 | 交易所真值和本地策略快照分开；明确时间/缺失；失败不冒充空仓 |
| 未知态长期阻塞 | 退避查单、显示订单身份与最后核对时间，引导人工核对；不自动释放或重发 |
| 限价单长期占用准入 | 这是保守一期取舍；用户可在对话或既有交易面板撤单，核对终态后再启动自动交易 |
| 交易已终结但持仓/保护仍存在 | 准入锁仅串行聊天未结订单；不宣称 filled 后仓位已无人管理。启动自动交易仍受现有真实仓位/保护委托门禁，聊天不自动转移仓位归属 |
| 预览造成错误的成交保证 | 市价显示估计和漂移检查含义；实际数量/均价来自成交回执，不承诺滑点上限 |
| 凭据或模型上下文泄漏 | 固定白名单 DTO、错误脱敏、输出哨兵测试；现有 DB 明文凭据存储另案处理 |
| 大文件与在途功能冲突 | 新代码拆模块，仅在 loop/main/manager 加接线；保留当前 journal/evidence 与用户改动 |
| 单进程锁被错误用于多 worker | 保持现有桌面单后端部署约束；多 worker 需独立数据库租约设计，本期不支持 |
| 数据累积与回滚 | 意图/事件不自动删除，以保留资金审计；只存小型规范化字段，不录 token 流；未来留存策略另案 |

## 十一、设计节点自检与实施过程发现的方案缺陷

设计节点检查：需求与现状分开；路径锚点已按当前工作树核实；接口、字段、
状态机、文件清单、配置、测试命令和验收判据均给出。本文的 TTL、轮询间隔
与保守互斥是本次选定的产品/工程默认值，不冒充交易所强制规定。

本次没有执行实现测试，也没有发起任何交易。v2 已对照当前源码完成逐节
审查，验证内容限文档结构、契约一致性、文件计划和问题闭环；下游实现
及最终评审必须提供未知提交恢复、旧 API 兼容及单进程准入的实际测试证据。

实施过程发现的方案缺陷：本节点尚未实施，暂无；后续记录应包含问题、
影响、修订决策、相关文件和新增验收用例，不能仅写“已处理”。

## 评审结论

**有条件通过**。v2 的 4 项 P0、12 项 P1 设计问题均已落实到正文、文件
计划与 A29–A44 验收；R17 的保守可用性代价明确接受。当前 FastAPI/
SQLAlchemy/SQLite 与 React 技术栈能够实现该方案，无需引入消息队列、
分布式租约或独立执行服务。准予按第九节开发，不代表交易功能已上线。

实施及发布条件如下，均为已确定设计的验收门禁，不是尚待决定的问题：

1. 在真实 SQLite 与可计数假交易所上通过 A01–A44，特别是实际 POST
   边界、冷启动屏障、环境切换恢复及撤单未知态；禁止以 mock 掉事务替代。
2. 全部恢复和核对机制完成前保持执行开关关闭；正式开放须有前后端定向、
   集成、类型检查、构建及界面冒烟证据，不以本文审查替代运行验证。
3. 保留工作树已有 journal/evidence 等改动，登记必要的大文件接线豁免，
   不放宽其他规范。旧下单/平仓/自动交易行为须经兼容测试证明。
4. 单进程、全局未结互斥、保护状态未核实及外部交易竞态必须按正文向用户
   明示；未知状态不得自动清零、重发或跨环境核对。回滚遵守未结订单门禁。

## 实施过程发现的方案缺陷

### I01：全局模式按钮存在独立的图表模式副作用

`AccountModeSwitch.selectSim` 原本依次同步更新账户模式及图表模式。
只按原清单把 store 的账户模式改成异步绑定 CAS，会让后一个图表更新
在绑定失败之前生效。补入该组件和对应测试的最小接线：等待绑定成功
才更新图表模式，等待时禁用切换，失败保持原模式并显示中文原因。
此修订实现 A32 的既定语义，不扩大交易能力。

### I02：旧管理器单元测试绕过应用启动生命周期

旧自动交易测试直接调用管理器，没有运行 FastAPI lifespan。新增启动恢复
屏障后，这些测试在验证原有业务行为之前即被 `TRADING_NOT_READY` 拦截。
单跑旧跨车道并发测试已复现：原本应一成功一失败，实际两边均被恢复门禁
拒绝。补入 `tests/conftest.py` 的测试生命周期与单例隔离，使旧业务单元
测试声明其既定前提“启动已完成”，并避免准入状态跨用例泄漏。新交易及
启动关闭测试继续显式验证未就绪拒绝、恢复后放行和关闭后拒绝，不改变
生产默认关闭的恢复屏障，也不 mock 掉数据库事务。
另将旧暂停契约测试的状态消费者白名单补入 `agent_trading/admission.py`，
仍逐行要求使用 `is not None` 判断，防止把暂停占位误判成空闲。
恢复旧暂停测试真实流程后，原 FakeSession 缺少现有 scheduler 所需的
`experience.rule` 字段；仅在该测试桩补入未装配状态，避免后台未检索异常。

### I03：全库网络超时测试包含调用数量快照

前端全量测试中 3144 项通过，一项数量快照仍要求原有 24 处 fetch／22 个
文件；新增 `agentTradingApi.ts` 合法增加一处调用及一个文件。更新该数量
快照为 25／23，保留对每一处调用经过 `withTimeout` 的原有检查。这是新
客户端的既有质量契约接线，不放宽超时策略。

### I04：内部守卫字段与旧订单序列化契约冲突

原文要求直接向 `OrderInput` 加字段，但旧自动交易测试对 dataclass 的
序列化内容作逐字段兼容校验，即使新增值为 null 也会改变契约。修订为
聊天专用的内部 `IntentOrderInput` 子类承载守卫，旧类保持原字段。
此外，未知提交需要作废聊天持仓缓存；该处理归属聊天执行器，不进入旧
交易服务的异常分支，从而保留旧路径“仅写成功才作废”的既有行为。
三个旧兼容契约及新发送/恢复/守卫组合 35 项复验通过。

### 实施验证记录（2026-09-28）

79 个计划文件均已落盘并有变更；无新增计划外路径，未提交 git。最终
后端定向 237 项、前端全量 3145 项、生产构建、Ruff 与 diff 检查通过。
浏览器隔离冒烟覆盖三种宽度、明暗主题、单次确认及未知态核对。
完整后端初跑失败经修复复验后，仍保留 10 项本轮未修改区域的既有失败
（因子扩展 7 项、日志层数 2 项、自愈接线数量 1 项）；不宣称全库绿色。
详细证据及原生测试退出异常说明见
`.agentmesh/agent-trading-validation.md`。执行开关保持默认关闭，未真实下单；
全部 A01–A44 发布边界组合仍须由后续评审核实，不以本次测试数量代替。

以上“仅修改设计”描述适用于设计节点；实施及最终评审结果分别见下文和验收记录。

### 最终代码评审修订（2026-09-28）

逐项核对第七节 79 个文件以及读工具、意图、提交、恢复、UI、旧接口接线；
修复下列八项问题，均先复现再验证修复。所有改动仍在原计划文件范围内。

| 编号 | 问题及修复机制 | 回归位置 |
|---|---|---|
| F01 | 净仓发送省略 posSide 时，核对仍必须匹配 net，禁止错误侧响应推进终态 | test_agent_trading_recovery.py |
| F02 | 完整逐单业务拒绝回执建立内部证据，明确拒单不再永久占用 unknown | 同上，开/平/撤参数化回执 |
| F03 | 不重试不等于确定拒绝；51149、50004、重复客户 ID、网络故障及空回执保留 unknown | 同上 |
| F04 | 返回旧会话时按当前全局模式 CAS 同步绑定；提交资格及点击瞬间都检查 live | TradingAccountBar / TradeIntentCard 测试 |
| F05 | 提交途中重连后清理 busy，但仍忽略旧轮次响应，恢复只读查询 | TradeIntentCard 测试 |
| F06 | 平仓卡明示仓位方向和委托方向，可区分买入平空及卖出平多 | TradeIntentCard 测试 |
| F07 | 混合账户按字段携带数量、价格、保证金及费用币种，现货市价委托考虑 tgtCcy；未知单位不补 USDT | test_agent_trading_reads.py / AccountSnapshotCard 测试 |
| F08 | 委托分页仅使用分页器的统一期限，超时保留已完成页并标记 partial，避免外层计时先取消整份结果 | test_agent_trading_reads.py |

单位及不确定回执语义依据 [OKX 官方接口文档](https://www.okx.com/docs-v5/en/)。
字段单位增大载荷时仍服从 24 KiB 上限，可减少 returnedCount，保持完整 JSON。

提交在 HEAD 加本功能的独立快照中验证，排除 journal/evidence 的其他改动。
因此 I03 的混合工作树计数 25/23 在独立提交中为 23/22；逐请求超时契约保持。
暂存区既有的 56 个其他功能文件保留其暂存选择，构建及运行时资料不进入提交。
详细终审命令、结果、限制与提交边界记录在 `.agentmesh/agent-trading-final-review.md`。
执行默认关闭；本次不进行真实下单，不将隔离测试等同于完整发布验收。
