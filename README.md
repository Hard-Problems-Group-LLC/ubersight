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

For normal development from this checkout:

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

The legacy `scripts/*.py` entry points are compatibility launchers. The
application package lives under `src/ubersight/`.
