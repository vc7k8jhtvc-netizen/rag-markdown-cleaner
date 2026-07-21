from __future__ import annotations

from pathlib import Path

from clean_auto.processor import (
    safe_log_path,
)


def test_path_inside_project_becomes_relative(
    tmp_path: Path,
) -> None:
    base_dir = tmp_path / "project"

    output_path = (
        base_dir
        / "output"
        / "教材"
        / "part_001.md"
    )

    result = safe_log_path(
        output_path,
        base_dir,
    )

    assert result == (
        "output/教材/part_001.md"
    )


def test_path_outside_project_only_uses_name(
    tmp_path: Path,
) -> None:
    base_dir = (
        tmp_path
        / "project"
    )

    outside_path = (
        tmp_path
        / "private"
        / "secret.md"
    )

    result = safe_log_path(
        outside_path,
        base_dir,
    )

    assert result == "secret.md"
