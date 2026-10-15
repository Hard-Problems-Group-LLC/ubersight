# ECR: Quality Gate Ignore Check Fails Open With Populated Submodules

## Target

`scripts/run_quality_gate_cached.py` in TheKnowledge and its managed copies
in consuming projects.

## Problem

The `entropy_check` and `entropy_tripwire_verify` scopes walk the whole
checkout and then drop Git-ignored files with one batched
`git check-ignore --stdin -z` call. `git check-ignore` rejects the whole
batch with `fatal: Pathspec '...' is in submodule '...'` (exit 128) whenever
any candidate path lies inside a populated submodule. Every consuming project
has such a submodule, `TheKnowledge/`, once it is checked out.

`git_ignored_paths` treats any exit code other than 0 or 1 as "nothing is
ignored". The gate then reads and hashes every ignored file in the checkout,
including all of project-root `.local/`. That is a sensitive-local-state
boundary violation even when it succeeds.

On a Windows consuming project, the gate crashed with `PermissionError` on a
locked browser profile database inside `.local/`, which blocked
`git_standard_commit_push.py` entirely. On Linux the same path silently
fingerprints `.local/` contents.

## Requested Contract

- Never walk or fingerprint project-root `.local/`, independent of Git
  ignore rules. Match by exact directory name, so unrelated paths ending in
  `local` are unaffected.
- Group ignore-check candidates by owning repository, using the gitlinks
  reported by `git ls-files --stage`. Ask each submodule about its own
  paths, recursing into nested submodules.
- Fail closed. A failed `git ls-files` or `git check-ignore` must stop the
  gate with a clear `FAIL` message instead of widening the scan.
- Add regression coverage with a real populated submodule, ignored local
  state, and a forced check-ignore failure.

## Evidence

In the consuming project, a batch mixing `TheKnowledge/AGENTS.md` and
`.local/ubersight/status.json` made `git check-ignore` exit 128. Checked
alone, the same `.local/` paths were reported ignored. The project now
carries a local fix and five regression tests in
`tests/test_quality_gate.py`. All five fail against the unmodified upstream
script and pass with the fix.

## Status

Open. Fixed locally in the consuming project's managed copy. TheKnowledge is
being superseded by FieldManual, which does not ship quality-gate tooling,
so upstream may prefer to record this against any remaining TheKnowledge
consumers rather than port it forward.
