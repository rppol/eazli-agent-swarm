"""What a validated room is still missing, measured rather than imagined.

`geometry.py` answers "does it fit". This answers the question that comes next
and that a fit-checker normally ducks: the sofa fits, the walkways are clear,
and the room still looks unfinished. Bare walls, an empty corner.

The tempting shortcut is to ask a language model to name a painting. That
produces a product that may not exist, at a size nobody measured, on a wall
that may be behind a door. So finishing is treated as the same kind of problem
as fit: measure the free wall runs and the free floor left over by the
validated layout, size the piece from a stated design rule, and be explicit
that the catalogue cannot supply it.

The captured amazon.sa set today has no wall art, no plants and no mirrors —
it is sofas, tables, storage, rugs and lamps. Every suggestion in those
categories therefore says `in_catalogue: false` and carries the search that
would find one. Naming that gap is more useful than papering over it; it is
also, for a shopping product, the actual finding: **the assortment cannot
finish a room it can furnish.** That statement is checkable, not asserted: see
`CAPTURED_CATEGORIES` below, which reads the real capture rather than
repeating this paragraph as code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from app.catalog import parse_capture
from app.geometry import (FLOOR_COVERING_MAX_H_CM, Placement, Room,
                          WALKWAY_SECONDARY_CM, Wall)

# Galleries hang the centre of a work at eye level and it is the one number
# everyone in the trade agrees on. Everything else here is derived from it.
HANG_CENTRE_CM = 145.0

# A work hung over furniture should span roughly two thirds of it: much less
# and it reads as a postage stamp, much more and it overhangs. 0.675 is the
# middle of the range the rule is usually quoted as (60-75%).
ART_SPAN_OF_ANCHOR = 0.675
ART_SPAN_RANGE = (0.60, 0.75)

# Under this and there is no wall worth hanging anything on.
MIN_HANGABLE_RUN_CM = 90.0

# An item shorter than this does not take the wall away — it is what you hang
# above. A wardrobe does take it away. The threshold sits below the 145 cm hang
# centre with room for the work itself.
BLOCKS_WALL_ABOVE_CM = 120.0

# How close to a wall an item has to be before it counts as against it.
AGAINST_WALL_CM = 30.0

# The smallest floor pocket worth naming: a plant, a stool, a basket.
MIN_POCKET_SIDE_CM = 60.0

GRID_CM = 10.0

WALL_AXIS: dict[Wall, str] = {"N": "x", "S": "x", "E": "y", "W": "y"}
OPPOSITE: dict[Wall, Wall] = {"N": "S", "S": "N", "E": "W", "W": "E"}


@dataclass
class WallRun:
    """A stretch of wall with nothing tall in front of it."""

    wall: Wall
    start_cm: float
    length_cm: float
    anchor: Placement | None = None  # the low piece it sits above, if any


@dataclass
class FloorPocket:
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width_cm(self) -> float:
        return self.x1 - self.x0

    @property
    def depth_cm(self) -> float:
        return self.y1 - self.y0

    @property
    def area_m2(self) -> float:
        return self.width_cm * self.depth_cm / 10_000


@dataclass
class Suggestion:
    kind: str
    width_cm: float
    height_cm: float
    wall: Wall | None
    offset_cm: float | None
    centre_height_cm: float | None
    because: str
    rule: str
    in_catalogue: bool
    search_query: str
    depth_cm: float | None = None
    footprint: tuple[float, float, float, float] | None = None
    at_cm: tuple[float, float] | None = None
    palette: list[str] = field(default_factory=list)


def _axis_span(p: Placement, axis: str) -> tuple[float, float]:
    x0, y0, x1, y1 = p.footprint()
    return (x0, x1) if axis == "x" else (y0, y1)


def _extent(p: Placement, axis: str) -> float:
    """How much of the wall the piece actually occupies.

    Not `dims.w`: a sofa turned to sit against a side wall presents its width
    along that wall only when it is unrotated, and sizing art off the wrong
    number produced a 144 cm print over a 76 cm run.
    """
    a, b = _axis_span(p, axis)
    return b - a


def _against(p: Placement, wall: Wall, room: Room) -> bool:
    x0, y0, x1, y1 = p.footprint()
    if wall == "N":
        return y0 <= AGAINST_WALL_CM
    if wall == "S":
        return y1 >= room.depth_cm - AGAINST_WALL_CM
    if wall == "W":
        return x0 <= AGAINST_WALL_CM
    return x1 >= room.width_cm - AGAINST_WALL_CM


def _subtract(total: float, blocked: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Intervals of [0, total] not covered by `blocked`, merged and clipped."""
    merged: list[list[float]] = []
    for lo, hi in sorted((max(0.0, a), min(total, b)) for a, b in blocked):
        if hi <= lo:
            continue
        if merged and lo <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])

    free: list[tuple[float, float]] = []
    cursor = 0.0
    for lo, hi in merged:
        if lo > cursor:
            free.append((cursor, lo))
        cursor = max(cursor, hi)
    if cursor < total:
        free.append((cursor, total))
    return free


def free_wall_runs(room: Room, placements: Sequence[Placement]) -> list[WallRun]:
    """Stretches of wall you could hang something on.

    A tall item removes the wall behind it. A low one — a sofa, a console, a
    bed — does not: it is the reason to hang something there, so it comes back
    as the run's `anchor`. Doors and windows are removed outright.
    """
    runs: list[WallRun] = []
    for wall in ("N", "E", "S", "W"):
        axis = WALL_AXIS[wall]
        total = room.width_cm if axis == "x" else room.depth_cm

        blocked: list[tuple[float, float]] = []
        lows: list[tuple[tuple[float, float], Placement]] = []
        for p in placements:
            if not p.dims.known or not _against(p, wall, room):
                continue
            span = _axis_span(p, axis)
            if (p.dims.h or 0) >= BLOCKS_WALL_ABOVE_CM:
                blocked.append(span)
            elif (p.dims.h or 0) > FLOOR_COVERING_MAX_H_CM and OPPOSITE[p.facing] == wall:
                # Two exclusions, both learned from bad output. A rug is 2 cm
                # tall and reaches every wall, so proximity alone made it the
                # anchor for the whole room. And a sofa that merely *ends* near
                # a wall is not against it — the wall a piece anchors is the one
                # its back is to, which `facing` already states. Sizing on
                # proximity produced a 54 cm print over a 214 cm sofa.
                lows.append((span, p))

        for opening in list(room.doors) + list(room.windows):
            if opening.wall == wall:
                blocked.append((opening.offset_cm, opening.offset_cm + opening.width_cm))

        for lo, hi in _subtract(total, blocked):
            if hi - lo < MIN_HANGABLE_RUN_CM:
                continue
            # The widest low piece under this run is what the art relates to.
            under = [p for (a, b), p in lows if min(b, hi) - max(a, lo) > 0]
            anchor = max(under, key=lambda p: _extent(p, axis), default=None)
            runs.append(WallRun(wall=wall, start_cm=lo, length_cm=hi - lo, anchor=anchor))
    return runs


def _can_spare_floor(room: Room) -> bool:
    """Whether giving up 60 cm of floor still leaves a walkway.

    A 137 cm toilet has free floor in the arithmetic sense; standing a plant in
    it would block the room. The narrow dimension has to survive the loss.
    """
    return min(room.width_cm, room.depth_cm) - MIN_POCKET_SIDE_CM >= WALKWAY_SECONDARY_CM


def free_floor_pockets(room: Room, placements: Sequence[Placement],
                       limit: int = 3) -> list[FloorPocket]:
    """The largest empty rectangles left on the floor, biggest first.

    A coarse 10 cm occupancy grid and the standard largest-rectangle-under-a-
    histogram scan. At this size (a 400x300 room is 40x30 cells) exactness is
    not worth more than legibility, and the answer only has to be good enough
    to say "there is a metre of nothing in that corner".
    """
    if not _can_spare_floor(room):
        return []

    cols = int(room.width_cm // GRID_CM)
    rows = int(room.depth_cm // GRID_CM)
    grid = [[True] * cols for _ in range(rows)]
    for p in placements:
        if not p.dims.known:
            continue
        x0, y0, x1, y1 = p.footprint()
        for r in range(max(0, int(y0 // GRID_CM)), min(rows, int(-(-y1 // GRID_CM)))):
            for c in range(max(0, int(x0 // GRID_CM)), min(cols, int(-(-x1 // GRID_CM)))):
                grid[r][c] = False

    pockets: list[FloorPocket] = []
    for _ in range(limit):
        best = _largest_rectangle(grid, rows, cols)
        if best is None:
            break
        r0, c0, r1, c1 = best
        if (c1 - c0) * GRID_CM < MIN_POCKET_SIDE_CM or (r1 - r0) * GRID_CM < MIN_POCKET_SIDE_CM:
            break
        pockets.append(FloorPocket(c0 * GRID_CM, r0 * GRID_CM, c1 * GRID_CM, r1 * GRID_CM))
        for r in range(r0, r1):
            for c in range(c0, c1):
                grid[r][c] = False
    return pockets


def _largest_rectangle(grid, rows: int, cols: int):
    heights = [0] * cols
    best_area, best = 0, None
    for r in range(rows):
        for c in range(cols):
            heights[c] = heights[c] + 1 if grid[r][c] else 0
        stack: list[int] = []
        for c in range(cols + 1):
            h = heights[c] if c < cols else 0
            start = c
            while stack and heights[stack[-1]] >= h:
                i = stack.pop()
                area = heights[i] * (c - i)
                if area > best_area:
                    best_area, best = area, (r + 1 - heights[i], i, r + 1, c)
                start = i
            stack.append(start)
    return best


# What each personality reaches for. The point of the table is that the styles
# genuinely diverge — a boho room and a luxury room do not want the same object
# on the same wall, and a recruiter clicking between them should see that.
PERSONALITY: dict[str, list[str]] = {
    "warm": ["wall_art", "large_plant"],
    "boho": ["textile_wall_hanging", "large_plant", "floor_basket"],
    "minimal": ["wall_art", "sculptural_plant"],
    "scandi": ["wall_art", "large_plant"],
    "modern": ["wall_mirror", "sculptural_object"],
    "luxury": ["wall_mirror", "statement_vase"],
    "industrial": ["gallery_pair", "arc_floor_lamp"],
    "mid_century": ["gallery_pair", "large_plant"],
}
DEFAULT_PERSONALITY = ["wall_art", "large_plant"]

WALL_KINDS = {"wall_art", "textile_wall_hanging", "wall_mirror", "gallery_pair"}

# What the amazon.sa capture actually contains, read from the capture itself
# rather than hand-typed here. A hand-typed set is a second copy of the truth
# that silently drifts the moment a re-scrape adds a wall_art or mirror
# listing — this catalogue would keep claiming `in_catalogue: false` for a
# category it now stocks, and nobody would notice because nothing failed.
# Deriving it means the claim can only ever be as stale as `parse_capture`.
CAPTURED_CATEGORIES = frozenset(p.category for p in parse_capture())

# Decor kind -> the catalog category that would satisfy it, if the capture
# ever has one. Only kinds whose category differs from their own name need an
# entry; `CATEGORY_FOR_KIND.get(kind, kind)` is the lookup everywhere else.
CATEGORY_FOR_KIND = {
    "arc_floor_lamp": "floor_lamp",
    "wall_art": "wall_art",
    "gallery_pair": "wall_art",
    "textile_wall_hanging": "wall_art",
    "wall_mirror": "mirror",
    "large_plant": "plant",
    "sculptural_plant": "plant",
    "statement_vase": "vase",
    "floor_basket": "vase",
}

# What the render should draw a floor piece as. The viewer has no opinion
# about decor; it draws the glyph it is told to.
GLYPH_FOR_KIND = {
    "large_plant": "plant", "sculptural_plant": "plant",
    "statement_vase": "vessel", "floor_basket": "vessel",
    "sculptural_object": "form", "arc_floor_lamp": "lamp",
}

SEARCH_FOR_KIND = {
    "wall_art": "framed wall art {w} cm",
    "textile_wall_hanging": "woven wall hanging macrame {w} cm",
    "wall_mirror": "large round wall mirror {w} cm",
    "gallery_pair": "framed print set of 2 metal frame {w} cm",
    "large_plant": "artificial fiddle leaf fig tree 150 cm",
    "sculptural_plant": "artificial olive tree potted 140 cm",
    "sculptural_object": "ceramic sculpture decor object",
    "statement_vase": "large floor vase ceramic 60 cm",
    "floor_basket": "seagrass floor basket blanket storage",
    "arc_floor_lamp": "arc floor lamp black metal",
}

READABLE = {
    "wall_art": "framed artwork", "textile_wall_hanging": "woven wall hanging",
    "wall_mirror": "wall mirror", "gallery_pair": "pair of framed prints",
    "large_plant": "large potted plant", "sculptural_plant": "sculptural potted tree",
    "sculptural_object": "sculptural object", "statement_vase": "floor vase",
    "floor_basket": "floor basket", "arc_floor_lamp": "arc floor lamp",
}


def _corner_spot(pocket: FloorPocket, room: Room, size: float) -> tuple[float, float]:
    """Where in an empty pocket a floor piece actually goes.

    The centre of the pocket is the wrong answer and it looked it: a plant
    marooned in the middle of nine square metres of floor. Real ones go in the
    corner. So pick whichever corner of the pocket is nearest a corner of the
    room and tuck the piece into it.
    """
    corners = [(pocket.x0, pocket.y0), (pocket.x1, pocket.y0),
               (pocket.x0, pocket.y1), (pocket.x1, pocket.y1)]
    room_corners = [(0.0, 0.0), (room.width_cm, 0.0),
                    (0.0, room.depth_cm), (room.width_cm, room.depth_cm)]
    cx, cy = min(corners, key=lambda c: min((c[0] - r[0]) ** 2 + (c[1] - r[1]) ** 2
                                            for r in room_corners))
    half = size / 2
    return (cx + half if cx <= (pocket.x0 + pocket.x1) / 2 else cx - half,
            cy + half if cy <= (pocket.y0 + pocket.y1) / 2 else cy - half)


def _clamp_span(anchor_w: float, run_len: float) -> float:
    lo, hi = anchor_w * ART_SPAN_RANGE[0], anchor_w * ART_SPAN_RANGE[1]
    return round(max(min(max(anchor_w * ART_SPAN_OF_ANCHOR, lo), hi, run_len - 10), 40))


def suggest_finishing(room: Room, placements: Sequence[Placement],
                      style: Sequence[str]) -> list[Suggestion]:
    """Finishing pieces the room has room for, sized from what is already in it.

    Nothing here is sourced. Each suggestion is a measurement and a rule, plus
    the query that would find the object — which is the honest shape of the
    answer when the catalogue does not stock the category.
    """
    kinds: list[str] = []
    for s in style:
        for k in PERSONALITY.get(s, []):
            if k not in kinds:
                kinds.append(k)
    if not kinds:
        kinds = list(DEFAULT_PERSONALITY)

    # A suggestion for something the planner already sourced is noise. The one
    # decor kind the catalogue does stock is the floor lamp, and the plan puts
    # one in most rooms, so an "arc floor lamp" was being offered next to the
    # lamp it duplicated.
    already = {p.resolved_role() for p in placements}
    kinds = [k for k in kinds if CATEGORY_FOR_KIND.get(k) not in already]

    runs = free_wall_runs(room, placements)
    # Prefer a run that sits over something — art over a sofa beats art over
    # nothing — then the longest.
    runs.sort(key=lambda r: (_extent(r.anchor, WALL_AXIS[r.wall])
                             if r.anchor is not None else 0.0,
                             r.length_cm), reverse=True)
    pockets = free_floor_pockets(room, placements)

    out: list[Suggestion] = []
    used_walls: set[Wall] = set()
    pocket_i = 0

    for kind in kinds:
        if kind in WALL_KINDS:
            run = next((r for r in runs if r.wall not in used_walls), None)
            if run is None:
                continue
            used_walls.add(run.wall)
            if run.anchor is not None:
                extent = _extent(run.anchor, WALL_AXIS[run.wall])
                width = _clamp_span(extent, run.length_cm)
                because = (
                    f"the {run.anchor.role} against the {run.wall} wall runs "
                    f"{extent:.0f} cm along it and stands only "
                    f"{run.anchor.dims.h:.0f} cm tall, so the wall above it is "
                    f"the strongest place in the room to hang something"
                )
            else:
                width = round(min(run.length_cm * 0.6, 140))
                because = (f"{run.length_cm:.0f} cm of the {run.wall} wall is bare "
                           f"with nothing standing against it")
            centre = run.start_cm + run.length_cm / 2
            if run.anchor is not None:
                a0, a1 = _axis_span(run.anchor, WALL_AXIS[run.wall])
                centre = (a0 + a1) / 2
            # Centred on the anchor, but never hanging off the end of the run.
            offset = min(max(centre - width / 2, run.start_cm),
                         run.start_cm + run.length_cm - width)
            out.append(Suggestion(
                kind=kind, width_cm=width, height_cm=round(width * 0.75),
                wall=run.wall, offset_cm=round(offset, 1),
                centre_height_cm=HANG_CENTRE_CM, because=because,
                rule=(f"hang the centre at {HANG_CENTRE_CM:.0f} cm and span "
                      f"{ART_SPAN_RANGE[0]:.0%}\u2013{ART_SPAN_RANGE[1]:.0%} of the piece below"),
                in_catalogue=CATEGORY_FOR_KIND.get(kind, kind) in CAPTURED_CATEGORIES,
                search_query=SEARCH_FOR_KIND[kind].format(w=int(width)),
            ))
        else:
            if pocket_i >= len(pockets):
                continue
            p = pockets[pocket_i]
            pocket_i += 1
            size = min(MIN_POCKET_SIDE_CM, p.width_cm, p.depth_cm)
            spot = _corner_spot(p, room, size)
            out.append(Suggestion(
                kind=kind, width_cm=size, height_cm=150,
                depth_cm=size, wall=None, at_cm=spot,
                offset_cm=None, centre_height_cm=None,
                because=(f"{p.area_m2:.1f} m² of floor is empty here; the corner "
                         f"at ({spot[0]:.0f}, {spot[1]:.0f}) cm takes a piece "
                         f"without narrowing a walkway"),
                footprint=(p.x0, p.y0, p.x1, p.y1),
                rule=(f"a floor piece needs {MIN_POCKET_SIDE_CM:.0f} cm square and must "
                      f"leave {WALKWAY_SECONDARY_CM:.0f} cm of passage"),
                in_catalogue=CATEGORY_FOR_KIND.get(kind, kind) in CAPTURED_CATEGORIES,
                search_query=SEARCH_FOR_KIND[kind],
            ))
    return out


def to_dict(s: Suggestion) -> dict:
    return {
        "kind": s.kind, "label": READABLE.get(s.kind, s.kind),
        "glyph": GLYPH_FOR_KIND.get(s.kind, "form"),
        "width_cm": s.width_cm, "height_cm": s.height_cm, "depth_cm": s.depth_cm,
        "wall": s.wall, "offset_cm": s.offset_cm,
        "centre_height_cm": s.centre_height_cm,
        "footprint": list(s.footprint) if s.footprint else None,
        "at_cm": list(s.at_cm) if s.at_cm else None,
        "because": s.because, "rule": s.rule,
        "in_catalogue": s.in_catalogue, "search_query": s.search_query,
    }
