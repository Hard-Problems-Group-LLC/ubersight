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
* Phase 11: Native Windows support, in progress (Slices 11-12 done).

Active phase:

* Phase 12: Terminal workspace autosplit, active.

Upcoming phases:

* Phase 5: Consuming-project onboarding contracts, planned.
* Phase 6: Background-job producer integration, planned.
* Phase 7: Status-schema hardening and validation, planned.
* Phase 8: Dashboard usability refinement, planned.

Active phase slices:

* Slice 17: Add autosplit core, WT, and tmux, active.
* Slice 18: Add autosplit GNU screen backend, implemented; awaiting Linux verification.
* Slice 19: Add WezTerm, kitty, Terminator, implemented; awaiting Linux verification.

Paused Phase 11 slices:

* Slice 13: Port stop and restart workflow, done.
* Slice 14: Add Windows network probes, planned.
* Slice 15: Add Windows install path, done.
* Slice 16: Reach Windows test parity, planned.

## Current Work

Phase 12 runs on `feature/ubersight-autosplit`, branched from
`feature/windows-platform-support` after Slices 11 and 12 were pushed. The
specification is `docs/specifications/ubersight-autosplit.md`, and the
approved proposal records the operator's decisions:
- support Windows Terminal, tmux, GNU screen, WezTerm, kitty, and
  Terminator;
- account for SSH and mosh sessions; and
- keep the invoking shell top-left.

All six backends are implemented with unit-tested plans. Windows Terminal
is verified on the workstation; the Linux backends await live operator
verification. Phase 11 resumes with Slice 13 afterward.
