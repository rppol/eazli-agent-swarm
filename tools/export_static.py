"""Build a static, backend-free copy of the studio for GitHub Pages.

GitHub Pages serves files. It cannot run `app/geometry.py`, and the whole claim
of this project is that Python decides. So rather than reimplement the rules in
JavaScript — which would quietly create a second, unverified engine — this runs
the real engine and ships its verdicts as data.

What the static build is: a faithful replay of real engine output.
What it is not: a working verifier. Any combination outside the bundle reports
`unverified` and points at the local build, which does run the engine live.

**Everything expensive happens here, in CI.** The page itself does nothing but
draw. The data is split so the first paint fetches one plan (~6 KB) instead of
all fifty plus 1,075 swap verdicts (2.5 MB to parse, 90% of it for interactions
the visitor may never make).

    data/index.json                 units, assumptions, category list
    data/candidates/<category>.json one per category, fetched when a picker opens
    data/plans/<ctx>.json           one per unit+room+style
    data/swaps/<ctx>.json           every swap verdict for that context, on demand

    PYTHONPATH=. uv run python tools/export_static.py
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from app.catalog import parse_capture
from app.home import load_home
from app.planner import CATEGORY_FOR_ROLE_FALLBACK, RECIPES, auto_plan, plan_flat, swap

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


def slug(unit: str, room: str, style: list[str]) -> str:
    """Filesystem-safe context key.

    The frontend builds the same string; `tests/test_studio.py` asserts both
    spellings match, because nothing else couples them and a silent mismatch
    would make every plan look missing.
    """
    return f"{unit}__{room}__{'-'.join(style) if style else 'any'}"


def _write(path: Path, payload) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, separators=(",", ":"))
    path.write_text(text, encoding="utf-8")
    return len(text)


def build_data() -> dict:
    home = load_home()
    catalog = parse_capture()
    data = OUT / "data"

    categories = sorted({p.category for p in catalog})
    by_category: dict[str, list[dict]] = {}
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
        by_category[cat] = items
        _write(data / "candidates" / f"{cat}.json",
               {"category": cat, "count": len(items), "items": items})

    plan_count = swap_count = 0
    plan_bytes = swap_bytes = 0

    for unit in home.units:
        for room in unit.rooms:
            if room.name not in PLANNABLE:
                continue
            for style in STYLES:
                ctx = slug(unit.id, room.name, style)
                plan = auto_plan(unit.id, room.name, 8000, style).to_dict()
                plan_bytes += _write(data / "plans" / f"{ctx}.json", plan)
                plan_count += 1

                # Every substitution the picker can offer for this exact
                # arrangement. Keyed within the context file, so opening a
                # picker costs one fetch rather than shipping all 1,075 up
                # front — and a verdict is never reused across contexts, since
                # it depends on everything else in the room.
                swaps: dict[str, dict] = {}
                for placed in plan["placed"]:
                    cat = CATEGORY_FOR_ROLE_FALLBACK.get(placed["role"])
                    # Held in memory rather than read back from what we just
                    # wrote: fewer syscalls, and one fewer thing to go wrong.
                    for cand in by_category.get(cat, []):
                        if not cand["usable"]:
                            continue
                        trial = [
                            {**p, "asin": cand["asin"], "title": cand["title"],
                             "price_sar": cand["price_sar"] or 0,
                             "dims_cm": cand["dims_cm"],
                             "dims_confidence": cand["dims_confidence"]}
                            if p["slot_id"] == placed["slot_id"] else p
                            for p in plan["placed"]
                        ]
                        swaps[f"{placed['slot_id']}|{cand['asin']}"] = swap(
                            unit.id, room.name, trial)
                swap_bytes += _write(data / "swaps" / f"{ctx}.json", swaps)
                swap_count += len(swaps)

    # Whole-flat plans: one per unit per style, same budget split by area.
    flat_count = 0
    for unit in home.units:
        for style in STYLES:
            payload = plan_flat(unit.id, 8000, style)
            if payload["rooms"]:
                _write(data / "flats" / f"{slug(unit.id, 'flat', style)}.json", payload)
                flat_count += 1

    _write(data / "index.json", {
        "generated_from": "app/geometry.py via tools/export_static.py",
        "units": [
            {"id": u.id, "label": u.label, "config": u.config,
             "rooms": [r.name for r in u.rooms]}
            for u in home.units
        ],
        "assumptions": home.assumptions,
        "plannable": sorted(PLANNABLE),
        "styles": ["-".join(s) if s else "any" for s in STYLES],
    })

    return {"plans": plan_count, "swaps": swap_count, "flats": flat_count,
            "plan_kb": plan_bytes / 1024, "swap_kb": swap_bytes / 1024}


def bundle_js() -> str:
    """Tree-shake and minify three.js + the studio into one file.

    The vendored three.module.js is 1.27 MB (257 KB gzipped) and the studio
    uses a small fraction of it. esbuild resolves the bare `three` specifier to
    the vendored copy, so nothing is fetched from a CDN at build time either.
    """
    cmd = [
        "npx", "--yes", "esbuild@0.24.0", str(SRC / "studio.js"),
        "--bundle", "--minify", "--format=esm", "--target=es2022",
        # --splitting emits the dynamically-imported viewer (and three.js with
        # it) as a separate chunk, so first paint does not wait on 490 KB.
        "--splitting", "--chunk-names=chunk-[hash]",
        # Absolute paths: a bare "app/static/..." is read as a package name.
        f"--alias:three={(SRC / 'vendor' / 'three.module.js').resolve()}",
        f"--alias:three/addons/controls/OrbitControls.js="
        f"{(SRC / 'vendor' / 'OrbitControls.js').resolve()}",
        f"--outdir={OUT}",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"esbuild failed:\n{result.stderr}")
    return result.stderr.strip().splitlines()[-1] if result.stderr else ""


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)

    stats = build_data()
    shutil.copy(SRC / "studio.css", OUT / "studio.css")
    esbuild_note = bundle_js()

    # One bundled module, no import map, relative paths.
    html = (SRC / "index.html").read_text(encoding="utf-8")
    html = html.replace('<link rel="stylesheet" href="/static/studio.css">',
                        '<link rel="stylesheet" href="./studio.css">')
    start = html.index('<script type="importmap">')
    end = html.index("</script>", start) + len("</script>")
    html = html[:start].rstrip() + "\n" + html[end:].lstrip()
    html = html.replace('<script type="module" src="/static/studio.js"></script>',
                        '<script type="module" src="./studio.js"></script>')
    html = html.replace('<html lang="en">', '<html lang="en" data-mode="static">')
    html = html.replace(
        '<span class="sub">plan a room, swap anything, every verdict from the Python engine</span>',
        '<span class="sub">static build — every verdict precomputed by app/geometry.py, '
        'not recalculated in the browser</span>')
    (OUT / "index.html").write_text(html, encoding="utf-8")
    (OUT / ".nojekyll").write_text("", encoding="utf-8")

    app_kb = (OUT / "studio.js").stat().st_size / 1024
    total = sum(f.stat().st_size for f in OUT.rglob("*") if f.is_file()) / 1024
    first_paint = (
        (OUT / "index.html").stat().st_size
        + (OUT / "studio.js").stat().st_size
        + (OUT / "studio.css").stat().st_size
        + (OUT / "data" / "index.json").stat().st_size
        + (OUT / "data" / "plans" / "unit01__living_dining__warm-minimal.json").stat().st_size
    ) / 1024

    print(f"plans        {stats['plans']:>5}   {stats['plan_kb']:>8.0f} KB")
    print(f"whole flats  {stats['flats']:>5}")
    print(f"swaps        {stats['swaps']:>5}   {stats['swap_kb']:>8.0f} KB  (fetched per context)")
    chunks = sorted(OUT.glob("chunk-*.js"))
    print(f"studio.js (entry)      {app_kb:>8.0f} KB  {esbuild_note}")
    for c in chunks:
        print(f"  {c.name:<20} {c.stat().st_size / 1024:>8.0f} KB  lazy: three.js + viewer")
    print(f"site total             {total:>8.0f} KB")
    print(f"FIRST PAINT            {first_paint:>8.0f} KB  (html + js + css + index + one plan)")


if __name__ == "__main__":
    main()
