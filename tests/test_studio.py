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
    would not be caught by any other test.

    This used to read `"cdn" not in html.lower()`, which stopped being the
    right question once the header started hotlinking the three eazli agent
    avatars from cdn.eazli.com. Those are decoration: `alt=""`, lazy, low
    priority, and backed by a CSS monogram, so losing them costs the page
    nothing. Code is the thing that may not come from someone else's host, so
    that is what is asserted — plus an allow-list, so the next off-host URL
    still has to be argued for rather than merely spelled without a `cdn.`
    """
    import re

    html = client.get("/studio").text

    # Anything the browser executes or applies: script sources, stylesheets,
    # and the module specifiers the import map resolves.
    code = re.findall(r'<script[^>]*\bsrc="([^"]+)"', html)
    code += re.findall(r'<link[^>]*\brel="stylesheet"[^>]*\bhref="([^"]+)"', html)
    code += re.findall(r':\s*"([^"]+\.js)"', html)
    assert code, "expected the page to load some code at all"
    for url in code:
        assert url.startswith("/static/"), f"code loaded from off-host: {url}"

    assert "unpkg" not in html
    remote = set(re.findall(r'(?:src|href)="(https?://[^"]+)"', html))
    assert remote == {
        f"https://cdn.eazli.com/agents/{who}_card_avatar.png"
        for who in ("zeina", "adam", "noura")
    }, f"unexpected off-host reference: {remote}"

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


# --------------------------------------------------------------------------
# the agent-reasoning log, and the brief behind each product
#
# These two features render prose, and prose is where a system that is careful
# everywhere else starts lying: a sentence template will happily fire "a
# considered choice for this space" over an empty field. So most of what is
# asserted here is what the copy may NOT do — and the strongest of them runs
# the builders in node over real exported plans and re-finds every measurement
# they print in the plan they printed it from.
# --------------------------------------------------------------------------

def _studio_js() -> str:
    from pathlib import Path

    return Path("app/static/studio.js").read_text(encoding="utf-8")


def _function_source(js: str, name: str) -> str:
    """The body of one top-level `function name(...) { ... }`, by brace count.

    Crude, and deliberately so: it lets a test say "Zeina's turn never mentions
    a price" about that function and nothing else."""
    start = js.index(f"function {name}(")
    depth, i = 0, js.index("{", start)
    for j in range(i, len(js)):
        depth += (js[j] == "{") - (js[j] == "}")
        if depth == 0:
            return js[start:j + 1]
    raise AssertionError(f"{name} is not brace-balanced")


def test_the_log_attributes_every_turn_to_the_agent_that_owns_the_decision():
    """Four turns, four agents, and the three eazli personas wear the avatars
    eazli publishes for them. An unattributed wall of reasoning is just the
    same data dump with a nicer font."""
    js = _studio_js()
    for key, avatar, role in (
        ("zeina", "zeina_card_avatar.png", "AI Guide"),
        ("noura", "noura_card_avatar.png", "AI Design Agent"),
        ("adam", "adam_card_avatar.png", "AI Sales Advisor"),
    ):
        assert f"'{avatar}'" in js or f'"{avatar}"' in js, f"{key} has no avatar"
        assert role in js, f"{key}'s role is not stated"
    assert "https://cdn.eazli.com/agents/" in js
    # fit-auditor is this project's addition, not an eazli persona, so there is
    # no card avatar to hotlink and it must not pretend there is one.
    assert "fit-auditor" in js
    assert "fit_auditor_card_avatar" not in js and "auditor_card_avatar" not in js


def test_a_cdn_outage_shows_a_monogram_rather_than_a_broken_avatar():
    """The avatars are hotlinked off a CDN this build does not control."""
    js = _studio_js()
    assert 'loading="lazy"' in js, "avatars must not compete with first paint"
    assert "onerror=" in js, "a 404 would otherwise render as a broken image"
    # The replacement is a data: URI, so the fallback cannot fail the same way.
    assert "this.onerror=null" in js, "a failing fallback would loop"
    assert "data:image/svg+xml" in js


def test_the_log_uses_the_class_names_the_stylesheet_defines():
    """The stylesheet is written by another pair of hands. Inventing a parallel
    set of names here produces an unstyled wall of text."""
    import re

    js = _studio_js()
    for cls in ("agent-log", "agent-turn", "agent-head", "agent-name",
                "agent-role", "agent-says", "agent-gotcha"):
        assert cls in js, f"the log never uses .{cls}"
    # The per-agent modifier is the AGENTS key, so a turn can only be styled
    # per agent if those keys are the four the stylesheet expects.
    keys = re.search(r"const AGENTS = \{(.*?)\n\};", js, re.S).group(1)
    for modifier in ("zeina", "noura", "adam", "auditor"):
        assert re.search(rf"^\s*{modifier}:", keys, re.M), f".agent-turn.{modifier} is unreachable"


def test_zeina_never_names_a_product():
    """eazli's own copy: she "doesn't give you a catalog or a quote; she gives
    you a map". An orientation agent that starts recommending sofas is the
    scope violation the whole persona split exists to prevent."""
    src = _function_source(_studio_js(), "zeinaTurn")
    for forbidden in ("title", "asin", "price_sar", "chose_because",
                      "ranked_above", "decision", "dims_cm"):
        assert forbidden not in src, f"Zeina reaches into {forbidden}"


def test_the_auditor_reads_from_the_rejection_record_and_nothing_else():
    """Its whole value is that every gotcha is traceable to a field. A hand-
    written "watch out for tight corridors" would be indistinguishable on
    screen and worthless."""
    src = _function_source(_studio_js(), "auditorTurn")
    for field in ("rejected", "stage", "why", "unfilled",
                  "validation.reasons", "candidates_rejected"):
        assert field in src, f"the auditor never looks at {field}"
    # And when the plan holds none of it, it says so instead of inventing.
    assert "Nothing to object to" in src
    assert "agent-gotcha" in _studio_js()


def test_a_gotcha_puts_its_measurement_in_code():
    """A number buried in a sentence is prose; a number in `<code>` is a
    measurement the reader can go and check against their own tape."""
    import re

    js = _studio_js()
    src = re.search(r"const measure = .*?\n\n", js, re.S).group(0)
    assert "<code>" in src
    assert "cm" in src and "SAR" in src
    # Escaped before markup is inserted, never after.
    assert "esc(" in src


def test_every_flag_the_catalogue_can_actually_emit_is_explained_in_words():
    """`assumed_depth` means nothing to a shopper. The glossary is derived from
    the capture rather than from memory, so a new parser flag fails here rather
    than reaching a customer as a raw schema token."""
    from app.catalog import parse_capture

    js = _studio_js()
    emitted = {f for p in parse_capture() for f in p.flags}
    # Sanity: the six the brief promises to explain are among them.
    assert {"no_published_dimensions", "implausible_price", "implausible_for_category",
            "colour_conflict", "assumed_depth", "category_mismatch"} <= emitted
    for flag in sorted(emitted):
        assert f"{flag}:" in js, f"{flag} would reach the reader as a schema token"


def test_the_brief_reuses_the_picker_shell_rather_than_opening_a_second_modal():
    """Two modals is two sets of close handlers, two escape keys and two
    stacking contexts. The swap flow keeps working because it is the same
    element."""
    js = _studio_js()
    html = __import__("pathlib").Path("app/static/index.html").read_text(encoding="utf-8")
    src = _function_source(js, "openBrief")
    assert "#picker-list" in src and "#picker-title" in src
    assert "$('#picker').hidden = false" in src
    # No second overlay was introduced in the markup.
    assert html.count('id="picker"') == 1
    assert "#brief" not in js, "the brief must not get its own overlay"
    # The swap flow still fills the same shell.
    assert "#picker-list" in _function_source(js, "openPicker")


def test_opening_a_brief_does_not_steal_the_row_buttons():
    """The row already carries "Why this?", "Swap…" and the hover-highlight for
    the 3D view. A click handler on the row swallows all three unless it stands
    aside for them."""
    js = _studio_js()
    assert ".actions" in js and "closest(" in js, "the row click must bail on its own buttons"
    row = js[js.index("querySelectorAll('.item[data-slot]')"):]
    row = row[:row.index("\n  }")] if "\n  }" in row else row
    assert "onmouseenter" in row and "highlight(" in row, "hover-highlight was dropped"
    assert "openBrief(" in row


def test_the_brief_says_not_published_in_words():
    """31 usable listings publish no colour and the render draws them as
    unknown on purpose. A brief that quietly omitted the row would let the
    reader assume the render was being decorative."""
    src = _function_source(_studio_js(), "briefHtml")
    assert "not published" in src
    for expected in ("asin", "price_sar", "i.url", "dims_confidence",
                     "colour_source", "material_source", "flags", "rating",
                     "access", "chose_because", "considered", "ranked_above"):
        assert expected in src, f"the brief never shows {expected}"
    assert 'target="_blank"' in src, "the listing link must not lose the studio"


# ---- the same builders, run over real plans -------------------------------
#
# The copy is a pure function of the plan on purpose. Anything else can only be
# tested by reading it, and reading it is exactly how a fabricated sentence
# survives review.

DRIVER = """
import { readFileSync } from 'node:fs';
// One proxy stands in for the whole DOM: studio.js only touches it from
// inside functions this harness never calls.
const stub = new Proxy(function () {}, {
  get: (_t, k) => (k === 'then' ? undefined : stub),
  set: () => true,
  apply: () => stub,
});
globalThis.document = stub;
globalThis.addEventListener = () => {};
const { agentLogHtml, briefHtml, flatten } = await import('./studio.mjs');
// `flatten` is what render() runs before anything downstream sees the plan,
// and it is a no-op on a single room. Going through it here means a whole-flat
// plan reaches the builders in the shape the page actually hands them, rather
// than a second flattening written beside it that can drift.
const plan = flatten(JSON.parse(readFileSync(process.argv[2], 'utf8')));
const ctx = JSON.parse(process.argv[3]);
process.stdout.write(JSON.stringify({
  log: agentLogHtml(plan, ctx),
  briefs: plan.placed.map((i) => briefHtml(i, plan)),
}));
"""


@pytest.fixture(scope="module")
def run_studio(tmp_path_factory):
    """Load studio.js in node with a stubbed DOM and hand back its builders."""
    import json
    import shutil
    import subprocess
    from pathlib import Path

    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")

    src = Path("app/static/studio.js").read_text(encoding="utf-8")
    assert "\nboot();" in src, "boot() moved — this harness needs updating"
    src = src.replace("\nboot();", "\n/* boot() is not called under test */")
    src = src.replace("'./palette.js'",
                      f"'{Path('app/static/palette.js').resolve().as_uri()}'")
    d = tmp_path_factory.mktemp("studio")
    (d / "studio.mjs").write_text(src, encoding="utf-8")
    (d / "run.mjs").write_text(DRIVER, encoding="utf-8")

    def run(plan_path, style=(), tier=None):
        out = subprocess.run(
            [node, str(d / "run.mjs"), str(plan_path),
             json.dumps({"style": list(style), "tier": tier})],
            capture_output=True, text=True)
        assert out.returncode == 0, out.stderr
        return json.loads(out.stdout)

    return run


def _exported_plans(*names):
    from pathlib import Path

    paths = [Path("site/data/plans") / n for n in names]
    if not all(p.exists() for p in paths):
        pytest.skip("no static build in site/ — run `make site`")
    return paths


def test_the_log_prints_no_measurement_the_plan_does_not_contain(run_studio):
    """The point of the whole feature. Every measurement the auditor puts in
    front of a reader has to be findable, verbatim, in the JSON it came from."""
    import re

    for path in _exported_plans(
        "unit01__living_dining__warm-minimal__standard.json",
        "unit01__bedroom__modern-luxury__premium.json",
        "unit05__living_dining__warm-minimal__starter.json",
    ):
        raw = path.read_text(encoding="utf-8")
        log = run_studio(path)["log"]
        for gotcha in re.findall(r'<div class="agent-gotcha">(.*?)</div>', log, re.S):
            for code in re.findall(r"<code>(.*?)</code>", gotcha):
                assert code in raw, f"{code!r} is not in {path.name}"
        # Everywhere else in the log, a number inside <code> is still a number
        # the plan states.
        for code in re.findall(r"<code>(.*?)</code>", log):
            for number in re.findall(r"\d+(?:\.\d+)?", code):
                assert number in raw, f"{number!r} was not read off {path.name}"


def test_an_auditor_with_nothing_to_object_to_says_so(run_studio):
    """The failure mode is an auditor that always finds something, because an
    auditor that always finds something is not reading the plan."""
    import json
    from pathlib import Path

    quiet = next(
        (p for p in sorted(Path("site/data/plans").glob("*.json"))
         if (d := json.loads(p.read_text()))
         and not d["unfilled"] and not d["validation"]["reasons"]
         and not any(i["decision"]["rejected"] for i in d["placed"])),
        None)
    if quiet is None:
        pytest.skip("no static build, or every exported plan has a gotcha")

    log = run_studio(quiet)["log"]
    assert "Nothing to object to" in log
    assert 'class="agent-gotcha"' not in log, f"{quiet.name} has nothing to flag"
    # It still reports the tally, which is a fact about the catalogue rather
    # than a complaint about this plan.
    assert "in scope" in log


def test_the_log_differs_between_configurations(run_studio):
    """Four near-identical paragraphs across every room would say the copy is
    boilerplate with the numbers swapped in."""
    a, b = _exported_plans("unit01__living_dining__warm-minimal__standard.json",
                           "unit01__bedroom__modern-luxury__premium.json")
    assert run_studio(a)["log"] != run_studio(b)["log"]


# ---- each agent leads with what was distinctive about THIS plan ------------
#
# `test_the_log_differs_between_configurations` above is satisfied by a
# template with the numbers substituted, and for a long time that is exactly
# what it was passing: Zeina opened "You asked for X, styled Y, on the Z tier"
# and Noura opened "The room is NxN cm with N doors on the plan" for all 260
# exported configurations. Whole paragraphs differed while every reader saw the
# same four sentences first.
#
# What follows asserts the fix rather than the symptom: the OPENING of each
# turn has to be chosen from the plan, so that plans which differ in a way that
# agent is responsible for open differently.

_TIER = {t["id"]: t for t in BUDGET_TIERS}


def _tier_ctx(name):
    """The ctx the page builds for a tier, read from Python's list, not typed
    out here — the same rule studio.js is held to."""
    return _TIER[name]


def _openings(run_studio, path, style=(), tier=None):
    """Each agent's first sentence, as the reader meets it."""
    import html
    import re

    log = run_studio(path, style=style, tier=tier)["log"]
    out = {}
    for who, says in re.findall(
            r'<div class="agent-turn (\w+)">.*?<div class="agent-says">(.*?)</div>',
            log, re.S):
        text = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", says))).strip()
        out[who] = re.split(r"(?<=[.!?]) ", text)[0]
    assert set(out) == {"zeina", "noura", "adam", "auditor"}, out.keys()
    return out


# unit, room, style, tier — varied on every axis the studio offers, and chosen
# to include the two shapes that must not read alike (a 335x551 corridor and a
# 610x335 hall), a starter tier that drops slots, and a whole flat.
#
# `unit01__living_dining__warm-minimal__standard.json` was added, and
# `unit04__living_dining__industrial-mid_century__comfort.json` swapped for
# `unit04__living_dining__warm-minimal__comfort.json`, once `validate_layout`
# started actually returning the `notes` it already collected (it built the
# list and threw it away before this fix -- see app/geometry.py). Noura's
# opening leads with an advisory over the room-shape description on purpose
# ("An advisory is about the room, so it outranks the room's proportions" —
# app/static/studio.js), so the two configurations the corridor/hall
# comparison depends on have to be ones where the plan genuinely raised none,
# or the test is asserting a sentence neither plan says. The original
# `unit01__living_dining__modern-luxury__premium.json` now carries two (a
# bookshelf and a dining table each pinned at their reach minimum by a
# neighbour) and stays in the spread for the auditor/adam assertions that
# don't care what Noura opens on.
_SPREAD = [
    ("plans", "unit01__living_dining__warm-minimal__starter.json",
     ["warm", "minimal"], "starter"),
    ("plans", "unit01__living_dining__modern-luxury__premium.json",
     ["modern", "luxury"], "premium"),
    ("plans", "unit01__bedroom__boho-scandi__standard.json",
     ["boho", "scandi"], "standard"),
    ("plans", "unit04__living_dining__warm-minimal__comfort.json",
     ["warm", "minimal"], "comfort"),
    ("plans", "unit04__master_bedroom_1__any__starter.json", [], "starter"),
    ("plans", "unit05__master_bedroom__warm-minimal__premium.json",
     ["warm", "minimal"], "premium"),
    ("flats", "unit04__flat__warm-minimal__starter.json",
     ["warm", "minimal"], "starter"),
    ("flats", "unit01__flat__modern-luxury__premium.json",
     ["modern", "luxury"], "premium"),
    ("plans", "unit01__living_dining__warm-minimal__standard.json",
     ["warm", "minimal"], "standard"),
]


@pytest.fixture(scope="module")
def spread(run_studio):
    from pathlib import Path

    paths = [Path("site/data") / d / n for d, n, _, _ in _SPREAD]
    if not all(p.exists() for p in paths):
        pytest.skip("no static build in site/ — run `make site`")
    return {
        n: _openings(run_studio, p, style, _tier_ctx(tier))
        for p, (_, n, style, tier) in zip(paths, _SPREAD)
    }


@pytest.mark.parametrize("who", ["zeina", "noura", "adam", "auditor"])
def test_each_agent_opens_on_something_this_configuration_did(spread, who):
    """Nine configurations, at most one repeated first sentence per agent.

    The bar is not "the paragraphs differ somewhere" — it is that the sentence
    a reader's eye lands on first was chosen from this plan's own data. One
    exact repeat is tolerated for two configurations that genuinely produced
    the same notable fact for one agent (two different bedrooms whose only
    advisory is the same rug under the same N-wall door swing say the same
    sentence, and inventing a difference would be the failure this whole file
    argues against). It does not allow for a template.
    """
    firsts = [cfg[who] for cfg in spread.values()]
    assert len(set(firsts)) >= len(firsts) - 1, (
        f"{who} reuses an opening across configurations:\n"
        + "\n".join(sorted(set(firsts))))


def test_a_brief_that_could_not_be_met_is_the_first_thing_zeina_says(spread):
    """Six of 200 exported room plans drop a slot. On those, "you did not get
    what you asked for" outranks every other framing — including on the flat,
    where nine of eighteen slots came back empty."""
    for name in ("unit01__living_dining__warm-minimal__starter.json",
                 "unit04__flat__warm-minimal__starter.json"):
        assert "came back empty" in spread[name]["zeina"], spread[name]["zeina"]
    # And a plan that dropped nothing must not borrow the line.
    assert "came back empty" not in spread[
        "unit01__living_dining__modern-luxury__premium.json"]["zeina"]


def test_noura_does_not_describe_a_corridor_and_a_hall_the_same_way(spread):
    """335x551 and 610x335 are both "the living/dining room" and are not
    remotely the same problem to lay out. The old copy printed both as "The
    room is NxN cm with 1 door on the plan".

    Both configurations here are chosen with zero `validation` advisories, so
    the room-shape sentence is actually what leads -- an advisory outranks it
    (see the note above `_SPREAD`), and a config carrying one would test
    nothing about how corridors and halls are described.
    """
    corridor = spread["unit01__living_dining__warm-minimal__standard.json"]["noura"]
    hall = spread["unit04__living_dining__warm-minimal__comfort.json"]["noura"]
    assert corridor != hall
    assert "551" in corridor and "610" in hall
    # Not merely different numbers in one sentence: a different reading of the
    # room, which is the thing a designer would actually have said.
    assert "corridor" in corridor and "hall" in hall


def test_the_auditor_opens_on_what_it_found_not_on_a_count(spread):
    """A delivery refusal, an empty slot and a clean plan are three different
    findings. The opening used to be "N things in this plan are worth seeing"
    for all three."""
    empty = spread["unit04__flat__warm-minimal__starter.json"]["auditor"]
    clean = spread["unit01__living_dining__modern-luxury__premium.json"]["auditor"]
    assert "empty" in empty
    assert "empty" not in clean


def test_adam_leads_on_the_slot_whose_decision_was_the_odd_one(spread):
    """Sourcing in recipe order put the sofa first in every living room ever
    planned. The lead is now the slot where the recorded reason was something
    other than a routine style match."""
    lead = spread["unit01__bedroom__boho-scandi__standard.json"]["adam"]
    # `chose_because` on that slot records a per-slot budget target, which is a
    # genuinely different kind of decision from "matched boho".
    assert "budget target" in lead
    # The premium living room has no such slot, so it falls back to the biggest
    # single line — a different sentence, not the same one with other numbers.
    other = spread["unit01__living_dining__modern-luxury__premium.json"]["adam"]
    assert "budget target" not in other and "biggest single line" in other


def test_the_opening_is_reproducible(run_studio):
    """No randomness, no clock, no iteration order: the same plan must produce
    the same words every time or the page cannot be reviewed."""
    path, = _exported_plans("unit01__living_dining__warm-minimal__standard.json")
    tier = _tier_ctx("standard")
    once = run_studio(path, style=["warm", "minimal"], tier=tier)["log"]
    twice = run_studio(path, style=["warm", "minimal"], tier=tier)["log"]
    assert once == twice


def test_every_exported_plan_renders_a_log_with_no_holes(run_studio):
    """`undefined`, `NaN` and `[object Object]` are what a builder that reached
    for a field the plan does not carry leaves on the page. Checked across a
    slice of the build rather than one file, because the fields that go missing
    are the ones only some configurations have.
    """
    import json
    from pathlib import Path

    plans = sorted(Path("site/data/plans").glob("*.json"))
    flats = sorted(Path("site/data/flats").glob("*.json"))
    if not plans or not flats:
        pytest.skip("no static build in site/ — run `make site`")

    # Every twelfth room plan plus every twelfth flat: a spread across units,
    # rooms, styles and tiers without paying for 260 node starts.
    for path in plans[::12] + flats[::12]:
        _, _, style, tier = path.stem.split("__")
        log = run_studio(path,
                         style=[] if style == "any" else style.split("-"),
                         tier=_tier_ctx(tier))["log"]
        for hole in ("undefined", "NaN", "[object Object]"):
            assert hole not in log, f"{path.name} rendered {hole}"
        # And the invariant that makes all of it checkable still holds here.
        raw = path.read_text(encoding="utf-8")
        for code in __import__("re").findall(r"<code>(.*?)</code>", log):
            for number in __import__("re").findall(r"\d+(?:\.\d+)?", code):
                assert number in raw, f"{number!r} was not read off {path.name}"
        assert json.loads(raw)  # the fixture really did read a plan


def test_the_brief_agrees_with_the_render_about_an_unpublished_colour(run_studio):
    """The render draws these in hatched slate. A brief that named a colour
    would make one of the two a liar."""
    import json

    path, = _exported_plans("unit01__living_dining__warm-minimal__standard.json")
    plan = json.loads(path.read_text())
    briefs = run_studio(path)["briefs"]
    checked = 0
    for item, brief in zip(plan["placed"], briefs):
        assert item["asin"] in brief
        assert item["url"] in brief, "no link to the listing"
        assert item["dims_confidence"] in brief
        if item["colour_hex"] is None:
            assert "not published" in brief
            checked += 1
        else:
            assert item["colour_hex"] in brief
    assert checked, "this plan was chosen because something in it has no colour"
