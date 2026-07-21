from __future__ import annotations

import sys

from .api_client import ApiClient
from .chunking import build_file_plans, find_input_files, plan_has_pending_chunks
from .config import (
    MAX_CONSECUTIVE_FAILURES,
    MAX_RETRIES,
    REQUIRE_CONFIRMATION,
    FilePlan,
    GracefulStop,
    ProcessStats,
    append_log,
    compact_error,
    load_runtime_config,
    parse_args,
    validate_args,
    yaml,
)
from .control import controlled_sleep, wait_for_enter_or_stop, wait_if_paused
from .locking import acquire_lock, release_lock
from .processor import process_file


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    validate_args(args)
    config = load_runtime_config(args)

    for directory in (config.input_dir, config.output_dir, config.log_dir):
        directory.mkdir(parents=True, exist_ok=True)

    source_paths = find_input_files(config.input_dir)
    if not source_paths:
        raise RuntimeError(f"input 文件夹中没有找到 Markdown 文件：{config.input_dir}")

    print("[准备] 正在读取文件并生成分片计划……")
    plans = build_file_plans(
        source_paths=source_paths,
        input_dir=config.input_dir,
        output_dir=config.output_dir,
        max_chars=config.max_chars,
        max_file_size=config.max_file_size,
    )

    pending_plans: list[FilePlan] = []
    for plan in plans:
        try:
            if plan_has_pending_chunks(
                plan=plan,
                prompt_sha256=config.prompt_sha256,
                model=config.model,
                base_url=config.base_url,
            ):
                pending_plans.append(plan)
            else:
                print(f"[已完成] {plan.relative_path}")
        except Exception as exc:
            pending_plans.append(plan)
            print(f"[状态检查提示] {plan.relative_path}：{compact_error(exc)}")

    selected_plans = (
        pending_plans[: config.max_files] if config.max_files > 0 else pending_plans
    )
    if not selected_plans:
        print("\n没有需要处理的文件，任务已完成。")
        return

    print()
    print("=" * 70)
    print("RAG Markdown 批量清洗工具")
    print("=" * 70)
    print(f"项目根目录：{config.base_dir}")
    print(f"模式：{'dry-run（不调用 API）' if config.dry_run else '正式处理'}")
    print(f"模型：{config.model or '(dry-run)'}")
    print(f"API：{config.base_url or '(dry-run)'}")
    print(f"输入目录：{config.input_dir}")
    print(f"输出目录：{config.output_dir}")
    print(f"全部文件：{len(plans)}")
    print(f"待处理文件：{len(pending_plans)}")
    print(f"本次处理文件：{len(selected_plans)}")
    print(f"单片最大字符数：{config.max_chars:,}")
    print(f"单文件大小上限：{config.max_file_size:,} bytes")
    print(
        "第 1 分片 Front Matter 校验："
        f"{'严格' if config.strict_validation else '宽松'}"
    )
    print(f"最大请求重试次数：{MAX_RETRIES}")
    print(f"连续失败停止阈值：{MAX_CONSECUTIVE_FAILURES}")
    print(f"暂停文件：{config.pause_file}")
    print(f"停止文件：{config.stop_file}")
    print(f"YAML 解析：{'PyYAML' if yaml is not None else '正则回退'}")
    print("=" * 70)

    if not config.dry_run:
        require_confirmation = (
            REQUIRE_CONFIRMATION and not args.yes and not args.no_confirm
        )
        if require_confirmation:
            answer = input("\n即将调用 API，输入 Y 继续：").strip().lower()
            if answer not in {"y", "yes"}:
                print("已取消。")
                return
        acquire_lock(config.lock_file, force_unlock=args.force_unlock)

    total_stats = ProcessStats()
    processed_files = 0
    failed_files = 0
    stopped = False
    consecutive_failures = 0

    try:
        if config.dry_run:
            for file_index, plan in enumerate(selected_plans, start=1):
                outcome = process_file(
                    plan=plan,
                    file_index=file_index,
                    total_files=len(selected_plans),
                    config=config,
                    client=None,
                    initial_consecutive_failures=0,
                )
                stats = outcome.stats
                total_stats.total_parts += stats.total_parts
                total_stats.success_parts += stats.success_parts
                total_stats.skipped_parts += stats.skipped_parts
                processed_files += 1
        else:
            with ApiClient(config.base_url, config.api_key, config.model) as client:
                for file_index, plan in enumerate(selected_plans, start=1):
                    try:
                        wait_if_paused(config.pause_file, config.stop_file)
                        outcome = process_file(
                            plan=plan,
                            file_index=file_index,
                            total_files=len(selected_plans),
                            config=config,
                            client=client,
                            initial_consecutive_failures=consecutive_failures,
                        )

                        stats = outcome.stats
                        total_stats.total_parts += stats.total_parts
                        total_stats.success_parts += stats.success_parts
                        total_stats.failed_parts += stats.failed_parts
                        total_stats.skipped_parts += stats.skipped_parts
                        processed_files += 1
                        consecutive_failures = outcome.consecutive_failures

                        if stats.failed_parts > 0:
                            failed_files += 1
                        if outcome.stopped:
                            stopped = True
                            append_log(
                                config.log_dir,
                                "BATCH",
                                "auto_stopped",
                                "连续失败达到阈值，停止本批次",
                            )
                            break

                        if (
                            config.pause_after_files > 0
                            and processed_files % config.pause_after_files == 0
                            and processed_files < len(selected_plans)
                        ):
                            wait_for_enter_or_stop(config.pause_file, config.stop_file)

                        if (
                            config.pause_between_files > 0
                            and processed_files < len(selected_plans)
                        ):
                            controlled_sleep(
                                config.pause_between_files,
                                config.pause_file,
                                config.stop_file,
                            )

                    except (GracefulStop, KeyboardInterrupt) as exc:
                        stopped = True
                        print(f"\n[安全停止] {compact_error(exc)}")
                        append_log(config.log_dir, "BATCH", "stopped", compact_error(exc))
                        break
                    except Exception as exc:
                        failed_files += 1
                        consecutive_failures += 1
                        append_log(
                            config.log_dir,
                            plan.relative_path.as_posix(),
                            "file_failed",
                            {
                                "error": compact_error(exc),
                                "consecutive_failures": consecutive_failures,
                            },
                        )
                        print(f"\n[文件失败] {plan.relative_path}")
                        print(f"错误：{compact_error(exc)}")
                        if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                            stopped = True
                            print("\n[自动停止] 连续文件失败达到阈值。")
                            break
                        print("继续处理下一个文件。")
    finally:
        if not config.dry_run:
            release_lock(config.lock_file)

    print()
    print("=" * 70)
    print("批量处理结束")
    print("=" * 70)
    print(f"全部文件：{len(plans)}")
    print(f"本次待处理文件：{len(selected_plans)}")
    print(f"已处理文件：{processed_files}")
    print(f"分片总数：{total_stats.total_parts}")
    print(f"成功分片：{total_stats.success_parts}")
    print(f"其中跳过：{total_stats.skipped_parts}")
    print(f"失败分片：{total_stats.failed_parts}")
    print(f"失败文件：{failed_files}")
    print(f"任务状态：{'已停止' if stopped else '本批次已完成'}")
    print(f"日志文件：{config.log_dir / 'batch.jsonl'}")
    print("=" * 70)

    if stopped:
        sys.exit(1)
    if total_stats.failed_parts > 0 or failed_files > 0:
        sys.exit(2)


def run(argv: list[str] | None = None) -> None:
    try:
        main(argv)
    except KeyboardInterrupt:
        print("\n操作已取消。")
        sys.exit(1)
    except Exception as exc:
        print(f"\n程序启动失败：{compact_error(exc)}")
        sys.exit(1)
