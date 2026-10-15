from pathlib import Path
import importlib.util
import io
import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from scripts.python_environment_bootstrap import (
    PythonContextConfig,
    context_requires_virtualenv,
    DIRENV_BEGIN,
    DIRENV_END,
    ENVRC_MARKER,
    build_direnv_hook_block,
    build_envrc_content,
    detect_shell_name,
    direnv_download_name,
    ensure_pyenv_installed,
    ensure_pyenv_virtualenv_plugin,
    load_python_environment_config,
    rebuild_pyenv_context,
    selection_name,
    shell_rc_path,
    upsert_managed_block,
)

ROOT = Path(__file__).resolve().parents[1]


# Behavior that exists only on POSIX hosts. The Windows stage two never
# builds or repairs a pyenv runtime: it uses the interpreter install.ps1
# selected. Windows also needs no native curses build prerequisites, because
# windows-curses ships prebuilt wheels.
POSIX_PYENV_ONLY = pytest.mark.skipif(
    os.name == "nt",
    reason=(
        "pyenv source builds and runtime repair run only on POSIX; Windows "
        "installs use the interpreter install.ps1 selects"
    ),
)
POSIX_SHELL_ONLY = pytest.mark.skipif(
    os.name == "nt",
    reason=(
        "install_prerequisites.sh is a POSIX shell installer; Windows needs no "
        "native curses build prerequisites"
    ),
)


def load_install_stage_2_module():
    """Load the stage-two installer module for focused tests."""
    module_path = ROOT / "scripts" / "install-stage-2.py"
    spec = importlib.util.spec_from_file_location("install_stage_2", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def load_dev_setup_module():
    """Load the development setup module without retaining its path fallback."""

    module_path = ROOT / "scripts" / "dev_setup.py"
    spec = importlib.util.spec_from_file_location("dev_setup_for_tests", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    original_path = list(sys.path)
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = original_path
    return module


def test_dev_setup_command_log_avoids_shell_redirection(monkeypatch, capsys):
    """Command logs must not resemble output redirection into executables."""

    dev_setup = load_dev_setup_module()
    calls = []

    def fake_run(command, *, cwd, check):
        """Capture the child invocation without starting a process."""

        calls.append((command, cwd, check))

    monkeypatch.setattr(dev_setup.subprocess, "run", fake_run)

    python = Path("/managed/venv/bin/python")
    dev_setup.run([python, "--version"])

    output = capsys.readouterr().out
    assert f"[dev-setup] RUN: {python} --version" in output
    assert " -> " not in output
    assert calls == [([str(python), "--version"], dev_setup.REPO_ROOT, True)]


def test_load_python_environment_config_reads_runtime_selection():
    """Verify load python environment config reads runtime selection."""
    config = load_python_environment_config(ROOT)

    assert config.bootstrap.required_version == "3.9"
    assert config.runtime.environment_name == "3.14.6"
    assert not context_requires_virtualenv(config.runtime)
    assert selection_name(config.runtime) == "3.14.6"


@POSIX_PYENV_ONLY
def test_existing_pyenv_checkout_is_reused_without_vcs_mutation(tmp_path):
    """Verify existing pyenv checkout is reused without vcs mutation."""
    pyenv_root = tmp_path / ".pyenv"
    executable = pyenv_root / "bin" / "pyenv"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)

    def unexpected_runner(command):
        """Fail if reuse unexpectedly invokes the command runner."""
        raise AssertionError("existing pyenv checkout must not be mutated")

    assert ensure_pyenv_installed(pyenv_root, unexpected_runner) == executable


def test_existing_pyenv_plugin_is_reused_without_vcs_mutation(tmp_path):
    """Verify existing pyenv plugin is reused without vcs mutation."""
    pyenv_root = tmp_path / ".pyenv"
    plugin_root = pyenv_root / "plugins" / "pyenv-virtualenv"
    plugin_root.mkdir(parents=True)

    def unexpected_runner(command):
        """Fail if reuse unexpectedly invokes the command runner."""
        raise AssertionError("existing pyenv plugin must not be mutated")

    assert ensure_pyenv_virtualenv_plugin(pyenv_root, unexpected_runner) == plugin_root


@POSIX_PYENV_ONLY
def test_runtime_context_setup_does_not_install_bootstrap_floor(monkeypatch, tmp_path):
    """Verify runtime context setup does not install bootstrap floor."""
    module = load_install_stage_2_module()
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    pyenv_root = tmp_path / ".pyenv"
    runtime_python = pyenv_root / "versions" / "3.14.6" / "bin" / "python"
    runtime_python.parent.mkdir(parents=True)
    runtime_python.write_text("", encoding="utf-8")
    bootstrap = PythonContextConfig("3.9", "3.9.21", "3.9.21")
    runtime = PythonContextConfig("3.12", "3.14.6", "3.14.6")
    config = SimpleNamespace(bootstrap=bootstrap, runtime=runtime)
    installed_contexts = []

    monkeypatch.setattr(module, "REPO_ROOT", repo_root)
    monkeypatch.setattr(
        module, "load_python_environment_config", lambda selected_root: config
    )
    monkeypatch.setattr(
        module, "default_install_pyenv_root", lambda user_home: pyenv_root
    )
    monkeypatch.setattr(module, "ensure_pyenv_installed", lambda root, runner: None)

    def unexpected_preflight():
        """Fail if an existing pyenv build unnecessarily checks headers."""

        raise AssertionError("existing pyenv runtimes must not need build headers")

    monkeypatch.setattr(
        module, "verify_curses_build_prerequisite", unexpected_preflight
    )

    def fake_ensure_context(root, context, runner):
        """Record the runtime contexts requested by stage two."""
        installed_contexts.append(context)
        return context.environment_name

    monkeypatch.setattr(module, "ensure_pyenv_context", fake_ensure_context)
    checked_runtimes = []
    checked_interpreters = []
    monkeypatch.setattr(
        module,
        "verify_python_runtime",
        lambda executable, version: checked_interpreters.append((executable, version)),
    )
    monkeypatch.setattr(
        module,
        "verify_curses_support",
        lambda executable: checked_runtimes.append(executable),
    )

    result = module.ensure_runtime_contexts(tmp_path)

    assert result == (pyenv_root, "3.14.6", runtime_python)
    assert installed_contexts == [runtime]
    assert checked_interpreters == [(runtime_python, "3.14.6")]
    assert checked_runtimes == [runtime_python]
    assert (repo_root / ".python-version").read_text(encoding="utf-8") == "3.14.6\n"


@POSIX_PYENV_ONLY
def test_runtime_context_checks_headers_before_building_pyenv(monkeypatch, tmp_path):
    """Verify a new pyenv runtime checks native headers before compilation."""

    module = load_install_stage_2_module()
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    pyenv_root = tmp_path / ".pyenv"
    runtime_python = pyenv_root / "versions" / "3.14.6" / "bin" / "python"
    bootstrap = PythonContextConfig("3.9", "3.9.21", "3.9.21")
    runtime = PythonContextConfig("3.12", "3.14.6", "3.14.6")
    config = SimpleNamespace(bootstrap=bootstrap, runtime=runtime)
    calls = []

    monkeypatch.setattr(module, "REPO_ROOT", repo_root)
    monkeypatch.setattr(
        module, "load_python_environment_config", lambda selected_root: config
    )
    monkeypatch.setattr(
        module, "default_install_pyenv_root", lambda user_home: pyenv_root
    )
    monkeypatch.setattr(module, "ensure_pyenv_installed", lambda root, runner: None)
    monkeypatch.setattr(
        module,
        "verify_curses_build_prerequisite",
        lambda: calls.append("headers"),
    )

    def fake_ensure_context(root, context, runner):
        """Create the controlled runtime after the preflight succeeds."""

        calls.append("pyenv")
        runtime_python.parent.mkdir(parents=True)
        runtime_python.write_text("", encoding="utf-8")
        return context.environment_name

    monkeypatch.setattr(module, "ensure_pyenv_context", fake_ensure_context)
    monkeypatch.setattr(
        module, "verify_python_runtime", lambda executable, version: None
    )
    monkeypatch.setattr(module, "verify_curses_support", lambda executable: None)

    assert module.ensure_runtime_contexts(tmp_path) == (
        pyenv_root,
        "3.14.6",
        runtime_python,
    )
    assert calls == ["headers", "pyenv"]


@POSIX_PYENV_ONLY
def test_runtime_context_rebuilds_existing_runtime_without_curses(
    monkeypatch, tmp_path
):
    """Verify a known-bad runtime is rebuilt after its headers are available."""

    module = load_install_stage_2_module()
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    pyenv_root = tmp_path / ".pyenv"
    runtime_python = pyenv_root / "versions" / "3.14.6" / "bin" / "python"
    runtime_python.parent.mkdir(parents=True)
    runtime_python.write_text("", encoding="utf-8")
    bootstrap = PythonContextConfig("3.9", "3.9.21", "3.9.21")
    runtime = PythonContextConfig("3.12", "3.14.6", "3.14.6")
    config = SimpleNamespace(bootstrap=bootstrap, runtime=runtime)
    calls = []

    monkeypatch.setattr(module, "REPO_ROOT", repo_root)
    monkeypatch.setattr(
        module, "load_python_environment_config", lambda selected_root: config
    )
    monkeypatch.setattr(
        module, "default_install_pyenv_root", lambda user_home: pyenv_root
    )
    monkeypatch.setattr(module, "ensure_pyenv_installed", lambda root, runner: None)
    monkeypatch.setattr(
        module,
        "ensure_pyenv_context",
        lambda root, context, runner: calls.append("ensure")
        or context.environment_name,
    )
    monkeypatch.setattr(
        module,
        "verify_curses_build_prerequisite",
        lambda: calls.append("headers"),
    )
    monkeypatch.setattr(
        module, "verify_python_runtime", lambda executable, version: None
    )
    verification_count = {"value": 0}

    def verify_curses(executable):
        """Fail once so the test exercises the automatic repair path."""

        assert executable == runtime_python
        calls.append("verify")
        verification_count["value"] += 1
        if verification_count["value"] == 1:
            raise module.CursesSupportError("missing _curses")

    monkeypatch.setattr(module, "verify_curses_support", verify_curses)

    def fake_rebuild(root, context, runner, validator=None):
        """Run the supplied validator as a successful rebuild would."""

        calls.append("rebuild")
        assert validator is not None
        validator(runtime_python)
        return context.environment_name

    monkeypatch.setattr(module, "rebuild_pyenv_context", fake_rebuild)

    assert module.ensure_runtime_contexts(tmp_path) == (
        pyenv_root,
        "3.14.6",
        runtime_python,
    )
    assert calls == ["ensure", "verify", "headers", "rebuild", "verify"]
    assert (repo_root / ".python-version").read_text(encoding="utf-8") == "3.14.6\n"


@POSIX_PYENV_ONLY
def test_runtime_context_does_not_rebuild_without_curses_headers(monkeypatch, tmp_path):
    """Verify a failed header preflight leaves an invalid runtime untouched."""

    module = load_install_stage_2_module()
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    pyenv_root = tmp_path / ".pyenv"
    runtime_python = pyenv_root / "versions" / "3.14.6" / "bin" / "python"
    runtime_python.parent.mkdir(parents=True)
    runtime_python.write_text("", encoding="utf-8")
    bootstrap = PythonContextConfig("3.9", "3.9.21", "3.9.21")
    runtime = PythonContextConfig("3.12", "3.14.6", "3.14.6")
    config = SimpleNamespace(bootstrap=bootstrap, runtime=runtime)
    calls = []

    monkeypatch.setattr(module, "REPO_ROOT", repo_root)
    monkeypatch.setattr(
        module, "load_python_environment_config", lambda selected_root: config
    )
    monkeypatch.setattr(
        module, "default_install_pyenv_root", lambda user_home: pyenv_root
    )
    monkeypatch.setattr(module, "ensure_pyenv_installed", lambda root, runner: None)
    monkeypatch.setattr(
        module,
        "ensure_pyenv_context",
        lambda root, context, runner: calls.append("ensure")
        or context.environment_name,
    )
    monkeypatch.setattr(
        module, "verify_python_runtime", lambda executable, version: None
    )
    monkeypatch.setattr(
        module,
        "verify_curses_support",
        lambda executable: (_ for _ in ()).throw(
            module.CursesSupportError("missing _curses")
        ),
    )
    monkeypatch.setattr(
        module,
        "verify_curses_build_prerequisite",
        lambda: (_ for _ in ()).throw(RuntimeError("missing curses.h")),
    )
    monkeypatch.setattr(
        module,
        "rebuild_pyenv_context",
        lambda root, context, runner: (_ for _ in ()).throw(
            AssertionError("rebuild must not run without headers")
        ),
    )

    try:
        module.ensure_runtime_contexts(tmp_path)
    except RuntimeError as error:
        message = str(error)
    else:
        raise AssertionError("Expected the missing-header preflight to fail.")

    assert message == "missing curses.h"
    assert calls == ["ensure"]


@POSIX_PYENV_ONLY
def test_broken_runtime_requires_explicit_rebuild_flag(monkeypatch, tmp_path):
    """An empty shared runtime must remain untouched without explicit consent."""

    module = load_install_stage_2_module()
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    pyenv_root = tmp_path / ".pyenv"
    runtime_python = pyenv_root / "versions" / "3.14.6" / "bin" / "python"
    runtime_python.parent.mkdir(parents=True)
    runtime_python.write_text("", encoding="utf-8")
    runtime = PythonContextConfig("3.12", "3.14.6", "3.14.6")
    config = SimpleNamespace(
        bootstrap=PythonContextConfig("3.9", "3.9.21", "3.9.21"),
        runtime=runtime,
    )

    monkeypatch.setattr(module, "REPO_ROOT", repo_root)
    monkeypatch.setattr(module, "load_python_environment_config", lambda root: config)
    monkeypatch.setattr(
        module, "default_install_pyenv_root", lambda user_home: pyenv_root
    )
    monkeypatch.setattr(module, "ensure_pyenv_installed", lambda root, runner: None)
    monkeypatch.setattr(
        module,
        "ensure_pyenv_context",
        lambda root, context, runner: context.environment_name,
    )
    monkeypatch.setattr(
        module,
        "rebuild_runtime_context",
        lambda root, context: (_ for _ in ()).throw(
            AssertionError("rebuild must require explicit authorization")
        ),
    )

    try:
        module.ensure_runtime_contexts(tmp_path)
    except RuntimeError as error:
        message = str(error)
    else:
        raise AssertionError("Expected the empty runtime to stop installation.")

    assert "interpreter file is empty" in message
    assert "--rebuild-broken-pyenv" in message
    assert "did not modify" in message
    assert runtime_python.stat().st_size == 0
    assert not (repo_root / ".python-version").exists()


@POSIX_PYENV_ONLY
def test_broken_runtime_rebuilds_after_explicit_authorization(monkeypatch, tmp_path):
    """The opt-in flag should rebuild and validate an unusable shared runtime."""

    module = load_install_stage_2_module()
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    pyenv_root = tmp_path / ".pyenv"
    runtime_python = pyenv_root / "versions" / "3.14.6" / "bin" / "python"
    runtime_python.parent.mkdir(parents=True)
    runtime_python.write_text("", encoding="utf-8")
    runtime = PythonContextConfig("3.12", "3.14.6", "3.14.6")
    config = SimpleNamespace(
        bootstrap=PythonContextConfig("3.9", "3.9.21", "3.9.21"),
        runtime=runtime,
    )
    calls = []

    monkeypatch.setattr(module, "REPO_ROOT", repo_root)
    monkeypatch.setattr(module, "load_python_environment_config", lambda root: config)
    monkeypatch.setattr(
        module, "default_install_pyenv_root", lambda user_home: pyenv_root
    )
    monkeypatch.setattr(module, "ensure_pyenv_installed", lambda root, runner: None)
    monkeypatch.setattr(
        module,
        "ensure_pyenv_context",
        lambda root, context, runner: context.environment_name,
    )
    monkeypatch.setattr(
        module,
        "verify_curses_build_prerequisite",
        lambda: calls.append("headers"),
    )
    monkeypatch.setattr(
        module,
        "rebuild_runtime_context",
        lambda root, context: calls.append("rebuild") or context.environment_name,
    )

    result = module.ensure_runtime_contexts(
        tmp_path,
        rebuild_broken_pyenv=True,
    )

    assert result == (pyenv_root, "3.14.6", runtime_python)
    assert calls == ["headers", "rebuild"]
    assert (repo_root / ".python-version").read_text(encoding="utf-8") == "3.14.6\n"


@POSIX_PYENV_ONLY
def test_rebuild_pyenv_context_preserves_then_replaces_runtime(tmp_path):
    """A rebuild should avoid pyenv's destructive in-place force mode."""

    pyenv_root = tmp_path / ".pyenv"
    context = PythonContextConfig("3.12", "3.14.6", "3.14.6")
    prefix = pyenv_root / "versions" / "3.14.6"
    prefix.mkdir(parents=True)
    (prefix / "generation.txt").write_text("old\n", encoding="utf-8")
    calls = []

    def record(command, **kwargs):
        """Record pyenv and model a successful fresh-prefix installation."""

        calls.append((list(command), kwargs))
        (prefix / "bin").mkdir(parents=True)
        (prefix / "bin" / "python").write_text("new\n", encoding="utf-8")

    checked = []

    assert (
        rebuild_pyenv_context(
            pyenv_root,
            context,
            record,
            validator=lambda candidate: checked.append(candidate),
        )
        == "3.14.6"
    )
    assert len(calls) == 1
    command, kwargs = calls[0]
    assert command == [
        str(pyenv_root / "bin" / "pyenv"),
        "install",
        "3.14.6",
    ]
    assert kwargs["env"]["PYENV_ROOT"] == str(pyenv_root)
    assert checked == [prefix / "bin" / "python"]
    assert (prefix / "bin" / "python").read_text(encoding="utf-8") == "new\n"
    assert not list((pyenv_root / "versions").glob(".*.ubersight-backup-*"))


def test_rebuild_pyenv_context_restores_runtime_after_failure(tmp_path):
    """A failed fresh build should restore the complete previous prefix."""

    pyenv_root = tmp_path / ".pyenv"
    context = PythonContextConfig("3.12", "3.14.6", "3.14.6")
    prefix = pyenv_root / "versions" / "3.14.6"
    prefix.mkdir(parents=True)
    marker = prefix / "generation.txt"
    marker.write_text("old\n", encoding="utf-8")

    def fail_after_partial_install(command, **kwargs):
        """Create partial output before modeling a failed source build."""

        (prefix / "bin").mkdir(parents=True)
        (prefix / "bin" / "python").write_text("", encoding="utf-8")
        raise subprocess.CalledProcessError(1, command)

    try:
        rebuild_pyenv_context(pyenv_root, context, fail_after_partial_install)
    except subprocess.CalledProcessError:
        pass
    else:
        raise AssertionError("Expected the controlled pyenv build failure.")

    assert marker.read_text(encoding="utf-8") == "old\n"
    assert not (prefix / "bin").exists()
    assert not list((pyenv_root / "versions").glob(".*.ubersight-backup-*"))


def test_rebuild_pyenv_context_restores_runtime_after_validation_failure(tmp_path):
    """A replacement that fails integrity checks must not displace the old one."""

    pyenv_root = tmp_path / ".pyenv"
    context = PythonContextConfig("3.12", "3.14.6", "3.14.6")
    prefix = pyenv_root / "versions" / "3.14.6"
    prefix.mkdir(parents=True)
    marker = prefix / "generation.txt"
    marker.write_text("old\n", encoding="utf-8")

    def install_invalid_runtime(command, **kwargs):
        """Model a completed pyenv build whose resulting runtime is invalid."""

        (prefix / "bin").mkdir(parents=True)
        (prefix / "bin" / "python").write_text("invalid\n", encoding="utf-8")

    def reject_runtime(candidate):
        """Reject the modeled replacement during post-build validation."""

        raise RuntimeError("integrity probe failed")

    try:
        rebuild_pyenv_context(
            pyenv_root,
            context,
            install_invalid_runtime,
            validator=reject_runtime,
        )
    except RuntimeError as error:
        assert str(error) == "integrity probe failed"
    else:
        raise AssertionError("Expected the controlled validation failure.")

    assert marker.read_text(encoding="utf-8") == "old\n"
    assert not (prefix / "bin").exists()
    assert not list((pyenv_root / "versions").glob(".*.ubersight-backup-*"))


def test_parse_args_requires_explicit_broken_pyenv_rebuild_flag():
    """The destructive recovery authorization must default to disabled."""

    module = load_install_stage_2_module()

    ordinary = module.parse_args([])
    authorized = module.parse_args(["--rebuild-broken-pyenv"])

    assert ordinary.rebuild_broken_pyenv is False
    assert authorized.rebuild_broken_pyenv is True


def test_verify_curses_build_prerequisite_uses_configured_compiler(
    monkeypatch,
):
    """Verify the preflight honors compiler and include-path configuration."""

    module = load_install_stage_2_module()
    commands = []
    monkeypatch.setenv("CC", "ccache gcc")
    monkeypatch.setenv("CPPFLAGS", "-I/custom/include")

    def fake_run(command, **kwargs):
        """Record a successful no-output header probe."""

        commands.append((list(command), kwargs))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    module.verify_curses_build_prerequisite()

    assert commands == [
        (
            ["ccache", "gcc", "-I/custom/include", "-x", "c", "-fsyntax-only", "-"],
            {
                "cwd": module.REPO_ROOT,
                "check": False,
                "input": module.CURSES_BUILD_PROBE_SOURCE,
                "capture_output": True,
                "text": True,
            },
        )
    ]


def test_verify_curses_build_prerequisite_reports_missing_headers(monkeypatch):
    """Verify a missing header stops pyenv before its source build starts."""

    module = load_install_stage_2_module()

    def fake_run(command, **_kwargs):
        """Simulate the compiler error from an absent ncurses header."""

        return subprocess.CompletedProcess(
            command,
            1,
            "",
            "fatal error: curses.h: No such file or directory\n",
        )

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    try:
        module.verify_curses_build_prerequisite()
    except RuntimeError as error:
        message = str(error)
    else:
        raise AssertionError("Expected a missing-header prerequisite failure.")

    assert "curses.h: No such file or directory" in message
    assert "./scripts/install_prerequisites.sh" in message
    assert "ncurses-devel" in message


def test_verify_curses_support_uses_selected_runtime(monkeypatch):
    """Verify installer capability checks run through the selected Python."""

    module = load_install_stage_2_module()
    commands = []
    runtime_python = Path("/managed/python")

    def fake_run(command, **kwargs):
        """Record the runtime capability check without launching Python."""

        commands.append((list(command), kwargs))

    monkeypatch.setattr(module, "run", fake_run)

    module.verify_curses_support(runtime_python)

    assert commands == [
        (
            [str(runtime_python), "-c", "import curses"],
            {"cwd": module.REPO_ROOT, "capture_output": True},
        )
    ]


def test_verify_curses_support_reports_rebuild_path(monkeypatch):
    """Verify missing native curses support gives actionable remediation."""

    module = load_install_stage_2_module()
    runtime_python = Path("/managed/python")
    # The ncurses advice is the POSIX remedy; Windows has its own message.
    monkeypatch.setattr(module, "is_windows", lambda: False)

    def fake_run(command, **_kwargs):
        """Simulate a pyenv build that omitted the `_curses` extension."""

        raise subprocess.CalledProcessError(
            1,
            command,
            stderr="No module named '_curses'",
        )

    monkeypatch.setattr(module, "run", fake_run)

    try:
        module.verify_curses_support(runtime_python)
    except RuntimeError as error:
        message = str(error)
    else:
        raise AssertionError("Expected a missing-curses runtime failure.")

    assert "No module named '_curses'" in message
    assert "./scripts/install_prerequisites.sh" in message
    assert "ncurses-devel" in message
    assert "libncursesw5-dev" in message
    assert "ncurses" in message


def test_verify_python_runtime_rejects_empty_executable(tmp_path):
    """A zero-byte executable should fail before subprocess execution."""

    module = load_install_stage_2_module()
    runtime_python = tmp_path / "python"
    runtime_python.write_text("", encoding="utf-8")
    runtime_python.chmod(0o755)

    try:
        module.verify_python_runtime(runtime_python, "3.14.6")
    except module.PythonRuntimeError as error:
        message = str(error)
    else:
        raise AssertionError("Expected the empty executable to be rejected.")

    assert message == "interpreter file is empty"


@POSIX_PYENV_ONLY
def test_verify_python_runtime_accepts_matching_executable(tmp_path):
    """A runnable interpreter probe must report the configured patch release."""

    module = load_install_stage_2_module()
    runtime_python = tmp_path / "python"
    runtime_python.write_text("#!/bin/sh\nprintf '3.14.6\\n'\n", encoding="utf-8")
    runtime_python.chmod(0o755)

    module.verify_python_runtime(runtime_python, "3.14.6")


def test_verify_install_checks_curses_support(monkeypatch):
    """Verify final installer verification checks terminal support too."""

    module = load_install_stage_2_module()
    runtime_python = Path("/managed/venv/bin/python")
    commands = []
    checked_runtimes = []

    def fake_run(command, **kwargs):
        """Record the ordinary version check without launching Python."""

        commands.append((list(command), kwargs))

    monkeypatch.setattr(module, "run", fake_run)
    monkeypatch.setattr(
        module,
        "verify_curses_support",
        lambda executable: checked_runtimes.append(executable),
    )

    module.verify_install(runtime_python)

    assert commands == [([str(runtime_python), "--version"], {"cwd": module.REPO_ROOT})]
    assert checked_runtimes == [runtime_python]


def test_standard_virtualenv_keeps_matching_base_interpreter(monkeypatch, tmp_path):
    """Verify standard virtualenv keeps matching base interpreter."""
    module = load_install_stage_2_module()
    base_python = tmp_path / "pyenv" / "bin" / "python"
    venv_path = tmp_path / "venv"
    venv_python = module.venv_python_path(venv_path)
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("", encoding="utf-8")
    commands = []

    monkeypatch.setattr(
        module,
        "interpreter_base_identity",
        lambda executable: Path("/managed/python"),
    )
    monkeypatch.setattr(
        module,
        "run",
        lambda command, cwd: commands.append((list(command), cwd)),
    )

    assert module.ensure_virtualenv(base_python, venv_path) == venv_python
    assert commands == []


def test_standard_virtualenv_rebuilds_after_base_interpreter_drift(
    monkeypatch, tmp_path
):
    """Verify standard virtualenv rebuilds after base interpreter drift."""
    module = load_install_stage_2_module()
    base_python = tmp_path / "pyenv" / "bin" / "python"
    venv_path = tmp_path / "venv"
    venv_python = module.venv_python_path(venv_path)
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("", encoding="utf-8")
    commands = []

    def fake_identity(executable):
        """Return controlled base-interpreter identities for the test."""
        if executable == base_python:
            return Path("/managed/python")
        return Path("/system/python")

    monkeypatch.setattr(module, "interpreter_base_identity", fake_identity)
    monkeypatch.setattr(
        module,
        "run",
        lambda command, cwd: commands.append((list(command), cwd)),
    )

    assert module.ensure_virtualenv(base_python, venv_path) == venv_python
    assert commands == [
        (
            [str(base_python), "-m", "venv", "--clear", str(venv_path)],
            module.REPO_ROOT,
        )
    ]


def test_detect_shell_name_defaults_to_bash_when_empty():
    """Verify detect shell name defaults to bash when empty."""
    assert detect_shell_name("") == "bash"


def test_shell_rc_path_supports_bash_and_zsh(tmp_path):
    """Verify shell rc path supports bash and zsh."""
    assert shell_rc_path(tmp_path, "bash") == tmp_path / ".bashrc"
    assert shell_rc_path(tmp_path, "zsh") == tmp_path / ".zshrc"
    assert shell_rc_path(tmp_path, "fish") is None


def test_build_direnv_hook_block_contains_managed_markers():
    """Verify build direnv hook block contains managed markers."""
    block = build_direnv_hook_block("bash")

    assert DIRENV_BEGIN in block
    assert '*":$HOME/.local/bin:"*) ;;' in block
    assert 'eval "$(direnv hook bash)"' in block
    assert DIRENV_END in block


def test_upsert_managed_block_replaces_existing_block():
    """Verify upsert managed block replaces existing block."""
    original = "\n".join([DIRENV_BEGIN, "old", DIRENV_END, "tail", ""])
    replacement = "\n".join([DIRENV_BEGIN, "new", DIRENV_END, ""])

    updated = upsert_managed_block(original, DIRENV_BEGIN, DIRENV_END, replacement)

    assert "old" not in updated
    assert "new" in updated
    assert updated.endswith("tail\n")


def test_build_envrc_content_sources_repo_venv():
    """Verify build envrc content sources repo venv."""
    content = build_envrc_content()

    assert ENVRC_MARKER in content
    assert 'source "$PWD/.venv/bin/activate"' in content


def test_direnv_download_name_supports_linux_targets():
    """Verify direnv download name supports linux targets."""
    assert direnv_download_name("linux", "x86_64") == "direnv.linux-amd64"
    assert direnv_download_name("linux", "aarch64") == "direnv.linux-arm64"


def test_install_git_hooks_prefers_repo_local_installer(monkeypatch, tmp_path):
    """Verify install git hooks prefers repo local installer."""
    module = load_install_stage_2_module()
    repo_root = tmp_path / "repo"
    installer = repo_root / "scripts" / "install_git_hooks.py"
    fallback = repo_root / "TheKnowledge" / "scripts" / "install_git_hooks.py"
    installer.parent.mkdir(parents=True)
    fallback.parent.mkdir(parents=True)
    installer.write_text("", encoding="utf-8")
    fallback.write_text("", encoding="utf-8")
    commands = []

    monkeypatch.setattr(module, "REPO_ROOT", repo_root)
    monkeypatch.setattr(
        module,
        "run",
        lambda command, cwd: commands.append((list(command), cwd)),
    )

    venv_python = Path("/tmp/venv/bin/python")
    module.install_git_hooks(venv_python)

    assert commands == [
        (
            [str(venv_python), str(installer)],
            repo_root,
        )
    ]


def test_install_git_hooks_fallback_targets_repo_root(monkeypatch, tmp_path):
    """Verify install git hooks fallback targets repo root."""
    module = load_install_stage_2_module()
    repo_root = tmp_path / "repo"
    installer = repo_root / "TheKnowledge" / "scripts" / "install_git_hooks.py"
    installer.parent.mkdir(parents=True)
    installer.write_text("", encoding="utf-8")
    commands = []

    monkeypatch.setattr(module, "REPO_ROOT", repo_root)
    monkeypatch.setattr(
        module,
        "run",
        lambda command, cwd: commands.append((list(command), cwd)),
    )

    venv_python = Path("/tmp/venv/bin/python")
    module.install_git_hooks(venv_python)

    assert commands == [
        (
            [
                str(venv_python),
                str(installer),
                "--repo-root",
                str(repo_root),
            ],
            repo_root,
        )
    ]


def test_ensure_direnv_downloads_with_timeout(monkeypatch, tmp_path):
    """Verify ensure direnv downloads with timeout."""
    module = load_install_stage_2_module()
    requested = {}

    class FakeResponse(io.BytesIO):
        """Provide context-managed bytes for the direnv download test."""

        def __enter__(self):
            """Enter the fake download response context."""
            return self

        def __exit__(self, exc_type, exc, tb):
            """Close the fake download response context."""
            self.close()
            return False

    monkeypatch.setattr(module.shutil, "which", lambda name: None)
    monkeypatch.setattr(
        module, "default_user_bin_dir", lambda user_home: tmp_path / "bin"
    )
    monkeypatch.setattr(
        module,
        "direnv_download_name",
        lambda platform_name, machine: "direnv.linux-amd64",
    )

    def fake_urlopen(url, timeout):
        """Capture the requested timeout and return a fake payload."""
        requested["url"] = url
        requested["timeout"] = timeout
        return FakeResponse(b"#!/bin/sh\nexit 0\n")

    monkeypatch.setattr(module, "urlopen", fake_urlopen)

    installed = module.ensure_direnv(auto_install=True, user_home=tmp_path)

    assert installed == tmp_path / "bin" / "direnv"
    assert installed.read_bytes() == b"#!/bin/sh\nexit 0\n"
    assert requested["timeout"] == module.DIRENV_DOWNLOAD_TIMEOUT_SECONDS
    assert os.access(installed, os.X_OK)


def test_ensure_direnv_reports_download_failure(monkeypatch, tmp_path):
    """Verify ensure direnv reports download failure."""
    module = load_install_stage_2_module()

    monkeypatch.setattr(module.shutil, "which", lambda name: None)
    monkeypatch.setattr(
        module, "default_user_bin_dir", lambda user_home: tmp_path / "bin"
    )
    monkeypatch.setattr(
        module,
        "direnv_download_name",
        lambda platform_name, machine: "direnv.linux-amd64",
    )
    monkeypatch.setattr(
        module,
        "urlopen",
        lambda url, timeout: (_ for _ in ()).throw(OSError("timed out")),
    )

    try:
        module.ensure_direnv(auto_install=True, user_home=tmp_path)
    except RuntimeError as error:
        message = str(error)
    else:
        raise AssertionError("Expected direnv download to fail.")

    assert "Failed to download direnv" in message
    assert "within 30 seconds" in message


def test_install_sh_preserves_existing_pyenv_checkout():
    """Keep stage 1 from pulling or otherwise mutating an existing pyenv."""

    content = (ROOT / "install.sh").read_text(encoding="utf-8")

    assert 'pyenv_fallback_version="3.14.6"' in content
    assert 'git -C "$pyenv_root" pull --ff-only' not in content


def test_install_sh_can_bypass_broken_pyenv_shims():
    """Stage 1 should probe physical interpreters after PATH shims fail."""

    content = (ROOT / "install.sh").read_text(encoding="utf-8")

    assert "/usr/bin/python3" in content
    assert '"${HOME}/.pyenv/versions/"*/bin/python3' in content
    assert "--rebuild-broken-pyenv" in content


def test_install_sh_delegates_root_bootstrap_prerequisites():
    """Verify root bootstrap uses the dedicated prerequisite installer."""

    content = (ROOT / "install.sh").read_text(encoding="utf-8")

    assert '"${repo_root}/scripts/install_prerequisites.sh"' in content
    assert "detect_package_manager()" not in content


@POSIX_SHELL_ONLY
def test_prerequisite_installer_plans_native_curses_dependencies():
    """Verify the direct installer selects a platform-native curses package."""

    installer = ROOT / "scripts" / "install_prerequisites.sh"
    completed = subprocess.run(
        [str(installer), "--json"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    plan = json.loads(completed.stdout)
    expected_curses_packages = {
        "apt-get": "libncursesw5-dev",
        "dnf": "ncurses-devel",
        "yum": "ncurses-devel",
        "brew": "ncurses",
    }

    assert installer.is_file()
    assert os.access(installer, os.X_OK)
    assert plan["package_manager"] in expected_curses_packages
    assert expected_curses_packages[plan["package_manager"]] in plan["packages"]

    content = installer.read_text(encoding="utf-8")
    assert "libncursesw5-dev" in content
    assert "ncurses-devel" in content
    assert "packages=(python git curl ncurses)" in content
    assert "Rerun ./install.sh; it will rebuild" in content
    assert "Rebuild any existing pyenv runtime" not in content


def test_validation_runtime_preserves_venv_launcher_path():
    """Keep a venv Python path intact when normalizing tool executables."""

    from scripts.tool_validation_profiles import _normalize_executable_candidate

    normalized = _normalize_executable_candidate(ROOT, ".venv/bin/python")

    assert normalized == str(ROOT / ".venv" / "bin" / "python")
