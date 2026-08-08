"""Geometry engine tests.

Every numeric claim the agent swarm makes about space has to come from here,
because language models are unreliable at spatial arithmetic. These tests are
the contract.

All units are centimetres. Real measurements from the reference floor plan are
used where possible so the failures are ones that actually occur.
"""

import pytest

from app.geometry import (
    Dims,
    Door,
    PathSegment,
    Placement,
    Room,
    check_access_path,
    check_fit,
    validate_layout,
)

# A living room roughly matching "LIVING / DINING 11' x 18'1"" on the plan.
LIVING = Room(
    name="living",
    width_cm=335,
    depth_cm=551,
    height_cm=290,
    doors=[Door(wall="N", offset_cm=40, width_cm=90, swing="in")],
)

SOFA = Dims(w=218, d=95, h=84)
SMALL_SOFA = Dims(w=185, d=88, h=80)


# --------------------------------------------------------------------------
# check_fit
# --------------------------------------------------------------------------

def test_item_within_bounds_and_clearances_passes():
    verdict = check_fit(Placement("sofa", SMALL_SOFA, x=120, y=300), LIVING)
    assert verdict.status == "pass"


def test_item_wider_than_the_room_fails_on_bounds():
    oversized = Dims(w=400, d=95, h=84)
    verdict = check_fit(Placement("sofa", oversized, x=0, y=300), LIVING)
    assert verdict.status == "fail"
    assert any("bounds" in r for r in verdict.reasons)


def test_item_with_missing_dimensions_is_unverified_not_failed():
    """The honest-agent path. An unknown size must never be reported as fitting."""
    unknown = Dims(w=None, d=None, h=None, confidence="missing")
    verdict = check_fit(Placement("sofa", unknown, x=0, y=0), LIVING)
    assert verdict.status == "unverified"
    assert any("dimensions" in r.lower() for r in verdict.reasons)


def test_item_parked_in_the_door_swing_fails():
    """A 90cm door swinging inward needs a 90cm quarter-disc kept clear."""
    verdict = check_fit(Placement("sofa", SMALL_SOFA, x=40, y=0), LIVING)
    assert verdict.status == "fail"
    assert any("door" in r.lower() for r in verdict.reasons)


def test_walkway_narrower_than_the_minimum_fails():
    """Front edge lands 23cm off the far wall: below the 90cm primary rule."""
    verdict = check_fit(Placement("sofa", SMALL_SOFA, x=40, y=440), LIVING)
    assert verdict.status == "fail"
    assert any("walkway" in r.lower() or "clearance" in r.lower() for r in verdict.reasons)


# --------------------------------------------------------------------------
# validate_layout
# --------------------------------------------------------------------------

def test_overlapping_items_fail():
    placements = [
        Placement("sofa", SMALL_SOFA, x=40, y=300),
        Placement("table", Dims(w=120, d=60, h=45), x=100, y=320),
    ]
    verdict = validate_layout(LIVING, placements)
    assert verdict.status == "fail"
    assert any("overlap" in r.lower() for r in verdict.reasons)


def test_sofa_and_coffee_table_too_close_fails():
    """Design rule: 40-45cm between sofa front and coffee table."""
    placements = [
        Placement("sofa", SMALL_SOFA, x=40, y=300),
        Placement("coffee_table", Dims(w=110, d=60, h=42), x=40, y=395),
    ]
    verdict = validate_layout(LIVING, placements)
    assert verdict.status == "fail"


def test_layout_with_unrecognisable_ids_is_unverified_not_passed():
    """Regression: rules used to be selected by substring-matching item_id, so
    passing bare ASINs silently skipped the seating and sofa-table rules and
    returned a clean `pass`. Skipping a check must never look like passing it.
    """
    placements = [
        Placement("B0FR3WVLTS", SMALL_SOFA, x=40, y=300),
        Placement("B0H8PQ9KDJ", Dims(w=80, d=80, h=30), x=40, y=395),
    ]
    verdict = validate_layout(LIVING, placements)
    assert verdict.status == "unverified"
    assert any("role" in r.lower() for r in verdict.reasons)


def test_explicit_roles_apply_the_rules_regardless_of_id():
    """The same ASINs, with roles declared, must catch the 7cm sofa-table gap."""
    placements = [
        Placement("B0FR3WVLTS", SMALL_SOFA, x=40, y=300, role="sofa"),
        Placement("B0H8PQ9KDJ", Dims(w=80, d=80, h=30), x=40, y=395, role="coffee_table"),
    ]
    verdict = validate_layout(LIVING, placements)
    assert verdict.status == "fail"


def test_well_spaced_layout_passes():
    placements = [
        Placement("sofa", SMALL_SOFA, x=40, y=180),
        Placement("coffee_table", Dims(w=110, d=60, h=42), x=60, y=310),
    ]
    verdict = validate_layout(LIVING, placements)
    assert verdict.status == "pass"


# --------------------------------------------------------------------------
# check_access_path  — the delivery route, not the room
# --------------------------------------------------------------------------

# The real route from the reference plan: building lift, common corridor,
# then the unit's internal passage.
LIFT = PathSegment("building lift", kind="lift", width_cm=218, height_cm=208, depth_cm=208)
COMMON_CORRIDOR = PathSegment("common passage", kind="corridor", width_cm=150, height_cm=240)
FLAT_DOOR = PathSegment("flat entrance", kind="door", width_cm=90, height_cm=210)
INNER_PASSAGE = PathSegment("2'11\" internal passage", kind="corridor", width_cm=89, height_cm=270)


def test_sofa_deeper_than_the_passage_can_still_pass_on_its_side():
    """95cm deep will not go down an 89cm passage flat, but 84cm tall will.

    Tipping is how this works in reality, so the engine must model orientation
    rather than naively comparing depth to width.
    """
    verdict = check_access_path(SOFA, [INNER_PASSAGE])
    assert verdict.status == "pass"
    assert any("side" in r.lower() or "rotat" in r.lower() for r in verdict.reasons)


def test_upright_carry_is_preferred_when_it_works():
    """Don't tell someone to tip a sofa on its end through a 150cm corridor.

    Orientation search must prefer keeping the item's own height vertical, and
    only report tipping when upright genuinely does not clear.
    """
    verdict = check_access_path(SMALL_SOFA, [COMMON_CORRIDOR])
    assert verdict.status == "pass"
    assert not any("side" in r.lower() for r in verdict.reasons)


def test_item_too_big_in_every_orientation_fails():
    wardrobe = Dims(w=240, d=120, h=220)
    verdict = check_access_path(wardrobe, [INNER_PASSAGE])
    assert verdict.status == "fail"
    assert any("passage" in r.lower() for r in verdict.reasons)


def test_item_that_cannot_clear_the_door_leaf_fails():
    door_blocker = Dims(w=200, d=100, h=190)
    verdict = check_access_path(door_blocker, [FLAT_DOOR])
    assert verdict.status == "fail"
    assert any("door" in r.lower() for r in verdict.reasons)


def test_long_item_cannot_turn_the_corner_between_two_corridors():
    """Right-angle turn from the 150cm common corridor into the 89cm passage."""
    long_sofa = Dims(w=300, d=95, h=84)
    turn = PathSegment(
        "corridor turn",
        kind="turn",
        width_cm=150,
        height_cm=240,
        turn_into_cm=89,
    )
    verdict = check_access_path(long_sofa, [turn])
    assert verdict.status == "fail"
    assert any("turn" in r.lower() or "corner" in r.lower() for r in verdict.reasons)


def test_short_item_can_turn_the_same_corner():
    stool = Dims(w=45, d=45, h=45)
    turn = PathSegment(
        "corridor turn",
        kind="turn",
        width_cm=150,
        height_cm=240,
        turn_into_cm=89,
    )
    assert check_access_path(stool, [turn]).status == "pass"


def test_item_too_large_for_the_lift_car_fails():
    wardrobe = Dims(w=250, d=70, h=230)
    verdict = check_access_path(wardrobe, [LIFT])
    assert verdict.status == "fail"
    assert any("lift" in r.lower() for r in verdict.reasons)


def test_access_path_names_every_failing_segment():
    wardrobe = Dims(w=240, d=120, h=220)
    verdict = check_access_path(wardrobe, [LIFT, COMMON_CORRIDOR, FLAT_DOOR, INNER_PASSAGE])
    assert verdict.status == "fail"
    assert any("flat entrance" in r for r in verdict.reasons)


def test_missing_dimensions_makes_the_whole_route_unverified():
    unknown = Dims(w=None, d=None, h=None, confidence="missing")
    verdict = check_access_path(unknown, [LIFT, FLAT_DOOR])
    assert verdict.status == "unverified"


def test_full_real_route_passes_for_a_sensibly_sized_sofa():
    verdict = check_access_path(SMALL_SOFA, [LIFT, COMMON_CORRIDOR, FLAT_DOOR, INNER_PASSAGE])
    assert verdict.status == "pass"


# --------------------------------------------------------------------------
# Regressions from the adversarial audit and code review.
# Every one of these returned a confident `pass` before the fix.
# --------------------------------------------------------------------------

def test_walkway_is_measured_to_other_furniture_not_only_to_walls():
    """Two sofas 3cm apart used to pass: _front_gap only looked at the wall."""
    a = Placement("sofaA", Dims(w=200, d=90, h=80), x=0, y=0, facing="S", role="sofa")
    b = Placement("sofaB", Dims(w=200, d=90, h=80), x=0, y=93, facing="N", role="sofa")
    assert validate_layout(LIVING, [a, b]).status == "fail"


def test_the_two_endpoints_agree_about_a_dining_table():
    """check_fit failed an 80cm table at y=386 while validate_layout passed it.

    The disagreement was real and the resolution is that neither answer was
    right by accident: a dining table does not need a 90cm circulation walkway
    behind it, it needs chair pull-out room. Clearance is now chosen by role,
    and both endpoints choose it the same way.
    """
    table = Placement("t", Dims(w=140, d=80, h=76), x=98, y=386, facing="S", role="dining_table")
    assert check_fit(table, LIVING).status == validate_layout(LIVING, [table]).status == "pass"


def test_a_dining_table_jammed_against_the_wall_still_fails():
    """75cm of pull-out room is the floor. 30cm is not a dining table."""
    table = Placement("t", Dims(w=140, d=80, h=76), x=98, y=441, facing="S", role="dining_table")
    assert check_fit(table, LIVING).status == "fail"


def test_a_wall_hugging_item_needs_no_front_clearance():
    """A TV console lives against the wall. Demanding 90cm in front of it was
    a seating rule fired at the wrong role.

    Placed on the south wall, away from the north door — a console parked in a
    door swing is a different (real) failure, covered above.
    """
    console = Placement("c", Dims(w=150, d=35, h=55), x=25, y=516, facing="N", role="tv_console")
    assert check_fit(console, LIVING).status == "pass"


def test_non_finite_dimensions_are_rejected():
    for bad in (float("nan"), float("inf"), -300, 0):
        verdict = check_fit(Placement("x", Dims(w=bad, d=90, h=80), x=0, y=0), LIVING)
        assert verdict.status != "pass", f"{bad} produced a pass"


def test_empty_route_is_unverified_not_passed():
    """Nothing was checked, so nothing passed."""
    assert check_access_path(Dims(500, 500, 500), []).status == "unverified"


def test_empty_layout_is_unverified_not_passed():
    assert validate_layout(LIVING, []).status == "unverified"


def test_door_swing_rule_actually_runs_for_real_rooms():
    """The rule existed but every Room was built with doors=[], so it never
    executed once. Advertised-but-dead is the worst kind of check."""
    from app.home import load_home

    room = load_home().unit("unit01").room("living_dining").to_room()
    assert room.doors, "living_dining has no door, so the swing rule cannot run"


def test_turn_reports_when_an_item_must_be_stood_on_end():
    """A 190cm sofa only makes the 150->89cm corner stood upright. The door
    check says 'must be carried on its side'; the turn said nothing."""
    turn = PathSegment("corridor turn", kind="turn", width_cm=150, height_cm=240, turn_into_cm=89)
    verdict = check_access_path(Dims(w=190, d=85, h=85), [turn])
    assert verdict.status == "pass"
    assert any("end" in r.lower() or "upright" in r.lower() for r in verdict.reasons)


def test_failing_route_does_not_mix_reassuring_notes_into_reasons():
    """A `fail` used to ship 'clears at 90x90cm' as reasons[0]."""
    segs = [
        PathSegment("wide door", kind="door", width_cm=120, height_cm=210),
        PathSegment("narrow door", kind="door", width_cm=60, height_cm=210),
    ]
    verdict = check_access_path(Dims(w=90, d=90, h=90), segs)
    assert verdict.status == "fail"
    assert all("clears" not in r for r in verdict.reasons)


def test_a_coffee_table_out_of_reach_of_the_seating_fails():
    """A brute-force search found 'valid' positions for a coffee table behind
    the sofa. Every rule passed and the result was useless furniture.

    Constraints are not the same as function: the 40cm rule only fired when the
    pair were already close, so putting them 2m apart satisfied everything.
    """
    placements = [
        Placement("sofa", SMALL_SOFA, x=5, y=205, facing="N", role="sofa"),
        # 57cm behind the sofa: outside the 40cm rule (which only fires when too
        # CLOSE), and clear of every walkway. Nothing at all used to fire.
        Placement("table", Dims(w=80, d=80, h=30), x=0, y=350, role="coffee_table"),
    ]
    verdict = validate_layout(LIVING, placements)
    assert verdict.status == "fail"
    assert any("out of reach" in r.lower() for r in verdict.reasons)


def test_a_coffee_table_in_front_of_the_sofa_passes():
    """The Ashley Roanhowe at the position a brute-force search found: 66cm
    deep, 49cm clear of the sofa front. In reach, and legal."""
    placements = [
        Placement("sofa", SMALL_SOFA, x=5, y=205, facing="N", role="sofa"),
        Placement("table", Dims(w=127, d=66, h=48.6), x=0, y=90, role="coffee_table"),
    ]
    assert validate_layout(LIVING, placements).status == "pass"


def test_a_coffee_table_with_no_seating_at_all_is_not_penalised():
    """The rule is about a relationship. With nothing to relate to, it is silent."""
    placements = [Placement("table", Dims(w=80, d=80, h=30), x=0, y=330, role="coffee_table")]
    assert validate_layout(LIVING, placements).status == "pass"


def test_a_dining_chair_tucked_at_its_table_is_not_a_violation():
    """The same bug class as the dining table, one level down. A chair 2cm from
    its table is correct; the walkway rule was treating the table as an
    obstruction in front of the chair, and the chair as one in front of the table.
    """
    placements = [
        Placement("t", Dims(w=140, d=80, h=76), x=98, y=386, role="dining_table", facing="S"),
        Placement("c", Dims(w=45, d=50, h=95), x=108, y=334, role="dining_chairs_pair", facing="S"),
    ]
    assert validate_layout(LIVING, placements).status == "pass"


def test_a_dining_chair_with_no_room_to_push_back_fails():
    """Pull-out space is BEHIND a dining chair, not in front of it — in front is
    the table. 20cm to the wall behind is not a usable seat."""
    placements = [
        Placement("t", Dims(w=140, d=80, h=76), x=98, y=100, role="dining_table", facing="S"),
        Placement("c", Dims(w=45, d=50, h=95), x=108, y=20, role="dining_chairs_pair", facing="S"),
    ]
    verdict = validate_layout(LIVING, placements)
    assert verdict.status == "fail"
    assert any("push back" in r.lower() or "pull-out" in r.lower() for r in verdict.reasons)


def test_a_hairline_clearance_is_reported_as_tight_not_clean():
    """85cm through an 85cm opening is zero margin. Real openings lose width to
    hinges, door stops and the hands carrying the item, so a verdict that reads
    identically to a comfortable pass is how a return happens."""
    verdict = check_access_path(
        Dims(w=190, d=85, h=85),
        [PathSegment("lift car doors", kind="door", width_cm=85, height_cm=210)],
    )
    assert verdict.status == "pass"
    assert any("tight" in r.lower() for r in verdict.reasons)


def test_a_comfortable_clearance_is_not_flagged_as_tight():
    verdict = check_access_path(
        Dims(w=190, d=60, h=70),
        [PathSegment("lift car doors", kind="door", width_cm=85, height_cm=210)],
    )
    assert verdict.status == "pass"
    assert not any("tight" in r.lower() for r in verdict.reasons)


def test_a_rug_does_not_block_the_walkway_it_lies_in():
    """You walk on a rug. It was counted as an obstruction in front of the sofa
    it was laid under, which made rugs unplaceable in any furnished room."""
    placements = [
        Placement("sofa", SMALL_SOFA, x=5, y=205, facing="N", role="sofa"),
        Placement("rug", Dims(w=300, d=200, h=2), x=15, y=20, role="rug"),
    ]
    assert validate_layout(LIVING, placements).status == "pass"


def test_something_tall_in_the_same_place_still_blocks():
    """The rule is about height, not about being called a rug."""
    placements = [
        Placement("sofa", SMALL_SOFA, x=5, y=205, facing="N", role="sofa"),
        Placement("bookshelf", Dims(w=300, d=200, h=180), x=15, y=20, role="bookshelf"),
    ]
    assert validate_layout(LIVING, placements).status == "fail"


def test_a_rug_may_lie_under_the_furniture_standing_on_it():
    placements = [
        Placement("sofa", SMALL_SOFA, x=40, y=200, facing="N", role="sofa"),
        Placement("rug", Dims(w=300, d=200, h=2), x=20, y=150, role="rug"),
    ]
    v = validate_layout(LIVING, placements)
    assert not any("overlap" in r.lower() for r in v.reasons)


def test_a_long_thin_item_can_go_diagonally_into_a_lift_car():
    """A 3m rolled rug does not fit a 218x208x220cm car along any axis, but the
    car's space diagonal is 373cm — you stand it corner to corner, which is what
    anyone actually does with a rug or a ladder."""
    car = PathSegment("lift", kind="lift", width_cm=218, height_cm=220, depth_cm=208)
    verdict = check_access_path(Dims(w=300, d=30, h=30), [car])
    assert verdict.status == "pass"
    assert any("angled" in r.lower() or "diagonal" in r.lower() for r in verdict.reasons)


def test_a_bulky_item_still_cannot_be_wedged_in_diagonally():
    """The diagonal only helps something slender. A wardrobe is not slender."""
    car = PathSegment("lift", kind="lift", width_cm=218, height_cm=220, depth_cm=208)
    assert check_access_path(Dims(w=300, d=150, h=150), [car]).status == "fail"


def test_something_longer_than_the_space_diagonal_still_fails():
    car = PathSegment("lift", kind="lift", width_cm=218, height_cm=220, depth_cm=208)
    assert check_access_path(Dims(w=420, d=20, h=20), [car]).status == "fail"


def test_a_flexible_item_is_not_held_to_the_rigid_corner_maths():
    """The turning formula assumes a rigid rectangle — right for a sofa, wrong
    for a rolled rug, which you simply flex round the corner."""
    turn = PathSegment("turn", kind="turn", width_cm=150, height_cm=240, turn_into_cm=90)
    rug = Dims(w=300, d=30, h=30)
    assert check_access_path(rug, [turn]).status == "fail"
    lenient = check_access_path(rug, [turn], flexible=True)
    assert lenient.status == "pass"
    assert any("bends" in r.lower() for r in lenient.reasons)


def test_flexibility_does_not_excuse_a_doorway_it_cannot_fit_through():
    """The exemption is for turns only. A door is still a hole of a given size."""
    door = PathSegment("door", kind="door", width_cm=75, height_cm=210)
    assert check_access_path(Dims(w=200, d=120, h=100), [door], flexible=True).status == "fail"
