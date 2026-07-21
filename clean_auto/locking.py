from __future__ import annotations

import json
import os
import time
from pathlib import Path

from .config import compact_error, now_iso


def process_exists(pid: int) -> bool:
    """
    判断指定 PID 的进程是否仍然存在。

    Windows 和类 Unix 系统均可使用 os.kill(pid, 0)
    进行不发送真实信号的进程存在性检测。
    """
    if pid <= 0:
        return False

    try:
        os.kill(pid, 0)
    except PermissionError:
        # 进程存在，但当前用户无权访问。
        return True
    except ProcessLookupError:
        return False
    except OSError:
        return False

    return True


def read_lock_data(lock_file: Path) -> dict[str, object] | None:
    """
    读取锁文件。

    如果锁文件正在由另一个进程写入，短暂等待后重试，
    避免把刚刚创建但尚未写完的锁误判为失效锁。
    """
    for attempt in range(5):
        try:
            text = lock_file.read_text(
                encoding="utf-8",
            ).strip()

            if not text:
                raise ValueError("锁文件暂时为空")

            data = json.loads(text)

            if not isinstance(data, dict):
                raise ValueError("锁文件内容不是对象")

            return data

        except FileNotFoundError:
            return None
        except (OSError, ValueError, json.JSONDecodeError):
            if attempt < 4:
                time.sleep(0.2)

    return None


def read_lock_pid(lock_file: Path) -> int:
    """
    读取锁文件中的 PID。

    无法读取或 PID 非法时返回 0。
    """
    data = read_lock_data(lock_file)

    if data is None:
        return 0

    try:
        return int(data.get("pid", 0))
    except (TypeError, ValueError):
        return 0


def write_lock(fd: int) -> None:
    """
    将当前进程信息写入已经原子创建的锁文件。
    """
    payload = {
        "pid": os.getpid(),
        "created_at": now_iso(),
        "script": "rag-cleaner",
    }

    with os.fdopen(fd, "w", encoding="utf-8") as file:
        json.dump(
            payload,
            file,
            ensure_ascii=False,
            indent=2,
        )
        file.flush()

        try:
            os.fsync(file.fileno())
        except OSError:
            pass


def try_create_lock(lock_file: Path) -> bool:
    """
    原子创建锁文件。

    成功返回 True。
    文件已经存在时返回 False。
    """
    try:
        fd = os.open(
            str(lock_file),
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
        )
    except FileExistsError:
        return False

    try:
        write_lock(fd)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass

        try:
            lock_file.unlink(missing_ok=True)
        except OSError:
            pass

        raise

    return True


def acquire_lock(
    lock_file: Path,
    force_unlock: bool = False,
) -> None:
    """
    获取任务锁，防止多个实例同时处理同一个输出目录。

    处理规则：

    1. 首先使用 O_EXCL 原子创建锁；
    2. 如果锁对应的进程仍然存在，拒绝启动；
    3. 如果锁对应的进程已经退出，自动清理失效锁；
    4. 损坏或无法识别的锁默认不删除；
    5. 只有显式使用 --force-unlock 时，才删除损坏锁；
    6. 删除旧锁后仍然使用 O_EXCL 重新竞争，避免并发竞态。
    """
    lock_file.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    # 最常见路径：锁不存在，直接原子创建。
    if try_create_lock(lock_file):
        return

    # 锁存在，读取其内容。
    lock_data = read_lock_data(lock_file)

    if lock_data is None:
        # 无法确认锁是否属于正在启动的其他实例。
        if not force_unlock:
            raise RuntimeError(
                f"锁文件存在但内容无效或正在写入：{lock_file}。"
                "如果确认没有其他程序正在运行，"
                "请使用 --force-unlock。"
            )

        print(
            f"[提示] 根据 --force-unlock "
            f"清理无法识别的锁文件：{lock_file}"
        )

        try:
            lock_file.unlink()
        except FileNotFoundError:
            pass

    else:
        try:
            old_pid = int(lock_data.get("pid", 0))
        except (TypeError, ValueError):
            old_pid = 0

        if old_pid > 0 and process_exists(old_pid):
            raise RuntimeError(
                "检测到另一个 rag-cleaner 正在运行。"
                f"PID={old_pid}，锁文件：{lock_file}。"
                "请等待其结束，不要删除活动锁文件。"
            )

        if old_pid <= 0 and not force_unlock:
            raise RuntimeError(
                f"锁文件没有有效 PID：{lock_file}。"
                "如果确认没有其他程序正在运行，"
                "请使用 --force-unlock。"
            )

        print(
            f"[提示] 清理失效锁文件：{lock_file}"
        )

        try:
            lock_file.unlink()
        except FileNotFoundError:
            pass

    # 删除旧锁后重新原子竞争。
    # 如果此时另一个实例先获得锁，本实例必须退出。
    if not try_create_lock(lock_file):
        raise RuntimeError(
            "无法获得任务锁，另一个实例可能刚刚启动。"
        )


def release_lock(lock_file: Path) -> None:
    """
    释放属于当前进程的任务锁。

    如果锁已经被替换，绝不删除其他进程的锁。
    """
    try:
        if not lock_file.exists():
            return

        lock_pid = read_lock_pid(lock_file)

        if lock_pid == os.getpid():
            lock_file.unlink(missing_ok=True)
        elif lock_pid > 0:
            print(
                "[警告] 锁文件不属于当前进程，"
                "为避免影响其他实例，未删除锁文件。"
            )
        else:
            print(
                "[警告] 无法确认锁文件所有者，"
                "为避免误删，已保留锁文件。"
            )

    except Exception as exc:
        print(
            f"[警告] 释放锁失败："
            f"{compact_error(exc)}"
        )
