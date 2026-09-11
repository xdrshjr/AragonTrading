"""后训练数据目录：默认根目录、归一化、可用性探测与分组子目录命名（trajectory-capture §2.12）。

两条贯穿本模块的纪律：

- **默认根目录只有一个真源**：`default_root` 经 `db.session.db_file_path` 推导，绝不在这里再写一遍
  「相对路径以 services/quant 为根」——两份必然漂移。
- **探测绝不创建目标目录本身**：用户逐字输入时每次失焦都建一个半截目录是垃圾。探测只在「最近的
  已存在祖先目录」里建删一个探针子目录（及其中一个文件）；真正建目录只发生在启动时的
  `recorder._prepare_group_dir`。

后端下发的 `message` 一律中文、**不含 `**`**（JSX 会显示成字面星号，本仓库已复发四次）。
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import re
import shutil
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from .wire import SCHEMA_VERSION

# 段文件相对路径的上界是 149 字符（按压缩中间文件 `.jsonl.gz.tmp` 计，含 `e<4 位 epoch 序号>-` 前缀，
# §2.12.2 第 3 条与实施发现 I-29）；Windows MAX_PATH 含结尾 NUL 为 260 ⇒ 根目录 ≤ 110，取 100 留余量。
WINDOWS_ROOT_MAX_CHARS = 100
# 只有 Anthropic 适配层会转出 `thinking_delta`（§1.3 N5）。
THINKING_CAPTURED_PROVIDERS = ("anthropic",)

_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]")
_DEVICE_PREFIXES = ("\\\\?\\", "\\\\.\\", "//?/", "//./")
# Windows 文件夹名里不允许的字符与保留设备名。`Path.exists()` 对它们只回 False、不抛，探测会一路走到
# 可写的祖先并判「可用」，直到启动时 mkdir 才以 WinError 123 失败——校验与启动说两种话。
_WINDOWS_BAD_CHARS = frozenset('<>:"|?*') | frozenset(chr(code) for code in range(32))
_WINDOWS_RESERVED = frozenset({"CON", "PRN", "AUX", "NUL",
                               *(f"COM{index}" for index in range(1, 10)),
                               *(f"LPT{index}" for index in range(1, 10))})


@dataclass(frozen=True, slots=True)
class DirVerdict:
    """一次目录判定的结果（**数据，不是错误**：`ok=False` 的端点照样回 200）。"""

    ok: bool
    code: str | None
    message: str | None
    directory: str
    is_default: bool
    exists: bool
    free_bytes: int | None
    min_free_bytes: int

    def wire(self) -> dict:
        """`POST /ai-trade/trajectory/validate` 的响应体（§2.12.2）。"""
        return {"ok": self.ok, "code": self.code, "message": self.message,
                "directory": self.directory, "isDefault": self.is_default,
                "exists": self.exists, "freeBytes": self.free_bytes,
                "minFreeBytes": self.min_free_bytes}


def safe_component(text) -> str:
    """把 `groupId` / `instId` 变成可进路径的一段（非法字符替换为 `_`，截到 96 字符）。

    `.` 与 `..` 在正则里是合法字符，却会让 `members/<instId>` 指回上级目录，故一并替换。
    """
    component = _SAFE_RE.sub("_", str(text or ""))[:96]
    return "_" if component in ("", ".", "..") else component


def group_dir_name(created_ts: int, group_id: str) -> str:
    """分组子目录名 `<YYYYMMDD>-<groupId>`；日期取分组 `created_ts` 的 UTC 日期（冷续跑复用同一目录）。"""
    day = time.strftime("%Y%m%d", time.gmtime(int(created_ts)))
    return f"{day}-{safe_component(group_id)}"


def normalize_directory(raw: str) -> Path:
    """去首尾空白与成对引号 → 展开环境变量与 `~`。**不** `resolve()`：输入阶段不访问磁盘。"""
    text = str(raw).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        text = text[1:-1].strip()
    return Path(os.path.expanduser(os.path.expandvars(text)))


def default_root(settings) -> Path:
    """默认根目录（单一真源）：显式配置优先，否则 `<数据库文件所在目录>/datasets/ai-trade`。

    显式配置是相对路径 ⇒ 以数据库文件所在目录为根。原样留着会被 `_reject_static` 判成「请填写完整的
    绝对路径」，而默认根目录的 422 只剩「本次不采集」一条出路——用户对一个运维配置无能为力。
    """
    raw = str(getattr(settings, "ai_trade_trajectory_default_dir", "") or "").strip()
    configured = normalize_directory(raw) if raw else None
    if configured is not None and configured.is_absolute():
        return configured
    from ...db.session import db_file_path    # 延迟导入：纯函数用例与编译 CLI 不连带 import 数据库层

    base = db_file_path(settings).parent
    return base / configured if configured is not None else base / "datasets" / "ai-trade"


def format_bytes(value: int) -> str:
    """人读字节数（1024 进位）。只用于后端文案；前端有自己的一份。"""
    size = float(value)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def resolve_target(raw: str | None, settings) -> tuple[Path, bool]:
    """把用户输入解析成目标目录与「是不是默认根目录」（空输入 ⇒ 默认根目录）。"""
    text = str(raw or "").strip()
    default = default_root(settings)
    if not text:
        return default, True
    path = normalize_directory(text)
    return path, _same_path(path, default)


def check_directory(raw: str | None, settings) -> DirVerdict:
    """按 §2.12.2 的表逐条短路判定（同步、含 IO，只在线程里调用）。

    Args:
        raw: 用户输入的目录；`None` / 空串 ⇒ 校验默认根目录。
        settings: 生效的 `Settings`。

    Returns:
        `DirVerdict`。绝不抛：路径本身非法（含 NUL 等）也落成一条带原因码的判定。
    """
    min_free = int(getattr(settings, "ai_trade_trajectory_min_free_bytes", 0) or 0)
    path, is_default = resolve_target(raw, settings)
    base = {"directory": str(path), "is_default": is_default, "min_free_bytes": min_free}
    rejected = _reject_static(settings, path)
    if rejected is not None:
        return DirVerdict(False, *rejected, exists=False, free_bytes=None, **base)
    try:
        code, message, exists, free = _probe_io(path, min_free)
    except (OSError, ValueError) as exc:
        code, message, exists, free = ("TRAJECTORY_DIR_NOT_WRITABLE",
                                       f"目录不可写：{exc}", False, None)
    return DirVerdict(code is None, code, message, exists=exists, free_bytes=free, **base)


async def probe_directory(raw: str | None, settings) -> DirVerdict:
    """`check_directory` 的有界异步包装：线程里跑，死线 `ai_trade_trajectory_probe_timeout_s`。"""
    timeout = float(settings.ai_trade_trajectory_probe_timeout_s)
    try:
        return await asyncio.wait_for(asyncio.to_thread(check_directory, raw, settings), timeout)
    except (TimeoutError, asyncio.TimeoutError):
        path, is_default = resolve_target(raw, settings)
        return DirVerdict(
            False, "TRAJECTORY_DIR_PROBE_TIMEOUT",
            "检查目录超时（网络盘或设备无响应），请换一个本地文件夹",
            directory=str(path), is_default=is_default, exists=False, free_bytes=None,
            min_free_bytes=int(settings.ai_trade_trajectory_min_free_bytes))


def trajectory_config_wire(settings) -> dict:
    """`GET /ai-trade/trajectory/config` 的响应体（卡片据它说明「为什么不能开」与默认目录）。"""
    return {"enabled": bool(settings.ai_trade_trajectory_enabled),
            "defaultRoot": str(default_root(settings)),
            "minFreeBytes": int(settings.ai_trade_trajectory_min_free_bytes),
            "segmentMaxBytes": int(settings.ai_trade_trajectory_segment_max_bytes),
            "compressSegments": bool(settings.ai_trade_trajectory_compress_segments),
            "schemaVersion": SCHEMA_VERSION, "backtestSupported": False,
            "thinkingCapturedProviders": list(THINKING_CAPTURED_PROVIDERS)}


def _reject_static(settings, path: Path) -> tuple[str, str] | None:
    """表的第 0–4 条（不访问磁盘）。"""
    text = str(path)
    if not getattr(settings, "ai_trade_trajectory_enabled", True):
        return "TRAJECTORY_DISABLED_BY_OPS", "后训练数据采集已被运维关闭"
    if "\x00" in text or text.startswith(_DEVICE_PREFIXES):
        return "TRAJECTORY_DIR_UNSUPPORTED", "不支持设备命名空间路径，请选择普通文件夹"
    if not path.is_absolute():
        return "TRAJECTORY_DIR_NOT_ABSOLUTE", "请填写完整的绝对路径（例如 D:\\data\\ai-trade）"
    if sys.platform == "win32" and _has_bad_windows_name(path):
        return ("TRAJECTORY_DIR_UNSUPPORTED",
                "文件夹名里有 Windows 不允许的字符（如 < > : | ? *）或保留名（如 CON、NUL），请换一个名字")
    if sys.platform == "win32" and len(text) > WINDOWS_ROOT_MAX_CHARS:
        return ("TRAJECTORY_DIR_PATH_TOO_LONG",
                "路径过长：段文件完整路径会超过 Windows 260 字符限制，请选择更短的目录")
    if _inside_program_dir(path):
        return "TRAJECTORY_DIR_FORBIDDEN", "不能写入程序安装目录，请另选文件夹"
    return None


def _has_bad_windows_name(path: Path) -> bool:
    """盘符 / UNC 锚点之外的每一段：含非法字符，或（去掉扩展名后）是保留设备名。"""
    for part in PureWindowsPath(str(path)).parts[1:]:
        if _WINDOWS_BAD_CHARS & set(part):
            return True
        if part.split(".")[0].strip().upper() in _WINDOWS_RESERVED:
            return True
    return False


def _probe_io(path: Path, min_free: int) -> tuple[str | None, str | None, bool, int | None]:
    """表的第 5–7 条：是否为文件 → 最近祖先可写 → 可用空间。"""
    exists = path.is_dir()
    if path.exists() and not exists:
        return "TRAJECTORY_DIR_IS_FILE", "该路径是一个文件，请选择文件夹", False, None
    ancestor = _nearest_existing(path)
    if ancestor is None or not ancestor.is_dir():
        return ("TRAJECTORY_DIR_NOT_WRITABLE",
                "目录不可写：找不到可用的上级目录（磁盘不存在，或路径中有同名文件）", False, None)
    failure = _write_probe(ancestor)
    if failure is not None:
        return "TRAJECTORY_DIR_NOT_WRITABLE", failure, exists, None
    free = _free_bytes(ancestor)
    if min_free > 0 and free is not None and free < min_free:
        return ("TRAJECTORY_DIR_LOW_SPACE",
                f"可用空间 {format_bytes(free)} 低于下限 {format_bytes(min_free)}，"
                "采集会立即自动暂停，请换一个磁盘", exists, free)
    return None, None, exists, free


def _nearest_existing(path: Path) -> Path | None:
    for candidate in (path, *path.parents):
        if candidate.exists():
            return candidate
    return None


def _write_probe(directory: Path) -> str | None:
    """在目录里建删一个探针**子目录**、并在其中建删一个文件；失败 ⇒ 中文原因（含 strerror 与 errno）。

    与真正的打开同形：启动时要先在这里建 `<日期>-<分组号>/members/…` 再往里写文件。只试「在这里建
    文件」会误伤系统盘根目录——`C:\\` 默认只授予已认证用户「创建文件夹」而不授予「创建文件」，
    `C:\\ai-trade-data` 这种完全可用的目录就会被判成不可写、启动按钮被挡住。
    """
    probe = directory / f".aragon-probe-{uuid.uuid4().hex}"
    try:
        probe.mkdir()
        try:
            with open(probe / "w", "xb"):
                pass
        finally:
            with contextlib.suppress(OSError):
                (probe / "w").unlink()
            with contextlib.suppress(OSError):
                probe.rmdir()
    except OSError as exc:
        return f"目录不可写：{exc.strerror or exc}（{exc.errno}）"
    return None


def _free_bytes(directory: Path) -> int | None:
    try:
        return int(shutil.disk_usage(directory).free)
    except OSError:
        return None


def _same_path(left: Path, right: Path) -> bool:
    return (os.path.normcase(os.path.abspath(str(left)))
            == os.path.normcase(os.path.abspath(str(right))))


def _program_dirs() -> list[Path]:
    """后端程序目录：`app` 包所在目录，打包形态下另加 PyInstaller 的 `_MEIPASS`。"""
    dirs = [Path(__file__).resolve().parents[2]]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        dirs.append(Path(meipass))
    return dirs


def _inside_program_dir(path: Path) -> bool:
    target = os.path.normcase(os.path.abspath(str(path)))
    for base in _program_dirs():
        root = os.path.normcase(os.path.abspath(str(base)))
        try:
            if os.path.commonpath([target, root]) == root:
                return True
        except ValueError:          # 不同盘符 ⇒ 必然不在其内
            continue
    return False
