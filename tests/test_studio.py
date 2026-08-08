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
    # Advisories (a rug running under a door swing) are notes, not reasons:
    # a pass with populated `reasons` would blur the two.
    assert isinstance(plan.validation.get("notes", []), list)


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
    assert (body["room_cm"]["width"], body["room_cm"]["depth"]) == (335, 551)
    # The door ships with the room so the studio draws the real one instead of
    # hardcoding a width the server already owns.
    assert body["room_cm"]["doors"], "the studio needs the door to draw its swing"
    assert body["room_cm"]["doors"][0]["width_cm"] == 90


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

def test_static_context_key_matches_between_exporter_and_frontend():
    """The exporter names data files and studio.js fetches them. Nothing else
    couples the two, so a format change on either side would silently produce a
    site where every plan looks missing.
    """
    from pathlib import Path

    from tools.export_static import slug

    js = Path("app/static/studio.js").read_text(encoding="utf-8")
    assert ("const planKey = (u, r, s) => "
            "`${u}__${r}__${(s ?? []).length ? s.join('-') : 'any'}`") in js

    # Same three cases, both spellings.
    for unit, room, style in [
        ("unit01", "living_dining", ["warm", "minimal"]),
        ("unit04", "master_bedroom_1", []),
        ("unit05", "bedroom", ["boho", "scandi"]),
    ]:
        expected = f"{unit}__{room}__{'-'.join(style) if style else 'any'}"
        assert slug(unit, room, style) == expected


def test_static_studio_falls_back_rather_than_guessing():
    """A combination the bundle lacks must report `unverified`, never a made-up
    pass. The static build has no engine; guessing would be the one failure the
    whole project argues against."""
    from pathlib import Path

    js = Path("app/static/studio.js").read_text(encoding="utf-8")
    assert "'unverified'" in js
    assert "was not precomputed" in js


def test_the_studio_frontend_contains_no_geometry():
    """If a rule threshold ever appears in the browser, there are two engines."""
    from pathlib import Path
    js = Path("app/static/studio.js").read_text(encoding="utf-8")
    body = "\n".join(l for l in js.splitlines() if not l.strip().startswith(("//", "*", "/*")))
    for forbidden in ("WALKWAY", "90 * CM", "clearance >", "0.9 *", "40 * CM", "75 * CM"):
        assert forbidden not in body, f"{forbidden!r} suggests a rule threshold leaked into the browser"


def test_the_studio_translates_asins_into_words_for_the_user():
    """Verdict reasons name items by the id they were sent with — an ASIN.
    "Only 35cm between B0FR3WVLTS and B0H8PQ9KDJ" is precise and unreadable."""
    from pathlib import Path

    js = Path("app/static/studio.js").read_text(encoding="utf-8")
    assert "function humanise(" in js
    # Every place a reason reaches the user must go through it.
    assert "function humaniseWithin(" in js
    for site in ("humanise(first)", "humanise(r)", "humaniseWithin(text, r.placed)"):
        assert site in js, f"a reason path is missing humanise(): {site}"


def test_the_failure_reason_is_shown_next_to_the_verdict():
    """A red badge whose explanation sits below five product cards is not an
    explanation. The reason renders in the viewport and the panel scrolls back."""
    from pathlib import Path

    js = Path("app/static/studio.js").read_text(encoding="utf-8")
    html = Path("app/static/index.html").read_text(encoding="utf-8")
    assert 'id="verdict-why"' in html
    assert "$('#panel').scrollTo" in js


def test_the_loading_overlay_can_actually_hide():
    """`display:flex` on the base rule beats the UA stylesheet's [hidden], so
    the spinner sat over the canvas forever."""
    from pathlib import Path

    css = Path("app/static/studio.css").read_text(encoding="utf-8")
    assert "#loading[hidden] { display: none; }" in css


def test_unfilled_slots_are_not_drawn_at_invented_positions():
    """They were, as translucent footprints at Math.random() positions. The
    planner never computed a position for something it could not place, so a
    marker on the floor asserts a location that does not exist — the same class
    of lie as guessing a dimension."""
    from pathlib import Path

    for name in ("studio.js", "viewer.js"):
        js = Path(f"app/static/{name}").read_text(encoding="utf-8")
        body = "\n".join(l for l in js.splitlines()
                          if not l.strip().startswith(("//", "*", "/*")))
        assert "Math.random" not in body, name
        assert "addGhost" not in body, name


def test_a_failed_refresh_does_not_leave_a_stale_pass_on_screen():
    """The old verdict badge kept saying `pass` over a layout that had not been
    re-checked, which claims a verification that did not happen."""
    from pathlib import Path

    js = Path("app/static/studio.js").read_text(encoding="utf-8")
    assert "'not run'" in js
    assert "has not been re-checked" in js


def test_unfilled_reasons_do_not_leak_schema_underscores():
    p = auto_plan("unit01", "bedroom", 450)
    for u in p.unfilled:
        assert "floor_lamp" not in u.reason and "dining_table" not in u.reason, u.reason


def test_three_js_is_not_in_the_first_paint_bundle():
    """three.js is ~480 KB minified and 97% of the page's JavaScript. The plan,
    the reasoning and the verdict are text and must not wait on it."""
    from pathlib import Path

    js = Path("app/static/studio.js").read_text(encoding="utf-8")
    assert "import * as THREE" not in js, "three.js must not be a static import here"
    assert "import('./viewer.js')" in js, "the viewer must load dynamically"


def test_the_palette_has_one_definition():
    """The swatch in the panel and the material in the viewport were separate
    maps, so a legend dot could drift from the object it labelled."""
    from pathlib import Path

    palette = Path("app/static/palette.js").read_text(encoding="utf-8")
    assert "export const PALETTE" in palette
    for name in ("studio.js", "viewer.js"):
        js = Path(f"app/static/{name}").read_text(encoding="utf-8")
        assert "./palette.js" in js, f"{name} should import the shared palette"
        assert "const SWATCH" not in js and "const COLOR = {" not in js


def test_the_viewport_is_optional_scenery():
    """If three.js fails, the plan, verdicts and reasoning must still work."""
    from pathlib import Path

    js = Path("app/static/studio.js").read_text(encoding="utf-8")
    assert ".catch(" in js and "viewport-fallback" in js
