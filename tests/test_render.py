"""Renderer tests.

The drawings are generated from the same coordinates the geometry engine
reasons about, which is only useful if that stays true. These assert the
projection maths and that the rendered layout is one the engine actually
validates — a picture of an invalid layout would be worse than no picture.
"""

import re
import xml.etree.ElementTree as ET

import pytest

from app.geometry import validate_layout
from app.home import load_home
from viz.render import Item, build, floorplan_svg, iso, isometric_svg, to_obj


@pytest.fixture(scope="module")
def rendered(tmp_path_factory, monkeypatch_module=None):
    return build()


def test_the_rendered_layout_is_one_the_engine_validates(rendered):
    """If this ever fails, the picture shows a layout that does not hold."""
    assert rendered["layout_status"] == "pass"
    assert rendered["layout_reasons"] == []


def test_isometric_projection_is_the_standard_one():
    assert iso(0, 0, 0) == (0.0, 0.0)
    x, y = iso(100, 0, 0)
    assert x == pytest.approx(86.6, abs=0.1)
    assert y == pytest.approx(50.0, abs=0.1)
    # z is up: raising an object moves it up the screen, i.e. -y
    assert iso(0, 0, 50)[1] == pytest.approx(-50.0)


def test_equal_x_and_y_collapse_to_the_vertical_axis():
    assert iso(120, 120, 0)[0] == pytest.approx(0.0)


def test_svgs_are_well_formed_xml():
    home = load_home()
    room = home.unit("unit01").room("living_dining").to_room()
    items = [Item("s", "sofa", "sofa", 5, 205, 190, 85, 85, "N")]
    for svg in (floorplan_svg(room, items, "t"), isometric_svg(room, items, "t")):
        ET.fromstring(svg)  # raises on malformed output


def test_labels_are_xml_escaped():
    home = load_home()
    room = home.unit("unit01").room("living_dining").to_room()
    items = [Item("s", "sofa & <chair>", "sofa", 5, 205, 190, 85, 85, "N")]
    svg = floorplan_svg(room, items, "a & b")
    ET.fromstring(svg)
    assert "&amp;" in svg


def test_unfilled_slots_are_drawn_but_marked():
    home = load_home()
    room = home.unit("unit01").room("living_dining").to_room()
    items = [Item("c", "coffee table", "coffee_table", 45, 112, 110, 50, 45,
                  filled=False, note="nothing fits")]
    svg = floorplan_svg(room, items, "t")
    assert "stroke-dasharray" in svg
    assert "nothing fits" in svg


def test_obj_is_a_valid_wavefront_model():
    home = load_home()
    room = home.unit("unit01").room("living_dining").to_room()
    items = [Item("B0FR3WVLTS", "sofa", "sofa", 5, 205, 190, 85, 85, "N")]
    obj, mtl = to_obj(room, items)

    verts = [l for l in obj.splitlines() if l.startswith("v ")]
    faces = [l for l in obj.splitlines() if l.startswith("f ")]
    # floor + two walls + one sofa, eight corners and six quads each
    assert len(verts) == 4 * 8
    assert len(faces) == 4 * 6
    assert "mtllib room.mtl" in obj
    assert "newmtl sofa" in mtl

    # Face indices are 1-based and must all reference a real vertex.
    for face in faces:
        for idx in face.split()[1:]:
            assert 1 <= int(idx) <= len(verts)


def test_obj_is_in_metres_and_y_up():
    """3D tools expect metres. A 190cm sofa should be 1.9 long, not 190."""
    home = load_home()
    room = home.unit("unit01").room("living_dining").to_room()
    obj, _ = to_obj(room, [Item("s", "sofa", "sofa", 0, 0, 190, 85, 85)])
    values = [
        [float(v) for v in line.split()[1:]]
        for line in obj.splitlines() if line.startswith("v ")
    ]
    assert max(abs(v) for row in values for v in row) < 10  # metres, not cm
    # The engine's z (height) becomes OBJ's y.
    assert any(abs(row[1] - 0.85) < 1e-6 for row in values)


def test_unfilled_items_are_not_extruded_into_the_model():
    home = load_home()
    room = home.unit("unit01").room("living_dining").to_room()
    obj, _ = to_obj(room, [Item("ghost", "x", "sofa", 0, 0, 100, 100, 100, filled=False)])
    assert "o ghost" not in obj


def test_every_referenced_material_is_defined():
    home = load_home()
    room = home.unit("unit01").room("living_dining").to_room()
    obj, mtl = to_obj(room, [
        Item("a", "sofa", "sofa", 0, 0, 190, 85, 85),
        Item("b", "lamp", "floor_lamp", 250, 0, 30, 30, 155),
    ])
    used = set(re.findall(r"^usemtl (\S+)", obj, re.M))
    defined = set(re.findall(r"^newmtl (\S+)", mtl, re.M))
    assert used <= defined
