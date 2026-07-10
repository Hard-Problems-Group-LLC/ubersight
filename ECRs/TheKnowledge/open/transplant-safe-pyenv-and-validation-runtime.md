# ECR: Transplant-Safe Pyenv and Validation Runtime

## Target

TheKnowledge managed Python bootstrap and validation starter.

## Summary

A consuming project restored onto a newer Linux host exposed four managed
starter assumptions that conflict with deliberate interpreter ownership:

- existing pyenv and pyenv-virtualenv checkouts are updated with
  `git pull --ff-only`;
- stage two installs the minimum bootstrap context even after stage one has
  already satisfied that minimum;
- standard user venvs are reused without checking their base interpreter; and
- validation resolves `.venv/bin/python` through its symlink to the sparse
  pyenv base interpreter, which falsely hides Black and other venv tools.

## Requested Contract

- Clone a missing version-manager checkout, but never mutate an existing one as
  a project-install side effect.
- Validate the bootstrap floor and provision only the selected steady-state
  runtime during normal stage two.
- Compare an existing standard venvs canonical base interpreter with the
  selected base and rebuild on mismatch.
- Make relative venv command paths absolute without resolving their final
  symlink.
- Carry deliberate runtime overrides into serialized Black subprocesses.
- Add restored-host fixtures covering detached tags, removed interpreter
  builds, drifted venvs, and Python 3.14 project environments.

## Evidence

A clean scrub dry run selected Rocky system Python 3.12.13 for the project
because the checked-in pyenv 3.12.12 runtime no longer existed. An explicit
pyenv 3.14.6 plan was rebuildable. The same symlink-resolution failure had
already been reproduced in another consuming project.

## Status

Open. The local repair passed standard/development installs and the full
Python 3.14.6 quality gate while this request awaits upstream review.
