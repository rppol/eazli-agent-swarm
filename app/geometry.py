"""Deterministic spatial reasoning. No model ever decides whether something fits.

eazli's own AI Agent Disclaimers page warns that agent outputs "may be incomplete,
inaccurate, or contain incorrect assumptions" and tells users to verify
"especially for measurements". Their Fitment clause goes further and disclaims
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
from dataclasses import dataclass, field
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
}

WALKWAY_PRIMARY_CM = RULES["walkway_primary"]["value_cm"]
WALKWAY_SECONDARY_CM = RULES["walkway_secondary"]["value_cm"]
SOFA_TO_TABLE_MIN_CM = RULES["sofa_to_coffee_table"]["value_cm"]


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
        return None not in (self.w, self.d, self.h)

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
TABLE_ROLES = {"coffee_table", "dining_table", "table", "side_table"}
KNOWN_ROLES = SEATING_ROLES | TABLE_ROLES | {
    "bed", "wardrobe", "bookshelf", "tv_console", "floor_lamp", "rug", "other",
}


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
    front_clearance_cm: float = WALKWAY_PRIMARY_CM,
) -> Verdict:
    if not placement.dims.known:
        return _unverified(placement.dims)

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
        gap = _front_gap(placement, room)
        if gap < front_clearance_cm:
            reasons.append(
                f"Only {gap:.0f}cm of walkway in front of {placement.item_id}; "
                f"{front_clearance_cm:.0f}cm needed. {RULES['walkway_primary']['text']}"
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


def _front_gap(placement: Placement, room: Room) -> float:
    x0, y0, x1, y1 = placement.footprint()
    return {
        "S": room.depth_cm - y1,
        "N": y0,
        "E": room.width_cm - x1,
        "W": x0,
    }[placement.facing]


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
        # Only seating gets a front-walkway requirement; a coffee table in the
        # middle of the room is not an obstruction to itself.
        needs_front = p.resolved_role() in SEATING_ROLES
        v = check_fit(p, room, front_clearance_cm=WALKWAY_PRIMARY_CM if needs_front else 0)
        statuses.append(v.status)
        reasons.extend(v.reasons)

    known = [p for p in placements if p.dims.known]
    for i, a in enumerate(known):
        for b in known[i + 1:]:
            fa, fb = a.footprint(), b.footprint()
            if _overlaps(fa, fb):
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

    return Verdict(status=_combine(statuses), reasons=reasons)


def _is_sofa_table_pair(a: Placement, b: Placement) -> bool:
    roles = {a.resolved_role(), b.resolved_role()}
    return bool(roles & {"sofa", "armchair"}) and bool(roles & TABLE_ROLES)


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

    reasons: list[str] = []
    statuses: list[Status] = []

    for seg in segments:
        if seg.kind == "lift":
            ok, note = _fits_in_box(dims, seg)
        elif seg.kind == "turn":
            ok, note = _can_turn(dims, seg)
        else:
            ok, note = _fits_through_opening(dims, seg)

        statuses.append("pass" if ok else "fail")
        if note:
            reasons.append(note)

    return Verdict(status=_combine(statuses), reasons=reasons)


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
            note = (
                f"{seg.name} ({seg.kind} {seg.width_cm:.0f}x{seg.height_cm:.0f}cm): "
                f"clears at {across:.0f}x{up:.0f}cm"
                + (" — must be carried on its side." if tipped else ".")
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
            return True, f"{seg.name} (lift car {seg.width_cm:.0f}x{depth:.0f}x{seg.height_cm:.0f}cm): fits."
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
            return True, (
                f"{seg.name} (turn {a:.0f}cm into {b:.0f}cm): clears with "
                f"{length:.0f}cm length against a {limit:.0f}cm limit."
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
