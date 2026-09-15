"""核验诊断产物、源码摘要与历史开平数量，不代表业务缺陷已修复。"""

import ast
import hashlib
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def read_json(name):
    """统一以UTF-8解析已经保存的证据。"""
    return json.loads((HERE / name).read_text(encoding="utf-8"))


def check_scripts():
    """检查审计和复现脚本的语法及项目尺寸要求。"""
    for script in HERE.glob("*.py"):
        source = script.read_text(encoding="utf-8")
        tree = ast.parse(source)
        assert max(map(len, source.splitlines())) <= 80
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert node.end_lineno - node.lineno + 1 <= 50, node.name


def check_pairs(trades):
    """确认固定分组每笔开仓都有等量平仓，汇总口径可用。"""
    positions, paired = {}, 0
    for trade in trades:
        run = trade["run_id"]
        if trade["side"] in ("long", "short"):
            assert run not in positions
            positions[run] = trade
        else:
            opened = positions.pop(run)
            assert abs(opened["qty"] - trade["qty"]) < 1e-8
            paired += 1
    assert paired == 8 and not positions
    return paired


def main():
    """检查成功后才写入验证结果，断言失败时保留非零退出码。"""
    report = (HERE / "analysis.md").read_text(encoding="utf-8")
    evidence = read_json("desktop-evidence.json")
    repro = read_json("repro-results.json")
    for section in ["问题描述", "复现步骤", "根因分析与证据", "影响面",
                    "候选修复方案对比", "推荐方案及理由"]:
        assert section in report, section
    check_scripts()
    for path, digest in evidence["源码SHA256"].items():
        actual = hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
        assert actual == digest, path
    citations = re.findall(r"(services/[\w/]+\.py):(\d+)", report)
    for path, number in citations:
        source = (ROOT / path).read_text(encoding="utf-8")
        assert 0 < int(number) <= len(source.splitlines())
    assert repro["用例数"] == 6 and repro["超限用例数"] == 3
    assert repro["用例"][0]["原始仓位"] == 0.4
    assert repro["用例"][0]["最终仓位"] == 0.5
    assert repro["用例"][1]["最终仓位"] == 0.4
    paired = check_pairs(evidence["成交"])
    summary = evidence["摘要"]
    assert abs(summary["本地净损益USDT"] + 8.62887881) < 1e-8
    assert summary["缩减被完全抵消轮数"] == 177
    result = {
        "状态": "通过", "报告必需章节": 6,
        "源码行号引用": len(citations),
        "源码摘要一致文件数": len(evidence["源码SHA256"]),
        "复现用例数": 6, "已观察超限用例数": 3,
        "历史开平配对数": paired,
        "说明": "诊断交付完整；安全断言仍失败，未实施业务修复。",
    }
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    (HERE / "validation.json").write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
