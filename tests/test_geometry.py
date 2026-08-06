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
