# pcb-physical-bench

Post-layout physical verification for boards that route clean and still fail.

## The problem

`ratsnest = 0` means every net is connected. It says nothing about whether the
return current has a plane to flow in, whether the decoupling cap sits inside its
self-resonant window, or whether a trace will cook itself. Those three account
for most field returns on high-speed boards, and all of them pass standard DRC.

## What is here

`verifier/kicad_sexpr_parser.py`: native `.kicad_pcb` S-expression parser, no
KiCad install required. Pulls copper zone polygons, net classes, differential
pair geometry, via stitching, and footprint pad positions into `board.agentic.json`.

`verifier/si_thermal_drc.py`: three checks over that JSON.

1. Reference-plane split beneath 90-ohm USB/PCIe pairs. Ray-casts each segment
   midpoint against the adjacent GND zone, reports length over split and Z0 delta.
2. Decoupling via-to-pad loop inductance, via barrel plus trace minus mutual,
   gated at 1.8 nH.
3. IPC-2152 current-density delta-T, internal versus external copper, gated at
   45 C rise.

`benchmarks/layout_trap_corpus.json`: four boards. One clean, three that pass
ratsnest and standard DRC but fail physically, each with the field failure mode
it produces.

## Run

```
python verifier/kicad_sexpr_parser.py board.kicad_pcb
python verifier/si_thermal_drc.py board.agentic.json --load VBUS=4.7
```

Standard library only.

## Why the corpus is adversarial

Every trap board is DRC-clean by construction. A verifier that only re-runs
connectivity scores 4/4 on them and is worthless. The corpus exists so the grader
is measured on the physics it claims to check.


## Expanded project documentation

The `docs/` directory contains project-specific design notes, verification scenario specifications, operations guides, data contracts, configuration templates, safety notes, and project reference templates.

**Important:** added documents are planning and review aids. They do not claim that tests have been executed, that placeholder configurations are supported runtime profiles, or that this project is certified for physical/clinical/production deployment. Confirm interfaces and limits against implementation and measured evidence.
