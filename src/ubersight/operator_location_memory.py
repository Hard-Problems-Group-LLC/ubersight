#!/usr/bin/env python3
"""Manage checkout-local operator location memory for Ubersight.

This tool intentionally stores learned SSIDs, local network signals, and
operator-facing location aliases only under `.local/operator-location/`.
Tracked source code defines the schema and matching behavior, but not the
operator's actual places.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any, Sequence

from . import dashboard as ubersight
from . import operator_location_memory_store as location_memory


def _print_observation(observation: location_memory.LocationObservation) -> None:
    print(f"[location-memory] host: {observation.hostname}")
    print(f"[location-memory] user: {observation.username}")
    print(f"[location-memory] wifi: {observation.wifi_ssid}")
    if observation.wifi_bssid:
        print("[location-memory] bssid: captured")
    if observation.local_subnets:
        print(
            f"[location-memory] local subnets: {', '.join(observation.local_subnets)}"
        )
    if observation.gateway_macs:
        print(
            f"[location-memory] gateway MACs: {len(observation.gateway_macs)} captured"
        )
    estimate = observation.network_estimate
    if estimate is not None:
        print(f"[location-memory] coarse: {estimate.location}")
        if estimate.latitude is not None and estimate.longitude is not None:
            uncertainty = ubersight._format_uncertainty(estimate.uncertainty_meters)
            suffix = f" +/- {uncertainty}" if uncertainty else ""
            print(
                "[location-memory] latlong: "
                f"{estimate.latitude:.5f}, {estimate.longitude:.5f}{suffix}"
            )


def _observation_json(observation: location_memory.LocationObservation) -> str:
    return json.dumps(
        location_memory.observation_to_mapping(observation),
        indent=2,
        sort_keys=True,
    )


def _build_observation(args: argparse.Namespace) -> location_memory.LocationObservation:
    return ubersight.observe_operator_location(
        geolocate=not args.no_geolocate,
    )


def _known_sensitive_terms(
    memory_path: Path, network_profiles_path: Path
) -> tuple[str, ...]:
    terms: list[str] = []
    for record in location_memory.load_location_memory(memory_path):
        label = str(record.get("label", "")).strip()
        if label:
            terms.append(label)
        display = record.get("display")
        if isinstance(display, dict):
            terms.extend(
                str(display.get(key, "")).strip() for key in ("coarse", "fine")
            )
        matchers = record.get("matchers")
        if isinstance(matchers, dict):
            terms.extend(location_memory.string_list(matchers.get("ssids")))
    for record in location_memory.load_network_profiles(network_profiles_path):
        matchers = record.get("matchers")
        if isinstance(matchers, dict):
            terms.extend(location_memory.string_list(matchers.get("ssids")))
    unique: list[str] = []
    for term in terms:
        if len(term) >= 3 and term not in unique:
            unique.append(term)
    return tuple(unique)


def _git_grep(term: str) -> list[str]:
    """Return `git grep` hits for one term in the current checkout's files.

    Git prints file bytes as they are, so the output is decoded as UTF-8
    with replacement; the Windows default code page would fail on some
    UTF-8 text and lose the very match being reported.
    """

    result = subprocess.run(
        ["git", "grep", "-n", "--fixed-strings", "--", term],
        cwd=Path.cwd(),
        encoding="utf-8",
        errors="replace",
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode not in {0, 1}:
        raise RuntimeError(result.stderr.strip() or "git grep failed")
    return [line for line in result.stdout.splitlines() if line.strip()]


def _redact_check(memory_path: Path, network_profiles_path: Path) -> int:
    leaks: list[tuple[str, str]] = []
    for term in _known_sensitive_terms(memory_path, network_profiles_path):
        for line in _git_grep(term):
            leaks.append((term, line))
    if not leaks:
        print("[location-memory] PASS: no known local terms found in tracked files")
        return 0
    print("[location-memory] FAIL: known local terms found in tracked files")
    for term, line in leaks:
        print(f"[location-memory] term={term!r}: {line}")
    return 1


def _record_summary(record: dict[str, Any]) -> str:
    matchers = record.get("matchers")
    if not isinstance(matchers, dict):
        matchers = {}
    matcher_counts = {
        key: len(value) if isinstance(value, list) else 0
        for key, value in matchers.items()
    }
    return json.dumps(
        {
            "id": record.get("id"),
            "label": record.get("label"),
            "opsec_level": record.get("opsec_level"),
            "traits": (
                record.get("traits") if isinstance(record.get("traits"), dict) else {}
            ),
            "matchers": matcher_counts,
            "updated_at": record.get("updated_at"),
        },
        indent=2,
        sort_keys=True,
    )


def build_parser() -> argparse.ArgumentParser:
    """Create the command-line parser."""

    parser = argparse.ArgumentParser(
        description="Manage local-only operator location memory."
    )
    parser.add_argument(
        "--memory",
        default=str(location_memory.DEFAULT_LOCATION_MEMORY_PATH),
        help="local known-location memory path",
    )
    parser.add_argument(
        "--salt",
        default=str(location_memory.DEFAULT_LOCATION_MEMORY_SALT_PATH),
        help="local hash salt path",
    )
    parser.add_argument(
        "--network-profiles",
        default=str(location_memory.DEFAULT_NETWORK_PROFILES_PATH),
        help="local network trait profile path",
    )
    parser.add_argument(
        "--no-geolocate",
        action="store_true",
        help="do not contact network geolocation providers",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    observe = subparsers.add_parser("observe", help="print the current observation")
    observe.add_argument("--json", action="store_true", help="emit JSON")

    lookup = subparsers.add_parser("lookup", help="match current location memory")
    lookup.add_argument("--json", action="store_true", help="emit JSON")

    learn = subparsers.add_parser("learn", help="learn current signals under a label")
    learn.add_argument("--label", required=True, help="operator-safe location alias")
    learn.add_argument("--fine-location", default="", help="optional local-only detail")
    learn.add_argument(
        "--opsec-level",
        default="private-trusted",
        choices=("private-trusted", "private", "public", "unknown"),
        help="OPSEC classification to apply when this location matches",
    )
    learn.add_argument(
        "--confidence-threshold",
        type=float,
        default=None,
        help=(
            "minimum score required to match this location; use values above 80 "
            "when a common public SSID must not match by SSID alone"
        ),
    )
    learn.add_argument(
        "--no-geo-matcher",
        action="store_true",
        help="do not store the coarse geolocation as a weak matcher",
    )

    hotspot = subparsers.add_parser(
        "mark-hotspot",
        help="mark one local-only WiFi network profile as a mobile hotspot",
    )
    hotspot.add_argument("--ssid", required=True, help="literal SSID to mark")
    hotspot.add_argument(
        "--not-hotspot",
        action="store_true",
        help="clear the hotspot trait instead of setting it",
    )

    subparsers.add_parser(
        "redact-check",
        help="fail if known local labels or SSIDs appear in tracked files",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the local-memory management command."""

    ubersight.harden_windows_executable_search()
    parser = build_parser()
    args = parser.parse_args(argv)
    memory_path = Path(args.memory)
    salt_path = Path(args.salt)
    network_profiles_path = Path(args.network_profiles)

    if args.command == "redact-check":
        return _redact_check(memory_path, network_profiles_path)

    if args.command == "mark-hotspot":
        record = location_memory.mark_wifi_network_hotspot(
            args.ssid,
            is_hotspot=not args.not_hotspot,
            profile_path=network_profiles_path,
        )
        state = "hotspot" if not args.not_hotspot else "not hotspot"
        print(f"[location-memory] marked local WiFi network as {state}")
        print(f"[location-memory] network profiles: {network_profiles_path}")
        print(_record_summary(record))
        return 0

    observation = _build_observation(args)
    if args.command == "observe":
        if args.json:
            print(_observation_json(observation))
        else:
            _print_observation(observation)
        return 0

    if args.command == "lookup":
        match = location_memory.lookup_location_memory(
            observation,
            memory_path=memory_path,
            salt_path=salt_path,
        )
        if args.json:
            payload = {
                "matched": match is not None and not match.ambiguous,
                "ambiguous": bool(match.ambiguous) if match is not None else False,
                "score": match.score if match is not None else 0,
                "threshold": match.threshold if match is not None else 0,
                "reasons": list(match.reasons) if match is not None else [],
                "label": match.record.get("label") if match is not None else "",
            }
            print(json.dumps(payload, indent=2, sort_keys=True))
        elif match is None:
            print("[location-memory] no match")
        elif match.ambiguous:
            print(f"[location-memory] ambiguous match: {match.record.get('label')}")
        else:
            print(
                "[location-memory] matched "
                f"{match.record.get('label')} "
                f"({match.score:.0f}/{match.threshold:.0f}; "
                f"{', '.join(match.reasons)})"
            )
        return 0

    if args.command == "learn":
        record = location_memory.learn_location_memory(
            args.label,
            fine_location=args.fine_location,
            opsec_level=args.opsec_level,
            confidence_threshold=args.confidence_threshold,
            observation=observation,
            memory_path=memory_path,
            salt_path=salt_path,
            include_geo=not args.no_geo_matcher,
        )
        print(f"[location-memory] learned: {args.label}")
        print(f"[location-memory] memory: {memory_path}")
        print(_record_summary(record))
        return 0

    parser.error(f"unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
