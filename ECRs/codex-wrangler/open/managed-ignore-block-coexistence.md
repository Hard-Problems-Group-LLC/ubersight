# ECR: Managed Ignore-Block Coexistence

## Target

codex-wrangler ignore management and its contract with standards-managed
project ignore files.

## Problem

A consuming project already had standards-managed ignore entries protecting
its local Codex payload, isolated home, `.local` state, launcher, and local
README. Repairing the payload added a second codex-wrangler-owned block with
overlapping patterns. The duplicate patterns are functionally harmless and
the ownership marker supports self-test and conservative uninstall behavior,
but an operator may reasonably mistake the second block for removable noise.
Manual deduplication can then break owner-specific verification.

## Requested Change

- Document that overlapping managed blocks may be intentional ownership
  records and must not be collapsed merely because their patterns match.
- Make inspect and self-test distinguish "path is protected" from "this tool's
  ownership marker is present."
- Consider an adoption or coexistence contract for projects whose standards
  manager already protects every required path.
- Never rewrite or delete another manager's block.
- Include `git check-ignore -v` in diagnostics so effective protection remains
  independently verifiable.

## Status

Open. The consuming project retains both managed blocks and verified that all
protected paths resolve to explicit ignore rules.
