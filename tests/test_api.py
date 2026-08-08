"""FastAPI surface tests.

The API is the only thing the MCP shim talks to, and the shim is the only thing
the agents talk to. So the contract asserted here is effectively the contract
the whole swarm depends on.
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


# --------------------------------------------------------------------------
# knowledge base
# --------------------------------------------------------------------------

def test_kb_search_finds_the_designer_agent_answer(client):
    r = client.post("/kb/search", json={"query": "what does the designer agent do", "k": 3})
    assert r.status_code == 200
    results = r.json()["results"]
    assert results
    assert any("Designer Agent" in hit["text"] for hit in results)


def test_kb_search_returns_provenance_on_every_hit(client):
    r = client.post("/kb/search", json={"query": "return policy", "k": 3})
    for hit in r.json()["results"]:
        assert hit["source_url"]
        assert hit["doc_type"]


def test_kb_search_can_filter_to_policy_only(client):
    r = client.post("/kb/search", json={"query": "what may Adam do", "k": 5, "doc_type": "policy"})
    hits = r.json()["results"]
    assert hits
    assert {h["doc_type"] for h in hits} == {"policy"}


def test_kb_search_never_attributes_our_analysis_to_eazli(client):
    """Our own commentary is indexed too. It must be labelled, not laundered."""
    r = client.post("/kb/search", json={"query": "the access path gap", "k": 3})
    for hit in r.json()["results"]:
        if hit["doc_type"] == "analysis":
            assert "eazli.com" not in hit["source_url"]


# --------------------------------------------------------------------------
# home / floor plan
# --------------------------------------------------------------------------

def test_lists_the_surveyed_units(client):
    ids = {u["id"] for u in client.get("/home/units").json()["units"]}
    assert ids == {"unit01", "unit04", "unit05"}


def test_returns_a_single_room_with_its_imperial_source(client):
    room = client.get("/home/unit01/room/living_dining").json()
    assert room["width_cm"] == 335
    assert room["imperial"] == "11' x 18'1\""


def test_unknown_room_is_a_404(client):
    assert client.get("/home/unit01/room/ballroom").status_code == 404


# --------------------------------------------------------------------------
# fit
# --------------------------------------------------------------------------

def test_fit_check_passes_a_reasonable_sofa(client):
    r = client.post("/fit/check", json={
        "unit": "unit01", "room": "living_dining",
        "item": {"id": "sofa", "w": 185, "d": 88, "h": 80},
        "x": 120, "y": 300,
    })
    assert r.json()["status"] == "pass"


def test_fit_check_reports_unverified_for_missing_dimensions(client):
    r = client.post("/fit/check", json={
        "unit": "unit01", "room": "living_dining",
        "item": {"id": "sofa", "w": None, "d": None, "h": None, "confidence": "missing"},
        "x": 0, "y": 0,
    })
    body = r.json()
    assert body["status"] == "unverified"
    assert body["reasons"]


# --------------------------------------------------------------------------
# access path — the delivery route
# --------------------------------------------------------------------------

def test_access_check_uses_carton_dimensions_when_supplied(client):
    """A flat-packed item travels as panels, not as its assembled envelope.

    A bulky armchair whose every dimension exceeds the 89cm passage cannot be
    carried in assembled. The same chair boxed flat walks straight through, so
    judging the route on assembled size would reject a perfectly deliverable item.
    """
    armchair = {"id": "armchair", "w": 90, "d": 90, "h": 105}

    assembled_only = client.post("/access/check", json={
        "unit": "unit01", "room": "bedroom", "item": armchair,
    }).json()

    with_carton = client.post("/access/check", json={
        "unit": "unit01", "room": "bedroom", "item": armchair,
        "carton": {"w": 95, "d": 88, "h": 40},
    }).json()

    assert assembled_only["status"] == "fail"
    assert with_carton["status"] == "pass"
    assert with_carton["measured_using"] == "carton"


def test_access_check_routes_differ_by_destination_room(client):
    sofa = {"id": "sofa", "w": 218, "d": 95, "h": 84}
    living = client.post("/access/check", json={
        "unit": "unit01", "room": "living_dining", "item": sofa}).json()
    bedroom = client.post("/access/check", json={
        "unit": "unit01", "room": "bedroom", "item": sofa}).json()

    assert living["status"] == "pass"
    assert bedroom["status"] == "fail"


# --------------------------------------------------------------------------
# plan quota — Explorer vs Active
# --------------------------------------------------------------------------

def test_explorer_is_capped_at_five_layouts(client):
    r = client.post("/plan/quota", json={"plan": "explorer", "layouts_used": 5, "redesigns_used": 0})
    body = r.json()
    assert body["layouts_allowed"] is False
    assert "5" in body["message"]


def test_active_allows_fifteen_layouts(client):
    r = client.post("/plan/quota", json={"plan": "active", "layouts_used": 5, "redesigns_used": 0})
    assert r.json()["layouts_allowed"] is True


def test_explorer_is_single_room(client):
    r = client.post("/plan/quota", json={"plan": "explorer", "rooms_used": 2})
    assert r.json()["rooms_allowed"] is False


def test_layout_without_roles_is_unverified_not_passed(client):
    """Regression guard at the API boundary: bare ASINs used to yield a clean
    `pass` because the rules were selected by substring-matching the item id."""
    r = client.post("/layout/validate", json={
        "unit": "unit01", "room": "living_dining",
        "placements": [
            {"item": {"id": "B0FR3WVLTS", "w": 185, "d": 88, "h": 80}, "x": 40, "y": 300},
            {"item": {"id": "B0H8PQ9KDJ", "w": 80, "d": 80, "h": 30}, "x": 40, "y": 395},
        ],
    })
    assert r.json()["status"] == "unverified"


def test_layout_with_roles_catches_the_sofa_table_gap(client):
    r = client.post("/layout/validate", json={
        "unit": "unit01", "room": "living_dining",
        "placements": [
            {"item": {"id": "B0FR3WVLTS", "w": 185, "d": 88, "h": 80}, "x": 40, "y": 300, "role": "sofa"},
            {"item": {"id": "B0H8PQ9KDJ", "w": 80, "d": 80, "h": 30}, "x": 40, "y": 395, "role": "coffee_table"},
        ],
    })
    assert r.json()["status"] == "fail"


def test_fit_check_accepts_a_role_so_it_agrees_with_layout_validate(client):
    """The role fix reached validate_layout but not /fit/check, so the two
    endpoints returned different verdicts for the same dining table."""
    body = {"unit": "unit01", "room": "living_dining",
            "item": {"id": "t", "w": 140, "d": 80, "h": 76}, "x": 98, "y": 386,
            "role": "dining_table"}
    fit = client.post("/fit/check", json=body).json()
    layout = client.post("/layout/validate", json={
        "unit": "unit01", "room": "living_dining",
        "placements": [{"item": body["item"], "x": 98, "y": 386, "role": "dining_table"}],
    }).json()
    assert fit["status"] == layout["status"] == "pass"


def test_room_filter_does_not_hide_multi_room_products(client):
    """Every floor lamp is tagged 'living_dining,bedroom'. An exact-match
    filter made all eight invisible to a living-room search, which would lead
    an agent to report the lighting slot unfillable."""
    r = client.post("/products/search", json={
        "query": "floor lamp standing light", "room": "living_dining", "k": 10})
    cats = [p["category"] for p in r.json()["results"]]
    assert "floor_lamp" in cats


def test_unknown_price_is_not_treated_as_free(client):
    """Unknown prices are stored as -1.0, which passed every budget filter."""
    r = client.post("/products/search", json={
        "query": "sofa", "max_price_sar": 1200, "k": 20})
    for p in r.json()["results"]:
        assert p["price_sar"] is None or p["price_sar"] <= 1200


def test_health_is_not_ok_when_a_collection_is_missing(client):
    body = client.get("/health").json()
    assert body["ok"] == all(body["collections"].values())


def test_products_index_is_not_stale_against_the_capture_file(client):
    """`/products/search` reads the persisted `products` Chroma collection,
    which `ingest/build_catalog.py` builds from `parse_capture()` but does
    NOT rebuild automatically when the raw capture file grows. The capture
    went 75 -> 465 listings over four scrape passes and the index was
    rebuilt for some of those passes but not the last one: production sat at
    280 while the live capture (and every deterministic planner path, which
    calls `parse_capture()` directly) had already moved to 465. An agent
    calling `search_products` for B0CC2PLFGN -- the flagship target-seeking
    example in SHOWCASE.md -- got zero hits for a product that fits and is
    in budget, because it was scraped in a pass the index never absorbed.
    Equal counts is what "the search tool sees the same catalogue the
    planner does" actually means; nothing less proves it.
    """
    from app import store
    from app.catalog import parse_capture

    assert store.collection("products").count() == len(parse_capture())


def test_products_search_can_find_a_listing_from_the_latest_scrape_pass(client):
    """Concrete instance of the staleness above: this ASIN is real, priced,
    dimensioned and named explicitly in SHOWCASE.md's budget-tier debate. A
    stale index returns zero hits for it, which is indistinguishable from
    "does not exist" to every agent built on top of `search_products`.
    """
    r = client.post("/products/search", json={"query": "VanAcc L-shaped sofa bed", "k": 20})
    asins = [p["asin"] for p in r.json()["results"]]
    assert "B0CC2PLFGN" in asins


def test_design_principles_index_is_not_stale_against_the_rule_table(client):
    """`ingest/build_kb.py` builds `design_principles` from `RULES.items()` --
    one chunk per rule -- but nothing rebuilds it when `RULES` grows. `RULES`
    went from 6 entries to 13 across two commits (`reach_clearance`,
    `bedside_reach`, `bed_access`, `coffee_table_reach`, `tv_sightline`,
    `rug_anchors_seating`, `lamp_within_reach_of_seating`,
    `seat_has_a_focal_point` all landed after the index was last built), and
    the persisted collection was still sitting at exactly the original 6 --
    `rule-coffee_table_reach`, `rule-door_swing`, `rule-pull_out`,
    `rule-sofa_to_coffee_table`, `rule-walkway_primary`,
    `rule-walkway_secondary` -- with every ergonomic rule this project's own
    changelog calls out by name missing from it. `search_eazli_kb` is Noura's
    citation tool: asked why an armchair needs to face the bed, it had no
    rule to point to for seven of this engine's thirteen constraints.
    """
    from app import store
    from app.geometry import RULES

    assert store.collection("design_principles").count() == len(RULES)


def test_principles_search_can_cite_a_newly_landed_ergonomic_rule(client):
    """Concrete instance: `bed_access` is real, enforced in `geometry.py`,
    and referenced in this project's own commit history. A stale index
    returns zero hits for it."""
    r = client.post("/principles/search", json={
        "query": "how much clear space does a bed need to get into it", "k": 5})
    ids = [hit.get("rule") or hit.get("id") for hit in r.json()["results"]]
    assert any("bed_access" in str(i) for i in ids)
