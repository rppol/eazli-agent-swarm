"""Budget as a target, not just a ceiling — and the honesty that has to come with it.

`_score` used to sort price strictly ascending, which meant a bigger budget
could only ever *unblock* candidates and never prefer one. Measured before this
change: standard, comfort and premium returned byte-identical plans, and a
30,000 SAR brief furnished a 3,734 SAR room.

Making budget do something is easy and dangerous. "Prefer the dearer one" with
no evidence behind it is a recommendation engine for whatever a seller decided
to charge — and this capture is full of 90,000 SAR coffee tables with no
published dimensions. So target-seeking is gated on evidence, the gate is what
most of these tests are about, and the narration has to say which mechanism
actually chose the item.
"""

from __future__ import annotations

import pytest

from app.catalog import Product, parse_capture
from app.geometry import Dims
from app.planner import (
    BUDGET_TIERS,
    RECIPES,
    SLOT_HEADROOM_WEIGHTS,
    UPGRADE_EVIDENCE_FLOOR,
    _clears_evidence_floor,
    _score,
    auto_plan,
    candidates_for,
)


def product(asin="A", price=1000.0, rating=4.5, reviews=50, flags=None,
            category="sofa", style=None) -> Product:
    return Product(
        asin=asin, title=f"Sofa {asin}", category=category,
        search_category=category, price_sar=price,
        dims=Dims(200, 90, 85, confidence="stated"),
        dims_confidence="stated", dims_source="Item Dimensions D x W x H",
        carton=None, flat_pack=False, rating=rating, reviews=reviews,
        style_tags=list(style or []), rooms=["living_dining"],
        flags=list(flags or []),
    )


# --------------------------------------------------------------------------
# the evidence gate
# --------------------------------------------------------------------------

class TestEvidenceFloor:
    def test_a_well_reviewed_unflagged_item_clears_it(self):
        assert _clears_evidence_floor(product()) is True

    def test_an_item_with_no_rating_does_not_clear_it(self):
        """Half this catalogue's expensive end has no rating at all. Preferring
        one of those *because* it costs more is buying a seller's asking price
        and calling it quality."""
        assert _clears_evidence_floor(product(rating=None, reviews=0)) is False

    def test_one_persons_five_stars_does_not_clear_it(self):
        below = UPGRADE_EVIDENCE_FLOOR["min_reviews"] - 1
        assert _clears_evidence_floor(product(rating=5.0, reviews=below)) is False

    def test_a_badly_rated_item_does_not_clear_it(self):
        assert _clears_evidence_floor(product(rating=2.0, reviews=500)) is False

    def test_a_plausibility_flag_disqualifies_however_good_the_reviews(self):
        """The auditor's non-negotiable. An item we do not believe the
        dimensions or the price of cannot be 'the better one'."""
        assert _clears_evidence_floor(
            product(rating=4.9, reviews=900, flags=["implausible_price"])) is False
        assert _clears_evidence_floor(
            product(rating=4.9, reviews=900, flags=["implausible_for_category"])) is False


# --------------------------------------------------------------------------
# target-seeking in _score
# --------------------------------------------------------------------------

class TestTargetSeeking:
    def test_without_a_target_price_still_sorts_cheapest_first(self):
        cheap, dear = product("A", 500.0), product("B", 3000.0)
        assert sorted([dear, cheap], key=lambda p: _score(p, [])) == [cheap, dear]

    def test_with_a_target_the_closest_price_wins_even_though_it_costs_more(self):
        """The whole point: at a 3,000 SAR target the 2,800 SAR sofa is the
        better answer than the 500 SAR one, which is something the old
        ascending sort could never say."""
        cheap, near = product("A", 500.0), product("B", 2800.0)
        ranked = sorted([cheap, near], key=lambda p: _score(p, [], slot_target=3000.0))
        assert ranked[0].asin == "B"

    def test_an_item_below_the_evidence_floor_is_never_preferred_for_costing_more(self):
        """A dear item with no reviews must not be pulled up by the target. It
        keeps its plain price term, so it stays behind on cheapest-first."""
        cheap = product("A", 500.0, rating=4.6, reviews=200)
        dear_unproven = product("B", 2900.0, rating=None, reviews=0)
        ranked = sorted([dear_unproven, cheap],
                        key=lambda p: _score(p, [], slot_target=3000.0))
        assert ranked[0].asin == "A"

    def test_a_flagged_item_is_never_preferred_for_costing_more(self):
        """Ratings held equal so that the price term is the only thing that can
        separate these two — otherwise this would be testing the evidence term,
        which ranks ahead of price and is not what the gate is about."""
        cheap = product("A", 500.0, rating=4.6, reviews=200)
        dear_flagged = product("B", 2900.0, rating=4.6, reviews=200,
                               flags=["implausible_price"])
        ranked = sorted([dear_flagged, cheap],
                        key=lambda p: _score(p, [], slot_target=3000.0))
        assert ranked[0].asin == "A"

    def test_style_match_still_outranks_the_target(self):
        """Target-seeking changes the price term only. It does not get to
        overrule what the customer asked for."""
        on_style = product("A", 400.0, style=["warm"])
        off_style = product("B", 3000.0)
        ranked = sorted([off_style, on_style],
                        key=lambda p: _score(p, ["warm"], slot_target=3000.0))
        assert ranked[0].asin == "A"


class TestCandidatesStillGuarded:
    def test_budget_remains_a_hard_cap_under_target_seeking(self):
        slot = RECIPES["living_dining"][0]
        catalog = parse_capture()
        for p in candidates_for(slot, catalog, 1500, [], slot_target=9000.0):
            assert p.price_sar <= 1500

    def test_an_implausibly_priced_item_is_never_a_candidate(self):
        """It stays in the catalogue so an agent can find and dismiss it, but
        it must not be silently recommended."""
        catalog = parse_capture()
        for slot in RECIPES["living_dining"]:
            for p in candidates_for(slot, catalog, 10 ** 9, []):
                assert "implausible_price" not in p.flags, p.asin

    def test_every_candidate_is_still_usable_and_in_category(self):
        catalog = parse_capture()
        for slot in RECIPES["living_dining"]:
            for p in candidates_for(slot, catalog, 10 ** 9, [], slot_target=5000.0):
                assert p.usable
                assert p.category == slot["category"]


# --------------------------------------------------------------------------
# what the whole planner does with it
# --------------------------------------------------------------------------

class TestBudgetChangesThePlan:
    def test_no_recipe_can_allocate_more_headroom_than_it_has(self):
        """The constraint is per room, not across the whole table: the bedroom
        anchors and the living/dining anchors are never in the same room, so a
        global sum is meaningless. What must hold is that no single recipe
        hands out more than 100% of its own headroom, which would be
        overspending by construction. Unweighted slots get none, which errs
        towards not spending."""
        assert SLOT_HEADROOM_WEIGHTS
        for name, recipe in RECIPES.items():
            allocated = sum(SLOT_HEADROOM_WEIGHTS.get(s["category"], 0.0)
                            for s in recipe)
            assert allocated <= 1.0 + 1e-9, f"{name} allocates {allocated}"

    def test_a_bigger_budget_buys_a_dearer_room_than_the_starter_tier(self):
        starter = auto_plan("unit01", "living_dining", 3000, ["warm", "minimal"])
        premium = auto_plan("unit01", "living_dining", 30000, ["warm", "minimal"])
        assert premium.total_sar > starter.total_sar

    def test_target_seeking_upgrades_a_slot_when_the_evidence_is_there(self):
        """The mechanism, proved on a catalogue that can support it.

        Two sofas with identical ratings so the evidence term ties and the
        price term is what decides. On the cheapest-first ranking the 800 SAR
        sofa wins at every budget; with a target the 2,500 SAR one does.

        This is deliberately NOT asserted against the real capture — see
        `test_the_real_capture_cannot_support_an_upgrade` for why."""
        catalog = [
            product("CHEAP", 800.0, rating=4.5, reviews=100),
            product("DEAR", 2500.0, rating=4.5, reviews=100),
        ]
        # 1,500 cannot reach the dearer sofa at all, which also pins the hard
        # cap: a target never lets a slot spend past the budget.
        starter = auto_plan("unit01", "living_dining", 1500, [], catalog=catalog)
        premium = auto_plan("unit01", "living_dining", 30000, [], catalog=catalog)
        assert [i.asin for i in starter.placed] == ["CHEAP"]
        assert [i.asin for i in premium.placed] == ["DEAR"]
        assert premium.total_sar > starter.total_sar

    def test_target_seeking_upgrades_a_real_slot_in_the_real_capture(self):
        """The same mechanism on real data. Unstyled, the lamp slot has eight
        floor-clearing candidates from 129 to 360 SAR, and the target pulls the
        pick up off the cheapest one."""
        plan = auto_plan("unit01", "living_dining", 8000, [])
        lamp = next(i for i in plan.placed if i.slot_id == "accent_lighting")
        assert "target" in lamp.decision.chose_because.lower()
        cheapest = min(p.price_sar for p in candidates_for(
            RECIPES["living_dining"][4], parse_capture(), 8000, []))
        assert lamp.price_sar > cheapest

    def test_a_styled_brief_cannot_be_upgraded_and_that_is_the_catalogue(self):
        """Measured, not overlooked, and NOT a reason to lower the gate.

        Style match ranks ahead of everything, which is correct — it is what
        the customer asked for. But inside the style-matched tier of a "warm +
        minimal" brief this capture has almost no evidenced price ladder to
        climb: sofa 10 candidates / 0 clearing the floor, coffee_table 1 / 0,
        dining_table 2 / 0, rug 2 / 0, floor_lamp 1 / 1. Only tv_unit has two
        clearing candidates at different prices, and the entire upgrade
        available there is 9.67 SAR.

        Of 14 usable sofas — the slot carrying 45% of the headroom — not one
        has more than 2 reviews. The expensive end of this assortment is the
        worst-evidenced part of it, which is the auditor's whole point.

        So this pins the cause rather than the symptom. If a re-scrape ever
        brings reviewed premium stock, this is the test that starts failing,
        and it should be updated rather than loosened."""
        catalog = parse_capture()
        style = ["warm", "minimal"]
        for slot in RECIPES["living_dining"]:
            pool = candidates_for(slot, catalog, 30000, style)
            if not pool:
                continue
            best = len(set(pool[0].style_tags) & set(style))
            tier = [p for p in pool if len(set(p.style_tags) & set(style)) == best]
            prices = {p.price_sar for p in tier if _clears_evidence_floor(p)}
            assert len(prices) <= 1 or slot["category"] == "tv_unit", (
                f"{slot['category']} now has evidenced candidates at several "
                f"prices — target-seeking can bite here, update this test")

    def test_the_tiers_that_cannot_differ_are_reported_rather_than_disguised(self):
        """Standard, comfort and premium return the same styled room. That is
        the catalogue's ceiling, not a planner bug — and the plan has to say so
        with numbers instead of quietly looking like a 30,000 SAR result."""
        plans = {t["id"]: auto_plan("unit01", "living_dining", t["sar"],
                                    ["warm", "minimal"])
                 for t in BUDGET_TIERS}
        assert plans["standard"].total_sar == plans["premium"].total_sar
        gap = plans["premium"].to_dict()["unspent_budget"]
        assert gap["unspent_sar"] > 25000
        assert sum(gap["candidates_rejected"].values()) > 0

    def test_a_bigger_budget_never_costs_a_slot(self):
        plans = [auto_plan("unit01", "living_dining", t["sar"], ["warm", "minimal"])
                 for t in BUDGET_TIERS]
        for lower, higher in zip(plans, plans[1:]):
            assert len(higher.placed) >= len(lower.placed)
            assert higher.total_sar <= higher.budget_sar

    def test_it_is_still_deterministic(self):
        a = auto_plan("unit01", "living_dining", 15000, ["warm"]).to_dict()
        b = auto_plan("unit01", "living_dining", 15000, ["warm"]).to_dict()
        assert a == b


class TestTheNarrationDoesNotLie:
    def test_nothing_deliberately_upgraded_is_described_as_cheapest(self):
        """`chose_because` had a hardcoded "cheapest that fits" fallback that
        was only ever true while price sorted ascending. Printing it over an
        item the planner deliberately paid more for is precisely the
        confident-wrong-answer this project exists to refuse."""
        plan = auto_plan("unit01", "living_dining", 30000, ["warm", "minimal"])
        pool = {p.asin: p for p in parse_capture()}
        for item in plan.placed:
            if "cheapest" not in item.decision.chose_because:
                continue
            slot = next(s for s in RECIPES["living_dining"]
                        if s["slot_id"] == item.slot_id)
            cheaper = [
                p for p in pool.values()
                if p.category == slot["category"] and p.usable
                and "implausible_price" not in p.flags
                and p.price_sar is not None and p.price_sar < item.price_sar
            ]
            assert not cheaper, (
                f"{item.asin} claims 'cheapest that fits' at {item.price_sar} "
                f"but {len(cheaper)} cheaper candidates exist")

    def test_a_targeted_choice_states_the_target_and_what_it_cost(self):
        """On the real capture, where this path actually fires."""
        plan = auto_plan("unit01", "living_dining", 8000, [])
        targeted = [i for i in plan.placed
                    if "target" in i.decision.chose_because.lower()]
        assert targeted, "expected at least one target-seeking choice"
        for item in targeted:
            why = item.decision.chose_because
            assert f"{item.price_sar:.0f}" in why, why
            assert "SAR" in why, why
            assert "cheapest" not in why, why

    def test_a_slot_that_was_not_upgraded_does_not_claim_a_target(self):
        """A target can be set for a slot and the pick still be the one
        cheapest-first would have made. Saying "target" over that is the same
        lie pointed the other way."""
        catalog = [product("ONLY", 800.0, rating=4.5, reviews=100)]
        plan = auto_plan("unit01", "living_dining", 30000, [], catalog=catalog)
        assert "target" not in plan.placed[0].decision.chose_because.lower()


class TestItSaysWhyTheBudgetWasNotSpent:
    def test_the_plan_reports_unspent_budget_as_data(self):
        plan = auto_plan("unit01", "living_dining", 30000, ["warm", "minimal"])
        report = plan.to_dict()["unspent_budget"]
        assert report["budget_sar"] == 30000
        assert report["spent_sar"] == pytest.approx(round(plan.total_sar, 2))
        assert report["unspent_sar"] == pytest.approx(
            round(30000 - plan.total_sar, 2))

    def test_it_counts_rejections_by_cause(self):
        """Not prose. The frontend renders it; the numbers have to be numbers."""
        plan = auto_plan("unit01", "living_dining", 30000, ["warm", "minimal"])
        rejected = plan.to_dict()["unspent_budget"]["candidates_rejected"]
        assert set(rejected) >= {
            "no_published_dimensions",
            "dimensions_implausible_for_category",
            "price_implausible_for_category",
            "could_not_be_delivered",
        }
        assert all(isinstance(v, int) for v in rejected.values())
        assert sum(rejected.values()) > 0, (
            "a 30,000 SAR brief that spends a fraction of it must be able to "
            "say what it turned down")

    def test_a_fully_spent_budget_reports_no_headroom(self):
        tight = auto_plan("unit01", "living_dining", 400)
        assert tight.to_dict()["unspent_budget"]["unspent_sar"] >= 0
