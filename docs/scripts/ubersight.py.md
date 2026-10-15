# `ubersight` / `scripts/ubersight.py`

## Purpose

`ubersight` is a terminal side-window dashboard for long Codex
phase/slice work. It is designed to run in a compact Terminator split, with
40x40 as the default target and 40x40 to 60x48 as the expected comfortable
range, while Codex works in the adjacent terminal.

Ubersight reads a checkout-local status file and renders:

- a single work-safety status as the top colored row;
- `PROJECT: <name>` near the top of the display;
- a phase stack showing the last two completed phases, the current active
  phase, and the next five known phases when a phase stack is available;
- ordered slice list with the active slice marked;
- high-level notes from the agent;
- stale/blocked/complete status; and
- optional best-effort worker process summaries for a supplied process root;
  and
- a three-row data area that aggregates live download, runtime fill,
  indexing, ingestion, processing, and other background jobs from Ubersight
  status files; and
- an OPSEC notes/warnings block above colored footer blocks with hostname,
  running user, safe WiFi label, Tailscale state, coarse locality, and
  best-effort fine location. OPSEC, Tailscale, host/user/WiFi, and each
  location block are allowed to wrap up to three rows when the terminal has
  enough vertical space.

It is not a GUI program and does not launch an X window.

## Normal Usage

Start Ubersight:

```bash
ubersight
```

The compatibility launcher `scripts/ubersight.py` runs the same package entry
point from a source checkout.

In live mode, Ubersight exposes a local hotkey bar:

- `R`: force-refresh Git, local network, geolocation, Tailscale, process, and
  background-job status on the next draw.
- `E`: open the local-only location-memory editor for the current network.
  - **On WiFi**, it shows the network and access point, and you name the
    Place.
  - **On Ethernet**, it shows the router's MAC address and subnet, and you
    name both the LAN, shown as `Ethernet (<name>)`, and the Place.
  - **Fields:** Detail adds a finer location. OPSEC cycles with Left/Right
    or Space. Threshold sets the match confidence.
  - **Keys:** Up/Down or Tab moves between fields, Enter saves, and Esc
    cancels. Every printable character can be typed, including `q`.

  Saving updates the record that matched the current network by access
  point or router, so renaming a place never duplicates it. A match on the
  WiFi name alone, which several sites may share, saves by Place name
  instead, so naming a second office never renames the first.
- `Q`: quit Ubersight.

Print a one-shot text snapshot, useful for tests or log capture:

```bash
ubersight --once
```

Monitor a known Codex or helper process subtree:

```bash
ubersight --process-root "$PID"
```

Write a status snapshot for Ubersight:

```bash
ubersight --write-status \
  --phase-id 6 \
  --phase-title "Product display" \
  --phase-row "4:done:Compositor profiling" \
  --phase-row "5:done:GIBS preload" \
  --phase-row "6:active:Product display" \
  --phase-row "7:pending:Cache fill" \
  --phase-row "8:pending:DTED rendering" \
  --slice "1:done:Inspect UI seams" \
  --slice "2:active:Wire controls" \
  --slice "3:pending:Validate" \
  --notes "Implementing the runtime cache/pinned-store handshake."
```

By default, the status file is:

```text
.local/ubersight/status.json
```

The `.local/` tree is checkout-local operator state and must not be committed.

Additional local caches live beside the status file:

```text
.local/ubersight/origin-cache.json
.local/ubersight/location-cache.json
.local/ubersight/tailscale-cache.json
```

Known operator-location memory lives in a separate ignored local tree:

```text
.local/operator-location/known-locations.json
.local/operator-location/observations.jsonl
.local/operator-location/match-salt
```

On POSIX hosts, runtime background-job status lives outside the checkout by
default:

```text
$XDG_RUNTIME_DIR/ubersight/<project-name>/background-jobs
```

On typical Linux desktops this is equivalent to:

```text
/run/user/$UID/ubersight/<project-name>/background-jobs
```

If no per-user runtime directory is available, scripts fall back to:

```text
/tmp/ubersight-$UID/<project-name>/background-jobs
```

On native Windows, project-scoped runtime state stays under the project's
ignored `.local/` tree. The default background-job directory is relative to
the working directory, like the other defaults:

```text
.local/ubersight/background-jobs
```

The default project name is the final path component of the current working
directory. On POSIX, `--project-name NAME` selects a different Ubersight
runtime context. On Windows the project-local directory already identifies
the project, so `--project-name` changes only the banner. Obsolete
pre-Ubersight status files are not part of the current protocol.

## Important Options

- `--status PATH`: read or write a different status JSON file.
- `--events PATH`: reserved for future JSON Lines event history.
- `--codex-pid PID`: alias for monitoring a Codex process root.
- `--process-root PID`: monitor a process tree rooted at `PID`.
- `--refresh-ms N`: live refresh cadence; default is 1000 ms.
- `--repo-root PATH`: repository root for origin checks; default is `.`.
- `--project-name NAME`: project banner label and Ubersight runtime context;
  default is the final path component of the current working directory.
- `--origin-cache PATH`: local origin status cache path.
- `--origin-refresh-seconds N`: maximum origin status age; default is 3600.
- `--origin-fetch-timeout-seconds N`: maximum time to wait for `origin`
  fetch; default is 8 seconds.
- `--no-origin-fetch`: compare local refs without fetching `origin`.
- `--location-cache PATH`: local operator location cache path.
- `--location-memory PATH`: local known-location memory path.
- `--location-network-profiles PATH`: local WiFi network trait profile path.
- `--location-observations PATH`: local location-observation JSONL path.
- `--location-refresh-seconds N`: maximum geolocation age; default is 900.
- `--network-refresh-seconds N`: maximum local network-identity age; default
  is 30 seconds. This is a cheap local probe and does not imply a public
  geolocation lookup.
- `--no-geolocate`: show host, user, and WiFi without network geolocation.
- `--tailscale-cache PATH`: local Tailscale status cache path.
- `--tailscale-refresh-seconds N`: maximum Tailscale probe age; default is
  15 seconds.
- `--no-tailscale`: hide Tailscale status and skip the local Tailscale CLI
  probe.
- `--background-job-dir PATH`: read active background-job files from a
  different Ubersight background-job directory.
- `--ascii`: reserved ASCII-only rendering flag.
- `--no-color`: disable curses colors.
- `--once`: print a single snapshot and exit.
- `--width N --height N`: geometry for `--once` snapshots. Defaults to 40x40.
- `--stale-seconds N`: mark status stale after this age.
- `--write-status`: write status JSON and exit.
- `--supervise`: keep the dashboard running. It restarts after
  `ubersight-stop` requests and after crashes; Q or Ctrl+C ends
  supervision.

Phase rows passed to `--phase-row` use:

```text
ID:STATE:TITLE
```

`ID` should normally be a compact phase number. `TITLE` should be only the
short display name. Do not include `Phase`, the state, a sentence, or a long
description in the title. Ubersight renders phase rows as one line:

```text
 Phase <n>: <short name>
>Phase <n>: <short name>
```

Slice rows passed to `--slice` use:

```text
ID:STATE:TITLE
```

`ID` should normally be a compact slice number within the active phase.
`TITLE` can be more descriptive than a phase title, but it still occupies one
display row only. Ubersight clips long slice text rather than wrapping it.
It renders slice rows as:

```text
 Slice <n>: <description to end of line>
>Slice <n>: <description to end of line>
```

Supported states are `pending`, `active`, `done`, `failed`, `blocked`, and
`skipped`.

When `phases` is present in the status file, Ubersight renders the phase
stack before the slice stack. It shows the two rows before the active phase,
the active phase, and up to five later rows. Agents should normally supply
the last two completed phases, the current phase, and the next five known
phases. Ubersight then renders a blank separator line before the active
phase's slice stack. If no phase stack is present, Ubersight keeps the
older single-current-phase display for compatibility with existing status
files.

New `--write-status` calls are stricter than the display loader. They must
include at least one `--phase-row`, exactly one active phase row, at least one
`--slice`, and no more than one active slice. The writer derives `phase.id`
and `phase.title` from the active phase row when `--phase-id` and
`--phase-title` are omitted. If they are provided, they must match the active
phase row. This prevents a quick legacy shorthand write from overwriting the
Ubersight's durable phase stack.

The row marker and color carry status. The text itself should not include
`done`, `run`, `pending`, `blocked`, or similar state words unless those words
are truly part of the work item's name.

## Status Format

The status JSON schema is deliberately small:

```json
{
  "schema": "ubersight.status.v1",
  "updated_at": "2026-05-24T20:06:42-07:00",
  "phase": {"id": "6", "title": "Product display"},
  "phases": [
    {"id": "4", "title": "Compositor profiling", "state": "done"},
    {"id": "5", "title": "GIBS preload", "state": "done"},
    {"id": "6", "title": "Product display", "state": "active"},
    {"id": "7", "title": "Cache fill", "state": "pending"}
  ],
  "slices": [
    {"id": "1", "title": "Inspect UI seams", "state": "done"},
    {"id": "2", "title": "Wire controls", "state": "active"}
  ],
  "notes": "Short high-level status text.",
  "blocked": false,
  "complete": false
}
```

The script writes this file atomically by replacing a temporary file in the
same directory. Agents should keep notes high-level and should not include
prompt text, transcripts, credentials, client names, or other sensitive
material.

For long autonomous work, also preserve the active phase and slice plan in:

```text
project-management/state/phase-slice-stack.md
```

That tracked file is the recovery anchor if the conversation context is
compacted. Update it at phase boundaries, slice transitions, and before long
validation or ACP waits. It should mirror Ubersight's phase/slice stack at
a high level, but it must not contain prompts, transcripts, credentials,
operator-location details, WiFi names, Tailnet names, or other sensitive
runtime state. When changing this Ubersight convention, update both
`AGENTS.md` and this document so standing orders and operator documentation
remain aligned.

## Runtime Background-Job Status

Ubersight can display download, runtime fill, indexing,
database ingestion, and other background activity from a file-per-job
protocol. Writers create one JSON file per active job and replace it
atomically on each update. Readers snapshot all visible files, discard
malformed input, and aggregate the live jobs for display.

The per-job schema is:

```json
{
  "schema": "ubersight.background-job-status.v1",
  "kind": "background-job",
  "updated_at": "2026-05-26T19:00:00-07:00",
  "pid": 12345,
  "boot_id": "example-boot-id",
  "process_start_ticks": "987654",
  "job_id": "example-worker-12345-fixture",
  "context": "example-app",
  "app": "example-worker",
  "category": "download",
  "subject": "Example background job",
  "verb": "preload",
  "source": "example-source",
  "state": "running",
  "ticker": "Example background job preload 512/5461 items",
  "work_completed": 512,
  "work_total": 5461,
  "work_unit": "tiles",
  "work_items_per_second": 2.8,
  "bytes_completed": 10485760,
  "bytes_total": 178956970,
  "bytes_per_second": 57344,
  "eta_seconds": 1767,
  "message": "downloading",
  "details": {"preset_id": "blue-marble"}
}
```

`subject` names what the job is about, `verb` names what the job is doing,
and the `work_*` fields carry unit-neutral progress. Tile downloads, database
ingests, index builds, cache fills, render jobs, and future background work
should all map their native units into these fields. Writers must not emit
obsolete pre-Ubersight schemas or old product-specific alias fields.

The PID, boot ID, and process start ticks let Ubersight reject stale files
after dirty shutdowns or PID reuse. When a file names a process that no
longer exists or no longer matches the recorded identity, Ubersight ignores
the file and may remove it. Completed background-job files are also removed
after Ubersight observes them, because the footer represents active work
rather than completed history.

The start-ticks value depends on the platform:

- **Linux:** field 22 of `/proc/<pid>/stat`, counted from boot, so writers
  also record `boot_id`.
- **Windows:** the process creation time as a decimal count of
  100-nanosecond intervals since 1601-01-01 UTC (a `FILETIME`). That value
  is unique across boots, so Windows ignores `boot_id`. A PowerShell writer
  can record it with `(Get-Process -Id $PID).StartTime.ToFileTimeUtc()`, and
  .NET code with `Process.StartTime.ToFileTimeUtc()`.

When Windows refuses to let Ubersight inspect the recorded process, it
keeps the file rather than risk deleting status for a live job.

The data area uses three footer rows:

- ticker row: a slowly scrolling verbose description of all active jobs;
- gauge row: one combined progress bar and aggregate state; and
- statistics row: compact throughput, ETA, rate, progress, job count, and
  completed byte count, dropping lower-priority fields as the pane narrows.

Status writers must not include raw URLs, credentials, API tokens, WiFi
names, operator-location details, Tailnet names, or other sensitive values in
background-job files. Product names, source IDs, non-sensitive preset IDs,
generic subject names, counts, byte totals, and rates are acceptable.

## Work-Safety and Location Status

The top row summarizes whether the checkout is safe for ongoing work and
whether local work would survive loss of the laptop. It combines branch
origin status with local worktree cleanliness so the operator does not have to
interpret multiple Git diagnostic rows. Local uncommitted or untracked work
takes precedence over a synced upstream relationship because that is work
that would be lost if the machine disappeared.

The current top-row meanings are:

- `WORK: SAFE, SYNCED`: clean worktree and local branch is even with
  upstream.
- `WORK: UNCOMMITTED LOCAL`: uncommitted, staged, unstaged, or untracked local
  work exists and is not yet protected by a pushed commit.
- `WORK: UNPUSHED COMMITS`: local commits are ahead of upstream and would be
  lost with the laptop unless pushed elsewhere.
- `WORK: PULL FIRST`: upstream has commits that local lacks.
- `WORK: CONFLICT`: local and upstream have diverged, or unmerged paths are
  present.
- `WORK: CHECK GIT`: no ordinary upstream, detached HEAD, Git failure, or
  failed origin fetch.

Narrow panes use shorter labels such as `WORK: SAFE`, `WORK: LOCAL`,
`WORK: PUSH`, `WORK: PULL`, and `WORK: CHECK`.

Origin status is refreshed when the cache is more than one hour old or when
the cheap local Git branch marker changes, which catches ordinary Git
operation blocks. By default, refresh runs `git fetch --quiet origin` before
comparing the current branch with its upstream. Git subprocesses disable
terminal prompts and use SSH batch mode with a short connect timeout, so a
hostile or captive network should degrade to `WORK: CHECK GIT` instead of
blocking the side-window display. They also run with `GIT_OPTIONAL_LOCKS=0`,
so the dashboard's frequent `git status` checks never hold `.git/index.lock`
while the operator or an agent stages or commits.

Location status separates cheap local network observation from public
geolocation. By default, local hostname, username, WiFi SSID, and related
network identity are rechecked every 30 seconds so a network change is noticed
quickly. Public geolocation remains cached with
`--location-refresh-seconds`, defaulting to 15 minutes, and is not repeated
merely because the local-network observation interval elapsed. The raw SSID is
treated as local-only signal data: it may be cached under `.local/` and used
for matching and public-network detection. The terminal footer displays the
currently observed SSID because that is local operator awareness, not tracked
project state. Do not copy literal operator WiFi names from Ubersight into
tracked docs, project-management records, source comments, commit messages,
or GitHub. Unless `--no-geolocate` is supplied, Ubersight can use a
best-effort IP geolocation lookup for coarse city/region context after a
cache miss, an expired geolocation cache, or a local network change. Before
relying on provider-derived text, Ubersight checks
`.local/operator-location/known-locations.json` for a local memory match
based on signals such as SSID, salted BSSID hash, salted gateway MAC hash,
local subnet, and coarse geolocation. A successful local match displays the
operator-safe location alias on the `LOC:` row, while the `WIFI:` row remains
the observed WiFi name.

Ubersight also checks
`.local/operator-location/network-profiles.json` for local-only traits of a
WiFi network. A network profile can mark an SSID as a hotspot even when its
literal name does not contain words such as `hotspot` or `mobile`. Hotspot
profiles are transient network identities, not fixed places, so they bypass
fixed-location memory matches and display a geolocation estimate instead.

When the current WiFi name looks like transient mobile tethering, Ubersight
does not try to turn the SSID into a venue name. Instead, if public
geolocation is enabled and available, the coarse display becomes
`Mobile hotspot near <geolocation description>`. The literal hotspot name and
the geolocation result remain local-only cache data under `.local/` and must
not be copied into tracked records, source comments, commit messages, or
GitHub.

If local memory does not identify the location, Ubersight attempts a venue
lookup by combining the active WiFi SSID with the coarse location in
OpenStreetMap Nominatim and, when latitude/longitude is available,
OpenStreetMap Overpass. The footer shows the coarse locality first, then the
finer premises/business location when available. If no premises match is
available but coordinates are, it shows latitude/longitude with an
uncertainty estimate instead of bare coordinates. This allows a neutral
fixture SSID such as `Example Venue Guest WiFi` plus `Example City, Example
Region, Example Country` to resolve to a likely venue such as `Example Venue,
Example City, Example Region, Example Country`.

WiFi detection uses four live inspection tiers before giving up: active
NetworkManager scan SSIDs, NetworkManager connection literals, wireless
tool inspection through `iwgetid`, `iw`, or `iwconfig`, and, on Windows,
`netsh wlan show interfaces`. The active scan SSID
is preferred because connection profile names may be operator-defined and can
lag the actual network after a network change. If those all fail,
Ubersight reuses the last cached SSID literal when one is available, so a
temporary desktop/session inspection failure does not immediately degrade the
internal location match to `unknown`. A wired connection is the exception,
since the cable explains the missing SSID. On a cable, the footer shows
`Ethernet (<LAN name>)` instead of `WIFI:`. Windows recognizes the cable when
`netsh` reports no WLAN connection; Linux reads `/sys/class/net`, and counts
a physical Ethernet NIC, or a bridge or VLAN on one, but not a virtual
machine's paravirtual NIC. A failed live inspection does not mark
the cached SSID as freshly observed; Ubersight will keep retrying the local
network probe instead of treating stale fallback data as proof that the
operator has not moved.

When the active SSID suggests shared public access, such as `guest`, `public`,
`free`, `open`, `hotspot`, or `visitor`, the OPSEC footer changes from the
normal OPSEC color to white on red and renders `OPSEC!: PUBLIC NETWORK`.
Known local-memory records classified as `private`, `private-trusted`, or
`trusted` suppress that public warning for the matched location.

The lookup is intended for operator awareness, not navigation or audit-quality
location evidence. When local memory does not match an unknown, non-hotspot
WiFi network, Ubersight may query BeaconDB with the active WiFi BSSID
before falling back to IP geolocation. BeaconDB-derived location lines carry
`*(BeaconDB)` at the end of the descriptive `LOC:` line. Ubersight does not
send gateway or Ethernet MAC addresses to BeaconDB; those remain local
matching signals only. Public BeaconDB, IP geolocation, Nominatim, and
Overpass calls are cached with the rest of the location status and are not
made more often than the configured location refresh interval unless the
operator presses `R`.

Tailscale status is shown between the OPSEC block and the host/location
blocks. Ubersight runs `tailscale status --json` through a short local-only
probe and extracts only the Tailnet DNS alias from MagicDNS-style names such
as `fussybox.alpha-bravo.ts.net`; this renders as
`Tailscale: alpha-bravo UP`. If Tailscale is unavailable, stopped, logged out,
or unreadable, the row shows `DOWN`, `LOGGED OUT`, or `ERROR`. When the
service is down and a prior local cache recorded the Tailnet alias, the row
may continue to identify that local alias as down.

The Tailnet alias and probe details are sensitive operator state. They are
cached only under `.local/ubersight/tailscale-cache.json`, use neutral
examples in tracked documentation and tests, and must not be copied into
project-management records, source comments, commit messages, or other
tracked prose.

## Side Effects and Safety Boundaries

Ubersight reads the status JSON file, local cache files, Git branch state,
and optionally process metadata: `/proc` on Linux, or read-only Win32
process queries on Windows. In `--write-status` mode, it writes
only the configured status path. In display mode, it may update origin,
location, and Tailscale cache files under `.local/ubersight/`, and it may
append local location observations under `.local/operator-location/`. Local
network traits are read from
`.local/operator-location/network-profiles.json`.

Default origin refresh may contact the configured `origin` remote. Default
location refresh may contact BeaconDB, a public IP geolocation endpoint,
OpenStreetMap Nominatim, or OpenStreetMap Overpass. These calls are throttled
by local caches and can be disabled with `--no-geolocate`; origin fetches can
be disabled with `--no-origin-fetch`.

Ubersight does not inspect Codex prompts or transcripts, mutate repository
source files, or require elevated permissions.

Use `ubersight-location-memory` to learn or diagnose local-memory records.
Its tracked documentation is in
`docs/scripts/operator_location_memory.py.md`.

## Restarting the Side Pane

Run the side pane under the supervisor, on any platform:

```bash
ubersight --supervise
```

`ubersight-autosplit` starts its dashboard pane this way. The supervisor
handles each exit differently:

- After a restart request, it restarts the dashboard immediately.
- After a crash, for example while code is being edited, it restarts the
  dashboard after one second.
- When you press Q or Ctrl+C, it exits.

After visual rendering changes, or after reinstalling Ubersight, agents
restart every visible dashboard by running:

```bash
ubersight-stop
```

It exists so the operator can preauthorize one stable command rather than
approving ad hoc `kill` or `pkill` process IDs. The compatibility helper
`scripts/stop_ubersight.py` runs the same command from a source checkout.

How it works:

- **Restart requests:** the helper asks every same-user dashboard to
  restart. Each dashboard leaves curses cleanly and exits with status 75,
  and its supervisor starts it again with the new code. An unsupervised
  dashboard just exits.
- **Finding dashboards on Windows:** each dashboard exposes a named
  restart event in the logon session. The helper therefore reaches the
  operator's dashboards even from a sandboxed agent session.
- **Finding dashboards on POSIX:** the helper finds dashboard processes
  in `/proc` and sends `SIGTERM`, which the dashboard treats as a restart
  request. A dashboard is `python -m ubersight` or an interpreter running
  `ubersight` or `ubersight.py`. Supervisors, `--once` and
  `--write-status` runs, and editors or linters that merely open
  `ubersight.py` are never matched.
- **Waiting and exit status:** the helper waits up to `--wait-seconds`
  (default 5). It exits with status 1 if any dashboard is still running.
  `--force` then ends those dashboards, checking each process's identity
  first so that a reused process ID is not killed; a process whose
  identity could not be read is left alone. `--dry-run` lists the matches
  without signaling them.

The older `scripts/ubersight_wrapper.sh` still works on POSIX. It restarts
after any exit, including Q.

## Provider Diagnostics

Use the location provider probe when Ubersight's premises inference needs
debugging:

```bash
ubersight-probe-location
```

Useful overrides include:

```bash
ubersight-probe-location \
  --wifi-ssid "Example Venue Guest WiFi" \
  --coarse-location "Example City, Example Region, Example Country" \
  --latitude 35.00 \
  --longitude -100.00
```

The probe prints generated Nominatim venue queries, candidate provider
results, and the Overpass fallback result without updating Ubersight cache.
Use `--provider nominatim` or `--provider overpass` to narrow a run, `--json`
for machine-readable output, and `--dry-run` to see generated queries without
contacting network providers.

## Windows

Ubersight runs natively on Windows 11 in Windows Terminal or the classic
console host. Windows support was delivered in roadmap Phase 11000. See
`docs/specifications/windows-platform-support.md` for the details.

### Terminal library

The standard Windows CPython build has no `curses`. On Windows, Ubersight
installs `windows-curses` (PDCurses) automatically as a platform-specific
dependency. That dependency is temporary until the planned Expletives
terminal-library port.

### Running it

Install it once from the checkout with `.\install.ps1`. That puts
`ubersight.exe` and the other commands in `~\.local\bin` and adds that
directory to your user PATH if needed. After that, run `ubersight` from
any project directory.

Keep the leading `.\` on relative paths. Without it, PowerShell reads
`.venv\Scripts\ubersight` as a command inside a module named `.venv`.

To open it as a narrow side pane in the current Windows Terminal window:

```powershell
wt -w 0 split-pane -V -s 0.3 -d "$PWD" ubersight
```

Run it from Windows Terminal or the console host, not mintty, so curses
gets a real console.

### Where files go

- **Project-scoped runtime state** stays under the project's ignored
  `.local/` tree.
- **Cross-project state** belongs under `%LOCALAPPDATA%\ubersight`. Use that
  root only for material that must be visible across and between projects.
  Nothing uses it today.

### Process inspection

Ubersight inspects processes through standard-library `ctypes` calls into
`kernel32`, so the `windows-curses` dependency is the only addition.
`--process-root` shows executable names rather than full command lines. On
Windows the run-state column shows `-`.

If that inspection proves fragile, or full command lines become necessary,
the approved fallback is a Windows-only `psutil` dependency behind the same
internal interface.

### Known limits

- Windows 11 can withhold WLAN details from command-line tools unless
  location services are enabled for desktop apps. The WiFi row then reads
  `unknown`, while router-based recognition keeps working.
- In a Windows virtual machine without a WiFi adapter, the virtual network
  counts as Ethernet.
- Terminal rendering still uses curses through the `windows-curses`
  package until the planned move to the Expletives library.

## Related Documents

- `project-management/proposals/approved/ubersight-side-window-dashboard.md`
- `project-management/proposals/approved/windows-platform-support.md`
- `docs/specifications/windows-platform-support.md`
- `AGENTS.md`, project-specific Ubersight side-window dashboard guidance

## Tests

Relevant tests live in:

```text
tests/test_ubersight.py
tests/test_windows_support.py
```
