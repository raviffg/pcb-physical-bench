"""Post-layout physical verification for traps that pass ratsnest = 0.

A board can be fully routed, DRC-clean, and still fail in the field. Three
geometric checks that connectivity cannot see:

1. Return-path discontinuity: a high-speed differential pair crossing a split in
   its reference plane. Loop inductance spikes, the eye closes at temperature.
2. Decoupling loop starvation: via-to-pad loop inductance above 1.8 nH, which
   pushes the capacitor past self-resonance at the transient edge.
3. IPC-2152 current-density thermal rise above 45 C, internal copper worse than
   external.

Input is board.agentic.json from kicad_sexpr_parser.py.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Sequence

RETURN_GAP_MM = 0.30
SPLIT_TOLERANCE_MM = 0.5
Z0_SENSITIVITY = 0.42
L_VIA_PER_MM = 0.85    # nH/mm of barrel, 0.25 mm drill
L_TRACE_PER_MM = 0.32  # nH/mm of 0.2 mm trace over plane
LOOP_LIMIT_NH = 1.8
MAX_PAD_TO_VIA_MM = 2.2
K_INTERNAL, K_EXTERNAL = 0.024, 0.048
DELTA_T_LIMIT_C = 45.0


def point_in_ring(ring: Sequence[Sequence[float]], x: float, y: float) -> bool:
    """Ray casting."""
    inside, n = False, len(ring)
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            if x < (x2 - x1) * (y - y1) / ((y2 - y1) or 1e-12) + x1:
                inside = not inside
    return inside


def reference_layer(layer: str, copper: list[str]) -> str | None:
    """Nearest inner copper plane adjacent to a signal layer."""
    inner = [l for l in copper if l not in ("F.Cu", "B.Cu")]
    if layer == "F.Cu":
        return inner[0] if inner else None
    if layer == "B.Cu":
        return inner[-1] if inner else None
    if layer in inner:
        i = inner.index(layer)
        return inner[i + 1] if i + 1 < len(inner) else inner[i - 1]
    return inner[0] if inner else None


def check_return_paths(board: dict) -> list[dict]:
    copper = board.get("copper_layers", [])
    zones = board.get("zones", [])
    out: list[dict] = []
    for pair in board.get("diff_pairs", []):
        for layer in pair.get("layers", []):
            ref = reference_layer(layer, copper)
            if not ref:
                continue
            rings = [r for z in zones
                     if z.get("net") in ("GND", "GNDA", "GNDD", "0")
                     and ref in z.get("layers", [])
                     for r in z.get("polygons", [])]
            if not rings:
                out.append({"base_net": pair["base_net"], "layer": layer,
                            "reference_layer": ref, "gap_mm": None,
                            "length_over_split_mm": pair.get("total_length_mm", 0.0),
                            "z0_delta_pct": 100.0, "passed": False,
                            "reason": "no reference plane zone on adjacent layer"})
                continue
            over_split = 0.0
            for seg in pair.get("segments", []):
                if seg["layer"] != layer:
                    continue
                mx = (seg["start"][0] + seg["end"][0]) / 2.0
                my = (seg["start"][1] + seg["end"][1]) / 2.0
                if not any(point_in_ring(r, mx, my) for r in rings):
                    over_split += seg["length_mm"]
            gap = RETURN_GAP_MM * 2.0 if over_split else 0.0
            out.append({"base_net": pair["base_net"], "layer": layer,
                        "reference_layer": ref, "gap_mm": round(gap, 3),
                        "length_over_split_mm": round(over_split, 3),
                        "z0_delta_pct": round(100.0 * Z0_SENSITIVITY * gap, 2),
                        "passed": over_split <= SPLIT_TOLERANCE_MM,
                        "reason": "pair crosses reference-plane split" if over_split
                        else "continuous reference"})
    return out


def loop_inductance_nh(pad_to_via_mm: float, n_vias: int = 2,
                       plane_depth_mm: float = 0.8) -> float:
    """Via-to-pad loop inductance for a decoupling cap mounting."""
    via = n_vias * L_VIA_PER_MM * plane_depth_mm
    trace = L_TRACE_PER_MM * pad_to_via_mm
    mutual = 0.35 * math.sqrt(via * trace) if via > 0 and trace > 0 else 0.0
    return via + trace - mutual


def check_decoupling(board: dict) -> list[dict]:
    by_fp: dict[str, list[dict]] = {}
    for pad in board.get("pads", []):
        by_fp.setdefault(pad["footprint"], []).append(pad)
    grounds = [(v["x"], v["y"]) for v in board.get("vias", [])
               if v.get("net") in ("GND", "0")]
    out = []
    for ref, pads in by_fp.items():
        if not ref.upper().startswith("C"):
            continue
        for pad in pads:
            if pad.get("net") not in ("GND", "0"):
                continue
            d = min((math.dist((pad["x"], pad["y"]), g) for g in grounds), default=99.0)
            l_loop = loop_inductance_nh(d)
            out.append({"footprint": ref, "pad": pad["pad"],
                        "pad_to_via_mm": round(d, 3),
                        "loop_inductance_nh": round(l_loop, 3),
                        "limit_nh": LOOP_LIMIT_NH,
                        "passed": l_loop <= LOOP_LIMIT_NH and d <= MAX_PAD_TO_VIA_MM})
    return out


def ipc2152_delta_t(current_a: float, width_mm: float, thickness_oz: float,
                    internal: bool) -> float:
    """IPC-2221/2152 curve fit. Cross-section in mils^2, k=0.024 internal."""
    area_mils2 = width_mm * 39.3701 * thickness_oz * 1.378
    if area_mils2 <= 0:
        return float("inf")
    k = K_INTERNAL if internal else K_EXTERNAL
    return (current_a / (k * area_mils2**0.725)) ** (1.0 / 0.44)


def check_current_density(board: dict, loads: dict[str, float]) -> list[dict]:
    """`loads` maps net name -> continuous amps. Worst segment per net is reported."""
    default_w = board.get("net_classes", {}).get("Default", {}).get("track_width_mm", 0.25)
    worst: dict[str, dict] = {}
    for tr in board.get("tracks", []):
        amps = loads.get(tr["net"])
        if amps is None:
            continue
        width = tr["width_mm"] or default_w
        internal = tr["layer"] not in ("F.Cu", "B.Cu")
        dt = ipc2152_delta_t(amps, width, 1.0, internal)
        row = {"net": tr["net"], "layer": tr["layer"], "width_mm": width,
               "current_a": amps, "internal": internal,
               "delta_t_c": round(min(dt, 999.0), 2), "limit_c": DELTA_T_LIMIT_C,
               "passed": dt <= DELTA_T_LIMIT_C}
        if tr["net"] not in worst or row["delta_t_c"] > worst[tr["net"]]["delta_t_c"]:
            worst[tr["net"]] = row
    return list(worst.values())


def verify(board_json: Path, loads: dict[str, float] | None = None) -> dict:
    board = json.loads(board_json.read_text(encoding="utf-8"))
    checks = {"return_path": check_return_paths(board),
              "decoupling_loop": check_decoupling(board),
              "current_density": check_current_density(board, loads or {})}
    failed = [c for group in checks.values() for c in group if not c.get("passed", True)]
    return {"board": board.get("source"),
            "ratsnest_clean_but_physical_failures": len(failed),
            "checks": checks, "verdict": "pass" if not failed else "fail"}


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Post-layout physical DRC")
    ap.add_argument("board_json", type=Path)
    ap.add_argument("--load", action="append", default=[], metavar="NET=AMPS")
    a = ap.parse_args()
    load_map = {}
    for item in a.load:
        net, _, amps = item.partition("=")
        load_map[net] = float(amps)
    print(json.dumps(verify(a.board_json, load_map), indent=2))
