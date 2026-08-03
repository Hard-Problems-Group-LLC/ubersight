# Installer Command Log Safety

Status: Implemented

## Purpose

Installer diagnostics must remain readable without turning an accidentally
replayed terminal transcript into destructive shell redirection.

## Requirements

- Command diagnostics must use a plain label such as `RUN:` before the
  rendered command.
- Command diagnostics must not place `>`, `>>`, `<`, `|`, or another shell
  control operator between the diagnostic prefix and an executable path.
- Installer output remains informational and is not a shell script. Operators
  should rerun the reported installer command instead of replaying its output.
- Runtime integrity checks and transactional pyenv recovery remain the final
  defense when an executable is damaged outside the installer.
- Recovery diagnostics must direct operators back through `./install.sh` and
  must not recommend `pyenv install --force` against a live prefix.

## Verification

Tests must capture command diagnostics from the development setup and project
install hooks, require the `RUN:` label, and reject the former ` -> ` marker.
