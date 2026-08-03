# Pyenv Curses Prerequisite Preflight

Status: Complete  
Date: 2026-07-20  
Owner: Codex and operator

## Purpose

Ensure that a managed Ubersight installation does not report success when the
selected Python runtime was compiled without the native `curses` extension
needed by the interactive terminal dashboard.

## Problem

`pyenv install` can successfully produce a Python interpreter while omitting
optional standard-library extensions whose system development headers were not
available at build time. On a first Fedora development installation,
`ncurses-devel` was absent. The managed Python 3.14.6 runtime, its virtual
environment, and the Ubersight launchers were created, but interactive startup
then failed because `_curses` was absent.

## Requirements

- `scripts/install_prerequisites.sh` must install ncurses development headers
  for the supported RPM, Debian-family, and Homebrew package managers. The
  root bootstrap path in `install.sh` must delegate to that script rather than
  maintaining a second package list.
- Stage two must verify that every selected Ubersight runtime can import
  `curses` before reporting a successful installation.
- Development and venv-only modes must validate the selected pyenv runtime
  immediately after pyenv provisioning, before creating or reusing the
  repository virtual environment.
- When pyenv must build a new base runtime, development and venv-only modes
  must first use the configured compiler and include flags to verify that a
  native curses header is available.
- When an existing development runtime lacks `curses`, stage two must check
  those headers, transactionally rebuild the configured pyenv context
  automatically, and verify the rebuilt interpreter before continuing. If that
  rebuild fails, it must restore the previous prefix and print the exact manual
  pyenv command.
- Standard installs must validate the final managed virtual environment.
- A failed check must give a concise, platform-relevant rebuild path and must
  not silently borrow another interpreter or elevate privileges.
- `scripts/install_prerequisites.sh` must be a directly usable prerequisite
  installer for a normal user before a pyenv build; normal development mode
  must not invoke it automatically or request elevated privileges.
- Regression tests must cover successful and failed native-module checks plus
  the development-runtime integration point.

## Non-Goals

- Automatically invoking `sudo` from a normal user-scoped development install.
- Rebuilding an existing pyenv runtime before the native-header preflight
  succeeds.
- Validating every optional CPython extension as part of this narrowly scoped
  terminal-dashboard prerequisite repair.

## Acceptance Criteria

- A missing `_curses` extension makes the managed installation fail with a
  useful remediation message rather than ending in `PASS`.
- Fedora/Rocky prerequisite plans include `ncurses-devel`.
- Debian-family prerequisite plans include `libncursesw5-dev`.
- Homebrew prerequisite plans include `ncurses`.
- After the host dependency is installed and `./install.sh --mode dev` is
  rerun, the configured pyenv runtime is rebuilt automatically and Ubersight
  starts in its normal curses mode.
