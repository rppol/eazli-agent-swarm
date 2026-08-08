"""Build a static, backend-free copy of the studio for GitHub Pages.

GitHub Pages serves files. It cannot run `app/geometry.py`, and the whole claim
of this project is that Python decides. So rather than reimplement the rules in
JavaScript — which would quietly create a second, unverified engine — this
precomputes every verdict the static UI can reach and ships them as data.

What the static build is: a faithful replay of real engine output.
What it is not: a working verifier. Any combination outside the bundle reports
`unverified` and points at the local build, which does run the engine live.

    PYTHONPATH=. uv run python tools/export_static.py
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from app.catalog import parse_capture
from app.home import load_home
from app.main import PLANS  # noqa: F401  (kept so the import graph mirrors the app)
from app.planner import CATEGORY_FOR_ROLE_FALLBACK, RECIPES, auto_plan, swap

SRC = Path("app/static")
OUT = Path("site")
STYLES = [
    ["warm", "minimal"],
    ["modern", "luxury"],
    ["industrial", "mid_century"],
    ["boho", "scandi"],
    [],
]
PLANNABLE = set(RECIPES)


def build() -> dict:
    home = load_home()
    catalog = parse_capture()

    units_payload = {
        "units": [
            {"id": u.id, "label": u.label, "config": u.config,
             "rooms": [r.name for r in u.rooms]}
            for u in home.units
        ],
        "assumptions": home.assumptions,
    }

    categories = sorted({p.category for p in catalog})
    candidates = {}
    for cat in categories:
        items = [
            {"asin": p.asin, "title": p.title, "url": p.url,
             "price_sar": p.price_sar, "rating": p.rating, "reviews": p.reviews,
             "dims_cm": {"w": p.dims.w, "d": p.dims.d, "h": p.dims.h},
             "dims_confidence": p.dims_confidence, "style": p.style_tags,
             "flags": p.flags, "usable": p.usable, "flat_pack": p.flat_pack}
            for p in catalog if p.category == cat
        ]
        items.sort(key=lambda i: (not i["usable"], i["price_sar"] or 1e9))
        candidates[cat] = {"category": cat, "count": len(items), "items": items}

    plans: dict[str, dict] = {}
    swaps: dict[str, dict] = {}

    for unit in home.units:
        for room in unit.rooms:
            if room.name not in PLANNABLE:
                continue
            for style in STYLES:
                plan = auto_plan(unit.id, room.name, 8000, style).to_dict()
                plans[f"{unit.id}|{room.name}|{','.join(style)}"] = plan

                # Precompute the result of substituting each candidate into each
                # slot, leaving everything else where the planner put it. That is
                # exactly the interaction the UI offers, so it is exactly the set
                # worth computing.
                for placed in plan["placed"]:
                    cat = CATEGORY_FOR_ROLE_FALLBACK.get(placed["role"])
                    for cand in candidates.get(cat, {}).get("items", []):
                        if not cand["usable"]:
                            continue
                        # Keyed by the whole context, not just the item: a
                        # swap verdict depends on everything else in the room,
                        # which differs per plan and per style. Deduping on
                        # (slot, asin) alone would serve one plan's verdict for
                        # another plan's arrangement.
                        sig = (f"{unit.id}|{room.name}|{','.join(style)}"
                               f"|{placed['slot_id']}|{cand['asin']}")
                        if sig in swaps:
                            continue
                        trial = [
                            {**p, "asin": cand["asin"], "title": cand["title"],
                             "price_sar": cand["price_sar"] or 0,
                             "dims_cm": cand["dims_cm"],
                             "dims_confidence": cand["dims_confidence"]}
                            if p["slot_id"] == placed["slot_id"] else p
                            for p in plan["placed"]
                        ]
                        swaps[sig] = swap(unit.id, room.name, trial)

    return {
        "generated_from": "app/geometry.py via tools/export_static.py",
        "units": units_payload,
        "candidates": candidates,
        "plans": plans,
        "swaps": swaps,
    }


def main() -> None:
    bundle = build()

    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    shutil.copytree(SRC / "vendor", OUT / "vendor")
    shutil.copy(SRC / "studio.css", OUT / "studio.css")
    shutil.copy(SRC / "studio.js", OUT / "studio.js")

    (OUT / "bundle.js").write_text(
        "window.__STATIC_BUNDLE = " + json.dumps(bundle, separators=(",", ":")) + ";\n",
        encoding="utf-8",
    )

    # Same markup, relative paths, bundle loaded before the module.
    html = (SRC / "index.html").read_text(encoding="utf-8")
    html = html.replace('"/static/', '"./').replace('"/static/vendor/', '"./vendor/')
    html = html.replace(
        '<script type="module" src="./studio.js"></script>',
        '<script src="./bundle.js"></script>\n'
        '<script type="module" src="./studio.js"></script>',
    )
    html = html.replace(
        '<span class="sub">plan a room, swap anything, every verdict from the Python engine</span>',
        '<span class="sub">static build — every verdict precomputed by app/geometry.py, '
        'not recalculated in the browser</span>',
    )
    (OUT / "index.html").write_text(html, encoding="utf-8")
    (OUT / ".nojekyll").write_text("", encoding="utf-8")

    size = sum(f.stat().st_size for f in OUT.rglob("*") if f.is_file())
    print(f"plans      {len(bundle['plans']):>6}")
    print(f"swaps      {len(bundle['swaps']):>6}")
    print(f"categories {len(bundle['candidates']):>6}")
    print(f"site/      {size / 1_048_576:.1f} MB")


if __name__ == "__main__":
    main()
