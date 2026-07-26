from __future__ import annotations

import json
import re
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any

from .api_client import (
    ApiClient,
    build_user_message,
)
from .assembly import assemble_completed_file
from .chunking import (
    build_expected_metadata,
    build_output_metadata,
    get_chunk_paths,
    get_failed_chunk_paths,
    is_completed_chunk,
    remove_failed_chunk_result,
    save_failed_chunk_result,
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
    read_text,
    sha256_text,
)
from .control import (
    controlled_sleep,
    wait_if_paused,
)
from .metadata_schema import (
    CHUNK_METADATA_SCHEMA,
    CHUNK_METADATA_SCHEMA_VERSION,
    add_schema_identity,
)
from .quality import assess_quality
from .progress import ProgressContext, ProgressReporter
from .validation import (
    remove_outer_code_fence,
    validate_result,
)


def safe_log_path(
    path: Path,
    base_dir: Path,
) -> str:
    """
    将文件路径转换为相对于项目根目录的日志路径。

    项目目录内的路径：

    D:\\Apps\\Rag-cleaner\\output\\example.md

    会被记录为：

    output/example.md

    如果路径不位于项目目录内，只记录文件名，
    避免日志泄露本机绝对路径。
    """
    try:
        resolved_path = path.resolve()
        resolved_base = base_dir.resolve()

        return (
            resolved_path
            .relative_to(resolved_base)
            .as_posix()
        )

    except (
        OSError,
        ValueError,
    ):
        return path.name


def _safe_progress_error(
    exc: Exception,
    config: RuntimeConfig,
) -> str:
    text = compact_error(exc, limit=240)
    try:
        text = text.replace(
            str(config.base_dir.resolve()),
            "<项目目录>",
        )
    except OSError:
        pass
    text = re.sub(
        r"(?i)[A-Z]:[\\/][^\s;，。；]+",
        "<路径>",
        text,
    )
    return text


def remove_incomplete_output(
    output_path: Path,
    metadata_path: Path,
) -> None:
    """
    删除没有完整提交的分片结果。

    如果正文写入成功但 metadata 写入失败，
    不应保留一个看起来已经完成的分片。
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
    output_path: Path,
    metadata_path: Path,
    result: str,
    metadata: dict[str, Any],
) -> None:
    """
    保存分片结果和 metadata。

    两个文件分别使用原子写入。

    如果其中任一步失败，删除本次不完整结果，
    下次运行时该分片会被安全重试。
    """
    try:
        atomic_write_text(
            output_path,
            result,
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


def _promote_failed_candidate(
    failed_path: Path,
    failed_metadata_path: Path,
    output_path: Path,
    output_metadata_path: Path,
    expected: dict[str, Any],
) -> bool:
    """将当前指纹匹配且已人工确认的失败候选转为完成分片。"""
    if not failed_path.is_file() or not failed_metadata_path.is_file():
        return False

    try:
        result = read_text(failed_path)
        failed_metadata = json.loads(read_text(failed_metadata_path))
    except Exception:
        return False

    if not isinstance(failed_metadata, dict) or not result.strip():
        return False

    for key, expected_value in expected.items():
        if failed_metadata.get(key) != expected_value:
            return False

    metadata = build_output_metadata(
        expected=expected,
        result=result,
        warnings=["用户确认使用失败候选"],
    )
    metadata["version"] = 2
    add_schema_identity(
        metadata=metadata,
        schema=CHUNK_METADATA_SCHEMA,
        schema_version=CHUNK_METADATA_SCHEMA_VERSION,
    )
    metadata["status"] = "completed"
    metadata["review_required"] = True
    metadata["accepted_failed_candidate"] = True
    metadata["accepted_failed_candidate_at"] = metadata["completed_at"]
    metadata["failed_candidate_reason"] = failed_metadata.get(
        "failure_reason",
        "自动校验失败",
    )
    metadata["quality"] = {
        "severe_errors": [],
        "warnings": ["用户确认使用失败候选"],
        "review_required": True,
        "accepted_failed_candidate": True,
    }
    save_chunk_result(
        output_path=output_path,
        metadata_path=output_metadata_path,
        result=result,
        metadata=metadata,
    )
    return True


def _process_file_concurrently(
    plan: FilePlan,
    file_index: int,
    total_files: int,
    config: RuntimeConfig,
    client: ApiClient | None,
    initial_consecutive_failures: int,
    reporter: ProgressReporter | None,
) -> ProcessOutcome:
    """在一个文件内部有界并行处理分片。

    每个 future 只处理一个分片，并复用现有的单分片路径。
    future 数量始终不超过 workers；多个文件之间的实际 API 请求
    仍由 ApiClient 的共享 semaphore 统一限制。
    """
    stats = ProcessStats(total_parts=len(plan.chunks))
    consecutive_failures = initial_consecutive_failures
    if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
        return ProcessOutcome(
            stats=stats,
            consecutive_failures=consecutive_failures,
            stopped=True,
        )

    next_part = 1
    stopped = False
    futures: dict[Future[ProcessOutcome], int] = {}

    def submit_available(executor: ThreadPoolExecutor) -> None:
        nonlocal next_part, stopped
        while (
            next_part <= len(plan.chunks)
            and len(futures) < config.workers
        ):
            if config.stop_file.exists():
                stopped = True
                return
            part_number = next_part
            next_part += 1
            future = executor.submit(
                process_file,
                plan=plan,
                file_index=file_index,
                total_files=total_files,
                config=config,
                client=client,
                initial_consecutive_failures=0,
                reporter=reporter,
                _part_numbers={part_number},
                _assemble=False,
            )
            futures[future] = part_number

    with ThreadPoolExecutor(
        max_workers=config.workers,
        thread_name_prefix="rag-cleaner-chunk",
    ) as executor:
        submit_available(executor)
        while futures:
            done, _ = wait(
                futures,
                timeout=0.1,
                return_when=FIRST_COMPLETED,
            )
            if not done:
                if config.stop_file.exists():
                    stopped = True
                if stopped:
                    for future in list(futures):
                        future.cancel()
                continue

            for future in sorted(
                done,
                key=lambda item: futures[item],
            ):
                futures.pop(future)
                if future.cancelled():
                    continue
                try:
                    outcome = future.result()
                except (GracefulStop, KeyboardInterrupt):
                    stopped = True
                    continue

                stats.success_parts += outcome.stats.success_parts
                stats.skipped_parts += outcome.stats.skipped_parts
                stats.failed_parts += outcome.stats.failed_parts

                if outcome.stats.failed_parts:
                    consecutive_failures += 1
                    if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                        stopped = True
                elif outcome.stats.success_parts:
                    consecutive_failures = 0

                if outcome.stopped:
                    stopped = True

            if stopped:
                for future in list(futures):
                    future.cancel()
                futures = {
                    future: part_number
                    for future, part_number in futures.items()
                    if not future.cancelled()
                }
            else:
                submit_available(executor)

    if stopped:
        for future in futures:
            future.cancel()

    if (
        not config.dry_run
        and not stopped
        and stats.failed_parts == 0
        and stats.success_parts == stats.total_parts
    ):
        try:
            if reporter is None:
                assemble_completed_file(plan=plan, config=config)
            else:
                assemble_completed_file(
                    plan=plan,
                    config=config,
                    reporter=reporter,
                    context=ProgressContext(
                        file_index=file_index,
                        total_files=total_files,
                        relative_path=plan.relative_path,
                    ),
                )
        except Exception as exc:
            stats.failed_parts += 1
            append_log(
                config.log_dir,
                plan.relative_path.as_posix(),
                "assembly_failed",
                {
                    "error": compact_error(exc),
                    "output_dir": safe_log_path(
                        plan.output_dir,
                        config.base_dir,
                    ),
                },
            )

    return ProcessOutcome(
        stats=stats,
        consecutive_failures=consecutive_failures,
        stopped=stopped,
    )


def process_file(
    plan: FilePlan,
    file_index: int,
    total_files: int,
    config: RuntimeConfig,
    client: ApiClient | None,
    initial_consecutive_failures: int,
    reporter: ProgressReporter | None = None,
    _part_numbers: set[int] | None = None,
    _assemble: bool = True,
) -> ProcessOutcome:
    """
    处理一个输入文件的全部分片。

    处理流程：

    1. 检查暂停和停止标记；
    2. 验证分片是否已经完成；
    3. 构造模型请求；
    4. 调用 API；
    5. 校验输出格式；
    6. 检查内容质量；
    7. 保存正文和 metadata；
    8. 全部分片成功后合并完整文件。
    """
    stats = ProcessStats(
        total_parts=len(plan.chunks)
    )

    consecutive_failures = (
        initial_consecutive_failures
    )

    if plan.is_empty:
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

    if (
        config.workers > 1
        and _part_numbers is None
        and len(plan.chunks) > 1
    ):
        return _process_file_concurrently(
            plan=plan,
            file_index=file_index,
            total_files=total_files,
            config=config,
            client=client,
            initial_consecutive_failures=initial_consecutive_failures,
            reporter=reporter,
        )

    plan.output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    total_parts = len(plan.chunks)

    for part_number, chunk in enumerate(
        plan.chunks,
        start=1,
    ):
        if _part_numbers is not None and part_number not in _part_numbers:
            continue
        progress_context = ProgressContext(
            file_index=file_index,
            total_files=total_files,
            relative_path=plan.relative_path,
            part_number=part_number,
            total_parts=total_parts,
        )
        wait_if_paused(
            config.pause_file,
            config.stop_file,
            reporter=reporter,
            context=progress_context,
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
        failed_path, failed_metadata_path = get_failed_chunk_paths(
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
            strict_validation=config.strict_validation,
            quality_policy_sha256=(
                config.quality_policy_sha256
            ),
        )

        # ----------------------------------------------------
        # 已完成分片
        # ----------------------------------------------------

        if is_completed_chunk(
            output_path,
            metadata_path,
            expected,
            config.prompt_sha256_aliases,
        ):
            if reporter is not None:
                reporter.file_event(progress_context, "chunk_skipped")
            stats.success_parts += 1
            stats.skipped_parts += 1
            consecutive_failures = 0
            remove_failed_chunk_result(
                failed_path,
                failed_metadata_path,
            )
            continue

        if config.accept_failed and _promote_failed_candidate(
            failed_path=failed_path,
            failed_metadata_path=failed_metadata_path,
            output_path=output_path,
            output_metadata_path=metadata_path,
            expected=expected,
        ):
            remove_failed_chunk_result(
                failed_path,
                failed_metadata_path,
            )
            if reporter is not None:
                reporter.file_event(
                    progress_context,
                    "detail",
                    message="已接受人工确认的失败候选",
                )
                reporter.file_event(
                    progress_context,
                    "chunk_completed",
                )
            stats.success_parts += 1
            consecutive_failures = 0
            continue

        if config.accept_failed:
            message = (
                "没有找到与当前文件和配置匹配的失败候选，"
                "本次未调用 API"
            )
            stats.failed_parts += 1
            consecutive_failures += 1
            if reporter is not None:
                reporter.file_event(
                    progress_context,
                    "chunk_failed",
                    error=message,
                )
            append_log(
                config.log_dir,
                plan.relative_path.as_posix(),
                "failed",
                {
                    "part": f"{part_number}/{total_parts}",
                    "error": message,
                    "failed_candidate": safe_log_path(
                        failed_path,
                        config.base_dir,
                    ),
                },
            )
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                return ProcessOutcome(
                    stats=stats,
                    consecutive_failures=consecutive_failures,
                    stopped=True,
                )
            continue

        # ----------------------------------------------------
        # Dry-run
        # ----------------------------------------------------

        if config.dry_run:
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

        candidate_result: str | None = None
        try:
            # ------------------------------------------------
            # 调用模型
            # ------------------------------------------------

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
                sleep_fn=lambda seconds, pause_file, stop_file: controlled_sleep(
                    seconds,
                    pause_file,
                    stop_file,
                    reporter=reporter,
                    context=progress_context,
                ),
                reporter=reporter,
                context=progress_context,
            )

            result = remove_outer_code_fence(
                request_result.text
            )
            candidate_result = result

            # ------------------------------------------------
            # 基础格式校验
            # ------------------------------------------------

            (
                errors,
                validation_warnings,
            ) = validate_result(
                result=result,
                input_chunk=chunk,
                strict_validation=(
                    config.strict_validation
                ),
                part_number=part_number,
            )

            if errors:
                raise RuntimeError(
                    "输出校验失败："
                    + "；".join(errors)
                )

            # ------------------------------------------------
            # 教材内容质量检查
            # ------------------------------------------------

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

            # ------------------------------------------------
            # 构造分片 metadata
            # ------------------------------------------------

            metadata = build_output_metadata(
                expected=expected,
                result=result,
                warnings=warnings,
            )

            # 保留旧 version 字段，兼容已经生成的分片。
            metadata["version"] = 2

            # 增加明确的 schema 类型和 schema 版本。
            add_schema_identity(
                metadata=metadata,
                schema=CHUNK_METADATA_SCHEMA,
                schema_version=(
                    CHUNK_METADATA_SCHEMA_VERSION
                ),
            )

            metadata["status"] = "completed"
            metadata["strict_validation"] = config.strict_validation
            metadata["review_required"] = (
                quality.review_required
            )
            metadata["quality"] = (
                quality.to_dict()
            )

            # ------------------------------------------------
            # 保存分片结果
            # ------------------------------------------------

            save_chunk_result(
                output_path=output_path,
                metadata_path=metadata_path,
                result=result,
                metadata=metadata,
            )
            remove_failed_chunk_result(
                failed_path,
                failed_metadata_path,
            )

            # 正式结果和 metadata 均保存成功后，
            # 才删除 partial 文件。
            try:
                partial_path.unlink(
                    missing_ok=True
                )
            except OSError:
                pass

            # ------------------------------------------------
            # 成功日志
            #
            # 只记录相对于项目目录的路径。
            # ------------------------------------------------

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
                    "output": safe_log_path(
                        output_path,
                        config.base_dir,
                    ),
                    "metadata": safe_log_path(
                        metadata_path,
                        config.base_dir,
                    ),
                    "warnings": warnings,
                },
            )

            if quality.review_required:
                if reporter is not None:
                    reporter.file_event(
                        progress_context,
                        "quality_warning",
                        message="需要人工复核",
                    )

            if warnings:
                if reporter is not None:
                    reporter.file_event(
                        progress_context,
                        "quality_warning",
                        message="；".join(warnings),
                    )

            if reporter is not None:
                reporter.file_event(progress_context, "chunk_completed")

            stats.success_parts += 1
            consecutive_failures = 0

        except KeyboardInterrupt as exc:
            raise GracefulStop(
                "用户按下 Ctrl+C"
            ) from exc

        except GracefulStop:
            raise

        except Exception as exc:
            # ------------------------------------------------
            # 分片失败
            # ------------------------------------------------

            stats.failed_parts += 1
            consecutive_failures += 1

            if reporter is not None:
                reporter.file_event(
                    progress_context,
                    "chunk_failed",
                    error=_safe_progress_error(exc, config),
                )

            partial_log_path = (
                safe_log_path(
                    partial_path,
                    config.base_dir,
                )
                if partial_path.exists()
                else None
            )
            failed_log_path = None
            if candidate_result and candidate_result.strip():
                try:
                    save_failed_chunk_result(
                        failed_path,
                        failed_metadata_path,
                        candidate_result,
                        {
                            "schema": "rag-cleaner/failed-chunk-metadata",
                            "version": 1,
                            **expected,
                            "status": "failed",
                            "failure_reason": compact_error(exc),
                            "output_sha256": sha256_text(candidate_result),
                            "output_chars": len(candidate_result),
                        },
                    )
                    failed_log_path = safe_log_path(
                        failed_path,
                        config.base_dir,
                    )
                    if reporter is not None:
                        reporter.file_event(
                            progress_context,
                            "detail",
                            message=(
                                "已保存失败候选："
                                f"{failed_path.name}"
                            ),
                        )
                except Exception:
                    if reporter is not None:
                        reporter.file_event(
                            progress_context,
                            "detail",
                            message="保存失败候选结果失败",
                        )

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
                    "partial": partial_log_path,
                    "failed_candidate": failed_log_path,
                },
            )

            if (
                consecutive_failures
                >= MAX_CONSECUTIVE_FAILURES
            ):
                return ProcessOutcome(
                    stats=stats,
                    consecutive_failures=(
                        consecutive_failures
                    ),
                    stopped=True,
                )

        # ----------------------------------------------------
        # 分片间暂停
        # ----------------------------------------------------

        if config.pause_between_chunks > 0:
            controlled_sleep(
                config.pause_between_chunks,
                config.pause_file,
                config.stop_file,
                reporter=reporter,
                context=progress_context,
            )

    # Dry-run 只规划分片，不生成完整文件。
    if config.dry_run:
        return ProcessOutcome(
            stats=stats,
            consecutive_failures=(
                consecutive_failures
            ),
        )

    # --------------------------------------------------------
    # 合并完整文件
    #
    # 只有所有分片均成功或已经验证完成时才会执行。
    # --------------------------------------------------------

    if (
        _assemble
        and
        stats.failed_parts == 0
        and stats.success_parts
        == stats.total_parts
    ):
        try:
            if reporter is None:
                assemble_completed_file(
                    plan=plan,
                    config=config,
                )
            else:
                assemble_completed_file(
                    plan=plan,
                    config=config,
                    reporter=reporter,
                    context=ProgressContext(
                        file_index=file_index,
                        total_files=total_files,
                        relative_path=plan.relative_path,
                    ),
                )

        except Exception as exc:
            stats.failed_parts += 1

            append_log(
                config.log_dir,
                plan.relative_path.as_posix(),
                "assembly_failed",
                {
                    "error": compact_error(exc),
                    "output_dir": safe_log_path(
                        plan.output_dir,
                        config.base_dir,
                    ),
                },
            )

    return ProcessOutcome(
        stats=stats,
        consecutive_failures=(
            consecutive_failures
        ),
    )
