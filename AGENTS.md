<!-- THEKNOWLEDGE_MANAGED_HEADER_START -->
This project uses The Hard Problems Group's specifications and guidance
framework, TheKnowledge. Both AI agents and human developers should reference
`TheKnowledge/AGENTS.md` for detailed instructions.

AI agents must load `TheKnowledge/AGENTS.md` before running automated
tooling that edits, validates, stages, or tests repository files.

Keep only a very small amount of context locked in active memory: that
preflight rule, any live-state override record map, and repo-specific
non-destructive safety limits.

---

Any project-specific `AGENTS.md` content should go below this line and above
the managed TheKnowledge footer.
<!-- THEKNOWLEDGE_MANAGED_HEADER_END -->

# Ubersight Project Guidance

Ubersight is a terminal sidecar application for software-development
workflows. It is intended to run in a thin, tall terminal window beside a
larger multi-pane development terminal while human operators and AI agents
work in the main project context.

The application is being generalized from an existing project-specific
instance. While that migration is in progress, expect some incoming files to
carry old paths, local assumptions, or project-specific naming that should be
reviewed before being treated as stable product direction.

Ubersight must remain project-neutral. It may define status schemas, render
generic dashboard views, and read externally written JSON files, but it must
not import, name, or own consuming-project domain systems, renderers, data
products, search providers, or application runtimes.
Consuming projects write Ubersight-compatible status files from their own
domain code; Ubersight observes those files without knowing which project
produced them.

## Ubersight Status Reporting

- During long autonomous phase/slice work, create or update
  `.local/ubersight/status.json` with `ubersight --write-status`.
- Update `phase`, `phases`, `slices`, and `notes` at phase boundaries, slice
  transitions, before long validation waits, before ACP waits, and when
  blocked.
- For multi-phase work, publish the last two completed phases, the active
  phase, and the next five known phases when that information is known.
- Keep Ubersight phase and slice lists sourced from
  `project-management/development-roadmap.md`. That roadmap document should
  always exist; create or restore it before relying on Ubersight status when
  it is missing.
- Keep phase titles short. Source phase titles as titles only, not prose
  descriptions or state-bearing labels.
- Keep slice titles to one display row. Ubersight clips long slice text
  rather than wrapping it.
- Preserve the same high-level phase/slice plan in
  `project-management/state/phase-slice-stack.md` during long work so context
  compaction can recover the active stack.
- Use atomic writes for local status files and never commit
  `.local/ubersight/`.
- Keep status notes high-level. Do not include prompts, transcripts,
  credentials, operator-location details, WiFi names, Tailnet names, or other
  sensitive runtime state.
- Leave the status file stale rather than inventing progress after a crash or
  interruption.
- Clear or mark the status complete during final debrief.

## Sensitive Local State

- Treat project-root `.local/` as sensitive local-only state. It may contain
  operating-location, network, transfer, job-monitoring, operator-note, or
  other environment-specific information.
- Never stage, commit, push, quote, summarize, copy into tracked files, or
  otherwise publish `.local/` contents.
- Do not inspect `.local/` unless the active task specifically requires using
  those files as local policy inputs. If inspection is required, keep the
  details out of conversation summaries and tracked artifacts.
- Before any Git staging or commit operation, verify that no `.local/` paths
  are staged. If a `.local/` path is tracked or staged, stop and ask the
  operator how to proceed.
- Keep `.local/` ignored in the project `.gitignore`; do not remove or weaken
  that ignore rule.

<!-- THEKNOWLEDGE_MANAGED_FOOTER_START -->
---

<!-- TheKnowledge-managed footer: place overrides here when the consuming
project needs behavior different from TheKnowledge's own repository setup. -->

## TheKnowledge Overrides
- Use `project-management/backlog.txt` as ordered pending work.
- Keep `project-management/tasks-in-progress.txt` minimal and current.
- Record finished work at the top of
  `project-management/completed-tasks.txt` with ISO 8601 timestamps.
- Track operator actions for AI in `project-management/ai-human-requests.txt`.
- Track proposal records as Markdown files under
  `project-management/proposals/` using `approved/`, `rejected/`,
  `deferred/`, and `under-review/`.
- Use `project-management/deferred.txt` for explicitly deferred work.
- Queue brief commit-ready summaries in
  `project-management/state/pending-commit-changes.txt`.
- Maintain bug lifecycle summary files under `project-management/bugs/` and
  detailed bug records under `open/`, `in-progress/`, and `closed/`.
- Use `TheKnowledge/standards-and-practices/docs/`
  `AI-backlog-iteration.txt` when told to iterate the backlog.
- Use the consuming project's own `project-management/git-flow.txt` for branch
  and merge operations.
- Treat source documentation as delivery work. Every maintained source file
  should carry top-of-file context, every type and callable should be
  documented, and non-trivial control flow should carry local rationale where
  structure alone would be ambiguous.
- Prefer Markdown (`.md`) for maintained prose documents, internal records,
  starter documents, and generated guidance unless a file is machine-consumed,
  intentionally extensionless, or temporarily kept in a legacy format during
  an explicit migration.
- Treat project-root `.local/` as the standard home for checkout-local
  operator state and local policy inputs that AI agents must consider but
  must not commit or push.
- Load `.local/ai-local-notes.md` or `.local/ai-local-notes.txt` before broad
  automation, validation, staging, or cleanup when either file exists.
- Treat those local note files plus `.theknowledge-restricted-names.local` as
  local-only policy inputs: obey them when present, keep them untracked, and
  do not copy their contents into tracked prose unless the operator explicitly
  directs that.
- Do not name external client projects in TheKnowledge-bound records,
  proposals, bugs, feedback, or generated guidance. Use `an external project`
  or another operator-approved generic phrase instead.
- Keep any restricted-name source list local and untracked, such as
  `.theknowledge-restricted-names.local` or an out-of-tree equivalent, and do
  not flag the list itself during validation.
- If a restricted name appears as a directory or filename component, stop and
  ask the operator how to resolve the path before renaming, deleting,
  redacting, or moving anything.
- Imported ECR holding directories must use project-neutral names such as
  `imported-ecrs/`, not source-project names. Keep imported ECR basenames
  unchanged as source-facing identifiers, and track accepted or implemented
  files in the active TheKnowledge `accepted-ecrs-list.md`.
- Bootstrap, setup, prerequisite, and environment-selection code should
  follow Python 3.9 best practices unless a higher floor is documented.
- Normal runtime, automation, test, and developer-tooling code should follow
  Python 3.12 best practices unless a component is intentionally constrained.
- Use `./install.sh` when the project is relying on the managed starter
  Python toolchain. The default starter installs `install.sh`,
  `bootstrap.sh`, `scripts/install-stage-2.py`, `bootstrap-stage2.py`,
  `python-environments.json`, `.python-version`, `set-context.sh`,
  `set-context-bootstrap.sh`, `requirements-dev.txt`,
  `scripts/dev_setup.py`, `scripts/python_environment_bootstrap.py`,
  `scripts/tool_validation_profiles.py`, `tool_execution_constraints.json`,
  `tool_validation_profiles.json`, and a managed `.gitignore` block. That
  flow starts from Python 3.9+, defaults to a user-local standard install,
  supports explicit repo-local development mode and venv-only mode, permits
  system installs only with `--system` under root, requires `direnv` for
  development mode, and proactively ignores `.local/`,
  `.theknowledge-restricted-names.local`, `.codex-local/`, `.codex-home/`,
  `.codex`, `bin/codex-local`, and `README-LOCAL-Start-Codex.md`.
  `bootstrap.sh` and `bootstrap-stage2.py` remain compatibility wrappers.
- For substantive development work, prefix intermediary status
  updates with an inline bracketed ISO 8601 timestamp including the
  timezone offset, for example
  `[2026-03-25T01:05:12-07:00] Running full pytest.`
- Use timestamped updates when work begins, before and after
  commands or waits likely to take more than a few seconds, and at
  major phase boundaries.
- Include elapsed durations when they are easy to compute.
- Keep final answers readable; this rule applies to intermediary
  development updates for workflow profiling, not to every sentence
  of casual chat.
- Before any `git add`, list the files about to be staged and ask the
  operator whether to review them.
- Offer these staging-review choices: `1.` review at least one file in the
  changeset, `2.` proceed without review for this changeset, `3.` proceed and
  suppress review prompts for the rest of the current session until the
  operator asks to resume them.
- If review is requested, prefer changeset review in Meld when
  available as the default visual review path.
- Launch `meld .` from the repository or submodule root so Meld opens its
  version-control view for the full working tree.
- If Meld version-control view is unavailable or unsuitable, compare a
  temporary clean snapshot directory against the working tree in Meld's
  folder-comparison mode.
- Otherwise offer file-by-file review in the conversation or abort the
  staging step.
- Never guess a Git author or committer email address from commit history,
  hostnames, remote URLs, network overlays, or similar context.
- Require an explicit commit identity before creating a commit. Prefer
  configured `git` properties such as `user.name` and `user.email`; explicit
  `GIT_COMMITTER_*` and optional separate `GIT_AUTHOR_*` overrides are also
  acceptable when set deliberately.
- When a separate author identity is not explicitly configured, reuse the
  explicit committer identity for author instead of inventing a second
  address.
- Run `git diff` before any `git add` and `git diff --cached` before any
  commit. The standardized commit helper does both automatically.
- Use `python TheKnowledge/scripts/git_standard_commit_push.py -m
  "<subject>"` after the operator has either reviewed the changes or
  explicitly approved proceeding. Pass `--assume-reviewed` only after an
  explicit review decision made outside the helper. Use
  `--resume-review-prompts` to re-enable prompts for the current shell
  session. The helper rejects commits whose author/committer identity is not
  explicitly configured.
- `ACP` means "add, commit, push" through the project's VCS workflow. Treat
  it as a request to stage, commit, and push meaningful blocks with
  appropriate comments; it is not permission to collapse unrelated work into
  one monolithic commit or skip review, validation, and identity rules.
- When working primarily in the consuming project and discovering bugs,
  proposals, complaints, or general notes about TheKnowledge itself,
  record them on the TheKnowledge `Feedback` branch.
- When filing that feedback from a consuming project, use the active
  `TheKnowledge/` submodule checkout in that project. Prefer
  `python TheKnowledge/scripts/send_theknowledge_feedback.py prepare`
  and `finish` so the helper captures and restores the submodule state for
  you. Use `--push` only when the configured remote push URL is writable for
  the current operator, and use `abort` when you want to restore the prior
  state without publishing.
- When the active `TheKnowledge/` checkout is read-only for upstream
  maintenance, draft the request first under `ECRs/TheKnowledge/open/` in
  the consuming project so the handoff stays reviewable before it reaches a
  writable TheKnowledge checkout. Move the record to
  `ECRs/TheKnowledge/in-progress/` when active upstream handling begins, and
  move it to `ECRs/TheKnowledge/closed/` when a TheKnowledge proposal,
  implementation, rejection, or deferral record resolves it. Closed records
  should note which TheKnowledge record or commit settled the request.
- The `Feedback` branch is only for cross-project feedback flowing back
  into TheKnowledge. Direct maintenance of TheKnowledge itself should keep
  using its normal internal trees on `trunk`.
- Projects may keep proprietary or third-party knacks in the consuming
  project's top-level `knacks/` directory, separate from the stock knacks
  under `TheKnowledge/knacks/`.
- Validate changed knack files with
  `python TheKnowledge/scripts/validate_knacks.py --project-root .`.
- Knack validation should stay lightweight: malformed Markdown and
  high-entropy findings are errors, word-count overruns are warnings, the
  validator uses `.git/knack-validation-cache.json` when a writable
  Git-backed cache path is available and
  `.cache/knack-validation-cache.json` otherwise, and path collisions with
  stock knacks should warn while still evaluating both files.
- When updating the TheKnowledge submodule itself, prefer
  `python TheKnowledge/scripts/update_theknowledge_submodule.py`
  `--project-root . --knowledge-root TheKnowledge`. That helper fetches
  and briefly summarizes the incoming upstream `trunk` delta, adopts the
  reviewed version, runs the drift report, refreshes the managed starter
  files when needed, and leaves a reviewable parent-repo diff.
- For a manual managed-file check without adopting a new upstream commit, run
  `python TheKnowledge/scripts/report_managed_agents_drift.py`
  `--project-root . --knowledge-root TheKnowledge`.
- Follow `tool_execution_constraints.json` when it marks a tool-and-
  environment combination unsafe to parallelize. In a matched Codex sandbox,
  run Black only through
  `python TheKnowledge/scripts/run_tool_with_timeout.py black`; the
  harness will fall back to exactly one file at a time when Black would
  otherwise hit the known multi-file hang. Do not assume `black -W 1` is
  enough. If another file-safe formatter or linter still hangs inside a
  Codex sandbox on a multi-file run, retry the explicit file list one file at
  a time, and rerun the full required checks outside the affected sandbox or
  in CI before clearing the work.
<!-- THEKNOWLEDGE_MANAGED_FOOTER_END -->
