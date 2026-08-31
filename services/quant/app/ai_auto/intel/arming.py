"""情报流水线的装配状态（arm state）——ai-trade-intel-arm-state 唯一的新纯核心模块。

三个事实必须分开，它们各自有不同的出路：

| 事实 | 谁能改 | 出路 |
|---|---|---|
| ``provider_configured`` 检索源配没配 | 用户（设置中心） | 去设置里选源 + 存 key |
| ``intel_enabled``      总闸开没开     | 用户（设置中心） | 打开「启用市场情报采集」 |
| ``armed``              此刻真的在跑吗 | 系统             | 重新装配 / 开启自动交易 |

把三者压成一个布尔，就会让「你没配」这句话出现在一个**已经配好了**的用户面前——那不只是文案
不准，那是系统在对用户说一句关于用户自己配置的假话。

⚠️ **本模块只许依赖 `search` / `taxonomy` / `settings_bridge` 这三个包内叶子**（评审 P0-2）：
一行 ``from . import service`` 会与 `service.py` 的反向 import 模块级成环，任何**先**解析
`arming` 的入口（`api/ai_intel.py` 的 import、`tests/test_ai_intel_arming.py` 的直接 import）都会
在 arming 的 import 段转去执行 service，而 service 第一件事就是回头取 arming 的名字——那时它还
不存在，于是 ``ImportError: cannot import name … from partially initialized module``，**整个后端
起不来**，且打包冒烟就死在装配期。修法**不是**函数内延迟导入（那只是把环藏起来，并让「谁先谁
后」变成运行期才知道的事），而是把 `degrade_wire` 搬回它本来的家 `taxonomy.py`。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from . import search as search_mod
from . import taxonomy as tax
from .settings_bridge import INTEL_SETTING_BRIDGE

# 最近一次装配失败的原因（按分组）。**模块级**，与 `runtime._RUNTIMES` 同款理由：让「它活得比
# manager 实例久」在类型上就不可能发生。住在**本模块**而不是 `runtime.py`（实施发现 I-1）：
# `resolve_arm` 的 `arm_failed` 分支要读它，而 `arming` 依 P0-2 不许 import `runtime`——把表放在
# 判据这一侧是唯一不成环的落点。`runtime.py` 原样重新导出三个写读函数，故
# `intel_runtime.record_arm_error(...)` 的调用点一个字节不改。
_ARM_ERRORS: dict[str, str] = {}
# 没有分组身份可用时的兜底（`_RUNTIMES` 为空 ⇒ `resolve_arm` 拿不到 group_id）。manager 同一时刻
# 只持一个活跃分组（`self._group`），故「最近记下的那一条」与「这个分组的那一条」等价。
_LAST_ARM_ERROR_KEY = ""


@dataclass(frozen=True)
class ArmState:
    """装配状态的四个事实 + 一个细节（frozen；本模块外**只读**）。"""

    provider_configured: bool
    intel_enabled: bool
    armed: bool
    reason: str = ""        # `tax.ARM_REASONS` 之一；armed=True 时恒为 ""
    detail: str = ""        # 仅 reason == "arm_failed" 时非空（异常文本，已截断到 200 字符）

    def __post_init__(self) -> None:
        """不变式：`armed` ⇒ 三者全真。

        一个真的在跑的流水线，不可能没配源、也不可能总闸关着。这条不变式让前端可以放心地
        「先看 `armed`」，不必再写一遍与后端不同源的组合逻辑。

        Raises:
            ValueError: 四个字段自相矛盾时（消息里带全部四个值）。

        ⚠️ **绝不用 `assert`**（评审 P1-5）：本方法跑在**每一次 summary 读**的路径上。
        `python -O` 会把 assert 整条剥掉 ⇒ 不变式形同不存在；不剥掉时抛出的又是一个**没有任何
        定位线索**的裸 `AssertionError`，一路变成 REST 500。两头都违反 CLAUDE.md 五。
        """
        if self.armed and not (self.provider_configured and self.intel_enabled
                               and not self.reason):
            raise ValueError(
                "ArmState 自相矛盾：armed=True 却 "
                f"provider_configured={self.provider_configured} "
                f"intel_enabled={self.intel_enabled} armed={self.armed} "
                f"reason={self.reason!r}")


def arm_of_code(code: str) -> ArmState:
    """一个降级码 → 它对应的未装配 `ArmState`（纯函数）。

    存在的理由（评审 P1-2）：`runtime._static_summary` 也必须带齐 arm 四键，而它是一个只知道
    「降级码」的模块级函数。有了本函数，静态 summary 仍然经 `arm_wire` 这**同一个**写点出键，
    §3.0 第 3 条「同一列只有一个人写」不被破坏，而 `_static_summary(inst_id, *, code)` 的签名
    也一个字节不改（`tests/test_ai_intel_alert_shutdown.py` 的调用点因此继续绿）。

    Args:
        code: `tax.ARM_REASONS` 之一；**不认识的码**（例如轮内降级码 ``"no_provider"``）一律
            回落成「三个布尔全假 + 原样带上该码」——诚实回落，绝不猜。
    """
    if code not in tax.ARM_REASONS:
        return ArmState(False, False, False, code)
    return ArmState(
        provider_configured=code not in ("provider_unset", "key_missing", "provider_unknown"),
        intel_enabled=code != "disabled",
        armed=False, reason=code)


def classify_config(settings) -> str:
    """只看**库里存了什么**的分类判定（纯函数）。

    Args:
        settings: **必须是 `resolve_intel_settings` 的派生副本**，不是进程级 `Settings`。
            打包形态下 API Key 只在 `app_settings` 里（env 是空的），而派生副本会把它叠上来；
            传进程级 `Settings` 会让本函数恒判 `key_missing`，于是本轮的原命题一个字都没修好
            ——而且不报错。

    Returns:
        ``""`` = 配置齐备；否则 ``"disabled"`` / ``"provider_unset"`` /
        ``"key_missing"`` / ``"provider_unknown"``。

    顺序有意义：总闸排最前。一个把总闸关掉的用户不需要被追问「你还没选检索源」。
    """
    if not bool(getattr(settings, "ai_trade_intel_enabled", False)):
        return "disabled"
    return search_mod.classify_provider(
        str(getattr(settings, "ai_trade_intel_search_provider", "none")),
        str(getattr(settings, "ai_trade_intel_search_api_key", "") or ""))


def arm_of_deps(deps) -> ArmState:
    """运行时侧的 arm：``deps.search is not None`` 是**最硬**的证据。

    它就是那个真的会去发检索请求的对象——它在，就说明装配的那一刻配置是齐的。故
    `armed ⇒ provider_configured` 由这里直接兑现，而不是再去 settings 上问一次（离线用例常常
    拿一个裸 `object()` 当 search runner，settings 却是默认值）。
    """
    if getattr(deps, "search", None) is not None:
        return ArmState(True, True, True)
    return arm_of_code(classify_config(deps.settings) or "arm_failed")


def resolve_arm(settings, *, runtime, member: bool) -> ArmState:
    """REST 侧的 arm（`api/ai_intel._summary`、`_require_runtime` 与
    `api/app_settings._intel_wire` **共用同一份判据**）。

    Args:
        settings: **派生副本**（`resolve_intel_settings` 的产物），不是进程级 Settings。
        runtime: 该标的 / 该分组的 `IntelRuntime`（`runtime_for(inst_id)`），可为 None。
        member: 该标的此刻是不是正在跑的自动交易分组的成员。

    判定顺序（guard clause 阶梯，每一条都对应一句不同的话）：
        ① 有一次重装排在队里 ⇒ ``arm_pending``（评审 P1-8：pending 在**这里**就解进
           `ArmState.reason`，绝不留给 `arm_wire` 的一个额外形参——那会让 `read_summary` 那条
           不传 pending 的通路说 ``armReason=""``、而缓存那条说 ``degrade.code="arm_pending"``，
           同一份 wire 的两个键互相矛盾。**它排在 enabled 之前**：`mark_arm_pending` 刻意不动
           `enabled`，一个尚未装上的壳同样可能挂着待应用的重装。）
        ② runtime 在且 enabled ⇒ armed
        ③ 配置本身不齐（总闸关 / 未选源 / 没存 key / 源不认识）⇒ 那个码
        ④ 配置齐、但这个标的不在分组里 ⇒ ``not_running``
        ⑤ 配置齐、在分组里、却没有 enabled 的 runtime ⇒ ``arm_failed``
           （`detail` 取 `_ARM_ERRORS` 记下的最近一次原因；没有记录时由 `arm_wire` 补
           「原因未记录」，绝不留空——见 `taxonomy.DEGRADE_TEXT` 的 ``{reason}`` 占位）
    """
    if runtime is not None and getattr(runtime, "arm_pending", False):
        return arm_of_code("arm_pending")
    if runtime is not None and getattr(runtime, "enabled", False):
        return ArmState(True, True, True)
    code = classify_config(settings)
    if code:
        return arm_of_code(code)
    if not member:
        return arm_of_code("not_running")
    return ArmState(True, True, False, "arm_failed",
                    arm_error_for(getattr(runtime, "group_id", "")))


def _arm_facts(state: ArmState) -> tuple[bool, bool, bool, str]:
    """`ArmState` → 四个事实的**唯一**投影（两张 wire 共用，评审 P1-7 / G-29）。"""
    return (bool(state.provider_configured), bool(state.intel_enabled),
            bool(state.armed), str(state.reason))


def arm_wire(state: ArmState) -> dict:
    """`AiTradeIntelSummary` 上 arm 四键 + `degrade` 的**唯一**写点（§3.0 第 3 条）。

    Returns:
        ``{"providerConfigured","intelEnabled","armed","armReason"}``；未装配时另带
        ``"degrade"``（码与 ``armReason`` **同名**，文案取 `taxonomy.DEGRADE_TEXT` 那张单一
        真源表，``retryInS`` 恒为 ``None``）。

    ⚠️ ``retryInS=None`` 是刻意的（评审 P2-11）：装配状态没有「多久后自动重试」这回事——它要么
    等用户动手（去设置 / 点重新装配），要么等一轮跑完（``arm_pending``，而那一轮多久跑完并不
    可知）。编一个倒计时出来，会得到一个走到 0 却什么都没发生的计时器，比不显示更坏。
    前端 `degradeText` 在 ``retryInS == null`` 时天然不拼那半句，无需改动。

    ⚠️ **没有 `pending=` 形参**（评审 P1-8）：pending 由 `resolve_arm` / `IntelRuntime` 解进
    ``state.reason``，本函数只做映射不做判定。
    """
    configured, enabled, armed, reason = _arm_facts(state)
    wire = {"providerConfigured": configured, "intelEnabled": enabled,
            "armed": armed, "armReason": reason}
    if not armed:
        wire["degrade"] = tax.degrade_wire(reason or "no_provider",
                                           reason=state.detail or "原因未记录")
    return wire


def arm_settings_wire(state: ArmState, *, group_running: bool,
                      armed_members: list[str]) -> dict:
    """`GET/PUT/POST /api/settings/intel` 回执里那个 ``arm`` 块的**唯一**写点（§3.3.4）。

    与 `arm_wire` **共用同一个 `ArmState`**，四个布尔逐字段由 `_arm_facts` 这同一处映射出来
    （评审 P1-7）：让路由层另拼一份、还把 ``armReason`` 改叫 ``reason``，那就是本仓库点名过的
    `groupStateFromStatus` 形状——两张手抄表，漂移了不报错。

    Args:
        state: 与 summary 那份 wire **同一个** `ArmState`。
        group_running: 此刻有没有活跃的自动交易分组。
        armed_members: 分组成员里真的挂着 enabled runtime 的那些（取 `group_status()`
            的 members，**不是**遍历 `_BY_INST`——后者会把上一个分组的残留成员算进来）。

    Returns:
        9 键：``providerConfigured`` / ``intelEnabled`` / ``armed`` / ``reason`` /
        ``message`` / ``detail`` / ``groupRunning`` / ``armedMembers`` / ``pending``。
        ``reason`` 与 `arm_wire` 的 ``armReason`` **恒同值**（G-29 冻结）；
        ``pending`` = ``state.reason == "arm_pending"``，不是第二个自变量。
    """
    configured, enabled, armed, reason = _arm_facts(state)
    return {"providerConfigured": configured, "intelEnabled": enabled, "armed": armed,
            "reason": reason, "message": _arm_message(state, armed_members),
            "detail": str(state.detail), "groupRunning": bool(group_running),
            "armedMembers": list(armed_members), "pending": reason == "arm_pending"}


def _arm_message(state: ArmState, armed_members: list[str]) -> str:
    """``arm`` 块那一句话。**后端下发，前端一个字都不编**（与 `degradeText` 同一条纪律）。"""
    if not state.armed:
        return tax.degrade_message(state.reason or "no_provider",
                                   reason=state.detail or "原因未记录")
    if not armed_members:
        return "情报采集正在运行"
    if len(armed_members) == 1:
        return f"情报采集正在运行：{armed_members[0]}"
    return f"情报采集正在运行：{armed_members[0]} 等 {len(armed_members)} 个标的"


def settings_fingerprint(settings, api_key: str) -> tuple:
    """「这份设置到底变没变」的指纹（纯函数）。

    把 `INTEL_SETTING_BRIDGE` 的 23 个字段值 + ``bool(api_key)`` + `api_key` 的 sha256 前 8
    字符拼成一个元组。**绝不把明文 key 放进指纹**（它进日志与回执的可能性太高）。

    只覆盖桥表里那 23 个键是够的：其余旋钮（``search_base_url`` / ``search_timeout_s`` /
    ``search_max_retries`` / ``proxy_url`` / ``verify_max_tokens`` / ``reader_*``）**只能由 env
    给**，而进程活着的时候 env 不会变。桥表新增键**自动进指纹**，故不存在「忘了往名单里加一行」
    这条本仓库前科三次的路径。
    """
    values = tuple(str(getattr(settings, field, "")) for _wire, _app, field, _cast
                   in INTEL_SETTING_BRIDGE)
    key = (api_key or "").strip()
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:8] if key else ""
    return values + (bool(key), digest)


# ---------------- 装配失败原因（E-10 那个「fail-open 把原因吞了」的正面修法） ----------------
def record_arm_error(group_id: str, exc: object) -> None:
    """记下一次装配失败的原因（截断到 200 字符）。

    `_wire_intel` 与 `rearm_group` 的 `except` 各调一次——没有这个落点，一次 fail-open 的装配
    失败在整个分组生命周期里都不会有任何人知道，而用户唯一的自救手段是停掉一次真实资金会话。
    """
    global _LAST_ARM_ERROR_KEY

    _ARM_ERRORS[str(group_id)] = str(exc)[:200]
    _LAST_ARM_ERROR_KEY = str(group_id)


def clear_arm_error(group_id: str) -> None:
    """装配成功 / 分组拆卸时清除（残留会让下一个分组读到上一个分组的失败原因）。"""
    global _LAST_ARM_ERROR_KEY

    _ARM_ERRORS.pop(str(group_id), None)
    if _LAST_ARM_ERROR_KEY == str(group_id):
        _LAST_ARM_ERROR_KEY = ""


def arm_error_for(group_id: str) -> str:
    """最近一次装配失败的原因；没有记录 ⇒ ``""``（由 `arm_wire` 补「原因未记录」）。

    `group_id` 为空是 `resolve_arm` 的 `arm_failed` 支路的常态——那条支路的前提正是
    「一个 runtime 都没有」，于是拿不到分组身份。此时回落到最近记下的那一条：manager 同一时刻
    只持一个活跃分组，两者等价。
    """
    key = str(group_id) or _LAST_ARM_ERROR_KEY
    return _ARM_ERRORS.get(key, "")
