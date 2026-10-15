"""Local-only operator location memory persistence and matching.

Ubersight uses this module to recognize operator-approved location
aliases from checkout-local signals. The module deliberately knows nothing
about public geolocation providers or terminal rendering; it only loads,
scores, writes, and audits local memory records under `.local/`.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import math
import secrets
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from .atomic_files import atomic_write_text

DEFAULT_LOCATION_MEMORY_PATH = Path(".local/operator-location/known-locations.json")
DEFAULT_LOCATION_OBSERVATIONS_PATH = Path(".local/operator-location/observations.jsonl")
DEFAULT_LOCATION_MEMORY_SALT_PATH = Path(".local/operator-location/match-salt")
DEFAULT_NETWORK_PROFILES_PATH = Path(".local/operator-location/network-profiles.json")
LOCATION_MEMORY_SCHEMA = "ubersight.operator-location-memory.v1"
NETWORK_PROFILES_SCHEMA = "ubersight.operator-network-profiles.v1"


@dataclass(frozen=True)
class LocationObservation:
    """Local-only signals that can identify the operator's current location."""

    hostname: str
    username: str
    wifi_ssid: str
    wifi_bssid: str = ""
    local_subnets: tuple[str, ...] = ()
    gateway_macs: tuple[str, ...] = ()
    network_estimate: Any | None = None
    observed_at: datetime | None = None
    # True only when the WiFi probe affirmatively found no WLAN connection
    # while a default gateway was reachable: the operator is on a cable.
    wired: bool = False


@dataclass(frozen=True)
class LocationMemoryMatch:
    """Best local-memory match for a current location observation."""

    record: dict[str, Any]
    score: float
    threshold: float
    reasons: tuple[str, ...] = ()
    ambiguous: bool = False


@dataclass(frozen=True)
class NetworkProfileMatch:
    """Best local-only network profile match for the current observation."""

    record: dict[str, Any]
    score: float
    threshold: float
    reasons: tuple[str, ...] = ()


def local_now() -> datetime:
    """Return a timezone-aware local timestamp."""

    return datetime.now().astimezone()


def location_memory_newer_than(path: Path, timestamp: datetime | None) -> bool:
    """Return whether the local memory was updated after a cached status."""

    if timestamp is None:
        return path.exists()
    try:
        modified = datetime.fromtimestamp(path.stat().st_mtime).astimezone()
    except OSError:
        return False
    return modified > timestamp.astimezone(modified.tzinfo)


def location_inputs_newer_than(
    paths: Sequence[Path], timestamp: datetime | None
) -> bool:
    """Return whether any local operator-location input changed after a cache."""

    return any(location_memory_newer_than(path, timestamp) for path in paths)


def load_location_memory(path: Path) -> list[dict[str, Any]]:
    """Load local-only known-location records from `.local/`."""

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(raw, dict) or raw.get("schema") != LOCATION_MEMORY_SCHEMA:
        return []
    records = raw.get("locations")
    if not isinstance(records, list):
        return []
    return [record for record in records if isinstance(record, dict)]


def load_network_profiles(path: Path) -> list[dict[str, Any]]:
    """Load local-only network trait profiles from `.local/`."""

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(raw, dict) or raw.get("schema") != NETWORK_PROFILES_SCHEMA:
        return []
    profiles = raw.get("networks")
    if not isinstance(profiles, list):
        return []
    return [profile for profile in profiles if isinstance(profile, dict)]


def write_location_memory(path: Path, records: Sequence[dict[str, Any]]) -> None:
    """Atomically write local-only known-location records."""

    payload = {
        "schema": LOCATION_MEMORY_SCHEMA,
        "updated_at": local_now().isoformat(timespec="seconds"),
        "locations": list(records),
    }
    atomic_write_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def write_network_profiles(path: Path, records: Sequence[dict[str, Any]]) -> None:
    """Atomically write local-only network trait profiles."""

    payload = {
        "schema": NETWORK_PROFILES_SCHEMA,
        "updated_at": local_now().isoformat(timespec="seconds"),
        "networks": list(records),
    }
    atomic_write_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def load_location_memory_salt(path: Path, *, create: bool = False) -> str:
    """Load or create the local salt used for nonliteral network identifiers."""

    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError:
        value = ""
    if value or not create:
        return value
    value = secrets.token_hex(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return value


def hash_location_signal(value: str, salt: str) -> str:
    """Hash a local network identifier with a checkout-local salt."""

    normalized = " ".join(value.strip().lower().split())
    if not normalized or not salt:
        return ""
    digest = hashlib.sha256()
    digest.update(salt.encode("utf-8"))
    digest.update(b"\0")
    digest.update(normalized.encode("utf-8"))
    return digest.hexdigest()


def network_estimate_to_mapping(estimate: Any | None) -> dict[str, Any]:
    """Serialize a network estimate object without depending on its class."""

    if estimate is None:
        return {}
    return {
        "location": getattr(estimate, "location", ""),
        "source": getattr(estimate, "source", ""),
        "latitude": getattr(estimate, "latitude", None),
        "longitude": getattr(estimate, "longitude", None),
        "uncertainty_meters": getattr(estimate, "uncertainty_meters", None),
    }


def observation_to_mapping(observation: LocationObservation) -> dict[str, Any]:
    """Serialize one local-only location observation."""

    return {
        "observed_at": (observation.observed_at or local_now()).isoformat(
            timespec="seconds"
        ),
        "hostname": observation.hostname,
        "username": observation.username,
        "wifi_ssid": observation.wifi_ssid,
        "wifi_bssid": observation.wifi_bssid,
        "local_subnets": list(observation.local_subnets),
        "gateway_macs": list(observation.gateway_macs),
        "wired": observation.wired,
        "network_estimate": network_estimate_to_mapping(observation.network_estimate),
    }


def append_location_observation(path: Path, observation: LocationObservation) -> None:
    """Append one local-only observation for later operator labeling."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(observation_to_mapping(observation)) + "\n")


def lookup_location_memory(
    observation: LocationObservation,
    memory_path: Path = DEFAULT_LOCATION_MEMORY_PATH,
    salt_path: Path = DEFAULT_LOCATION_MEMORY_SALT_PATH,
) -> LocationMemoryMatch | None:
    """Return the best nonambiguous local-memory match for an observation."""

    records = load_location_memory(memory_path)
    if not records:
        return None
    salt = load_location_memory_salt(salt_path)
    matches = [
        match
        for match in (
            score_location_memory_record(record, observation, salt)
            for record in records
        )
        if match.score >= match.threshold
    ]
    if not matches:
        return None
    matches.sort(key=lambda item: item.score, reverse=True)
    best = matches[0]
    if len(matches) > 1 and matches[1].score >= best.score - 20:
        return LocationMemoryMatch(
            best.record,
            best.score,
            best.threshold,
            best.reasons,
            ambiguous=True,
        )
    return best


def lookup_network_profile(
    observation: LocationObservation,
    profile_path: Path = DEFAULT_NETWORK_PROFILES_PATH,
    salt_path: Path = DEFAULT_LOCATION_MEMORY_SALT_PATH,
) -> NetworkProfileMatch | None:
    """Return the best matching local network profile for an observation."""

    records = load_network_profiles(profile_path)
    if not records:
        return None
    salt = load_location_memory_salt(salt_path)
    matches = [
        match
        for match in (
            score_network_profile_record(record, observation, salt)
            for record in records
        )
        if match.score >= match.threshold
    ]
    if not matches:
        return None
    matches.sort(key=lambda item: item.score, reverse=True)
    return matches[0]


def score_location_memory_record(
    record: dict[str, Any],
    observation: LocationObservation,
    salt: str,
) -> LocationMemoryMatch:
    """Score one known-location record against an observation."""

    matchers = record.get("matchers")
    if not isinstance(matchers, dict):
        matchers = {}
    score = 0.0
    reasons: list[str] = []
    ssids = {
        str(value).strip().casefold()
        for value in matchers.get("ssids", [])
        if str(value).strip()
    }
    if observation.wifi_ssid.casefold() in ssids:
        score += 80
        reasons.append("ssid")
    bssid_hashes = set(string_list(matchers.get("bssids_sha256")))
    bssid_hash = hash_location_signal(observation.wifi_bssid, salt)
    if bssid_hash and bssid_hash in bssid_hashes:
        score += 120
        reasons.append("bssid")
    gateway_hashes = set(string_list(matchers.get("gateway_macs_sha256")))
    gateway_matches = [
        mac
        for mac in observation.gateway_macs
        if hash_location_signal(mac, salt) in gateway_hashes
    ]
    if gateway_matches:
        score += 100
        reasons.append("gateway")
    matcher_subnets = ip_networks(matchers.get("local_subnets"))
    observed_subnets = ip_networks(observation.local_subnets)
    if any(a.overlaps(b) for a in matcher_subnets for b in observed_subnets):
        score += 30
        reasons.append("subnet")
    if observation_in_geo_matcher(observation, matchers.get("geo")):
        score += 30
        reasons.append("geo")
    threshold = float_or_none(record.get("confidence_threshold")) or 80.0
    return LocationMemoryMatch(record, score, threshold, tuple(reasons))


def score_network_profile_record(
    record: dict[str, Any],
    observation: LocationObservation,
    salt: str,
) -> NetworkProfileMatch:
    """Score one local network profile against an observation."""

    matchers = record.get("matchers")
    if not isinstance(matchers, dict):
        matchers = {}
    score = 0.0
    reasons: list[str] = []
    ssids = {
        str(value).strip().casefold()
        for value in matchers.get("ssids", [])
        if str(value).strip()
    }
    if observation.wifi_ssid.casefold() in ssids:
        score += 80
        reasons.append("ssid")
    bssid_hashes = set(string_list(matchers.get("bssids_sha256")))
    bssid_hash = hash_location_signal(observation.wifi_bssid, salt)
    if bssid_hash and bssid_hash in bssid_hashes:
        score += 120
        reasons.append("bssid")
    gateway_hashes = set(string_list(matchers.get("gateway_macs_sha256")))
    gateway_matches = [
        mac
        for mac in observation.gateway_macs
        if hash_location_signal(mac, salt) in gateway_hashes
    ]
    if gateway_matches:
        score += 100
        reasons.append("gateway")
    threshold = float_or_none(record.get("confidence_threshold")) or 80.0
    return NetworkProfileMatch(record, score, threshold, tuple(reasons))


def network_profile_has_trait(
    match: NetworkProfileMatch | None,
    trait: str,
) -> bool:
    """Return whether a matched network profile carries the requested trait."""

    if match is None:
        return False
    traits = match.record.get("traits")
    if not isinstance(traits, dict):
        return False
    return bool(traits.get(trait))


def learn_location_memory(
    label: str,
    *,
    fine_location: str = "",
    opsec_level: str = "private-trusted",
    confidence_threshold: float | None = None,
    observation: LocationObservation,
    memory_path: Path = DEFAULT_LOCATION_MEMORY_PATH,
    salt_path: Path = DEFAULT_LOCATION_MEMORY_SALT_PATH,
    include_geo: bool = True,
    lan_name: str | None = None,
    record_id: str | None = None,
) -> dict[str, Any]:
    """Create or update a local-only known-location record.

    With `record_id`, that record is updated even when its label changes,
    so renaming a place in the editor never duplicates it. Without one,
    the record is found by label, as the command-line helper expects.
    `lan_name` names a wired LAN, shown as `Ethernet (<name>)`; None leaves
    it unchanged and an empty string clears it.
    """

    label = label.strip()
    if not label:
        raise ValueError("location label must not be empty")
    salt = load_location_memory_salt(salt_path, create=True)
    records = load_location_memory(memory_path)
    now = local_now().isoformat(timespec="seconds")
    if record_id:
        record = next(
            (item for item in records if str(item.get("id", "")) == record_id),
            None,
        )
    else:
        record = next(
            (
                item
                for item in records
                if str(item.get("label", "")).casefold() == label.casefold()
            ),
            None,
        )
    if record is None:
        record = {
            "id": secrets.token_hex(16),
            "created_at": now,
            "confidence_threshold": 80,
        }
        records.append(record)
    record["label"] = label
    record["updated_at"] = now
    record["opsec_level"] = opsec_level.strip() or "private-trusted"
    if lan_name is not None:
        record["lan_name"] = lan_name.strip()
    if confidence_threshold is not None:
        # NaN and infinity pass `<= 0` but would stop the record matching.
        if not math.isfinite(confidence_threshold) or confidence_threshold <= 0:
            raise ValueError("confidence threshold must be a positive number")
        record["confidence_threshold"] = confidence_threshold
    record["display"] = {
        "coarse": label,
        "fine": fine_location.strip(),
    }
    matchers = record.get("matchers")
    if not isinstance(matchers, dict):
        matchers = {}
    matchers["ssids"] = merged_list(
        matchers.get("ssids"),
        [observation.wifi_ssid] if observation.wifi_ssid != "unknown" else [],
    )
    matchers["local_subnets"] = merged_list(
        matchers.get("local_subnets"),
        observation.local_subnets,
    )
    matchers["bssids_sha256"] = merged_list(
        matchers.get("bssids_sha256"),
        [hash_location_signal(observation.wifi_bssid, salt)],
    )
    matchers["gateway_macs_sha256"] = merged_list(
        matchers.get("gateway_macs_sha256"),
        [hash_location_signal(mac, salt) for mac in observation.gateway_macs],
    )
    if include_geo and observation.network_estimate is not None:
        estimate = observation.network_estimate
        latitude = getattr(estimate, "latitude", None)
        longitude = getattr(estimate, "longitude", None)
        if latitude is not None and longitude is not None:
            uncertainty = getattr(estimate, "uncertainty_meters", None)
            radius = max(1000.0, uncertainty or 25_000.0)
            matchers["geo"] = merged_geo_matchers(
                matchers.get("geo"),
                {
                    "latitude": latitude,
                    "longitude": longitude,
                    "radius_meters": radius,
                },
            )
    record["matchers"] = matchers
    record["last_observation"] = observation_to_mapping(observation)
    write_location_memory(memory_path, records)
    return record


def mark_wifi_network_hotspot(
    ssid: str,
    *,
    is_hotspot: bool = True,
    profile_path: Path = DEFAULT_NETWORK_PROFILES_PATH,
) -> dict[str, Any]:
    """Mark one SSID as a local-only mobile hotspot network profile."""

    ssid = ssid.strip()
    if not ssid:
        raise ValueError("wifi SSID must not be empty")
    records = load_network_profiles(profile_path)
    now = local_now().isoformat(timespec="seconds")
    record = next(
        (
            item
            for item in records
            if ssid.casefold()
            in {
                value.casefold()
                for value in string_list(
                    item.get("matchers", {}).get("ssids")
                    if isinstance(item.get("matchers"), dict)
                    else []
                )
            }
        ),
        None,
    )
    if record is None:
        record = {
            "id": secrets.token_hex(16),
            "created_at": now,
            "confidence_threshold": 80,
            "label": "local WiFi network",
        }
        records.append(record)
    record["updated_at"] = now
    traits = record.get("traits")
    if not isinstance(traits, dict):
        traits = {}
    traits["hotspot"] = bool(is_hotspot)
    record["traits"] = traits
    matchers = record.get("matchers")
    if not isinstance(matchers, dict):
        matchers = {}
    matchers["ssids"] = merged_list(matchers.get("ssids"), [ssid])
    record["matchers"] = matchers
    write_network_profiles(profile_path, records)
    return record


def string_list(value: Any) -> tuple[str, ...]:
    """Return string list values from an arbitrary JSON value."""

    if not isinstance(value, list):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


def ip_networks(
    values: Any,
) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    """Return parsed IP networks from JSON list values."""

    networks = []
    for value in string_list(values):
        try:
            networks.append(ipaddress.ip_network(value, strict=False))
        except ValueError:
            continue
    return tuple(networks)


def observation_in_geo_matcher(observation: LocationObservation, value: Any) -> bool:
    """Return whether an observation lies inside any configured geo matcher."""

    estimate = observation.network_estimate
    if estimate is None:
        return False
    latitude = getattr(estimate, "latitude", None)
    longitude = getattr(estimate, "longitude", None)
    if latitude is None or longitude is None or not isinstance(value, list):
        return False
    for item in value:
        if not isinstance(item, dict):
            continue
        matcher_latitude = float_or_none(item.get("latitude"))
        matcher_longitude = float_or_none(item.get("longitude"))
        radius_meters = float_or_none(item.get("radius_meters")) or 0
        if matcher_latitude is None or matcher_longitude is None or radius_meters <= 0:
            continue
        if (
            distance_meters(
                latitude,
                longitude,
                matcher_latitude,
                matcher_longitude,
            )
            <= radius_meters
        ):
            return True
    return False


def float_or_none(value: Any) -> float | None:
    """Return a float for JSON-ish values or `None` when parsing fails."""

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def distance_meters(lat_a: float, lon_a: float, lat_b: float, lon_b: float) -> float:
    """Return an approximate great-circle distance in meters."""

    from math import asin, cos, radians, sin, sqrt

    radius_meters = 6_371_008.8
    phi_a = radians(lat_a)
    phi_b = radians(lat_b)
    delta_phi = radians(lat_b - lat_a)
    delta_lambda = radians(lon_b - lon_a)
    haversine = (
        sin(delta_phi / 2) ** 2 + cos(phi_a) * cos(phi_b) * sin(delta_lambda / 2) ** 2
    )
    return 2 * radius_meters * asin(sqrt(haversine))


def merged_list(existing: Any, additions: Sequence[str]) -> list[str]:
    """Merge list values while preserving first-seen order."""

    merged = list(string_list(existing))
    for addition in additions:
        addition = str(addition).strip()
        if addition and addition not in merged:
            merged.append(addition)
    return merged


def merged_geo_matchers(
    existing: Any, addition: dict[str, float]
) -> list[dict[str, float]]:
    """Merge a new coarse geo matcher into an existing matcher list."""

    merged = (
        [item for item in existing if isinstance(item, dict)]
        if isinstance(existing, list)
        else []
    )
    for item in merged:
        latitude = float_or_none(item.get("latitude"))
        longitude = float_or_none(item.get("longitude"))
        if latitude is None or longitude is None:
            continue
        if distance_meters(
            latitude,
            longitude,
            addition["latitude"],
            addition["longitude"],
        ) <= max(float(item.get("radius_meters", 0)), addition["radius_meters"]):
            item["radius_meters"] = max(
                float(item.get("radius_meters", 0)),
                addition["radius_meters"],
            )
            return merged
    merged.append(addition)
    return merged
