"""Split the current terminal into the Ubersight working layout.

``ubersight-autosplit`` turns the terminal it runs in into three panes:

- the invoking pane stays top-left, normally for an AI agent such as Claude
  or Codex;
- a new utility console takes the bottom third of the left column; and
- a full-height Ubersight dashboard takes the right side of the window.

Focus returns to the invoking pane wherever the backend allows it. Each
supported terminal or multiplexer has a backend that turns the layout into
that tool's own commands. The plan is plain data, so ``--dry-run`` can show
it and tests can check it without touching a real terminal.

Backend detection walks up the process tree and picks the nearest terminal
or multiplexer whose environment variables are present. Variables such as
``TMUX`` or ``TERMINATOR_UUID`` leak into nested sessions, so the process
tree breaks ties. Emulator backends (Windows Terminal, WezTerm GUI, kitty,
Terminator) can only split a window on the machine running the emulator,
so they are refused inside SSH or mosh sessions. Multiplexers running on
the remote host (tmux, GNU screen, WezTerm mux servers) still work there.
IDE terminals such as VS Code's inherit the variables of whatever launched
the IDE, so an emulator marker found there is refused unless the process
tree confirms it.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping, Sequence

from . import windows_processes
from .dashboard import harden_windows_executable_search

DEFAULT_UBERSIGHT_WIDTH = 0.30
DEFAULT_CONSOLE_HEIGHT = 0.33
MIN_FRACTION = 0.10
MAX_FRACTION = 0.90
DASHBOARD_TITLE = "Ubersight"
CONSOLE_TITLE = "Console"

WINDOWS_TERMINAL = "windows-terminal"
TMUX = "tmux"
SCREEN = "screen"
WEZTERM = "wezterm"
KITTY = "kitty"
TERMINATOR = "terminator"
BACKENDS = (TMUX, SCREEN, WEZTERM, KITTY, TERMINATOR, WINDOWS_TERMINAL)
EMULATOR_PRIORITY = (WEZTERM, KITTY, TERMINATOR, WINDOWS_TERMINAL)

# Ancestor process names that mean this shell is running on a remote host.
REMOTE_ANCESTOR_NAMES = {
    "mosh-server": "mosh",
    "sshd": "ssh",
    "sshd.exe": "ssh",
    "sshd-session": "ssh",
    "sshd-session.exe": "ssh",
}
REMOTE_ENVIRONMENT_KEYS = ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY")
REMOTE_MARKER = "remote"
MAX_ANCESTOR_DEPTH = 64

# Minimum tool versions for the commands each plan uses.
SCREEN_MIN_VERSION = (4, 2)
WEZTERM_MIN_SPLIT_BUILD = 20220624
WEZTERM_MIN_ACTIVATE_BUILD = 20230326
KITTY_MIN_SPLITS = (0, 17, 0)
KITTY_MIN_BIAS = (0, 36, 0)
KITTY_MIN_NEXT_TO = (0, 40, 0)
TERMINATOR_MIN_EXECUTE = (2, 1, 2)
REMOTINATOR_ERROR_PREFIX = "ERROR"

RunCommand = Callable[..., "subprocess.CompletedProcess[str]"]


class AutosplitError(RuntimeError):
    """A layout request that cannot be planned or executed safely."""


@dataclass(frozen=True)
class LayoutRequest:
    """What the layout should contain, independent of any backend."""

    working_dir: Path
    dashboard_command: tuple[str, ...]
    ubersight_width: float = DEFAULT_UBERSIGHT_WIDTH
    console_height: float = DEFAULT_CONSOLE_HEIGHT


@dataclass(frozen=True)
class SplitStep:
    """One backend command in a layout plan.

    ``interactive`` steps inherit the terminal's standard streams. GNU screen
    identifies the caller's display from stdin, and kitty can talk to the
    terminal over its tty, so those steps must not be captured.
    ``failure_prefix`` marks output that signals failure despite exit 0.
    """

    argv: tuple[str, ...]
    cwd: Path | None = None
    interactive: bool = False
    failure_prefix: str = ""


@dataclass(frozen=True)
class SplitPlan:
    """Backend commands that build the layout, run in order."""

    backend: str
    steps: tuple[SplitStep, ...]
    notes: tuple[str, ...] = field(default_factory=tuple)


def percent(fraction: float) -> int:
    """Return a layout fraction as a whole percentage."""

    return int(round(fraction * 100))


# Process ancestry -----------------------------------------------------------


def _ancestor_names_from_proc(
    pid: int, proc_root: Path = Path("/proc")
) -> tuple[str, ...]:
    """Return ancestor command names from a Linux-style `/proc` tree.

    The kernel stores the command name as raw bytes cut at 15 bytes, which
    can split a multi-byte character, so the file is decoded leniently.
    """

    names: list[str] = []
    current = pid
    seen: set[int] = set()
    while current > 1 and current not in seen and len(names) < MAX_ANCESTOR_DEPTH:
        seen.add(current)
        try:
            stat = (
                (proc_root / str(current) / "stat")
                .read_bytes()
                .decode("utf-8", errors="replace")
            )
        except (OSError, ValueError):
            break
        open_paren = stat.find("(")
        close_paren = stat.rfind(")")
        if open_paren < 0 or close_paren < open_paren:
            break
        names.append(stat[open_paren + 1 : close_paren])
        fields = stat[close_paren + 2 :].split()
        try:
            current = int(fields[1])
        except (IndexError, ValueError):
            break
    return tuple(names[1:])


def _ancestor_names_from_windows(pid: int) -> tuple[str, ...]:
    """Return ancestor executable names from a Windows process snapshot.

    Windows keeps a creator PID but no live parent link, so a recorded
    parent created after its child is a reused PID and ends the walk.
    """

    entries = {entry.pid: entry for entry in windows_processes.snapshot_processes()}
    names: list[str] = []
    current = entries.get(pid)
    seen: set[int] = set()
    while (
        current is not None
        and current.pid not in seen
        and len(names) < MAX_ANCESTOR_DEPTH
    ):
        seen.add(current.pid)
        parent = entries.get(current.ppid)
        if parent is None or parent.pid == current.pid:
            break
        child_created = windows_processes.probe_process(current.pid).creation_filetime
        parent_created = windows_processes.probe_process(parent.pid).creation_filetime
        if (
            child_created is not None
            and parent_created is not None
            and parent_created > child_created
        ):
            break
        names.append(parent.exe_name)
        current = parent
    return tuple(names)


def ancestor_process_names(pid: int | None = None) -> tuple[str, ...]:
    """Return lower-case names of this process's ancestors, nearest first.

    Hosts without `/proc`, such as macOS, return no names, and detection
    then relies on environment variables alone.
    """

    target = os.getpid() if pid is None else pid
    if os.name == "nt":
        names = _ancestor_names_from_windows(target)
    else:
        names = _ancestor_names_from_proc(target)
    return tuple(name.lower() for name in names)


def classify_ancestor(name: str) -> str | None:
    """Map an ancestor process name to a backend, the remote marker, or None.

    WezTerm matches by prefix because Linux cuts `/proc` command names at
    15 bytes, so `wezterm-mux-server` appears as `wezterm-mux-ser`.
    """

    lowered = name.lower()
    if lowered in REMOTE_ANCESTOR_NAMES:
        return REMOTE_MARKER
    if lowered.startswith("tmux"):
        return TMUX
    if lowered.startswith("screen"):
        return SCREEN
    stem = lowered.removesuffix(".exe")
    if stem == "wezterm" or stem.startswith("wezterm-"):
        return WEZTERM
    if lowered == "kitty":
        return KITTY
    if lowered == "terminator":
        return TERMINATOR
    if lowered == "windowsterminal.exe":
        return WINDOWS_TERMINAL
    return None


# Detection ------------------------------------------------------------------


def remote_session_reason(env: Mapping[str, str], ancestor_names: Sequence[str]) -> str:
    """Return `ssh` or `mosh` when this shell runs on a remote host, else ``""``.

    mosh keeps the bootstrap connection's `SSH_*` variables, so those
    variables cover both. The ancestor check also catches sessions whose
    environment was cleaned.
    """

    for name in ancestor_names:
        reason = REMOTE_ANCESTOR_NAMES.get(name.lower())
        if reason:
            return reason
    if any(env.get(key) for key in REMOTE_ENVIRONMENT_KEYS):
        return "ssh"
    return ""


def unsupported_terminal_reason(env: Mapping[str, str]) -> str:
    """Name the unsupported IDE terminal hosting this shell, else ``""``.

    VS Code (and its forks) and JetBrains IDEs inherit the environment of
    the terminal that launched them, so a marker such as `WT_SESSION` or
    `KITTY_WINDOW_ID` there points at a different window.
    """

    if env.get("TERM_PROGRAM", "").strip().lower() == "vscode":
        return "the VS Code integrated terminal (TERM_PROGRAM=vscode)"
    emulator = env.get("TERMINAL_EMULATOR", "").strip()
    if emulator:
        return f"an IDE terminal (TERMINAL_EMULATOR={emulator})"
    return ""


def available_backends(env: Mapping[str, str], *, is_windows: bool) -> set[str]:
    """Return backends whose identifying environment variables are present."""

    present: set[str] = set()
    if env.get("TMUX") and env.get("TMUX_PANE"):
        present.add(TMUX)
    if env.get("STY"):
        present.add(SCREEN)
    if env.get("WEZTERM_PANE"):
        present.add(WEZTERM)
    if env.get("KITTY_WINDOW_ID"):
        present.add(KITTY)
    if env.get("TERMINATOR_UUID"):
        present.add(TERMINATOR)
    if is_windows and env.get("WT_SESSION"):
        present.add(WINDOWS_TERMINAL)
    return present


def detect_backend(
    env: Mapping[str, str],
    *,
    is_windows: bool,
    ancestor_names: Sequence[str],
) -> str:
    """Choose the backend that owns the invoking pane.

    The nearest terminal or multiplexer ancestor wins when its variables are
    present, which resolves leaked variables and nesting such as screen
    inside tmux. Crossing an SSH or mosh boundary first means no local
    emulator is reachable. Without ancestry, multiplexers are preferred and
    emulators are refused in remote sessions and in unsupported IDE
    terminals, where any emulator marker was inherited from elsewhere.
    """

    present = available_backends(env, is_windows=is_windows)
    for name in ancestor_names:
        kind = classify_ancestor(name)
        if kind == REMOTE_MARKER:
            break
        if kind is not None and kind in present:
            return kind

    multiplexers = [backend for backend in (TMUX, SCREEN) if backend in present]
    if len(multiplexers) == 2:
        raise AutosplitError(
            "Both tmux and GNU screen variables are set and the nesting could "
            "not be determined. Pass --backend tmux or --backend screen."
        )
    if multiplexers:
        return multiplexers[0]
    remote = remote_session_reason(env, ancestor_names)
    if remote:
        raise AutosplitError(
            f"This shell is running over {remote}, so the local terminal "
            "window cannot be split from here. Run ubersight-autosplit inside "
            "tmux or GNU screen on this host, or in a local terminal."
        )
    unsupported = unsupported_terminal_reason(env)
    if unsupported:
        raise AutosplitError(
            f"This shell is running in {unsupported}, which ubersight-autosplit "
            "cannot split; any terminal variables here were probably inherited "
            "from the terminal that launched it. Run ubersight-autosplit in a "
            "supported terminal, or pass --backend to choose one explicitly."
        )
    for backend in EMULATOR_PRIORITY:
        if backend in present:
            return backend
    raise AutosplitError(
        "No supported terminal was detected. Supported: Windows Terminal, "
        "tmux, GNU screen, WezTerm, kitty, and Terminator. Pass --backend to "
        "choose one explicitly."
    )


# Versions -------------------------------------------------------------------


def _version_text(run: RunCommand, argv: Sequence[str]) -> str:
    """Return a tool's combined version output, or an empty string.

    Some tools, including GNU screen, print their version with a nonzero
    exit status, so the status is ignored.
    """

    try:
        completed = run(
            list(argv),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=5.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return f"{completed.stdout or ''}\n{completed.stderr or ''}"


def parse_tmux_version(text: str) -> tuple[int, int] | None:
    """Return `(major, minor)` from `tmux -V` output such as `tmux 3.3a`."""

    match = re.search(r"(\d+)\.(\d+)", text)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2))


def parse_screen_version(text: str) -> tuple[int, int] | None:
    """Return `(major, minor)` from `Screen version 4.09.01 (GNU) ...`."""

    match = re.search(r"[Vv]ersion\s+(\d+)\.(\d+)", text)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2))


def parse_wezterm_build(text: str) -> int | None:
    """Return the build date from `wezterm 20240203-110809-5046fc22`."""

    match = re.search(r"\b(\d{8})-\d{6}", text)
    return int(match.group(1)) if match else None


def parse_semver(text: str) -> tuple[int, int, int] | None:
    """Return the first `major.minor.patch` triple found in `text`."""

    match = re.search(r"(\d+)\.(\d+)\.(\d+)", text)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


# Plans ----------------------------------------------------------------------


def _escape_windows_terminal_argument(argument: str) -> str:
    """Escape `;`, which `wt` treats as a subcommand delimiter in any argument."""

    return argument.replace(";", "\\;")


def _quote_for_windows_terminal_join(argument: str) -> str:
    """Pre-quote one dashboard argument for `wt`'s naive command-line join.

    `wt` rebuilds the pane's command line by joining its arguments with
    spaces and wrapping an argument in quotes only when it contains a space,
    without escaping embedded quotes or trailing backslashes. Encoding each
    argument with the MSVC rules first makes that join produce a correct
    command line. For an argument with a space, the outer quotes are left
    for `wt` to add.
    """

    quoted = subprocess.list2cmdline([argument])
    if " " in argument:
        return quoted[1:-1]
    return quoted


def plan_windows_terminal(request: LayoutRequest, env: Mapping[str, str]) -> SplitPlan:
    """Plan the layout as one chained `wt` invocation in the current window.

    `split-pane -s` sizes the new pane. Reusing `WT_PROFILE_ID` opens both
    new panes with the invoking pane's profile. `-w 0` means the most
    recently used window, which is the one the operator is typing in.
    """

    profile = env.get("WT_PROFILE_ID", "").strip()
    profile_args = ("-p", profile) if profile else ()
    # `-d` is a wt option, so only the dashboard argv goes through the join.
    working_dir = _escape_windows_terminal_argument(str(request.working_dir))
    dashboard = tuple(
        _escape_windows_terminal_argument(_quote_for_windows_terminal_join(argument))
        for argument in request.dashboard_command
    )
    argv = (
        "wt",
        "-w",
        "0",
        "split-pane",
        "-V",
        "-s",
        f"{request.ubersight_width:g}",
        *profile_args,
        "--title",
        DASHBOARD_TITLE,
        "-d",
        working_dir,
        *dashboard,
        ";",
        "move-focus",
        "left",
        ";",
        "split-pane",
        "-H",
        "-s",
        f"{request.console_height:g}",
        *profile_args,
        "-d",
        working_dir,
        ";",
        "move-focus",
        "up",
    )
    return SplitPlan(WINDOWS_TERMINAL, (SplitStep(argv),))


def _tmux_size_args(
    fraction: float, version: tuple[int, int] | None
) -> tuple[str, ...]:
    """Return tmux size flags; `-l N%` needs tmux 3.1, older uses `-p N`."""

    if version is not None and version < (3, 1):
        return ("-p", str(percent(fraction)))
    return ("-l", f"{percent(fraction)}%")


def plan_tmux(
    request: LayoutRequest,
    env: Mapping[str, str],
    version: tuple[int, int] | None,
) -> SplitPlan:
    """Plan the layout as two tmux splits of the invoking pane.

    Both splits target `$TMUX_PANE` and use `-d`, so focus stays on the
    invoking pane; a final `select-pane` makes that explicit. The right split
    runs first so the dashboard spans the original pane's full height. The
    dashboard argv follows `--`, so tmux runs it directly instead of through
    the user's `default-shell`, which may not be POSIX.
    """

    pane = env.get("TMUX_PANE", "").strip()
    if not pane:
        raise AutosplitError("The tmux backend needs TMUX_PANE; run inside tmux.")
    # tmux expands `#` formats in -c and silently falls back to $HOME when
    # the result does not exist; `##` is a literal `#`. The argv after `--`
    # is not expanded.
    working_dir = str(request.working_dir).replace("#", "##")
    steps = (
        SplitStep(
            (
                "tmux",
                "split-window",
                "-h",
                "-d",
                "-t",
                pane,
                *_tmux_size_args(request.ubersight_width, version),
                "-c",
                working_dir,
                "--",
                *request.dashboard_command,
            )
        ),
        SplitStep(
            (
                "tmux",
                "split-window",
                "-v",
                "-d",
                "-t",
                pane,
                *_tmux_size_args(request.console_height, version),
                "-c",
                working_dir,
            )
        ),
        SplitStep(("tmux", "select-pane", "-t", pane)),
    )
    notes = ()
    if version is None:
        notes = ("Could not read the tmux version; assuming tmux 3.1 or newer.",)
    return SplitPlan(TMUX, steps, notes)


def plan_screen(request: LayoutRequest, version: tuple[int, int] | None) -> SplitPlan:
    """Plan the layout as GNU screen region commands.

    Region changes go through `screen -X`. New windows are created with a
    plain `screen` call started in the project directory. That call passes
    its exact argv and working directory to the session, whereas
    `-X chdir` would permanently change the server's directory and `-X`
    re-parses `$` and backslashes. Every step inherits the terminal so screen
    can match the caller's display by tty.
    """

    if version is not None and version < SCREEN_MIN_VERSION:
        raise AutosplitError(
            "GNU screen 4.2 or newer is needed for vertical splits; found "
            f"{version[0]}.{version[1]}."
        )

    def command(*argv: str, cwd: Path | None = None) -> SplitStep:
        """Return a screen step that inherits the caller's terminal."""

        return SplitStep(tuple(argv), cwd=cwd, interactive=True)

    working_dir = request.working_dir
    steps = (
        command("screen", "-X", "split", "-v"),
        command("screen", "-X", "focus", "right"),
        command(
            "screen",
            "-t",
            DASHBOARD_TITLE,
            *request.dashboard_command,
            cwd=working_dir,
        ),
        command("screen", "-X", "resize", "-h", f"{percent(request.ubersight_width)}%"),
        command("screen", "-X", "focus", "left"),
        command("screen", "-X", "split"),
        command("screen", "-X", "focus", "down"),
        command("screen", "-t", CONSOLE_TITLE, cwd=working_dir),
        command("screen", "-X", "resize", "-v", f"{percent(request.console_height)}%"),
        command("screen", "-X", "focus", "up"),
    )
    notes = [
        "GNU screen reports success even when a -X command fails; check the "
        "layout visually.",
    ]
    if version is None:
        notes.append("Could not read the screen version; assuming 4.2 or newer.")
    return SplitPlan(SCREEN, steps, tuple(notes))


def plan_wezterm(
    request: LayoutRequest, env: Mapping[str, str], build: int | None
) -> SplitPlan:
    """Plan the layout as `wezterm cli` splits of the invoking pane.

    WezTerm activates each new pane, so a final `activate-pane` returns
    focus when the installed build supports it.
    """

    pane = env.get("WEZTERM_PANE", "").strip()
    if not pane:
        raise AutosplitError("The WezTerm backend needs WEZTERM_PANE.")
    if build is not None and build < WEZTERM_MIN_SPLIT_BUILD:
        raise AutosplitError(
            "WezTerm 20220624 or newer is needed for directional splits; "
            f"found {build}."
        )
    working_dir = str(request.working_dir)
    steps = [
        SplitStep(
            (
                "wezterm",
                "cli",
                "split-pane",
                "--pane-id",
                pane,
                "--right",
                "--percent",
                str(percent(request.ubersight_width)),
                "--cwd",
                working_dir,
                "--",
                *request.dashboard_command,
            )
        ),
        SplitStep(
            (
                "wezterm",
                "cli",
                "split-pane",
                "--pane-id",
                pane,
                "--bottom",
                "--percent",
                str(percent(request.console_height)),
                "--cwd",
                working_dir,
            )
        ),
    ]
    notes: list[str] = []
    if build is not None and build < WEZTERM_MIN_ACTIVATE_BUILD:
        notes.append("This WezTerm build cannot move focus; select the top-left pane.")
    else:
        steps.append(SplitStep(("wezterm", "cli", "activate-pane", "--pane-id", pane)))
    if build is None:
        notes.append("Could not read the WezTerm version; assuming a current build.")
    return SplitPlan(WEZTERM, tuple(steps), tuple(notes))


def plan_kitty(
    request: LayoutRequest,
    env: Mapping[str, str],
    version: tuple[int, int, int] | None,
    *,
    use_kitten: bool,
) -> SplitPlan:
    """Plan the layout with kitty remote control in the splits layout.

    `vsplit` and `hsplit` only work in the splits layout, so the tab
    switches to it first. `--bias` gives the new window its share, and
    `--keep-focus` leaves focus on the invoking window. Remote control must
    be enabled with `allow_remote_control` in kitty.conf.
    """

    window_id = env.get("KITTY_WINDOW_ID", "").strip()
    if not window_id:
        raise AutosplitError("The kitty backend needs KITTY_WINDOW_ID.")
    if version is not None and version < KITTY_MIN_SPLITS:
        raise AutosplitError("kitty 0.17 or newer is needed for the splits layout.")
    control = ("kitten", "@") if use_kitten else ("kitty", "@")
    notes: list[str] = []
    bias_supported = version is None or version >= KITTY_MIN_BIAS
    next_to = (
        (f"--next-to=id:{window_id}",)
        if version is None or version >= KITTY_MIN_NEXT_TO
        else ()
    )
    if not bias_supported:
        notes.append("kitty before 0.36 cannot size new windows; splits are 50/50.")
    if version is None:
        notes.append("Could not read the kitty version; assuming a current release.")

    def launch(location: str, fraction: float, *extra: str) -> SplitStep:
        """Return a `launch` step that splits the invoking kitty window."""

        bias = (f"--bias={percent(fraction)}",) if bias_supported else ()
        return SplitStep(
            (
                *control,
                "launch",
                "--type=window",
                "--self",
                *next_to,
                f"--location={location}",
                *bias,
                "--keep-focus",
                f"--cwd={request.working_dir}",
                *extra,
            ),
            interactive=True,
        )

    steps = (
        SplitStep(
            (*control, "goto-layout", "--match", f"window_id:{window_id}", "splits"),
            interactive=True,
        ),
        launch(
            "vsplit",
            request.ubersight_width,
            f"--title={DASHBOARD_TITLE}",
            "--",
            *request.dashboard_command,
        ),
        launch("hsplit", request.console_height, f"--title={CONSOLE_TITLE}"),
    )
    notes.append(
        "kitty needs allow_remote_control enabled in kitty.conf; the tab now "
        "uses the splits layout."
    )
    return SplitPlan(KITTY, steps, tuple(notes))


def plan_terminator(
    request: LayoutRequest,
    env: Mapping[str, str],
    version: tuple[int, int, int] | None,
) -> SplitPlan:
    """Plan the layout with Terminator's `remotinator` D-Bus helper.

    `vsplit` creates side-by-side panes and `hsplit` stacked ones; both
    target the invoking terminal by UUID. Terminator splits are always
    even, focus moves to each new pane, and failures are printed with exit
    status 0, so output starting with `ERROR` is treated as failure.

    Terminator runs the `-x` string through the user's login shell as
    `$SHELL -c`, so it needs a shell that accepts `&&`. A default profile
    with `use_custom_command` enabled replaces the string entirely. Both
    limits are noted in the plan rather than worked around.
    """

    uuid = env.get("TERMINATOR_UUID", "").strip()
    if not uuid:
        raise AutosplitError("The Terminator backend needs TERMINATOR_UUID.")
    if version is not None and version < TERMINATOR_MIN_EXECUTE:
        raise AutosplitError(
            "Terminator 2.1.2 or newer is needed for remotinator --execute; "
            "found {}.{}.{}.".format(*version)
        )
    dashboard = (
        f"cd {shlex.quote(str(request.working_dir))} && "
        f"exec {shlex.join(request.dashboard_command)}"
    )
    steps = (
        SplitStep(
            (
                "remotinator",
                "vsplit",
                "-u",
                uuid,
                "-T",
                DASHBOARD_TITLE,
                "-x",
                dashboard,
            ),
            failure_prefix=REMOTINATOR_ERROR_PREFIX,
        ),
        SplitStep(
            ("remotinator", "hsplit", "-u", uuid, "-T", CONSOLE_TITLE),
            failure_prefix=REMOTINATOR_ERROR_PREFIX,
        ),
    )
    notes = [
        "Terminator splits are always 50/50 and focus moves to the newest "
        "pane; select the top-left pane to continue.",
        "The console pane starts in the invoking terminal's directory.",
        "The dashboard command runs through your login shell, which must "
        "accept `&&` (nushell and elvish do not), and a default profile with "
        "a custom command replaces it.",
    ]
    if version is None:
        notes.append("Could not read the Terminator version; assuming 2.1.2 or newer.")
    return SplitPlan(TERMINATOR, steps, tuple(notes))


def build_plan(
    backend: str,
    request: LayoutRequest,
    env: Mapping[str, str],
    *,
    run: RunCommand = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
) -> SplitPlan:
    """Return the backend-specific command plan for a layout request."""

    if backend == WINDOWS_TERMINAL:
        return plan_windows_terminal(request, env)
    if backend == TMUX:
        return plan_tmux(
            request, env, parse_tmux_version(_version_text(run, ["tmux", "-V"]))
        )
    if backend == SCREEN:
        return plan_screen(
            request, parse_screen_version(_version_text(run, ["screen", "-v"]))
        )
    if backend == WEZTERM:
        return plan_wezterm(
            request,
            env,
            parse_wezterm_build(_version_text(run, ["wezterm", "--version"])),
        )
    if backend == KITTY:
        use_kitten = which("kitten") is not None
        version_tool = "kitten" if use_kitten else "kitty"
        return plan_kitty(
            request,
            env,
            parse_semver(_version_text(run, [version_tool, "--version"])),
            use_kitten=use_kitten,
        )
    if backend == TERMINATOR:
        return plan_terminator(
            request,
            env,
            parse_semver(_version_text(run, ["terminator", "--version"])),
        )
    raise AutosplitError(f"Unsupported backend: {backend}")


# Execution ------------------------------------------------------------------


def execute_plan(
    plan: SplitPlan,
    *,
    run: RunCommand = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
) -> None:
    """Run each plan step, stopping with a clear error at the first failure.

    Panes created before a failure are left in place; closing them could
    discard work the operator has already started there.
    """

    for index, step in enumerate(plan.steps, start=1):
        if which(step.argv[0]) is None:
            raise AutosplitError(f"{step.argv[0]} was not found on PATH.")
        cwd = str(step.cwd) if step.cwd is not None else None
        # A tool found on PATH can still fail to start, for example a broken
        # `wt.exe` app-execution alias or a working directory that vanished.
        try:
            if step.interactive:
                completed = run(list(step.argv), cwd=cwd, check=False)
                output = ""
            else:
                completed = run(
                    list(step.argv),
                    cwd=cwd,
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    encoding="utf-8",
                    errors="replace",
                    check=False,
                )
                output = f"{completed.stdout or ''}{completed.stderr or ''}".strip()
        except OSError as error:
            raise AutosplitError(
                f"{plan.backend} step {index} of {len(plan.steps)} failed: "
                f"could not start {step.argv[0]}: {error}"
            ) from error
        failed_by_output = bool(step.failure_prefix) and output.startswith(
            step.failure_prefix
        )
        if completed.returncode != 0 or failed_by_output:
            raise AutosplitError(
                f"{plan.backend} step {index} of {len(plan.steps)} failed "
                f"with exit {completed.returncode}: {output or 'no output'}"
            )


def describe_step(step: SplitStep) -> str:
    """Return a shell-neutral, human-readable line for one plan step."""

    location = f" (in {step.cwd})" if step.cwd is not None else ""
    return f"RUN{location}: {shlex.join(step.argv)}"


# Command line ---------------------------------------------------------------


def resolve_project_dir(
    cwd: Path,
    explicit: Path | None,
    *,
    run: RunCommand = subprocess.run,
) -> Path:
    """Return the project directory the new panes should start in.

    Ubersight reads `.local/ubersight/status.json` relative to its working
    directory, so the dashboard must start at the project root. An explicit
    directory is trusted as given. Otherwise the enclosing Git checkout's
    top level is used, and running outside any checkout is refused rather
    than building a layout around a dashboard with no project.
    """

    if explicit is not None:
        project = (cwd / explicit).resolve()
        if not project.is_dir():
            raise AutosplitError(f"--project-dir {explicit} is not a directory.")
        return project
    try:
        completed = run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(cwd),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=10.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        completed = None
    toplevel = completed.stdout.strip() if completed is not None else ""
    if completed is None or completed.returncode != 0 or not toplevel:
        raise AutosplitError(
            f"{cwd} is not inside a project checkout. Run ubersight-autosplit "
            "from your project, pass --project-dir PATH, or pass --force to "
            "use this directory anyway."
        )
    return Path(toplevel)


def _fraction(value: str) -> float:
    """Parse a layout fraction between 0.1 and 0.9."""

    try:
        fraction = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number such as 0.30") from exc
    if not MIN_FRACTION <= fraction <= MAX_FRACTION:
        raise argparse.ArgumentTypeError(
            f"must be between {MIN_FRACTION:g} and {MAX_FRACTION:g}"
        )
    return fraction


def build_parser() -> argparse.ArgumentParser:
    """Create the `ubersight-autosplit` command-line parser."""

    parser = argparse.ArgumentParser(
        prog="ubersight-autosplit",
        description=(
            "Split the current terminal: keep this pane top-left, add a "
            "utility console below it, and run Ubersight full height on the "
            "right. Arguments after `--` are passed to ubersight."
        ),
    )
    parser.add_argument(
        "--backend",
        choices=("auto", *BACKENDS),
        default="auto",
        help="terminal backend; auto-detected by default",
    )
    parser.add_argument(
        "--ubersight-width",
        type=_fraction,
        default=DEFAULT_UBERSIGHT_WIDTH,
        help="fraction of the window width for the Ubersight pane (default 0.30)",
    )
    parser.add_argument(
        "--console-height",
        type=_fraction,
        default=DEFAULT_CONSOLE_HEIGHT,
        help="fraction of the left column for the console pane (default 0.33)",
    )
    parser.add_argument(
        "--project-dir",
        type=Path,
        default=None,
        help=(
            "project directory for the console and dashboard panes; default "
            "is the Git checkout containing the current directory"
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="build the layout in the current directory even outside a checkout",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the backend commands without running them",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="with --dry-run, print the plan as JSON",
    )
    parser.add_argument(
        "ubersight_args",
        nargs=argparse.REMAINDER,
        help="arguments after `--` are passed to the ubersight dashboard",
    )
    return parser


def _plan_as_json(plan: SplitPlan) -> str:
    """Return a machine-readable rendering of a plan for `--dry-run --json`."""

    return json.dumps(
        {
            "backend": plan.backend,
            "steps": [
                {
                    "argv": list(step.argv),
                    "cwd": str(step.cwd) if step.cwd is not None else None,
                    "interactive": step.interactive,
                }
                for step in plan.steps
            ],
            "notes": list(plan.notes),
        },
        indent=2,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Plan and run the Ubersight working layout for the current terminal."""

    # `git`, `wt`, and `shutil.which` would otherwise find a program planted
    # in the project directory before the real one on Windows.
    harden_windows_executable_search()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.json and not args.dry_run:
        parser.error("--json requires --dry-run")
    passthrough = list(args.ubersight_args)
    if passthrough[:1] == ["--"]:
        passthrough = passthrough[1:]
    env = dict(os.environ)
    try:
        try:
            working_dir = resolve_project_dir(Path.cwd(), args.project_dir)
        except AutosplitError as error:
            # --force only relaxes the checkout requirement; an explicit
            # --project-dir that does not exist is still an error.
            if not args.force or args.project_dir is not None:
                raise
            print(
                f"[ubersight-autosplit] warning: {error} Continuing because "
                "--force was given.",
                file=sys.stderr,
            )
            working_dir = Path.cwd()
        request = LayoutRequest(
            working_dir=working_dir,
            # The supervisor keeps the pane alive across `ubersight-stop`
            # restart requests and crashes; Q still closes it.
            dashboard_command=(
                sys.executable,
                "-m",
                "ubersight",
                "--supervise",
                *passthrough,
            ),
            ubersight_width=args.ubersight_width,
            console_height=args.console_height,
        )
        backend = args.backend
        if backend == "auto":
            backend = detect_backend(
                env,
                is_windows=os.name == "nt",
                ancestor_names=ancestor_process_names(),
            )
        plan = build_plan(backend, request, env)
        if args.dry_run:
            if args.json:
                print(_plan_as_json(plan))
            else:
                for note in plan.notes:
                    print(f"NOTE: {note}")
                for step in plan.steps:
                    print(describe_step(step))
            return 0
        for note in plan.notes:
            print(f"[ubersight-autosplit] {note}", file=sys.stderr)
        execute_plan(plan)
    except AutosplitError as error:
        print(f"[ubersight-autosplit] {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
