# Operator Location Memory

Ubersight can keep checkout-local knowledge about the operator's working
locations so the dashboard does not have to rediscover every place from
public network geolocation. This memory exists to support OPSEC
awareness and operator convenience during development. It is not navigation
data, evidence, or a source of truth for application map features.

The design separates tracked behavior from local facts. Tracked code defines
the schema, matching rules, and commands. Literal WiFi names, operator place
names, local subnets, venue aliases, and current-position observations live
only under `.local/operator-location/`, which is ignored by Git.

## Files

The default local files are:

```text
.local/operator-location/known-locations.json
.local/operator-location/observations.jsonl
.local/operator-location/match-salt
```

`known-locations.json` stores labeled locations and matchers. `observations`
is append-only operational evidence for "what did the laptop see here?"
questions. `match-salt` is a checkout-local salt used when hashing BSSIDs and
gateway MAC addresses.

None of these files should be committed, copied into tracked documentation, or
quoted in Codex summaries except by explicit operator instruction.

The Ubersight side-window dashboard also maintains a short-lived Tailscale
status cache beside its other local status caches:

```text
.local/ubersight/tailscale-cache.json
```

That file may contain a Tailnet DNS alias and local probe diagnostics. Treat
it with the same rules as operator-location memory: it is checkout-local,
ignored, and must not be copied into tracked prose, project-management
records, source comments, commit messages, or test fixtures. Tracked
examples should use neutral placeholders such as `alpha-bravo`.

## Schema

The tracked schema name is:

```text
ubersight.operator-location-memory.v1
```

A known-location record has this shape:

```json
{
  "id": "opaque-local-id",
  "label": "Example Home Office",
  "lan_name": "Example Wired LAN",
  "opsec_level": "private-trusted",
  "confidence_threshold": 80,
  "display": {
    "coarse": "Example Home Office",
    "fine": ""
  },
  "matchers": {
    "ssids": ["Example Private WiFi"],
    "bssids_sha256": ["salted hash"],
    "gateway_macs_sha256": ["salted hash"],
    "local_subnets": ["192.0.2.0/24"],
    "geo": [
      {
        "latitude": 35.0,
        "longitude": -100.0,
        "radius_meters": 25000.0
      }
    ]
  },
  "last_observation": {},
  "created_at": "2026-05-25T00:00:00-07:00",
  "updated_at": "2026-05-25T00:00:00-07:00"
}
```

The `ssids` field is literal because the file is already local-only and a
literal SSID is useful for human maintenance. The matchers store BSSIDs and
gateway MAC addresses only as salted hashes. Each record's
`last_observation`, however, keeps the raw observation it was learned from,
including the BSSID, router MACs, SSID, and subnets, as local evidence like
`observations.jsonl`. That is one more reason never to copy or sync this
file. Local subnets and coarse geolocation are
weak matchers; they should not identify a location by themselves unless an
operator deliberately lowers a record's threshold.

`lan_name` is optional. It names a wired LAN, and the dashboard shows it as
`Ethernet (<lan_name>)` when the operator is on a cable. On Ethernet the
router's MAC address stands in for an SSID, and a gateway match alone
scores 100, enough for the default threshold of 80. The location editor
shows the raw router MAC and access-point BSSID on screen so the operator
can recognize them. The matchers it saves hold only their salted hashes;
the raw values stay in `last_observation`, as described above.

## Matching

Ubersight collects a local observation, then checks known locations before
using public IP and OpenStreetMap venue lookup results. Matching currently
uses these weights:

- SSID literal match: 80.
- BSSID salted-hash match: 120.
- Gateway MAC salted-hash match: 100.
- Overlapping local subnet: 30.
- Coarse geolocation inside a configured radius: 30.

The default confidence threshold is 80, which means an SSID match is enough,
but local subnet or geolocation alone is not. If two records are too close in
score, the match is treated as ambiguous and Ubersight falls back to its
normal provider-derived display while noting the ambiguity in the fine
location line.

Common public SSIDs should use a higher threshold, normally 100 or 120, so
the SSID alone cannot identify a premises. At those thresholds the record
must also match a stronger local signal such as the active BSSID or the
default gateway router MAC. Ubersight obtains default gateway MACs from the
kernel neighbor table for the route's gateway IP; it must not treat the
laptop's own interface MAC as the router identity.

When a matched record has `opsec_level` of `private`, `private-trusted`, or
`trusted`, Ubersight suppresses public-WiFi warnings for that record even if
the SSID text contains words such as `guest`. Unknown public SSIDs still
escalate to the red OPSEC warning.

## Operator Workflow

When Codex or the operator notices an unknown location, the safe workflow is:

```bash
ubersight-location-memory observe
ubersight-location-memory learn --label "Example Home Office"
ubersight-location-memory lookup
ubersight-location-memory redact-check
```

For a common public SSID, use a stricter threshold:

```bash
ubersight-location-memory learn \
  --label "Example Public Venue" \
  --opsec-level public \
  --confidence-threshold 100
```

The label supplied to `learn` should be an operator-safe alias. If the alias
itself is sensitive, it still belongs only in `.local/`; tracked docs and
tests must use neutral examples. `redact-check` searches tracked files for
known local labels and literal SSIDs from the local memory file.

The Codex collaboration pattern is:

- Codex observes that Ubersight cannot identify the current location.
- Codex asks the operator what safe alias should be used.
- The operator replies with the alias.
- Codex runs the learn command, validates lookup, and keeps the learned facts
  in `.local/`.

## Safety Boundaries

The local memory commands do not need elevated permissions. Observation may
call local tools such as `nmcli`, `iw`, and `ip`, and on Linux it reads
`/sys/class/net` to tell a cable from WiFi. If geolocation is enabled,
the observation path may contact the same public IP-geolocation providers
used by Ubersight. Ubersight can also use BeaconDB as an optional WiFi BSSID
fallback for unknown networks. Use `--no-geolocate` when working
in a network where external lookups are inappropriate.

Ubersight may update `.local/ubersight/location-cache.json` and append to
`.local/operator-location/observations.jsonl`. It should not mutate tracked
files, and it should not copy local location facts into project-management
records, proposals, bugs, or documentation.
