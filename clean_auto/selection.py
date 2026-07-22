from __future__ import annotations

import json
import os
import re
from pathlib import Path, PurePosixPath

from .chunking import ensure_path_inside, is_cleaned_file


SELECTION_SCHEMA = "rag-cleaner/selection"
SELECTION_SCHEMA_VERSION = 1
WINDOWS_DRIVE_PATTERN = re.compile(r"^[A-Za-z]:")


def resolve_selection_file(
    value: str,
    base_dir: Path,
) -> Path:
    path = Path(value).expanduser()

    if not path.is_absolute():
        path = base_dir / path

    resolved = path.resolve()

    if not resolved.is_file():
        raise RuntimeError(
            f"找不到选择清单文件：{resolved}"
        )

    return resolved


def _load_selection_data(
    selection_file: Path,
) -> dict[str, object]:
    try:
        raw = selection_file.read_bytes()
    except OSError as exc:
        raise RuntimeError(
            f"无法读取选择清单文件：{selection_file}"
        ) from exc

    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise RuntimeError(
            "选择清单必须使用 UTF-8 编码："
            f"{selection_file}"
        ) from exc

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"选择清单不是有效 JSON：{selection_file}"
        ) from exc

    if not isinstance(data, dict):
        raise RuntimeError(
            "选择清单根节点必须是 JSON 对象"
        )

    return data


def _validate_selection_schema(
    data: dict[str, object],
) -> list[object]:
    if data.get("schema") != SELECTION_SCHEMA:
        raise RuntimeError(
            "选择清单 schema 必须是 "
            f"{SELECTION_SCHEMA!r}"
        )

    version = data.get("schema_version")

    if (
        type(version) is not int
        or version != SELECTION_SCHEMA_VERSION
    ):
        raise RuntimeError(
            "不支持的选择清单 schema_version："
            f"{version!r}"
        )

    if data.get("source") != {
        "kind": "files",
        "root": None,
    }:
        raise RuntimeError(
            "选择清单 source 必须是 "
            '{"kind": "files", "root": null}'
        )

    paths = data.get("paths")

    if not isinstance(paths, list):
        raise RuntimeError(
            "选择清单 paths 必须是 JSON 数组"
        )

    return paths


def _validate_relative_path(value: object) -> tuple[str, ...]:
    if not isinstance(value, str) or not value:
        raise RuntimeError(
            "选择清单中的路径必须是非空字符串"
        )

    if "\\" in value:
        raise RuntimeError(
            "选择清单路径必须使用 POSIX 风格的 / 分隔符："
            f"{value!r}"
        )

    pure_path = PurePosixPath(value)
    parts = value.split("/")

    if (
        pure_path.is_absolute()
        or WINDOWS_DRIVE_PATTERN.match(value)
        or any(
            part in {"", ".", ".."}
            for part in parts
        )
    ):
        raise RuntimeError(
            "选择清单路径必须是 input 内的规范相对路径："
            f"{value!r}"
        )

    return tuple(parts)


def _reject_symlink_components(
    input_dir: Path,
    parts: tuple[str, ...],
    selected_path: str,
) -> None:
    current = input_dir

    if current.is_symlink():
        raise RuntimeError(
            f"选择路径不允许包含符号链接：{selected_path}"
        )

    for part in parts:
        current = current / part

        if current.is_symlink():
            raise RuntimeError(
                "选择路径不允许包含符号链接："
                f"{selected_path}"
            )


def load_selection_paths(
    selection_file: Path,
    input_dir: Path,
) -> list[Path]:
    data = _load_selection_data(selection_file)
    raw_paths = _validate_selection_schema(data)
    selected_paths: list[Path] = []
    seen: set[str] = set()

    for raw_path in raw_paths:
        parts = _validate_relative_path(raw_path)
        relative_path = PurePosixPath(*parts)
        selected_path = relative_path.as_posix()
        candidate = input_dir.joinpath(*parts)

        if candidate.suffix.lower() != ".md":
            raise RuntimeError(
                "选择清单只允许 Markdown 文件："
                f"{selected_path}"
            )

        if is_cleaned_file(candidate):
            raise RuntimeError(
                "选择清单不允许 cleaned 输出文件："
                f"{selected_path}"
            )

        _reject_symlink_components(
            input_dir,
            parts,
            selected_path,
        )

        try:
            resolved_candidate = ensure_path_inside(
                candidate,
                input_dir,
                "选择路径",
            )
        except OSError as exc:
            raise RuntimeError(
                f"无法解析选择路径：{selected_path}"
            ) from exc

        if (
            candidate.exists()
            and not candidate.is_file()
        ):
            raise RuntimeError(
                "选择路径不是普通文件："
                f"{selected_path}"
            )

        identity = os.path.normcase(
            str(resolved_candidate)
        )

        if identity in seen:
            continue

        seen.add(identity)
        selected_paths.append(resolved_candidate)

    return selected_paths
