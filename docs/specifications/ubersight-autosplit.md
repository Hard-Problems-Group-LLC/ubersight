# Ubersight Autosplit

Status: Implemented; live verification pending on Linux backends  
Date: 2026-10-13  
Owner: Claude and operator  
Proposal: `project-management/proposals/approved/ubersight-autosplit.md`

## Purpose

`ubersight-autosplit` turns the current terminal into the operator's
working layout:

```text
+---------------------------+-----------+
|                           |           |
|  invoking pane (agent)    |           |
|  about 67% of the height  | Ubersight |
|                           |   about   |
+---------------------------+   30% of  |
|  console                  |  the width|
|  about 33% of the height  |           |
+---------------------------+-----------+
```

The invoking pane keeps its shell and gets focus back wherever the backend
allows it. The console and dashboard panes start in the project directory.

## Command Line

```text
ubersight-autosplit [--backend auto|tmux|screen|wezterm|kitty|terminator|windows-terminal]
                    [--ubersight-width 0.30] [--console-height 0.33]
                    [--project-dir PATH] [--force] [--dry-run [--json]]
                    [-- UBERSIGHT_ARGS...]
```

- `--ubersight-width` is the dashboard's share of the window width.
  `--console-height` is the console's share of the left column. Both
  accept 0.1 through 0.9.
- The dashboard runs as `<current Python> -m ubersight UBERSIGHT_ARGS`, using
  absolute paths. A pane started by a terminal server does not inherit the
  caller's `PATH` or activated virtual environment.
- `--dry-run` prints `RUN:` lines without changing anything. These are
  shell-neutral labels, not a script. `--json` prints the plan as JSON.

## Project Directory

- The default project directory is `git rev-parse --show-toplevel` run from
  the current directory. Running from a subdirectory still starts the
  dashboard at the project root, where `.local/ubersight/status.json`
  lives.
- Outside a Git checkout, the command fails with a message naming
  `--project-dir` and `--force`.
- `--project-dir PATH` selects an explicit directory, which must exist.
- `--force` proceeds in the current directory with a warning. It does not
  excuse a missing `--project-dir`.

## Backend Detection

1. Read the environment markers:
   - `TMUX` together with `TMUX_PANE`;
   - `STY`;
   - `WEZTERM_PANE`;
   - `KITTY_WINDOW_ID`;
   - `TERMINATOR_UUID`;
   - `WT_SESSION`, on Windows only.
2. Walk the process ancestry, nearest first. The first terminal or
   multiplexer process whose marker is present wins. Markers leak into
   nested sessions, so the process tree breaks ties, for example screen
   inside tmux, or tmux started in Terminator.
3. Stop the walk at an SSH or mosh boundary (`sshd`, `sshd-session`,
   `sshd.exe`, `mosh-server`). An emulator above that boundary belongs to
   another session.
4. Without a decisive ancestor:
   - use tmux or screen when exactly one marker is present;
   - if both are present, fail and ask for `--backend`;
   - refuse emulator backends when the session is remote. mosh keeps the
     bootstrap `SSH_*` variables, so those variables cover mosh too.
5. Otherwise use the first emulator marker found, in this order: WezTerm,
   kitty, Terminator, Windows Terminal.

An explicit `--backend` skips detection, including the remote refusal.

## Backends

### Windows Terminal

One chained `wt -w 0` call:

1. `split-pane -V -s WIDTH` runs the dashboard on the right.
2. `move-focus left`.
3. `split-pane -H -s HEIGHT` adds the console below.
4. `move-focus up`.

Details:

- Both new panes reuse `WT_PROFILE_ID` with `-p` when it is set.
- `wt` treats an unescaped `;` in any argument as a subcommand delimiter,
  so inserted values escape it as `\;`.
- `-w 0` targets the most recently used window, which is the one the
  operator is typing in.

### tmux

1. `split-window -h -d -t $TMUX_PANE -l W% -c DIR -- DASHBOARD...`
2. `split-window -v -d -t $TMUX_PANE -l H% -c DIR`
3. `select-pane -t $TMUX_PANE`

Details:

- `-d` keeps focus on the invoking pane.
- Before tmux 3.1, the plan uses `-p N` instead of `-l N%`.
- The dashboard argv follows `--`, so tmux runs it directly instead of
  through `default-shell`, which may not be POSIX.
- Splits are relative to the invoking pane, so run it from an unsplit
  window for a full-height dashboard.

### GNU screen (4.2 or newer)

The plan uses `screen -X` for region commands, in this order: `split -v`;
`focus right`; a dashboard window; `resize -h W%`; `focus left`; `split`;
`focus down`; a console window; `resize -v H%`; `focus up`.

Details:

- New windows are created by a plain `screen -t TITLE [DASHBOARD...]`
  started in the project directory. `-X chdir` would permanently change
  the server's directory, and `-X` re-parses `$` and backslashes.
- Every step inherits the terminal, because screen identifies the caller's
  display from stdin.
- `screen -X` exits 0 even when a command fails, so the layout must be
  checked visually.
- Password-protected sessions refuse `-X`.

### WezTerm (build 20220624 or newer)

1. `wezterm cli split-pane --pane-id $WEZTERM_PANE --right --percent W --cwd DIR -- DASHBOARD...`
2. `... --bottom --percent H --cwd DIR`
3. `wezterm cli activate-pane --pane-id $WEZTERM_PANE`, from build 20230326

Details:

- Older builds leave focus on the console and print a note.
- Inside a WezTerm mux server, including SSH mux domains, the CLI runs on
  that host and the backend works remotely.

### kitty

The plan uses `kitten @`, or `kitty @` when `kitten` is not installed:

1. `goto-layout --match window_id:$KITTY_WINDOW_ID splits`
2. `launch --type=window --self --next-to=id:ID --location=vsplit --bias=W --keep-focus --cwd=DIR -- DASHBOARD...`
3. `launch ... --location=hsplit --bias=H --keep-focus --cwd=DIR`

Requirements and limits:

- `allow_remote_control` must be enabled in `kitty.conf`.
- `vsplit` and `hsplit` only work in the splits layout. The tab switches
  to it, which rearranges other windows in that tab.
- Older releases degrade:
  - `--bias` needs 0.36; without it, splits are even.
  - `--next-to` needs 0.40.
  - The splits layout needs 0.17.
- Steps inherit the terminal because kitty can answer over the tty.
- Remote control over SSH would launch the windows on the local machine,
  so remote sessions are refused.

### Terminator (2.1.2 or newer)

1. `remotinator vsplit -u $TERMINATOR_UUID -T Ubersight -x "cd DIR && exec DASHBOARD"`
2. `remotinator hsplit -u $TERMINATOR_UUID -T Console`

Details:

- `vsplit` makes side-by-side panes and `hsplit` makes stacked ones.
- `remotinator` prints `ERROR: ...` with exit status 0, so output starting
  with `ERROR` counts as failure.

Known limitations:

- Splits are always 50/50.
- Focus moves to the newest pane and cannot be returned.
- The console starts in the invoking terminal's directory.
- D-Bus control is local-only.
- Terminator layouts can set ratios but only create new windows. An exact
  ratio mode would have to open a new window.

## Execution

- Steps run in order. The first failure stops the command with the step
  number, exit status, and output.
- Panes created before a failure are left in place, since closing them
  could discard work.
- A missing tool is reported before anything runs.
- Version probes that fail fall back to assuming a current release, with a
  printed note.

## Acceptance Criteria

- Unit tests cover:
  - detection: ancestry tie-breaks, leaked variables, remote refusal, mux
    servers;
  - every backend plan and its version gates;
  - executor failure handling and the project-directory rules.
- On Windows, running from an unsplit Windows Terminal tab in a checkout
  produces the layout with focus on the invoking pane.
- The operator verifies tmux, screen, WezTerm, kitty, and Terminator live
  on Linux, including tmux and screen over SSH and mosh.
