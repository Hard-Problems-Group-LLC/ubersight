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
`import curses`, checks the native header, and transactionally rebuilds the
configured pyenv runtime when the header is available. If that rebuild fails,
resolve the reported build error and rerun the original installer command; do
not force-rebuild the live prefix manually.

The installer also verifies that a configured pyenv interpreter is nonempty,
executable, runnable, and reports the configured patch release. If that
integrity probe fails, installation stops without modifying the shared runtime.
Review the reported path, then authorize recovery by adding the explicit flag
to the same command:

```bash
./install.sh --mode dev --rebuild-broken-pyenv
```

Stage one can use a healthy physical system or alternate pyenv interpreter
even when the active pyenv shims are broken. During authorized recovery, stage
two moves the old runtime aside, builds and validates a fresh prefix, restores
the old prefix if installation or validation fails, and removes the backup
only after the replacement passes its version and `curses` probes.

Installer command diagnostics use `RUN:` as a shell-neutral label. They are
informational output, not a script to replay in a shell.

## Windows

Ubersight runs natively on Windows 11. From a normal PowerShell terminal
in the checkout, run:

```powershell
.\install.ps1
```

What it does:

- builds a per-user virtual environment in `~\.local\share\ubersight\venv`;
- copies the console commands to `~\.local\bin` as `.exe` launchers;
- adds `~\.local\bin` to your user PATH when it is missing (pass
  `--no-modify-path` to skip this);
- uses the pinned Python when the `py` launcher has it, or the newest
  Python 3 otherwise.

After that, `ubersight` and the other commands work from any new terminal.
Rerun `.\install.ps1` to upgrade. Open dashboard panes do not block the
upgrade.

For development, run `.\install.ps1 --mode dev`. It builds the checkout's
`.venv` with an editable install, installs the managed Git hooks, and points
the launchers at that `.venv`. pyenv, direnv, and shell-init setup are
skipped on Windows.

The installer refuses to run from an AI agent session whose home directory
is inside the checkout. Run it from a normal terminal, or pass
`--user-home PATH`.

Two PowerShell notes:

- If the execution policy blocks local scripts, run
  `powershell -ExecutionPolicy Bypass -File .\install.ps1`.
- Write relative paths with a leading `.\`. Without it, PowerShell reads
  `.venv\Scripts\ubersight` as a command inside a module named `.venv`.

On Windows, `windows-curses` is installed automatically as a
platform-specific dependency. Background-job status lives under the
project's ignored `.local/ubersight/background-jobs` directory. See
`docs/scripts/ubersight.py.md` for the side-pane recipe, process-identity
rules for job writers, and the known Windows limits.
