# Native Windows Platform Support

| Field | Value |
| --- | --- |
| Title | Native Windows Platform Support |
| Author | Claude, drafter; Matt Heck, operator |
| Date | 2026-10-13T22:56:20-07:00 |
| Status | Approved |
| Reviewers | Matt Heck, operator |
| Related Work | `docs/specifications/windows-platform-support.md`; roadmap Phase 11 |

## Problem Statement

Ubersight runs only on Linux and similar POSIX hosts. On native Windows 11
the dashboard, the status writer, and the stop helper fail before they
render or write anything. Several probes also misbehave without failing:

- runtime-root selection calls `os.getuid()`;
- background-job liveness and process-tree inspection read `/proc`;
- network probes call `nmcli`, `iw`, and `ip`; and
- the managed installer builds Python with pyenv and expects ncurses
  headers.

The operator now works on Windows and needs the side-window dashboard, and
the `--write-status` workflow that agents use, to work there.

## Goals

- Run the interactive dashboard and `--once` snapshots on native Windows
  in Windows Terminal or the classic console host.
- Let agents publish status with `ubersight --write-status` on Windows.
- Keep background-job liveness checks correct on Windows, including after
  PID reuse, and never delete a live job's status file.
- Restore `--process-root` subtree summaries on Windows.
- Reach Windows parity for the stop/restart helper, network/location
  probes, installation, and the test suite in later slices.
- Preserve existing Linux behavior.

## Non-Goals

- Porting terminal rendering to the Expletives library. That port is a
  separate future effort on its own branch.
- Migrating project guidance from TheKnowledge to FieldManual. That
  migration is pending and out of scope here.
- WSL-specific behavior. WSL runs the existing Linux path.
- A graphical user interface.

## Constraints and Assumptions

- Windows 11, CPython from python.org, PowerShell 7, and Windows Terminal
  are the reference environment.
- The standard Windows CPython build has no `_curses`. Ubersight
  temporarily depends on `windows-curses` (PDCurses) on Windows only.
- Ubersight runtime code should stay standard-library-only apart from that
  platform-marked dependency.
- Checkout-local state belongs under the project's ignored `.local/` tree.

## Proposed Approach

Deliver the work as roadmap Phase 11, one slice at a time, on
`feature/windows-platform-support`:

1. Fix the runtime blockers. Select a Windows background-job directory
   without `os.getuid()`, add the `windows-curses` dependency, decode
   subprocess output as UTF-8, retry atomic replacements around Windows
   sharing violations, handle PDCurses resizes, and keep probes from
   prompting.
2. Inspect Windows processes with standard-library `ctypes` calls into
   `kernel32`: Toolhelp snapshots, `OpenProcess`, and `GetProcessTimes`.
3. Replace signal-based stop/restart with a cross-platform supervisor and
   stop request.
4. Add Windows network and location probes.
5. Add a Windows installation path, initially a documented plain virtual
   environment and later managed-installer support.
6. Bring the existing test suite to parity on Windows.

## Risks and Mitigations

- **Risk:** `windows-curses` behaves differently from ncurses.
  **Mitigation:** Keep curses usage small, handle `KEY_RESIZE`, and verify
  live in Windows Terminal. The Expletives port will replace it later.
- **Risk:** Windows reuses PIDs quickly.
  **Mitigation:** Compare process creation times, which are unique across
  boots.
- **Risk:** `ctypes` inspection proves fragile or too limited, for example
  for full command lines.
  **Mitigation:** Fall back to a Windows-only `psutil` dependency behind
  the same internal interface.

## Testing and Validation

- Unit-test Windows and POSIX path selection, atomic-write retries,
  process identity, and process-tree construction on both platforms using
  injected fakes. Run real `ctypes` tests only on Windows.
- Keep the full suite passing on Linux. Reduce Windows failures slice by
  slice.
- Smoke-test the live dashboard in a Windows Terminal split pane.

## Decision Log

- 2026-10-13T22:46:10-07:00 - Operator directed the native Windows port
  after an initial survey of POSIX dependencies.
- 2026-10-13T22:48:00-07:00 - Operator decisions:
  - Use `windows-curses` temporarily. The intended long-term direction is
    a separate port to the Expletives library, kept out of this branch.
  - Approved standard-library `ctypes` process inspection. Document a
    `psutil` fallback.
  - Approved running from a plain virtual environment first, with managed
    installer support later.
  - Keep project-scoped runtime material under the project's ignored
    `.local/` tree. Use `%LOCALAPPDATA%` only for material that must be
    visible across projects. Nothing currently needs that, but the
    convention should be documented.
- 2026-10-13T22:49:00-07:00 - Operator noted that TheKnowledge is being
  replaced by FieldManual. That migration is out of scope for this branch.
