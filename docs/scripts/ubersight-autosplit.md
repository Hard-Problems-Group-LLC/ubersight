# `ubersight-autosplit`

## Purpose

`ubersight-autosplit` arranges the current terminal into the usual working
layout:

- the pane you run it from stays top-left, for Claude or Codex;
- a utility console opens below it, taking about a third of the height;
  and
- Ubersight runs full height on the right, taking about 30% of the width.

It works in Windows Terminal, tmux, GNU screen, WezTerm, kitty, and
Terminator. The full contract is in
`docs/specifications/ubersight-autosplit.md`.

## Usage

Run it from a fresh, unsplit tab or window inside your project checkout.

PowerShell:

```powershell
.\.venv\Scripts\ubersight-autosplit
```

POSIX shell:

```bash
ubersight-autosplit
```

Useful options:

- `--dry-run` shows the commands without changing anything. Add `--json`
  for machine-readable output; `--json` only works with `--dry-run`.
- `--ubersight-width 0.25 --console-height 0.4` changes the proportions.
- `--backend tmux` (or `screen`, `wezterm`, `kitty`, `terminator`,
  `windows-terminal`) skips detection.
- `--project-dir PATH` targets a specific project from anywhere.
- `--force` builds the layout in the current directory even outside a
  Git checkout. Without it, running outside a checkout fails, because the
  dashboard would have no project to show.
- Arguments after `--` go to the dashboard, for example
  `ubersight-autosplit -- --no-geolocate`.

From a subdirectory, the console and dashboard still start at the project
root, where `.local/ubersight/status.json` lives.

## SSH and mosh

A terminal emulator can only be split on the machine where it runs. Inside
an SSH or mosh session, `ubersight-autosplit` therefore uses tmux or GNU
screen running on that remote host, or a WezTerm mux domain. Without one
of those, it refuses with a message. Start tmux or screen on the remote
host first, then run it there.

## IDE terminals

The integrated terminals of VS Code (and editors built on it) and of
JetBrains IDEs cannot be split. They also inherit variables from the
terminal that launched the IDE, which could send the new panes to that
other window. `ubersight-autosplit` therefore refuses there; run tmux or
GNU screen inside the IDE terminal first if you want the layout in it.
Pass `--backend` only if you are sure which terminal should be split.

## Per-terminal notes

- **Windows Terminal:** both new panes use your current profile.
- **tmux:** run it from an unsplit window to get a full-height dashboard.
  tmux older than 3.1 is supported through `-p` sizing.
- **GNU screen:** needs screen 4.2 or newer. Screen does not report failed
  region commands, so check the layout visually. Regions are lost on
  detach unless you save a layout.
- **WezTerm:** focus returns to the top-left pane on builds from 2023-03
  onward.
- **kitty:** enable `allow_remote_control` in `kitty.conf`. The tab
  switches to the `splits` layout.
- **Terminator:** needs 2.1.2 or newer, with its D-Bus option enabled (the
  default). Splits are always 50/50, and focus ends on the newest pane, so
  click the top-left pane afterward. The dashboard starts through your
  login shell, which must accept `&&` (nushell and elvish do not). If
  your default profile runs a custom command, that command replaces the
  dashboard.

## Side Effects

The command only creates panes or windows in the current terminal and
starts `python -m ubersight --supervise` in the project directory. The
supervisor restarts the dashboard after `ubersight-stop` requests and
after crashes, and Q closes the pane. The command does not modify project
files. Panes created before a failed step are left open.
