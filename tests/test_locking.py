from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import clean_auto.locking as locking


def _write_lock_data(
    lock_file: Path,
    pid: object,
) -> None:
    lock_file.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    lock_file.write_text(
        json.dumps({"pid": pid}),
        encoding="utf-8",
    )


def test_acquire_lock_atomically_creates_owned_lock(
    tmp_path: Path,
) -> None:
    """Cover fresh acquisition and ownership metadata used by release_lock."""
    lock_file = tmp_path / "run.lock"

    locking.acquire_lock(lock_file)

    data = json.loads(
        lock_file.read_text(encoding="utf-8")
    )
    assert data["pid"] == os.getpid()
    assert data["script"] == "rag-cleaner"
    assert data["created_at"]


@pytest.mark.parametrize("force_unlock", [False, True])
def test_active_lock_is_never_removed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    force_unlock: bool,
) -> None:
    """Cover the safety rule that force-unlock cannot replace a live owner."""
    lock_file = tmp_path / "run.lock"
    _write_lock_data(lock_file, os.getpid())
    monkeypatch.setattr(
        locking,
        "process_exists",
        lambda _pid: True,
    )

    with pytest.raises(RuntimeError):
        locking.acquire_lock(
            lock_file,
            force_unlock=force_unlock,
        )

    assert json.loads(
        lock_file.read_text(encoding="utf-8")
    )["pid"] == os.getpid()


def test_stale_lock_is_replaced_by_current_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover automatic stale-lock cleanup followed by atomic reacquisition."""
    lock_file = tmp_path / "run.lock"
    _write_lock_data(lock_file, 12345)
    monkeypatch.setattr(
        locking,
        "process_exists",
        lambda _pid: False,
    )

    locking.acquire_lock(lock_file)

    assert json.loads(
        lock_file.read_text(encoding="utf-8")
    )["pid"] == os.getpid()


def test_corrupt_lock_requires_force_unlock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover conservative handling of unreadable locks and explicit recovery."""
    lock_file = tmp_path / "run.lock"
    lock_file.write_text("not-json", encoding="utf-8")
    monkeypatch.setattr(
        locking.time,
        "sleep",
        lambda _seconds: None,
    )

    with pytest.raises(RuntimeError):
        locking.acquire_lock(lock_file)

    assert lock_file.read_text(encoding="utf-8") == "not-json"

    locking.acquire_lock(
        lock_file,
        force_unlock=True,
    )

    assert json.loads(
        lock_file.read_text(encoding="utf-8")
    )["pid"] == os.getpid()


def test_invalid_pid_requires_force_unlock(
    tmp_path: Path,
) -> None:
    """Cover valid JSON whose PID cannot identify a lock owner."""
    lock_file = tmp_path / "run.lock"
    _write_lock_data(lock_file, "invalid")

    with pytest.raises(RuntimeError):
        locking.acquire_lock(lock_file)

    assert lock_file.exists()

    locking.acquire_lock(
        lock_file,
        force_unlock=True,
    )
    assert json.loads(
        lock_file.read_text(encoding="utf-8")
    )["pid"] == os.getpid()


def test_reacquisition_race_preserves_competing_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover losing the O_EXCL race after a stale lock has been removed."""
    lock_file = tmp_path / "run.lock"
    _write_lock_data(lock_file, 12345)
    monkeypatch.setattr(
        locking,
        "process_exists",
        lambda _pid: False,
    )
    attempts = 0

    def fake_try_create(path: Path) -> bool:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return False

        _write_lock_data(path, 67890)
        return False

    monkeypatch.setattr(
        locking,
        "try_create_lock",
        fake_try_create,
    )

    with pytest.raises(RuntimeError):
        locking.acquire_lock(lock_file)

    assert attempts == 2
    assert json.loads(
        lock_file.read_text(encoding="utf-8")
    )["pid"] == 67890


def test_lock_write_failure_removes_partial_lock_and_closes_fd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover cleanup when writing a newly-created lock fails mid-acquisition."""
    lock_file = tmp_path / "run.lock"
    closed_fds: list[int] = []
    original_close = locking.os.close

    def fail_write(_fd: int) -> None:
        raise OSError("write failed")

    def record_close(fd: int) -> None:
        closed_fds.append(fd)
        original_close(fd)

    monkeypatch.setattr(locking, "write_lock", fail_write)
    monkeypatch.setattr(locking.os, "close", record_close)

    with pytest.raises(OSError, match="write failed"):
        locking.try_create_lock(lock_file)

    assert closed_fds
    assert not lock_file.exists()


def test_release_lock_only_removes_current_process_lock(
    tmp_path: Path,
) -> None:
    """Cover ownership enforcement while releasing a lock."""
    owned_lock = tmp_path / "owned.lock"
    foreign_lock = tmp_path / "foreign.lock"
    _write_lock_data(owned_lock, os.getpid())
    _write_lock_data(foreign_lock, os.getpid() + 1)

    locking.release_lock(owned_lock)
    locking.release_lock(foreign_lock)

    assert not owned_lock.exists()
    assert foreign_lock.exists()


def test_release_lock_preserves_corrupt_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover fail-closed release behavior when ownership cannot be verified."""
    lock_file = tmp_path / "run.lock"
    lock_file.write_text("not-json", encoding="utf-8")
    monkeypatch.setattr(
        locking.time,
        "sleep",
        lambda _seconds: None,
    )

    locking.release_lock(lock_file)

    assert lock_file.read_text(encoding="utf-8") == "not-json"
