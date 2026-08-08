"""Deterministic spatial reasoning. No model ever decides whether something fits.

eazli's AI Agent Disclaimers page warns that agent outputs "may be incomplete,
inaccurate, or contain incorrect assumptions", and their FAQ tells users to verify
"especially for measurements, installation, safety, or compliance". The policy's
Fitment clause goes further and disclaims
liability for whether an item "can be delivered, moved in, installed, or used as
intended" through "doorways, hallways, stairs, and elevators".

So this module answers two separate questions, and it answers them in Python:

    check_fit          does the item work *in* the room?
    check_access_path  can the item physically *get to* the room?

An item can pass the first and fail the second. That case is the whole point.

Units are centimetres throughout.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from itertools import permutations
from typing import Literal, Sequence

Status = Literal["pass", "fail", "unverified"]
Wall = Literal["N", "S", "E", "W"]

# --------------------------------------------------------------------------
# Design rules. Each carries its rationale so Noura can cite *why*, not just
# assert. These are also loaded into the `design_principles` Chroma collection.
# --------------------------------------------------------------------------

RULES: dict[str, dict] = {
    "walkway_primary": {
        "value_cm": 90,
        "text": "A primary circulation route needs at least 90cm so two people can pass "
                "and furniture can be carried through.",
    },
    "walkway_secondary": {
        "value_cm": 75,
        "text": "A secondary route used by one person at a time needs at least 75cm.",
    },
    "sofa_to_coffee_table": {
        "value_cm": 40,
        "text": "Leave 40-45cm between the sofa front and the coffee table: close enough "
                "to reach, far enough to walk past and to stand up without knocking it.",
    },
    "door_swing": {
        "text": "An inward-swinging door sweeps a quarter-disc equal to its leaf width. "
                "Nothing may occupy that arc.",
    },
    "pull_out": {
        "value_cm": 75,
        "text": "A dining chair needs about 75cm behind it to push back and stand "
                "up. In front of it is the table, not a walkway.",
    },
    "coffee_table_reach": {
        "value_cm": 100,
        "text": "A coffee table has to be reachable from the seating it serves — "
                "within about a metre, and in front of it rather than behind. A "
                "table that satisfies every clearance but sits out of reach is "
                "legal and useless.",
    },
}

WALKWAY_PRIMARY_CM = RULES["walkway_primary"]["value_cm"]
WALKWAY_SECONDARY_CM = RULES["walkway_secondary"]["value_cm"]
SOFA_TO_TABLE_MIN_CM = RULES["sofa_to_coffee_table"]["value_cm"]
COFFEE_TABLE_REACH_CM = RULES["coffee_table_reach"]["value_cm"]

# Below this, a "pass" is not a comfortable one. Stated opening widths are
# nominal: hinges, door stops and the hands carrying the item all eat into
# them, so a 1cm margin on paper is no margin in a stairwell.
TIGHT_MARGIN_CM = 3.0


# --------------------------------------------------------------------------
# Value types
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Dims:
    """Item dimensions. `confidence` records how much we actually trust them.

    Real marketplace listings frequently omit dimensions or bury them in free
    text, so the provenance travels with the numbers. `missing` must never be
    silently treated as zero or guessed.
    """

    w: float | None
    d: float | None
    h: float | None
    confidence: Literal["stated", "parsed", "inferred", "missing"] = "stated"

    @property
    def known(self) -> bool:
        """True only for three real, positive, finite measurements.

        A bare `is not None` check let NaN through, and every comparison
        against NaN is False — so no rule ever appended a reason and the item
        came back `pass`. Negatives and zeros did the same. Anything that is
        not a usable measurement is treated as no measurement at all.
        """
        return all(
            v is not None and math.isfinite(v) and v > 0
            for v in (self.w, self.d, self.h)
        )

    @property
    def triple(self) -> tuple[float, float, float]:
        if not self.known:
            raise ValueError("dimensions unknown")
        return (self.w, self.d, self.h)  # type: ignore[return-value]


@dataclass(frozen=True)
class Door:
    wall: Wall
    offset_cm: float
    width_cm: float
    swing: Literal["in", "out", "none"] = "in"


@dataclass
class Room:
    name: str
    width_cm: float
    depth_cm: float
    height_cm: float = 280
    doors: list[Door] = field(default_factory=list)
    windows: list[Door] = field(default_factory=list)
    fixed: list["Placement"] = field(default_factory=list)


SEATING_ROLES = {"sofa", "armchair", "chair", "seat", "dining_chair", "dining_chairs_pair"}
DINING_CHAIR_ROLES = {"dining_chair", "dining_chairs_pair"}
TABLE_ROLES = {"coffee_table", "dining_table", "table", "side_table"}
WALL_ROLES = {"bed", "wardrobe", "bookshelf", "tv_console", "floor_lamp", "rug", "other"}
KNOWN_ROLES = SEATING_ROLES | TABLE_ROLES | WALL_ROLES

# How much clear space each role needs in front of it, and why they differ.
#
#   seating  — a real circulation route runs past a sofa, so the 90cm rule
#   tables   — what a dining table needs behind it is chair pull-out room, not
#              a walkway; demanding 90cm there was a seating rule fired at the
#              wrong role, and it cost the catalogue's best-reviewed table
#   wall     — a console or bookshelf lives against the wall by design
#
# Both check_fit and validate_layout resolve clearance through this table, so
# the two endpoints cannot disagree about the same item again.
FRONT_CLEARANCE_BY_ROLE: dict[str, float] = {
    **{r: WALKWAY_PRIMARY_CM for r in SEATING_ROLES},
    **{r: WALKWAY_SECONDARY_CM for r in TABLE_ROLES},
    **{r: WALKWAY_SECONDARY_CM for r in DINING_CHAIR_ROLES},
    **{r: 0.0 for r in WALL_ROLES},
}


def front_clearance_for(role: str | None) -> float | None:
    """None means "we cannot tell which rule applies".

    Guessing the strictest rule for an unidentified item sounds safe but is
    not: it produces a confident `fail` for a reason that may be entirely
    spurious. The honest answer to "what rule applies here?" when we do not
    know is `unverified`, not a rejection.
    """
    if role is None:
        return None
    return FRONT_CLEARANCE_BY_ROLE.get(role, WALKWAY_PRIMARY_CM)


@dataclass
class Placement:
    """An item positioned in a room. Origin is the room's north-west corner,
    x runs east, y runs south. `facing` is the direction the usable front points.

    `role` says what the item *is*, which decides which rules apply to it. It
    used to be inferred by substring-matching `item_id`, but callers naturally
    pass product SKUs as ids — and an ASIN matches nothing, so the seating and
    sofa-to-table rules were silently skipped and the layout came back clean.
    Declare the role. An undeclared one that cannot be inferred makes the whole
    layout `unverified`, because a rule that did not run has not been passed.
    """

    item_id: str
    dims: Dims
    x: float
    y: float
    rotation: Literal[0, 90] = 0
    facing: Wall = "S"
    role: str | None = None

    def resolved_role(self) -> str | None:
        if self.role:
            return self.role if self.role in KNOWN_ROLES else None
        lowered = self.item_id.lower()
        for candidate in sorted(KNOWN_ROLES, key=len, reverse=True):
            if candidate in lowered:
                return candidate
        return None

    def footprint(self) -> tuple[float, float, float, float]:
        """(x0, y0, x1, y1) of the item's floor area."""
        w, d = self.dims.w, self.dims.d
        if not self.dims.known:
            raise ValueError("dimensions unknown")
        if self.rotation == 90:
            w, d = d, w
        return (self.x, self.y, self.x + w, self.y + d)  # type: ignore[operator]


@dataclass
class PathSegment:
    """One leg of the delivery route: a door, a corridor, a right-angle turn,
    a lift car, or a stair flight.
    """

    name: str
    kind: Literal["door", "corridor", "turn", "lift", "stair"]
    width_cm: float
    height_cm: float
    depth_cm: float | None = None       # lift cars only
    turn_into_cm: float | None = None   # turns only: width of the corridor turned into


@dataclass
class Verdict:
    status: Status
    reasons: list[str] = field(default_factory=list)
    details: dict = field(default_factory=dict)


def _combine(statuses: Sequence[Status]) -> Status:
    # Nothing checked is not everything passed. An empty route or an empty
    # layout must not read as a clean bill of health.
    if not statuses:
        return "unverified"
    if "fail" in statuses:
        return "fail"
    if "unverified" in statuses:
        return "unverified"
    return "pass"


def _overlaps(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> bool:
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def _unverified(dims: Dims) -> Verdict:
    return Verdict(
        status="unverified",
        reasons=[
            "Item dimensions are unverified, so fit cannot be guaranteed. "
            "Confirm the measurements with the seller before ordering."
        ],
        details={"dims_confidence": dims.confidence},
    )


# --------------------------------------------------------------------------
# check_fit — does the item work in the room?
# --------------------------------------------------------------------------

def check_fit(
    placement: Placement,
    room: Room,
    front_clearance_cm: float | None = None,
    others: Sequence["Placement"] = (),
) -> Verdict:
    """Does the item work in the room?

    `front_clearance_cm` defaults to whatever the item's role requires. Pass a
    number to override, or 0 to skip. `others` are the rest of the layout: the
    walkway in front of an item is blocked by furniture, not only by walls.
    """
    if not placement.dims.known:
        return _unverified(placement.dims)

    role_unknown = False
    if front_clearance_cm is None:
        front_clearance_cm = front_clearance_for(placement.resolved_role())
        if front_clearance_cm is None:
            role_unknown, front_clearance_cm = True, 0.0

    reasons: list[str] = []
    x0, y0, x1, y1 = placement.footprint()

    if x0 < 0 or y0 < 0 or x1 > room.width_cm or y1 > room.depth_cm:
        reasons.append(
            f"{placement.item_id} falls outside the room bounds: occupies "
            f"{x0:.0f}-{x1:.0f} x {y0:.0f}-{y1:.0f}cm in a "
            f"{room.width_cm:.0f} x {room.depth_cm:.0f}cm room."
        )

    if placement.dims.h and placement.dims.h > room.height_cm:
        reasons.append(
            f"{placement.item_id} is {placement.dims.h:.0f}cm tall but the ceiling "
            f"is {room.height_cm:.0f}cm."
        )
    else:
        # The classic wardrobe failure: it stands up fine, but you cannot rotate
        # it from flat to upright because the diagonal exceeds the ceiling.
        # Assembled tall items arrive lying down and have to be tilted in place.
        h, d = placement.dims.h or 0, placement.dims.d or 0
        diagonal = math.hypot(h, d)
        if h > room.height_cm * 0.75 and diagonal > room.height_cm:
            reasons.append(
                f"{placement.item_id} cannot be tilted upright: its {h:.0f}x{d:.0f}cm "
                f"profile needs {diagonal:.0f}cm of diagonal swing but the ceiling is "
                f"{room.height_cm:.0f}cm. It has to be assembled standing, or arrive flat-packed."
            )

    for door in room.doors:
        if door.swing != "in":
            continue
        if _overlaps((x0, y0, x1, y1), _swing_box(door, room)):
            reasons.append(
                f"{placement.item_id} blocks the {door.wall} door swing "
                f"({door.width_cm:.0f}cm leaf). {RULES['door_swing']['text']}"
            )

    for fixture in room.fixed:
        if _overlaps((x0, y0, x1, y1), fixture.footprint()):
            reasons.append(f"{placement.item_id} overlaps fixed fixture {fixture.item_id}.")

    if front_clearance_cm:
        # A dining chair's clear space is BEHIND it — in front is the table it
        # is pulled up to. Measuring in front demanded the chair sit away from
        # its own table.
        role = placement.resolved_role()
        pull_out = role in DINING_CHAIR_ROLES
        probe = _flip(placement) if pull_out else placement

        gap, blocker = _front_gap(probe, room, others)
        if gap < front_clearance_cm:
            against = blocker if blocker else "the wall"
            # The rationale has to match the number quoted, or the sentence
            # contradicts itself — and agents are told to quote these verbatim.
            rule = (
                "pull_out" if pull_out
                else "walkway_primary" if front_clearance_cm >= WALKWAY_PRIMARY_CM
                else "walkway_secondary"
            )
            where = "to push back behind" if pull_out else "of walkway in front of"
            reasons.append(
                f"Only {gap:.0f}cm {where} {placement.item_id} "
                f"(blocked by {against}); {front_clearance_cm:.0f}cm needed. "
                f"{RULES[rule]['text']}"
            )

    if role_unknown and not reasons:
        return Verdict(
            status="unverified",
            reasons=[
                f"Cannot determine the role of {placement.item_id}, so the walkway "
                f"rule was not applied. Pass an explicit role (one of: "
                f"{', '.join(sorted(KNOWN_ROLES))}) to get a verified result."
            ],
            details={"footprint": [x0, y0, x1, y1]},
        )

    return Verdict(
        status="fail" if reasons else "pass",
        reasons=reasons,
        details={"footprint": [x0, y0, x1, y1]},
    )


def _swing_box(door: Door, room: Room) -> tuple[float, float, float, float]:
    """The quarter-disc a door sweeps, approximated conservatively as a square
    of the leaf width just inside the wall it sits on.
    """
    r = door.width_cm
    if door.wall == "N":
        return (door.offset_cm, 0, door.offset_cm + door.width_cm, r)
    if door.wall == "S":
        return (door.offset_cm, room.depth_cm - r, door.offset_cm + door.width_cm, room.depth_cm)
    if door.wall == "W":
        return (0, door.offset_cm, r, door.offset_cm + door.width_cm)
    return (room.width_cm - r, door.offset_cm, room.width_cm, door.offset_cm + door.width_cm)


def _front_gap(
    placement: Placement,
    room: Room,
    others: Sequence[Placement] = (),
) -> tuple[float, str | None]:
    """Clear distance in front of an item, and what limits it.

    Previously this measured only to the wall, so two sofas 3cm apart passed:
    the rule never looked at the furniture standing in the way. A coffee table
    is exempt in front of seating — it is the intended companion, and the
    40cm reach rule governs that relationship instead.
    """
    x0, y0, x1, y1 = placement.footprint()
    gap = {
        "S": room.depth_cm - y1,
        "N": y0,
        "E": room.width_cm - x1,
        "W": x0,
    }[placement.facing]
    blocker: str | None = None

    for other in others:
        if other is placement or not other.dims.known:
            continue
        if _is_companion(placement, other):
            continue
        ox0, oy0, ox1, oy1 = other.footprint()

        if placement.facing == "S" and ox1 > x0 and ox0 < x1 and oy0 >= y1:
            distance = oy0 - y1
        elif placement.facing == "N" and ox1 > x0 and ox0 < x1 and oy1 <= y0:
            distance = y0 - oy1
        elif placement.facing == "E" and oy1 > y0 and oy0 < y1 and ox0 >= x1:
            distance = ox0 - x1
        elif placement.facing == "W" and oy1 > y0 and oy0 < y1 and ox1 <= x0:
            distance = x0 - ox1
        else:
            continue

        if distance < gap:
            gap, blocker = distance, other.item_id

    return gap, blocker


# Pairs that belong next to each other. Neither obstructs the other's
# clearance, and a chair may overlap the table it tucks under.
COMPANION_PAIRS = [
    {"sofa", "coffee_table"},
    {"armchair", "coffee_table"},
    *({"dining_table", r} for r in DINING_CHAIR_ROLES),
]


def _is_companion(a: Placement, b: Placement) -> bool:
    """A coffee table belongs in front of a sofa; a dining chair belongs at its
    table. Treating either as an obstruction rejects the correct arrangement."""
    roles = {a.resolved_role(), b.resolved_role()}
    return any(roles == pair for pair in COMPANION_PAIRS)


# --------------------------------------------------------------------------
# validate_layout — do the items work together?
# --------------------------------------------------------------------------

def validate_layout(room: Room, placements: Sequence[Placement]) -> Verdict:
    reasons: list[str] = []
    statuses: list[Status] = []

    unknown = [p.item_id for p in placements if p.resolved_role() is None]
    if unknown:
        reasons.append(
            f"Cannot determine the role of {', '.join(unknown)}, so the seating "
            f"walkway and sofa-to-table clearance rules were not applied. Pass an "
            f"explicit role (one of: {', '.join(sorted(KNOWN_ROLES))}) to get a "
            f"verified result."
        )
        statuses.append("unverified")

    for p in placements:
        # Clearance comes from the role table, the same one check_fit uses, and
        # the rest of the layout is passed in so a walkway can be blocked by
        # furniture rather than only by a wall.
        v = check_fit(p, room, others=[o for o in placements if o is not p])
        statuses.append(v.status)
        reasons.extend(v.reasons)

    known = [p for p in placements if p.dims.known]
    for i, a in enumerate(known):
        for b in known[i + 1:]:
            fa, fb = a.footprint(), b.footprint()
            if _overlaps(fa, fb):
                # A dining chair tucks under its table, so their footprints
                # overlap by design. Reporting that as a collision rejected
                # every realistically-placed dining set.
                if _is_companion(a, b) and {a.resolved_role(), b.resolved_role()} & DINING_CHAIR_ROLES:
                    continue
                reasons.append(f"{a.item_id} and {b.item_id} overlap.")
                statuses.append("fail")
                continue
            gap = _gap_between(fa, fb)
            if _is_sofa_table_pair(a, b) and gap < SOFA_TO_TABLE_MIN_CM:
                reasons.append(
                    f"Only {gap:.0f}cm between {a.item_id} and {b.item_id}. "
                    f"{RULES['sofa_to_coffee_table']['text']}"
                )
                statuses.append("fail")

    # Functional adjacency, not just clearance. Every rule above answers "is
    # there room for this?"; none answers "does this arrangement work?" A
    # brute-force position search found coffee tables that satisfied every
    # clearance while sitting behind the sofa — legal, and useless.
    seating = [p for p in known if p.resolved_role() in {"sofa", "armchair"}]
    for table in (p for p in known if p.resolved_role() == "coffee_table"):
        if not seating:
            continue
        distances = [_gap_between(table.footprint(), s.footprint()) for s in seating]
        nearest = min(distances)
        in_front = any(_is_in_front(s, table) for s in seating)
        if nearest > COFFEE_TABLE_REACH_CM or not in_front:
            where = "behind the seating" if not in_front else f"{nearest:.0f}cm away"
            reasons.append(
                f"{table.item_id} is out of reach of the seating ({where}). "
                f"{RULES['coffee_table_reach']['text']}"
            )
            statuses.append("fail")

    return Verdict(status=_combine(statuses), reasons=reasons)


def _flip(placement: Placement) -> Placement:
    """The same placement looking the other way, for measuring behind it."""
    opposite = {"N": "S", "S": "N", "E": "W", "W": "E"}[placement.facing]
    return replace(placement, facing=opposite)


def _is_in_front(seat: Placement, other: Placement) -> bool:
    """Is `other` on the side the seat actually faces?"""
    sx0, sy0, sx1, sy1 = seat.footprint()
    ox0, oy0, ox1, oy1 = other.footprint()
    return {
        "S": oy0 >= sy1,
        "N": oy1 <= sy0,
        "E": ox0 >= sx1,
        "W": ox1 <= sx0,
    }[seat.facing]


def _is_sofa_table_pair(a: Placement, b: Placement) -> bool:
    """The 40cm reach rule is about a coffee table in front of seating.

    It used to match any seating against any table, so an armchair pulled up to
    a dining table was told to sit 40cm back from it — the opposite of what a
    dining chair should do.
    """
    roles = {a.resolved_role(), b.resolved_role()}
    return bool(roles & {"sofa", "armchair"}) and "coffee_table" in roles


def _gap_between(a: tuple, b: tuple) -> float:
    dx = max(0.0, b[0] - a[2], a[0] - b[2])
    dy = max(0.0, b[1] - a[3], a[1] - b[3])
    return math.hypot(dx, dy)


# --------------------------------------------------------------------------
# check_access_path — can the item get to the room at all?
# --------------------------------------------------------------------------

def check_access_path(dims: Dims, segments: Sequence[PathSegment]) -> Verdict:
    """Walk the delivery route and test the item against each leg.

    This is the check eazli's Fitment clause pushes onto the user. A sofa that
    fits the living room perfectly is still a return if it cannot clear the
    hallway.
    """
    if not dims.known:
        return _unverified(dims)

    passed: list[str] = []
    failed: list[str] = []
    statuses: list[Status] = []

    for seg in segments:
        if seg.kind == "lift":
            ok, note = _fits_in_box(dims, seg)
        elif seg.kind == "turn":
            ok, note = _can_turn(dims, seg)
        else:
            ok, note = _fits_through_opening(dims, seg)

        statuses.append("pass" if ok else "fail")
        (passed if ok else failed).append(note)

    status = _combine(statuses)
    # A failing verdict used to ship "clears at 90x90cm" as reasons[0], which
    # reads as reassurance attached to a rejection. On a fail, the reasons are
    # the failures; the rest stays in details for anyone who wants the whole
    # route.
    return Verdict(
        status=status,
        reasons=failed if status == "fail" else passed,
        details={"route_notes": passed + failed},
    )


def _fits_through_opening(dims: Dims, seg: PathSegment) -> tuple[bool, str]:
    """An item clears an opening if some orientation presents a cross-section
    that fits, with the remaining dimension running along the direction of travel.
    """
    w, d, h = dims.triple
    # Prefer keeping the item's own height vertical: that is how it actually
    # gets carried. Only fall back to tipping it when upright will not clear.
    candidates = sorted(
        permutations((w, d, h)),
        key=lambda o: (o[1] != h, o[0]),
    )
    for across, up, along in candidates:
        if across <= seg.width_cm and up <= seg.height_cm:
            tipped = up != h
            margin = min(seg.width_cm - across, seg.height_cm - up)
            note = (
                f"{seg.name} ({seg.kind} {seg.width_cm:.0f}x{seg.height_cm:.0f}cm): "
                f"clears at {across:.0f}x{up:.0f}cm"
                + (" — must be carried on its side" if tipped else "")
                + (
                    f" — TIGHT, only {margin:.0f}cm to spare. A stated opening loses "
                    f"width to hinges, door stops and the hands carrying the item; "
                    f"measure the clear opening before ordering."
                    if margin < TIGHT_MARGIN_CM else "."
                )
            )
            return True, note
    return False, (
        f"{seg.name} ({seg.kind} {seg.width_cm:.0f}x{seg.height_cm:.0f}cm): item "
        f"{w:.0f}x{d:.0f}x{h:.0f}cm cannot pass in any orientation."
    )


def _fits_in_box(dims: Dims, seg: PathSegment) -> tuple[bool, str]:
    depth = seg.depth_cm if seg.depth_cm is not None else seg.width_cm
    w, d, h = dims.triple
    for a, b, c in permutations((w, d, h)):
        if a <= seg.width_cm and b <= depth and c <= seg.height_cm:
            margin = min(seg.width_cm - a, depth - b, seg.height_cm - c)
            tight = (f" TIGHT, only {margin:.0f}cm to spare."
                     if margin < TIGHT_MARGIN_CM else "")
            return True, (f"{seg.name} (lift car {seg.width_cm:.0f}x{depth:.0f}x"
                          f"{seg.height_cm:.0f}cm): fits.{tight}")
    return False, (
        f"{seg.name} (lift car {seg.width_cm:.0f}x{depth:.0f}x{seg.height_cm:.0f}cm): item "
        f"{w:.0f}x{d:.0f}x{h:.0f}cm does not fit the car in any orientation."
    )


def _can_turn(dims: Dims, seg: PathSegment) -> tuple[bool, str]:
    """Right-angle turn between corridors of width A and B.

    For a rigid rectangle of length L and width t, the clear length available at
    approach angle theta is:

        L(theta) = A/sin(theta) + B/cos(theta) - t*(cot(theta) + tan(theta))

    The binding constraint is the minimum over theta, since the item has to fit
    at every angle it passes through. With t = 0 this reduces to the classic
    ladder-around-a-corner result, (A^(2/3) + B^(2/3))^(3/2).

    Assumes the item stays horizontal through the turn, which is the
    conservative case; movers often tilt, so a fail here means "needs a human
    to look at it", not "physically impossible".
    """
    a = seg.width_cm
    b = seg.turn_into_cm if seg.turn_into_cm is not None else seg.width_cm
    w, d, h = dims.triple

    best: tuple[float, float, float] | None = None
    for vertical, (length, thickness) in _horizontal_options(w, d, h):
        if vertical > seg.height_cm:
            continue
        if thickness > a or thickness > b:
            continue
        limit = _max_turn_length(a, b, thickness)
        if length <= limit:
            # The door check says "must be carried on its side" when it has to
            # tip something; the turn said nothing, so a sofa that only makes
            # the corner stood on its end was reported as a bare pass.
            tipped = "" if vertical == h else " — must be stood on end to make the corner."
            return True, (
                f"{seg.name} (turn {a:.0f}cm into {b:.0f}cm): clears with "
                f"{length:.0f}cm length against a {limit:.0f}cm limit{tipped or '.'}"
            )
        if best is None or limit > best[2]:
            best = (length, thickness, limit)

    if best is None:
        return False, (
            f"{seg.name} (turn {a:.0f}cm into {b:.0f}cm): item {w:.0f}x{d:.0f}x{h:.0f}cm "
            f"is too wide for the corner in every orientation."
        )
    length, thickness, limit = best
    return False, (
        f"{seg.name} (turn {a:.0f}cm into {b:.0f}cm): item needs to swing {length:.0f}cm "
        f"of length around the corner but only {limit:.0f}cm is available at "
        f"{thickness:.0f}cm thick."
    )


def _horizontal_options(w: float, d: float, h: float):
    """Each way of standing the item up: (vertical dim, (length, thickness)).

    Ordered so the upright carry (the item's own height vertical) is tried
    first, matching how the item would really be moved.
    """
    dims = (w, d, h)
    options = []
    seen = set()
    for i in range(3):
        vertical = dims[i]
        rest = tuple(dims[j] for j in range(3) if j != i)
        for length, thickness in (rest, rest[::-1]):
            key = (vertical, length, thickness)
            if key not in seen:
                seen.add(key)
                options.append((vertical != h, vertical, (length, thickness)))
    for _, vertical, pair in sorted(options, key=lambda o: (o[0], o[2][1])):
        yield vertical, pair


def _max_turn_length(a: float, b: float, thickness: float, samples: int = 900) -> float:
    limit = float("inf")
    for i in range(1, samples):
        theta = math.pi / 2 * i / samples
        s, c = math.sin(theta), math.cos(theta)
        value = a / s + b / c - thickness * (c / s + s / c)
        limit = min(limit, value)
    return max(0.0, limit)
