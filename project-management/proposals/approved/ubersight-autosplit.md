# Ubersight Autosplit Workspace Command

| Field | Value |
| --- | --- |
| Title | Ubersight Autosplit Workspace Command |
| Author | Claude, drafter; Matt Heck, operator |
| Date | 2026-10-13T23:41:00-07:00 |
| Status | Approved |
| Reviewers | Matt Heck, operator |
| Related Work | `docs/specifications/ubersight-autosplit.md`; roadmap Phase 12 |

## Problem Statement

The operator's usual working layout has three panes:

- an AI agent (Claude or Codex) top-left;
- a utility console bottom-left; and
- Ubersight in a full-height right panel.

Building that layout by hand takes several terminal-specific steps every
session, and the steps differ between Windows Terminal, Linux terminal
emulators, and terminal multiplexers. A one-off `wt split-pane` recipe
showed that the terminal can build the layout itself from the command line.

## Goals

- Add a new console command, `ubersight-autosplit`, on Windows and Linux.
- Build the layout from the invoking pane:
  - the invoking pane stays top-left and gets focus back;
  - a new console takes the bottom third of the left column;
  - Ubersight runs full height on the right, about 30% of the width.
- Support Windows Terminal, tmux, GNU screen, WezTerm, kitty, and
  Terminator.
- Behave correctly in SSH and mosh sessions. Only a multiplexer running
  on the remote host can be split from there.
- Make the plan inspectable (`--dry-run`, `--json`) and testable without a
  real terminal.

## Non-Goals

- Launching the agent automatically. The invoking shell stays as-is and the
  operator starts Claude or Codex there. An optional top-pane command can
  come later.
- Restoring or saving arbitrary layouts.
- Restarting Ubersight inside the pane. That belongs to the Slice 13 stop
  and restart workflow.

## Constraints and Assumptions

- Terminal emulators can only be controlled on the machine running them.
  Environment variables such as `WT_SESSION`, `TERMINATOR_UUID`,
  `KITTY_WINDOW_ID`, and `WEZTERM_PANE` can leak into nested or forwarded
  sessions and must not be trusted inside SSH or mosh.
- Multiplexers own the visible pane when one runs inside an emulator, so
  they take precedence.
- Ubersight stays project-neutral. The command only arranges panes and
  runs `python -m ubersight` in the current directory.

## Proposed Approach

- A new module, `src/ubersight/autosplit.py`, plans the layout as plain data.
  One backend per terminal turns the layout into that tool's commands, and
  an executor runs the steps and stops at the first failure.
- Backend detection prefers tmux and screen, refuses emulator backends in
  remote sessions, and accepts an explicit `--backend` override.
- Ratios are configurable with `--ubersight-width` and `--console-height`.
- Arguments after `--` pass through to the dashboard.

## Risks and Mitigations

- **Risk:** Linux backends cannot be exercised on the Windows workstation.
  **Mitigation:** Unit-test every plan, base the command syntax on
  documented interfaces, and have the operator verify each backend live on
  Linux.
- **Risk:** A stale environment variable selects the wrong terminal.
  **Mitigation:** Use multiplexer precedence, refuse emulators in remote
  sessions, and offer `--backend` plus `--dry-run`.
- **Risk:** A failed step leaves a partial layout.
  **Mitigation:** Report exactly which step failed. Created panes are left
  in place rather than closed, since closing them could discard work.

## Testing and Validation

- Unit tests for detection, remote refusal, and every backend plan.
- Live verification in Windows Terminal on the workstation, and of the
  Linux backends on the operator's Linux hosts.

## Decision Log

- 2026-10-13T23:33:00-07:00 - Operator requested `ubersight-autosplit` on
  both platforms with the agent, console, and dashboard layout, after
  trying the Windows Terminal side-pane recipe.
- 2026-10-13T23:36:00-07:00 - Operator decisions:
  - Support Terminator, tmux, WezTerm, kitty, and GNU screen, and account
    for SSH and mosh connections.
  - Keep the invoking shell top-left rather than launching the agent.
  - Build the command on a new branch, `feature/ubersight-autosplit`,
    created from `feature/windows-platform-support`.
