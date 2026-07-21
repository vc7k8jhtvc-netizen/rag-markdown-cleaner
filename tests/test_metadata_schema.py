from __future__ import annotations

from clean_auto.metadata_schema import (
    CHUNK_METADATA_SCHEMA,
    CHUNK_METADATA_SCHEMA_VERSION,
    FINAL_METADATA_SCHEMA,
    FINAL_METADATA_SCHEMA_VERSION,
    REVIEW_REPORT_SCHEMA,
    REVIEW_REPORT_SCHEMA_VERSION,
    add_schema_identity,
    is_schema,
)


def test_schema_names_are_distinct() -> None:
    """
    三种 metadata 必须具有不同的 schema 名称。
    """
    schemas = {
        CHUNK_METADATA_SCHEMA,
        FINAL_METADATA_SCHEMA,
        REVIEW_REPORT_SCHEMA,
    }

    assert len(schemas) == 3


def test_schema_versions_are_positive() -> None:
    """
    schema 版本必须是正整数。
    """
    versions = (
        CHUNK_METADATA_SCHEMA_VERSION,
        FINAL_METADATA_SCHEMA_VERSION,
        REVIEW_REPORT_SCHEMA_VERSION,
    )

    assert all(
        isinstance(version, int)
        and version >= 1
        for version in versions
    )


def test_add_chunk_schema_identity() -> None:
    """
    分片 metadata 可以加入明确的 schema 标识，
    同时保留旧 version 字段。
    """
    metadata = {
        "version": 2,
        "status": "completed",
        "output_chars": 100,
    }

    result = add_schema_identity(
        metadata=metadata,
        schema=CHUNK_METADATA_SCHEMA,
        schema_version=(
            CHUNK_METADATA_SCHEMA_VERSION
        ),
    )

    assert result is metadata

    assert result["schema"] == (
        "rag-cleaner/chunk-metadata"
    )

    assert result["schema_version"] == 1

    # 兼容旧 metadata 字段。
    assert result["version"] == 2
    assert result["status"] == "completed"


def test_is_schema_accepts_expected_schema() -> None:
    metadata = {
        "schema": FINAL_METADATA_SCHEMA,
        "schema_version": 1,
    }

    assert is_schema(
        metadata,
        FINAL_METADATA_SCHEMA,
    )


def test_is_schema_rejects_wrong_schema() -> None:
    metadata = {
        "schema": CHUNK_METADATA_SCHEMA,
        "schema_version": 1,
    }

    assert not is_schema(
        metadata,
        FINAL_METADATA_SCHEMA,
    )


def test_is_schema_rejects_missing_version() -> None:
    metadata = {
        "schema": CHUNK_METADATA_SCHEMA,
    }

    assert not is_schema(
        metadata,
        CHUNK_METADATA_SCHEMA,
    )


def test_is_schema_rejects_string_version() -> None:
    metadata = {
        "schema": CHUNK_METADATA_SCHEMA,
        "schema_version": "1",
    }

    assert not is_schema(
        metadata,
        CHUNK_METADATA_SCHEMA,
    )


def test_is_schema_respects_minimum_version() -> None:
    metadata = {
        "schema": CHUNK_METADATA_SCHEMA,
        "schema_version": 1,
    }

    assert is_schema(
        metadata,
        CHUNK_METADATA_SCHEMA,
        minimum_version=1,
    )

    assert not is_schema(
        metadata,
        CHUNK_METADATA_SCHEMA,
        minimum_version=2,
    )


def test_legacy_metadata_has_no_explicit_schema() -> None:
    """
    旧 metadata 没有 schema 字段时不会被误认为新格式。
    旧文件是否兼容由具体业务代码决定。
    """
    metadata = {
        "version": 2,
        "status": "completed",
        "output_sha256": "test-hash",
    }

    assert not is_schema(
        metadata,
        CHUNK_METADATA_SCHEMA,
    )
