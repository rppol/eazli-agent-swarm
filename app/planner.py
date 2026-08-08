"""Deterministic auto-planner: furnish a room by search, not by generation.

This is the "run a plan" half of the studio. It is not a language model. It
enumerates candidate products and candidate positions and asks `validate_layout`
about each, keeping the first arrangement that holds — so every plan it emits
is, by construction, one the engine validates.

That matters for the same reason the rest of the project does: a generated
layout has to be checked anyway, and if you are going to check it you may as
well search the checked space directly. The agents remain useful for the parts
search cannot do — reading intent, weighing taste, explaining a trade-off — but
placement is arithmetic and belongs here.

The room recipes below encode which slots a room type wants and in what order
they get placed. Anchors go first because everything else is positioned
relative to them.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from app.catalog import Product, parse_capture
from app import styling
from app.geometry import (
    Dims,
    Placement,
    Room,
    check_access_path,
    validate_layout,
)
from app.home import load_home

# Slot recipes per room type. Order is placement order: the anchor first, then
# whatever hangs off it.
RECIPES: dict[str, list[dict]] = {
    "living_dining": [
        {"slot_id": "primary_seating", "role": "sofa", "category": "sofa",
         "facings": ["N", "S"], "priority": 1, "required": True},
        {"slot_id": "coffee_table", "role": "coffee_table", "category": "coffee_table",
         "facings": ["N"], "priority": 2, "required": False},
        {"slot_id": "media_console", "role": "tv_console", "category": "tv_unit",
         "facings": ["S", "N"], "priority": 3, "required": False},
        {"slot_id": "dining_table", "role": "dining_table", "category": "dining_table",
         "facings": ["S"], "priority": 4, "required": False},
        {"slot_id": "accent_lighting", "role": "floor_lamp", "category": "floor_lamp",
         "facings": ["N"], "priority": 5, "required": False},
        # Last, because it goes under whatever is already there.
        {"slot_id": "floor_covering", "role": "rug", "category": "rug",
         "facings": ["N"], "priority": 6, "required": False},
    ],
    "bedroom": [
        {"slot_id": "bed", "role": "bed", "category": "bed",
         "facings": ["S"], "priority": 1, "required": True},
        {"slot_id": "storage", "role": "wardrobe", "category": "wardrobe",
         "facings": ["S"], "priority": 2, "required": False},
        {"slot_id": "reading_light", "role": "floor_lamp", "category": "floor_lamp",
         "facings": ["S"], "priority": 3, "required": False},
        {"slot_id": "floor_covering", "role": "rug", "category": "rug",
         "facings": ["S"], "priority": 4, "required": False},
    ],
}
RECIPES["master_bedroom"] = RECIPES["bedroom"]
RECIPES["master_bedroom_1"] = RECIPES["bedroom"]
RECIPES["master_bedroom_2"] = RECIPES["bedroom"]

GRID_CM = 15


@dataclass
class Decision:
    """Why this product, at this spot, and what lost.

    A plan that cannot explain itself is indistinguishable from a plan that was
    guessed. Every field here is recorded as the search runs, not reconstructed
    afterwards — a rationale written after the fact is a caption.
    """
    considered: int
    ranked_above: list[dict]
    rejected: list[dict]
    positions_tried: int
    chose_because: str
    placed_because: str


@dataclass
class PlacedItem:
    slot_id: str
    role: str
    asin: str
    title: str
    url: str
    price_sar: float
    dims_cm: dict
    dims_confidence: str
    x: float
    y: float
    facing: str
    access: dict
    flat_pack: bool
    colour_hex: str | None
    material: str | None
    decision: Decision


@dataclass
class UnfilledSlot:
    slot_id: str
    role: str
    reason: str
    candidates_considered: int
    rejected: list[dict] = field(default_factory=list)


@dataclass
class Plan:
    unit: str
    room: str
    room_cm: dict
    placed: list[PlacedItem] = field(default_factory=list)
    unfilled: list[UnfilledSlot] = field(default_factory=list)
    total_sar: float = 0.0
    budget_sar: float = 0.0
    validation: dict = field(default_factory=dict)
    finishing: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "unit": self.unit,
            "room": self.room,
            "room_cm": self.room_cm,
            "placed": [asdict(p) for p in self.placed],
            "unfilled": [asdict(u) for u in self.unfilled],
            "total_sar": round(self.total_sar, 2),
            "budget_sar": self.budget_sar,
            "within_budget": self.total_sar <= self.budget_sar,
            "validation": self.validation,
            "finishing": self.finishing,
        }


def _score(product: Product, style: list[str]) -> tuple:
    """Rank candidates. Style match first, then evidence, then price.

    Ties are broken deterministically so the same request always produces the
    same plan — a planner that shuffles is impossible to review.
    """
    matched = len(set(product.style_tags) & set(style or []))
    return (
        -matched,
        -(product.rating or 0) * min(product.reviews, 200) / 200,
        product.price_sar or 1e9,
        product.asin,
    )


def candidates_for(
    slot: dict,
    catalog: list[Product],
    budget_left: float,
    style: list[str] | None = None,
) -> list[Product]:
    pool = [
        p for p in catalog
        if p.category == slot["category"]
        and p.usable
        and p.price_sar is not None
        and p.price_sar <= budget_left
    ]
    return sorted(pool, key=lambda p: _score(p, style or []))


def _front_space(x: float, y: float, w: float, d: float, facing: str, room: Room) -> float:
    return {
        "N": y, "S": room.depth_cm - (y + d),
        "W": x, "E": room.width_cm - (x + w),
    }[facing]


def _positions(room: Room, product: Product, slot: dict, placed: list[Placement]):
    """Candidate positions on a coarse grid, ordered by what the role wants.

    Sweeping every centimetre is wasteful and produces floating furniture, so
    the grid is coarse and wall-hugging positions come first.

    Ordering is role-aware because greedy placement is otherwise short-sighted:
    the first valid sofa position was against the north wall, which satisfied
    every rule and left nowhere for the coffee table that comes next. Seating
    is now placed with its back to a wall and as much room in front as
    possible, which is both better design and better for what follows.
    """
    w, d = product.dims.w or 0, product.dims.d or 0
    max_x, max_y = room.width_cm - w, room.depth_cm - d
    if max_x < 0 or max_y < 0:
        return

    xs = sorted({0.0, max_x, *[float(v) for v in range(0, int(max_x) + 1, GRID_CM)]})
    ys = sorted({0.0, max_y, *[float(v) for v in range(0, int(max_y) + 1, GRID_CM)]})
    role = slot["role"]
    anchors = [p for p in placed if p.resolved_role() in {"sofa", "armchair"}]

    def rank(candidate):
        x, y, facing = candidate
        touching = x <= 1 or y <= 1 or x >= max_x - 1 or y >= max_y - 1
        front = _front_space(x, y, w, d, facing, room)

        if role in {"sofa", "armchair"}:
            # Back to a wall, then as much open space in front as the room allows.
            back_to_wall = _front_space(x, y, w, d, _OPPOSITE[facing], room) <= 1
            return (not back_to_wall, -front, x + y)
        if role == "coffee_table" and anchors:
            # As close to the seating as the rules permit.
            ax, ay, *_ = anchors[0].footprint()
            return (abs(x - ax) + abs(y - ay), 0, 0)
        if role == "dining_table" and anchors:
            # The other end of the room from the seating group.
            ax, ay, *_ = anchors[0].footprint()
            return (-(abs(x - ax) + abs(y - ay)), 0, 0)
        return (not touching, x + y, 0)

    grid = [(x, y, f) for x in xs for y in ys for f in slot["facings"]]
    yield from sorted(grid, key=rank)


_OPPOSITE = {"N": "S", "S": "N", "E": "W", "W": "E"}


def auto_plan(
    unit: str,
    room_name: str,
    budget_sar: float = 8000.0,
    style: list[str] | None = None,
    catalog: list[Product] | None = None,
    max_positions: int = 400,
) -> Plan:
    home = load_home()
    spec = home.unit(unit).room(room_name)
    room = spec.to_room()
    catalog = catalog if catalog is not None else parse_capture()
    recipe = RECIPES.get(room_name) or RECIPES.get(room_name.rstrip("_12")) or []

    plan = Plan(
        unit=unit, room=room_name, budget_sar=budget_sar,
        room_cm={
            "width": room.width_cm, "depth": room.depth_cm, "height": room.height_cm,
            # The door travels with the room so the studio can draw the real one
            # rather than assume a width. Its position is a convention (see
            # Home.assumptions), which is exactly why it must not be invented
            # a second time in the browser.
            "doors": [
                {"wall": d.wall, "offset_cm": d.offset_cm, "width_cm": d.width_cm}
                for d in room.doors
            ],
        },
    )
    route = home.route_to(unit, room_name)
    placed: list[Placement] = []

    for slot in sorted(recipe, key=lambda s: s["priority"]):
        budget_left = budget_sar - plan.total_sar
        pool = candidates_for(slot, catalog, budget_left, style)
        chosen = None
        rejected: list[dict] = []
        positions_tried_total = 0

        for product in pool:
            # Deliverability is a property of the product, not the position, so
            # it is checked once per candidate rather than once per placement.
            #
            # A rug is the one thing here that is not rigid: it ships rolled, so
            # judging a 3m rug against a 2.18m lift car rejects every rug in the
            # catalogue. None of them publish a carton size, so the rolled form
            # is INFERRED —the long side, about 30cm across — and the plan says
            # so rather than presenting it as measured.
            if product.category == "rug" and product.carton is None and product.dims.known:
                longest = max(product.dims.w, product.dims.d)
                carton = Dims(w=longest, d=30.0, h=30.0, confidence="inferred")
            else:
                carton = product.carton or product.dims
            access = check_access_path(
                carton, route, flexible=product.category == "rug")
            if access.status == "fail":
                blocker = next(
                    (r for r in access.reasons
                     if "cannot pass" in r or "does not fit" in r or "needs to swing" in r),
                    "cannot reach this room",
                )
                rejected.append({
                    "asin": product.asin, "title": product.title,
                    "price_sar": product.price_sar, "stage": "delivery",
                    "why": blocker,
                })
                continue

            tried = 0
            for x, y, facing in _positions(room, product, slot, placed):
                tried += 1
                if tried > max_positions:
                    break
                trial = Placement(
                    product.asin, product.dims, x=x, y=y,
                    facing=facing, role=slot["role"],
                )
                verdict = validate_layout(room, placed + [trial])
                if verdict.status == "pass":
                    chosen = (product, trial, access, tried)
                    break
                last_reason = verdict.reasons[0] if verdict.reasons else "no valid position"
            positions_tried_total += tried
            if chosen:
                break
            rejected.append({
                "asin": product.asin, "title": product.title,
                "price_sar": product.price_sar, "stage": "placement",
                "why": last_reason,
            })

        if chosen:
            product, trial, access, tried = chosen
            matched = sorted(set(product.style_tags) & set(style or []))
            chose_because = (
                f"matched {' + '.join(matched)}" if matched
                else (f"best evidence available ({product.rating}\u2605, {product.reviews} reviews)"
                      if product.rating else "cheapest that fits; no style tag or rating to go on")
            )
            placed_because = (
                f"first of {tried} positions tried that passed every rule "
                f"with the {len(placed)} item(s) already in the room"
            )
            placed.append(trial)
            plan.total_sar += product.price_sar or 0
            plan.placed.append(PlacedItem(
                slot_id=slot["slot_id"], role=slot["role"], asin=product.asin,
                title=product.title, url=product.url,
                price_sar=product.price_sar or 0,
                dims_cm={"w": product.dims.w, "d": product.dims.d, "h": product.dims.h},
                dims_confidence=product.dims_confidence,
                x=trial.x, y=trial.y, facing=trial.facing,
                access={
                    "status": access.status, "reasons": access.reasons,
                    "measured_using": (
                        "carton" if product.carton
                        else "rolled (inferred)" if product.category == "rug"
                        else "assembled"
                    ),
                },
                flat_pack=product.flat_pack,
                colour_hex=product.colour_hex,
                material=product.material,
                decision=Decision(
                    considered=len(pool),
                    ranked_above=[
                        {"asin": p.asin, "title": p.title, "price_sar": p.price_sar}
                        for p in pool[:4] if p.asin != product.asin
                    ],
                    rejected=rejected[:6],
                    positions_tried=tried,
                    chose_because=chose_because,
                    placed_because=placed_because,
                ),
            ))
        else:
            what = slot["category"].replace("_", " ")
            reason = (
                f"No {what} under {budget_left:.0f} SAR could be placed anywhere in "
                f"the room alongside what is already there."
                if pool else
                f"Nothing in the {what} category costs under the {budget_left:.0f} SAR "
                f"left in the budget."
            )
            plan.unfilled.append(UnfilledSlot(
                slot_id=slot["slot_id"], role=slot["role"],
                reason=reason, candidates_considered=len(pool),
                rejected=rejected[:8],
            ))

    final = validate_layout(room, placed)
    plan.validation = {"status": final.status, "reasons": final.reasons,
                       "notes": final.details.get("notes", [])}
    # What the room still lacks once it passes. Measured from the free wall and
    # floor the layout leaves behind, not sourced — the capture has no decor.
    plan.finishing = [styling.to_dict(s)
                      for s in styling.suggest_finishing(room, placed, style or [])]
    return plan


def swap(
    unit: str,
    room_name: str,
    placements: list[dict],
) -> dict:
    """Re-validate an arrangement the user has edited in the studio.

    The frontend can move and substitute anything; this is the only thing that
    decides whether the result holds. Nothing in the browser is trusted.
    """
    home = load_home()
    room = home.unit(unit).room(room_name).to_room()
    route = home.route_to(unit, room_name)

    objects = [
        Placement(
            p["asin"], Dims(p["dims_cm"]["w"], p["dims_cm"]["d"], p["dims_cm"]["h"]),
            x=p["x"], y=p["y"], facing=p.get("facing", "S"), role=p["role"],
        )
        for p in placements
    ]
    verdict = validate_layout(room, objects)

    access = {}
    for p in placements:
        dims = Dims(p["dims_cm"]["w"], p["dims_cm"]["d"], p["dims_cm"]["h"])
        a = check_access_path(dims, route)
        access[p["asin"]] = {"status": a.status, "reasons": a.reasons}

    return {
        "validation": {"status": verdict.status, "reasons": verdict.reasons,
                       "notes": verdict.details.get("notes", [])},
        "access": access,
        "total_sar": round(sum(p.get("price_sar", 0) for p in placements), 2),
    }


# Role -> catalogue category. Kept here rather than in the frontend so the
# static exporter and the browser cannot disagree about which products may
# fill which slot.
CATEGORY_FOR_ROLE_FALLBACK = {
    slot["role"]: slot["category"]
    for recipe in RECIPES.values() for slot in recipe
}


# --------------------------------------------------------------------------
# whole-flat planning
# --------------------------------------------------------------------------

def plan_flat(
    unit: str,
    budget_sar: float = 20000.0,
    style: list[str] | None = None,
) -> dict:
    """Plan every plannable room in a flat, sharing one budget.

    Budget is split by floor area, which is crude but defensible: a 335x551
    living room has real claim to more of the money than a 335x305 bedroom.
    Each room is then planned and validated independently — the engine has no
    concept of a flat, only of rooms, and inventing cross-room constraints it
    cannot check would be worse than not having them.

    **Rooms are NOT assembled into a floor plate.** The surveyed plan gives room
    dimensions but not their positions relative to one another, and placing them
    would mean inventing coordinates. They are returned as separate to-scale
    rooms, and the studio lays them out for viewing with that stated.
    """
    home = load_home()
    unit_obj = home.unit(unit)
    rooms = [r for r in unit_obj.rooms if r.name in RECIPES]
    if not rooms:
        return {"unit": unit, "rooms": [], "total_sar": 0.0, "budget_sar": budget_sar}

    areas = {r.name: r.width_cm * r.depth_cm for r in rooms}
    total_area = sum(areas.values())

    plans, spent = [], 0.0
    for room in rooms:
        share = budget_sar * areas[room.name] / total_area
        plan = auto_plan(unit, room.name, share, style).to_dict()
        plan["area_share_sar"] = round(share, 2)
        spent += plan["total_sar"]
        plans.append(plan)

    return {
        "unit": unit,
        "label": unit_obj.label,
        "rooms": plans,
        "total_sar": round(spent, 2),
        "budget_sar": budget_sar,
        "within_budget": spent <= budget_sar,
        "placed": sum(len(p["placed"]) for p in plans),
        "unfilled": sum(len(p["unfilled"]) for p in plans),
        "positions_surveyed": False,
        "layout_note": (
            "The floor plan dimensions each room but does not give their positions "
            "relative to one another, so these are shown side by side to scale "
            "rather than assembled into a floor. Every room's own layout is "
            "verified; the spacing between rooms is presentation only."
        ),
    }
