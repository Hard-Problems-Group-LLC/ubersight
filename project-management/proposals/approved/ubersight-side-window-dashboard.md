# Ubersight Side-Window Dashboard

| Field | Value |
| --- | --- |
| Title | Ubersight Side-Window Dashboard |
| Author | Matt Heck, operator; Codex, drafter |
| Date | 2026-05-24T22:39:15-07:00 |
| Status | Approved |
| Reviewers | Matt Heck, operator; Codex, implementer |
| Related Work | TheKnowledge Codex support; phase/slice workflows |

## Problem Statement

The phase-and-slice workflow is working well, but long Codex runs still leave
the operator watching a text transcript that is optimized for conversation,
not operational awareness. The operator wants a small Ubersight side-window dashboard
that can sit next to a Codex terminal and show the current phase, slice,
high-level notes, and worker-process state at a glance.

Codex already has enough context to report this information, but it needs a
low-friction, structured feedback path that does not require constant chat
updates. A side-window terminal dashboard would make long autonomous runs
easier to supervise and would also give TheKnowledge a reusable pattern for
future AI-assisted development sessions.

## Goals

- Provide an ncurses terminal dashboard sized for roughly 40 rows by 40
  columns, with clean behavior in the expected 40x40 to 60x48 Terminator
  split range.
- Display a compact phase stack, ordered slices for the active phase, active
  phase and slice markers, debrief state, short notes, and worker-process
  summaries.
- Let Codex and project scripts publish status through one or more JSON files.
- Let external tools and runtimes publish active background-job status
  without sharing one mutable status file.
- Preserve the active phase/slice stack in a tracked working file so a
  compacted context can resume without relying only on live Ubersight state.
- Observe the Codex process stack without needing invasive debugger access.
- Keep Ubersight read-only with respect to repository source files.
- Add project AGENTS.md guidance that tells AI agents when and how to update
  the status files during long autonomous work.
- Keep the implementation project-local first, with a clean path to promote
  the pattern into TheKnowledge if it proves useful.

## Non-Goals

- Do not replace chat updates, final summaries, or project-management records.
- Do not require Codex internals or privileged process inspection.
- Do not scrape or persist sensitive prompt or transcript content.
- Do not implement a graphical UI.
- Do not make Ubersight mandatory for short tasks.

## Use Cases

- The operator runs Codex in one terminal and Ubersight in a narrow side
  terminal while Codex works through a multi-slice mission.
- Codex updates status after finishing a slice, starting validation, entering
  ACP, waiting on a long-running command, or hitting a blocker.
- Ubersight shows phase/slice progress and a few wrapped note lines while
  also showing whether build, test, git, or GUI smoke worker processes are
  active.
- A future TheKnowledge integration gives multiple projects the same
  monitoring convention with project-local status paths.

## Constraints and Assumptions

- The default display target is ASCII geometry near 40x40, but Ubersight
  should adapt to slightly larger or smaller terminals.
- ANSI color and simple box/line graphics are acceptable; Unicode should be
  optional because some terminals and remote shells may be conservative.
- Status files should live under a local-only path such as
  `.local/ubersight/` so they are never committed.
- JSON writes should be atomic so Ubersight never reads half-written status.
- Ubersight may use Python's standard `curses` module for the first
  implementation.
- Process monitoring should begin with ordinary process-tree inspection of
  the Codex PID or terminal session, then evolve if better signals appear.

## Proposed Approach

Add a project-local script such as `scripts/ubersight.py`. It should read
two classes of status input:

- **Agent status JSON:** written voluntarily by Codex or helper scripts.
- **Process status:** discovered from a configured Codex PID, parent PID,
  process group, terminal, or command-name filter.

The main agent status file should be small and stable. A first schema could
look like this:

```json
{
  "schema": "ubersight.status.v1",
  "updated_at": "2026-05-24T22:39:15-07:00",
  "phase": {"id": "6", "title": "Product display"},
  "phases": [
    {"id": "4", "title": "Transfer telemetry", "state": "done"},
    {"id": "5", "title": "GIBS preload", "state": "done"},
    {"id": "6", "title": "Product display", "state": "active"},
    {"id": "7", "title": "Cache fill", "state": "pending"}
  ],
  "slices": [
    {"id": "1", "title": "Inspect UI seams", "state": "done"},
    {"id": "2", "title": "Wire controls", "state": "active"},
    {"id": "3", "title": "Validate", "state": "pending"}
  ],
  "notes": "Word-wrapped sentences about what Codex is doing now.",
  "blocked": false
}
```

Ubersight should render something like:

```text
 Phase 4: Transfer telemetry
 Phase 5: GIBS preload
>Phase 6: Product display
 Phase 7: Cache fill

 Slice 1: Inspect UI seams
>Slice 2: Wire controls
 Slice 3: Validate

Notes
Here are a few word-wrapped
sentences about the current work.

Workers
build-debug.sh      run  00:41
pytest              idle
git helper          run  01:12
```

The display should use restrained ANSI styling:

- compact phase rows in the exact shape
  `<space-or->Phase <n>: <short name>`;
- compact slice rows in the exact shape
  `<space-or->Slice <n>: <description to end of line>`;
- one physical display row per phase or slice; long text is clipped, not
  wrapped;
- color, not inline state labels, to distinguish done, active, pending,
  blocked, failed, and skipped rows;
- red blocked or failed status;
- a narrow progress glyph or bar only when it adds signal; and
- fallback monochrome rendering when color is disabled.

Ubersight should accept command-line options:

- `--status PATH` for the agent status JSON;
- `--events PATH` for optional JSON Lines event history;
- `--codex-pid PID` or `--process-root PID`;
- `--refresh-ms N`, defaulting to a low, non-busy cadence;
- `--project-name NAME`, defaulting to the final path component of the
  current working directory and selecting both the displayed project and
  Ubersight runtime context;
- `--background-job-dir PATH`, defaulting to the selected Ubersight context's
  background-job directory;
- `--width N` and `--height N`, defaulting to 40x40 for `--once` snapshots;
- `--ascii` to avoid Unicode box drawing;
- `--no-color`; and
- `--once` for test snapshots.

### Background-Job Status

Runtime background-job status should use a file-per-job protocol under an
Ubersight-owned runtime root:

```text
$XDG_RUNTIME_DIR/ubersight/<project-name-or-context>/background-jobs
```

For a consuming project named `example-app`, this becomes:

```text
$XDG_RUNTIME_DIR/ubersight/example-app/background-jobs
```

If `XDG_RUNTIME_DIR` is unavailable, writers and readers should try
`/run/user/$UID/ubersight/...` and then fall back to
`/tmp/ubersight-$UID/...`.

Each active job writes one JSON file that includes its schema, update time,
PID, boot ID, process start ticks, job ID, source, subject, verb, state,
ticker text, work totals, throughput, and ETA. Writers replace the file
atomically on each update. Readers snapshot every visible job file, ignore
malformed data, and remove stale files when the recorded PID no longer exists
or no longer matches the recorded process identity.

Ubersight should render three data rows above the OPSEC/footer block:

- a ticker row that scrolls verbose job descriptions;
- a combined gauge row for all known active jobs; and
- a responsive statistics row that shows throughput, ETA, progress, job
  count, and byte counts as space allows.

This path avoids race conditions from a shared status file, gives
batch scripts and the C++ runtime the same low-friction publication protocol,
and keeps transient state out of the tracked checkout.

### Durable Phase/Slice Stack

Live Ubersight state remains local and transient. For long autonomous work,
agents should also maintain:

```text
project-management/state/phase-slice-stack.md
```

That file is a tracked recovery stack for context compaction. It should list
recent, active, and upcoming phases, the active slice stack, and short
operational notes. It must stay free of prompts, transcripts, credentials,
operator-location details, WiFi names, Tailnet names, and similar sensitive
state.

## AGENTS.md Guidance

The proposal should add project-level guidance, later promotable to
TheKnowledge, telling agents to:

- create or update `.local/ubersight/status.json` during long autonomous
  phase/slice work;
- update `phase`, `phases`, `slices`, and `notes` at phase boundaries,
  before long validation waits, before ACP waits, and when blocked;
- for multi-phase work, publish the last two completed phases, current active
  phase, and next five known phases when that information is known;
- keep phase titles short and source them as titles only, not prose
  descriptions or state-bearing labels;
- keep slice titles to one display row; they may be more descriptive than
  phase titles, but Ubersight will clip rather than wrap them;
- preserve the same high-level phase/slice plan in
  `project-management/state/phase-slice-stack.md` during long work;
- use atomic writes and never commit the status directory;
- keep notes high-level, avoiding secrets, transcript dumps, and unnecessary
  implementation detail;
- leave the status file stale rather than inventing progress after a crash or
  interruption; and
- clear or mark complete at final debrief.

## Risks and Mitigations

- **Risk:** Status reporting becomes busywork.
  **Mitigation:** Require updates only at phase/slice boundaries, waits, and
  blockers, not every command.
- **Risk:** Ubersight leaks private transcript or client details.
  **Mitigation:** Store only phase titles, short notes, and process summaries;
  keep paths local-only and untracked.
- **Risk:** Process detection is unreliable across shells and sandboxes.
  **Mitigation:** Make process monitoring best effort and let the JSON status
  path carry the authoritative high-level state.
- **Risk:** The 20x50 display becomes too cramped.
  **Mitigation:** Prioritize phase, active slice, notes, and worker status;
  truncate lower-priority fields with obvious ellipses.

## Testing and Validation

- Unit-test status schema parsing, stale-file handling, note wrapping,
  one-line phase/slice rows, color toggles, and process-summary
  normalization.
- Add `--once` golden-output tests for 40x40 and a smaller degraded terminal.
- Test atomic status updates by writing status files while Ubersight runs.
- Manually run Ubersight beside a Codex session during at least one
  multi-slice consuming-project task.
- Capture operator feedback before promoting the convention into
  TheKnowledge.

## Alternatives Considered

- **Continue chat-only status.** This keeps tooling simple but does not solve
  side-window operational awareness during long waits.
- **Use tmux status lines.** This is compact but too small for phase/slice
  notes and process summaries.
- **Build a web dashboard.** This is more flexible but heavier, less
  terminal-native, and unnecessary for the first proof.
- **Write directly into project-management records.** Those records are
  durable delivery artifacts, not live telemetry.

## Open Questions

- Should the first Ubersight implementation live only in one consuming project, or
  should a generic TheKnowledge prototype be drafted at the same time?
- Should the JSON schema support nested phase groups immediately, or keep one
  phase with one ordered slice list until a real need appears?
- Which process root is most reliable for the local Codex invocation on this
  operator's machines?
- Should status notes be retained as JSON Lines history, or should only the
  latest status be kept by default?

## Milestones

1. Draft schema and AGENTS.md guidance.
2. Implement `scripts/ubersight.py` with `--once`, `--ascii`,
   `--no-color`, and status-file input.
3. Add worker-process discovery for a supplied PID/process group.
4. Add tests for layout, wrapping, stale status, and process summaries.
5. Trial Ubersight during one long phase/slice task.
6. Decide whether to promote the convention into TheKnowledge Codex support.

## Adoption and Rollout

Start as an optional development utility. Keep all live status files under
`.local/` and do not require the tool for ordinary tasks. If the operator
finds it useful, keep the Ubersight schema and AGENTS.md guidance reusable for
other consuming projects.

## Decision Log

- 2026-05-24T22:39:15-07:00 - Drafted from operator request after a long
  phase/slice work stack completed.
- 2026-05-24T20:06:42-07:00 - Approved by the operator for immediate
  implementation as a terminal dashboard suitable for a Terminator split.
- 2026-05-26T13:12:00-07:00 - Added phase-stack standing behavior: render the
  last two completed phases, current phase, and next five known phases above
  the active slice stack.
- 2026-05-26T19:40:00-07:00 - Added Ubersight runtime background-job
  contexts and a tracked phase/slice recovery stack.
- 2026-05-27T00:11:00-07:00 - Standardized phase and slice display rows to
  compact, one-line forms with marker and color carrying state.
