# ECR: Git Hook Wrappers Are Written With CRLF on Windows

## Target

`scripts/install_git_hooks.py` in TheKnowledge.

## Problem

`install_hook` writes the managed bash wrappers with
`hook_path.write_text(wrapper, encoding="utf-8")`. Text mode translates
`\n` to `\r\n` on Windows. Git for Windows then runs each hook with bash,
which fails on `set -euo pipefail\r`, so every commit and push is blocked
once the hooks are installed.

## Requested Contract

- Write hook wrappers with LF line endings on every platform, for example
  `open(path, "w", encoding="utf-8", newline="\n")`, or `write_bytes`.
- Add regression coverage that the written wrapper contains no `\r`.

## Evidence

A Windows development install of an external project installed
`pre-commit` and `pre-push` with CRLF endings. After converting them to LF,
both hooks run correctly under Git for Windows' bash, and the full quality
gate passes from `pre-push`.

## Status

Open. The consuming project converts the managed hooks to LF after
installation on Windows (`normalize_git_hook_line_endings` in
`scripts/install-stage-2.py`). TheKnowledge is being superseded by
FieldManual, so this matters only for remaining TheKnowledge consumers.
