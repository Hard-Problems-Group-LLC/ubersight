# Fedora Pyenv Curses Prerequisite

Status: Closed  
Date Opened: 2026-07-20T21:04:26-07:00  
Date Resolved: 2026-07-20T23:28:52-07:00  
Owner: Codex and operator

## Symptom

After `./install.sh --mode dev`, the interactive Ubersight command exited with
`curses is unavailable: No module named '_curses'`.

## Root Cause

The selected pyenv Python 3.14.6 build omitted `_curses` because Fedora's
`ncurses-devel` package was not installed. The managed installer verified only
the Python version and therefore reported success despite the missing runtime
capability. Focused installer testing also exposed a source-path ordering
hazard that could load the legacy compatibility launcher instead of the
package while stage two imports project-specific installation helpers.

## Resolution

Added the missing distribution package prerequisites to
`scripts/install_prerequisites.sh` and made root bootstrap delegate to that
installer. Stage two now validates `curses`, preserves source-package import
precedence, checks native headers before new pyenv builds, and automatically
rebuilds a configured pyenv runtime that lacks curses after prerequisites are
available. Rebuilds now preserve the prior prefix and restore it when the fresh
installation or validation fails.

## Verification

The operator installed the Fedora prerequisites and reran the development
installer. The rebuilt runtime imported `curses` and `_curses`, and Ubersight
started in a timeout-limited real terminal smoke test. Black, Ruff, compileall,
entropy scanning, entropy tripwire validation, and the full 105-test suite
passed.
