# ECR: Local-State Inspection Versus Publication Boundary

## Target

AI-assisted administration guidance and project-local agent instructions.

## Problem

Guidance that says local state is sensitive can be interpreted as a blanket
ban on inspection, even when an active recovery task requires that state as
policy input or preservation evidence. The intended boundary is about effects
that can reach version control or publication, not ordinary local diagnosis.

## Requested Change

- State that authorized agents may inspect project-local state needed for
  diagnosis, recovery, status rendering, and preservation.
- Prohibit staging, committing, pushing, quoting, or copying local sensitive
  contents into tracked artifacts.
- Require publication-capable tools to exclude local-state paths and derived
  sensitive details.
- Separate inspect permission from mutate permission. Cleanup or schema
  migration still requires explicit scope and a preservation plan.
- Keep final summaries high-level even when detailed local inspection was
  necessary.

## Status

Open. The shared porting guide now states the inspection/publication boundary; project and system guidance still need consolidation.
