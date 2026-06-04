#!/usr/bin/env python3
"""Stop live Ubersight instances so wrappers can restart them.

The operator often runs ``scripts/ubersight_wrapper.sh`` in a side pane.
This utility gives agents one stable, preauthorizable command for restarting
that display after visual Ubersight changes without asking for approval to
kill unknown process IDs.
"""

from __future__ import annotations

import argparse
import os
import signal
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


@dataclass(frozen=True)
class UbersightProcess:
    """One matching live Ubersight process."""

    pid: int
    command: str


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
    """Return whether one command line appears to be a Ubersight dashboard."""

    names = {Path(part).name for part in command.split() if part}
    return bool({"ubersight.py", "ubersight"} & names)


def find_ubersight_processes(
    proc_root: Path = Path("/proc"),
) -> tuple[UbersightProcess, ...]:
    """Return same-user Ubersight dashboard processes, excluding this process."""

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
        matches.append(UbersightProcess(pid, command))
    return tuple(sorted(matches, key=lambda process: process.pid))


def _process_exists(pid: int) -> bool:
    return Path("/proc", str(pid)).exists()


def stop_ubersight_processes(
    processes: Sequence[UbersightProcess],
    *,
    dry_run: bool = False,
    force: bool = False,
    wait_seconds: float = 2.0,
) -> int:
    """Terminate Ubersight processes and return the number signaled."""

    if dry_run:
        return len(processes)
    for process in processes:
        try:
            os.kill(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            continue
    deadline = time.monotonic() + max(0.0, wait_seconds)
    while time.monotonic() < deadline:
        if not any(_process_exists(process.pid) for process in processes):
            break
        time.sleep(0.05)
    if force:
        for process in processes:
            if not _process_exists(process.pid):
                continue
            try:
                os.kill(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                continue
    return len(processes)


def build_parser() -> argparse.ArgumentParser:
    """Create the command-line parser."""

    parser = argparse.ArgumentParser(
        description="Stop same-user Ubersight dashboard processes."
    )
    parser.add_argument("--dry-run", action="store_true", help="list matches only")
    parser.add_argument(
        "--force",
        action="store_true",
        help="send SIGKILL after SIGTERM and the wait interval",
    )
    parser.add_argument(
        "--wait-seconds",
        type=float,
        default=2.0,
        help="seconds to wait after SIGTERM before optional --force",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run Ubersight stopper."""

    args = build_parser().parse_args(argv)
    processes = find_ubersight_processes()
    for process in processes:
        prefix = "would stop" if args.dry_run else "stopping"
        print(f"[ubersight-stop] {prefix}: {process.pid} {process.command}")
    count = stop_ubersight_processes(
        processes,
        dry_run=args.dry_run,
        force=args.force,
        wait_seconds=args.wait_seconds,
    )
    print(f"[ubersight-stop] matched {count} Ubersight process(es).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
