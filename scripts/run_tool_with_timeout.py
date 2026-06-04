"""Run standard quality tools with timeout enforcement."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

try:
    from tool_execution_constraints import (
        load_execution_constraints,
        serial_execution_constraint_label,
    )
    from tool_validation_profiles import (
        black_profiles,
        discover_black_paths,
        group_black_paths_by_profile,
        resolve_runtime_policy_executable,
    )
except ImportError:  # pragma: no cover - import path varies by entry point.
    from scripts.tool_execution_constraints import (
        load_execution_constraints,
        serial_execution_constraint_label,
    )
    from scripts.tool_validation_profiles import (
        black_profiles,
        discover_black_paths,
        group_black_paths_by_profile,
        resolve_runtime_policy_executable,
    )


CONFIG_FILE = Path(__file__).resolve().with_name("tool_timeouts.json")
ENSURE_TOOL_RUNTIME_SCRIPT = (
    Path(__file__).resolve().with_name("ensure_theknowledge_tool_runtime.py")
)
EXECUTION_CONSTRAINTS_FILE = "tool_execution_constraints.json"
BLACK_INCLUDE_IGNORED_FLAG = "--include-ignored"
BLACK_LONG_OPTIONS_WITH_VALUE = {
    "--code",
    "--config",
    "--exclude",
    "--extend-exclude",
    "--force-exclude",
    "--ipynb",
    "--line-length",
    "--line-ranges",
    "--python-cell-magics",
    "--required-version",
    "--stdin-filename",
    "--target-version",
    "--workers",
}
BLACK_SHORT_OPTIONS_WITH_VALUE = {
    "-c",
    "-l",
    "-t",
    "-W",
}


def _load_config() -> Dict[str, object]:
    with CONFIG_FILE.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("timeout configuration must be a JSON object")
    return data


def _is_theknowledge_direct_checkout(repo_root: Path) -> bool:
    return (repo_root / "internal" / "overrides" / "README.txt").is_file()


def _configure_direct_checkout_runtime(repo_root: Path) -> None:
    if not _is_theknowledge_direct_checkout(repo_root):
        return
    if os.environ.get("THEKNOWLEDGE_PYTHON_TOOLS"):
        return
    result = subprocess.run(
        [sys.executable, str(ENSURE_TOOL_RUNTIME_SCRIPT), "--print-python"],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(
            "failed to ensure the direct-checkout tool runtime: {}".format(message)
        )
    runtime_python = (result.stdout or "").strip()
    if not runtime_python:
        raise RuntimeError("tool-runtime helper did not print an interpreter path")
    os.environ["THEKNOWLEDGE_PYTHON_TOOLS"] = runtime_python
    runtime_bin = str(Path(runtime_python).resolve().parent)
    current_path = os.environ.get("PATH", "")
    path_parts = current_path.split(os.pathsep) if current_path else []
    if runtime_bin not in path_parts:
        os.environ["PATH"] = (
            runtime_bin + os.pathsep + current_path if current_path else runtime_bin
        )


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a configured tool command with a timeout bailout."
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=None,
        help="Override timeout in seconds for this invocation.",
    )
    parser.add_argument(
        "tool", help="Configured tool key in scripts/tool_timeouts.json"
    )
    parsed, tool_args = parser.parse_known_args(argv)
    parsed.tool_args = tool_args
    return parsed


def _resolve_tool_run(
    config: Dict[str, object],
    tool: str,
    override_timeout: int | None,
    tool_args: List[str],
) -> Tuple[List[str], int, int, List[str]]:
    tools = config.get("tools")
    if not isinstance(tools, dict):
        raise ValueError("configuration must include a 'tools' object")

    entry = tools.get(tool)
    if not isinstance(entry, dict):
        available = ", ".join(sorted(tools))
        raise KeyError(f"unknown tool '{tool}'. Available tools: {available}")

    command = entry.get("command")
    if not isinstance(command, list) or not all(
        isinstance(part, str) for part in command
    ):
        raise ValueError(f"tool '{tool}' must define command as a list of strings")

    use_wrapper_python = entry.get("use_wrapper_python", False)
    if not isinstance(use_wrapper_python, bool):
        raise ValueError("use_wrapper_python must be a boolean")

    runtime_policy = entry.get("runtime_policy")
    if runtime_policy is not None and not isinstance(runtime_policy, str):
        raise ValueError("runtime_policy must be a string when present")

    replace_default_scope_with_args = entry.get(
        "replace_default_scope_with_args", False
    )
    if not isinstance(replace_default_scope_with_args, bool):
        raise ValueError("replace_default_scope_with_args must be a boolean")

    timeout = override_timeout
    if timeout is None:
        configured_timeout = entry.get("timeout_seconds")
        if isinstance(configured_timeout, int) and configured_timeout > 0:
            timeout = configured_timeout
        else:
            default_timeout = config.get("default_timeout_seconds")
            if not isinstance(default_timeout, int) or default_timeout <= 0:
                raise ValueError("default_timeout_seconds must be a positive integer")
            timeout = default_timeout

    if timeout <= 0:
        raise ValueError("timeout must be a positive integer")

    retries = entry.get("timeout_retries", 0)
    if not isinstance(retries, int) or retries < 0:
        raise ValueError("timeout_retries must be a non-negative integer")

    cleanup_patterns = entry.get("cleanup_patterns", [])
    if not isinstance(cleanup_patterns, list) or not all(
        isinstance(pattern, str) for pattern in cleanup_patterns
    ):
        raise ValueError("cleanup_patterns must be a list of strings")

    args = list(tool_args)
    if args and args[0] == "--":
        args = args[1:]

    resolved_command = list(command)
    if args and replace_default_scope_with_args:
        placeholder_indexes = [
            index for index, part in enumerate(resolved_command) if part == "."
        ]
        if len(placeholder_indexes) != 1:
            raise ValueError(
                f"tool '{tool}' must contain exactly one '.' scope placeholder "
                "when replace_default_scope_with_args is enabled"
            )
        placeholder_index = placeholder_indexes[0]
        resolved_command = (
            resolved_command[:placeholder_index]
            + args
            + resolved_command[placeholder_index + 1 :]
        )
        args = []

    resolved_command += args
    if runtime_policy and resolved_command[:1] == ["python"]:
        resolved_command[0] = resolve_runtime_policy_executable(
            Path.cwd(), runtime_policy
        )
    elif use_wrapper_python and resolved_command[:1] == ["python"]:
        resolved_command[0] = sys.executable

    return resolved_command, timeout, retries, cleanup_patterns


def _cleanup_processes(patterns: List[str]) -> None:
    if not patterns:
        return
    if os.name != "posix":
        return

    for pattern in patterns:
        try:
            subprocess.run(["pkill", "-f", pattern], check=False)
            print(f"[timeout-wrapper] cleanup attempted for pattern: {pattern}")
        except FileNotFoundError:
            print("[timeout-wrapper] cleanup skipped; pkill not available.")
            return


def _run_command_with_timeout(
    command: List[str],
    *,
    timeout: int,
    retries: int,
    cleanup_patterns: List[str],
) -> int:
    pretty = " ".join(shlex.quote(part) for part in command)
    print(f"[timeout-wrapper] running: {pretty}")
    print(f"[timeout-wrapper] timeout: {timeout}s")

    attempts = retries + 1
    for attempt in range(1, attempts + 1):
        try:
            result = subprocess.run(command, check=False, timeout=timeout)
            return result.returncode
        except subprocess.TimeoutExpired:
            print(
                f"[timeout-wrapper] timeout exceeded after {timeout}s "
                f"(attempt {attempt}/{attempts}).",
                file=sys.stderr,
            )
            if attempt >= attempts:
                print(
                    "[timeout-wrapper] no retries remain. Review scope or raise "
                    "--timeout-seconds for justified runs.",
                    file=sys.stderr,
                )
                return 124
            print("[timeout-wrapper] attempting cleanup before retry.")
            _cleanup_processes(cleanup_patterns)

    return 124


def _normalize_tool_args(tool_args: Sequence[str]) -> List[str]:
    normalized = list(tool_args)
    if normalized and normalized[0] == "--":
        normalized = normalized[1:]
    return normalized


def _extract_black_wrapper_args(
    tool_args: Sequence[str],
) -> tuple[List[str], List[str]]:
    wrapper_args: List[str] = []
    passthrough_args: List[str] = []
    for arg in _normalize_tool_args(tool_args):
        if arg == BLACK_INCLUDE_IGNORED_FLAG:
            wrapper_args.append(arg)
            continue
        passthrough_args.append(arg)
    return wrapper_args, passthrough_args


def _black_option_and_path_args(
    tool_args: Sequence[str],
) -> tuple[List[str], List[str]]:
    option_args: List[str] = []
    path_args: List[str] = []
    expecting_value = False

    for arg in tool_args:
        if expecting_value:
            option_args.append(arg)
            expecting_value = False
            continue

        if arg == "--":
            continue

        if arg.startswith("--") and arg != "--":
            option_args.append(arg)
            if "=" not in arg and arg in BLACK_LONG_OPTIONS_WITH_VALUE:
                expecting_value = True
            continue

        if arg.startswith("-") and arg != "-":
            option_args.append(arg)
            short_flag = arg[:2]
            if len(arg) == 2 and short_flag in BLACK_SHORT_OPTIONS_WITH_VALUE:
                expecting_value = True
            continue

        path_args.append(arg)

    return option_args, path_args


def _supports_black_profile_mode(option_args: Sequence[str]) -> bool:
    for option in option_args:
        if option in {"-c", "--code"}:
            return False
        if option.startswith("--code="):
            return False
    return True


def _black_has_explicit_target_version(option_args: Sequence[str]) -> bool:
    for option in option_args:
        if option in {"-t", "--target-version"}:
            return True
        if option.startswith("--target-version="):
            return True
    return False


def _black_config_entry(
    config: Dict[str, object],
) -> tuple[List[str], int, int, List[str]]:
    tools = config.get("tools")
    if not isinstance(tools, dict):
        raise ValueError("configuration must include a 'tools' object")
    entry = tools.get("black")
    if not isinstance(entry, dict):
        raise KeyError("unknown tool 'black'")

    command = entry.get("command")
    if not isinstance(command, list) or not all(
        isinstance(part, str) for part in command
    ):
        raise ValueError("tool 'black' must define command as a list of strings")

    configured_timeout = entry.get("timeout_seconds")
    if not isinstance(configured_timeout, int) or configured_timeout <= 0:
        default_timeout = config.get("default_timeout_seconds")
        if not isinstance(default_timeout, int) or default_timeout <= 0:
            raise ValueError("default_timeout_seconds must be a positive integer")
        configured_timeout = default_timeout

    retries = entry.get("timeout_retries", 0)
    if not isinstance(retries, int) or retries < 0:
        raise ValueError("timeout_retries must be a non-negative integer")

    cleanup_patterns = entry.get("cleanup_patterns", [])
    if not isinstance(cleanup_patterns, list) or not all(
        isinstance(pattern, str) for pattern in cleanup_patterns
    ):
        raise ValueError("cleanup_patterns must be a list of strings")

    return list(command), configured_timeout, retries, cleanup_patterns


def _nested_timeout_command(
    tool: str,
    override_timeout: int | None,
    tool_args: Sequence[str],
) -> List[str]:
    command = [sys.executable, str(Path(__file__).resolve())]
    if override_timeout is not None:
        command.extend(["--timeout-seconds", str(override_timeout)])
    command.append(tool)
    command.extend(["--", *tool_args])
    return command


def _build_black_command(
    runtime_executable: str,
    base_command: Sequence[str],
    option_args: Sequence[str],
    path_args: Sequence[str],
    *,
    target_version: str | None,
) -> List[str]:
    command = [runtime_executable, *base_command]
    if target_version is not None:
        command.extend(["--target-version", target_version])
    command.extend(option_args)
    command.extend(path_args)
    return command


def _black_raw_runtime_policy(cwd: Path) -> str:
    profiles = black_profiles(cwd)
    if not profiles:
        raise ValueError("tools.black.profiles must not be empty")
    runtime_policy = profiles[0].get("runtime_policy")
    if not isinstance(runtime_policy, str):
        raise ValueError("Black profile runtime_policy must be a string")
    return runtime_policy


def _maybe_run_black(
    override_timeout: int | None,
    tool_args: Sequence[str],
) -> int:
    cwd = Path.cwd()
    config = _load_config()
    base_command, configured_timeout, retries, cleanup_patterns = _black_config_entry(
        config
    )
    timeout = override_timeout or configured_timeout

    try:
        execution_constraints = load_execution_constraints(
            cwd / EXECUTION_CONSTRAINTS_FILE
        )
    except ValueError as error:
        print(f"[timeout-wrapper] {error}", file=sys.stderr)
        return 2

    wrapper_args, passthrough_args = _extract_black_wrapper_args(tool_args)
    option_args, path_args = _black_option_and_path_args(passthrough_args)

    if not _supports_black_profile_mode(option_args):
        runtime_executable = resolve_runtime_policy_executable(
            cwd,
            _black_raw_runtime_policy(cwd),
        )
        command = _build_black_command(
            runtime_executable,
            base_command,
            passthrough_args,
            [],
            target_version=None,
        )
        return _run_command_with_timeout(
            command,
            timeout=timeout,
            retries=retries,
            cleanup_patterns=cleanup_patterns,
        )

    discovered_paths = discover_black_paths(
        cwd,
        path_args or None,
        include_ignored=BLACK_INCLUDE_IGNORED_FLAG in wrapper_args,
    )
    if discovered_paths is None:
        runtime_executable = resolve_runtime_policy_executable(
            cwd,
            _black_raw_runtime_policy(cwd),
        )
        command = _build_black_command(
            runtime_executable,
            base_command,
            passthrough_args,
            [],
            target_version=None,
        )
        return _run_command_with_timeout(
            command,
            timeout=timeout,
            retries=retries,
            cleanup_patterns=cleanup_patterns,
        )

    groups = group_black_paths_by_profile(cwd, discovered_paths)
    if not groups:
        print("[timeout-wrapper] black: no eligible files discovered for the scope.")
        return 0

    invocation_kind = "explicit_paths" if path_args else "default_scope"
    serial_allowed = all(
        bool(profile.get("allow_serial_fallback", False)) for profile, _ in groups
    )
    if serial_allowed:
        constraint_label = serial_execution_constraint_label(
            execution_constraints,
            "black",
            len(discovered_paths),
            invocation_kind,
        )
        if constraint_label is not None:
            print(
                f"[timeout-wrapper] SERIAL black: matched {constraint_label}; "
                f"running {len(discovered_paths)} files one at a time through the "
                "timeout harness."
            )
            child_base_args = [*wrapper_args, *option_args]
            for index, path_arg in enumerate(discovered_paths, start=1):
                print(
                    f"[timeout-wrapper] SERIAL black: "
                    f"{index}/{len(discovered_paths)} {path_arg}"
                )
                child_command = _nested_timeout_command(
                    "black",
                    override_timeout,
                    [*child_base_args, path_arg],
                )
                result = subprocess.run(child_command, check=False)
                if result.returncode != 0:
                    print(
                        f"[timeout-wrapper] SERIAL black: {path_arg} exited "
                        f"{result.returncode}.",
                        file=sys.stderr,
                    )
                    return result.returncode
            return 0

    explicit_target_version = _black_has_explicit_target_version(option_args)
    for profile, group_paths in groups:
        runtime_policy = profile.get("runtime_policy")
        if not isinstance(runtime_policy, str):
            raise ValueError("Black profile runtime_policy must be a string")
        runtime_executable = resolve_runtime_policy_executable(cwd, runtime_policy)
        target_version = None
        if not explicit_target_version:
            profile_target = profile.get("target_version")
            if not isinstance(profile_target, str):
                raise ValueError("Black profile target_version must be a string")
            target_version = profile_target
        print(
            f"[timeout-wrapper] PROFILE black: {profile.get('name')} "
            f"({len(group_paths)} files)."
        )
        command = _build_black_command(
            runtime_executable,
            base_command,
            option_args,
            group_paths,
            target_version=target_version,
        )
        returncode = _run_command_with_timeout(
            command,
            timeout=timeout,
            retries=retries,
            cleanup_patterns=cleanup_patterns,
        )
        if returncode != 0:
            return returncode
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    try:
        _configure_direct_checkout_runtime(Path.cwd())
    except RuntimeError as error:
        print(f"[timeout-wrapper] {error}", file=sys.stderr)
        return 2
    if args.tool == "black":
        return _maybe_run_black(args.timeout_seconds, args.tool_args)

    config = _load_config()
    command, timeout, retries, cleanup_patterns = _resolve_tool_run(
        config=config,
        tool=args.tool,
        override_timeout=args.timeout_seconds,
        tool_args=args.tool_args,
    )
    return _run_command_with_timeout(
        command,
        timeout=timeout,
        retries=retries,
        cleanup_patterns=cleanup_patterns,
    )


if __name__ == "__main__":
    raise SystemExit(main())
