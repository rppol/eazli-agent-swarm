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
    "reach_clearance": {
        "value_cm": 75,
        "text": "Storage has to open. A wardrobe door or a drawer needs about "
                "75cm in front of it and a bookshelf about 45cm to reach a "
                "shelf and read a spine. Clearance between footprints says "
                "nothing about whether the doors will open.",
    },
    "bedside_reach": {
        "value_cm": 30,
        "text": "A bedside table is for the lamp, the book and the glass of "
                "water, so it has to be within arm's reach of the pillow — "
                "touching the bed, or within about 30cm of it, and alongside "
                "rather than beyond the foot.",
    },
    "bed_access": {
        "value_cm": 60,
        "text": "You have to be able to get into the bed. At least one long "
                "side needs about 60cm clear of walls and furniture; a double "
                "shared by two people wants that on both sides.",
    },
    "coffee_table_reach": {
        "value_cm": 100,
        "text": "A coffee table has to be reachable from the seating it serves — "
                "within about a metre, and in front of it rather than behind. A "
                "table that satisfies every clearance but sits out of reach is "
                "legal and useless.",
    },
    "tv_sightline": {
        "value_cm": 120,
        "text": "A television has to be in front of the seating rather than off "
                "to one side: the console and the seat must share some of the "
                "same lateral band, and sit at least 120cm apart. 120cm is the "
                "practical minimum viewing distance — no screen diagonal is "
                "published anywhere in this catalogue, so no diagonal-based "
                "rule could be honest about where it got its number.",
    },
    "rug_anchors_seating": {
        "value_cm": 15,
        "text": "A rug is what makes a sofa, a chair and a table read as one "
                "group, so it has to lie under that group: at least 15% shared "
                "area with the seating group's footprint. Deliberately loose — "
                "it catches a rug that is disconnected from the room, not "
                "whether the front legs are on it.",
    },
    "lamp_within_reach_of_seating": {
        "value_cm": 75,
        "text": "At least one floor lamp has to stand within 75cm of somewhere "
                "you sit or lie down to read — a sofa, an armchair or a bed. "
                "The same 75cm as the reach clearance, because it is the same "
                "measurement: an arm's length from where the person is. Only "
                "one; a second lamp placed elsewhere is a lighting scheme.",
    },
    "seat_has_a_focal_point": {
        "value_cm": 300,
        "text": "An armchair has to be turned toward something — the sofa, the "
                "coffee table or the television — within about 3 metres. A "
                "chair angled at open floor satisfies every clearance in this "
                "engine and is still nobody's seat.",
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

# Anything this low is a floor covering, not an obstruction: you walk over it.
# Without this a rug counted as blocking the walkway in front of the sofa it
# was laid under, which made rugs unplaceable in any furnished room.
FLOOR_COVERING_MAX_H_CM = 5.0


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
# What has to open, and how much room it needs to do it. Everything in
# WALL_ROLES used to be 0.0, which let the planner stand an armchair flat
# against 180cm of shelving and call the room a pass — the footprints did not
# overlap, so nothing objected. A gap between two boxes is not the same claim
# as "you can use both of them".
REACH_DOOR_CM = RULES["reach_clearance"]["value_cm"]     # hinged door, drawer
REACH_SHELF_CM = 45.0                                    # reach in, read a spine
STORAGE_REACH_BY_ROLE: dict[str, float] = {
    "wardrobe": REACH_DOOR_CM,
    "bookshelf": REACH_SHELF_CM,
}

FRONT_CLEARANCE_BY_ROLE: dict[str, float] = {
    **{r: WALKWAY_PRIMARY_CM for r in SEATING_ROLES},
    **{r: WALKWAY_SECONDARY_CM for r in TABLE_ROLES},
    **{r: WALKWAY_SECONDARY_CM for r in DINING_CHAIR_ROLES},
    **{r: 0.0 for r in WALL_ROLES},
    # A TV console is looked at from across the room and a bed is got into
    # from the side, so neither wants a front rule; both are handled elsewhere
    # or not at all. Storage is the exception that needed one.
    **STORAGE_REACH_BY_ROLE,
}

BEDSIDE_REACH_CM = RULES["bedside_reach"]["value_cm"]
BED_ACCESS_CM = RULES["bed_access"]["value_cm"]
BEDSIDE_ROLES = {"side_table", "nightstand", "table"}

TV_VIEWING_MIN_CM = RULES["tv_sightline"]["value_cm"]
RUG_ANCHOR_MIN_FRACTION = RULES["rug_anchors_seating"]["value_cm"] / 100.0
FOCAL_POINT_MAX_CM = RULES["seat_has_a_focal_point"]["value_cm"]

# Deliberately the same number as `reach_clearance` rather than a new one. A
# lamp you cannot reach from the seat is the same failure as a drawer you
# cannot reach from in front of it, and inventing a second 75cm would have
# invited the two to drift apart.
LAMP_REACH_CM = RULES["reach_clearance"]["value_cm"]

# Who is allowed to watch the television. NOT every seat: nobody arranges a
# dining chair around a screen, and counting one would let a console satisfy
# the rule against furniture that is not facing it.
TV_AUDIENCE_ROLES = {"sofa", "armchair"}

# What a rug has to lie under. A dining set is a separate zone of the same
# room with its own rug, if any, so it is not part of this group.
SEATING_GROUP_ROLES = {"sofa", "armchair", "coffee_table"}

# Where a person sits or lies down to read. The bed is in here on purpose: a
# lamp 36cm off the head of the bed is a bedside reading light, which is the
# canonical case for this rule rather than an exception to it. Scoping the rule
# to sofas and armchairs alone would also have deleted furniture — measured
# across every bedroom in the home, there is NO valid armchair position within
# 75cm of the lamp the planner places at priority 3, so the premium tier's
# armchair would have come back "could not be placed anywhere in the room".
LAMP_REACH_ROLES = {"sofa", "armchair", "bed"}

# What an armchair is allowed to be facing. The sofa is the anchor everything
# else orients around, so it is a target and never a subject: asking the first
# piece placed to face something would make an empty room unfurnishable.
FOCAL_ROLES = {"sofa", "coffee_table", "tv_console"}


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
    notes: list[str] = []
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

    floor_covering = (placement.dims.h or 0) <= FLOOR_COVERING_MAX_H_CM
    for door in room.doors:
        if door.swing != "in":
            continue
        if not _overlaps((x0, y0, x1, y1), _swing_box(door, room)):
            continue
        if floor_covering:
            # A door sweeps over a rug rather than into it — but only if the
            # leaf is undercut enough to clear the pile, which is typically
            # 10-20mm. Worth saying out loud rather than either failing the
            # layout or pretending the question does not arise.
            notes.append(
                f"{placement.item_id} runs under the {door.wall} door swing. A door "
                f"leaf is usually undercut 10-20mm, so check it clears the pile "
                f"({placement.dims.h:.0f}cm here)."
            )
            continue
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
        details={"footprint": [x0, y0, x1, y1], "notes": notes},
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
        if (other.dims.h or 0) <= FLOOR_COVERING_MAX_H_CM:
            continue   # a rug is walked on, not walked around
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
    # A bedside table belongs pressed against the bed. Counting it as an
    # obstruction rejected the one arrangement that is actually correct.
    *({"bed", r} for r in ("side_table", "nightstand", "table")),
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

    notes: list[str] = []
    for p in placements:
        # Clearance comes from the role table, the same one check_fit uses, and
        # the rest of the layout is passed in so a walkway can be blocked by
        # furniture rather than only by a wall.
        v = check_fit(p, room, others=[o for o in placements if o is not p])
        statuses.append(v.status)
        reasons.extend(v.reasons)
        notes.extend(v.details.get("notes", []))

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
                # A rug lies under the furniture standing on it. Treating that
                # as a collision would make rugs unplaceable in a furnished room.
                if "rug" in {a.resolved_role(), b.resolved_role()}:
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

    # Usability, after geometry. Everything above asks whether the boxes fit;
    # these ask whether a person could use what is in them.
    ergonomic = (
        _bedside_within_reach(placements)
        + _bed_can_be_got_into(room, placements)
        + _tv_has_a_sightline(placements)
        + _rug_anchors_seating(placements)
        + _lamp_within_reach_of_seating(placements)
        + _seat_has_a_focal_point(placements)
    )
    if ergonomic:
        reasons.extend(ergonomic)
        statuses.append("fail")

    return Verdict(status=_combine(statuses), reasons=reasons)


def _bedside_within_reach(placements) -> list[str]:
    """A bedside table has to be beside the bed.

    A generated bedroom put a 40cm side table 35cm clear of the bed and level
    with nothing, and it passed: it overlapped nothing and cleared every
    walkway. Nobody could have reached it from the pillow, which is the only
    thing a bedside table is for.
    """
    beds = [p for p in placements if p.resolved_role() == "bed" and p.dims.known]
    if not beds:
        return []                    # a side table in a living room is not this
    out: list[str] = []
    for tbl in placements:
        if tbl.resolved_role() not in BEDSIDE_ROLES or not tbl.dims.known:
            continue
        gap = min(_gap_between(tbl.footprint(), b.footprint()) for b in beds)
        if gap > BEDSIDE_REACH_CM:
            out.append(
                f"{tbl.item_id} is {gap:.0f}cm from the bed. A bedside table has to "
                f"be within {BEDSIDE_REACH_CM:.0f}cm to be reachable from the "
                f"pillow — beyond that it is a side table standing on its own."
            )
    return out


def _bed_can_be_got_into(room: Room, placements) -> list[str]:
    """At least one long side of a bed needs room to stand and get in.

    Long sides only: the head is against something by design, and the foot is
    not how anyone gets into bed.
    """
    out: list[str] = []
    for bed in placements:
        if bed.resolved_role() != "bed" or not bed.dims.known:
            continue
        x0, y0, x1, y1 = bed.footprint()
        horizontal = (x1 - x0) >= (y1 - y0)      # which way the mattress lies
        sides = ((("N", y0), ("S", room.depth_cm - y1)) if horizontal
                 else (("W", x0), ("E", room.width_cm - x1)))
        best = 0.0
        for wall, to_wall in sides:
            clear = to_wall
            for o in placements:
                if o is bed or not o.dims.known:
                    continue
                if (o.dims.h or 0) <= FLOOR_COVERING_MAX_H_CM:
                    continue             # a rug is not an obstruction
                ox0, oy0, ox1, oy1 = o.footprint()
                if horizontal and ox1 > x0 and ox0 < x1:
                    d = (y0 - oy1) if wall == "N" else (oy0 - y1)
                elif not horizontal and oy1 > y0 and oy0 < y1:
                    d = (x0 - ox1) if wall == "W" else (ox0 - x1)
                else:
                    continue
                if d >= 0:
                    clear = min(clear, d)
            best = max(best, clear)
        if best < BED_ACCESS_CM:
            out.append(
                f"{bed.item_id} has only {best:.0f}cm clear on its most open long "
                f"side. Getting into bed needs about {BED_ACCESS_CM:.0f}cm."
            )
    return out


def _span_overlap(a: tuple, b: tuple, axis: Literal["x", "y"]) -> float:
    """How much of the same lateral band two footprints occupy.

    Zero means they are side by side rather than one in front of the other,
    which is the difference between a television you look at and a television
    you look past.
    """
    i = 0 if axis == "x" else 1
    return max(0.0, min(a[i + 2], b[i + 2]) - max(a[i], b[i]))


def _lateral_axis(facing: Wall) -> Literal["x", "y"]:
    """The axis across an item's line of sight. Something facing north or south
    is lined up with what is in front of it on x."""
    return "x" if facing in ("N", "S") else "y"


def _seating_group_box(placements) -> tuple[float, float, float, float] | None:
    """The rectangle the sofa, the armchairs and the coffee table sit inside.

    A bounding box rather than a union of footprints, because that is what a
    rug is actually being asked to cover: the arrangement, including the floor
    between the sofa and the table, not the silhouette of the furniture.
    """
    boxes = [p.footprint() for p in placements
             if p.resolved_role() in SEATING_GROUP_ROLES and p.dims.known]
    if not boxes:
        return None
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def _tv_has_a_sightline(placements) -> list[str]:
    """A television has to be in front of the seating, not off to one side.

    `_positions` had no tv_console branch, so a console fell through to the
    generic "hug a wall, then nearest the origin" ordering and was placed with
    no knowledge of where the sofa was. unit01/living_dining/8000/warm+minimal
    put a 40cm console at x=0-40 against a sofa spanning x=120-293 — zero
    shared band, so you would be looking 80cm to the side of the screen — and
    it returned `pass`, in 15 of 18 living/dining combinations.

    Both halves are needed. Overlap alone allows a console 90cm from the sofa
    front, which is a screen you sit under rather than watch; distance alone
    allows the 80cm-to-the-side case above.
    """
    audience = [p for p in placements
                if p.resolved_role() in TV_AUDIENCE_ROLES and p.dims.known]
    if not audience:
        return []                    # a console in an unfurnished room is not this
    out: list[str] = []
    for tv in placements:
        if tv.resolved_role() != "tv_console" or not tv.dims.known:
            continue
        box = tv.footprint()
        axis = _lateral_axis(tv.facing)
        aligned = [s for s in audience if _span_overlap(box, s.footprint(), axis) > 0]
        if not aligned:
            nearest = min(audience, key=lambda s: _gap_between(box, s.footprint()))
            out.append(
                f"{tv.item_id} shares no sightline with {nearest.item_id}: their "
                f"footprints do not overlap on the {axis} axis at all, so the "
                f"screen sits off to one side of the seat rather than in front "
                f"of it. {RULES['tv_sightline']['text']}"
            )
            continue
        far = max(_gap_between(box, s.footprint()) for s in aligned)
        if far < TV_VIEWING_MIN_CM:
            out.append(
                f"{tv.item_id} is only {far:.0f}cm from the seating it faces. "
                f"{RULES['tv_sightline']['text']}"
            )
    return out


def _rug_anchors_seating(placements) -> list[str]:
    """A rug has to lie under the group it is there to tie together.

    Same root cause: no rug branch in `_positions`, so a 120x120cm rug went to
    the origin corner. In unit01/living_dining/8000/warm+minimal it met the
    sofa exactly at x=120 — touching along a line, sharing no area — while the
    seating group ran from x=120 to x=293. 12 of 194 generated rugs shared
    exactly 0% of their area with the group they were bought for.

    Measured against the SMALLER of the two areas so that neither a small rug
    beside a large group nor a large rug under a small group is judged by the
    other one's size.
    """
    group = _seating_group_box(placements)
    if group is None:
        return []                    # a rug in a bedroom answers to nothing here
    gx0, gy0, gx1, gy1 = group
    group_area = (gx1 - gx0) * (gy1 - gy0)
    out: list[str] = []
    for rug in placements:
        if rug.resolved_role() != "rug" or not rug.dims.known:
            continue
        box = rug.footprint()
        rug_area = (box[2] - box[0]) * (box[3] - box[1])
        smaller = min(rug_area, group_area)
        if smaller <= 0:
            continue
        shared = _span_overlap(box, group, "x") * _span_overlap(box, group, "y")
        fraction = shared / smaller
        if fraction < RUG_ANCHOR_MIN_FRACTION:
            out.append(
                f"{rug.item_id} covers {fraction * 100:.0f}% of the seating group "
                f"it is meant to anchor, against a {RUG_ANCHOR_MIN_FRACTION * 100:.0f}% "
                f"minimum. {RULES['rug_anchors_seating']['text']}"
            )
    return out


def _lamp_within_reach_of_seating(placements) -> list[str]:
    """One floor lamp has to be within reach of somewhere you sit.

    unit01/living_dining/30000/industrial+mid_century placed its accent lamp
    95cm off the end of the sofa and its reading lamp 446cm from the nearest
    seat: two lights in a room, neither of them lighting anybody. 73 of 107
    generated lamps stood more than 90cm from any seat.

    Stated over the room rather than per lamp on purpose. `_positions`
    deliberately spreads twins apart so the premium tier's second lamp does not
    stand 6cm from the first, and a per-lamp rule would fight that: an accent
    lamp in the far corner is legitimate exactly as long as something else in
    the room is doing the reading light's job.
    """
    lamps = [p for p in placements
             if p.resolved_role() == "floor_lamp" and p.dims.known]
    seats = [p for p in placements
             if p.resolved_role() in LAMP_REACH_ROLES and p.dims.known]
    if not lamps or not seats:
        return []
    nearest = min(_gap_between(l.footprint(), s.footprint())
                  for l in lamps for s in seats)
    if nearest <= LAMP_REACH_CM:
        return []
    return [
        f"The nearest floor lamp is {nearest:.0f}cm from anywhere you could sit "
        f"or lie down, so none of the {len(lamps)} in this room lights a seat. "
        f"{RULES['lamp_within_reach_of_seating']['text']}"
    ]


def _seat_has_a_focal_point(placements) -> list[str]:
    """An armchair has to be turned toward the group, not at open floor.

    `_positions` pulls an armchair's POSITION toward the sofa but never inspects
    `facing`, so among equally close candidates it took whichever facing sorted
    first. unit01/living_dining/30000/industrial+mid_century produced a chair at
    x=280-335 facing south down 506cm of empty floor, with the sofa ending at
    x=273.8 — outside its band by 6cm. 59 generated armchairs faced nothing.

    Scoped to the armchair. The sofa is the anchor the rest of the room orients
    around, and requiring it to face something would make the first piece
    placed in an empty room impossible.
    """
    targets = [p for p in placements
               if p.resolved_role() in FOCAL_ROLES and p.dims.known]
    if not targets:
        return []                    # nothing in the room to look at yet
    out: list[str] = []
    for chair in placements:
        if chair.resolved_role() != "armchair" or not chair.dims.known:
            continue
        box = chair.footprint()
        axis = _lateral_axis(chair.facing)
        seen = [
            t for t in targets
            if t is not chair
            and _is_in_front(chair, t)
            and _span_overlap(box, t.footprint(), axis) > 0
            and _gap_between(box, t.footprint()) <= FOCAL_POINT_MAX_CM
        ]
        if not seen:
            out.append(
                f"{chair.item_id} faces {chair.facing} into open floor: nothing "
                f"it could be turned toward lies in that band within "
                f"{FOCAL_POINT_MAX_CM:.0f}cm. "
                f"{RULES['seat_has_a_focal_point']['text']}"
            )
    return out


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

def check_access_path(
    dims: Dims,
    segments: Sequence[PathSegment],
    flexible: bool = False,
) -> Verdict:
    """Walk the delivery route and test the item against each leg.

    This is the check eazli's Fitment clause pushes onto the user. A sofa that
    fits the living room perfectly is still a return if it cannot clear the
    hallway.

    `flexible` marks goods that bend — a rolled rug, a mattress. The
    corner-turning maths assumes a rigid rectangle, which is right for a sofa
    and wrong for a rug: it rejected every rug in the catalogue for needing
    300cm of swing where 275cm was available, when in practice you simply flex
    it round. The exemption is narrow (turns only) and always said out loud.
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
            if not ok and flexible:
                ok = True
                note = (
                    f"{seg.name}: too long to swing round rigidly, but this item "
                    f"bends — carried flexed or folded. Not verified geometry."
                )
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
    # Nothing fits along an axis. A slender item can still go in corner to
    # corner — which is what anyone actually does with a rug, a ladder or a
    # curtain pole. Conservative: the long side must clear the space diagonal
    # AND the other two must be slim next to the smallest side, so a wardrobe
    # cannot be wedged in on a technicality.
    longest, *rest = sorted((w, d, h), reverse=True)
    diagonal = math.sqrt(seg.width_cm ** 2 + depth ** 2 + seg.height_cm ** 2)
    slim = min(seg.width_cm, depth, seg.height_cm) / 4
    if longest <= diagonal and all(v <= slim for v in rest):
        return True, (
            f"{seg.name} (lift car {seg.width_cm:.0f}x{depth:.0f}x{seg.height_cm:.0f}cm): "
            f"{longest:.0f}cm does not fit along any wall, but clears the {diagonal:.0f}cm "
            f"space diagonal — must be angled corner to corner."
        )

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
