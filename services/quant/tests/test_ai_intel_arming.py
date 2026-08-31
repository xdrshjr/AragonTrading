"""情报装配状态的纯核心（ai-trade-intel-arm-state §7.1 · G-1…G-7 / G-6b / G-9 / G-25b /
G-28 / G-29）。

零 IO、零网络、零 DB。这一层回答的是三个**各自独立**的问题——「配没配」「开没开」「装没装上」
——把它们压成一个布尔，就会让「你没配」这句话出现在一个已经配好了的用户面前。
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

from app.ai_auto.intel import arming
from app.ai_auto.intel import search as search_mod
from app.ai_auto.intel import taxonomy as tax


class _Settings:
    """派生副本的最小替身（`resolve_intel_settings` 产出的那一份的形状）。"""

    def __init__(self, *, enabled=True, provider="tavily", api_key="k"):
        self.ai_trade_intel_enabled = enabled
        self.ai_trade_intel_search_provider = provider
        self.ai_trade_intel_search_api_key = api_key


class _Runtime:
    def __init__(self, *, enabled=True, pending=False, group_id="grp-1"):
        self.enabled = enabled
        self.arm_pending = pending
        self.group_id = group_id


# ---------------- G-1：`classify_provider` 的真值表 ----------------
@pytest.mark.parametrize(("slug", "api_key", "expected"), [
    ("none", "k", "provider_unset"),
    ("", "k", "provider_unset"),
    ("tavily", "", "key_missing"),
    ("tavily", "   ", "key_missing"),
    ("bogus", "k", "provider_unknown"),
    ("tavily", "k", ""),
    (" Tavily ", "k", ""),
    ("BRAVE", "k", ""),
])
def test_classify_provider_truth_table(slug, api_key, expected):
    """G-1：三码真值表 + 大小写与前后空格归一 + 纯空白 key 判 `key_missing`。"""
    assert search_mod.classify_provider(slug, api_key) == expected


# ---------------- G-2：`resolve_search_provider` 改写前后行为等价 ----------------
@pytest.mark.parametrize(("slug", "api_key", "is_none"), [
    ("none", "k", True),
    ("tavily", "", True),
    ("bogus", "k", True),
    ("tavily", "k", False),
    ("brave", "k", False),
    ("serper", "k", False),
])
def test_resolve_search_provider_returns_same_as_before(slug, api_key, is_none):
    """G-2：五组入参的返回值逐一相同（`None` 或对应适配器）。"""
    provider = search_mod.resolve_search_provider(slug=slug, api_key=api_key)
    assert (provider is None) is is_none
    if provider is not None:
        assert provider.slug == slug


def test_unknown_slug_logs_once_and_key_missing_stays_silent(caplog):
    """G-2：`provider_unknown` 打且只打一行 info；`key_missing` 仍然静默。"""
    with caplog.at_level("INFO", logger="app.ai_auto.intel.search"):
        assert search_mod.resolve_search_provider(slug="bogus", api_key="k") is None
    assert sum("未实现的检索源" in r.message for r in caplog.records) == 1
    caplog.clear()
    with caplog.at_level("INFO", logger="app.ai_auto.intel.search"):
        assert search_mod.resolve_search_provider(slug="tavily", api_key="") is None
    assert not [r for r in caplog.records if "未实现的检索源" in r.message]


def test_unknown_slug_with_empty_key_now_logs_deliberately(caplog):
    """G-2：**刻意登记的行为变化**——「拼错的 slug + 空 key」由静默改为打一行日志。

    原实现先判空 key、再判 slug 是否实现，故这一组走的是静默分支。新实现把
    `provider_unknown` 排在 `key_missing` 之前：用户拼错了源名，比「key 没填」更值得说出来。
    """
    with caplog.at_level("INFO", logger="app.ai_auto.intel.search"):
        assert search_mod.resolve_search_provider(slug="bogus", api_key="") is None
    assert sum("未实现的检索源" in r.message for r in caplog.records) == 1


# ---------------- G-3：`ArmState` 的不变式 ----------------
def test_arm_state_rejects_contradiction_with_valueerror():
    """G-3：`armed=True` 却没配源 ⇒ `ValueError`（**不是** `AssertionError`），且四值在消息里。"""
    with pytest.raises(ValueError) as excinfo:
        arming.ArmState(False, True, True)
    message = str(excinfo.value)
    for fragment in ("provider_configured=False", "intel_enabled=True", "armed=True",
                     "reason=''"):
        assert fragment in message


# ---------------- G-4 / G-6b：`arm_of_deps` 与 `arm_of_code` ----------------
class _Deps:
    def __init__(self, *, search=None, settings=None):
        self.search = search
        self.settings = settings or _Settings(provider="none", api_key="")


def test_arm_of_deps_trusts_the_search_runner_even_on_default_settings():
    """G-4：`deps.search` 非 None ⇒ 全真，**即便 settings 是默认值**（离线用例的常态）。"""
    state = arming.arm_of_deps(_Deps(search=object()))
    assert (state.provider_configured, state.intel_enabled, state.armed) == (True, True, True)
    assert state.reason == ""


def test_arm_of_deps_without_search_falls_back_to_config_classification():
    assert arming.arm_of_deps(_Deps()).reason == "provider_unset"
    assert arming.arm_of_deps(_Deps(settings=_Settings(enabled=False))).reason == "disabled"
    # 配置齐却没有 search runner ⇒ 装配那一步失败过。
    assert arming.arm_of_deps(_Deps(settings=_Settings())).reason == "arm_failed"


def test_arm_of_code_falls_back_for_unknown_codes():
    """G-6b：轮内降级码 `no_provider` **不在** `ARM_REASONS` 里 ⇒ 三布尔全假 + 原样带码。"""
    state = arming.arm_of_code("no_provider")
    assert (state.provider_configured, state.intel_enabled, state.armed) == (False, False, False)
    assert state.reason == "no_provider"
    disabled = arming.arm_of_code("disabled")
    assert disabled.provider_configured is True
    assert disabled.intel_enabled is False


# ---------------- G-5 / G-9 / G-25b：四种未装配处境与总闸的层级 ----------------
@pytest.mark.parametrize(("settings", "expected"), [
    (_Settings(enabled=False), "disabled"),
    (_Settings(provider="none"), "provider_unset"),
    (_Settings(api_key=""), "key_missing"),
    (_Settings(provider="bogus"), "provider_unknown"),
    (_Settings(), ""),
])
def test_classify_config_layers(settings, expected):
    """G-5 / G-9：四种未装配处境各回不同的码；配齐时回空串。"""
    assert arming.classify_config(settings) == expected


def test_classify_config_master_switch_wins_over_provider(caplog):
    """G-25b：总闸关时恒回 `disabled`，**不论** provider 与 key 配得多齐；开时逐字等于
    `classify_provider`。"""
    assert arming.classify_config(_Settings(enabled=False, provider="tavily",
                                            api_key="k")) == "disabled"
    assert arming.classify_config(_Settings(enabled=False, provider="none",
                                            api_key="")) == "disabled"
    for provider, api_key in (("tavily", "k"), ("none", "k"), ("tavily", ""), ("bogus", "k")):
        settings = _Settings(provider=provider, api_key=api_key)
        assert arming.classify_config(settings) == search_mod.classify_provider(provider, api_key)


# ---------------- G-6：`arm_wire` 的文案与 retryInS ----------------
def test_arm_wire_gives_every_reason_a_chinese_sentence_without_placeholders():
    """G-6：每个 `ARM_REASONS` 元素都拿得到一句**不含 `{`** 的中文；`retryInS` 恒为 None。"""
    for code in tax.ARM_REASONS:
        wire = arming.arm_wire(arming.arm_of_code(code))
        assert wire["armReason"] == code
        assert wire["armed"] is False
        degrade = wire["degrade"]
        assert degrade["code"] == code
        assert "{" not in degrade["message"]
        assert degrade["retryInS"] is None


def test_arm_failed_message_carries_the_recorded_reason():
    wire = arming.arm_wire(arming.ArmState(True, True, False, "arm_failed", "provider 不可用"))
    assert "provider 不可用" in wire["degrade"]["message"]
    blank = arming.arm_wire(arming.ArmState(True, True, False, "arm_failed"))
    assert "原因未记录" in blank["degrade"]["message"]


def test_arm_wire_leaves_degrade_untouched_when_armed():
    """装配好的时候 `arm_wire` **不写** `degrade`——一次真实的轮内降级才该占据那个键。"""
    wire = arming.arm_wire(arming.ArmState(True, True, True))
    assert "degrade" not in wire
    assert wire == {"providerConfigured": True, "intelEnabled": True, "armed": True,
                    "armReason": ""}


# ---------------- G-7 的一半：`resolve_arm` 的五级阶梯 ----------------
def test_resolve_arm_ladder():
    armed = arming.resolve_arm(_Settings(), runtime=_Runtime(), member=True)
    assert (armed.armed, armed.reason) == (True, "")
    pending = arming.resolve_arm(_Settings(), runtime=_Runtime(pending=True), member=True)
    assert (pending.armed, pending.reason) == (False, "arm_pending")
    # 壳上挂着待应用的重装同样要说 `arm_pending`（`mark_arm_pending` 刻意不动 `enabled`）。
    shell_pending = arming.resolve_arm(_Settings(),
                                       runtime=_Runtime(enabled=False, pending=True),
                                       member=True)
    assert shell_pending.reason == "arm_pending"
    unset = arming.resolve_arm(_Settings(provider="none"), runtime=None, member=True)
    assert unset.reason == "provider_unset"
    not_running = arming.resolve_arm(_Settings(), runtime=None, member=False)
    assert not_running.reason == "not_running"
    assert not_running.provider_configured is True


def test_resolve_arm_reports_arm_failed_with_the_recorded_detail():
    arming.record_arm_error("grp-9", RuntimeError("LLM provider 未配置"))
    try:
        state = arming.resolve_arm(_Settings(), runtime=None, member=True)
        assert state.reason == "arm_failed"
        assert state.detail == "LLM provider 未配置"
        assert "LLM provider 未配置" in arming.arm_wire(state)["degrade"]["message"]
    finally:
        arming.clear_arm_error("grp-9")
    assert arming.arm_error_for("grp-9") == ""


# ---------------- G-29：两张 wire 的四个事实恒同值 ----------------
@pytest.mark.parametrize("code", ("", *tax.ARM_REASONS))
def test_two_wires_agree_on_every_fact(code):
    """G-29：对同一个 `ArmState`，两张 wire 的四对字段逐一相等（同值不同名可以，两处各判一次
    不行）。"""
    state = arming.ArmState(True, True, True) if code == "" else arming.arm_of_code(code)
    summary_wire = arming.arm_wire(state)
    settings_wire = arming.arm_settings_wire(state, group_running=True,
                                             armed_members=["BTC-USDT-SWAP"])
    assert summary_wire["providerConfigured"] == settings_wire["providerConfigured"]
    assert summary_wire["intelEnabled"] == settings_wire["intelEnabled"]
    assert summary_wire["armed"] == settings_wire["armed"]
    assert summary_wire["armReason"] == settings_wire["reason"]
    assert settings_wire["pending"] == (state.reason == "arm_pending")
    assert set(settings_wire) == {"providerConfigured", "intelEnabled", "armed", "reason",
                                  "message", "detail", "groupRunning", "armedMembers",
                                  "pending"}


def test_arm_settings_wire_message_names_the_running_members():
    armed = arming.ArmState(True, True, True)
    one = arming.arm_settings_wire(armed, group_running=True, armed_members=["BTC-USDT-SWAP"])
    assert one["message"] == "情报采集正在运行：BTC-USDT-SWAP"
    three = arming.arm_settings_wire(
        armed, group_running=True,
        armed_members=["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP"])
    assert three["message"] == "情报采集正在运行：BTC-USDT-SWAP 等 3 个标的"
    off = arming.arm_settings_wire(arming.arm_of_code("disabled"), group_running=False,
                                   armed_members=[])
    assert off["message"] == tax.DEGRADE_TEXT["disabled"]
    assert off["groupRunning"] is False


# ---------------- 指纹 ----------------
def test_settings_fingerprint_changes_with_config_and_never_carries_the_plain_key():
    a = arming.settings_fingerprint(_Settings(), "secret-key")
    assert a == arming.settings_fingerprint(_Settings(), "secret-key")
    assert a != arming.settings_fingerprint(_Settings(provider="brave"), "secret-key")
    assert a != arming.settings_fingerprint(_Settings(), "other-key")
    assert a != arming.settings_fingerprint(_Settings(), "")
    assert "secret-key" not in "|".join(str(part) for part in a)


# ---------------- G-28：`arming` 绝不与 `service` 成环 ----------------
def test_arming_imports_alone_without_touching_service():
    """G-28：一个**只 import arming**的干净进程必须起得来，且 arming 自己不持有 `service`。

    这条闸守的是「后端还起不起得来」：`arming` 一旦为了拼一个三键字典去 import `service`，
    与 `service` 反向 import `arming` 即成环，**先解析 `arming` 的入口必然 `ImportError`**
    （`api/ai_intel.py` 的 `from ..ai_auto.intel import arming` 就是这样一个入口）。

    进程必须是干净的：本文件其余用例已经把整个 `intel` 包拉进 `sys.modules`，在同一个进程里
    再 import 一次什么都验不到。
    """
    code = ("import app.ai_auto.intel.arming as m; "
            "assert not hasattr(m, 'service'), 'arming 持有了 service 引用'; "
            "print(m.ArmState.__name__)")
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                          cwd=str(Path(__file__).resolve().parents[1]))
    assert proc.returncode == 0, proc.stderr
    assert "ArmState" in proc.stdout


def test_arming_source_never_imports_service():
    """G-28 的第二半：源码扫描（`core.autocrlf=true` ⇒ 正则一律写 `\\r?\\n`）。"""
    source = (Path(__file__).resolve().parents[1] / "app" / "ai_auto" / "intel"
              / "arming.py").read_text(encoding="utf-8")
    assert not re.search(r"(?m)^from \.service import.*\r?\n", source)
    assert not re.search(r"(?m)^from \. import service.*\r?\n", source)
    assert not re.search(r"(?m)^from \. import runtime.*\r?\n", source)
