"""后训练轨迹采集 · 目录（ai-trade-trajectory-capture §2.12 · test_ai_trade_trajectory_directory）。

两条贯穿本文件的纪律（与被测模块同源）：

- **探测绝不创建目标目录本身**：用例在判定之后断言目标目录仍不存在、祖先里不留探针文件；
- **后端下发的 message 不含 `**`**（JSX 会显示成字面星号，本仓库已复发四次）：每条判定都过一遍。

根目录一律用 `tempfile.mkdtemp(prefix="trj")` 造：pytest 的 `tmp_path` 在 Windows 上可能超过
100 字符上限，令本该测别的原因码的用例先撞上 `TRAJECTORY_DIR_PATH_TOO_LONG`。
"""
from __future__ import annotations

import errno
import os
import shutil
import sys
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

import app
from app.ai_auto.trajectory import directory as directory_mod
from app.ai_auto.trajectory.directory import (
    WINDOWS_ROOT_MAX_CHARS,
    DirVerdict,
    check_directory,
    default_root,
    group_dir_name,
    normalize_directory,
    probe_directory,
    resolve_target,
    safe_component,
)
from app.ai_auto.trajectory.writer import SegmentWriter
from app.config import Settings
from app.db.session import db_file_path

QUANT_ROOT = Path(app.__file__).resolve().parents[1]
WIRE_KEYS = {"ok", "code", "message", "directory", "isDefault", "exists", "freeBytes",
             "minFreeBytes"}


@pytest.fixture
def short_root():
    """短根目录（见模块 docstring）；用例结束即删。"""
    root = Path(tempfile.mkdtemp(prefix="trj"))
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _settings(**overrides) -> Settings:
    knobs = {"ai_trade_trajectory_min_free_bytes": 0, "ai_trade_trajectory_default_dir": ""}
    knobs.update(overrides)
    return Settings(**knobs)


def _assert_verdict(verdict: DirVerdict, code: str | None) -> DirVerdict:
    """原因码 + 文案纪律 + 响应体形状（每条判定都过一遍）。"""
    assert verdict.code == code, (verdict.code, verdict.message)
    assert verdict.ok is (code is None)
    if code is None:
        assert verdict.message is None
    else:
        assert verdict.message and "**" not in verdict.message
    assert set(verdict.wire()) == WIRE_KEYS
    return verdict


# ---------------- 归一化 / 默认根目录 / 命名 ----------------
def test_normalize_strips_whitespace_and_a_matching_pair_of_quotes(short_root):
    target = str(short_root / "ds")
    assert normalize_directory(f'  "{target}"  ') == Path(target)
    assert normalize_directory(f"' {target} '") == Path(target)
    assert normalize_directory(f"\t{target}\n") == Path(target)
    assert normalize_directory(f"\"{target}'") == Path(f"\"{target}'"), "不成对的引号不是包裹符"
    assert not (short_root / "ds").exists(), "归一化阶段不访问磁盘，更不建目录"


def test_normalize_expands_environment_variables_before_home(monkeypatch, short_root):
    monkeypatch.setenv("TRJ_DATA_ROOT", str(short_root))
    monkeypatch.setenv("TRJ_HOME_REF", "~")
    home = Path(os.path.expanduser("~"))
    assert normalize_directory("${TRJ_DATA_ROOT}/ds") == short_root / "ds"
    assert normalize_directory("$TRJ_DATA_ROOT/ds") == short_root / "ds"
    if os.name == "nt":
        assert normalize_directory("%TRJ_DATA_ROOT%\\ds") == short_root / "ds"
    assert normalize_directory("~/ds") == home / "ds"
    # 先展开环境变量、再展开 `~`：变量值里的 `~` 也会被展开（§2.12.2 的顺序）。
    assert normalize_directory("${TRJ_HOME_REF}/ds") == home / "ds"
    assert normalize_directory("relative/ds") == Path("relative/ds"), "不 resolve()"


def test_default_root_prefers_the_explicit_setting(monkeypatch, short_root):
    monkeypatch.setenv("TRJ_DATA_ROOT", str(short_root))
    settings = _settings(ai_trade_trajectory_default_dir='  "${TRJ_DATA_ROOT}/sets"  ')
    assert default_root(settings) == short_root / "sets"
    assert resolve_target(None, settings) == (short_root / "sets", True)


def test_default_root_falls_back_to_the_database_directory(short_root):
    absolute = _settings(db_path=str(short_root / "desk" / "aragon.db"),
                         ai_trade_trajectory_default_dir="   ")
    assert default_root(absolute) == short_root / "desk" / "datasets" / "ai-trade"
    relative = _settings(db_path="aragon-trj.db")
    assert default_root(relative) == db_file_path(relative).parent / "datasets" / "ai-trade"
    assert default_root(relative) == QUANT_ROOT / "datasets" / "ai-trade"
    assert not (short_root / "desk").exists()


def test_a_relative_default_dir_is_anchored_at_the_database_directory(short_root):
    """运维把默认目录配成相对路径：原样留着会被判「请填写完整的绝对路径」，而默认根目录的 422 只剩
    「本次不采集」一条出路——每个没改目录的用户都被一个运维配置挡在门外。"""
    settings = _settings(db_path=str(short_root / "desk" / "aragon.db"),
                         ai_trade_trajectory_default_dir="sets/ai-trade")
    assert default_root(settings) == short_root / "desk" / "sets" / "ai-trade"
    _assert_verdict(check_directory(None, settings), None)
    assert list(short_root.iterdir()) == []


def test_resolve_target_flags_the_default_root_regardless_of_spelling(short_root):
    settings = _settings(ai_trade_trajectory_default_dir=str(short_root))
    assert resolve_target("", settings) == (short_root, True)
    assert resolve_target("   ", settings) == (short_root, True)
    assert resolve_target(f"{short_root}{os.sep}", settings)[1] is True
    if os.name == "nt":
        assert resolve_target(str(short_root).upper(), settings)[1] is True
    assert resolve_target(str(short_root / "other"), settings) == (short_root / "other", False)


def test_safe_component_replaces_illegal_characters_and_caps_length():
    assert safe_component("BTC-USDT-SWAP") == "BTC-USDT-SWAP"
    assert safe_component("aitradegrp_0a1B.x") == "aitradegrp_0a1B.x"
    assert safe_component('a/b\\c:d*e?f"g<h>i|j k') == "a_b_c_d_e_f_g_h_i_j_k"
    assert safe_component("比特币-USDT") == "___-USDT"
    assert safe_component("x" * 200) == "x" * 96
    assert safe_component("") == safe_component(None) == "_"
    assert group_dir_name(1_788_000_000, "aitradegrp_ab/cd") == "20260829-aitradegrp_ab_cd"


# ---------------- Windows 长路径阈值（RV-P2-2） ----------------
def test_windows_root_threshold_is_100_characters(monkeypatch, short_root):
    """win32 下根目录 > 100 字符即拒、恰 100 放行；非 win32 不设此限。"""
    assert WINDOWS_ROOT_MAX_CHARS == 100
    settings = _settings()
    base = str(short_root) + os.sep
    at_limit, over = base + "a" * (100 - len(base)), base + "b" * (101 - len(base))
    assert (len(at_limit), len(over)) == (100, 101)
    monkeypatch.setattr(sys, "platform", "win32")
    _assert_verdict(check_directory(over, settings), "TRAJECTORY_DIR_PATH_TOO_LONG")
    _assert_verdict(check_directory(at_limit, settings), None)
    monkeypatch.setattr(sys, "platform", "linux")
    _assert_verdict(check_directory(base + "c" * 150, settings), None)
    assert list(short_root.iterdir()) == [], "判定不建目标目录、不留探针文件"


def test_segment_paths_under_a_limit_root_fit_windows_max_path():
    """阈值由段文件相对路径上界反推（RV-P2-2）。

    最长分组名 + 32 字符标的 + 压缩中间文件名（含 `e<4 位 epoch 序号>-` 前缀，实施发现 I-29）
    = 149 字符；100 字符的根 + 149 < 260（MAX_PATH）。
    """
    group = group_dir_name(1_788_000_000, "aitradegrp_" + "f" * 32)
    member = safe_component("X" * 32)
    fake_writer = SimpleNamespace(_rec=SimpleNamespace(epoch="e" * 32, epoch_ordinal=9999))
    segment = SegmentWriter._segment_name(fake_writer, 99999) + ".gz.tmp"
    relative = f"\\{group}\\members\\{member}\\{segment}"
    assert len(relative) == 149
    assert WINDOWS_ROOT_MAX_CHARS + len(relative) < 260


# ---------------- 判定表的每一个原因码 ----------------
def test_ops_switch_off_rejects_before_anything_else(short_root):
    settings = _settings(ai_trade_trajectory_enabled=False)
    verdict = _assert_verdict(check_directory("relative", settings), "TRAJECTORY_DISABLED_BY_OPS")
    assert (verdict.exists, verdict.free_bytes) == (False, None)


@pytest.mark.parametrize("raw", ["C:\\da\x00ta", "\\\\?\\C:\\data", "\\\\.\\PhysicalDrive0",
                                 "//?/C:/data"])
def test_device_namespace_and_nul_paths_are_unsupported(raw):
    _assert_verdict(check_directory(raw, _settings()), "TRAJECTORY_DIR_UNSUPPORTED")


def test_relative_paths_are_rejected():
    _assert_verdict(check_directory("datasets/ai-trade", _settings()),
                    "TRAJECTORY_DIR_NOT_ABSOLUTE")
    if os.name == "nt":       # 有根无盘符（`\data`）与有盘符无根（`C:data`）在 Windows 上都不是绝对路径
        _assert_verdict(check_directory("\\data", _settings()), "TRAJECTORY_DIR_NOT_ABSOLUTE")
        _assert_verdict(check_directory("C:data", _settings()), "TRAJECTORY_DIR_NOT_ABSOLUTE")


@pytest.mark.parametrize("name", ["a<b", "x?y", "report|2026", "CON", "nul.txt", "COM1"])
def test_windows_forbidden_names_are_rejected_before_probing(monkeypatch, short_root, name):
    """`Path.exists()` 对这类名字只回 False、不抛：不先静态拦下，探测会走到可写的祖先判「可用」，
    启动时 mkdir 才以 WinError 123 失败——校验与启动说两种话。"""
    monkeypatch.setattr(sys, "platform", "win32")
    target = short_root / name / "sets"
    _assert_verdict(check_directory(str(target), _settings()), "TRAJECTORY_DIR_UNSUPPORTED")
    assert list(short_root.iterdir()) == []


def test_program_directories_are_forbidden(monkeypatch, short_root):
    app_dir = Path(app.__file__).resolve().parent
    for raw in (str(app_dir), str(app_dir / "datasets")):
        _assert_verdict(check_directory(raw, _settings()), "TRAJECTORY_DIR_FORBIDDEN")
    if os.name == "nt":
        upper = str(app_dir / "datasets").upper()
        _assert_verdict(check_directory(upper, _settings()), "TRAJECTORY_DIR_FORBIDDEN")
    bundle = short_root / "bundle"
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    _assert_verdict(check_directory(str(bundle / "data"), _settings()), "TRAJECTORY_DIR_FORBIDDEN")
    # 同名前缀的兄弟目录不在程序目录之内（按路径段比较，不按字符串前缀）。
    _assert_verdict(check_directory(str(short_root / "bundle-data"), _settings()), None)
    assert not (app_dir / "datasets").exists() and list(short_root.iterdir()) == []


def test_an_existing_file_is_not_a_directory(short_root):
    target = short_root / "notes.txt"
    target.write_text("x", encoding="utf-8")
    _assert_verdict(check_directory(str(target), _settings()), "TRAJECTORY_DIR_IS_FILE")


def test_an_unwritable_ancestor_reports_strerror_and_errno(monkeypatch, short_root):
    real_open = open

    def denying_open(path, mode="r", *args, **kwargs):
        if ".aragon-probe-" in str(path):
            raise PermissionError(errno.EACCES, "拒绝访问")
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(directory_mod, "open", denying_open, raising=False)
    target = short_root / "a" / "b"
    verdict = _assert_verdict(check_directory(str(target), _settings()),
                              "TRAJECTORY_DIR_NOT_WRITABLE")
    assert verdict.message == f"目录不可写：拒绝访问（{errno.EACCES}）"
    assert verdict.exists is False and not (short_root / "a").exists()
    assert list(short_root.iterdir()) == [], "失败的探针子目录同样删干净"


def test_the_write_probe_creates_a_folder_first_like_the_real_open(monkeypatch, short_root):
    """系统盘根目录（`C:\\`）默认只授予「创建文件夹」、不授予「创建文件」：探测必须与真正的打开同形
    （先建目录、再在里面写文件），否则 `C:\\ai-trade-data` 这种完全可用的目录会被判成不可写。"""
    real_open = open

    def root_denies_files(path, mode="r", *args, **kwargs):
        if Path(path).parent == short_root:
            raise PermissionError(errno.EACCES, "拒绝访问")
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(directory_mod, "open", root_denies_files, raising=False)
    _assert_verdict(check_directory(str(short_root / "ai-trade-data"), _settings()), None)
    assert list(short_root.iterdir()) == [], "探针子目录与其中的文件都已删掉"


def test_a_file_in_the_middle_of_the_path_is_not_writable(short_root):
    (short_root / "blocker").write_text("x", encoding="utf-8")
    _assert_verdict(check_directory(str(short_root / "blocker" / "sub"), _settings()),
                    "TRAJECTORY_DIR_NOT_WRITABLE")


def test_free_space_below_the_floor_is_low_space(short_root):
    settings = _settings(ai_trade_trajectory_min_free_bytes=10**18)
    verdict = _assert_verdict(check_directory(str(short_root), settings),
                              "TRAJECTORY_DIR_LOW_SPACE")
    assert isinstance(verdict.free_bytes, int) and verdict.exists is True
    assert verdict.wire()["minFreeBytes"] == 10**18


def test_a_passing_check_reports_facts_without_creating_anything(short_root):
    target = short_root / "a" / "b" / "c"
    verdict = _assert_verdict(check_directory(str(target), _settings()), None)
    assert (verdict.directory, verdict.is_default, verdict.exists) == (str(target), False, False)
    assert isinstance(verdict.free_bytes, int) and verdict.free_bytes > 0
    assert list(short_root.iterdir()) == [], "探测只在最近祖先里建删探针，绝不建目标目录"
    default = _settings(ai_trade_trajectory_default_dir=str(short_root))
    existing = _assert_verdict(check_directory(None, default), None)
    assert (existing.exists, existing.is_default) == (True, True)


async def test_a_hung_probe_becomes_a_timeout_verdict(monkeypatch, short_root):
    release = threading.Event()

    def hung_check(raw, settings):
        release.wait(10.0)

    monkeypatch.setattr(directory_mod, "check_directory", hung_check)
    target = short_root / "slow"
    try:
        verdict = await probe_directory(str(target),
                                        _settings(ai_trade_trajectory_probe_timeout_s=0.2))
    finally:
        release.set()
    _assert_verdict(verdict, "TRAJECTORY_DIR_PROBE_TIMEOUT")
    assert (verdict.directory, verdict.exists, verdict.free_bytes) == (str(target), False, None)


async def test_probe_directory_returns_the_check_verdict_when_it_finishes(short_root):
    verdict = await probe_directory(str(short_root / "fast"), _settings())
    _assert_verdict(verdict, None)
    assert list(short_root.iterdir()) == []
