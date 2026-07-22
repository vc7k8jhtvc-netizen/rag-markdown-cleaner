from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from .config import atomic_write_json, compact_error, now_iso


BATCH_MANIFEST_SCHEMA = "rag-cleaner/batch-manifest"
BATCH_MANIFEST_SCHEMA_VERSION = 1
BATCH_LATEST_SCHEMA = "rag-cleaner/batch-latest"
BATCH_LATEST_SCHEMA_VERSION = 1

FILE_STATUSES = (
    "pending",
    "running",
    "succeeded",
    "failed",
    "skipped",
    "interrupted",
)
BATCH_STATUSES = (
    "running",
    "completed",
    "completed_with_failures",
    "incomplete",
    "stopped",
)
SELECTION_SOURCES = (
    "scan",
    "selection_file",
    "resume",
    "retry_failed",
)

BATCH_ID_PATTERN = re.compile(
    r"^[0-9]{8}T[0-9]{12}Z-[0-9a-f]{12}$"
)
WINDOWS_DRIVE_PATTERN = re.compile(r"^[A-Za-z]:")
_UNSET = object()


def batches_dir(log_dir: Path) -> Path:
    return log_dir / "batches"


def validate_batch_id(batch_id: object) -> str:
    if (
        not isinstance(batch_id, str)
        or not BATCH_ID_PATTERN.fullmatch(batch_id)
    ):
        raise RuntimeError(
            f"无效 batch ID：{batch_id!r}"
        )

    return batch_id


def manifest_path(
    log_dir: Path,
    batch_id: object,
) -> Path:
    return batches_dir(log_dir) / (
        f"{validate_batch_id(batch_id)}.json"
    )


def latest_path(log_dir: Path) -> Path:
    return batches_dir(log_dir) / "latest.json"


def generate_batch_id() -> str:
    timestamp = datetime.now(
        timezone.utc
    ).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{timestamp}-{uuid.uuid4().hex[:12]}"


def _timestamp(value: str | None) -> str:
    return value if value is not None else now_iso()


def _validate_relative_path(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise RuntimeError(
            "批次文件路径必须是非空字符串"
        )

    if "\\" in value:
        raise RuntimeError(
            "批次文件路径必须使用 POSIX 分隔符"
        )

    path = PurePosixPath(value)
    parts = value.split("/")

    if (
        path.is_absolute()
        or WINDOWS_DRIVE_PATTERN.match(value)
        or any(part in {"", ".", ".."} for part in parts)
        or path.suffix.lower() != ".md"
        or path.stem.lower().endswith("_cleaned")
    ):
        raise RuntimeError(
            f"批次文件路径无效：{value!r}"
        )

    return path.as_posix()


def _empty_counts(total: int) -> dict[str, int]:
    return {
        "total": total,
        **{status: 0 for status in FILE_STATUSES},
    }


def recount(manifest: dict[str, Any]) -> dict[str, int]:
    files = manifest.get("files")

    if not isinstance(files, list):
        raise RuntimeError(
            "批次 manifest files 必须是数组"
        )

    counts = _empty_counts(len(files))

    for item in files:
        if not isinstance(item, dict):
            raise RuntimeError(
                "批次 manifest 文件项必须是对象"
            )

        status = item.get("status")

        if status not in FILE_STATUSES:
            raise RuntimeError(
                f"批次文件状态无效：{status!r}"
            )

        counts[status] += 1

    manifest["counts"] = counts
    return counts


def _validate_optional_text(
    value: object,
    description: str,
) -> None:
    if value is not None and not isinstance(value, str):
        raise RuntimeError(
            f"{description} 必须是字符串或 null"
        )


def _validate_file_item(item: object) -> str:
    if not isinstance(item, dict):
        raise RuntimeError(
            "批次 manifest 文件项必须是对象"
        )

    expected_fields = {
        "path",
        "status",
        "source_sha256",
        "error",
        "attempts",
        "started_at",
        "finished_at",
    }

    if set(item) != expected_fields:
        raise RuntimeError(
            "批次 manifest 文件项包含未知或缺失字段"
        )

    path = _validate_relative_path(item.get("path"))
    status = item.get("status")

    if status not in FILE_STATUSES:
        raise RuntimeError(
            f"批次文件状态无效：{status!r}"
        )

    source_sha256 = item.get("source_sha256")

    if (
        source_sha256 is not None
        and (
            not isinstance(source_sha256, str)
            or not re.fullmatch(
                r"[0-9a-f]{64}",
                source_sha256,
            )
        )
    ):
        raise RuntimeError(
            "source_sha256 必须是 64 位十六进制 SHA-256 或 null"
        )
    _validate_optional_text(item.get("error"), "error")
    _validate_optional_text(
        item.get("started_at"),
        "started_at",
    )
    _validate_optional_text(
        item.get("finished_at"),
        "finished_at",
    )

    attempts = item.get("attempts")

    if (
        type(attempts) is not int
        or attempts < 0
    ):
        raise RuntimeError(
            "批次文件 attempts 必须是非负整数"
        )

    return path


def validate_manifest(
    manifest: object,
    expected_batch_id: str | None = None,
) -> dict[str, Any]:
    if not isinstance(manifest, dict):
        raise RuntimeError(
            "批次 manifest 根节点必须是 JSON 对象"
        )

    if manifest.get("schema") != BATCH_MANIFEST_SCHEMA:
        raise RuntimeError(
            "批次 manifest schema 无效"
        )

    if (
        manifest.get("schema_version")
        != BATCH_MANIFEST_SCHEMA_VERSION
    ):
        raise RuntimeError(
            "不支持的批次 manifest schema_version"
        )

    expected_fields = {
        "schema",
        "schema_version",
        "batch_id",
        "created_at",
        "updated_at",
        "completed_at",
        "status",
        "workers",
        "selection",
        "counts",
        "files",
    }

    if set(manifest) != expected_fields:
        raise RuntimeError(
            "批次 manifest 包含未知或缺失字段"
        )

    batch_id = validate_batch_id(
        manifest.get("batch_id")
    )

    if (
        expected_batch_id is not None
        and batch_id != expected_batch_id
    ):
        raise RuntimeError(
            "批次 manifest 的 batch_id 与文件名不一致"
        )

    for field in (
        "created_at",
        "updated_at",
    ):
        value = manifest.get(field)

        if not isinstance(value, str) or not value:
            raise RuntimeError(
                f"批次 manifest {field} 无效"
            )

    _validate_optional_text(
        manifest.get("completed_at"),
        "completed_at",
    )

    if manifest.get("status") not in BATCH_STATUSES:
        raise RuntimeError(
            "批次 manifest 顶层状态无效"
        )

    if manifest.get("workers") != 1:
        raise RuntimeError(
            "当前批次 manifest workers 必须为 1"
        )

    selection = manifest.get("selection")

    if not isinstance(selection, dict):
        raise RuntimeError(
            "批次 manifest selection 必须是对象"
        )

    if set(selection) != {
        "source",
        "selection_file",
        "parent_batch_id",
    }:
        raise RuntimeError(
            "批次 manifest selection 包含未知或缺失字段"
        )

    if selection.get("source") not in SELECTION_SOURCES:
        raise RuntimeError(
            "批次 manifest selection source 无效"
        )

    _validate_optional_text(
        selection.get("selection_file"),
        "selection_file",
    )
    parent_batch_id = selection.get(
        "parent_batch_id"
    )

    if parent_batch_id is not None:
        validate_batch_id(parent_batch_id)

    files = manifest.get("files")

    if not isinstance(files, list):
        raise RuntimeError(
            "批次 manifest files 必须是数组"
        )

    seen: set[str] = set()

    for item in files:
        path = _validate_file_item(item)

        if path in seen:
            raise RuntimeError(
                f"批次 manifest 包含重复文件：{path}"
            )

        seen.add(path)

    stored_counts = manifest.get("counts")
    calculated = _empty_counts(len(files))

    for item in files:
        calculated[item["status"]] += 1

    if stored_counts != calculated:
        raise RuntimeError(
            "批次 manifest counts 与 files 状态不一致"
        )

    return manifest


def _read_json(
    path: Path,
    description: str,
) -> object:
    try:
        raw = path.read_bytes()
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"{description}不存在：{path}"
        ) from exc
    except OSError as exc:
        raise RuntimeError(
            f"无法读取{description}：{path}"
        ) from exc

    try:
        return json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise RuntimeError(
            f"{description}必须使用 UTF-8 编码：{path}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"{description}不是有效 JSON：{path}"
        ) from exc


def write_manifest(
    log_dir: Path,
    manifest: dict[str, Any],
    *,
    timestamp: str | None = None,
) -> None:
    manifest["updated_at"] = _timestamp(timestamp)
    recount(manifest)
    validate_manifest(manifest)
    atomic_write_json(
        manifest_path(log_dir, manifest["batch_id"]),
        manifest,
    )


def write_latest(
    log_dir: Path,
    batch_id: str,
    *,
    timestamp: str | None = None,
) -> None:
    updated_at = _timestamp(timestamp)
    pointer = {
        "schema": BATCH_LATEST_SCHEMA,
        "schema_version": BATCH_LATEST_SCHEMA_VERSION,
        "batch_id": validate_batch_id(batch_id),
        "updated_at": updated_at,
    }
    atomic_write_json(latest_path(log_dir), pointer)


def create_manifest(
    log_dir: Path,
    relative_paths: list[str],
    selection_source: str,
    selection_file: str | None = None,
    parent_batch_id: str | None = None,
    *,
    batch_id: str | None = None,
    timestamp: str | None = None,
) -> dict[str, Any]:
    if selection_source not in SELECTION_SOURCES:
        raise RuntimeError(
            f"批次选择来源无效：{selection_source!r}"
        )

    if parent_batch_id is not None:
        validate_batch_id(parent_batch_id)

    selected_id = validate_batch_id(
        batch_id or generate_batch_id()
    )
    created_at = _timestamp(timestamp)
    files: list[dict[str, Any]] = []
    seen: set[str] = set()

    for relative_path in relative_paths:
        path = _validate_relative_path(relative_path)

        if path in seen:
            raise RuntimeError(
                f"批次文件路径重复：{path}"
            )

        seen.add(path)
        files.append(
            {
                "path": path,
                "status": "pending",
                "source_sha256": None,
                "error": None,
                "attempts": 0,
                "started_at": None,
                "finished_at": None,
            }
        )

    manifest: dict[str, Any] = {
        "schema": BATCH_MANIFEST_SCHEMA,
        "schema_version": BATCH_MANIFEST_SCHEMA_VERSION,
        "batch_id": selected_id,
        "created_at": created_at,
        "updated_at": created_at,
        "completed_at": None,
        "status": "running",
        "workers": 1,
        "selection": {
            "source": selection_source,
            "selection_file": selection_file,
            "parent_batch_id": parent_batch_id,
        },
        "counts": _empty_counts(len(files)),
        "files": files,
    }
    recount(manifest)
    validate_manifest(manifest)
    target_path = manifest_path(
        log_dir,
        selected_id,
    )

    if target_path.exists():
        raise RuntimeError(
            f"批次 manifest 已存在：{target_path}"
        )

    atomic_write_json(
        target_path,
        manifest,
    )
    write_latest(
        log_dir,
        selected_id,
        timestamp=created_at,
    )
    return manifest


def load_manifest(
    log_dir: Path,
    batch_id: str,
) -> dict[str, Any]:
    selected_id = validate_batch_id(batch_id)
    data = _read_json(
        manifest_path(log_dir, selected_id),
        "批次 manifest",
    )
    return validate_manifest(data, selected_id)


def _load_latest_id(log_dir: Path) -> str:
    path = latest_path(log_dir)
    data = _read_json(path, "latest 批次指针")

    if not isinstance(data, dict):
        raise RuntimeError(
            "latest 批次指针根节点必须是 JSON 对象"
        )

    if data.get("schema") != BATCH_LATEST_SCHEMA:
        raise RuntimeError(
            "latest 批次指针 schema 无效"
        )

    if (
        data.get("schema_version")
        != BATCH_LATEST_SCHEMA_VERSION
    ):
        raise RuntimeError(
            "不支持的 latest 批次指针 schema_version"
        )

    if set(data) != {
        "schema",
        "schema_version",
        "batch_id",
        "updated_at",
    }:
        raise RuntimeError(
            "latest 批次指针包含未知或缺失字段"
        )

    updated_at = data.get("updated_at")

    if not isinstance(updated_at, str) or not updated_at:
        raise RuntimeError(
            "latest 批次指针 updated_at 无效"
        )

    return validate_batch_id(data.get("batch_id"))


def load_resume_manifest(
    log_dir: Path,
    requested: str,
) -> dict[str, Any]:
    batch_id = (
        _load_latest_id(log_dir)
        if requested == "latest"
        else validate_batch_id(requested)
    )
    return load_manifest(log_dir, batch_id)


def _find_file(
    manifest: dict[str, Any],
    relative_path: str,
) -> dict[str, Any]:
    normalized = _validate_relative_path(relative_path)

    for item in manifest["files"]:
        if item["path"] == normalized:
            return item

    raise RuntimeError(
        f"批次 manifest 中找不到文件：{normalized}"
    )


def file_status(
    manifest: dict[str, Any],
    relative_path: str,
) -> str:
    return _find_file(
        manifest,
        relative_path,
    )["status"]


def update_file(
    log_dir: Path,
    manifest: dict[str, Any],
    relative_path: str,
    *,
    status: str,
    source_sha256: object = _UNSET,
    error: object = _UNSET,
    increment_attempts: bool = False,
    timestamp: str | None = None,
) -> None:
    if status not in FILE_STATUSES:
        raise RuntimeError(
            f"批次文件状态无效：{status!r}"
        )

    changed_at = _timestamp(timestamp)
    item = _find_file(manifest, relative_path)
    item["status"] = status

    if source_sha256 is not _UNSET:
        if (
            source_sha256 is not None
            and (
                not isinstance(source_sha256, str)
                or not re.fullmatch(
                    r"[0-9a-f]{64}",
                    source_sha256,
                )
            )
        ):
            raise RuntimeError(
                "source_sha256 必须是 64 位十六进制 SHA-256 或 null"
            )
        item["source_sha256"] = source_sha256

    if error is not _UNSET:
        if error is not None and not isinstance(error, str):
            raise RuntimeError(
                "error 必须是字符串或 null"
            )
        item["error"] = (
            compact_error(RuntimeError(error))
            if isinstance(error, str)
            else None
        )

    if increment_attempts:
        item["attempts"] += 1

    if status == "running":
        item["started_at"] = changed_at
        item["finished_at"] = None
        item["error"] = None
    elif status in {
        "succeeded",
        "failed",
        "skipped",
        "interrupted",
    }:
        item["finished_at"] = changed_at

        if status in {"succeeded", "skipped"}:
            item["error"] = None

    write_manifest(
        log_dir,
        manifest,
        timestamp=changed_at,
    )


def prepare_resume(
    log_dir: Path,
    manifest: dict[str, Any],
    *,
    timestamp: str | None = None,
) -> None:
    changed_at = _timestamp(timestamp)

    for item in manifest["files"]:
        if item["status"] == "running":
            item["status"] = "interrupted"
            item["error"] = "上次运行异常中断"
            item["finished_at"] = changed_at

    manifest["selection"]["source"] = "resume"
    manifest["status"] = "running"
    manifest["completed_at"] = None
    write_manifest(
        log_dir,
        manifest,
        timestamp=changed_at,
    )
    write_latest(
        log_dir,
        manifest["batch_id"],
        timestamp=changed_at,
    )


def resumable_paths(
    manifest: dict[str, Any],
) -> list[str]:
    return [
        item["path"]
        for item in manifest["files"]
        if item["status"] in {
            "pending",
            "interrupted",
        }
    ]


def finalize_manifest(
    log_dir: Path,
    manifest: dict[str, Any],
    *,
    stopped: bool,
    timestamp: str | None = None,
) -> None:
    changed_at = _timestamp(timestamp)
    counts = recount(manifest)

    if stopped:
        status = "stopped"
    elif any(
        counts[item] > 0
        for item in (
            "pending",
            "running",
            "interrupted",
        )
    ):
        status = "incomplete"
    elif counts["failed"] > 0:
        status = "completed_with_failures"
    else:
        status = "completed"

    manifest["status"] = status
    manifest["completed_at"] = (
        changed_at
        if status in {
            "completed",
            "completed_with_failures",
        }
        else None
    )
    write_manifest(
        log_dir,
        manifest,
        timestamp=changed_at,
    )
