"""应用配置 —— 从环境变量 / .env 读取 (spec §12)。

所有密钥仅存于环境变量，绝不下发前端、不入库、不进日志。
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    # ---- AI 自动交易（ai-auto-trading spec §三/config：AI Agent 作为多策略信号的决策者）----
    # 字段名即环境变量名（AI_TRADE_*，大小写不敏感）。**必须真的声明这些字段**：Settings 为
    # extra="ignore"，未声明的 AI_TRADE_* env 会被静默丢弃（P1-6）。桌面打包须在
    # desktop/src/lib/config.js prod 白名单补 key.startsWith('AI_TRADE_') 方能透传（P1-6）。
    # 安全：自动化专用总闸 ai_trade_enabled 默认关闭（AI 下单默认关闭闸）；启动须 API 层显式同时
    # 校验 ai_trade_enabled 与 live_trading_enabled 皆为 True 方可放行（双闸）。
    ai_trade_enabled: bool = False               # 自动化专用总闸：False 时 /ai-trade 写类一律 403
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
    # ---- 成本记账与最小 edge 门禁（ai-trade-cost-accounting-and-edge-gate §3.9）----
    # 本段全部字段的**作用域是 AI 会话**：`FeeLedger` 只由 `ai_auto/manager.py` 经 `LiveExecutor` 的
    # `fee_ledger=` 构造缝注入，`live_auto`（非 AI）恒传 None ⇒ 这些开关取任何值都不影响非 AI 链路
    # （§3.2.0，Z-7 钉死）。env 名 AI_TRADE_* 已在 desktop config.js prod 透传白名单内（AI_TRADE_ 前缀），
    # 此处须真的声明——Settings extra="ignore" 会静默丢弃未声明的 env。
    # A0-1 记录层：每笔成交按确定性费率模型入账（notional × feeBps）。False ⇒ trade["fee"] 逐字回退 0.0。
    ai_trade_fee_accounting_enabled: bool = True
    # A0-2 口径切换：_close_pnl 扣双边费用 ⇒ session_pnl_usd 变净额。**破坏性变更，默认关**。
    ai_trade_net_pnl_enabled: bool = False
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
    # ---- AI 自动交易 · 回测模式（ai-auto-trading-backtest §2.5，三重决策成本护栏）----
    # 回测每决策根调一次 LLM，成本/时长敏感；三重护栏：决策间隔（降频）+ 决策轮数硬上限（越界启动前
    # 拒绝，绝不静默截断）+ 并发闸（限同时消耗 LLM 的回测数）。字段名即环境变量名（AI_BACKTEST_*，
    # 大小写不敏感）；须真的声明（Settings extra="ignore"，未声明的 env 会被静默丢弃）。
    ai_backtest_max_rounds: int = Field(default=400, ge=1)          # 单次回测的决策轮数硬上限（越界 400 拒绝）
    ai_backtest_default_decision_every_bars: int = Field(default=1, ge=1)  # AI 每 K 根决策一次的默认间隔
    ai_backtest_max_jobs: int = Field(default=2, ge=1, le=8)        # 同时进行的 AI 回测作业上限（超出 429）

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
