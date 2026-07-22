from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from clean_auto.config import append_log
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


def test_append_log_is_thread_safe_jsonl(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    record_count = 100

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [
            executor.submit(
                append_log,
                log_dir,
                f"file-{index}.md",
                "success",
                {"index": index},
            )
            for index in range(record_count)
        ]
        for future in futures:
            future.result()

    lines = (log_dir / "batch.jsonl").read_text(
        encoding="utf-8"
    ).splitlines()
    records = [json.loads(line) for line in lines]
    assert len(records) == record_count
    assert {record["detail"]["index"] for record in records} == set(
        range(record_count)
    )
