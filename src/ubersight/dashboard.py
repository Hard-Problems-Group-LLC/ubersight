#!/usr/bin/env python3
"""Terminal Ubersight side-window dashboard for long Codex phase/slice work.

Ubersight is intentionally small and local. It reads a checkout-local JSON
status file, optionally summarizes a process subtree, and renders a compact
dashboard that can sit next to a Codex terminal in a split pane. It never
reads prompts, transcripts, or repository source content.
"""

from __future__ import annotations

import argparse
import getpass
import ipaddress
import json
import os
import re
import signal
import socket
import subprocess
import sys
import textwrap
import time
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from . import atomic_files
from . import operator_location_memory_store as location_memory
from . import windows_processes

DEFAULT_STATUS_PATH = Path(".local/ubersight/status.json")
# Windows keeps project-scoped runtime state under the project's ignored
# `.local/` tree; POSIX hosts use a per-user runtime directory instead.
# Material that must be visible across projects belongs under
# `%LOCALAPPDATA%\ubersight` on Windows; nothing needs that today.
WINDOWS_PROJECT_RUNTIME_ROOT = Path(".local/ubersight")
GIT_SSH_BATCH_OPTIONS = "-o BatchMode=yes -o ConnectTimeout=5"
# Exit statuses shared by the dashboard, `ubersight --supervise`, and
# `ubersight-stop`. 75 is EX_TEMPFAIL: the dashboard should start again.
RESTART_EXIT_CODE = 75
KEYBOARD_INTERRUPT_EXIT_CODE = 130
WINDOWS_CONTROL_C_EXIT = 0xC000013A
SUPERVISOR_CRASH_DELAY_SECONDS = 1.0
DEFAULT_ORIGIN_CACHE_PATH = Path(".local/ubersight/origin-cache.json")
DEFAULT_LOCATION_CACHE_PATH = Path(".local/ubersight/location-cache.json")
DEFAULT_TAILSCALE_CACHE_PATH = Path(".local/ubersight/tailscale-cache.json")
DEFAULT_LOCATION_MEMORY_PATH = location_memory.DEFAULT_LOCATION_MEMORY_PATH
DEFAULT_LOCATION_MEMORY_SALT_PATH = location_memory.DEFAULT_LOCATION_MEMORY_SALT_PATH
DEFAULT_LOCATION_OBSERVATIONS_PATH = location_memory.DEFAULT_LOCATION_OBSERVATIONS_PATH
DEFAULT_NETWORK_PROFILES_PATH = location_memory.DEFAULT_NETWORK_PROFILES_PATH
DEFAULT_SNAPSHOT_WIDTH = 40
DEFAULT_SNAPSHOT_HEIGHT = 40
DEFAULT_ORIGIN_FETCH_TIMEOUT_SECONDS = 8.0
BEACONDB_GEOLOCATE_URL = "https://api.beacondb.net/v1/geolocate"
HOTKEY_BAR_TEXT = "(R)efresh  (E)nter location  (Q)uit"
OPSEC_FOOTER_TEXT = (
    "LOCAL STATUS / LOCATION ESTIMATE; external IP/OSM lookups may occur; "
    "keep cache local"
)
OPSEC_PUBLIC_FOOTER_TEXT = (
    "PUBLIC NETWORK; assume hostile transport; external IP/OSM lookups may "
    "occur; keep cache local"
)
SCHEMA = "ubersight.status.v1"
BACKGROUND_JOB_SCHEMA = "ubersight.background-job-status.v1"
ORIGIN_CACHE_SCHEMA = "ubersight.origin-cache.v1"
LOCATION_CACHE_SCHEMA = "ubersight.location-cache.v6"
TAILSCALE_CACHE_SCHEMA = "ubersight.tailscale-cache.v1"
CONTEXT_ENV = "UBERSIGHT_PROJECT_NAME"
DEFAULT_BACKGROUND_JOB_SUBDIRECTORY = "background-jobs"
VALID_SLICE_STATES = {"pending", "active", "done", "failed", "blocked", "skipped"}
VALID_ORIGIN_KINDS = {"synced", "out_of_date", "conflict", "complicated", "newer"}
VALID_LOCAL_KINDS = {"clean", "uncommitted"}
VALID_TAILSCALE_KINDS = {"up", "down", "logged_out", "error"}
VALID_BACKGROUND_JOB_STATES = {
    "complete",
    "error",
    "interrupted",
    "paused",
    "running",
    "starting",
}


@dataclass(frozen=True)
class SliceStatus:
    """One ordered phase/slice row in the side-window display."""

    slice_id: str
    title: str
    state: str = "pending"


@dataclass(frozen=True)
class PhaseStatus:
    """One ordered high-level phase row in the side-window display."""

    phase_id: str
    title: str
    state: str = "pending"


@dataclass(frozen=True)
class UbersightStatus:
    """High-level agent-authored state loaded from the status JSON file."""

    phase_id: str
    phase_title: str
    slices: tuple[SliceStatus, ...]
    notes: str
    phases: tuple[PhaseStatus, ...] = ()
    blocked: bool = False
    complete: bool = False
    updated_at: datetime | None = None
    source_path: Path | None = None
    source_error: str = ""


@dataclass(frozen=True)
class WorkerProcess:
    """Best-effort summary for one process in a monitored subtree."""

    pid: int
    ppid: int
    state: str
    command: str
    elapsed_seconds: float | None = None


@dataclass(frozen=True)
class CommandResult:
    """Small subprocess result model used by testable Ubersight probes."""

    args: tuple[str, ...]
    returncode: int
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True)
class OriginStatus:
    """Cached relationship between the current branch and its upstream."""

    kind: str
    label: str
    updated_at: datetime | None = None
    marker: str = ""
    details: str = ""


@dataclass(frozen=True)
class LocalStatus:
    """Current local worktree state that is separate from upstream status."""

    kind: str
    label: str
    updated_at: datetime | None = None
    details: str = ""


@dataclass(frozen=True)
class OperatorLocationStatus:
    """Best-effort operator environment and physical-location summary."""

    hostname: str
    username: str
    wifi_ssid: str
    location: str
    updated_at: datetime | None = None
    source: str = ""
    fine_location: str = ""
    opsec_level: str = ""
    network_observed_at: datetime | None = None
    network_fingerprint: str = ""
    wired: bool = False
    # Label of the matched location-memory record, as named with `E`.
    network_label: str = ""


@dataclass(frozen=True)
class LocationEntryDefaults:
    """Prefill values for the interactive local location-memory editor.

    `record_id` names the matched record, so saving updates it even when
    the place is renamed.
    """

    label: str
    fine_location: str
    opsec_level: str
    confidence_threshold: float
    matched: bool
    stable_identity: bool
    lan_name: str = ""
    record_id: str = ""


@dataclass(frozen=True)
class TailscaleStatus:
    """Local-only Tailnet status for the side-window OPSEC area."""

    kind: str
    label: str
    updated_at: datetime | None = None
    tailnet_alias: str = ""
    details: str = ""


@dataclass(frozen=True)
class BackgroundJobStatus:
    """One active Ubersight runtime background-job status file."""

    job_id: str
    pid: int
    state: str
    updated_at: datetime | None = None
    app: str = ""
    category: str = ""
    source: str = ""
    subject: str = ""
    verb: str = ""
    ticker: str = ""
    work_completed: float | None = None
    work_total: float | None = None
    work_unit: str = ""
    bytes_completed: float | None = None
    bytes_total: float | None = None
    work_items_per_second: float | None = None
    bytes_per_second: float | None = None
    eta_seconds: float | None = None
    message: str = ""
    source_path: Path | None = None


@dataclass(frozen=True)
class NetworkLocationEstimate:
    """Network-derived coarse location used as context for venue inference."""

    location: str
    source: str
    latitude: float | None = None
    longitude: float | None = None
    uncertainty_meters: float | None = None


def _local_now() -> datetime:
    """Return a timezone-aware local timestamp."""

    return datetime.now().astimezone()


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.astimezone()
    return parsed


def _timestamp_age_seconds(timestamp: datetime | None, now: datetime) -> float | None:
    if timestamp is None:
        return None
    return max(0.0, (now - timestamp.astimezone(now.tzinfo)).total_seconds())


def _is_windows() -> bool:
    """Return whether Ubersight is running on native Windows."""

    return os.name == "nt"


def _configured_git_ssh_command(cwd: Path | None) -> str:
    """Return the repository's `core.sshCommand`, or an empty string."""

    try:
        completed = subprocess.run(
            ["git", "config", "--get", "core.sshCommand"],
            cwd=str(cwd) if cwd is not None else None,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            encoding="utf-8",
            errors="replace",
            timeout=5.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()


def _git_ssh_command(
    env: dict[str, str],
    cwd: Path | None,
    *,
    configured_ssh_command: Any = _configured_git_ssh_command,
) -> str | None:
    """Return the batch-mode `GIT_SSH_COMMAND` for a fetch, or `None`.

    An environment `GIT_SSH_COMMAND` takes precedence over `core.sshCommand`,
    so a blanket default would silently replace a user's configured SSH
    client, such as Windows OpenSSH with its agent. Ubersight therefore keeps
    an explicit environment choice, extends a configured command with batch
    options, and only falls back to plain `ssh` when nothing is configured.
    """

    if env.get("GIT_SSH_COMMAND") or env.get("GIT_SSH"):
        return None
    configured = configured_ssh_command(cwd)
    if configured:
        return f"{configured} {GIT_SSH_BATCH_OPTIONS}"
    return f"ssh {GIT_SSH_BATCH_OPTIONS}"


def _run_command(
    args: Sequence[str],
    *,
    cwd: Path | None = None,
    timeout_seconds: float = 10.0,
) -> CommandResult:
    """Run a short local command without invoking a shell.

    Probes read from the null device so no command can consume the
    dashboard's terminal input, and output is decoded as UTF-8 with
    replacement so an unexpected byte cannot raise.
    """

    env = os.environ.copy()
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    # Read-only probes such as `git status` otherwise take `.git/index.lock`
    # to refresh cached file stats. Polling every second, that makes the
    # operator's own `git add` or commit fail at random with a lock error.
    env.setdefault("GIT_OPTIONAL_LOCKS", "0")
    # Only fetches reach a remote, so only they pay for the extra
    # `git config` lookup that preserves a configured SSH client.
    if tuple(args[:2]) == ("git", "fetch"):
        ssh_command = _git_ssh_command(env, cwd)
        if ssh_command is not None:
            env["GIT_SSH_COMMAND"] = ssh_command
    try:
        completed = subprocess.run(
            list(args),
            cwd=str(cwd) if cwd is not None else None,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CommandResult(tuple(args), 127, "", str(exc))
    return CommandResult(
        tuple(args),
        completed.returncode,
        _decode_probe_output(completed.stdout),
        _decode_probe_output(completed.stderr),
    )


def _decode_probe_output(data: bytes | str | None) -> str:
    """Decode probe output without ever raising.

    Git, Tailscale, and modern tools write UTF-8. Windows console tools such
    as `netsh` write the OEM code page when piped, so non-ASCII WiFi names
    need that codec once UTF-8 fails. Anything else is decoded with
    replacement characters.
    """

    if data is None:
        return ""
    if isinstance(data, str):
        return data
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    if _is_windows():
        try:
            return data.decode("oem")
        except (LookupError, UnicodeDecodeError):
            pass
    return data.decode("utf-8", errors="replace")


def _coerce_status_row(raw: Any, *, id_key: str) -> tuple[str, str, str] | None:
    if not isinstance(raw, dict):
        return None
    item_id = str(raw.get("id", raw.get(id_key, ""))).strip()
    title = str(raw.get("title", "")).strip()
    state = str(raw.get("state", "pending")).strip().lower()
    if not item_id and not title:
        return None
    if state not in VALID_SLICE_STATES:
        state = "pending"
    return item_id, title, state


def _coerce_slice(raw: Any) -> SliceStatus | None:
    coerced = _coerce_status_row(raw, id_key="slice_id")
    if coerced is None:
        return None
    slice_id, title, state = coerced
    return SliceStatus(slice_id=slice_id, title=title, state=state)


def _coerce_phase(raw: Any) -> PhaseStatus | None:
    coerced = _coerce_status_row(raw, id_key="phase_id")
    if coerced is None:
        return None
    phase_id, title, state = coerced
    return PhaseStatus(phase_id=phase_id, title=title, state=state)


def status_from_mapping(
    raw: dict[str, Any], source_path: Path | None = None
) -> UbersightStatus:
    """Normalize a status-file mapping into Ubersight's internal model."""

    phase = raw.get("phase")
    if not isinstance(phase, dict):
        phase = {}
    phase_id = str(phase.get("id", raw.get("phase_id", ""))).strip()
    phase_title = str(phase.get("title", raw.get("phase_title", ""))).strip()
    phases = tuple(
        phase_status
        for phase_status in (_coerce_phase(item) for item in raw.get("phases", []))
        if phase_status is not None
    )
    slices = tuple(
        slice_status
        for slice_status in (_coerce_slice(item) for item in raw.get("slices", []))
        if slice_status is not None
    )
    notes = str(raw.get("notes", "")).strip()
    return UbersightStatus(
        phase_id=phase_id,
        phase_title=phase_title,
        phases=phases,
        slices=slices,
        notes=notes,
        blocked=bool(raw.get("blocked", False)),
        complete=bool(raw.get("complete", False)),
        updated_at=_parse_timestamp(raw.get("updated_at")),
        source_path=source_path,
    )


def load_status(path: Path) -> UbersightStatus:
    """Load status JSON, returning a displayable fallback on missing/bad input."""

    if not path.exists():
        return UbersightStatus(
            phase_id="",
            phase_title="No Ubersight status file",
            slices=(),
            notes=(
                "No status file exists yet. Start Codex, then publish status with "
                "ubersight --write-status or let the agent update "
                ".local/ubersight/status.json."
            ),
            source_path=path,
            source_error="missing",
        )
    try:
        raw = json.loads(atomic_files.read_text_with_retry(path))
    except OSError as exc:
        return UbersightStatus(
            phase_id="",
            phase_title="Could not read status",
            slices=(),
            notes=str(exc),
            source_path=path,
            source_error=str(exc),
        )
    except json.JSONDecodeError as exc:
        return UbersightStatus(
            phase_id="",
            phase_title="Invalid status JSON",
            slices=(),
            notes=f"{exc.msg} at line {exc.lineno}, column {exc.colno}",
            source_path=path,
            source_error=str(exc),
        )
    if not isinstance(raw, dict):
        return UbersightStatus(
            phase_id="",
            phase_title="Invalid status JSON",
            slices=(),
            notes="The status file root must be a JSON object.",
            source_path=path,
            source_error="root-not-object",
        )
    return status_from_mapping(raw, source_path=path)


def _slice_to_mapping(slice_status: SliceStatus) -> dict[str, str]:
    return {
        "id": slice_status.slice_id,
        "title": slice_status.title,
        "state": slice_status.state,
    }


def _phase_to_mapping(phase_status: PhaseStatus) -> dict[str, str]:
    return {
        "id": phase_status.phase_id,
        "title": phase_status.title,
        "state": phase_status.state,
    }


def write_status_file(status: UbersightStatus, path: Path) -> None:
    """Atomically write Ubersight status JSON."""

    payload = {
        "schema": SCHEMA,
        "updated_at": _local_now().isoformat(timespec="seconds"),
        "phase": {"id": status.phase_id, "title": status.phase_title},
        "phases": [_phase_to_mapping(phase_status) for phase_status in status.phases],
        "slices": [_slice_to_mapping(slice_status) for slice_status in status.slices],
        "notes": status.notes,
        "blocked": status.blocked,
        "complete": status.complete,
    }
    atomic_files.atomic_write_text(
        path, json.dumps(payload, indent=2, sort_keys=True) + "\n"
    )


def parse_slice_option(value: str) -> SliceStatus:
    """Parse ``ID:STATE:TITLE`` command-line slice descriptions."""

    parts = value.split(":", 2)
    if len(parts) != 3:
        raise argparse.ArgumentTypeError("slice must use ID:STATE:TITLE")
    slice_id, state, title = (part.strip() for part in parts)
    if not slice_id:
        raise argparse.ArgumentTypeError("slice ID must not be empty")
    if state not in VALID_SLICE_STATES:
        states = ", ".join(sorted(VALID_SLICE_STATES))
        raise argparse.ArgumentTypeError(f"slice state must be one of: {states}")
    if not title:
        raise argparse.ArgumentTypeError("slice title must not be empty")
    return SliceStatus(slice_id=slice_id, title=title, state=state)


def parse_phase_option(value: str) -> PhaseStatus:
    """Parse ``ID:STATE:TITLE`` command-line phase descriptions."""

    parts = value.split(":", 2)
    if len(parts) != 3:
        raise argparse.ArgumentTypeError("phase row must use ID:STATE:TITLE")
    phase_id, state, title = (part.strip() for part in parts)
    if not phase_id:
        raise argparse.ArgumentTypeError("phase ID must not be empty")
    if state not in VALID_SLICE_STATES:
        states = ", ".join(sorted(VALID_SLICE_STATES))
        raise argparse.ArgumentTypeError(f"phase state must be one of: {states}")
    if not title:
        raise argparse.ArgumentTypeError("phase title must not be empty")
    return PhaseStatus(phase_id=phase_id, title=title, state=state)


def _origin_label(kind: str) -> str:
    labels = {
        "synced": "ORIGIN: SYNCED",
        "out_of_date": "ORIGIN: OUT-OF-DATE",
        "conflict": "ORIGIN: CONFLICT",
        "complicated": "ORIGIN: IT'S COMPLICATED",
        "newer": "ORIGIN: NEWER",
    }
    return labels.get(kind, labels["complicated"])


def _local_label(kind: str, width: int) -> str:
    if kind != "uncommitted":
        return ""
    full = "LOCAL: UNCOMMITTED"
    narrow = "LOCAL: DIRTY"
    return narrow if width < len(full) else full


def _work_safety_label(
    origin_status: OriginStatus,
    local_status: LocalStatus | None,
    width: int,
) -> str:
    """Summarize whether current work is operationally safe in one row."""

    if local_status is not None and local_status.kind == "uncommitted":
        full = "WORK: UNCOMMITTED LOCAL"
        short = "WORK: LOCAL"
    elif origin_status.kind == "synced":
        full = "WORK: SAFE, SYNCED"
        short = "WORK: SAFE"
    elif origin_status.kind == "out_of_date":
        full = "WORK: PULL FIRST"
        short = "WORK: PULL"
    elif origin_status.kind == "newer":
        full = "WORK: UNPUSHED COMMITS"
        short = "WORK: PUSH"
    elif origin_status.kind == "conflict":
        full = "WORK: CONFLICT"
        short = "WORK: CONFLICT"
    else:
        full = "WORK: CHECK GIT"
        short = "WORK: CHECK"
    return short if width < len(full) else full


def _coerce_origin_status(raw: dict[str, Any]) -> OriginStatus:
    kind = str(raw.get("kind", "complicated")).strip()
    if kind not in VALID_ORIGIN_KINDS:
        kind = "complicated"
    return OriginStatus(
        kind=kind,
        label=str(raw.get("label", _origin_label(kind))).strip() or _origin_label(kind),
        updated_at=_parse_timestamp(raw.get("updated_at")),
        marker=str(raw.get("marker", "")),
        details=str(raw.get("details", "")),
    )


def _load_origin_cache(path: Path) -> OriginStatus | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    return _coerce_origin_status(raw)


def _write_origin_cache(path: Path, status: OriginStatus) -> None:
    payload = {
        "schema": ORIGIN_CACHE_SCHEMA,
        "updated_at": (status.updated_at or _local_now()).isoformat(timespec="seconds"),
        "kind": status.kind,
        "label": status.label,
        "marker": status.marker,
        "details": status.details,
    }
    atomic_files.atomic_write_text(
        path, json.dumps(payload, indent=2, sort_keys=True) + "\n"
    )


def _git_status_marker(
    repo_root: Path,
    run_command: Any = _run_command,
) -> str:
    result = run_command(
        ("git", "status", "--porcelain=v2", "--branch"),
        cwd=repo_root,
        timeout_seconds=10.0,
    )
    if result.returncode != 0:
        return f"git-status-error:{result.stderr.strip()}"
    branch_lines = [
        line for line in result.stdout.splitlines() if line.startswith("# branch.")
    ]
    return "\n".join(branch_lines)


def _parse_origin_status_output(
    output: str, *, marker: str, now: datetime
) -> OriginStatus:
    branch_head = ""
    upstream = ""
    ahead = None
    behind = None
    has_unmerged = False
    for line in output.splitlines():
        if line.startswith("u "):
            has_unmerged = True
        if line.startswith("# branch.head "):
            branch_head = line.removeprefix("# branch.head ").strip()
        elif line.startswith("# branch.upstream "):
            upstream = line.removeprefix("# branch.upstream ").strip()
        elif line.startswith("# branch.ab "):
            parts = line.removeprefix("# branch.ab ").split()
            for part in parts:
                if part.startswith("+"):
                    try:
                        ahead = int(part[1:])
                    except ValueError:
                        ahead = None
                elif part.startswith("-"):
                    try:
                        behind = int(part[1:])
                    except ValueError:
                        behind = None

    kind = "complicated"
    details = ""
    if has_unmerged:
        kind = "conflict"
        details = "working tree has unmerged paths"
    elif not branch_head or branch_head == "(detached)" or not upstream:
        kind = "complicated"
        details = "branch has no ordinary upstream"
    elif ahead is None or behind is None:
        kind = "complicated"
        details = "git did not report ahead/behind counts"
    elif ahead == 0 and behind == 0:
        kind = "synced"
    elif ahead == 0 and behind > 0:
        kind = "out_of_date"
    elif ahead > 0 and behind == 0:
        kind = "newer"
    elif ahead > 0 and behind > 0:
        kind = "conflict"
        details = f"local is {ahead} ahead and {behind} behind {upstream}"

    return OriginStatus(
        kind=kind,
        label=_origin_label(kind),
        updated_at=now,
        marker=marker,
        details=details,
    )


def _refresh_origin_status(
    repo_root: Path,
    *,
    now: datetime,
    run_command: Any = _run_command,
    fetch: bool = True,
    fetch_timeout_seconds: float = DEFAULT_ORIGIN_FETCH_TIMEOUT_SECONDS,
) -> OriginStatus:
    if fetch:
        fetch_result = run_command(
            ("git", "fetch", "--quiet", "origin"),
            cwd=repo_root,
            timeout_seconds=fetch_timeout_seconds,
        )
        if fetch_result.returncode != 0:
            marker = _git_status_marker(repo_root, run_command=run_command)
            return OriginStatus(
                kind="complicated",
                label=_origin_label("complicated"),
                updated_at=now,
                marker=marker,
                details=fetch_result.stderr.strip() or "git fetch origin failed",
            )
    status_result = run_command(
        ("git", "status", "--porcelain=v2", "--branch"),
        cwd=repo_root,
        timeout_seconds=10.0,
    )
    marker = (
        "\n".join(
            line
            for line in status_result.stdout.splitlines()
            if line.startswith("# branch.")
        )
        if status_result.returncode == 0
        else f"git-status-error:{status_result.stderr.strip()}"
    )
    if status_result.returncode != 0:
        return OriginStatus(
            kind="complicated",
            label=_origin_label("complicated"),
            updated_at=now,
            marker=marker,
            details=status_result.stderr.strip() or "git status failed",
        )
    return _parse_origin_status_output(status_result.stdout, marker=marker, now=now)


def load_or_refresh_origin_status(
    repo_root: Path,
    cache_path: Path,
    *,
    now: datetime | None = None,
    max_age_seconds: int = 3600,
    run_command: Any = _run_command,
    fetch: bool = True,
    fetch_timeout_seconds: float = DEFAULT_ORIGIN_FETCH_TIMEOUT_SECONDS,
) -> OriginStatus:
    """Return cached origin status, refreshing after Git changes or one hour."""

    now = now or _local_now()
    marker = _git_status_marker(repo_root, run_command=run_command)
    cached = _load_origin_cache(cache_path)
    if cached is not None:
        age = _timestamp_age_seconds(cached.updated_at, now)
        if age is not None and age <= max_age_seconds and cached.marker == marker:
            return cached
    refreshed = _refresh_origin_status(
        repo_root,
        now=now,
        run_command=run_command,
        fetch=fetch,
        fetch_timeout_seconds=fetch_timeout_seconds,
    )
    _write_origin_cache(cache_path, refreshed)
    return refreshed


def _has_uncommitted_changes(status_output: str) -> bool:
    """Return whether porcelain output contains uncommitted local changes."""

    for line in status_output.splitlines():
        if not line or line.startswith("#"):
            continue
        return True
    return False


def load_local_status(
    repo_root: Path,
    *,
    now: datetime | None = None,
    run_command: Any = _run_command,
) -> LocalStatus | None:
    """Return local worktree warning status, if one is currently applicable."""

    result = run_command(
        ("git", "status", "--porcelain=v1", "--untracked-files=all"),
        cwd=repo_root,
        timeout_seconds=10.0,
    )
    now = now or _local_now()
    if result.returncode != 0:
        return LocalStatus(
            "clean",
            "",
            updated_at=now,
            details=result.stderr.strip() or "git status failed",
        )
    if _has_uncommitted_changes(result.stdout):
        return LocalStatus(
            "uncommitted",
            "LOCAL: UNCOMMITTED",
            updated_at=now,
            details="working tree has uncommitted or untracked files",
        )
    return None


def _tailscale_label(kind: str, tailnet_alias: str = "") -> str:
    """Return the display label for a Tailnet status without leaking details."""

    normalized_alias = tailnet_alias.strip()
    if kind == "up" and normalized_alias:
        body = f"{normalized_alias} UP"
    elif kind == "down":
        body = f"{normalized_alias} DOWN" if normalized_alias else "DOWN"
    elif kind == "logged_out":
        body = "LOGGED OUT"
    elif kind == "error":
        body = "ERROR"
    else:
        body = "ERROR"
    return f"Tailscale: {body}"


def _coerce_tailscale_status(raw: dict[str, Any]) -> TailscaleStatus | None:
    kind = str(raw.get("kind", "error")).strip()
    if kind not in VALID_TAILSCALE_KINDS:
        kind = "error"
    alias = str(raw.get("tailnet_alias", "")).strip()
    return TailscaleStatus(
        kind=kind,
        label=str(raw.get("label", _tailscale_label(kind, alias))).strip()
        or _tailscale_label(kind, alias),
        updated_at=_parse_timestamp(raw.get("updated_at")),
        tailnet_alias=alias,
        details=str(raw.get("details", "")).strip(),
    )


def _load_tailscale_cache(path: Path) -> TailscaleStatus | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict) or raw.get("schema") != TAILSCALE_CACHE_SCHEMA:
        return None
    return _coerce_tailscale_status(raw)


def _write_tailscale_cache(path: Path, status: TailscaleStatus) -> None:
    payload = {
        "schema": TAILSCALE_CACHE_SCHEMA,
        "updated_at": (status.updated_at or _local_now()).isoformat(timespec="seconds"),
        "kind": status.kind,
        "label": status.label,
        "tailnet_alias": status.tailnet_alias,
        "details": status.details,
    }
    atomic_files.atomic_write_text(
        path, json.dumps(payload, indent=2, sort_keys=True) + "\n"
    )


def _parse_tailscale_dns_alias(dns_name: str) -> str:
    """Extract the Tailnet DNS alias from a MagicDNS host name."""

    normalized = dns_name.strip().strip(".").casefold()
    suffix = ".ts.net"
    if not normalized.endswith(suffix):
        return ""
    labels = normalized[: -len(suffix)].split(".")
    if len(labels) < 2:
        return ""
    alias = ".".join(label for label in labels[1:] if label)
    return alias


def _tailscale_alias_from_payload(payload: dict[str, Any]) -> str:
    self_record = payload.get("Self")
    if isinstance(self_record, dict):
        dns_name = str(self_record.get("DNSName", "")).strip()
        alias = _parse_tailscale_dns_alias(dns_name)
        if alias:
            return alias

    current_tailnet = payload.get("CurrentTailnet")
    if isinstance(current_tailnet, dict):
        for key in ("MagicDNSSuffix", "Name"):
            value = str(current_tailnet.get(key, "")).strip().strip(".").casefold()
            if not value:
                continue
            suffix = ".ts.net"
            if value.endswith(suffix):
                value = value[: -len(suffix)]
            if value:
                return value
    return ""


def _parse_tailscale_status_payload(
    payload: dict[str, Any],
    *,
    now: datetime,
    fallback_alias: str = "",
) -> TailscaleStatus:
    backend_state = str(payload.get("BackendState", "")).strip()
    state = backend_state.casefold().replace("-", "").replace("_", "")
    alias = _tailscale_alias_from_payload(payload) or fallback_alias.strip()
    logged_out_states = {"needslogin", "needsmachineauth"}
    down_states = {"nostate", "starting", "stopped"}
    if state == "running":
        kind = "up" if alias else "error"
        details = "" if alias else "running but MagicDNS alias was unavailable"
    elif state in logged_out_states:
        kind = "logged_out"
        details = backend_state
        alias = ""
    elif state in down_states:
        kind = "down"
        details = backend_state
    else:
        kind = "error"
        details = backend_state or "missing BackendState"
    return TailscaleStatus(
        kind=kind,
        label=_tailscale_label(kind, alias),
        updated_at=now,
        tailnet_alias=alias if kind in {"up", "down"} else "",
        details=details,
    )


def _tailscale_status_from_command_result(
    result: CommandResult,
    *,
    now: datetime,
    fallback_alias: str = "",
) -> TailscaleStatus:
    combined = f"{result.stdout}\n{result.stderr}".casefold()
    if result.returncode != 0:
        alias = fallback_alias.strip()
        if "not logged in" in combined or "logged out" in combined:
            kind = "logged_out"
            alias = ""
        elif (
            "not running" in combined
            or "stopped" in combined
            or "failed to connect to local tailscaled" in combined
            or result.returncode == 127
        ):
            kind = "down"
        else:
            kind = "error"
            alias = ""
        return TailscaleStatus(
            kind=kind,
            label=_tailscale_label(kind, alias),
            updated_at=now,
            tailnet_alias=alias if kind == "down" else "",
            details=result.stderr.strip() or result.stdout.strip(),
        )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return TailscaleStatus(
            "error",
            _tailscale_label("error"),
            updated_at=now,
            details="tailscale status returned invalid JSON",
        )
    if not isinstance(payload, dict):
        return TailscaleStatus(
            "error",
            _tailscale_label("error"),
            updated_at=now,
            details="tailscale status JSON root was not an object",
        )
    return _parse_tailscale_status_payload(
        payload, now=now, fallback_alias=fallback_alias
    )


def _refresh_tailscale_status(
    *,
    now: datetime,
    fallback_alias: str = "",
    run_command: Any = _run_command,
) -> TailscaleStatus:
    result = run_command(
        ("tailscale", "status", "--json"),
        timeout_seconds=3.0,
    )
    return _tailscale_status_from_command_result(
        result,
        now=now,
        fallback_alias=fallback_alias,
    )


def load_or_refresh_tailscale_status(
    cache_path: Path,
    *,
    now: datetime | None = None,
    max_age_seconds: int = 15,
    run_command: Any = _run_command,
) -> TailscaleStatus:
    """Return short-lived local Tailnet status without touching tracked files."""

    now = now or _local_now()
    cached = _load_tailscale_cache(cache_path)
    if cached is not None:
        age = _timestamp_age_seconds(cached.updated_at, now)
        if age is not None and age <= max_age_seconds:
            return cached
    refreshed = _refresh_tailscale_status(
        now=now,
        fallback_alias=cached.tailnet_alias if cached is not None else "",
        run_command=run_command,
    )
    _write_tailscale_cache(cache_path, refreshed)
    return refreshed


def _coerce_float(value: Any) -> float | None:
    """Return a finite float or `None` for missing/non-numeric values."""

    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in {float("inf"), float("-inf")}:
        return None
    return number


def _safe_context_name(value: str) -> str:
    """Return a filesystem-safe runtime context name."""

    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip())
    return normalized.strip(".-") or "ubersight"


def default_project_name(cwd: Path | None = None) -> str:
    """Return the default project/context name for this terminal session."""

    environment_context = os.environ.get(CONTEXT_ENV, "").strip()
    if environment_context:
        return _safe_context_name(environment_context)
    path = cwd or Path.cwd()
    return _safe_context_name(path.name or "ubersight")


def ubersight_runtime_root() -> Path:
    """Return the runtime root for Ubersight background-job status files.

    POSIX hosts use a per-user runtime directory shared by every project
    context. Windows keeps project-scoped runtime state under the project's
    ignored `.local/ubersight` tree, relative to the working directory like
    the other Ubersight defaults.
    """

    if _is_windows():
        return WINDOWS_PROJECT_RUNTIME_ROOT
    xdg_runtime = os.environ.get("XDG_RUNTIME_DIR", "").strip()
    if xdg_runtime:
        return Path(xdg_runtime) / "ubersight"
    run_user = Path("/run/user") / str(os.getuid())
    if run_user.is_dir():
        return run_user / "ubersight"
    return Path("/tmp") / f"ubersight-{os.getuid()}"


def ubersight_context_root(context: str | None = None) -> Path:
    """Return a runtime context root such as `.../ubersight/example-app`.

    On Windows the project-local runtime root already identifies the
    project, so the context name selects only the banner, not a directory.
    """

    if _is_windows():
        return ubersight_runtime_root()
    return ubersight_runtime_root() / _safe_context_name(
        context or default_project_name()
    )


def background_job_dir(context: str | None = None) -> Path:
    """Return the directory holding active background-job status files."""

    return ubersight_context_root(context) / DEFAULT_BACKGROUND_JOB_SUBDIRECTORY


def _boot_id() -> str:
    """Return the Linux boot ID, or an empty string where none applies.

    Windows process creation times are absolute and unique across boots, so
    Windows needs no boot ID and returns an empty string, which disables
    boot-ID comparison.
    """

    if _is_windows():
        return ""
    try:
        return (
            Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()
        )
    except OSError:
        return ""


def _process_start_ticks(pid: int) -> str:
    """Return a process start token, or an empty string when unavailable.

    Linux reports `/proc/<pid>/stat` start ticks since boot. Windows reports
    the process creation time as decimal FILETIME ticks, which producers can
    read with `(Get-Process -Id $PID).StartTime.ToFileTimeUtc()`.
    """

    if _is_windows():
        creation = windows_processes.process_creation_filetime(pid)
        return "" if creation is None else str(creation)
    try:
        stat = (Path("/proc") / str(pid) / "stat").read_text(encoding="utf-8")
    except OSError:
        return ""
    try:
        after_comm = stat.rsplit(")", 1)[1].strip()
    except IndexError:
        return ""
    fields = after_comm.split()
    if len(fields) < 20:
        return ""
    return fields[19]


def _process_identity_is_live(
    pid: int,
    *,
    expected_boot_id: str = "",
    expected_start_ticks: str = "",
) -> bool:
    """Return whether `pid` appears to be the same live process."""

    if _is_windows():
        return _windows_process_identity_is_live(
            pid, expected_start_ticks=expected_start_ticks
        )
    proc_dir = Path("/proc") / str(pid)
    if not proc_dir.exists():
        return False
    if expected_boot_id and _boot_id() and expected_boot_id != _boot_id():
        return False
    if expected_start_ticks:
        current_start = _process_start_ticks(pid)
        if current_start and current_start != expected_start_ticks:
            return False
    return True


def _windows_process_identity_is_live(
    pid: int,
    *,
    expected_start_ticks: str = "",
    probe_process: Any = None,
) -> bool:
    """Return whether a Windows `pid` is still the recorded live process.

    Only proof of exit, absence, or a different creation time counts as
    dead. When Windows refuses inspection, the job file is kept rather than
    deleting status for a process that may still be running.
    """

    probe = (probe_process or windows_processes.probe_process)(pid)
    if probe.state in {
        windows_processes.PROCESS_MISSING,
        windows_processes.PROCESS_EXITED,
    }:
        return False
    if probe.state != windows_processes.PROCESS_RUNNING:
        return True
    if expected_start_ticks and probe.creation_filetime is not None:
        return str(probe.creation_filetime) == expected_start_ticks
    return True


def _coerce_background_job(
    raw: dict[str, Any],
    *,
    source_path: Path,
) -> BackgroundJobStatus | None:
    """Normalize one runtime background-job JSON object."""

    if raw.get("schema") != BACKGROUND_JOB_SCHEMA:
        return None
    try:
        pid = int(raw.get("pid", 0))
    except (TypeError, ValueError):
        return None
    if pid <= 0:
        return None
    state = str(raw.get("state", "running")).strip().casefold()
    if state not in VALID_BACKGROUND_JOB_STATES:
        state = "running"
    job_id = str(raw.get("job_id", source_path.stem)).strip() or source_path.stem
    return BackgroundJobStatus(
        job_id=job_id,
        pid=pid,
        state=state,
        updated_at=_parse_timestamp(raw.get("updated_at")),
        app=str(raw.get("app", "")).strip(),
        category=str(raw.get("category", "")).strip(),
        source=str(raw.get("source", "")).strip(),
        subject=str(raw.get("subject", "")).strip(),
        verb=str(raw.get("verb", "")).strip(),
        ticker=str(raw.get("ticker", "")).strip(),
        work_completed=_coerce_float(raw.get("work_completed")),
        work_total=_coerce_float(raw.get("work_total")),
        work_unit=str(raw.get("work_unit", "")).strip(),
        bytes_completed=_coerce_float(raw.get("bytes_completed")),
        bytes_total=_coerce_float(raw.get("bytes_total")),
        work_items_per_second=_coerce_float(raw.get("work_items_per_second")),
        bytes_per_second=_coerce_float(raw.get("bytes_per_second")),
        eta_seconds=_coerce_float(raw.get("eta_seconds")),
        message=str(raw.get("message", "")).strip(),
        source_path=source_path,
    )


def _load_background_job_file(path: Path) -> BackgroundJobStatus | None:
    """Load one background-job file when it is well-formed and live."""

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    status = _coerce_background_job(raw, source_path=path)
    if status is None:
        return None
    if not _process_identity_is_live(
        status.pid,
        expected_boot_id=str(raw.get("boot_id", "")).strip(),
        expected_start_ticks=str(raw.get("process_start_ticks", "")).strip(),
    ):
        _unlink_background_job_file(path)
        return None
    if status.state == "complete":
        _unlink_background_job_file(path)
        return None
    return status


def _unlink_background_job_file(path: Path) -> None:
    """Best-effort cleanup for stale or complete job status files."""

    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def load_background_jobs(background_job_dir: Path) -> tuple[BackgroundJobStatus, ...]:
    """Load all live background-job files from one directory."""

    try:
        paths = sorted(background_job_dir.glob("*.json"))
    except OSError:
        return ()
    statuses = [
        status
        for status in (_load_background_job_file(path) for path in paths)
        if status is not None
    ]
    return tuple(statuses)


def _coerce_location_status(raw: dict[str, Any]) -> OperatorLocationStatus | None:
    hostname = str(raw.get("hostname", "")).strip()
    username = str(raw.get("username", "")).strip()
    if not hostname or not username:
        return None
    return OperatorLocationStatus(
        hostname=hostname,
        username=username,
        wifi_ssid=str(raw.get("wifi_ssid", "")).strip() or "unknown",
        location=str(raw.get("location", "")).strip() or "unknown",
        updated_at=_parse_timestamp(raw.get("updated_at")),
        source=str(raw.get("source", "")).strip(),
        fine_location=str(raw.get("fine_location", "")).strip(),
        opsec_level=str(raw.get("opsec_level", "")).strip(),
        network_observed_at=_parse_timestamp(raw.get("network_observed_at")),
        network_fingerprint=str(raw.get("network_fingerprint", "")).strip(),
        wired=raw.get("wired") is True,
        network_label=str(raw.get("network_label", "")).strip(),
    )


def _load_location_cache(
    path: Path, *, accept_legacy_schema: bool = False
) -> OperatorLocationStatus | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    if not accept_legacy_schema and raw.get("schema") != LOCATION_CACHE_SCHEMA:
        return None
    return _coerce_location_status(raw)


def _write_location_cache(path: Path, status: OperatorLocationStatus) -> None:
    payload = {
        "schema": LOCATION_CACHE_SCHEMA,
        "updated_at": (status.updated_at or _local_now()).isoformat(timespec="seconds"),
        "hostname": status.hostname,
        "username": status.username,
        "wifi_ssid": status.wifi_ssid,
        "location": status.location,
        "fine_location": status.fine_location,
        "opsec_level": status.opsec_level,
        "source": status.source,
        "network_fingerprint": status.network_fingerprint,
        "wired": status.wired,
        "network_label": status.network_label,
        "network_observed_at": (
            status.network_observed_at or status.updated_at or _local_now()
        ).isoformat(timespec="seconds"),
    }
    atomic_files.atomic_write_text(
        path, json.dumps(payload, indent=2, sort_keys=True) + "\n"
    )


def observe_operator_location(
    *,
    run_command: Any = _run_command,
    geolocate: bool = True,
    urlopen_func: Any = urlopen,
    fallback_ssid: str = "",
) -> location_memory.LocationObservation:
    """Collect local-only signals describing the current operator environment."""

    network_estimate = (
        _geolocation_from_ip(urlopen_func=urlopen_func) if geolocate else None
    )
    wifi_ssid = _active_wifi_ssid(run_command=run_command, fallback_ssid=fallback_ssid)
    gateway_macs = _default_gateway_macs(run_command=run_command)
    # Wired only when WiFi is affirmatively off-line: a WLAN query that
    # Windows withholds for location permission must not read as a cable.
    wired = (
        wifi_ssid == "unknown"
        and bool(gateway_macs)
        and _wifi_reported_disconnected(run_command)
    )
    return location_memory.LocationObservation(
        hostname=socket.gethostname(),
        username=getpass.getuser(),
        wifi_ssid=wifi_ssid,
        wifi_bssid=_active_wifi_bssid(run_command=run_command),
        local_subnets=_local_ipv4_subnets(run_command=run_command),
        gateway_macs=gateway_macs,
        network_estimate=network_estimate,
        observed_at=_local_now(),
        wired=wired,
    )


def _active_wifi_bssid(run_command: Any = _run_command) -> str:
    """Return the active WiFi BSSID when local tools expose it."""

    nmcli = run_command(
        ("nmcli", "-t", "-f", "ACTIVE,BSSID", "dev", "wifi"),
        timeout_seconds=3.0,
    )
    if nmcli.returncode == 0:
        for line in nmcli.stdout.splitlines():
            fields = _split_nmcli_line(line)
            if len(fields) >= 2 and fields[0] == "yes" and fields[1]:
                return fields[1].strip().lower()
    iw = run_command(("iw", "dev"), timeout_seconds=3.0)
    if iw.returncode == 0:
        for interface in _wireless_interfaces_from_iw(iw.stdout):
            link = run_command(("iw", "dev", interface, "link"), timeout_seconds=3.0)
            if link.returncode != 0:
                continue
            match = re.search(r"Connected to ([0-9a-fA-F:]{17})", link.stdout)
            if match:
                return match.group(1).lower()
    return _wifi_bssid_from_netsh(run_command)


def _local_ipv4_subnets(run_command: Any = _run_command) -> tuple[str, ...]:
    """Return globally scoped local IPv4 networks visible to this session.

    Linux reads `ip addr`; Windows, where `ip` does not exist, falls back to
    the on-link routes of interfaces that carry a default route.
    """

    result = run_command(
        ("ip", "-o", "-4", "addr", "show", "scope", "global"),
        timeout_seconds=3.0,
    )
    if result.returncode != 0:
        return _windows_local_subnets(run_command)
    subnets: list[str] = []
    for line in result.stdout.splitlines():
        parts = line.split()
        if "inet" not in parts:
            continue
        try:
            cidr = parts[parts.index("inet") + 1]
            network = ipaddress.ip_interface(cidr).network
        except (ValueError, IndexError):
            continue
        label = str(network)
        if label not in subnets:
            subnets.append(label)
    return tuple(subnets)


def _default_gateway_routes(
    run_command: Any = _run_command,
) -> tuple[tuple[str, str], ...]:
    """Return default route `(interface, gateway-ip)` pairs.

    Linux names the interface (`eth0`); the Windows fallback identifies it
    by its IPv4 address, which `arp -N` accepts.
    """

    result = run_command(("ip", "route", "show", "default"), timeout_seconds=3.0)
    if result.returncode != 0:
        return _windows_default_routes(run_command)
    routes: list[tuple[str, str]] = []
    for line in result.stdout.splitlines():
        parts = line.split()
        if "dev" not in parts or "via" not in parts:
            continue
        try:
            interface = parts[parts.index("dev") + 1].strip()
            gateway_ip = parts[parts.index("via") + 1].strip()
        except IndexError:
            continue
        if interface and gateway_ip and (interface, gateway_ip) not in routes:
            routes.append((interface, gateway_ip))
    return tuple(routes)


def _default_gateway_interfaces(run_command: Any = _run_command) -> tuple[str, ...]:
    """Return interfaces that carry a default route."""

    interfaces: list[str] = []
    for interface, _gateway_ip in _default_gateway_routes(run_command):
        if interface not in interfaces:
            interfaces.append(interface)
    return tuple(interfaces)


def _normalized_mac(value: str) -> str:
    """Return a normalized MAC address found in `value`, if any.

    Accepts colon form (Linux) and dash form (Windows `arp`), and returns
    lower-case colon form so stored signals match across platforms.
    """

    match = re.search(
        r"\b[0-9a-fA-F]{2}([:-])[0-9a-fA-F]{2}(?:\1[0-9a-fA-F]{2}){4}\b", value
    )
    return match.group(0).replace("-", ":").lower() if match else ""


def _neighbor_mac_for_gateway(
    gateway_ip: str,
    interface: str,
    *,
    run_command: Any = _run_command,
) -> str:
    """Return the neighbor-table MAC for one default gateway."""

    specific = run_command(
        ("ip", "neigh", "show", gateway_ip, "dev", interface),
        timeout_seconds=3.0,
    )
    if specific.returncode == 0:
        for line in specific.stdout.splitlines():
            if line.split(maxsplit=1)[0:1] == [gateway_ip]:
                mac = _normalized_mac(line)
                if mac:
                    return mac
    neighbors = run_command(
        ("ip", "neigh", "show", "dev", interface),
        timeout_seconds=3.0,
    )
    if neighbors.returncode != 0:
        return _windows_gateway_mac(gateway_ip, interface, run_command)
    for line in neighbors.stdout.splitlines():
        parts = line.split()
        if parts and parts[0] == gateway_ip:
            return _normalized_mac(line)
    return ""


def _default_gateway_macs(run_command: Any = _run_command) -> tuple[str, ...]:
    """Return default-gateway router MAC addresses when the OS exposes them."""

    macs: list[str] = []
    for interface, gateway_ip in _default_gateway_routes(run_command):
        mac = _neighbor_mac_for_gateway(
            gateway_ip,
            interface,
            run_command=run_command,
        )
        if mac and mac != "00:00:00:00:00:00" and mac not in macs:
            macs.append(mac)
    return tuple(macs)


def _ipv4(value: str) -> ipaddress.IPv4Address | None:
    """Return `value` as an IPv4 address, or None when it is not one."""

    try:
        return ipaddress.IPv4Address(value)
    except ValueError:
        return None


def _parse_windows_default_routes(output: str) -> tuple[tuple[str, str], ...]:
    """Parse `route print -4` rows into `(interface-ip, gateway-ip)` pairs.

    Default-route rows read `0.0.0.0 0.0.0.0 <gateway> <interface> <metric>`.
    Matching addresses and column positions instead of translated headings
    keeps this working in every Windows display language. The interface is
    read from the right: some languages translate `On-link` into several
    words, which shifts every column counted from the left.
    """

    routes: list[tuple[str, str]] = []
    for line in output.splitlines():
        fields = line.split()
        if len(fields) < 5 or fields[0] != "0.0.0.0" or fields[1] != "0.0.0.0":
            continue
        gateway, interface = fields[2], fields[-2]
        if _ipv4(gateway) is None or _ipv4(interface) is None:
            continue
        if (interface, gateway) not in routes:
            routes.append((interface, gateway))
    return tuple(routes)


def _parse_windows_local_subnets(
    output: str, interface_ips: Sequence[str]
) -> tuple[str, ...]:
    """Parse on-link `route print -4` rows into subnets of the given interfaces.

    A row counts when its destination is the network of its interface
    address under its netmask. Host, default, loopback, link-local, and
    multicast routes are skipped.
    """

    wanted = set(interface_ips)
    subnets: list[str] = []
    for line in output.splitlines():
        fields = line.split()
        # The interface is the second-to-last column; see the note on
        # translated `On-link` text in `_parse_windows_default_routes`.
        if len(fields) < 5 or fields[-2] not in wanted:
            continue
        destination, netmask, interface = fields[0], fields[1], fields[-2]
        if _ipv4(destination) is None or _ipv4(netmask) is None:
            continue
        if netmask in {"0.0.0.0", "255.255.255.255"}:
            continue
        try:
            network = ipaddress.IPv4Network(f"{destination}/{netmask}")
            own_network = ipaddress.IPv4Interface(f"{interface}/{netmask}").network
        except ValueError:
            continue
        if network != own_network:
            continue
        if network.is_loopback or network.is_link_local or network.is_multicast:
            continue
        label = str(network)
        if label not in subnets:
            subnets.append(label)
    return tuple(subnets)


def _windows_route_table(run_command: Any) -> str:
    """Return `route print -4` output, or an empty string when unavailable."""

    result = run_command(("route", "print", "-4"), timeout_seconds=3.0)
    return result.stdout if result.returncode == 0 else ""


def _windows_default_routes(run_command: Any) -> tuple[tuple[str, str], ...]:
    """Return Windows default routes as `(interface-ip, gateway-ip)` pairs."""

    return _parse_windows_default_routes(_windows_route_table(run_command))


def _windows_local_subnets(run_command: Any) -> tuple[str, ...]:
    """Return subnets of Windows interfaces that carry a default route.

    Interfaces without a default route, such as VPN tunnels, look the same
    everywhere and would only blur location matching.
    """

    table = _windows_route_table(run_command)
    interfaces = [interface for interface, _ in _parse_windows_default_routes(table)]
    if not interfaces:
        return ()
    return _parse_windows_local_subnets(table, interfaces)


def _windows_gateway_mac(gateway_ip: str, interface_ip: str, run_command: Any) -> str:
    """Return a gateway's MAC address from the Windows ARP cache."""

    if _ipv4(gateway_ip) is None or _ipv4(interface_ip) is None:
        return ""
    result = run_command(
        ("arp", "-a", gateway_ip, "-N", interface_ip), timeout_seconds=3.0
    )
    if result.returncode != 0:
        return ""
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) >= 2 and fields[0] == gateway_ip:
            return _normalized_mac(fields[1])
    return ""


def _netsh_wlan_output(run_command: Any) -> str | None:
    """Return `netsh wlan show interfaces` output, or None when unavailable."""

    result = run_command(("netsh", "wlan", "show", "interfaces"), timeout_seconds=3.0)
    return result.stdout if result.returncode == 0 else None


def _parse_netsh_ssid(output: str) -> str:
    """Return the connected SSID from `netsh wlan show interfaces` output."""

    for line in output.splitlines():
        match = re.match(r"^\s*SSID\s*:\s*(.*?)\s*$", line)
        if match and match.group(1):
            return match.group(1)
    return "unknown"


def _parse_netsh_bssid(output: str) -> str:
    """Return the connected access point's BSSID from netsh output.

    Newer Windows 11 builds label it `AP BSSID`; older builds use `BSSID`.
    """

    for line in output.splitlines():
        if re.match(r"^\s*(?:AP\s+)?BSSID\s*:", line):
            mac = _normalized_mac(line)
            if mac:
                return mac
    return ""


def _active_wifi_from_netsh(run_command: Any) -> str:
    """Return the WiFi SSID through netsh, the Windows inspection tier."""

    output = _netsh_wlan_output(run_command)
    return "unknown" if output is None else _parse_netsh_ssid(output)


def _wifi_bssid_from_netsh(run_command: Any) -> str:
    """Return the WiFi BSSID through netsh, or an empty string."""

    output = _netsh_wlan_output(run_command)
    return "" if output is None else _parse_netsh_bssid(output)


def _wifi_reported_disconnected(run_command: Any) -> bool:
    """Return whether the WLAN service affirmatively reports no connection.

    netsh succeeding without an SSID means the WiFi adapters are
    disconnected, turned off, or absent. Output that mentions location
    permission means Windows withheld the details, which proves nothing.
    Where netsh does not exist, as on Linux, nothing is affirmed.
    """

    output = _netsh_wlan_output(run_command)
    if output is None:
        return False
    if "location" in output.casefold():
        return False
    return _parse_netsh_ssid(output) == "unknown"


def _location_network_fingerprint(
    observation: location_memory.LocationObservation,
    *,
    salt_path: Path = DEFAULT_LOCATION_MEMORY_SALT_PATH,
) -> str:
    """Return a stable local-only fingerprint for cache invalidation.

    The cache should distinguish common public SSIDs at different premises,
    but it must not write raw BSSIDs, router MACs, or SSIDs into additional
    files beyond the existing local-only observation and memory stores.
    """

    salt = location_memory.load_location_memory_salt(salt_path, create=False)
    if not salt:
        return ""
    raw_signals = [
        ("ssid", observation.wifi_ssid if observation.wifi_ssid != "unknown" else ""),
        ("bssid", observation.wifi_bssid),
    ]
    raw_signals.extend(("gateway", mac) for mac in observation.gateway_macs)
    raw_signals.extend(("subnet", subnet) for subnet in observation.local_subnets)
    hashed = [
        f"{kind}:{digest}"
        for kind, value in raw_signals
        if (digest := location_memory.hash_location_signal(value, salt))
    ]
    return "|".join(sorted(dict.fromkeys(hashed)))


def _status_from_location_memory_match(
    observation: location_memory.LocationObservation,
    match: location_memory.LocationMemoryMatch,
    now: datetime,
) -> OperatorLocationStatus:
    record = match.record
    display = record.get("display")
    if not isinstance(display, dict):
        display = {}
    label = str(record.get("label", "")).strip()
    coarse = str(display.get("coarse", "")).strip() or label or "known location"
    fine = str(display.get("fine", "")).strip()
    return OperatorLocationStatus(
        hostname=observation.hostname,
        username=observation.username,
        wifi_ssid=observation.wifi_ssid,
        location=coarse,
        updated_at=now,
        source=f"local memory: {', '.join(match.reasons) or 'manual'}",
        fine_location=fine,
        opsec_level=str(record.get("opsec_level", "")).strip(),
        network_observed_at=now,
        network_fingerprint=_location_network_fingerprint(observation),
        wired=observation.wired,
        network_label=str(record.get("lan_name", "")).strip(),
    )


def _network_observation_age_seconds(
    status: OperatorLocationStatus, now: datetime
) -> float | None:
    """Return the age of the last cheap local-network observation."""

    return _timestamp_age_seconds(status.network_observed_at or status.updated_at, now)


def _same_operator_network(
    status: OperatorLocationStatus,
    observation: location_memory.LocationObservation,
    *,
    salt_path: Path = DEFAULT_LOCATION_MEMORY_SALT_PATH,
) -> bool:
    """Return whether a cheap observation still matches cached network state."""

    observed_fingerprint = _location_network_fingerprint(
        observation,
        salt_path=salt_path,
    )
    if status.network_fingerprint and observed_fingerprint:
        return status.network_fingerprint == observed_fingerprint
    cached_ssid = status.wifi_ssid.strip().casefold()
    observed_ssid = observation.wifi_ssid.strip().casefold()
    if not cached_ssid or cached_ssid == "unknown":
        return observed_ssid in {"", "unknown"}
    return observed_ssid == cached_ssid


def _active_wifi_ssid(
    run_command: Any = _run_command, *, fallback_ssid: str = ""
) -> str:
    for inspector in (
        _active_wifi_from_nmcli_scan,
        _active_wifi_from_nmcli_connection,
        _active_wifi_from_wireless_tools,
        _active_wifi_from_netsh,
    ):
        ssid = inspector(run_command)
        if ssid != "unknown":
            return ssid
    if fallback_ssid and fallback_ssid != "unknown":
        return fallback_ssid
    return "unknown"


def _active_wifi_from_nmcli_scan(run_command: Any) -> str:
    nmcli = run_command(
        ("nmcli", "-t", "-f", "ACTIVE,SSID", "dev", "wifi"),
        timeout_seconds=3.0,
    )
    if nmcli.returncode == 0:
        for line in nmcli.stdout.splitlines():
            active, _, ssid = line.partition(":")
            if active == "yes" and ssid:
                return ssid.replace("\\:", ":").strip() or "unknown"
    return "unknown"


def _active_wifi_from_nmcli_connection(run_command: Any) -> str:
    status = run_command(
        ("nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "dev", "status"),
        timeout_seconds=3.0,
    )
    if status.returncode == 0:
        for line in status.stdout.splitlines():
            fields = _split_nmcli_line(line)
            if len(fields) < 4:
                continue
            _, connection_type, state, connection = fields[:4]
            if connection_type == "wifi" and state == "connected" and connection:
                if connection != "--":
                    return connection
    active = run_command(
        ("nmcli", "-t", "-f", "NAME,TYPE", "connection", "show", "--active"),
        timeout_seconds=3.0,
    )
    if active.returncode == 0:
        for line in active.stdout.splitlines():
            fields = _split_nmcli_line(line)
            if len(fields) >= 2 and fields[1] == "802-11-wireless" and fields[0]:
                return fields[0]
    return "unknown"


def _active_wifi_from_wireless_tools(run_command: Any) -> str:
    iwgetid = run_command(("iwgetid", "-r"), timeout_seconds=3.0)
    if iwgetid.returncode == 0 and iwgetid.stdout.strip():
        return iwgetid.stdout.strip()
    iw = run_command(("iw", "dev"), timeout_seconds=3.0)
    if iw.returncode == 0:
        for interface in _wireless_interfaces_from_iw(iw.stdout):
            link = run_command(("iw", "dev", interface, "link"), timeout_seconds=3.0)
            if link.returncode != 0:
                continue
            ssid = _ssid_from_iw_link(link.stdout)
            if ssid != "unknown":
                return ssid
    iwconfig = run_command(("iwconfig",), timeout_seconds=3.0)
    if iwconfig.returncode == 0:
        ssid = _ssid_from_iwconfig(iwconfig.stdout)
        if ssid != "unknown":
            return ssid
    return "unknown"


def _split_nmcli_line(line: str) -> list[str]:
    placeholder = "\0"
    return [
        field.replace(placeholder, ":").strip()
        for field in line.replace("\\:", placeholder).split(":")
    ]


def _wireless_interfaces_from_iw(output: str) -> tuple[str, ...]:
    interfaces: list[str] = []
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("Interface "):
            interface = stripped.removeprefix("Interface ").strip()
            if interface:
                interfaces.append(interface)
    return tuple(interfaces)


def _ssid_from_iw_link(output: str) -> str:
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("SSID:"):
            ssid = stripped.removeprefix("SSID:").strip()
            return ssid or "unknown"
    return "unknown"


def _ssid_from_iwconfig(output: str) -> str:
    match = re.search(r'ESSID:"([^"]+)"', output)
    if match and match.group(1):
        return match.group(1).strip() or "unknown"
    return "unknown"


def _geolocation_from_ip(
    timeout_seconds: float = 2.5,
    *,
    urlopen_func: Any = urlopen,
) -> NetworkLocationEstimate:
    providers = (
        ("https://ipapi.co/json/", _location_from_ipapi),
        ("https://ipinfo.io/json", _location_from_ipinfo),
    )
    for url, parser in providers:
        request = Request(url, headers={"User-Agent": "Ubersight"})
        try:
            with urlopen_func(request, timeout=timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception:
            continue
        estimate = parser(payload, source=url)
        if estimate.location:
            return estimate
    return NetworkLocationEstimate("unknown", "unavailable")


def _location_from_ipapi(payload: Any, *, source: str) -> NetworkLocationEstimate:
    if not isinstance(payload, dict):
        return NetworkLocationEstimate("", source)
    parts = [
        str(payload.get("city", "")).strip(),
        str(payload.get("region", "")).strip(),
        str(payload.get("country_name", "")).strip(),
    ]
    location = ", ".join(part for part in parts if part)
    latitude = _float_or_none(payload.get("latitude"))
    longitude = _float_or_none(payload.get("longitude"))
    if not location and latitude is not None and longitude is not None:
        location = f"{latitude}, {longitude}"
    uncertainty_meters = _network_uncertainty_from_payload(payload)
    return NetworkLocationEstimate(
        location, source, latitude, longitude, uncertainty_meters
    )


def _location_from_ipinfo(payload: Any, *, source: str) -> NetworkLocationEstimate:
    if not isinstance(payload, dict):
        return NetworkLocationEstimate("", source)
    parts = [
        str(payload.get("city", "")).strip(),
        str(payload.get("region", "")).strip(),
        str(payload.get("country", "")).strip(),
    ]
    location = ", ".join(part for part in parts if part)
    latitude = None
    longitude = None
    loc = str(payload.get("loc", "")).strip()
    if loc:
        lat_text, _, lon_text = loc.partition(",")
        latitude = _float_or_none(lat_text)
        longitude = _float_or_none(lon_text)
    if not location:
        location = loc
    uncertainty_meters = _network_uncertainty_from_payload(payload)
    return NetworkLocationEstimate(
        location, source, latitude, longitude, uncertainty_meters
    )


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _latlong_label(estimate: NetworkLocationEstimate) -> str:
    if estimate.latitude is None or estimate.longitude is None:
        return ""
    uncertainty = _format_uncertainty(estimate.uncertainty_meters)
    suffix = f" +/- {uncertainty}" if uncertainty else ""
    return f"{estimate.latitude:.5f}, {estimate.longitude:.5f}{suffix}"


def _network_uncertainty_from_payload(payload: dict[str, Any]) -> float | None:
    for key in ("accuracy", "accuracy_radius", "radius"):
        value = _float_or_none(payload.get(key))
        if value is not None and value > 0:
            return value
    return 25_000.0


def _format_uncertainty(meters: float | None) -> str:
    if meters is None or meters <= 0:
        return ""
    if meters >= 1000:
        return f"{meters / 1000:.0f} km"
    return f"{meters:.0f} m"


def _beacondb_location_from_observation(
    observation: location_memory.LocationObservation,
    *,
    timeout_seconds: float = 3.0,
    urlopen_func: Any = urlopen,
) -> NetworkLocationEstimate | None:
    """Return a BeaconDB WiFi-position estimate for an observed BSSID.

    BeaconDB uses a Mozilla Location Service compatible API. Querying it is
    appropriate only when external geolocation is enabled, and this helper
    deliberately sends the WiFi BSSID only. Gateway or Ethernet MAC addresses
    are useful local matchers, but they are not WiFi beacons.
    """

    bssid = _normalized_mac(observation.wifi_bssid)
    if not bssid:
        return None
    request_payload = {
        "wifiAccessPoints": [
            {
                "macAddress": bssid,
            }
        ]
    }
    request = Request(
        BEACONDB_GEOLOCATE_URL,
        data=json.dumps(request_payload).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "Ubersight local operator-status resolver",
        },
    )
    try:
        with urlopen_func(request, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    location = payload.get("location")
    if not isinstance(location, dict):
        return None
    latitude = _float_or_none(location.get("lat"))
    longitude = _float_or_none(location.get("lng"))
    if latitude is None or longitude is None:
        return None
    estimate = NetworkLocationEstimate(
        "",
        f"BeaconDB {BEACONDB_GEOLOCATE_URL}",
        latitude,
        longitude,
        _float_or_none(payload.get("accuracy")),
    )
    label = _latlong_label(estimate) or f"{latitude:.5f}, {longitude:.5f}"
    return replace(estimate, location=label)


def _venue_location_from_wifi(
    wifi_ssid: str,
    network_estimate: NetworkLocationEstimate,
    *,
    timeout_seconds: float = 3.0,
    urlopen_func: Any = urlopen,
) -> NetworkLocationEstimate | None:
    """Infer a named venue from WiFi SSID plus coarse network location."""

    if network_estimate.location in {"", "unknown"}:
        return None
    venue_name = _venue_query_from_wifi_ssid(wifi_ssid)
    if not venue_name:
        return None
    for query in _venue_search_queries(venue_name, network_estimate.location)[:1]:
        payload = _nominatim_search(
            query,
            network_estimate,
            timeout_seconds=timeout_seconds,
            urlopen_func=urlopen_func,
        )
        if not isinstance(payload, list):
            continue
        for candidate in payload:
            location = _venue_location_from_nominatim(candidate, venue_name)
            if location:
                return NetworkLocationEstimate(
                    location,
                    f"OpenStreetMap Nominatim via WiFi+{network_estimate.source}",
                    _float_or_none(candidate.get("lat")),
                    _float_or_none(candidate.get("lon")),
                    25.0,
                )
    overpass_estimate = _overpass_venue_location(
        venue_name,
        network_estimate,
        timeout_seconds=timeout_seconds,
        urlopen_func=urlopen_func,
    )
    if overpass_estimate is not None:
        return overpass_estimate
    return None


def _venue_search_queries(venue_name: str, coarse_location: str) -> tuple[str, ...]:
    """Return rate-limit-friendly Nominatim queries for a candidate venue."""

    venue_names = _expanded_venue_query_names(venue_name)
    queries = [
        query
        for name in venue_names
        for query in (f"{name} {coarse_location}", f"{name}, {coarse_location}")
    ]
    expanded = " ".join(_expand_small_digits(venue_name).split())
    if expanded.lower() != venue_name.lower():
        queries.append(f"{expanded} {coarse_location}")
    unique: list[str] = []
    for query in queries:
        if query not in unique:
            unique.append(query)
    return tuple(unique)


def _expanded_venue_query_names(venue_name: str) -> tuple[str, ...]:
    """Add conservative business-category hints implied by the SSID text."""

    lowered = venue_name.lower()
    names: list[str] = []
    if any(word in lowered for word in ("arcade", "game", "gamer", "player")):
        names.append(f"{venue_name} Video Game Bar")
    names.append(venue_name)
    return tuple(dict.fromkeys(names))


def _nominatim_search(
    query: str,
    network_estimate: NetworkLocationEstimate,
    *,
    timeout_seconds: float,
    urlopen_func: Any,
) -> Any:
    parameters = {
        "format": "jsonv2",
        "limit": "5",
        "addressdetails": "1",
        "namedetails": "1",
        "q": query,
    }
    if network_estimate.latitude is not None and network_estimate.longitude is not None:
        lat = network_estimate.latitude
        lon = network_estimate.longitude
        parameters["viewbox"] = f"{lon - 0.4},{lat + 0.4},{lon + 0.4},{lat - 0.4}"
    url = "https://nominatim.openstreetmap.org/search?" + urlencode(parameters)
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "Ubersight local operator-status resolver",
        },
    )
    try:
        with urlopen_func(request, timeout=timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception:
        return None


def _overpass_venue_location(
    venue_name: str,
    network_estimate: NetworkLocationEstimate,
    *,
    timeout_seconds: float,
    urlopen_func: Any,
) -> NetworkLocationEstimate | None:
    if network_estimate.latitude is None or network_estimate.longitude is None:
        return None
    regex = _overpass_name_regex(venue_name)
    if not regex:
        return None
    latitude = network_estimate.latitude
    longitude = network_estimate.longitude
    radius_meters = 25_000
    query = f"""
[out:json][timeout:{max(1, int(timeout_seconds))}];
(
  node(around:{radius_meters},{latitude:.6f},{longitude:.6f})["name"~"{regex}",i];
  way(around:{radius_meters},{latitude:.6f},{longitude:.6f})["name"~"{regex}",i];
  relation(around:{radius_meters},{latitude:.6f},{longitude:.6f})["name"~"{regex}",i];
);
out center tags 10;
"""
    request = Request(
        "https://overpass-api.de/api/interpreter",
        data=urlencode({"data": query}).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "Ubersight local operator-status resolver",
        },
    )
    try:
        with urlopen_func(request, timeout=timeout_seconds + 2.0) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return None
    elements = payload.get("elements") if isinstance(payload, dict) else None
    if not isinstance(elements, list):
        return None
    for element in elements:
        location = _venue_location_from_overpass(element, venue_name, network_estimate)
        if not location:
            continue
        lat, lon = _overpass_element_latlon(element)
        return NetworkLocationEstimate(
            location,
            f"OpenStreetMap Overpass via WiFi+{network_estimate.source}",
            lat,
            lon,
            25.0,
        )
    return None


def _overpass_name_regex(venue_name: str) -> str:
    tokens = re.findall(r"[A-Za-z0-9]+", venue_name)
    if not tokens:
        return ""
    parts = []
    digit_words = {
        "0": "zero",
        "1": "one",
        "2": "two",
        "3": "three",
        "4": "four",
        "5": "five",
        "6": "six",
        "7": "seven",
        "8": "eight",
        "9": "nine",
    }
    for token in tokens:
        lowered = token.lower()
        if lowered in digit_words:
            parts.append(f"({re.escape(token)}|{digit_words[lowered]})")
        else:
            parts.append(re.escape(token))
    return ".*".join(parts)


def _venue_location_from_overpass(
    element: Any,
    venue_name: str,
    network_estimate: NetworkLocationEstimate,
) -> str:
    if not isinstance(element, dict):
        return ""
    tags = element.get("tags")
    if not isinstance(tags, dict):
        return ""
    name = str(tags.get("name", "")).strip()
    if not _nominatim_candidate_matches(name, name, venue_name):
        return ""
    network_parts = [part.strip() for part in network_estimate.location.split(",")]
    city = str(tags.get("addr:city", "")).strip() or _list_item(network_parts, 0)
    region = str(tags.get("addr:state", "")).strip() or _list_item(network_parts, 1)
    country = str(tags.get("addr:country", "")).strip() or _list_item(network_parts, 2)
    return ", ".join(part for part in (name, city, region, country) if part)


def _overpass_element_latlon(
    element: dict[str, Any],
) -> tuple[float | None, float | None]:
    lat = _float_or_none(element.get("lat"))
    lon = _float_or_none(element.get("lon"))
    center = element.get("center")
    if (lat is None or lon is None) and isinstance(center, dict):
        lat = _float_or_none(center.get("lat"))
        lon = _float_or_none(center.get("lon"))
    return lat, lon


def _list_item(values: Sequence[str], index: int) -> str:
    if index >= len(values):
        return ""
    return values[index]


def _venue_query_from_wifi_ssid(wifi_ssid: str) -> str:
    """Convert a public SSID into a plausible venue-name query."""

    normalized = " ".join(wifi_ssid.replace("_", " ").replace("-", " ").split())
    if normalized.lower() in {"", "unknown"}:
        return ""
    ignored_tokens = {
        "2g",
        "2.4g",
        "5g",
        "5ghz",
        "free",
        "guest",
        "guests",
        "public",
        "wifi",
        "wi-fi",
        "wireless",
    }
    tokens = [
        token
        for token in normalized.split()
        if token.strip().lower() not in ignored_tokens
    ]
    candidate = " ".join(tokens).strip(" -_/")
    if len(candidate) < 3:
        return ""
    generic = {"airport", "eduroam", "hotel", "internet", "linksys", "xfinitywifi"}
    if candidate.lower() in generic:
        return ""
    return candidate


def _venue_location_from_nominatim(candidate: Any, venue_name: str) -> str:
    if not isinstance(candidate, dict):
        return ""
    display_name = str(candidate.get("display_name", "")).strip()
    name = _nominatim_name(candidate, display_name)
    if not _nominatim_candidate_matches(name, display_name, venue_name):
        return ""
    address = candidate.get("address")
    if not isinstance(address, dict):
        address = {}
    city = _first_mapping_value(
        address, ("city", "town", "village", "hamlet", "municipality", "county")
    )
    region = _first_mapping_value(address, ("state", "region", "province"))
    country = str(address.get("country", "")).strip()
    parts = [name, city, region, country]
    compact = ", ".join(part for part in parts if part)
    return compact or display_name


def _nominatim_name(candidate: dict[str, Any], display_name: str) -> str:
    namedetails = candidate.get("namedetails")
    if isinstance(namedetails, dict):
        name = str(namedetails.get("name", "")).strip()
        if name:
            return name
    name = str(candidate.get("name", "")).strip()
    if name:
        return name
    return display_name.split(",", 1)[0].strip()


def _nominatim_candidate_matches(name: str, display_name: str, venue_name: str) -> bool:
    haystack = _normalize_match_text(f"{name} {display_name}")
    needle = _normalize_match_text(venue_name)
    if needle and needle in haystack:
        return True
    return _normalize_match_text(_expand_small_digits(venue_name)) in haystack


def _normalize_match_text(value: str) -> str:
    return "".join(character.lower() for character in value if character.isalnum())


def _expand_small_digits(value: str) -> str:
    replacements = {
        "0": " zero ",
        "1": " one ",
        "2": " two ",
        "3": " three ",
        "4": " four ",
        "5": " five ",
        "6": " six ",
        "7": " seven ",
        "8": " eight ",
        "9": " nine ",
    }
    return "".join(replacements.get(character, character) for character in value)


def _first_mapping_value(mapping: dict[str, Any], keys: Sequence[str]) -> str:
    for key in keys:
        value = str(mapping.get(key, "")).strip()
        if value:
            return value
    return ""


def load_or_refresh_operator_location(
    cache_path: Path,
    *,
    now: datetime | None = None,
    max_age_seconds: int = 900,
    network_max_age_seconds: int = 30,
    run_command: Any = _run_command,
    geolocate: bool = True,
    urlopen_func: Any = urlopen,
    memory_path: Path = DEFAULT_LOCATION_MEMORY_PATH,
    network_profiles_path: Path = DEFAULT_NETWORK_PROFILES_PATH,
    observation_log_path: Path = DEFAULT_LOCATION_OBSERVATIONS_PATH,
) -> OperatorLocationStatus:
    """Return cached operator location while probing local network changes often."""

    now = now or _local_now()
    cached = _load_location_cache(cache_path)
    cached_fallback = cached or _load_location_cache(
        cache_path, accept_legacy_schema=True
    )
    if cached is not None:
        age = _timestamp_age_seconds(cached.updated_at, now)
        network_age = _network_observation_age_seconds(cached, now)
        if (
            age is not None
            and age <= max_age_seconds
            and not location_memory.location_inputs_newer_than(
                (memory_path, network_profiles_path),
                cached.updated_at,
            )
        ):
            if network_age is not None and network_age <= network_max_age_seconds:
                return cached
            network_observation = observe_operator_location(
                run_command=run_command,
                geolocate=False,
                urlopen_func=urlopen_func,
                fallback_ssid="",
            )
            # An unknown SSID usually means a failed WiFi probe, so the
            # cache wins; on a cable it is the normal state.
            if (
                network_observation.wifi_ssid == "unknown"
                and not network_observation.wired
            ):
                return cached
            if _same_operator_network(cached, network_observation):
                refreshed_network = replace(
                    cached,
                    hostname=network_observation.hostname,
                    username=network_observation.username,
                    wifi_ssid=network_observation.wifi_ssid,
                    network_observed_at=now,
                    network_fingerprint=_location_network_fingerprint(
                        network_observation
                    ),
                    wired=network_observation.wired,
                )
                _write_location_cache(cache_path, refreshed_network)
                return refreshed_network
    fallback_ssid = cached_fallback.wifi_ssid if cached_fallback is not None else ""
    observation = observe_operator_location(
        run_command=run_command,
        geolocate=False,
        urlopen_func=urlopen_func,
        fallback_ssid=fallback_ssid,
    )
    network_profile = location_memory.lookup_network_profile(
        observation,
        profile_path=network_profiles_path,
    )
    network_profile_hotspot = location_memory.network_profile_has_trait(
        network_profile,
        "hotspot",
    )
    wifi_hotspot = network_profile_hotspot or _network_is_mobile_hotspot(
        observation.wifi_ssid
    )
    memory_match = (
        None
        if wifi_hotspot
        else location_memory.lookup_location_memory(
            observation,
            memory_path=memory_path,
        )
    )
    if memory_match is not None and not memory_match.ambiguous:
        refreshed = _status_from_location_memory_match(observation, memory_match, now)
        _write_location_cache(cache_path, refreshed)
        location_memory.append_location_observation(observation_log_path, observation)
        return refreshed
    location, source, fine_location = ("unknown", "disabled", "")
    network_estimate = None
    if geolocate:
        if not wifi_hotspot:
            network_estimate = _beacondb_location_from_observation(
                observation,
                urlopen_func=urlopen_func,
            )
        if network_estimate is None:
            network_estimate = _geolocation_from_ip(urlopen_func=urlopen_func)
        observation = replace(observation, network_estimate=network_estimate)
        venue_estimate = (
            _venue_location_from_wifi(
                observation.wifi_ssid,
                network_estimate,
                urlopen_func=urlopen_func,
            )
            if network_estimate is not None and not wifi_hotspot
            else None
        )
        if network_estimate is not None:
            location = network_estimate.location
            source = network_estimate.source
            if wifi_hotspot:
                location = f"Mobile hotspot near {location or _latlong_label(network_estimate) or 'unknown'}"
                fine_location = _latlong_label(network_estimate)
                if network_profile_hotspot:
                    source = f"{source}; local network profile: hotspot"
                else:
                    source = f"{source}; mobile hotspot"
            elif venue_estimate is not None:
                fine_location = venue_estimate.location
                source = venue_estimate.source
            else:
                fine_location = _latlong_label(network_estimate)
        elif wifi_hotspot:
            location = "Mobile hotspot near unknown"
            source = (
                "local network profile: hotspot"
                if network_profile_hotspot
                else "mobile hotspot"
            )
    if memory_match is not None and memory_match.ambiguous:
        fine_location = "ambiguous local location memory match"
        source = source or "local memory ambiguous"
    refreshed = OperatorLocationStatus(
        hostname=observation.hostname,
        username=observation.username,
        wifi_ssid=observation.wifi_ssid,
        location=location,
        updated_at=now,
        source=source,
        fine_location=fine_location,
        opsec_level="public" if wifi_hotspot else "",
        network_observed_at=now,
        network_fingerprint=_location_network_fingerprint(observation),
        wired=observation.wired,
    )
    _write_location_cache(cache_path, refreshed)
    location_memory.append_location_observation(observation_log_path, observation)
    return refreshed


def _read_uptime(proc_root: Path) -> float | None:
    try:
        text = (proc_root / "uptime").read_text(encoding="utf-8")
        return float(text.split()[0])
    except (OSError, ValueError, IndexError):
        return None


def _clock_ticks_per_second() -> int:
    """Return the `/proc` clock-tick rate, defaulting to Linux's usual 100.

    Hosts without `os.sysconf`, such as Windows reading a test fixture tree,
    use the default rate.
    """

    sysconf = getattr(os, "sysconf", None)
    if sysconf is None:
        return 100
    return int(sysconf(os.sysconf_names["SC_CLK_TCK"]))


def _read_process(
    pid: int, proc_root: Path, uptime_seconds: float | None
) -> WorkerProcess | None:
    stat_path = proc_root / str(pid) / "stat"
    cmdline_path = proc_root / str(pid) / "cmdline"
    try:
        stat = stat_path.read_text(encoding="utf-8")
    except OSError:
        return None
    close_paren = stat.rfind(")")
    if close_paren < 0:
        return None
    comm_start = stat.find("(")
    comm = stat[comm_start + 1 : close_paren] if comm_start >= 0 else str(pid)
    rest = stat[close_paren + 2 :].split()
    if len(rest) < 2:
        return None
    try:
        ppid = int(rest[1])
    except ValueError:
        return None
    state = rest[0]
    elapsed_seconds = None
    if uptime_seconds is not None and len(rest) > 19:
        try:
            ticks_per_second = _clock_ticks_per_second()
            start_ticks = int(rest[19])
            elapsed_seconds = max(
                0.0, uptime_seconds - (start_ticks / ticks_per_second)
            )
        except (KeyError, OSError, ValueError):
            elapsed_seconds = None
    try:
        command_bytes = cmdline_path.read_bytes()
    except OSError:
        command = comm
    else:
        command = (
            command_bytes.replace(b"\x00", b" ")
            .decode("utf-8", errors="replace")
            .strip()
        )
        if not command:
            command = comm
    return WorkerProcess(
        pid=pid,
        ppid=ppid,
        state=state,
        command=command,
        elapsed_seconds=elapsed_seconds,
    )


def _collect_windows_process_tree(
    root_pid: int,
    *,
    snapshot: Any = None,
    probe_process: Any = None,
    now_filetime: int | None = None,
) -> tuple[WorkerProcess, ...]:
    """Return a Windows process subtree from a Toolhelp snapshot.

    Windows exposes no cheap run state comparable to Linux `/proc` states,
    so each row shows `-`; the command is the executable name.
    """

    take_snapshot = snapshot or windows_processes.snapshot_processes
    probe = probe_process or windows_processes.probe_process
    creation_times: dict[int, int | None] = {}

    def creation_time(pid: int) -> int | None:
        if pid not in creation_times:
            creation_times[pid] = probe(pid).creation_filetime
        return creation_times[pid]

    entries = windows_processes.build_process_tree(
        root_pid, take_snapshot(), creation_time
    )
    now_ticks = (
        windows_processes.current_filetime() if now_filetime is None else now_filetime
    )
    return tuple(
        WorkerProcess(
            pid=entry.pid,
            ppid=entry.ppid,
            state="-",
            command=entry.exe_name or str(entry.pid),
            elapsed_seconds=windows_processes.filetime_age_seconds(
                creation_time(entry.pid), now_ticks
            ),
        )
        for entry in entries
    )


def collect_process_tree(
    root_pid: int | None, proc_root: Path | None = None
) -> tuple[WorkerProcess, ...]:
    """Return a best-effort process tree rooted at ``root_pid``.

    An explicit ``proc_root`` always reads a `/proc`-style tree. Otherwise
    Windows uses native process snapshots and POSIX reads `/proc`.
    """

    if root_pid is None:
        return ()
    if proc_root is None:
        if _is_windows():
            return _collect_windows_process_tree(root_pid)
        proc_root = Path("/proc")
    uptime_seconds = _read_uptime(proc_root)
    processes: dict[int, WorkerProcess] = {}
    children_by_parent: dict[int, list[int]] = {}
    try:
        proc_entries = list(proc_root.iterdir())
    except OSError:
        return ()
    for entry in proc_entries:
        if not entry.name.isdecimal():
            continue
        process = _read_process(int(entry.name), proc_root, uptime_seconds)
        if process is None:
            continue
        processes[process.pid] = process
        children_by_parent.setdefault(process.ppid, []).append(process.pid)
    if root_pid not in processes:
        return ()
    ordered: list[WorkerProcess] = []
    stack = [root_pid]
    seen: set[int] = set()
    while stack:
        pid = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        process = processes.get(pid)
        if process is None:
            continue
        ordered.append(process)
        stack.extend(reversed(sorted(children_by_parent.get(pid, []))))
    return tuple(ordered)


def _ellipsize(text: str, width: int) -> str:
    clean = " ".join(str(text).split())
    if width <= 0:
        return ""
    if len(clean) <= width:
        return clean
    if width <= 3:
        return clean[:width]
    return clean[: width - 3] + "..."


def _clip_preserve_spaces(text: str, width: int) -> str:
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    if width <= 3:
        return text[:width]
    return text[: width - 3] + "..."


def _center_banner(text: str, width: int) -> str:
    clipped = _clip_preserve_spaces(text, width)
    return clipped.center(width)


def _format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "--:--"
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _format_updated_at(
    status: UbersightStatus, now: datetime, stale_seconds: int
) -> str:
    if status.updated_at is None:
        return "updated: unknown"
    age_seconds = max(
        0.0, (now - status.updated_at.astimezone(now.tzinfo)).total_seconds()
    )
    label = _format_duration(age_seconds)
    if age_seconds > stale_seconds:
        return f"STALE {label}"
    return f"updated {label} ago"


def _append_wrapped(lines: list[str], text: str, width: int, prefix: str = "") -> None:
    if not text:
        return
    wrap_width = max(8, width - len(prefix))
    for wrapped in textwrap.wrap(text, width=wrap_width) or [""]:
        lines.append(_ellipsize(f"{prefix}{wrapped}", width))


def _append_slice_row(
    lines: list[str],
    slice_status: SliceStatus,
    *,
    marker: str,
    width: int,
) -> None:
    """Append one compact slice row."""

    slice_name = f"Slice {slice_status.slice_id}" if slice_status.slice_id else "Slice"
    lines.append(
        _clip_preserve_spaces(f"{marker}{slice_name}: {slice_status.title}", width)
    )


def _append_phase_row(
    lines: list[str],
    phase_status: PhaseStatus,
    *,
    marker: str,
    width: int,
) -> None:
    """Append one compact phase-stack row."""

    phase_name = f"Phase {phase_status.phase_id}" if phase_status.phase_id else "Phase"
    lines.append(
        _clip_preserve_spaces(f"{marker}{phase_name}: {phase_status.title}", width)
    )


def _visible_phase_stack(status: UbersightStatus) -> tuple[PhaseStatus, ...]:
    """Return Ubersight's two-before, current, five-after phase window."""

    if not status.phases:
        return ()
    active_index = next(
        (
            index
            for index, phase_status in enumerate(status.phases)
            if phase_status.state == "active"
        ),
        None,
    )
    if active_index is None and status.phase_id:
        active_index = next(
            (
                index
                for index, phase_status in enumerate(status.phases)
                if phase_status.phase_id == status.phase_id
            ),
            None,
        )
    if active_index is None:
        return status.phases[:8]
    start = max(0, active_index - 2)
    end = min(len(status.phases), active_index + 6)
    return status.phases[start:end]


# Display state for the operator identity rows, which have no text prefix.
HOST_STATE = "host"


def _operator_identity_lines(
    status: OperatorLocationStatus, width: int
) -> tuple[str, str]:
    """Return the `user@host` row and the network row below it.

    The network row is `WIFI: <ssid>` or, on a cable, `Ethernet (<label>)`.
    """

    return (
        _ellipsize(f"{status.username}@{status.hostname}", width),
        _ellipsize(_network_display_segment(status), width),
    )


def _operator_footer_lines(
    status: OperatorLocationStatus,
    width: int,
    *,
    max_lines_per_status: int = 1,
) -> tuple[str, ...]:
    attribution = _operator_location_attribution_suffix(status)
    second = (
        status.location if status.fine_location else f"{status.location}{attribution}"
    )
    lines = _operator_identity_lines(status, width) + _wrapped_labeled_status_lines(
        "LOC", second, width, max_lines_per_status
    )
    if status.fine_location:
        lines += _wrapped_labeled_status_lines(
            "LOC",
            f"{_compact_fine_location(status.location, status.fine_location)}"
            f"{attribution}",
            width,
            1,
        )
    return lines


def _safe_wifi_display_label(status: OperatorLocationStatus) -> str:
    """Return the locally observed WiFi label for the operator's terminal."""

    if not status.wifi_ssid or status.wifi_ssid == "unknown":
        return "unknown"
    return status.wifi_ssid


def _network_display_segment(status: OperatorLocationStatus) -> str:
    """Return the footer's network segment: the WiFi name, or the wired LAN.

    On a cable the router stands in for an SSID, and the LAN is shown by
    the label the operator gave this place in the location editor.
    """

    if status.wired:
        return f"Ethernet ({status.network_label or 'unknown LAN'})"
    return f"WIFI: {_safe_wifi_display_label(status)}"


def _operator_location_attribution_suffix(status: OperatorLocationStatus) -> str:
    """Return compact source attribution for operator-visible location lines."""

    return " *(BeaconDB)" if "beacondb" in status.source.casefold() else ""


def _tailscale_footer_lines(
    status: TailscaleStatus,
    width: int,
    *,
    max_lines_per_status: int = 1,
) -> tuple[str, ...]:
    body = status.label.partition(":")[2].strip() or "ERROR"
    return _wrapped_labeled_status_lines(
        "Tailscale",
        body,
        width,
        max_lines_per_status,
    )


def _compact_fine_location(coarse_location: str, fine_location: str) -> str:
    """Drop repeated locality suffixes from premises-level location text."""

    fine_parts = [part.strip() for part in fine_location.split(",") if part.strip()]
    coarse_parts = [part.strip() for part in coarse_location.split(",") if part.strip()]
    while fine_parts and coarse_parts:
        if fine_parts[-1].casefold() != coarse_parts[-1].casefold():
            break
        fine_parts.pop()
        coarse_parts.pop()
    compact = ", ".join(fine_parts).strip()
    return compact or fine_location


def _wrapped_labeled_status_lines(
    label: str,
    body: str,
    width: int,
    max_lines: int,
    *,
    center_first: bool = False,
) -> tuple[str, ...]:
    max_lines = max(1, min(3, max_lines))
    prefix = f"{label}: "
    wrapped = textwrap.wrap(
        body,
        width=max(8, width - len(prefix)),
        break_long_words=False,
    )
    if not wrapped:
        wrapped = [""]
    visible = [_ellipsize(prefix + line, width) for line in wrapped[:max_lines]]
    if len(wrapped) > max_lines:
        visible[-1] = _ellipsize(visible[-1] + " ...", width)
    if center_first and visible:
        visible[0] = _center_banner(visible[0], width)
    return tuple(visible)


def _network_is_public(wifi_ssid: str) -> bool:
    """Return whether the current WiFi name suggests public/shared access."""

    normalized = " ".join(wifi_ssid.replace("_", " ").replace("-", " ").split()).lower()
    if not normalized or normalized == "unknown":
        return False
    public_tokens = {
        "free",
        "guest",
        "guests",
        "hotspot",
        "open",
        "public",
        "visitor",
        "visitors",
    }
    tokens = set(normalized.split())
    return bool(tokens & public_tokens) or normalized in {"attwifi", "xfinitywifi"}


def _network_is_mobile_hotspot(wifi_ssid: str) -> bool:
    """Return whether a WiFi name looks like transient mobile tethering."""

    normalized = " ".join(wifi_ssid.replace("_", " ").replace("-", " ").split()).lower()
    if not normalized or normalized == "unknown":
        return False
    tokens = set(normalized.split())
    mobile_tokens = {"android", "cell", "cellular", "hotspot", "iphone", "mobile"}
    return bool(tokens & mobile_tokens)


def _operator_location_is_public(status: OperatorLocationStatus) -> bool:
    """Return whether a location status should carry the public-network warning."""

    trusted_levels = {"private", "private-trusted", "trusted"}
    opsec_level = status.opsec_level.strip().casefold()
    if opsec_level in trusted_levels:
        return False
    if opsec_level == "public":
        return True
    return _network_is_public(status.wifi_ssid)


def _opsec_footer_lines(
    width: int,
    *,
    max_lines_per_status: int = 1,
    operator_location: OperatorLocationStatus | None = None,
) -> tuple[str, ...]:
    is_public = operator_location is not None and _operator_location_is_public(
        operator_location
    )
    # Centering only reads as a title inside the alarm's colored band; on
    # plain routine rows it just looks like stray indentation.
    return _wrapped_labeled_status_lines(
        "OPSEC!" if is_public else "OPSEC",
        OPSEC_PUBLIC_FOOTER_TEXT if is_public else OPSEC_FOOTER_TEXT,
        width,
        max_lines_per_status,
        center_first=is_public,
    )


def _footer_status_line_budget(height: int) -> int:
    """Return the per-status footer line budget for lower status blocks."""

    if height >= 28:
        return 3
    if height >= 20:
        return 2
    return 1


def _format_binary_amount(value: float | None) -> str:
    """Return a compact binary byte amount."""

    if value is None:
        return "--"
    if value >= 1024 * 1024 * 1024:
        return f"{value / (1024 * 1024 * 1024):.1f} GiB"
    if value >= 1024 * 1024:
        return f"{value / (1024 * 1024):.1f} MiB"
    return f"{value / 1024:.1f} KiB"


def _format_byte_rate(value: float | None) -> str:
    """Return a compact binary byte-rate value."""

    if value is None:
        return "-- KiB/s"
    if value >= 1024 * 1024:
        return f"{value / (1024 * 1024):.1f} MiB/s"
    return f"{value / 1024:.1f} KiB/s"


def _format_hhmmss(seconds: float | None) -> str:
    """Return a fixed-width ETA."""

    if seconds is None or seconds < 0:
        return "--:--:--"
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _format_background_job_count(value: float | None) -> str:
    """Return a compact item count without noisy decimals."""

    if value is None:
        return "--"
    if abs(value - round(value)) < 0.05:
        return str(int(round(value)))
    return f"{value:.1f}"


def _scroll_text(text: str, width: int, *, now: datetime) -> str:
    """Return one ticker window, advancing roughly one char every 250 ms."""

    if width <= 0:
        return ""
    cleaned = " ".join(text.split()) or "idle"
    tape = f"{cleaned}   "
    if len(tape) <= width:
        return _clip_preserve_spaces(tape, width).ljust(width)
    offset = int(now.timestamp() * 4) % len(tape)
    doubled = tape + tape
    return doubled[offset : offset + width]


def _background_job_display_text(status: BackgroundJobStatus) -> str:
    """Return sanitized ticker text for one background job."""

    if status.ticker:
        return status.ticker
    parts = [
        status.subject or status.source or status.app or status.job_id,
        status.verb,
        status.state,
        status.message,
    ]
    return " ".join(part for part in parts if part)


def _background_job_overall_state(jobs: Sequence[BackgroundJobStatus]) -> str:
    """Return a compact aggregate background-job state."""

    states = {job.state for job in jobs}
    if not states:
        return "IDLE"
    if "error" in states:
        return "ERROR"
    if "interrupted" in states:
        return "INT"
    if "paused" in states:
        return "PAUSE"
    if "running" in states:
        return "RUN"
    if "starting" in states:
        return "START"
    if states == {"complete"}:
        return "DONE"
    return "RUN"


def _aggregate_background_job_progress(
    jobs: Sequence[BackgroundJobStatus],
) -> tuple[float | None, float | None, str]:
    """Return aggregate completed, total, and unit for the gauge."""

    item_known = [
        job for job in jobs if job.work_completed is not None and job.work_total
    ]
    if item_known:
        unit = next(
            (job.work_unit for job in item_known if job.work_unit),
            "items",
        )
        return (
            sum(job.work_completed or 0.0 for job in item_known),
            sum(job.work_total or 0.0 for job in item_known),
            unit,
        )
    byte_known = [
        job for job in jobs if job.bytes_completed is not None and job.bytes_total
    ]
    if byte_known:
        return (
            sum(job.bytes_completed or 0.0 for job in byte_known),
            sum(job.bytes_total or 0.0 for job in byte_known),
            "bytes",
        )
    return None, None, ""


def _background_job_gauge(
    completed: float | None,
    total: float | None,
    *,
    width: int,
) -> tuple[str, str]:
    """Return an ASCII aggregate gauge and percentage label."""

    if completed is None or not total or total <= 0:
        bar_width = max(6, min(18, width - 18))
        return f"[{'?' * bar_width}]", "--%"
    ratio = min(1.0, max(0.0, completed / total))
    bar_width = max(6, min(18, width - 22))
    filled = int(round(ratio * bar_width))
    bar = "#" * filled + "." * (bar_width - filled)
    return f"[{bar}]", f"{ratio * 100:3.0f}%"


def _background_job_eta(
    jobs: Sequence[BackgroundJobStatus],
    completed: float | None,
    total: float | None,
    unit: str,
) -> float | None:
    """Return best aggregate ETA estimate."""

    if completed is not None and total is not None and total > completed:
        if unit == "bytes":
            rate = sum(job.bytes_per_second or 0.0 for job in jobs)
        else:
            rate = sum(job.work_items_per_second or 0.0 for job in jobs)
        if rate > 0:
            return (total - completed) / rate
    known_etas = [job.eta_seconds for job in jobs if job.eta_seconds is not None]
    return max(known_etas) if known_etas else None


def _background_job_lines(
    jobs: Sequence[BackgroundJobStatus],
    width: int,
    *,
    now: datetime,
) -> tuple[str, str, str]:
    """Render the three-row Ubersight background-job area."""

    if not jobs:
        return (
            _ellipsize("DATA: idle", width),
            _ellipsize("DATA [......] IDLE 0 jobs", width),
            _ellipsize("DATA no active jobs", width),
        )
    ticker = "; ".join(_background_job_display_text(job) for job in jobs)
    state = _background_job_overall_state(jobs)
    completed, total, unit = _aggregate_background_job_progress(jobs)
    gauge, percent = _background_job_gauge(completed, total, width=width)
    gauge_row = f"DATA {gauge} {percent} {state} {len(jobs)} jobs"
    bytes_per_second = sum(job.bytes_per_second or 0.0 for job in jobs)
    items_per_second = sum(job.work_items_per_second or 0.0 for job in jobs)
    eta = _background_job_eta(jobs, completed, total, unit)
    item_known = [
        job for job in jobs if job.work_completed is not None and job.work_total
    ]
    progress = ""
    if item_known:
        item_completed = sum(job.work_completed or 0.0 for job in item_known)
        item_total = sum(job.work_total or 0.0 for job in item_known)
        item_unit = next(
            (job.work_unit for job in item_known if job.work_unit),
            "items",
        )
        progress = (
            f"{_format_background_job_count(item_completed)}/"
            f"{_format_background_job_count(item_total)}"
        )
        if item_unit:
            progress += f" {item_unit}"
    elif completed is not None and total is not None:
        progress = (
            f"{_format_background_job_count(completed)}/"
            f"{_format_background_job_count(total)}"
        )
        if unit and unit != "bytes":
            progress += f" {unit}"
    bytes_done = sum(
        job.bytes_completed or 0.0 for job in jobs if job.bytes_completed is not None
    )
    fields = ["DATA", _format_byte_rate(bytes_per_second)]
    if progress:
        fields.append(progress)
    fields.append(f"ETA {_format_hhmmss(eta)}")
    fields.append(f"{len(jobs)} jobs")
    if items_per_second > 0:
        item_unit = unit if unit and unit != "bytes" else "items"
        fields.append(f"{items_per_second:.1f} {item_unit}/s")
    if bytes_done > 0:
        fields.append(_format_binary_amount(bytes_done))
    stats = fields[:]
    while len("  ".join(stats)) > width and len(stats) > 3:
        stats.pop()
    return (
        _scroll_text(f"DATA: {ticker}", width, now=now),
        _ellipsize(gauge_row, width),
        _ellipsize("  ".join(stats), width),
    )


def _hotkey_bar_line(width: int) -> str:
    """Return the interactive Ubersight hotkey row."""

    return _clip_preserve_spaces(HOTKEY_BAR_TEXT, width)


def render_status_screen(
    status: UbersightStatus,
    workers: Sequence[WorkerProcess] = (),
    *,
    origin_status: OriginStatus | None = None,
    local_status: LocalStatus | None = None,
    operator_location: OperatorLocationStatus | None = None,
    tailscale_status: TailscaleStatus | None = None,
    background_jobs: Sequence[BackgroundJobStatus] = (),
    project_name: str = "UBERSIGHT",
    width: int = 50,
    height: int = 20,
    now: datetime | None = None,
    stale_seconds: int = 900,
) -> list[str]:
    """Render the dashboard as clipped text lines for tests or ``--once``."""

    now = now or _local_now()
    usable_width = max(20, width)
    usable_height = max(8, height)
    lines: list[str] = []
    origin = origin_status or OriginStatus(
        kind="complicated",
        label=_origin_label("complicated"),
        updated_at=None,
        details="origin status has not been checked",
    )
    lines.append(
        _center_banner(
            _work_safety_label(origin, local_status, usable_width), usable_width
        )
    )
    lines.append(_ellipsize(f"PROJECT: {project_name}", usable_width))
    visible_phases = _visible_phase_stack(status)
    if visible_phases:
        for phase_status in visible_phases:
            marker = ">" if phase_status.state == "active" else " "
            _append_phase_row(lines, phase_status, marker=marker, width=usable_width)
    else:
        phase_text = "Phase"
        if status.phase_id:
            phase_text = f"Phase {status.phase_id}"
        if status.phase_title:
            phase_text += f": {status.phase_title}"
        if status.blocked:
            phase_text += " [BLOCKED]"
        elif status.complete:
            phase_text += " [COMPLETE]"
        lines.append(_ellipsize(phase_text, usable_width))
    lines.append("")

    slice_budget = max(
        1,
        min(len(status.slices), max(1, (usable_height - 8) // 3)),
    )
    active_index = next(
        (
            index
            for index, slice_status in enumerate(status.slices)
            if slice_status.state == "active"
        ),
        None,
    )
    start = 0
    if active_index is not None and len(status.slices) > slice_budget:
        start = max(
            0, min(active_index - slice_budget // 2, len(status.slices) - slice_budget)
        )
    visible_slices = status.slices[start : start + slice_budget]
    if start:
        lines.append(_ellipsize(f"... {start} earlier slices", usable_width))
    for slice_status in visible_slices:
        marker = ">" if slice_status.state == "active" else " "
        _append_slice_row(lines, slice_status, marker=marker, width=usable_width)
    remaining = len(status.slices) - start - len(visible_slices)
    if remaining > 0:
        lines.append(_ellipsize(f"... {remaining} later slices", usable_width))

    lines.append("")
    lines.append(
        _ellipsize(_format_updated_at(status, now, stale_seconds), usable_width)
    )
    lines.append("")
    lines.append("Notes")
    notes = status.notes or "No current notes."
    note_budget = max(2, usable_height - len(lines) - 5)
    note_lines: list[str] = []
    _append_wrapped(note_lines, notes, usable_width)
    lines.extend(note_lines[:note_budget])
    if len(note_lines) > note_budget:
        lines.append(_ellipsize("...", usable_width))

    lines.append("")
    lines.append("Workers")
    if workers:
        worker_budget = max(1, usable_height - len(lines))
        for worker in workers[:worker_budget]:
            command = (
                Path(worker.command.split()[0]).name
                if worker.command
                else str(worker.pid)
            )
            row = (
                f"{worker.pid:<7} {worker.state:<2} "
                f"{_format_duration(worker.elapsed_seconds):>7} {command}"
            )
            lines.append(_ellipsize(row, usable_width))
        if len(workers) > worker_budget:
            lines.append(
                _ellipsize(
                    f"... {len(workers) - worker_budget} more workers", usable_width
                )
            )
    else:
        lines.append("No process root configured.")

    footer_budget = _footer_status_line_budget(usable_height)
    footer_lines: tuple[str, ...] = _background_job_lines(
        background_jobs,
        usable_width,
        now=now,
    )
    footer_lines = (_hotkey_bar_line(usable_width),) + footer_lines
    if operator_location is not None or tailscale_status is not None:
        footer_lines += _opsec_footer_lines(
            usable_width,
            max_lines_per_status=footer_budget,
            operator_location=operator_location,
        )
    if tailscale_status is not None:
        footer_lines += _tailscale_footer_lines(
            tailscale_status,
            usable_width,
            max_lines_per_status=footer_budget,
        )
    if operator_location is not None:
        footer_lines += _operator_footer_lines(
            operator_location,
            usable_width,
            max_lines_per_status=footer_budget,
        )
    body_height = usable_height - len(footer_lines)
    body = [_clip_preserve_spaces(line, usable_width) for line in lines[:body_height]]
    while len(body) < body_height:
        body.append("")
    return body + list(footer_lines)


def _draw_once(args: argparse.Namespace) -> int:
    status = load_status(Path(args.status))
    workers = collect_process_tree(_process_root_from_args(args))
    background_jobs = load_background_jobs(Path(args.background_job_dir))
    origin_status = load_or_refresh_origin_status(
        Path(args.repo_root),
        Path(args.origin_cache),
        max_age_seconds=args.origin_refresh_seconds,
        fetch=not args.no_origin_fetch,
        fetch_timeout_seconds=args.origin_fetch_timeout_seconds,
    )
    local_status = load_local_status(Path(args.repo_root))
    operator_location = load_or_refresh_operator_location(
        Path(args.location_cache),
        max_age_seconds=args.location_refresh_seconds,
        network_max_age_seconds=args.network_refresh_seconds,
        geolocate=not args.no_geolocate,
        memory_path=Path(args.location_memory),
        network_profiles_path=Path(args.location_network_profiles),
        observation_log_path=Path(args.location_observations),
    )
    tailscale_status = None
    if not args.no_tailscale:
        tailscale_status = load_or_refresh_tailscale_status(
            Path(args.tailscale_cache),
            max_age_seconds=args.tailscale_refresh_seconds,
        )
    for line in render_status_screen(
        status,
        workers,
        origin_status=origin_status,
        local_status=local_status,
        operator_location=operator_location,
        tailscale_status=tailscale_status,
        background_jobs=background_jobs,
        project_name=args.project_name,
        width=args.width,
        height=args.height,
        stale_seconds=args.stale_seconds,
    ):
        print(line)
    return 0


def _replace_unencodable_output(stream: Any = None) -> None:
    """Replace characters that the snapshot output encoding cannot represent.

    Redirected Windows output defaults to a legacy code page, so a status
    note with characters outside it would otherwise crash `--once`.
    """

    target = sys.stdout if stream is None else stream
    reconfigure = getattr(target, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(errors="replace")


def _process_root_from_args(args: argparse.Namespace) -> int | None:
    if args.process_root is not None:
        return args.process_root
    return args.codex_pid


def _phase_slice_line_states(status: UbersightStatus, width: int) -> dict[str, str]:
    """Return compact phase/slice display rows mapped to their states."""

    usable_width = max(20, width)
    states: dict[str, str] = {}
    for phase_status in _visible_phase_stack(status):
        marker = ">" if phase_status.state == "active" else " "
        phase_name = (
            f"Phase {phase_status.phase_id}" if phase_status.phase_id else "Phase"
        )
        line = _clip_preserve_spaces(
            f"{marker}{phase_name}: {phase_status.title}", usable_width
        )
        states[line] = phase_status.state
    for slice_status in status.slices:
        marker = ">" if slice_status.state == "active" else " "
        slice_name = (
            f"Slice {slice_status.slice_id}" if slice_status.slice_id else "Slice"
        )
        line = _clip_preserve_spaces(
            f"{marker}{slice_name}: {slice_status.title}", usable_width
        )
        states[line] = slice_status.state
    return states


# Dashboard color pairs as (foreground, background); None is the pane's own
# background. Dark text on a colored band is never bold: PDCurses, and
# terminals that render bold as bright, turn bold black into low-contrast
# grey. Light text may be bold.
CURSES_COLOR_PAIRS: dict[int, tuple[str, str | None]] = {
    1: ("CYAN", None),  # phase rows
    2: ("YELLOW", None),  # active phase or slice
    3: ("RED", None),  # blocked or failed rows
    4: ("GREEN", None),  # done rows
    5: ("BLACK", "GREEN"),  # WORK: SAFE
    6: ("BLACK", "YELLOW"),  # ORIGIN: OUT-OF-DATE
    7: ("WHITE", "RED"),  # WORK: CONFLICT and the OPSEC! alarm
    8: ("BLACK", "CYAN"),  # WORK: CHECK GIT
    9: ("YELLOW", "BLACK"),  # WORK: PUSH
    10: ("BLACK", "WHITE"),  # HOST rows and idle data
    11: ("WHITE", "BLUE"),  # LOC rows
    12: ("MAGENTA", None),  # routine OPSEC notes
    13: ("BLACK", "YELLOW"),  # WORK: LOCAL and WORK: PULL
    14: ("BLACK", "GREEN"),  # Tailscale up and running data
    15: ("BLACK", "YELLOW"),  # Tailscale down, data errors, form messages
    16: ("WHITE", "RED"),  # Tailscale logged out
    17: ("BLACK", "CYAN"),  # Tailscale error
    18: ("BLACK", "CYAN"),  # hotkey bar and form title
}


def _init_color_pairs(curses_module: Any) -> None:
    """Register the dashboard color pairs with curses."""

    for pair, (foreground, background) in CURSES_COLOR_PAIRS.items():
        curses_module.init_pair(
            pair,
            getattr(curses_module, f"COLOR_{foreground}"),
            -1 if background is None else getattr(curses_module, f"COLOR_{background}"),
        )


def _pair_attr(curses_module: Any, pair: int) -> int:
    """Return a pair's attribute, bold unless its text is dark on a band."""

    foreground, _background = CURSES_COLOR_PAIRS[pair]
    attr = curses_module.color_pair(pair)
    if foreground == "BLACK":
        return attr
    return attr | curses_module.A_BOLD


def _curses_attr_for_line(
    curses_module: Any,
    line: str,
    enable_color: bool,
    display_state: str | None = None,
) -> int:
    if not enable_color:
        return curses_module.A_NORMAL
    if display_state in {"blocked", "failed"}:
        return _pair_attr(curses_module, 3)
    if display_state == "active":
        return _pair_attr(curses_module, 2)
    if display_state == "done":
        return curses_module.color_pair(4)
    if display_state == "skipped":
        return curses_module.color_pair(8)
    if display_state == HOST_STATE:
        return _pair_attr(curses_module, 10)
    if display_state == "pending":
        return curses_module.A_NORMAL
    stripped = line.strip()
    if stripped == _origin_label("synced"):
        return _pair_attr(curses_module, 5)
    if stripped == _origin_label("out_of_date"):
        return _pair_attr(curses_module, 6)
    if stripped == _origin_label("conflict"):
        return _pair_attr(curses_module, 7)
    if stripped == _origin_label("complicated"):
        return _pair_attr(curses_module, 8)
    if stripped == _origin_label("newer"):
        return _pair_attr(curses_module, 9)
    if stripped in {"WORK: SAFE, SYNCED", "WORK: SAFE"}:
        return _pair_attr(curses_module, 5)
    if stripped in {
        "WORK: UNCOMMITTED LOCAL",
        "WORK: LOCAL",
        "WORK: PULL FIRST",
        "WORK: PULL",
    }:
        return _pair_attr(curses_module, 13)
    if stripped in {"WORK: UNPUSHED COMMITS", "WORK: PUSH"}:
        return _pair_attr(curses_module, 9)
    if stripped in {"WORK: CONFLICT"}:
        return _pair_attr(curses_module, 7)
    if stripped in {"WORK: CHECK GIT", "WORK: CHECK"}:
        return _pair_attr(curses_module, 8)
    if stripped.startswith("OPSEC!:"):
        return _pair_attr(curses_module, 7)
    if stripped.startswith("OPSEC:"):
        return _pair_attr(curses_module, 12)
    if stripped.startswith("Tailscale:") and stripped.endswith(" UP"):
        return _pair_attr(curses_module, 14)
    if stripped.startswith("Tailscale:") and stripped.endswith(" DOWN"):
        return _pair_attr(curses_module, 15)
    if stripped == "Tailscale: LOGGED OUT":
        return _pair_attr(curses_module, 16)
    if stripped.startswith("Tailscale:"):
        return _pair_attr(curses_module, 17)
    if stripped.startswith("DATA") and ("ERROR" in stripped or "INT" in stripped):
        return _pair_attr(curses_module, 15)
    if stripped.startswith("DATA") and "IDLE" in stripped:
        return _pair_attr(curses_module, 10)
    if stripped.startswith("DATA"):
        return _pair_attr(curses_module, 14)
    if stripped.startswith("(R)efresh"):
        return _pair_attr(curses_module, 18)
    if line.startswith("LOC:"):
        return _pair_attr(curses_module, 11)
    if line.startswith("Phase") or line.startswith(" Phase"):
        return _pair_attr(curses_module, 1)
    if line.startswith(">"):
        return _pair_attr(curses_module, 2)
    if "BLOCKED" in line or "fail" in line:
        return _pair_attr(curses_module, 3)
    if "done" in line:
        return curses_module.color_pair(4)
    return curses_module.A_NORMAL


def _line_uses_full_width_background(line: str) -> bool:
    """Return whether a colored row should be painted across the pane."""

    stripped = line.strip()
    return (
        stripped in {_origin_label(kind) for kind in VALID_ORIGIN_KINDS}
        or stripped
        in {
            "WORK: SAFE, SYNCED",
            "WORK: SAFE",
            "WORK: UNCOMMITTED LOCAL",
            "WORK: LOCAL",
            "WORK: UNPUSHED COMMITS",
            "WORK: PUSH",
            "WORK: PULL FIRST",
            "WORK: PULL",
            "WORK: CONFLICT",
            "WORK: CHECK GIT",
            "WORK: CHECK",
        }
        or stripped.startswith("OPSEC!:")
        or stripped.startswith("Tailscale:")
        or stripped.startswith("DATA")
        or stripped.startswith("(R)efresh")
        or line.startswith("LOC:")
    )


def _curses_line_text(line: str, width: int, *, band: bool = False) -> str:
    """Prepare a rendered row, preserving colored-band background coverage.

    `band` marks rows whose band is known from data rather than from a text
    prefix, such as the operator identity rows.
    """

    text = _clip_preserve_spaces(line, width)
    if band or _line_uses_full_width_background(line):
        return text.ljust(width)
    return text


OPSEC_LEVELS = ("private-trusted", "private", "public", "unknown")
ENTRY_LAN_NAME = "LAN name"
ENTRY_PLACE = "Place"
ENTRY_DETAIL = "Detail"
ENTRY_OPSEC = "OPSEC"
ENTRY_THRESHOLD = "Threshold"
ENTRY_CANCEL = "cancel"
ENTRY_SAVE = "save"


def _location_entry_defaults(
    observation: location_memory.LocationObservation,
    *,
    memory_path: Path,
) -> LocationEntryDefaults:
    """Return edit-form defaults for the current network identity."""

    match = location_memory.lookup_location_memory(
        observation,
        memory_path=memory_path,
    )
    record = match.record if match is not None and not match.ambiguous else {}
    display = record.get("display")
    if not isinstance(display, dict):
        display = {}
    opsec_level = str(record.get("opsec_level", "")).strip()
    if opsec_level not in OPSEC_LEVELS:
        opsec_level = (
            "public" if _network_is_public(observation.wifi_ssid) else "unknown"
        )
    default_threshold = 100.0 if _network_is_public(observation.wifi_ssid) else 80.0
    threshold = _float_or_none(record.get("confidence_threshold")) or default_threshold
    return LocationEntryDefaults(
        label=str(record.get("label", "")).strip(),
        fine_location=str(display.get("fine", "")).strip(),
        opsec_level=opsec_level,
        confidence_threshold=threshold,
        matched=bool(record),
        stable_identity=bool(observation.wifi_bssid or observation.gateway_macs),
        lan_name=str(record.get("lan_name", "")).strip(),
        record_id=str(record.get("id", "")).strip(),
    )


def _location_entry_fields(
    defaults: LocationEntryDefaults, *, wired: bool
) -> list[tuple[str, str]]:
    """Return the editor's fields; a wired network also gets a LAN name."""

    fields = [(ENTRY_LAN_NAME, defaults.lan_name)] if wired else []
    return fields + [
        (ENTRY_PLACE, defaults.label),
        (ENTRY_DETAIL, defaults.fine_location),
        (ENTRY_OPSEC, defaults.opsec_level),
        (ENTRY_THRESHOLD, f"{defaults.confidence_threshold:g}"),
    ]


def _location_entry_header(
    observation: location_memory.LocationObservation,
    defaults: LocationEntryDefaults,
) -> tuple[str, list[str]]:
    """Return the editor title and the network, help, and status rows.

    Raw router and access-point addresses are shown here for the operator
    to recognize; location memory stores only their salted hashes.
    """

    routers = ", ".join(observation.gateway_macs)
    if observation.wired:
        title = "NAME THIS NETWORK"
        rows = [
            f"Wired LAN via router {routers}",
            f"Subnet: {', '.join(observation.local_subnets) or 'unknown'}",
        ]
    else:
        title = "NAME THIS LOCATION"
        if observation.wifi_ssid != "unknown":
            rows = [
                f"WiFi: {observation.wifi_ssid}",
                f"Access point: {observation.wifi_bssid or 'not captured'}",
            ]
        else:
            rows = ["WiFi name not available"]
        if routers:
            rows.append(f"Router: {routers}")
    rows += ["Up/Down: field  Left/Right: OPSEC", "Enter: save  Esc: cancel"]
    if not defaults.stable_identity:
        rows.append("No access point or router captured; cannot save.")
    elif defaults.matched:
        rows.append("Editing the saved record for this network.")
    return title, rows


def _cycle_opsec(value: str, step: int) -> str:
    """Return the OPSEC level `step` places from `value`, wrapping around."""

    index = OPSEC_LEVELS.index(value) if value in OPSEC_LEVELS else -1
    return OPSEC_LEVELS[(index + step) % len(OPSEC_LEVELS)]


def _apply_location_entry_key(
    fields: list[tuple[str, str]],
    active_index: int,
    key: Any,
    curses_module: Any,
) -> tuple[list[tuple[str, str]], int, str | None]:
    """Apply one key to the editor form.

    Returns the updated fields, the active field index, and an action:
    `save`, `cancel`, or None. Keys come from `get_wch` (strings for
    characters) or `getch` (integer codes). Only Esc cancels, so every
    printable character, including `q`, can be typed. The OPSEC field
    cycles through its fixed levels instead of accepting text.
    """

    def code(name: str) -> Any:
        return getattr(curses_module, name, None)

    if key in ("\x1b", 27):
        return fields, active_index, ENTRY_CANCEL
    if key in ("\n", "\r", 10, 13, code("KEY_ENTER"), code("KEY_F2")):
        return fields, active_index, ENTRY_SAVE
    if key in (code("KEY_UP"), code("KEY_BTAB")):
        return fields, (active_index - 1) % len(fields), None
    if key in (code("KEY_DOWN"), "\t", 9):
        return fields, (active_index + 1) % len(fields), None
    label, value = fields[active_index]
    updated = list(fields)
    if label == ENTRY_OPSEC:
        if key == code("KEY_LEFT"):
            updated[active_index] = (label, _cycle_opsec(value, -1))
        elif key in (code("KEY_RIGHT"), " "):
            updated[active_index] = (label, _cycle_opsec(value, 1))
        return updated, active_index, None
    if key in (code("KEY_BACKSPACE"), "\b", "\x7f", 8, 127):
        updated[active_index] = (label, value[:-1])
    elif isinstance(key, str) and len(key) == 1 and key.isprintable():
        updated[active_index] = (label, value + key)
    elif isinstance(key, int) and 32 <= key <= 126:
        updated[active_index] = (label, value + chr(key))
    return updated, active_index, None


def _draw_location_entry_form(
    stdscr: Any,
    curses_module: Any,
    *,
    title: str,
    rows: Sequence[str],
    fields: list[tuple[str, str]],
    active_index: int,
    message: str,
    enable_color: bool,
) -> None:
    """Draw the local-memory editor page."""

    height, width = stdscr.getmaxyx()
    stdscr.erase()
    attr_title = _pair_attr(curses_module, 18) if enable_color else curses_module.A_BOLD
    stdscr.addstr(0, 0, _curses_line_text(title, width - 1, band=True), attr_title)
    y = 2
    for row in rows:
        if y >= height - 1:
            break
        stdscr.addstr(y, 0, _ellipsize(row, width - 1))
        y += 1
    y += 1
    label_width = max(len(label) for label, _ in fields) + 1
    for offset, (label, value) in enumerate(fields):
        if y >= height - 1:
            break
        marker = ">" if offset == active_index else " "
        shown = f"< {value} >" if label == ENTRY_OPSEC else value
        row = f"{marker}{label + ':':<{label_width}} {shown}"
        attr = (
            curses_module.A_BOLD if offset == active_index else curses_module.A_NORMAL
        )
        stdscr.addstr(y, 0, _ellipsize(row, width - 1), attr)
        y += 1
    if message and height >= 2:
        stdscr.addstr(
            height - 1,
            0,
            _curses_line_text(message, width - 1, band=True),
            (_pair_attr(curses_module, 15) if enable_color else curses_module.A_BOLD),
        )
    stdscr.refresh()


def _save_location_entry(
    fields: Sequence[tuple[str, str]],
    observation: location_memory.LocationObservation,
    *,
    memory_path: Path,
    record_id: str = "",
) -> str:
    """Validate and save one editor form; return an error message or "".

    A wired form saves its LAN name too. When only the LAN is named, the
    place takes the LAN's name.
    """

    values = {label: value.strip() for label, value in fields}
    lan_name = values.get(ENTRY_LAN_NAME)
    place = values.get(ENTRY_PLACE, "") or (lan_name or "")
    opsec_level = values.get(ENTRY_OPSEC, "")
    threshold = _float_or_none(values.get(ENTRY_THRESHOLD))
    if not place:
        return "Name the place (or the LAN) before saving."
    if not observation.wifi_bssid and not observation.gateway_macs:
        return "No access point or router captured; cannot save."
    if opsec_level not in OPSEC_LEVELS:
        return "OPSEC must be private-trusted, private, public, or unknown."
    if threshold is None or threshold <= 0:
        return "Threshold must be a positive number."
    location_memory.learn_location_memory(
        place,
        fine_location=values.get(ENTRY_DETAIL, ""),
        opsec_level=opsec_level,
        confidence_threshold=threshold,
        observation=observation,
        memory_path=memory_path,
        salt_path=DEFAULT_LOCATION_MEMORY_SALT_PATH,
        include_geo=observation.network_estimate is not None,
        lan_name=lan_name,
        record_id=record_id or None,
    )
    return ""


def _read_entry_key(stdscr: Any, curses_module: Any) -> Any:
    """Read one key, preferring `get_wch` so non-ASCII names can be typed."""

    get_wch = getattr(stdscr, "get_wch", None)
    if get_wch is None:
        return stdscr.getch()
    try:
        return get_wch()
    except curses_module.error:
        return -1


def _run_location_entry_page(
    stdscr: Any,
    curses_module: Any,
    args: argparse.Namespace,
    *,
    enable_color: bool,
) -> bool:
    """Run the interactive location-memory editor; return True after a save."""

    observation = observe_operator_location(
        geolocate=not args.no_geolocate,
        fallback_ssid="",
    )
    memory_path = Path(args.location_memory)
    defaults = _location_entry_defaults(observation, memory_path=memory_path)
    title, rows = _location_entry_header(observation, defaults)
    fields = _location_entry_fields(defaults, wired=observation.wired)
    active_index = 0
    message = ""
    stdscr.nodelay(False)
    try:
        while True:
            _draw_location_entry_form(
                stdscr,
                curses_module,
                title=title,
                rows=rows,
                fields=fields,
                active_index=active_index,
                message=message,
                enable_color=enable_color,
            )
            key = _read_entry_key(stdscr, curses_module)
            if key == getattr(curses_module, "KEY_RESIZE", None):
                _adopt_terminal_resize(curses_module)
                continue
            fields, active_index, action = _apply_location_entry_key(
                fields, active_index, key, curses_module
            )
            if action == ENTRY_CANCEL:
                return False
            if action == ENTRY_SAVE:
                message = _save_location_entry(
                    fields,
                    observation,
                    memory_path=memory_path,
                    record_id=defaults.record_id,
                )
                if not message:
                    return True
    finally:
        stdscr.nodelay(True)


def _adopt_terminal_resize(curses_module: Any) -> None:
    """Make curses adopt a new terminal size after `KEY_RESIZE`.

    ncurses updates its screen size before delivering `KEY_RESIZE`. PDCurses,
    used by `windows-curses`, keeps the old size until `resize_term(0, 0)`
    asks it to read the console size again.
    """

    if _is_windows():
        resize_term = getattr(curses_module, "resize_term", None)
        if resize_term is not None:
            resize_term(0, 0)


class RestartSignal:
    """Restart requests delivered to a live dashboard by `ubersight-stop`.

    Windows uses a named event in the logon session. POSIX catches `SIGTERM`,
    so a request leaves curses through its normal cleanup instead of killing
    the process with the terminal still in curses mode.
    """

    def __init__(self) -> None:
        self._requested = False
        self._event: windows_processes.RestartEvent | None = None
        self._previous_handler: Any = None
        self._installed_handler = False

    def install(self) -> None:
        """Start accepting restart requests for this process."""

        if _is_windows():
            self._event = windows_processes.RestartEvent.create()
            return
        self._previous_handler = signal.signal(signal.SIGTERM, self._on_sigterm)
        self._installed_handler = True

    def _on_sigterm(self, signum: int, frame: Any) -> None:
        """Record a POSIX restart request without interrupting curses."""

        self._requested = True

    def requested(self) -> bool:
        """Return whether a restart has been requested."""

        if self._requested:
            return True
        if self._event is not None and self._event.is_set():
            self._requested = True
        return self._requested

    def close(self) -> None:
        """Stop accepting restart requests and restore the previous state."""

        if self._event is not None:
            self._event.close()
            self._event = None
        if self._installed_handler:
            signal.signal(signal.SIGTERM, self._previous_handler)
            self._installed_handler = False


def _supervisor_log(message: str) -> None:
    """Print one supervisor notice to standard error."""

    print(f"[ubersight-supervise] {message}", file=sys.stderr, flush=True)


def run_supervised(
    child_args: Sequence[str],
    *,
    run: Any = subprocess.run,
    sleep: Any = time.sleep,
    log: Any = _supervisor_log,
) -> int:
    """Keep the dashboard running in this terminal until the operator quits.

    The child shares the terminal. A restart request (status 75) restarts it
    at once; Q (status 0) and Ctrl+C end supervision; any other exit, such
    as a crash while code is being edited, restarts after a short delay.
    """

    command = [sys.executable, "-m", "ubersight", *child_args]
    interrupted = {KEYBOARD_INTERRUPT_EXIT_CODE, WINDOWS_CONTROL_C_EXIT}
    if hasattr(signal, "SIGINT"):
        interrupted.add(-signal.SIGINT)
    while True:
        try:
            completed = run(command, check=False)
        except KeyboardInterrupt:
            return KEYBOARD_INTERRUPT_EXIT_CODE
        status = completed.returncode
        if status == 0:
            return 0
        if status in interrupted:
            return KEYBOARD_INTERRUPT_EXIT_CODE
        if status == RESTART_EXIT_CODE:
            log("restart requested; restarting Ubersight.")
            continue
        log(
            f"Ubersight exited with status {status}; restarting in "
            f"{SUPERVISOR_CRASH_DELAY_SECONDS:g}s. Press Ctrl+C to stop."
        )
        try:
            sleep(SUPERVISOR_CRASH_DELAY_SECONDS)
        except KeyboardInterrupt:
            return KEYBOARD_INTERRUPT_EXIT_CODE


def _run_curses(args: argparse.Namespace) -> int:
    """Run the live dashboard and return its exit status.

    Returns 0 when the operator quits, `RESTART_EXIT_CODE` when
    `ubersight-stop` requests a restart, and `KEYBOARD_INTERRUPT_EXIT_CODE`
    after Ctrl+C.
    """

    try:
        import curses
    except ImportError as exc:
        raise SystemExit(f"curses is unavailable: {exc}") from exc

    restart = RestartSignal()
    outcome = {"status": 0}

    def loop(stdscr: Any) -> None:
        curses.curs_set(0)
        stdscr.nodelay(True)
        enable_color = not args.no_color and curses.has_colors()
        if enable_color:
            curses.start_color()
            curses.use_default_colors()
            _init_color_pairs(curses)
        refresh_seconds = max(0.1, args.refresh_ms / 1000.0)
        force_refresh = False
        while True:
            if restart.requested():
                outcome["status"] = RESTART_EXIT_CODE
                return
            height, width = stdscr.getmaxyx()
            status = load_status(Path(args.status))
            workers = collect_process_tree(_process_root_from_args(args))
            background_jobs = load_background_jobs(Path(args.background_job_dir))
            origin_status = load_or_refresh_origin_status(
                Path(args.repo_root),
                Path(args.origin_cache),
                max_age_seconds=0 if force_refresh else args.origin_refresh_seconds,
                fetch=not args.no_origin_fetch,
                fetch_timeout_seconds=args.origin_fetch_timeout_seconds,
            )
            local_status = load_local_status(Path(args.repo_root))
            operator_location = load_or_refresh_operator_location(
                Path(args.location_cache),
                max_age_seconds=0 if force_refresh else args.location_refresh_seconds,
                network_max_age_seconds=(
                    0 if force_refresh else args.network_refresh_seconds
                ),
                geolocate=not args.no_geolocate,
                memory_path=Path(args.location_memory),
                network_profiles_path=Path(args.location_network_profiles),
                observation_log_path=Path(args.location_observations),
            )
            tailscale_status = None
            if not args.no_tailscale:
                tailscale_status = load_or_refresh_tailscale_status(
                    Path(args.tailscale_cache),
                    max_age_seconds=(
                        0 if force_refresh else args.tailscale_refresh_seconds
                    ),
                )
            force_refresh = False
            lines = render_status_screen(
                status,
                workers,
                origin_status=origin_status,
                local_status=local_status,
                operator_location=operator_location,
                tailscale_status=tailscale_status,
                background_jobs=background_jobs,
                project_name=args.project_name,
                width=width,
                height=height,
                stale_seconds=args.stale_seconds,
            )
            line_states = _phase_slice_line_states(status, width)
            if operator_location is not None:
                for identity_line in _operator_identity_lines(
                    operator_location, max(20, width)
                ):
                    line_states[identity_line] = HOST_STATE
            stdscr.erase()
            for y, line in enumerate(lines[:height]):
                if width <= 1:
                    continue
                text = _curses_line_text(
                    line,
                    width - 1,
                    band=line_states.get(line) == HOST_STATE,
                )
                stdscr.addstr(
                    y,
                    0,
                    text,
                    _curses_attr_for_line(
                        curses,
                        line,
                        enable_color,
                        line_states.get(line),
                    ),
                )
            stdscr.refresh()
            deadline = time.monotonic() + refresh_seconds
            while time.monotonic() < deadline:
                if restart.requested():
                    outcome["status"] = RESTART_EXIT_CODE
                    return
                key = stdscr.getch()
                if key in (ord("q"), ord("Q"), 27):
                    return
                if key == curses.KEY_RESIZE:
                    _adopt_terminal_resize(curses)
                    break
                if key in (ord("r"), ord("R")):
                    force_refresh = True
                    break
                if key in (ord("e"), ord("E")):
                    if _run_location_entry_page(
                        stdscr,
                        curses,
                        args,
                        enable_color=enable_color,
                    ):
                        force_refresh = True
                    break
                time.sleep(0.05)

    restart.install()
    try:
        curses.wrapper(loop)
    except KeyboardInterrupt:
        # curses.wrapper has already restored the terminal; exit quietly.
        return KEYBOARD_INTERRUPT_EXIT_CODE
    finally:
        restart.close()
    return outcome["status"]


def _status_from_args(args: argparse.Namespace) -> UbersightStatus:
    return UbersightStatus(
        phase_id=args.phase_id.strip(),
        phase_title=args.phase_title.strip(),
        slices=tuple(args.slice or ()),
        notes=args.notes.strip(),
        phases=tuple(args.phase_row or ()),
        blocked=args.blocked,
        complete=args.complete,
    )


def _normalized_status_for_write(status: UbersightStatus) -> UbersightStatus:
    """Validate and normalize status writes to the standing Ubersight contract."""

    if not status.phases:
        raise ValueError(
            "--write-status requires at least one --phase-row; "
            "the legacy single-phase shorthand is display-only compatibility"
        )
    active_phases = [
        phase_status for phase_status in status.phases if phase_status.state == "active"
    ]
    if len(active_phases) != 1:
        raise ValueError("--write-status requires exactly one active --phase-row")
    active_phase = active_phases[0]
    phase_id = status.phase_id or active_phase.phase_id
    phase_title = status.phase_title or active_phase.title
    if phase_id != active_phase.phase_id:
        raise ValueError(
            "--phase-id must match the active --phase-row ID, or be omitted"
        )
    if phase_title != active_phase.title:
        raise ValueError(
            "--phase-title must match the active --phase-row title, or be omitted"
        )
    if not status.slices:
        raise ValueError("--write-status requires at least one --slice row")
    active_slices = [
        slice_status for slice_status in status.slices if slice_status.state == "active"
    ]
    if len(active_slices) > 1:
        raise ValueError("--write-status allows at most one active --slice row")
    if not status.complete and not active_slices:
        raise ValueError(
            "--write-status requires one active --slice row unless --complete is set"
        )
    return UbersightStatus(
        phase_id=phase_id,
        phase_title=phase_title,
        phases=status.phases,
        slices=status.slices,
        notes=status.notes,
        blocked=status.blocked,
        complete=status.complete,
    )


def build_parser() -> argparse.ArgumentParser:
    """Create the command-line parser for Ubersight and status-writer modes."""

    parser = argparse.ArgumentParser(
        description="Run Ubersight in a terminal side pane."
    )
    parser.add_argument(
        "--status", default=str(DEFAULT_STATUS_PATH), help="agent status JSON path"
    )
    parser.add_argument("--events", help="reserved JSON Lines event history path")
    parser.add_argument("--codex-pid", type=int, help="Codex process PID to monitor")
    parser.add_argument(
        "--process-root", type=int, help="process subtree root PID to monitor"
    )
    parser.add_argument(
        "--refresh-ms", type=int, default=1000, help="interactive refresh interval"
    )
    parser.add_argument(
        "--repo-root", default=".", help="repository root for origin checks"
    )
    parser.add_argument(
        "--project-name",
        default=default_project_name(),
        help=(
            "project banner and Ubersight runtime context; default is the "
            "terminal path component of the current working directory"
        ),
    )
    parser.add_argument(
        "--origin-cache",
        default=str(DEFAULT_ORIGIN_CACHE_PATH),
        help="local origin status cache path",
    )
    parser.add_argument(
        "--origin-refresh-seconds",
        type=int,
        default=3600,
        help="maximum age for origin status before refresh",
    )
    parser.add_argument(
        "--origin-fetch-timeout-seconds",
        type=float,
        default=DEFAULT_ORIGIN_FETCH_TIMEOUT_SECONDS,
        help="network timeout for origin fetch refreshes",
    )
    parser.add_argument(
        "--no-origin-fetch",
        action="store_true",
        help="compare local refs without fetching origin",
    )
    parser.add_argument(
        "--location-cache",
        default=str(DEFAULT_LOCATION_CACHE_PATH),
        help="local operator location cache path",
    )
    parser.add_argument(
        "--location-memory",
        default=str(DEFAULT_LOCATION_MEMORY_PATH),
        help="local known-location memory path",
    )
    parser.add_argument(
        "--location-network-profiles",
        default=str(DEFAULT_NETWORK_PROFILES_PATH),
        help="local network trait profile path",
    )
    parser.add_argument(
        "--location-observations",
        default=str(DEFAULT_LOCATION_OBSERVATIONS_PATH),
        help="local location-observation JSONL path",
    )
    parser.add_argument(
        "--location-refresh-seconds",
        type=int,
        default=900,
        help="maximum age for geolocation before refresh",
    )
    parser.add_argument(
        "--network-refresh-seconds",
        type=int,
        default=30,
        help="maximum age for cheap local network identity before refresh",
    )
    parser.add_argument(
        "--no-geolocate",
        action="store_true",
        help="show host, user, and WiFi without network geolocation",
    )
    parser.add_argument(
        "--tailscale-cache",
        default=str(DEFAULT_TAILSCALE_CACHE_PATH),
        help="local Tailscale status cache path",
    )
    parser.add_argument(
        "--tailscale-refresh-seconds",
        type=int,
        default=15,
        help="maximum age for local Tailscale status before refresh",
    )
    parser.add_argument(
        "--no-tailscale",
        action="store_true",
        help="hide Tailscale status and skip the local tailscale CLI probe",
    )
    parser.add_argument(
        "--background-job-dir",
        default=None,
        help=(
            "background-job status directory; default is derived from "
            "--project-name as XDG_RUNTIME_DIR/ubersight/<project>/"
            "background-jobs"
        ),
    )
    parser.add_argument(
        "--ascii",
        dest="ascii_only",
        action="store_true",
        help="reserve ASCII-only rendering",
    )
    parser.add_argument("--no-color", action="store_true", help="disable curses color")
    parser.add_argument(
        "--once", action="store_true", help="print one text snapshot and exit"
    )
    parser.add_argument(
        "--width",
        type=int,
        default=DEFAULT_SNAPSHOT_WIDTH,
        help="snapshot width used with --once",
    )
    parser.add_argument(
        "--height",
        type=int,
        default=DEFAULT_SNAPSHOT_HEIGHT,
        help="snapshot height used with --once",
    )
    parser.add_argument(
        "--stale-seconds", type=int, default=900, help="age threshold for stale status"
    )
    parser.add_argument(
        "--supervise",
        action="store_true",
        help=(
            "keep the dashboard running: restart it after ubersight-stop "
            "requests and crashes; Q or Ctrl+C ends supervision"
        ),
    )

    writer = parser.add_argument_group("status writer")
    writer.add_argument(
        "--write-status", action="store_true", help="write status JSON and exit"
    )
    writer.add_argument("--phase-id", default="", help="phase ID for --write-status")
    writer.add_argument(
        "--phase-title", default="", help="phase title for --write-status"
    )
    writer.add_argument(
        "--phase-row",
        action="append",
        type=parse_phase_option,
        default=[],
        metavar="ID:STATE:TITLE",
        help="phase row for --write-status; repeatable",
    )
    writer.add_argument(
        "--slice",
        action="append",
        type=parse_slice_option,
        default=[],
        metavar="ID:STATE:TITLE",
        help="slice row for --write-status; repeatable",
    )
    writer.add_argument(
        "--notes", default="", help="high-level notes for --write-status"
    )
    writer.add_argument(
        "--blocked", action="store_true", help="mark written status blocked"
    )
    writer.add_argument(
        "--complete", action="store_true", help="mark written status complete"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run Ubersight or write one status snapshot."""

    parser = build_parser()
    raw_args = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(raw_args)
    if args.codex_pid is not None and args.process_root is not None:
        parser.error("--codex-pid and --process-root are aliases; provide only one")
    if args.supervise:
        if args.once or args.write_status:
            parser.error("--supervise cannot be combined with --once or --write-status")
        return run_supervised([arg for arg in raw_args if arg != "--supervise"])
    if args.background_job_dir is None:
        args.background_job_dir = str(background_job_dir(args.project_name))
    if args.write_status:
        try:
            status = _normalized_status_for_write(_status_from_args(args))
        except ValueError as exc:
            parser.error(str(exc))
        write_status_file(status, Path(args.status))
        print(f"[ubersight] wrote {args.status}")
        return 0
    if args.once:
        _replace_unencodable_output()
        return _draw_once(args)
    return _run_curses(args)


if __name__ == "__main__":
    raise SystemExit(main())
