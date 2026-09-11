"""后训练轨迹采集 · 设置项（ai-trade-trajectory-capture §5.5 · test_ai_trade_trajectory_settings）。

三件事，都属于「写错了不会有任何现场症状」的那一类：

- `.env.example` 与 `Settings` 的 `ai_trade_trajectory_*` 家族**双向逐键对齐**——桌面壳按 `AI_TRADE_`
  前缀透传 env，漏登记的旋钮在打包形态下没有任何途径被发现；
- 启动期校验器 `_check_trajectory_wiring` 的两条倒挂各一正一反；
- D-3：`_db_url()` 与 `db_file_path()` 指向同一文件——默认根目录经后者推导，两份路径解析必然漂移。

源码 / `.env.example` 扫描一律 `re.M` 行首匹配、值不跨行：`core.autocrlf=true` 下干净检出是 CRLF。
"""
from __future__ import annotations

import os
import pathlib
import re

import pytest

from app.ai_auto.trajectory.directory import default_root
from app.config import Settings
from app.db import session as db_session

ROOT = pathlib.Path(__file__).resolve().parents[3]
SQLITE_PREFIX = "sqlite+aiosqlite:///"


def _env_example() -> str:
    return (ROOT / ".env.example").read_text(encoding="utf-8")


def test_every_trajectory_setting_is_registered_in_env_example():
    """`.env.example` 与 `Settings` 的 `ai_trade_trajectory_*` 家族**双向逐键对齐**。

    prod 的 PyInstaller 后端不读 `.env`、只读进程 env，而桌面壳按 `AI_TRADE_` 前缀从 `agent.env`
    透传 —— 一个没写进 `.env.example` 的旋钮，用户在打包形态下**没有任何途径**知道它存在；反过来，
    一个写在 `.env.example` 却已被删掉的键，是一句会被照抄进真实配置的废话。

    判红之后的唯一正确动作是**把缺的那一行补进 `.env.example`**（或删掉多余的那一行），
    不是放宽这里的正则。
    """
    cfg = (ROOT / "services" / "quant" / "app" / "config.py").read_text(encoding="utf-8")

    fields = {name.upper() for name in
              re.findall(r"^    (ai_trade_trajectory_[a-z0-9_]+)\s*:", cfg, re.M)}
    declared = set(re.findall(r"^(AI_TRADE_TRAJECTORY_[A-Z0-9_]+)=", _env_example(), re.M))
    assert fields, "正则没匹配到任何字段 ⇒ 本用例已经失效，先修它"
    assert fields - declared == set(), f"这些设置没写进 .env.example：{sorted(fields - declared)}"
    assert declared - fields == set(), f".env.example 里有已不存在的键：{sorted(declared - fields)}"
    assert len(fields) == 10, "§5.5 登记的是 10 项；增删须同步 spec 与本断言"


def test_env_example_values_equal_the_settings_defaults():
    """`.env.example` 里写的值就是 `Settings` 的默认值：照抄示例文件不能悄悄改变行为。"""
    pairs = dict(re.findall(r"^(AI_TRADE_TRAJECTORY_[A-Z0-9_]+)=([^\r\n]*)", _env_example(), re.M))
    assert len(pairs) == 10
    for key, raw in pairs.items():
        default = Settings.model_fields[key.lower()].default
        text = raw.strip()
        value = text.lower() == "true" if isinstance(default, bool) else type(default)(text)
        assert value == default, f"{key}：.env.example 写 {raw!r}，Settings 默认 {default!r}"


def test_close_deadline_must_be_under_the_shutdown_step_timeout():
    """校验 1：倒挂时 `ai_trade` 步整体先被外层 cancel，写线程的关段与 manifest 收尾永远轮不到。"""
    with pytest.raises(ValueError, match="AI_TRADE_TRAJECTORY_CLOSE_TIMEOUT_S"):
        Settings(ai_trade_trajectory_close_timeout_s=20.0, shutdown_step_timeout_s=20.0)
    ok = Settings(ai_trade_trajectory_close_timeout_s=19.9, shutdown_step_timeout_s=20.0)
    assert ok.ai_trade_trajectory_close_timeout_s == 19.9


def test_probe_deadline_must_not_exceed_the_close_deadline():
    """校验 2：一次卡住的目录打开不能拖过关停预算；相等是允许的（`<=`）。"""
    with pytest.raises(ValueError, match="AI_TRADE_TRAJECTORY_PROBE_TIMEOUT_S"):
        Settings(ai_trade_trajectory_probe_timeout_s=4.5, ai_trade_trajectory_close_timeout_s=4.0)
    ok = Settings(ai_trade_trajectory_probe_timeout_s=4.0, ai_trade_trajectory_close_timeout_s=4.0)
    assert ok.ai_trade_trajectory_probe_timeout_s == ok.ai_trade_trajectory_close_timeout_s


def test_shipped_defaults_satisfy_both_wiring_checks():
    """出厂默认值本身必须过两条校验（读字段默认值，不受本机 `.env` / 进程 env 影响）。"""
    fields = Settings.model_fields
    close = fields["ai_trade_trajectory_close_timeout_s"].default
    assert close < fields["shutdown_step_timeout_s"].default
    assert fields["ai_trade_trajectory_probe_timeout_s"].default <= close


@pytest.mark.parametrize("relative", [False, True], ids=["absolute_db_path", "relative_db_path"])
def test_db_url_and_db_file_path_point_at_the_same_file(monkeypatch, tmp_path, relative):
    """D-3：`_db_url()` 与 `db_file_path(get_settings())` 指向同一文件；相对路径以 services/quant 为根。"""
    db_path = "aragon-trajectory-d3.db" if relative else str(tmp_path / "desk" / "aragon.db")
    settings = Settings(db_path=db_path, ai_trade_trajectory_default_dir="")
    monkeypatch.setattr(db_session, "get_settings", lambda: settings)

    url = db_session._db_url()
    assert url.startswith(SQLITE_PREFIX)
    resolved = db_session.db_file_path(settings)
    assert resolved.is_absolute()
    assert pathlib.Path(url[len(SQLITE_PREFIX):]) == resolved
    expected = ROOT / "services" / "quant" / db_path if relative else pathlib.Path(db_path)
    assert os.path.normcase(str(resolved)) == os.path.normcase(str(expected))
    assert default_root(settings) == resolved.parent / "datasets" / "ai-trade"
