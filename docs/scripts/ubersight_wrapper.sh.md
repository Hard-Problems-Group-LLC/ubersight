# `scripts/ubersight_wrapper.sh`

## Purpose

`scripts/ubersight_wrapper.sh` keeps Ubersight running in a terminal side
pane while the package is being edited or restarted.
It is intended for local development use in a Terminator split.

## Normal Usage

```bash
scripts/ubersight_wrapper.sh
```

All arguments are passed through to the source-checkout compatibility
launcher `scripts/ubersight.py`:

```bash
scripts/ubersight_wrapper.sh --process-root "$PID" --refresh-ms 500
```

If Ubersight exits, the wrapper waits one second and starts it again. Press
Ctrl-C in the wrapper terminal to stop the loop.

`ubersight --supervise` replaces this wrapper on every platform, including
Windows. Unlike the wrapper, the supervisor exits when you press Q in the
dashboard. It restarts at once after an `ubersight-stop` request, and after
one second following a crash.

The wrapper prefers the repository `.venv/bin/python` interpreter when it is
available, then falls back to `python3` from `PATH`. This keeps Ubersight
aligned with the managed development runtime even when the shell's default
`python3` is older.

## Side Effects and Safety Boundaries

The wrapper does not mutate repository files. It repeatedly launches
Ubersight with the supplied arguments and prints restart notices to standard
error.
