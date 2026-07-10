# Rocky Linux 10 Transplant Recovery

## Purpose

Define how Ubersight should recover after its source tree and local assistant
state are transplanted from Rocky Linux 9.8 to Rocky Linux 10.2 while
host-generated Python, Node, and cache state is rebuilt.

## Protected Existing Work

- Preserve all pre-existing tracked and untracked roadmap work.
- Treat `.local/` as inspectable local policy and runtime state whose contents
  and sensitive derivatives must not enter Git or publication-capable output.
  Path-level ignore rules and policy documentation are allowed.
- Preserve `.codex-home/` and any project-local Claude home in full,
  including authentication, memories, sessions, history, rules, goals, and
  other accumulated assistant context.
- Back up protected assistant homes before package repair.
- Never derive Git identity from remotes. The operator-provided HPG identity is
  authoritative for this repository if a commit is later requested.

## Generated State

The following state may be removed and reproduced:

- `.venv/` and Python bytecode, test, formatter, and build caches;
- `.codex-local/node_modules/` and bounded npm scratch/cache paths; and
- other scrubber-classified generated artifacts that are untracked and outside
  protected local or assistant-home paths.

The managed Codex manifest, metadata, launcher, local README, and isolated home
must remain available for explicit repair after scrubbing.

## Python Requirements

- Retain Python 3.9 as the package and bootstrap compatibility floor.
- Select pyenv CPython 3.14.6 as the steady-state project runtime.
- Do not use Rocky system Python as a project dependency container.
- Do not install the Python 3.9 compatibility floor during normal stage-two
  development bootstrap.
- Reuse existing pyenv and pyenv-virtualenv controller checkouts without Git
  mutation. Detached reviewed tags are valid.
- Rebuild a standard user venv when its reported canonical base interpreter
  differs from the selected base.

## Validation Runtime Requirements

- Preserve the `.venv/bin/python` path when making it absolute. Do not resolve
  its final symlink to the sparse pyenv base interpreter.
- Carry the selected venv path into nested or serialized Black subprocesses.
- Run Black through the repository timeout harness, then run the complete
  required quality gate on the real Rocky Linux host.

## Recovery Sequence

1. Run `scrub-volatiles.py` in dry-run mode with explicit exclusions for
   `.local/**`, `.codex-home/**`, and possible Claude home paths.
2. Reject any plan that selects `/usr/bin/python` for a project venv.
3. Rerun with explicit
   `--python /home/mheck/.pyenv/versions/3.14.6/bin/python`,
   `--upgrade-bootstrap`, and `--pip-check`.
4. Back up protected assistant state and apply only the reviewed plan.
5. Repair source declarations and bootstrap behavior, then dog-food standard
   and development installation.
6. Publish high-level recovery progress through Ubersight without placing
   sensitive `.local` contents or derivatives in tracked or publication-capable
   output.
7. Refresh and repair the isolated Codex package with `codex-wrangler` while
   preserving the isolated home.
8. Verify Ubersight commands, Codex inspect/self-test/version/login, assistant
   state metadata, and the full quality gate before closing the work.

## Acceptance Criteria

- Standard and development installs report Python 3.14.6.
- Ubersight console commands resolve from the intended managed environments.
- The existing roadmap diff remains intact.
- Protected assistant-home size/file-count and memory/session directory
  metadata survive package repair.
- Codex inspect reports no issues or warnings and self-test passes.
- Black, Ruff, compileall, entropy scan, entropy tripwire, and pytest pass.
- No `.local` contents or sensitive derivatives appear in the Git diff;
  protective ignore rules and path-level policy references are permitted.
