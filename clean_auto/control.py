from __future__ import annotations

import time
from pathlib import Path

from .config import GracefulStop


def wait_if_paused(
    pause_file: Path,
    stop_file: Path,
    poll_seconds: float = 2.0,
) -> None:
    announced = False
    while pause_file.exists():
        if stop_file.exists():
            raise GracefulStop(f"检测到停止文件：{stop_file}")
        if not announced:
            print(f"\n[暂停] 检测到 {pause_file.name}。删除该文件后继续。")
            announced = True
        time.sleep(poll_seconds)

    if announced:
        print("[继续] 暂停文件已删除，继续处理。")

    if stop_file.exists():
        raise GracefulStop(f"检测到停止文件：{stop_file}")


def controlled_sleep(seconds: float, pause_file: Path, stop_file: Path) -> None:
    if seconds <= 0:
        return
    end_time = time.monotonic() + seconds
    while True:
        wait_if_paused(pause_file=pause_file, stop_file=stop_file)
        remaining = end_time - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(1.0, remaining))


def wait_for_enter_or_stop(pause_file: Path, stop_file: Path) -> None:
    print("\n[暂停] 已达到 --pause-after-files 设置的数量。")
    print("按 Enter 继续；创建 stop.flag 后按 Enter 可停止。")
    while True:
        wait_if_paused(pause_file=pause_file, stop_file=stop_file)
        try:
            input()
            break
        except EOFError:
            break
    if stop_file.exists():
        raise GracefulStop(f"检测到停止文件：{stop_file}")
