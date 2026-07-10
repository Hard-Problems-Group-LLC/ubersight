# ECR: Scrubber Interpreter Ownership

## Target

`scrub-volatiles.py` and the AI-assisted workstation recovery workflow.

## Problem

A dry run against a project whose checked-in pyenv version had been removed
selected `/usr/bin/python` 3.12.13 and declared the project venv rebuildable.
That conflicts with the workstation rule that Rocky system Python remains
DNF-owned and must not become a project dependency container.

The dry run did expose the selected interpreter clearly, which allowed manual
rejection. The unsafe ownership choice should become a first-class diagnostic
rather than relying on operator scrutiny.

## Requested Change

- Add an interpreter-ownership policy or explicit rejection mode for system
  Python when rebuilding project venvs.
- When a project-local selector names an unavailable exact pyenv version, fail
  closed or require `--python` instead of silently accepting a system
  interpreter.
- Report which selector, fallback, and ownership rule produced the choice.
- Support a reviewed configuration that selects the user-global pyenv runtime
  for modernization while retaining the projects declared compatibility floor.
- Document protected-state exclusion examples for `.local`, project-local
  Codex homes, and project-local Claude homes.
- Add a machine-readable plan mode so automation can reject ownership or
  protected-path violations before `--apply`.

## Status

Open. The recovery succeeded with an explicit pyenv 3.14.6 override and
protected-path exclusions; scrubber-level ownership enforcement remains pending.
