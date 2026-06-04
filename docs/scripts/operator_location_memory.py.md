# `ubersight-location-memory` / `scripts/operator_location_memory.py`

## Purpose

`ubersight-location-memory` manages checkout-local operator location
memory for the Ubersight side-window dashboard. It lets Codex and the operator
learn safe aliases for recurring work locations without committing literal
WiFi names, local network details, or operator-specific place names.

The tool stores all learned facts under `.local/operator-location/`.

## Normal Usage

Inspect the current local signals:

```bash
ubersight-location-memory observe
```

Learn the current signals under a safe alias:

```bash
ubersight-location-memory learn --label "Example Home Office"
```

Look up the current location against known local memory:

```bash
ubersight-location-memory lookup
```

Check tracked files for accidental local-memory leaks:

```bash
ubersight-location-memory redact-check
```

Mark a WiFi network as a transient hotspot rather than a fixed place:

```bash
ubersight-location-memory mark-hotspot --ssid "Example Mobile Hotspot"
```

## Important Options

- `--memory PATH`: use a non-default known-location memory file.
- `--network-profiles PATH`: use a non-default network-profile file.
- `--salt PATH`: use a non-default local hash salt file.
- `--no-geolocate`: skip public network geolocation while observing.
- `observe --json`: print the raw observation as JSON.
- `lookup --json`: print match status as JSON.
- `learn --fine-location TEXT`: store an optional local-only fine label.
- `learn --opsec-level LEVEL`: classify the learned location as
  `private-trusted`, `private`, `public`, or `unknown`.
- `learn --confidence-threshold N`: override the score required to match
  the record. Use values above `80` for common public SSIDs where the SSID
  alone should not identify a premises.
- `learn --no-geo-matcher`: do not store the current coarse geolocation as a
  weak matcher.
- `mark-hotspot --ssid TEXT`: mark one local SSID as a hotspot. This stores
  the literal SSID only in `.local/operator-location/network-profiles.json`.
- `mark-hotspot --not-hotspot --ssid TEXT`: clear the hotspot trait.

## Files

By default, the tool writes:

```text
.local/operator-location/known-locations.json
.local/operator-location/network-profiles.json
.local/operator-location/observations.jsonl
.local/operator-location/match-salt
```

These files are local operator state and must remain ignored by Git.

## Side Effects

`observe` and `lookup` do not write known-location records. `learn` creates or
updates a local known-location record and may create the local hash salt.
`mark-hotspot` creates or updates a local network trait profile.
`redact-check` reads the local memory and network-profile files and searches
tracked files through `git grep`.

Observation may call local system tools such as `nmcli`, `iw`, and `ip`.
Unless `--no-geolocate` is supplied, observation may also use Ubersight's
public IP-geolocation path to capture a weak coarse-location matcher.

## Related Documents

- `docs/security/operator-location-memory.md`
- `docs/scripts/ubersight.py.md`
- `tests/test_ubersight.py`
