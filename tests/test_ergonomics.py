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
       facing: str = "S", name: str | None = None) -> Placement:
    """`name` separates two items of the same role — a room with two floor
    lamps in it has to be able to say which one is stranded."""
    return Placement(name or role, Dims(w, d, h, confidence="stated"), x, y,
                     facing=facing, role=role)


def failed(room: Room, *items: Placement) -> str:
    v = validate_layout(room, list(items))
    return " ".join(v.reasons).lower() if v.status == "fail" else ""


@pytest.fixture
def bedroom() -> Room:
    return Room(name="bedroom", width_cm=335, depth_cm=305, height_cm=290)


@pytest.fixture
def living() -> Room:
    """unit01 living_dining, the room every case below was generated in."""
    return Room(name="living_dining", width_cm=335, depth_cm=551, height_cm=290)


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

    def test_reach_clearance_exactly_at_the_minimum_against_furniture_is_a_tight_pass_not_a_silent_one(self, living):
        """unit04/living_dining/30000/['warm', 'minimal'], exactly as generated:
        a bookshelf at (0, 90) facing S and a 40cm-tall side table at (0, 165).
        `_front_gap` reports exactly 45cm, which equals `REACH_SHELF_CM` to the
        centimetre, so this returns a bare `pass` -- identical to a bookshelf
        with 3 clear metres in front of it.

        The 45cm number was written for reaching into a shelf that opens onto
        open floor, where a person has room to adjust stance or crouch for a
        low shelf. Here the 45cm pocket is capped by another object rather
        than open room: there is nowhere to give if you need to lean down or
        step back mid-reach, and no floor to set the book down on except the
        table that is already at the boundary. `check_fit` already treats an
        opening within `TIGHT_MARGIN_CM` of failing as pass-with-a-note (the
        door and lift checks say so explicitly); a reach clearance that is
        capped by furniture with zero margin deserves the same honesty rather
        than reading as identical to a bookshelf against open floor.
        """
        v = validate_layout(living, [
            at("bookshelf", 0, 90, 60, 30, 158, facing="S"),
            at("side_table", 0, 165, 39.5, 39.5, 40, facing="S"),
        ])
        assert v.status == "pass", v.reasons
        notes = v.details.get("notes", [])
        assert any("tight" in n.lower() for n in notes), (
            f"a reach clearance pinned to its minimum by another piece of "
            f"furniture should surface that, not pass silently: {notes}"
        )


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


class TestATelevisionHasToBeVisibleFromTheSeating:
    """`_positions` had no tv_console branch, so a console fell through to the
    generic "hug a wall, then nearest the origin" order and landed wherever the
    origin corner happened to be free — with no idea where the sofa was."""

    def test_a_console_off_to_one_side_of_the_sofa_is_rejected(self, living):
        # unit01/living_dining/8000/['warm','minimal'], exactly as generated.
        # The sofa spans x 120-293, the console x 0-40: you would be looking
        # 80cm to the side of the screen.
        why = failed(living,
                     at("sofa", 120, 0, 173.0, 86.9, 82.3, facing="S"),
                     at("tv_console", 0, 90, 40.0, 180.0, 60.0, facing="S"))
        assert why, "zero lateral overlap with the seating should fail"
        assert "sightline" in why or "screen" in why or "side of" in why

    def test_a_console_pushed_up_against_the_sofa_is_rejected(self, living):
        """Lined up with the sofa, and 93cm from it. You cannot watch a
        television from that distance; you can only sit under it."""
        why = failed(living,
                     at("sofa", 120, 0, 173.0, 86.9, 82.3, facing="S"),
                     at("tv_console", 127, 180, 120.0, 40.0, 60.0, facing="N"))
        assert why
        assert "120" in why

    def test_a_console_across_the_room_from_the_sofa_passes(self, living):
        v = validate_layout(living, [
            at("sofa", 120, 0, 173.0, 86.9, 82.3, facing="S"),
            at("tv_console", 127, 450, 120.0, 40.0, 60.0, facing="N"),
        ])
        assert v.status == "pass", v.reasons

    def test_a_console_in_a_room_with_no_seating_is_not_penalised(self, living):
        """The rule is about a relationship. With nothing to watch it from, it
        is silent — the same shape as the coffee-table reach rule."""
        v = validate_layout(living, [at("tv_console", 0, 500, 120.0, 40.0, 60.0,
                                        facing="N")])
        assert v.status == "pass", v.reasons


class TestARugHasToBeUnderTheSeatingItAnchors:
    """A rug is what makes a sofa, a chair and a table read as one group. One
    in the far corner is a floor covering with no floor to cover."""

    def test_a_rug_touching_the_group_at_a_single_edge_is_rejected(self, living):
        # Same generated plan. The rug's east edge and the sofa's west edge are
        # both at x=120, so they touch along a line and share no area at all.
        why = failed(living,
                     at("sofa", 120, 0, 173.0, 86.9, 82.3, facing="S"),
                     at("coffee_table", 120, 135, 80.0, 80.0, 45.0, facing="N"),
                     at("rug", 0, 0, 120.0, 120.0, 2.0))
        assert why, "a rug sharing 0% of its area with the group should fail"
        assert "rug" in why

    def test_a_rug_laid_under_the_group_passes(self, living):
        v = validate_layout(living, [
            at("sofa", 120, 0, 173.0, 86.9, 82.3, facing="S"),
            at("coffee_table", 120, 135, 80.0, 80.0, 45.0, facing="N"),
            at("rug", 100, 40, 220.0, 200.0, 2.0),
        ])
        assert v.status == "pass", v.reasons

    def test_a_rug_in_a_room_with_no_seating_group_is_not_penalised(self, living):
        v = validate_layout(living, [at("rug", 0, 0, 120.0, 120.0, 2.0)])
        assert v.status == "pass", v.reasons


class TestALampHasToLightSomethingYouSitOn:
    """73 of 107 generated lamps stood more than 90cm from any seat. A floor
    lamp is task lighting; one out in the middle of the floor is a pole."""

    def test_the_room_where_the_reading_light_is_four_metres_away_is_rejected(self, living):
        # unit01/living_dining/30000/['industrial','mid_century'] as generated.
        # The accent lamp is 95cm off the sofa's west end and the reading lamp
        # is 446cm from the nearest seat, so nothing in the room lights a seat.
        why = failed(living,
                     at("sofa", 120, 0, 153.8, 75.7, 82.3, facing="S"),
                     at("floor_lamp", 0, 0, 25.0, 25.0, 180.0, name="accent"),
                     at("floor_lamp", 305, 521, 30.0, 30.0, 155.0, name="reading"))
        assert why, "no lamp within reach of a seat should fail"
        assert "lamp" in why or "light" in why

    def test_one_lamp_beside_the_sofa_is_enough(self, living):
        """4cm off the sofa's west end, which is where a reading lamp goes."""
        v = validate_layout(living, [
            at("sofa", 120, 0, 173.0, 86.9, 82.3, facing="S"),
            at("floor_lamp", 90, 0, 26.0, 30.0, 155.0, name="reading"),
        ])
        assert v.status == "pass", v.reasons

    def test_a_second_lamp_across_the_room_is_still_legitimate(self, living):
        """The rule is "at least one", not "every one". An accent lamp in the
        far corner is a lighting scheme, and `_positions` deliberately spreads
        twins apart — a rule that fought that would undo it."""
        v = validate_layout(living, [
            at("sofa", 120, 0, 173.0, 86.9, 82.3, facing="S"),
            at("floor_lamp", 90, 0, 26.0, 30.0, 155.0, name="reading"),
            at("floor_lamp", 305, 521, 30.0, 30.0, 155.0, name="accent"),
        ])
        assert v.status == "pass", v.reasons

    def test_a_lamp_beside_the_bed_counts(self, bedroom):
        """A bed is where you read in a bedroom, and the lamp 36cm off its head
        end is a bedside reading light rather than a stranded pole. Scoping
        this rule to sofas and armchairs alone would have deleted the premium
        bedroom's armchair instead: measured on every bedroom in the home,
        there is no valid armchair position within 75cm of that lamp."""
        v = validate_layout(bedroom, [
            at("bed", 0, 75, 200.0, 160.0, 36.0),
            at("floor_lamp", 0, 0, 26.0, 39.0, 155.0),
        ])
        assert v.status == "pass", v.reasons


class TestASeatHasToFaceSomething:
    """59 generated armchairs faced open floor. `_positions` pulls the chair's
    POSITION toward the sofa but never looks at `facing`, so among equally
    close candidates it took whichever facing sorted first."""

    def test_an_armchair_facing_five_metres_of_empty_floor_is_rejected(self, living):
        # unit01/living_dining/30000/['industrial','mid_century']. The chair's
        # band is x 280-335; the sofa ends at 273.8, so the band is empty for
        # the whole 506cm ahead of it.
        why = failed(living,
                     at("sofa", 120, 0, 153.8, 75.7, 82.3, facing="S"),
                     at("armchair", 280, 0, 55.0, 45.0, 80.0, facing="S"))
        assert why, "an armchair facing nothing should fail"
        assert "armchair" in why

    def test_an_armchair_turned_side_on_to_the_group_is_rejected(self, living):
        # unit01/living_dining/15000/['modern','luxury']: facing E from the
        # west wall, with the sofa 180cm north of the band it looks down.
        why = failed(living,
                     at("sofa", 120, 0, 153.8, 75.7, 82.3, facing="S"),
                     at("armchair", 0, 255, 68.5, 68.0, 80.0, facing="E"))
        assert why

    def test_an_armchair_turned_toward_the_coffee_table_passes(self, living):
        v = validate_layout(living, [
            at("armchair", 0, 120, 55.0, 45.0, 80.0, facing="E"),
            at("coffee_table", 120, 120, 80.0, 80.0, 45.0, facing="N"),
        ])
        assert v.status == "pass", v.reasons

    def test_the_sofa_itself_is_not_asked_to_face_anything(self, living):
        """The sofa is the anchor the others orient around. Requiring it to
        face a focal point would make an empty room unfurnishable: the first
        piece placed has nothing to look at yet."""
        v = validate_layout(living, [
            at("sofa", 120, 0, 173.0, 86.9, 82.3, facing="S")])
        assert v.status == "pass", v.reasons

    def test_a_bedroom_armchair_facing_away_from_the_bed_is_rejected(self, bedroom):
        """FOCAL_ROLES was {sofa, coffee_table, tv_console} -- none of which a
        `bedroom` recipe ever contains, so `_seat_has_a_focal_point` could
        never fire in any bedroom: `targets` came back empty and the function
        returned early every time. unit01/bedroom/30000/['industrial',
        'mid_century'], exactly as generated: an armchair at (0, 240) facing
        E, with the bed at (0, 75) sharing zero lateral band with it -- the
        living-room equivalent of this exact shape is rejected two tests
        above. 25 of 35 generated premium-tier bedrooms placed the armchair
        with zero lateral overlap with the bed for the same reason.
        """
        why = failed(bedroom,
                     at("bed", 0, 75, 200.0, 160.0, 35.0, facing="S"),
                     at("armchair", 0, 240, 55.0, 45.0, 67.0, facing="E"))
        assert why, "an armchair facing away from the only thing in a bedroom should fail"
        assert "armchair" in why

    def test_a_bedroom_armchair_turned_toward_the_bed_passes(self):
        # A bigger room than the shared fixture: the armchair needs its own
        # 90cm walkway clearance AND the bed within FOCAL_POINT_MAX_CM, and
        # the bed is not a COMPANION_PAIRS exemption the way a coffee table
        # is, so both distances have to be satisfied at once.
        big_bedroom = Room(name="bedroom", width_cm=400, depth_cm=400, height_cm=290)
        v = validate_layout(big_bedroom, [
            at("bed", 0, 0, 200.0, 160.0, 35.0, facing="S"),
            at("armchair", 50, 250, 55.0, 45.0, 67.0, facing="N"),
        ])
        assert v.status == "pass", v.reasons


class TestTheRulesStayHonestAboutWhatTheyChecked:
    def test_every_new_rule_is_published_in_the_rule_table(self):
        """The studio renders the rule text next to the verdict, so a rule the
        engine enforces but cannot explain is a black box."""
        from app.geometry import RULES
        for key in ("reach_clearance", "bedside_reach", "bed_access",
                    "tv_sightline", "rug_anchors_seating",
                    "lamp_within_reach_of_seating", "seat_has_a_focal_point"):
            assert key in RULES, f"{key} enforced but not published"
            assert RULES[key].get("text")
