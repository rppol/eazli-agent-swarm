"""What is still missing from a room that already passes every fit rule.

A layout can clear every clearance and still look unfinished — bare walls, a
corner of empty floor. The obvious way to "fix" that is to have a model name a
painting, which is exactly the kind of unfalsifiable claim the rest of this
project refuses to make. So finishing is treated as geometry too: measure the
free wall runs and the free floor, size the piece from a stated design rule,
and say plainly whether the catalogue can supply one.
"""

from __future__ import annotations

import pytest

from app.geometry import Dims, Door, Placement, Room
from app.styling import free_floor_pockets, free_wall_runs, suggest_finishing


@pytest.fixture
def empty_room() -> Room:
    return Room(name="living_dining", width_cm=400, depth_cm=300, height_cm=290)


def sofa(x: float, y: float, w: float = 200, h: float = 85) -> Placement:
    return Placement("sofa", Dims(w, 90, h, confidence="stated"), x, y,
                     facing="S", role="sofa")


class TestFreeWallRuns:
    def test_an_empty_room_offers_each_wall_whole(self, empty_room):
        runs = {r.wall: r for r in free_wall_runs(empty_room, [])}
        assert runs["N"].length_cm == 400
        assert runs["E"].length_cm == 300

    def test_a_tall_wardrobe_consumes_the_wall_behind_it(self, empty_room):
        wardrobe = Placement("wardrobe", Dims(200, 60, 220, confidence="stated"),
                             0, 0, facing="S", role="wardrobe")
        north = [r for r in free_wall_runs(empty_room, [wardrobe]) if r.wall == "N"]
        assert all(r.length_cm <= 200 for r in north)
        assert sum(r.length_cm for r in north) == 200

    def test_a_low_sofa_leaves_the_wall_above_it_hangable(self, empty_room):
        """A sofa is 85 cm tall and art hangs at 145 cm. The wall is not gone —
        it is the single best place in the room to hang something."""
        north = [r for r in free_wall_runs(empty_room, [sofa(100, 0)]) if r.wall == "N"]
        assert len(north) == 1
        assert north[0].length_cm == 400
        assert north[0].anchor is not None
        assert north[0].anchor.role == "sofa"

    def test_a_run_shorter_than_a_hangable_minimum_is_not_offered(self, empty_room):
        wide = Placement("wardrobe", Dims(370, 60, 230, confidence="stated"),
                         0, 0, facing="S", role="wardrobe")
        north = [r for r in free_wall_runs(empty_room, [wide]) if r.wall == "N"]
        assert north == []

    def test_a_wall_with_a_door_in_it_loses_the_doorway(self, empty_room):
        empty_room.doors = [Door(wall="N", offset_cm=150, width_cm=90)]
        north = [r for r in free_wall_runs(empty_room, []) if r.wall == "N"]
        assert sum(r.length_cm for r in north) == pytest.approx(400 - 90)


class TestFreeFloor:
    def test_an_empty_room_is_one_big_pocket(self, empty_room):
        pockets = free_floor_pockets(empty_room, [])
        assert pockets[0].area_m2 == pytest.approx(12.0)

    def test_furniture_reduces_the_pocket_it_stands_in(self, empty_room):
        with_sofa = free_floor_pockets(empty_room, [sofa(0, 0)])
        assert max(p.area_m2 for p in with_sofa) < 12.0

    def test_a_pocket_too_small_to_stand_in_is_not_offered(self):
        tiny = Room(name="common_toilet", width_cm=137, depth_cm=100, height_cm=290)
        assert free_floor_pockets(tiny, []) == []


class TestSuggestions:
    def test_it_sizes_art_against_the_furniture_it_hangs_over(self, empty_room):
        picks = suggest_finishing(empty_room, [sofa(100, 0)], ["warm", "minimal"])
        art = next(p for p in picks if p.kind == "wall_art")
        # The rule is 60-75% of the piece below. 200 cm sofa -> 120-150 cm.
        assert 120 <= art.width_cm <= 150
        assert art.centre_height_cm == 145
        assert "sofa" in art.because

    def test_it_never_claims_the_catalogue_has_something_it_does_not(self, empty_room):
        picks = suggest_finishing(empty_room, [sofa(100, 0)], ["warm", "minimal"])
        art = next(p for p in picks if p.kind == "wall_art")
        assert art.in_catalogue is False
        assert art.search_query

    def test_personality_changes_what_is_suggested(self, empty_room):
        warm = {p.kind for p in suggest_finishing(empty_room, [sofa(100, 0)], ["warm", "boho"])}
        lux = {p.kind for p in suggest_finishing(empty_room, [sofa(100, 0)], ["modern", "luxury"])}
        assert warm != lux

    def test_a_room_with_no_bare_wall_is_told_it_needs_nothing_on_the_walls(self):
        """Full-height storage on north and south leaves 80 cm of each side
        wall — under the 90 cm it takes to be worth hanging anything on."""
        packed = Room(name="bedroom", width_cm=200, depth_cm=200, height_cm=290)
        full = [
            Placement("wardrobe-n", Dims(200, 60, 230, confidence="stated"),
                      0, 0, facing="S", role="wardrobe"),
            Placement("wardrobe-s", Dims(200, 60, 230, confidence="stated"),
                      0, 140, facing="N", role="wardrobe"),
        ]
        assert free_wall_runs(packed, full) == []
        assert not [p for p in suggest_finishing(packed, full, ["warm"])
                    if p.kind in ("wall_art", "wall_mirror")]

    def test_it_does_not_suggest_something_the_plan_already_placed(self, empty_room):
        """The catalogue does stock floor lamps and the planner already sources
        one. Offering an arc lamp next to it is noise, and the viewer drew it
        with the potted-plant glyph on top of that."""
        lamp = Placement("floor_lamp", Dims(26, 39, 158, confidence="stated"),
                         0, 0, facing="N", role="floor_lamp")
        picks = suggest_finishing(empty_room, [sofa(100, 0), lamp],
                                  ["industrial", "mid_century"])
        assert "arc_floor_lamp" not in {p.kind for p in picks}
        assert "large_plant" in {p.kind for p in picks}

    def test_every_suggestion_carries_the_rule_that_produced_it(self, empty_room):
        for p in suggest_finishing(empty_room, [sofa(100, 0)], ["warm", "minimal"]):
            assert p.because
            assert p.rule
