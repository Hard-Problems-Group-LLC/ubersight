# Shared Pyenv Interpreter Corruption

Status: Closed  
Date Opened: 2026-08-03T14:14:50-07:00  
Date Resolved: 2026-08-03T15:05:24-07:00  
Owner: Codex and operator

## Symptom

Ubersight and unrelated Python tools emitted shell errors such as `import:
command not found`. Their generated entry points were valid Python scripts, but
their shebangs ultimately selected the same damaged pyenv Python 3.14.6
runtime.

## Incident Evidence

The shared `python3.14` executable was healthy during a successful development
install at 13:59 and became an executable zero-byte file at
2026-08-03T14:02:19-07:00. No other file in its 443 MiB pyenv prefix changed in
that interval. Two command-name files were created empty in an external
checkout during the following second.

Shell history preserved a terminal transcript as commands. Its managed
installer diagnostics used the form `[component] -> <command>`. Bash parsed
the marker as an ordinary `-` argument followed by `>` output redirection.
Multiple lines redirected into a virtual-environment Python symlink whose
target was the shared pyenv interpreter. Later lines redirected first and
then twice into the two command-name files. Their birth and modification times
match that history order.

The writes overlapped a Codex escalated-command broker interval in another
checkout, but exact-version Codex source inspection found that its PATH-alias
and escalation paths create private symlinks, sockets, and processes without
opening intercepted executables for writing. The overlap was coincidental.

## Root Cause

A terminal transcript was replayed by Bash. The shell treated command-log
`->` markers as output redirection and followed a virtual-environment Python
symlink while opening its target with truncation. Ubersight amplified the
impact because stage one could remain trapped behind shims for the broken
pyenv selection, stage two considered an existing interpreter path sufficient,
and the prior recovery helper used `pyenv install --force` against the live
prefix.

## Resolution

- Stage one now probes physical system and alternate pyenv interpreters after
  PATH-based candidates fail, allowing stage two to start around broken shims.
- Stage two verifies that the selected interpreter is nonempty, executable,
  runnable, and reports the configured patch release.
- An integrity failure exits without changing pyenv unless the operator adds
  `--rebuild-broken-pyenv` to the install command.
- An authorized rebuild moves existing base and legacy virtualenv prefixes to
  private backups, performs a fresh non-force install, validates version and
  `curses`, restores the backups after any build or validation failure, and
  removes backups only after success.
- Ubersight command diagnostics now use the shell-neutral `RUN:` label instead
  of `->`, with tests covering both development setup and project installation.
- Recovery errors direct operators back through the transactional installer
  and no longer recommend a manual force rebuild against the live prefix.
- A project-local TheKnowledge ECR requests the same log-safety contract for
  shared helpers and generated installer templates.

## Verification

The real unflagged development installer detected the empty interpreter and
exited with the guarded recovery instruction. The flagged installer rebuilt
Python 3.14.6, validated `curses`, refreshed the editable Ubersight environment,
and restored working project and user launchers. A safe temporary reproduction
confirmed that representative `->` diagnostics produce the observed
truncation and empty files. The uncached full quality gate passed after the
repair and final hardening.
