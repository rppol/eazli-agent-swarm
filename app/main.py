"""The eazli swarm's tool service.

Every factual claim the agents make about space, price, or eazli's own policy
has to come through here. The agents supply judgement; this supplies facts.

Run:  uv run uvicorn app.main:app --reload
Docs: http://localhost:8000/docs
"""

from __future__ import annotations

from typing import Literal

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import store
from app.catalog import parse_capture
from app.planner import BUDGET_TIERS, DEFAULT_TIER, auto_plan, plan_flat, swap
from app.geometry import (
    Dims,
    Placement,
    check_access_path,
    check_fit,
    validate_layout,
)
from app.home import load_home

app = FastAPI(
    title="eazli swarm tools",
    version="0.1.0",
    description=(
        "Grounding layer for the eazli agent swarm. Spatial answers are computed "
        "deterministically, never inferred by a model."
    ),
)

# Plan limits taken verbatim from https://www.eazli.com/pricing
PLANS = {
    "explorer": {"rooms": 1, "layouts": 5, "redesigns": 10},
    "active": {"rooms": 99, "layouts": 15, "redesigns": 20},
}

STATIC_DIR = Path(__file__).parent / "static"


class RevalidatingStatic(StaticFiles):
    """Serve the studio assets with `no-cache`.

    Not "don't cache" — `no-cache` means "revalidate before reuse", so the
    ETag still saves the transfer when nothing changed. Without it the browser
    heuristically cached studio.js and kept running a build from several edits
    ago, which looks exactly like a fix that did not work.
    """

    def is_not_modified(self, response_headers, request_headers) -> bool:
        response_headers["cache-control"] = "no-cache"
        return super().is_not_modified(response_headers, request_headers)

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        response.headers["cache-control"] = "no-cache"
        return response


app.mount("/static", RevalidatingStatic(directory=STATIC_DIR), name="static")


# --------------------------------------------------------------------------
# models
# --------------------------------------------------------------------------

class ItemIn(BaseModel):
    id: str = "item"
    w: float | None = None
    d: float | None = None
    h: float | None = None
    confidence: Literal["stated", "parsed", "inferred", "missing"] = "stated"

    def to_dims(self) -> Dims:
        return Dims(w=self.w, d=self.d, h=self.h, confidence=self.confidence)


class KbQuery(BaseModel):
    query: str
    k: int = 5
    doc_type: str | None = None
    agent: str | None = None


class ProductQuery(BaseModel):
    query: str
    k: int = 8
    room: str | None = None
    max_price_sar: float | None = None
    max_width_cm: float | None = None


class FitRequest(BaseModel):
    unit: str
    room: str
    item: ItemIn
    x: float
    y: float
    facing: Literal["N", "S", "E", "W"] = "S"
    role: str | None = Field(
        default=None,
        description=(
            "What the item is. Decides how much clear space it needs in front: "
            "90cm for seating, 75cm of pull-out room for tables, none for "
            "wall-standing pieces. Omit it and the result is 'unverified' "
            "rather than a guess."
        ),
    )


class PlacementIn(BaseModel):
    item: ItemIn
    x: float
    y: float
    facing: Literal["N", "S", "E", "W"] = "S"
    role: str | None = Field(
        default=None,
        description=(
            "What the item is — sofa, coffee_table, dining_table, floor_lamp, etc. "
            "Decides which rules apply. Omit it and the role is inferred from the "
            "item id; if that fails the layout comes back 'unverified' rather than "
            "'pass', because a rule that did not run has not been passed."
        ),
    )


class LayoutRequest(BaseModel):
    unit: str
    room: str
    placements: list[PlacementIn]


class AccessRequest(BaseModel):
    unit: str
    room: str
    item: ItemIn
    carton: ItemIn | None = Field(
        default=None,
        description=(
            "Shipping carton dimensions. Supply these whenever known: a flat-packed "
            "item travels as panels, so scoring the delivery route against its "
            "assembled envelope rejects items that would arrive fine."
        ),
    )


class QuotaRequest(BaseModel):
    plan: Literal["explorer", "active"] = "explorer"
    rooms_used: int = 0
    layouts_used: int = 0
    redesigns_used: int = 0


# --------------------------------------------------------------------------
# knowledge base
# --------------------------------------------------------------------------

@app.post("/kb/search", tags=["knowledge"])
def kb_search(q: KbQuery) -> dict:
    """Search what eazli says about itself: FAQs, policies, plans, agent scope."""
    clauses = []
    if q.doc_type:
        clauses.append({"doc_type": q.doc_type})
    if q.agent:
        clauses.append({"agent": q.agent})
    where = clauses[0] if len(clauses) == 1 else ({"$and": clauses} if clauses else None)

    hits = store.search("eazli_kb", q.query, k=q.k, where=where)
    return {"results": hits, "count": len(hits)}


@app.post("/principles/search", tags=["knowledge"])
def principles_search(q: KbQuery) -> dict:
    """The spatial design rules geometry.py enforces, with their rationale."""
    hits = store.search("design_principles", q.query, k=q.k)
    return {"results": hits, "count": len(hits)}


@app.post("/products/search", tags=["catalog"])
def products_search(q: ProductQuery) -> dict:
    """Search the amazon.sa catalog, with hard filters applied after retrieval."""
    # Filter on the per-room boolean, not the comma-joined string.
    where = {f"room_{q.room}": True} if q.room else None
    hits = store.search("products", q.query, k=max(q.k * 3, 24), where=where)

    dropped_unknown_price = 0

    def keep(p: dict) -> bool:
        nonlocal dropped_unknown_price
        price = p.get("price_sar")
        if q.max_price_sar is not None:
            # -1 is the sentinel for "no price published". `or 0` turned that
            # into free, so unpriced items passed every budget filter.
            if price is None or price < 0:
                dropped_unknown_price += 1
                return False
            if price > q.max_price_sar:
                return False
        if q.max_width_cm is not None:
            width = p.get("width_cm")
            # An unknown width is not a small width. Keep it, flagged, and let
            # the fit check mark it unverified rather than silently dropping it.
            if width and width > q.max_width_cm:
                return False
        return True

    filtered = [h for h in hits if keep(h)][: q.k]
    for hit in filtered:
        # Surface "no published price" as null rather than as -1.
        if hit.get("price_sar") is not None and hit["price_sar"] < 0:
            hit["price_sar"] = None
    return {
        "results": filtered,
        "count": len(filtered),
        "dropped_unknown_price": dropped_unknown_price,
    }


# --------------------------------------------------------------------------
# home
# --------------------------------------------------------------------------

@app.get("/home/units", tags=["home"])
def list_units() -> dict:
    home = load_home()
    return {
        "units": [
            {"id": u.id, "label": u.label, "config": u.config,
             "rooms": [r.name for r in u.rooms]}
            for u in home.units
        ],
        "assumptions": home.assumptions,
        # The studio's budget dropdown is filled from here, and from the same
        # constant in the static build's index.json. Live and static must offer
        # the same amounts; a list retyped into studio.js would be a second
        # source of truth that drifts the first time one of them changes.
        "budget_tiers": BUDGET_TIERS,
        "default_tier": DEFAULT_TIER,
    }


@app.get("/home/{unit_id}/room/{room_name}", tags=["home"])
def get_room(unit_id: str, room_name: str) -> dict:
    home = load_home()
    try:
        room = home.unit(unit_id).room(room_name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "unit": unit_id,
        "name": room.name,
        "imperial": room.imperial,
        "width_cm": room.width_cm,
        "depth_cm": room.depth_cm,
        "height_cm": room.height_cm,
        "height_confidence": "assumed",
    }


# --------------------------------------------------------------------------
# spatial checks
# --------------------------------------------------------------------------

def _room_or_404(unit_id: str, room_name: str):
    home = load_home()
    try:
        return home, home.unit(unit_id).room(room_name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/fit/check", tags=["spatial"])
def fit_check(req: FitRequest) -> dict:
    """Does the item work *in* the room? Uses assembled dimensions."""
    _, spec = _room_or_404(req.unit, req.room)
    verdict = check_fit(
        Placement(
            req.item.id, req.item.to_dims(),
            x=req.x, y=req.y, facing=req.facing, role=req.role,
        ),
        spec.to_room(),
    )
    return {"status": verdict.status, "reasons": verdict.reasons, "details": verdict.details}


@app.post("/layout/validate", tags=["spatial"])
def layout_validate(req: LayoutRequest) -> dict:
    """Do the items work together: overlap, walkways, door swings."""
    _, spec = _room_or_404(req.unit, req.room)
    placements = [
        Placement(p.item.id, p.item.to_dims(), x=p.x, y=p.y, facing=p.facing, role=p.role)
        for p in req.placements
    ]
    verdict = validate_layout(spec.to_room(), placements)
    return {"status": verdict.status, "reasons": verdict.reasons,
            "notes": verdict.details.get("notes", [])}


@app.post("/access/check", tags=["spatial"])
def access_check(req: AccessRequest) -> dict:
    """Can the item physically *get to* the room?

    This is the check eazli's Fitment clause makes the customer responsible for.
    Uses carton dimensions when supplied, assembled dimensions otherwise.
    """
    home, _ = _room_or_404(req.unit, req.room)
    measured_using = "carton" if req.carton else "assembled"
    dims = (req.carton or req.item).to_dims()

    route = home.route_to(req.unit, req.room)
    verdict = check_access_path(dims, route)
    return {
        "status": verdict.status,
        "reasons": verdict.reasons,
        "measured_using": measured_using,
        "route": [s.name for s in route],
        "caveat": (
            "Assembled dimensions were used because no carton size was available. "
            "A flat-packed item may still arrive fine."
            if measured_using == "assembled"
            else None
        ),
    }


# --------------------------------------------------------------------------
# plan quota
# --------------------------------------------------------------------------

@app.post("/plan/quota", tags=["plan"])
def plan_quota(req: QuotaRequest) -> dict:
    """Enforce the Explorer / Active limits published on eazli's pricing page."""
    limits = PLANS[req.plan]
    rooms_ok = req.rooms_used < limits["rooms"]
    layouts_ok = req.layouts_used < limits["layouts"]
    redesigns_ok = req.redesigns_used < limits["redesigns"]

    messages = []
    if not rooms_ok:
        messages.append(
            f"The {req.plan.title()} plan covers {limits['rooms']} room(s). "
            "Designing another room needs the Active plan."
        )
    if not layouts_ok:
        messages.append(
            f"You've used all {limits['layouts']} layout options this month on the "
            f"{req.plan.title()} plan."
        )
    if not redesigns_ok:
        messages.append(
            f"You've used all {limits['redesigns']} re-designs this month on the "
            f"{req.plan.title()} plan."
        )

    return {
        "plan": req.plan,
        "limits": limits,
        "rooms_allowed": rooms_ok,
        "layouts_allowed": layouts_ok,
        "redesigns_allowed": redesigns_ok,
        "message": " ".join(messages) or "Within plan limits.",
    }


# --------------------------------------------------------------------------
# studio — the 3D simulator
# --------------------------------------------------------------------------

class PlanRequest(BaseModel):
    unit: str = "unit01"
    room: str = "living_dining"
    budget_sar: float = 8000
    style: list[str] = Field(default_factory=lambda: ["warm", "minimal"])


class SwapRequest(BaseModel):
    unit: str
    room: str
    placements: list[dict]


@app.post("/plan/auto", tags=["studio"])
def plan_auto(req: PlanRequest) -> dict:
    """Furnish a room by search. Deterministic — no model involved.

    Every arrangement it returns has already been through `validate_layout`,
    so the studio never renders a layout the engine has not accepted.
    """
    return auto_plan(req.unit, req.room, req.budget_sar, req.style).to_dict()


@app.post("/plan/flat", tags=["studio"])
def plan_whole_flat(req: PlanRequest) -> dict:
    """Plan every plannable room in a flat against one shared budget.

    Rooms are returned separately and to scale. The surveyed plan gives room
    sizes but not their positions relative to one another, so assembling them
    into a floor plate would mean inventing coordinates.
    """
    return plan_flat(req.unit, req.budget_sar, req.style)


@app.post("/plan/swap", tags=["studio"])
def plan_swap(req: SwapRequest) -> dict:
    """Re-validate an arrangement the user edited in the browser.

    Nothing the frontend says about fit is trusted; it sends coordinates and
    this returns the verdict.
    """
    return swap(req.unit, req.room, req.placements)


@app.get("/plan/candidates/{category}", tags=["studio"])
def plan_candidates(category: str, max_price_sar: float = 100000) -> dict:
    """Everything in the catalogue that could fill a slot, swappable or not.

    Unusable items are included and flagged rather than hidden — the studio
    shows them greyed out with the reason, because "why can't I pick that one"
    is the question a customer actually asks.
    """
    items = [
        {
            "asin": p.asin, "title": p.title, "url": p.url,
            "price_sar": p.price_sar, "rating": p.rating, "reviews": p.reviews,
            "dims_cm": {"w": p.dims.w, "d": p.dims.d, "h": p.dims.h},
            "dims_confidence": p.dims_confidence,
            "style": p.style_tags, "flags": p.flags,
            "usable": p.usable, "flat_pack": p.flat_pack,
        }
        for p in parse_capture()
        if p.category == category
    ]
    items.sort(key=lambda i: (not i["usable"], i["price_sar"] or 1e9))
    return {"category": category, "count": len(items), "items": items}


@app.get("/studio", include_in_schema=False)
def studio() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse("/studio")


@app.get("/health", tags=["meta"])
def health() -> dict:
    collections = {
        name: store.has_collection(name)
        for name in ("eazli_kb", "design_principles", "products")
    }
    # `ok` used to be hardcoded True while reporting all three missing.
    return {"ok": all(collections.values()), "collections": collections}
