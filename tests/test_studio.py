"""Planner and studio-endpoint tests.

The studio's whole claim is that the browser draws and the engine decides. So
what matters here is that a plan is never emitted unless `validate_layout`
accepted it, and that an edit made in the browser is re-judged server-side
rather than taken on trust.
"""

import pytest
from fastapi.testclient import TestClient

from app.geometry import Dims, Placement, validate_layout
from app.home import load_home
from app.main import app
from app.planner import RECIPES, auto_plan, candidates_for, swap


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


# --------------------------------------------------------------------------
# the planner
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def plan():
    return auto_plan("unit01", "living_dining", 8000, ["warm", "minimal"])


def test_every_plan_it_emits_is_one_the_engine_accepts(plan):
    """The planner searches the validated space, so this is true by
    construction — and worth asserting, because the day it stops being true the
    studio starts rendering layouts that do not hold."""
    assert plan.validation["status"] == "pass"
    assert plan.validation["reasons"] == []


def test_the_plan_reproduces_when_re_validated_independently(plan):
    room = load_home().unit("unit01").room("living_dining").to_room()
    placements = [
        Placement(i.asin, Dims(i.dims_cm["w"], i.dims_cm["d"], i.dims_cm["h"]),
                  x=i.x, y=i.y, facing=i.facing, role=i.role)
        for i in plan.placed
    ]
    assert validate_layout(room, placements).status == "pass"


def test_it_fills_the_living_room_rather_than_giving_up(plan):
    """Greedy placement used to strand the sofa against a wall with no room in
    front, leaving the coffee table unfillable. Role-aware ordering fixed it."""
    assert len(plan.placed) >= 4
    assert any(i.role == "coffee_table" for i in plan.placed)


def test_it_stays_within_budget(plan):
    assert plan.total_sar <= plan.budget_sar


def test_a_tiny_budget_is_reported_not_exceeded():
    tight = auto_plan("unit01", "living_dining", 400)
    assert tight.total_sar <= 400
    assert tight.unfilled, "an unaffordable slot must be reported, not skipped"
    assert all(u.reason for u in tight.unfilled)


def test_planning_is_deterministic():
    """A planner that shuffles cannot be reviewed."""
    a = auto_plan("unit01", "living_dining", 8000, ["warm"]).to_dict()
    b = auto_plan("unit01", "living_dining", 8000, ["warm"]).to_dict()
    assert [i["asin"] for i in a["placed"]] == [i["asin"] for i in b["placed"]]
    assert [(i["x"], i["y"]) for i in a["placed"]] == [(i["x"], i["y"]) for i in b["placed"]]


def test_style_changes_what_gets_picked():
    warm = {i.asin for i in auto_plan("unit01", "living_dining", 8000, ["warm", "minimal"]).placed}
    lux = {i.asin for i in auto_plan("unit01", "living_dining", 8000, ["modern", "luxury"]).placed}
    assert warm != lux


def test_candidates_exclude_unusable_products():
    from app.catalog import parse_capture
    slot = RECIPES["living_dining"][0]
    for p in candidates_for(slot, parse_capture(), 100000, []):
        assert p.usable
        assert p.category == "sofa"


def test_a_bedroom_gets_a_bed_not_a_sofa():
    p = auto_plan("unit01", "bedroom", 6000)
    assert p.validation["status"] == "pass"
    assert all(i.role != "sofa" for i in p.placed)


# --------------------------------------------------------------------------
# swap — the browser proposes, the engine disposes
# --------------------------------------------------------------------------

def test_a_swap_that_breaks_a_clearance_is_rejected(plan):
    """Substituting a differently-sized sofa moves the geometry. The browser
    has no idea; the engine does."""
    placements = [
        {**{k: getattr(i, k) for k in ("slot_id", "role", "asin", "x", "y", "facing")},
         "dims_cm": i.dims_cm, "price_sar": i.price_sar}
        for i in plan.placed
    ]
    for p in placements:
        if p["role"] == "sofa":
            p["dims_cm"] = {"w": 190, "d": 85, "h": 85}   # shallower: table drifts out of range
    result = swap("unit01", "living_dining", placements)
    assert result["validation"]["status"] in {"fail", "unverified"}


def test_swap_returns_a_delivery_verdict_per_item(plan):
    placements = [
        {**{k: getattr(i, k) for k in ("slot_id", "role", "asin", "x", "y", "facing")},
         "dims_cm": i.dims_cm, "price_sar": i.price_sar}
        for i in plan.placed
    ]
    result = swap("unit01", "living_dining", placements)
    assert set(result["access"]) == {i.asin for i in plan.placed}
    assert all("status" in v for v in result["access"].values())


# --------------------------------------------------------------------------
# endpoints
# --------------------------------------------------------------------------

def test_plan_auto_endpoint(client):
    r = client.post("/plan/auto", json={
        "unit": "unit01", "room": "living_dining", "budget_sar": 8000,
        "style": ["warm", "minimal"]})
    body = r.json()
    assert r.status_code == 200
    assert body["validation"]["status"] == "pass"
    assert body["within_budget"] is True
    assert body["room_cm"] == {"width": 335, "depth": 551, "height": 290}


def test_candidates_endpoint_includes_unusable_items_flagged(client):
    """Hiding them would answer the wrong question. A customer asks 'why can't
    I pick that one', which needs the item present and the reason attached."""
    body = client.get("/plan/candidates/coffee_table").json()
    assert body["count"] >= 4
    assert all("dims_confidence" in i for i in body["items"])

    # `dining_table` has no unusable members — the table runners that polluted
    # that search were reclassified to `accessory`, which is the parser working.
    # Wardrobes keep theirs: three contradict themselves across fields.
    wardrobes = client.get("/plan/candidates/wardrobe").json()["items"]
    blocked = [w for w in wardrobes if not w["usable"]]
    assert blocked, "expected the self-contradicting wardrobes to survive as flagged"
    assert all(w["flags"] for w in blocked), "a blocked item must say why"


def test_studio_page_is_served(client):
    r = client.get("/studio")
    assert r.status_code == 200
    assert "eazli studio" in r.text


def test_three_js_is_vendored_locally_not_from_a_cdn(client):
    """The page must work with no network. A CDN import would break that and
    would not be caught by any other test."""
    html = client.get("/studio").text
    assert "unpkg" not in html and "cdn" not in html.lower()
    assert client.get("/static/vendor/three.module.js").status_code == 200


def test_root_redirects_to_the_studio(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (307, 308)
    assert r.headers["location"] == "/studio"


# --------------------------------------------------------------------------
# the static build
# --------------------------------------------------------------------------

def test_static_bundle_keys_match_what_the_frontend_computes():
    """The exporter writes keys and studio.js reads them. Nothing else couples
    the two, so a format change on either side would silently produce a site
    where every swap reports 'not precomputed'.
    """
    import re
    from pathlib import Path

    js = Path("app/static/studio.js").read_text(encoding="utf-8")
    # frontend: `${u}|${r}|${(s ?? []).join(',')}` then `${ctx}|${slot}|${asin}`
    assert "const key = (u, r, s) => `${u}|${r}|${(s ?? []).join(',')}`" in js
    assert re.search(r"BUNDLE\.swaps\[`\$\{ctx\}\|\$\{[a-z]+\.slot_id\}\|\$\{[a-z]+\.asin\}`\]", js)

    exporter = Path("tools/export_static.py").read_text(encoding="utf-8")
    assert "f\"{unit.id}|{room.name}|{','.join(style)}\"" in exporter
    assert "f\"|{placed['slot_id']}|{cand['asin']}\"" in exporter


def test_static_studio_falls_back_rather_than_guessing():
    """A combination the bundle lacks must report `unverified`, never a made-up
    pass. The static build has no engine; guessing would be the one failure the
    whole project argues against."""
    from pathlib import Path

    js = Path("app/static/studio.js").read_text(encoding="utf-8")
    assert "'unverified'" in js
    assert "not precomputed" in js


def test_the_studio_frontend_contains_no_geometry():
    """If a rule threshold ever appears in the browser, there are two engines."""
    from pathlib import Path
    js = Path("app/static/studio.js").read_text(encoding="utf-8")
    for forbidden in ("WALKWAY", "90 ", "clearance >", "0.9 *"):
        assert forbidden not in js.replace("// ", ""), f"{forbidden!r} suggests geometry leaked into the browser"
