"""Tests for `ubersight-autosplit` backend detection and command plans.

Plans are plain data, so these tests check the exact commands each backend
would run without splitting a real terminal. The command syntax follows each
tool's documented interface; live verification on each terminal remains an
operator check.
"""

from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path

import pytest

from ubersight import autosplit

WORKDIR = Path("/work/project")
DASHBOARD = ("/venv/bin/python", "-m", "ubersight")


def request(**overrides: object) -> autosplit.LayoutRequest:
    values: dict[str, object] = {
        "working_dir": WORKDIR,
        "dashboard_command": DASHBOARD,
    }
    values.update(overrides)
    return autosplit.LayoutRequest(**values)  # type: ignore[arg-type]


def argvs(plan: autosplit.SplitPlan) -> list[tuple[str, ...]]:
    return [step.argv for step in plan.steps]


# Detection ----------------------------------------------------------------

TMUX_ENV = {"TMUX": "/tmp/tmux-1/default,99,0", "TMUX_PANE": "%3"}


def test_tmux_wins_over_the_emulator_around_it() -> None:
    env = {**TMUX_ENV, "TERMINATOR_UUID": "urn:uuid:1"}

    assert (
        autosplit.detect_backend(
            env, is_windows=False, ancestor_names=("bash", "tmux: server")
        )
        == autosplit.TMUX
    )


def test_nearest_ancestor_resolves_screen_inside_tmux() -> None:
    env = {**TMUX_ENV, "STY": "123.pts-0.host"}

    assert (
        autosplit.detect_backend(
            env,
            is_windows=False,
            ancestor_names=("bash", "screen", "bash", "tmux: server"),
        )
        == autosplit.SCREEN
    )
    assert (
        autosplit.detect_backend(
            env,
            is_windows=False,
            ancestor_names=("bash", "tmux: server", "screen"),
        )
        == autosplit.TMUX
    )


def test_ambiguous_multiplexers_without_ancestry_require_backend() -> None:
    env = {**TMUX_ENV, "STY": "123.pts-0.host"}

    with pytest.raises(autosplit.AutosplitError, match="--backend tmux"):
        autosplit.detect_backend(env, is_windows=False, ancestor_names=())


def test_leaked_emulator_variable_loses_to_nearer_emulator() -> None:
    env = {"TERMINATOR_UUID": "urn:uuid:1", "KITTY_WINDOW_ID": "4"}

    assert (
        autosplit.detect_backend(
            env, is_windows=False, ancestor_names=("bash", "kitty", "bash")
        )
        == autosplit.KITTY
    )


def test_leaked_tmux_variable_loses_to_the_actual_emulator() -> None:
    env = {**TMUX_ENV, "WEZTERM_PANE": "7"}

    assert (
        autosplit.detect_backend(
            env, is_windows=False, ancestor_names=("bash", "wezterm-gui")
        )
        == autosplit.WEZTERM
    )


def test_multiplexers_are_allowed_inside_remote_sessions() -> None:
    env = {**TMUX_ENV, "SSH_CONNECTION": "a 1 b 22"}

    assert (
        autosplit.detect_backend(env, is_windows=False, ancestor_names=("sshd",))
        == autosplit.TMUX
    )
    assert (
        autosplit.detect_backend(
            {"STY": "1.pts-0.host", "SSH_TTY": "/dev/pts/0"},
            is_windows=False,
            ancestor_names=("bash", "screen", "bash", "mosh-server"),
        )
        == autosplit.SCREEN
    )


def test_wezterm_mux_server_works_on_a_remote_host() -> None:
    env = {"WEZTERM_PANE": "2", "SSH_CONNECTION": "a 1 b 22"}

    assert (
        autosplit.detect_backend(
            env,
            is_windows=False,
            ancestor_names=("bash", "wezterm-mux-server", "sshd"),
        )
        == autosplit.WEZTERM
    )


def test_windows_terminal_detected_from_wt_session() -> None:
    assert (
        autosplit.detect_backend(
            {"WT_SESSION": "abc"},
            is_windows=True,
            ancestor_names=("pwsh.exe", "windowsterminal.exe"),
        )
        == autosplit.WINDOWS_TERMINAL
    )


def test_wt_session_is_ignored_off_windows() -> None:
    with pytest.raises(autosplit.AutosplitError, match="No supported terminal"):
        autosplit.detect_backend(
            {"WT_SESSION": "forwarded-through-wslenv"},
            is_windows=False,
            ancestor_names=(),
        )


@pytest.mark.parametrize(
    ("env", "ancestors", "reason"),
    [
        ({"SSH_CONNECTION": "10.0.0.2 5000 10.0.0.1 22"}, (), "ssh"),
        ({"SSH_TTY": "/dev/pts/3"}, (), "ssh"),
        ({}, ("bash", "mosh-server"), "mosh"),
        ({}, ("bash", "sshd-session", "sshd"), "ssh"),
        ({}, ("pwsh.exe", "sshd.exe"), "ssh"),
    ],
)
def test_remote_sessions_refuse_emulator_backends(
    env: dict[str, str], ancestors: tuple[str, ...], reason: str
) -> None:
    leaked = {
        **env,
        "WT_SESSION": "leaked",
        "TERMINATOR_UUID": "urn:uuid:leaked",
        "KITTY_WINDOW_ID": "1",
    }

    assert autosplit.remote_session_reason(leaked, ancestors) == reason
    with pytest.raises(autosplit.AutosplitError, match=f"over {reason}"):
        autosplit.detect_backend(leaked, is_windows=True, ancestor_names=ancestors)


def test_remote_boundary_stops_the_ancestor_walk() -> None:
    """An emulator above sshd belongs to another session on this host."""

    with pytest.raises(autosplit.AutosplitError, match="over ssh"):
        autosplit.detect_backend(
            {"KITTY_WINDOW_ID": "1"},
            is_windows=False,
            ancestor_names=("bash", "sshd", "kitty"),
        )


def test_stale_tmux_variable_without_pane_is_not_trusted() -> None:
    with pytest.raises(autosplit.AutosplitError, match="No supported terminal"):
        autosplit.detect_backend({"TMUX": "stale"}, is_windows=False, ancestor_names=())


def test_proc_ancestor_walk_reads_parent_chain(tmp_path: Path) -> None:
    def write_stat(pid: int, comm: str, ppid: int) -> None:
        directory = tmp_path / str(pid)
        directory.mkdir()
        (directory / "stat").write_text(
            f"{pid} ({comm}) S {ppid} 0 0 0 0\n", encoding="utf-8"
        )

    write_stat(40, "python3", 30)
    write_stat(30, "bash", 20)
    write_stat(20, "tmux: server", 1)

    assert autosplit._ancestor_names_from_proc(40, tmp_path) == (
        "bash",
        "tmux: server",
    )


# Windows Terminal ---------------------------------------------------------


def test_windows_terminal_plan_chains_layout_in_current_window() -> None:
    plan = autosplit.plan_windows_terminal(
        request(), {"WT_PROFILE_ID": "{profile-guid}"}
    )

    assert plan.backend == autosplit.WINDOWS_TERMINAL
    assert argvs(plan) == [
        (
            "wt",
            "-w",
            "0",
            "split-pane",
            "-V",
            "-s",
            "0.3",
            "-p",
            "{profile-guid}",
            "--title",
            "Ubersight",
            "-d",
            str(WORKDIR),
            *DASHBOARD,
            ";",
            "move-focus",
            "left",
            ";",
            "split-pane",
            "-H",
            "-s",
            "0.33",
            "-p",
            "{profile-guid}",
            "-d",
            str(WORKDIR),
            ";",
            "move-focus",
            "up",
        )
    ]


def test_windows_terminal_plan_escapes_semicolons_and_omits_missing_profile() -> None:
    plan = autosplit.plan_windows_terminal(
        request(
            working_dir=Path("/odd;dir"),
            dashboard_command=("python", "-m", "ubersight", "--notes=a;b"),
        ),
        {},
    )

    argv = plan.steps[0].argv
    assert "-p" not in argv
    assert "--notes=a\\;b" in argv
    assert str(Path("/odd;dir")).replace(";", "\\;") in argv
    assert argv.count(";") == 3


# tmux ---------------------------------------------------------------------


def test_tmux_plan_splits_invoking_pane_without_moving_focus() -> None:
    plan = autosplit.plan_tmux(request(), {"TMUX_PANE": "%7"}, (3, 4))

    assert argvs(plan) == [
        (
            "tmux",
            "split-window",
            "-h",
            "-d",
            "-t",
            "%7",
            "-l",
            "30%",
            "-c",
            str(WORKDIR),
            "--",
            *DASHBOARD,
        ),
        (
            "tmux",
            "split-window",
            "-v",
            "-d",
            "-t",
            "%7",
            "-l",
            "33%",
            "-c",
            str(WORKDIR),
        ),
        ("tmux", "select-pane", "-t", "%7"),
    ]
    assert plan.notes == ()


def test_tmux_plan_uses_percentage_flag_before_3_1() -> None:
    plan = autosplit.plan_tmux(request(), {"TMUX_PANE": "%1"}, (2, 9))

    assert plan.steps[0].argv[6:8] == ("-p", "30")
    assert plan.steps[1].argv[6:8] == ("-p", "33")


def test_tmux_plan_notes_unknown_version() -> None:
    plan = autosplit.plan_tmux(request(), {"TMUX_PANE": "%1"}, None)

    assert plan.steps[0].argv[6:8] == ("-l", "30%")
    assert "3.1" in plan.notes[0]


@pytest.mark.parametrize(
    ("text", "version"),
    [
        ("tmux 3.3a\n", (3, 3)),
        ("tmux next-3.5\n", (3, 5)),
        ("tmux 2.9\n", (2, 9)),
        ("tmux master\n", None),
    ],
)
def test_parse_tmux_version(text: str, version: tuple[int, int] | None) -> None:
    assert autosplit.parse_tmux_version(text) == version


# GNU screen ---------------------------------------------------------------


def test_screen_plan_builds_regions_and_windows_in_project_dir() -> None:
    plan = autosplit.plan_screen(request(), (4, 9))

    assert argvs(plan) == [
        ("screen", "-X", "split", "-v"),
        ("screen", "-X", "focus", "right"),
        ("screen", "-t", "Ubersight", *DASHBOARD),
        ("screen", "-X", "resize", "-h", "30%"),
        ("screen", "-X", "focus", "left"),
        ("screen", "-X", "split"),
        ("screen", "-X", "focus", "down"),
        ("screen", "-t", "Console"),
        ("screen", "-X", "resize", "-v", "33%"),
        ("screen", "-X", "focus", "up"),
    ]
    assert all(step.interactive for step in plan.steps)
    window_steps = [step for step in plan.steps if "-X" not in step.argv]
    assert [step.cwd for step in window_steps] == [WORKDIR, WORKDIR]
    assert not any("chdir" in step.argv for step in plan.steps)


def test_screen_plan_refuses_versions_without_vertical_split() -> None:
    with pytest.raises(autosplit.AutosplitError, match="4.2"):
        autosplit.plan_screen(request(), (4, 1))


@pytest.mark.parametrize(
    ("text", "version"),
    [
        ("Screen version 4.09.01 (GNU) 20-Aug-23\n", (4, 9)),
        ("Screen version 5.00.02 (build on 2025)\n", (5, 0)),
        ("Screen version 4.00.03jw4 (FAU) 2-May-06\n", (4, 0)),
        ("", None),
    ],
)
def test_parse_screen_version(text: str, version: tuple[int, int] | None) -> None:
    assert autosplit.parse_screen_version(text) == version


# WezTerm ------------------------------------------------------------------


def test_wezterm_plan_splits_pane_and_restores_focus() -> None:
    plan = autosplit.plan_wezterm(request(), {"WEZTERM_PANE": "12"}, 20240203)

    assert argvs(plan) == [
        (
            "wezterm",
            "cli",
            "split-pane",
            "--pane-id",
            "12",
            "--right",
            "--percent",
            "30",
            "--cwd",
            str(WORKDIR),
            "--",
            *DASHBOARD,
        ),
        (
            "wezterm",
            "cli",
            "split-pane",
            "--pane-id",
            "12",
            "--bottom",
            "--percent",
            "33",
            "--cwd",
            str(WORKDIR),
        ),
        ("wezterm", "cli", "activate-pane", "--pane-id", "12"),
    ]


def test_wezterm_plan_degrades_and_refuses_by_build() -> None:
    older = autosplit.plan_wezterm(request(), {"WEZTERM_PANE": "1"}, 20221119)

    assert len(older.steps) == 2
    assert "cannot move focus" in older.notes[0]
    with pytest.raises(autosplit.AutosplitError, match="20220624"):
        autosplit.plan_wezterm(request(), {"WEZTERM_PANE": "1"}, 20220101)


def test_parse_wezterm_build() -> None:
    assert (
        autosplit.parse_wezterm_build("wezterm 20240203-110809-5046fc22\n") == 20240203
    )
    assert autosplit.parse_wezterm_build("wezterm nightly") is None


# kitty --------------------------------------------------------------------


def test_kitty_plan_switches_to_splits_and_keeps_focus() -> None:
    plan = autosplit.plan_kitty(
        request(), {"KITTY_WINDOW_ID": "5"}, (0, 41, 1), use_kitten=True
    )

    assert argvs(plan) == [
        ("kitten", "@", "goto-layout", "--match", "window_id:5", "splits"),
        (
            "kitten",
            "@",
            "launch",
            "--type=window",
            "--self",
            "--next-to=id:5",
            "--location=vsplit",
            "--bias=30",
            "--keep-focus",
            f"--cwd={WORKDIR}",
            "--title=Ubersight",
            "--",
            *DASHBOARD,
        ),
        (
            "kitten",
            "@",
            "launch",
            "--type=window",
            "--self",
            "--next-to=id:5",
            "--location=hsplit",
            "--bias=33",
            "--keep-focus",
            f"--cwd={WORKDIR}",
            "--title=Console",
        ),
    ]
    assert all(step.interactive for step in plan.steps)
    assert any("allow_remote_control" in note for note in plan.notes)


def test_kitty_plan_adapts_to_older_releases() -> None:
    plan = autosplit.plan_kitty(
        request(), {"KITTY_WINDOW_ID": "5"}, (0, 30, 0), use_kitten=False
    )

    launch = plan.steps[1].argv
    assert launch[:2] == ("kitty", "@")
    assert not any(arg.startswith("--bias") for arg in launch)
    assert not any(arg.startswith("--next-to") for arg in launch)
    assert any("50/50" in note for note in plan.notes)
    with pytest.raises(autosplit.AutosplitError, match="0.17"):
        autosplit.plan_kitty(
            request(), {"KITTY_WINDOW_ID": "5"}, (0, 16, 0), use_kitten=False
        )


# Terminator ---------------------------------------------------------------


def test_terminator_plan_uses_remotinator_with_error_detection() -> None:
    plan = autosplit.plan_terminator(
        request(), {"TERMINATOR_UUID": "urn:uuid:abc"}, (2, 1, 3)
    )

    assert argvs(plan) == [
        (
            "remotinator",
            "vsplit",
            "-u",
            "urn:uuid:abc",
            "-T",
            "Ubersight",
            "-x",
            f"cd {shlex.quote(str(WORKDIR))} && exec /venv/bin/python -m ubersight",
        ),
        ("remotinator", "hsplit", "-u", "urn:uuid:abc", "-T", "Console"),
    ]
    assert all(step.failure_prefix == "ERROR" for step in plan.steps)
    assert any("50/50" in note for note in plan.notes)


def test_terminator_plan_quotes_paths_and_requires_execute_support() -> None:
    plan = autosplit.plan_terminator(
        request(
            working_dir=Path("/work/my project"),
            dashboard_command=("/opt/my python/bin/python", "-m", "ubersight"),
        ),
        {"TERMINATOR_UUID": "urn:uuid:abc"},
        None,
    )

    command = plan.steps[0].argv[-1]
    assert "'/opt/my python/bin/python' -m ubersight" in command
    with pytest.raises(autosplit.AutosplitError, match="2.1.2"):
        autosplit.plan_terminator(
            request(), {"TERMINATOR_UUID": "urn:uuid:abc"}, (2, 1, 1)
        )


# build_plan ---------------------------------------------------------------


def fake_version_run(outputs: dict[str, tuple[int, str, str]]):
    def run(args, **kwargs):
        returncode, stdout, stderr = outputs[args[0]]
        return subprocess.CompletedProcess(args, returncode, stdout, stderr)

    return run


def test_build_plan_reads_versions_for_each_backend() -> None:
    run = fake_version_run(
        {
            "tmux": (0, "tmux 2.8\n", ""),
            "screen": (1, "Screen version 4.01.00 (GNU)\n", ""),
            "wezterm": (0, "wezterm 20221119-145034-49b9839f\n", ""),
            "kitty": (0, "kitty 0.30.0 created by Kovid Goyal\n", ""),
        }
    )

    tmux = autosplit.build_plan(autosplit.TMUX, request(), {"TMUX_PANE": "%2"}, run=run)
    assert tmux.steps[0].argv[6:8] == ("-p", "30")
    with pytest.raises(autosplit.AutosplitError, match="4.2"):
        autosplit.build_plan(autosplit.SCREEN, request(), {}, run=run)
    wezterm = autosplit.build_plan(
        autosplit.WEZTERM, request(), {"WEZTERM_PANE": "1"}, run=run
    )
    assert len(wezterm.steps) == 2
    kitty = autosplit.build_plan(
        autosplit.KITTY,
        request(),
        {"KITTY_WINDOW_ID": "3"},
        run=run,
        which=lambda name: None,
    )
    assert kitty.steps[0].argv[:2] == ("kitty", "@")


def test_build_plan_tolerates_missing_version_tools() -> None:
    def missing(args, **kwargs):
        raise FileNotFoundError(args[0])

    plan = autosplit.build_plan(
        autosplit.TERMINATOR,
        request(),
        {"TERMINATOR_UUID": "urn:uuid:1"},
        run=missing,
    )

    assert any("Could not read the Terminator version" in n for n in plan.notes)


# Execution ----------------------------------------------------------------


def test_execute_plan_stops_at_first_failed_step() -> None:
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 1, "", "no server running")

    plan = autosplit.SplitPlan(
        "tmux", (autosplit.SplitStep(("tmux", "a")), autosplit.SplitStep(("tmux", "b")))
    )

    with pytest.raises(autosplit.AutosplitError, match="step 1 of 2 failed"):
        autosplit.execute_plan(plan, run=fake_run, which=lambda name: name)

    assert calls == [["tmux", "a"]]


def test_execute_plan_treats_remotinator_error_output_as_failure() -> None:
    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(args, 0, "ERROR: no such terminal\n", "")

    plan = autosplit.SplitPlan(
        "terminator",
        (autosplit.SplitStep(("remotinator", "vsplit"), failure_prefix="ERROR"),),
    )

    with pytest.raises(autosplit.AutosplitError, match="no such terminal"):
        autosplit.execute_plan(plan, run=fake_run, which=lambda name: name)


def test_execute_plan_inherits_terminal_for_interactive_steps() -> None:
    seen: list[dict[str, object]] = []

    def fake_run(args, **kwargs):
        seen.append(kwargs)
        return subprocess.CompletedProcess(args, 0)

    plan = autosplit.SplitPlan(
        "screen",
        (
            autosplit.SplitStep(
                ("screen", "-t", "Ubersight"), cwd=WORKDIR, interactive=True
            ),
            autosplit.SplitStep(("tmux", "select-pane")),
        ),
    )

    autosplit.execute_plan(plan, run=fake_run, which=lambda name: name)

    assert seen[0] == {"cwd": str(WORKDIR), "check": False}
    assert seen[1]["stdin"] is subprocess.DEVNULL
    assert seen[1]["capture_output"] is True


def test_execute_plan_reports_missing_tool() -> None:
    plan = autosplit.SplitPlan("tmux", (autosplit.SplitStep(("tmux", "a")),))

    with pytest.raises(autosplit.AutosplitError, match="not found on PATH"):
        autosplit.execute_plan(plan, which=lambda name: None)


def test_describe_step_is_shell_neutral() -> None:
    assert autosplit.describe_step(
        autosplit.SplitStep(("screen", "-t", "Console"), cwd=WORKDIR)
    ) == (f"RUN (in {WORKDIR}): screen -t Console")


# Project directory --------------------------------------------------------


def test_project_dir_defaults_to_git_toplevel(tmp_path: Path) -> None:
    def fake_run(args, **kwargs):
        assert args == ["git", "rev-parse", "--show-toplevel"]
        assert kwargs["cwd"] == str(tmp_path / "sub")
        return subprocess.CompletedProcess(args, 0, "C:/work/project\n", "")

    assert autosplit.resolve_project_dir(tmp_path / "sub", None, run=fake_run) == Path(
        "C:/work/project"
    )


def test_project_dir_refuses_outside_a_checkout(tmp_path: Path) -> None:
    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(args, 128, "", "fatal: not a git repo")

    with pytest.raises(autosplit.AutosplitError, match="--force"):
        autosplit.resolve_project_dir(tmp_path, None, run=fake_run)


def test_explicit_project_dir_needs_no_checkout(tmp_path: Path) -> None:
    def unexpected(args, **kwargs):
        raise AssertionError("git lookup is unnecessary")

    project = tmp_path / "project"
    project.mkdir()

    assert (
        autosplit.resolve_project_dir(tmp_path, Path("project"), run=unexpected)
        == project.resolve()
    )
    with pytest.raises(autosplit.AutosplitError, match="not a directory"):
        autosplit.resolve_project_dir(tmp_path, Path("missing"), run=unexpected)


# Command line -------------------------------------------------------------


def tmux_session(monkeypatch) -> None:
    """Simulate running inside tmux with a fixed version and no ancestry."""

    monkeypatch.setenv("TMUX", "sock,1,0")
    monkeypatch.setenv("TMUX_PANE", "%4")
    for key in ("STY", "WEZTERM_PANE", "KITTY_WINDOW_ID", "TERMINATOR_UUID"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(autosplit, "_version_text", lambda run, argv: "tmux 3.4\n")
    monkeypatch.setattr(autosplit, "ancestor_process_names", lambda: ())


def refuse_project(cwd: Path, explicit: Path | None) -> Path:
    if explicit is not None:
        raise autosplit.AutosplitError(f"--project-dir {explicit} is not a directory.")
    raise autosplit.AutosplitError(f"{cwd} is not inside a project checkout.")


def test_dry_run_prints_plan_and_passes_dashboard_arguments(
    monkeypatch, capsys
) -> None:
    tmux_session(monkeypatch)
    monkeypatch.setattr(autosplit, "resolve_project_dir", lambda cwd, explicit: WORKDIR)

    status = autosplit.main(["--dry-run", "--json", "--", "--no-geolocate"])

    assert status == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["backend"] == "tmux"
    first = plan["steps"][0]["argv"]
    assert first[first.index("-c") + 1] == str(WORKDIR)
    assert first[-4:] == ["-m", "ubersight", "--supervise", "--no-geolocate"]


def test_text_dry_run_prints_run_lines(monkeypatch, capsys) -> None:
    tmux_session(monkeypatch)
    monkeypatch.setattr(autosplit, "resolve_project_dir", lambda cwd, explicit: WORKDIR)

    assert autosplit.main(["--dry-run"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 3
    assert all(line.startswith("RUN") for line in lines)


def test_cli_rejects_out_of_range_fractions(capsys) -> None:
    with pytest.raises(SystemExit):
        autosplit.main(["--ubersight-width", "0.95", "--dry-run"])

    assert "between 0.1 and 0.9" in capsys.readouterr().err


def test_cli_reports_detection_failures(monkeypatch, capsys) -> None:
    for key in (
        "TMUX",
        "TMUX_PANE",
        "STY",
        "WEZTERM_PANE",
        "KITTY_WINDOW_ID",
        "TERMINATOR_UUID",
        "WT_SESSION",
        "SSH_CONNECTION",
        "SSH_CLIENT",
        "SSH_TTY",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(autosplit, "ancestor_process_names", lambda: ())
    monkeypatch.setattr(autosplit, "resolve_project_dir", lambda cwd, explicit: WORKDIR)

    assert autosplit.main(["--dry-run"]) == 1
    assert "No supported terminal" in capsys.readouterr().err


def test_cli_fails_outside_project_without_force(monkeypatch, capsys) -> None:
    tmux_session(monkeypatch)
    monkeypatch.setattr(autosplit, "resolve_project_dir", refuse_project)

    assert autosplit.main(["--dry-run"]) == 1
    captured = capsys.readouterr()
    assert "not inside a project checkout" in captured.err
    assert captured.out == ""


def test_cli_force_warns_and_uses_current_directory(
    monkeypatch, capsys, tmp_path: Path
) -> None:
    tmux_session(monkeypatch)
    monkeypatch.setattr(autosplit, "resolve_project_dir", refuse_project)
    monkeypatch.chdir(tmp_path)

    assert autosplit.main(["--force", "--dry-run", "--json"]) == 0
    captured = capsys.readouterr()
    assert "warning:" in captured.err and "--force" in captured.err
    first = json.loads(captured.out)["steps"][0]["argv"]
    assert first[first.index("-c") + 1] == str(tmp_path)


def test_cli_force_does_not_excuse_a_missing_project_dir(monkeypatch, capsys) -> None:
    tmux_session(monkeypatch)
    monkeypatch.setattr(autosplit, "resolve_project_dir", refuse_project)

    assert autosplit.main(["--force", "--project-dir", "missing", "--dry-run"]) == 1
    assert "not a directory" in capsys.readouterr().err


def test_explicit_backend_skips_detection(monkeypatch, capsys) -> None:
    tmux_session(monkeypatch)
    monkeypatch.setenv("WEZTERM_PANE", "9")
    monkeypatch.setattr(autosplit, "resolve_project_dir", lambda cwd, explicit: WORKDIR)
    monkeypatch.setattr(
        autosplit,
        "detect_backend",
        lambda *args, **kwargs: pytest.fail("detection must not run"),
    )
    monkeypatch.setattr(
        autosplit, "_version_text", lambda run, argv: "wezterm 20240203-110809-x\n"
    )

    assert autosplit.main(["--backend", "wezterm", "--dry-run", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["backend"] == "wezterm"
