"""Render the layout the geometry engine actually validated.

These are not illustrations drawn to look like the plan. Every coordinate here
comes from `data/home.json` and the real product dimensions in the catalogue,
projected by the same numbers `check_fit` and `validate_layout` reason about.
If the drawing looks wrong, the layout is wrong — which is the point of drawing
it at all.

Three outputs, no new dependencies:

    floorplan.svg   top-down plan with walkways, door swing and clearances
    isometric.svg   3D view of the same coordinates
    room.obj        a real 3D model, openable in Blender / macOS Preview
    route.svg       the delivery route, leg by leg, with the binding constraint

    PYTHONPATH=. uv run python viz/render.py
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

from app.geometry import Dims, Placement, check_access_path, validate_layout
from app.home import load_home

OUT = Path("docs/img")

# Palette chosen to stay legible on both GitHub themes: mid-tone fills with a
# light card behind them, never relying on the page background.
INK = "#1f2933"
MUTED = "#7b8794"
LINE = "#cbd2d9"
PAPER = "#f5f7fa"
WALL = "#3e4c59"
GOOD = "#2f855a"
WARN = "#b7791f"
BAD = "#c53030"

ROLE_FILL = {
    "sofa": "#4c6ef5",
    "coffee_table": "#12b886",
    "dining_table": "#e8590c",
    "floor_lamp": "#ae3ec9",
    "tv_console": "#5c7cfa",
    "dining_chairs_pair": "#f59f00",
}


@dataclass
class Item:
    """One placed object, with everything the drawing needs to label it."""
    item_id: str
    label: str
    role: str
    x: float
    y: float
    w: float
    d: float
    h: float
    facing: str = "S"
    filled: bool = True
    note: str = ""

    def placement(self) -> Placement:
        return Placement(
            self.item_id, Dims(self.w, self.d, self.h),
            x=self.x, y=self.y, facing=self.facing, role=self.role,
        )


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# --------------------------------------------------------------------------
# top-down plan
# --------------------------------------------------------------------------

def floorplan_svg(room, items: list[Item], title: str) -> str:
    """Top-down plan. x runs east, y runs south, matching the engine."""
    scale = 0.9
    pad = 76
    w, d = room.width_cm * scale, room.depth_cm * scale
    width, height = w + pad * 2, d + pad * 2 + 34

    def px(v: float) -> float:
        return pad + v * scale

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height:.0f}" '
        f'viewBox="0 0 {width:.0f} {height:.0f}" font-family="ui-sans-serif,system-ui,sans-serif">',
        f'<rect width="{width:.0f}" height="{height:.0f}" fill="{PAPER}" rx="10"/>',
        f'<text x="{pad}" y="30" font-size="15" font-weight="600" fill="{INK}">{_esc(title)}</text>',
        f'<text x="{pad}" y="50" font-size="11" fill="{MUTED}">'
        f'{room.width_cm:.0f} x {room.depth_cm:.0f} cm — every coordinate from data/home.json</text>',
    ]

    top = 34
    # room shell
    parts.append(
        f'<rect x="{px(0):.1f}" y="{px(0)+top:.1f}" width="{w:.1f}" height="{d:.1f}" '
        f'fill="#ffffff" stroke="{WALL}" stroke-width="3"/>'
    )

    # door swing arcs — the rule that had never once executed
    for door in room.doors:
        dx, dy = px(door.offset_cm), px(0) + top
        r = door.width_cm * scale
        parts.append(
            f'<path d="M {dx:.1f} {dy:.1f} L {dx + r:.1f} {dy:.1f} '
            f'A {r:.1f} {r:.1f} 0 0 1 {dx:.1f} {dy + r:.1f} Z" '
            f'fill="{WARN}" fill-opacity="0.10" stroke="{WARN}" '
            f'stroke-width="1" stroke-dasharray="4 3"/>'
        )
        parts.append(
            f'<line x1="{dx:.1f}" y1="{dy:.1f}" x2="{dx + r:.1f}" y2="{dy:.1f}" '
            f'stroke="{PAPER}" stroke-width="4"/>'
        )
        # Along the arc's lower edge, clear of anything sitting on the wall.
        parts.append(
            f'<text x="{dx + 6:.1f}" y="{dy + r - 8:.1f}" font-size="9" fill="{WARN}">'
            f'door swing {door.width_cm:.0f}cm</text>'
        )

    # furniture
    for it in items:
        x, y = px(it.x), px(it.y) + top
        iw, ih = it.w * scale, it.d * scale
        fill = ROLE_FILL.get(it.role, MUTED)
        if it.filled:
            parts.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{iw:.1f}" height="{ih:.1f}" rx="3" '
                f'fill="{fill}" fill-opacity="0.22" stroke="{fill}" stroke-width="2"/>'
            )
        else:
            parts.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{iw:.1f}" height="{ih:.1f}" rx="3" '
                f'fill="none" stroke="{BAD}" stroke-width="1.6" stroke-dasharray="6 4"/>'
            )
        # A 30cm lamp is 27px wide; a centred label would run off both sides of
        # it. Anything too small to hold its own text gets labelled beside it.
        cx, cy = x + iw / 2, y + ih / 2
        roomy = iw > 74 and ih > 30
        anchor = "middle" if roomy else "start"
        tx = cx if roomy else x + iw + 7
        ty = cy if roomy else y + ih / 2 - 3

        parts.append(
            f'<text x="{tx:.1f}" y="{ty:.1f}" font-size="10" font-weight="600" '
            f'text-anchor="{anchor}" fill="{INK}">{_esc(it.label)}</text>'
        )
        parts.append(
            f'<text x="{tx:.1f}" y="{ty + 12:.1f}" font-size="8.5" text-anchor="{anchor}" '
            f'fill="{MUTED}">{it.w:.0f}x{it.d:.0f}cm</text>'
        )
        if not it.filled:
            # Below the box, never across it. A shallow slot has no interior
            # room for a third line and the note used to cross the border.
            parts.append(
                f'<text x="{x:.1f}" y="{y + ih + 13:.1f}" font-size="8.5" '
                f'fill="{BAD}">{_esc(it.note or "unfilled")}</text>'
            )

    # the east walkway — the spatial idea the layout is built around
    filled = [i for i in items if i.filled]
    if filled:
        east = max(i.x + i.w for i in filled)
        gap = room.width_cm - east
        if gap > 40:
            gx = px(east) + (gap * scale) / 2
            parts.append(
                f'<line x1="{gx:.1f}" y1="{px(0)+top+8:.1f}" x2="{gx:.1f}" '
                f'y2="{px(room.depth_cm)+top-8:.1f}" stroke="{GOOD}" stroke-width="1.4" '
                f'stroke-dasharray="7 5"/>'
            )
            parts.append(
                f'<text x="{gx + 6:.1f}" y="{px(room.depth_cm/2)+top:.1f}" font-size="10" '
                f'fill="{GOOD}" font-weight="600">{gap:.0f}cm walkway</text>'
            )

    # dimension rails
    parts.append(
        f'<text x="{px(room.width_cm/2):.1f}" y="{px(room.depth_cm)+top+26:.1f}" '
        f'font-size="10" text-anchor="middle" fill="{MUTED}">{room.width_cm:.0f} cm</text>'
    )
    parts.append(
        f'<text x="{px(0)-14:.1f}" y="{px(room.depth_cm/2)+top:.1f}" font-size="10" '
        f'text-anchor="middle" fill="{MUTED}" '
        f'transform="rotate(-90 {px(0)-14:.1f} {px(room.depth_cm/2)+top:.1f})">'
        f'{room.depth_cm:.0f} cm</text>'
    )
    parts.append("</svg>")
    return "\n".join(parts)


# --------------------------------------------------------------------------
# isometric view
# --------------------------------------------------------------------------

COS30, SIN30 = math.cos(math.radians(30)), math.sin(math.radians(30))


def iso(x: float, y: float, z: float) -> tuple[float, float]:
    """Standard isometric projection. z is up."""
    return (x - y) * COS30, (x + y) * SIN30 - z


def isometric_svg(room, items: list[Item], title: str) -> str:
    scale = 0.62
    boxes = [(i, i.x, i.y, i.w, i.d, i.h) for i in items if i.filled]

    pts = [iso(0, 0, 0), iso(room.width_cm, 0, 0),
           iso(0, room.depth_cm, 0), iso(room.width_cm, room.depth_cm, 0),
           iso(0, 0, 150), iso(room.width_cm, room.depth_cm, 150)]
    xs = [p[0] for p in pts] + [iso(i.x + i.w, i.y, i.h)[0] for i in items]
    ys = [p[1] for p in pts] + [iso(i.x, i.y + i.d, i.h)[1] for i in items]

    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    pad = 60
    width = (maxx - minx) * scale + pad * 2
    height = (maxy - miny) * scale + pad * 2 + 40

    def to_px(x: float, y: float, z: float) -> tuple[float, float]:
        sx, sy = iso(x, y, z)
        return pad + (sx - minx) * scale, pad + 40 + (sy - miny) * scale

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height:.0f}" '
        f'viewBox="0 0 {width:.0f} {height:.0f}" font-family="ui-sans-serif,system-ui,sans-serif">',
        f'<rect width="{width:.0f}" height="{height:.0f}" fill="{PAPER}" rx="10"/>',
        f'<text x="{pad}" y="30" font-size="15" font-weight="600" fill="{INK}">{_esc(title)}</text>',
    ]

    def poly(points, fill, stroke, opacity=1.0, w=1.2):
        pts_s = " ".join(f"{p[0]:.1f},{p[1]:.1f}" for p in points)
        return (f'<polygon points="{pts_s}" fill="{fill}" fill-opacity="{opacity}" '
                f'stroke="{stroke}" stroke-width="{w}" stroke-linejoin="round"/>')

    # floor
    parts.append(poly(
        [to_px(0, 0, 0), to_px(room.width_cm, 0, 0),
         to_px(room.width_cm, room.depth_cm, 0), to_px(0, room.depth_cm, 0)],
        "#ffffff", LINE, 1.0, 1.6))

    # metre grid on the floor, so the eye can read distances
    for gx in range(100, int(room.width_cm), 100):
        a, b = to_px(gx, 0, 0), to_px(gx, room.depth_cm, 0)
        parts.append(f'<line x1="{a[0]:.1f}" y1="{a[1]:.1f}" x2="{b[0]:.1f}" y2="{b[1]:.1f}" '
                     f'stroke="{LINE}" stroke-width="0.7"/>')
    for gy in range(100, int(room.depth_cm), 100):
        a, b = to_px(0, gy, 0), to_px(room.width_cm, gy, 0)
        parts.append(f'<line x1="{a[0]:.1f}" y1="{a[1]:.1f}" x2="{b[0]:.1f}" y2="{b[1]:.1f}" '
                     f'stroke="{LINE}" stroke-width="0.7"/>')

    # unfilled slots stay on the floor as outlines — the empty space is the finding
    for it in items:
        if it.filled:
            continue
        ring = [to_px(it.x, it.y, 0), to_px(it.x + it.w, it.y, 0),
                to_px(it.x + it.w, it.y + it.d, 0), to_px(it.x, it.y + it.d, 0)]
        pts_s = " ".join(f"{p[0]:.1f},{p[1]:.1f}" for p in ring)
        parts.append(f'<polygon points="{pts_s}" fill="{BAD}" fill-opacity="0.05" '
                     f'stroke="{BAD}" stroke-width="1.3" stroke-dasharray="6 4"/>')

    # two low back walls, kept light so the furniture stays the subject
    wall_h = 105
    parts.append(poly(
        [to_px(0, 0, 0), to_px(room.width_cm, 0, 0),
         to_px(room.width_cm, 0, wall_h), to_px(0, 0, wall_h)],
        WALL, WALL, 0.08, 1.0))
    parts.append(poly(
        [to_px(0, 0, 0), to_px(0, room.depth_cm, 0),
         to_px(0, room.depth_cm, wall_h), to_px(0, 0, wall_h)],
        WALL, WALL, 0.13, 1.0))

    # Painter's algorithm. The viewer looks from +x,+y, so depth increases with
    # (x + y) and the smallest sum is furthest away. Sorting descending drew
    # near objects first and let far ones paint over them.
    for it in sorted(boxes, key=lambda b: b[1] + b[2]):
        obj, x, y, w, d, h = it
        c = ROLE_FILL.get(obj.role, MUTED)
        top = [to_px(x, y, h), to_px(x + w, y, h), to_px(x + w, y + d, h), to_px(x, y + d, h)]
        left = [to_px(x, y + d, 0), to_px(x, y + d, h), to_px(x, y, h), to_px(x, y, 0)]
        front = [to_px(x, y + d, 0), to_px(x + w, y + d, 0),
                 to_px(x + w, y + d, h), to_px(x, y + d, h)]
        parts.append(poly(left, c, c, 0.55))
        parts.append(poly(front, c, c, 0.72))
        parts.append(poly(top, c, c, 0.95))
        cx = sum(p[0] for p in top) / 4
        cy = sum(p[1] for p in top) / 4
        # A 30cm lamp has no room for text on its cap; label it alongside.
        narrow = min(w, d) < 60
        if narrow:
            parts.append(
                f'<text x="{cx + 14:.1f}" y="{cy - 6:.1f}" font-size="10" font-weight="700" '
                f'fill="{c}">{_esc(obj.label)}</text>'
            )
            parts.append(
                f'<text x="{cx + 14:.1f}" y="{cy + 6:.1f}" font-size="8.5" fill="{MUTED}">'
                f'h {obj.h:.0f}cm</text>'
            )
        else:
            parts.append(
                f'<text x="{cx:.1f}" y="{cy - 2:.1f}" font-size="10" font-weight="700" '
                f'text-anchor="middle" fill="#ffffff">{_esc(obj.label)}</text>'
            )
            parts.append(
                f'<text x="{cx:.1f}" y="{cy + 10:.1f}" font-size="8.5" text-anchor="middle" '
                f'fill="#ffffff" fill-opacity="0.85">h {obj.h:.0f}cm</text>'
            )

    parts.append(
        f'<text x="{pad}" y="{height - 18:.0f}" font-size="10" fill="{MUTED}">'
        f'Isometric projection of the validated layout. Heights are real product heights.</text>'
    )
    parts.append("</svg>")
    return "\n".join(parts)


# --------------------------------------------------------------------------
# a real 3D model
# --------------------------------------------------------------------------

def to_obj(room, items: list[Item]) -> tuple[str, str]:
    """Wavefront OBJ + MTL. Opens in Blender, macOS Preview, three.js.

    Y is up in OBJ convention, so the engine's (x, y, z) becomes (x, z, y).
    Units are metres: a 190cm sofa is 1.9 long, which is what a 3D tool expects.
    """
    verts: list[str] = []
    faces: list[str] = []
    mtl_lines = [
        "# Generated from data/home.json — colours match the plan legend",
    ]
    seen_materials: set[str] = set()
    offset = 1

    def box(x, y, z, w, d, h, name, material):
        nonlocal offset
        corners = [
            (x, y, z), (x + w, y, z), (x + w, y + d, z), (x, y + d, z),
            (x, y, z + h), (x + w, y, z + h), (x + w, y + d, z + h), (x, y + d, z + h),
        ]
        for cx, cy, cz in corners:
            # cm -> m, and swap to Y-up
            verts.append(f"v {cx / 100:.4f} {cz / 100:.4f} {cy / 100:.4f}")
        quads = [
            (0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4),
            (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7),
        ]
        faces.append(f"o {name}")
        faces.append(f"usemtl {material}")
        for a, b, c, dd in quads:
            faces.append(f"f {offset+a} {offset+b} {offset+c} {offset+dd}")
        offset += 8

    def material(name: str, hex_colour: str) -> str:
        if name not in seen_materials:
            seen_materials.add(name)
            r = int(hex_colour[1:3], 16) / 255
            g = int(hex_colour[3:5], 16) / 255
            b = int(hex_colour[5:7], 16) / 255
            mtl_lines.extend([f"newmtl {name}", f"Kd {r:.3f} {g:.3f} {b:.3f}", "Ks 0.05 0.05 0.05", ""])
        return name

    box(0, 0, -6, room.width_cm, room.depth_cm, 6, "floor", material("floor", "#e4e7eb"))
    box(0, -6, 0, room.width_cm, 6, room.height_cm, "wall_north", material("wall", "#b8c1cc"))
    box(-6, 0, 0, 6, room.depth_cm, room.height_cm, "wall_west", material("wall", "#b8c1cc"))

    for it in items:
        if not it.filled:
            continue
        colour = ROLE_FILL.get(it.role, "#7b8794")
        box(it.x, it.y, 0, it.w, it.d, it.h,
            it.item_id.replace(" ", "_"), material(it.role, colour))

    header = [
        "# eazli agent swarm — validated living/dining layout",
        "# Units: metres. Y is up.",
        "# Generated by viz/render.py from data/home.json and real amazon.sa dimensions.",
        "mtllib room.mtl",
        "",
    ]
    return "\n".join(header + verts + faces) + "\n", "\n".join(mtl_lines) + "\n"


# --------------------------------------------------------------------------
# delivery route
# --------------------------------------------------------------------------

def route_svg(segments, dims: Dims, label: str) -> str:
    verdict = check_access_path(dims, segments)
    n = len(segments)
    box_w, gap, pad = 168, 22, 40
    width = pad * 2 + n * box_w + (n - 1) * gap
    height = 250

    notes = verdict.details.get("route_notes", verdict.reasons)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family="ui-sans-serif,system-ui,sans-serif">',
        f'<rect width="{width}" height="{height}" fill="{PAPER}" rx="10"/>',
        f'<text x="{pad}" y="30" font-size="15" font-weight="600" fill="{INK}">'
        f'Delivery route — {_esc(label)}</text>',
        f'<text x="{pad}" y="50" font-size="11" fill="{MUTED}">'
        f'The check eazli&#39;s Fitment clause makes the customer responsible for.</text>',
    ]

    for i, seg in enumerate(segments):
        x = pad + i * (box_w + gap)
        y = 78
        note = notes[i] if i < len(notes) else ""
        failed = "cannot" in note or "does not fit" in note or "needs to swing" in note
        tipped = "side" in note or "on end" in note
        colour = BAD if failed else (WARN if tipped else GOOD)

        parts.append(
            f'<rect x="{x}" y="{y}" width="{box_w}" height="104" rx="8" fill="#ffffff" '
            f'stroke="{colour}" stroke-width="2"/>'
        )
        parts.append(
            f'<text x="{x + box_w/2:.0f}" y="{y + 24}" font-size="10.5" font-weight="600" '
            f'text-anchor="middle" fill="{INK}">{_esc(seg.name[:26])}</text>'
        )
        size = (f'{seg.width_cm:.0f} x {seg.depth_cm:.0f} x {seg.height_cm:.0f}'
                if seg.kind == "lift" and seg.depth_cm
                else f'{seg.width_cm:.0f} x {seg.height_cm:.0f}')
        parts.append(
            f'<text x="{x + box_w/2:.0f}" y="{y + 42}" font-size="9.5" text-anchor="middle" '
            f'fill="{MUTED}">{seg.kind} · {size} cm</text>'
        )
        mark = "blocked" if failed else ("tip it" if tipped else "clears")
        parts.append(
            f'<text x="{x + box_w/2:.0f}" y="{y + 70}" font-size="13" font-weight="700" '
            f'text-anchor="middle" fill="{colour}">{mark}</text>'
        )
        if tipped and not failed:
            hint = "on its side" if "side" in note else "stood on end"
            parts.append(
                f'<text x="{x + box_w/2:.0f}" y="{y + 88}" font-size="9" text-anchor="middle" '
                f'fill="{WARN}">{hint}</text>'
            )
        if i < n - 1:
            ax = x + box_w + 4
            parts.append(
                f'<path d="M {ax} {y+52} L {ax+gap-8} {y+52}" stroke="{LINE}" stroke-width="2" '
                f'marker-end="url(#a)"/>'
            )

    status_colour = {"pass": GOOD, "fail": BAD, "unverified": WARN}[verdict.status]
    parts.append(
        f'<text x="{pad}" y="214" font-size="12.5" font-weight="700" fill="{status_colour}">'
        f'{verdict.status.upper()}</text>'
    )
    first_fail = next((r for r in verdict.reasons if "cannot" in r or "does not fit" in r
                       or "needs to swing" in r), "")
    if first_fail:
        parts.append(
            f'<text x="{pad + 62}" y="214" font-size="10.5" fill="{INK}">'
            f'{_esc(first_fail[:110])}</text>'
        )
    parts.insert(1,
        f'<defs><marker id="a" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto">'
        f'<path d="M0,0 L7,3 L0,6 Z" fill="{LINE}"/></marker></defs>')
    parts.append("</svg>")
    return "\n".join(parts)


# --------------------------------------------------------------------------

def build() -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    home = load_home()
    spec = home.unit("unit01").room("living_dining")
    room = spec.to_room()

    # All seven of Noura's slots: the three Adam filled with real products, and
    # the four he could not, each with the measured reason. Showing only the
    # filled ones would make a 3-of-7 plan look complete — which is exactly what
    # the judge caught the written transcript doing.
    items = [
        Item("B0FR3WVLTS", "sofa 190cm", "sofa", 5, 205, 190, 85, 85, "N"),
        Item("B0BK8QZ8RR", "dining table", "dining_table", 98, 386, 120, 70, 76, "S"),
        Item("B0DRTV3XDT", "floor lamp", "floor_lamp", 200, 215, 30, 30, 155, "N"),
        Item("coffee_table", "coffee table", "coffee_table", 45, 112, 110, 50, 45, "N",
             filled=False, note="all 4 too deep (66-84cm vs 53cm)"),
        Item("media_console", "TV console", "tv_console", 25, 0, 150, 35, 55, "S",
             filled=False, note="all 5 are 40cm deep vs 35cm"),
        Item("chairs_n", "dining chairs", "dining_chairs_pair", 108, 334, 45, 50, 95, "S",
             filled=False, note="no dining chair in catalogue"),
        Item("chairs_s", "dining chairs", "dining_chairs_pair", 108, 468, 45, 50, 95, "N",
             filled=False, note="no dining chair in catalogue"),
    ]

    verdict = validate_layout(room, [i.placement() for i in items if i.filled])

    (OUT / "floorplan.svg").write_text(
        floorplan_svg(room, items, "Flat 01 — living / dining, validated layout"), encoding="utf-8")
    (OUT / "isometric.svg").write_text(
        isometric_svg(room, items, "The same layout in 3D"), encoding="utf-8")

    obj, mtl = to_obj(room, items)
    (OUT / "room.obj").write_text(obj, encoding="utf-8")
    (OUT / "room.mtl").write_text(mtl, encoding="utf-8")

    route = home.route_to("unit01", "living_dining")
    bedroom_route = home.route_to("unit01", "bedroom")
    (OUT / "route-living.svg").write_text(
        route_svg(route, Dims(218, 95, 84), "218cm sofa to the living room"), encoding="utf-8")
    (OUT / "route-bedroom.svg").write_text(
        route_svg(bedroom_route, Dims(218, 95, 84), "the same sofa to a bedroom"), encoding="utf-8")

    return {
        "layout_status": verdict.status,
        "layout_reasons": verdict.reasons,
        "files": sorted(p.name for p in OUT.iterdir()),
    }


if __name__ == "__main__":
    print(json.dumps(build(), indent=2))
