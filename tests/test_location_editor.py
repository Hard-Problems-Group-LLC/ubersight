"""Tests for the location-memory editor (the `E` page) on WiFi and Ethernet.

The editor's logic lives in pure helpers for defaults, fields, header, keys,
and saving, so these tests drive it without a terminal. Fixtures use
documentation addresses and example MACs.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ubersight import dashboard as ubersight
from ubersight import operator_location_memory_store as location_memory

FakeCurses = SimpleNamespace(
    KEY_UP=259,
    KEY_DOWN=258,
    KEY_LEFT=260,
    KEY_RIGHT=261,
    KEY_BACKSPACE=263,
    KEY_ENTER=343,
    KEY_F2=266,
    KEY_BTAB=353,
)

WIRED = location_memory.LocationObservation(
    "example-host",
    "operator",
    "unknown",
    local_subnets=("192.0.2.0/24",),
    gateway_macs=("00:11:22:aa:bb:cc",),
    wired=True,
)
WIFI = location_memory.LocationObservation(
    "example-host",
    "operator",
    "Example Cafe Guest",
    wifi_bssid="66:77:88:99:aa:bb",
    gateway_macs=("00:11:22:aa:bb:cc",),
)


@pytest.fixture(autouse=True)
def isolated_salt(monkeypatch, tmp_path: Path) -> None:
    """Keep the editor's default salt path inside the test directory."""

    monkeypatch.chdir(tmp_path)


def defaults_for(observation, memory: Path) -> ubersight.LocationEntryDefaults:
    return ubersight._location_entry_defaults(observation, memory_path=memory)


def type_text(fields, index, text):
    for character in text:
        fields, index, _ = ubersight._apply_location_entry_key(
            fields, index, character, FakeCurses
        )
    return fields, index


# Fields and header --------------------------------------------------------


def test_wired_form_names_the_lan_and_the_place(tmp_path: Path) -> None:
    defaults = defaults_for(WIRED, tmp_path / "memory.json")
    fields = ubersight._location_entry_fields(defaults, wired=True)
    title, rows = ubersight._location_entry_header(WIRED, defaults)

    assert [label for label, _ in fields] == [
        "LAN name",
        "Place",
        "Detail",
        "OPSEC",
        "Threshold",
    ]
    assert title == "NAME THIS NETWORK"
    assert rows[0] == "Wired LAN via router 00:11:22:aa:bb:cc"
    assert rows[1] == "Subnet: 192.0.2.0/24"
    assert not any("SSID" in row for row in rows)


def test_wifi_form_shows_network_and_access_point(tmp_path: Path) -> None:
    defaults = defaults_for(WIFI, tmp_path / "memory.json")
    fields = ubersight._location_entry_fields(defaults, wired=False)
    title, rows = ubersight._location_entry_header(WIFI, defaults)

    assert fields[0][0] == "Place"
    assert "LAN name" not in dict(fields)
    assert title == "NAME THIS LOCATION"
    assert rows[:3] == [
        "WiFi: Example Cafe Guest",
        "Access point: 66:77:88:99:aa:bb",
        "Router: 00:11:22:aa:bb:cc",
    ]


def test_header_warns_when_nothing_identifies_the_network(tmp_path: Path) -> None:
    bare = location_memory.LocationObservation("h", "u", "unknown")
    defaults = defaults_for(bare, tmp_path / "memory.json")

    _, rows = ubersight._location_entry_header(bare, defaults)

    assert rows[0] == "WiFi name not available"
    assert rows[-1] == "No access point or router captured; cannot save."


# Keys ---------------------------------------------------------------------


def wired_fields():
    return [
        ("LAN name", ""),
        ("Place", ""),
        ("Detail", ""),
        ("OPSEC", "unknown"),
        ("Threshold", "80"),
    ]


def test_every_printable_character_can_be_typed_including_q() -> None:
    fields, index = type_text(wired_fields(), 0, "Quincy Café q")

    assert fields[0] == ("LAN name", "Quincy Café q")
    assert index == 0


def test_escape_cancels_and_enter_saves() -> None:
    for key in ("\x1b", 27):
        assert ubersight._apply_location_entry_key(wired_fields(), 0, key, FakeCurses)[
            2
        ] == ("cancel")
    for key in ("\n", "\r", 10, FakeCurses.KEY_ENTER, FakeCurses.KEY_F2):
        assert (
            ubersight._apply_location_entry_key(wired_fields(), 0, key, FakeCurses)[2]
            == "save"
        )


def test_navigation_wraps_and_backspace_edits() -> None:
    fields = wired_fields()
    _, index, _ = ubersight._apply_location_entry_key(
        fields, 0, FakeCurses.KEY_UP, FakeCurses
    )
    assert index == len(fields) - 1
    _, index, _ = ubersight._apply_location_entry_key(fields, index, "\t", FakeCurses)
    assert index == 0
    fields, index = type_text(fields, 0, "Dyst")
    fields, _, _ = ubersight._apply_location_entry_key(
        fields, 0, FakeCurses.KEY_BACKSPACE, FakeCurses
    )
    assert fields[0] == ("LAN name", "Dys")


def test_opsec_cycles_with_arrows_and_ignores_typing() -> None:
    fields = wired_fields()
    opsec = 3

    fields, _, _ = ubersight._apply_location_entry_key(
        fields, opsec, FakeCurses.KEY_RIGHT, FakeCurses
    )
    assert fields[opsec] == ("OPSEC", "private-trusted")
    fields, _, _ = ubersight._apply_location_entry_key(
        fields, opsec, FakeCurses.KEY_LEFT, FakeCurses
    )
    assert fields[opsec] == ("OPSEC", "unknown")
    fields, _ = type_text(fields, opsec, "x")
    assert fields[opsec] == ("OPSEC", "unknown")


# Saving -------------------------------------------------------------------


def saved_records(memory: Path) -> list[dict]:
    return json.loads(memory.read_text(encoding="utf-8"))["locations"]


def test_wired_save_stores_lan_name_and_place_then_displays_both(
    tmp_path: Path,
) -> None:
    memory = tmp_path / "memory.json"
    fields = wired_fields()
    fields[0] = ("LAN name", "Example Wired LAN")
    fields[1] = ("Place", "Home office")
    fields[2] = ("Detail", "Upstairs desk")
    fields[3] = ("OPSEC", "private-trusted")

    assert ubersight._save_location_entry(fields, WIRED, memory_path=memory) == ""

    (record,) = saved_records(memory)
    assert record["label"] == "Home office"
    assert record["lan_name"] == "Example Wired LAN"
    assert record["display"] == {"coarse": "Home office", "fine": "Upstairs desk"}
    match = location_memory.lookup_location_memory(WIRED, memory_path=memory)
    assert match is not None and "gateway" in match.reasons
    status = ubersight._status_from_location_memory_match(
        WIRED, match, ubersight._local_now()
    )
    assert ubersight._network_display_segment(status) == "Ethernet (Example Wired LAN)"
    assert status.location == "Home office"
    assert status.fine_location == "Upstairs desk"


def test_naming_only_the_lan_uses_it_as_the_place(tmp_path: Path) -> None:
    memory = tmp_path / "memory.json"
    fields = wired_fields()
    fields[0] = ("LAN name", "Example Wired LAN")

    assert ubersight._save_location_entry(fields, WIRED, memory_path=memory) == ""

    (record,) = saved_records(memory)
    assert record["label"] == "Example Wired LAN"
    assert record["lan_name"] == "Example Wired LAN"


def test_renaming_a_matched_place_updates_it_instead_of_duplicating(
    tmp_path: Path,
) -> None:
    memory = tmp_path / "memory.json"
    first = wired_fields()
    first[0] = ("LAN name", "Example Wired LAN")
    first[1] = ("Place", "Home office")
    ubersight._save_location_entry(first, WIRED, memory_path=memory)

    defaults = defaults_for(WIRED, memory)
    assert defaults.matched and defaults.record_id
    assert (defaults.lan_name, defaults.label) == ("Example Wired LAN", "Home office")
    renamed = ubersight._location_entry_fields(defaults, wired=True)
    renamed[1] = ("Place", "Studio")
    ubersight._save_location_entry(
        renamed, WIRED, memory_path=memory, record_id=defaults.record_id
    )

    records = saved_records(memory)
    assert [record["label"] for record in records] == ["Studio"]
    assert records[0]["lan_name"] == "Example Wired LAN"


def test_wifi_save_leaves_an_existing_lan_name_alone(tmp_path: Path) -> None:
    memory = tmp_path / "memory.json"
    location_memory.learn_location_memory(
        "Home office",
        observation=WIRED,
        memory_path=memory,
        lan_name="Example Wired LAN",
    )
    fields = [
        ("Place", "Home office"),
        ("Detail", ""),
        ("OPSEC", "private"),
        ("Threshold", "80"),
    ]

    ubersight._save_location_entry(fields, WIFI, memory_path=memory)

    (record,) = saved_records(memory)
    assert record["lan_name"] == "Example Wired LAN"
    assert record["opsec_level"] == "private"


@pytest.mark.parametrize(
    ("place", "observation", "threshold", "message"),
    [
        ("", WIFI, "80", "Name the place"),
        (
            "Somewhere",
            location_memory.LocationObservation("h", "u", "x"),
            "80",
            "cannot save",
        ),
        ("Somewhere", WIFI, "0", "Threshold"),
    ],
)
def test_save_reports_problems_without_writing(
    tmp_path: Path, place, observation, threshold, message
) -> None:
    memory = tmp_path / "memory.json"
    fields = [
        ("Place", place),
        ("Detail", ""),
        ("OPSEC", "unknown"),
        ("Threshold", threshold),
    ]

    assert message in ubersight._save_location_entry(
        fields, observation, memory_path=memory
    )
    assert not memory.exists()
