import argparse
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from ubersight import dashboard as ubersight
from ubersight import operator_location_memory_store as location_memory
from ubersight import project_install
from ubersight import stop_ubersight


def sample_status() -> ubersight.UbersightStatus:
    return ubersight.UbersightStatus(
        phase_id="6",
        phase_title="Build background job framework",
        slices=(
            ubersight.SliceStatus("1", "Scaffold storage model", "done"),
            ubersight.SliceStatus("2", "Wire runtime lookup", "active"),
            ubersight.SliceStatus("3", "Validate and ACP", "pending"),
        ),
        notes="Implementing the cache and pinned-store handshake for runtime use.",
        updated_at=datetime.fromisoformat("2026-05-24T20:00:00-07:00"),
    )


def write_background_job_fixture(
    directory: Path,
    job_id: str,
    *,
    state: str = "running",
    **overrides: object,
) -> Path:
    """Write one Ubersight-compatible background-job fixture file."""

    pid = os.getpid()
    raw: dict[str, object] = {
        "schema": ubersight.BACKGROUND_JOB_SCHEMA,
        "updated_at": "2026-05-24T20:00:00-07:00",
        "pid": pid,
        "boot_id": ubersight._boot_id(),
        "process_start_ticks": ubersight._process_start_ticks(pid),
        "job_id": job_id,
        "state": state,
    }
    raw.update(overrides)
    path = directory / f"{job_id.replace(' ', '-')}.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


def sample_status_with_phase_stack() -> ubersight.UbersightStatus:
    return ubersight.UbersightStatus(
        phase_id="9",
        phase_title="Add GIBS imagery presets",
        phases=(
            ubersight.PhaseStatus("6", "Background job kernel", "done"),
            ubersight.PhaseStatus("7", "Live compositor integration", "done"),
            ubersight.PhaseStatus("8", "Retained panning", "done"),
            ubersight.PhaseStatus("9", "Add GIBS imagery presets", "active"),
            ubersight.PhaseStatus("10", "Raster surface binding", "pending"),
            ubersight.PhaseStatus("11", "On-demand imagery cache", "pending"),
            ubersight.PhaseStatus("12", "Perspective presets", "pending"),
            ubersight.PhaseStatus("13", "Imagery GUI smoke", "pending"),
            ubersight.PhaseStatus("14", "Closeout and ACP", "pending"),
        ),
        slices=(
            ubersight.SliceStatus("1", "Document phase plan", "active"),
            ubersight.SliceStatus("2", "Implement adapter foundation", "pending"),
        ),
        notes="Preparing the Ubersight-visible plan before GIBS implementation.",
        updated_at=datetime.fromisoformat("2026-05-24T20:00:00-07:00"),
    )


def test_render_defaults_fit_40_by_40() -> None:
    lines = ubersight.render_status_screen(
        sample_status(),
        origin_status=ubersight.OriginStatus(
            "synced",
            "ORIGIN: SYNCED",
            updated_at=datetime.fromisoformat("2026-05-24T20:00:00-07:00"),
        ),
        operator_location=ubersight.OperatorLocationStatus(
            "diogenes",
            "mheck",
            "FixtureNet",
            "Example City, Example Region, Example Country",
            updated_at=datetime.fromisoformat("2026-05-24T20:00:00-07:00"),
            source="fixture",
        ),
        width=40,
        height=40,
        now=datetime.fromisoformat("2026-05-24T20:00:30-07:00"),
    )

    assert lines[0].strip() == "WORK: SAFE, SYNCED"
    assert lines[1] == "PROJECT: UBERSIGHT"
    assert any(line.startswith(">Slice 2") for line in lines)
    assert any("Workers" == line for line in lines)
    assert sum(line.strip().startswith("OPSEC:") for line in lines[-9:]) >= 2
    assert "mheck@diogenes" in lines[-9:]
    assert "WIFI: FixtureNet" in lines[-9:]
    assert any(line.startswith("LOC: Example City") for line in lines[-9:])
    assert len(lines) <= 40
    assert all(len(line) <= 40 for line in lines)


def test_render_shows_phase_stack_before_slice_stack() -> None:
    lines = ubersight.render_status_screen(
        sample_status_with_phase_stack(),
        width=44,
        height=32,
        now=datetime.fromisoformat("2026-05-24T20:00:30-07:00"),
    )

    phase_rows = [line for line in lines if line.startswith((" Phase", ">Phase"))]
    assert len(phase_rows) == 8
    assert phase_rows[0].startswith(" Phase 7")
    assert phase_rows[1].startswith(" Phase 8")
    assert phase_rows[2].startswith(">Phase 9")
    assert phase_rows[-1].startswith(" Phase 14")
    active_phase_index = lines.index(
        next(line for line in lines if line.startswith(">Phase 9"))
    )
    active_slice_index = lines.index(
        next(line for line in lines if line.startswith(">Slice 1:"))
    )
    assert "" in lines[active_phase_index:active_slice_index]
    assert active_phase_index < active_slice_index


def test_render_prioritizes_uncommitted_local_work_in_safety_status() -> None:
    lines = ubersight.render_status_screen(
        sample_status(),
        origin_status=ubersight.OriginStatus("synced", "ORIGIN: SYNCED"),
        local_status=ubersight.LocalStatus("uncommitted", "LOCAL: UNCOMMITTED"),
        width=40,
        height=40,
        now=datetime.fromisoformat("2026-05-24T20:00:30-07:00"),
    )

    assert lines[0].strip() == "WORK: UNCOMMITTED LOCAL"
    assert lines[1] == "PROJECT: UBERSIGHT"


def test_render_uses_short_safety_status_for_narrow_width() -> None:
    lines = ubersight.render_status_screen(
        sample_status(),
        origin_status=ubersight.OriginStatus("synced", "ORIGIN: SYNCED"),
        local_status=ubersight.LocalStatus("uncommitted", "LOCAL: UNCOMMITTED"),
        width=20,
        height=40,
        now=datetime.fromisoformat("2026-05-24T20:00:30-07:00"),
    )

    assert lines[0].strip() == "WORK: LOCAL"


def test_render_clips_to_smaller_terminal() -> None:
    status = ubersight.UbersightStatus(
        phase_id="Long",
        phase_title="A very long phase title that must be clipped",
        slices=tuple(
            ubersight.SliceStatus(
                str(index),
                f"slice title {index}",
                "active" if index == 5 else "pending",
            )
            for index in range(1, 12)
        ),
        notes=" ".join(["word"] * 40),
        updated_at=datetime.fromisoformat("2026-05-24T20:00:00-07:00"),
    )

    lines = ubersight.render_status_screen(
        status,
        width=24,
        height=12,
        now=datetime.fromisoformat("2026-05-24T20:01:00-07:00"),
    )

    assert len(lines) <= 12
    assert all(len(line) <= 24 for line in lines)
    assert any(line.startswith(">Slice 5") for line in lines)


def test_slice_descriptions_stay_on_one_row() -> None:
    status = ubersight.UbersightStatus(
        phase_id="7",
        phase_title="Long slice",
        slices=(
            ubersight.SliceStatus(
                "1",
                "Build a provider resolver with enough status text to require wrapping",
                "active",
            ),
        ),
        notes="short",
        updated_at=datetime.fromisoformat("2026-05-24T20:00:00-07:00"),
    )

    lines = ubersight.render_status_screen(
        status,
        width=36,
        height=20,
        now=datetime.fromisoformat("2026-05-24T20:00:30-07:00"),
    )

    slice_index = next(
        index for index, line in enumerate(lines) if line.startswith(">")
    )
    assert lines[slice_index].startswith(">Slice 1:")
    assert len(lines[slice_index]) <= 36
    assert not lines[slice_index + 1].startswith(" ")


def test_status_file_round_trip_is_atomic_shape(tmp_path: Path) -> None:
    status_path = tmp_path / "status.json"
    ubersight.write_status_file(sample_status_with_phase_stack(), status_path)

    raw = json.loads(status_path.read_text(encoding="utf-8"))
    loaded = ubersight.load_status(status_path)

    assert raw["schema"] == ubersight.SCHEMA
    assert raw["phase"]["id"] == "9"
    assert raw["phases"][2]["id"] == "8"
    assert loaded.phase_title == "Add GIBS imagery presets"
    assert loaded.phases[3].state == "active"
    assert loaded.slices[0].state == "active"
    assert not list(tmp_path.glob("*.tmp"))


def test_status_writer_rejects_legacy_single_phase_shape() -> None:
    legacy = ubersight.UbersightStatus(
        phase_id="Bugfix",
        phase_title="Product visibility",
        slices=(ubersight.SliceStatus("1", "Validated", "done"),),
        notes="Old shorthand must not overwrite the phase stack.",
        complete=True,
    )

    with pytest.raises(ValueError, match="requires at least one --phase-row"):
        ubersight._normalized_status_for_write(legacy)


def test_status_writer_derives_active_phase_from_stack() -> None:
    status = ubersight.UbersightStatus(
        phase_id="",
        phase_title="",
        phases=(
            ubersight.PhaseStatus("8", "Runtime classes", "done"),
            ubersight.PhaseStatus("9", "DTED rendering", "active"),
            ubersight.PhaseStatus("10", "Panning cache", "pending"),
        ),
        slices=(
            ubersight.SliceStatus("1", "Elevation contract", "active"),
            ubersight.SliceStatus("2", "Terrarium decode", "pending"),
        ),
        notes="Preparing the next phase.",
    )

    normalized = ubersight._normalized_status_for_write(status)

    assert normalized.phase_id == "9"
    assert normalized.phase_title == "DTED rendering"


def test_status_writer_rejects_multiple_active_phases() -> None:
    status = ubersight.UbersightStatus(
        phase_id="9",
        phase_title="DTED rendering",
        phases=(
            ubersight.PhaseStatus("9", "DTED rendering", "active"),
            ubersight.PhaseStatus("10", "Panning cache", "active"),
        ),
        slices=(ubersight.SliceStatus("1", "Elevation contract", "active"),),
        notes="Bad stack.",
    )

    with pytest.raises(ValueError, match="exactly one active"):
        ubersight._normalized_status_for_write(status)


def test_load_missing_status_returns_displayable_fallback(tmp_path: Path) -> None:
    status = ubersight.load_status(tmp_path / "missing.json")

    assert status.source_error == "missing"
    assert status.phase_title == "No Ubersight status file"
    assert status.notes


def test_parse_slice_option_validates_state() -> None:
    parsed = ubersight.parse_slice_option("2:active:Wire Ubersight")
    assert parsed == ubersight.SliceStatus("2", "Wire Ubersight", "active")

    with pytest.raises(argparse.ArgumentTypeError):
        ubersight.parse_slice_option("2:running:bad")


def test_parse_phase_option_validates_state() -> None:
    parsed = ubersight.parse_phase_option("9:active:Add GIBS imagery")
    assert parsed == ubersight.PhaseStatus("9", "Add GIBS imagery", "active")

    with pytest.raises(argparse.ArgumentTypeError):
        ubersight.parse_phase_option("9:running:bad")


def test_parser_defaults_project_name_to_current_directory(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """The default Ubersight context should be the current directory leaf."""

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(ubersight.CONTEXT_ENV, raising=False)

    args = ubersight.build_parser().parse_args(["--once"])

    assert args.project_name == tmp_path.name


def test_main_derives_background_job_dir_from_project_name(monkeypatch) -> None:
    """The runtime job directory should follow the selected project context."""

    captured: dict[str, str] = {}

    def fake_draw_once(args: argparse.Namespace) -> int:
        captured["background_job_dir"] = args.background_job_dir
        return 0

    monkeypatch.setattr(ubersight, "_draw_once", fake_draw_once)

    status = ubersight.main(["--once", "--project-name", "Demo Context"])

    assert status == 0
    assert Path(captured["background_job_dir"]) == ubersight.background_job_dir(
        "Demo Context"
    )


def test_origin_status_parses_sync_states() -> None:
    now = datetime.fromisoformat("2026-05-24T20:00:00-07:00")

    synced = ubersight._parse_origin_status_output(
        "# branch.head main\n# branch.upstream origin/main\n# branch.ab +0 -0\n",
        marker="marker",
        now=now,
    )
    out_of_date = ubersight._parse_origin_status_output(
        "# branch.head main\n# branch.upstream origin/main\n# branch.ab +0 -3\n",
        marker="marker",
        now=now,
    )
    newer = ubersight._parse_origin_status_output(
        "# branch.head main\n# branch.upstream origin/main\n# branch.ab +2 -0\n",
        marker="marker",
        now=now,
    )
    conflict = ubersight._parse_origin_status_output(
        "# branch.head main\n# branch.upstream origin/main\n# branch.ab +2 -3\n",
        marker="marker",
        now=now,
    )

    assert synced.kind == "synced"
    assert out_of_date.label == "ORIGIN: OUT-OF-DATE"
    assert newer.kind == "newer"
    assert conflict.kind == "conflict"


def test_origin_cache_skips_refresh_when_marker_is_fresh(tmp_path: Path) -> None:
    cache_path = tmp_path / "origin.json"
    now = datetime.fromisoformat("2026-05-24T20:00:00-07:00")
    cached = ubersight.OriginStatus(
        "synced",
        "ORIGIN: SYNCED",
        updated_at=now,
        marker="# branch.head main\n# branch.upstream origin/main\n# branch.ab +0 -0",
    )
    ubersight._write_origin_cache(cache_path, cached)
    calls: list[tuple[str, ...]] = []

    def fake_run_command(args, *, cwd=None, timeout_seconds=10.0):
        calls.append(tuple(args))
        return ubersight.CommandResult(
            tuple(args),
            0,
            "# branch.head main\n# branch.upstream origin/main\n# branch.ab +0 -0\n",
            "",
        )

    status = ubersight.load_or_refresh_origin_status(
        tmp_path,
        cache_path,
        now=now,
        run_command=fake_run_command,
    )

    assert status.kind == "synced"
    assert ("git", "fetch", "--quiet", "origin") not in calls


def test_origin_refresh_uses_configured_fetch_timeout(tmp_path: Path) -> None:
    cache_path = tmp_path / "origin.json"
    now = datetime.fromisoformat("2026-05-24T20:00:00-07:00")
    calls: list[tuple[tuple[str, ...], float]] = []

    def fake_run_command(args, *, cwd=None, timeout_seconds=10.0):
        calls.append((tuple(args), timeout_seconds))
        if tuple(args) == ("git", "fetch", "--quiet", "origin"):
            return ubersight.CommandResult(tuple(args), 0, "", "")
        return ubersight.CommandResult(
            tuple(args),
            0,
            "# branch.head main\n# branch.upstream origin/main\n# branch.ab +0 -0\n",
            "",
        )

    status = ubersight.load_or_refresh_origin_status(
        tmp_path,
        cache_path,
        now=now,
        run_command=fake_run_command,
        fetch_timeout_seconds=2.0,
    )

    assert status.kind == "synced"
    assert (("git", "fetch", "--quiet", "origin"), 2.0) in calls


def test_tailscale_alias_parses_magic_dns_name() -> None:
    assert (
        ubersight._parse_tailscale_dns_alias("fussybox.alpha-bravo.ts.net.")
        == "alpha-bravo"
    )
    assert ubersight._parse_tailscale_dns_alias("not-a-tailnet.example") == ""


def test_tailscale_status_parses_running_payload() -> None:
    now = datetime.fromisoformat("2026-05-24T20:00:00-07:00")

    status = ubersight._parse_tailscale_status_payload(
        {
            "BackendState": "Running",
            "Self": {"DNSName": "fussybox.alpha-bravo.ts.net."},
        },
        now=now,
    )

    assert status.kind == "up"
    assert status.label == "Tailscale: alpha-bravo UP"
    assert status.tailnet_alias == "alpha-bravo"


def test_tailscale_status_reuses_alias_when_down() -> None:
    now = datetime.fromisoformat("2026-05-24T20:00:00-07:00")

    status = ubersight._parse_tailscale_status_payload(
        {"BackendState": "Stopped"},
        now=now,
        fallback_alias="alpha-bravo",
    )

    assert status.kind == "down"
    assert status.label == "Tailscale: alpha-bravo DOWN"


def test_tailscale_status_detects_logged_out_error_text() -> None:
    now = datetime.fromisoformat("2026-05-24T20:00:00-07:00")

    status = ubersight._tailscale_status_from_command_result(
        ubersight.CommandResult(
            ("tailscale", "status", "--json"),
            1,
            "",
            "not logged in",
        ),
        now=now,
        fallback_alias="alpha-bravo",
    )

    assert status.kind == "logged_out"
    assert status.label == "Tailscale: LOGGED OUT"
    assert status.tailnet_alias == ""


def test_tailscale_cache_prevents_repeated_cli_probe(tmp_path: Path) -> None:
    cache_path = tmp_path / "tailscale-cache.json"
    now = datetime.fromisoformat("2026-05-24T20:00:00-07:00")
    ubersight._write_tailscale_cache(
        cache_path,
        ubersight.TailscaleStatus(
            "up",
            "Tailscale: alpha-bravo UP",
            updated_at=now,
            tailnet_alias="alpha-bravo",
        ),
    )
    calls = 0

    def fake_run_command(args, *, cwd=None, timeout_seconds=10.0):
        nonlocal calls
        calls += 1
        return ubersight.CommandResult(tuple(args), 127, "", "unexpected")

    status = ubersight.load_or_refresh_tailscale_status(
        cache_path,
        now=now,
        run_command=fake_run_command,
    )

    assert status.kind == "up"
    assert calls == 0


def test_local_status_detects_uncommitted_changes(tmp_path: Path) -> None:
    def fake_run_command(args, *, cwd=None, timeout_seconds=10.0):
        assert tuple(args) == (
            "git",
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        )
        return ubersight.CommandResult(tuple(args), 0, " M scripts/example.py\n", "")

    status = ubersight.load_local_status(
        tmp_path,
        now=datetime.fromisoformat("2026-05-24T20:00:00-07:00"),
        run_command=fake_run_command,
    )

    assert status is not None
    assert status.kind == "uncommitted"


def test_local_status_detects_staged_only_changes(tmp_path: Path) -> None:
    def fake_run_command(args, *, cwd=None, timeout_seconds=10.0):
        return ubersight.CommandResult(tuple(args), 0, "M  scripts/example.py\n", "")

    status = ubersight.load_local_status(
        tmp_path,
        now=datetime.fromisoformat("2026-05-24T20:00:00-07:00"),
        run_command=fake_run_command,
    )

    assert status is not None
    assert status.kind == "uncommitted"


def test_local_status_detects_untracked_files(tmp_path: Path) -> None:
    def fake_run_command(args, *, cwd=None, timeout_seconds=10.0):
        return ubersight.CommandResult(tuple(args), 0, "?? scratch.txt\n", "")

    status = ubersight.load_local_status(
        tmp_path,
        now=datetime.fromisoformat("2026-05-24T20:00:00-07:00"),
        run_command=fake_run_command,
    )

    assert status is not None
    assert status.kind == "uncommitted"


def test_location_cache_prevents_repeated_geolocation(tmp_path: Path) -> None:
    cache_path = tmp_path / "location.json"
    now = datetime.fromisoformat("2026-05-24T20:00:00-07:00")
    cached = ubersight.OperatorLocationStatus(
        "diogenes",
        "mheck",
        "FixtureNet",
        "Example City, Example Region, Example Country",
        updated_at=now,
        source="fixture",
    )
    ubersight._write_location_cache(cache_path, cached)

    status = ubersight.load_or_refresh_operator_location(
        cache_path,
        now=now,
        geolocate=False,
        memory_path=tmp_path / "known-locations.json",
        network_profiles_path=tmp_path / "network-profiles.json",
    )

    assert status.location == "Example City, Example Region, Example Country"
    assert status.wifi_ssid == "FixtureNet"


def test_location_cache_rechecks_network_without_geolocation(tmp_path: Path) -> None:
    cache_path = tmp_path / "location.json"
    now = datetime.fromisoformat("2026-05-24T20:00:00-07:00")
    cached = ubersight.OperatorLocationStatus(
        "diogenes",
        "mheck",
        "FixtureNet",
        "Example City, Example Region, Example Country",
        updated_at=now,
        source="fixture",
        network_observed_at=now.replace(hour=19, minute=59),
    )
    ubersight._write_location_cache(cache_path, cached)

    def fake_run_command(args, *, cwd=None, timeout_seconds=10.0):
        if tuple(args) == (
            "nmcli",
            "-t",
            "-f",
            "DEVICE,TYPE,STATE,CONNECTION",
            "dev",
            "status",
        ):
            return ubersight.CommandResult(
                tuple(args), 0, "wlp3s0:wifi:connected:FixtureNet\n", ""
            )
        return ubersight.CommandResult(tuple(args), 1, "", "")

    def fake_urlopen(request, timeout):
        raise AssertionError(request.full_url)

    status = ubersight.load_or_refresh_operator_location(
        cache_path,
        now=now,
        network_max_age_seconds=30,
        run_command=fake_run_command,
        urlopen_func=fake_urlopen,
        memory_path=tmp_path / "known-locations.json",
        network_profiles_path=tmp_path / "network-profiles.json",
    )

    assert status.location == "Example City, Example Region, Example Country"
    assert status.wifi_ssid == "FixtureNet"
    assert status.network_observed_at == now


def test_location_cache_does_not_mark_unknown_probe_as_fresh(tmp_path: Path) -> None:
    cache_path = tmp_path / "location.json"
    now = datetime.fromisoformat("2026-05-24T20:00:00-07:00")
    observed_at = now.replace(hour=19, minute=59)
    cached = ubersight.OperatorLocationStatus(
        "diogenes",
        "mheck",
        "FixtureNet",
        "Example City, Example Region, Example Country",
        updated_at=now,
        source="fixture",
        network_observed_at=observed_at,
    )
    ubersight._write_location_cache(cache_path, cached)

    def fake_run_command(args, *, cwd=None, timeout_seconds=10.0):
        return ubersight.CommandResult(tuple(args), 1, "", "")

    status = ubersight.load_or_refresh_operator_location(
        cache_path,
        now=now,
        network_max_age_seconds=30,
        run_command=fake_run_command,
        geolocate=False,
        memory_path=tmp_path / "known-locations.json",
        network_profiles_path=tmp_path / "network-profiles.json",
    )

    assert status.wifi_ssid == "FixtureNet"
    assert status.network_observed_at == observed_at


def test_location_refresh_detects_mobile_hotspot_change(tmp_path: Path) -> None:
    cache_path = tmp_path / "location.json"
    now = datetime.fromisoformat("2026-05-24T20:00:00-07:00")
    cached = ubersight.OperatorLocationStatus(
        "diogenes",
        "mheck",
        "FixtureNet",
        "Example Office",
        updated_at=now,
        source="fixture",
        network_observed_at=now.replace(hour=19, minute=59),
    )
    ubersight._write_location_cache(cache_path, cached)

    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self):
            return json.dumps(self.payload).encode("utf-8")

    def fake_urlopen(request, timeout):
        if request.full_url.startswith("https://ipapi.co/"):
            return FakeResponse(
                {
                    "city": "Example City",
                    "region": "Example Region",
                    "country_name": "Example Country",
                    "latitude": 35.0,
                    "longitude": -100.0,
                    "accuracy": 12000,
                }
            )
        raise AssertionError(request.full_url)

    def fake_run_command(args, *, cwd=None, timeout_seconds=10.0):
        if tuple(args) == (
            "nmcli",
            "-t",
            "-f",
            "DEVICE,TYPE,STATE,CONNECTION",
            "dev",
            "status",
        ):
            return ubersight.CommandResult(
                tuple(args), 0, "wlp3s0:wifi:connected:Example Mobile Hotspot\n", ""
            )
        return ubersight.CommandResult(tuple(args), 1, "", "")

    status = ubersight.load_or_refresh_operator_location(
        cache_path,
        now=now,
        network_max_age_seconds=30,
        run_command=fake_run_command,
        urlopen_func=fake_urlopen,
        memory_path=tmp_path / "known-locations.json",
        network_profiles_path=tmp_path / "network-profiles.json",
    )

    assert status.wifi_ssid == "Example Mobile Hotspot"
    assert (
        status.location
        == "Mobile hotspot near Example City, Example Region, Example Country"
    )
    assert status.fine_location == "35.00000, -100.00000 +/- 12 km"
    assert "mobile hotspot" in status.source


def test_location_network_profile_marks_hotspot_without_ssid_hint(
    tmp_path: Path,
) -> None:
    cache_path = tmp_path / "location.json"
    profiles_path = tmp_path / "network-profiles.json"
    location_memory.mark_wifi_network_hotspot(
        "Example Tether",
        profile_path=profiles_path,
    )

    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self):
            return json.dumps(self.payload).encode("utf-8")

    def fake_urlopen(request, timeout):
        if request.full_url.startswith("https://ipapi.co/"):
            return FakeResponse(
                {
                    "city": "Example City",
                    "region": "Example Region",
                    "country_name": "Example Country",
                    "latitude": 35.0,
                    "longitude": -100.0,
                    "accuracy": 12000,
                }
            )
        raise AssertionError(request.full_url)

    def fake_run_command(args, *, cwd=None, timeout_seconds=10.0):
        if tuple(args) == ("nmcli", "-t", "-f", "ACTIVE,SSID", "dev", "wifi"):
            return ubersight.CommandResult(tuple(args), 0, "yes:Example Tether\n", "")
        return ubersight.CommandResult(tuple(args), 1, "", "")

    status = ubersight.load_or_refresh_operator_location(
        cache_path,
        now=datetime.fromisoformat("2026-05-24T20:00:00-07:00"),
        network_max_age_seconds=30,
        run_command=fake_run_command,
        urlopen_func=fake_urlopen,
        memory_path=tmp_path / "known-locations.json",
        network_profiles_path=profiles_path,
    )

    assert status.wifi_ssid == "Example Tether"
    assert (
        status.location
        == "Mobile hotspot near Example City, Example Region, Example Country"
    )
    assert "local network profile: hotspot" in status.source
    assert status.opsec_level == "public"


def test_wifi_detection_uses_active_scan_ssid() -> None:
    def fake_run_command(args, *, cwd=None, timeout_seconds=10.0):
        if tuple(args) == ("nmcli", "-t", "-f", "ACTIVE,SSID", "dev", "wifi"):
            return ubersight.CommandResult(
                tuple(args), 0, "yes:Example Arcade Guest WiFi\n", ""
            )
        raise AssertionError(args)

    assert ubersight._active_wifi_ssid(fake_run_command) == "Example Arcade Guest WiFi"


def test_wifi_detection_falls_back_to_connection_literal() -> None:
    def fake_run_command(args, *, cwd=None, timeout_seconds=10.0):
        if tuple(args) == ("nmcli", "-t", "-f", "ACTIVE,SSID", "dev", "wifi"):
            return ubersight.CommandResult(tuple(args), 1, "", "")
        if tuple(args) == (
            "nmcli",
            "-t",
            "-f",
            "DEVICE,TYPE,STATE,CONNECTION",
            "dev",
            "status",
        ):
            return ubersight.CommandResult(
                tuple(args), 0, "wlp3s0:wifi:connected:Example Arcade Guest WiFi\n", ""
            )
        raise AssertionError(args)

    assert ubersight._active_wifi_ssid(fake_run_command) == "Example Arcade Guest WiFi"


def test_wifi_detection_falls_back_to_active_connection_list() -> None:
    def fake_run_command(args, *, cwd=None, timeout_seconds=10.0):
        if tuple(args) == ("nmcli", "-t", "-f", "ACTIVE,SSID", "dev", "wifi"):
            return ubersight.CommandResult(tuple(args), 1, "", "")
        if tuple(args) == (
            "nmcli",
            "-t",
            "-f",
            "DEVICE,TYPE,STATE,CONNECTION",
            "dev",
            "status",
        ):
            return ubersight.CommandResult(tuple(args), 1, "", "")
        if tuple(args) == (
            "nmcli",
            "-t",
            "-f",
            "NAME,TYPE",
            "connection",
            "show",
            "--active",
        ):
            return ubersight.CommandResult(
                tuple(args), 0, "Example Arcade Guest WiFi:802-11-wireless\n", ""
            )
        raise AssertionError(args)

    assert ubersight._active_wifi_ssid(fake_run_command) == "Example Arcade Guest WiFi"


def test_wifi_detection_falls_back_to_wireless_tools() -> None:
    def fake_run_command(args, *, cwd=None, timeout_seconds=10.0):
        if tuple(args[:1]) == ("nmcli",):
            return ubersight.CommandResult(tuple(args), 1, "", "")
        if tuple(args) == ("iwgetid", "-r"):
            return ubersight.CommandResult(tuple(args), 1, "", "")
        if tuple(args) == ("iw", "dev"):
            return ubersight.CommandResult(
                tuple(args), 0, "phy#0\n\tInterface wlp3s0\n", ""
            )
        if tuple(args) == ("iw", "dev", "wlp3s0", "link"):
            return ubersight.CommandResult(
                tuple(args),
                0,
                "Connected to aa:bb\n\tSSID: Example Arcade Guest WiFi\n",
                "",
            )
        raise AssertionError(args)

    assert ubersight._active_wifi_ssid(fake_run_command) == "Example Arcade Guest WiFi"


def test_wifi_detection_uses_cached_literal_as_last_resort() -> None:
    def fake_run_command(args, *, cwd=None, timeout_seconds=10.0):
        return ubersight.CommandResult(tuple(args), 1, "", "")

    assert (
        ubersight._active_wifi_ssid(
            fake_run_command, fallback_ssid="Example Arcade Guest WiFi"
        )
        == "Example Arcade Guest WiFi"
    )


def test_beacondb_lookup_uses_wifi_bssid_payload() -> None:
    observation = location_memory.LocationObservation(
        "diogenes",
        "mheck",
        "Example Public WiFi",
        wifi_bssid="aa:bb:cc:dd:ee:ff",
        gateway_macs=("00:11:22:33:44:55",),
    )

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self):
            return json.dumps(
                {
                    "location": {
                        "lat": 36.1,
                        "lng": -115.2,
                    },
                    "accuracy": 42,
                }
            ).encode("utf-8")

    def fake_urlopen(request, timeout):
        assert request.full_url == ubersight.BEACONDB_GEOLOCATE_URL
        payload = json.loads(request.data.decode("utf-8"))
        assert payload == {
            "wifiAccessPoints": [
                {
                    "macAddress": "aa:bb:cc:dd:ee:ff",
                }
            ]
        }
        return FakeResponse()

    estimate = ubersight._beacondb_location_from_observation(
        observation,
        urlopen_func=fake_urlopen,
    )

    assert estimate is not None
    assert estimate.latitude == 36.1
    assert estimate.longitude == -115.2
    assert estimate.uncertainty_meters == 42
    assert "BeaconDB" in estimate.source


def test_default_gateway_macs_use_router_neighbor_entry() -> None:
    def fake_run_command(args, *, cwd=None, timeout_seconds=10.0):
        if tuple(args) == ("ip", "route", "show", "default"):
            return ubersight.CommandResult(
                tuple(args),
                0,
                "default via 192.0.2.1 dev wlp3s0 proto dhcp metric 600\n",
                "",
            )
        if tuple(args) == (
            "ip",
            "neigh",
            "show",
            "192.0.2.1",
            "dev",
            "wlp3s0",
        ):
            return ubersight.CommandResult(
                tuple(args),
                0,
                "192.0.2.1 dev wlp3s0 lladdr aa:bb:cc:dd:ee:ff REACHABLE\n",
                "",
            )
        raise AssertionError(args)

    assert ubersight._default_gateway_macs(fake_run_command) == ("aa:bb:cc:dd:ee:ff",)


def add_sysfs_interface(
    root: Path,
    name: str,
    *,
    link_type: str = "1",
    driver: str | None = None,
    devtype: str | None = None,
    phy80211: bool = False,
    lowers: tuple[str, ...] = (),
) -> None:
    """Add one interface to a fake `/sys/class/net` tree.

    Real sysfs uses symlinks for `device`, `phy80211`, and `lower_*`. The
    check only tests that they exist and reads `device/uevent`, so plain
    directories stand in for them on every platform.
    """

    path = root / name
    path.mkdir(parents=True)
    uevent = f"DEVTYPE={devtype}\n" if devtype else ""
    (path / "uevent").write_text(f"{uevent}INTERFACE={name}\n", encoding="utf-8")
    (path / "type").write_text(f"{link_type}\n", encoding="utf-8")
    if driver is not None:
        (path / "device").mkdir()
        (path / "device" / "uevent").write_text(f"DRIVER={driver}\n", encoding="utf-8")
    if phy80211:
        (path / "phy80211").mkdir()
    for lower in lowers:
        (path / f"lower_{lower}").mkdir()


def linux_sys_class_net(root: Path) -> Path:
    """Build a fake `/sys/class/net` holding common Linux link shapes."""

    add_sysfs_interface(root, "enp3s0", driver="e1000e")
    add_sysfs_interface(root, "wlp2s0", driver="iwlwifi", devtype="wlan", phy80211=True)
    add_sysfs_interface(root, "wwan0", driver="cdc_ether", devtype="wwan")
    add_sysfs_interface(root, "eth0", driver="hv_netvsc")
    add_sysfs_interface(root, "ens3", driver="virtio_net")
    add_sysfs_interface(root, "tun0", link_type="65534")
    add_sysfs_interface(root, "veth1")
    add_sysfs_interface(root, "br0", devtype="bridge", lowers=("enp3s0", "veth1"))
    add_sysfs_interface(root, "br0.42", devtype="vlan", lowers=("br0",))
    add_sysfs_interface(root, "virbr0", devtype="bridge", lowers=("veth1",))
    add_sysfs_interface(root, "bond0", devtype="bond", lowers=("enp3s0", "wlp2s0"))
    return root


@pytest.mark.parametrize(
    ("interface", "expected"),
    [
        ("enp3s0", "wired"),
        ("wlp2s0", "wireless"),
        ("wwan0", "wireless"),
        ("eth0", "other"),
        ("ens3", "other"),
        ("tun0", "other"),
        ("veth1", "other"),
        ("br0", "wired"),
        ("br0.42", "wired"),
        ("virbr0", "other"),
        ("bond0", "wireless"),
        ("missing0", "other"),
        ("..", "other"),
    ],
)
def test_linux_interface_kind_reads_sysfs(
    tmp_path: Path, interface: str, expected: str
) -> None:
    root = linux_sys_class_net(tmp_path / "net")

    assert ubersight._linux_interface_kind(root, interface) == expected


def linux_wired_probe_runner(*default_routes: str):
    """Fake `run_command` for a Linux host with no WiFi tools.

    `default_routes` are interface names; each one's gateway answers with
    its own MAC address.
    """

    routes = "".join(
        f"default via 192.0.2.{index} dev {name} proto dhcp metric {index}00\n"
        for index, name in enumerate(default_routes, start=1)
    )

    def run(args, *, cwd=None, timeout_seconds=10.0):
        args = tuple(args)
        if args == ("ip", "route", "show", "default"):
            return ubersight.CommandResult(args, 0, routes, "")
        if args[:3] == ("ip", "neigh", "show") and len(args) == 6:
            gateway, interface = args[3], args[5]
            mac = f"00:11:22:33:44:{gateway.rsplit('.', 1)[1].zfill(2)}"
            return ubersight.CommandResult(
                args, 0, f"{gateway} dev {interface} lladdr {mac} REACHABLE\n", ""
            )
        if args[:2] == ("ip", "-o"):
            return ubersight.CommandResult(
                args, 0, "2: enp3s0    inet 192.0.2.50/24 scope global enp3s0\n", ""
            )
        return ubersight.CommandResult(args, 127, "", "not found")

    return run


@pytest.mark.parametrize(
    ("default_routes", "expected"),
    [
        (("enp3s0",), True),
        (("br0.42",), True),
        (("wlp2s0",), False),
        (("enp3s0", "wlp2s0"), False),
        (("eth0",), False),
        (("tun0",), False),
    ],
    ids=["nic", "vlan-on-bridge", "wifi", "nic-and-wifi", "wsl2", "tunnel"],
)
def test_linux_observation_is_wired_only_over_a_wired_link(
    tmp_path: Path, default_routes: tuple[str, ...], expected: bool
) -> None:
    observation = ubersight.observe_operator_location(
        run_command=linux_wired_probe_runner(*default_routes),
        geolocate=False,
        sys_class_net=linux_sys_class_net(tmp_path / "net"),
    )

    assert observation.wifi_ssid == "unknown"
    assert observation.gateway_macs
    assert observation.wired is expected


@pytest.mark.parametrize(
    ("default_route", "expected_ssid", "expected_wired"),
    [("enp3s0", "unknown", True), ("wlp2s0", "Example Cafe Guest", False)],
    ids=["cable-explains-it", "unexplained-gap"],
)
def test_cached_ssid_fills_only_an_unexplained_wifi_gap(
    tmp_path: Path, default_route: str, expected_ssid: str, expected_wired: bool
) -> None:
    observation = ubersight.observe_operator_location(
        run_command=linux_wired_probe_runner(default_route),
        geolocate=False,
        fallback_ssid="Example Cafe Guest",
        sys_class_net=linux_sys_class_net(tmp_path / "net"),
    )

    assert observation.wifi_ssid == expected_ssid
    assert observation.wired is expected_wired


def test_moving_from_wifi_to_a_cable_shows_ethernet(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The cached WiFi name must not outlive the move to a cable."""

    monkeypatch.setattr(
        ubersight, "LINUX_SYS_CLASS_NET", linux_sys_class_net(tmp_path / "net")
    )
    now = ubersight._local_now()
    cache = tmp_path / "location-cache.json"
    ubersight._write_location_cache(
        cache,
        ubersight.OperatorLocationStatus(
            hostname="example-host",
            username="operator",
            wifi_ssid="Example Cafe Guest",
            location="Example City",
            updated_at=now - timedelta(seconds=60),
            network_observed_at=now - timedelta(seconds=120),
        ),
    )

    status = ubersight.load_or_refresh_operator_location(
        cache,
        now=now,
        network_max_age_seconds=30,
        run_command=linux_wired_probe_runner("enp3s0"),
        geolocate=False,
        memory_path=tmp_path / "memory.json",
        network_profiles_path=tmp_path / "profiles.json",
        observation_log_path=tmp_path / "observations.jsonl",
    )

    assert status.wired is True
    assert status.wifi_ssid == "unknown"
    assert ubersight._network_display_segment(status) == "Ethernet (unknown LAN)"


def test_linux_wired_check_needs_a_router_mac(tmp_path: Path) -> None:
    """A cable with no known router has nothing to remember the LAN by."""

    def run(args, *, cwd=None, timeout_seconds=10.0):
        if tuple(args) == ("ip", "route", "show", "default"):
            return ubersight.CommandResult(
                tuple(args), 0, "default via 192.0.2.1 dev enp3s0\n", ""
            )
        if tuple(args)[:2] == ("ip", "neigh"):
            return ubersight.CommandResult(tuple(args), 0, "", "")
        return ubersight.CommandResult(tuple(args), 127, "", "not found")

    observation = ubersight.observe_operator_location(
        run_command=run,
        geolocate=False,
        sys_class_net=linux_sys_class_net(tmp_path / "net"),
    )

    assert observation.gateway_macs == ()
    assert observation.wired is False


def test_same_ssid_cache_uses_network_fingerprint_when_available(
    tmp_path: Path,
) -> None:
    salt_path = tmp_path / "match-salt"
    salt_path.write_text("fixture-salt\n", encoding="utf-8")
    first_observation = location_memory.LocationObservation(
        "diogenes",
        "mheck",
        "Example Public WiFi",
        gateway_macs=("aa:bb:cc:dd:ee:01",),
    )
    second_observation = location_memory.LocationObservation(
        "diogenes",
        "mheck",
        "Example Public WiFi",
        gateway_macs=("aa:bb:cc:dd:ee:02",),
    )
    cached = ubersight.OperatorLocationStatus(
        "diogenes",
        "mheck",
        "Example Public WiFi",
        "Example Public Venue",
        network_fingerprint=ubersight._location_network_fingerprint(
            first_observation,
            salt_path=salt_path,
        ),
    )

    assert ubersight._same_operator_network(
        cached,
        first_observation,
        salt_path=salt_path,
    )
    assert not ubersight._same_operator_network(
        cached,
        second_observation,
        salt_path=salt_path,
    )


def test_location_entry_defaults_raise_threshold_for_public_ssid(
    tmp_path: Path,
) -> None:
    observation = location_memory.LocationObservation(
        "diogenes",
        "mheck",
        "Example Public WiFi",
        wifi_bssid="aa:bb:cc:dd:ee:ff",
    )

    defaults = ubersight._location_entry_defaults(
        observation,
        memory_path=tmp_path / "known-locations.json",
    )

    assert defaults.confidence_threshold == 100.0
    assert defaults.opsec_level == "public"
    assert defaults.stable_identity


def test_wifi_and_ip_location_can_resolve_named_venue(tmp_path: Path) -> None:
    cache_path = tmp_path / "location.json"

    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self):
            return json.dumps(self.payload).encode("utf-8")

    def fake_urlopen(request, timeout):
        url = request.full_url
        if url.startswith("https://ipapi.co/"):
            return FakeResponse(
                {
                    "city": "Example City",
                    "region": "Example Region",
                    "country_name": "Example Country",
                    "latitude": 35.00,
                    "longitude": -100.00,
                }
            )
        if url.startswith("https://nominatim.openstreetmap.org/search?"):
            return FakeResponse(
                [
                    {
                        "display_name": (
                            "Example Arcade, Example Street, "
                            "Example City, Example Region, Example Country"
                        ),
                        "namedetails": {"name": "Example Arcade"},
                        "address": {
                            "city": "Example City",
                            "state": "Example Region",
                            "country": "Example Country",
                        },
                        "lat": "35.010",
                        "lon": "-100.010",
                    }
                ]
            )
        raise AssertionError(url)

    def fake_run_command(args, *, cwd=None, timeout_seconds=10.0):
        if tuple(args) == ("nmcli", "-t", "-f", "ACTIVE,SSID", "dev", "wifi"):
            return ubersight.CommandResult(
                tuple(args), 0, "yes:Example Arcade Guest WiFi\n", ""
            )
        return ubersight.CommandResult(tuple(args), 1, "", "")

    status = ubersight.load_or_refresh_operator_location(
        cache_path,
        now=datetime.fromisoformat("2026-05-24T20:00:00-07:00"),
        run_command=fake_run_command,
        urlopen_func=fake_urlopen,
        network_profiles_path=tmp_path / "network-profiles.json",
    )

    assert status.wifi_ssid == "Example Arcade Guest WiFi"
    assert status.location == "Example City, Example Region, Example Country"
    assert status.fine_location == (
        "Example Arcade, Example City, Example Region, Example Country"
    )
    assert status.source.startswith("OpenStreetMap Nominatim via WiFi+")


def test_example_arcade_wifi_generates_game_bar_query_hint() -> None:
    queries = ubersight._venue_search_queries(
        "Example Arcade", "Example City, Example Region, Example Country"
    )

    assert (
        queries[0]
        == "Example Arcade Video Game Bar Example City, Example Region, Example Country"
    )


def test_location_display_uses_coarse_then_fine_rows() -> None:
    lines = ubersight.render_status_screen(
        sample_status(),
        operator_location=ubersight.OperatorLocationStatus(
            "diogenes",
            "mheck",
            "Example Arcade Guest WiFi",
            "Example City, Example Region, Example Country",
            fine_location="Example Arcade, Example City, Example Region, Example Country",
        ),
        width=64,
        height=40,
        now=datetime.fromisoformat("2026-05-24T20:00:30-07:00"),
    )

    loc_lines = [line for line in lines if line.startswith("LOC:")]
    assert loc_lines[0].startswith("LOC: Example City")
    assert loc_lines[1].startswith("LOC: Example Arcade")
    assert len(loc_lines) == 2


def test_footer_displays_raw_public_wifi_ssid() -> None:
    lines = ubersight.render_status_screen(
        sample_status(),
        operator_location=ubersight.OperatorLocationStatus(
            "diogenes",
            "mheck",
            "Example Arcade Guest WiFi",
            "Example City, Example Region, Example Country",
            fine_location="Example Arcade, Example City, Example Region, Example Country",
        ),
        width=72,
        height=40,
        now=datetime.fromisoformat("2026-05-24T20:00:30-07:00"),
    )

    rendered = "\n".join(lines)
    assert "WIFI: Example Arcade Guest WiFi" in rendered


def test_footer_displays_raw_private_wifi_ssid() -> None:
    lines = ubersight.render_status_screen(
        sample_status(),
        operator_location=ubersight.OperatorLocationStatus(
            "diogenes",
            "mheck",
            "PrivateFixtureNet",
            "Example Home Office",
            opsec_level="private-trusted",
        ),
        width=72,
        height=40,
        now=datetime.fromisoformat("2026-05-24T20:00:30-07:00"),
    )

    rendered = "\n".join(lines)
    assert "WIFI: PrivateFixtureNet" in rendered


def test_footer_displays_raw_unrecognized_private_wifi_ssid() -> None:
    lines = ubersight.render_status_screen(
        sample_status(),
        operator_location=ubersight.OperatorLocationStatus(
            "diogenes",
            "mheck",
            "PrivateFixtureNet",
            "Example City, Example Region, Example Country",
        ),
        width=72,
        height=40,
        now=datetime.fromisoformat("2026-05-24T20:00:30-07:00"),
    )

    rendered = "\n".join(lines)
    assert "WIFI: PrivateFixtureNet" in rendered


def test_latlong_fallback_displays_uncertainty() -> None:
    estimate = ubersight.NetworkLocationEstimate(
        "Example City, Example Region, Example Country",
        "fixture",
        35.00,
        -100.00,
        25_000.0,
    )

    assert ubersight._latlong_label(estimate) == "35.00000, -100.00000 +/- 25 km"


def test_location_memory_learn_and_lookup_uses_local_alias(tmp_path: Path) -> None:
    memory_path = tmp_path / "known-locations.json"
    salt_path = tmp_path / "match-salt"
    observation = location_memory.LocationObservation(
        "diogenes",
        "mheck",
        "Example Guest WiFi",
        wifi_bssid="aa:bb:cc:dd:ee:ff",
        local_subnets=("192.0.2.0/24",),
        gateway_macs=("aa:bb:cc:00:00:01",),
        network_estimate=ubersight.NetworkLocationEstimate(
            "Example City, Example Region, Example Country",
            "fixture",
            35.0,
            -100.0,
            25_000.0,
        ),
    )

    location_memory.learn_location_memory(
        "Example Home Office",
        observation=observation,
        memory_path=memory_path,
        salt_path=salt_path,
    )
    match = location_memory.lookup_location_memory(
        observation,
        memory_path=memory_path,
        salt_path=salt_path,
    )

    assert match is not None
    assert not match.ambiguous
    assert match.record["label"] == "Example Home Office"
    status = ubersight._status_from_location_memory_match(
        observation,
        match,
        datetime.fromisoformat("2026-05-24T20:00:00-07:00"),
    )
    assert status.location == "Example Home Office"
    assert status.opsec_level == "private-trusted"
    assert not ubersight._operator_location_is_public(status)


def test_location_memory_threshold_prevents_common_ssid_only_match(
    tmp_path: Path,
) -> None:
    memory_path = tmp_path / "known-locations.json"
    salt_path = tmp_path / "match-salt"
    full_observation = location_memory.LocationObservation(
        "diogenes",
        "mheck",
        "Example Public WiFi",
        gateway_macs=("aa:bb:cc:00:00:01",),
    )
    ssid_only_observation = location_memory.LocationObservation(
        "diogenes",
        "mheck",
        "Example Public WiFi",
    )

    location_memory.learn_location_memory(
        "Example Public Venue",
        observation=full_observation,
        memory_path=memory_path,
        salt_path=salt_path,
        confidence_threshold=100,
    )

    assert (
        location_memory.lookup_location_memory(
            full_observation,
            memory_path=memory_path,
            salt_path=salt_path,
        )
        is not None
    )
    assert (
        location_memory.lookup_location_memory(
            ssid_only_observation,
            memory_path=memory_path,
            salt_path=salt_path,
        )
        is None
    )


def test_operator_location_refresh_prefers_local_memory(tmp_path: Path) -> None:
    memory_path = tmp_path / "known-locations.json"
    salt_path = tmp_path / "match-salt"
    cache_path = tmp_path / "location-cache.json"
    observation_log_path = tmp_path / "observations.jsonl"
    observation = location_memory.LocationObservation(
        "diogenes",
        "mheck",
        "Example Guest WiFi",
    )
    location_memory.learn_location_memory(
        "Example Home Office",
        observation=observation,
        memory_path=memory_path,
        salt_path=salt_path,
    )

    def fake_run_command(args, *, cwd=None, timeout_seconds=10.0):
        if tuple(args) == (
            "nmcli",
            "-t",
            "-f",
            "DEVICE,TYPE,STATE,CONNECTION",
            "dev",
            "status",
        ):
            return ubersight.CommandResult(
                tuple(args), 0, "wlp3s0:wifi:connected:Example Guest WiFi\n", ""
            )
        return ubersight.CommandResult(tuple(args), 1, "", "")

    status = ubersight.load_or_refresh_operator_location(
        cache_path,
        geolocate=False,
        run_command=fake_run_command,
        memory_path=memory_path,
        network_profiles_path=tmp_path / "network-profiles.json",
        observation_log_path=observation_log_path,
    )

    assert status.location == "Example Home Office"
    assert status.source == "local memory: ssid"
    assert status.opsec_level == "private-trusted"
    assert observation_log_path.exists()


def test_footer_status_blocks_can_expand_to_three_lines() -> None:
    location = ubersight.OperatorLocationStatus(
        "very-long-hostname",
        "operator",
        "Long Wireless Network Name",
        "A very specific city, a detailed region, a country with a long name",
        source="https://example.invalid/geolocation/provider",
    )

    lines = ubersight.render_status_screen(
        sample_status(),
        operator_location=location,
        width=32,
        height=32,
        now=datetime.fromisoformat("2026-05-24T20:00:30-07:00"),
    )

    footer = lines[-9:]
    opsec = [i for i, line in enumerate(footer) if line.startswith("OPSEC:")]
    identity = footer.index("operator@very-long-hostname")
    loc = [i for i, line in enumerate(footer) if line.startswith("LOC:")]
    assert len(opsec) == 3 and max(opsec) < identity
    assert footer[identity + 1] == "WIFI: Long Wireless Network Name"
    assert loc and min(loc) == identity + 2
    assert all(len(line) <= 32 for line in footer)


def test_tailscale_footer_renders_below_opsec() -> None:
    location = ubersight.OperatorLocationStatus(
        "diogenes",
        "mheck",
        "FixtureNet",
        "Example City, Example Region, Example Country",
    )
    tailscale = ubersight.TailscaleStatus(
        "up",
        "Tailscale: alpha-bravo UP",
        tailnet_alias="alpha-bravo",
    )

    lines = ubersight.render_status_screen(
        sample_status(),
        operator_location=location,
        tailscale_status=tailscale,
        width=40,
        height=40,
        now=datetime.fromisoformat("2026-05-24T20:00:30-07:00"),
    )

    opsec_index = next(
        index for index, line in enumerate(lines) if line.strip().startswith("OPSEC:")
    )
    tailscale_index = next(
        index
        for index, line in enumerate(lines)
        if line.strip().startswith("Tailscale:")
    )
    host_index = lines.index("mheck@diogenes")
    assert opsec_index < tailscale_index < host_index
    assert lines[tailscale_index].startswith("Tailscale:")
    assert "alpha-bravo UP" in lines[tailscale_index]


def test_hotkey_bar_renders_above_footer_blocks() -> None:
    location = ubersight.OperatorLocationStatus(
        "diogenes",
        "mheck",
        "FixtureNet",
        "Example City, Example Region, Example Country",
    )

    lines = ubersight.render_status_screen(
        sample_status(),
        operator_location=location,
        width=44,
        height=40,
        now=datetime.fromisoformat("2026-05-24T20:00:30-07:00"),
    )

    hotkey_index = next(
        index for index, line in enumerate(lines) if line.startswith("(R)efresh")
    )
    data_index = next(
        index for index, line in enumerate(lines) if line.startswith("DATA")
    )
    opsec_index = next(
        index for index, line in enumerate(lines) if line.strip().startswith("OPSEC:")
    )
    assert hotkey_index < data_index < opsec_index


def test_hotkey_bar_preserves_requested_spacing() -> None:
    assert ubersight._hotkey_bar_line(80).startswith(
        "(R)efresh  (E)nter location  (Q)uit"
    )


def test_beacondb_footer_attribution_uses_descriptive_location_line() -> None:
    location = ubersight.OperatorLocationStatus(
        "diogenes",
        "mheck",
        "Example Public WiFi",
        "36.10000, -115.20000 +/- 42 m",
        fine_location="Example Venue, Example City",
        source="OpenStreetMap Nominatim via WiFi+BeaconDB",
    )

    lines = ubersight._operator_footer_lines(
        location,
        80,
        max_lines_per_status=2,
    )

    assert any(line == "LOC: Example Venue, Example City *(BeaconDB)" for line in lines)
    assert not any(
        line == "LOC: 36.10000, -115.20000 +/- 42 m *(BeaconDB)" for line in lines
    )


def test_public_wifi_escalates_opsec_footer() -> None:
    location = ubersight.OperatorLocationStatus(
        "diogenes",
        "mheck",
        "Example Arcade Guest WiFi",
        "Example City, Example Region, Example Country",
    )

    lines = ubersight.render_status_screen(
        sample_status(),
        operator_location=location,
        width=40,
        height=40,
        now=datetime.fromisoformat("2026-05-24T20:00:30-07:00"),
    )

    opsec_lines = [line for line in lines if line.strip().startswith("OPSEC!:")]
    assert opsec_lines
    assert "PUBLIC NETWORK" in opsec_lines[0]


def test_private_wifi_keeps_normal_opsec_footer() -> None:
    location = ubersight.OperatorLocationStatus(
        "diogenes",
        "mheck",
        "PrivateFixtureNet",
        "Example City, Example Region, Example Country",
    )

    lines = ubersight.render_status_screen(
        sample_status(),
        operator_location=location,
        width=40,
        height=40,
        now=datetime.fromisoformat("2026-05-24T20:00:30-07:00"),
    )

    assert any(line.strip().startswith("OPSEC:") for line in lines)
    assert not any(line.strip().startswith("OPSEC!:") for line in lines)


def test_colored_header_and_footer_lines_paint_full_width() -> None:
    assert ubersight._curses_line_text("WORK: SAFE, SYNCED", 30) == (
        "WORK: SAFE, SYNCED".ljust(30)
    )
    # Routine OPSEC notes are colored text without a band; only the
    # public-network alarm paints across the pane.
    assert ubersight._curses_line_text("OPSEC: LOCAL STATUS", 30) == (
        "OPSEC: LOCAL STATUS"
    )
    assert ubersight._curses_line_text("OPSEC!: PUBLIC NETWORK", 30) == (
        "OPSEC!: PUBLIC NETWORK".ljust(30)
    )
    assert ubersight._curses_line_text("WORK: UNCOMMITTED LOCAL", 30) == (
        "WORK: UNCOMMITTED LOCAL".ljust(30)
    )
    assert ubersight._curses_line_text("Tailscale: alpha-bravo UP", 30) == (
        "Tailscale: alpha-bravo UP".ljust(30)
    )
    assert ubersight._curses_line_text("DATA [###...] RUN", 30) == (
        "DATA [###...] RUN".ljust(30)
    )
    assert ubersight._curses_line_text("(R)efresh  (E)nter", 30) == (
        "(R)efresh  (E)nter".ljust(30)
    )
    assert ubersight._curses_line_text("mheck@diogenes", 30, band=True) == (
        "mheck@diogenes".ljust(30)
    )
    assert ubersight._curses_line_text("PROJECT: UBERSIGHT", 30) == (
        "PROJECT: UBERSIGHT"
    )


def test_public_opsec_uses_white_on_red() -> None:
    class FakeCurses:
        A_BOLD = 1
        A_NORMAL = 0

        @staticmethod
        def color_pair(index):
            return index * 100

    assert ubersight._curses_attr_for_line(
        FakeCurses, "OPSEC!: PUBLIC NETWORK", True
    ) == (FakeCurses.color_pair(7) | FakeCurses.A_BOLD)


def test_routine_opsec_is_text_only_while_public_alarm_keeps_its_band() -> None:
    """Routine OPSEC notes use colored text; only the alarm paints a band."""

    class FakeCurses:
        A_BOLD = 1
        A_NORMAL = 0

        @staticmethod
        def color_pair(index):
            return index * 100

    assert ubersight._curses_attr_for_line(
        FakeCurses, "OPSEC: LOCAL STATUS / LOCATION ESTIMATE;", True
    ) == (FakeCurses.color_pair(12) | FakeCurses.A_BOLD)
    assert not ubersight._line_uses_full_width_background("OPSEC: LOCAL STATUS")
    assert ubersight._line_uses_full_width_background("OPSEC!: PUBLIC NETWORK")
    assert ubersight._curses_line_text("OPSEC: short", 20) == "OPSEC: short"


def test_local_work_banner_is_plain_black_on_yellow() -> None:
    """The local/pull banner avoids bold, which greys out black text."""

    class FakeCurses:
        A_BOLD = 1
        A_NORMAL = 0

        @staticmethod
        def color_pair(index):
            return index * 100

    for label in ("WORK: UNCOMMITTED LOCAL", "WORK: LOCAL", "WORK: PULL FIRST"):
        assert ubersight._curses_attr_for_line(
            FakeCurses, f"  {label}  ", True
        ) == FakeCurses.color_pair(13)


def test_only_the_public_opsec_alarm_centers_its_first_row() -> None:
    """Routine OPSEC rows stay left-aligned; the alarm band centers its title."""

    routine = ubersight._opsec_footer_lines(48, max_lines_per_status=3)
    public = ubersight._opsec_footer_lines(
        48,
        max_lines_per_status=3,
        operator_location=ubersight.OperatorLocationStatus(
            hostname="example-host",
            username="operator",
            wifi_ssid="Example Free Public WiFi",
            location="Example City",
        ),
    )

    assert all(line.startswith("OPSEC: ") for line in routine)
    assert public[0].startswith(" ") and public[0].strip().startswith("OPSEC!: ")


def test_identity_rows_show_user_at_host_then_network() -> None:
    wifi = ubersight.OperatorLocationStatus("diogenes", "mheck", "FixtureNet", "x")
    wired = ubersight.OperatorLocationStatus(
        "diogenes", "mheck", "unknown", "x", wired=True, network_label="Example Wired LAN"
    )

    assert ubersight._operator_identity_lines(wifi, 40) == (
        "mheck@diogenes",
        "WIFI: FixtureNet",
    )
    assert ubersight._operator_identity_lines(wired, 40) == (
        "mheck@diogenes",
        "Ethernet (Example Wired LAN)",
    )
    assert ubersight._operator_identity_lines(wired, 10)[0] == "mheck@d..."


def test_identity_rows_are_styled_from_data_not_text() -> None:
    class FakeCurses:
        A_BOLD = 1
        A_NORMAL = 0

        @staticmethod
        def color_pair(index):
            return index * 100

    assert ubersight._curses_attr_for_line(
        FakeCurses, "mheck@diogenes", True, ubersight.HOST_STATE
    ) == FakeCurses.color_pair(10)
    assert (
        ubersight._curses_attr_for_line(FakeCurses, "mheck@diogenes", True)
        == FakeCurses.A_NORMAL
    )
    assert ubersight._curses_line_text("mheck@diogenes", 20) == "mheck@diogenes"


def test_tailscale_status_colors_reflect_state() -> None:
    class FakeCurses:
        A_BOLD = 1
        A_NORMAL = 0

        @staticmethod
        def color_pair(index):
            return index * 100

    # Dark text on a band is plain; light text on a band is bold.
    assert ubersight._curses_attr_for_line(
        FakeCurses, "Tailscale: alpha-bravo UP", True
    ) == FakeCurses.color_pair(14)
    assert ubersight._curses_attr_for_line(
        FakeCurses, "Tailscale: alpha-bravo DOWN", True
    ) == FakeCurses.color_pair(15)
    assert ubersight._curses_attr_for_line(
        FakeCurses, "Tailscale: LOGGED OUT", True
    ) == (FakeCurses.color_pair(16) | FakeCurses.A_BOLD)
    assert ubersight._curses_attr_for_line(
        FakeCurses, "Tailscale: ERROR", True
    ) == FakeCurses.color_pair(17)


def test_dark_text_on_bands_is_never_bold() -> None:
    """Bold black renders grey in PDCurses and bold-as-bright terminals."""

    class FakeCurses:
        A_BOLD = 1

        @staticmethod
        def color_pair(index):
            return index * 100

    for pair, (foreground, background) in ubersight.CURSES_COLOR_PAIRS.items():
        attr = ubersight._pair_attr(FakeCurses, pair)
        if foreground == "BLACK":
            assert background is not None
            assert attr == FakeCurses.color_pair(pair), pair
        else:
            assert attr == FakeCurses.color_pair(pair) | FakeCurses.A_BOLD, pair
    # No band pairs red text with a cyan background, which is unreadable.
    assert ("RED", "CYAN") not in ubersight.CURSES_COLOR_PAIRS.values()


def test_color_pairs_register_with_curses_constants() -> None:
    registered: dict[int, tuple[int, int]] = {}

    class FakeCurses:
        COLOR_BLACK, COLOR_RED, COLOR_GREEN, COLOR_YELLOW = 0, 1, 2, 3
        COLOR_BLUE, COLOR_MAGENTA, COLOR_CYAN, COLOR_WHITE = 4, 5, 6, 7

        @staticmethod
        def init_pair(pair, foreground, background):
            registered[pair] = (foreground, background)

    ubersight._init_color_pairs(FakeCurses)

    assert sorted(registered) == list(range(1, 19))
    assert registered[1] == (FakeCurses.COLOR_CYAN, -1)
    assert registered[8] == (FakeCurses.COLOR_BLACK, FakeCurses.COLOR_CYAN)
    assert registered[13] == (FakeCurses.COLOR_BLACK, FakeCurses.COLOR_YELLOW)


def test_load_background_jobs_reads_live_status_files(tmp_path: Path) -> None:
    """Ubersight should load live background-job files."""

    write_background_job_fixture(
        tmp_path,
        "fixture-job",
        app="example-worker",
        source="fixture",
        category="index",
        subject="Fixture Index",
        verb="build",
        state="running",
        ticker="Fixture Index build",
        work_completed=5,
        work_total=10,
        work_unit="shards",
        bytes_completed=1024,
        bytes_total=2048,
        work_items_per_second=2.5,
        bytes_per_second=512,
        eta_seconds=2,
    )

    statuses = ubersight.load_background_jobs(tmp_path)

    assert len(statuses) == 1
    assert statuses[0].job_id == "fixture-job"
    assert statuses[0].subject == "Fixture Index"
    assert statuses[0].state == "running"
    assert statuses[0].work_unit == "shards"


def test_load_background_jobs_rejects_obsolete_schema(tmp_path: Path) -> None:
    """Ubersight should ignore pre-generalization status files."""

    status_path = tmp_path / "obsolete-job.json"
    status_path.write_text(
        json.dumps(
            {
                "schema": "knowledgeable.transfer-status.v1",
                "updated_at": "2026-05-24T20:00:00-07:00",
                "pid": 12345,
                "boot_id": ubersight._boot_id(),
                "process_start_ticks": "1",
                "job_id": "obsolete-job",
                "product": "Fixture Tiles",
                "operation": "preload",
                "state": "running",
                "completed": 5,
                "total": 10,
                "unit": "tiles",
            }
        ),
        encoding="utf-8",
    )

    statuses = ubersight.load_background_jobs(tmp_path)

    assert statuses == ()
    assert status_path.exists()


def test_load_background_jobs_discards_stale_process_files(tmp_path: Path) -> None:
    """Dead background jobs should not leave stale rows in Ubersight."""

    stale = {
        "schema": ubersight.BACKGROUND_JOB_SCHEMA,
        "updated_at": "2026-05-24T20:00:00-07:00",
        "pid": 99999999,
        "boot_id": ubersight._boot_id(),
        "process_start_ticks": "0",
        "job_id": "stale-job",
        "state": "running",
    }
    status_path = tmp_path / "stale-job.json"
    status_path.write_text(json.dumps(stale), encoding="utf-8")

    assert ubersight.load_background_jobs(tmp_path) == ()
    assert not status_path.exists()


def test_load_background_jobs_discards_complete_job_files(tmp_path: Path) -> None:
    """Completed jobs should not pollute active aggregate monitor rows."""

    status_path = write_background_job_fixture(
        tmp_path,
        "complete-job",
        state="complete",
        subject="Fixture Tiles",
        work_completed=10,
        work_total=10,
        work_unit="tiles",
    )

    assert ubersight.load_background_jobs(tmp_path) == ()
    assert not status_path.exists()


def test_background_job_cleanup_tolerates_read_only_runtime_files(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Read-only runtime dirs should not make Ubersight crash on cleanup."""

    status_path = write_background_job_fixture(
        tmp_path,
        "complete-job",
        state="complete",
        subject="Fixture Tiles",
    )

    def fail_unlink(*_args, **_kwargs) -> None:
        raise OSError("read-only runtime")

    monkeypatch.setattr(Path, "unlink", fail_unlink)

    assert ubersight.load_background_jobs(tmp_path) == ()
    assert status_path.exists()


def test_render_includes_three_background_job_rows() -> None:
    """The footer should expose ticker, gauge, and responsive job stats."""

    lines = ubersight.render_status_screen(
        sample_status(),
        background_jobs=(
            ubersight.BackgroundJobStatus(
                "fixture-job",
                123,
                "running",
                subject="Fixture Tiles",
                verb="preload",
                ticker="Fixture Tiles preload",
                work_completed=5,
                work_total=10,
                work_unit="tiles",
                bytes_completed=1024,
                bytes_total=2048,
                work_items_per_second=2.5,
                bytes_per_second=512,
                eta_seconds=2,
            ),
        ),
        width=44,
        height=40,
        now=datetime.fromisoformat("2026-05-24T20:00:30-07:00"),
    )

    data_lines = [line for line in lines if line.startswith("DATA")]
    assert len(data_lines) == 3
    assert any("RUN" in line for line in data_lines)
    assert any("5/10 tiles" in line for line in data_lines)


def test_background_job_eta_prefers_exact_item_progress_over_byte_estimate() -> None:
    """Estimated byte totals should not distort tiled-job ETA."""

    lines = ubersight._background_job_lines(
        (
            ubersight.BackgroundJobStatus(
                "gibs-live-job",
                123,
                "running",
                subject="NASA GIBS Blue Marble",
                verb="preload",
                work_completed=1983,
                work_total=5461,
                work_unit="tiles",
                bytes_completed=9.3 * 1024 * 1024,
                bytes_total=170.7 * 1024 * 1024,
                work_items_per_second=2.8,
                bytes_per_second=13.4 * 1024,
            ),
        ),
        72,
        now=datetime.fromisoformat("2026-05-24T20:00:30-07:00"),
    )

    assert "ETA 00:20:42" in lines[2]
    assert "1983/5461 tiles" in lines[2]
    assert "13.4 KiB/s" in lines[2]


def test_collect_process_tree_uses_fake_proc(tmp_path: Path) -> None:
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    (proc_root / "uptime").write_text("100.0 10.0\n", encoding="utf-8")
    write_fake_proc(proc_root, 100, 1, "S", "codex", ["codex", "run"], start_ticks=1000)
    write_fake_proc(proc_root, 101, 100, "R", "pytest", ["pytest"], start_ticks=2000)
    write_fake_proc(proc_root, 200, 1, "S", "other", ["other"], start_ticks=3000)

    workers = ubersight.collect_process_tree(100, proc_root=proc_root)

    assert [worker.pid for worker in workers] == [100, 101]
    assert workers[1].command == "pytest"


def test_stop_helper_matches_legacy_and_installed_dashboard_commands() -> None:
    assert stop_ubersight._is_ubersight_dashboard_command(
        "python scripts/ubersight.py --once"
    )
    assert stop_ubersight._is_ubersight_dashboard_command(
        "/repo/.venv/bin/python /repo/.venv/bin/ubersight"
    )
    assert not stop_ubersight._is_ubersight_dashboard_command(
        "python scripts/stop_ubersight.py"
    )


def test_project_install_manages_all_console_launchers(tmp_path: Path) -> None:
    """Launcher management covers every installed Ubersight command."""

    venv_path = tmp_path / "venv"
    venv_bin = venv_path / ("Scripts" if os.name == "nt" else "bin")
    bin_dir = tmp_path / "published-bin"
    venv_bin.mkdir(parents=True)
    for command_name in project_install.COMMANDS:
        target_path = project_install.command_path(venv_path, command_name)
        target_path.write_text("#!/bin/sh\n", encoding="utf-8")

    launchers = project_install.manage_launchers(
        mode="standard",
        venv_path=venv_path,
        bin_dir=bin_dir,
        copy_launchers=False,
    )

    assert {launcher.name for launcher in launchers} == set(project_install.COMMANDS)
    for command_name in project_install.COMMANDS:
        launcher = bin_dir / command_name
        assert launcher.is_symlink()
        assert launcher.resolve() == project_install.command_path(
            venv_path, command_name
        )

    assert (
        project_install.manage_launchers(
            mode="venv-only",
            venv_path=venv_path,
            bin_dir=bin_dir,
            copy_launchers=False,
        )
        == ()
    )
    assert not any(
        (bin_dir / command_name).exists() for command_name in project_install.COMMANDS
    )


def test_project_install_preserves_python_symlink_path(tmp_path: Path) -> None:
    """The installer must run pip through `.venv/bin/python`, not its target."""

    target = tmp_path / "python-target"
    link = tmp_path / "python"
    target.write_text("", encoding="utf-8")
    link.symlink_to(target)

    assert project_install.absolute_path_preserving_symlinks(link) == link
    assert project_install.absolute_path_preserving_symlinks(link) != target.resolve()


def test_project_install_command_log_avoids_shell_redirection(
    monkeypatch, capsys, tmp_path: Path
) -> None:
    """Project install logs must not redirect into the venv interpreter."""

    calls = []

    def fake_run(command, *, cwd, check):
        """Capture the child invocation without starting a process."""

        calls.append((command, cwd, check))

    monkeypatch.setattr(project_install.subprocess, "run", fake_run)
    command = [tmp_path / "venv" / "bin" / "python", "--version"]

    project_install.run(command, repo_root=tmp_path)

    output = capsys.readouterr().out
    assert "[install-project] RUN:" in output
    assert " -> " not in output
    assert calls == [([str(part) for part in command], tmp_path, True)]


def write_fake_proc(
    proc_root: Path,
    pid: int,
    ppid: int,
    state: str,
    comm: str,
    argv: list[str],
    *,
    start_ticks: int,
) -> None:
    process_dir = proc_root / str(pid)
    process_dir.mkdir()
    fields = [
        state,
        str(ppid),
        "1",
        "1",
        "1",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        str(start_ticks),
    ]
    (process_dir / "stat").write_text(
        f"{pid} ({comm}) {' '.join(fields)}\n", encoding="utf-8"
    )
    (process_dir / "cmdline").write_bytes(
        b"\x00".join(argument.encode() for argument in argv) + b"\x00"
    )
