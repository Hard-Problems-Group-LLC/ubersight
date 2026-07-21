# ubersight

Ubersight is a compact terminal dashboard for long-running development
sessions. It watches generic project status files, process trees, local
worktree state, optional operator-location context, and background-job
telemetry without knowing anything about the application that produced the
signals.

Projects use Ubersight by writing `ubersight.status.v1` and
`ubersight.background-job-status.v1` JSON files under the selected runtime
context. Ubersight itself remains project-neutral.

## Install

For normal development from this checkout, use the managed Python 3.14.6
runtime:

```bash
./install.sh --mode dev
```

That creates the managed `.venv`, installs Ubersight editable with its pinned
developer tools, installs managed Git hooks when available, and publishes the
console commands:

- `ubersight`
- `ubersight-location-memory`
- `ubersight-probe-location`
- `ubersight-stop`

For a user-local standard install, run:

```bash
./install.sh
```

User-scoped paths follow the selected user home. If `HOME` points at this
checkout's isolated `.codex-home`, the installer stops instead of placing pyenv,
launchers, or the standard venv there. Run it from a normal terminal, or pass
`--user-home PATH` to name the intended home explicitly.

The legacy `scripts/*.py` entry points are compatibility launchers. The
application package lives under `src/ubersight/`.

## Native terminal support

The interactive dashboard requires Python's native `curses` extension. The
managed development runtime is compiled through pyenv, so a fresh host needs
the matching ncurses development package before that runtime is built. Run the
project prerequisite installer from a normal terminal before the first pyenv
build:

```bash
./scripts/install_prerequisites.sh
```

It selects `ncurses-devel` on Fedora/Rocky, `libncursesw5-dev` on
Debian/Ubuntu, and `ncurses` with Homebrew, along with the other pyenv build
dependencies. Use `./scripts/install_prerequisites.sh --dry-run` to inspect
the command first.

If an existing pyenv runtime was built without `_curses`, run the prerequisite
installer and rerun `./install.sh --mode dev`. The installer verifies
`import curses`, checks the native header, and automatically force-rebuilds the
configured pyenv runtime when the header is available. It prints a copyable
pyenv command only if that automatic rebuild fails.
