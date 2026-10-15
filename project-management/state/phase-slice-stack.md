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

* Phase 10: Execution-layer failure forensics, done.
* Phase 11: Native Windows support, done.

Active phase:

* Phase 12: Terminal workspace autosplit, active.

Upcoming phases:

* Phase 5: Consuming-project onboarding contracts, planned.
* Phase 6: Background-job producer integration, planned.
* Phase 7: Status-schema hardening and validation, planned.
* Phase 8: Dashboard usability refinement, planned.

Active phase slices:

* Slice 17: Add autosplit core, WT, and tmux, done.
* Slice 18: Add autosplit GNU screen backend, done.
* Slice 19: Add WezTerm, kitty, Terminator, implemented; awaiting live verification.
* Slice 21: Pre-merge review and trunk merge, active.

Phase 11 slices, all done:

* Slice 11: Fix Windows runtime blockers, done.
* Slice 12: Add Windows process identity, done.
* Slice 13: Port stop and restart workflow, done.
* Slice 14: Add Windows network probes, done.
* Slice 15: Add Windows install path, done.
* Slice 16: Reach Windows test parity, done.
* Slice 20: Add Linux wired-network mark, done.

## Current Work

Phase 12 runs on `feature/ubersight-autosplit`, which merges
`feature/windows-platform-support` as Phase 11 slices land. The
specification is `docs/specifications/ubersight-autosplit.md`, and the
approved proposal records the operator's decisions.

Status:
- All six autosplit backends are implemented with unit-tested plans.
- Windows Terminal was verified live by the operator.
- The dashboard pane runs under `ubersight --supervise`, so
  `ubersight-stop` restarts it in place.
- tmux and GNU screen were verified live on the WSL2 Rocky and Fedora
  bench, locally and over SSH, and tmux over mosh. WezTerm, kitty, and
  Terminator await the operator's workstation and are backlogged.
- Phase 11 passed its Linux test run on 2026-10-14. Slice 20, the Linux
  `Ethernet (<LAN name>)` mark, was added afterwards.
- The operator approved merging both branches to `trunk` after a
  multi-angle review (Slice 21). The review's findings are fixed on both
  branches; the merge comes next.
