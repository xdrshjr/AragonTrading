# 权重模型工作区 — 正确性收尾与体验精修方案 v2（Refinement）

> **版本**：v2（经 Subtask #1 设计评审，2026-06-30；新增并修复 1 项 P1 / 澄清 8 项 P2，详见下「评审记录」与文末「评审结论」）。

> Feature: `weight-model-workspace-refinement`
> 所属层：因子层（Factor Layer）· 因子实验室（Factor Lab）· 权重模型工作区（Weight Model Workspace）
> 迭代：Loop Iteration 3/3 ｜ 全局目标：**确保所有功能正确可用、稳健可靠**
> 上游：迭代 1 `docs/plans/weight-model-workspace/spec.md`（已实现并提交 `9552ac9`）、迭代 2
> `docs/plans/weight-model-workspace-hardening/spec.md`（已实现并提交 `961a9a3`）。本文档在此二者之上做
> **正确性收尾 + 一致性补齐 + 可观测性 + 体验精修 + 测试护栏**，**不重做已成功并已加固的功能**。
> 约定（沿用上游全部铁律）：字段 camelCase、时间秒级 UTC、错误体统一 `{error:{code,message}}`、单一归一化契约、
> 方向冻结 long/short、**绝不假数据 / 绝不静默降级**；**纯加法、零破坏**：不动 `f=1.0` 末步与 `POST /factor-sets/train`
> 的字节级等价、不动 `factor_sets` 表与 factor_set ⇄ factor_score 策略 1:1 同步契约、**不新增任何表 / 列**（零迁移）。

---

## 评审记录（Review Notes · v2 — Subtask #1 设计评审）

> **评审方法**：基准同上游——对照**已提交实现**（`961a9a3`）逐节读码核验。本轮以 2 路并行审计（后端 `delete`/`cancel`/`reconcile`、
> `_run` except 阶梯、`_validate_train_config`/`_config_num`、`config.py`、`build_train_result`/`_normalize_weights`；前端
> `WeightTrainLive`/`WeightModelDetail`/`WeightModelList`/`FactorSetBuilder`、`weightModel*.ts`）+ 评审者直读关键路径
> （`_TrainJob.run_id`/`is_terminal`、`setWeightTrainResult` 全仓写入点）交叉核验。维度：可行性 / 完整性 / 一致性 / 适度性。
> 严重度：P0 数据损坏 / 悬挂 / 崩溃 / 安全；P1 在特定操作序列下结果与界面不符 / 破坏既有不变量 / **修复方案本身留洞**；
> P2 一致性 / 健壮性 / 可观测性 / 体验。
>
> **核验结论**：本文档定位的 12 项 G-B、11 项 G-F、11 项 G-T 缺口对照真实代码**全部属实**，`文件:行` 锚点**全部准确**
> （含 G-B2 的 `:154-160` 与 G-B5 的 `:145-146` 各自正确、G-F1 的 `:177-182`/`:242-250`/`:243-261/251/257` 精确）；
> 「纯加法 / 零 schema / 不触 `f=1.0` 等价装配」纪律守住。**未发现 P0。** 评审**新增并就地修复 1 项 P1**（R-01：G-B1
> 修复方案对孤儿 running run 留洞），另记 7 项 P2（2 项就地加固、5 项澄清 / 确认合理）。**升版 v2，无遗留 P0 / P1。**

| # | 维度 | 严重度 | 评审发现 | 处置 |
|---|---|---|---|---|
| R-01 | 完整性 / 正确性 | **P1** | **G-B1 的「409 守门」对孤儿 running run 留洞。** `cancel` 以 `job_id` 寻 `_jobs`（`weight_train_manager.py:304-308`），对「DB=running 但无在跑 job」的孤儿 run 返 `"unknown"`→404，**无任何 API 能把孤儿 run 转终态**；唯一清算者是启动对账 `reconcile_dangling_weight_runs`（`main.py:47`，仅进程启动一次）。孤儿态**单进程内可达**（`weight_train_manager.py:235-240` persist 二次失败显式留 run=running，即 E4 动机）。故朴素「任一 run==running 即 409」会令**仅余孤儿 run 的模型永久不可删、且不可取消**（直到重启）——**defeats G-B1 自身「删除一致」目标**，且无应用内恢复路径。 | **已就地修复**：§3.1 重写为守门**区分真·在跑 vs 孤儿**——仅对**有在跑 job** 的 running 返 409；孤儿 running 在**删除路径上就地对账为 `failed`+`repoint` 再删**。新增 `WeightTrainManager.active_run_ids()`（纯加法）+ service 判别 + repo scoped 对账。该子项**升入必交付**；补「孤儿可删 / 真在跑 409」双向测试；详情页孤儿 run 取消入口由「可选」升「应交付」配套。 |
| R-02 | 完整性 | P2 | **G-B4 指针清空用序列化串比对**（`_dump(patch.members)!=row.members_json`），而 `_dump=json.dumps(…, ensure_ascii=False)`（`repositories.py:92`）**无 `sort_keys`**。若客户端送的成员 / config dict 键序与落库时不同，**no-op PUT 会被误判 `spec_changed` → 误清当前权重指针**。 | **已就地加固**：§3.3 补「比对前规范化（排序键 / 比对解析后结构）」，仅改判定不改清空逻辑；测试加「仅改 name/enabled 及键序不同的等价 config 不清指针」。 |
| R-03 | 一致性 | P2 | **G-B4 对 `refresh_weight_model_spec`（builder 重训复用路径，`:147-153`）的「陈旧窗口仅同事务内、对外不可见（自愈）」论证不准确**：该函数**自身 commit**（:151），随后训练异步进行，「新成员 + 旧权重指针」在**整个训练时长内已提交且对外可见**。 | **已就地修正**：§3.3 修正论证并给出取舍——重训路径同样在 spec 变更时清指针（与 G-B4 同构），或明确「重训期详情以 running run 态呈现、不渲染旧权重卡」为可接受取舍并落测试。 |
| R-04 | 完整性 | P2（确认合理 · 非洞） | **G-F1 修复完备性已核验**：`setWeightTrainResult` 全仓**唯一写入点**为 `WeightTrainLive.tsx:180`（`applyToBuilder` 内），无 `finishStream` / success-effect 旁路自动回流。故「按钮 + `applyToBuilder` 双重 `origin==="builder"` 守门」即彻底封死跨上下文劫持。 | 维持 §4.1；§9.2 补断言「全仓唯一 setter 经 origin 守门、无旁路」。 |
| R-05 | 一致性 / 适度性 | P2（确认合理） | **G-B7 §3.9 实现复杂度澄清**：`_normalize_weights` 的 `total==0` 回落（`training.py:252-256`）与 `note` 装配（`build_train_result:299-310`，两路经 `train_weights` 共享）**分处两函数**——若于共享装配加 note，需把「退化」信号从 `_normalize_weights` 上传，复杂度略增且触碰等价装配。 | 维持 **G-B7 可选**；评审**采纳 spec 提供的降级口径**：优先「仅前端 / WS 侧展示 note、零改装配」，规避等价性风险；如确需改装配须守 §3.9 强约束（两路同源仍字节级一致 + 等价测试先绿）。 |
| R-06 | 适度性 | P2（确认合理） | **右尺寸核验**：发现量（12+11+11）较大，但 §9.4 已分「必交付 / 应交付 / 可选」三批 + 「前批未全绿不进下批」闸；必交付集右尺寸适当，应 / 可选批为体验精修，gated 后不拖累正确性。 | 维持分批；R-01 后必交付集 = 「G-F1 + G-B1（含孤儿处置）+ G-B2/G-B4/G-B5/G-F2/G-F4/G-F6 + G-T1/G-T2」。 |
| R-07 | 健壮性 / 措辞 | P2（确认合理） | **G-B9 `le` 上限**收紧可能使既有部署的超界覆写在启动校验失败（§10 已载，宽松上限 16/20/500 远高于合理用值，可接受）。另：§4.3 注释「`deleteRun` onSuccess」措辞微瑕——`deleteRun` 实为普通 `async` 函数（`WeightModelDetail.tsx:35-40`），失效行为一致，仅措辞。 | 维持 G-B9 上限；§4.3 措辞已校正为「`deleteRun` 完成后失效」。 |

---

## 〇、本轮定位（Why this iteration）

迭代 1 把「权重模型工作区」从设计落到实现：左导航「权重模型」类别、模型 CRUD、训练历史回放（自绘 loss/IC 曲线）、
「训练权重」跳转实时流式训练、产物一键回流组合。迭代 2 做了系统性可靠性加固：消除「落库失败致 WS 永久悬挂」P0、
并发硬上限、取消 / 关停及时、边界 400 校验、前端三态 / 可访问性 / 曲线可读性。两轮后端 `pytest 688 passed`、
前端 `vitest 723 passed`、`tsc --noEmit` 全绿，功能骨架健全且异常路径稳健。

本轮以**三路并行审计对照真实落地代码**（后端 `weight_train_manager.py` / `weight_model_service.py` /
`api/weight_models.py` / `db/weight_model_repositories.py` / `engine/factors/training.py`；前端
`WeightTrainLive.tsx` / `WeightModelDetail.tsx` / `WeightModelList.tsx` / `FactorSetBuilder.tsx` /
`lib/weightModel*.ts`；以及 13 个测试套件）做了一次体检，结论是：**两轮加固守住了——无新增 P0、无数据损坏 / 悬挂 /
崩溃；等价路径（`f=1.0` ↔ 同步 train）经核验字节级正确，重复成员安全、`startTs≥endTs` 已被 `_FactorRange`
校验覆盖、迭代 2 标注「可选」的 7 项 P2（H-B6/B7/B8、H-F7…F10）经读码核验全部已真实落地**。但体检定位出
**2 个 P1 正确性缺陷**（model-origin 训练误覆盖组合搭建草稿并劫持进编辑无关组合；删除带「训练中」run 的模型无 409
守门且不取消在跑作业，破坏既有不变量）、**一批 P2 一致性 / 健壮性 / 可观测性缺口**（吞错致脏错误码外泄、改 spec
后指针陈旧、`normWindow`/`clip` 非数值漏校验、详情页 running 不刷新、删 run 不失效列表缓存、错误码生映射缺失、
无 aria-live 等）与 **测试护栏缺口**（`WeightModelList` 零测试、缺端到端「搭建→训练→回流→保存组合→伴生策略同步」链路测试）。

本方案把上述发现整理为带 `G-` 编号、可逐行实现的修复项，并按「**必交付 / 应交付 / 可选**」分批（§9.4 优先级闸），
保证单轮可交付且不让体验精修拖累正确性。所有改动都是**外科手术式、纯加法**：要么是 service / 组件内部的局部改写，
要么新增一处边界校验 / 一个错误码映射 / 一段日志，**没有任何改动会触碰已通过的等价性回归、factor_set 同步事务或数据库
schema**——这保证「修精修」本身绝不引入新的回归。

---

## 一、概述（Overview）

权重模型工作区的价值在于把「一次性同步训练」升级为「有专属面板、可管理、可回溯、可实时观测」的一等功能。经过两轮
建设与加固，正常路径与大多数异常路径都已可靠。本轮要解决的是**两类仍然存在的真实风险**：其一是少数**正确性 / 一致性
缺陷**——它们不在「服务悬挂 / 崩溃」这类显性故障里，而藏在「跨上下文操作」「删除 / 编辑后的派生状态」「错误分类」这些
派生路径上，会让用户在特定操作序列下得到**与界面所示不符的结果**（例如点「应用到组合」却把当前草稿换成了另一个组合、
删模型时悄悄丢弃在跑训练、改完模型成员后详情仍展示旧成员的权重）；其二是一批**体验与可观测性**缺口——它们不影响
正确性，但关系到用户的「美观优雅、符合人机交互最佳实践」体感与运维者的排障能力（详情页训练态不刷新、列表卡片删 run
后陈旧、错误码裸串外泄、屏幕阅读器无播报、成功训练无日志）。

本方案分三条主线推进。**主线 A（后端正确性与一致性）**：给「删除带在跑 run 的模型」补 409 守门（或先取消作业）以恢复
既有不变量；把数据加载阶段抛出的 `HTTPException`（如 `EMPTY_RANGE`）正确分类、不再降级成 `WEIGHT_TRAIN_FAILED`
并外泄 Python dict repr；编辑模型 spec（成员 / 方法 / 配置）时清空指向旧 spec 的 `latest_run_id` / `best_valid_ic`
指针，杜绝「新成员配旧权重」的陈旧展示；把 `normWindow` / `clip` 这两个被引擎消费却漏校验的 config 键纳入边界 400
校验；补齐启动 / 完成日志与设置上限护栏。**主线 B（前端正确性与体验）**：把「应用到组合并返回」严格限定在组合搭建发起
的训练上（杜绝跨上下文覆盖草稿这一 P1）；详情页训练态轮询刷新与删 run 后失效列表缓存（消除陈旧）；落地迭代 2 标称却
未实装的错误码友好映射；补 aria-live 播报、训练时间窗呈现、文案本地化对齐、骨架屏与减少动效等精修。**主线 C（测试护栏）**：
把本轮所有修复以及两轮遗留的高价值未覆盖路径（`WeightModelList` 组件、端到端链路、并发 finalize、WS 中途重连回放、
曲线组件渲染）补成稳定可复现的回归测试，把「过去与潜在的坑」变成「未来的护栏」。

设计原则与上游完全一致——**纯加法、零破坏、绝不假数据 / 绝不静默降级**。每条 `G-` 项都标注精确 `文件:行` 锚点与最小
改写范围，下游可据此逐行落地，无需再做架构决策；bug 类项一律遵循「先写复现测试（红）→ 再改业务（绿）」。

---

## 二、问题清单与定位（Findings · 对照真实代码）

> 体检基准为**已提交实现**（`961a9a3`）。严重度：P1 在特定操作序列下结果与界面不符 / 破坏既有不变量；
> P2 一致性 / 健壮性 / 可观测性 / 体验缺口。锚点均为本轮直接读码核验的 `文件:行`。

### 2.1 后端正确性与一致性（主线 A）

| # | 严重度 | 问题 | 锚点 | 处置 |
|---|---|---|---|---|
| G-B1 | **P1** | `delete_weight_model` 删模型时连带删其**全部** run（含 `running`），且**从不取消在跑作业**、**无 409 守门**——与「`delete_weight_run` 对 running 拒删 409」的既有不变量直接相悖。在跑作业续训一个已被删的 run（`finalize` 时 `get(run_id)→None` 静默 no-op），并发名额被占到自然结束、WS 仍推一个「幽灵 run」。 | `db/weight_model_repositories.py:193-207`（无 running 守门，删全部 run）对比 `:315-324`（run 删的 409 范式）；`api/weight_models.py:76-81`（无 409 路径）；`weight_model_service.py:70-71`（不触 manager）；孤儿不可达取消 `weight_train_manager.py:304-308` | §3.1（含 R-01 孤儿 run 处置） |
| G-B2 | P2 | 数据加载抛的 `HTTPException`（`EMPTY_RANGE` 等）被吞进 `WEIGHT_TRAIN_FAILED`：`_run` 仅特判 `OKXError`，`HTTPException` 落入泛 `except Exception`，run 落库 `error_code="WEIGHT_TRAIN_FAILED"`、`error_message="400: {'code': 'EMPTY_RANGE', ...}"`——真实码丢失、**Python dict repr 外泄到 UI**（同步 `POST /factor-sets/train` 则返回干净 400）。 | `weight_train_manager.py:154-160`（except 阶梯仅 OKXError）；`agent/services.py:107-109`（`EMPTY_RANGE` HTTPException 源） | §3.2 |
| G-B4 | P2 | 编辑模型 spec 后指针陈旧：`update_weight_model` 改 `members_json`/`method`/`config_json` 却**不重置** `latest_run_id`/`best_valid_ic`，模型遂以「**旧成员名的权重**」对「新成员」展示、`bestValidIc` 仍按旧 spec 计算（`weight_train_run_out` 摘要也含 `weights`，陈旧权重直接外露）。 | `db/weight_model_repositories.py:163-190`（改 spec 不动指针）；`schemas.py`（`weight_train_run_out` 摘要含 `weights` + `latestRun`） | §3.3 |
| G-B5 | P2 | `normWindow`/`clip` 非数值漏校验：`_validate_train_config` 仅校验 `split`/`l2`/`nEstimators`/`maxDepth`，但 `_run` 对 `normWindow` 做 `int(...)`、对 `clip` 做 `float(...)`。`normWindow:"x"` / `clip:"x"` 经 pydantic（`dict[str,Any]`）后在 `_run` 抛 `ValueError` → 泛 except → 落 `WEIGHT_TRAIN_FAILED` run，而非启动即干净 400。 | `weight_model_service.py:117-134`（校验缺这两键）；`weight_train_manager.py:145-146`（`int()/float()` 抛点） | §3.4 |
| G-B3 | P2（潜伏） | 慢订阅者可丢 `result`（权重载荷）且永不重投：`_push` 满队列 `except QueueFull: pass`；H-B7 心跳兜底**仅重投终态 `buf[-1]`**（`done`），非终态的 `result` 一旦被丢则该连接永远拿不到最终权重（需用户改走 REST 重取，界面不显然）。默认 `curveSteps=6`（≈13 事件≪256）几乎不触发，但 `weight_train_curve_steps` 调高即可达（联动 G-B9）。 | `weight_train_manager.py:103-109`（丢弃）；`api/weight_models.py:141-146`（兜底仅 `buf[-1]`） | §3.5 |
| G-B6 | P2（极罕见潜伏） | `start_job` 在 `add_weight_run` 提交后若抛（仅 `create_task` 在无事件循环时）：job 已入 `_jobs`（`status="running"`、`_task=None`），被 `active_count()` 永久计入（永不终态 → 永不剪枝），committed run 成孤儿待下次启动对账；外层二次 `release` 与已转计数叠加。 | `weight_train_manager.py:292-299`（`release()` 先于 `job.start()`，抛点半注册）；`weight_model_service.py:213-215`（二次 release） | §3.6 |
| G-B8 | P2（可观测性） | 成功 / 启动训练无日志：仅失败重试耗尽、剪枝、对账有日志；`_finalize_success` 不发 `logger.info`（无 run_id / 方法 / 生效后端 / 样本数 / finishedTs）。长生命周期流式特性的真实排障盲点。 | `weight_train_manager.py:194-207`（无 log）对比 `db/weight_model_repositories.py:279-281`（剪枝有 log） | §3.7 |
| G-B9 | P2（minor） | 设置无上限：`weight_train_curve_steps`（`ge=2`）、`max_weight_train_jobs`（`ge=1`）、`weight_model_run_cap`（`ge=1`）无 `le`。过高 `curve_steps` 直接膨胀缓冲 + WS 事件数（并使 G-B3 可达）；过高 `max_weight_train_jobs` 抹掉保护性上限。 | `config.py:126-128` | §3.8 |
| G-B10 | P2（minor / 一致性） | `create_model`/`update_model` 校验 `bar` 却不校验 `config`：手动建 / 改模型可落入非法 `config`（如 `split:5`），虽下次 builder 训练会覆写，但不一致且可被展示。 | `weight_model_service.py:47-56`（create）、`:59-67`（update）未调 `_validate_train_config` | §3.8 |
| G-B7 | P2（可选 · 等价敏感） | 全零原始权重静默回落等权：`_normalize_weights` 在 `total==0`（如树无分裂 → 全零重要度）静默返回等权，结果报 `method="random_forest"` 配等权且无 `note` 说明，近「静默降级」。 | `engine/factors/training.py:252-256` | §3.9 |

> **后端增强（非 bug，标注为增强）**：**E1** `instId` 大小写未规范（`"btc-usdt"` 与 `"BTC-USDT"` 建出两个模型、且 OKX 拒小写→502）→ schema 层 upper-case；**E2** `GET /runs?status=foo` 自由串静默返回 `[]` → 白名单校验 422；**E3** `_dump`/`json.dumps` 默认 `allow_nan=True`，权重若退化 NaN/inf 会以非法 JSON token 外发 → `allow_nan=False` 诚实报错；**E4** persist 二次失败的 run 留 `running` 至下次重启对账（H-B1 既定取舍，非本轮新增）→ 周期性**仅对不在 `_jobs` 的 running 行**做进程内对账，闭合而不碰在跑 run。

### 2.2 前端正确性与体验（主线 B）

| # | 严重度 | 问题 | 锚点 | 处置 |
|---|---|---|---|---|
| G-F1 | **P1** | 「应用到组合并返回」对 **model-origin** 训练也展示并执行，**静默覆盖组合搭建草稿并劫持进编辑无关组合**：按钮仅以 `status==="succeeded"` 解禁，`applyToBuilder` 无视 `target.origin` 一律 `setWeightTrainResult`+`setSection("builder")`；从列表 / 详情发起（`origin:"model"`）时 `buildTrainResult` 带模型的 `factorSetId`，builder 消费 effect 遂 `setMembers/Method/Thresholds/Trained` 覆盖用户当前草稿、并 `setEditingSetId(result.factorSetId)` 把 builder 切进「更新组合 #N」——用户从未打开过该组合。H-F2 只治 `boundModelId` 记忆，不治此覆盖。 | `WeightTrainLive.tsx:177-182`（applyToBuilder 无视 origin）、`:242-250`（按钮仅看 succeeded）；`lib/weightModel.ts:270`（result 带 factorSetId）；`FactorSetBuilder.tsx:243-261`（消费 effect 覆盖草稿 + `setEditingSetId`，:251/:257） | §4.1 |
| G-F2 | P2 | 详情页训练态不刷新：`WeightModelDetail` 的 `runs` 查询无 `refetchInterval`（H-F10 的 running 轮询只加在**列表**），打开一个在别处启动训练的模型，状态永久停在「训练中」、曲线静止。 | `WeightModelDetail.tsx:31`（无 refetchInterval）对比 `WeightModelList.tsx:38-41` | §4.2 |
| G-F4 | P2 | 删 run 不失效列表缓存：`deleteRun` 仅失效 `["weight-models", modelId]` 与 `[..., "runs"]`，列表键 `["weight-models", {enabled…}]` 前缀不匹配，删掉最佳 / 最近 run 后**列表卡片 IC / 最近训练时间陈旧**。（直播路径无此问题——`finishStream` 失效裸 `["weight-models"]`。） | `WeightModelDetail.tsx:35-40`；列表键 `WeightModelList.tsx:35`；直播 `WeightTrainLive.tsx:144` | §4.3 |
| G-F6 | P2 | 错误码裸串外泄、迭代 2 标称的 H-F9「errorCode 友好映射」**实为未落地**：`WeightTrainLive.tsx:219` 渲染 `（{error.code}）`（`CANCEL_TIMEOUT`/`CANCEL_FAILED`/裸后端码）、`WeightModelDetail.tsx:215` 渲染 `（{data.errorCode ?? "ERROR"}）`；全仓 grep 无任何 code→label 映射。 | `WeightTrainLive.tsx:219`；`WeightModelDetail.tsx:215`；`lib/weightModel.ts`（无 codeLabel，应与 methodLabel/phaseLabel 并列） | §4.4 |
| G-F5 | P2 | 无 aria-live 播报：ProgressBar / 结果 / 错误条 / 连接中断 / 等待数据 / builder · editor 的 `notice` 均无 `aria-live`/`role="alert"`，屏幕阅读器在「训练完成 / 失败 / 断连」时**毫无播报**（与 `FactorCatalog.tsx:41` 已有的 `role="status" aria-live="polite"` 不一致）。H-F8 治表格键盘 / 焦点 / ConfirmDialog，非 live region。 | `WeightTrainLive.tsx:204/206-215/217-221/261-279`；`FactorSetBuilder.tsx:470`；`WeightModelEditor.tsx:154` | §4.5 |
| G-F8 | P2 | 回流结果区 i18n 不一致：`FactorSetBuilder.tsx:426` 直显原始方法 key `<Badge>{method}</Badge>`（"linear"/"ic_weight"），`:431` 用 `n_train`/`n_valid`，而直播 `ResultSummary` 用本地化「训练样本 / 验证样本」。同一数据两套术语。 | `FactorSetBuilder.tsx:426/431` 对比 `WeightTrainLive.tsx:288` | §4.6 |
| G-F3 | P2 | 训练历史无界渲染：`WeightModelDetail.tsx:31` 调 `weightModelApi.runs(modelId)` 不传 `limit`，`RunHistoryTable` 全量 map；API 已支持 `limit`/`status`（`weightModelApi.ts:88-94`）却未用，重度训练模型渲染数百行。 | `WeightModelDetail.tsx:31`、`:163-195`；`weightModelApi.ts:88-94` | §4.7 |
| G-F7 | P2 | 训练时间窗从不呈现：直播头部 `WeightTrainLive.tsx:195` 只显 `instId · bar · N 成员`，不显 `startTs/endTs`（窗口静默取自 FactorLab 顶栏）；详情「训练新版本」亦无窗口提示。用户无法核对训练用的日期段。 | `WeightTrainLive.tsx:195`；`WeightModelDetail.tsx:61` | §4.7 |
| G-F9 | P2（低） | 取消超时守卫闭包陈旧 + 失败后权重标签误导：`WeightTrainLive.tsx:162-167` 读 mutate 时捕获的 `status`（渲染闭包），现仅靠每条终态 `clearTimers()` 才正确（脆弱）；`status==="failed"` 后权重卡仍标「收敛中的权重」（`:228-236` 标签 `:231`）。 | `WeightTrainLive.tsx:162-167`、`:228-236` | §4.8 |
| G-F10 | P2（低） | 直播视图未按 target keyed + 不防重复训练在跑模型：实例跨 `view.kind==="live"` 复用无 `key`，`startedRef` 每挂载只训一次（live→live 切 target 会复用旧 run，当前不可达但是 footgun）；`WeightModelList.tsx:160`「训练」在 `latestRun?.status==="running"` 时仍触发，允许重叠 run。 | `WeightTrainLive.tsx:70/107-109`；`WeightModelList.tsx:160` | §4.8 |
| G-F11 | P2（低） | 开关标签重复 / 关联错误：`WeightModelList.tsx:69-72` 把可见文「显示已停用」放进包裹 `role="switch"` 按钮的 `<label>`，而按钮已有 `aria-label="显示已停用"` → 名称被念两遍，且包裹 `<label>` 不可靠触发按钮。 | `WeightModelList.tsx:69-72` | §4.8 |

> **前端设计增强（非 bug，为「美观优雅、顶级设计」，标注为增强）**：骨架屏（`ui/index.tsx:146` 已有 `Skeleton` 原语，列表 / 记录 / 曲线加载态仍为纯文本）、`WeightModelDetail` 概要 `model.isPending` 骨架（消除弹入跳变，`:49-62` 只处理 isError）、`prefers-reduced-motion` 门（全仓零处理）、曲线 tooltip 键盘 / 触屏可达 + 数据表回退（`TrainingCurveChart.tsx:85-96` 仅鼠标）、RankIC 面板零基线参考线（`:28`）、详情页对「孤儿 running run」给取消入口（当前仅直播可取消，`:184` 删禁用）、富化空状态（`WeightModelList.tsx:77-84`、`WeightModelDetail.tsx:142-148` 纯文本）。

### 2.3 测试护栏缺口（主线 C）

| # | 缺口 | 锚点 |
|---|---|---|
| G-T1 | **`WeightModelList.tsx` 零测试**：H-F10 全部行为（显示已停用切换改 queryKey、4s running 轮询、删除 ConfirmDialog 流、删除中禁用、`remove` 后 `invalidateQueries(["weight-models"])` 缓存失效）无组件级守护。 | `components/Factor/WeightModelList.tsx`（无 `.test.tsx`） |
| G-T2 | **无跨特性端到端链路**：搭建→训练→回流→保存组合→伴生 `factor_score` 策略同步，各环节孤立有测但从未串联。 | `weight_model_service` ↔ `factors/service.backfill_companions` 边界 |
| G-T3 | H-B7 满队列终态兜底分支（`weight_models.py:141-146`，含 G-B3 `result` 重投）未测。 | `api/weight_models.py:141-146` |
| G-T4 | 中途 WS 重连回放未测（现仅终态后重连）：重连到**仍在跑**作业，断言快照前缀 + 续增量→终态无重复。 | `test_weight_train_ws.py`；`weight_models.py:125-135` |
| G-T5 | 并发 finalize / 指针重算竞态未测（现仅顺序剪枝）。 | `weight_model_repositories.py:284-312` |
| G-T6 | `TrainingCurveChart` SVG 组件未测（仅测几何 `metricGeometry`）。 | `TrainingCurveChart.tsx:17-122`（无 `.test.tsx`） |
| G-T7 | `WeightModelDetail` 删 run 流 + CRUD 缓存失效未测（现仅 error/cancelled/键盘）。 | `WeightModelDetail.tsx:35-40,98-108` |
| G-T8 | `WeightModelEditor` 新建路径未测（现仅编辑路径 H-F6 名校验）。 | `WeightModelEditor.test.tsx`（单测） |
| G-T9 | horizon 边界 / 12 成员 / 非 1H 周期端到端未测。 | 各 `test_weight_*` fixtures |
| G-T10 | 曲线数学 NaN/Infinity 边界未测（现覆盖 null/单点/空）。 | `weightModel.test.ts`；`weightModel.ts:34-75` |
| G-T11 | 前端 WS 客户端 `weightTrainWs.ts` 无直测（组件里全 `vi.mock`）。 | `apps/web/lib/weightTrainWs.ts` |

---

## 三、技术方案 · 主线 A（后端正确性与一致性）

> 总原则：与 §1 不变量对齐——删除 / 编辑后的派生状态必须自洽；错误必须被正确分类、绝不外泄实现细节；漏校验的输入在
> 请求边界判错。所有改动在 `weight_model_service.py` / `db/weight_model_repositories.py` / `weight_train_manager.py` /
> `api/weight_models.py` / `config.py` 内，**不触碰** `engine/factors/training.py` 等价路径（G-B7 例外见 §3.9 强约束）与 `db/models.py` schema。

### 3.1 G-B1（P1）删模型对在跑 run 守门——恢复「训练中不可删」不变量（含孤儿 run 处置，R-01）

**根因**：`delete_weight_model`（`db/weight_model_repositories.py:193-207`）无视 run 状态删全部，路由（`api/weight_models.py:76-81`）无 409 路径，service（`weight_model_service.py:70-71`）不触 manager。

**⚠ R-01 修正（关键，避免修复留洞）**：朴素「任一 run==running 即 409」会**引入新洞**——`cancel` 以 `job_id` 寻 `_jobs`
（`weight_train_manager.py:304-308`），对「DB=running 但 manager 无在跑 job」的**孤儿 run** 返 404 而**不置终态**；孤儿态
单进程内即可达成（persist 二次失败显式留 run=running，`:235-240`，即 E4 动机），唯一清算者是**启动对账**（仅重启触发）。
若守门不区分，**仅余孤儿 run 的模型将永久不可删、且不可取消**，反而破坏「用户可删 / 可恢复」不变量。故守门必须
**区分真·在跑 vs 孤儿**：

- **真·在跑**（该 model 的 running run 在 manager 有对应**非终态** job）→ 409 `WEIGHT_RUN_RUNNING`，提示「请先取消训练」。
- **孤儿 running**（DB=running 但 manager 无在跑 job）→ **删除路径上就地对账为 `failed`（+ `repoint_weight_model`）再删**，
  即时恢复可删性，无需等下次重启。

**改写（manager 暴露在跑 run 集 · service 判别 · repo scoped 对账 + 既有级联删）**：

```python
# weight_train_manager.py:WeightTrainManager —— 纯加法访问器：当前非终态作业的 run_id 集（区分真·在跑 / 孤儿，R-01）
def active_run_ids(self) -> set[int]:
    return {job.run_id for job in self._jobs.values() if not job.is_terminal}
```

```python
# db/weight_model_repositories.py —— scoped 对账：把某 model 的孤儿 running 行置 failed + repoint（仿 reconcile_dangling，单事务内 flush）
async def fail_orphan_running_runs(session: AsyncSession, model_id: int, *, alive: set[int]) -> int:
    rows = (await session.execute(select(WeightTrainRun).where(
        WeightTrainRun.model_id == model_id, WeightTrainRun.status == "running"))).scalars().all()
    orphans = [r for r in rows if r.id not in alive]   # 无在跑 job 的 running = 孤儿
    for run in orphans:
        run.status = "failed"
        run.error_code = "WEIGHT_TRAIN_INTERRUPTED"
        run.error_message = "训练作业已不在运行（删除前进程内对账改判）。"
    if orphans:
        model = await session.get(WeightModel, model_id)
        if model is not None:
            await repoint_weight_model(session, model)   # R-05：指针随终态化重算
    return len(orphans)
```

```python
# weight_model_service.py:delete_model —— 真·在跑 409；孤儿先对账后删（透传 'not_found'/'running'/'ok'）
async def delete_model(session, model_id) -> str:
    model = await repo.get_weight_model(session, model_id)
    if model is None:
        return "not_found"
    running = await repo.list_weight_runs(session, model_id, status="running")
    if running:
        alive = get_weight_train_manager().active_run_ids()
        if any(run.id in alive for run in running):
            return "running"                                            # 真·在跑 → 409
        await repo.fail_orphan_running_runs(session, model_id, alive=alive)  # 孤儿 → 对账失败
        await session.commit()
    return await repo.delete_weight_model(session, model_id)            # 既有级联删（此刻已无 running）
```

```python
# api/weight_models.py:delete_weight_model —— 增 409 分支（镜像 delete_weight_run）
result = await service.delete_model(session, model_id)
if result == "not_found":
    raise HTTPException(404, detail=_NOT_FOUND)
if result == "running":
    raise HTTPException(409, detail={"code": "WEIGHT_RUN_RUNNING", "message": "训练进行中，请先取消训练再删除模型"})
return Response(status_code=204)
```

> **防御纵深**：`delete_weight_model`（repo）保留一道轻量 running 守门（仍有 running 即返 `"running"`，镜像 `delete_weight_run:323-324`），
> 使**直接 repo 调用**亦安全；service 已先对账孤儿，故正常路径该兜底只对**真·在跑**触发。
> **不变量恢复且不留洞**：真·在跑 → 409（与 run-删一致，用户取消后即可删）；孤儿 → 进程内对账自愈即删。**E4 的周期对账由此降为
> 纯纵深防御**（删除路径已即时闭合孤儿），仍可选。前端配合见 §4.3（删 run / 模型的 409 友好提示 + 孤儿 run 取消入口）。

### 3.2 G-B2（P2）数据加载 HTTPException 正确分类，不外泄 dict repr

在 `_run` 的 except 阶梯中、`OKXError` 之后、泛 `except Exception` 之前插入 `except HTTPException`，从 `exc.detail` 提取
`code`/`message` 如实落库与下发。

```python
# weight_train_manager.py 顶部
from fastapi import HTTPException

# _run except 阶梯（154-160 之间）：OKXError → HTTPException(新) → CancelledError → Exception
except HTTPException as exc:
    code, message = _http_detail(exc)     # detail 可能是 dict 或 str，安全提取
    await self._fail(code, message)

# 模块级小助手（≤8 行，圈复杂度低）
def _http_detail(exc: HTTPException) -> tuple[str, str]:
    detail = exc.detail
    if isinstance(detail, dict):
        return str(detail.get("code", "WEIGHT_TRAIN_FAILED")), str(detail.get("message", ""))
    return "WEIGHT_TRAIN_FAILED", str(detail)
```

> 等价性不受影响：同步 `POST /factor-sets/train` 本就在路由层直接返回该 HTTPException；本改动只是让**流式路径**的
> 错误分类与同步路径对齐（`EMPTY_RANGE` 等真实码不再被吞成 `WEIGHT_TRAIN_FAILED` + dict repr）。

### 3.3 G-B4（P2）编辑模型 spec 后清空陈旧指针

在 `update_weight_model`：当 `members` / `method` / `config` 任一被**实际改动**时，清 `latest_run_id=None` 与
`best_valid_ic=None`（**历史 run 全部保留**，仅作废「当前训练产物」指针，避免新成员配旧权重 / 旧 spec 的 `bestValidIc`）。

```python
# db/weight_model_repositories.py:update_weight_model —— 改 spec 即作废指针（在写入各字段时判定）
# ⚠ R-02：_dump=json.dumps(…, ensure_ascii=False) 无 sort_keys；直接串比对会因键序差异把 no-op PUT 误判为变更
# → 误清当前权重指针。判定一律走规范化比对 _norm（排序键 / 比对解析后结构），仅改判定、不改清空逻辑。
def _norm(value) -> str:                       # 规范化序列化（键有序），用于「语义是否变更」判定
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
spec_changed = False
if patch.members is not None and _norm(patch.members) != _norm(json.loads(row.members_json)):
    row.members_json = _dump(patch.members); spec_changed = True
if patch.method is not None and patch.method != row.method:
    row.method = patch.method; spec_changed = True
if patch.config is not None and _norm(patch.config) != _norm(json.loads(row.config_json or "{}")):
    row.config_json = _dump(patch.config); spec_changed = True
# …（其余 name/instId/bar/horizon/notes/enabled/factorSetId 字段照旧赋值）
if spec_changed:
    row.latest_run_id = None     # 当前训练产物随 spec 改动失效（历史 run 仍在 list）
    row.best_valid_ic = None
```

> 与因子层「权重必须覆盖当前成员（`assert_weights_cover` / `weightsCoverMembers`）才可用」的陈旧不变量同构：spec 一变，
> 旧权重对新成员不再有效，指针即应作废。
>
> **⚠ R-03（修正上游论证）**：`refresh_weight_model_spec`（builder 重训复用路径，`:147-153`）**自身 `commit`（`:151`）**，随后训练
> **异步**进行——「新成员 + 旧权重指针」在**整个训练时长内已提交且对外可见**（详情页 / 列表可读到），**并非「仅同事务内、对外不可见」**。
> 取舍二选一（实现期定夺，二者皆守纯加法）：**(a)** 在 `refresh_weight_model_spec` 的 spec 变更分支同样清 `latest_run_id`/`best_valid_ic`
> （与 G-B4 同构，重训期详情不再展示旧权重）；**(b)** 保留指针但**重训进行中（存在 running run）时详情不渲染「当前权重」卡、只呈现
> running run 进度**。本方案**默认 (a)**（更彻底、与 G-B4 一致）；G-T7/详情测试须断言重训中不展示陈旧权重。

### 3.4 G-B5（P2）`normWindow` / `clip` 纳入边界 400 校验

在 `_validate_train_config` 末尾补两键（复用既有 `_config_num`，保持「全部可选、缺省合法」与 `clip≤0` 关闭裁剪哨兵语义）：

```python
# weight_model_service.py:_validate_train_config 追加（紧接 nEstimators/maxDepth 段后）
nw = _config_num(config, "normWindow")           # present → 须正整数
if nw is not None and (nw != int(nw) or nw <= 0):
    raise HTTPException(400, detail={"code": "FACTOR_TRAIN_INVALID_CONFIG",
        "message": f"normWindow 须为正整数，收到：{config['normWindow']!r}"})
_config_num(config, "clip")                       # present → 仅断言可转数值（clip≤0 是合法「关闭裁剪」哨兵，不限范围）
```

> `_config_num` 对非数值已抛 400 `FACTOR_TRAIN_INVALID_CONFIG`；对 `clip` 只调用以触发数值性校验、**不**加范围约束（保 H-B5/R-01
> 的哨兵语义）。如此 `normWindow:"x"` / `clip:"x"` 在请求边界即干净 400，不再漏到 `_run` 抛 `ValueError` 落 `WEIGHT_TRAIN_FAILED`。

### 3.5 G-B3（P2）满队列终态兜底连带重投 `result`

在 H-B7 心跳兜底（`api/weight_models.py:141-146`）里，关闭前从 `snapshot()` 找出**最后一个 `result` 事件**（若有且本连接
可能未收）一并重投，再发终态、关闭。`result` 客户端是 `setResult(...)`（幂等覆盖、非 append），重投安全；`curve`（append）不重投。

```python
# api/weight_models.py:ws_weight_train 终态兜底（141-146 改）
if job.is_terminal:
    buf = job.snapshot()
    last_result = next((e for e in reversed(buf) if e.get("type") == "result"), None)
    if last_result is not None:
        await websocket.send_text(json.dumps(last_result))     # G-B3：补发权重载荷（幂等）
    if buf and buf[-1].get("type") in _TERMINAL_TYPES:
        await websocket.send_text(json.dumps(buf[-1]))
    await websocket.close(); break
```

> 与 §3.8 的 `weight_train_curve_steps` 上限（G-B9）联动：上限使缓冲（≈`steps+phase+3` 事件）远小于 256 队列容量，
> 令丢弃在构造上不可达；本兜底为纵深防御（仅在慢消费者已丢事件的超时路径触发，重投 `result` 幂等不致重复渲染）。

### 3.6 G-B6（P2 潜伏）`start_job` 半注册自愈

`start_job` 把 `job.start()` 包进 try，失败则从 `_jobs` 弹出半注册 job 并复位计数；`reserve/release` 维持「预占→注册即转
计数」语义不变，杜绝 `active_count()` 永久虚高。

```python
# weight_train_manager.py:start_job
self._jobs[job.job_id] = job
try:
    self.release()           # 预占转 running 计数
    job.start()
except Exception:
    self._jobs.pop(job.job_id, None)   # 半注册回滚（避免永不终态的幽灵 running 计数）
    raise
return job
```

### 3.7 G-B8（P2 可观测性）启动 / 终态结构化日志

`_run` 入口与三处 finalize 各补一条 `logger.info`（run_id、method、resolved backend、样本数、状态）。纯加法，不改控制流。

```python
# weight_train_manager.py
logger.info("weight run %d 启动流式训练：method=%s inst=%s bar=%s",
            self.run_id, spec.method, spec.window.inst_id, spec.window.bar)   # _run 开头
logger.info("weight run %d 训练完成 succeeded：backend=%s nTrain=%s",
            self.run_id, result.trainerVersion, result.metrics.get("nTrain"))  # _finalize_success
# _fail / _finalize_cancelled 各补 logger.info(状态 + code)
```

### 3.8 G-B9 / G-B10（P2 minor）设置上限 + 模型 CRUD 校验 config

```python
# config.py:126-128 —— 补 le 上限（保留既有 default/ge）
max_weight_train_jobs:   int = Field(default=4,  ge=1, le=16)
weight_train_curve_steps:int = Field(default=6,  ge=2, le=20)
weight_model_run_cap:    int = Field(default=50, ge=1, le=500)
```

```python
# weight_model_service.py:create_model / update_model —— 与 train 入口对齐，补 config 校验（G-B10）
_validate_train_config(req.config or {})    # create_model：紧接 _validate_bar(req.bar) 后
if req.config is not None:                   # update_model：仅当传了 config
    _validate_train_config(req.config)
```

### 3.9 G-B7（P2 · 可选 · 等价敏感）全零权重退化诚实标注

当某后端产出全零原始权重（`engine/factors/training.py:252-256` 的 `total==0` 分支）回落等权时，于**单一共享装配
`build_train_result`** 内向 `note` 追加退化提示（如「⚠ 后端返回全零重要度，已回落等权」），使其不被当作真实拟合。

> **强约束（等价性硬门禁）**：该 `note` 改动必须落在 `f=1.0` 末步与 `POST /factor-sets/train` **共用**的 `build_train_result`
> 单源装配里，使两路产物**仍字节级一致**（同输入→同 note）。等价性回归测试须保持绿；若该测试逐字段比对含 `note`，因两路同源
> 故仍相等。**本项标为可选**：若实现期评估触碰共享装配风险偏高，可降级为「后端返回全零→`_run` 内对 result 附带 WS 侧 `note`
> 提示」而**完全不改装配**（仅前端展示层提示，零等价风险），由 Subtask #1 评审定夺取舍。

> **E1–E4 增强**（可选，§9.4 第 3 批）：E1 schema 层 `instId` upper-case（`schemas.py` Weight* 请求模型）；E2 `list_weight_runs`
> 的 `status` 白名单校验 → 422（`api/weight_models.py:87` Query 校验或 service 内判）；E3 `_dump`/result `json.dumps` 传
> `allow_nan=False` 并对 NaN 权重诚实报错；E4 lifespan 周期任务仅对**不在 `_jobs`** 的 running 行做进程内对账（复用
> `reconcile_dangling_weight_runs` 思路但加「跳过仍在注册表的 run」过滤，闭合 H-B1 残留而不误杀在跑 run）。

---

## 四、技术方案 · 主线 B（前端正确性与体验）

> 改动均在 `apps/web/components/Factor/*`、`apps/web/lib/weightModel*.ts` 内；交接态为瞬态、不入 partialize，
> **无需 persist 版本号变更**（沿用 v23）。设计 token / 明暗主题零适配延续上游。

### 4.1 G-F1（P1）「应用到组合并返回」严格限定 builder-origin

`applyToBuilder` 与「应用」按钮仅在 `target.origin === "builder"` 时可用 / 渲染；model-origin 训练完成后只提供「查看模型详情 / 返回列表」，杜绝覆盖用户当前组合搭建草稿与劫持编辑上下文。

```tsx
// WeightTrainLive.tsx
const canApply = target.origin === "builder";      // 仅组合搭建发起的训练可回流
const applyToBuilder = () => {
  if (!canApply) return;                            // 防御：model-origin 绝不回流
  const ids = handlesRef.current;
  if (!result || !ids) return;
  setWeightTrainResult(buildTrainResult(result, target, ids));
  setSection("builder");
};
// 按钮：{canApply && (<Button … disabled={status!=="succeeded" || !result} onClick={applyToBuilder}>应用到组合并返回</Button>)}
```

> 不动 `FactorSetBuilder` 消费 effect（builder-origin 路径正确：编辑既有组合时 `target.factorSetId=editingSetId`，回流恢复
> 编辑上下文正是所需）。本修复**只收窄触发条件**，反而**保护** factor_set / 同步链路不被跨上下文污染。备选「确认后再丢弃
> 非空草稿」更重，留待 Subtask #1 评审取舍；默认取「隐藏」简洁方案。

### 4.2 G-F2 详情页训练态轮询

`WeightModelDetail` 的 `runs` 查询镜像列表：任一 run `status==="running"` 时设 `refetchInterval`（如 4000ms），否则不轮询。

```tsx
// WeightModelDetail.tsx：runs 查询
refetchInterval: (q) => (q.state.data?.some((r) => r.status === "running") ? 4000 : false),
```

### 4.3 G-F4 删 run 失效列表缓存 + 409 友好提示

`deleteRun`（普通 `async` 函数，`WeightModelDetail.tsx:35-40`，**非 `useMutation`**——R-07 措辞校正）删除完成后追加失效裸
`["weight-models"]`（列表前缀）。并对 §3.1 的 409（模型删 / run 删训练中）给应用内提示（沿用既有错误解包），而非静默失败。

```tsx
// WeightModelDetail.tsx:deleteRun —— 删除完成后失效（普通 async 完成路径，非 onSuccess 回调）
qc.invalidateQueries({ queryKey: ["weight-models", modelId] });
qc.invalidateQueries({ queryKey: ["weight-models", modelId, "runs"] });
qc.invalidateQueries({ queryKey: ["weight-models"] });   // G-F4：覆盖列表键
// catch：若 ApiRequestError.code === "WEIGHT_RUN_RUNNING" → 提示「训练进行中，请先取消训练」
```

> **R-01 配套（应交付，配合 §3.1 孤儿处置）**：详情页对「孤儿 running run」（`status==="running"` 但取消返 404/`unknown`）给
> **取消 / 清除入口**——点取消若得 404（无在跑 job），即调一次模型删除前置对账或提示「该训练已不在运行，删除模型即自动清算」，
> 使用户在删模型前也能就地了结孤儿 run，与 §3.1 删除路径对账互补。

### 4.4 G-F6 错误码友好映射（落地 H-F9 未实装项）

在 `lib/weightModel.ts` 新增 `codeLabel(code)`（与 `methodLabel`/`phaseLabel` 并列），覆盖
`WEIGHT_TRAIN_INSUFFICIENT_SAMPLES`/`OKX_UNREACHABLE`/`FACTOR_TRAIN_TOO_LARGE`/`WEIGHT_TRAIN_BUSY`/
`WEIGHT_TRAIN_INTERRUPTED`/`WEIGHT_TRAIN_CANCELLED`/`FACTOR_INVALID_BAR`/`FACTOR_TRAIN_INVALID_CONFIG`/
`EMPTY_RANGE`/`CANCEL_TIMEOUT`/`CANCEL_FAILED` 等；未知码回落原串。`WeightTrainLive.tsx:219`、`WeightModelDetail.tsx:215`
改用 `codeLabel(...)`，原始码降级为 `title` tooltip。

### 4.5 G-F5 aria-live 播报

ProgressBar / ResultSummary 容器加 `role="status" aria-live="polite"`；错误条加 `role="alert"`；连接中断 / 等待数据条
加 `aria-live="polite"`；builder / editor 的 `notice` 段加 `role="status"`。复用 `FactorCatalog.tsx:41` 既有模式。

### 4.6 G-F8 回流结果 i18n 对齐

`FactorSetBuilder.tsx:426` 改 `methodLabel(method)`；`:431` 的 `n_train`/`n_valid` 改本地化「训练样本 / 验证样本」，与
`WeightTrainLive.tsx:288` `ResultSummary` 同口径。

### 4.7 G-F3 / G-F7 历史分页 + 时间窗呈现

- G-F3：`WeightModelDetail` 的 `runs` 查询传 `limit`（如 50），`RunHistoryTable` 末尾加「加载更多」（递增 limit 或翻页）；
  复用 `weightModelApi.runs(modelId, { limit })`（`weightModelApi.ts:88-94`）。
- G-F7：直播头部（`WeightTrainLive.tsx:195`）与详情「训练新版本」（`WeightModelDetail.tsx:61`）附格式化 `startTs–endTs`
  （复用 `lib/format.ts` 日期格式化），让用户核对训练日期段。

### 4.8 G-F9 / G-F10 / G-F11 低优修补

- G-F9：取消超时守卫改用 `statusRef`（或函数式读取最新 status）判定，去除闭包陈旧依赖；`status==="failed"` 时不渲染
  `liveWeights` 卡（或标签明确为「失败前最后权重」）。
- G-F10：`WeightModelPanel` 渲染 `WeightTrainLive` 时加 `key={target.jobKey ?? target.modelId ?? "live"}` 防 target 切换复用；
  `WeightModelList.tsx:160`「训练」按钮在 `latestRun?.status==="running"` 时禁用 + tooltip「该模型正在训练」。
- G-F11：移除 switch 的冗余 `aria-label`（保留可见文），或改 `<label htmlFor>` 关联，消除双重播报。

### 4.9 设计精修（增强，§9.4 第 2/3 批）

骨架屏替换纯文本加载态（`WeightModelList`/`WeightModelDetail`，复用 `ui` `Skeleton`）；`WeightModelDetail` 概要
`model.isPending` 骨架；全局 `prefers-reduced-motion` 门（gate 进度条 / 骨架脉冲 / 过渡）；曲线 tooltip 键盘 / 触屏可达 +
数据表回退；RankIC 面板零基线参考线；详情页对孤儿 running run 给取消入口；富化空状态（轻图标）。

---

## 五、技术方案 · 主线 C（测试护栏）

> 沿用后端 `asyncio_mode=auto` + `conftest.py` 唯一临时 `DB_PATH` + `make_synthetic_candles`；前端 `vitest + jsdom +
> NODE_ENV=test` + `vi.mock`。bug 类（G-B1/G-B2/G-B4/G-B5/G-F1）一律**先红后绿**。高风险路径（删模型守门、并发 finalize、
> WS 重连）用真实集成（真 manager + 真 WS + 临时 DB），不 mock 状态机本身。

- **G-B1（R-01 双向）**：`test_weight_models_api.py`（改）/ 新增——(i) **真·在跑**（manager 注册一个非终态 job 含该 run_id）→
  `DELETE /weight-models/{id}` 返 409 `WEIGHT_RUN_RUNNING`，模型与 run 仍在；run 转终态后可删（204）。(ii) **孤儿 running**
  （DB 直插一条 running run、manager 无对应 job）→ `DELETE` 返 204，删前该 run 经 `fail_orphan_running_runs` 对账为 `failed`、
  指针 repoint，删后随级联清。两例缺一不可，专防「孤儿致永久不可删」回归。
- **G-B2**：空区间训练 → run `failed` 且 `errorCode=="EMPTY_RANGE"`、`errorMessage` 无 `"400: {"` dict repr。
- **G-B4**：`test_weight_model_repo.py`（改）——finalize 出 `latest_run_id`/`best_valid_ic` 后 `update_model` 改 members →
  二者清空、历史 run 仍在；仅改 `name`/`enabled` 不清指针。
- **G-B5**：`test_weight_models_validation.py`（改）——`normWindow:"x"` / `clip:"x"` →400 `FACTOR_TRAIN_INVALID_CONFIG`，
  `clip:-1`（合法哨兵）/ 缺省 `normWindow` 不被拒。
- **G-B3/G-T3**：满队列丢 `result` 后超时兜底重投 `result`+终态、关闭，客户端拿到权重。
- **G-T2（端到端）**：构造真实合成 K 线 → builder 训练完成 → 回流 result → 保存组合 → 断言伴生 `factor_score` 策略已建 /
  同步（复用既有同步测试断言）；杜绝链路回归。
- **G-T1**：新 `WeightModelList.test.tsx`——显示已停用切换改 list 入参、running 4s 轮询、删除 ConfirmDialog 流、删除中禁用、
  `remove` 后 `invalidateQueries(["weight-models"])`。
- **G-F1**：`WeightTrainLive.test.tsx`（改）——`origin:"model"` 训练成功**不渲染**「应用到组合并返回」、`applyToBuilder` no-op；
  `origin:"builder"` 正常回流。
- **G-F2/G-F4/G-F6**：详情 running 轮询触发 refetch；删 run 后列表查询被失效；错误码经 `codeLabel` 友好展示。
- **G-T4…G-T11**（应交付 / 可选）：WS 中途重连回放、并发 finalize 指针、`TrainingCurveChart` 渲染、详情删流、editor 新建
  路径、horizon/12 成员/非 1H 端到端、曲线 NaN 边界、`weightTrainWs.ts` 直测。

---

## 六、文件 / 模块变更计划（File / Module Change Plan）

> 全部为**修改既有文件 + 新增测试**；**无新增源模块、无新增表 / 列**（零迁移）。`(改)` 修改、`(新)` 新建。

### 后端（services/quant）

| 文件 | 动作 | 意图（一行） |
|---|---|---|
| `app/db/weight_model_repositories.py` | 改 | G-B1 `delete_weight_model` 保留轻量 running 兜底守门 + 新增 `fail_orphan_running_runs`（R-01 孤儿对账）；G-B4 `update_weight_model` 改 spec（规范化比对，R-02）即清 `latest_run_id`/`best_valid_ic`，`refresh_weight_model_spec` 同步清（R-03 取舍 a）。 |
| `app/api/weight_models.py` | 改 | G-B1 模型删 409 `WEIGHT_RUN_RUNNING` 分支；G-B3 WS 终态兜底连带重投 `result`；（E2 可选）`status` Query 白名单 422。 |
| `app/factors/weight_model_service.py` | 改 | G-B1 `delete_model` 区分真·在跑 / 孤儿（R-01：真在跑 409、孤儿先对账后删）；G-B5 `_validate_train_config` 补 `normWindow`/`clip` 校验；G-B10 `create_model`/`update_model` 调 `_validate_train_config`。 |
| `app/factors/weight_train_manager.py` | 改 | G-B1 新增 `active_run_ids()` 访问器（R-01）；G-B2 `_run`（except 阶梯 `:154-160`）增 `except HTTPException` + `_http_detail` 助手；G-B6 `start_job` 半注册自愈；G-B8 启动 / 终态 `logger.info`。 |
| `app/config.py` | 改 | G-B9 三项权重设置补 `le` 上限。 |
| `app/engine/factors/training.py` | 改（可选） | G-B7 全零权重退化在共享 `build_train_result` 附 `note`（等价敏感，§3.9 强约束；可降级为不改装配）。 |
| `app/schemas.py` | 改（可选） | E1 `instId` upper-case 规范（Weight* 请求模型）；E3 `allow_nan=False`。 |
| `app/main.py` | 改（可选） | E4 周期性「仅非 `_jobs` running 行」进程内对账（闭合 H-B1 残留，不碰在跑 run）。 |

### 前端（apps/web）

| 文件 | 动作 | 意图（一行） |
|---|---|---|
| `components/Factor/WeightTrainLive.tsx` | 改 | G-F1 应用按钮 / `applyToBuilder` 限定 `origin==="builder"`；G-F5 aria-live；G-F6 `codeLabel`；G-F7 时间窗；G-F9 取消守卫去闭包陈旧 + 失败不显「收敛中权重」。 |
| `components/Factor/WeightModelDetail.tsx` | 改 | G-F2 running 轮询；G-F3 历史 limit + 加载更多；G-F4 删 run 失效列表 + 409 提示；G-F6 `codeLabel`；G-F7 时间窗提示；（增强）概要骨架 / 孤儿 run 取消入口。 |
| `components/Factor/WeightModelList.tsx` | 改 | G-F10 在跑模型禁「训练」；G-F11 开关标签去重；（增强）骨架屏 / 富空状态。 |
| `components/Factor/WeightModelPanel.tsx` | 改 | G-F10 `WeightTrainLive` 加 `key`（按 target 隔离实例）。 |
| `components/Factor/FactorSetBuilder.tsx` | 改 | G-F8 回流结果 `methodLabel` + 「训练样本 / 验证样本」本地化；G-F5 `notice` role=status。 |
| `components/Factor/TrainingCurveChart.tsx` | 改（增强） | RankIC 零基线参考线；曲线 tooltip 键盘 / 触屏可达 + 数据表回退；`prefers-reduced-motion`。 |
| `lib/weightModel.ts` | 改 | G-F6 新增 `codeLabel(code)`；（G-F1）`WeightTrainResult` 仅在 origin=builder 构造时使用（构造函数不变，触发收窄在组件）。 |
| `components/ui/index.tsx` | 改（增强） | `prefers-reduced-motion` 友好的过渡 / 骨架脉冲（加法工具类）。 |

### 测试

| 文件 | 动作 | 意图 |
|---|---|---|
| `services/quant/tests/test_weight_models_api.py` | 改 | G-B1 删模型：**真·在跑 run → 409**、**仅孤儿 running run → 对账后 204 可删**（R-01 双向）、run 转终态后可删；G-T9 横向补 12 成员 / 非 1H。 |
| `services/quant/tests/test_weight_models_validation.py` | 改 | G-B5 `normWindow`/`clip` 校验；G-B10 create/update config 校验。 |
| `services/quant/tests/test_weight_model_repo.py` | 改 | G-B1 `fail_orphan_running_runs` 置 failed + repoint（R-01）；G-B4 改 spec 清指针 / 仅改 name·enabled·**键序不同的等价 config** 不清（R-02）/ `refresh_weight_model_spec` 改 spec 同清（R-03）；G-T5 并发 finalize 指针。 |
| `services/quant/tests/test_weight_train_resilience.py` | 改 | G-B2 加载 HTTPException 分类；G-B6 半注册自愈。 |
| `services/quant/tests/test_weight_train_ws.py` | 改 | G-B3/G-T3 满队列重投 `result`；G-T4 中途重连回放。 |
| `services/quant/tests/test_weight_models_e2e.py` | 新 | G-T2 端到端：训练→回流→保存组合→伴生策略同步。 |
| `apps/web/components/Factor/WeightModelList.test.tsx` | 新 | G-T1 列表全行为（H-F10 守护 + 缓存失效）。 |
| `apps/web/components/Factor/WeightTrainLive.test.tsx` | 改 | G-F1 origin 限定回流；G-F9 取消守卫 / 失败权重隐藏。 |
| `apps/web/components/Factor/WeightModelDetail.test.tsx` | 改 | G-F2 running 轮询；G-F4 删 run 失效列表 + 409 提示；G-F6 codeLabel；G-T7 删流。 |
| `apps/web/components/Factor/WeightModelEditor.test.tsx` | 改 | G-T8 新建路径（含 config 校验提示）。 |
| `apps/web/components/Factor/TrainingCurveChart.test.tsx` | 新（可选） | G-T6 SVG 渲染 / tooltip / 零基线 / 空态。 |
| `apps/web/lib/weightModel.test.ts` | 改 | G-F6 `codeLabel` 覆盖 / 回落；G-T10 曲线 NaN/Infinity 边界。 |

---

## 七、接口设计（Interface Design）

> **零破坏**：现有 REST/WS 契约**形状不变**。本轮仅新增 / 复用错误码与设置上限，均为**加法**。

### 7.1 新增 / 复用错误码（沿用 `{error:{code,message}}`）

| 端点 | 状态码 | code | 触发 |
|---|---|---|---|
| `DELETE /api/weight-models/{id}` | 409 | `WEIGHT_RUN_RUNNING` | 模型存在在跑 run（G-B1，复用 run-删既有码）。 |
| `POST/PUT /api/weight-models`、`POST /api/weight-models/train` | 400 | `FACTOR_TRAIN_INVALID_CONFIG` | `normWindow`/`clip` 非数值、`normWindow≤0`（G-B5）；create/update 亦校验 config（G-B10）。 |
| 流式 `error` 事件 / run `errorCode` | — | `EMPTY_RANGE`（及加载层真实码） | 数据加载 HTTPException 正确分类（G-B2），不再吞成 `WEIGHT_TRAIN_FAILED`+dict repr。 |
| `GET /api/weight-models/{id}/runs?status=` | 422（可选 E2） | 校验失败 | `status` 非白名单 `{running,succeeded,failed,cancelled}`。 |

`WS /api/ws/weight-train/{job_id}` 事件序列（`run→phase*→curve*→result→done|error|cancelled`）与回放语义**完全不变**；
G-B3 仅在满队列丢事件的超时兜底路径补发缓冲 `result`（幂等，不改正常时序）。设置上限（G-B9）为内部约束，不改任何请求形状。

### 7.2 前端交接 / 派生行为（契约不变，行为收窄 / 修正）

- G-F1：「应用到组合并返回」仅 `target.origin==="builder"` 可触发；`WeightTrainTarget`/`WeightTrainResult` 类型形状不变。
- G-F2/G-F4：详情 `runs` 查询 `refetchInterval`（running 时）+ 删 run 失效 `["weight-models"]`，纯客户端缓存策略，无后端契约变化。

---

## 八、数据模型（Data Model）

### 8.1 持久层：零变更（硬约束）

**不新增 / 不修改任何表或列**：`weight_models`、`weight_train_runs` 结构与索引保持上游不变；`init_db()` 的 `create_all`
加法建表口径不变；**零数据迁移**。G-B1 仅在删除路径加查询守门（运行期行为）；G-B4 仅在 `update` 路径**写已存列**
`latest_run_id=None`/`best_valid_ic=None`（运行期行为，非 schema 变更）；G-B7 仅复用既有 `note` 列。`latest_run_id`
（FK-less 整数）与 `best_valid_ic`（反范式冗余）语义与上游一致，仅在「编辑 spec 后」更诚实（陈旧指针即作废）。

### 8.2 进程内 / 配置形状（加法）

| 位置 | 新增 / 改写 | 说明 |
|---|---|---|
| `weight_train_manager._run` | `except HTTPException` + `_http_detail()` 助手 | G-B2 错误分类。 |
| `WeightTrainManager.active_run_ids` | 新增访问器：非终态 job 的 `run_id` 集 | G-B1/R-01 区分真·在跑 vs 孤儿。 |
| `weight_model_repositories.fail_orphan_running_runs` | 新增：scoped 孤儿 running→failed + repoint | G-B1/R-01 删除路径孤儿对账。 |
| `WeightTrainManager.start_job` | try/rollback 半注册 | G-B6 自愈。 |
| `config.Settings` | 三项 `le` 上限 | G-B9，仅收紧校验、默认值不变。 |
| `lib/weightModel.ts` | `codeLabel(code:string):string` 纯函数 | G-F6 错误码→中文映射（未知回落原串）。 |
| `WeightModelDetail` / `WeightTrainLive` 组件 | `refetchInterval`、`limit`、`statusRef`、`codeLabel` 引用 | G-F2/G-F3/G-F9/G-F6。 |

### 8.3 不变量（强约束）

- **指针自洽**：`latest_run_id`/`best_valid_ic` 永远指向**当前 spec 下**的有效 succeeded run；spec 改动 → 指针作废（G-B4）；
  剪枝 / 删除 → 重算（R-05，上游既有）。
- **删除一致**：模型 / run 删除对**真·在跑**（manager 有非终态 job）一律 409（G-B1 + 既有 run-删），不存在「删了在跑训练却不取消」
  的窗口；对**孤儿 running**（DB=running 但无在跑 job）则删除路径**就地对账 `failed` 再删**（R-01），不存在「永久不可删」死锁。
- **错误诚实**：任何失败 run 的 `errorCode` 必为稳定语义码（绝不 `"400: {...}"` dict repr），`error` WS 事件同。

---

## 九、测试与验收标准（Testing & Acceptance Criteria）

### 9.1 测试策略（先复现、再修复）

bug 类 G- 项（G-B1/G-B2/G-B4/G-B5/G-F1）**必须先写能稳定复现的失败测试，再改业务码**（红→绿）。高风险路径（删模型守门、
WS 终态重投、并发 finalize、端到端链路）用真实集成。前端组件测试沿用 `NODE_ENV=test` + `vi.mock`（`weightModelApi`/
`weightTrainWs`/`FactorLab`）。

### 9.2 关键回归断言

- **G-B1（R-01）**：**真·在跑**（manager 有非终态 job）删模型 →409、模型 / run 不变；run 终态后可删 204；**仅孤儿 running run**
  （DB=running 但无在跑 job）删模型 →孤儿对账 `failed` 后 204 可删（**不再永久卡 409**）；删后历史 run 随级联清。
- **G-B2**：空区间 → run `failed` 且 `errorCode=="EMPTY_RANGE"`、`errorMessage` 不含 dict repr。
- **G-B4（R-02/R-03）**：改 members/method/config →指针清空、历史 run 保留；仅改 name/enabled **及键序不同的等价 config** 不清指针；
  `refresh_weight_model_spec` 改 spec 同清（取舍 a）。
- **G-B5**：`normWindow`/`clip` 非数值 / `normWindow≤0` →400；`clip≤0`（哨兵）/ 缺省 `normWindow` 不被拒。
- **G-F1（R-04）**：model-origin 训练成功**不渲染**应用按钮、`applyToBuilder` no-op；builder-origin 正常回流且不回归既有交接断言；
  并断言 `setWeightTrainResult` 全仓**唯一写入点**经 origin 守门（无旁路自动回流）。
- **G-T2 端到端**：训练→回流→保存组合→伴生 `factor_score` 策略同步**全链路绿**（沿用既有同步断言守护）。
- **等价性硬门禁**：`f=1.0` 末步与 `POST /factor-sets/train` 的 `weights/normStats/metrics`（及 `note`，若 G-B7 落地则同源仍相等）
  逐字段相等的既有测试**继续绿**；factor_set CRUD、factor_score 回测 / 对比 / Walk-forward 既有测试**继续绿**。

### 9.3 验收标准（Definition of Done · 面向 Subtask #2/#3）

1. 2 个 P1（G-F1、G-B1）与必交付 P2（G-B2/G-B4/G-B5/G-F2/G-F4/G-F6）均有**先红后绿**回归测试守护；新增 / 改写代码守
   800 行 / 方法 50 行 / 参数 ≤5 / 嵌套 ≤4 / 圈复杂度 ≤10 / 行宽 ≤80 阈值。
2. 后端 `pytest`（quant，含本轮新增 / 改写套件）全绿、默认集（`-m 'not network'`）全绿；前端 `pnpm test`（vitest，`NODE_ENV=test`）
   全绿；`tsc --noEmit` 与 lint 通过。
3. 既有等价性、factor_set ⇄ strategy 同步、factor_score 全链路测试**零回归**；**无新增表 / 列、零迁移**；`scripts/dev.py` 可启动 / 编译。
4. 实测三类一致性缺陷消除：删带在跑训练的模型被 409 拦（而非悄悄删）、改模型成员后详情不再展示旧权重、空区间训练显示
   `EMPTY_RANGE` 友好文案（而非 dict repr）。
5. 交互体验达标：详情训练态实时刷新、删 run 后列表卡片即时更新、错误码经 `codeLabel` 友好展示、关键状态有 aria-live 播报、
   回流结果文案与直播一致。

### 9.4 交付优先级闸（Right-sizing）

发现量适中且分层清晰，为保单轮可交付、避免体验精修拖累正确性，按下列优先级分批，前一批未全绿不进下一批：

1. **必交付（Must-ship，门禁）**：P1 `G-F1`/`G-B1`（**含 R-01 孤儿 run 处置——朴素 409 守门不达标**）+ 高价值一致性 P2
   `G-B2`/`G-B4`（含 R-02 规范化比对 / R-03 重训路径同清）/`G-B5`/`G-F2`/`G-F4`/`G-F6` + HIGH 测试缺口 `G-T1`/`G-T2`，及其
   先红后绿回归测试（含 R-01 孤儿可删 / 真在跑 409 双向）。**此批全绿才算本轮达标。**
2. **应交付（Should-ship）**：后端健壮 / 可观测 `G-B3`(+`G-B9`)/`G-B6`/`G-B8`/`G-B10`；前端体验 `G-F3`/`G-F5`/`G-F7`/`G-F8`/`G-F9`/
   `G-F10`/`G-F11`；**详情页孤儿 run 取消 / 清除入口（R-01 配套，由可选升应交付）**；设计精修（骨架屏 / 概要骨架 / 减少动效 /
   富空状态）；中等测试缺口 `G-T3…G-T8`。
3. **可选（Optional / Nice-to-have）**：`G-B7`（等价敏感，§3.9 取舍；R-05 采纳「仅前端 / WS 侧 note、零改装配」降级）、`E1–E4`
   （其中 E4 周期对账经 R-01 降为纯纵深防御）、曲线 tooltip 键盘 / 触屏 a11y、RankIC 零基线、低优测试 `G-T9…G-T11`。

---

## 十、风险与缓解（Risks & Mitigations）

| 风险 | 影响 | 缓解 |
|---|---|---|
| 修精修触碰 `f=1.0` 等价路径 | 双路径漂移、违反硬门禁 | 本轮**不动** `train_weights`/`fit_at_fraction`；唯一可选触碰点 G-B7 的 `note` 落在**单一共享** `build_train_result`（两路同源仍字节级一致），且可降级为「仅前端展示提示、零改装配」；等价性测试作为门禁先绿。 |
| G-B1 改 409 后用户「删不掉」在跑模型 | 体感受阻 | 与既有 run-删 409 不变量一致；前端 409 友好提示「请先取消训练再删」。 |
| **G-B1 朴素 409 致孤儿 run 模型永久不可删（R-01）** | **正确性 / 不可恢复** | 守门**区分真·在跑 vs 孤儿**：仅真·在跑（manager 有非终态 job）409；孤儿 running（DB=running 无在跑 job）在删除路径**就地对账 `failed` 再删**，无需等重启；防御纵深保留 repo 兜底守门；双向测试守护。 |
| G-B4 清指针误伤合法历史 | 历史 run 丢失 | **只清指针、绝不删 run**；历史 run 仍在 `GET /runs`；仅改 name/enabled 不触发（测试守护两侧）。 |
| G-B2 except 顺序错位 | CancelledError 被误吞 / 500 复现 | `HTTPException` 插在 `OKXError` 后、`CancelledError` 前（`CancelledError` 在 3.8+ 为 BaseException，不被 `except Exception` 捕获）；测试断言空区间→`EMPTY_RANGE`、取消→`cancelled` 各自正确。 |
| G-F1 收窄误伤 builder-origin 回流 | 正常回流失效 | 仅在 `origin!=="builder"` 隐藏 / no-op，builder-origin 全程不变；测试断言两侧（model 不回流、builder 正常回流 + 既有交接断言不回归）。 |
| G-B3 重投 `result` 致重复渲染 | UI 抖动 | `result` 客户端为 `setResult`（幂等覆盖、非 append），重投安全；`curve` 不重投；仅超时兜底路径触发。 |
| 详情轮询 / 列表轮询叠加致请求放大 | 网络负载 | 仅 `running` 时轮询（4s），无 running 即停；与列表 H-F10 同口径，测试断言无 running 不轮询。 |
| aria-live / 焦点改动引入键盘陷阱 | 可访问性回归 | 复用 `FactorCatalog` 既有 `role=status` 模式与 H-F8 焦点范式，不自造逻辑。 |
| 设置上限收紧破坏既有部署覆写 | 启动校验失败 | `le` 取宽松上限（16/20/500，远高于合理用值），默认值不变；仅拦截病态超大值。 |
| 端到端测试引入网络依赖 | CI 不稳定 | 复用 `make_synthetic_candles` + monkeypatch rest，全程离线、可重复（与既有 streaming 等价测试同范式）。 |

---

## 十一、对下游实现者的提示（Implementation Notes）

- **顺序**：先补复现测试（红）→ 再改业务（绿）→ 跑等价性 + factor_set 全链路确认零回归。建议
  「P1(G-F1/G-B1) → 必交付 P2(G-B2/G-B4/G-B5/G-F2/G-F4/G-F6) → HIGH 测试(G-T1/G-T2) → 应交付 → 可选」推进。
- **复用而非另起**：G-B1 真·在跑 409 抄 `delete_weight_run` 范式、孤儿对账 `fail_orphan_running_runs` 抄 `reconcile_dangling_weight_runs`
  （`:333-353`）思路但 scoped 到单 model + 过滤 `active_run_ids()`；G-B2 的 HTTPException 提取复用 `main.py` 既有错误体结构；
  G-F6 `codeLabel` 与 `methodLabel`/`phaseLabel` 并列同文件；aria-live 抄 `FactorCatalog.tsx:41`；骨架抄 `ui` `Skeleton`；
  端到端复用 `test_weight_train_streaming.py` 的合成 K 线 + 既有同步断言。
- **绝不触碰**：`db/models.py` 的 `WeightModel`/`WeightTrainRun` 列定义、`factor_sets` 表、factor_set ⇄ factor_score 同步事务、
  `POST /factor-sets/train`、`f=1.0` 等价装配（G-B7 例外须守 §3.9 强约束或降级为零装配改动）。
- **清洁度**：新增 / 改写均守 800/50/≤5/≤4/≤10/≤80 阈值；`_http_detail`/`codeLabel` 等助手保持纯函数 + 单测；DB 写聚合
  传参延续 frozen dataclass。
- **若需偏离本方案任一决策**：回填本文件「实施过程发现的方案缺陷」段并重新过评审（与上游同范式）。

---

> 本文档仅产出**设计**，不含实现代码改动；除本 `spec.md` 外不修改任何源文件，不执行 `git commit`。下游 Subtask 据此逐行落地。

---

## 评审结论（Review Verdict）

**结论：有条件通过（Approved with Conditions）。**

本方案对照已提交实现（`961a9a3`）逐节核验，**可行性 / 完整性 / 一致性 / 适度性**四维度整体扎实：定位的 34 项缺口
（12 G-B + 11 G-F + 11 G-T）**全部属实、锚点全部准确**；「纯加法 / 零 schema / 零迁移 / 不触 `f=1.0` 等价装配 / 不动
factor_set 同步契约」铁律守住；优先级分批（§9.4）右尺寸得当，必交付集聚焦真实正确性缺陷而不让体验精修拖累交付。
**未发现 P0。** 评审定位的**唯一 P1（R-01：G-B1 朴素 409 守门对孤儿 run 留洞）已在 §3.1 就地修复**（守门区分真·在跑
vs 孤儿、孤儿删除路径就地对账自愈），相关测试 / 接口 / 数据模型 / 风险 / 优先级章节已同步更新；R-02/R-03 两项 P2 一致性
缺口已就地加固，R-04…R-07 已澄清 / 确认合理。**升版 v2，无遗留 P0 / P1。**

**放行条件（下游 Subtask #2/#3 必须逐条满足，缺一不视为达标）**：

1. **G-B1 必须按 R-01 落地孤儿处置**——「真·在跑（manager 有非终态 job）→ 409；孤儿 running（无在跑 job）→ 删除路径就地
   对账 `failed` 再删」。**严禁退化为「任一 run==running 即 409」的朴素实现**（会致孤儿模型永久不可删）。必带「孤儿可删 /
   真在跑 409」双向先红后绿测试（§9.2 G-B1、§5 G-B1）。
2. **等价性硬门禁先绿后改**：任何改动落地前，`f=1.0` 末步 ↔ `POST /factor-sets/train` 的 `weights/normStats/metrics`（及
   `note`）逐字段相等的既有测试、factor_set ⇄ factor_score 同步、factor_score 回测 / 对比 / Walk-forward 既有测试**必须保持绿**。
   `G-B7` 若实现，**优先采纳 R-05 降级口径（仅前端 / WS 侧 note、零改装配）**；如确需改共享 `build_train_result`，须守 §3.9 强
   约束并使等价测试仍绿，否则**不得合入**。
3. **G-B4 指针清空走规范化比对（R-02）**，杜绝键序差异致 no-op PUT 误清指针；并就 `refresh_weight_model_spec` 重训路径
   按 R-03 取舍 (a)（spec 变更同清指针）落地 + 测试，消除「重训期详情展示旧权重」陈旧窗口。
4. **必交付批全绿才算本轮达标**（§9.4 批 1）：2 个 P1 + 必交付 P2 + `G-T1`/`G-T2`，及其先红后绿回归测试；后端 `pytest`
   默认集（`-m 'not network'`）、前端 `pnpm test`（`NODE_ENV=test`）、`tsc --noEmit` 与 lint 全绿；**无新增表 / 列、零迁移**。
5. **清洁度阈值**（CLAUDE.md：文件 ≤800 / 方法 ≤50 / 参数 ≤5 / 嵌套 ≤4 / 圈复杂度 ≤10 / 行宽 ≤80）：新增的
   `active_run_ids` / `fail_orphan_running_runs` / `_http_detail` / `codeLabel` 等保持纯函数 + 单测，`delete_model` 判别逻辑
   注意嵌套与圈复杂度（必要时抽辅助函数）。

满足以上五条即视为评审通过。若实现期发现本方案任一决策有误，按 §11 末条回填「方案缺陷」段并重新过评审。

> 评审者：Anthropic 工程团队资深评审（Subtask #1，迭代 3/3）。评审仅修改本 `spec.md`，未触碰任何源码，未执行 `git commit`。
