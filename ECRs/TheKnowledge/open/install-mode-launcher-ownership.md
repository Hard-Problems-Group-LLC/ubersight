# ECR: Install-Mode Launcher Ownership

## Target

TheKnowledge managed Python bootstrap and project install-hook contract.

## Problem

A consuming project successfully ran standard mode and development mode in
sequence. Both modes published the same commands under the user-local bin
directory. The final development run therefore repointed the public commands
from the stable user venv to the repo-local editable venv. Both acceptance
checks passed, but their order determined the machine's final command target.
An explicit `--user-home` run also did not prove that the ordinary documented
command would select the correct current HOME.

## Requested Contract

- Require installers to report the effective target HOME, why it was selected,
  the launcher directory, and the environment each launcher targets.
- Test ordinary current-HOME selection, explicit-home selection, and
  isolated-HOME refusal as separate acceptance paths.
- Document that standard/dev execution order can change the final published
  command target.
- Offer a development no-publish mode or distinct development launcher names
  when stable and editable commands need to coexist.
- Add a post-install inspection command or fixture that verifies launcher
  ownership without executing application behavior.

## Status

Open. The consuming project intentionally leaves development launchers active
and records their final target during recovery verification.
