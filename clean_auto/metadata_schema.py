from __future__ import annotations

from typing import Any


CHUNK_METADATA_SCHEMA = (
    "rag-cleaner/chunk-metadata"
)

FINAL_METADATA_SCHEMA = (
    "rag-cleaner/final-metadata"
)

REVIEW_REPORT_SCHEMA = (
    "rag-cleaner/review-report"
)

CHUNK_METADATA_SCHEMA_VERSION = 1
FINAL_METADATA_SCHEMA_VERSION = 1
REVIEW_REPORT_SCHEMA_VERSION = 1


def add_schema_identity(
    metadata: dict[str, Any],
    schema: str,
    schema_version: int,
) -> dict[str, Any]:
    """
    为 metadata 增加明确的格式标识。

    该函数会修改并返回原字典。

    原有的 version 字段暂时保留，用于兼容此前已经生成的
    metadata 和断点续跑状态。
    """
    metadata["schema"] = schema
    metadata["schema_version"] = (
        schema_version
    )

    return metadata


def is_schema(
    metadata: dict[str, Any],
    expected_schema: str,
    minimum_version: int = 1,
) -> bool:
    """
    检查 metadata 是否属于指定 schema。

    旧 metadata 可能没有 schema 字段，因此调用方应根据
    业务场景决定是否兼容旧格式。
    """
    if (
        metadata.get("schema")
        != expected_schema
    ):
        return False

    schema_version = metadata.get(
        "schema_version"
    )

    if not isinstance(
        schema_version,
        int,
    ):
        return False

    return (
        schema_version
        >= minimum_version
    )
