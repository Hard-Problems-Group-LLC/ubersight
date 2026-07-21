# ECR: Pyenv Native-Extension Prerequisite Preflight

## Target

TheKnowledge managed Python bootstrap, installer templates, and prerequisite
installer guidance.

## Problem

A first Fedora development installation exposed that the managed bootstrap can
compile a pyenv Python without its optional `_curses` extension when native
ncurses development headers are absent. The resulting virtual environment and
launchers can be created successfully, while a curses application fails only
at interactive launch. The shared prerequisite installer package lists omit
`ncurses-devel` for RPM-family systems and an ncurses development package for
Debian-family systems. The shared installer also does not validate native
extensions after selecting a runtime.

## Requested Contract

- Include supported-platform ncurses development packages in the pyenv build
  prerequisite lists.
- Validate native capabilities required by a consuming project before an
  installer reports success, beginning with `import curses` for terminal UIs.
- Fail before or immediately after runtime provisioning with an actionable
  rebuild path, without silently selecting an unrelated interpreter.
- After the prerequisite preflight succeeds, automatically force-rebuild a
  known-bad managed pyenv runtime rather than requiring an operator to recall
  a version-specific pyenv command.
- Cover Fedora, Rocky, and Debian-family package mapping in installer tests.
- Make `scripts/install_prerequisites.sh` the directly usable source of the
  managed starter's host-package mapping, with `install.sh` delegating to it
  for its privileged bootstrap path.

## Evidence

The pyenv build completed on Fedora, but its `lib-dynload` directory had no
`_curses` extension, the runtime configuration had no curses flags, and the
installed `ncurses-devel` package query failed. The same managed flow had
previously been exercised on Rocky Linux hosts where build prerequisites were
already present.

## Status

Open. The consuming-project remediation is verified on Fedora; this record
captures the remaining upstream managed-starter contract gap for review.
