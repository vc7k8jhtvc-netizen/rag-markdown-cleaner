from __future__ import annotations

from .api_client import ApiClient, build_user_message
from .assembly import assemble_completed_file
from .chunking import (
    build_expected_metadata,
    build_output_metadata,
    get_chunk_paths,
    is_completed_chunk,
)
from .config import (
    MAX_CONSECUTIVE_FAILURES,
    FilePlan,
    GracefulStop,
    ProcessOutcome,
    ProcessStats,
    RuntimeConfig,
    append_log,
    atomic_write_json,
    atomic_write_text,
    compact_error,
    sha256_text,
)
from .control import controlled_sleep, wait_if_paused
from .quality import assess_quality
from .validation import (
    remove_outer_code_fence,
    validate_result,
)


def remove_incomplete_output(
    output_path,
    metadata_path,
) -> None:
    """
    清理没有完整提交的分片结果。
    """
    try:
        output_path.unlink(
            missing_ok=True
        )
    except OSError:
        pass

    try:
        metadata_path.unlink(
            missing_ok=True
        )
    except OSError:
        pass


def save_chunk_result(
    output_path,
    metadata_path,
    result: str,
    metadata: dict,
) -> None:
    """
    保存分片结果和 metadata。

    如果其中任一步失败，删除本次不完整结果。
    """
    try:
        atomic_write_text(
            output_path,
            result.rstrip() + "\n",
        )
        atomic_write_json(
            metadata_path,
            metadata,
        )
    except Exception:
        remove_incomplete_output(
            output_path,
            metadata_path,
        )
        raise


def process_file(
    plan: FilePlan,
    file_index: int,
    total_files: int,
    config: RuntimeConfig,
    client: ApiClient | None,
    initial_consecutive_failures: int,
) -> ProcessOutcome:
    stats = ProcessStats(
        total_parts=len(plan.chunks)
    )

    consecutive_failures = (
        initial_consecutive_failures
    )

    if plan.is_empty:
        print(
            f"[跳过] 空文件："
            f"{plan.relative_path}"
        )

        append_log(
            config.log_dir,
            plan.relative_path.as_posix(),
            "skipped",
            "空文件",
        )

        return ProcessOutcome(
            stats=stats,
            consecutive_failures=0,
        )

    plan.output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    print()
    print("=" * 70)
    print(
        f"[文件 {file_index}/{total_files}] "
        f"{plan.relative_path}"
    )
    print(
        f"原文字符数："
        f"{plan.source_chars:,}"
    )
    print(
        f"分片数量："
        f"{len(plan.chunks)}"
    )
    print(
        f"输出目录："
        f"{plan.output_dir}"
    )
    print("=" * 70)

    total_parts = len(plan.chunks)

    for part_number, chunk in enumerate(
        plan.chunks,
        start=1,
    ):
        wait_if_paused(
            config.pause_file,
            config.stop_file,
        )

        (
            output_path,
            metadata_path,
            partial_path,
        ) = get_chunk_paths(
            plan.output_dir,
            plan.source_path,
            part_number,
        )

        expected = build_expected_metadata(
            relative_path=plan.relative_path,
            source_sha256=plan.source_sha256,
            chunk_sha256=sha256_text(chunk),
            prompt_sha256=config.prompt_sha256,
            model=config.model,
            base_url=config.base_url,
            part_number=part_number,
            total_parts=total_parts,
        )

        if is_completed_chunk(
            output_path,
            metadata_path,
            expected,
        ):
            print(
                f"[跳过] 已完成："
                f"{output_path.name}"
            )

            stats.success_parts += 1
            stats.skipped_parts += 1
            consecutive_failures = 0
            continue

        if config.dry_run:
            print(
                f"[dry-run] 将处理："
                f"{plan.relative_path} "
                f"第 {part_number}/{total_parts} 片 "
                f"({len(chunk):,} 字符) -> "
                f"{output_path.name}"
            )

            stats.success_parts += 1
            continue

        if client is None:
            raise RuntimeError(
                "正式处理模式需要 ApiClient"
            )

        user_message = build_user_message(
            chunk=chunk,
            part_number=part_number,
            total_parts=total_parts,
            relative_path=plan.relative_path,
        )

        try:
            request_result = client.stream_request(
                system_prompt=config.system_prompt,
                user_message=user_message,
                file_index=file_index,
                total_files=total_files,
                part_number=part_number,
                total_parts=total_parts,
                pause_file=config.pause_file,
                stop_file=config.stop_file,
                partial_path=partial_path,
                sleep_fn=controlled_sleep,
            )

            result = remove_outer_code_fence(
                request_result.text
            )

            errors, validation_warnings = (
                validate_result(
                    result=result,
                    input_chunk=chunk,
                    strict_validation=(
                        config.strict_validation
                    ),
                    part_number=part_number,
                )
            )

            if errors:
                raise RuntimeError(
                    "输出校验失败："
                    + "；".join(errors)
                )

            quality = assess_quality(
                input_text=chunk,
                output_text=result,
            )

            if quality.severe_errors:
                raise RuntimeError(
                    "输出质量检查失败："
                    + "；".join(
                        quality.severe_errors
                    )
                )

            warnings = list(
                dict.fromkeys(
                    validation_warnings
                    + quality.warnings
                )
            )

            metadata = build_output_metadata(
                expected=expected,
                result=result,
                warnings=warnings,
            )

            metadata["version"] = 2
            metadata["review_required"] = (
                quality.review_required
            )
            metadata["quality"] = (
                quality.to_dict()
            )

            save_chunk_result(
                output_path=output_path,
                metadata_path=metadata_path,
                result=result,
                metadata=metadata,
            )

            try:
                partial_path.unlink(
                    missing_ok=True
                )
            except OSError:
                pass

            append_log(
                config.log_dir,
                plan.relative_path.as_posix(),
                "success",
                {
                    "part": (
                        f"{part_number}/"
                        f"{total_parts}"
                    ),
                    "input_chars": len(chunk),
                    "output_chars": len(result),
                    "retained_ratio": (
                        quality.retained_ratio
                    ),
                    "removed_ratio": (
                        quality.removed_ratio
                    ),
                    "review_required": (
                        quality.review_required
                    ),
                    "elapsed_seconds": round(
                        request_result.elapsed_seconds,
                        1,
                    ),
                    "output": str(output_path),
                    "warnings": warnings,
                },
            )

            print(
                f"[保存] {output_path}"
            )

            print(
                "[质量] "
                f"保留比例："
                f"{quality.retained_ratio:.1%}；"
                f"删除比例："
                f"{quality.removed_ratio:.1%}"
            )

            if quality.review_required:
                print(
                    "[需要复核] "
                    "此分片存在质量提示。"
                )

            if warnings:
                print(
                    "[检查提示] "
                    + "；".join(warnings)
                )

            stats.success_parts += 1
            consecutive_failures = 0

        except KeyboardInterrupt as exc:
            raise GracefulStop(
                "用户按下 Ctrl+C"
            ) from exc

        except GracefulStop:
            raise

        except Exception as exc:
            stats.failed_parts += 1
            consecutive_failures += 1

            append_log(
                config.log_dir,
                plan.relative_path.as_posix(),
                "failed",
                {
                    "part": (
                        f"{part_number}/"
                        f"{total_parts}"
                    ),
                    "error": compact_error(exc),
                    "consecutive_failures": (
                        consecutive_failures
                    ),
                    "partial": (
                        str(partial_path)
                        if partial_path.exists()
                        else None
                    ),
                },
            )

            print(
                f"\n[失败] "
                f"{plan.relative_path} "
                f"第 {part_number} 片："
                f"{compact_error(exc)}"
            )

            if (
                consecutive_failures
                >= MAX_CONSECUTIVE_FAILURES
            ):
                print(
                    f"\n[自动停止] 连续失败达到 "
                    f"{MAX_CONSECUTIVE_FAILURES} 次，"
                    "可能是网络、API 服务或模型输出异常。"
                )

                return ProcessOutcome(
                    stats=stats,
                    consecutive_failures=(
                        consecutive_failures
                    ),
                    stopped=True,
                )

            print(
                "继续处理下一个分片；"
                "再次运行时会重试本片。"
            )

        if config.pause_between_chunks > 0:
            controlled_sleep(
                config.pause_between_chunks,
                config.pause_file,
                config.stop_file,
            )

    # dry-run 只规划分片，不生成合并文件。
    if config.dry_run:
        return ProcessOutcome(
            stats=stats,
            consecutive_failures=consecutive_failures,
        )

    # 只有所有分片都成功或已验证跳过时，才生成完整文件。
    if (
        stats.failed_parts == 0
        and stats.success_parts == stats.total_parts
    ):
        try:
            final_path, _ = (
                assemble_completed_file(
                    plan=plan,
                    config=config,
                )
            )

            print(
                f"[合并] 已生成完整文件："
                f"{final_path}"
            )

        except Exception as exc:
            stats.failed_parts += 1

            append_log(
                config.log_dir,
                plan.relative_path.as_posix(),
                "assembly_failed",
                {
                    "error": compact_error(exc),
                },
            )

            print(
                f"[合并失败] "
                f"{plan.relative_path}："
                f"{compact_error(exc)}"
            )

    return ProcessOutcome(
        stats=stats,
        consecutive_failures=consecutive_failures,
    )
