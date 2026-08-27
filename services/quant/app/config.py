"""应用配置 —— 从环境变量 / .env 读取 (spec §12)。

所有密钥仅存于环境变量，绝不下发前端、不入库、不进日志。
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .security.origins import DEFAULT_ORIGINS


class Settings(BaseSettings):
    """全局设置。字段名即环境变量名（大小写不敏感）。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- OKX 端点 ----
    okx_rest_base: str = "https://www.okx.com"
    # ⚠️ OKX V5 把公共行情拆到两个 WS 端点：trades/tickers/books5 仅在 **public**
    # 端点；candle{bar} K 线仅在 **business** 端点（在 public 订阅 candle 会回 60018
    # "channel doesn't exist"，反之亦然）。故两端点都要配：盘口/成交/ticker 走
    # public，K 线走 business。
    okx_ws_public: str = "wss://ws.okx.com:8443/ws/v5/public"
    okx_ws_business: str = "wss://ws.okx.com:8443/ws/v5/business"

    # ---- OKX 凭据（本期仅占位，公共行情不需要）----
    okx_api_key: str = ""
    okx_secret_key: str = ""
    okx_passphrase: str = ""

    # ---- 实盘账户（设置中心 · 实盘账户私有签名请求，spec §12）----
    # 字段名即环境变量名（OKX_ACCOUNT_*，大小写不敏感）；凭证真源为库内多账户，env 不参与。
    okx_account_demo: bool = False             # True 时私有请求附 x-simulated-trading:1（模拟盘）
    okx_account_timeout_seconds: float = 15.0  # 私有请求超时上限

    # ---- OKX 链路韧性加固（okx-link-resilience §六）----
    # 字段名即环境变量名（OKX_LINK_*，大小写不敏感）。桌面打包须在 desktop/src/lib/config.js
    # 白名单补 key.startsWith('OKX_') 方能透传——Settings 为 extra="ignore"，漏改从不报错，
    # 只会让打包版的所有旋钮静默失效（本仓库已为这类漏改栽过两次）。
    # 纪律：每个机制都有一个**关得掉的取值**（阈值 0 / 速率 0 / TTL 0），不新增分叉代码路径。
    okx_link_ws_ping_interval_s: float = 15.0      # 入站静默多久后发 "ping"
    okx_link_ws_pong_timeout_s: float = 8.0        # 发 ping 后多久无任何入站帧即判失活（0=不判）
    okx_link_ws_silence_timeout_s: float = 45.0    # 入站静默多久即判失活（兜底；0=不判）
    okx_link_ws_subscribe_timeout_s: float = 10.0  # 订阅确认超时（0=不判）
    okx_link_ws_healthy_after_s: float = 20.0      # 连接存活多久才允许复位退避（0=连上即复位）
    okx_link_ws_backoff_base_s: float = 1.0        # WS 重连退避基数
    okx_link_ws_backoff_max_s: float = 30.0        # WS 重连退避上限
    # 全局建连速率闸，**只作用于重连**（attempt ≥ 1）：首次建连不取令牌。需要被压制的是故障期间的
    # 重连风暴，而首建并发度本就由 live_auto_start_concurrency 与「一个浏览器一次订阅」约束住。
    okx_link_ws_connect_rate_per_s: float = 2.0    # 0 = 不限
    # 通道级**数据静默**判据（ai-trade-feed-self-healing 轴 A）。与 okx_link_ws_silence_timeout_s
    # 的差别是**唯一**的：那条读 last_inbound（任何入站帧，含 pong），这条读 last_data（仅业务数据
    # 帧）。「socket 活着、心跳正常、candle 频道再也不推数据」是最常见也最难查的一类断线——在本判据
    # 出现之前它**永远不会触发重连**，只表现为「K 线不动了」。
    # 预算 = clamp(bar_seconds × factor, min, max)。**对 bar ≥ 15m 恒等于上界 900 秒，这不是缺陷**：
    # candle 频道推送的是**未确认根**，推送密度只与成交密度相关、与 K 线周期无关，故 900 秒是一条
    # 与周期无关的绝对静默上限；bar_seconds 派生只在 1m～15m 区间真正起作用。
    okx_link_ws_data_silence_factor: float = 1.0    # × bar 秒数；0 = 整条判据关闭
    okx_link_ws_data_silence_min_s: float = 120.0   # 夹取下界（盘口频道与 1m 周期用这个）
    okx_link_ws_data_silence_max_s: float = 900.0   # 夹取上界（6H/12H/1D 不至于等一整根）
    okx_link_rest_max_attempts: int = 4            # 公共 REST 最大尝试次数（含首次；1=不重试）
    okx_link_rest_budget_s: float = 25.0           # 单次公共调用总墙钟预算（含等待）
    okx_link_public_rate_per_s: float = 8.0        # 公共 REST 令牌桶速率（0=不限；≈既有 0.12s 间隔）
    okx_link_public_burst: float = 4.0             # 公共桶容量
    okx_link_private_rate_per_s: float = 5.0       # **每账户**私有令牌桶速率（0=不限）
    okx_link_private_burst: float = 5.0            # 私有桶容量
    # 熔断只作用于**公共读**；私有读写永不熔断（一个裸奔的持仓要平仓时，最不该出现的就是一个本地
    # 开关拒绝把请求发出去）。探针类调用（health / 服务器时间 / 补根取数 / 手动探测）一律豁免。
    okx_link_breaker_failures: int = 5             # 公共读连续失败多少次熔断（0=不熔断）
    okx_link_breaker_cooldown_s: float = 20.0      # 熔断冷却（失败翻倍，上限 120s；任一成功即复位）
    okx_link_pool_idle_ttl_s: float = 120.0        # 私有连接池空闲存活（0=用完即关，旧行为）
    okx_link_clock_sync_interval_s: float = 900.0  # 服务器时钟同步周期（0=只在 50102 时同步）
    okx_link_gap_repair_max_bars: int = 300        # 断线补根上限（0=不补根）
    okx_link_down_alert_s: float = 20.0            # 链路断开多久推降级提示（极大值=不提示）
    okx_link_verify_attempts: int = 3              # 平仓「读回核验」最多读几次持仓（1=只读一次）
    okx_link_verify_backoff_s: float = 0.6         # 两次核验读之间的间隔（线性，不退避）

    # ---- 实盘交易（Phase 2「guarded order placement」，实盘交易模式 spec §3）----
    # 字段名即环境变量名（LIVE_*，大小写不敏感）；本轮需求把实盘下单默认翻为开启。
    # 桌面端经 desktop/src/lib/config.js 白名单透传（C1-6），dev 经 services/quant/.env 读取。
    # 安全阀仍在：设 LIVE_TRADING_ENABLED=false 即一键回退「real trading disabled」安全姿态；
    # 逐单二次确认、名义上限、标的 / net_mode 反向单本地拦截等护栏均未削弱。
    live_trading_enabled: bool = True            # 总开关：False 时 /trading/order|leverage 一律 403
    # 单笔下单名义价值本地上限（USDT），越界本地拦截。C1-7：默认收紧至 1000，避免高默认值架空
    # §8 风险①护栏③；需放大须显式 env（LIVE_MAX_ORDER_NOTIONAL_USD）覆写。
    live_max_order_notional_usd: float = 1000.0
    live_default_td_mode: str = "cross"          # 合约默认保证金模式（cross | isolated）
    live_default_leverage: float = 3.0           # 下单面板默认杠杆

    # ---- 实时自动交易（live-auto-trading spec §三/config，策略驱动无人值守下单）----
    # 字段名即环境变量名（LIVE_AUTO_*，大小写不敏感）；桌面端经 desktop/src/lib/config.js
    # 白名单 key.startsWith('LIVE_') 天然透传。安全：自动化总闸默认关闭（R1）——自动下单真正的
    # 默认关闭闸是 live_auto_trading_enabled（live_trading_enabled 现默认 True），启动须 API 层
    # 显式同时校验两闸皆为 True 方可放行。
    live_auto_trading_enabled: bool = False      # 自动化专用总闸：False 时 /live-auto 写类一律 403
    # 容量语义已由「单活跃分组（1..N 会话）+ live_auto_max_group_symbols」取代（watchlist-auto-trade-group
    # §2.4 / P2-1）：一次至多一个活跃分组，分组内成员数受 live_auto_max_group_symbols 封顶。本字段现无
    # 引用，仅作历史兼容保留（容量判定改由 manager 直接判 self._group），避免文档漂移。
    live_auto_max_sessions: int = 1              # （已弃用）旧「单会话独占」语义，现由单活跃分组取代
    # 单个活跃自动交易分组内的标的数硬上限（真实资金多标的并发的封顶护栏，R1/R6；前端拖入超限即拒绝）。
    live_auto_max_group_symbols: int = 8
    # 分组启动时签名私有请求（account/config + account/balance）的并发上限：每 N 个标的一组排队，
    # 避免启动瞬间把 N×2 发私有请求一次性打爆 OKX 私有限频触发 429（见 docs/diagnoses/
    # live-auto-multi-symbol-rate-limit-429）。默认 2，可经 env LIVE_AUTO_START_CONCURRENCY 调参。
    live_auto_start_concurrency: int = 2
    # 单标的旧路径留空默认（取可用 USDT 的 1%，保守）；分组路径不引用它（分组留空默认 = 上限本身）。
    live_auto_default_capital_frac: float = 0.01
    # 单标的旧路径绝对天花板（前置门禁强制夹紧，R4）。**保持 0.05 不变**——分组天花板见下一字段。
    live_auto_max_capital_frac: float = 0.05
    # 分组路径运营天花板（auto-trade-budget-and-allocation-settings RN-1）：默认 1.0 = 不额外限制；
    # 运营可经 env LIVE_AUTO_GROUP_MAX_CAPITAL_FRAC 收紧（如生产设 0.2 强制分组 ≤ 20%，无视前端配置）。
    # 与「自动交易设置 · 总预算上限百分比」取 min 得分组有效上限分数；服务端权威，前端不可越过。
    live_auto_group_max_capital_frac: float = 1.0
    # max_ratio（最大比例）分配模式下新增成员的保守默认上限百分比（前端 store 同名常量与之对齐）。
    live_auto_default_max_ratio_pct: float = 20.0
    live_auto_poll_interval_s: int = 5           # 持仓 reconcile / 会话盯市轮询基准间隔（秒，hardening H1 接线）
    # ---- 加固增强（live-auto-trading-hardening）：独立盯市循环 + 容错 + 看门狗（H1/H2/H3/H4）----
    # 行情停滞看门狗阈值 = bar_seconds × 该系数；超阈推降级 status（默认仅告警不平仓）。
    live_auto_staleness_factor: float = 3.0
    # 行情停滞是否自动平仓（默认 False：瞬时抖动平仓危害更大；仅运维显式开启且停滞超长才触发）。
    live_auto_flatten_on_stale: bool = False
    # reconcile 连续失败达此次数即推 degraded（默认 6 ≈ 30s @ poll_interval_s=5），恢复后清零。
    live_auto_reconcile_max_failures: int = 6
    # 停止会话平仓失败的有界重试次数（默认 2；仍失败则诚实返回 residual 并强提示人工平仓，H4）。
    live_auto_flatten_max_retries: int = 2

    # ---- 进程关停（resilience-and-hci-hardening D1）----
    # 关停链单步收敛死线（秒）。超时即取消该步并继续——绝不让一步挂死拖住引擎与连接池的释放。
    # 20s 对默认的 ai_trade_shutdown_action="hold"（只写 DB 不下单）绰绰有余；慢盘/慢盘符环境可调大。
    shutdown_step_timeout_s: float = 20.0

    # ---- AI 自动交易（ai-auto-trading spec §三/config：AI Agent 作为多策略信号的决策者）----
    # 字段名即环境变量名（AI_TRADE_*，大小写不敏感）。**必须真的声明这些字段**：Settings 为
    # extra="ignore"，未声明的 AI_TRADE_* env 会被静默丢弃（P1-6）。桌面打包须在
    # desktop/src/lib/config.js prod 白名单补 key.startsWith('AI_TRADE_') 方能透传（P1-6）。
    # AI 自动交易总闸默认开启；仍可经 AI_TRADE_ENABLED=false 显式关闭。
    # 启动须 API 层显式同时
    # 校验 ai_trade_enabled 与 live_trading_enabled 皆为 True 方可放行（双闸）。
    ai_trade_enabled: bool = True                # 自动化专用总闸：False 时 /ai-trade 写类一律 403
    ai_trade_default_capital_frac: float = 0.01  # 留空默认预算 = availUsdt × 该值（保守 1%）
    # AI 分组预算的**可选**绝对天花板分数（默认 1.0=不约束，实际天花板委托给账户级 maxBudgetPct 与分组运营
    # 天花板 live_auto_group_max_capital_frac，与非 AI 多标的分组口径统一）；运维如需 AI 专属更紧的绝对上限
    # （如恢复历史 5% 红线），可经 env AI_TRADE_MAX_CAPITAL_FRAC 下调本值（min 只能收紧、绝不放大天花板）。
    ai_trade_max_capital_frac: float = 1.0
    ai_trade_default_sl_pct: float = 0.05        # 会话默认单仓止损 pct（交易所侧 OCO 兜底；0 即不挂）
    ai_trade_default_tp_pct: float = 0.10        # 会话默认单仓止盈 pct（交易所侧 OCO 兜底；0 即不挂）
    ai_trade_default_max_hold_bars: int = 0      # 默认强制时间平仓根数（0=禁用，退出交由 AI close + 会话止盈止损）
    ai_trade_max_strategies: int = 8             # 单会话可喂给 AI 的策略数上限（超出即 400 守门）
    # AI 自动交易「分组」标的数上限（ai-trade-multi-symbol-and-timeframe §4.1）：一个 AI 分组内至多 N 个
    # 标的，每标的各是一个独立 AiTradeSession（各自一次/轮 LLM 决策）。刻意 < live_auto_max_group_symbols(8)：
    # N 个 decider 每轮各一次 LLM 调用，成本随标的数线性上升。分组总预算按 allocationPct **切分**（非倍增），
    # 故总账户敞口仍受分组总预算天花板约束——该天花板 = min(maxBudgetPct/100, ai_trade_max_capital_frac(默认
    # 1.0=不约束), live_auto_group_max_capital_frac)，由账户级 maxBudgetPct 与运营天花板共同主导（与非 AI 分组
    # 口径统一，ai-trade-total-budget-allocation 有意放开历史 5% 绝对红线）。
    ai_trade_max_symbols: int = Field(default=5, ge=1)
    ai_trade_default_context_rounds: int = 10    # 上下文刷新轮数默认值
    ai_trade_min_context_rounds: int = Field(default=3, ge=1)    # 上下文刷新轮数下限
    ai_trade_max_context_rounds: int = Field(default=200, ge=1)  # 上下文刷新轮数上限（与下方 messages 相互独立，P1-3）
    # 喂给决策模型的滚动窗口硬上限（与 ai_trade_max_context_rounds 解耦：摘要真源是 _journal 而非
    # 模型窗口 _context，P1-3）。仅为「回喂 LLM 的近 N 条」护栏，防极端参数下越界。
    ai_trade_max_context_messages: int = Field(default=40, ge=2)
    ai_trade_decision_timeout_s: float = 60.0    # 单轮 LLM 决策超时（超时一律回落 hold）
    # 单轮决策重试护栏（round-2）：仅对「瞬时 LLM 失败 / 未产出有效决策」类回落重试，穷尽后才降级 hold。
    # 语义：首次尝试之外的最多重试次数——默认 3（即最多 4 次 LLM 调用）；0=沿用旧行为「首次失败即降级」。
    # genuine 决策（合法动作 / 刻意 hold / low_confidence 风险门禁）不计入、不重试。
    ai_trade_decision_max_retries: int = Field(default=3, ge=0)
    ai_trade_retry_base_ms: int = 500     # 指数退避基数（复用 agent.llm.retry.backoff_delay_ms 语义）
    ai_trade_retry_max_ms: int = 8000     # 单次退避封顶
    ai_trade_min_confidence: float = 0.0         # 置信度门槛（<该值降级 hold）；默认 0=不启用
    # ---- 下单前二次反思校验（ai-trade-pre-trade-reflection §4.3）----
    # 决策与真实下单之间插入一次独立的反思 LLM 子调用：仅对交易意愿（open_long/open_short/close）触发，
    # hold 跳过。反思只会更保守（否决→hold 或收缩仓位，绝不放大/反向），fail-closed（超时/错误→hold）。
    # 总闸默认开（真实交易理应经校验，用户明确诉求）；False 逐字回退当前「决策即下单」行为。决策器直接
    # 构造默认关（reflection_enabled: bool=False），生产装配（manager/backtest_manager）从此处显式打开。
    # env 名 AI_TRADE_REFLECTION_* 已在 desktop config.js prod 透传白名单内（AI_TRADE_ 前缀）。
    ai_trade_reflection_enabled: bool = True      # 反思总闸；False 逐字回退「决策即下单」
    ai_trade_reflection_timeout_s: float = 45.0   # 单次反思 LLM 超时（超时 fail-closed → hold）
    # 反思瞬时失败（超时/LLM 错误/无工具/非法裁决）重试次数，复用决策同款退避基建；穷尽后 fail-closed。
    # abort 是模型真实终态裁决，零重试。默认 2（即最多 3 次反思 LLM 调用）。
    ai_trade_reflection_max_retries: int = Field(default=2, ge=0)
    # ---- 综合研判增强包（ai-auto-trading-enhancements v4 · E1/E2/E4）----
    # E1 市场体制上下文：默认开（只读输入增强，向决策 prompt 注入一行市场体制；数据不足/异常自动省略）。
    ai_trade_regime_context: bool = True         # E1：每轮注入市场体制上下文（只读；False=不注入）
    # E2 共识门禁：默认 0=关闭（等价 v2）。>0 时开仓方向同向策略数不足即硬降级 hold（独立于 LLM，仅更保守）。
    # 与既有 ai_trade_min_confidence 独立叠加：开仓须同时过「置信度 ≥ min_confidence」与「同向共识 ≥ min_consensus」。
    ai_trade_min_consensus: int = Field(default=0, ge=0)   # E2：开仓最小同向策略数门禁（0=关闭）
    # E4 单仓 OCO pct 夹紧上限：入参 stopLossPct/takeProfitPct 越界即夹到这里（留空则回落上方 default_sl/tp_pct）。
    ai_trade_max_sl_pct: float = 0.20            # E4：单仓止损 pct 硬上限（入参越界夹紧）
    ai_trade_max_tp_pct: float = 0.50            # E4：单仓止盈 pct 硬上限（入参越界夹紧）
    # ---- 用户可配置仓位操作档位（ai-trade-position-sizing-steps §2.1/§五）----
    # 请求 positionStepPct 留空时的默认档位步长（fraction，0.25=每次 25% 一格 → 档位 {25,50,75,100}%）。
    # 决策器把 LLM 给出的连续 sizeFraction 吸附到 {k·step} 档位（唯一真源 signals.position_ladder）；
    # 0=连续（旧行为，仅后端零回归默认 / API 退出口，弹窗不露出）。env 名 AI_TRADE_DEFAULT_POSITION_STEP_PCT
    # 已在 desktop config.js prod 透传白名单内（AI_TRADE_ 前缀），此处声明后方不被 extra="ignore" 静默丢弃。
    ai_trade_default_position_step_pct: float = 0.25
    # ---- 限价委托挂单（ai-trade-limit-orders §5.3）----
    # 给 AI 决策链路补齐限价能力：Agent 可在 submit_trade_decision 里给 orderType=limit + limitPrice，
    # 并用新动作 cancel_order 撤回自己未成交的挂单；仍可照旧市价委托（需求原文「之后 Agent 可以限价
    # 委托，也可以市价委托」）。总闸 False ⇒ schema 不露出 orderType/limitPrice、动作枚举回退 4 值、
    # order_plan 恒 None ⇒ 行为**逐字回退**到本特性合入前的 market-only。默认 True 与既有 ai_trade_enabled /
    # ai_trade_reflection_enabled 的「默认即生效」约定一致（拍板记录见 spec 文末〈实施过程发现的方案缺陷〉）。
    # env 名 AI_TRADE_LIMIT_* 已在 desktop config.js prod 透传白名单内（AI_TRADE_ 前缀），此处须真的声明
    # ——Settings extra="ignore" 会静默丢弃未声明的 env。
    ai_trade_limit_orders_enabled: bool = True    # 限价总闸；False 逐字回退 market-only
    # 本地价带门禁：|limitPx-lastPx|/lastPx 超此值即降级 hold（可重试）。比 OKX 侧 ±5% 限价带
    # （错误码 51006）更紧、先触发、给中文可解释原因、不消耗 API 配额。
    ai_trade_limit_price_max_deviation_pct: float = 0.02
    # 挂单存活上限（秒），到期由 _poll_resting 自动撤销。Agent 唤醒是离散的，一笔永不成交的挂单会长期
    # 占用保证金与会话预算；TTL 是兜底，Agent 的 cancel_order 是主动手段，二者互补。
    ai_trade_limit_order_ttl_s: float = 300.0
    # ---- 思维链研判增强（ai-trade-reasoning-chain §2.8）----
    # 向决策 prompt 注入「价格走势 / 成交量 / 技术因子」三段紧凑证据摘要，配合分步思维链研判纪律：策略/因子
    # 信号只作参考与触发线索、可被 Agent 推翻。默认开（只读输入增强；禁用 / 数据不足 / 异常自动省略该段，
    # 确定性行为零回归）。指标周期（RSI14/EMA9,21/MACD12,26,9/BB20,2/ATR14）与 _MIN_BARS=35 作为模块级常量
    # 写在 market_digest.py（规避「AI_TRADE_ env 三处联动」既知易踩点，与 trace.py 阈值常量同风格）。
    ai_trade_market_digest: bool = True          # 总开关：注入价格走势/成交量/因子证据摘要（False=不注入）
    ai_trade_multi_timeframe_context: bool = True  # 多周期中线背景总开关（只读；缺数时显式降级）
    ai_trade_digest_trend_window: int = Field(default=20, ge=2)  # 价格走势/区间位置回看根数
    ai_trade_digest_vol_window: int = Field(default=20, ge=2)    # 成交量均值/zscore 回看根数
    # ---- Agent 墙钟定时唤醒（ai-auto-trading-wake-interval §2.5）----
    # 在既有「确认 K 线 → 决策」触发之外叠加第三个唤醒源：一个独立于 K 线周期的墙钟定时唤醒，默认每 2
    # 分钟让 Agent 用「当前实时价 + 最近信号板 + 持仓」重新研判（两根 K 线之间也能及时止盈损/调仓）。
    # 复用同一决策管线与全部资金/并发护栏，仅多一种「何时唤醒」的来源。env 名 AI_TRADE_WAKE_INTERVAL_*
    # 已在 desktop config.js prod 透传白名单内（AI_TRADE_ 前缀），此处声明后方不被 extra="ignore" 静默丢弃。
    ai_trade_wake_interval_default_min: float = 2.0    # 未显式传值时的默认唤醒周期（分钟）
    ai_trade_wake_interval_floor_s: float = 30.0       # 唤醒周期硬下限（秒），防高频 LLM 烧钱 / 抖动占比过大
    ai_trade_wake_interval_ceil_min: float = 240.0     # 唤醒周期硬上限（分钟，= 4h）
    # 空仓重复唤醒跳过守卫（ai-trade-reflection-hold-inertia-fix F4c）：空仓+无挂单+无新确认根且价格
    # 相对上一决策轮变动 < 此比例（fraction，0.0005=0.05%）时跳过定时唤醒——同一份行情数据反复决策只会
    # 产出重复 hold 流水、强化观望惯性。0=完全关闭守卫（逐字回退现行为）；持仓/挂单轮永不跳过。
    # env 名 AI_TRADE_WAKE_SKIP_UNCHANGED_PCT 已被 desktop config.js 的 AI_TRADE_ 前缀透传白名单覆盖。
    ai_trade_wake_skip_unchanged_pct: float = Field(default=0.0005, ge=0.0)
    # AI 自动交易连续观望自察阈值（hold-inertia-observability N1）：第一阶段连续真实观望（非降级轮）
    # 达到该轮数后，决策 prompt 注入一条对称措辞的自察注记；0 = 完全关闭（prompt 逐字节回退现行为）。
    # env 名 AI_TRADE_HOLD_STREAK_NOTE_AFTER 已被 desktop config.js 的 AI_TRADE_ 前缀透传白名单覆盖。
    ai_trade_hold_streak_note_after: int = Field(default=10, ge=0)
    # ---- 唤醒感知上下文（ai-trade-wake-aware-context §4.2）----
    # 两条正交的轴：① 战术周期证据——在「中线背景（高于执行周期）」与「执行周期」之外补上缺失的第三层
    # （严格短于执行周期、贴唤醒节奏的 1~2 个周期），让定时唤醒轮拿到**真的不同于**上一轮的行情证据；
    # ② 唤醒原因——把 trigger 的二值扩成七种结构化 kind，同时驱动跳过守卫与 prompt 措辞（判定与文案
    # 同源，杜绝「prompt 说没变、守卫认为变了」）。三个总闸全关 ⇒ payload/prompt/事件逐字节回退。
    # env 名 AI_TRADE_TACTICAL_* / AI_TRADE_WAKE_* / AI_TRADE_PROGRESSIVE_* 已被 desktop config.js 的
    # AI_TRADE_ 前缀透传白名单覆盖；此处须真的声明——Settings extra="ignore" 会静默丢弃未声明的 env。
    ai_trade_tactical_context: bool = True       # 战术周期总闸；False ⇒ 整键缺席 + prompt 逐字回退
    ai_trade_tactical_max_bars: int = Field(default=2, ge=0, le=3)   # 注入周期数；0 等价关闭
    # 每个战术周期窗口根数（≥ market_digest._MIN_BARS=35 且 ≥ classify_regime 的 60）。
    ai_trade_tactical_window_bars: int = Field(default=120, ge=40, le=300)
    ai_trade_tactical_min_refresh_s: float = Field(default=20.0, gt=0, le=600)  # REST 配额护栏，防 429
    # 单次战术取数硬超时：本取数跑在 5 秒盯市 tick 上，而同一个 tick 还守着利润保护棘轮与会话止损
    # 这些**真实资金**路径；交易所慢响应最坏可达 ~60s/周期，不封顶会把盯市整体拖停。
    ai_trade_tactical_fetch_timeout_s: float = Field(default=8.0, gt=0, le=60)
    ai_trade_tactical_max_chars: int = Field(default=700, ge=0, le=4000)  # 战术段字符上限；0=不截断
    ai_trade_wake_reason_enabled: bool = True    # 唤醒原因总闸（payload / prompt / 事件 / 归因）
    # 价格异动判据（×执行周期 ATR，**经 horizon_scale 的 √t 律折算**到唤醒窗口的时间尺度）；
    # 0 ⇒ 逐字回退纯 pct 判据（ai_trade_wake_skip_unchanged_pct）。折算系数刻意**不做成配置项**：
    # 它是量纲折算而非调参旋钮，开放出去等于允许用户重新制造「1D 执行周期下永久跳过」的失配。
    ai_trade_wake_move_atr_mult: float = Field(default=0.5, ge=0, le=10)
    # 连续跳过上限，到即强制放行一轮（饿死兜底）；0 = 不封顶（回退纯守卫行为）。ATR 判据把跳过率
    # 推高后没有这条上界，静止行情下 AI 会变成事实上的停机，而用户看不到任何原因。
    ai_trade_wake_max_skip_streak: int = Field(default=5, ge=0, le=100)
    ai_trade_progressive_disclosure: bool = True  # 重复轮压缩总闸；False ⇒ 执行披露恒 full
    # ---- 唤醒调度闭环（ai-trade-wake-scheduler-closed-loop §4.2）----
    # 四条正交的轴：A 补跑（被在飞轮吞掉的唤醒记待办、轮末立刻补跑一轮）、B 持仓轮跳过（确定性退出层
    # 证明在岗时才跳）、C 信号驱动唤醒（未确认根上无副作用预演出「投影净共识翻转」再唤醒）、D 唤醒来源
    # 胜率归因（纯前端投影，无配置）。全部总闸关闭 ⇒ payload / prompt / 事件 / DB 逐字节回退 a7ed70c。
    # env 名 AI_TRADE_WAKE_* / AI_TRADE_SIGNAL_WAKE_* 已被 desktop config.js 的 AI_TRADE_ 前缀透传白名单
    # 覆盖；此处须真的声明——Settings extra="ignore" 会静默丢弃未声明的 env。
    ai_trade_wake_backlog_enabled: bool = True   # 轴 A 总闸；False ⇒ 只计数不补跑（观测保留，行为回退）
    # 连续补跑上限。**刻意无「0=不封顶」**：无上限的补跑链 = 无上限的 LLM 花费，「不补跑」由上一行
    # 的总闸表达，不需要第二条通往无限花费的路。
    ai_trade_wake_backlog_max_chain: int = Field(default=3, ge=1, le=20)
    ai_trade_wake_skip_position_watch: bool = True   # 轴 B 总闸；False ⇒ 持仓轮逐字回退「永不跳过」
    # 持仓连续跳过上限（比空仓的 5 更紧：持仓时「什么都不做」的机会成本更高）。同样**无「0=不封顶」**
    # ——持仓期无限期不研判能亏真钱，那不是一个该留给配置的形态。
    ai_trade_wake_position_watch_max_skip_streak: int = Field(default=3, ge=1, le=100)
    # 轴 C 总闸。**首发默认关**：本轴是唯一会占用守着真实资金的 5 秒盯市 tick 与线程池的一条，且镜像
    # signal-prior 那轮「新轴默认全关、验收达标再翻」的既有做法。晋级需一次真实会话 ≥24h 的人工观察窗。
    ai_trade_signal_wake_enabled: bool = False
    ai_trade_signal_wake_hold_s: float = Field(default=45.0, ge=0, le=600)  # 翻转须连续保持的时长；0=只看样本数
    # 翻转须被**有效**观测到的次数（行情确实变过才算一次）。下限取 2 而非 1：`hold_s=0 + min_samples=1`
    # 是「零防抖」，正是本轴最怕的 repaint 放大器。
    ai_trade_signal_wake_min_samples: int = Field(default=3, ge=2, le=20)
    ai_trade_signal_wake_min_gap_s: float = Field(default=120.0, ge=30, le=3600)  # 两次信号唤醒最小间隔（花费上界）
    # 预演节流（≈每 3 个盯市 tick 一次）；首次到期按 session_id 稳定哈希在 [0, interval) 内错峰，
    # 避免分组 N 个成员步调一致地同时向线程池投 CPU 密集预演。
    ai_trade_signal_wake_preview_interval_s: float = Field(default=15.0, ge=5, le=600)
    # 单次预演硬超时；超时即退避（间隔翻倍，上限 300s），且**不解锁在飞守卫**（线程本身不可取消）。
    ai_trade_signal_wake_timeout_s: float = Field(default=3.0, gt=0, le=30)
    # 判定价新鲜度闸：`now - _last_px_wall` 超过即不预演。**必须读 `_last_px_wall` 而非 `_last_candle_wall`**
    # ——后者只在确认根更新，12H/1D 执行周期下几十小时不跳，会让防抖器在冻结数据上凑出「已保持 45 秒」。
    ai_trade_signal_wake_stale_s: float = Field(default=90.0, gt=0, le=3600)
    # ---- 【基础策略】铁律层（ai-trade-base-strategy-ma-band §5.3）----
    # 与常规/组合/因子策略并列的第四种「策略类型」，但**不是**策略库里的行：它是信号板之外的硬约束层
    # （宪章 prompt 软引导 + base_rules.py 确定性门禁硬保证），默认开启、可在启动弹窗关闭、优先级最高。
    # 预置规则 ma_band_entry：开仓的**参考成交价**（限价判委托价）必须落在执行周期 MA 通道的上轨/中轨/
    # 下轨任一锚区内，否则本轮开仓降级为 hold。总闸 False ⇒ 规则一律不装配、prompt 不注入、门禁不调用、
    # 轨迹不发 ⇒ **逐字回退**到本特性合入前。env 名 AI_TRADE_BASE_RULE* 已在 desktop config.js prod 透传
    # 白名单内（AI_TRADE_ 前缀），此处须真的声明——Settings extra="ignore" 会静默丢弃未声明的 env。
    ai_trade_base_rules_enabled: bool = True     # 基础策略总闸；False 逐字回退
    ai_trade_base_rule_ma_period: int = Field(default=20, ge=2, le=200)   # 中轨 SMA 周期
    ai_trade_base_rule_ma_std_mult: float = Field(default=2.0, gt=0, le=5)  # 上下轨标准差倍数
    # %B 锚区容差（「附近」的量纲自适应判据）；0.25 时三锚区首尾相接 ⇒ 退化为「仅限带内」，故锁死上界。
    ai_trade_base_rule_band_tolerance: float = Field(default=0.15, gt=0, le=0.25)
    # 方向性模式（做多不追顶 / 做空不杀底）默认关：开启会与突破/趋势跟随类策略正面冲突。
    ai_trade_base_rule_directional: bool = False
    # ---- 利润保护层（ai-trade-profit-protection §4.3）----
    # 与【基础策略】铁律层同构的第二条铁律，但极性相反（fail-open）：持仓浮盈达阈值后记录峰值，回吐
    # 超比例或跌回保本线即由系统**直接市价平仓**，判定只用实时价（不等 K 线收盘）。总闸 False ⇒ 规则
    # 一律不装配、prompt 不注入、盯市不判定 ⇒ **逐字回退**到本特性合入前。全部阈值为**保证金收益率
    # 口径**（诊断根因 ②：5× 杠杆下标的 10% ≈ 保证金 +50%，用标的口径根本触发不到）。env 名
    # AI_TRADE_PROFIT_GUARD_* 已在 desktop config.js prod 透传白名单内（AI_TRADE_ 前缀），此处须真的
    # 声明——Settings extra="ignore" 会静默丢弃未声明的 env。
    ai_trade_profit_guard_enabled: bool = True   # 利润保护总闸；False 逐字回退
    ai_trade_profit_guard_arm_return_pct: float = Field(default=0.05, gt=0, le=5)
    ai_trade_profit_guard_giveback_pct: float = Field(default=0.40, gt=0, le=1)
    # 0 = 关闭保本线（保本专治「一路回吐直到翻负才平」的 DOGE R91 情形）。
    ai_trade_profit_guard_breakeven_arm_return_pct: float = Field(default=0.03, ge=0, le=5)
    # 保本线相对入场的正向偏移（覆盖双边手续费——本地派生浮盈不含费用，比交易所 upl 略乐观）。
    ai_trade_profit_guard_breakeven_offset_return_pct: float = Field(default=0.002, ge=0, le=1)
    ai_trade_profit_guard_min_hold_s: float = Field(default=30.0, ge=0, le=3600)
    # 0 = 关闭硬止盈（请求体的 null 在 resolve_profit_guard_rule 归一为此值）。
    ai_trade_profit_guard_hard_tp_return_pct: float = Field(default=0.0, ge=0, le=20)
    # 判定价最大允许年龄（秒）：超龄或行情降级 ⇒ 快照 status="unavailable" 且**零动作**（fail-open）。
    # 90s = 18 个盯市周期（live_auto_poll_interval_s=5）无任何实时价推送，足以判定断流；远小于
    # _check_staleness 的既有阈值，使保护层在会话级降级反应之前就先行停手。
    ai_trade_profit_guard_max_px_age_s: float = Field(default=90.0, gt=0, le=3600)
    # 盈利持仓自察注记阈值（连续几轮浮盈持有后要求模型给出退出线）；0 = 关闭（prompt 逐字节回退）。
    ai_trade_profit_guard_note_after_rounds: int = Field(default=1, ge=0)
    # ---- 自适应利润保护（ai-trade-profit-protection-adaptive §4.3）----
    # 一刀切的回吐比例在两端都不对：浮盈很小时 40% 回吐折合标的价格不到 0.5%（低于分钟级噪音，会在趋势
    # 还在时把仓位扫掉）；浮盈很大时又意味着眼看着 20 个点利润溜走。故回吐比例走**利润阶梯**、回吐
    # 距离再经 **ATR 上下界**归一。全部字段的「关闭值」都让本层逐字回退到第 1 轮行为。
    # 阶梯串格式 `阈值:回吐` 逗号分隔；空串 = 关闭阶梯（退回固定 giveback_pct）。解析与校验的唯一真源是
    # `profit_guard.parse_giveback_tiers`（阈值严格递增 + 回吐单调不增，违反即启动失败 / 422）。
    ai_trade_profit_guard_giveback_tiers: str = "0.10:0.35,0.25:0.28,0.50:0.20"
    # ATR 周期：与 market_digest._ATR_PERIOD 同值但**各自独立**（后者是展示读数，共享配置会让一改两处
    # 语义漂移）。
    ai_trade_profit_guard_atr_period: int = Field(default=14, ge=2, le=200)
    ai_trade_profit_guard_atr_floor_mult: float = Field(default=0.8, ge=0, le=10)   # 0 = 关下界
    ai_trade_profit_guard_atr_cap_mult: float = Field(default=3.0, ge=0, le=50)     # 0 = 关上界
    # 保护性平仓后的冷却秒数（0 = 关闭）：防「保护层刚锁住利润 → 同根内反手同向重开 → 二次止损」。
    ai_trade_profit_guard_cooldown_s: float = Field(default=180.0, ge=0, le=86400)
    ai_trade_profit_guard_cooldown_scope: str = "same_side"      # same_side | both
    # AI 回测侧总闸：false ⇒ 回测恒不装配保护层（逐字回到第 1 轮的回测行为，含 payload 无键、prompt 回退）。
    ai_trade_profit_guard_backtest_enabled: bool = True
    # ---- 承诺退出线棘轮（ai-trade-profit-protection-commitment C1/C3）----
    # 承诺退出线总闸：把模型每轮被索要、却从不被系统采集/执行的退出线做成结构化、单调棘轮、确定性兑现
    # 的第三条保护线。False ⇒ 决策工具字段 / 宪章段 / 判定逐字节回退 v2（G-1/G-2）。
    ai_trade_profit_guard_commitment_enabled: bool = True
    # ---- 交易周期模式 · 持仓周期铁律层（ai-trade-holding-horizon-modes §5.1）----
    # 与【基础策略】铁律层、利润保护层并列的第三条铁律，极性同利润保护（fail-open）：它只约束
    # **Agent 主动发起的**平仓与反手，止损 / 交易所 OCO / 利润保护层 / 会话止盈损 / 强制时间平仓
    # 一律不受影响。总闸 False ⇒ 恒不装配、prompt 不注入、门禁直通 ⇒ **逐字回退**到本特性合入前。
    # env 名 AI_TRADE_HORIZON_* 已在 desktop config.js prod 透传白名单内（AI_TRADE_ 前缀），此处
    # 须真的声明——Settings extra="ignore" 会静默丢弃未声明的 env。
    ai_trade_horizon_enabled: bool = True
    # 字段缺省时装配的模式（**Literal 而非 str**：写错一个字母就该在进程启动时炸，而不是在第一次
    # start 时 KeyError）。
    ai_trade_horizon_default_profile: Literal[
        "hourly_swing", "intraday_swing", "short_swing", "trend_hold", "custom",
    ] = "intraday_swing"
    ai_trade_horizon_backtest_enabled: bool = True    # 回测侧运维总闸（一键回到不装配的回测行为）
    ai_trade_horizon_gate_reverse: bool = True        # 是否把「反手」也纳入约束（退出口）
    # 解除阈的派生系数：有单仓 OCO 止损时 `阈 = stopLossPct × leverage × frac`（保证金收益率口径）。
    ai_trade_horizon_loss_release_frac: float = Field(default=0.5, ge=0, le=1)
    # 无 OCO 止损时的兜底解除阈（保证金收益率）。
    ai_trade_horizon_loss_release_return_pct: float = Field(default=0.10, gt=0, le=5)
    # 连续被本层拦截 N 轮后注入一段自察注记（0=关闭 ⇒ 整键缺席 ⇒ prompt 逐字节回退）。消费方见
    # §2.9(e) 的 `blockedStreakNote`——本仓库不留没有展示落点的常量。
    ai_trade_horizon_note_after_rounds: int = Field(default=3, ge=0)
    # 判定价年龄阈**刻意不新增字段**，复用 `ai_trade_profit_guard_max_px_age_s`（90s）——
    # 「判定价多老算老」在一个会话里只能有一个答案。
    # ---- 交易周期模式 · 开仓侧换手纪律（ai-trade-turnover-budget-and-backstop §5.1）----
    # 第 1 轮的三道闸只约束「平」，留下一条完整的合法绕行路径：开仓 → 持够最短窗口 → 平仓 →
    # 下一轮立刻同向重开。本段补上「不许过快重开」的另一半（G4 再入场冷却 + G5 换手预算）。
    # 总闸关 ⇒ 两道闸整层不装配、`horizon.turnover` 整键缺席、prompt 逐字节回退。
    ai_trade_horizon_turnover_enabled: bool = True
    ai_trade_horizon_turnover_backtest_enabled: bool = True   # 回测侧独立总闸（沿用既有先例）
    # G4：再入场冷却 = frac × min_hold_s。0 ⇒ 关闭本闸（不是「无限长」）。
    ai_trade_horizon_reentry_frac: float = Field(default=0.5, ge=0, le=4)
    ai_trade_horizon_reentry_scope: Literal["same_side", "both"] = "same_side"
    # G5：令牌桶。速率**不在此处配置**——它恒等于验收卡第 3 行的阈值（§2.2.1 / D-1），另开一个
    # 旋钮就等于允许「弹窗承诺 6 次、运行期执行 12 次」这种自相矛盾。
    ai_trade_horizon_turnover_burst_frac: float = Field(default=0.5, gt=0, le=1)
    # 容量下限 2 是刻意的安全阀（D-2）：`trend_hold` 速率是 1 次/日，容量若也取 1，则一次早期止损
    # 之后会话要整整 22 小时开不出下一单——那正是 `hold-inertia` 那一轮的「永不开单」形态。
    ai_trade_horizon_turnover_min_capacity: float = Field(default=2.0, ge=1, le=100)
    # ---- 离场优先级与离场意图兑现（ai-trade-exit-priority-and-commitment §5.1）----
    # 与持仓周期三闸并列的第四条纪律，极性与它相同（fail-open）但方向相反：三闸约束「什么时候
    # 不许平」，本层约束「什么时候必须让它平」。总闸 False ⇒ 恒不装配、决策工具 schema 恒等于
    # DECISION_TOOL_SCHEMA、prompt 三份逐字节回退、note_intent 回退到「一轮即清零」。
    # env 名 AI_TRADE_EXIT_* 已在 desktop config.js prod 透传白名单内（AI_TRADE_ 前缀）。
    ai_trade_exit_priority_enabled: bool = True                                    # C-1
    ai_trade_exit_priority_backtest_enabled: bool = True                           # C-1b
    # 0 ⇒ 逐字回退今天的「一轮非离场即清零」（F-1 的回滚开关）。**默认值分层是刻意的**：settings
    # 默认 2、`HorizonRule.exit_intent_decay_rounds` 默认 **0**（既有用例直接构造 HorizonRule 且
    # 断言今天的行为，靠的正是后者）——**不要顺手把 HorizonRule 的默认也改成 2**。
    ai_trade_exit_intent_decay_rounds: int = Field(default=2, ge=0, le=10)         # C-2
    # exit_pending 豁免 = mult × confirm_window_s；0 ⇒ 关闭本豁免。
    ai_trade_exit_pending_release_mult: float = Field(default=2.0, ge=0, le=10)    # C-3
    ai_trade_exit_override_rounds: int = Field(default=2, ge=1, le=10)             # C-4
    # G1 的不可解除内核：override 也不解这一段（挡「开仓当轮立刻 final 平仓」的病态形态）。
    ai_trade_exit_override_min_hold_s: float = Field(default=60.0, ge=0, le=3600)  # C-5
    # 会话级配额；0 ⇒ 关闭 override 档（final 一律按 high 处理，如实写进 prompt）。
    # `override` 与 `adjudicated` **共用**这一份配额——裁决不是配额外的第二条通道，否则
    # 「配额用尽 ⇒ 走裁决 ⇒ 配额继续涨」会让本项变成一个只在文档里存在的数字（X-4b）。
    ai_trade_exit_override_max_per_session: int = Field(default=6, ge=0, le=100)   # C-6
    ai_trade_exit_evidence_min_chars: int = Field(default=10, ge=0, le=200)        # C-7
    # X-6：close 的反思 fail-closed 反转为 fail-open。False ⇒ 逐字回退今天（F-3 的回滚开关）。
    ai_trade_exit_reflection_fail_open: bool = True                                # C-8
    ai_trade_exit_commitment_enabled: bool = True                                  # C-9
    ai_trade_exit_commitment_retry_interval_s: float = Field(default=10.0, ge=1, le=300)   # C-10
    ai_trade_exit_commitment_max_attempts: int = Field(default=6, ge=1, le=50)     # C-11
    ai_trade_exit_commitment_max_age_s: float = Field(default=300.0, ge=10, le=3600)       # C-12
    # 【立即离场】运维总闸（人工授权的单次市价离场端点）；关 ⇒ 端点 409（`AI_TRADE_MANUAL_EXIT_
    # DISABLED`），面板按钮**照常渲染**、点下去如实回显那句中文——本值不进任何 wire，前端无从
    # 预知它。这是刻意的取舍：为一个运维态开关多开一条下发通道，比让用户点一次看见原因更贵。
    ai_trade_manual_exit_enabled: bool = True                                      # C-13
    # ---- 行情断连自愈（ai-trade-feed-self-healing 轴 B / 轴 C）----
    # 今天的 `_check_staleness` 只置标志 + 推文案，而 `_stale_degraded` 是一个**吸收态**：三条唤醒源
    # 全被它挡住，而唯一能清它的是那条已经死掉的 WS 自己。本组开关把这条链路从「观测」升级为「自愈」。
    # 总闸关 ⇒ 四处逐字回落：plan() 只回 clear/degrade/none 三态、`feedRecovery` 整键缺席、
    # /relink 端点 409、前端因键缺席而不渲染恢复卡。**四处回落缺一，这个开关就只是一句声明。**
    ai_trade_feed_selfheal_enabled: bool = True                                    # 整轴总闸
    ai_trade_feed_backfill_interval_s: float = Field(default=60.0, gt=0)           # REST 活性补取冷却
    # × stale_after（= bar × live_auto_staleness_factor(3)）才换链 ⇒ 会话级换链门槛 = bar × 6，
    # 1H 周期下是 6 小时。这不是笔误：判定表第 2 行（not ws_alive / link_state == "down"）才是常态下
    # 的换链触发点，本门槛只是「WS 任务活着、登记簿也没判 down、但根就是不来」的最后一层兜底。
    ai_trade_feed_relink_factor: float = Field(default=2.0, ge=1.0)
    ai_trade_feed_relink_min_interval_s: float = Field(default=120.0, gt=0)        # 换链冷却
    ai_trade_feed_escalate_after_relinks: int = Field(default=3, ge=0)             # 0 = 永不升级
    # 人工 /relink 的**服务端硬死线**：前端对本路径落 FAST_MS = 20_000（httpTimeout.ts 的长跑白名单
    # 不含它），而本端点最该被点击的场景恰恰是「网络仍不通」——此时 fetch_missing 会走完整的 REST
    # 重试 / 退避，很容易越过 20 秒，用户就会在**唯一一个兜底按钮**上读到「本次操作结果未知」。
    ai_trade_feed_relink_deadline_s: float = Field(default=12.0, gt=0)
    # 盯市判定价的 REST 回落阈值（轴 C）：判据用 `_last_px_wall` 而**不是** `_last_candle_wall`
    # ——后者在 12H/1D 周期下几十小时不跳，用它会让回落在正常运行时也恒生效、每 5 秒打一次 REST。
    # 0 = 关（逐字回退到本轮之前：窗口非空即用最后一根收盘价）。
    ai_trade_mark_px_fallback_after_s: float = Field(default=180.0, ge=0)
    # ---- 交易所侧兜底真值化（§2.3）----
    # 「交易所侧还有兜底」是一句从未被核实过的话（真源是本地成交回执，从不查交易所），而它恰是
    # 跨日模式「不必每轮盯盘」的唯一授权来源。总闸关 ⇒ 一个请求都不发、兜底文案逐字回退到本地
    # 回执口径（并如实说明「本会话尚无核实通道」）。**本轮明确不做自动补挂 / 重挂**（§2.3.5）：
    # 那需要一条全新的下单写路径，与本仓库全部 AI 侧安全层「绝不新增下单路径」的纪律冲突。
    ai_trade_backstop_check_enabled: bool = True
    ai_trade_backstop_poll_ttl_s: float = Field(default=120.0, ge=30, le=3600)
    ai_trade_backstop_timeout_s: float = Field(default=6.0, gt=0, le=30)
    # 核实结果的最大可信年龄；超过即从 `exchange` 降级为 `unavailable(stale)`（绝不拿旧读数背书）。
    ai_trade_backstop_max_age_s: float = Field(default=300.0, ge=60, le=86400)
    ai_trade_backstop_min_coverage: float = Field(default=0.999, gt=0, le=1)
    ai_trade_backstop_gate_scale_in: bool = True      # G6；关 ⇒ 只报不拦
    # ---- 交易所侧兜底自愈（ai-trade-backstop-repair-and-net-backtest §2.2）----
    # 上一轮把「交易所侧到底挂着什么」做成了可核实的事实，但检测到「没有兜底」之后系统什么都不做：
    # 一个 trend_hold 会话会带着一个裸露的仓位继续跑 1–3 天。本段补上执行体。
    # 总闸关 ⇒ 整层不装配、`horizon.backstop.repair` 整键缺席、prompt 逐字回退到上一轮的五句、
    # 人工端点 404、一个写请求都不发。
    ai_trade_backstop_repair_enabled: bool = True
    # 缺省模式（启动请求未显式指定时回填）。**默认 plan**：本轮是本仓库第一条 AI 侧自动写路径，
    # 默认给方案 + 一键执行，而不是默认自动花钱（§九 D-1）。
    ai_trade_backstop_repair_default_mode: Literal["plan", "arm"] = "plan"
    # 触发价与现价之间要求的最小距离（bps）。补一张会立刻触发的止损 = 立刻市价平仓，
    # 那是把安全层变成亏钱机器的最短路径（H1-e）。
    ai_trade_backstop_repair_min_gap_bps: float = Field(default=15.0, ge=0, le=1000)
    # 单持仓周期内最多补几次；**0 ⇒ 本层整体关闭（自动与人工都不补）**，不是「无限次」。
    ai_trade_backstop_repair_max_per_epoch: int = Field(default=2, ge=0, le=10)
    ai_trade_backstop_repair_cooldown_s: float = Field(default=60.0, ge=0, le=3600)
    ai_trade_backstop_repair_max_per_session: int = Field(default=8, ge=0, le=100)
    # 覆盖率阈值**不新开旋钮**：恒复用 `ai_trade_backstop_min_coverage`（G6 的同一个数）。
    # 判定价年龄阈同理复用 `ai_trade_profit_guard_max_px_age_s`（默认 90s，本层是它的第三个复用方）
    # ——用一个过期的价去论证「现价还没越过止损」，正是「补一张会立刻触发的止损」最现实的发生路径。
    # ---- 回测净额真值（§2.3）----
    # 回测的**记账**自上一轮起已含真实资金费，但**成本估算**两个入口仍硬传 funding_enabled=False，
    # 于是 G3 与 edge 门禁在回测里按「资金费为 0」判定，模式越长越乐观。
    # 关 ⇒ 两处入参逐字节回退到本轮之前的 `(None, False)`，回测结果逐字节相同。
    ai_trade_funding_cost_backtest_enabled: bool = True
    # ---- AI 自动交易 · 持仓周期验收闭环（ai-trade-horizon-acceptance-loop §5.1）----
    # 轴 A：资金费真值化。总闸关 ⇒ 账本恒空转、`aiStats.funding` 整键缺席、验收卡第 5 行判
    # insufficient。本开关**必须**并进 `CostRuntime` 里 `FundingCache` 的 enabled 析取式
    # （cost_runtime.py，[P1-7]），否则本层开着、资金费率快照却被 `ai_trade_cost_awareness_enabled`
    # 连坐关死，卡片只会说「口径不可得」而说不出是被谁关的。
    ai_trade_funding_ledger_enabled: bool = True
    # 结算周期合理性区间：交易所读数落在区间外即视为坏读数 ⇒ 本次**不计提**（不猜 8h，调查约束 #7）。
    # 与 `cost_model` 的处置**有意不对称**（§九 D-9）：成本估算宁可按 8h 保守高估，记账宁可不记——
    # 两层的失败方向不同，绝不把一个猜出来的数当真值上报。
    ai_trade_funding_min_interval_s: float = Field(default=1800.0, gt=0, le=86400)
    ai_trade_funding_max_interval_s: float = Field(default=28800.0, gt=0, le=86400)
    ai_trade_funding_backtest_enabled: bool = True    # 回测侧历史费率曲线运维总闸
    ai_trade_funding_history_max_pages: int = Field(default=8, ge=1, le=50)
    # 轴 B：退出来源归因。关 ⇒ `explain.meta.exitSource` 整键缺席（老数据同样缺席，查询侧按 unknown）。
    ai_trade_exit_attribution_enabled: bool = True
    # 轴 C：周期验收卡（五行判据 + 退出来源分布）。关 ⇒ acceptance 整键为 null。
    ai_trade_acceptance_enabled: bool = True
    ai_trade_acceptance_min_round_trips: int = Field(default=20, ge=1, le=1000)
    ai_trade_acceptance_min_within_target_rate: float = Field(default=0.6, ge=0, le=1)
    ai_trade_acceptance_max_fee_to_gross: float = Field(default=0.35, gt=0, le=10)
    # ---- 成本记账与最小 edge 门禁（ai-trade-cost-accounting-and-edge-gate §3.9）----
    # 本段全部字段的**作用域是 AI 会话**：`FeeLedger` 只由 `ai_auto/manager.py` 经 `LiveExecutor` 的
    # `fee_ledger=` 构造缝注入，`live_auto`（非 AI）恒传 None ⇒ 这些开关取任何值都不影响非 AI 链路
    # （§3.2.0，Z-7 钉死）。env 名 AI_TRADE_* 已在 desktop config.js prod 透传白名单内（AI_TRADE_ 前缀），
    # 此处须真的声明——Settings extra="ignore" 会静默丢弃未声明的 env。
    # A0-1 记录层：每笔成交按确定性费率模型入账（notional × feeBps）。False ⇒ trade["fee"] 逐字回退 0.0。
    ai_trade_fee_accounting_enabled: bool = True
    # A0-2 口径切换：_close_pnl 扣双边费用 ⇒ session_pnl_usd 变净额。**破坏性口径变更**。
    # ai-trade-cost-truth-and-net-pnl §3.2 B-1 起默认 **True**：本项就是用户报告的缺陷本身（看板盈亏
    # 高于真实到手金额一个往返成本），默认关等于交付一个默认不生效的修复。置 false 即逐字回退。
    ai_trade_net_pnl_enabled: bool = True
    # 费率常量刻意**不复用** default_taker_fee_bps（本文件下方「撮合默认参数」段）：后者同时服务
    # SimBroker / 纸面 / 影子 broker，改它等于静默重写每一次历史回测的成本假设。两个世界的费率必须能各自演进。
    ai_trade_maker_fee_bps: float = Field(default=2.0, ge=0, le=100)
    ai_trade_taker_fee_bps: float = Field(default=5.0, ge=0, le=100)
    ai_trade_half_spread_fallback_bps: float = Field(default=1.0, ge=0, le=100)  # 无 books5 时的保守假设
    ai_trade_impact_bps: float = Field(default=0.5, ge=0, le=100)
    # 成本模型的预期持仓根数（资金费率结算周期数由它推）。
    ai_trade_cost_expected_hold_bars: int = Field(default=8, ge=1, le=1000)
    # 最小 edge 门禁：判据 毛edge(bps) ≥ k_min × C(bps)。**0 = 门禁空转（默认，逐字回退）**。
    # 启用 runbook 见 spec §7.5：先满 50 笔费率入账 → 开 A0-2 → 观察 100 笔 → 再置 2.0。
    ai_trade_edge_gate_k_min: float = Field(default=0.0, ge=0, le=10)
    ai_trade_edge_gate_degraded_mult: float = Field(default=1.2, ge=1.0, le=3.0)  # 成本 degraded 时的收紧倍数
    ai_trade_edge_atr_target_mult: float = Field(default=1.0, gt=0, le=10)        # 粗估毛 edge 的 ATR 倍数
    # 资金费率：总闸关 ⇒ fundingCarryBps=0.0 且 confidence 保持 "ok"（有意选择 ≠ 数据缺失）。
    ai_trade_funding_enabled: bool = True
    ai_trade_funding_poll_ttl_s: float = Field(default=60.0, gt=0, le=3600)
    # 实测成交明细旁路审计（校准 feeBps / 观测滑点）；绝不进决策轮，绝不回写盈亏。
    ai_trade_fill_audit_enabled: bool = True
    ai_trade_fill_audit_interval_s: float = Field(default=60.0, gt=0, le=3600)
    # ---- 成本真值化与净额盈亏（ai-trade-cost-truth-and-net-pnl §5.5）----
    # 轴 A：官方费率真源（`GET /account/trade-fee`，签名私有）。关 ⇒ 一个请求都不发，费率恒取上面
    # 的配置常数且 wire 上 source=="config"。取数失败一律回落常数，**绝不阻断会话启动**。
    ai_trade_fee_rate_source_enabled: bool = True
    ai_trade_fee_rate_ttl_s: float = Field(default=3600.0, gt=0, le=86400)
    # 轴 D：Agent 成本意识（宪章章 + 每轮成本段）。关 ⇒ costAwareness 整键缺席 + 宪章章为 ""
    # ⇒ prompt 渲染逐字节回退。
    ai_trade_cost_awareness_enabled: bool = True
    # 轴 C：交易所对账（`GET /account/positions-history`，签名私有）。本层**只观测、不回写盈亏**。
    ai_trade_reconcile_enabled: bool = True
    ai_trade_reconcile_interval_s: float = Field(default=300.0, gt=0, le=86400)
    ai_trade_reconcile_abs_tol_usd: float = Field(default=0.5, ge=0)
    ai_trade_reconcile_rel_tol: float = Field(default=0.05, ge=0, le=1)
    ai_trade_reconcile_alert_streak: int = Field(default=3, ge=1, le=100)
    # stop 收尾强制对账的硬超时（P1-5）：观测层绝不延长真实资金平仓后的落库收尾。
    ai_trade_reconcile_stop_timeout_s: float = Field(default=3.0, gt=0, le=30)
    # 老 run（config_json 缺 costBasis）的口径**推定**（P0-3）。默认 "gross" 对出厂配置正确；
    # 已按阶段一 §7.5 runbook 提前打开 ai_trade_net_pnl_enabled 的部署须置 "net"，否则那批
    # 已是净额的历史行会被聚合层二次扣费。回执恒带 costBasisAssumed=true，UI 分开显示「推定 / 已知」。
    ai_trade_history_legacy_cost_basis: str = "gross"        # gross | net
    # ---- 信号先验体系与 EV 分子（ai-trade-signal-prior-and-ev-gate §七）----
    # 三个**默认关闭**的独立总闸：全关时系统逐字节回退到阶段一行为（KV 里遗留的快照行不被读取，
    # 也无任何副作用）。env 名同为 AI_TRADE_ 前缀，desktop config.js 的 prod 透传白名单已覆盖。
    ai_trade_signal_prior_enabled: bool = False        # 总闸①：加载快照、payload/prompt 露出先验
    ai_trade_weighted_consensus_enabled: bool = False  # 总闸②：加权共识（等权口径与门禁逐字不变）
    # 总闸③：先验进 edge 分子。与①分开是为了把「展示」与「门禁」拆成两个可分别归因的观察窗口
    # ——③关闭时 priorEvBps / priorWouldVeto 反事实读数**照发**（P1-2），L3 才有据可依地定 k_min。
    ai_trade_edge_prior_enabled: bool = False
    # 先验持有期**不新增字段**：恒取 ai_trade_cost_expected_hold_bars（§3.2 推论 1 单一真源）；
    # 两者不等即整键缺席（C-3），改了它必须立刻重建快照。
    ai_trade_prior_min_samples: int = Field(default=30, ge=1)      # 单条先验的事件数下限
    # 独立事件段数下限，**仅作用于 strategy_replay 条目**：立场根高度重叠，一段 200 根的单边趋势
    # 能单独满足 min_samples，不设它显著性判据形同虚设。0 = 关闭（不建议）。
    ai_trade_prior_min_runs: int = Field(default=10, ge=0)
    ai_trade_prior_min_segments: int = Field(default=6, ge=2)      # 与 MIN_IC_SEGMENTS 同源
    ai_trade_prior_decay_half_life_days: float = Field(default=30.0, gt=0)  # 置极大 = 关衰减
    ai_trade_prior_embargo_bars: int = Field(default=12, ge=0)     # 0 = 关跨段隔离
    ai_trade_prior_ir_clip: float = Field(default=2.0, ge=0)       # 0 ⇒ 权重恒 0 ⇒ 先验恒缺席
    ai_trade_prior_top_n: int = Field(default=5, ge=0)             # prompt 证据明细条数上限
    ai_trade_prior_max_age_s: float = Field(default=604800.0, gt=0)  # 7 天陈旧度门禁
    ai_trade_prior_lookback_bars: int = Field(default=4000, ge=100)   # rebuild 缺省回看根数
    ai_trade_prior_max_bars: int = Field(default=8000, ge=100)        # rebuild 取数前硬上限（413）
    # ---- 波动率目标化仓位 + ATR 止损 + 相关性预算 + 回撤刹车（ai-trade-vol-sizing-and-drawdown-brake §4.4）----
    # 四个**相互独立、默认全关**的总闸：全关时系统逐字节回退到阶段二第一步（commit c6db95d）的行为。
    # env 名同为 AI_TRADE_ 前缀，desktop config.js 的 prod 透传白名单已覆盖；此处须真的声明——
    # Settings extra="ignore" 会静默丢弃未声明的 env。
    ai_trade_sizing_enabled: bool = False               # 总闸①：波动率目标化仓位求解
    ai_trade_vol_target_annual: float = Field(default=0.20, ge=0)   # 年化波动率目标（0=该项不参与 min）
    ai_trade_realized_vol_window: int = Field(default=96, ge=2)     # 回看根数（15m ⇒ 一整天）
    ai_trade_kelly_lambda: float = Field(default=0.25, ge=0, le=1)  # 凯利缩放（0=凯利项不参与 min）
    # 总闸②：ATR 止损倍数。**与总闸①完全独立**（[P1-1]：runbook L1 是「只开 ATR 止损、不开仓位层」，
    # 受①门控则那一级恒不可达）。0 ⇒ 单仓 OCO 逐字回退固定 pct（ai_trade_default_sl/tp_pct）。
    ai_trade_stop_atr_mult: float = Field(default=0.0, ge=0, le=20)
    ai_trade_stop_rr: float = Field(default=2.0, ge=0, le=20)       # 盈亏比（止盈 = rr × 止损）
    # 总闸③：组合有效敞口上限（单标的满仓名义额的倍数）。取值域 {0} ∪ [1,10]，越界即启动失败（422）
    # ——下界 1.0 是「单成员分组恒不受约束」的**结构性**零回归保证（S-8），而不是靠默认值碰巧成立。
    ai_trade_effective_exposure_cap_mult: float = Field(default=0.0, ge=0, le=10)
    ai_trade_correlation_min_samples: int = Field(default=96, ge=2)  # 重叠样本不足 ⇒ ρ=1（最保守）
    # 总闸④：三级刹车，三档各自独立关闭。**阈值必须按杠杆定标**（[P1-4]）：session_pnl 落在已含杠杆的
    # 名义额上而 dd 的分母是保证金口径的 budget，3× 下 8%/15% 等价于标的价反向 2.7%/5.0%。建议生产值
    # 以 3× 为锚点按 `× 3/leverage` 折算（3× ⇒ 0.08/0.15；5× ⇒ 0.048/0.09）。
    ai_trade_brake_slow_dd_pct: float = Field(default=0.0, ge=0, le=1)    # ④a 第一档减速
    ai_trade_brake_slow_size_mult: float = Field(default=0.5, ge=0, le=1)  # 第一档仓位系数（1.0=关）
    ai_trade_brake_halt_dd_pct: float = Field(default=0.0, ge=0, le=1)   # ④b 第二档「只平不开」
    ai_trade_brake_loss_streak: int = Field(default=0, ge=0)             # ④c 连亏熔断笔数（0=关）
    ai_trade_brake_loss_cooldown_bars: float = Field(default=4.0, ge=0)  # 冷却时长（× 一根 K 线周期）
    # ---- 交易纪律预设（ai-trade-discipline-preset-and-integrity §3.1.4）----
    # 上面那一整排 `ai_trade_*` 阈值的**既有默认值本轮一个都没动**（它们仍是「全关」）；改变的只有
    # 一件事：从 API 层启动的会话会默认套上一份预设副本。故行为默认值的改变**集中且只发生在**
    # `ai_trade_discipline_default_preset` 这一处（[P1-11]），env 仍是最终的运维退出口。
    # `ai_trade_discipline_enabled=False` 是本特性**唯一的一键回滚路径**，且**优先级高于请求体**
    # （沿用 `ai_trade_horizon_backtest_enabled` 等既有总闸惯例）：关 ⇒ 整轴不装配 ⇒ 等价 `off`。
    ai_trade_discipline_enabled: bool = True
    ai_trade_discipline_default_preset: Literal["steady", "balanced", "aggressive", "off"] = (
        "balanced")
    # ---- 可交易性预检 / 成本感知止损下界 / 摘要健康度（ai-trade-tradability-and-open-funnel §6.1）----
    # ATR 止损的**成本下界**：止损距离不得落进往返成本带以内（实测 BTC 1m 上 62.3% 的 2×ATR 止损
    # 比一笔市价往返成本还窄 ⇒ 一台结构性手续费机器）。`0` ⇒ 逐字回退合入前行为。
    ai_trade_stop_cost_floor_mult: float = Field(default=2.0, ge=0, le=20)
    # 轴 A 总闸：关 ⇒ 端点仍在但恒回 `level:"unknown"`，前端整段不渲染（探针只提示、绝不阻断）。
    ai_trade_tradability_enabled: bool = True
    ai_trade_tradability_window_bars: int = Field(default=200, ge=60, le=500)
    ai_trade_tradability_infeasible_veto_rate: float = Field(default=0.80, gt=0, le=1)
    ai_trade_tradability_marginal_veto_rate: float = Field(default=0.40, gt=0, le=1)
    # 滚动摘要的**独立**超时：摘要是背景任务，不该占用决策的 60s 预算（D-5）。
    ai_trade_summary_timeout_s: float = Field(default=30.0, gt=0, le=300)
    ai_trade_summary_max_failures: int = Field(default=3, ge=1, le=20)   # 连续失败达该数即 stalled
    ai_trade_summary_journal_cap_mult: int = Field(default=2, ge=1, le=10)  # 失败后保留 mult × 刷新轮数
    # ---- AI 自动交易 · 回测模式（ai-auto-trading-backtest §2.5，三重决策成本护栏）----
    # 回测每决策根调一次 LLM，成本/时长敏感；三重护栏：决策间隔（降频）+ 决策轮数硬上限（越界启动前
    # 拒绝，绝不静默截断）+ 并发闸（限同时消耗 LLM 的回测数）。字段名即环境变量名（AI_BACKTEST_*，
    # 大小写不敏感）；须真的声明（Settings extra="ignore"，未声明的 env 会被静默丢弃）。
    ai_backtest_max_rounds: int = Field(default=400, ge=1)          # 单次回测的决策轮数硬上限（越界 400 拒绝）
    ai_backtest_default_decision_every_bars: int = Field(default=1, ge=1)  # AI 每 K 根决策一次的默认间隔
    ai_backtest_max_jobs: int = Field(default=2, ge=1, le=8)        # 同时进行的 AI 回测作业上限（超出 429）
    # ---- 已实现 edge 校准与盈利体检（ai-trade-realized-edge-calibration §6.1）----
    ai_trade_trip_ledger_enabled: bool = True          # 轴 A 总闸；False ⇒ explain.meta 逐字回退
    ai_trade_profitability_min_trips: int = Field(default=30, ge=5, le=1000)     # 轴 B 下结论下限
    ai_trade_profitability_t_threshold: float = Field(default=2.0, ge=0, le=10)  # 显著性判据
    ai_trade_profitability_bucket_min_trips: int = Field(default=10, ge=2, le=200)  # 轴 C 桶内下限
    ai_trade_track_record_enabled: bool = True         # 轴 D 总闸；False ⇒ payload 整键缺席
    ai_trade_track_record_min_trips: int = Field(default=20, ge=5, le=1000)      # 轴 D 装配下限
    # [P0-4] 成本句的独立门禁：带费率记录的往返占比低于此值 ⇒ prompt **整行省略**成本句。
    # 绝不向模型下发一个由「没记费率」算出来的 0.0 bps 往返成本（D-9）。
    ai_trade_track_record_min_fee_coverage: float = Field(default=0.5, ge=0, le=1)
    ai_trade_bracket_check_enabled: bool = True        # 轴 E-0 总闸；False ⇒ bracket 整键缺席
    ai_trade_bracket_max_atr_mult: float = Field(default=8.0, gt=0, le=100)      # 固定 pct 止损的告警上限
    # ---- 暂停 / 继续（ai-trade-pause-resume §6.4）----
    ai_trade_pause_enabled: bool = True             # 整轴总闸；False ⇒ 写端点 403 + 启动对账逐字回退老行为
    ai_trade_pause_hold_position_default: bool = True    # 用户暂停时「保留持仓」的默认选项
    # 优雅退出 / quit-pause 对**运行中**分组的处置：hold（带仓暂停，默认）| flatten（平仓归档）。
    # 默认 hold：D-4 证明今天那条平仓路径几乎从不执行，而 quit-pause 是能跑通的 HTTP 钩子——
    # 默认 flatten 等于**新增**「每次关软件都平真仓」，用户重开后无卡可续，本轮需求原地失效。
    ai_trade_shutdown_action: str = "hold"
    ai_trade_resume_max_age_s: float = Field(default=604800.0, gt=0)   # 超龄的暂停契约只许归档（7 天）
    ai_trade_resume_size_tolerance: float = Field(default=0.02, ge=0, le=1)   # 持仓张数对账相对容差
    ai_trade_resume_backstop_required: bool = False   # 带仓续跑是否强制要求兜底已覆盖（默认只警示）
    ai_trade_pause_persist_interval_s: float = Field(default=60.0, gt=0)      # 心跳写点节流（W3）
    # ---- 生命周期真值化（ai-trade-lifecycle-truth §七）----
    # 整轴总闸。False ⇒ 一键**逐字**回滚到本轮之前：`group_status()` 回落 `getattr(group,"state",…)`、
    # 不下发 `lifecycle` / `declaredState` / `lifecycleUnknown` / `serverTs` 四个加法键、
    # `SessionLink.on_stream` 判据回落 `state == "running"`、续跑复检不参与、前端因 `lifecycle`
    # 缺席而自动回落到 `group.state`。四处回落缺一，这个开关就只是一句声明（用例 12 是唯一护栏）。
    ai_trade_lifecycle_truth_enabled: bool = True

    # ---- 交易经验上下文图（context-graph §6.5 的 36 项 + compounding §6.2 的 11 项 = 47 项）----
    # 总闸关 ⇒ 全链路空转：payload 无 `experience` 键 ⇒ `build_decision_prompt` 输出**逐字节**
    # 等于本特性合入前（不变式 I-2，用例 G-2 钉死）。
    ai_trade_experience_enabled: bool = True
    ai_trade_experience_default_on: bool = True          # 启动请求缺省语义（需求：默认开启）
    ai_trade_experience_write_enabled: bool = True       # 采集写闸（只读模式：用经验但不沉淀）
    ai_trade_experience_min_samples: int = Field(default=8, ge=1)
    ai_trade_experience_iron_samples: int = Field(default=20, ge=1)
    ai_trade_experience_t_threshold: float = Field(default=2.0, gt=0)
    ai_trade_experience_iron_t_threshold: float = Field(default=2.5, gt=0)
    ai_trade_experience_half_life_days: float = Field(default=30.0, gt=0, le=3650)
    ai_trade_experience_stale_days: float = Field(default=90.0, gt=0, le=3650)
    ai_trade_experience_l0_max: int = Field(default=3, ge=0, le=10)
    # 字符预算**含框架句**（I-6）：L0 页眉 ≈33 字 + 页脚 ≈37 字 + 3×(120+4) = 442 ⇒ 420 装不下
    # 三条满长铁律。默认 480 留 38 字裕量；下界 160 与 `_check_experience_budgets` 的断言共同守。
    ai_trade_experience_l0_max_chars: int = Field(default=480, ge=160, le=4000)
    ai_trade_experience_l1_max: int = Field(default=3, ge=0, le=10)
    ai_trade_experience_l1_max_chars: int = Field(default=660, ge=160, le=4000)
    ai_trade_experience_reflection_max_chars: int = Field(default=240, ge=0, le=2000)
    ai_trade_experience_seed_expand_alpha: float = Field(default=0.5, ge=0, le=1)
    ai_trade_experience_co_edge_min_weight: float = Field(default=0.15, ge=0, le=1)
    ai_trade_experience_co_edge_min_support: int = Field(default=5, ge=1)
    ai_trade_experience_max_expanded_contexts: int = Field(default=24, ge=0, le=200)
    ai_trade_experience_candidate_cap: int = Field(default=200, ge=1, le=5000)
    ai_trade_experience_mmr_lambda: float = Field(default=0.5, ge=0, le=1)
    ai_trade_experience_graph_node_cap: int = Field(default=4000, ge=100, le=100000)
    ai_trade_experience_global_min_symbols: int = Field(default=3, ge=1, le=50)
    ai_trade_experience_distill_enabled: bool = True
    # **只被 manager 侧的后台任务读**，绝不出现在任何同步等待 `stop()` 的路径上（评审 P0-1）：
    # `stop_group` 串行遍历成员，而客户端 `/stop` 超时是 20s（FAST_MS）——一次 45s 的同步蒸馏
    # 会在真实资金的停止按钮上弹出「本次操作结果未知」。用例 G-5 / G-6 把这条钉死。
    ai_trade_experience_distill_timeout_s: float = Field(default=45.0, gt=0, le=300)
    ai_trade_experience_distill_max_lessons: int = Field(default=5, ge=1, le=20)
    # 按 `(instId, bar)` 分组算 `costRegime` 三分位的最小样本；不足即回落全库分位并在 UI 标注。
    ai_trade_experience_cost_regime_min_samples: int = Field(default=30, ge=1, le=5000)
    ai_trade_experience_export_page_size: int = Field(default=500, ge=50, le=5000)
    # ---- 后台物化调度（D-1 / D-3 的修法；三项都只被 manager 侧后台任务读）----
    # 去抖：距上次成功物化不足该秒数 ⇒ 只置脏标志不起任务（防每笔平仓触发一次全库重建）。
    ai_trade_experience_materialize_min_interval_s: float = Field(default=60.0, ge=0, le=3600)
    # ★ E-6（评审 P0-6）修订语义：这**不是**「CPU 段的硬超时」——`asyncio.timeout` 停不掉
    # `run_in_threadpool` 里已经开跑的线程，那个工作线程会把整轮全库重建跑完。它只约束
    # 「排队等 `materialize_lock` 最多等多久」，等不到就放弃本次（脏标志留着，下次再来）。
    ai_trade_experience_materialize_timeout_s: float = Field(default=20.0, gt=0, le=300)
    # 慢物化告警线：单次耗时超过它即在 `materialize_health.slow` 置真，UI 明示规模墙（I-8）。
    ai_trade_experience_materialize_slow_ms: int = Field(default=3000, ge=100, le=600000)
    # ---- ★ E-2（评审 P0-2）：让非 stat 经验真的能被检索到 ----
    # manual / imported / narrative 出生即 support=0、weight=0.0，乘进 score 恒为 0 并被
    # `score > 0` 剔除 ⇒ 蒸馏 / 手工新增 / 导入三条写入路径全是只写不读。
    ai_trade_experience_manual_seed_weight: float = Field(default=0.35, ge=0, le=1)
    # 用户显式置顶 = 用户提供的证据。不设这个下限，I-4 承诺的「升 iron 只有一条路径——用户显式
    # 置顶」在代码里根本不存在（§7.1 那句「置顶的经验不受证据门槛约束」也就是假的）。
    ai_trade_experience_pinned_evidence_floor: float = Field(default=0.60, ge=0, le=1)
    # ---- ★ E-4（评审 P0-4）：关停 drain 的死线，**必须** < shutdown_step_timeout_s（默认 20.0）----
    # 倒挂时外层先 cancel，drain 自己那半段「cancel 剩余任务」永远不执行，后台任务活着穿过
    # `dispose_engine`。姿态与 `main._cancel_background` 的 5.0 秒逐字一致：先 cancel、再有界收敛。
    ai_trade_experience_drain_timeout_s: float = Field(default=5.0, gt=0, le=60)
    # ---- ★ E-6（评审 P0-6）：**全量重建**的规模闸 ----
    # 超过即**不自动跑全量**，只在 `materialize_health.overflow` 置真并由 UI 明示。宁可停下来说
    # 清楚，也不要一个永远跑不完、每 60 秒还叠一个的后台任务（那个线程池全进程共用）。
    #
    # ⚠️ **它只约束全量路径**（ai-trade-experience-compounding ★ E-3）。增量物化的成本与库大小
    # 近似无关——它只读 `id > watermark` 的那一批且被 `incremental_batch` 封顶，触碰的桶数由本批
    # 事件决定——故增量**不受本闸约束**。下一个读到这个名字的人请勿把它当成总闸再抬回去。
    ai_trade_experience_materialize_max_events: int = Field(default=200000, ge=1000, le=5000000)
    # ---- ★ E-10（评审 P1-4）：导入是不可信输入，体量也是 ----
    ai_trade_experience_import_max_nodes: int = Field(default=2000, ge=1, le=100000)
    # ---- ★ E-15（评审 P1-9）：`enabled:false` = 既不读也不写。默认露出那句明文；关掉只是不显示 ----
    ai_trade_experience_disclose_off_semantics: bool = True

    # ---- W-B 增量物化（ai-trade-experience-compounding §6.2，6 项）----
    # 三个总闸（incremental / utility / adopt_harvest）**各自独立**，理由是回滚粒度：W-B 出问题
    # 只需要关增量，不该把 W-C 一并关掉。关任一都退回上一轮（`8e05dd9`）的对应行为。
    ai_trade_experience_incremental_enabled: bool = True
    # 一次增量最多吃多少条新事件；满批 ⇒ 脏标志保留、`pendingEvents` 如实回报、下次继续。
    ai_trade_experience_incremental_batch: int = Field(default=20000, ge=100, le=200000)
    # `costRegime` 三分位的漂移容忍（判据 B-2）。分位漂了却继续用旧标签 ⇒「今天的 low 和半年前
    # 的 low 不是一回事」，缓慢失真且不可察觉——超阈即回全量并 `epoch+1`。
    ai_trade_experience_cuts_drift_bps: float = Field(default=1.0, ge=0, le=100)
    # `medianNetBps` 的稀疏定宽直方图分箱与夹紧（J-2 允许的唯一误差，UI 必须标「≈」）。
    ai_trade_experience_hist_bin_bps: float = Field(default=1.0, gt=0, le=50)
    ai_trade_experience_hist_clamp_bps: float = Field(default=5000.0, gt=0)
    # 桶级去重标的集合的容量上限；溢出即按 `>= cap` 处理（任何 `global_min_symbols` 阈值都会通过）。
    ai_trade_experience_symbols_cap: int = Field(default=64, ge=1, le=1000)

    # ---- W-C 效用闭环（4 项）----
    # False ⇒ 不写台账、不算、不静音（`overview` 也不下发 `utility` 键）。
    ai_trade_experience_utility_enabled: bool = True
    # 失效判据的三重门槛：注入次数 ≥ min_samples，且注入后净额均值与其断言符号系统性相反，
    # 且 |t| ≥ t_threshold。**只导向静音，绝不反向加权**（J-1）。
    ai_trade_experience_utility_min_samples: int = Field(default=12, ge=1, le=10000)
    ai_trade_experience_utility_t_threshold: float = Field(default=2.0, gt=0, le=100)
    ai_trade_experience_utility_detail_rows: int = Field(default=20, ge=1, le=200)

    # ---- W-A 采全（1 项）----
    # False ⇒ 退回上一轮行为：接管 / 离线平仓的往返**只计缺口、不补采**。
    ai_trade_experience_adopt_harvest_enabled: bool = True

    # ---- 策略与因子挖掘（strategy-factor-discovery §六，env 前缀 DISCOVERY_）----
    # 字段名即环境变量名（DISCOVERY_*，大小写不敏感）。⚠️ 三处联动缺一即静默失效：
    # ① 此处必须**真的声明**字段（Settings extra="ignore" 会吞掉未声明的 env）；
    # ② desktop/src/lib/config.js 的 prod 白名单必须放行 DISCOVERY_ 前缀；③ .env.example 补文档。
    discovery_enabled: bool = True                  # 总闸；False ⇒ 写类端点 403（读类仍可看历史）
    discovery_max_runs: int = Field(default=1, ge=1, le=3)   # 并发挖掘任务上限（硬上限 3）
    discovery_default_interval_s: float = Field(default=300.0, gt=0)   # 迭代间隔默认（对齐报告的 5 分钟）
    discovery_min_interval_s: float = Field(default=60.0, gt=0)        # 间隔夹紧下界
    discovery_max_interval_s: float = Field(default=3600.0, gt=0)      # 间隔夹紧上界
    discovery_max_candidates_per_iter: int = Field(default=200, ge=1)  # 单轮候选上限
    discovery_max_window_bars: int = Field(default=8000, ge=200)       # 数据窗口根数上限（取数护栏）
    # 单轮工作量上限（候选数 × 根数），超限 413（沿用 MINE_WORK_UNIT_LIMIT 语义）。
    discovery_work_unit_limit: int = Field(default=3_000_000, ge=1000)
    discovery_candidate_timeout_s: float = Field(default=20.0, gt=0)   # 单候选验证墙钟超时
    # 单轮墙钟预算（评审 P2-4）：candidateTimeoutS × candidatesPerIteration 最坏 800s 远超默认
    # 300s 间隔，必须有上限。到点停取新候选、正常 commit 已完成者、记 truncatedByTimeout + 丢弃数。
    discovery_iteration_timeout_s: float = Field(default=600.0, gt=0)
    # 验证层进 run_in_threadpool 的并发信号量（评审 P2-6）：与所有 API 请求**共用** anyio 默认
    # 线程池，24/7 挖掘若无节制并发会与交易 / 回测请求争抢线程，表现为「整个 App 变卡」却查不到原因。
    discovery_validate_concurrency: int = Field(default=2, ge=1, le=8)
    discovery_llm_enabled: bool = True                                 # LLM 子代理总闸
    discovery_llm_timeout_s: float = Field(default=60.0, gt=0)
    discovery_llm_max_retries: int = Field(default=1, ge=0)
    discovery_llm_max_calls_per_hour: int = Field(default=24, ge=0)    # 成本硬闸（0=禁用 LLM 调用）
    discovery_max_candidate_rows: int = Field(default=20000, ge=100)   # 每 run 拒绝候选保留上限
    discovery_max_iteration_rows: int = Field(default=5000, ge=50)     # 每 run 迭代记录保留上限
    discovery_ws_ring: int = Field(default=400, ge=50)                 # WS 环形缓冲条数
    # 进程重启后自动续跑 running 任务。⚠️ **受 discovery_enabled 一票否决**（评审 P1-8）：总闸为
    # False 时一律 paused(disabled_by_config)，绝不拉起后台循环——写类端点 403 却在后台继续烧
    # LLM 与 OKX 配额，是「关了但没关」的假安全姿态。续跑必先跑完 kept 池重建才进第一轮。
    discovery_auto_resume: bool = True
    discovery_resume_delay_s: float = Field(default=10.0, ge=0)        # 自动续跑前的静置延时

    # ---- 三条策略轴（discovery-strategy-axes §六）----
    # ⚠️ 计数单位是**底层 run_backtest 调用次数**，不是候选数：engine/combo.py::_build_member_curves
    # 对每个成员各跑一次，一个 strategy_combo 候选背后是 4–12 次。按候选级计数会低估 4–6 倍，
    # 墙钟护栏形同虚设（评审 P1-3）。
    discovery_max_backtests_per_iteration: int = Field(default=120, ge=1)
    # 单个策略候选的墙钟超时（**IS / OOS / S9 压力，三段合计**）。
    discovery_backtest_timeout_s: float = Field(default=30.0, gt=0)
    discovery_max_htf_frames: int = Field(default=3, ge=0, le=8)       # 一个 run 的高周期帧数上限
    discovery_member_catalog_max: int = Field(default=60, ge=2)        # 因子成员目录上限
    # 低于此规模的轴本轮跳过 FSA 扫描（BL-10 后半）：池里只有 3 条时每条都占 33% > 15%，
    # 会被**全部**冻结——回测型的三条策略轴入库速度比因子轴低一到两个数量级，早期必然踩中。
    discovery_fsa_min_pool: int = Field(default=20, ge=1)
    # ---- 成果资产台账与持续样本外体检（discovery-asset-ledger §六，env 前缀 DISCOVERY_HEALTH_）----
    # 15 个键。⚠️ desktop/src/lib/config.js 的 prod 白名单已放行整个 DISCOVERY_ 前缀，本组
    # **无需**改 desktop（见 .env.example 的同名说明）。
    discovery_health_enabled: bool = True             # 体检守护总闸，受 discovery_enabled 一票否决
    discovery_health_tick_s: float = Field(default=900.0, gt=0)        # 守护轮询间隔
    discovery_health_interval_s: float = Field(default=21600.0, gt=0)  # 单资产体检周期（默认 6h）
    discovery_health_batch: int = Field(default=8, ge=1, le=64)        # 单 tick 处理的资产数
    # ⚠️ 计数单位是**底层 run_backtest 调用次数**：run_split_backtest 无条件跑 IS + OOS 两段，
    # 预估口径一律 2 × backtests_per_segment()（AL-9）。按 per_segment 计放行量正好翻倍，
    # 且表现为「从没触发过闸」——看起来完全正常。
    discovery_health_max_backtests_per_tick: int = Field(default=40, ge=1)
    discovery_health_asset_timeout_s: float = Field(default=45.0, gt=0)   # 单资产墙钟超时
    discovery_health_window_bars: int = Field(default=1500, ge=200, le=8000)  # 一组一次取这么多
    # 每个资产的暖机段根数；freshStartTs 与 maxFreshBars 都由它派生（§2.3）。
    # 校验：必须 ≥ discovery_health_min_warmup_bars 且 < discovery_health_window_bars，
    # 否则 maxFreshBars ≤ 0、fresh 段恒空——启动即 fail-fast，不许运行期才发现。
    discovery_health_warmup_bars: int = Field(default=400, ge=50, le=4000)
    discovery_health_min_fresh_bars: int = Field(default=120, ge=10)   # 不足即 unknown（AL-2）
    discovery_health_min_warmup_bars: int = Field(default=300, ge=50)  # 夹上界后实为取数缺口告警
    discovery_health_min_fresh_trades: int = Field(default=5, ge=1)    # 策略资产 fresh 段成交下限
    discovery_health_retain_frac: float = Field(default=0.5, ge=0, le=1)  # H5 / H6 的留存系数
    discovery_health_fail_streak: int = Field(default=2, ge=1, le=10)  # 迟滞（AL-8）
    discovery_health_max_check_rows: int = Field(default=200, ge=10)   # 每资产体检记录上限
    discovery_health_max_assets: int = Field(default=5000, ge=100)     # 台账规模护栏（不报错、不删旧）

    # ---- 代理（透传给 httpx / websockets）----
    http_proxy: str = ""
    https_proxy: str = ""
    all_proxy: str = ""

    # ---- 持久化 ----
    db_path: str = "aragon.db"

    # ---- 默认值 ----
    default_inst: str = "BTC-USDT-SWAP"
    default_bar: str = "1H"
    initial_capital: float = 10000.0

    # ---- 行为开关 ----
    allow_cached_fallback: bool = False
    max_paper_sessions: int = 12

    # ---- 撮合默认参数 (spec §6.1) ----
    default_taker_fee_bps: float = 5.0       # OKX 永续 taker ≈ 5 bps
    default_slippage_bps: float = 2.0
    default_leverage: float = 1.0

    # ---- 回测取数安全闸（高级回测：滑动窗口 / 全区间分页 spec §6.3）----
    # 字段名即环境变量名（BACKTEST_MAX_BARS / WALK_FORWARD_MAX_WINDOWS，大小写不敏感）。
    backtest_max_bars: int = Field(default=20000, ge=1000)   # 单次回测取数安全上限（防超长区间拖垮）
    walk_forward_max_windows: int = Field(default=200, ge=1)  # 高级回测窗口数硬上限
    # 同步权重训练（/api/factor-sets/train）的有效根数预算：超出即取数前 413 FACTOR_TRAIN_TOO_LARGE 快速
    # 失败，而非让无界长请求被传输层窗口切断成 socket hang up（docs/diagnoses/
    # factor-lab-train-long-range-socket-hang-up）。须严格 < backtest_max_bars，否则因 target 早被夹到
    # 20000 而沦为 no-op；默认 8000（≈80 次 OKX 串行请求、≥10s 限频地板，落在多数链路超时窗口内），可经
    # 环境变量 FACTOR_TRAIN_MAX_BARS 调参。
    factor_train_max_bars: int = Field(default=8000, ge=1000)
    # ---- 因子实验室「标的组」多标的扇出上限（factor-lab-symbol-groups §五，取数前判定，绝不无界扇出）----
    # 字段名即环境变量名（MAX_TRAIN_SYMBOLS / MAX_EVAL_SYMBOLS，大小写不敏感）。多标的评估 / 训练逐标的
    # 串行取真实 OKX K 线，标的数越界即取数前 400 快速失败（评估 FACTOR_EVAL_TOO_MANY_SYMBOLS / 训练
    # FACTOR_TRAIN_TOO_MANY_SYMBOLS），把逐标的 OKX 请求压回 _candle_cache 量级附近，重演 429 / socket 压力
    # 的风险受控（C-1）。挖掘扇出由既有 413 工作量护栏（计入标的数）在取数前拦截，无需独立标的数上限。
    max_train_symbols: int = Field(default=20, ge=1)   # 单次权重训练的标的组成员数上限
    max_eval_symbols: int = Field(default=30, ge=1)    # 单次因子评估的标的组成员数上限
    # ---- 权重模型工作区（weight-model-workspace §四/config，流式训练 + 模型管理）----
    # 字段名即环境变量名（MAX_WEIGHT_TRAIN_JOBS 等，大小写不敏感）。流式训练作业与 socket 解耦，
    # 故并发上限独立于同步训练护栏：超出即 429 WEIGHT_TRAIN_BUSY（守门优先，绝不先落库再拒，R-01）。
    # G-B9：补 le 上限（宽松、远高于合理用值），仅拦截病态超大值——过高 curve_steps 膨胀缓冲 + WS 事件数，
    # 过高 max_weight_train_jobs 抹掉保护性并发上限；默认值不变，既有部署不受影响。
    max_weight_train_jobs: int = Field(default=4, ge=1, le=16)      # 同时在跑的流式训练作业上限（R-01 429 守门）
    weight_train_curve_steps: int = Field(default=6, ge=2, le=20)   # 扩样本学习曲线的 fraction 网格步数（末点恒 1.0）
    weight_model_run_cap: int = Field(default=50, ge=1, le=500)     # 单模型训练记录上限；超出按时间剪枝且显式 log（R-05）
    # 多模型训练竞技场（weight-multi-model-training §3.2/config）：单个批次可勾选的候选方法 / 超参变体数上限，
    # 界定单批资源上界（候选超可用名额时进服务端 FIFO 队列，名额释放即泵起，绝不突破 max_weight_train_jobs）。
    max_weight_batch_candidates: int = Field(default=8, ge=2, le=16)  # 单批候选数上限（超出即 400 守门，绝不无界扇出）
    # 因子组合探索训练（factor-combo-exploration §2.3/§4.1/§7）：单次探索展开的「组合 × 方法」候选数上限。
    # 探索走自有端点（不受 WeightBatchStartReq.candidates 的 max_length=16 约束），故上限独立在此界定。默认 12
    # （约为 arena max_weight_batch_candidates=8 的 1.5×），并发仍受 max_weight_train_jobs=4 限、墙钟随候选数
    # 线性增长；超限一律显式 400 WEIGHT_EXPLORE_TOO_MANY_CANDIDATES 阻断，绝不静默截断（P1-3）。
    max_weight_explore_candidates: int = Field(default=12, ge=2, le=24)

    # ---- Agent（对话式 AI 助手）LLM 接入 (Agent Trading Copilot spec §3.1) ----
    # 字段名即环境变量名（AGENT_*，大小写不敏感）；密钥仅存后端，绝不下发前端 / 不入库 / 不进日志。
    agent_provider: str = "anthropic"                       # anthropic | openai（双标准适配）
    agent_base_url: str = "https://api.kimi.com/coding"     # KIMI Coding 端点
    agent_api_key: str = ""                                 # 缺省为空 → 面板优雅引导而非崩溃
    # 默认采用任务显式指定值 kimi-k2.6；kimi-k2.5 为 docs/examples 已实测可用的回退值（R1/P1-A）。
    agent_model: str = "kimi-k2.6"
    agent_anthropic_version: str = "2023-06-01"
    agent_user_agent: str = "claude-code/1.0"               # KIMI 端点校验 UA（R2）
    agent_max_tokens: int = 2048
    agent_temperature: float = 1.0
    agent_top_p: float = 0.95
    # 失控护栏 / 上下文裁剪（RS-13 边界校验：轮数 ≥1、回喂条数 ≥2，杜绝 .env 误填 0/负值）。
    agent_max_tool_rounds: int = Field(default=12, ge=1)     # 失控护栏（spec §2.3）
    agent_max_context_messages: int = Field(default=40, ge=2)  # 回喂模型的近 N 条裁剪（P2-2）
    agent_timeout_seconds: float = 120.0
    # Agent LLM 独立代理：留空回落 settings.proxy_url；显式 "none" 强制直连（R5）。
    agent_proxy: str = ""
    # ---- 加固增强（iteration 2）：LLM 传输层有界重试 + 断连 drain 超时 (hardening H1/H4) ----
    # 字段名即 AGENT_* 环境变量名（大小写不敏感），可经 .env 覆盖。
    agent_max_retries: int = Field(default=2, ge=0)  # pre-stream 重试上限（最多 1+max 次，0 即不重试）
    agent_retry_base_ms: int = 500      # 指数退避基数（第 i 次退避 base*2**i，封顶 max）；RN-6：不新增校验
    agent_retry_max_ms: int = 8000      # 单次退避封顶（亦为 Retry-After 整数秒解析上限）；RN-6：不新增校验
    agent_drain_timeout_seconds: float = Field(default=8.0, gt=0)  # 断连后兜底 drain 超时
    # ---- 韧性流式（iteration 3）：run/socket 解耦续流 + 心跳保活（RS-1/RS-3/RS-7/RS-13）----
    # 字段名即 AGENT_* 环境变量名；缺省回落以下默认值，桌面端经白名单按需覆盖。
    agent_detach_grace_seconds: float = Field(default=30.0, ge=0)   # 断连存活宽限；0 即断连立即取消（回退迭代2）
    agent_heartbeat_seconds: float = Field(default=20.0, gt=0)      # run 期心跳间隔（须 < 中间层 idle 超时）
    agent_event_buffer_size: int = Field(default=512, ge=64)        # 会话事件缓冲上限（重放窗口）

    # ---- 信任边界（trust-boundary-and-signal-integrity D1 / §6.1）----
    # 逗号分隔的 Origin 白名单；空 = 用 `app/security/origins.py` 的 DEFAULT_ORIGINS。
    # 显式配置即**完全替换**默认（不做合并，避免「以为收紧了其实没有」）。
    # ⚠️ 前缀必须是 `SECURITY_`：`desktop/src/lib/config.js` 的 `backendEnv` 用**前缀白名单**决定
    # 哪些 env 进得了打包后端子进程，而 prod 的 PyInstaller 后端**不读 `.env`、只读进程 env**。
    # 裸名 `ALLOWED_ORIGINS` 一个前缀都不沾 ⇒ 打包形态下这个开关恒失效，白名单一旦不匹配就是
    # 「点什么都 403 且用户无从补救」。改名/加字段时必须同步那份白名单（该文件已为此栽过两次）。
    security_allowed_origins: str = ""

    @model_validator(mode="after")
    def _check_allowed_origins(self) -> "Settings":
        """白名单里不许出现 ``*`` 或 ``null`` —— 启动即 fail-fast。

        一个「安全开关」允许被一键关掉，等于没有这个开关；`null` 是 sandboxed iframe 的不透明源，
        放行它等于给整条边界留一个任意网页三行 HTML 就能走通的正门（spec §三 D1 [v2·P0-1]）。
        """
        self.allowed_origin_list()
        return self

    @model_validator(mode="after")
    def _check_health_window(self) -> "Settings":
        """体检窗口三个根数的相容性（discovery-asset-ledger §六）—— 启动即 fail-fast。

        ``maxFreshBars = window_bars − warmup_bars`` 必须为正，否则 fresh 段恒空、每个资产
        永远判 ``unknown``；而 ``unknown`` 按 D-26 是中性文案「暂不评级」，界面上**看不出
        任何异常**。这类「不报错的空转」只能在启动时挡下来。
        """
        if self.discovery_health_warmup_bars < self.discovery_health_min_warmup_bars:
            raise ValueError(
                f"DISCOVERY_HEALTH_WARMUP_BARS({self.discovery_health_warmup_bars}) 必须 ≥ "
                f"DISCOVERY_HEALTH_MIN_WARMUP_BARS({self.discovery_health_min_warmup_bars})")
        if self.discovery_health_warmup_bars >= self.discovery_health_window_bars:
            raise ValueError(
                f"DISCOVERY_HEALTH_WARMUP_BARS({self.discovery_health_warmup_bars}) 必须 < "
                f"DISCOVERY_HEALTH_WINDOW_BARS({self.discovery_health_window_bars})，"
                "否则体检的 fresh 段恒为空、全部资产永久「暂不评级」")
        return self

    @model_validator(mode="after")
    def _check_tradability_thresholds(self) -> "Settings":
        """`marginal < infeasible`（§6.1）—— 启动即 fail-fast。

        反过来配（marginal ≥ infeasible）会让 `marginal` 这一档**恒不可达**：判定链先判
        `infeasible` 再判 `marginal`，任何达到 marginal 阈的断面都已先被判成 infeasible。
        那是一个不报错的空转，只能在构造期挡下来。
        """
        if self.ai_trade_tradability_marginal_veto_rate >= (
                self.ai_trade_tradability_infeasible_veto_rate):
            raise ValueError(
                f"AI_TRADE_TRADABILITY_MARGINAL_VETO_RATE"
                f"({self.ai_trade_tradability_marginal_veto_rate}) 必须 < "
                f"AI_TRADE_TRADABILITY_INFEASIBLE_VETO_RATE"
                f"({self.ai_trade_tradability_infeasible_veto_rate})，否则 marginal 档恒不可达")
        return self

    @model_validator(mode="after")
    def _check_experience_budgets(self) -> "Settings":
        """经验披露的字符预算自洽性（不变式 I-6）—— 违反即拒绝启动，**不静默降级**。

        判据：`max_chars ≥ 框架开销 + 单条上限(120) + 序号与换行(4)`。`label ≤ 120 字` 是写入期
        schema 硬限，故满足该式即保证「第一条一定装得下」。不满足时的行为是**整层一个字都不渲染**
        （零条目 ⇒ 整段省略），那是一个不报错的空转：用户配了经验模式、日志全绿、模型一条都收不到。
        """
        from .ai_auto.experience.disclosure import (
            FRAME_OVERHEAD,
            MIN_ITEM_BUDGET,
            MIN_ITEM_BUDGET_L2,
            budget_floor,
        )

        for name, value, layer in (
                ("AI_TRADE_EXPERIENCE_L0_MAX_CHARS", self.ai_trade_experience_l0_max_chars, "l0"),
                ("AI_TRADE_EXPERIENCE_L1_MAX_CHARS", self.ai_trade_experience_l1_max_chars, "l1")):
            floor = budget_floor(layer)     # ★ E-7：与 `PUT /settings/experience` 共用同一判据
            if value < floor:
                raise ValueError(
                    f"{name}({value}) 必须 ≥ {floor}（框架句 {FRAME_OVERHEAD[layer]} 字 + 单条上限 "
                    f"{MIN_ITEM_BUDGET} 字），否则该层第一条经验就装不下 ⇒ 整层一个字都不渲染")
        # ★ D-3 配套（ai-trade-experience-compounding §4.2）· L2 是这条判据漏掉的第三层。
        # `reflection_max_chars` 是 `Field(ge=0, le=2000)` 的合法配置项且**不在**设置桥表里，
        # 故它此前完全不受 `budget_floor` 保护：落在 (0, 208) 区间会通过启动期校验，却让 L2
        # 永远渲染不出一个字——一个不报错的空转（与 `v1:E-7` 逐字同形）。
        # `0` 是合法的「整段关闭」，中间值一律拒绝启动，姿态与上面两层一致：不静默降级。
        reflection = self.ai_trade_experience_reflection_max_chars
        l2_floor = budget_floor("l2")
        if 0 < reflection < l2_floor:
            raise ValueError(
                f"AI_TRADE_EXPERIENCE_REFLECTION_MAX_CHARS({reflection}) 必须为 0（整段关闭）"
                f"或 ≥ {l2_floor}（框架句 {FRAME_OVERHEAD['l2']} 字 + 单条上限 "
                f"{MIN_ITEM_BUDGET_L2} 字，L2 无序号前缀），否则反例段一个字都渲染不出来")
        return self

    @model_validator(mode="after")
    def _check_experience_drain_deadline(self) -> "Settings":
        """★ E-4（评审 P0-4）· 关停 drain 的死线**必须**严格小于单步关停死线——倒挂即拒绝启动。

        `main._run_shutdown` 的单步死线取 `shutdown_step_timeout_s`，超时即 `cancel` 该步并只再给
        `_SHUTDOWN_CANCEL_GRACE_S = 1.0` 秒。若 drain 内部等得比它久，外层的 cancel 会把 drain
        打断在 `asyncio.wait` 上，**drain 自己那半段「cancel 剩余任务」永远不会执行**——恰恰在它
        被设计来应对的那一种情况下。那些后台任务于是活着穿过 `("db_engine", dispose_engine)` 那一步。

        这条不等式一旦倒挂，失败是**完全静默**的：日志里只多一行「shutdown step … did not settle」。
        故只能在构造期挡下来（用例 A-47 正反两向各一条）。
        """
        if self.ai_trade_experience_drain_timeout_s >= self.shutdown_step_timeout_s:
            raise ValueError(
                f"AI_TRADE_EXPERIENCE_DRAIN_TIMEOUT_S({self.ai_trade_experience_drain_timeout_s})"
                f" 必须 < SHUTDOWN_STEP_TIMEOUT_S({self.shutdown_step_timeout_s})，否则外层先 "
                "cancel、drain 内层的清理永不执行，后台任务会活着穿过 dispose_engine")
        return self

    def allowed_origin_list(self) -> list[str]:
        """解析 ``SECURITY_ALLOWED_ORIGINS`` 为白名单；空串 → ``DEFAULT_ORIGINS``。

        Returns:
            至少含一项的 Origin 列表（逐项 ``strip()`` 后丢弃空项）。

        Raises:
            ValueError: 白名单里出现 ``*`` 或 ``null``（两者都等价于「把边界关掉」）。
        """
        raw = self.security_allowed_origins.strip()
        if not raw:
            return list(DEFAULT_ORIGINS)
        items = [part.strip() for part in raw.split(",") if part.strip()]
        for item in items:
            if item == "*" or item.lower() == "null":
                raise ValueError(
                    f"SECURITY_ALLOWED_ORIGINS 不接受 {item!r}：通配符与不透明源 null 都等价于"
                    "关闭信任边界（null 是 sandboxed iframe 的来源，正是本设计要挡的那一类）")
        return items or list(DEFAULT_ORIGINS)

    @property
    def proxy_url(self) -> str | None:
        """优先 HTTPS_PROXY，其次 HTTP_PROXY / ALL_PROXY；都没有则 None。"""
        return self.https_proxy or self.http_proxy or self.all_proxy or None

    @property
    def agent_proxy_url(self) -> str | None:
        """Agent LLM 调用专用代理（R5）：'none' → None（强制直连）；空 → 回落 proxy_url。"""
        if self.agent_proxy.strip().lower() == "none":
            return None
        return self.agent_proxy or self.proxy_url


@lru_cache
def get_settings() -> Settings:
    """单例配置。"""
    return Settings()
