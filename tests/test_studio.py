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
from app.planner import BUDGET_TIERS, RECIPES, auto_plan, candidates_for, swap


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
# budget tiers — the studio's vocabulary, not the engine's
# --------------------------------------------------------------------------

def test_the_tiers_span_the_range_the_catalogue_can_actually_reach():
    """A tier the exporter cannot tell apart from its neighbour is a fifth
    dropdown entry that produces an identical plan. The four here are spaced
    across the real price range, cheapest plan to dearest."""
    ids = [t["id"] for t in BUDGET_TIERS]
    assert ids == ["starter", "standard", "comfort", "premium"]
    amounts = [t["sar"] for t in BUDGET_TIERS]
    assert amounts == sorted(amounts), "the picker reads top to bottom"
    assert all(t["label"] and t["note"] for t in BUDGET_TIERS), "a tier must say what it buys"


def test_a_bigger_tier_never_returns_a_smaller_room():
    """Budget is a ceiling first. Raising it can only widen the candidate pool,
    so it must never cost a slot that a tighter tier managed to fill.

    Budget is now also a per-slot target (see `planner.SLOT_HEADROOM_WEIGHTS`),
    but a target may only pull the pick upwards when the dearer item clears an
    evidence floor, and this catalogue almost never offers one: within the
    style-matched tier of a "warm + minimal" brief, no living/dining slot has
    two floor-clearing candidates at different prices except the TV unit, where
    the whole available upgrade is 9.67 SAR.

    So what this does NOT assert stands: that every step up changes something.
    Above the starter tier it does not — standard, comfort and premium all
    return the same room, 3,750.61 of 30,000 spent, and `unspent_budget` says
    why with counts. Asserting a difference here would be asserting a wish."""
    plans = [
        auto_plan("unit01", "living_dining", t["sar"], ["warm", "minimal"])
        for t in BUDGET_TIERS
    ]
    for lower, higher in zip(plans, plans[1:]):
        assert len(higher.placed) >= len(lower.placed)
        assert higher.total_sar <= higher.budget_sar
    # The cheapest tier is a real constraint, not a relabelling of the default.
    assert len(plans[0].placed) < len(plans[1].placed)


def test_the_api_still_takes_any_budget_a_caller_asks_for(client):
    """The tiers are what the dropdown offers. An agent or a CLI call is not
    holding a dropdown, and constraining the endpoint to four amounts would
    narrow the engine to fit a UI affordance."""
    r = client.post("/plan/auto", json={
        "unit": "unit01", "room": "living_dining", "budget_sar": 6543,
        "style": ["warm", "minimal"]})
    body = r.json()
    assert r.status_code == 200
    assert body["budget_sar"] == 6543
    assert body["total_sar"] <= 6543


def test_the_tier_list_reaches_the_browser_as_data_in_both_modes():
    """Live and static must offer the same four. A list retyped into studio.js
    is a second source of truth that drifts the first time an amount changes."""
    from pathlib import Path

    from app.main import list_units
    from app.planner import DEFAULT_TIER
    from tools.export_static import index_payload

    for served in (list_units(), index_payload()):
        assert served["budget_tiers"] == BUDGET_TIERS
        # Which one the page opens on is Python's call too, so the tier the
        # dropdown starts on is always one the exporter planned for.
        assert served["default_tier"] == DEFAULT_TIER
    assert DEFAULT_TIER in {t["id"] for t in BUDGET_TIERS}

    js = Path("app/static/studio.js").read_text(encoding="utf-8")
    # The one legitimate 8000 in that file is the uvicorn port in the "run it
    # locally" hint, which is not a budget.
    js = js.replace("port 8000", "port <port>")
    for tier in BUDGET_TIERS:
        assert str(tier["sar"]) not in js, f"{tier['id']}'s amount is hardcoded in the browser"
        assert tier["label"] not in js, f"{tier['id']}'s label is hardcoded in the browser"
        assert f"'{tier['id']}'" not in js, f"{tier['id']} is named in the browser"


def test_the_budget_control_offers_tiers_rather_than_a_free_text_number():
    """It was `<input type=number>`. On the static build nothing is computed in
    the browser — every plan is precomputed per context — so typing 8137 SAR
    changed the number beside the verdict and nothing else. The control now
    offers exactly the amounts that were planned for."""
    from pathlib import Path

    html = Path("app/static/index.html").read_text(encoding="utf-8")
    assert '<select id="budget">' in html
    assert 'id="budget" type="number"' not in html


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
    # Derived from the sofa actually placed, not a hardcoded 190x85. The
    # catalogue grew from 75 to 280 listings, the winning sofa changed, and a
    # literal "shallower" depth stopped being shallower than the new one — the
    # test passed a clean layout and asserted it was broken.
    sofa = next(p for p in placements if p["role"] == "sofa")
    sofa["dims_cm"] = {**sofa["dims_cm"], "d": 20}
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
    assert ("const planKey = (u, r, s, t) => "
            "`${u}__${r}__${(s ?? []).length ? s.join('-') : 'any'}__${t}`") in js

    # Same three cases, both spellings.
    for unit, room, style, tier in [
        ("unit01", "living_dining", ["warm", "minimal"], "standard"),
        ("unit04", "master_bedroom_1", [], "starter"),
        ("unit05", "bedroom", ["boho", "scandi"], "premium"),
    ]:
        expected = f"{unit}__{room}__{'-'.join(style) if style else 'any'}__{tier}"
        assert slug(unit, room, style, tier) == expected


def test_static_studio_falls_back_rather_than_guessing():
    """A combination the bundle lacks must report `unverified`, never a made-up
    pass. The static build has no engine; guessing would be the one failure the
    whole project argues against."""
    from pathlib import Path

    js = Path("app/static/studio.js").read_text(encoding="utf-8")
    assert "'unverified'" in js
    assert "was not precomputed" in js


# --------------------------------------------------------------------------
# content-addressed swap tables
#
# Named by context, the swap tables were 200 files holding 28 distinct
# payloads: 27.6 MB of which 86% was byte-identical, because most budget tiers
# currently plan the same room and so offer the same substitutions. Naming a
# file after the hash of its content collapses the copies, whatever the ratio
# turns out to be after the planner changes.
# --------------------------------------------------------------------------

def test_identical_swap_tables_are_written_once():
    """The whole point: two contexts whose swap verdicts came out the same must
    end up pointing at one file, not two copies of 138 KB."""
    import tempfile
    from pathlib import Path

    from tools.export_static import write_addressed

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        table = {"sofa_1|B0ABC": {"validation": {"status": "pass"}}}
        first = write_addressed(out, table)
        # Same verdicts, built in a different insertion order — the same table
        # as far as anything reading it is concerned.
        second = write_addressed(out, dict(reversed(list(table.items()))))
        assert first == second
        assert [p.name for p in out.glob("*.json")] == [f"{first}.json"]


def test_the_swap_digest_is_stable_across_builds():
    """A digest that moved with dict ordering or float formatting would rewrite
    every swap file on every build — the churn the content addressing exists to
    remove — and blow up the Pages artifact diff."""
    import tempfile
    from pathlib import Path

    from tools.export_static import write_addressed

    table = {"b|B0002": {"total_sar": 1234.5}, "a|B0001": {"total_sar": 10.0}}
    digests = []
    for _ in range(2):
        with tempfile.TemporaryDirectory() as tmp:
            digests.append(write_addressed(Path(tmp), table))
    assert digests[0] == digests[1]
    assert len(digests[0]) >= 8, "too short a digest starts colliding"


def test_every_swaps_ref_a_plan_names_exists_on_disk():
    """The dangling-reference test, and the reason the other two exist.

    The digest is no longer derivable from the context key, so a plan that
    names a file the exporter did not write is not a 404 the visitor sees — it
    is a swap picker that silently reports `unverified` for every candidate.
    Nothing else in the build would notice.

    One unit rather than all three: the same code path, a third of the engine
    time in the suite.
    """
    import json
    import tempfile
    from pathlib import Path

    from tools.export_static import build_data

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        stats = build_data(out=out, units=["unit05"])
        data = out / "data"

        plans = sorted((data / "plans").glob("*.json"))
        assert plans, "the fixture built nothing to check"
        for path in plans:
            ref = json.loads(path.read_text(encoding="utf-8")).get("swaps_ref")
            assert ref, f"{path.name} names no swap table"
            assert (data / "swaps" / f"{ref}.json").exists(), \
                f"{path.name} points at a swap table that was never written"

        # Every file written is one some plan asks for: an orphan means a
        # context was planned twice, or a digest was computed two ways.
        named = {json.loads(p.read_text(encoding="utf-8"))["swaps_ref"] for p in plans}
        on_disk = {p.stem for p in (data / "swaps").glob("*.json")}
        assert on_disk == named
        assert stats["swap_files"] == len(on_disk)


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


# --------------------------------------------------------------------------
# telling one product from another in the render
#
# Measured against the capture: 31 usable items publish no colour, 67 publish
# no material, and 22 sat in 9 groups that drew pixel-identically. The colour
# fallback was the worst of it — a sofa with no published colour rendered
# exactly like one that genuinely is that colour, which is a differentiation
# failure and a claim nobody made.
# --------------------------------------------------------------------------

def test_material_changes_the_surface_not_just_the_word_in_the_panel():
    """Every piece got one surface — roughness 0.72, metalness 0.02 — so two
    grey sofas, one leather and one bouclé, were the same object on screen."""
    import re
    from pathlib import Path

    from app.catalog import MATERIAL_WORDS

    js = Path("app/static/viewer.js").read_text(encoding="utf-8")
    block = re.search(r"const SURFACES = \{(.*?)\n\};", js, re.S)
    assert block, "viewer.js must map the published material to a surface"
    surfaces = {
        name: {k: float(v) for k, v in re.findall(r"(\w+): ([\d.]+)", body)}
        for name, body in re.findall(r"^\s*(\w+):\s*\{([^}]*)\}", block.group(1), re.M)
    }

    # Every word the parser can emit must land somewhere, or an item silently
    # falls back to the same surface as an item that published nothing.
    for word in MATERIAL_WORDS:
        name = "boucle" if word == "bouclé" else word
        assert name in surfaces, f"{name} is a material the catalogue publishes"

    # Gloss is the difference you can see from across the room.
    assert surfaces["leather"]["roughness"] < surfaces["linen"]["roughness"]
    assert surfaces["marble"]["roughness"] < surfaces["boucle"]["roughness"]
    assert surfaces["metal"]["metalness"] > surfaces["fabric"]["metalness"]
    # "metal" and "steel" are two different published claims, and two otherwise
    # identical beds differ by nothing else.
    assert surfaces["metal"] != surfaces["steel"]


def test_an_unpublished_colour_is_not_replaced_by_an_invented_one():
    """31 usable items state no colour. Painting them in the role's colour
    asserted a colour nobody published and made them indistinguishable from the
    items that really are that colour. Deriving one from the ASIN would be the
    same lie with an extra step."""
    import re
    from pathlib import Path

    palette = Path("app/static/palette.js").read_text(encoding="utf-8")
    viewer = Path("app/static/viewer.js").read_text(encoding="utf-8")
    studio = Path("app/static/studio.js").read_text(encoding="utf-8")

    unknown = re.search(r"UNKNOWN_COLOUR = '(#[0-9a-f]{6})'", palette)
    assert unknown, "palette.js must define one colour for 'not published'"

    roles = re.search(r"export const PALETTE = \{(.*?)\n\};", palette, re.S).group(1)
    assert unknown.group(1) not in roles, "unknown must not be one of the real colours"

    # The two places the fallback used to live.
    assert "colourFor(item.role)" not in viewer
    assert "PALETTE[i.role]" not in studio
    # Nothing about how a piece looks may come from its id.
    for name, js in (("viewer.js", viewer), ("studio.js", studio)):
        body = "\n".join(l for l in js.splitlines()
                         if not l.strip().startswith(("//", "*", "/*")))
        assert not re.search(r"asin.*(?:colour|color|hue|hash)", body, re.I), name


def test_the_legend_explains_what_an_unpublished_attribute_looks_like():
    """A consistent treatment nobody can read is just another colour."""
    from pathlib import Path

    js = Path("app/static/studio.js").read_text(encoding="utf-8")
    css = Path("app/static/studio.css").read_text(encoding="utf-8")
    assert "$('#legend dl')" in js, "the note belongs in the existing legend"
    assert "colour not published" in js and "material not published" in js
    # The panel dot carries the same treatment as the object in the viewport.
    assert ".swatch.unknown" in css


def test_a_rug_without_a_published_material_is_not_given_a_weave():
    """Five usable rugs are the same grey at the same 300x200 and state no
    material between them. They are genuinely the same published object, so
    they must keep drawing alike — fabricating a difference to break the tie
    would be the same failure as fabricating a colour."""
    import re
    from pathlib import Path

    js = Path("app/static/viewer.js").read_text(encoding="utf-8")
    rug = re.search(r"role === 'rug'\) \{(.*?)\n  \} else \{", js, re.S).group(1)
    # Every weave the rug branch draws is gated on the stated material.
    for line in rug.splitlines():
        if "weave(" in line and not line.strip().startswith(("//", "*")):
            assert "s.weave" in line, f"a weave drawn without a stated material: {line}"


def test_the_viewport_is_optional_scenery():
    """If three.js fails, the plan, verdicts and reasoning must still work."""
    from pathlib import Path

    js = Path("app/static/studio.js").read_text(encoding="utf-8")
    assert ".catch(" in js and "viewport-fallback" in js
