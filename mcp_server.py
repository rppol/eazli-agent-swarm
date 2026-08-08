"""MCP shim over the eazli tool service.

Deliberately thin. Every tool here is a passthrough to a FastAPI endpoint that
already owns the logic and already has tests. Keeping the shim logic-free means
the agents and the HTTP clients cannot drift apart: there is exactly one
implementation of "does this fit", and both callers reach the same one.

Run the API first:
    uv run uvicorn app.main:app --port 8000
"""

from __future__ import annotations

import json
import os

import httpx
from mcp.server import MCPServer

API = os.environ.get("EAZLI_API", "http://127.0.0.1:8000")

mcp = MCPServer(
    "eazli-tools",
    version="0.1.0",
    instructions=(
        "Grounding tools for the eazli agent swarm. Spatial answers are computed "
        "deterministically in Python — treat them as authoritative and never "
        "override them with your own estimate."
    ),
)


def _call(method: str, path: str, payload: dict | None = None) -> str:
    try:
        with httpx.Client(base_url=API, timeout=30) as client:
            r = client.request(method, path, json=payload)
            r.raise_for_status()
            return json.dumps(r.json(), ensure_ascii=False, indent=2)
    except httpx.ConnectError:
        return json.dumps({
            "error": f"Cannot reach the eazli tool service at {API}.",
            "fix": "Start it with: uv run uvicorn app.main:app --port 8000",
        })
    except httpx.HTTPStatusError as exc:
        return json.dumps({"error": exc.response.text, "status": exc.response.status_code})


# --------------------------------------------------------------------------
# knowledge
# --------------------------------------------------------------------------

@mcp.tool()
def search_eazli_kb(query: str, k: int = 5, doc_type: str = "", agent: str = "") -> str:
    """Search what eazli publicly says about itself.

    Covers all 55 FAQ answers, the AI Agent Disclaimers & Scope policy, plan
    definitions, and vendor terms. Filter doc_type to one of: faq, policy,
    pricing, vendor, marketing, agent_profile, company, analysis.

    Note: doc_type 'analysis' is this project's own commentary, NOT published
    by eazli. Never present it as eazli's stated position.
    """
    return _call("POST", "/kb/search", {
        "query": query, "k": k,
        "doc_type": doc_type or None,
        "agent": agent or None,
    })


@mcp.tool()
def search_design_principles(query: str, k: int = 4) -> str:
    """Retrieve the spatial design rules, with the reasoning behind each.

    These are the same rules the geometry engine enforces, so citing one here
    is citing something that is actually checked, not a plausible-sounding
    guideline.
    """
    return _call("POST", "/principles/search", {"query": query, "k": k})


@mcp.tool()
def search_products(
    query: str,
    k: int = 8,
    room: str = "",
    max_price_sar: float = 0,
    max_width_cm: float = 0,
) -> str:
    """Search the amazon.sa catalog.

    Results carry dims_confidence. An item with confidence 'missing' has no
    usable dimensions: it must not be presented as a confirmed fit.
    """
    return _call("POST", "/products/search", {
        "query": query, "k": k,
        "room": room or None,
        "max_price_sar": max_price_sar or None,
        "max_width_cm": max_width_cm or None,
    })


# --------------------------------------------------------------------------
# home
# --------------------------------------------------------------------------

@mcp.tool()
def list_home_units() -> str:
    """List the surveyed flats, their configuration, and every room in each.

    Call this first when you do not already know which unit or room to work in.
    The response also lists which measurements were assumed rather than read off
    the floor plan (ceiling height, door leaf widths, lift car height) — say so
    when a verdict depends on one of them.
    """
    return _call("GET", "/home/units")


@mcp.tool()
def get_room(unit: str, room: str) -> str:
    """Get one room's real dimensions in cm, with the imperial source from the plan.

    Always call this before proposing a layout or judging whether something fits.
    Never estimate a room size from the room's name or from what is typical —
    these are surveyed numbers and they are the only ones that count.
    """
    return _call("GET", f"/home/{unit}/room/{room}")


# --------------------------------------------------------------------------
# spatial — deterministic, never inferred
# --------------------------------------------------------------------------

@mcp.tool()
def check_fit(
    unit: str,
    room: str,
    width_cm: float,
    depth_cm: float,
    height_cm: float,
    x: float,
    y: float,
    role: str = "",
    item_id: str = "item",
    facing: str = "S",
    dims_confidence: str = "stated",
) -> str:
    """Check whether an item fits at a position IN a room.

    Uses ASSEMBLED dimensions. Checks bounds, ceiling, door swing, fixed
    fixtures and the walkway rule.

    ALWAYS pass `role` — sofa, coffee_table, dining_table, floor_lamp,
    tv_console, bookshelf, wardrobe, bed, rug. It decides how much clear space
    the item needs in front: 90cm for seating, 75cm of pull-out room for
    tables, none for wall-standing pieces. Without it the result is
    'unverified' rather than a guess, because applying the seating rule to a
    console would reject it for a reason that does not apply.

    Pass dims_confidence='missing' when the listing has no dimensions: the
    result will be 'unverified', never 'pass'.
    """
    return _call("POST", "/fit/check", {
        "unit": unit, "room": room, "x": x, "y": y, "facing": facing,
        "role": role or None,
        "item": {
            "id": item_id, "w": width_cm, "d": depth_cm, "h": height_cm,
            "confidence": dims_confidence,
        },
    })


@mcp.tool()
def validate_layout(unit: str, room: str, placements_json: str) -> str:
    """Validate a whole layout: overlaps, walkways, door swings, sofa-table gaps.

    placements_json is a JSON list like:
      [{"item": {"id": "B0FR3WVLTS", "w": 185, "d": 88, "h": 80},
        "x": 40, "y": 180, "role": "sofa"},
       {"item": {"id": "B0H8PQ9KDJ", "w": 110, "d": 60, "h": 42},
        "x": 60, "y": 310, "role": "coffee_table"}]

    ALWAYS pass `role`. It selects which rules apply — seating gets a 90cm
    front-walkway requirement, and a sofa/table pair gets the 40cm reach rule.
    Product SKUs like ASINs carry no role information, so without it the result
    comes back 'unverified' rather than 'pass'.
    """
    try:
        placements = json.loads(placements_json)
    except json.JSONDecodeError as exc:
        return json.dumps({"error": f"placements_json is not valid JSON: {exc}"})
    return _call("POST", "/layout/validate", {
        "unit": unit, "room": room, "placements": placements,
    })


@mcp.tool()
def check_access_path(
    unit: str,
    room: str,
    width_cm: float,
    depth_cm: float,
    height_cm: float,
    carton_width_cm: float = 0,
    carton_depth_cm: float = 0,
    carton_height_cm: float = 0,
    item_id: str = "item",
) -> str:
    """Check whether the item can physically GET to the room.

    Walks the real delivery route: lift car, lobby, corridor, the right-angle
    turn, the flat entrance, and (for rooms behind a door) the 89cm internal
    passage and 75cm room door.

    Always pass carton dimensions when the listing gives them. A flat-packed
    item travels as panels, and judging it on assembled size rejects deliveries
    that would arrive fine.

    This is the check eazli's Fitment clause makes the customer responsible for.
    """
    payload = {
        "unit": unit, "room": room,
        "item": {"id": item_id, "w": width_cm, "d": depth_cm, "h": height_cm},
    }
    if carton_width_cm and carton_depth_cm and carton_height_cm:
        payload["carton"] = {
            "id": f"{item_id}-carton",
            "w": carton_width_cm, "d": carton_depth_cm, "h": carton_height_cm,
        }
    return _call("POST", "/access/check", payload)


@mcp.tool()
def check_plan_quota(
    plan: str = "explorer",
    rooms_used: int = 0,
    layouts_used: int = 0,
    redesigns_used: int = 0,
) -> str:
    """Check the Explorer/Active limits published on eazli's pricing page.

    Explorer: 1 room, 5 layouts, 10 re-designs per month.
    Active: multiple rooms, 15 layout revisions, 20 re-designs per month.
    """
    return _call("POST", "/plan/quota", {
        "plan": plan, "rooms_used": rooms_used,
        "layouts_used": layouts_used, "redesigns_used": redesigns_used,
    })


if __name__ == "__main__":
    mcp.run()
