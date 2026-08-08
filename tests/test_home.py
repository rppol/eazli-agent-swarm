"""The home model: loading the surveyed floor plan and composing delivery routes.

A delivery route is not one fixed list. Getting a sofa into the living/dining
area crosses fewer doors than getting a wardrobe into a back bedroom, and
scoring both against the same worst-case route produces false rejections.
"""

import pytest

from app.geometry import Dims, check_access_path
from app.home import Home, load_home


@pytest.fixture(scope="module")
def home() -> Home:
    return load_home("data/home.json")


def test_loads_the_three_surveyed_units(home):
    assert {u.id for u in home.units} == {"unit01", "unit04", "unit05"}


def test_unit04_is_the_three_bedroom(home):
    unit = home.unit("unit04")
    bedrooms = [r for r in unit.rooms if "bedroom" in r.name]
    assert len(bedrooms) == 3


def test_room_dimensions_match_the_plan(home):
    living = home.unit("unit01").room("living_dining")
    assert (living.width_cm, living.depth_cm) == (335, 551)


def test_route_to_living_dining_has_no_internal_room_door(home):
    """The living/dining opens off the passage, so no 75cm leaf is in the way."""
    route = home.route_to("unit01", "living_dining")
    assert not any(s.kind == "door" and s.width_cm == 75 for s in route)


def test_route_to_a_bedroom_includes_the_internal_room_door(home):
    route = home.route_to("unit01", "bedroom")
    assert any(s.kind == "door" and s.width_cm == 75 for s in route)


def test_route_starts_at_the_lift_doors_not_the_lift_car(home):
    """A lift car is entered through an opening much narrower than the car.
    Omitting the doors let a 218cm sofa 'pass' a lift it cannot enter — a false
    pass on exactly the obstacle class this check exists to catch.
    """
    route = home.route_to("unit04", "master_bedroom_1")
    assert route[0].kind == "door"
    assert "lift" in route[0].name.lower()
    assert route[0].width_cm < route[1].width_cm, "the doors must be narrower than the car"
    assert route[1].kind == "lift"
    assert any(s.name == "flat entrance" for s in route)


def test_the_lift_door_assumption_is_declared(home):
    assert any("lift car door" in a.lower() for a in home.assumptions)


def test_three_seat_sofa_reaches_the_living_room_but_not_a_bedroom(home):
    """The check that motivates per-room routing.

    A 218cm assembled sofa clears the flat entrance on its side, so it reaches
    the living/dining. The same sofa cannot clear a 75cm bedroom door.
    """
    sofa = Dims(w=218, d=95, h=84)
    assert check_access_path(sofa, home.route_to("unit01", "living_dining")).status == "pass"
    assert check_access_path(sofa, home.route_to("unit01", "bedroom")).status == "fail"
