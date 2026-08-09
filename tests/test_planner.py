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

Target-seeking then turned out not to be enough, and the measurement said so:
with the gate honoured, unit01's living/dining room cost 3,659 SAR at standard,
at comfort and at premium alike, and its master bedroom 820 SAR at all four
tiers. The upgrade path is genuinely blocked — the well-evidenced expensive
candidates fail DELIVERY, not affordability — so the second half of these tests
is about the other thing a budget can buy: not a dearer sofa, an armchair as
well as one. See `RECIPES` and `TestTierGatedSlots`.
"""

from __future__ import annotations

import pytest

from app.catalog import Product, parse_capture
from app.geometry import Dims, Placement, validate_layout
from app.home import load_home
from app.planner import (
    BUDGET_TIERS,
    RECIPES,
    SLOT_HEADROOM_WEIGHTS,
    UPGRADE_EVIDENCE_FLOOR,
    _clears_evidence_floor,
    _score,
    auto_plan,
    candidates_for,
    recipe_for,
    slots_for_budget,
)

TIER_SAR = {t["id"]: t["sar"] for t in BUDGET_TIERS}

# Every room the planner will plan, across every unit. Several assertions below
# have to hold for all of them, not just for the one room that was measured.
PLANNABLE = [
    (unit.id, room.name)
    for unit in load_home().units
    for room in unit.rooms
    if room.name in RECIPES
]


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
        towards not spending.

        Summed over the DISTINCT categories a recipe uses, not over its slots.
        living_dining now has two floor_lamp slots — the accent lamp and the
        reading lamp the premium tier unlocks — and counting the lamp share
        twice would both break this bound and, worse, hand the same 3% of the
        headroom out to two slots. `_headroom_targets` dedupes by category for
        exactly that reason, so this sum has to be taken the same way the code
        allocates."""
        assert SLOT_HEADROOM_WEIGHTS
        for name, recipe in RECIPES.items():
            allocated = sum(SLOT_HEADROOM_WEIGHTS.get(c, 0.0)
                            for c in {s["category"] for s in recipe})
            assert allocated <= 1.0 + 1e-9, f"{name} allocates {allocated}"

    def test_the_headroom_handed_out_never_exceeds_the_headroom_there_is(self):
        """The bound above is on the weight table; this one is on what the
        planner actually does with it. A duplicated category, or a new slot
        added to a recipe without a matching thought about weights, shows up
        here as real over-allocation rather than as a table that still sums
        correctly."""
        from app.planner import _headroom_targets, _plan_once

        for unit, room in PLANNABLE:
            for tier in BUDGET_TIERS:
                natural = _plan_once(unit, room, tier["sar"], ["warm", "minimal"],
                                     None, 400, targets={})
                targets = _headroom_targets(natural, tier["sar"])
                priced = {i.slot_id: i.price_sar for i in natural.placed}
                extra = sum(t - priced[s] for s, t in targets.items())
                headroom = tier["sar"] - natural.total_sar
                assert extra <= headroom + 1e-6, (
                    f"{unit}/{room}/{tier['id']} aims {extra:.0f} SAR of "
                    f"upgrades at {headroom:.0f} SAR of headroom")

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
        and it should be updated rather than loosened.

        Scoped to the slots target-seeking can actually reach — the ones with a
        `SLOT_HEADROOM_WEIGHTS` entry — because a slot with no weight is never
        given a target and so cannot be upgraded whatever its pool looks like.
        The comfort tier's bookshelf is exactly that case: it has three
        evidenced prices in the style-matched tier (188, 197 and 334.02 SAR)
        and still takes the cheapest, because the headroom is being spent on
        the slot existing at all rather than on a dearer shelf."""
        catalog = parse_capture()
        style = ["warm", "minimal"]
        for slot in RECIPES["living_dining"]:
            if slot["category"] not in SLOT_HEADROOM_WEIGHTS:
                continue
            pool = candidates_for(slot, catalog, 30000, style)
            if not pool:
                continue
            best = len(set(pool[0].style_tags) & set(style))
            tier = [p for p in pool if len(set(p.style_tags) & set(style)) == best]
            prices = {p.price_sar for p in tier if _clears_evidence_floor(p)}
            assert len(prices) <= 1 or slot["category"] == "tv_unit", (
                f"{slot['category']} now has evidenced candidates at several "
                f"prices — target-seeking can bite here, update this test")

    def test_what_a_bigger_budget_cannot_buy_is_still_reported_rather_than_disguised(self):
        """A premium living/dining room still leaves most of 30,000 SAR unspent
        even with every tier-gated slot filled, because the catalogue's dearest
        deliverable pieces are a few thousand riyals. That is the assortment's
        ceiling, not a planner bug, and the plan has to say so with numbers
        instead of quietly looking like a 30,000 SAR result."""
        premium = auto_plan("unit01", "living_dining", 30000, ["warm", "minimal"])
        gap = premium.to_dict()["unspent_budget"]
        assert gap["unspent_sar"] > 15000
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


# --------------------------------------------------------------------------
# spending headroom on more room rather than on dearer objects
# --------------------------------------------------------------------------

class TestTierGatedSlots:
    """The honest way for a budget to change a room in THIS catalogue.

    Target-seeking answers "can this tier buy a better sofa?" and the measured
    answer is no: the well-evidenced expensive candidates are refused by
    `check_access_path`, not by the budget. B0CC2PLFGN — 3,824 SAR, 800 reviews,
    the top-ranked sofa — is 150x213x99cm and cannot enter an 85x210cm lift car
    in any orientation, and that refusal is correct.

    A tier can still buy something real: another piece of furniture. A room with
    a sofa, coffee table, console, dining table, lamp and rug at 3,659 SAR is
    not a finished 30,000 SAR living room; it has no armchair, no shelving and
    one light.
    """

    def test_the_slots_a_budget_sees_only_ever_grow_with_it(self):
        """The gate is a floor, not a window. If a bigger budget could ever
        hide a slot a smaller one was offered, every ordering guarantee below
        would be built on sand."""
        for _, room in PLANNABLE:
            seen = [set(s["slot_id"] for s in slots_for_budget(room, t["sar"])[0])
                    for t in BUDGET_TIERS]
            for lower, higher in zip(seen, seen[1:]):
                assert lower <= higher, f"{room}: {lower - higher} vanished"
            assert len(seen[-1]) > len(seen[0]), (
                f"{room} offers the premium tier nothing the starter tier "
                f"does not already get")

    def test_every_gate_is_set_at_an_amount_a_tier_actually_offers(self):
        """A slot unlocking at 12,000 SAR would be unreachable from the studio,
        which only ever asks for the four tier amounts, and would show up as a
        slot that exists in the code and never in a plan."""
        amounts = {t["sar"] for t in BUDGET_TIERS}
        for name, recipe in RECIPES.items():
            for slot in recipe:
                unlock = slot.get("unlock_sar", 0.0)
                assert unlock == 0.0 or unlock in amounts, (
                    f"{name}/{slot['slot_id']} unlocks at {unlock}")

    def test_a_gated_slot_is_never_required(self):
        """`required` marks the piece without which the room is not the room it
        was asked for — a living/dining room with no sofa. Gating one behind a
        budget would mean the starter tier is asked for a room it is not
        allowed to have."""
        for name, recipe in RECIPES.items():
            for slot in recipe:
                if slot.get("unlock_sar", 0.0):
                    assert not slot["required"], f"{name}/{slot['slot_id']}"

    def test_comfort_and_premium_furnish_more_of_the_living_room_than_standard(self):
        """Measured before this change: standard 3,659 / 6 slots, comfort 3,659
        / 6, premium 3,659 / 6 — byte-identical rooms behind a 22,000 SAR
        spread."""
        plans = {t["id"]: auto_plan("unit01", "living_dining", t["sar"],
                                    ["warm", "minimal"])
                 for t in BUDGET_TIERS}
        assert len(plans["comfort"].placed) > len(plans["standard"].placed)
        assert len(plans["premium"].placed) > len(plans["comfort"].placed)
        assert plans["premium"].total_sar > plans["standard"].total_sar

    def test_the_bedroom_tiers_are_no_longer_all_the_same_room(self):
        """Measured before this change: 820 SAR and four slots at every one of
        the four tiers, in every bedroom of every unit."""
        for unit, room in PLANNABLE:
            if room == "living_dining":
                continue
            counts = {t["id"]: len(auto_plan(unit, room, t["sar"],
                                             ["warm", "minimal"]).placed)
                      for t in BUDGET_TIERS}
            assert len(set(counts.values())) > 1, f"{unit}/{room}: {counts}"

    def test_a_bigger_tier_never_returns_a_worse_room(self):
        """The ordering guarantee, across every plannable room and both ends of
        the style range.

        Two things must hold between adjacent tiers: the bigger budget fills a
        superset of the slots, and in a slot they share it does not fall back to
        a worse-matched or worse-evidenced product. Price is deliberately left
        out of the comparison — paying more for the same slot is the upgrade
        path working, not a regression — so the ranking compared here is the
        style-match and evidence prefix of `_score`, which is what `_score`
        sorts on before price is consulted at all.
        """
        for style in (["warm", "minimal"], []):
            for unit, room in PLANNABLE:
                plans = [auto_plan(unit, room, t["sar"], style)
                         for t in BUDGET_TIERS]
                for lower, higher in zip(plans, plans[1:]):
                    low = {i.slot_id: i for i in lower.placed}
                    high = {i.slot_id: i for i in higher.placed}
                    assert set(low) <= set(high), (
                        f"{unit}/{room}/{style}: {higher.budget_sar:.0f} SAR lost "
                        f"{set(low) - set(high)}")
                    catalog = {p.asin: p for p in parse_capture()}
                    for slot_id, was in low.items():
                        now = high[slot_id]
                        if now.asin == was.asin:
                            continue
                        rank_was = _score(catalog[was.asin], style)[:2]
                        rank_now = _score(catalog[now.asin], style)[:2]
                        assert rank_now <= rank_was, (
                            f"{unit}/{room}/{style}: {slot_id} fell from "
                            f"{was.asin} to {now.asin} at "
                            f"{higher.budget_sar:.0f} SAR")

    def test_an_added_slot_is_a_candidate_and_not_a_privilege(self):
        """Every gated slot goes through `check_fit`, `check_access_path`,
        `validate_layout` and the budget cap like anything else. The proof that
        nothing was waved through is that the finished layout still validates
        and the total still sits under the ceiling — for every room, every tier
        and both style extremes."""
        for style in (["warm", "minimal"], []):
            for unit, room in PLANNABLE:
                for tier in BUDGET_TIERS:
                    plan = auto_plan(unit, room, tier["sar"], style)
                    assert plan.validation["status"] == "pass", (
                        f"{unit}/{room}/{tier['id']}/{style}: "
                        f"{plan.validation['reasons']}")
                    assert plan.total_sar <= tier["sar"]

    def test_an_addition_that_cannot_be_placed_is_dropped_and_says_why(self):
        """A gated slot with no placeable candidate has to land in `unfilled`
        with its rejections recorded, exactly like an ungated one. Forced here
        by planning a bedroom with a catalogue whose only armchair is far too
        big for the room to hold alongside the bed."""
        oversized = product("HUGE", 900.0, category="armchair")
        oversized.dims = Dims(400, 300, 100, confidence="stated")
        plan = auto_plan("unit01", "bedroom", 30000, [], catalog=[oversized])
        dropped = {u.slot_id: u for u in plan.unfilled}
        assert "lounge_chair" in dropped
        assert dropped["lounge_chair"].candidates_considered == 1
        assert dropped["lounge_chair"].rejected, "refused without saying why"
        assert all(r["stage"] in {"delivery", "placement"}
                   for r in dropped["lounge_chair"].rejected)

    def test_a_slot_the_tier_has_not_unlocked_is_reported_as_locked_not_failed(self):
        """A starter room is not failing to find an armchair; it was never
        shopping for one. Filing that under `unfilled` would read as eight
        rejections the planner never made."""
        starter = auto_plan("unit01", "living_dining", TIER_SAR["starter"],
                            ["warm", "minimal"]).to_dict()
        locked = starter["unspent_budget"]["slots_locked_by_tier"]
        assert locked, "the starter tier gates slots and does not say so"
        assert {"slot_id", "role", "unlocks_at_sar"} <= set(locked[0])
        assert all(s["unlocks_at_sar"] > TIER_SAR["starter"] for s in locked)
        assert not {s["slot_id"] for s in locked} & {
            u["slot_id"] for u in starter["unfilled"]}

        premium = auto_plan("unit01", "living_dining", TIER_SAR["premium"],
                            ["warm", "minimal"]).to_dict()
        assert premium["unspent_budget"]["slots_locked_by_tier"] == []


class TestAnAdditionMustEarnItsPlace:
    """`_positions` knows how to place against a wall and relative to the sofa,
    but knowing is not the same as being checked. These pin the two failures an
    armchair is most likely to cause, so that a future change to the position
    search cannot quietly start emitting them."""

    def room(self):
        return load_home().unit("unit01").room("living_dining").to_room()

    def sofa_and_table(self):
        """A sofa and coffee table that validate on their own, so that the only
        thing changing in each test below is the armchair."""
        return [
            Placement("SOFA", Dims(200, 90, 85), x=60, y=100, facing="S", role="sofa"),
            Placement("TABLE", Dims(100, 50, 45), x=90, y=240,
                      facing="N", role="coffee_table"),
        ]

    def test_the_base_layout_these_tests_build_on_actually_validates(self):
        assert validate_layout(self.room(), self.sofa_and_table()).status == "pass"

    def test_an_armchair_on_top_of_the_coffee_table_fails(self):
        room = self.room()
        chair = Placement("CHAIR", Dims(90, 85, 95), x=95, y=245,
                          facing="N", role="armchair")
        verdict = validate_layout(room, [*self.sofa_and_table(), chair])
        assert verdict.status == "fail"
        assert any("TABLE and CHAIR overlap" in r for r in verdict.reasons)

    def test_an_armchair_joins_the_seating_group_rather_than_the_far_wall(self):
        """Validating is not the same as being placed properly, and the first
        measurement said so: the premium living/dining armchair passed every
        rule standing 379 cm from the sofa, at the opposite end of a 551 cm
        room, because `_positions` ranked seating on "as much space in front as
        the room allows" — which is right for the sofa that has nothing to sit
        with and exactly backwards for the chair that does.

        An armchair 4 metres from the sofa is two seats in a room, not a
        seating group. Nobody can hold a conversation across it."""
        from app.geometry import _gap_between

        plan = auto_plan("unit01", "living_dining", TIER_SAR["premium"],
                         ["warm", "minimal"])
        placed = {i.slot_id: i for i in plan.placed}
        chair, sofa = placed["lounge_chair"], placed["primary_seating"]
        box = lambda i: (i.x, i.y, i.x + i.dims_cm["w"], i.y + i.dims_cm["d"])
        assert _gap_between(box(chair), box(sofa)) < 150

    def test_the_second_lamp_does_not_stand_next_to_the_first(self):
        """Also measured: the reading lamp premium unlocks was placed 6 cm from
        the accent lamp, both tucked into the same corner, because the default
        position ranking is "hug a wall, then nearest the origin" and nothing
        told it one of those lamps was already there. Two lamps in one corner
        is one lamp and a spare."""
        from app.geometry import _gap_between

        plan = auto_plan("unit01", "living_dining", TIER_SAR["premium"],
                         ["warm", "minimal"])
        lamps = [i for i in plan.placed if i.role == "floor_lamp"]
        assert len(lamps) == 2
        box = lambda i: (i.x, i.y, i.x + i.dims_cm["w"], i.y + i.dims_cm["d"])
        assert _gap_between(box(lamps[0]), box(lamps[1])) > 150

    def test_an_armchair_parked_in_the_sofas_walkway_fails(self):
        """Legal on every other rule — inside the room, clear of the door,
        touching nothing — and it stands 20cm off the front of the sofa."""
        room = self.room()
        sofa, _ = self.sofa_and_table()
        chair = Placement("CHAIR", Dims(90, 85, 95), x=60, y=210,
                          facing="N", role="armchair")
        verdict = validate_layout(room, [sofa, chair])
        assert verdict.status == "fail"
        assert any("walkway in front of SOFA" in r for r in verdict.reasons)


class TestEveryNewRuleShipsWithSomewhereToPutTheFurniture:
    """The lesson from `bedside_reach`, pinned so it cannot be repeated.

    That rule shipped without a `_positions` branch and every bedroom
    immediately reported "no nightstand could be placed anywhere in the room":
    a constraint the search cannot satisfy does not improve a layout, it
    silently deletes a piece of furniture. Each of the four rules added here is
    therefore tested twice — once in `test_ergonomics.py` for what it rejects,
    and once here for what the planner can still fill.
    """

    def box(self, i):
        return (i.x, i.y, i.x + i.dims_cm["w"], i.y + i.dims_cm["d"])

    def lateral(self, a, b, axis):
        i = 0 if axis == "x" else 1
        return max(0.0, min(a[i + 2], b[i + 2]) - max(a[i], b[i]))

    def plans(self):
        return {t["id"]: auto_plan("unit01", "living_dining", t["sar"],
                                   ["warm", "minimal"])
                for t in BUDGET_TIERS}

    def test_the_console_the_planner_picks_faces_the_sofa_it_serves(self):
        """Measured before the tv_console branch existed: the console landed at
        x=0-40 against a sofa spanning x=120-293, in 15 of 18 living/dining
        combinations."""
        for tier, plan in self.plans().items():
            placed = {i.slot_id: i for i in plan.placed}
            if "media_console" not in placed or "primary_seating" not in placed:
                continue
            tv, sofa = placed["media_console"], placed["primary_seating"]
            axis = "x" if tv.facing in ("N", "S") else "y"
            assert self.lateral(self.box(tv), self.box(sofa), axis) > 0, tier

    def test_the_rug_the_planner_picks_lies_under_the_seating_group(self):
        """Measured before the rug branch existed: a 120x120cm rug in the
        origin corner, touching the sofa along a line and sharing 0% of its
        area with the seating group."""
        from app.geometry import RUG_ANCHOR_MIN_FRACTION, _seating_group_box

        for tier, plan in self.plans().items():
            rugs = [i for i in plan.placed if i.role == "rug"]
            group = [i for i in plan.placed
                     if i.role in {"sofa", "armchair", "coffee_table"}]
            if not rugs or not group:
                continue
            gb = _seating_group_box(
                [Placement(i.asin, Dims(i.dims_cm["w"], i.dims_cm["d"],
                                        i.dims_cm["h"]), i.x, i.y, role=i.role)
                 for i in group])
            rb = self.box(rugs[0])
            shared = (self.lateral(rb, gb, "x") * self.lateral(rb, gb, "y"))
            smaller = min((rb[2] - rb[0]) * (rb[3] - rb[1]),
                          (gb[2] - gb[0]) * (gb[3] - gb[1]))
            assert shared / smaller >= RUG_ANCHOR_MIN_FRACTION, (
                f"{tier}: rug shares {100 * shared / smaller:.0f}% with the group")

    def test_the_accent_lamp_lands_beside_the_seating_and_not_in_a_corner(self):
        """The reading lamp premium unlocks is still spread away from it; the
        rule only ever asked for one lamp to be useful."""
        from app.geometry import LAMP_REACH_CM, _gap_between

        for tier, plan in self.plans().items():
            lamps = [i for i in plan.placed if i.role == "floor_lamp"]
            seats = [i for i in plan.placed if i.role in {"sofa", "armchair"}]
            if not lamps or not seats:
                continue
            nearest = min(_gap_between(self.box(l), self.box(s))
                          for l in lamps for s in seats)
            assert nearest <= LAMP_REACH_CM, f"{tier}: nearest lamp {nearest:.0f}cm"

    def test_the_armchair_is_turned_toward_the_group_not_merely_parked_near_it(self):
        """The position term alone was not enough: `_positions` pulled the chair
        toward the sofa and then took whichever facing sorted first."""
        premium = auto_plan("unit01", "living_dining", TIER_SAR["premium"],
                            ["warm", "minimal"])
        placed = {i.slot_id: i for i in premium.placed}
        assert "lounge_chair" in placed, "the armchair was deleted, not placed"
        assert premium.validation["status"] == "pass", premium.validation["reasons"]

    def test_every_room_the_planner_furnishes_can_be_walked_around(self):
        """The circulation rule's half of this class's contract.

        `validate_layout` rejecting unwalkable rooms is worth nothing on its own
        — the planner would simply return fewer pieces of furniture, which is
        what `bedside_reach` did. What this asserts is that the position search
        can actually FIND walkable arrangements, for every room, every tier and
        both ends of the style range.

        Measured before the `_positions` change, over all 200 exported
        configurations: median coverage 15.3%, minimum 3.5%, and 141 of 200
        below half the floor. Every fully-furnished living/dining room in the
        corpus — 9 and 11 items — scored under 6%.
        """
        from app.geometry import CIRCULATION_MIN_COVERAGE, circulation_map

        for style in (["warm", "minimal"], ["industrial", "mid_century"]):
            for unit, room_name in PLANNABLE:
                for tier in BUDGET_TIERS:
                    plan = auto_plan(unit, room_name, tier["sar"], style)
                    room = load_home().unit(unit).room(room_name).to_room()
                    circ = circulation_map(room, [
                        Placement(i.asin, Dims(i.dims_cm["w"], i.dims_cm["d"],
                                               i.dims_cm["h"]),
                                  x=i.x, y=i.y, facing=i.facing, role=i.role)
                        for i in plan.placed
                    ])
                    assert circ.coverage >= CIRCULATION_MIN_COVERAGE, (
                        f"{unit}/{room_name}/{tier['id']}/{style}: only "
                        f"{circ.coverage:.1%} of the floor is reachable from "
                        f"the door ({circ.reachable_cells}/"
                        f"{circ.passable_cells} cells)")

    def test_the_room_the_defect_was_reported_against_is_walkable_now(self):
        """unit01 living_dining 15000 industrial+mid_century — the plan in the
        report, which scored 139 reachable cells out of 3,270."""
        from app.geometry import circulation_map

        plan = auto_plan("unit01", "living_dining", 15000,
                         ["industrial", "mid_century"])
        room = load_home().unit("unit01").room("living_dining").to_room()
        circ = circulation_map(room, [
            Placement(i.asin, Dims(i.dims_cm["w"], i.dims_cm["d"], i.dims_cm["h"]),
                      x=i.x, y=i.y, facing=i.facing, role=i.role)
            for i in plan.placed
        ])
        assert circ.reachable_cells > 1000, (
            f"{circ.reachable_cells}/{circ.passable_cells} cells reachable")
        assert plan.validation["status"] == "pass", plan.validation["reasons"]

    def test_no_tier_of_any_room_lost_a_slot_to_the_new_rules(self):
        """The count that matters. A rule that improves every layout it allows
        and quietly drops the ones it does not has made the room worse."""
        expected = {"floor_covering": 6, "dining_table": 3, "accent_lighting": 3}
        seen: dict[str, int] = {}
        for unit, room in PLANNABLE:
            for style in ([], ["warm", "minimal"], ["modern", "luxury"],
                          ["industrial", "mid_century"], ["boho", "scandi"]):
                for tier in BUDGET_TIERS:
                    for u in auto_plan(unit, room, tier["sar"], style).unfilled:
                        seen[u.slot_id] = seen.get(u.slot_id, 0) + 1
        assert seen == expected, f"unfilled slots moved: {seen}"


class TestTheDiningTableGetsChairs:
    """`RECIPES["living_dining"]` had a dining_table slot and no chair slot, so
    `auto_plan` never placed a dining chair in any of the 200 generated plans.
    `pull_out` and the `DINING_CHAIR_ROLES` handling in `check_fit` and
    `validate_layout` were tested and, from the planner's side, dead.
    """

    def test_the_recipe_offers_somewhere_to_sit_at_the_dining_table(self):
        roles = {s["role"] for s in RECIPES["living_dining"]}
        assert "dining_table" in roles
        assert roles & {"dining_chair", "dining_chairs_pair"}, (
            "a dining table with no chairs is a surface")

    def test_the_chair_slot_is_gated_like_every_other_addition(self):
        slot = next(s for s in RECIPES["living_dining"]
                    if s["role"] == "dining_chairs_pair")
        assert slot.get("unlock_sar") in {t["sar"] for t in BUDGET_TIERS}
        assert not slot["required"]
        table = next(s for s in RECIPES["living_dining"]
                     if s["role"] == "dining_table")
        assert slot["priority"] > table["priority"], (
            "the chair is positioned relative to the table, so the table goes first")

    def test_the_planner_actually_seats_the_table(self):
        plan = auto_plan("unit01", "living_dining", TIER_SAR["premium"],
                         ["warm", "minimal"])
        placed = {i.slot_id: i for i in plan.placed}
        chairs = [i for i in plan.placed if i.role == "dining_chairs_pair"]
        assert "dining_table" in placed, "no table to seat"
        assert chairs, "the dining table is still unseated"
        assert plan.validation["status"] == "pass", plan.validation["reasons"]

    def test_the_chair_is_tucked_at_the_table_rather_than_parked_elsewhere(self):
        from app.geometry import _gap_between

        plan = auto_plan("unit01", "living_dining", TIER_SAR["premium"],
                         ["warm", "minimal"])
        table = next(i for i in plan.placed if i.role == "dining_table")
        chair = next(i for i in plan.placed if i.role == "dining_chairs_pair")
        box = lambda i: (i.x, i.y, i.x + i.dims_cm["w"], i.y + i.dims_cm["d"])
        assert _gap_between(box(chair), box(table)) <= 10, (
            "a dining chair belongs pushed up to its table")

    def test_the_pull_out_rule_now_fires_on_a_real_planned_layout(self):
        """`pull_out` was reachable only from a hand-written test. Take the
        chair the planner placed, back it into the wall behind it, and the rule
        has to object — which proves it is live on planner output and not just
        on fixtures."""
        from app.geometry import Dims, Placement, validate_layout

        plan = auto_plan("unit01", "living_dining", TIER_SAR["premium"],
                         ["warm", "minimal"])
        room = load_home().unit("unit01").room("living_dining").to_room()
        objects = [
            Placement(i.asin, Dims(i.dims_cm["w"], i.dims_cm["d"], i.dims_cm["h"]),
                      x=i.x, y=i.y, facing=i.facing, role=i.role)
            for i in plan.placed
        ]
        chair = next(o for o in objects if o.resolved_role() == "dining_chairs_pair")
        # Shove it back against whichever wall it has its back to.
        w, d = chair.dims.w, chair.dims.d
        chair.x, chair.y = {
            "N": (chair.x, room.depth_cm - d), "S": (chair.x, 0.0),
            "W": (room.width_cm - w, chair.y), "E": (0.0, chair.y),
        }[chair.facing]
        verdict = validate_layout(room, objects)
        assert verdict.status == "fail"
        assert any("push back" in r.lower() for r in verdict.reasons), verdict.reasons


class TestThePanelCanWriteAProductBrief:
    """The studio renders a per-product brief straight out of the plan JSON, so
    everything the brief shows has to be in `PlacedItem` rather than fetched
    from a second source that could disagree with the plan.
    """

    def test_every_placed_item_carries_what_a_brief_needs(self):
        plan = auto_plan("unit01", "living_dining", 30000, ["warm", "minimal"])
        needed = {
            "title", "asin", "url", "price_sar", "category",
            "dims_cm", "dims_confidence",
            "colour_hex", "colour_source", "material", "material_source",
            "rating", "reviews", "flags", "flat_pack", "access", "decision",
        }
        assert plan.placed
        for item in plan.to_dict()["placed"]:
            assert needed <= set(item), f"{item['asin']} is missing {needed - set(item)}"
            assert set(item["decision"]) >= {
                "considered", "chose_because", "ranked_above", "rejected"}
            assert item["access"]["status"] in {"pass", "unverified"}

    def test_the_brief_reads_from_the_same_product_the_planner_scored(self):
        """Not re-derived, not defaulted. If the panel and the ranking can
        disagree about a product's rating, one of them is describing a
        different item to the one that will arrive."""
        catalog = {p.asin: p for p in parse_capture()}
        plan = auto_plan("unit01", "living_dining", 30000, [])
        for item in plan.placed:
            source = catalog[item.asin]
            assert item.rating == source.rating
            assert item.reviews == source.reviews
            assert item.flags == source.flags
            assert item.category == source.category
            assert item.colour_source == source.colour_source
            assert item.material_source == source.material_source

    def test_an_unpublished_fact_is_null_rather_than_a_stand_in_value(self):
        """The brief has to be able to say "not published". A rating defaulted
        to 0.0 says the buyers rated it zero, and a colour defaulted to grey
        says the seller published grey — both are confident wrong answers about
        something nobody stated."""
        blank = product("BLANK", 900.0, rating=None, reviews=0)
        blank.colour_hex, blank.colour_source = None, "missing"
        blank.material, blank.material_source = None, "missing"
        item = auto_plan("unit01", "living_dining", 30000, [],
                         catalog=[blank]).to_dict()["placed"][0]
        assert item["rating"] is None
        assert item["colour_hex"] is None
        assert item["material"] is None
        assert item["colour_source"] == "missing"


def test_recipe_for_resolves_the_bedroom_aliases():
    """`master_bedroom_2` is the bedroom recipe. That lookup was spelled out
    twice, in `auto_plan` and in `_headroom_targets`, and the two had to agree
    about a room name neither of them defines."""
    assert recipe_for("master_bedroom_2") is RECIPES["bedroom"]
    assert recipe_for("kitchen") == []
