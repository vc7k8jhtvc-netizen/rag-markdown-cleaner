from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from typing import Literal

from .config import (
    FilePlan,
    GracefulStop,
    ProcessOutcome,
    ProcessStats,
    RuntimeConfig,
    compact_error,
    sha256_file,
)
from .control import wait_if_paused
from .processor import process_file


FileStatus = Literal["succeeded", "failed", "interrupted"]
ProcessFunction = Callable[..., ProcessOutcome]


@dataclass(frozen=True)
class FileResult:
    index: int
    plan: FilePlan
    status: FileStatus
    stats: ProcessStats
    error: str | None = None
    stop_requested: bool = False


@dataclass(frozen=True)
class SchedulerSummary:
    stopped: bool
    submitted: int
    completed: int
    max_in_flight: int


def ensure_source_unchanged(plan: FilePlan) -> None:
    current_sha256 = sha256_file(plan.source_path)
    if current_sha256 != plan.source_sha256:
        raise RuntimeError(
            "源文件在生成处理计划后发生变化，"
            f"拒绝处理：{plan.relative_path} (SHA-256 不一致)"
        )


def _run_file(
    index: int,
    plan: FilePlan,
    *,
    total_files: int,
    config: RuntimeConfig,
    client: object,
    process_fn: ProcessFunction,
) -> FileResult:
    try:
        wait_if_paused(config.pause_file, config.stop_file)
        ensure_source_unchanged(plan)
        outcome = process_fn(
            plan=plan,
            file_index=index + 1,
            total_files=total_files,
            config=config,
            client=client,
            initial_consecutive_failures=0,
        )
        status: FileStatus = (
            "failed"
            if outcome.stats.failed_parts > 0 or outcome.stopped
            else "succeeded"
        )
        error = "文件包含失败分片" if status == "failed" else None
        return FileResult(
            index=index,
            plan=plan,
            status=status,
            stats=outcome.stats,
            error=error,
        )
    except (GracefulStop, KeyboardInterrupt) as exc:
        return FileResult(
            index=index,
            plan=plan,
            status="interrupted",
            stats=ProcessStats(total_parts=len(plan.chunks)),
            error=compact_error(exc),
            stop_requested=True,
        )
    except Exception as exc:
        return FileResult(
            index=index,
            plan=plan,
            status="failed",
            stats=ProcessStats(total_parts=len(plan.chunks)),
            error=compact_error(exc),
        )


def run_file_scheduler(
    plans: list[FilePlan],
    *,
    config: RuntimeConfig,
    client: object,
    workers: int,
    process_fn: ProcessFunction = process_file,
    on_started: Callable[[int, FilePlan], None] | None = None,
    on_result: Callable[[FileResult], None] | None = None,
    on_cancelled: Callable[[int, FilePlan], None] | None = None,
) -> SchedulerSummary:
    if not 2 <= workers <= 5:
        raise RuntimeError("并发调度器 workers 必须在 2 到 5 之间")

    next_index = 0
    submitted = 0
    completed = 0
    max_in_flight = 0
    stopped = False
    futures: dict[Future[FileResult], tuple[int, FilePlan]] = {}

    def submit_available(executor: ThreadPoolExecutor) -> None:
        nonlocal next_index, submitted, max_in_flight, stopped
        while next_index < len(plans) and len(futures) < workers:
            if config.stop_file.exists():
                stopped = True
                return
            index = next_index
            plan = plans[index]
            next_index += 1
            if on_started is not None:
                on_started(index, plan)
            future = executor.submit(
                _run_file,
                index,
                plan,
                total_files=len(plans),
                config=config,
                client=client,
                process_fn=process_fn,
            )
            futures[future] = (index, plan)
            submitted += 1
            max_in_flight = max(max_in_flight, len(futures))

    with ThreadPoolExecutor(
        max_workers=workers,
        thread_name_prefix="rag-cleaner-file",
    ) as executor:
        submit_available(executor)

        while futures:
            done, _ = wait(futures, return_when=FIRST_COMPLETED)
            for future in done:
                futures.pop(future)
                if future.cancelled():
                    continue
                result = future.result()
                completed += 1
                if on_result is not None:
                    on_result(result)
                if result.stop_requested or config.stop_file.exists():
                    stopped = True

            if stopped:
                for future, (index, plan) in list(futures.items()):
                    if future.cancel():
                        futures.pop(future)
                        if on_cancelled is not None:
                            on_cancelled(index, plan)
            else:
                submit_available(executor)

    return SchedulerSummary(
        stopped=stopped,
        submitted=submitted,
        completed=completed,
        max_in_flight=max_in_flight,
    )
