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

    data/index.json                 units, assumptions, budget tiers, categories
    data/candidates/<category>.json one per category, fetched when a picker opens
    data/plans/<ctx>.json           one per unit+room+style+budget tier
    data/swaps/<digest>.json        one per distinct swap table, named by content

**Swap tables are content-addressed, not context-named.** One file per context
meant 200 files holding 28 distinct payloads — 86% of 27.6 MB was byte-identical
duplication, because most budget tiers currently plan the same room and so offer
the same substitutions. Hashing the serialised table and naming the file after
the digest collapses the copies automatically, and keeps collapsing whatever
fraction is still shared once the tiers diverge.

The plan file that needs a table carries its digest as `swaps_ref`. The page
already fetches the plan before anything can be swapped, so the reference costs
no round trip and needs no index. Plans and flats keep their context-keyed
names: `slug()` below and `planKey()` in studio.js must agree, and only the
swap path is freed from that.

    PYTHONPATH=. uv run python tools/export_static.py
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from app.catalog import parse_capture
from app.home import load_home
from app.planner import (
    BUDGET_TIERS,
    DEFAULT_TIER,
    CATEGORY_FOR_ROLE_FALLBACK,
    RECIPES,
    auto_plan,
    plan_flat,
    swap,
)

SRC = Path("app/static")
OUT = Path("site")
RUNS = Path("docs/agent-runs")
STYLES = [
    ["warm", "minimal"],
    ["modern", "luxury"],
    ["industrial", "mid_century"],
    ["boho", "scandi"],
    [],
]
PLANNABLE = set(RECIPES)


def slug(unit: str, room: str, style: list[str], tier: str) -> str:
    """Filesystem-safe context key.

    The frontend builds the same string; `tests/test_studio.py` asserts both
    spellings match, because nothing else couples them and a silent mismatch
    would make every plan look missing.

    The tier is part of the key because the budget is part of the plan. It was
    not, and the studio shipped a number box that could not change anything a
    visitor was looking at.
    """
    return f"{unit}__{room}__{'-'.join(style) if style else 'any'}__{tier}"


def _write(path: Path, payload) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, separators=(",", ":"))
    path.write_text(text, encoding="utf-8")
    return len(text)


def write_addressed(directory: Path, payload) -> str:
    """Write `payload` under a name derived from its own bytes; return the name.

    Two contexts that produced the same verdicts now share one file instead of
    two copies of ~138 KB. That is most of them: 200 context-named swap tables
    held 28 distinct payloads.

    `sort_keys` and a fixed separator are what make the name a name rather than
    a nonce. Serialise the same table twice with dictionary insertion order and
    the digest moves, every file in the directory is rewritten on every build,
    and the churn costs more than the duplication did.
    """
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    # 12 hex chars is 48 bits. At the scale of a build (hundreds of tables) a
    # collision is not a practical risk, and the shorter name keeps the plan
    # payload and the fetch URL readable.
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    path = directory / f"{digest}.json"
    if not path.exists():
        directory.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return digest


def index_payload(home=None) -> dict:
    """Everything the page needs before it can ask for its first plan.

    The budget tiers ride along here rather than being retyped in studio.js.
    The dropdown and the filenames it then fetches have to agree, and they only
    do while one Python constant spells both.
    """
    home = home if home is not None else load_home()
    return {
        "generated_from": "app/geometry.py via tools/export_static.py",
        "units": [
            {"id": u.id, "label": u.label, "config": u.config,
             "rooms": [r.name for r in u.rooms]}
            for u in home.units
        ],
        "assumptions": home.assumptions,
        "plannable": sorted(PLANNABLE),
        "styles": ["-".join(s) if s else "any" for s in STYLES],
        "budget_tiers": BUDGET_TIERS,
        "default_tier": DEFAULT_TIER,
    }


def build_data(out: Path = OUT, units: list[str] | None = None) -> dict:
    """Write the whole data tree under `out`.

    `units` narrows the build to a subset. Nothing in the site uses it — it
    exists so the test that checks every `swaps_ref` resolves can run the real
    exporter over one unit instead of paying for all three.
    """
    home = load_home()
    catalog = parse_capture()
    data = out / "data"
    wanted = [u for u in home.units if units is None or u.id in units]

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
    plan_bytes = 0
    swap_refs: set[str] = set()

    for unit in wanted:
        for room in unit.rooms:
            if room.name not in PLANNABLE:
                continue
            for style in STYLES:
                for tier in BUDGET_TIERS:
                    ctx = slug(unit.id, room.name, style, tier["id"])
                    plan = auto_plan(unit.id, room.name, tier["sar"], style).to_dict()
                    plan_count += 1

                    # Every substitution the picker can offer for this exact
                    # arrangement, in one file, so opening a picker costs one
                    # fetch rather than shipping all of them up front. A verdict
                    # depends on everything else in the room, so it is never
                    # reused across contexts — but two contexts that planned the
                    # same room produce the same table, and the digest is what
                    # notices.
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
                    swap_count += len(swaps)

                    # The plan carries the digest, so the page needs no index
                    # and no second round trip: it has already fetched the plan
                    # before anything can be swapped. Written after the table,
                    # because the reference has to exist before it is named.
                    ref = write_addressed(data / "swaps", swaps)
                    swap_refs.add(ref)
                    plan["swaps_ref"] = ref
                    plan_bytes += _write(data / "plans" / f"{ctx}.json", plan)

    # Whole-flat plans: one per unit per style per tier, budget split by area.
    # No `swaps_ref` here — each room in a flat is planned against a share of
    # the budget rather than a tier, so no swap table was ever computed for one.
    # The picker on a whole-flat plan says `unverified`, which is true.
    flat_count = 0
    for unit in wanted:
        for style in STYLES:
            for tier in BUDGET_TIERS:
                payload = plan_flat(unit.id, tier["sar"], style)
                if payload["rooms"]:
                    _write(data / "flats" / f"{slug(unit.id, 'flat', style, tier['id'])}.json",
                           payload)
                    flat_count += 1

    _write(data / "index.json", index_payload(home))

    swap_bytes = sum((data / "swaps" / f"{d}.json").stat().st_size for d in swap_refs)
    return {"plans": plan_count, "swaps": swap_count, "flats": flat_count,
            "swap_files": len(swap_refs),
            "plan_kb": plan_bytes / 1024, "swap_kb": swap_bytes / 1024}


def minify_css() -> None:
    """Ship studio.css the way studio.js is already shipped.

    It was `shutil.copy`, so every comment in the stylesheet was a comment the
    visitor downloaded before the page could paint — and that sheet is heavily
    commented on purpose. At 22 KB source it was 8 KB of the first paint budget
    spent on prose for whoever edits it next. Minified it is 14 KB and the
    comments cost nothing. Same esbuild that bundles the JS; no new dependency.
    """
    result = subprocess.run(
        ["npx", "--yes", "esbuild@0.24.0", str(SRC / "studio.css"),
         "--minify", f"--outfile={OUT / 'studio.css'}"],
        capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"esbuild failed on studio.css:\n{result.stderr}")


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


def eager_chunks() -> set[Path]:
    """The chunks the browser must have before studio.js can run.

    `--splitting` emits two kinds of chunk and the number that matters treats
    them completely differently. `import('./viewer.js')` and
    `import('./brief.js')` produce chunks nothing fetches until the user asks —
    those are free. Code shared between the entry and a lazy chunk (palette.js,
    text.js) is hoisted into a chunk the ENTRY imports statically, and the
    browser blocks on it exactly as if it were still inline.

    Counting only studio.js therefore understates first paint by however much
    got hoisted, and understates it MORE every time something is split out —
    which would turn the one number this build reports into a way of making
    deferral look better than it is. Read off the entry's own import
    statements, followed transitively, so the figure cannot be gamed by moving
    code around.
    """
    found: set[Path] = set()
    queue = [OUT / "studio.js"]
    while queue:
        src = queue.pop()
        for name in re.findall(r'from\s*"\./(chunk-[^"]+\.js)"',
                               src.read_text(encoding="utf-8")):
            chunk = OUT / name
            if chunk.exists() and chunk not in found:
                found.add(chunk)
                queue.append(chunk)
    return found


def publish_trace() -> int:
    """Publish /trace: the agent-trace viewer and the run files it reads.

    The viewer is a separate page. It is deliberately *not* part of the studio
    bundle — no import, no shared stylesheet — so none of it is charged to the
    first-paint number printed below.

    The index is globbed, not listed. Dropping another `eazli.agent-trace/v1`
    file into docs/agent-runs/ publishes it and puts it in the page's picker
    with no edit here and none in trace.js; only the four keys the picker
    labels an option with are read, so a schema that grows still exports.
    """
    dest = OUT / "data" / "agent-runs"
    dest.mkdir(parents=True, exist_ok=True)
    runs = []
    for path in sorted(RUNS.glob("*.json")):
        if path.name == "index.json":
            continue
        run = json.loads(path.read_text(encoding="utf-8"))
        shutil.copy(path, dest / path.name)
        runs.append({
            "file": path.name,
            "run_id": run.get("run_id"),
            "recorded": run.get("recorded"),
            "provenance_kind": run.get("provenance_kind", "recorded"),
        })
    _write(dest / "index.json", {"runs": runs})

    html = (SRC / "trace.html").read_text(encoding="utf-8")
    for a, b in (('href="/static/trace.css"', 'href="./trace.css"'),
                 ('src="/static/trace.js"', 'src="./trace.js"'),
                 ('href="/static/index.html"', 'href="./index.html"')):
        html = html.replace(a, b)
    (OUT / "trace.html").write_text(html, encoding="utf-8")
    shutil.copy(SRC / "trace.css", OUT / "trace.css")
    shutil.copy(SRC / "trace.js", OUT / "trace.js")
    return len(runs)


@contextlib.contextmanager
def _only_one_build_at_a_time():
    """A lock file beside the output, removed on the way out.

    Not a mutex across machines and not trying to be — this exists because
    several agents ran the exporter at once on one checkout.
    """
    lock = OUT.parent / ".export_static.lock"
    fd = None
    for attempt in range(600):                      # up to ten minutes
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            if attempt == 0:
                print("another export is running; waiting for it to finish...",
                      flush=True)
            time.sleep(1)
    if fd is None:
        raise SystemExit(f"{lock} held for ten minutes. If no build is running, "
                         f"delete it.")
    try:
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        yield
    finally:
        lock.unlink(missing_ok=True)


def main() -> None:
    # One build at a time. `rmtree` then rebuild is fine alone and lethal in
    # parallel: two concurrent runs delete each other's files mid-write, and
    # the failures do not look like a race — they look like a missing page.
    # Three separate builds died on FileNotFoundError / "Directory not empty"
    # during one session, and a served site 404'd on a page that was in the
    # tree a second earlier. Cheap to prevent, expensive to diagnose.
    with _only_one_build_at_a_time():
        if OUT.exists():
            shutil.rmtree(OUT)
        OUT.mkdir(parents=True)

        stats = build_data()
        minify_css()
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
        html = html.replace('href="/static/trace.html"', 'href="./trace.html"')
        html = html.replace(
            '<span class="sub">plan a room, swap anything, every verdict from the Python engine</span>',
            '<span class="sub">static build — every verdict precomputed by app/geometry.py, '
            'not recalculated in the browser</span>')
        (OUT / "index.html").write_text(html, encoding="utf-8")
        (OUT / ".nojekyll").write_text("", encoding="utf-8")
        n_runs = publish_trace()

        eager = eager_chunks()
        app_bytes = (OUT / "studio.js").stat().st_size + sum(c.stat().st_size for c in eager)
        app_kb = app_bytes / 1024
        total = sum(f.stat().st_size for f in OUT.rglob("*") if f.is_file()) / 1024
        first_paint = (
            (OUT / "index.html").stat().st_size
            + app_bytes
            + (OUT / "studio.css").stat().st_size
            + (OUT / "data" / "index.json").stat().st_size
            + (OUT / "data" / "plans"
               / f"{slug('unit01', 'living_dining', ['warm', 'minimal'], DEFAULT_TIER)}.json"
               ).stat().st_size
        ) / 1024

        print(f"plans        {stats['plans']:>5}   {stats['plan_kb']:>8.0f} KB")
        print(f"whole flats  {stats['flats']:>5}")
        print(f"swaps        {stats['swaps']:>5}   {stats['swap_kb']:>8.0f} KB  "
              f"in {stats['swap_files']} content-addressed files "
              f"for {stats['plans']} contexts, fetched on demand")
        print(f"studio.js (entry)      {app_kb:>8.0f} KB  {esbuild_note}")
        for c in sorted(OUT.glob("chunk-*.js")):
            how = ("blocking: imported by the entry" if c in eager
                   else "lazy: fetched on demand")
            print(f"  {c.name:<20} {c.stat().st_size / 1024:>8.0f} KB  {how}")
        trace_kb = sum(
            (OUT / n).stat().st_size for n in ("trace.html", "trace.js", "trace.css")
        ) / 1024
        print(f"site total             {total:>8.0f} KB")
        print(f"/trace                 {trace_kb:>8.0f} KB  {n_runs} run file(s), separate page, "
              f"not in the studio's first paint")
        print(f"FIRST PAINT            {first_paint:>8.0f} KB  (html + js + css + index + one plan)")


if __name__ == "__main__":
    main()
