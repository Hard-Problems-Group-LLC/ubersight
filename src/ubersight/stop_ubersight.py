#!/usr/bin/env python3
"""Ask live Ubersight dashboards to restart, so new code takes effect.

Agents run ``ubersight-stop`` after visual Ubersight changes. It gives them
one stable, preauthorizable command instead of approving kills of unknown
process IDs.

A request makes each dashboard leave curses cleanly and exit with status
75. A dashboard running under ``ubersight --supervise`` (or the older POSIX
``scripts/ubersight_wrapper.sh``) then starts again with the new code. An
unsupervised dashboard just exits.

Finding dashboards:

- **Windows:** each dashboard creates a named restart event in the logon
  session, so this helper reaches it even from a sandboxed agent session.
- **POSIX:** same-user dashboard processes are found in ``/proc`` and sent
  ``SIGTERM``, which the dashboard treats as a restart request.
"""

from __future__ import annotations

import argparse
import os
import signal
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from . import dashboard, windows_processes

DEFAULT_WAIT_SECONDS = 5.0


@dataclass(frozen=True)
class UbersightProcess:
    """One matching live Ubersight dashboard process.

    ``start_ticks`` records the process identity at discovery time, so
    waiting and forcing never act on a PID that was reused afterward.
    """

    pid: int
    command: str
    start_ticks: str = ""


def _read_cmdline(path: Path) -> str:
    try:
        raw = path.read_bytes()
    except OSError:
        return ""
    return " ".join(
        part.decode("utf-8", errors="replace") for part in raw.split(b"\0") if part
    )


def _read_uid(path: Path) -> int | None:
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("Uid:"):
                return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        return None
    return None


def _is_ubersight_dashboard_command(command: str) -> bool:
    """Return whether one command line appears to be a Ubersight dashboard.

    Supervisors run the same entry point with ``--supervise``; they are not
    dashboards, and stopping one would end the restart loop.
    """

    parts = [part for part in command.split() if part]
    if "--supervise" in parts:
        return False
    names = {Path(part).name for part in parts}
    return bool({"ubersight.py", "ubersight"} & names)


def find_ubersight_processes(
    proc_root: Path = Path("/proc"),
) -> tuple[UbersightProcess, ...]:
    """Return same-user POSIX dashboard processes, excluding this process."""

    current_pid = os.getpid()
    current_uid = os.getuid()
    matches: list[UbersightProcess] = []
    try:
        entries = tuple(proc_root.iterdir())
    except OSError:
        return ()
    for entry in entries:
        if not entry.name.isdecimal():
            continue
        pid = int(entry.name)
        if pid == current_pid:
            continue
        if _read_uid(entry / "status") != current_uid:
            continue
        command = _read_cmdline(entry / "cmdline")
        if not command:
            continue
        if not _is_ubersight_dashboard_command(command):
            continue
        matches.append(
            UbersightProcess(pid, command, dashboard._process_start_ticks(pid))
        )
    return tuple(sorted(matches, key=lambda process: process.pid))


def find_windows_dashboards(
    *,
    snapshot: Callable[[], Sequence[windows_processes.ProcessEntry]] | None = None,
    has_restart_event: Callable[[int], bool] | None = None,
) -> tuple[UbersightProcess, ...]:
    """Return session dashboards that expose a restart event, excluding this one."""

    take_snapshot = snapshot or windows_processes.snapshot_processes
    is_dashboard = has_restart_event or windows_processes.has_restart_event
    current_pid = os.getpid()
    matches = [
        UbersightProcess(
            entry.pid, entry.exe_name, dashboard._process_start_ticks(entry.pid)
        )
        for entry in take_snapshot()
        if entry.pid != current_pid and is_dashboard(entry.pid)
    ]
    return tuple(sorted(matches, key=lambda process: process.pid))


def find_dashboards() -> tuple[UbersightProcess, ...]:
    """Return the live dashboards this helper can reach on this platform."""

    if os.name == "nt":
        return find_windows_dashboards()
    return find_ubersight_processes()


def _is_running(process: UbersightProcess) -> bool:
    """Return whether the discovered process is still the same live process."""

    return dashboard._process_identity_is_live(
        process.pid, expected_start_ticks=process.start_ticks
    )


def request_restart(process: UbersightProcess) -> bool:
    """Send one dashboard a restart request; return whether it was delivered."""

    if os.name == "nt":
        return windows_processes.signal_restart_event(process.pid)
    try:
        os.kill(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return False
    return True


def force_stop(process: UbersightProcess) -> bool:
    """Forcibly end a dashboard that is still the discovered process."""

    if not _is_running(process):
        return False
    if os.name == "nt":
        expected = int(process.start_ticks) if process.start_ticks.isdecimal() else None
        return windows_processes.terminate_process(process.pid, expected)
    try:
        os.kill(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return False
    return True


def stop_ubersight_processes(
    processes: Sequence[UbersightProcess],
    *,
    dry_run: bool = False,
    force: bool = False,
    wait_seconds: float = DEFAULT_WAIT_SECONDS,
    request: Callable[[UbersightProcess], bool] = request_restart,
    is_running: Callable[[UbersightProcess], bool] = _is_running,
    force_stop_process: Callable[[UbersightProcess], bool] = force_stop,
    sleep: Callable[[float], Any] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> tuple[UbersightProcess, ...]:
    """Request restarts and return the dashboards still running afterward.

    Waits up to ``wait_seconds`` for every requested dashboard to exit. With
    ``force``, dashboards that did not respond are ended forcibly.
    """

    if dry_run or not processes:
        return ()
    for process in processes:
        request(process)
    deadline = monotonic() + max(0.0, wait_seconds)
    remaining = [process for process in processes if is_running(process)]
    while remaining and monotonic() < deadline:
        sleep(0.1)
        remaining = [process for process in remaining if is_running(process)]
    if force:
        for process in remaining:
            force_stop_process(process)
        remaining = [process for process in remaining if is_running(process)]
    return tuple(remaining)


def build_parser() -> argparse.ArgumentParser:
    """Create the command-line parser."""

    parser = argparse.ArgumentParser(
        description=(
            "Ask same-user Ubersight dashboards to restart. Supervised "
            "dashboards (ubersight --supervise) start again with new code."
        )
    )
    parser.add_argument("--dry-run", action="store_true", help="list matches only")
    parser.add_argument(
        "--force",
        action="store_true",
        help="after the wait, forcibly end dashboards that did not exit",
    )
    parser.add_argument(
        "--wait-seconds",
        type=float,
        default=DEFAULT_WAIT_SECONDS,
        help="seconds to wait for dashboards to exit (default 5)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Request dashboard restarts and report the outcome.

    Returns 0 when every matched dashboard exited and 1 when some are still
    running.
    """

    args = build_parser().parse_args(argv)
    processes = find_dashboards()
    for process in processes:
        prefix = "would restart" if args.dry_run else "restarting"
        print(f"[ubersight-stop] {prefix}: {process.pid} {process.command}")
    remaining = stop_ubersight_processes(
        processes,
        dry_run=args.dry_run,
        force=args.force,
        wait_seconds=args.wait_seconds,
    )
    for process in remaining:
        print(f"[ubersight-stop] still running: {process.pid} {process.command}")
    print(
        f"[ubersight-stop] matched {len(processes)} Ubersight dashboard(s); "
        f"{len(remaining)} still running."
    )
    return 1 if remaining else 0


if __name__ == "__main__":
    raise SystemExit(main())
