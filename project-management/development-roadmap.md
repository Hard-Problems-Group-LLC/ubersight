# Development Roadmap

Use this roadmap as the source of truth for Ubersight phase and slice lists.
Keep it current enough that `.local/ubersight/status.json` and
`project-management/state/phase-slice-stack.md` can show the two most recent
completed entries, the current entry, and up to five future entries without
inventing scope.

Do not record prompts, transcripts, credentials, operator-location details,
WiFi names, Tailnet names, or other sensitive runtime state here.

## Numbering

- Phases are numbered in thousands: Phase 1000, Phase 2000, and so on.
- Slices are numbered in thousands within their phase, starting again at
  Slice 1000 in every phase.
- The gaps leave room to insert work later without renumbering, such as
  Slice 1500 between Slices 1000 and 2000.
- Outside its own phase, name a slice with its phase, for example
  Phase 11000 Slice 4000.
- Pass bare numbers to `ubersight --write-status`, for example
  `--phase-row "12000:active:Title"` and `--slice "3000:active:Title"`.
  The dashboard adds the words "Phase" and "Slice".
- List rows in roadmap order: the dashboard shows them in the order given,
  in a window around the active row.

## Deferred Work

When part of a slice has to wait, for example for operator time, but
nothing is stuck, move that part to a later slice or phase, or add a new
one where it belongs. Update this roadmap and the Ubersight status
together. A slice is never left active just to wait.

## Phases and Slices

* Phase 1000: Standalone package foundation, done.
  * Slice 1000: Establish package layout and launchers, done.
  * Slice 2000: Restore managed quality-gate files, done.
* Phase 2000: Side-window dashboard runtime, done.
* Phase 3000: Operator context safety panels, done.
* Phase 4000: Roadmap-backed status workflow, done.
  * Slice 1000: Add roadmap-backed status convention, done.
  * Slice 2000: Number phases and slices in thousands, done.
* Phase 5000: Consuming-project onboarding contracts, planned.
  * Slice 1000: Document consuming-project onboarding, planned.
* Phase 6000: Background-job producer integration, planned.
* Phase 7000: Status-schema hardening and validation, planned.
  * Slice 1000: Harden status-writer validation, planned.
  * Slice 2000: Add roadmap parsing helper, planned.
* Phase 8000: Dashboard usability refinement, planned.
  * Slice 1000: Broaden dashboard smoke coverage, planned.
* Phase 9000: Release and install polish, done.
  * Slice 1000: Polish install and update workflow, done.
* Phase 10000: Execution-layer failure forensics, done.
  * Slice 1000: Investigate executable truncation, done.
  * Slice 2000: Harden command logs and recovery guidance, done.
* Phase 11000: Native Windows support, done.
  * Slice 1000: Fix Windows runtime blockers, done.
  * Slice 2000: Add Windows process identity, done.
  * Slice 3000: Port stop and restart workflow, done.
  * Slice 4000: Add Windows network probes, done.
  * Slice 5000: Add Windows install path, done.
  * Slice 6000: Reach Windows test parity, done.
  * Slice 7000: Add Linux wired-network mark, done.
* Phase 12000: Terminal workspace autosplit, done.
  * Slice 1000: Add autosplit core, WT, and tmux, done.
  * Slice 2000: Add autosplit GNU screen backend, done.
  * Slice 3000: Add WezTerm, kitty, Terminator, done.
  * Slice 4000: Pre-merge review and trunk merge, done.
* Phase 13000: Live terminal verification, planned.
  * Slice 1000: Verify WezTerm, kitty, and Terminator live, planned.
  * Slice 2000: Verify GNU screen over mosh, planned.

Notes:

- Phase 4000 was reopened on 2026-10-14 for the numbering convention. Its
  planned roadmap parsing helper moved to Phase 7000, where status
  validation also lives.
- Phase 12000's live checks of the graphical terminals wait for the
  operator's Linux workstation, so they moved to Phase 13000.
- Old Slice 2 (restore managed quality-gate files) was done during the
  2026-07-10 recovery, while old Phase 4 was active. It is filed under
  Phase 1000 by topic.

## Old Numbers

Records and commit messages written before this renumbering, late on
2026-10-14, use the earlier flat numbering, in which slices were numbered
across all phases. Old Phase N is
now Phase N000. Old slices map as follows:

| Old slice | New phase and slice |
| --- | --- |
| 1, 2 | Phase 1000, Slices 1000 and 2000 |
| 3 | Phase 4000, Slice 1000 |
| 4 | Phase 7000, Slice 2000 |
| 5 | Phase 5000, Slice 1000 |
| 6 | Phase 7000, Slice 1000 |
| 7 | Phase 8000, Slice 1000 |
| 8 | Phase 9000, Slice 1000 |
| 9, 10 | Phase 10000, Slices 1000 and 2000 |
| 11 to 16 | Phase 11000, Slices 1000 to 6000 |
| 17, 18 | Phase 12000, Slices 1000 and 2000 |
| 19 | Phase 12000, Slice 3000; its live checks are Phase 13000 |
| 20 | Phase 11000, Slice 7000 |
| 21 | Phase 12000, Slice 4000 |
