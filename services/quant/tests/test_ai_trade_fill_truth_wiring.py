"""本轮 13 项设置的登记与回退（ai-trade-fill-truth-and-effective-controls 跨轴门禁）。

门禁 `G-Env`（仓库根 `.env.example` **双向**登记）、`G-X1`（九把闸全置回退档 ⇒ 零新键）、
`G-X2`（每个新 `aiStats` 键在 `apps/web` 的非测试文件里至少有一个读点）、
`G-X3`（仓库级文案门禁：后端会进 `message` / `note` 的中文常量不含字面 `**`）。

⚠️ 正则一律写 `\\r?\\n`：`core.autocrlf=true` 下写死 `\\n` 会在干净检出上恒红（成文教训）。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.config import Settings

# 本轮新增的 13 项（`AI_TRADE_` 前缀是硬约束——桌面端的 env 透传白名单是**前缀式**的，
# 而打包后端不读 `.env`、只读进程 env）。九把闸 + 四个参数，见 spec §八。
_NEW_SETTINGS = (
    "ai_trade_settlement_truth_enabled",
    "ai_trade_settlement_deadline_s",
    "ai_trade_settlement_poll_interval_s",
    "ai_trade_settlement_match_tol_s",
    "ai_trade_orphan_detect_enabled",
    "ai_trade_orphan_confirm_rounds",
    "ai_trade_orphan_block_open",
    "ai_trade_sizing_zero_is_veto",
    "ai_trade_sizing_floor_ratio",
    "ai_trade_contrarian_gear_downgrade",
    "ai_trade_resting_ttl_wall_clock",
    "ai_trade_reconcile_final_verdict_strict",
    "ai_trade_reconcile_capital_tol_ratio",
)

# 九把**闸**（有「回退档」这个概念）；其余四项是参数，回退由所属轴的总闸承担。
_ROLLBACK = {
    "ai_trade_settlement_truth_enabled": False,
    "ai_trade_orphan_detect_enabled": False,
    "ai_trade_orphan_block_open": False,
    "ai_trade_sizing_zero_is_veto": False,
    "ai_trade_sizing_floor_ratio": 0.0,
    "ai_trade_contrarian_gear_downgrade": False,
    "ai_trade_resting_ttl_wall_clock": False,
    "ai_trade_reconcile_final_verdict_strict": False,
    "ai_trade_reconcile_capital_tol_ratio": 0.0,
}

_REPO_ROOT = Path(__file__).resolve().parents[3]
_APP = Path(__file__).resolve().parents[1] / "app"


def _env_example() -> str:
    """仓库**根**的 `.env.example`。这是本仓库第三次纠正同一条路径——`services/quant/.env.example`
    并不存在，往那里写等于这批开关在打包形态里按不下去。"""
    path = _REPO_ROOT / ".env.example"
    assert path.is_file(), f"仓库根 .env.example 不存在：{path}"
    return path.read_text(encoding="utf-8")


# ---------- G-Env：双向登记 ----------

@pytest.mark.parametrize("field", _NEW_SETTINGS)
def test_every_new_setting_is_declared_on_settings(field):
    assert field in Settings.model_fields, field
    getattr(Settings(), field)                      # 取得到值即已声明（extra="ignore" 会静默丢弃）


@pytest.mark.parametrize("field", _NEW_SETTINGS)
def test_every_new_setting_is_registered_in_the_root_env_example(field):
    assert re.search(rf"(?m)^{field.upper()}=", _env_example()), field


def test_no_new_env_example_appears_under_services_quant():
    """`services/quant/.env.example` 不存在，也不许在本轮被创造出来。"""
    assert not (Path(__file__).resolve().parents[1] / ".env.example").exists()


def test_the_reverse_direction_holds_too():
    """反向：`.env.example` 里出现的本轮键必须真的在 `Settings` 上声明过。

    单向登记的失败模式是静默的——env 里写了、`Settings` 没声明 ⇒ pydantic 直接忽略，
    运维以为自己关掉了一把闸而其实没有。
    """
    declared = {name.upper() for name in Settings.model_fields}
    for line in _env_example().splitlines():
        key = line.split("=", 1)[0].strip()
        if key.startswith("AI_TRADE_SETTLEMENT") or key.startswith("AI_TRADE_ORPHAN"):
            assert key in declared, key


def test_every_new_setting_carries_the_prefix_the_desktop_bundle_forwards():
    """`desktop/src/lib/config.js` 的后端 env 透传白名单是**前缀式**的（硬约束 F-13）。"""
    for field in _NEW_SETTINGS:
        assert field.startswith("ai_trade_"), field


# ---------- 跨字段不变式 ----------

def test_a_poll_interval_that_can_never_fire_is_rejected_at_startup():
    """节流间隔 ≥ 死线 ⇒ 结算等待窗内一次取数都发不出去 ⇒ 轴 A 100% 静默空转。

    **绝不允许一条永远不会被求值的配置存在**——这正是评审 RV-P1-2 在挂单 TTL 上钉死过一次的
    同一个形状（它当时发现 v1 写的 `max_age_s=1800` 是一条死配置）。
    """
    with pytest.raises(Exception) as exc:
        # 两个值都在各自的字段值域内（poll ≤ 60 / deadline ≤ 600），跨字段那条才是拒绝它的理由。
        Settings(ai_trade_settlement_poll_interval_s=30.0,
                 ai_trade_settlement_deadline_s=20.0)
    assert "AI_TRADE_SETTLEMENT_POLL_INTERVAL_S" in str(exc.value)
    # 合法组合照常构造
    Settings(ai_trade_settlement_poll_interval_s=8.0, ai_trade_settlement_deadline_s=90.0)


# ---------- G-X1：九把闸全置回退档 ----------

def test_all_gates_at_their_rollback_notch_construct_cleanly():
    settings = Settings(**_ROLLBACK)
    for field, value in _ROLLBACK.items():
        assert getattr(settings, field) == value, field


def test_the_rollback_notch_disarms_every_runtime_it_governs():
    """回退档下，本轮四个纯核心的装配判据全部为假。"""
    from app.ai_auto.signals import consensus_rule_from_settings
    from app.ai_auto.sizing import parse_floor_policy
    from app.ai_auto.trend_verdict import trend_rule_from_settings
    from app.ai_auto.reconcile import resolve_reconcile_params

    settings = Settings(**_ROLLBACK)
    assert consensus_rule_from_settings(settings).gear_downgrade is False
    assert trend_rule_from_settings(settings).gear_downgrade is False
    policy = parse_floor_policy(settings)
    assert policy.zero_is_veto is False and policy.floor_ratio == 0.0
    params = resolve_reconcile_params(settings)
    assert params.strict_final is False and params.capital_tol_ratio == 0.0


# ---------- 装配缝：纸面 / 无凭证会话绝不装配轴 A ----------

def test_the_settlement_seam_is_not_wired_without_credentials():
    """`creds is None`（纸面 / 无凭证）⇒ `settlement_fn` 整条不传（等价于总闸关）。

    缺了这道判断，纸面会话每遇到一次外部平仓就会白等一整个 `deadline`——取数必然抛异常、被
    fail-safe 兜住，但那仍是 90 秒的无谓延迟，而它本来就没有可问的交易所。判据用源码扫描而不是
    构造一个完整的 `AiTradeGroupStartRequest`：这是一处**装配**判定，构造那份请求需要十几个真实
    依赖，而门禁要守的只是「那一行有没有判 `creds is None`」。
    """
    source = (_APP / "ai_auto" / "manager.py").read_text(encoding="utf-8")
    assert "settlement_fn=(None if creds is None" in source, (
        "manager._build_executor 必须在 creds 缺席时不传结算缝")


def test_the_settlement_seam_defaults_to_absent_on_the_executor():
    """`LiveExecutor` 的形参默认 `None` ⇒ `live_auto` 全部调用点与既有单测逐字回退（`G-A10`）。"""
    import inspect

    from app.live_auto.executor import LiveExecutor

    sig = inspect.signature(LiveExecutor.__init__)
    assert sig.parameters["settlement_fn"].default is None


def test_identity_tracker_is_scoped_to_the_ai_settlement_port():
    from app.live_auto.settlement_identity import IdentityConfig
    from tests.test_external_close_settlement import _make

    ordinary, _ = _make()
    ordinary.configure_settlement_identity(IdentityConfig(1, 1, "SOL"))
    assert ordinary.settlement_identity is None
    ai, calls = _make(settlement_rows=[])
    assert ai.settlement_identity.config.run_id == 1
    assert ai.settlement_identity.config.account_id == 1
    assert calls["reads"] == calls["settlement"] == 0


# ---------- G-X3：仓库级文案门禁 ----------

_MESSAGE_SOURCES = ("ai_auto/orphan.py", "ai_auto/gear_resolution.py",
                    "ai_auto/reconcile.py", "live_auto/settlement.py")


@pytest.mark.parametrize("relative", _MESSAGE_SOURCES)
def test_backend_user_facing_strings_never_carry_markdown_bold(relative):
    """会进 `message` / `note` / `headline` 的中文字符串常量不得含字面 `**`。

    理由写在这里而不是提交信息里：**前端的双侧断言看不见后端字面量**，而 JSX 会把 `**粗体**`
    原样显示成星号。这是本仓库第 5 次复发同一条缺陷，故门禁从「本轮新增的两个函数」升级为
    对整个文件的源码扫描。注释与 docstring 里的 `**` 是给读代码的人看的，不进界面，故只扫
    **可执行行上的字符串字面量**。
    """
    source = (_APP / relative).read_text(encoding="utf-8")
    offenders = []
    for lineno, line in enumerate(re.split(r"\r?\n", source), start=1):
        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith('"""') or '"""' in line:
            continue
        for literal in re.findall(r'"([^"\\]*(?:\\.[^"\\]*)*)"', line):
            if "**" in literal and re.search(r"[一-鿿]", literal):
                offenders.append((lineno, literal))
    assert offenders == [], offenders
