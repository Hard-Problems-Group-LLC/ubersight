"""Tests for the cross-platform stop and restart workflow (Slice 13).

These cover the dashboard's restart signal, the `--supervise` loop, and the
`ubersight-stop` helper. Tests marked ``windows_only`` use real named events
and processes; they always restrict discovery to their own child processes so
the operator's live dashboards are never signaled.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from ubersight import dashboard as ubersight
from ubersight import stop_ubersight, windows_processes

windows_only = pytest.mark.skipif(os.name != "nt", reason="requires native Windows")
ROOT = Path(__file__).resolve().parents[1]
# A Windows venv's python.exe is a redirector that starts the real
# interpreter as a child; the base interpreter keeps one process per PID.
BASE_PYTHON = getattr(sys, "_base_executable", sys.executable)

EVENT_CHILD = """
import sys, time
from ubersight import windows_processes
event = windows_processes.RestartEvent.create()
print("ready" if event else "no-event", flush=True)
deadline = time.monotonic() + 30
while time.monotonic() < deadline:
    if event is not None and event.is_set():
        sys.exit(75)
    time.sleep(0.02)
sys.exit(3)
"""


def spawn_event_child() -> subprocess.Popen[str]:
    """Start a child that behaves like a dashboard waiting for a restart."""

    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    child = subprocess.Popen(
        [BASE_PYTHON, "-c", EVENT_CHILD],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        text=True,
        env=env,
    )
    assert child.stdout is not None
    assert child.stdout.readline().strip() == "ready"
    return child


def descendants_snapshot(root_pid: int):
    """Return a snapshot function limited to `root_pid` and its descendants."""

    def snapshot():
        entries = windows_processes.snapshot_processes()
        tree = windows_processes.build_process_tree(
            root_pid,
            entries,
            lambda pid: windows_processes.probe_process(pid).creation_filetime,
        )
        return tree

    return snapshot


# Restart signal -----------------------------------------------------------


def test_posix_restart_signal_catches_sigterm_and_restores_handler(
    monkeypatch,
) -> None:
    monkeypatch.setattr(ubersight, "_is_windows", lambda: False)
    previous = signal.getsignal(signal.SIGTERM)
    restart = ubersight.RestartSignal()

    restart.install()
    try:
        assert not restart.requested()
        signal.raise_signal(signal.SIGTERM)
        assert restart.requested()
    finally:
        restart.close()

    assert signal.getsignal(signal.SIGTERM) == previous


@windows_only
def test_windows_restart_signal_uses_named_event() -> None:
    restart = ubersight.RestartSignal()
    restart.install()
    try:
        assert windows_processes.has_restart_event(os.getpid())
        assert not restart.requested()
        assert windows_processes.signal_restart_event(os.getpid())
        assert restart.requested()
    finally:
        restart.close()

    assert not windows_processes.has_restart_event(os.getpid())


def test_restart_event_helpers_report_missing_dashboards() -> None:
    assert windows_processes.restart_event_name(42).endswith("-restart-42")
    assert not windows_processes.has_restart_event(0)
    assert not windows_processes.signal_restart_event(-1)
    assert not windows_processes.terminate_process(0, None)


# Supervisor ---------------------------------------------------------------


def scripted_run(statuses: list[object]):
    """Return a fake `subprocess.run` replaying statuses or exceptions."""

    calls: list[list[str]] = []

    def run(command, **kwargs):
        calls.append(command)
        outcome = statuses.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return subprocess.CompletedProcess(command, outcome)

    return run, calls


def test_supervisor_restarts_on_request_and_crash_then_exits_on_quit() -> None:
    run, calls = scripted_run([75, 1, 0])
    sleeps: list[float] = []
    notes: list[str] = []

    status = ubersight.run_supervised(
        ["--no-geolocate"], run=run, sleep=sleeps.append, log=notes.append
    )

    assert status == 0
    assert len(calls) == 3
    assert calls[0] == [sys.executable, "-m", "ubersight", "--no-geolocate"]
    assert sleeps == [ubersight.SUPERVISOR_CRASH_DELAY_SECONDS]
    assert "restart requested" in notes[0]
    assert "status 1" in notes[1]


@pytest.mark.parametrize(
    "outcome",
    [130, ubersight.WINDOWS_CONTROL_C_EXIT, KeyboardInterrupt()],
)
def test_supervisor_stops_on_ctrl_c(outcome: object) -> None:
    run, calls = scripted_run([outcome])

    assert ubersight.run_supervised([], run=run, sleep=lambda _: None) == 130
    assert len(calls) == 1


def test_supervisor_restarts_after_forced_stop() -> None:
    run, calls = scripted_run([-9 if os.name != "nt" else 1, 0])

    assert ubersight.run_supervised([], run=run, sleep=lambda _: None) == 0
    assert len(calls) == 2


def test_supervisor_stops_when_interrupted_during_crash_delay() -> None:
    run, _ = scripted_run([1])

    def interrupted_sleep(seconds: float) -> None:
        raise KeyboardInterrupt

    assert ubersight.run_supervised([], run=run, sleep=interrupted_sleep) == 130


def test_main_supervise_strips_flag_and_rejects_snapshot_modes(
    monkeypatch, capsys
) -> None:
    captured: list[list[str]] = []
    monkeypatch.setattr(
        ubersight, "run_supervised", lambda args: captured.append(list(args)) or 0
    )

    assert ubersight.main(["--supervise", "--refresh-ms", "500"]) == 0
    assert captured == [["--refresh-ms", "500"]]
    with pytest.raises(SystemExit):
        ubersight.main(["--supervise", "--once"])
    assert "--supervise cannot be combined" in capsys.readouterr().err


# ubersight-stop ------------------------------------------------------------


def test_dashboard_command_matching_excludes_supervisors() -> None:
    assert stop_ubersight._is_ubersight_dashboard_command(
        "/repo/.venv/bin/python -m ubersight --refresh-ms 500"
    )
    assert not stop_ubersight._is_ubersight_dashboard_command(
        "/repo/.venv/bin/python -m ubersight --supervise"
    )
    assert not stop_ubersight._is_ubersight_dashboard_command(
        "/usr/bin/python3 /home/op/.local/bin/ubersight-autosplit"
    )


def fake_processes() -> tuple[stop_ubersight.UbersightProcess, ...]:
    return (
        stop_ubersight.UbersightProcess(11, "python.exe", "100"),
        stop_ubersight.UbersightProcess(22, "python.exe", "200"),
    )


def test_stop_waits_for_requested_dashboards_to_exit() -> None:
    requested: list[int] = []
    running = {11: [True, False], 22: [True, True, False]}
    clock = iter(range(100))

    def is_running(process):
        states = running[process.pid]
        return states.pop(0) if len(states) > 1 else states[0]

    remaining = stop_ubersight.stop_ubersight_processes(
        fake_processes(),
        wait_seconds=5,
        request=lambda process: requested.append(process.pid) or True,
        is_running=is_running,
        force_stop_process=lambda process: pytest.fail("no force requested"),
        sleep=lambda _: None,
        monotonic=lambda: next(clock) * 0.1,
    )

    assert requested == [11, 22]
    assert remaining == ()


def test_stop_reports_unresponsive_dashboards_and_forces_when_asked() -> None:
    forced: list[int] = []
    clock = iter(range(1000))

    def run(force: bool):
        return stop_ubersight.stop_ubersight_processes(
            fake_processes(),
            wait_seconds=0.5,
            force=force,
            request=lambda process: True,
            is_running=lambda process: process.pid not in forced,
            force_stop_process=lambda process: forced.append(process.pid) or True,
            sleep=lambda _: None,
            monotonic=lambda: next(clock) * 0.1,
        )

    assert [process.pid for process in run(force=False)] == [11, 22]
    assert run(force=True) == ()
    assert forced == [11, 22]


def test_dry_run_and_empty_matches_do_nothing() -> None:
    def forbidden(process):
        raise AssertionError("must not act")

    assert (
        stop_ubersight.stop_ubersight_processes(
            fake_processes(), dry_run=True, request=forbidden
        )
        == ()
    )
    assert stop_ubersight.stop_ubersight_processes((), request=forbidden) == ()


def test_main_reports_and_exits_nonzero_when_dashboards_remain(
    monkeypatch, capsys
) -> None:
    monkeypatch.setattr(stop_ubersight, "find_dashboards", fake_processes)
    monkeypatch.setattr(
        stop_ubersight,
        "stop_ubersight_processes",
        lambda processes, **kwargs: processes[1:],
    )

    assert stop_ubersight.main([]) == 1
    output = capsys.readouterr().out
    assert "restarting: 11 python.exe" in output
    assert "still running: 22 python.exe" in output
    assert "matched 2 Ubersight dashboard(s); 1 still running." in output


def test_windows_discovery_uses_restart_events(monkeypatch) -> None:
    entries = (
        windows_processes.ProcessEntry(os.getpid(), 1, "python.exe"),
        windows_processes.ProcessEntry(31, 1, "python.exe"),
        windows_processes.ProcessEntry(32, 1, "notepad.exe"),
    )
    monkeypatch.setattr(ubersight, "_process_start_ticks", lambda pid: f"t{pid}")

    found = stop_ubersight.find_windows_dashboards(
        snapshot=lambda: entries,
        has_restart_event=lambda pid: pid in {os.getpid(), 31},
    )

    assert found == (stop_ubersight.UbersightProcess(31, "python.exe", "t31"),)


@windows_only
def test_stop_restarts_a_real_windows_dashboard_child() -> None:
    child = spawn_event_child()
    try:
        processes = stop_ubersight.find_windows_dashboards(
            snapshot=descendants_snapshot(child.pid)
        )
        assert [process.pid for process in processes] == [child.pid]

        remaining = stop_ubersight.stop_ubersight_processes(processes, wait_seconds=10)

        assert remaining == ()
        assert child.wait(timeout=10) == ubersight.RESTART_EXIT_CODE
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=10)
        if child.stdout is not None:
            child.stdout.close()


@windows_only
def test_force_stop_ends_only_the_discovered_process() -> None:
    sleeper = subprocess.Popen(
        [BASE_PYTHON, "-c", "import time; time.sleep(30)"],
        stdin=subprocess.DEVNULL,
    )
    try:
        ticks = ubersight._process_start_ticks(sleeper.pid)
        assert ticks
        stale = stop_ubersight.UbersightProcess(
            sleeper.pid, "python.exe", str(int(ticks) - 1)
        )
        assert not stop_ubersight.force_stop(stale)
        assert sleeper.poll() is None

        current = stop_ubersight.UbersightProcess(sleeper.pid, "python.exe", ticks)
        assert stop_ubersight.force_stop(current)
        assert sleeper.wait(timeout=10) == windows_processes.FORCED_EXIT_CODE
    finally:
        if sleeper.poll() is None:
            sleeper.kill()
            sleeper.wait(timeout=10)


@windows_only
def test_supervised_dashboard_restarts_in_its_own_console(tmp_path: Path) -> None:
    """Run `ubersight --supervise` in a hidden console and restart it."""

    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0  # SW_HIDE
    command = [
        sys.executable,
        "-m",
        "ubersight",
        "--supervise",
        "--no-geolocate",
        "--no-origin-fetch",
        "--no-tailscale",
        "--refresh-ms",
        "200",
        "--status",
        str(tmp_path / "status.json"),
        "--origin-cache",
        str(tmp_path / "origin.json"),
        "--location-cache",
        str(tmp_path / "location.json"),
        "--location-observations",
        str(tmp_path / "observations.jsonl"),
        "--background-job-dir",
        str(tmp_path / "jobs"),
    ]
    # Only stderr is redirected: curses still needs the console for output.
    log_path = tmp_path / "supervisor.log"
    log_file = log_path.open("w", encoding="utf-8")
    supervisor = subprocess.Popen(
        command,
        cwd=tmp_path,
        creationflags=subprocess.CREATE_NEW_CONSOLE,
        startupinfo=startup,
        stderr=log_file,
    )
    snapshot = descendants_snapshot(supervisor.pid)

    def wait_for_dashboard(exclude: set[int]) -> stop_ubersight.UbersightProcess:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            assert supervisor.poll() is None, "supervisor exited unexpectedly"
            found = [
                process
                for process in stop_ubersight.find_windows_dashboards(snapshot=snapshot)
                if process.pid not in exclude
            ]
            if found:
                return found[0]
            time.sleep(0.1)
        pytest.fail("dashboard did not create its restart event")

    try:
        first = wait_for_dashboard(set())
        remaining = stop_ubersight.stop_ubersight_processes((first,), wait_seconds=15)
        assert remaining == ()

        second = wait_for_dashboard({first.pid})
        assert second.pid != first.pid
        assert supervisor.poll() is None
    finally:
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(supervisor.pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        supervisor.wait(timeout=15)
        log_file.close()

    # The new dashboard must come from the restart path, not a crash restart.
    log = log_path.read_text(encoding="utf-8", errors="replace")
    assert "restart requested" in log
    assert "exited with status" not in log
    assert "Traceback" not in log
