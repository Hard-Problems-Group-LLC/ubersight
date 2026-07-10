from pathlib import Path
import importlib.util
import io
import os
from types import SimpleNamespace

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
    selection_name,
    shell_rc_path,
    upsert_managed_block,
)

ROOT = Path(__file__).resolve().parents[1]


def load_install_stage_2_module():
    """Load the stage-two installer module for focused tests."""
    module_path = ROOT / "scripts" / "install-stage-2.py"
    spec = importlib.util.spec_from_file_location("install_stage_2", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_load_python_environment_config_reads_runtime_selection():
    """Verify load python environment config reads runtime selection."""
    config = load_python_environment_config(ROOT)

    assert config.bootstrap.required_version == "3.9"
    assert config.runtime.environment_name == "3.14.6"
    assert not context_requires_virtualenv(config.runtime)
    assert selection_name(config.runtime) == "3.14.6"


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

    def fake_ensure_context(root, context, runner):
        """Record the runtime contexts requested by stage two."""
        installed_contexts.append(context)
        return context.environment_name

    monkeypatch.setattr(module, "ensure_pyenv_context", fake_ensure_context)

    result = module.ensure_runtime_contexts(tmp_path)

    assert result == (pyenv_root, "3.14.6", runtime_python)
    assert installed_contexts == [runtime]
    assert (repo_root / ".python-version").read_text(encoding="utf-8") == "3.14.6\n"


def test_standard_virtualenv_keeps_matching_base_interpreter(monkeypatch, tmp_path):
    """Verify standard virtualenv keeps matching base interpreter."""
    module = load_install_stage_2_module()
    base_python = tmp_path / "pyenv" / "bin" / "python"
    venv_path = tmp_path / "venv"
    venv_python = venv_path / "bin" / "python"
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
    venv_python = venv_path / "bin" / "python"
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

    module.install_git_hooks(Path("/tmp/venv/bin/python"))

    assert commands == [
        (
            ["/tmp/venv/bin/python", str(installer)],
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

    module.install_git_hooks(Path("/tmp/venv/bin/python"))

    assert commands == [
        (
            [
                "/tmp/venv/bin/python",
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


def test_validation_runtime_preserves_venv_launcher_path():
    """Keep a venv Python path intact when normalizing tool executables."""

    from scripts.tool_validation_profiles import _normalize_executable_candidate

    normalized = _normalize_executable_candidate(ROOT, ".venv/bin/python")

    assert normalized == str(ROOT / ".venv" / "bin" / "python")
