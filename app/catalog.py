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
from functools import lru_cache
from statistics import median
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
    # wall_art/mirror: extent is measured on the print or glass itself, not the
    # 2-3cm frame depth, so this is the same "largest dimension" reading as
    # everything else — a postage-stamp print and a door-sized one are both
    # real, but a 5cm "canvas" is a listing error, not decor.
    "wall_art": (15, 250),
    "mirror": (15, 220),
    "plant": (30, 260),
    "vase": (10, 150),
    "nightstand": (25, 90),
    "dining_chairs_pair": (40, 130),
}

# Plausible HEIGHT (cm) per category, judged on its own axis.
#
# PLAUSIBLE_MAX_EXTENT only ever looked at the single largest dimension, which
# means a mislabelled axis sails through it. B0DV7MZK5D — a 39,264 SAR
# "Transformer Table Solid Wood Extendable Round Dining Table Set" — parsed as
# 76.2w x 43.2d x 7.6h cm with `dims_confidence: stated`, zero flags and
# `usable: True`, because 76.2 cm is an unremarkable largest extent for a
# dining table. The 7.6 cm was the listing's own tabletop *thickness* (its
# attrs carry `Tabletop Thickness: "3 Inches"`) read as the table's height.
# A dining table 7.6 cm tall is not a dining table.
#
# Same spirit as the extent bounds: generous enough that only nonsense trips
# it. The low ends are deliberately set below the real minimum of each kind —
# platform beds really are 20 cm, and rugs really are 2 cm, so a blanket
# "furniture is taller than this" rule would reject correct data. Every one of
# the 11 listings this catches in the current capture is a genuine parse or
# classification error, not a short product.
PLAUSIBLE_HEIGHT_CM = {
    "sofa": (55, 120),
    "armchair": (55, 140),
    "coffee_table": (20, 65),
    "dining_table": (60, 110),
    "tv_unit": (25, 130),
    "wardrobe": (90, 260),
    # Low platform frames are real (the capture has 20 cm ones) and so are
    # tall headboards, so this band is wide on purpose.
    "bed": (15, 200),
    "bookshelf": (35, 260),
    # Rugs are ~2 cm and that is CORRECT. This bound exists to catch a rug
    # whose height axis picked up a floor dimension, not to doubt the 2.
    "rug": (0.1, 8),
    "floor_lamp": (90, 220),
    # For a hung piece the height axis is the artwork itself — the thin axis
    # is the depth, which is checked separately below.
    "wall_art": (15, 250),
    "mirror": (15, 220),
    "plant": (25, 260),
    "vase": (10, 150),
    "nightstand": (25, 95),
    "dining_chairs_pair": (45, 130),
}

# Smallest believable footprint side (cm) for things that stand on the floor.
#
# The other half of the same landmine: B0DV7MZK5D also claims a 43.2 cm depth,
# which no dining table has. Height alone would have caught this particular
# listing, but the next mislabelled one may break a different axis — the
# check has to cover the axis that was not the biggest AND was not the height.
#
# Only categories with an irreducible depth are listed. Deliberately absent:
# rug and bookshelf (genuinely shallow), wall_art and mirror (a 3 cm frame
# depth is the correct reading, see DEFAULT_HANGABLE_DEPTH_CM), coffee_table
# (the capture has real 28 cm-deep C-shaped side tables), plant and vase (a
# 10 cm pot is ordinary).
PLAUSIBLE_MIN_FOOTPRINT_CM = {
    "sofa": 60,
    "armchair": 40,
    "dining_table": 60,
    "tv_unit": 25,
    "wardrobe": 30,
    "dining_chairs_pair": 35,
}

# How far above its category's median a price has to sit before it is called
# implausible. The capture holds a genuine 185,350.38 SAR "coffee table" from
# seller ZZZXCDSX (B0FG1849P3) against a 799 SAR category median — the live
# page was checked by hand and that really is the listed price, so this is not
# a scraping bug to fix but a listing to distrust. The expensive tail of this
# assortment is also its worst-evidenced part: of the items over 15,000 SAR,
# most publish no dimensions at all.
#
# Calibrated, not guessed. Against the 465-item capture, measured on two known
# cases — the 185,350 SAR coffee table (0 reviews) must be caught, and a
# Yaheetech dining set of four at 3,445 SAR with 44 reviews must not be:
#
#     baseline            x6    x10   x15   x20
#     usable-median      116    109   102    80   <- spares the good only at x20
#     all-priced-median  108     99    86    74   <- spares it from x10
#
# 15x on the all-priced median catches every junk listing checked while
# leaving legitimately dear-for-its-category stock alone.
#
# 6x is the middle of the 5-8x range that was argued for, and nothing in this
# capture sits between 5x and 8x — the flagged set is identical at either end,
# so the exact number is not load-bearing here and the choice can stay
# conservative. Same policy as every other check in this module: flag it and
# keep it, so an agent can find the listing and say out loud that it is
# dismissing it, rather than have it quietly disappear or quietly rank.
PRICE_OUTLIER_MULTIPLE = 15.0

# Below this many priced, usable listings a category median is one seller's
# opinion rather than a market rate, and flagging against it would manufacture
# an outlier out of a small sample. Categories under the threshold are simply
# not judged on price.
MIN_PRICED_SAMPLE_FOR_MEDIAN = 5

# Title keywords that identify what a listing actually is. Checked in order, so
# the more specific patterns must come first ("coffee machine" before "table").
CATEGORY_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("appliance", re.compile(r"coffee machine|espresso|blender|kettle|air fryer|microwave", re.I)),
    ("rug", re.compile(r"\brugs?\b|carpet", re.I)),
    ("floor_lamp", re.compile(r"floor lamp|uplighter|standing lamp|uplight", re.I)),
    ("bookshelf", re.compile(r"bookshel|bookcase|etagere|display shelf", re.I)),
    # Wardrobe before mirror: real listing B0CJRP625Y is "MALMO 2 DOOR
    # WARDROBE WITH MIRROR - BROWN" — a wardrobe whose door happens to be
    # mirrored, not a standalone mirror. With mirror tried first this
    # wardrobe silently became a mirror. Furniture that merely *has* a
    # mirror has to be classified as the furniture; mirror only wins for a
    # listing that is nothing but the mirror.
    ("wardrobe", re.compile(r"wardrobe|closet|armoire", re.I)),
    # Nightstand before bed: "bedside table" and "night stand" both contain
    # "bed", but neither is a bed frame.
    ("nightstand", re.compile(r"nightstand|night stand|bedside table|bed side table", re.I)),
    ("bed", re.compile(r"bed frame|platform bed|bedstead", re.I)),
    ("tv_unit", re.compile(r"tv stand|tv unit|tv table|entertainment cent|television console", re.I)),
    ("coffee_table", re.compile(r"coffee table|center table|centre table", re.I)),
    # Dining table before the dining-chairs-only pattern: a "Dining Table and
    # 4 Chairs Set" is a table listing, and the table is the anchor piece.
    ("dining_table", re.compile(r"dining table|dinner table|kitchen table|dining round", re.I)),
    ("dining_chairs_pair", re.compile(r"dining chairs?\b|kitchen chairs?\b|set of \d+ chairs", re.I)),
    # Side tables before sofa, for the same reason armchair is before sofa:
    # "sofa" and "couch" are used here as adjectives naming what the piece
    # stands NEXT TO, not what it is. Five real listings — B0FQ37LJYH "Clear
    # Sofa End Table", B0FKMVD68V "Sofa End TableTop", B0GRGPKM14 "Living Room
    # Sofa End Table", B0F13MGH3K "Sofa Side Table", B0G7KHHNDH "Side Table
    # for Sofa & Couch" — all classified as `sofa`, and were kept out of plans
    # only because their size tripped `implausible_for_category`. That is luck:
    # a side table inside the 120-400 cm sofa band would have been placed in a
    # living room's primary seating slot.
    #
    # They land in coffee_table because that is already where this capture
    # puts the identical C-shaped acrylic table when its title happens to say
    # "coffee" too (B0H58F48XJ, B0B6PG26ZF). A `side_table` category for five
    # listings would split one kind of object across two names.
    #
    # Placed AFTER dining_table so that the several listings a seller titled
    # "Side table Minimalist ... Dining Table" (B0FZ256Q2S, B0DKCXKC9V) keep
    # reading as the dining tables they are.
    ("coffee_table", re.compile(
        r"(?:sofa|couch)\s+(?:side|end)\s+tables?"
        r"|c[\s-]?shaped(?:\s+[\w&-]+){0,3}\s+tables?", re.I)),
    # Armchair before sofa: amazon.sa sellers routinely write "Single Sofa" in
    # accent-chair titles, so the narrower match has to be tried first.
    ("armchair", re.compile(r"armchair|accent chair|recliner|lounge chair|wingback", re.I)),
    ("sofa", re.compile(r"\bsofa\b|settee|couch|loveseat", re.I)),
    # Mirror and wall_art come after every category of freestanding furniture
    # they could be a feature of (wardrobe above, but the same risk exists for
    # a dresser or console with "mirror" in its blurb) and before accessory:
    # accessory's "wall mount" is a substring of "wall mounted mirror", and
    # its "wall decor" phrasing would otherwise be read as a mirror listing
    # too. Narrower decor patterns have to be tried before the catch-all one.
    ("mirror", re.compile(r"\bmirrors?\b", re.I)),
    ("wall_art", re.compile(r"wall art|canvas print|framed print|framed art|art print|wall decor|wall hanging|tapestry|macrame", re.I)),
    ("accessory", re.compile(r"table runner|cheesecloth|tablecloth|placemat|cushion cover|wall mount|mount stand|monitor light|screenbar|rolling cart", re.I)),
    # One optional adjective between the qualifier and the noun. Sellers write
    # "Artificial Olive Tree" and "Faux Fiddle Leaf Plant", and requiring the
    # two words to be adjacent dropped every olive tree in the capture through
    # to the search-term fallback.
    ("plant", re.compile(r"(?:artificial|faux|potted|silk|fake)\s+(?:\w+\s+){0,2}(?:plant|tree)", re.I)),
    ("vase", re.compile(r"\bvases?\b", re.I)),
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

# Colour and material read off the listing title. Renders were per-role, so a
# black leatherette sofa and a greige bouclé sofa drew identically — the plan
# changed with style but the picture never did. These are the seller's own
# words, so the render reflects the product rather than its category.
#
# Ordered: the first match wins, so "rose hip" beats a bare "rose", and
# compound shades come before the plain ones they contain.
COLOUR_WORDS: list[tuple[str, str]] = [
    # Compound shades first, so "rose hip" beats a bare "rose".
    ("greige", "#b6ada0"), ("rose hip", "#b5687a"), ("dusty pink", "#c99aa2"),
    ("matte black", "#2b2f36"), ("off white", "#ece7de"),
    # Then plain colours. These come BEFORE the wood tones because on an
    # upholstered piece the wood is the legs and the colour is the fabric:
    # "Grey Linen Fabric, Beech Legs" is a grey sofa, not a beech one.
    ("charcoal", "#3a3f47"), ("black", "#2b2f36"), ("white", "#eceff3"),
    ("cream", "#e8dfd0"), ("ivory", "#eee7d9"), ("beige", "#d6c7ae"),
    ("grey", "#98a0aa"), ("gray", "#98a0aa"),
    ("brown", "#7a5238"), ("tan", "#b6875a"),
    ("navy", "#2f3f63"), ("blue", "#4c6ef5"),
    ("green", "#4b7f5c"), ("olive", "#6b7148"),
    ("pink", "#d29aa8"), ("rose", "#c07b88"), ("red", "#a5433c"),
    ("gold", "#c9a227"), ("brass", "#c9a227"), ("silver", "#b9c0c8"),
    # Wood and stone last: for a table with no other colour word, the timber
    # IS the colour.
    ("walnut", "#7b5334"), ("oak", "#b08e5f"), ("beech", "#d2b48c"),
    ("marble", "#e6e4df"), ("ceramic", "#eae7e1"),
    ("natural wood", "#a9784c"), ("wooden", "#a9784c"), ("wood", "#a9784c"),
]

MATERIAL_WORDS = [
    "leatherette", "leather", "bouclé", "boucle", "velvet", "linen", "fabric",
    "marble", "ceramic", "glass", "rattan", "bamboo", "bouclette",
    "metal", "iron", "steel", "oak", "walnut", "beech", "wood",
]

# A re-scrape lands structured Color/Material attrs alongside the title on
# most listings (246 of 280 have a Color or Colour). Preferred over the title
# (see `_colour`/`_material` below) because it is the seller's own field
# rather than marketing copy — "Interwood Astor Sofa" says nothing about
# colour, its `Color` attribute does. Checked in this order per item: first
# attr key present wins.
#
# Deliberately NOT gated by value length. The tempting rule is "a real colour
# name is short, so distrust anything over N words" — but real values across
# the capture disprove it both ways: legitimate colours run to 4 words
# ("Camel Beige and Tan", "Dark Walnut - Brown"), and junk shows up just as
# often as a single word or SKU fragment ("9-2-BK", "B", "3 Pack" on
# wall-art variants). Length is not the signal. `_word`'s whole-word match
# already is: it is what stops "Golden Tree Floral Wall Art" from reading as
# gold ("golden" is not "gold"), and it lets a real colour word inside a
# longer, honest description through ("Dark Walnut - Brown" still reads as
# walnut). No separate trust threshold is applied.
COLOUR_ATTR_KEYS = ("Color", "Colour", "Color Name")
MATERIAL_ATTR_KEYS = ("Material", "Fabric Type", "Frame Material", "Top Material Type")

ROOM_BY_CATEGORY = {
    "sofa": ["living_dining"],
    "armchair": ["living_dining", "bedroom"],
    "coffee_table": ["living_dining"],
    "dining_table": ["living_dining"],
    "dining_chairs_pair": ["living_dining"],
    "tv_unit": ["living_dining"],
    "rug": ["living_dining", "bedroom"],
    "floor_lamp": ["living_dining", "bedroom"],
    "bookshelf": ["living_dining", "bedroom"],
    "wardrobe": ["bedroom"],
    "bed": ["bedroom"],
    "nightstand": ["bedroom"],
    "wall_art": ["living_dining", "bedroom"],
    "mirror": ["living_dining", "bedroom"],
    "plant": ["living_dining", "bedroom"],
    "vase": ["living_dining"],
}

# wall_art and mirror hang; they have no floor footprint. amazon.sa states
# their size as "Item Dimensions L x W" with no depth axis at all — unlike a
# rug, where L x W really are the two floor dimensions with height implicitly
# ~0. See `_from_labelled` and `_extract_dims`.
HANGABLE_CATEGORIES = {"wall_art", "mirror"}
DEFAULT_HANGABLE_DEPTH_CM = 3.0

# The confirmed real capture's search term for this category is the shorter
# "dining_chairs" — but "dining_chairs_pair" is the category/role name every
# other module already keys on (`DINING_CHAIR_ROLES` in geometry.py, the
# renderer's glyph table), so it is not renamed here. Without this alias
# every real dining-chair listing whose title matched the dining_chairs_pair
# pattern would get flagged `category_mismatch` against its own search term —
# a spelling difference mistaken for pollution, not an actual wrong result.
SEARCH_CATEGORY_ALIASES = {"dining_chairs": "dining_chairs_pair"}


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
    colour_hex: str | None = None
    colour_source: str = "missing"
    material: str | None = None
    material_source: str = "missing"
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
            "colour_hex": self.colour_hex or "",
            "colour_source": self.colour_source,
            "material": self.material or "",
            "material_source": self.material_source,
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


def _from_labelled(axes: dict[str, float], hangable: bool = False) -> tuple[float, float, float] | None:
    """Map axis letters to (w, d, h), or None if a plan axis is missing.

    This used to fall back to 0.0 for an absent axis. Zero is not None, so
    `Dims.known` was True, confidence stayed 'stated', nothing was flagged, and
    a sofa with no depth cleared every door on the route. An axis we did not
    read is an axis we do not know.
    """
    if hangable and "W" in axes and "L" in axes and "D" not in axes and "H" not in axes:
        # wall_art / mirror: 'L x W' is height x width with no depth axis
        # stated at all — reading it with the rug rule below (long side is
        # the width, height defaults to 2cm) turned a 90x60cm print into "90
        # wide, 2 tall, 60 deep": a picture lying flat, not one hanging on a
        # wall. The frame runs a few centimetres deep; DEFAULT_HANGABLE_DEPTH_CM
        # stands in for that since amazon.sa never states it.
        return axes["W"], DEFAULT_HANGABLE_DEPTH_CM, axes["L"]

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

def _word(needle: str, haystack: str) -> bool:
    """Whole-word match.

    A substring test made "TV Stand", "Stain Resistant" and "Rectangular" all
    match "tan", so half the catalogue rendered the same shade of tan. Short
    colour words are especially prone to this.
    """
    return re.search(rf"(?<![a-z]){re.escape(needle)}(?![a-z])", haystack) is not None


def _colour_word(text: str) -> str | None:
    lowered = text.lower()
    for word, value in COLOUR_WORDS:
        if _word(word, lowered):
            return value
    return None


def _material_word(text: str) -> str | None:
    lowered = text.lower()
    for word in MATERIAL_WORDS:
        if _word(word, lowered):
            return "boucle" if word == "bouclé" else word
    return None


def _review_count(raw: object) -> int:
    """Read a review count out of whatever amazon.sa rendered.

    The first capture saw bare numbers. The second scrape, taken server-side,
    gets the parenthesised form — "(2)", "(1,204)" — and `int()` on that raised,
    which did not cost one product its review count: it made the entire
    catalogue fail to load and took every test suite down at collection time.
    Anything unreadable is no reviews, never an exception.
    """
    digits = re.sub(r"[^\d]", "", str(raw or ""))
    return int(digits) if digits else 0


def _colour(title: str, attrs: dict) -> tuple[str | None, str, list[str]]:
    """Attrs first, title second. The attribute is the seller's structured
    field rather than marketing copy, so it wins when both are present. An
    attr that names a colour COLOUR_WORDS doesn't recognise (a SKU code,
    "Multicolor") is a miss, not licence to invent a hex — it falls through
    to the title rather than stopping there.

    `Color` and `Colour` are two spellings of the same field, and 8 real
    listings state a different value under each (B0DT1DXRKS: Grey vs Black;
    B0DT1FYTKH: "Olive Green" vs "Greg Boukle"; ...). Comparing the raw text
    catches this even when one side is gibberish to COLOUR_WORDS — the
    listing has already told us it disagrees with itself before either value
    is interpreted. Once two present keys disagree, this deliberately does
    not try to pick the more plausible-looking one: judging which of two
    contradicting seller fields to believe is exactly the kind of silent call
    this project refuses to make. A confident wrong colour is worse than no
    colour, so the result is `colour_hex=None`, not a guess.
    """
    present = [
        (key, re.sub(r"\s+", " ", str(attrs[key])).strip())
        for key in COLOUR_ATTR_KEYS if attrs.get(key)
    ]
    distinct_raw = {text.lower() for _, text in present}
    if len(distinct_raw) > 1:
        return None, "conflicted", ["colour_conflict"]

    for _, text in present:
        hex_ = _colour_word(text)
        if hex_:
            return hex_, "attrs", []

    hex_ = _colour_word(title)
    if hex_:
        return hex_, "title", []
    return None, "missing", []


def _material(title: str, attrs: dict) -> tuple[str | None, str]:
    for key in MATERIAL_ATTR_KEYS:
        raw = attrs.get(key)
        if raw:
            word = _material_word(str(raw))
            if word:
                return word, "attrs"
    word = _material_word(title)
    if word:
        return word, "title"
    return None, "missing"


def _classify(title: str, search_category: str) -> tuple[str, list[str]]:
    search_category = SEARCH_CATEGORY_ALIASES.get(search_category, search_category)
    for name, pattern in CATEGORY_PATTERNS:
        if pattern.search(title):
            flags = ["category_mismatch"] if name != search_category else []
            return name, flags
    return search_category, ["unclassified"]


def _plausibility_flags(category: str, dims: Dims) -> list[str]:
    """Three readings of the same question: are these dimensions possible?

    Checking only the largest extent — which is all this did — cannot see a
    mislabelled axis, because the largest number is usually the one the seller
    got right. B0DV7MZK5D passed with a 7.6 cm "height" that was really its
    tabletop thickness, on the strength of an unremarkable 76.2 cm extent.
    So the height and the footprint are now judged on their own terms too.

    One flag covers all three: an item whose dimensions are impossible for its
    category is untrustworthy for placement whichever axis gave it away, and
    `usable` already turns on this flag.
    """
    if not dims.known:
        return []

    extent_bounds = PLAUSIBLE_MAX_EXTENT.get(category)
    if extent_bounds:
        low, high = extent_bounds
        extent = max(dims.w or 0, dims.d or 0, dims.h or 0)
        if not (low <= extent <= high):
            return ["implausible_for_category"]

    height_bounds = PLAUSIBLE_HEIGHT_CM.get(category)
    if height_bounds and dims.h is not None:
        low, high = height_bounds
        if not (low <= dims.h <= high):
            return ["implausible_for_category"]

    min_side = PLAUSIBLE_MIN_FOOTPRINT_CM.get(category)
    if min_side is not None and dims.w is not None and dims.d is not None:
        if min(dims.w, dims.d) < min_side:
            return ["implausible_for_category"]

    return []


def _flag_price_outliers(products: tuple[Product, ...]) -> None:
    """Mark listings priced far above what their own category costs.

    This is the one check that cannot be made from a single listing: "too
    expensive" only means anything relative to the other things of its kind,
    so it runs once over the whole parsed capture rather than inside
    `parse_item`. The median is taken over `usable` listings only — the
    expensive tail of this assortment is disproportionately the part with no
    published dimensions, and letting that tail set the baseline would raise
    the bar until the outliers looked normal.

    Deliberately does NOT touch `usable`. `usable` is a claim about whether
    the piece can be verified to fit and be delivered, and a suspicious price
    says nothing about that. Keeping the item usable-but-flagged is what lets
    an agent surface the 185,350 SAR coffee table and dismiss it out loud;
    `planner.candidates_for` is where it is kept out of a recommendation.
    """
    # Median over EVERY priced listing in the category, not just the usable
    # ones. Restricting it to `usable` looked cautious and was the opposite:
    # dear listings omit their dimensions far more often than cheap ones, so
    # the usable subset is the bargain tail and the baseline it sets is too
    # low. A `wardrobe` median of 664 SAR put the cutoff at 3,984 — an
    # ordinary price for a wardrobe — and six dimensioned, sensibly sized,
    # genuinely reviewed items were being kept out of recommendations.
    priced: dict[str, list[float]] = {}
    for p in products:
        if p.price_sar is not None:
            priced.setdefault(p.category, []).append(p.price_sar)

    medians = {
        category: median(prices)
        for category, prices in priced.items()
        if len(prices) >= MIN_PRICED_SAMPLE_FOR_MEDIAN
    }

    for p in products:
        cutoff = medians.get(p.category)
        if cutoff is not None and p.price_sar is not None:
            if p.price_sar > cutoff * PRICE_OUTLIER_MULTIPLE and not _vouched_for(p):
                p.flags.append("implausible_price")


# A median multiple is only ever a proxy for "nobody vouches for this price".
# Direct evidence beats the proxy: these are buyers who paid it and came back
# to say so.
VOUCHED_MIN_REVIEWS = 100


def _vouched_for(p: Product) -> bool:
    """Whether enough real buyers stand behind this price to trust it.

    Without this the outlier test deleted the best-evidenced expensive items in
    the catalogue. `B08569KN5F` — a ZINUS platform bed at 15,385 SAR, 4.6
    stars, 1,313 reviews, dimensions stated — sat 46x above a `bed` median that
    300 SAR metal frames had set, so it was flagged and `candidates_for`
    dropped it. A 30,000 SAR brief then bought a 329 SAR bed and reported the
    budget as unspendable, which was false: the catalogue had exactly the item
    the tier was meant to buy, and this check had thrown it away.

    The 185,350 SAR coffee table stays flagged. It has no reviews at all, which
    is the difference the flag is actually trying to capture.
    """
    return bool(p.rating) and p.reviews >= VOUCHED_MIN_REVIEWS


def _extract_dims(attrs: dict, category: str = "") -> tuple[Dims, str, str, list[str]]:
    """Returns (dims, confidence, source_field, flags)."""
    hangable = category in HANGABLE_CATEGORIES
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

    resolved = _from_labelled(labelled, hangable=hangable) if labelled else None

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

    depth_assumed = False
    if resolved:
        w, d, h = resolved
        confidence, source = "stated", labelled_key
        # True whenever `_from_labelled`'s hangable branch fired: the depth in
        # `resolved` is DEFAULT_HANGABLE_DEPTH_CM, not something read off the
        # listing. Recorded as a flag rather than folded silently into `dims`
        # — this project marks assumptions rather than burying them.
        depth_assumed = hangable and "D" not in labelled and "H" not in labelled
    elif bare:
        if hangable and len(bare) == 2:
            # No axis labels and only two numbers: a hung piece with no depth
            # stated at all, not a floor good whose missing axis defaults to a
            # negligible height. Padding it the ordinary way would call the
            # second of the two numbers a "depth" and the missing third a 2cm
            # "height" — the same lying-flat error `_from_labelled` avoids.
            w, d, h = bare[0], DEFAULT_HANGABLE_DEPTH_CM, bare[1]
            confidence, source = "parsed", bare_key or ""
            depth_assumed = True
        else:
            # No axis labels, and the ordering is not consistent across listings,
            # so this is a weaker reading even though the numbers are usually right.
            values = bare + [2.0] * (3 - len(bare))
            w, d, h = values[0], values[1], values[2]
            confidence, source = "parsed", bare_key or ""
    else:
        return Dims(None, None, None, confidence="missing"), "missing", "", []

    flags: list[str] = ["assumed_depth"] if depth_assumed else []
    if resolved and bare:
        # Compare against what was actually READ off the labelled field, not
        # `(w, d, h)` — for a 2-axis field (a rug's L x W, or a hangable
        # item's assumed depth) those carry a padded/assumed third number
        # that was never in the listing. Real example: B0GRFG1RW4's "Product
        # dimensions" and "Item Dimensions L x W" both say "2L x 3W Meters" —
        # the same two numbers stated twice under different field names — but
        # comparing the padded (w, d, h)=[300, 200, 2] against bare's [200,
        # 300] made two agreeing fields look like a 3-vs-2 count mismatch and
        # flagged a listing that does not, in fact, contradict itself.
        a = sorted(round(v) for v in labelled.values())
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
    dims, confidence, source, dim_flags = _extract_dims(attrs, category)
    colour_hex, colour_source, colour_flags = _colour(title, attrs)
    material, material_source = _material(title, attrs)
    flags = flags + dim_flags + colour_flags + _plausibility_flags(category, dims)

    # An item with no published dimensions is unusable, but until now it said
    # so only through `dims_confidence`. The candidates list renders a blocked
    # row from `flags`, so five portable wardrobes appeared greyed out with no
    # stated reason — refusing something without saying why is the one thing
    # this project is not allowed to do.
    if confidence == "missing" and "no_published_dimensions" not in flags:
        flags = flags + ["no_published_dimensions"]

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
        reviews=_review_count(raw.get("reviews")),
        style_tags=[s for s, pat in STYLE_PATTERNS.items() if re.search(pat, title, re.I)],
        colour_hex=colour_hex,
        colour_source=colour_source,
        material=material,
        material_source=material_source,
        rooms=ROOM_BY_CATEGORY.get(category, []),
        flags=flags,
    )


@lru_cache(maxsize=4)
def parse_capture(path: str = "catalog/raw/amazon-sa-capture.json") -> tuple[Product, ...]:
    """Parse the capture once per process.

    Every plan and every candidates request called this, re-reading and
    re-parsing 75 listings each time — enough to make the swap picker sit on
    "loading" for over a second. Returns a tuple so the cached value cannot be
    mutated by a caller.
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    products = tuple(parse_item(entry) for entry in raw["items"])
    # Price plausibility is the one judgement that needs the other listings in
    # the category to exist first, so it is a second pass over the whole set.
    _flag_price_outliers(products)
    return products
