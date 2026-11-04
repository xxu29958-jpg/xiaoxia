"""Render a readable view from the single collected result, without recollection."""

import argparse
import json
from pathlib import Path


def table(headers: list[str], rows: list[list]) -> list[str]:
    def line(values: list) -> str:
        return (
            "| "
            + " | ".join(str(v).replace("|", "\\|").replace("\n", " ") for v in values)
            + " |"
        )

    return [
        line(headers),
        line(["---"] * len(headers)),
        *[line(row) for row in rows],
        "",
    ]


def scale_tables(scale: dict) -> list[str]:
    lines = table(
        ["口径", "文件", "字节", "物理行", "有效代码行", "纯注释行", "空行", "内容行"],
        [
            [
                name,
                *[
                    scale[name][key]
                    for key in (
                        "files",
                        "bytes",
                        "physical_lines",
                        "code_lines",
                        "comment_lines",
                        "blank_lines",
                        "content_lines",
                    )
                ],
            ]
            for name in ("Repository Total", "First-party Production Total")
        ],
    )
    lines += ["## 自维护生产领域", ""] + table(
        ["领域", "文件", "物理行", "有效代码行"],
        [
            [name, row["files"], row["physical_lines"], row["code_lines"]]
            for name, row in scale["production_domains"].items()
        ],
    )
    return lines


def render(report: dict) -> str:
    lines = [
        "# 小夏 v1.3 文件地图与工程度量",
        "",
        f"Commit: `{report['commit']}`",
        f"Tree: `{report['tree']}`",
        "",
        "统计来自固定 Git 源码。维护责任不等于原创作者身份；规模和测试数量不作为质量评分。",
        "",
        *scale_tables(report["scale"]),
    ]
    complexity = report["complexity"]["first_party_production"]
    lines += ["## 复杂度与实际热点", "", complexity["scope"], ""]
    lines += table(
        ["C901 函数", "超过 10", "超过 15", "最大", "P50", "P90", "P95"],
        [
            [
                complexity["c901"][key]
                for key in (
                    "count",
                    "above_10",
                    "above_15",
                    "maximum",
                    "p50",
                    "p90",
                    "p95",
                )
            ]
        ],
    )
    lines += table(
        ["文件", "最大 C901", "历史修改", "直接消费者", "状态权威"],
        [
            [
                h["path"],
                h["maximum_complexity"],
                h["changes"],
                h["consumers"],
                h["state_authority"],
            ]
            for h in report["history"]["hotspots"][:12]
        ],
    )
    lines += [
        "## 影响与覆盖",
        "",
        "以下是源码、共享注册状态和 HTTP 地址的反向可达消费者；默认配置不证明运行时激活，是否必须修改仍需审查对应规则。",
        "",
    ]
    lines += table(
        ["代表改动", "生产消费者", "测试组", "缺失入口"],
        [
            [
                s["id"],
                len(s["production_consumers"]),
                ", ".join(s["test_groups"]),
                ", ".join(s["missing_seeds"]),
            ]
            for s in report["architecture"]["representative_changes"]
        ],
    )
    lines += [
        f"源码依赖边：{report['influence']['edges']}；HTTP 服务影响边：{report['influence'].get('protocol_edges', 0)}；源码依赖环组：{len(report['architecture']['cycles'])}；显式关系缺口文件：{len(report['influence']['map_gaps'])}。",
        "",
        "解析到的注册与实际运行激活分别标注；重复代码、同名表、多写入者是调查线索。未覆盖的动态关系与产品旅程不能算通过。",
        "",
    ]
    if delta := report.get("delta"):
        lines += [
            "## 固定基线差分",
            "",
            f"Base: `{delta['base_commit']}` / `{delta['base_tree']}`",
            "",
            f"物理行新增 {delta['added_lines']}、删除 {delta['deleted_lines']}、净变化 {delta['net_lines']}；逐文件改名／排版／生成物与绝对基线保留在 JSON。",
            "",
        ]
    lines += [
        "## 工具与规则",
        "",
        "```json",
        json.dumps(report["measurement"], ensure_ascii=False, indent=2),
        "```",
        "",
        "完整类别、语言、符号、来源对照、例外历史和消费者路径以同一 JSON 为准。CI 的实际执行、耗时、失败、跳过和漏检对照见同候选 result.json 与各组回执。",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    print(render(json.loads(args.report.read_text(encoding="utf-8"))))


if __name__ == "__main__":
    main()
