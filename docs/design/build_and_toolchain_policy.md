# Build and toolchain policy

## Scope
This note records the design considerations for KiCad PCB physical verification beyond connectivity DRC.

## Current project context
The repository currently implements S-expression parsing, return-path continuity, decoupling loop inductance, IPC-2152 thermal estimates. The main code paths are `verifier/kicad_sexpr_parser.py` and `verifier/si_thermal_drc.py`. The principal engineering concerns are reference plane splits, differential-pair geometry, pad-to-via loop, copper thickness, false-positive control.

## Design questions
- What inputs, units, coordinate conventions, and preconditions does this part of the system require?
- Which state is authoritative, and which values are derived?
- How are invalid, stale, incomplete, or physically implausible inputs represented?
- Which assumptions are supported by source code or measured evidence, and which remain provisional?
- What observable evidence allows a reviewer to reproduce the behavior?

## Recommended design record
Document the selected approach, alternatives considered, rationale, expected failure modes, and any measurable trade-offs. Keep numerical constants linked to source or calibration evidence; do not copy unexplained values into a second source of truth.

## Verification
Pair this design note with the relevant scenarios under `docs/verification/scenarios/`. A design decision is not considered verified merely because the code compiles.

## Status
Working reference. Confirm implementation details against the source before using this note as a hardware, clinical, or production specification.
