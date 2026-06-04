# `src/ubersight/operator_location_memory_store.py`

## Purpose

`src/ubersight/operator_location_memory_store.py` contains the local-only
storage, matching, hashing, and serialization logic for operator location
memory. It is shared by the `ubersight` and `ubersight-location-memory`
commands so Ubersight does not own the memory schema or persistence
mechanics.

The module is not intended to be executed directly.

## Responsibilities

- Load and write `.local/operator-location/known-locations.json`.
- Append observations to `.local/operator-location/observations.jsonl`.
- Create and read `.local/operator-location/match-salt`.
- Hash BSSID and gateway MAC matchers with the local salt.
- Score current observations against known-location records.
- Learn or update a location record from an operator-approved alias.

## Safety Boundaries

This module does not call public network providers and does not inspect Git.
It expects callers to provide already collected observations. All default
paths are under `.local/`, and records should never be copied into tracked
project files.

## Related Documents

- `docs/security/operator-location-memory.md`
- `docs/scripts/operator_location_memory.py.md`
- `docs/scripts/ubersight.py.md`
- `tests/test_ubersight.py`
