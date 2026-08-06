"""The eazli swarm's tool service.

Every factual claim the agents make about space, price, or eazli's own policy
has to come through here. The agents supply judgement; this supplies facts.

Run:  uv run uvicorn app.main:app --reload
Docs: http://localhost:8000/docs
"""

from __future__ import annotations

from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app import store
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
    where = {"room": q.room} if q.room else None
    hits = store.search("products", q.query, k=max(q.k * 3, 24), where=where)

    def keep(p: dict) -> bool:
        if q.max_price_sar is not None and (p.get("price_sar") or 0) > q.max_price_sar:
            return False
        if q.max_width_cm is not None:
            width = p.get("width_cm")
            # An unknown width is not a small width. Keep it, flagged, and let
            # the fit check mark it unverified rather than silently dropping it.
            if width and width > q.max_width_cm:
                return False
        return True

    filtered = [h for h in hits if keep(h)][: q.k]
    return {"results": filtered, "count": len(filtered)}


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
        Placement(req.item.id, req.item.to_dims(), x=req.x, y=req.y, facing=req.facing),
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
    return {"status": verdict.status, "reasons": verdict.reasons}


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


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {
        "ok": True,
        "collections": {
            name: store.has_collection(name)
            for name in ("eazli_kb", "design_principles", "products")
        },
    }
