# Windows Platform Support

Status: In progress (Slices 11 and 12)  
Date: 2026-10-13  
Owner: Claude and operator  
Proposal: `project-management/proposals/approved/windows-platform-support.md`

## Purpose

Define how Ubersight behaves on native Windows. Linux behavior stays the
same unless this specification says otherwise.

## Platform Detection

Ubersight treats `os.name == "nt"` as native Windows. WSL, Cygwin, and MSYS
Python builds report POSIX and keep the POSIX behavior.

## Slice 11: Runtime Blockers

### Terminal Library

- On Windows, Ubersight depends on `windows-curses`, declared in
  `pyproject.toml` as `windows-curses>=2.4.2; sys_platform == "win32"`.
  This dependency is temporary. A later port to the Expletives library
  will replace it.
- The live loop must handle `KEY_RESIZE`. On Windows it must call
  `curses.resize_term(0, 0)` so PDCurses adopts the new console size, then
  redraw immediately. On ncurses it must redraw without calling
  `resize_term`.

### Runtime Directories

- **Project-scoped runtime material** stays under the project's ignored
  `.local/` tree.
  - On Windows, the default background-job directory is
    `.local/ubersight/background-jobs`. Like the other defaults, it is
    relative to the working directory, which is normally the project root.
  - `ubersight_runtime_root()` and `ubersight_context_root()` both return
    `.local/ubersight` on Windows. The project is already the runtime
    context, so `--project-name` changes only the banner.
  - `--background-job-dir` still overrides the default.
- **Cross-project material** goes under `%LOCALAPPDATA%\ubersight`. This
  root is reserved; nothing uses it today. Use it only when material must
  be visible across and between projects.
- **POSIX** keeps
  `$XDG_RUNTIME_DIR/ubersight/<context>/background-jobs`, with the existing
  `/run/user/$UID` and `/tmp/ubersight-$UID` fallbacks.
- Selecting the background-job directory must never call POSIX-only APIs
  on Windows. Status writes in particular must not depend on it.

### Subprocess Probes

- Probe commands must decode output as UTF-8 and replace undecodable
  bytes, so an unexpected byte can never raise from a probe.
- Probe commands must receive `stdin` from the null device, so a probe can
  never read from the dashboard's terminal.
- `git fetch` probes must run SSH in batch mode with a short connect
  timeout without overriding the user's configured SSH client:
  - if the environment already sets `GIT_SSH_COMMAND` or `GIT_SSH`,
    Ubersight leaves it unchanged;
  - otherwise, if `git config core.sshCommand` is set, Ubersight uses that
    command with `-o BatchMode=yes -o ConnectTimeout=5` appended;
  - otherwise Ubersight uses `ssh -o BatchMode=yes -o ConnectTimeout=5`.

### Atomic Files

- Every Ubersight JSON writer writes a temporary file in the target
  directory and then calls `os.replace`.
- On Windows, the replacement must retry with a short backoff, about one
  second in total, when it raises `PermissionError`. That error means
  another process briefly had the target open.
- If the replacement still fails, Ubersight removes the temporary file and
  reraises the error.
- The status loader must retry a read briefly on `PermissionError` before
  reporting an unreadable status.

### Snapshot Output

`--once` must not crash when a rendered line contains characters the
output encoding cannot represent. Such characters are replaced.

## Slice 12: Process Identity and Process Trees

### Background-Job Liveness

- On Windows, a job's PID is live when `OpenProcess` succeeds and the
  process has not exited (`WaitForSingleObject` with a zero timeout).
- Access denied means the process exists but cannot be inspected. Ubersight
  treats it as live and does not delete the job file.
- Other open failures mean the process no longer exists.
- `process_start_ticks` on Windows is the process creation time as a
  decimal count of 100-nanosecond intervals since 1601-01-01 UTC
  (`FILETIME`). When a job file records it, Ubersight treats the job as
  stale if the live process's creation time differs.
- Creation times are unique across boots, so `boot_id` is optional on
  Windows and ignored.
- Producers can obtain the value from:
  - PowerShell: `(Get-Process -Id $PID).StartTime.ToFileTimeUtc()`
  - .NET: `Process.StartTime.ToFileTimeUtc()`
  - Win32: `GetProcessTimes`

### Process Trees

- On Windows, `--process-root` uses a Toolhelp process snapshot for PIDs,
  parent PIDs, and executable names.
- A process counts as a child only if its creation time is not earlier
  than its parent's. This rejects stale parent PIDs that Windows has
  reused.
- Elapsed time comes from creation time.
- Windows has no cheap equivalent of the Linux run state, so the state
  column shows `-`. The command column shows the executable name.
- POSIX keeps `/proc` inspection.

### Fallback

If `ctypes` inspection proves fragile or too limited, for example when
full command lines are needed, the approved fallback is to add
`psutil; sys_platform == "win32"` behind the same internal interface.

## Later Slices

These slices get their own requirements when they start:

- 13: stop and restart workflow
- 14: network and location probes
- 15: install path
- 16: test-suite parity

Until Slices 15 and 16 land, 17 installer tests are marked
`POSIX_INSTALLER_ONLY` and skipped on Windows with an explicit reason. They
still run on POSIX hosts. Slice 16 must remove those skips.

## Follow-Ups Outside This Repository

FieldManual's Ubersight knack documents the POSIX background-job path for
producers. It needs a follow-up describing the Windows project-local path
and Windows `process_start_ticks` semantics. That work belongs to the
pending FieldManual migration.

## Acceptance Criteria

- On Windows:
  - `ubersight --write-status` writes `.local/ubersight/status.json`.
  - `ubersight --once` renders a snapshot.
  - `ubersight` runs live in a Windows Terminal pane and redraws on
    resize.
  - Background-job files written by live processes stay visible, and files
    from exited processes are removed.
  - `--process-root` shows the subtree of a live process.
- The dashboard tests that were failing on Windows before Slice 11 pass.
- The Linux test suite still passes.
