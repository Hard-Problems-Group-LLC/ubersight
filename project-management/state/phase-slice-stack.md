# Ubersight Phase/Slice Stack

Use this tracked file as the recovery anchor for long autonomous work when
conversation context may be compacted.

Keep entries high-level and operational:

- Recent, active, and upcoming phases.
- Active slice stack and current slice.
- Short notes needed to resume work after interruption.

Do not record prompts, transcripts, credentials, operator-location details,
WiFi names, Tailnet names, or other sensitive runtime state here. Live status
for the side-window dashboard belongs under `.local/ubersight/` and must stay
untracked.

## Current Stack

Recent phases:

* Phase 9: Release and install polish, done.
* Phase 10: Execution-layer failure forensics, done.

Active phase:

* Phase 11: Native Windows support, active.

Upcoming phases:

* Phase 5: Consuming-project onboarding contracts, planned.
* Phase 6: Background-job producer integration, planned.
* Phase 7: Status-schema hardening and validation, planned.
* Phase 8: Dashboard usability refinement, planned.

Active phase slices:

* Slice 11: Fix Windows runtime blockers, active.
* Slice 12: Add Windows process identity, planned.
* Slice 13: Port stop and restart workflow, done.
* Slice 14: Add Windows network probes, planned.
* Slice 15: Add Windows install path, done.
* Slice 16: Reach Windows test parity, planned.

## Current Work

Phase 11 runs on `feature/windows-platform-support`. The specification is
`docs/specifications/windows-platform-support.md`, and the approved proposal
records operator decisions: `windows-curses` temporarily, standard-library
`ctypes` process inspection with a documented `psutil` fallback, plain-venv
first, and project-local `.local/` runtime state on Windows. Slices 11 and
12 land together because the POSIX-only liveness check would delete live
Windows background-job files. The Expletives terminal-library port and the
FieldManual migration stay out of this branch.
