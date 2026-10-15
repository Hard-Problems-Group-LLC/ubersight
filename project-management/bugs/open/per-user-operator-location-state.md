# Operator Location State Is Stored Per Project Instead of Per User

Status: Open  
Date Opened: 2026-10-14T01:23:00-07:00  
Owner: Claude and operator  
Related: `docs/specifications/windows-platform-support.md` (Phase 11000 Slice 4000);
`docs/security/operator-location-memory.md`

## Symptom

Ubersight answers "where am I?" for the operator and the machine, but it
keeps all of that state in each project checkout's `.local/` tree:

- `.local/operator-location/known-locations.json` (location memory);
- `.local/operator-location/observations.jsonl`;
- `.local/operator-location/match-salt`;
- `.local/operator-location/network-profiles.json`;
- `.local/ubersight/location-cache.json`;
- `.local/ubersight/tailscale-cache.json`.

As a result:

- A location taught with `E` in one project is unknown in every other
  project. That includes wired LAN names, which the footer shows as
  `Ethernet (<label>)`. Per the operator, these belong with the eventual
  shared user-and-machine settings, and stay under `.local/` until then.
- Each project keeps its own salt, so the same network hashes differently
  per project and memories cannot be shared or compared.
- Observation logs, geolocation lookups, and Tailscale probes are repeated
  and duplicated for every project that runs a dashboard.

## Root Cause

The design treated `.local/` as the only local-state root. That suits
project-scoped status, such as phases, slices, notes, and background jobs.
Operator and machine identity, however, is per-user and cross-project, and
the code has no per-user state location.

## Expected Behavior

Per-user operator state lives in one per-user location that every project's
dashboard shares:

- **Windows:** `%LOCALAPPDATA%\ubersight\`, the operator's convention for
  cross-project material.
- **POSIX:** an XDG location, for example `$XDG_STATE_HOME/ubersight/`
  (default `~/.local/state/ubersight/`) for caches and observations, and
  `$XDG_DATA_HOME` for durable memory.

Project-scoped status stays in the project's `.local/`.

## Considerations for the Fix

- **Sandboxed agent sessions:** these redirect `HOME`, `USERPROFILE`,
  `APPDATA`, and `LOCALAPPDATA` into the checkout. A dashboard started
  there must not silently create a second, divergent store; consider the
  same inside-the-checkout guard the installer uses.
- **Migration:** merge existing per-project memories and network profiles
  into the per-user store without losing labels. Salts differ per
  project, so hashed signals need re-learning or a deliberate re-salt.
- **Privacy:** the per-user store holds sensitive location data, so it
  needs owner-only permissions on POSIX and should stay under the user
  profile on Windows.
- **Options:** keep `--location-memory` and the related path options as
  explicit overrides.

## Workaround

Teach locations in the project you use most often. Other projects show
geolocation-based estimates until they learn the location separately.
