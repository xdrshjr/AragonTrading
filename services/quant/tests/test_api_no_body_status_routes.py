"""门禁：`204` 一类「不许带响应体」的端点，绝不能被 FastAPI 推出一个响应模型。

这条门禁是一次**整机打包冒烟当场死掉**换来的：`app/api/ai_intel.py` 两个 `status_code=204`
的删除端点写成了 `-> None`，而该模块（连同 `app/api/` 下几乎所有模块）都开着
`from __future__ import annotations`——注解于是变成字符串 `"None"`，FastAPI 解析它时走
`typing._type_check`，那里会把 `None` 转成 `NoneType`。`NoneType` 是个**真值**，于是被当作
`response_model`，`APIRoute.__init__` 当场
`AssertionError: Status code 204 must not have a response body`。

失败点在 **import 期**，所以后果远大于「一个端点坏了」：整个 `app.main` 打不开，桌面端后端
可执行体启动即退。而它在测试里几乎无声——全仓 4800+ 用例只有两个文件 import `app.main`，
两条 collection error 太容易被当成「既有红」放过去。

两条判据一动一静：
- 运行期那条守**事实**（真实装配出来的路由表），任何写法绕不过；
- 源码那条守**写法**，让新加的 204 端点在写下去的那一刻就被推回仓库现有的 `-> Response` 写法，
  而不是等某次打包再炸一遍。

判据取自 AST 而不是原文：`ai_intel.py` 的模块 docstring 里逐字写着 `-> None` 是怎么坏的，
按原文扫会把这段解释本身判成违规。
"""
from __future__ import annotations

import ast
from pathlib import Path

from fastapi.routing import APIRoute
from fastapi.utils import is_body_allowed_for_status_code

from app.main import app

_API_DIR = Path(__file__).resolve().parents[1] / "app" / "api"


def test_no_body_status_routes_carry_no_response_model():
    """运行期：装配好的路由表里，禁体状态码的端点一律没有响应模型。"""
    offenders = [
        (route.path, sorted(route.methods), route.status_code, route.response_model)
        for route in app.routes
        if isinstance(route, APIRoute)
        and route.status_code is not None
        and not is_body_allowed_for_status_code(route.status_code)
        and route.response_model
    ]
    assert offenders == [], (
        f"这些端点的状态码不许带响应体，却推出了响应模型：{offenders}；"
        "改成 `-> Response` + `return Response(status_code=204)`")


def _decorator_status_code(decorator: ast.expr) -> int | None:
    """装饰器里写死的 `status_code=<int>`；没写或不是字面量 ⇒ None。"""
    if not isinstance(decorator, ast.Call):
        return None
    for kw in decorator.keywords:
        if kw.arg == "status_code" and isinstance(kw.value, ast.Constant):
            value = kw.value.value
            return value if isinstance(value, int) else None
    return None


def _has_explicit_none_response_model(decorator: ast.expr) -> bool:
    """显式 `response_model=None`：那会跳过「按返回注解推模型」的整条分支，同样安全。"""
    if not isinstance(decorator, ast.Call):
        return False
    return any(kw.arg == "response_model" and isinstance(kw.value, ast.Constant)
               and kw.value.value is None
               for kw in decorator.keywords)


def _is_none_annotation(annotation: ast.expr | None) -> bool:
    return isinstance(annotation, ast.Constant) and annotation.value is None


def test_source_scan_no_204_endpoint_is_annotated_returning_none():
    """源码：`app/api/` 下任何 `status_code=204` 的端点都不得写 `-> None`。"""
    offenders: list[str] = []
    for path in sorted(_API_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                status_code = _decorator_status_code(decorator)
                if status_code is None or is_body_allowed_for_status_code(status_code):
                    continue
                if _has_explicit_none_response_model(decorator):
                    continue
                if _is_none_annotation(node.returns):
                    offenders.append(f"{path.name}:{node.lineno} {node.name}")
    assert offenders == [], (
        f"这些端点写了 `-> None` 却带禁体状态码：{offenders}；"
        "在 `from __future__ import annotations` 下这会被解析成 NoneType（真值），"
        "import 期即 AssertionError。请改成 `-> Response` + `return Response(status_code=204)`")
