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

from app.geometry import Dims, Door, Placement, Room, validate_layout


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


@pytest.fixture
def living_with_door() -> Room:
    """The same room as `load_home()` actually builds it.

    The fixture above has `doors=[]`, which is fine for the clearance rules —
    they are all about the relationship between two pieces of furniture. It is
    not fine for circulation, which is entirely about where you come in from.
    `_default_door` puts unit01's living/dining door on the north wall, 30cm
    from the corner, with the 90cm main leaf, and every coordinate in
    `TestYouHaveToBeAbleToWalkAroundTheRoom` was measured against that door.
    """
    return Room(name="living_dining", width_cm=335, depth_cm=551, height_cm=290,
                doors=[Door(wall="N", offset_cm=30, width_cm=90, swing="in")])


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


class TestYouHaveToBeAbleToWalkAroundTheRoom:
    """Every rule above this line is about one piece of furniture, or about two
    of them. None of them is about the floor that is left over.

    A user looked at a rendered room and said it still is not ergonomic — "can
    we roam around the house". Measured with a width-aware flood fill from the
    door (5cm grid, eroded by half a 55cm shoulder, so a cell counts only where
    a person actually fits), the generated rooms are not rooms you can walk
    around:

        unit01 living_dining 15000 industrial+mid_century   139/3270   4%
        unit01 living_dining 30000 warm+minimal             112/1951   6%
        unit04 living_dining 30000 warm+minimal             112/2864   4%
        unit01 bedroom       30000 boho+scandi              160/1063  15%

    All four returned `pass`. An earlier audit ran a circulation BFS over this
    same corpus and reported zero failures, because it used POINT connectivity:
    a 1cm slot between two bookshelves counted as a corridor. Width is the
    whole point, and `test_a_gap_too_narrow_to_walk_through_is_not_a_corridor`
    is the case that separates the two.
    """

    def reference_plan(self):
        """unit01 living_dining 15000 industrial+mid_century, exactly as
        generated. Nine products, every existing rule satisfied, and 4% of the
        floor reachable from the front door."""
        return [
            at("sofa", 120, 0, 153.8, 75.7, 78.6, facing="S"),
            at("coffee_table", 120, 120, 28, 39, 60, facing="N"),
            at("tv_console", 120, 371, 40, 180, 60, facing="N"),
            at("dining_table", 0, 390, 120, 80, 75.9, facing="S"),
            at("floor_lamp", 285, 0, 25, 25, 180, facing="N"),
            at("armchair", 0, 90, 55, 45, 67, facing="E"),
            at("bookshelf", 0, 135, 80, 30, 180, facing="S"),
            at("dining_chairs_pair", 15, 315, 54.1, 84.1, 45, facing="S"),
            at("rug", 0, 0, 300, 200, 2, facing="N"),
        ]

    def test_the_plan_a_person_cannot_walk_around_is_rejected(self, living_with_door):
        """The door opens into a 139-cell pocket bounded by the sofa to the
        east and the bookshelf to the south, and the other 96% of the floor —
        the dining table, the television, the armchair — is on the far side of
        a 40cm slot between the bookshelf and the coffee table."""
        why = failed(living_with_door, *self.reference_plan())
        assert why, "a room with 4% of its floor reachable should not pass"
        assert "reach" in why or "walk" in why or "circulation" in why

    def test_the_same_nine_products_arranged_around_a_clear_spine_pass(
            self, living_with_door):
        """The same nine products, the same room, the same door — rearranged.

        Found by brute-force search over the planner's own 15cm grid: every
        existing rule holds AND all 2,682 passable cells are reachable from the
        door. This is what fixes the threshold below. The failure above is a
        placement defect, not a furniture-does-not-fit defect, and a rule that
        no arrangement could satisfy would only delete furniture."""
        v = validate_layout(living_with_door, [
            at("sofa", 165, 330, 153.8, 75.7, 78.6, facing="S"),
            at("coffee_table", 45, 435, 28, 39, 60, facing="S"),
            at("tv_console", 0, 165, 40, 180, 60, facing="E"),
            at("dining_table", 210, 150, 120, 80, 75.9, facing="N"),
            at("floor_lamp", 210, 300, 25, 25, 180, facing="E"),
            at("armchair", 255, 240, 55, 45, 67, facing="W"),
            at("bookshelf", 45, 90, 80, 30, 180, facing="E"),
            at("dining_chairs_pair", 195, 90, 54.1, 84.1, 45, facing="S"),
            at("rug", 30, 135, 300, 200, 2, facing="E"),
        ])
        assert v.status == "pass", v.reasons

    def test_a_gap_too_narrow_to_walk_through_is_not_a_corridor(
            self, living_with_door):
        """The mistake the previous audit made, pinned.

        Two runs of shelving spanning the room with 30cm between them. Every
        clearance rule holds — both fronts face the door and open onto metres
        of floor — and a point-connectivity flood fill walks straight through
        the 30cm slot and calls the room connected. Nobody 55cm across gets
        through it, and the 3,135 cells beyond are unreachable."""
        why = failed(living_with_door,
                     at("bookshelf", 0, 200, 150, 30, 180, facing="N",
                        name="shelf_west"),
                     at("bookshelf", 180, 200, 155, 30, 180, facing="N",
                        name="shelf_east"))
        assert why, "a 30cm slot is not a corridor"

    def test_the_same_two_shelves_with_a_real_gap_between_them_pass(
            self, living_with_door):
        """60cm between the same two runs, and the room is one room again.

        The contrast matters: the rule has to be about the width of the gap and
        not about there being furniture in the middle of the room at all."""
        v = validate_layout(living_with_door, [
            at("bookshelf", 0, 200, 135, 30, 180, facing="N", name="shelf_west"),
            at("bookshelf", 195, 200, 140, 30, 180, facing="N", name="shelf_east"),
        ])
        assert v.status == "pass", v.reasons

    def test_the_failure_says_how_narrow_the_route_got(self, living_with_door):
        """"Unreachable" on its own is not actionable. The reference plan's
        bottleneck is the 40cm slot between the bookshelf at x=0-80 and the
        coffee table at x=120, and the report has to name a width so the reader
        can compare it against the 55cm a person needs."""
        why = failed(living_with_door, *self.reference_plan())
        assert "cm" in why
        assert "55" in why, "the shoulder width the gap is being judged against"

    def test_an_item_walled_into_an_alcove_is_named_even_when_the_room_is_open(
            self, living_with_door):
        """The other half of the rule, and the half a floor-coverage number
        cannot see.

        A bookshelf with 50cm in front of it clears `reach_clearance` (45cm)
        and contributes NO passable cells to the room, so the coverage share is
        still 100%. There is nowhere to stand to reach the shelf: a person
        needs about 55cm before their shoulders fit at all. The item has to be
        named, not averaged away."""
        why = failed(living_with_door,
                     at("bookshelf", 0, 300, 80, 30, 180, facing="S",
                        name="stranded_shelf"),
                     at("bookshelf", 0, 380, 110, 30, 180, facing="S",
                        name="wardrobe_run"))
        assert why
        assert "stranded_shelf" in why.lower(), (
            f"the unreachable item has to be named: {why}")
        assert "wardrobe_run" not in why.lower(), (
            "the run with open floor in front of it is fine and must not be "
            "swept up with the one that is not")

    def test_a_rug_is_walked_on_rather_than_walked_around(self, living_with_door):
        """`FLOOR_COVERING_MAX_H_CM` already exempts a rug from the walkway
        rules; circulation has to agree, or a 2cm rug laid across the room
        would sever it. This one spans the full 335cm width, and the shelf
        beyond it is reachable only by walking over it."""
        v = validate_layout(living_with_door, [
            at("rug", 0, 200, 335, 200, 2, facing="N"),
            at("bookshelf", 0, 450, 80, 30, 180, facing="N", name="shelf"),
        ])
        assert v.status == "pass", v.reasons

    def test_a_room_with_no_door_says_it_could_not_be_checked(self, living):
        """There is no such thing as reachability without somewhere to come in
        from, and `Room.doors` defaults to empty for any caller who does not
        supply one. Silently skipping is what made `door_swing` dead for a
        release while the API advertised it — so the check stands down and says
        so in `notes` rather than inventing a door or failing the layout."""
        v = validate_layout(living, [at("sofa", 120, 0, 153.8, 75.7, 78.6)])
        assert v.status == "pass", v.reasons
        notes = " ".join(v.details.get("notes", [])).lower()
        assert "door" in notes and "circulation" in notes, notes


class TestTheRulesStayHonestAboutWhatTheyChecked:
    def test_every_new_rule_is_published_in_the_rule_table(self):
        """The studio renders the rule text next to the verdict, so a rule the
        engine enforces but cannot explain is a black box."""
        from app.geometry import RULES
        for key in ("reach_clearance", "bedside_reach", "bed_access",
                    "tv_sightline", "rug_anchors_seating",
                    "lamp_within_reach_of_seating", "seat_has_a_focal_point",
                    "circulation"):
            assert key in RULES, f"{key} enforced but not published"
            assert RULES[key].get("text")

    def test_the_circulation_rule_states_its_threshold_and_where_it_came_from(self):
        """A coverage threshold is a judgement call, and the one number in this
        engine that was picked rather than measured has to say so out loud —
        including what a good layout actually scores."""
        from app.geometry import CIRCULATION_MIN_COVERAGE, RULES
        text = RULES["circulation"]["text"]
        assert f"{CIRCULATION_MIN_COVERAGE * 100:.0f}%" in text
        assert "55" in text, "the shoulder width the erosion uses"
