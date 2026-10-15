# Windows Platform Support

Status: Implemented (Slices 11 through 16, and 20); verified on Linux 2026-10-14  
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

## Slice 13: Stop and Restart

### Restart requests

A live dashboard accepts a restart request. It then leaves curses through
its normal cleanup and exits with status 75.

- **Windows:** each dashboard creates a manual-reset named event,
  `Local\ubersight-dashboard-restart-<pid>`, and checks it while waiting
  for keys. Named objects in `Local\` belong to the logon session, so a
  sandboxed agent session can reach the operator's dashboards even though
  its `%LOCALAPPDATA%` is separate.
- **POSIX:** the dashboard catches `SIGTERM` and treats it as a restart
  request.
- **Response time:** a request takes effect within about 50 ms while the
  dashboard waits for keys. During a long probe, such as a slow `git
  fetch`, it takes effect when the probe finishes, and while the location
  editor is open, when the editor closes.
- **Ctrl+C:** the dashboard exits with status 130, without a traceback.

### `ubersight --supervise`

`--supervise` runs the dashboard, with the same arguments minus
`--supervise`, as a child process in a loop. The child shares the terminal.
What happens next depends on how the child exits:

| Child exit | Supervisor action |
| --- | --- |
| Status 75 (restart request) | Restart immediately |
| Status 0 (the operator pressed Q) | Exit with status 0 |
| Ctrl+C (`KeyboardInterrupt`, status 130, or Windows `STATUS_CONTROL_C_EXIT`) | Exit with status 130 |
| Any other status, including a crash or a forced stop | Print a message and restart after one second |

`--supervise` cannot be combined with `--once` or `--write-status`. It
replaces `scripts/ubersight_wrapper.sh` on every platform. The wrapper
remains for existing POSIX setups and still restarts after any exit,
including Q.

Details:

- **No abbreviations:** `ubersight` options cannot be abbreviated. An
  abbreviation such as `--sup` would turn on supervision without matching
  the literal `--supervise` that is removed from the child's arguments, so
  every child would start another supervisor.
- **Ctrl+C on POSIX:** Ctrl+C reaches the supervisor and the dashboard
  together. While the dashboard runs, the supervisor leaves it alone, so
  the dashboard can restore the terminal before it exits with status 130.
- **Source checkouts:** `scripts/ubersight.py` puts the checkout's `src` on
  `PYTHONPATH`, so its supervised child imports the same code.

### `ubersight-stop`

- **Finding dashboards:**
  - On Windows, it walks a process snapshot and opens each process's
    restart event; only dashboards create one.
  - On POSIX, it scans `/proc` for same-user dashboards: `python -m
    ubersight`, or a Python interpreter running a script named `ubersight`
    or `ubersight.py`. Editors, linters, and shells that only name such a
    file are not matched.
  - Supervisors, `--once` and `--write-status` runs, and the helper itself
    are never matched.
  - A process is kept only if its creation time reads the same before and
    after it is identified (on Windows, if its restart event still exists
    after the read), so a PID reused mid-scan is dropped.
- **Default action:** it sends restart requests, then waits up to
  `--wait-seconds` (default 5) for each matched dashboard to exit. It exits
  with status 0 when every dashboard exited, and status 1 when some are
  still running.
- **`--dry-run`:** lists matches only, and its summary says nothing was
  signaled.
- **`--force`:** after the wait, ends remaining dashboards:
  `TerminateProcess` on Windows, `SIGKILL` on POSIX. It acts only while the
  process's recorded creation time still matches, and never on a process
  without one, so a reused PID is not killed. On POSIX a small window
  remains between that check and `SIGKILL`. A supervised dashboard ended
  this way is restarted by its supervisor.
- **Old code:** dashboards started from code older than Slice 13 create
  no restart event, so `ubersight-stop` cannot find them on Windows.

### Autosplit

Once Phase 12 merges this slice, `ubersight-autosplit` starts its dashboard
pane with `--supervise`.

## Slice 14: Network and Location Probes

On Ethernet, the router's identity stands in for an SSID: the default
gateway's MAC address. Location memory already matches on it, so a wired
location can be learned with `E` and recognized later.

### Probes

The Windows probes run as fallback tiers after the existing Linux tools.
Those tools fail fast on Windows, and the Windows tools fail fast on
Linux, so neither platform's behavior depends on detection.

| Signal | Windows source | Notes |
| --- | --- | --- |
| Default gateways | `route print -4` | Rows whose destination and netmask are both `0.0.0.0` give the gateway and interface address. |
| Gateway MAC | `arp -a <gateway> -N <interface>` | Dash-separated MACs are normalized to lower-case colon form. |
| Local subnets | `route print -4` | On-link rows on interfaces that carry a default route. Host, loopback, link-local, and multicast routes are excluded, as is any interface without a default route, such as a VPN tunnel. |
| WiFi SSID and BSSID | `netsh wlan show interfaces` | Reads the `SSID` and `BSSID` (or `AP BSSID`) lines. |

All of these parse addresses and fixed column positions rather than
translated labels, so they work in any Windows display language.

Each observation runs each command once and reuses its output. On
Windows, probes and other commands never run a program from the current
directory (`NoDefaultCurrentDirectoryInExePath`). The dashboard runs from
project checkouts, and a planted `route.exe` or `ip.exe` there would
otherwise run on every probe.

### Limits

- Windows 11 can withhold WLAN details from command-line tools unless
  location services are enabled for desktop apps. The WiFi name then reads
  `unknown`, while gateway-based recognition keeps working.
- A failed probe does not refresh the network's observation time, so it
  is retried, but at the network refresh interval (30 seconds by
  default), not on every redraw. Each Windows observation costs about a
  second.
- In a Windows virtual machine without a WiFi adapter, the virtual
  network counts as Ethernet. Unlike Linux (see Slice 20), Windows has no
  check for paravirtual adapters yet.
- Linux keeps its existing tools. Their broken `iwconfig` tier, which
  passed a string where a tuple was meant, is fixed.

### Output decoding

Probe output is decoded as UTF-8 when it is valid UTF-8. Otherwise, on
Windows, it is decoded with the OEM code page that console tools use, so
non-ASCII WiFi names survive.

### Wired connections

When the WiFi probe affirmatively reports no WLAN connection and a default
gateway has a MAC address, the observation is marked wired. The footer then
replaces `WIFI: <ssid>` with `Ethernet (<label>)`, where the label is the
location-memory record's LAN name (`lan_name`), set in the `E` editor's
"LAN name" field. Until the LAN is named, the footer shows
`Ethernet (unknown LAN)`. LAN names live in location memory under
`.local/` for now and move with the per-user state fix. A WLAN query
withheld for location permission never counts as "no connection".

Slice 20 added the Linux wired mark. Linux has no WLAN service to ask, so
the evidence comes from `/sys/class/net` instead. With no WiFi name and a
router MAC, the observation is wired when every interface carrying a
default route is wired:

- **Wired:** an Ethernet-type device (`type` 1) backed by hardware
  (`device`).
- **Never wired:** 802.11 devices (`wireless`, `phy80211`, or `DEVTYPE`
  `wlan`), mobile broadband (`DEVTYPE` `wwan`), tunnels, veth pairs, and
  paravirtual NICs (`hv_netvsc`, `virtio_net`, `vmxnet3`, and Xen's
  `vif`). A virtual machine's NIC, including WSL2's, says nothing about
  the host's uplink.
- **Bridges, bonds, and VLANs** take their kind from the devices beneath
  them (`lower_*`): wireless if any is wireless, otherwise wired if any is
  wired.

A virtual machine with an emulated NIC, such as an `e1000` under VMware,
looks like hardware and is marked wired.

When every WiFi probe fails, the dashboard normally reuses the cached
WiFi name, because a failed probe is no evidence of a move. A wired
observation is the exception on both platforms: the cable explains the
missing name. Before Slice 20, moving from WiFi to a cable kept showing
the old WiFi name and never showed `Ethernet`.

### Per-user state

Location state is still per project. Moving it to a per-user store is
tracked as `project-management/bugs/open/per-user-operator-location-state.md`.

## Slice 15: Install Path

### Stage one

`install.ps1` is the Windows counterpart of `install.sh`. It runs in
Windows PowerShell 5.1 and PowerShell 7, and passes every argument to
`scripts/install-stage-2.py`. It picks the interpreter in this order:

1. `THEKNOWLEDGE_BOOTSTRAP_PYTHON`;
2. the pinned runtime via the `py` launcher;
3. the newest Python 3 via the `py` launcher;
4. `python` or `python3` on `PATH`, never the Microsoft Store aliases.
   The aliases are dropped before the first match is taken, so a real
   interpreter later on `PATH` still counts.

The selected interpreter must run and report Python 3.9 or newer. Stage two
runs through the same command that passed that check. The interpreter path
is never read back from Python's output, because the code-page conversions
on that pipe garble non-ASCII paths, such as a per-user Python under an
accented or Cyrillic account name.

Native commands never fail on stderr output alone; their exit codes decide.
Under Windows PowerShell 5.1, the script-wide `Stop` preference would
otherwise reject an interpreter that prints a warning, and would abort the
install at the first pip or git notice whenever the installer's output is
redirected.

### Stage two on Windows

- **Standard mode** (the default) builds `~\.local\share\ubersight\venv`
  from the selected interpreter and installs the checkout
  non-editable.
- **Development and venv-only modes** build the checkout's `.venv` from
  the selected interpreter, which must meet the runtime floor. A version
  other than the pinned one is reported. pyenv, direnv, and shell-init
  setup are skipped, with a printed note.
- **Venv-only mode** also installs `windows-curses` into `.venv`, because
  it skips the package install that brings it in otherwise. The
  requirement is read from `pyproject.toml` without `tomllib`, since stage
  two runs on Python 3.9. A runtime without curses gets a Windows remedy,
  not the ncurses advice.
- **Rebuilding a venv in use.** When the selected interpreter changes, the
  standard venv is rebuilt with `venv --clear`, and `dev_setup.py` does the
  same to `.venv`. That clear deletes files in turn and fails at the first
  one Windows refuses to delete, after `Lib` and `pyvenv.cfg` are already
  gone. So stage two first checks whether any `Scripts\*.exe` in the venv is
  running and stops, with nothing deleted, when one is. The message asks
  the user to close the running dashboards (press Q in each pane) and rerun
  the installer. A running executable can still be renamed on Windows, so
  the check opens it for writing instead; that fails only while it runs.
- `--system` is rejected on Windows.
- Managed Git hook wrappers are converted to LF after installation, because
  the shared hook installer writes CRLF on Windows and bash rejects it.
  ECR: `ECRs/TheKnowledge/open/git-hook-wrapper-crlf-on-windows.md`.

### Launchers

- Launchers are copies of pip's `.exe` launchers in `~\.local\bin`.
  Symlinks would need Developer Mode or elevation.
- A copy runs its own environment because pip embeds the environment's
  absolute Python path. That embedded path also identifies ownership.
- An existing file is replaced only when it is one of these:
  - a launcher for this environment;
  - a Python console-script launcher from another install mode;
  - an older symlink into this environment.

  Any other file is refused.
- A running launcher cannot be overwritten, so it is renamed aside. Renamed
  copies are deleted once they exit, at the latest on the next install.
- Venv-only mode removes only launchers that belong to the checkout's
  `.venv`.

### PATH

- When the launcher directory is missing from the user PATH
  (`HKCU\Environment\Path`), it is appended. The value keeps its registry
  type, and a `WM_SETTINGCHANGE` broadcast lets new terminals see it.
- `--no-modify-path` prints instructions instead.
- A directory inside the checkout is never added, so an isolated agent
  home cannot leak into the real environment.
- POSIX hosts only report a missing launcher directory.

### Isolated homes

Any user home inside the checkout counts as an isolated agent home. That
covers Codex's `.codex-home` and Claude Code's `.local/claude/HOME`, which
also redirects `USERPROFILE`, `APPDATA`, and `LOCALAPPDATA` on Windows. The
installer refuses such a home unless `--user-home` or
`--allow-isolated-home` is given.

## Slice 16: Test Parity

The whole suite runs on Windows. An installer test is skipped there only
when the behavior it covers never runs on Windows, and its skip reason says
so permanently:

- `POSIX_PYENV_ONLY` covers nine tests: pyenv checkout reuse, runtime
  contexts, curses-header preflights, broken-runtime repair, transactional
  rebuilds, and the pyenv interpreter integrity probe. The Windows stage
  two never builds or repairs a pyenv runtime; it uses the interpreter
  `install.ps1` selected.
- `POSIX_SHELL_ONLY` covers one test, `install_prerequisites.sh`. Windows
  needs no native curses build prerequisites, because `windows-curses`
  ships prebuilt wheels.

Tests of behavior that does run on Windows use platform-correct fixtures
instead of POSIX path literals. Those cover the development command log,
the standard install virtual environment, and Git hook installation.

## Linux Verification

On 2026-10-14 the branch was checked on Rocky Linux 10.2 and Fedora 44,
both running under WSL2, from a fresh clone and with a Linux-only `PATH`:

- `scripts/install_prerequisites.sh`, then `./install.sh` in standard and
  development modes. Development mode built pyenv Python 3.14.6 with curses
  on both. On Rocky it also fetched direnv, which Rocky 10 does not
  package.
- The full test suite and the uncached quality gate.
- Live `ubersight-stop` restarts of dashboards under `--supervise`.

The run found three problems, all fixed:

- Minimal Fedora images, such as the WSL image, have no `awk`, which
  pyenv's Python build needs. `install_prerequisites.sh` now installs
  `gawk` with dnf or yum.
- A PATH test asserted Windows path rules on every platform. Its
  trailing-backslash case is now Windows-only, and a platform-neutral test
  covers trailing separators everywhere.
- `ubersight-stop --dry-run` reported "0 still running" although it
  signals nothing. It now says nothing was signaled.

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
