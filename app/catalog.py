"""Turn raw amazon.sa captures into a catalog the swarm can reason about.

Real marketplace data is not clean, and pretending otherwise is how a fit
checker ends up confidently wrong. Observed in this capture alone:

  * units drift between metres, centimetres, millimetres, inches and mm
  * the same listing disagrees with itself across dimension fields
  * the bare "Item Dimensions" field has no consistent axis order
  * axis labels are occasionally wrong outright (a floor lamp with D=154cm)
  * physically impossible values (a wingback armchair listed at 30cm wide)
  * search results contain other categories entirely (coffee machines)

Rather than smooth any of that over, the parser records what it could and could
not establish. `dims_confidence` and `flags` travel with every product so that
downstream agents can be honest about what they actually know.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.geometry import Dims

TO_CM = {
    "meter": 100.0, "metre": 100.0, "meters": 100.0, "metres": 100.0, "m": 100.0,
    "centimeter": 1.0, "centimetre": 1.0, "centimeters": 1.0, "centimetres": 1.0, "cm": 1.0,
    "millimeter": 0.1, "millimetre": 0.1, "millimeters": 0.1, "millimetres": 0.1, "mm": 0.1,
    "inch": 2.54, "inches": 2.54, "in": 2.54, '"': 2.54,
    "foot": 30.48, "feet": 30.48, "ft": 30.48,
}

# Plausible bounding-box extent (cm) for the largest dimension of each category.
# Deliberately generous: this is a nonsense detector, not a style guide.
PLAUSIBLE_MAX_EXTENT = {
    "sofa": (120, 400),
    "armchair": (55, 140),
    "coffee_table": (50, 180),
    "dining_table": (60, 300),
    "tv_unit": (50, 300),
    "wardrobe": (60, 300),
    "bed": (90, 260),
    "bookshelf": (40, 250),
    "rug": (60, 500),
    "floor_lamp": (30, 220),
}

# Title keywords that identify what a listing actually is. Checked in order, so
# the more specific patterns must come first ("coffee machine" before "table").
CATEGORY_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("appliance", re.compile(r"coffee machine|espresso|blender|kettle|air fryer|microwave", re.I)),
    ("accessory", re.compile(r"table runner|cheesecloth|tablecloth|placemat|cushion cover|wall mount|mount stand|monitor light|screenbar|rolling cart", re.I)),
    ("rug", re.compile(r"\brugs?\b|carpet", re.I)),
    ("floor_lamp", re.compile(r"floor lamp|uplighter|standing lamp|uplight", re.I)),
    ("bookshelf", re.compile(r"bookshel|bookcase|etagere|display shelf", re.I)),
    ("wardrobe", re.compile(r"wardrobe|closet|armoire", re.I)),
    ("bed", re.compile(r"bed frame|platform bed|bedstead", re.I)),
    ("tv_unit", re.compile(r"tv stand|tv unit|tv table|entertainment cent|television console", re.I)),
    ("coffee_table", re.compile(r"coffee table|center table|centre table", re.I)),
    ("dining_table", re.compile(r"dining table|dinner table|kitchen table|dining round", re.I)),
    # Armchair before sofa: amazon.sa sellers routinely write "Single Sofa" in
    # accent-chair titles, so the narrower match has to be tried first.
    ("armchair", re.compile(r"armchair|accent chair|recliner|lounge chair|wingback", re.I)),
    ("sofa", re.compile(r"\bsofa\b|settee|couch|loveseat", re.I)),
]

STYLE_PATTERNS = {
    "modern": r"modern|contemporary",
    "minimal": r"minimal|simple|clean",
    "industrial": r"industrial|metal frame|iron",
    "boho": r"boho|bohemian|oushak",
    "mid_century": r"mid-century|mid century",
    "luxury": r"luxury|premium|marble|ceramic",
    "warm": r"wood|wooden|bamboo|beech|linen|bouclé|boucle|velvet|beige|greige",
    "scandi": r"scandi|farmhouse|slatted",
}

ROOM_BY_CATEGORY = {
    "sofa": ["living_dining"],
    "armchair": ["living_dining", "bedroom"],
    "coffee_table": ["living_dining"],
    "dining_table": ["living_dining"],
    "tv_unit": ["living_dining"],
    "rug": ["living_dining", "bedroom"],
    "floor_lamp": ["living_dining", "bedroom"],
    "bookshelf": ["living_dining", "bedroom"],
    "wardrobe": ["bedroom"],
    "bed": ["bedroom"],
}


@dataclass
class Product:
    asin: str
    title: str
    category: str
    search_category: str
    price_sar: float | None
    dims: Dims
    dims_confidence: str
    dims_source: str
    carton: Dims | None
    flat_pack: bool
    rating: float | None
    reviews: int
    style_tags: list[str]
    rooms: list[str]
    flags: list[str] = field(default_factory=list)

    @property
    def url(self) -> str:
        return f"https://www.amazon.sa/-/en/dp/{self.asin}"

    @property
    def usable(self) -> bool:
        """Safe to recommend as a confirmed fit."""
        return (
            self.dims.known
            and self.dims_confidence in {"stated", "parsed"}
            and "implausible_for_category" not in self.flags
            and "category_mismatch" not in self.flags
        )

    def to_metadata(self) -> dict:
        return {
            "asin": self.asin,
            "title": self.title,
            "url": self.url,
            "category": self.category,
            "price_sar": self.price_sar if self.price_sar is not None else -1.0,
            "width_cm": self.dims.w or 0.0,
            "depth_cm": self.dims.d or 0.0,
            "height_cm": self.dims.h or 0.0,
            "dims_confidence": self.dims_confidence,
            "dims_source": self.dims_source,
            "carton_w_cm": (self.carton.w if self.carton else 0.0) or 0.0,
            "carton_d_cm": (self.carton.d if self.carton else 0.0) or 0.0,
            "carton_h_cm": (self.carton.h if self.carton else 0.0) or 0.0,
            "flat_pack": self.flat_pack,
            "rating": self.rating or 0.0,
            "reviews": self.reviews,
            "room": ",".join(self.rooms),
            # One boolean per room as well as the joined string. Chroma's
            # `where` is exact equality, so filtering on the joined value made
            # every dual-room product — all eight floor lamps, every rug —
            # invisible to a single-room search.
            **{f"room_{r}": True for r in self.rooms},
            "style": ",".join(self.style_tags),
            "flags": ",".join(self.flags),
            "usable": self.usable,
        }


# --------------------------------------------------------------------------
# field-level parsing
# --------------------------------------------------------------------------

def _price(raw: str) -> float | None:
    digits = re.sub(r"[^0-9.]", "", raw or "")
    try:
        return round(float(digits), 2) if digits else None
    except ValueError:
        return None


def _unit_factor(text: str) -> float:
    """Trailing unit for a dimension string. Centimetres when unstated —
    the overwhelming majority of amazon.sa furniture listings use cm."""
    match = re.search(r"([a-zA-Z\"]+)\s*$", text.strip())
    if not match:
        return 1.0
    return TO_CM.get(match.group(1).lower(), 1.0)


def _labelled(text: str) -> dict[str, float] | None:
    """Parse an axis-labelled string like '104.5D x 170W x 82.5H centimeters'.

    Axis labels make the reading unambiguous, which is why this form is
    preferred over the bare field even when both are present.
    """
    pairs = re.findall(r"([\d.]+)\s*([DWHL])\b", text, re.I)
    if len(pairs) < 2:
        return None
    # Split on ';' before reading the unit, exactly as _bare does. Without it,
    # "24D x 60W x 80H inches; 55 kg" matches "kg", finds no conversion and
    # silently falls back to 1.0 — recording a 203cm wardrobe as 80cm tall and
    # marking it 'stated'. Metres are usually rescued by the plausibility
    # bounds; a 2.54x shrink from inches lands inside them.
    factor = _unit_factor(text.split(";")[0])
    out: dict[str, float] = {}
    for value, axis in pairs:
        out[axis.upper()] = round(float(value) * factor, 1)
    return out


def _bare(text: str) -> list[float] | None:
    """Parse an unlabelled string like '101.6 x 168.9 x 81.2 centimeters'.

    The old lookahead required a delimiter after the digits, so '120x60x45cm'
    silently lost the 45 and the padding logic then made a coffee table 2cm
    tall. Match the numbers directly instead.
    """
    head = text.split(";")[0]
    values = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", head)][:3]
    if len(values) < 2:
        return None
    factor = _unit_factor(head)
    return [round(v * factor, 1) for v in values]


def _from_labelled(axes: dict[str, float]) -> tuple[float, float, float] | None:
    """Map axis letters to (w, d, h), or None if a plan axis is missing.

    This used to fall back to 0.0 for an absent axis. Zero is not None, so
    `Dims.known` was True, confidence stayed 'stated', nothing was flagged, and
    a sofa with no depth cleared every door on the route. An axis we did not
    read is an axis we do not know.
    """
    if "W" in axes and "L" in axes and "D" not in axes:
        # 'L x W' goods such as rugs: long side is the width, the other the depth.
        w, d = max(axes["L"], axes["W"]), min(axes["L"], axes["W"])
    else:
        w = axes.get("W", axes.get("L"))
        d = axes.get("D", axes.get("L") if "W" in axes else None)

    if w is None or d is None:
        return None

    # Height is the one axis a flat good legitimately omits.
    h = axes.get("H") or 2.0
    return w, d, h


# --------------------------------------------------------------------------
# item-level parsing
# --------------------------------------------------------------------------

def _classify(title: str, search_category: str) -> tuple[str, list[str]]:
    for name, pattern in CATEGORY_PATTERNS:
        if pattern.search(title):
            flags = ["category_mismatch"] if name != search_category else []
            return name, flags
    return search_category, ["unclassified"]


def _plausibility_flags(category: str, dims: Dims) -> list[str]:
    if not dims.known:
        return []
    bounds = PLAUSIBLE_MAX_EXTENT.get(category)
    if not bounds:
        return []
    low, high = bounds
    extent = max(dims.w or 0, dims.d or 0, dims.h or 0)
    return ["implausible_for_category"] if not (low <= extent <= high) else []


def _extract_dims(attrs: dict) -> tuple[Dims, str, str, list[str]]:
    """Returns (dims, confidence, source_field, flags)."""
    labelled_key = next(
        (k for k in attrs if re.search(r"dimensions?\s+[DWHL](\s*x\s*[DWHL])+", k, re.I)),
        None,
    )
    bare_key = next(
        (k for k in attrs
         if re.fullmatch(r"(item|product)\s+dimensions", k.strip(), re.I)),
        None,
    )

    labelled = _labelled(attrs[labelled_key]) if labelled_key else None
    bare = _bare(attrs[bare_key]) if bare_key else None

    resolved = _from_labelled(labelled) if labelled else None

    if labelled and resolved is None:
        # The labelled field exists but is missing a plan axis. Falling back to
        # the bare field would mean guessing which number is the depth, so the
        # honest answer is that we could not establish the dimensions.
        return (
            Dims(None, None, None, confidence="missing"),
            "conflicted",
            labelled_key or "",
            ["incomplete_labelled_dimensions"],
        )

    if resolved:
        w, d, h = resolved
        confidence, source = "stated", labelled_key
    elif bare:
        # No axis labels, and the ordering is not consistent across listings, so
        # this is a weaker reading even though the numbers are usually right.
        values = bare + [2.0] * (3 - len(bare))
        w, d, h = values[0], values[1], values[2]
        confidence, source = "parsed", bare_key or ""
    else:
        return Dims(None, None, None, confidence="missing"), "missing", "", []

    flags: list[str] = []
    if resolved and bare:
        # Compare as multisets: the fields often carry the same numbers in a
        # different order, which is not a conflict, only ambiguity we resolved.
        a = sorted(round(v) for v in (w, d, h))
        b = sorted(round(v) for v in bare)
        # A differing count is itself evidence of a parse problem. The old
        # `len(a) == len(b)` guard skipped exactly the cases most likely to be
        # wrong, so a short field was never compared against a full one.
        if len(a) != len(b) or any(abs(x - y) > 5 for x, y in zip(a, b)):
            flags.append("dimension_conflict")
            confidence = "conflicted"

    return Dims(w=w, d=d, h=h, confidence=confidence), confidence, source, flags


def _extract_carton(attrs: dict) -> Dims | None:
    """Only a *package* dimension is a carton.

    'Product Dimensions' is the assembled size — and it is also what the bare
    reader uses for the assembled dims. Claiming it as a carton made
    /access/check report `measured_using: "carton"` for a measurement that was
    never a carton, and suppressed the caveat that exists to say so.
    """
    for key in attrs:
        if re.search(r"package dimensions", key, re.I):
            values = _bare(attrs[key])
            if values and len(values) >= 3:
                return Dims(w=values[0], d=values[1], h=values[2], confidence="stated")
    return None


def parse_item(raw: dict) -> Product:
    title = (raw.get("title") or "").strip()
    attrs = raw.get("attrs") or {}
    search_category = raw.get("search_category", "")

    category, flags = _classify(title, search_category)
    dims, confidence, source, dim_flags = _extract_dims(attrs)
    flags = flags + dim_flags + _plausibility_flags(category, dims)

    assembly = str(attrs.get("Required Assembly", "")).strip().lower()

    return Product(
        asin=raw.get("asin", ""),
        title=title,
        category=category,
        search_category=search_category,
        price_sar=_price(raw.get("price", "")),
        dims=dims,
        dims_confidence=confidence,
        dims_source=source,
        carton=_extract_carton(attrs),
        flat_pack=assembly == "yes",
        rating=float(raw["rating"]) if raw.get("rating") else None,
        reviews=int(raw["reviews"]) if raw.get("reviews") else 0,
        style_tags=[s for s, pat in STYLE_PATTERNS.items() if re.search(pat, title, re.I)],
        rooms=ROOM_BY_CATEGORY.get(category, []),
        flags=flags,
    )


def parse_capture(path: str = "catalog/raw/amazon-sa-capture.json") -> list[Product]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return [parse_item(entry) for entry in raw["items"]]
