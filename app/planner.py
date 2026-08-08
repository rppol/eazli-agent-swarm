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

# What the studio's budget control offers.
#
# It used to be a free-text number box, which on the static build could not do
# anything: Pages runs no Python, so every plan is precomputed per context and
# an arbitrary amount had no file to fetch. The control now offers the amounts
# the exporter actually plans for, and the tier is part of the context key.
#
# Budget is a ceiling, not a target — the planner ranks on style match, then
# evidence, then price, and stops when the room is full. Above the starter tier
# this catalogue's best-scoring candidate in every slot is already affordable,
# so comfort and premium buy headroom rather than a different room, and the
# spend beside the verdict says so. The notes below describe the headroom.
#
# The API is deliberately not restricted to these. An agent or a CLI call is
# not holding a dropdown; `auto_plan` and /plan/auto still take any number.
BUDGET_TIERS: list[dict] = [
    {"id": "starter", "label": "Starter", "sar": 3000,
     "note": "furnish the essentials"},
    {"id": "standard", "label": "Standard", "sar": 8000,
     "note": "the default brief"},
    {"id": "comfort", "label": "Comfort", "sar": 15000,
     "note": "room to upgrade the seating"},
    {"id": "premium", "label": "Premium", "sar": 30000,
     "note": "best available in every slot"},
]

DEFAULT_TIER = "standard"


# What it takes before an item may be preferred *for costing more*.
#
# Target-seeking (below) can make the planner pay more for a slot. That is only
# defensible if the dearer piece is actually better evidenced, and in this
# capture the correlation runs the wrong way: of the 36 listings over 15,000
# SAR, 26 publish no dimensions at all and most carry no rating. "Dearer" here
# is frequently just a seller's asking price with nothing behind it.
#
# So an item may only win on being closer to a budget target if it has a real
# rating, a review count that is more than one person's opinion, and no flag
# saying we distrust its dimensions or its price. Below this floor the price
# term stays cheapest-first, exactly as it was.
#
# 5 reviews is where an average stops being a single anecdote. Measured
# consequence, stated because it matters: NO sofa in this catalogue clears
# this floor — the entire Interwood quality ladder (linen 1,001 SAR through
# air-leather 3,205 SAR) has between 0 and 2 reviews per listing. The slot
# that carries the most spend is therefore the slot that cannot be upgraded,
# and the honest response is to report that rather than to lower the bar
# until the feature looks like it works.
UPGRADE_EVIDENCE_FLOOR = {"min_rating": 3.5, "min_reviews": 5}

# Flags that mean "we do not believe this listing", as opposed to flags that
# merely record an assumption (`assumed_depth`) or a provenance problem that
# does not bear on fit (`colour_conflict`).
PLAUSIBILITY_FLAGS = frozenset({
    "implausible_for_category",
    "implausible_price",
    "dimension_conflict",
    "incomplete_labelled_dimensions",
})

# How spare budget is spread across the slots of a room.
#
# Keyed by catalogue category. The sofa takes the largest share because it is
# recipe priority 1, it carries roughly half of a living room's spend, and it
# is the only slot in this catalogue with anything resembling a quality ladder
# (linen -> bouclé -> velvet -> air-leather within the Interwood line).
#
# Deliberately NOT normalised to 1.0 across whichever slots a recipe happens to
# have. Normalising would push a bedroom's entire headroom into its rug and
# lamp — the only two weighted slots it has — and target a 240 SAR rug at
# several thousand. An unweighted slot gets no headroom, which errs towards
# not spending, and the totals below sum to 1.0 only for a fully filled
# living/dining room.
SLOT_HEADROOM_WEIGHTS = {
    "sofa": 0.45,
    "dining_table": 0.25,
    "coffee_table": 0.15,
    "tv_unit": 0.08,
    "rug": 0.04,
    "floor_lamp": 0.03,
    # The bedroom's anchors, on the same reasoning as the sofa and the dining
    # table: bed is recipe priority 1 and takes most of the room's spend,
    # wardrobe is the other large piece. Without these a bedroom's entire
    # headroom would land on its lamp and its rug (0.07 between them), which
    # is the one slot pairing where the catalogue has cheap, well-reviewed
    # stock and nothing to upgrade to.
    "bed": 0.45,
    "wardrobe": 0.25,
}


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
    # Why the budget was not spent. Counts, not prose — see `_unspent_budget`.
    unspent_budget: dict = field(default_factory=dict)

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
            "unspent_budget": self.unspent_budget,
        }


def _clears_evidence_floor(product: Product) -> bool:
    """Whether this item is allowed to win on being dearer. See
    UPGRADE_EVIDENCE_FLOOR for why the bar exists and where it bites."""
    return (
        product.rating is not None
        and product.rating >= UPGRADE_EVIDENCE_FLOOR["min_rating"]
        and product.reviews >= UPGRADE_EVIDENCE_FLOOR["min_reviews"]
        and not (set(product.flags) & PLAUSIBILITY_FLAGS)
    )


def _score(product: Product, style: list[str], slot_target: float | None = None) -> tuple:
    """Rank candidates. Style match first, then evidence, then price.

    Ties are broken deterministically so the same request always produces the
    same plan — a planner that shuffles is impossible to review.

    `slot_target` turns the price term from "cheapest" into "closest to this
    amount", which is the only reason a bigger budget can buy a better room:
    with price sorting strictly ascending, raising the ceiling could unblock a
    candidate but never prefer one, and standard, comfort and premium returned
    byte-identical plans.

    The switch is per candidate, not per slot. An item that does not clear the
    evidence floor keeps the plain ascending price term, so it can still be
    picked when it is genuinely the cheapest thing that fits, but it can never
    be picked *because* it is dearer.
    """
    matched = len(set(product.style_tags) & set(style or []))
    price = product.price_sar or 1e9
    price_term = (
        abs(price - slot_target)
        if slot_target is not None and _clears_evidence_floor(product)
        else price
    )
    return (
        -matched,
        -(product.rating or 0) * min(product.reviews, 200) / 200,
        price_term,
        product.asin,
    )


def candidates_for(
    slot: dict,
    catalog: list[Product],
    budget_left: float,
    style: list[str] | None = None,
    slot_target: float | None = None,
) -> list[Product]:
    """Everything that could fill this slot, best first.

    Budget stays a hard cap regardless of any target: `slot_target` changes
    the ORDER of this list, never its membership.

    `implausible_price` items are excluded here rather than in `Product.usable`
    on purpose. They remain in the catalogue and in search results — an agent
    has to be able to find the 185,350 SAR coffee table in order to say it is
    dismissing it — but "still in the index" and "silently recommended" are
    different things, and this is the recommendation path.
    """
    pool = [
        p for p in catalog
        if p.category == slot["category"]
        and p.usable
        and "implausible_price" not in p.flags
        and p.price_sar is not None
        and p.price_sar <= budget_left
    ]
    return sorted(pool, key=lambda p: _score(p, style or [], slot_target))


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
    """Furnish a room, twice.

    The first pass is the plan this algorithm has always produced: cheapest
    acceptable candidate per slot, budget as a ceiling. That pass is what
    establishes the room's *natural* spend, which is the only honest basis for
    saying how much money is actually spare — a target guessed from the budget
    alone would be a target for slots that may not even fill.

    The second pass re-runs the search with a per-slot target: natural spend
    plus that slot's share of the headroom. If there is no headroom, or no slot
    is eligible for any, the first plan is returned unchanged rather than
    recomputed to the same answer.
    """
    natural = _plan_once(unit, room_name, budget_sar, style, catalog,
                         max_positions, targets={})
    targets = _headroom_targets(natural, budget_sar)
    if not targets:
        return natural
    return _plan_once(unit, room_name, budget_sar, style, catalog,
                      max_positions, targets=targets,
                      natural_picks={i.slot_id: i.asin for i in natural.placed})


def _headroom_targets(natural: Plan, budget_sar: float) -> dict[str, float]:
    """Per-slot spend to aim at, given what the room costs left to itself.

    Returns {} when there is nothing to redistribute, which is the common case
    at the starter tier and the signal to skip the second pass entirely.
    """
    headroom = max(0.0, budget_sar - natural.total_sar)
    if headroom <= 0:
        return {}

    recipe = RECIPES.get(natural.room) or RECIPES.get(natural.room.rstrip("_12")) or []
    category_for_slot = {s["slot_id"]: s["category"] for s in recipe}

    targets: dict[str, float] = {}
    for item in natural.placed:
        weight = SLOT_HEADROOM_WEIGHTS.get(category_for_slot.get(item.slot_id, ""))
        if weight:
            targets[item.slot_id] = item.price_sar + headroom * weight
    return targets


def _plan_once(
    unit: str,
    room_name: str,
    budget_sar: float,
    style: list[str] | None,
    catalog: list[Product] | None,
    max_positions: int,
    targets: dict[str, float],
    natural_picks: dict[str, str] | None = None,
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
    # Tallied as the search runs, for the unspent-budget report. Counted here
    # rather than reconstructed afterwards for the same reason `Decision` is:
    # a number worked out after the fact is a guess about what happened.
    access_failures = 0
    placement_failures = 0

    for slot in sorted(recipe, key=lambda s: s["priority"]):
        budget_left = budget_sar - plan.total_sar
        slot_target = targets.get(slot["slot_id"])
        pool = candidates_for(slot, catalog, budget_left, style, slot_target)
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
                access_failures += 1
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
            placement_failures += 1
            rejected.append({
                "asin": product.asin, "title": product.title,
                "price_sar": product.price_sar, "stage": "placement",
                "why": last_reason,
            })

        if chosen:
            product, trial, access, tried = chosen
            matched = sorted(set(product.style_tags) & set(style or []))
            # The narration has to name the mechanism that actually chose this
            # item. "cheapest that fits" was a hardcoded fallback, true only
            # while price sorted ascending \u2014 printing it over a piece the
            # planner deliberately paid more for is exactly the confident
            # wrong answer this project exists to refuse.
            #
            # The test is whether the target CHANGED the pick, not merely
            # whether one was set. A slot can carry a target, and the item can
            # clear the evidence floor, and style or evidence can still have
            # decided it \u2014 claiming an upgrade there would be the same lie
            # pointed the other way.
            targeted = (
                slot_target is not None
                and _clears_evidence_floor(product)
                and natural_picks is not None
                and natural_picks.get(slot["slot_id"]) != product.asin
            )
            if targeted:
                chose_because = (
                    f"budget target for this slot was {slot_target:.0f} SAR; at "
                    f"{product.price_sar:.0f} SAR this is the closest piece to "
                    f"that target which clears the evidence floor "
                    f"({product.rating}\u2605, {product.reviews} reviews)"
                    + (f", and it matched {' + '.join(matched)}" if matched else "")
                )
            elif matched:
                chose_because = f"matched {' + '.join(matched)}"
            elif product.rating:
                chose_because = (f"best evidence available ({product.rating}\u2605, "
                                 f"{product.reviews} reviews)")
            else:
                chose_because = "cheapest that fits; no style tag or rating to go on"
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
    plan.unspent_budget = _unspent_budget(
        plan, recipe, catalog, access_failures, placement_failures)
    return plan


def _unspent_budget(
    plan: Plan,
    recipe: list[dict],
    catalog: list[Product],
    access_failures: int,
    placement_failures: int,
) -> dict:
    """Why the money was not spent, as counts.

    A premium plan that spends a fraction of its budget looks like a failure
    unless it can say what stood between the two numbers. It is not a failure:
    this catalogue genuinely cannot fill a verified 30,000 SAR living/dining
    room, and the reason is visible in the rejections — the expensive end of
    the assortment is overwhelmingly the part that publishes no dimensions, so
    it can never be verified to fit, whatever it costs.

    Deliberately data rather than a sentence. The frontend renders it, and a
    number the reader can check beats a paragraph they have to trust.
    """
    categories = {s["category"] for s in recipe}
    in_scope = [p for p in catalog if p.category in categories]

    return {
        "budget_sar": plan.budget_sar,
        "spent_sar": round(plan.total_sar, 2),
        "unspent_sar": round(plan.budget_sar - plan.total_sar, 2),
        "slots_filled": len(plan.placed),
        "slots_unfilled": len(plan.unfilled),
        "candidates_in_scope": len(in_scope),
        "candidates_rejected": {
            "no_published_dimensions": sum(
                1 for p in in_scope if not p.dims.known),
            "dimensions_implausible_for_category": sum(
                1 for p in in_scope if "implausible_for_category" in p.flags),
            "price_implausible_for_category": sum(
                1 for p in in_scope if "implausible_price" in p.flags),
            "wrong_category_for_the_search": sum(
                1 for p in in_scope if "category_mismatch" in p.flags),
            "no_price_published": sum(
                1 for p in in_scope if p.price_sar is None),
            "could_not_be_delivered": access_failures,
            "no_valid_position_left_in_the_room": placement_failures,
        },
    }


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
