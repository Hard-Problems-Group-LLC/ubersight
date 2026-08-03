# ECR: Installer Command Log Shell-Redirection Hazard

## Target

TheKnowledge command-log helpers, managed installer templates, and generated
consumer-project setup scripts.

## Problem

Several command runners render diagnostics as `[component] -> <command>`.
When a terminal transcript is accidentally replayed by a POSIX shell, `->` is
parsed as an ordinary `-` argument followed by `>` output redirection. The
shell opens the following executable path with truncation before it attempts
to run the invalid bracketed command.

This behavior damaged a shared pyenv interpreter through a generated
virtual-environment symlink. Two later diagnostic lines created empty files
named after GNOME commands, providing an independent timestamp match for the
same parsing mechanism.

## Requested Contract

- Replace `->` command markers with a shell-neutral label such as `RUN:` in
  shared helpers and managed templates.
- Add regression coverage that command diagnostics do not place shell control
  operators before rendered executable or destination paths.
- Keep diagnostics explicitly informational rather than copyable transcript
  fragments.
- Retain runtime-integrity detection and transactional rebuild behavior as
  defense in depth for damage caused outside the installer.
- Do not recommend `pyenv install --force` against a live managed prefix.

## Evidence

A safe temporary reproduction replayed representative diagnostic lines. The
line targeting a virtual-environment Python symlink reduced its underlying
runtime file to zero bytes. Two command-name targets became empty regular
files. These outcomes match the incident artifacts and their ordering.

## Status

Open. The consuming project now uses `RUN:` in its development setup and
project-install hooks and has regression coverage for both emitters.
