from __future__ import annotations

from pathlib import Path

import pytest

import clean_auto.control as control
from clean_auto.config import GracefulStop


def test_wait_if_paused_resumes_after_pause_flag_is_removed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Cover pause polling, one-time announcement, and normal resume."""
    pause_file = tmp_path / "pause.flag"
    stop_file = tmp_path / "stop.flag"
    pause_file.write_text("pause", encoding="utf-8")
    sleep_calls: list[float] = []

    def remove_pause(seconds: float) -> None:
        sleep_calls.append(seconds)
        pause_file.unlink()

    monkeypatch.setattr(
        control.time,
        "sleep",
        remove_pause,
    )

    control.wait_if_paused(
        pause_file=pause_file,
        stop_file=stop_file,
        poll_seconds=0.25,
    )

    assert sleep_calls == [0.25]
    assert capsys.readouterr().out.count(pause_file.name) == 1


def test_stop_flag_interrupts_while_paused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover immediate cancellation when stop is requested during a pause."""
    pause_file = tmp_path / "pause.flag"
    stop_file = tmp_path / "stop.flag"
    pause_file.write_text("pause", encoding="utf-8")
    stop_file.write_text("stop", encoding="utf-8")
    monkeypatch.setattr(
        control.time,
        "sleep",
        lambda _seconds: pytest.fail("stop must be checked before sleeping"),
    )

    with pytest.raises(GracefulStop):
        control.wait_if_paused(
            pause_file=pause_file,
            stop_file=stop_file,
        )


def test_stop_flag_interrupts_without_pause(
    tmp_path: Path,
) -> None:
    """Cover the final stop check when no pause flag exists."""
    pause_file = tmp_path / "pause.flag"
    stop_file = tmp_path / "stop.flag"
    stop_file.write_text("stop", encoding="utf-8")

    with pytest.raises(GracefulStop):
        control.wait_if_paused(
            pause_file=pause_file,
            stop_file=stop_file,
        )


def test_controlled_sleep_is_cancelled_between_sleep_slices(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover cancellation before a long controlled sleep can finish."""
    pause_file = tmp_path / "pause.flag"
    stop_file = tmp_path / "stop.flag"
    sleep_calls: list[float] = []

    def request_stop(seconds: float) -> None:
        sleep_calls.append(seconds)
        stop_file.write_text("stop", encoding="utf-8")

    monkeypatch.setattr(
        control.time,
        "sleep",
        request_stop,
    )

    with pytest.raises(GracefulStop):
        control.controlled_sleep(
            seconds=10.0,
            pause_file=pause_file,
            stop_file=stop_file,
        )

    assert sleep_calls == [1.0]
