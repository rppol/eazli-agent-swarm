"""Rules for furniture you can actually use, not merely fit.

Every layout in here passes `validate_layout` today. Each one is also a room
nobody could live in, which is the point: clearance between footprints is a
weaker claim than it sounds, and a plan that clears every gap can still put a
wardrobe where its doors will not open.

The cases are taken from a real generated plan — unit01 bedroom, premium,
boho + scandi — which returned `pass` with 180cm of shelving standing behind
an armchair and a bedside table 35cm adrift of the bed.
"""

from __future__ import annotations

import pytest

from app.geometry import Dims, Placement, Room, validate_layout


def at(role: str, x: float, y: float, w: float, d: float, h: float,
       facing: str = "S") -> Placement:
    return Placement(role, Dims(w, d, h, confidence="stated"), x, y,
                     facing=facing, role=role)


def failed(room: Room, *items: Placement) -> str:
    v = validate_layout(room, list(items))
    return " ".join(v.reasons).lower() if v.status == "fail" else ""


@pytest.fixture
def bedroom() -> Room:
    return Room(name="bedroom", width_cm=335, depth_cm=305, height_cm=290)


class TestYouHaveToBeAbleToOpenIt:
    """`FRONT_CLEARANCE_BY_ROLE` gave every wall role 0.0 cm, so storage was
    allowed to have furniture pressed flat against its doors."""

    def test_an_armchair_against_the_shelves_is_rejected(self, bedroom):
        # The real one: bookshelf front at y=210, armchair starting at y=210.
        why = failed(bedroom,
                     at("bookshelf", 0, 180, 80, 30, 180),
                     at("armchair", 0, 210, 68, 68, 81, facing="E"))
        assert why, "180cm of shelving with 0cm in front of it should fail"
        assert "bookshelf" in why or "shelv" in why

    def test_a_chest_of_drawers_needs_room_to_pull_them_out(self, bedroom):
        # Drawer unit facing south, bed 45cm in front of it.
        why = failed(bedroom,
                     at("wardrobe", 105, 0, 45, 30, 97),
                     at("bed", 0, 75, 200, 95, 36))
        assert why, "45cm is not enough to open a drawer"

    def test_storage_with_a_clear_front_passes(self, bedroom):
        v = validate_layout(bedroom, [
            at("bookshelf", 0, 0, 80, 30, 180),
            at("armchair", 0, 150, 68, 68, 81, facing="E"),
        ])
        assert v.status == "pass", v.reasons


class TestABedsideTableHasToBeBesideTheBed:
    """A side table in a bedroom is for the lamp, the book and the glass of
    water. One 35cm from the bed, level with nothing, is furniture in a
    warehouse."""

    def test_a_table_adrift_of_the_bed_is_rejected(self, bedroom):
        why = failed(bedroom,
                     at("bed", 0, 75, 200, 95, 36),
                     at("side_table", 150, 0, 40, 40, 40, facing="E"))
        assert why
        assert "reach" in why or "beside" in why or "bed" in why

    def test_a_table_touching_the_bed_passes(self, bedroom):
        v = validate_layout(bedroom, [
            at("bed", 0, 75, 200, 95, 36),
            at("side_table", 200, 75, 40, 40, 40, facing="W"),
        ])
        assert v.status == "pass", v.reasons


class TestYouHaveToBeAbleToGetIntoTheBed:
    def test_a_bed_boxed_in_on_both_long_sides_is_rejected(self, bedroom):
        why = failed(bedroom,
                     at("bed", 0, 100, 200, 95, 36),
                     at("bookshelf", 0, 60, 200, 30, 180),
                     at("wardrobe", 0, 195, 200, 40, 190))
        assert why

    def test_one_clear_long_side_is_enough(self, bedroom):
        v = validate_layout(bedroom, [
            at("bed", 0, 0, 200, 95, 36),
            # Pushed to the east wall: its doors need 75cm and the bed
            # ends at x=200, so 240 would have failed and rightly.
            at("wardrobe", 280, 0, 55, 80, 190, facing="W"),
        ])
        assert v.status == "pass", v.reasons


class TestTheRulesStayHonestAboutWhatTheyChecked:
    def test_every_new_rule_is_published_in_the_rule_table(self):
        """The studio renders the rule text next to the verdict, so a rule the
        engine enforces but cannot explain is a black box."""
        from app.geometry import RULES
        for key in ("reach_clearance", "bedside_reach", "bed_access"):
            assert key in RULES, f"{key} enforced but not published"
            assert RULES[key].get("text")
