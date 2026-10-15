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

Numbering follows `project-management/development-roadmap.md`: phases in
thousands, and slices in thousands restarting at 1000 in each phase.

## Current Stack

Recently finished phases:

* Phase 12000: Terminal workspace autosplit, done.
* Phase 4000: Roadmap-backed status workflow, done again after its
  Slice 2000 (numbering in thousands).

Active phase: none. No work is in progress.

Upcoming phases:

* Phase 13000: Live terminal verification, planned; waits for the
  operator's Linux workstation.
* Phase 5000: Consuming-project onboarding contracts, planned.
* Phase 6000: Background-job producer integration, planned.
* Phase 7000: Status-schema hardening and validation, planned.
* Phase 8000: Dashboard usability refinement, planned.

## Current Work

Phase 4000 Slice 2000 renumbered the roadmap: phases in thousands, and
slices in thousands restarting at 1000 in each phase, with slices nested
under their phases. The roadmap keeps a table from the old flat numbers,
which older records and commit messages still use.

Phases 11000 and 12000 were merged to `trunk` on 2026-10-14. Phase 12000's
remaining work, live checks of the WezTerm, kitty, and Terminator
backends and of GNU screen over mosh, moved to Phase 13000 because it
waits for operator time.
