from __future__ import annotations

import json
import sys
from pathlib import Path

from .api_client import ApiClient
from .chunking import (
    build_file_plan,
    find_input_files,
    plan_has_pending_chunks,
)
from .config import (
    MAX_CONSECUTIVE_FAILURES,
    MAX_RETRIES,
    REQUIRE_CONFIRMATION,
    FilePlan,
    GracefulStop,
    ProcessStats,
    append_log,
    compact_error,
    get_base_dir,
    load_runtime_config,
    parse_args,
    read_text,
    safe_name,
    sha256_text,
    validate_args,
    yaml,
)
from .control import (
    controlled_sleep,
    wait_for_enter_or_stop,
    wait_if_paused,
)
from .locking import acquire_lock, release_lock
from .processor import process_file
from .selection import (
    load_selection_paths,
    resolve_selection_file,
)


def final_output_is_current(
    plan: FilePlan,
    prompt_sha256: str,
    model: str,
    base_url: str,
) -> bool:
    """
    检查完整合并文件和 metadata 是否存在且对应当前分片计划。

    任意文件变化、完整文件缺失、metadata 损坏或完整文件被修改时，
    都返回 False，使程序进入 process_file() 重新合并。

    已完成分片会在 process_file() 中跳过，因此重新合并不会重复调用 API。
    """
    filename_stem = safe_name(
        plan.source_path.stem
    )

    final_path = (
        plan.output_dir
        / f"{filename_stem}_cleaned.md"
    )

    metadata_path = (
        plan.output_dir
        / f"{filename_stem}_cleaned.md.meta.json"
    )

    if (
        not final_path.is_file()
        or not metadata_path.is_file()
    ):
        return False

    try:
        final_text = read_text(
            final_path
        )

        if not final_text.strip():
            return False

        metadata = json.loads(
            read_text(metadata_path)
        )

        if not isinstance(metadata, dict):
            return False

        expected_values = {
            "status": "completed",
            "source_file": (
                plan.relative_path.as_posix()
            ),
            "source_sha256": plan.source_sha256,
            "prompt_sha256": prompt_sha256,
            "model": model,
            "base_url": base_url.rstrip("/"),
            "part_count": len(plan.chunks),
        }

        for key, expected_value in (
            expected_values.items()
        ):
            if (
                metadata.get(key)
                != expected_value
            ):
                return False

        return (
            metadata.get("output_sha256")
            == sha256_text(final_text)
        )

    except Exception:
        return False


def plan_needs_processing(
    plan: FilePlan,
    prompt_sha256: str,
    model: str,
    base_url: str,
) -> bool:
    """
    判断文件是否需要进入处理流程。

    以下任一情况会返回 True：

    - 有模型分片未完成；
    - 所有分片完成，但完整合并文件缺失或无效。

    第二种情况只会重新合并，不会重复调用 API。
    """
    if plan_has_pending_chunks(
        plan=plan,
        prompt_sha256=prompt_sha256,
        model=model,
        base_url=base_url,
    ):
        return True

    return not final_output_is_current(
        plan=plan,
        prompt_sha256=prompt_sha256,
        model=model,
        base_url=base_url,
    )


def relative_name(
    source_path: Path,
    input_dir: Path,
) -> str:
    """
    尽可能返回相对于 input 的安全日志名称。

    即使路径异常，也不要让记录失败逻辑本身崩溃。
    """
    try:
        return source_path.relative_to(
            input_dir
        ).as_posix()
    except ValueError:
        return source_path.name


def build_plans_safely(
    source_paths: list[Path],
    input_dir: Path,
    output_dir: Path,
    max_chars: int,
    max_file_size: int,
    log_dir: Path,
) -> tuple[list[FilePlan], list[dict[str, str]]]:
    """
    按文件建立计划，不让一个异常文件中断整批任务。

    失败文件不会进入后续 API 调用流程，但会：

    - 在终端显示错误；
    - 写入 batch.jsonl；
    - 计入最终失败文件数量；
    - 让批处理以退出码 2 结束。
    """
    plans: list[FilePlan] = []
    failed_plans: list[dict[str, str]] = []

    for source_path in source_paths:
        filename = relative_name(
            source_path,
            input_dir,
        )

        try:
            plan = build_file_plan(
                source_path=source_path,
                input_dir=input_dir,
                output_dir=output_dir,
                max_chars=max_chars,
                max_file_size=max_file_size,
            )

            plans.append(plan)

        except Exception as exc:
            message = compact_error(exc)

            failed_plans.append(
                {
                    "file": filename,
                    "error": message,
                }
            )

            print(
                f"[规划失败] {filename}："
                f"{message}"
            )

            append_log(
                log_dir,
                filename,
                "planning_failed",
                {
                    "error": message,
                    "stage": "build_file_plan",
                },
            )

    return plans, failed_plans


def main(
    argv: list[str] | None = None,
) -> None:
    args = parse_args(argv)
    validate_args(args)
    selected_source_paths: list[Path] | None = None
    selection_value = getattr(
        args,
        "selection_file",
        "",
    )

    if selection_value:
        if getattr(args, "base_dir", ""):
            selection_base_dir = (
                Path(args.base_dir)
                .expanduser()
                .resolve()
            )
        else:
            selection_base_dir = get_base_dir()
        selection_file = resolve_selection_file(
            selection_value,
            selection_base_dir,
        )
        selected_source_paths = load_selection_paths(
            selection_file,
            selection_base_dir / "input",
        )

        if not selected_source_paths:
            print(
                "选择清单为空，没有文件需要处理。"
            )
            return

    config = load_runtime_config(args)

    for directory in (
        config.input_dir,
        config.output_dir,
        config.log_dir,
    ):
        directory.mkdir(
            parents=True,
            exist_ok=True,
        )

    source_paths = (
        selected_source_paths
        if selected_source_paths is not None
        else find_input_files(config.input_dir)
    )

    if not source_paths:
        raise RuntimeError(
            "input 文件夹中没有找到 Markdown 文件："
            f"{config.input_dir}"
        )

    print(
        "[准备] 正在读取文件并生成分片计划……"
    )

    plans, planning_failures = (
        build_plans_safely(
            source_paths=source_paths,
            input_dir=config.input_dir,
            output_dir=config.output_dir,
            max_chars=config.max_chars,
            max_file_size=config.max_file_size,
            log_dir=config.log_dir,
        )
    )

    pending_plans: list[FilePlan] = []

    for plan in plans:
        try:
            has_pending_chunks = (
                plan_has_pending_chunks(
                    plan=plan,
                    prompt_sha256=(
                        config.prompt_sha256
                    ),
                    model=config.model,
                    base_url=config.base_url,
                )
            )

            if has_pending_chunks:
                pending_plans.append(plan)
                continue

            if not final_output_is_current(
                plan=plan,
                prompt_sha256=(
                    config.prompt_sha256
                ),
                model=config.model,
                base_url=config.base_url,
            ):
                pending_plans.append(plan)

                print(
                    "[待重新合并] "
                    f"{plan.relative_path}"
                )

                continue

            print(
                f"[已完成] "
                f"{plan.relative_path}"
            )

        except Exception as exc:
            # 状态检查失败时宁可进入处理流程，
            # 由 process_file 再次验证分片，
            # 不把文件误标记为已完成。
            pending_plans.append(plan)

            print(
                f"[状态检查提示] "
                f"{plan.relative_path}："
                f"{compact_error(exc)}"
            )

    selected_plans = (
        pending_plans[:config.max_files]
        if config.max_files > 0
        else pending_plans
    )

    # 所有可规划文件均已完成，但仍有规划失败文件。
    # 不需要调用 API，不过必须返回失败状态。
    if not selected_plans:
        print()

        if planning_failures:
            print(
                "没有待处理文件，"
                "但存在无法建立计划的文件。"
            )

            print(
                f"规划失败文件："
                f"{len(planning_failures)}"
            )

            print(
                f"日志文件："
                f"{config.log_dir / 'batch.jsonl'}"
            )

            sys.exit(2)

        print(
            "没有需要处理的文件，任务已完成。"
        )

        return

    print()
    print("=" * 70)
    print("RAG Markdown 批量清洗工具")
    print("=" * 70)
    print(
        f"项目根目录："
        f"{config.base_dir}"
    )
    print(
        "模式："
        f"{'dry-run（不调用 API）' if config.dry_run else '正式处理'}"
    )
    print(
        f"模型："
        f"{config.model or '(dry-run)'}"
    )
    print(
        f"API："
        f"{config.base_url or '(dry-run)'}"
    )
    print(
        f"输入目录："
        f"{config.input_dir}"
    )
    print(
        f"输出目录："
        f"{config.output_dir}"
    )
    print(
        f"全部发现文件："
        f"{len(source_paths)}"
    )
    print(
        f"成功建立计划："
        f"{len(plans)}"
    )
    print(
        f"规划失败文件："
        f"{len(planning_failures)}"
    )
    print(
        f"待处理或重新合并文件："
        f"{len(pending_plans)}"
    )
    print(
        f"本次处理文件："
        f"{len(selected_plans)}"
    )
    print(
        f"单片最大字符数："
        f"{config.max_chars:,}"
    )
    print(
        f"单文件大小上限："
        f"{config.max_file_size:,} bytes"
    )
    print(
        "第 1 分片 Front Matter 校验："
        f"{'严格' if config.strict_validation else '宽松'}"
    )
    print(
        f"最大请求重试次数："
        f"{MAX_RETRIES}"
    )
    print(
        f"连续失败停止阈值："
        f"{MAX_CONSECUTIVE_FAILURES}"
    )
    print(
        f"暂停文件："
        f"{config.pause_file}"
    )
    print(
        f"停止文件："
        f"{config.stop_file}"
    )
    print(
        "YAML 解析："
        f"{'PyYAML' if yaml is not None else '不可用'}"
    )
    print("=" * 70)

    if not config.dry_run:
        require_confirmation = (
            REQUIRE_CONFIRMATION
            and not args.yes
            and not args.no_confirm
        )

        if require_confirmation:
            answer = input(
                "\n即将调用 API，输入 Y 继续："
            ).strip().lower()

            if answer not in {
                "y",
                "yes",
            }:
                print("已取消。")
                return

        acquire_lock(
            config.lock_file,
            force_unlock=args.force_unlock,
        )

    total_stats = ProcessStats()
    processed_files = 0
    failed_files = len(planning_failures)
    stopped = False
    consecutive_failures = 0

    try:
        if config.dry_run:
            for file_index, plan in enumerate(
                selected_plans,
                start=1,
            ):
                outcome = process_file(
                    plan=plan,
                    file_index=file_index,
                    total_files=len(
                        selected_plans
                    ),
                    config=config,
                    client=None,
                    initial_consecutive_failures=0,
                )

                stats = outcome.stats

                total_stats.total_parts += (
                    stats.total_parts
                )
                total_stats.success_parts += (
                    stats.success_parts
                )
                total_stats.skipped_parts += (
                    stats.skipped_parts
                )

                processed_files += 1

        else:
            with ApiClient(
                config.base_url,
                config.api_key,
                config.model,
            ) as client:
                for file_index, plan in enumerate(
                    selected_plans,
                    start=1,
                ):
                    try:
                        wait_if_paused(
                            config.pause_file,
                            config.stop_file,
                        )

                        outcome = process_file(
                            plan=plan,
                            file_index=file_index,
                            total_files=len(
                                selected_plans
                            ),
                            config=config,
                            client=client,
                            initial_consecutive_failures=(
                                consecutive_failures
                            ),
                        )

                        stats = outcome.stats

                        total_stats.total_parts += (
                            stats.total_parts
                        )
                        total_stats.success_parts += (
                            stats.success_parts
                        )
                        total_stats.failed_parts += (
                            stats.failed_parts
                        )
                        total_stats.skipped_parts += (
                            stats.skipped_parts
                        )

                        processed_files += 1

                        consecutive_failures = (
                            outcome.consecutive_failures
                        )

                        if stats.failed_parts > 0:
                            failed_files += 1

                        if outcome.stopped:
                            stopped = True

                            append_log(
                                config.log_dir,
                                "BATCH",
                                "auto_stopped",
                                (
                                    "连续失败达到阈值，"
                                    "停止本批次"
                                ),
                            )

                            break

                        if (
                            config.pause_after_files
                            > 0
                            and processed_files
                            % config.pause_after_files
                            == 0
                            and processed_files
                            < len(selected_plans)
                        ):
                            wait_for_enter_or_stop(
                                config.pause_file,
                                config.stop_file,
                            )

                        if (
                            config.pause_between_files
                            > 0
                            and processed_files
                            < len(selected_plans)
                        ):
                            controlled_sleep(
                                config.pause_between_files,
                                config.pause_file,
                                config.stop_file,
                            )

                    except (
                        GracefulStop,
                        KeyboardInterrupt,
                    ) as exc:
                        stopped = True

                        print(
                            "\n[安全停止] "
                            f"{compact_error(exc)}"
                        )

                        append_log(
                            config.log_dir,
                            "BATCH",
                            "stopped",
                            compact_error(exc),
                        )

                        break

                    except Exception as exc:
                        failed_files += 1
                        consecutive_failures += 1

                        append_log(
                            config.log_dir,
                            plan.relative_path.as_posix(),
                            "file_failed",
                            {
                                "error": (
                                    compact_error(exc)
                                ),
                                "consecutive_failures": (
                                    consecutive_failures
                                ),
                            },
                        )

                        print(
                            f"\n[文件失败] "
                            f"{plan.relative_path}"
                        )
                        print(
                            f"错误："
                            f"{compact_error(exc)}"
                        )

                        if (
                            consecutive_failures
                            >= MAX_CONSECUTIVE_FAILURES
                        ):
                            stopped = True

                            print(
                                "\n[自动停止] "
                                "连续文件失败达到阈值。"
                            )

                            break

                        print(
                            "继续处理下一个文件。"
                        )

    finally:
        if not config.dry_run:
            release_lock(
                config.lock_file
            )

    print()
    print("=" * 70)
    print("批量处理结束")
    print("=" * 70)
    print(
        f"全部发现文件："
        f"{len(source_paths)}"
    )
    print(
        f"成功建立计划："
        f"{len(plans)}"
    )
    print(
        f"规划失败文件："
        f"{len(planning_failures)}"
    )
    print(
        f"本次待处理文件："
        f"{len(selected_plans)}"
    )
    print(
        f"已处理文件："
        f"{processed_files}"
    )
    print(
        f"分片总数："
        f"{total_stats.total_parts}"
    )
    print(
        f"成功分片："
        f"{total_stats.success_parts}"
    )
    print(
        f"其中跳过："
        f"{total_stats.skipped_parts}"
    )
    print(
        f"失败分片："
        f"{total_stats.failed_parts}"
    )
    print(
        f"失败文件："
        f"{failed_files}"
    )
    print(
        "任务状态："
        f"{'已停止' if stopped else '本批次已完成'}"
    )
    print(
        f"日志文件："
        f"{config.log_dir / 'batch.jsonl'}"
    )
    print("=" * 70)

    if stopped:
        sys.exit(1)

    if (
        total_stats.failed_parts > 0
        or failed_files > 0
    ):
        sys.exit(2)


def run(
    argv: list[str] | None = None,
) -> None:
    try:
        main(argv)

    except KeyboardInterrupt:
        print("\n操作已取消。")
        sys.exit(1)

    except Exception as exc:
        print(
            "\n程序启动失败："
            f"{compact_error(exc)}"
        )
        sys.exit(1)
