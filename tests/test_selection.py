from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

import clean_auto.pipeline as pipeline
from clean_auto.config import (
    FilePlan,
    ProcessOutcome,
    ProcessStats,
    parse_args,
)
from clean_auto.selection import (
    load_selection_paths,
    resolve_selection_file,
)


def _write_prompt(base_dir: Path) -> None:
    (base_dir / "prompt.md").write_bytes(
        "保留 Markdown 结构。\n".encode("utf-8")
    )


def _write_markdown(path: Path, text: str = "正文\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def _write_selection(
    path: Path,
    paths: list[str],
    *,
    schema: str = "rag-cleaner/selection",
    schema_version: int = 1,
    source: object | None = None,
    bom: bool = False,
) -> None:
    if source is None:
        source = {"kind": "files", "root": None}

    payload = {
        "schema": schema,
        "schema_version": schema_version,
        "source": source,
        "paths": paths,
    }
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((b"\xef\xbb\xbf" if bom else b"") + raw)


def _exit_code(callable_: Callable[[], object]) -> int:
    try:
        callable_()
    except SystemExit as exc:
        return int(exc.code)
    return 0


def _record_success(
    calls: list[Path],
) -> Callable[..., ProcessOutcome]:
    def process_file(
        **kwargs: object,
    ) -> ProcessOutcome:
        plan = kwargs["plan"]
        assert isinstance(plan, FilePlan)
        calls.append(plan.relative_path)
        return ProcessOutcome(
            stats=ProcessStats(
                total_parts=1,
                success_parts=1,
            ),
            consecutive_failures=0,
        )

    return process_file


def test_parse_args_accepts_selection_file() -> None:
    args = parse_args(
        ["--dry-run", "--selection-file", "configs/selected.json"]
    )

    assert args.selection_file == "configs/selected.json"


def test_selection_preserves_first_occurrence_and_subdirectory_names(
    tmp_path: Path,
) -> None:
    input_dir = tmp_path / "input"
    _write_markdown(input_dir / "same.md")
    _write_markdown(input_dir / "法规" / "same.md")
    _write_markdown(input_dir / "真题" / "2025.md")
    selection_file = tmp_path / "selection.json"
    _write_selection(
        selection_file,
        [
            "法规/same.md",
            "same.md",
            "法规/same.md",
            "真题/2025.md",
        ],
    )

    paths = load_selection_paths(selection_file, input_dir)

    assert paths == [
        (input_dir / "法规" / "same.md").resolve(),
        (input_dir / "same.md").resolve(),
        (input_dir / "真题" / "2025.md").resolve(),
    ]


def test_selection_accepts_utf8_bom(
    tmp_path: Path,
) -> None:
    input_dir = tmp_path / "input"
    _write_markdown(input_dir / "法规" / "安全生产法.md")
    selection_file = tmp_path / "selection.json"
    _write_selection(
        selection_file,
        ["法规/安全生产法.md"],
        bom=True,
    )

    assert load_selection_paths(selection_file, input_dir) == [
        (input_dir / "法规" / "安全生产法.md").resolve()
    ]


def test_selection_keeps_missing_input_path_for_planning_isolation(
    tmp_path: Path,
) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    selection_file = tmp_path / "selection.json"
    _write_selection(selection_file, ["missing.md"])

    assert load_selection_paths(selection_file, input_dir) == [
        (input_dir / "missing.md").resolve()
    ]


@pytest.mark.parametrize(
    "selected_path",
    [
        "/outside.md",
        "//server/share.md",
        "C:/outside.md",
        "C:outside.md",
        ".",
        "..",
        "../outside.md",
        "nested/../valid.md",
        "nested\\valid.md",
        "notes.txt",
        "old_cleaned.md",
    ],
)
def test_selection_rejects_unsafe_or_non_markdown_paths(
    tmp_path: Path,
    selected_path: str,
) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    selection_file = tmp_path / "selection.json"
    _write_selection(selection_file, [selected_path])

    with pytest.raises(RuntimeError):
        load_selection_paths(selection_file, input_dir)


def test_selection_rejects_symbolic_link_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_dir = tmp_path / "input"
    linked_path = input_dir / "linked.md"
    _write_markdown(linked_path)
    selection_file = tmp_path / "selection.json"
    _write_selection(selection_file, ["linked.md"])
    original_is_symlink = Path.is_symlink

    def is_symlink(path: Path) -> bool:
        return path.name == "linked.md" or original_is_symlink(path)

    monkeypatch.setattr(Path, "is_symlink", is_symlink)

    with pytest.raises(RuntimeError, match="符号链接"):
        load_selection_paths(selection_file, input_dir)


@pytest.mark.parametrize(
    "schema,schema_version,source",
    [
        ("other/selection", 1, {"kind": "files", "root": None}),
        ("rag-cleaner/selection", 2, {"kind": "files", "root": None}),
        ("rag-cleaner/selection", 1, {"kind": "directory", "root": None}),
    ],
)
def test_selection_rejects_unknown_schema(
    tmp_path: Path,
    schema: str,
    schema_version: int,
    source: object,
) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    selection_file = tmp_path / "selection.json"
    _write_selection(
        selection_file,
        ["valid.md"],
        schema=schema,
        schema_version=schema_version,
        source=source,
    )

    with pytest.raises(RuntimeError):
        load_selection_paths(selection_file, input_dir)


def test_selection_rejects_invalid_json_without_encoding_fallback(
    tmp_path: Path,
) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    selection_file = tmp_path / "selection.json"
    selection_file.write_bytes(b"\xffnot-json")

    with pytest.raises(RuntimeError):
        load_selection_paths(selection_file, input_dir)


def test_invalid_selection_fails_before_processing_any_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_markdown(tmp_path / "input" / "valid.md")
    selection_file = tmp_path / "selected.json"
    _write_selection(
        selection_file,
        ["valid.md", "../outside.md"],
    )
    monkeypatch.setattr(
        pipeline,
        "process_file",
        lambda **_kwargs: pytest.fail("must not process"),
    )

    with pytest.raises(RuntimeError):
        pipeline.main(
            [
                "--dry-run",
                "--base-dir",
                str(tmp_path),
                "--selection-file",
                "selected.json",
            ]
        )

    assert not (tmp_path / "output").exists()
    assert not (tmp_path / "logs").exists()


def test_resolve_selection_file_uses_base_dir_for_relative_paths(
    tmp_path: Path,
) -> None:
    selection_file = tmp_path / "configs" / "selected.json"
    selection_file.parent.mkdir()
    selection_file.write_bytes(b"{}")

    assert resolve_selection_file(
        "configs/selected.json",
        tmp_path,
    ) == selection_file.resolve()


def test_empty_selection_is_successful_no_op_before_directories_or_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_prompt(tmp_path)
    selection_file = tmp_path / "selected.json"
    _write_selection(selection_file, [])

    monkeypatch.setattr(
        pipeline,
        "acquire_lock",
        lambda *_args, **_kwargs: pytest.fail("must not lock"),
    )
    monkeypatch.setattr(
        pipeline,
        "process_file",
        lambda **_kwargs: pytest.fail("must not process"),
    )
    monkeypatch.setattr(
        pipeline,
        "ApiClient",
        lambda *_args, **_kwargs: pytest.fail("must not create API client"),
    )

    assert _exit_code(
        lambda: pipeline.main(
            [
                "--base-dir",
                str(tmp_path),
                "--selection-file",
                "selected.json",
            ]
        )
    ) == 0
    assert not (tmp_path / "output").exists()
    assert not (tmp_path / "logs").exists()


def test_selection_pipeline_uses_list_order_and_max_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_prompt(tmp_path)
    _write_markdown(tmp_path / "input" / "third.md")
    _write_markdown(tmp_path / "input" / "first.md")
    _write_markdown(tmp_path / "input" / "nested" / "second.md")
    selection_file = tmp_path / "configs" / "selected.json"
    _write_selection(
        selection_file,
        ["third.md", "nested/second.md", "first.md"],
    )
    calls: list[Path] = []
    monkeypatch.setattr(
        pipeline,
        "process_file",
        _record_success(calls),
    )

    assert _exit_code(
        lambda: pipeline.main(
            [
                "--dry-run",
                "--base-dir",
                str(tmp_path),
                "--selection-file",
                "configs/selected.json",
                "--max-files",
                "2",
            ]
        )
    ) == 0
    assert calls == [
        Path("third.md"),
        Path("nested/second.md"),
    ]


def test_missing_selection_entry_isolated_while_valid_file_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_prompt(tmp_path)
    _write_markdown(tmp_path / "input" / "valid.md")
    selection_file = tmp_path / "selected.json"
    _write_selection(
        selection_file,
        ["missing.md", "valid.md"],
    )
    calls: list[Path] = []
    monkeypatch.setattr(
        pipeline,
        "process_file",
        _record_success(calls),
    )

    assert _exit_code(
        lambda: pipeline.main(
            [
                "--dry-run",
                "--base-dir",
                str(tmp_path),
                "--selection-file",
                "selected.json",
            ]
        )
    ) == 2
    assert calls == [Path("valid.md")]
    assert "planning_failed" in (
        tmp_path / "logs" / "batch.jsonl"
    ).read_bytes().decode("utf-8")


def test_selection_dry_run_avoids_api_and_output_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_prompt(tmp_path)
    _write_markdown(tmp_path / "input" / "selected.md")
    selection_file = tmp_path / "selected.json"
    _write_selection(selection_file, ["selected.md"])

    monkeypatch.setattr(
        pipeline,
        "ApiClient",
        lambda *_args, **_kwargs: pytest.fail("must not create API client"),
    )
    monkeypatch.setattr(
        pipeline,
        "acquire_lock",
        lambda *_args, **_kwargs: pytest.fail("must not lock"),
    )

    assert _exit_code(
        lambda: pipeline.main(
            [
                "--dry-run",
                "--base-dir",
                str(tmp_path),
                "--selection-file",
                "selected.json",
            ]
        )
    ) == 0
    assert not list((tmp_path / "output").rglob("*.md"))
    assert not list((tmp_path / "logs").glob("batches/*.json"))


def test_directory_scan_mode_keeps_existing_sorted_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_prompt(tmp_path)
    _write_markdown(tmp_path / "input" / "z.md")
    _write_markdown(tmp_path / "input" / "b.md")
    _write_markdown(tmp_path / "input" / "nested" / "a.md")
    calls: list[Path] = []
    monkeypatch.setattr(
        pipeline,
        "process_file",
        _record_success(calls),
    )

    assert _exit_code(
        lambda: pipeline.main(
            ["--dry-run", "--base-dir", str(tmp_path)]
        )
    ) == 0
    assert calls == [
        Path("b.md"),
        Path("nested/a.md"),
        Path("z.md"),
    ]
