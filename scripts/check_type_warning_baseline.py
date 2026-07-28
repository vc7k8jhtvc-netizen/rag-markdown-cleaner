from __future__ import annotations

import argparse
import re
import subprocess
import sys


SUMMARY_PATTERN = re.compile(
    r"(?P<errors>\d+) errors?, "
    r"(?P<warnings>\d+) warnings?, "
    r"(?P<notes>\d+) notes?"
)


def configure_safe_output() -> None:
    """避免诊断中的特殊空白字符令 Windows 控制台输出失败。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(errors="backslashreplace")


def main() -> int:
    configure_safe_output()
    parser = argparse.ArgumentParser(
        description="阻止 basedpyright 错误或超过基线的新增警告。",
    )
    parser.add_argument(
        "--max-warnings",
        type=int,
        required=True,
    )
    args = parser.parse_args()

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "basedpyright",
            "clean_auto",
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output = completed.stdout + completed.stderr
    print(output, end="")

    summaries = list(SUMMARY_PATTERN.finditer(output))
    if not summaries:
        print(
            "无法读取 basedpyright 汇总，拒绝通过类型门禁。",
            file=sys.stderr,
        )
        return 2

    summary = summaries[-1]
    errors = int(summary.group("errors"))
    warnings = int(summary.group("warnings"))

    if errors:
        print(
            f"类型检查存在 {errors} 个错误。",
            file=sys.stderr,
        )
        return 1

    if warnings > args.max_warnings:
        print(
            "basedpyright 警告超过基线："
            f"{warnings} > {args.max_warnings}",
            file=sys.stderr,
        )
        return 1

    print(
        "basedpyright 门禁通过："
        f"0 errors，{warnings}/{args.max_warnings} warnings。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
