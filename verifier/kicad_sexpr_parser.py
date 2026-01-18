"""Native .kicad_pcb S-expression parser -> board.agentic.json.

Reads a KiCad 7/8 board with no KiCad install. Extracts the physical facts that
electrical checks cannot see: copper zone polygons, net classes, differential
pair segment geometry, via stitching, and footprint pad positions, flattened to
deterministic JSON. Reports geometry only; makes no design decisions.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_TOK = re.compile(r'\(|\)|"(?:[^"\\]|\\.)*"|[^\s()"]+')


def parse_sexpr(text: str) -> list:
    """Nested Python lists; quoted atoms lose their quotes."""
    stack: list[list] = [[]]
    for tok in _TOK.findall(text):
        if tok == "(":
            stack.append([])
        elif tok == ")":
            if len(stack) < 2:
                raise ValueError("unbalanced ')' in board file")
            node = stack.pop()
            stack[-1].append(node)
        else:
            stack[-1].append(tok[1:-1] if tok.startswith('"') else tok)
    if len(stack) != 1:
        raise ValueError("unbalanced '(' in board file")
    return stack[0]


def kids(node: list, key: str) -> list[list]:
    return [c for c in node if isinstance(c, list) and c and c[0] == key]


def one(node: list, key: str) -> list | None:
    found = kids(node, key)
    return found[0] if found else None


def num(node: list, key: str, default: float = 0.0) -> float:
    child = one(node, key)
    try:
        return float(child[1]) if child and len(child) > 1 else default
    except (TypeError, ValueError):
        return default


def text(node: list, key: str, default: str = "") -> str:
    child = one(node, key)
    return str(child[1]) if child and len(child) > 1 else default


def net_table(root: list) -> dict[str, str]:
    """Board-level (net 10 "USB3_DP") declarations."""
    return {str(n[1]): str(n[2]) for n in kids(root, "net") if len(n) > 2}


def net_name(node: list, table: dict[str, str]) -> str:
    """Segments carry a net id only; pads and zones may carry the name inline."""
    child = one(node, "net")
    if not child or len(child) < 2:
        return ""
    return str(child[2]) if len(child) > 2 else table.get(str(child[1]), str(child[1]))


def dist(a: list[float], b: list[float]) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def parse_net_classes(root: list) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for block in kids(root, "net_class"):
        name = str(block[1]) if len(block) > 1 and isinstance(block[1], str) else "Default"
        out[name] = {"clearance_mm": num(block, "clearance"),
                     "track_width_mm": num(block, "trace_width"),
                     "via_dia_mm": num(block, "via_dia"),
                     "via_drill_mm": num(block, "via_drill"),
                     "diff_pair_width_mm": num(block, "diff_pair_width"),
                     "diff_pair_gap_mm": num(block, "diff_pair_gap")}
    return out


def parse_zones(root: list, table: dict[str, str]) -> list[dict]:
    zones = []
    for zone in kids(root, "zone"):
        layer = one(zone, "layer")
        layers = [str(layer[1])] if layer else [str(l[1]) for l in kids(zone, "layers") if len(l) > 1]
        polygons = []
        for poly in kids(zone, "polygon"):
            pts = one(poly, "pts")
            ring = [[float(xy[1]), float(xy[2])] for xy in kids(pts, "xy") if len(xy) >= 3] if pts else []
            if ring:
                polygons.append(ring)
        zones.append({"net": text(zone, "net_name") or net_name(zone, table),
                      "layers": layers, "priority": int(num(zone, "priority")),
                      "filled": bool(one(zone, "filled_polygon")),
                      "polygons": polygons})
    return zones


def parse_tracks(root: list, table: dict[str, str]) -> list[dict]:
    """Every routed segment, not just differential pairs."""
    tracks = []
    for seg in kids(root, "segment"):
        start, end = one(seg, "start"), one(seg, "end")
        if not start or not end:
            continue
        p0 = [float(start[1]), float(start[2])]
        p1 = [float(end[1]), float(end[2])]
        tracks.append({"net": net_name(seg, table), "layer": text(seg, "layer"),
                       "width_mm": num(seg, "width"), "length_mm": round(dist(p0, p1), 4)})
    return tracks


def parse_diff_pairs(root: list, net_classes: dict[str, dict],
                     table: dict[str, str]) -> list[dict]:
    """Group segments by diff-pair base net (names ending _P/_N or +/-)."""
    groups: dict[str, list[dict]] = {}
    for seg in kids(root, "segment"):
        net = net_name(seg, table)
        base = re.sub(r"[_+-]?([PNpn]|[+-])$", "", net)
        start, end = one(seg, "start"), one(seg, "end")
        if not net or base == net or not start or not end:
            continue
        p0 = [float(start[1]), float(start[2])]
        p1 = [float(end[1]), float(end[2])]
        groups.setdefault(base, []).append({
            "net": net, "layer": text(seg, "layer"), "width_mm": num(seg, "width"),
            "start": p0, "end": p1, "length_mm": round(dist(p0, p1), 4)})

    cls = net_classes.get("HighSpeed", net_classes.get("Default", {}))
    pairs = []
    for base, segs in groups.items():
        if len({s["net"] for s in segs}) < 2:
            continue  # a single-ended net with a P/N-looking suffix is not a pair
        pairs.append({"base_net": base,
                      "nets": sorted({s["net"] for s in segs}),
                      "layers": sorted({s["layer"] for s in segs}),
                      "widths_mm": sorted({s["width_mm"] for s in segs}),
                      "total_length_mm": round(sum(s["length_mm"] for s in segs), 3),
                      "target_gap_mm": cls.get("diff_pair_gap_mm", 0.0),
                      "segments": segs})
    return pairs


def parse_vias(root: list, table: dict[str, str]) -> list[dict]:
    vias = []
    for via in kids(root, "via"):
        at = one(via, "at")
        if not at:
            continue
        vias.append({"x": float(at[1]), "y": float(at[2]), "net": net_name(via, table),
                     "size_mm": num(via, "size"), "drill_mm": num(via, "drill"),
                     "layers": [str(l[1]) for l in kids(via, "layers") if len(l) > 1]})
    return vias


def parse_footprints(root: list, table: dict[str, str]) -> tuple[list[dict], list[dict]]:
    """Footprints plus their pads flattened to absolute board coordinates."""
    fps, pads = [], []
    for fp in kids(root, "footprint"):
        ref = next((str(p[2]) for p in kids(fp, "property")
                    if len(p) > 2 and p[1] == "Reference"), "")
        if not ref:
            ref = next((str(t[2]) for t in kids(fp, "fp_text")
                        if len(t) > 2 and t[1] == "reference"), "")
        at = one(fp, "at")
        fx, fy = (float(at[1]), float(at[2])) if at else (0.0, 0.0)
        fps.append({"ref": ref, "x": fx, "y": fy, "layer": text(fp, "layer")})
        for pad in kids(fp, "pad"):
            pat = one(pad, "at")
            px, py = (float(pat[1]), float(pat[2])) if pat else (0.0, 0.0)
            pads.append({"footprint": ref,
                         "pad": str(pad[1]) if len(pad) > 1 else "",
                         "type": str(pad[2]) if len(pad) > 2 else "",
                         "shape": str(pad[3]) if len(pad) > 3 else "",
                         "x": round(fx + px, 4), "y": round(fy + py, 4),
                         "net": net_name(pad, table),
                         "layers": [str(l[1]) for l in kids(pad, "layers") if len(l) > 1]})
    return fps, pads


def parse_board(path: Path) -> dict:
    roots = parse_sexpr(path.read_text(encoding="utf-8-sig", errors="replace"))
    root = next((n for n in roots if isinstance(n, list)), None)
    if root is None:
        raise ValueError(f"{path.name}: no top-level (kicad_pcb ...) node")
    layers = one(root, "layers")
    table = net_table(root)
    net_classes = parse_net_classes(root)
    fps, pads = parse_footprints(root, table)
    return {"source": path.name,
            "copper_layers": [str(e[1]) for e in (layers[1:] if layers else [])
                              if isinstance(e, list) and len(e) > 1
                              and str(e[1]).endswith(".Cu")],
            "net_classes": net_classes, "zones": parse_zones(root, table),
            "diff_pairs": parse_diff_pairs(root, net_classes, table),
            "tracks": parse_tracks(root, table), "vias": parse_vias(root, table),
            "pads": pads, "footprints": fps}


def emit(board_path: Path, out_path: Path | None = None) -> dict:
    facts = parse_board(board_path)
    out = out_path or board_path.with_name("board.agentic.json")
    out.write_text(json.dumps(facts, indent=2), encoding="utf-8")
    return {"written": str(out), **{k: len(facts[k]) for k in
            ("zones", "diff_pairs", "tracks", "vias", "pads")}}


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="KiCad board -> board.agentic.json")
    ap.add_argument("board", type=Path)
    ap.add_argument("-o", "--out", type=Path, default=None)
    a = ap.parse_args()
    print(json.dumps(emit(a.board, a.out), indent=2))
