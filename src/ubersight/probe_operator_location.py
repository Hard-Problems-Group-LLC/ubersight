#!/usr/bin/env python3
"""Probe operator-location providers through a stable, preauthorizable tool.

This diagnostic script exercises the same coarse IP and WiFi-assisted venue
resolution path used by the ``ubersight`` dashboard command. It is
intentionally read-only: it does not update Ubersight's cached location
status.
"""

from __future__ import annotations

import argparse
import json
import time
from typing import Any, Sequence

from . import dashboard as ubersight


def _candidate_summary(candidate: Any, venue_name: str) -> dict[str, Any]:
    if not isinstance(candidate, dict):
        return {"match": False, "raw": candidate}
    name = ubersight._nominatim_name(candidate, str(candidate.get("display_name", "")))
    display_name = str(candidate.get("display_name", ""))
    return {
        "match": ubersight._nominatim_candidate_matches(name, display_name, venue_name),
        "name": name,
        "display_name": display_name,
        "lat": candidate.get("lat"),
        "lon": candidate.get("lon"),
        "category": candidate.get("category"),
        "type": candidate.get("type"),
    }


def _print_text_report(report: dict[str, Any]) -> None:
    print(f"[location-probe] wifi: {report['wifi_ssid']}")
    print(f"[location-probe] coarse: {report['coarse_location']}")
    print(f"[location-probe] coarse source: {report['coarse_source']}")
    print(f"[location-probe] venue candidate: {report['venue_name'] or 'none'}")
    for query in report["queries"]:
        print(f"[location-probe] query: {query['query']}")
        if report.get("dry_run"):
            print("[location-probe]   not executed (--dry-run)")
            continue
        if query.get("error"):
            print(f"[location-probe]   error: {query['error']}")
            continue
        results = query.get("results", [])
        print(f"[location-probe]   results: {len(results)}")
        for index, result in enumerate(results[:5], start=1):
            marker = "MATCH" if result.get("match") else "miss"
            print(
                "[location-probe]   "
                f"{index}. {marker}: {result.get('name') or result.get('display_name')}"
            )
            if result.get("lat") and result.get("lon"):
                print("[location-probe]      " f"{result['lat']}, {result['lon']}")
    overpass = report.get("overpass")
    if isinstance(overpass, dict):
        if report.get("dry_run"):
            print("[location-probe] overpass: not executed (--dry-run)")
            return
        print(f"[location-probe] overpass: {overpass.get('result') or 'no match'}")
        if overpass.get("lat") and overpass.get("lon"):
            print(
                f"[location-probe] overpass latlong: {overpass['lat']}, {overpass['lon']}"
            )


def build_parser() -> argparse.ArgumentParser:
    """Create the command-line parser."""

    parser = argparse.ArgumentParser(
        description="Probe operator location and venue-resolution providers."
    )
    parser.add_argument(
        "--wifi-ssid",
        help="SSID to use instead of probing the active WiFi connection",
    )
    parser.add_argument(
        "--coarse-location",
        help="coarse locality/city text to use instead of IP geolocation",
    )
    parser.add_argument("--latitude", type=float, help="coarse latitude hint")
    parser.add_argument("--longitude", type=float, help="coarse longitude hint")
    parser.add_argument(
        "--query",
        action="append",
        default=[],
        help="explicit Nominatim query; repeatable",
    )
    parser.add_argument(
        "--max-queries",
        type=int,
        default=4,
        help="maximum generated or explicit venue queries to execute",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=5.0,
        help="network timeout per provider request",
    )
    parser.add_argument(
        "--provider",
        choices=("all", "nominatim", "overpass"),
        default="all",
        help="provider family to probe",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print generated queries without contacting network providers",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON report")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the provider probe."""

    args = build_parser().parse_args(argv)
    wifi_ssid = args.wifi_ssid or ubersight._active_wifi_ssid()
    if args.coarse_location:
        network_estimate = ubersight.NetworkLocationEstimate(
            args.coarse_location,
            "operator-provided",
            args.latitude,
            args.longitude,
        )
    else:
        network_estimate = ubersight._geolocation_from_ip(
            timeout_seconds=args.timeout_seconds
        )
    venue_name = ubersight._venue_query_from_wifi_ssid(wifi_ssid)
    queries = tuple(args.query) or ubersight._venue_search_queries(
        venue_name, network_estimate.location
    )
    report: dict[str, Any] = {
        "wifi_ssid": wifi_ssid,
        "coarse_location": network_estimate.location,
        "coarse_source": network_estimate.source,
        "latitude": network_estimate.latitude,
        "longitude": network_estimate.longitude,
        "uncertainty_meters": network_estimate.uncertainty_meters,
        "venue_name": venue_name,
        "dry_run": args.dry_run,
        "queries": [],
    }
    if args.provider in {"all", "nominatim"}:
        for query in queries[: max(0, args.max_queries)]:
            query_report: dict[str, Any] = {"query": query, "results": []}
            if not args.dry_run:
                payload = ubersight._nominatim_search(
                    query,
                    network_estimate,
                    timeout_seconds=args.timeout_seconds,
                    urlopen_func=ubersight.urlopen,
                )
                if isinstance(payload, list):
                    query_report["results"] = [
                        _candidate_summary(candidate, venue_name)
                        for candidate in payload
                    ]
                else:
                    query_report["error"] = "provider returned no result list"
                time.sleep(1.05)
            report["queries"].append(query_report)
    if args.provider in {"all", "overpass"}:
        overpass: dict[str, Any] = {"result": ""}
        if not args.dry_run:
            estimate = ubersight._overpass_venue_location(
                venue_name,
                network_estimate,
                timeout_seconds=args.timeout_seconds,
                urlopen_func=ubersight.urlopen,
            )
            if estimate is not None:
                overpass = {
                    "result": estimate.location,
                    "source": estimate.source,
                    "lat": estimate.latitude,
                    "lon": estimate.longitude,
                }
        report["overpass"] = overpass
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        _print_text_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
