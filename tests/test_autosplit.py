"""Tests for `ubersight-autosplit` backend detection and command plans.

Plans are plain data, so these tests check the exact commands each backend
would run without splitting a real terminal. The command syntax follows each
tool's documented interface; live verification on each terminal remains an
operator check.
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

from ubersight import autosplit

WORKDIR = Path("/work/project")
DASHBOARD = ("/venv/bin/python", "-m", "ubersight")


def request(**overrides: object) -> autosplit.LayoutRequest:
    """Return a layout request for the test project, with optional overrides."""

    values: dict[str, object] = {
        "working_dir": WORKDIR,
        "dashboard_command": DASHBOARD,
    }
    values.update(overrides)
    return autosplit.LayoutRequest(**values)  # type: ignore[arg-type]


def argvs(plan: autosplit.SplitPlan) -> list[tuple[str, ...]]:
    """Return the argv of every step in a plan."""

    return [step.argv for step in plan.steps]


# Detection ----------------------------------------------------------------

TMUX_ENV = {"TMUX": "/tmp/tmux-1/default,99,0", "TMUX_PANE": "%3"}


def test_tmux_wins_over_the_emulator_around_it() -> None:
    """tmux owns the pane when it is nearer than the emulator around it."""

    env = {**TMUX_ENV, "TERMINATOR_UUID": "urn:uuid:1"}

    assert (
        autosplit.detect_backend(
            env, is_windows=False, ancestor_names=("bash", "tmux: server")
        )
        == autosplit.TMUX
    )


def test_nearest_ancestor_resolves_screen_inside_tmux() -> None:
    """The nearer of two nested multiplexers wins."""

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
    """Both multiplexer markers without ancestry ask for `--backend`."""

    env = {**TMUX_ENV, "STY": "123.pts-0.host"}

    with pytest.raises(autosplit.AutosplitError, match="--backend tmux"):
        autosplit.detect_backend(env, is_windows=False, ancestor_names=())


def test_leaked_emulator_variable_loses_to_nearer_emulator() -> None:
    """A leaked emulator marker loses to the emulator found in the ancestry."""

    env = {"TERMINATOR_UUID": "urn:uuid:1", "KITTY_WINDOW_ID": "4"}

    assert (
        autosplit.detect_backend(
            env, is_windows=False, ancestor_names=("bash", "kitty", "bash")
        )
        == autosplit.KITTY
    )


def test_leaked_tmux_variable_loses_to_the_actual_emulator() -> None:
    """A leaked tmux marker loses to the emulator found in the ancestry."""

    env = {**TMUX_ENV, "WEZTERM_PANE": "7"}

    assert (
        autosplit.detect_backend(
            env, is_windows=False, ancestor_names=("bash", "wezterm-gui")
        )
        == autosplit.WEZTERM
    )


def test_multiplexers_are_allowed_inside_remote_sessions() -> None:
    """tmux and screen running on a remote host work over SSH and mosh."""

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


@pytest.mark.parametrize(
    ("env", "ancestors"),
    [
        # Linux cuts /proc comm at 15 bytes, so this is the name the walk sees.
        ({"SSH_CONNECTION": "a 1 b 22"}, ("bash", "wezterm-mux-ser", "sshd")),
        # A daemonized server started from an SSH shell keeps SSH_TTY.
        ({"SSH_TTY": "/dev/pts/1"}, ("bash", "wezterm-mux-ser")),
    ],
)
def test_wezterm_mux_server_works_on_a_remote_host(
    env: dict[str, str], ancestors: tuple[str, ...]
) -> None:
    """A WezTerm mux server found under its truncated name works remotely."""

    assert (
        autosplit.detect_backend(
            {"WEZTERM_PANE": "2", **env},
            is_windows=False,
            ancestor_names=ancestors,
        )
        == autosplit.WEZTERM
    )


def test_wezterm_mux_server_beats_a_leaked_tmux_variable() -> None:
    """The truncated mux-server name still breaks ties against leaked tmux."""

    assert (
        autosplit.detect_backend(
            {**TMUX_ENV, "WEZTERM_PANE": "2"},
            is_windows=False,
            ancestor_names=("bash", "wezterm-mux-ser"),
        )
        == autosplit.WEZTERM
    )


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("wezterm-mux-ser", autosplit.WEZTERM),
        ("wezterm-mux-server", autosplit.WEZTERM),
        ("wezterm-gui", autosplit.WEZTERM),
        ("wezterm-gui.exe", autosplit.WEZTERM),
        ("wezterm", autosplit.WEZTERM),
        ("weztermish", None),
        ("sshd-session", autosplit.REMOTE_MARKER),
        ("sshd-session.exe", autosplit.REMOTE_MARKER),
        ("WindowsTerminal.exe", autosplit.WINDOWS_TERMINAL),
        ("bash", None),
    ],
)
def test_classify_ancestor(name: str, kind: str | None) -> None:
    """Ancestor names map to a backend, the remote marker, or None."""

    assert autosplit.classify_ancestor(name) == kind


def test_windows_terminal_detected_from_wt_session() -> None:
    """WT_SESSION with a Windows Terminal ancestor selects Windows Terminal."""

    assert (
        autosplit.detect_backend(
            {"WT_SESSION": "abc"},
            is_windows=True,
            ancestor_names=("pwsh.exe", "windowsterminal.exe"),
        )
        == autosplit.WINDOWS_TERMINAL
    )


def test_wt_session_is_ignored_off_windows() -> None:
    """WT_SESSION forwarded into WSL does not select Windows Terminal."""

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
        ({}, ("pwsh.exe", "sshd-session.exe", "sshd.exe"), "ssh"),
    ],
)
def test_remote_sessions_refuse_emulator_backends(
    env: dict[str, str], ancestors: tuple[str, ...], reason: str
) -> None:
    """Remote sessions refuse emulator backends even with leaked markers."""

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


@pytest.mark.parametrize(
    ("env", "is_windows", "ancestors", "named"),
    [
        # `code .` from a Windows Terminal tab passes WT_SESSION to VS Code.
        (
            {"WT_SESSION": "inherited", "TERM_PROGRAM": "vscode"},
            True,
            ("pwsh.exe", "code.exe", "code.exe"),
            "TERM_PROGRAM=vscode",
        ),
        (
            {"KITTY_WINDOW_ID": "4", "TERM_PROGRAM": "vscode"},
            False,
            ("bash", "code"),
            "TERM_PROGRAM=vscode",
        ),
        (
            {
                "TERMINATOR_UUID": "urn:uuid:1",
                "TERMINAL_EMULATOR": "JetBrains-JediTerm",
            },
            False,
            ("bash", "java"),
            "TERMINAL_EMULATOR=JetBrains-JediTerm",
        ),
        ({"TERM_PROGRAM": "vscode"}, False, (), "TERM_PROGRAM=vscode"),
    ],
)
def test_ide_terminals_refuse_inherited_emulator_markers(
    env: dict[str, str], is_windows: bool, ancestors: tuple[str, ...], named: str
) -> None:
    """IDE terminals refuse emulator markers they inherited and suggest --backend."""

    with pytest.raises(autosplit.AutosplitError, match="--backend") as error:
        autosplit.detect_backend(env, is_windows=is_windows, ancestor_names=ancestors)

    assert named in str(error.value)


def test_confirmed_terminals_and_multiplexers_win_inside_ide_terminals() -> None:
    """A terminal ancestor or a multiplexer marker still wins in an IDE shell."""

    vscode = {"TERM_PROGRAM": "vscode"}

    assert (
        autosplit.detect_backend(
            {**vscode, "WT_SESSION": "abc"},
            is_windows=True,
            ancestor_names=("pwsh.exe", "windowsterminal.exe"),
        )
        == autosplit.WINDOWS_TERMINAL
    )
    assert (
        autosplit.detect_backend(
            {**vscode, "KITTY_WINDOW_ID": "4"},
            is_windows=False,
            ancestor_names=("bash", "kitty"),
        )
        == autosplit.KITTY
    )
    assert (
        autosplit.detect_backend(
            {**vscode, "STY": "1.pts-0.host"}, is_windows=False, ancestor_names=()
        )
        == autosplit.SCREEN
    )
    assert (
        autosplit.detect_backend(
            {"TERM_PROGRAM": "WezTerm", "WEZTERM_PANE": "1"},
            is_windows=False,
            ancestor_names=(),
        )
        == autosplit.WEZTERM
    )


def test_stale_tmux_variable_without_pane_is_not_trusted() -> None:
    """TMUX without TMUX_PANE is not trusted as a tmux session."""

    with pytest.raises(autosplit.AutosplitError, match="No supported terminal"):
        autosplit.detect_backend({"TMUX": "stale"}, is_windows=False, ancestor_names=())


def write_proc_stat(proc_root: Path, pid: int, comm: bytes, ppid: int) -> None:
    """Write a minimal `/proc/<pid>/stat` file for one fake process."""

    directory = proc_root / str(pid)
    directory.mkdir()
    (directory / "stat").write_bytes(b"%d (%s) S %d 0 0 0 0\n" % (pid, comm, ppid))


def test_proc_ancestor_walk_reads_parent_chain(tmp_path: Path) -> None:
    """The `/proc` walk follows parent PIDs and skips the process itself."""

    write_proc_stat(tmp_path, 40, b"python3", 30)
    write_proc_stat(tmp_path, 30, b"bash", 20)
    write_proc_stat(tmp_path, 20, b"tmux: server", 1)

    assert autosplit._ancestor_names_from_proc(40, tmp_path) == (
        "bash",
        "tmux: server",
    )


def test_proc_ancestor_walk_survives_a_truncated_utf8_name(tmp_path: Path) -> None:
    """A command name cut mid-character decodes with a replacement character."""

    # The kernel keeps 15 bytes of a name such as "agents-" plus Japanese
    # text, ending partway through a three-byte character.
    write_proc_stat(tmp_path, 50, b"python3", 40)
    write_proc_stat(tmp_path, 40, b"agents-\xe8\xb5\xb7\xe5\x8b\x95\xe3\x82", 30)
    write_proc_stat(tmp_path, 30, b"bash", 1)

    assert autosplit._ancestor_names_from_proc(50, tmp_path) == (
        "agents-\u8d77\u52d5\ufffd",
        "bash",
    )


# Windows Terminal ---------------------------------------------------------

WT_DELIMITER = re.compile(r"^;|[^\\];")
WT_PYTHON = "C:\\Program Files\\Python312\\python.exe"
WT_PROJECT = Path("C:/My Projects/app")
# Passthrough arguments that a naive space join would corrupt, plus `;`.
AWKWARD_PASSTHROUGH = (
    "--repo-root",
    "C:\\My Projects\\app\\",
    "--project-name",
    'my "x"',
    'say"hi',
    "",
    "tab\there",
    "--notes=a;b",
    "a\\;b",
    ";",
    "C:\\plain\\",
    "--no-color",
)
AWKWARD_DASHBOARD = (WT_PYTHON, "-m", "ubersight", "--supervise", *AWKWARD_PASSTHROUGH)


def wt_subcommands(argv: Sequence[str]) -> list[list[str]]:
    """Model how `wt` splits its argv into subcommands at unescaped `;`.

    This follows `AppCommandlineArgs::_addCommandsForArg` and
    `Commandline::AddArg`, which then turn each escaped delimiter back
    into a plain `;`.
    """

    commands: list[list[str]] = [[]]
    for argument in argv[1:]:
        remaining = argument
        while True:
            match = WT_DELIMITER.search(remaining)
            if match is None:
                commands[-1].append(remaining.replace("\\;", ";"))
                break
            head = remaining[: match.end() - 1]
            if head:
                commands[-1].append(head.replace("\\;", ";"))
            commands.append([])
            remaining = remaining[match.end() :]
            if not remaining:
                break
    return commands


def wt_join(arguments: Sequence[str]) -> str:
    """Model how `wt` rebuilds a pane's command line from its arguments.

    `AppCommandlineArgs::_getNewTerminalArgs` joins with spaces and wraps an
    argument in quotes only when it contains a space, escaping nothing.
    """

    return " ".join(
        f'"{argument}"' if " " in argument else argument for argument in arguments
    )


def wt_dashboard_arguments(argv: Sequence[str]) -> list[str]:
    """Return the dashboard arguments `wt` sees in the first `split-pane`."""

    first = wt_subcommands(argv)[0]
    return first[first.index("-d") + 2 :]


def command_line_to_argv(command_line: str) -> list[str]:
    """Split a command line with the Windows `CommandLineToArgvW` API."""

    import ctypes
    from ctypes import wintypes

    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    shell32.CommandLineToArgvW.argtypes = (
        wintypes.LPCWSTR,
        ctypes.POINTER(ctypes.c_int),
    )
    shell32.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = (ctypes.c_void_p,)
    kernel32.LocalFree.restype = ctypes.c_void_p
    count = ctypes.c_int()
    argv = shell32.CommandLineToArgvW(command_line, ctypes.byref(count))
    if not argv:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return [argv[index] for index in range(count.value)]
    finally:
        kernel32.LocalFree(argv)


def test_windows_terminal_plan_chains_layout_in_current_window() -> None:
    """Windows Terminal runs the whole layout as one chained `wt` call."""

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
    """Inserted semicolons are escaped, and a missing profile adds no `-p`."""

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


def test_windows_terminal_plan_survives_the_wt_command_line_join() -> None:
    """`wt`'s naive join of the planned arguments gives a correct command line."""

    plan = autosplit.plan_windows_terminal(
        request(working_dir=WT_PROJECT, dashboard_command=AWKWARD_DASHBOARD), {}
    )

    argv = plan.steps[0].argv
    commands = wt_subcommands(argv)
    assert [command[0] for command in commands] == [
        "-w",
        "move-focus",
        "split-pane",
        "move-focus",
    ]
    first = commands[0]
    assert first[first.index("-d") + 1] == str(WT_PROJECT)
    assert wt_join(wt_dashboard_arguments(argv)) == subprocess.list2cmdline(
        AWKWARD_DASHBOARD
    )


@pytest.mark.skipif(sys.platform != "win32", reason="needs CommandLineToArgvW")
def test_windows_terminal_dashboard_arguments_round_trip_on_windows() -> None:
    """Real Windows parsing gives the dashboard every argument unchanged."""

    plan = autosplit.plan_windows_terminal(
        request(working_dir=WT_PROJECT, dashboard_command=AWKWARD_DASHBOARD), {}
    )
    argv = plan.steps[0].argv

    # subprocess quotes the plan for wt, which splits it back the same way.
    received = command_line_to_argv(subprocess.list2cmdline(argv))
    assert received == list(argv)
    child = command_line_to_argv(wt_join(wt_dashboard_arguments(received)))
    assert child == list(AWKWARD_DASHBOARD)


# tmux ---------------------------------------------------------------------


def test_tmux_plan_splits_invoking_pane_without_moving_focus() -> None:
    """tmux splits the invoking pane twice and then reselects it."""

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


def test_tmux_plan_escapes_hash_formats_in_start_directory() -> None:
    """A `#` in the project path reaches tmux `-c` as `##`, not as a format."""

    plan = autosplit.plan_tmux(
        request(
            working_dir=Path("/work/C#Sharp/x#{session_name}y"),
            dashboard_command=(*DASHBOARD, "--project-name", "C#Sharp"),
        ),
        {"TMUX_PANE": "%7"},
        (3, 4),
    )

    escaped = str(Path("/work/C##Sharp/x##{session_name}y"))
    for step in plan.steps[:2]:
        assert step.argv[step.argv.index("-c") + 1] == escaped
    # The argv after `--` is not format-expanded, so it stays unchanged.
    assert plan.steps[0].argv[-2:] == ("--project-name", "C#Sharp")


def test_tmux_plan_uses_percentage_flag_before_3_1() -> None:
    """tmux before 3.1 sizes splits with `-p`."""

    plan = autosplit.plan_tmux(request(), {"TMUX_PANE": "%1"}, (2, 9))

    assert plan.steps[0].argv[6:8] == ("-p", "30")
    assert plan.steps[1].argv[6:8] == ("-p", "33")


def test_tmux_plan_notes_unknown_version() -> None:
    """An unreadable tmux version assumes 3.1 or newer and says so."""

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
    """`tmux -V` output parses to `(major, minor)`, or None."""

    assert autosplit.parse_tmux_version(text) == version


# GNU screen ---------------------------------------------------------------


def test_screen_plan_builds_regions_and_windows_in_project_dir() -> None:
    """screen builds regions with `-X` and starts windows in the project."""

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
    """screen older than 4.2 is refused."""

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
    """`screen -v` output parses to `(major, minor)`, or None."""

    assert autosplit.parse_screen_version(text) == version


# WezTerm ------------------------------------------------------------------


def test_wezterm_plan_splits_pane_and_restores_focus() -> None:
    """WezTerm splits the invoking pane and then activates it again."""

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
    """Older WezTerm builds skip the focus step, and the oldest are refused."""

    older = autosplit.plan_wezterm(request(), {"WEZTERM_PANE": "1"}, 20221119)

    assert len(older.steps) == 2
    assert "cannot move focus" in older.notes[0]
    with pytest.raises(autosplit.AutosplitError, match="20220624"):
        autosplit.plan_wezterm(request(), {"WEZTERM_PANE": "1"}, 20220101)


def test_parse_wezterm_build() -> None:
    """`wezterm --version` output parses to its build date, or None."""

    assert (
        autosplit.parse_wezterm_build("wezterm 20240203-110809-5046fc22\n") == 20240203
    )
    assert autosplit.parse_wezterm_build("wezterm nightly") is None


# kitty --------------------------------------------------------------------


def test_kitty_plan_switches_to_splits_and_keeps_focus() -> None:
    """kitty switches to the splits layout and launches beside the window."""

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
    """Older kitty releases drop unsupported flags, and the oldest are refused."""

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
    """Terminator uses remotinator and treats `ERROR` output as failure."""

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


def test_terminator_plan_notes_shell_and_custom_command_limits() -> None:
    """The plan warns that the login shell and a custom command can interfere."""

    plan = autosplit.plan_terminator(
        request(), {"TERMINATOR_UUID": "urn:uuid:abc"}, (2, 1, 3)
    )

    assert any(
        "login shell" in note and "&&" in note and "custom command" in note
        for note in plan.notes
    )


def test_terminator_plan_quotes_paths_and_requires_execute_support() -> None:
    """Terminator quotes paths in the `-x` string and needs 2.1.2."""

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
    """Return a fake `run` that answers version probes from a table."""

    def run(args, **kwargs):
        """Return the canned version output for the probed tool."""

        returncode, stdout, stderr = outputs[args[0]]
        return subprocess.CompletedProcess(args, returncode, stdout, stderr)

    return run


def test_build_plan_reads_versions_for_each_backend() -> None:
    """build_plan probes each tool's version and passes it to the plan."""

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
    """A version tool that cannot start leaves a note instead of failing."""

    def missing(args, **kwargs):
        """Fail as if the version tool were not installed."""

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
    """Execution stops at the first failing step and names it."""

    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        """Record the call and fail it."""

        calls.append(args)
        return subprocess.CompletedProcess(args, 1, "", "no server running")

    plan = autosplit.SplitPlan(
        "tmux", (autosplit.SplitStep(("tmux", "a")), autosplit.SplitStep(("tmux", "b")))
    )

    with pytest.raises(autosplit.AutosplitError, match="step 1 of 2 failed"):
        autosplit.execute_plan(plan, run=fake_run, which=lambda name: name)

    assert calls == [["tmux", "a"]]


@pytest.mark.parametrize("interactive", [False, True])
def test_execute_plan_reports_launch_errors_with_the_step(interactive: bool) -> None:
    """An OSError starting a step becomes a step-numbered failure."""

    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        """Succeed once, then fail to start as a vanished directory would."""

        calls.append(args)
        if len(calls) > 1:
            raise FileNotFoundError(2, "No such file or directory", kwargs["cwd"])
        return subprocess.CompletedProcess(args, 0, "", "")

    plan = autosplit.SplitPlan(
        "screen",
        (
            autosplit.SplitStep(("screen", "-X", "split")),
            autosplit.SplitStep(
                ("screen", "-t", "Console"), cwd=WORKDIR, interactive=interactive
            ),
        ),
    )

    with pytest.raises(
        autosplit.AutosplitError, match="step 2 of 2 failed: could not start screen"
    ):
        autosplit.execute_plan(plan, run=fake_run, which=lambda name: name)


def test_execute_plan_treats_remotinator_error_output_as_failure() -> None:
    """`ERROR` output counts as failure even with exit status 0."""

    def fake_run(args, **kwargs):
        """Report a remotinator error with exit status 0."""

        return subprocess.CompletedProcess(args, 0, "ERROR: no such terminal\n", "")

    plan = autosplit.SplitPlan(
        "terminator",
        (autosplit.SplitStep(("remotinator", "vsplit"), failure_prefix="ERROR"),),
    )

    with pytest.raises(autosplit.AutosplitError, match="no such terminal"):
        autosplit.execute_plan(plan, run=fake_run, which=lambda name: name)


def test_execute_plan_inherits_terminal_for_interactive_steps() -> None:
    """Interactive steps inherit the terminal; other steps are captured."""

    seen: list[dict[str, object]] = []

    def fake_run(args, **kwargs):
        """Record the keyword arguments and succeed."""

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
    """A tool missing from PATH is reported before it runs."""

    plan = autosplit.SplitPlan("tmux", (autosplit.SplitStep(("tmux", "a")),))

    with pytest.raises(autosplit.AutosplitError, match="not found on PATH"):
        autosplit.execute_plan(plan, which=lambda name: None)


def test_describe_step_is_shell_neutral() -> None:
    """describe_step renders a step as a `RUN` label with its directory."""

    assert autosplit.describe_step(
        autosplit.SplitStep(("screen", "-t", "Console"), cwd=WORKDIR)
    ) == (f"RUN (in {WORKDIR}): screen -t Console")


# Project directory --------------------------------------------------------


def test_project_dir_defaults_to_git_toplevel(tmp_path: Path) -> None:
    """The project directory defaults to the Git top level."""

    def fake_run(args, **kwargs):
        """Answer the Git top-level query for the subdirectory."""

        assert args == ["git", "rev-parse", "--show-toplevel"]
        assert kwargs["cwd"] == str(tmp_path / "sub")
        return subprocess.CompletedProcess(args, 0, "C:/work/project\n", "")

    assert autosplit.resolve_project_dir(tmp_path / "sub", None, run=fake_run) == Path(
        "C:/work/project"
    )


def test_project_dir_refuses_outside_a_checkout(tmp_path: Path) -> None:
    """Outside a checkout the lookup fails with a message naming `--force`."""

    def fake_run(args, **kwargs):
        """Fail the Git query as if outside a repository."""

        return subprocess.CompletedProcess(args, 128, "", "fatal: not a git repo")

    with pytest.raises(autosplit.AutosplitError, match="--force"):
        autosplit.resolve_project_dir(tmp_path, None, run=fake_run)


def test_explicit_project_dir_needs_no_checkout(tmp_path: Path) -> None:
    """An explicit `--project-dir` skips Git but must exist."""

    def unexpected(args, **kwargs):
        """Fail the test if the Git lookup runs."""

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
    """Fail the project lookup the way `resolve_project_dir` does."""

    if explicit is not None:
        raise autosplit.AutosplitError(f"--project-dir {explicit} is not a directory.")
    raise autosplit.AutosplitError(f"{cwd} is not inside a project checkout.")


def test_dry_run_prints_plan_and_passes_dashboard_arguments(
    monkeypatch, capsys
) -> None:
    """`--dry-run --json` prints the plan with the passthrough arguments."""

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
    """A text dry run prints one `RUN` line per step."""

    tmux_session(monkeypatch)
    monkeypatch.setattr(autosplit, "resolve_project_dir", lambda cwd, explicit: WORKDIR)

    assert autosplit.main(["--dry-run"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 3
    assert all(line.startswith("RUN") for line in lines)


def test_cli_rejects_out_of_range_fractions(capsys) -> None:
    """Layout fractions outside 0.1 to 0.9 are argparse errors."""

    with pytest.raises(SystemExit):
        autosplit.main(["--ubersight-width", "0.95", "--dry-run"])

    assert "between 0.1 and 0.9" in capsys.readouterr().err


def test_cli_rejects_json_without_dry_run(capsys) -> None:
    """`--json` only formats a dry-run plan, so it needs `--dry-run`."""

    with pytest.raises(SystemExit) as exit_info:
        autosplit.main(["--json"])

    assert exit_info.value.code == 2
    assert "--json requires --dry-run" in capsys.readouterr().err


def test_cli_never_runs_programs_from_the_project_directory(monkeypatch) -> None:
    """A planted `git.exe` or `wt.exe` in the checkout must never run."""

    hardened: list[bool] = []
    monkeypatch.setattr(
        autosplit, "harden_windows_executable_search", lambda: hardened.append(True)
    )

    with pytest.raises(SystemExit):
        autosplit.main(["--json"])

    assert hardened == [True]


def test_cli_reports_detection_failures(monkeypatch, capsys) -> None:
    """Detection failures print a message and exit 1."""

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
        "TERM_PROGRAM",
        "TERMINAL_EMULATOR",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(autosplit, "ancestor_process_names", lambda: ())
    monkeypatch.setattr(autosplit, "resolve_project_dir", lambda cwd, explicit: WORKDIR)

    assert autosplit.main(["--dry-run"]) == 1
    assert "No supported terminal" in capsys.readouterr().err


def test_cli_fails_outside_project_without_force(monkeypatch, capsys) -> None:
    """Outside a checkout the CLI fails without printing a plan."""

    tmux_session(monkeypatch)
    monkeypatch.setattr(autosplit, "resolve_project_dir", refuse_project)

    assert autosplit.main(["--dry-run"]) == 1
    captured = capsys.readouterr()
    assert "not inside a project checkout" in captured.err
    assert captured.out == ""


def test_cli_force_warns_and_uses_current_directory(
    monkeypatch, capsys, tmp_path: Path
) -> None:
    """`--force` warns and builds the layout in the current directory."""

    tmux_session(monkeypatch)
    monkeypatch.setattr(autosplit, "resolve_project_dir", refuse_project)
    monkeypatch.chdir(tmp_path)

    assert autosplit.main(["--force", "--dry-run", "--json"]) == 0
    captured = capsys.readouterr()
    assert "warning:" in captured.err and "--force" in captured.err
    first = json.loads(captured.out)["steps"][0]["argv"]
    assert first[first.index("-c") + 1] == str(tmp_path)


def test_cli_force_does_not_excuse_a_missing_project_dir(monkeypatch, capsys) -> None:
    """`--force` does not excuse a `--project-dir` that does not exist."""

    tmux_session(monkeypatch)
    monkeypatch.setattr(autosplit, "resolve_project_dir", refuse_project)

    assert autosplit.main(["--force", "--project-dir", "missing", "--dry-run"]) == 1
    assert "not a directory" in capsys.readouterr().err


def test_explicit_backend_skips_detection(monkeypatch, capsys) -> None:
    """An explicit `--backend` bypasses detection."""

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
