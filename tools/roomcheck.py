"""Look at a planned room, as text, so the visual check can be automated.

The request behind this was "for every render pass through a visual check".
Nobody eyeballs 200 rooms and no agent opens 200 browser tabs, so the picture
has to be something a terminal, a diff and a CI log can all hold. This draws
the same plan `tools/export_static.py` ships, top down, with the floor you can
reach from the front door shaded — which is the one thing the 3D studio view
does not show and the thing that was wrong.

    PYTHONPATH=. uv run python tools/roomcheck.py unit01 living_dining 15000 warm minimal
    PYTHONPATH=. uv run python tools/roomcheck.py --all
    PYTHONPATH=. uv run python tools/roomcheck.py --all --limit 5 --maps

`--all` sweeps every unit x room x style x tier the exporter builds and ranks
them by how bad their circulation is, worst first, so the rooms worth looking
at surface without anyone guessing which those are.

Legend
    .   floor you can walk to from the door
    #   floor you would fit on but cannot reach — this is the defect
    (blank) no room to stand: inside furniture, or too close to it to fit
    A-Z the placed items, keyed underneath
    a-z a floor covering you can walk to; the same item in UPPER CASE is a
        stretch of rug you cannot get to
    v^<> the door, pointing the way in
"""

from __future__ import annotations

import argparse
import sys

from app.geometry import (
    CIRCULATION_MIN_COVERAGE,
    FLOOR_COVERING_MAX_H_CM,
    SHOULDER_WIDTH_CM,
    Circulation,
    Dims,
    Placement,
    Room,
    circulation_map,
    validate_layout,
)
from app.home import load_home
from app.planner import BUDGET_TIERS, RECIPES, auto_plan

# One character is 10cm across and 20cm down. Not square, because a terminal
# character is not square either: at 10x20 the drawing has roughly the
# proportions of the room it is drawing, which is the entire point of looking
# at it. A 335x551cm living room comes out 34 characters by 28 lines.
CHAR_W_CM = 10.0
CHAR_H_CM = 20.0

REACHABLE = "."
STRANDED = "#"
NO_ROOM = " "
# The arrow points the way in, and is deliberately not a letter: the first
# drawing of unit01's living/dining room marked the door "D" and the dining
# table was also item D, so the north wall and the middle of the room were
# labelled the same thing.
DOOR_MARK = {"N": "v", "S": "^", "W": ">", "E": "<"}

# Every style the exporter builds, in its order, so `--all` sweeps exactly the
# set that ships rather than a hand-copied subset that can drift from it.
STYLES = [
    ["warm", "minimal"],
    ["modern", "luxury"],
    ["industrial", "mid_century"],
    ["boho", "scandi"],
    [],
]


def placements_of(plan) -> list[Placement]:
    return [
        Placement(i.asin, Dims(i.dims_cm["w"], i.dims_cm["d"], i.dims_cm["h"],
                               confidence="stated"),
                  x=i.x, y=i.y, facing=i.facing, role=i.role)
        for i in plan.placed
    ]


def _door_columns(room: Room, cols: int) -> tuple[str, set[int], set[int]]:
    """Which wall the door is on, and which characters of it to mark."""
    if not room.doors:
        return "", set(), set()
    door = room.doors[0]
    if door.wall in ("N", "S"):
        lo = int(door.offset_cm / CHAR_W_CM)
        hi = max(lo + 1, int((door.offset_cm + door.width_cm) / CHAR_W_CM))
        return door.wall, set(range(lo, min(cols, hi))), set()
    lo = int(door.offset_cm / CHAR_H_CM)
    hi = max(lo + 1, int((door.offset_cm + door.width_cm) / CHAR_H_CM))
    return door.wall, set(), set(range(lo, hi))


def render(room: Room, placements: list[Placement], circ: Circulation) -> str:
    """The room from above, one character per 10x20cm of floor."""
    cols = max(1, int(round(room.width_cm / CHAR_W_CM)))
    rows = max(1, int(round(room.depth_cm / CHAR_H_CM)))

    keys = {p.item_id: chr(ord("A") + i) for i, p in enumerate(placements)}
    grid = [[NO_ROOM] * cols for _ in range(rows)]

    for j in range(rows):
        y0, y1 = j * CHAR_H_CM, (j + 1) * CHAR_H_CM
        for i in range(cols):
            x0, x1 = i * CHAR_W_CM, (i + 1) * CHAR_W_CM
            if circ.reachable_in(x0, y0, x1, y1):
                grid[j][i] = REACHABLE
            elif circ.passable_in(x0, y0, x1, y1):
                grid[j][i] = STRANDED

    # Furniture last so it draws over the floor, and the tall pieces last of
    # all so a rug never hides the sofa standing on it.
    #
    # A floor covering is drawn in lower case and keeps the reachability it is
    # lying on, because it is not an obstacle and the first version of this
    # drawing lost that: the rug in unit01's living/dining room is 300x200cm
    # and blanked out the whole north half of the map as if it were furniture,
    # hiding exactly the shading the tool exists to show.
    for p in sorted(placements, key=lambda q: q.dims.h or 0):
        x0, y0, x1, y1 = p.footprint()
        key = keys[p.item_id]
        covering = (p.dims.h or 0) <= FLOOR_COVERING_MAX_H_CM
        for j in range(max(0, int(y0 / CHAR_H_CM)),
                       min(rows, int(round(y1 / CHAR_H_CM)))):
            for i in range(max(0, int(x0 / CHAR_W_CM)),
                           min(cols, int(round(x1 / CHAR_W_CM)))):
                if not covering:
                    grid[j][i] = key
                elif grid[j][i] in (REACHABLE, NO_ROOM):
                    grid[j][i] = key.lower()
                elif grid[j][i] == STRANDED:
                    grid[j][i] = key

    wall, door_cols, door_rows = _door_columns(room, cols)
    mark = DOOR_MARK.get(wall, "-")
    top = "".join(mark if wall == "N" and i in door_cols else "-"
                  for i in range(cols))
    bottom = "".join(mark if wall == "S" and i in door_cols else "-"
                     for i in range(cols))
    out = [f"+{top}+"]
    for j, line in enumerate(grid):
        left = mark if wall == "W" and j in door_rows else "|"
        right = mark if wall == "E" and j in door_rows else "|"
        out.append(f"{left}{''.join(line)}{right}")
    out.append(f"+{bottom}+")
    return "\n".join(out)


def report(unit: str, room_name: str, budget: float, style: list[str],
           maps: bool = True) -> dict:
    home = load_home()
    plan = auto_plan(unit, room_name, budget, style)
    room = home.unit(unit).room(room_name).to_room()
    placements = placements_of(plan)
    circ = circulation_map(room, placements)
    verdict = validate_layout(room, placements)

    label = (f"{unit} {room_name} {budget:.0f} "
             f"{'+'.join(style) if style else 'any'}")
    if not maps:
        return {"label": label, "circ": circ, "plan": plan, "verdict": verdict}

    door = room.doors[0] if room.doors else None
    print(f"== {label}")
    print(f"   room {room.width_cm:.0f} x {room.depth_cm:.0f}cm"
          + (f", door {door.wall} wall offset {door.offset_cm:.0f} "
             f"width {door.width_cm:.0f}" if door else ", no door on record"))
    print()
    print(render(room, placements, circ))
    print()

    keys = {p.item_id: chr(ord("A") + i) for i, p in enumerate(placements)}
    for item, p in zip(plan.placed, placements):
        x0, y0, x1, y1 = p.footprint()
        got_to = circ.reachable_in(max(0.0, x0 - SHOULDER_WIDTH_CM),
                                   max(0.0, y0 - SHOULDER_WIDTH_CM),
                                   x1 + SHOULDER_WIDTH_CM,
                                   y1 + SHOULDER_WIDTH_CM)
        print(f"   {keys[p.item_id]}  {item.role:<19} "
              f"({x0:>5.0f},{y0:>5.0f})-({x1:>5.0f},{y1:>5.0f})  "
              f"h{item.dims_cm['h'] or 0:<4.0f} {p.facing}  "
              f"{'' if got_to else '<- nothing reaches it'}")
    for slot in plan.unfilled:
        print(f"   -  {slot.role:<19} UNFILLED: {slot.reason}")
    print()
    print(f"   reachable {circ.reachable_cells}/{circ.passable_cells} cells "
          f"({circ.coverage:.1%} of the floor a {SHOULDER_WIDTH_CM:.0f}cm "
          f"shoulder fits on; the rule wants "
          f"{CIRCULATION_MIN_COVERAGE:.0%})")
    print(f"   validation {verdict.status}")
    for reason in verdict.reasons:
        print(f"     ! {reason}")
    for note in verdict.details.get("notes", []):
        print(f"     ~ {note}")
    print()
    return {"label": label, "circ": circ, "plan": plan, "verdict": verdict}


def sweep(limit: int | None, maps: bool) -> int:
    """Every configuration the exporter builds, worst circulation first."""
    home = load_home()
    rows = []
    for unit in home.units:
        for room_spec in unit.rooms:
            if room_spec.name not in RECIPES:
                continue
            for style in STYLES:
                for tier in BUDGET_TIERS:
                    rows.append(report(unit.id, room_spec.name, tier["sar"],
                                       style, maps=False))
    rows.sort(key=lambda r: (r["circ"].coverage, r["label"]))

    print(f"{'cover':>7}  {'cells':>12}  {'valid':<10} configuration")
    print("-" * 96)
    for r in rows:
        circ, verdict = r["circ"], r["verdict"]
        flag = "" if circ.coverage >= CIRCULATION_MIN_COVERAGE else "  <-- BELOW"
        print(f"{circ.coverage:>7.1%}  "
              f"{circ.reachable_cells:>5}/{circ.passable_cells:<6} "
              f"{verdict.status:<10} {r['label']}{flag}")
        for reason in verdict.reasons:
            print(f"{'':>9}! {reason[:150]}")
        for slot in r["plan"].unfilled:
            print(f"{'':>9}- {slot.slot_id} unfilled: {slot.reason[:120]}")

    worst = rows[0]["circ"].coverage if rows else 1.0
    median = rows[len(rows) // 2]["circ"].coverage if rows else 1.0
    below = sum(1 for r in rows
                if r["circ"].coverage < CIRCULATION_MIN_COVERAGE)
    print("-" * 96)
    print(f"{len(rows)} configurations   worst {worst:.1%}   median {median:.1%}"
          f"   below the {CIRCULATION_MIN_COVERAGE:.0%} rule: {below}")

    if maps:
        print()
        for r in rows[:limit or 3]:
            unit, room_name, budget, style = r["label"].split()
            report(unit, room_name, float(budget),
                   [] if style == "any" else style.split("+"))
    return 1 if below else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Top-down text render of a planned room, with the floor "
                    "reachable from the door shaded.")
    parser.add_argument("unit", nargs="?", help="e.g. unit01")
    parser.add_argument("room", nargs="?", help="e.g. living_dining")
    parser.add_argument("budget", nargs="?", type=float, help="e.g. 15000")
    parser.add_argument("style", nargs="*", help="e.g. warm minimal")
    parser.add_argument("--all", action="store_true",
                        help="sweep every configuration, worst first")
    parser.add_argument("--limit", type=int, default=3,
                        help="with --all --maps, how many rooms to draw")
    parser.add_argument("--maps", action="store_true",
                        help="with --all, draw the worst rooms too")
    args = parser.parse_args(argv)

    if args.all:
        return sweep(args.limit, args.maps)
    if not (args.unit and args.room and args.budget):
        parser.error("give unit, room and budget — or --all")
    report(args.unit, args.room, args.budget, args.style)
    return 0


if __name__ == "__main__":
    sys.exit(main())
