"""Tests for native Windows support and its cross-platform seams.

Most tests simulate the other platform with fakes, so they run on both Linux
and Windows. Tests marked ``windows_only`` exercise real Win32 behavior such
as process probes and sharing violations, and are skipped elsewhere.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from ubersight import atomic_files
from ubersight import dashboard as ubersight
from ubersight import windows_processes

windows_only = pytest.mark.skipif(os.name != "nt", reason="requires native Windows")


def spawn_sleeper(seconds: float = 30.0) -> subprocess.Popen[bytes]:
    """Start a short-lived child Python process that just sleeps."""

    return subprocess.Popen(
        [sys.executable, "-c", f"import time; time.sleep({seconds})"],
        stdin=subprocess.DEVNULL,
    )


# Runtime directories -------------------------------------------------------


def test_windows_background_job_dir_is_project_local(monkeypatch) -> None:
    """Windows keeps project-scoped runtime state under `.local/`."""

    monkeypatch.setattr(ubersight, "_is_windows", lambda: True)

    assert ubersight.ubersight_runtime_root() == Path(".local/ubersight")
    assert ubersight.ubersight_context_root("Demo Context") == Path(".local/ubersight")
    assert ubersight.background_job_dir("Demo Context") == Path(
        ".local/ubersight/background-jobs"
    )


def test_windows_runtime_root_never_calls_getuid(monkeypatch) -> None:
    """Windows path selection must not touch POSIX-only APIs."""

    def forbidden_getuid() -> int:
        raise AssertionError("os.getuid must not be called on Windows")

    monkeypatch.setattr(ubersight, "_is_windows", lambda: True)
    monkeypatch.setattr(os, "getuid", forbidden_getuid, raising=False)
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/should/not/be/used")

    assert ubersight.background_job_dir() == Path(".local/ubersight/background-jobs")


def test_posix_background_job_dir_keeps_xdg_runtime_context(
    monkeypatch, tmp_path: Path
) -> None:
    """POSIX keeps the per-user, per-context runtime directory."""

    monkeypatch.setattr(ubersight, "_is_windows", lambda: False)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))

    assert ubersight.background_job_dir("Demo Context") == (
        tmp_path / "ubersight" / "Demo-Context" / "background-jobs"
    )


def test_posix_background_job_dir_falls_back_to_tmp_by_uid(monkeypatch) -> None:
    """Without a runtime directory, POSIX falls back to a per-UID `/tmp` root."""

    monkeypatch.setattr(ubersight, "_is_windows", lambda: False)
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.setattr(os, "getuid", lambda: 987654321, raising=False)

    assert ubersight.background_job_dir("demo") == (
        Path("/tmp") / "ubersight-987654321" / "demo" / "background-jobs"
    )


def test_write_status_on_windows_writes_lf_json_without_getuid(
    monkeypatch, tmp_path: Path
) -> None:
    """Agents must be able to publish status on Windows."""

    def forbidden_getuid() -> int:
        raise AssertionError("os.getuid must not be called on Windows")

    monkeypatch.setattr(ubersight, "_is_windows", lambda: True)
    monkeypatch.setattr(os, "getuid", forbidden_getuid, raising=False)
    status_path = tmp_path / "nested" / "status.json"

    result = ubersight.main(
        [
            "--write-status",
            "--status",
            str(status_path),
            "--phase-row",
            "11:active:Native Windows support",
            "--slice",
            "11:active:Fix Windows runtime blockers",
            "--notes",
            "Notes with a non-ASCII arrow \u2192 survive.",
        ]
    )

    assert result == 0
    raw = status_path.read_bytes()
    assert b"\r\n" not in raw
    payload = json.loads(raw.decode("utf-8"))
    assert payload["phase"] == {"id": "11", "title": "Native Windows support"}
    assert payload["notes"].endswith("\u2192 survive.")
    assert list(status_path.parent.glob("*.tmp")) == []


# Atomic files --------------------------------------------------------------


def test_replace_with_retry_recovers_from_transient_sharing_violation(
    tmp_path: Path,
) -> None:
    calls: list[tuple[Path, Path]] = []
    sleeps: list[float] = []

    def flaky_replace(source: Path, target: Path) -> None:
        calls.append((source, target))
        if len(calls) < 3:
            raise PermissionError("sharing violation")

    atomic_files.replace_with_retry(
        tmp_path / "a",
        tmp_path / "b",
        retry_permission_errors=True,
        delays=(0.01, 0.02, 0.03),
        replace=flaky_replace,
        sleep=sleeps.append,
    )

    assert len(calls) == 3
    assert sleeps == [0.01, 0.02]


def test_replace_with_retry_gives_up_after_bounded_delays(tmp_path: Path) -> None:
    sleeps: list[float] = []

    def always_denied(source: Path, target: Path) -> None:
        raise PermissionError("still open")

    with pytest.raises(PermissionError):
        atomic_files.replace_with_retry(
            tmp_path / "a",
            tmp_path / "b",
            retry_permission_errors=True,
            delays=(0.01, 0.02),
            replace=always_denied,
            sleep=sleeps.append,
        )

    assert sleeps == [0.01, 0.02]


def test_replace_with_retry_does_not_retry_posix_permission_errors(
    tmp_path: Path,
) -> None:
    sleeps: list[float] = []

    def denied(source: Path, target: Path) -> None:
        raise PermissionError("real permission problem")

    with pytest.raises(PermissionError):
        atomic_files.replace_with_retry(
            tmp_path / "a",
            tmp_path / "b",
            retry_permission_errors=False,
            replace=denied,
            sleep=sleeps.append,
        )

    assert sleeps == []


def test_replace_retry_budget_stays_near_one_second() -> None:
    assert 0.5 <= sum(atomic_files.REPLACE_RETRY_DELAYS_SECONDS) <= 1.5


def test_atomic_write_text_removes_temporary_file_on_failure(
    monkeypatch, tmp_path: Path
) -> None:
    target = tmp_path / "status.json"
    target.write_text("original\n", encoding="utf-8")

    def failing_replace(*args: object, **kwargs: object) -> None:
        raise PermissionError("target held open")

    monkeypatch.setattr(atomic_files, "replace_with_retry", failing_replace)

    with pytest.raises(PermissionError):
        atomic_files.atomic_write_text(target, "replacement\n")

    assert target.read_text(encoding="utf-8") == "original\n"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["status.json"]


def test_read_text_with_retry_recovers_from_transient_denial(
    monkeypatch, tmp_path: Path
) -> None:
    target = tmp_path / "status.json"
    target.write_text("{}", encoding="utf-8")
    original_read_text = Path.read_text
    attempts: list[int] = []

    def flaky_read_text(self: Path, *args: object, **kwargs: object) -> str:
        attempts.append(1)
        if len(attempts) == 1:
            raise PermissionError("replacement in progress")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", flaky_read_text)

    assert (
        atomic_files.read_text_with_retry(
            target, retry_permission_errors=True, sleep=lambda _: None
        )
        == "{}"
    )
    assert len(attempts) == 2


@windows_only
def test_atomic_write_survives_reader_holding_target_open(tmp_path: Path) -> None:
    """A reader briefly holding the status file must not fail the writer."""

    target = tmp_path / "status.json"
    target.write_text('{"old": true}\n', encoding="utf-8")
    reader = target.open("r", encoding="utf-8")
    closer = threading.Timer(0.15, reader.close)
    closer.start()
    try:
        atomic_files.atomic_write_text(target, '{"new": true}\n')
    finally:
        closer.join()
        reader.close()

    assert json.loads(target.read_text(encoding="utf-8")) == {"new": True}


# Subprocess probes ---------------------------------------------------------


def test_run_command_replaces_undecodable_output() -> None:
    result = ubersight._run_command(
        (sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'\\xff ok')"),
        timeout_seconds=30.0,
    )

    assert result.returncode == 0
    assert result.stdout.endswith(" ok")
    assert "\ufffd" in result.stdout


def test_run_command_never_shares_terminal_stdin() -> None:
    result = ubersight._run_command(
        (sys.executable, "-c", "import sys; print(repr(sys.stdin.read()))"),
        timeout_seconds=30.0,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "''"


def test_git_ssh_command_respects_explicit_environment() -> None:
    def unexpected(cwd: Path | None) -> str:
        raise AssertionError("core.sshCommand lookup is unnecessary")

    assert (
        ubersight._git_ssh_command(
            {"GIT_SSH_COMMAND": "custom-ssh"}, None, configured_ssh_command=unexpected
        )
        is None
    )
    assert (
        ubersight._git_ssh_command(
            {"GIT_SSH": "plink"}, None, configured_ssh_command=unexpected
        )
        is None
    )


def test_git_ssh_command_extends_configured_core_ssh_command() -> None:
    command = ubersight._git_ssh_command(
        {},
        Path("."),
        configured_ssh_command=lambda cwd: "C:/Windows/System32/OpenSSH/ssh.exe",
    )

    assert command == (
        "C:/Windows/System32/OpenSSH/ssh.exe -o BatchMode=yes -o ConnectTimeout=5"
    )


def test_git_ssh_command_defaults_to_batch_mode_ssh() -> None:
    command = ubersight._git_ssh_command(
        {}, Path("."), configured_ssh_command=lambda cwd: ""
    )

    assert command == "ssh -o BatchMode=yes -o ConnectTimeout=5"


def test_run_command_sets_ssh_command_only_for_fetch(monkeypatch) -> None:
    captured: list[tuple[list[str], dict[str, str]]] = []

    def fake_run(args, **kwargs):
        captured.append((args, kwargs["env"]))
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.delenv("GIT_SSH_COMMAND", raising=False)
    monkeypatch.delenv("GIT_SSH", raising=False)
    monkeypatch.setattr(ubersight.subprocess, "run", fake_run)
    monkeypatch.setattr(
        ubersight, "_git_ssh_command", lambda env, cwd: "configured -o BatchMode=yes"
    )

    ubersight._run_command(("git", "fetch", "--quiet", "origin"))
    ubersight._run_command(("git", "status", "--porcelain=v2", "--branch"))

    assert captured[0][1]["GIT_SSH_COMMAND"] == "configured -o BatchMode=yes"
    assert "GIT_SSH_COMMAND" not in captured[1][1]
    assert captured[1][1]["GIT_TERMINAL_PROMPT"] == "0"


def test_run_command_never_takes_optional_git_locks(monkeypatch) -> None:
    """Polling `git status` must not hold `.git/index.lock` against the operator."""

    captured: list[dict[str, str]] = []

    def fake_run(args, **kwargs):
        captured.append(kwargs["env"])
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(ubersight.subprocess, "run", fake_run)
    monkeypatch.delenv("GIT_OPTIONAL_LOCKS", raising=False)
    ubersight._run_command(("git", "status", "--porcelain=v1"))
    monkeypatch.setenv("GIT_OPTIONAL_LOCKS", "1")
    ubersight._run_command(("git", "status", "--porcelain=v1"))

    assert captured[0]["GIT_OPTIONAL_LOCKS"] == "0"
    assert captured[1]["GIT_OPTIONAL_LOCKS"] == "1"


def test_git_status_probe_leaves_no_index_lock(tmp_path: Path) -> None:
    """A real status probe succeeds and leaves another command's lock alone."""

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "tracked.txt").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
    lock = tmp_path / ".git" / "index.lock"
    lock.write_text("held by another git command\n", encoding="utf-8")

    result = ubersight._run_command(
        ("git", "status", "--porcelain=v2", "--branch"), cwd=tmp_path
    )

    assert result.returncode == 0
    assert lock.read_text(encoding="utf-8") == "held by another git command\n"


# Process identity ----------------------------------------------------------


@pytest.mark.parametrize(
    ("probe", "expected_ticks", "expected"),
    [
        (windows_processes.ProcessProbe("missing"), "", False),
        (windows_processes.ProcessProbe("exited", 10), "10", False),
        (windows_processes.ProcessProbe("inaccessible"), "10", True),
        (windows_processes.ProcessProbe("unsupported"), "10", True),
        (windows_processes.ProcessProbe("running", 10), "10", True),
        (windows_processes.ProcessProbe("running", 11), "10", False),
        (windows_processes.ProcessProbe("running", 11), "", True),
        (windows_processes.ProcessProbe("running", None), "10", True),
    ],
)
def test_windows_identity_only_rejects_proven_stale_processes(
    probe: windows_processes.ProcessProbe, expected_ticks: str, expected: bool
) -> None:
    assert (
        ubersight._windows_process_identity_is_live(
            1234,
            expected_start_ticks=expected_ticks,
            probe_process=lambda pid: probe,
        )
        is expected
    )


def test_process_identity_dispatches_to_windows_probe(monkeypatch) -> None:
    probed: list[int] = []

    def fake_probe(pid: int) -> windows_processes.ProcessProbe:
        probed.append(pid)
        return windows_processes.ProcessProbe("running", 42)

    monkeypatch.setattr(ubersight, "_is_windows", lambda: True)
    monkeypatch.setattr(windows_processes, "probe_process", fake_probe)

    assert ubersight._process_identity_is_live(4321, expected_start_ticks="42")
    assert not ubersight._process_identity_is_live(4321, expected_start_ticks="43")
    assert ubersight._boot_id() == ""
    assert probed == [4321, 4321]


def test_windows_background_job_loader_keeps_inaccessible_job_files(
    monkeypatch, tmp_path: Path
) -> None:
    """A job whose process cannot be inspected must not be deleted."""

    monkeypatch.setattr(ubersight, "_is_windows", lambda: True)
    monkeypatch.setattr(
        windows_processes,
        "probe_process",
        lambda pid: windows_processes.ProcessProbe("inaccessible"),
    )
    path = tmp_path / "job.json"
    path.write_text(
        json.dumps(
            {
                "schema": ubersight.BACKGROUND_JOB_SCHEMA,
                "pid": 4321,
                "process_start_ticks": "42",
                "job_id": "job",
                "state": "running",
            }
        ),
        encoding="utf-8",
    )

    jobs = ubersight.load_background_jobs(tmp_path)

    assert [job.job_id for job in jobs] == ["job"]
    assert path.exists()


def test_filetime_conversions() -> None:
    assert windows_processes.current_filetime(0.0) == 116_444_736_000_000_000
    assert windows_processes.filetime_age_seconds(None, 10) is None
    assert windows_processes.filetime_age_seconds(0, 25_000_000) == 2.5
    assert windows_processes.filetime_age_seconds(30, 10) == 0.0


def test_probe_rejects_nonpositive_pids() -> None:
    assert windows_processes.probe_process(0).state == "missing"
    assert windows_processes.probe_process(-5).state == "missing"


@windows_only
def test_probe_reports_current_process_running_with_stable_creation() -> None:
    first = windows_processes.probe_process(os.getpid())
    second = windows_processes.probe_process(os.getpid())

    assert first.state == "running"
    assert first.creation_filetime is not None
    assert first.creation_filetime == second.creation_filetime
    age = windows_processes.filetime_age_seconds(
        first.creation_filetime, windows_processes.current_filetime()
    )
    assert age is not None and 0.0 <= age < 7 * 24 * 3600
    assert ubersight._process_start_ticks(os.getpid()) == str(first.creation_filetime)


@windows_only
def test_probe_reports_exited_child_as_not_running() -> None:
    child = spawn_sleeper()
    try:
        running = windows_processes.probe_process(child.pid)
        assert running.state == "running"
        assert running.creation_filetime is not None
    finally:
        child.kill()
        child.wait(timeout=30)

    # While this test still holds the Popen handle, Windows may report the
    # process as exited rather than missing; both prove it is not running.
    assert windows_processes.probe_process(child.pid).state in {"exited", "missing"}
    assert not ubersight._process_identity_is_live(
        child.pid, expected_start_ticks=str(running.creation_filetime)
    )


@windows_only
def test_documented_powershell_producer_recipe_matches_probe() -> None:
    """The spec's PowerShell recipe must yield the ticks Ubersight compares."""

    script = (
        "$ticks = (Get-Process -Id $PID).StartTime.ToFileTimeUtc(); "
        'Write-Output "$PID $ticks"; [Console]::Out.Flush(); '
        "Start-Sleep -Seconds 30"
    )
    producer = subprocess.Popen(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        encoding="utf-8",
    )
    try:
        assert producer.stdout is not None
        pid_text, ticks_text = producer.stdout.readline().split()
        assert int(pid_text) == producer.pid
        assert ubersight._process_start_ticks(producer.pid) == ticks_text
        assert ubersight._process_identity_is_live(
            producer.pid, expected_start_ticks=ticks_text
        )
    finally:
        producer.kill()
        producer.wait(timeout=30)
        if producer.stdout is not None:
            producer.stdout.close()


@windows_only
def test_live_windows_job_file_survives_and_stale_ticks_are_removed(
    tmp_path: Path,
) -> None:
    pid = os.getpid()
    live_ticks = ubersight._process_start_ticks(pid)
    assert live_ticks
    for job_id, ticks in (("live", live_ticks), ("reused", str(int(live_ticks) - 1))):
        (tmp_path / f"{job_id}.json").write_text(
            json.dumps(
                {
                    "schema": ubersight.BACKGROUND_JOB_SCHEMA,
                    "pid": pid,
                    "process_start_ticks": ticks,
                    "job_id": job_id,
                    "state": "running",
                }
            ),
            encoding="utf-8",
        )

    jobs = ubersight.load_background_jobs(tmp_path)

    assert [job.job_id for job in jobs] == ["live"]
    assert (tmp_path / "live.json").exists()
    assert not (tmp_path / "reused.json").exists()


# Process trees -------------------------------------------------------------


def test_build_process_tree_orders_descendants_depth_first() -> None:
    entries = [
        windows_processes.ProcessEntry(10, 1, "root.exe"),
        windows_processes.ProcessEntry(30, 10, "b.exe"),
        windows_processes.ProcessEntry(20, 10, "a.exe"),
        windows_processes.ProcessEntry(21, 20, "a-child.exe"),
        windows_processes.ProcessEntry(99, 1, "unrelated.exe"),
    ]
    created = {10: 100, 20: 200, 21: 210, 30: 300}

    tree = windows_processes.build_process_tree(10, entries, created.get)

    assert [entry.pid for entry in tree] == [10, 20, 21, 30]


def test_build_process_tree_rejects_children_of_reused_parent_pids() -> None:
    """A process older than its claimed parent belonged to a prior PID owner."""

    entries = [
        windows_processes.ProcessEntry(10, 1, "root.exe"),
        windows_processes.ProcessEntry(20, 10, "real-child.exe"),
        windows_processes.ProcessEntry(30, 10, "orphan-of-old-pid-10.exe"),
        windows_processes.ProcessEntry(40, 10, "unknown-age.exe"),
    ]
    created = {10: 1_000, 20: 2_000, 30: 500, 40: None}

    tree = windows_processes.build_process_tree(10, entries, created.get)

    assert [entry.pid for entry in tree] == [10, 20, 40]


def test_build_process_tree_tolerates_self_parent_and_missing_root() -> None:
    entries = [windows_processes.ProcessEntry(0, 0, "[System Process]")]

    assert [
        entry.pid
        for entry in windows_processes.build_process_tree(0, entries, lambda pid: None)
    ] == [0]
    assert windows_processes.build_process_tree(5, entries, lambda pid: None) == ()


def test_collect_windows_process_tree_builds_worker_rows() -> None:
    entries = (
        windows_processes.ProcessEntry(10, 1, "codex.exe"),
        windows_processes.ProcessEntry(11, 10, "pytest.exe"),
    )
    created = {10: 0, 11: 50_000_000}

    workers = ubersight._collect_windows_process_tree(
        10,
        snapshot=lambda: entries,
        probe_process=lambda pid: windows_processes.ProcessProbe(
            "running", created[pid]
        ),
        now_filetime=100_000_000,
    )

    assert [(w.pid, w.ppid, w.state, w.command) for w in workers] == [
        (10, 1, "-", "codex.exe"),
        (11, 10, "-", "pytest.exe"),
    ]
    assert [w.elapsed_seconds for w in workers] == [10.0, 5.0]


def test_collect_process_tree_uses_proc_root_when_given_on_any_platform(
    monkeypatch, tmp_path: Path
) -> None:
    """An explicit `/proc`-style root bypasses native Windows inspection."""

    monkeypatch.setattr(ubersight, "_is_windows", lambda: True)
    monkeypatch.setattr(
        ubersight,
        "_collect_windows_process_tree",
        lambda root_pid: pytest.fail("native inspection must not run"),
    )

    assert ubersight.collect_process_tree(1, proc_root=tmp_path) == ()


def test_clock_ticks_default_without_sysconf(monkeypatch) -> None:
    monkeypatch.delattr(os, "sysconf", raising=False)

    assert ubersight._clock_ticks_per_second() == 100


@windows_only
def test_collect_process_tree_finds_real_child_process() -> None:
    child = spawn_sleeper()
    try:
        deadline = time.monotonic() + 10.0
        workers: tuple[ubersight.WorkerProcess, ...] = ()
        while time.monotonic() < deadline:
            workers = ubersight.collect_process_tree(os.getpid())
            if child.pid in {worker.pid for worker in workers}:
                break
            time.sleep(0.05)
    finally:
        child.kill()
        child.wait(timeout=30)

    assert workers[0].pid == os.getpid()
    child_rows = [worker for worker in workers if worker.pid == child.pid]
    assert len(child_rows) == 1
    assert child_rows[0].ppid == os.getpid()
    assert child_rows[0].command.lower().startswith("python")
    assert child_rows[0].elapsed_seconds is not None


# Terminal handling ---------------------------------------------------------


class FakeCurses:
    """Record PDCurses resize calls."""

    def __init__(self) -> None:
        self.resizes: list[tuple[int, int]] = []

    def resize_term(self, lines: int, columns: int) -> None:
        self.resizes.append((lines, columns))


def test_resize_adopts_new_console_size_on_windows(monkeypatch) -> None:
    fake = FakeCurses()
    monkeypatch.setattr(ubersight, "_is_windows", lambda: True)

    ubersight._adopt_terminal_resize(fake)

    assert fake.resizes == [(0, 0)]


def test_resize_leaves_ncurses_to_update_itself(monkeypatch) -> None:
    fake = FakeCurses()
    monkeypatch.setattr(ubersight, "_is_windows", lambda: False)

    ubersight._adopt_terminal_resize(fake)

    assert fake.resizes == []


def test_snapshot_output_replaces_unencodable_characters() -> None:
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp1252", newline="\n")

    ubersight._replace_unencodable_output(stream)
    stream.write("Phase 11 \u2192 Windows\n")
    stream.flush()

    assert raw.getvalue() == b"Phase 11 ? Windows\n"
