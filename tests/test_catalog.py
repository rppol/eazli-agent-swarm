"""Catalog parser tests, driven entirely by real amazon.sa listings.

Every fixture below is a verbatim attribute set captured from a live product
page. The messiness is the point: units drift between metres, millimetres and
inches, the same listing contradicts itself across fields, axis labels are
sometimes wrong, and search results contain products from the wrong category
entirely. A parser that assumes clean input produces confident nonsense.
"""

import pytest

import json
import re
from pathlib import Path

from app.catalog import _review_count, parse_capture, parse_item


def item(asin="X", cat="sofa", title="Test Sofa", price="SAR1,000.00", **attrs):
    return {"asin": asin, "search_category": cat, "title": title,
            "price": price, "rating": "", "reviews": "", "attrs": attrs}


# --------------------------------------------------------------------------
# price
# --------------------------------------------------------------------------

def test_parses_sar_price_with_thousands_separator():
    assert parse_item(item(price="SAR2,411.46")).price_sar == 2411.46


def test_missing_price_is_none_not_zero():
    """Zero would sail through every budget filter as the cheapest option."""
    assert parse_item(item(price="")).price_sar is None


# --------------------------------------------------------------------------
# dimensions: units
# --------------------------------------------------------------------------

def test_axis_labelled_centimetres():
    p = parse_item(item(**{"Item Dimensions D x W x H": "104.5D x 170W x 82.5H centimeters"}))
    assert (p.dims.w, p.dims.d, p.dims.h) == (170, 104.5, 82.5)
    assert p.dims_confidence == "stated"


def test_axis_labelled_metres_are_converted():
    p = parse_item(item(**{"Item Dimensions D x W x H": "1.05D x 2.2W x 0.83H Meters"}))
    assert (p.dims.w, p.dims.d, p.dims.h) == (220, 105, 83)


def test_axis_labelled_millimetres_are_converted():
    p = parse_item(item(cat="floor_lamp", title="Monitor Light Bar",
                        **{"Item Dimensions D x W x H": "160D x 20W x 30H millimeters"}))
    assert (p.dims.w, p.dims.d, p.dims.h) == (2, 16, 3)


def test_bare_dimension_field_in_metres_is_converted():
    p = parse_item(item(cat="bed", title="Bed Frame Queen",
                        **{"Item Dimensions": "2.12 x 1.6 x 0.95 Meters"}))
    assert max(p.dims.w, p.dims.d, p.dims.h) == 212


# --------------------------------------------------------------------------
# dimensions: confidence and provenance
# --------------------------------------------------------------------------

def test_axis_labelled_field_is_preferred_over_the_ambiguous_bare_field():
    """'Item Dimensions' has no axis labels and its order is not consistent
    across listings, so the D/W/H field wins when both are present."""
    p = parse_item(item(**{
        "Item Dimensions D x W x H": "66D x 127W x 48.6H centimeters",
        "Item Dimensions": "127 x 66 x 48.6 centimeters",
    }))
    assert (p.dims.w, p.dims.d) == (127, 66)
    assert p.dims_source == "Item Dimensions D x W x H"


def test_bare_field_alone_is_parsed_but_marked_lower_confidence():
    p = parse_item(item(cat="tv_unit", title="TV Stand",
                        **{"Item Dimensions": "180 x 40 x 50 centimeters"}))
    assert p.dims.known
    assert p.dims_confidence == "parsed"


def test_no_dimensions_at_all_is_missing_never_a_guess():
    p = parse_item(item(asin="B0BRNLMQYQ", cat="armchair", title="Swivel Accent Chair"))
    assert p.dims_confidence == "missing"
    assert not p.dims.known


def test_contradicting_dimension_fields_are_flagged():
    """Real listing B0DYDNS6FQ: the labelled field and the bare field disagree
    by more than 40cm. Trusting either silently would be indefensible."""
    p = parse_item(item(cat="wardrobe", title="Woodies Wardrobe 2 doors", **{
        "Item Dimensions D x W x H": "44D x 91W x 180H centimeters",
        "Item Dimensions": "79.4 x 36 x 175.5 centimeters",
    }))
    assert "dimension_conflict" in p.flags
    assert p.dims_confidence == "conflicted"


def test_agreeing_fields_are_not_flagged():
    p = parse_item(item(**{
        "Item Dimensions D x W x H": "85D x 190W x 85H centimeters",
        "Item Dimensions": "85 x 190 x 85 centimeters",
    }))
    assert "dimension_conflict" not in p.flags


# --------------------------------------------------------------------------
# plausibility
# --------------------------------------------------------------------------

def test_physically_implausible_dimensions_are_flagged():
    """B0GCD334HF is sold as a wingback armchair but lists 40 x 30 x 32 cm.
    A 30cm-wide armchair does not exist; the listing is simply wrong."""
    p = parse_item(item(cat="armchair", title="Boho Accent Armchair Wingback",
                        **{"Item Dimensions D x W x H": "40D x 30W x 32H centimeters"}))
    assert "implausible_for_category" in p.flags


def test_plausible_armchair_is_not_flagged():
    p = parse_item(item(cat="armchair", title="Accent Chair",
                        **{"Item Dimensions D x W x H": "68D x 68.5W x 81H centimeters"}))
    assert "implausible_for_category" not in p.flags


# --------------------------------------------------------------------------
# category pollution
# --------------------------------------------------------------------------

def test_coffee_machine_is_not_a_coffee_table():
    """Searching amazon.sa for 'coffee table' returns Philips coffee machines."""
    p = parse_item(item(cat="coffee_table", title="Philips Fully Automatic Coffee Machine, 12 Hot & Cold Beverages"))
    assert p.category != "coffee_table"
    assert "category_mismatch" in p.flags


def test_table_runner_is_not_a_dining_table():
    p = parse_item(item(cat="dining_table", title="Artoid Mode Boho Gauze Wedding Cheesecloth Table Runner"))
    assert "category_mismatch" in p.flags


def test_accent_chair_marketed_as_a_single_sofa_is_still_an_armchair():
    """Real listing B0F93H3W7D. amazon.sa sellers routinely put 'Single Sofa' in
    accent-chair titles, so the more specific match has to win."""
    p = parse_item(item(cat="armchair",
                        title="HXDream Accent Chair for Living Room, Single Sofa, Modern Lounge",
                        **{"Item Dimensions D x W x H": "68D x 68.5W x 81H centimeters"}))
    assert p.category == "armchair"
    assert p.flags == []


def test_genuine_dining_table_keeps_its_category():
    p = parse_item(item(cat="dining_table", title="Tribesigns 120CM Dining Table for 4, White Kitchen Dinner Table"))
    assert p.category == "dining_table"
    assert "category_mismatch" not in p.flags


# --------------------------------------------------------------------------
# carton vs assembled
# --------------------------------------------------------------------------

def test_required_assembly_marks_the_item_flat_pack():
    p = parse_item(item(cat="bookshelf", title="5 Tier Bookshelf",
                        **{"Item Dimensions D x W x H": "30D x 60W x 154H centimeters",
                           "Required Assembly": "Yes"}))
    assert p.flat_pack is True


def test_preassembled_item_is_not_flat_pack():
    p = parse_item(item(cat="sofa", title="Three Seater Sofa",
                        **{"Item Dimensions D x W x H": "85D x 190W x 85H centimeters",
                           "Required Assembly": "No"}))
    assert p.flat_pack is False


# --------------------------------------------------------------------------
# two-dimensional goods
# --------------------------------------------------------------------------

def test_rug_dimensions_parse_with_negligible_height():
    p = parse_item(item(cat="rug", title="Area Rug for Living Room",
                        **{"Item Dimensions L x W": "3L x 2W Meters"}))
    assert {p.dims.w, p.dims.d} == {300, 200}
    assert p.dims.h is not None and p.dims.h < 6


# --------------------------------------------------------------------------
# whole-file behaviour
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def catalog():
    return parse_capture("catalog/raw/amazon-sa-capture.json")


def test_parses_every_captured_listing(catalog):
    captured = json.loads(Path("catalog/raw/amazon-sa-capture.json")
                          .read_text())["items"]
    assert len(catalog) == len(captured)


def test_most_listings_yield_usable_dimensions(catalog):
    usable = [p for p in catalog if p.dims.known]
    assert len(usable) >= 60


def test_some_listings_genuinely_have_no_dimensions(catalog):
    """If this ever hits zero the fixture has been sanitised and the honest-agent
    path is no longer being exercised by real data."""
    assert any(p.dims_confidence == "missing" for p in catalog)


def test_the_polluted_search_results_are_caught(catalog):
    mismatched = [p for p in catalog if "category_mismatch" in p.flags]
    assert len(mismatched) >= 4
    assert any("Coffee Machine" in p.title for p in mismatched)


def test_every_product_has_a_stable_id_and_source_url(catalog):
    captured = json.loads(Path("catalog/raw/amazon-sa-capture.json")
                          .read_text())["items"]
    assert len({p.asin for p in catalog}) == len(captured)
    assert all(p.url.startswith("https://www.amazon.sa/") for p in catalog)


# --------------------------------------------------------------------------
# Regressions from the code review. Each of these produced a confident,
# plausible, wrong number.
# --------------------------------------------------------------------------

def test_trailing_weight_does_not_destroy_the_unit():
    """'inches; 5 kg' matched 'kg', found no conversion, and silently fell back
    to 1.0 — recording a 203cm wardrobe as 80cm tall, flagged 'stated'."""
    p = parse_item(item(cat="wardrobe", title="2 Door Wardrobe Closet Oak",
                        **{"Item Dimensions D x W x H": "24D x 60W x 80H inches; 55 kg"}))
    assert p.dims.h == pytest.approx(203.2, abs=0.5)
    assert p.dims.w == pytest.approx(152.4, abs=0.5)


def test_metres_with_a_trailing_weight_convert_correctly():
    p = parse_item(item(**{"Item Dimensions D x W x H": "1.05D x 2.2W x 0.83H Meters; 90 kg"}))
    assert (p.dims.w, p.dims.d, p.dims.h) == (220, 105, 83)


def test_a_missing_axis_is_not_silently_zero():
    """'220W x 83H' has no depth. Zero-filling made it 'known' and 'stated',
    so a sofa with no depth passed every door and hallway on the route."""
    p = parse_item(item(**{"Item Dimensions D x W x H": "220W x 83H centimeters"}))
    assert not p.dims.known or p.dims.d
    assert p.dims_confidence != "stated" or p.dims.d


def test_conflict_is_still_caught_when_one_field_is_short():
    p = parse_item(item(**{
        "Item Dimensions D x W x H": "220W x 83H centimeters",
        "Item Dimensions": "95 x 220 x 83 centimeters",
    }))
    assert not p.usable


def test_dimensions_glued_to_their_unit_are_not_dropped():
    """'120x60x45cm' lost the 45 because the regex needed a delimiter after it,
    then padding made the coffee table 2cm tall."""
    p = parse_item(item(cat="coffee_table", title="Coffee Table",
                        **{"Item Dimensions": "120x60x45cm"}))
    assert p.dims.known
    assert sorted(filter(None, [p.dims.w, p.dims.d, p.dims.h])) == [45, 60, 120]


def test_product_dimensions_are_not_claimed_as_a_carton():
    """'Product Dimensions' is the assembled size. Treating it as a carton made
    /access/check report measured_using='carton' for a measurement that was
    never a carton, and suppressed the assembled-dimensions caveat."""
    p = parse_item(item(cat="wardrobe", title="MALMO 2 DOOR WARDROBE",
                        **{"Product Dimensions": "84 x 53 x 198 cm; 64 kg"}))
    assert p.carton is None


def test_a_real_package_dimension_still_becomes_the_carton():
    p = parse_item(item(cat="bed", title="Bed Frame",
                        **{"Item Dimensions D x W x H": "200D x 160W x 35H centimeters",
                           "Package Dimensions": "120 x 40 x 18 cm; 30 kg"}))
    assert p.carton is not None
    assert max(p.carton.w, p.carton.d, p.carton.h) == 120


# --------------------------------------------------------------------------
# appearance, read from the seller's own words
# --------------------------------------------------------------------------

def test_colour_comes_from_the_title():
    assert parse_item(item(title="Interwood Astor Sofa – Black Air Leather")).colour_hex == "#2b2f36"
    assert parse_item(item(title="Interwood Anniston Sofa – Green Linen")).colour_hex == "#4b7f5c"


def test_upholstery_colour_beats_the_leg_timber():
    """"Premium Grey Linen Fabric, Beech Legs" is a grey sofa, not a beech one.
    Wood tones used to match first and painted it beech."""
    p = parse_item(item(title="Interwood Kissel 3 Seater Sofa – Premium Grey Linen Fabric, Beech Legs"))
    assert p.colour_hex == "#98a0aa"


def test_timber_is_the_colour_when_nothing_else_is_named():
    p = parse_item(item(cat="dining_table", title="Tribesigns Solid Walnut Dining Table"))
    assert p.colour_hex == "#7b5334"


def test_compound_shades_beat_the_plain_word_inside_them():
    assert parse_item(item(title="Cooper Velvet Sofa – Rose Hip")).colour_hex == "#b5687a"


def test_material_is_extracted_too():
    assert parse_item(item(title="Kent Bouclé Sofa – Greige")).material == "boucle"
    assert parse_item(item(title="Astor Sofa Black Air Leather")).material == "leather"


def test_a_title_with_no_colour_word_says_so_rather_than_guessing():
    p = parse_item(item(title="Three Seater Sofa W190 x D85 x H85 cm - MS_ID_3PSF_00060"))
    assert p.colour_hex is None and p.material is None


def test_colour_words_match_whole_words_only():
    """A substring test made "TV Stand", "Stain Resistant" and "Rectangular"
    all match "tan", so half the catalogue rendered the same shade."""
    for title in ("Modern TV Stand for TV up to 55 Inch",
                  "Ultra-Thin Washable Rug, Stain Resistant Anti Slip",
                  "Kitchen Dining Table Marble Tabletop: Rectangular 120cm"):
        p = parse_item(item(title=title))
        assert p.colour_hex != "#b6875a", f"{title!r} wrongly matched 'tan'"


def test_a_genuinely_tan_item_still_matches():
    assert parse_item(item(title="Tan Leather Armchair")).colour_hex == "#b6875a"


# --------------------------------------------------------------------------
# appearance, read from the `attrs` a re-scrape is landing (attrs first,
# title second). Confirmed live against two real amazon.sa pages: Color,
# Colour and Material are real keys, and Color can (a) disagree with Colour
# on the very same listing (ASIN B0DT1DXRKS: Color=Grey, Colour=Black) and
# (b) be marketing copy rather than a colour at all (a wall-art page returns
# Color="Golden Tree Floral Wall Art"). The merged capture with these attrs
# had not landed as of writing, so the fixtures below are still synthetic —
# built to the exact shapes and the exact real values that were confirmed.
# --------------------------------------------------------------------------

def test_colour_prefers_the_attribute_over_the_title():
    p = parse_item(item(title="Astor Sofa - Black Air Leather", **{"Color": "Grey"}))
    assert p.colour_hex == "#98a0aa"
    assert p.colour_source == "attrs"


def test_colour_falls_back_to_the_title_when_no_attribute_is_present():
    p = parse_item(item(title="Astor Sofa - Black Air Leather"))
    assert p.colour_hex == "#2b2f36"
    assert p.colour_source == "title"


def test_colour_attribute_key_variants_are_all_read():
    """Different amazon.sa listings use different names for the same field."""
    for key in ("Color", "Colour", "Color Name"):
        p = parse_item(item(title="Generic Sofa", **{key: "Navy Blue"}))
        assert p.colour_hex == "#2f3f63", key
        assert p.colour_source == "attrs", key


def test_colour_attribute_with_a_word_the_table_does_not_know_falls_back_to_the_title():
    """A 'miss' on the attribute (a SKU code, "Multicolor") is not licence to
    invent a hex, but it also should not stop the title from being tried."""
    p = parse_item(item(title="Astor Sofa - Black Air Leather", **{"Color": "Multicolor"}))
    assert p.colour_hex == "#2b2f36"
    assert p.colour_source == "title"


def test_colour_attribute_and_title_both_missing_is_reported_not_guessed():
    p = parse_item(item(title="Astor Sofa", **{"Color": "Multicolor"}))
    assert p.colour_hex is None
    assert p.colour_source == "missing"


def test_colour_and_colour_agreeing_is_not_flagged():
    p = parse_item(item(title="Modern Fabric Sofa", **{"Color": "Grey", "Colour": "Grey"}))
    assert p.colour_hex == "#98a0aa"
    assert p.colour_source == "attrs"
    assert "colour_conflict" not in p.flags


# The 8 real self-contradicting listings amazon.sa returns, confirmed against
# the merged capture (`Color != Colour`, case-insensitively, on the same
# ASIN). All 8 are used verbatim rather than invented cases: this is the
# actual shape of the disagreement, including cases where one side is not a
# recognisable colour word at all ("Greg Boukle", "grass") — the raw text
# already disagrees before either value is interpreted, so no COLOUR_WORDS
# match is needed to catch it.
REAL_COLOUR_CONFLICTS = [
    ("B0DT1DXRKS", "Grey", "Black"),
    ("B0DT1FYTKH", "Olive Green", "Greg Boukle"),
    ("B0DT1GNSFR", "Camel Beige and Tan", "grass"),
    ("B0DT1F25XV", "Grey", "Black"),
    ("B0DT1D5D8T", "Greige Boucle", "Grey"),
    ("B0DWNDSPX7", "White", "Black"),
    ("B0DT1C3BHP", "Ivory", "White BOKL"),
    ("B0DT1FP4Z6", "Greige Boucle", "Beige"),
]


def test_colour_and_colour_disagree_is_flagged_not_silently_resolved():
    """Two spellings of the same attribute contradicting each other is the
    same class of problem as a labelled/bare dimension conflict, and gets the
    same treatment — a flag and a provenance value that says so, not a guess.
    A confident wrong colour is worse than no colour."""
    for asin, color, colour in REAL_COLOUR_CONFLICTS:
        p = parse_item(item(asin=asin, title="Interwood Sofa", **{"Color": color, "Colour": colour}))
        assert p.colour_hex is None, asin
        assert p.colour_source == "conflicted", asin
        assert "colour_conflict" in p.flags, asin


def test_colour_conflict_is_not_adjudicated_even_when_one_side_looks_more_plausible():
    """Deliberate choice: once Color and Colour disagree, picking whichever
    value looks more like a real colour is exactly the kind of silent
    judgement call this project refuses to make — "Grey" is not preferred
    over "Greg Boukle" just because COLOUR_WORDS recognises it."""
    p = parse_item(item(title="Interwood Sofa", **{"Color": "Grey", "Colour": "Greg Boukle"}))
    assert p.colour_hex is None
    assert p.colour_source == "conflicted"


def test_colour_attribute_naming_the_products_subject_is_not_read_as_its_colour():
    """Real wall-art listings carry Color values like "Flower", "Blue tree" and
    "Animal-【Zwaan】" — the print's subject, not the frame's colour. Whole-word
    matching is what keeps this mostly honest without a length cutoff: "gold"
    only matches a standalone "gold", so "Golden Tree Floral Wall Art" (the
    kind of value amazon.sa returns for these listings) is correctly read as
    naming nothing COLOUR_WORDS recognises, not gold."""
    p = parse_item(item(cat="wall_art", title="Golden Tree Floral Canvas Print",
                        **{"Color": "Golden Tree Floral Wall Art"}))
    assert p.colour_hex is None
    assert p.colour_source == "missing"


def test_a_longer_colour_attribute_value_is_still_trusted_when_it_names_a_real_colour():
    """The rejected alternative: gate trust on value length. Real values
    disprove it — "Camel Beige and Tan" (a real Color value, ASIN
    B0DT1GNSFR) and "Dark Walnut - Brown" both run to 4 words and are both
    legitimate, so a word-count cutoff would have thrown away real signal to
    catch junk that whole-word matching already excludes for free."""
    p = parse_item(item(cat="wall_art", title="Sky Motif Canvas Print",
                        **{"Color": "Wall Art with Blue Sky Motif"}))
    assert p.colour_hex == "#4c6ef5"
    assert p.colour_source == "attrs"


def test_material_prefers_the_attribute_over_the_title():
    p = parse_item(item(title="Astor Sofa - Black Air Leather", **{"Material": "Velvet"}))
    assert p.material == "velvet"
    assert p.material_source == "attrs"


def test_material_falls_back_to_the_title_when_no_attribute_is_present():
    p = parse_item(item(title="Astor Sofa - Black Air Leather"))
    assert p.material == "leather"
    assert p.material_source == "title"


def test_material_attribute_key_variants_are_all_read():
    for key in ("Material", "Fabric Type", "Frame Material", "Top Material Type"):
        p = parse_item(item(title="Generic Table", **{key: "Solid Oak"}))
        assert p.material == "oak", key
        assert p.material_source == "attrs", key


def test_a_longer_material_attribute_value_is_still_trusted_when_it_names_a_real_material():
    """Same policy as Color, for the same reason: real Material values are
    usually short ("Canvas", "Fabric") but not always — "Alloy Steel,
    Plywood, Wood" and "High Quality Particle Wood (mdf) Wood Grain Oak
    Veneer Metal Hinge" are both real. No length cutoff is applied here
    either."""
    p = parse_item(item(title="Generic Sofa",
                        **{"Material": "Premium Handwoven Belgian Linen Fabric Blend"}))
    assert p.material == "linen"
    assert p.material_source == "attrs"


def test_lowercase_product_dimensions_key_is_still_read():
    """Confirmed real key spelling is 'Product dimensions' (lowercase d),
    not the 'Product Dimensions' this parser was first written against."""
    p = parse_item(item(cat="sofa", title="Test Sofa",
                        **{"Product dimensions": "190 x 90 x 85 centimeters"}))
    assert p.dims.known
    assert p.dims_confidence == "parsed"


# --------------------------------------------------------------------------
# new categories: decor the catalogue does not stock yet, but is being
# scraped. No listing in the current capture has these titles, so these
# fixtures are synthetic — shaped like real amazon.sa decor listings.
# --------------------------------------------------------------------------

# A deliberately wrong `cat=` on every test below: `_classify` falls back to
# `search_category` when no pattern matches, so a fixture that happens to
# set `cat=` to the category it expects would pass even with no pattern at
# all. Using a mismatched search_category forces the assertion through the
# actual regex.

def test_wall_art_is_classified():
    p = parse_item(item(cat="sofa", title="Modern Abstract Canvas Wall Art Print Set of 3"))
    assert p.category == "wall_art"


def test_mirror_beats_the_wall_art_pattern_even_when_marketed_as_wall_decor():
    """'Wall Decor' is also how sellers describe framed prints, so wall_art
    would otherwise swallow this. 'mirror' names a different object with a
    different search query and has to win first."""
    p = parse_item(item(cat="sofa", title="Round Wall Decor Mirror for Living Room"))
    assert p.category == "mirror"


def test_wall_mounted_mirror_is_not_swallowed_by_the_accessory_pattern():
    """The accessory pattern matches 'wall mount' (monitor mounts, TV mounts),
    which is a substring of 'wall mounted mirror' too. The narrower mirror
    pattern has to be tried before accessory."""
    p = parse_item(item(cat="sofa", title="Wall Mounted Mirror, Round Gold Frame"))
    assert p.category == "mirror"


def test_plant_is_classified():
    p = parse_item(item(cat="sofa", title="Artificial Fiddle Leaf Fig Tree 150cm Potted Plant"))
    assert p.category == "plant"


def test_vase_is_classified():
    p = parse_item(item(cat="sofa", title="Large Ceramic Floor Vase 60cm"))
    assert p.category == "vase"


def test_nightstand_is_classified():
    p = parse_item(item(cat="sofa", title="2 Drawer Bedside Table Nightstand Oak"))
    assert p.category == "nightstand"


def test_dining_chairs_pair_is_classified():
    p = parse_item(item(cat="sofa", title="Set of 2 Dining Chairs Fabric Grey"))
    assert p.category == "dining_chairs_pair"


def test_dining_table_with_bundled_chairs_still_reads_as_a_table():
    """'Dining Table and 4 Chairs Set' is a table listing, not a pair of
    chairs — dining_table has to be tried before dining_chairs_pair."""
    p = parse_item(item(cat="sofa", title="Dining Table and 4 Chairs Set, White"))
    assert p.category == "dining_table"


def test_the_real_search_term_for_dining_chairs_is_not_flagged_as_a_mismatch():
    """Confirmed real search_category is 'dining_chairs' (no '_pair'), but
    'dining_chairs_pair' is the category/role name geometry.py and the
    renderer already key on. A genuinely correct dining-chair listing must
    not be flagged category_mismatch purely because the two names are spelled
    differently for the same thing."""
    p = parse_item(item(cat="dining_chairs",
                        title="HXDream Dining Chair Set 2 Pcs, Kitchen Chair, Modern Accent Chair"))
    assert p.category == "dining_chairs_pair"
    assert "category_mismatch" not in p.flags


# --------------------------------------------------------------------------
# hung goods: wall_art and mirror have no floor footprint. Their listed
# "Item Dimensions L x W" is height x width, not the rug's "long side is the
# width, short side is the depth" — reading it with the rug rule turned a
# 90x60cm print into "90 wide, 2 tall, 60 deep": a picture lying flat.
#
# amazon.sa never states a depth for these at all. Any thickness used so the
# piece has a placeable footprint is therefore an assumption, not a reading —
# this project marks assumptions rather than burying them, so it is recorded
# as the `assumed_depth` flag rather than silently folded into `dims`.
# --------------------------------------------------------------------------

def test_wall_art_axis_labelled_l_by_w_reads_as_height_by_width():
    p = parse_item(item(cat="wall_art", title="Framed Canvas Print",
                        **{"Item Dimensions L x W": "90L x 60W centimeters"}))
    assert p.dims.h == 90
    assert p.dims.w == 60
    assert p.dims.d == pytest.approx(3.0)
    assert "implausible_for_category" not in p.flags
    assert "assumed_depth" in p.flags


def test_wall_art_with_a_stated_depth_is_not_flagged_as_assumed():
    """When amazon.sa does state a thickness — a canvas listed as D x W x H —
    it is a real reading, not a placeholder, and must not carry the caveat
    that says otherwise."""
    p = parse_item(item(cat="wall_art", title="Framed Canvas Print",
                        **{"Item Dimensions D x W x H": "3D x 90W x 60H centimeters"}))
    assert p.dims.d == 3
    assert "assumed_depth" not in p.flags


def test_mirror_axis_labelled_l_by_w_gets_the_same_treatment():
    p = parse_item(item(cat="mirror", title="Full Length Mirror",
                        **{"Item Dimensions L x W": "160L x 40W centimeters"}))
    assert p.dims.h == 160
    assert p.dims.w == 40
    assert "implausible_for_category" not in p.flags


def test_rug_l_by_w_reading_is_unaffected_by_the_hangable_fix():
    """Rugs still get the floor-footprint reading: a rug is not hung."""
    p = parse_item(item(cat="rug", title="Area Rug",
                        **{"Item Dimensions L x W": "3L x 2W Meters"}))
    assert {p.dims.w, p.dims.d} == {300, 200}
    assert p.dims.h is not None and p.dims.h < 6


def test_wall_art_bare_two_value_dimensions_default_a_depth_not_a_height():
    p = parse_item(item(cat="wall_art", title="Framed Print",
                        **{"Item Dimensions": "80 x 50 centimeters"}))
    assert p.dims.d == pytest.approx(3.0)
    assert {p.dims.w, p.dims.h} == {80, 50}
    assert p.dims_confidence == "parsed"
    assert "assumed_depth" in p.flags


def test_a_tiny_wall_art_dimension_is_flagged_implausible():
    p = parse_item(item(cat="wall_art", title="Postage Stamp Print",
                        **{"Item Dimensions L x W": "5L x 5W centimeters"}))
    assert "implausible_for_category" in p.flags


def test_a_duplicated_l_by_w_field_does_not_flag_itself_as_conflicting():
    """Real listing B0GRFG1RW4: 'Product dimensions' and 'Item Dimensions L x
    W' both say '2L x 3W Meters' — the same two numbers under two field
    names, not a contradiction. The old comparison padded the labelled
    reading to (w, d, h)=[300, 200, 2] before comparing it against the bare
    field's two numbers, so an agreeing pair looked like a 3-vs-2 count
    mismatch and a rug that agrees with itself came out 'conflicted'."""
    p = parse_item(item(cat="rug", title="Area Rug", **{
        "Item Dimensions L x W": "2L x 3W Meters",
        "Product dimensions": "2L x 3W Meters",
    }))
    assert "dimension_conflict" not in p.flags
    assert p.dims_confidence == "stated"


def test_a_duplicated_wall_art_l_by_w_field_does_not_flag_itself_either():
    """Same bug, hangable path: the assumed depth must not be compared
    against a bare field that never claimed to state one."""
    p = parse_item(item(cat="wall_art", title="Framed Print", **{
        "Item Dimensions L x W": "40L x 30W centimeters",
        "Product dimensions": "40L x 30W centimeters",
    }))
    assert "dimension_conflict" not in p.flags


# --------------------------------------------------------------------------
# curl-rendered pages carry Amazon's payment-plan widget as attr keys
# ("3 months", "6 months", "9 months", "12 months", "24 months"). 89 items
# have at least one of these; they are not product attributes and were kept
# verbatim per the capture's contract. They must not be mistaken for a
# dimension field (the "dimensions" key-regex) or read as a colour/material.
# --------------------------------------------------------------------------

def test_payment_plan_attr_keys_are_not_mistaken_for_dimensions_or_material():
    p = parse_item(item(cat="sofa", title="Interwood Astor Sofa", **{
        "Item Dimensions D x W x H": "85D x 190W x 85H centimeters",
        "3 months": "SAR 333.33/mo", "6 months": "SAR 166.67/mo",
        "9 months": "SAR 111.11/mo", "12 months": "SAR 83.33/mo",
        "24 months": "SAR 41.67/mo",
    }))
    assert (p.dims.w, p.dims.d, p.dims.h) == (190, 85, 85)
    assert p.dims_source == "Item Dimensions D x W x H"
    assert p.material is None


def test_a_normal_sized_wall_art_is_not_flagged_implausible():
    p = parse_item(item(cat="wall_art", title="Large Framed Canvas Print",
                        **{"Item Dimensions L x W": "120L x 80W centimeters"}))
    assert "implausible_for_category" not in p.flags


class TestMessyReviewCounts:
    """amazon.sa renders the review count as "(2)" on some pages and a bare
    "1,204" on others. The parser used int() straight off the string, so the
    second scrape — which picked up the parenthesised form — made the whole
    catalogue fail to load rather than one product lose its review count."""

    def test_a_parenthesised_count_is_read_as_a_number(self):
        assert _review_count("(2)") == 2
        assert _review_count("(1,204)") == 1204

    def test_a_bare_count_still_works(self):
        assert _review_count("812") == 812
        assert _review_count("1,204") == 1204

    def test_anything_unreadable_counts_as_no_reviews(self):
        for junk in ("", None, "Let us know", "-"):
            assert _review_count(junk) == 0


# --------------------------------------------------------------------------
# per-axis plausibility: the largest extent is not the whole story
#
# B0DV7MZK5D is a 39,264 SAR "Transformer Table Solid Wood Extendable Round
# Dining Table Set" that parsed as 76.2w x 43.2d x 7.6h cm with confidence
# `stated`, no flags at all, and `usable: True`. The 7.6 cm is the listing's
# own tabletop *thickness* — its attrs say `Tabletop Thickness: "3 Inches"` —
# read as if it were the table's height. The max-extent check passed it
# because 76.2 cm is a perfectly ordinary number for a dining table.
#
# A mislabelled axis is invisible to a check that only looks at the biggest
# one. Each axis has to be judged on its own terms.
# --------------------------------------------------------------------------

class TestPerAxisPlausibility:
    def test_the_transformer_table_no_longer_passes_as_usable(self):
        """The concrete listing that motivated the whole check."""
        table = next(p for p in parse_capture() if p.asin == "B0DV7MZK5D")
        assert table.category == "dining_table"
        assert table.dims.h == pytest.approx(7.6)
        assert "implausible_for_category" in table.flags
        assert table.usable is False

    def test_a_dining_table_the_height_of_its_own_tabletop_is_flagged(self):
        p = parse_item(item(cat="dining_table", title="Round Dining Table",
                            **{"Item Dimensions D x W x H":
                               "43.2D x 76.2W x 7.6H centimeters"}))
        assert "implausible_for_category" in p.flags
        assert p.usable is False

    def test_a_dining_table_of_ordinary_height_is_left_alone(self):
        p = parse_item(item(cat="dining_table", title="Round Dining Table",
                            **{"Item Dimensions D x W x H":
                               "90D x 140W x 75H centimeters"}))
        assert p.flags == []
        assert p.usable is True

    def test_a_two_centimetre_rug_is_correct_and_must_not_be_flagged(self):
        """Rugs really are ~2 cm tall. A blanket "nothing is under 20 cm tall"
        rule would reject every rug in the catalogue."""
        p = parse_item(item(cat="rug", title="Area Rug",
                            **{"Item Dimensions L x W": "3L x 2W Meters"}))
        assert "implausible_for_category" not in p.flags
        assert p.usable is True

    def test_a_three_centimetre_deep_wall_art_is_correct_and_must_not_be_flagged(self):
        """`DEFAULT_HANGABLE_DEPTH_CM` is 3 cm by design — a frame is thin.
        The depth axis of a hung piece is not evidence of a bad parse."""
        p = parse_item(item(cat="wall_art", title="Framed Canvas Print",
                            **{"Item Dimensions L x W": "90L x 60W centimeters"}))
        assert "implausible_for_category" not in p.flags
        assert p.usable is True

    def test_a_floor_lamp_ten_centimetres_tall_is_flagged(self):
        """Real: B0F37YHHTX parses to 10 cm tall. A floor lamp stands on the
        floor and reaches a shade; 10 cm is a puck light or a bad axis read."""
        p = parse_item(item(cat="floor_lamp", title="Modern Floor Lamp",
                            **{"Item Dimensions D x W x H":
                               "30D x 10W x 10H centimeters"}))
        assert "implausible_for_category" in p.flags

    def test_a_wardrobe_two_centimetres_tall_is_flagged(self):
        """Real: three "wardrobe" listings pad to h=2.0 because the bare field
        held only two numbers. A 2 cm wardrobe is not a wardrobe."""
        p = parse_item(item(cat="wardrobe", title="3 Door Wardrobe",
                            **{"Item Dimensions": "183 x 183 centimeters"}))
        assert "implausible_for_category" in p.flags

    def test_a_dining_table_too_narrow_to_eat_at_is_flagged_on_footprint(self):
        """The other half of the same landmine: 43.2 cm is not a depth a
        dining table can have, independently of what its height says. Checking
        only height would let the next mislabelled listing through on the axis
        this one happened not to break."""
        p = parse_item(item(cat="dining_table", title="Round Dining Table",
                            **{"Item Dimensions D x W x H":
                               "43.2D x 76.2W x 75H centimeters"}))
        assert "implausible_for_category" in p.flags

    def test_a_34cm_deep_bed_is_flagged_on_footprint(self):
        """Real: B0FZBJXHKM, "l'elefante Single Metal Bed Frame 90x190 cm",
        parses to w=120.0 x d=34.0 x h=91.0 with `dims_confidence: parsed` --
        the title's own 90x190cm is nowhere in that triple. `bed` was simply
        absent from `PLAUSIBLE_MIN_FOOTPRINT_CM` (the docstring's exemption
        list names rug, bookshelf, wall_art, mirror, coffee_table, plant and
        vase on purpose; bed was never one of them, just left out), so a
        34cm-deep bed -- no mattress is that narrow -- passed both the extent
        check (120cm, an unremarkable largest side) and the height check
        (91cm, a plausible headboard) and came back `usable`. It was then
        placed, unremarked, in real generated bedroom plans (unit01/bedroom
        and others at the premium tier), where `bed_access` and
        `bedside_reach` computed real-looking clearances against a bed that
        cannot exist.
        """
        bed = next(p for p in parse_capture() if p.asin == "B0FZBJXHKM")
        assert bed.category == "bed"
        assert bed.dims.d == pytest.approx(34.0)
        assert "implausible_for_category" in bed.flags
        assert bed.usable is False

    def test_beds_with_a_believable_footprint_are_not_flagged(self):
        p = parse_item(item(cat="bed", title="Queen Platform Bed Frame",
                            **{"Item Dimensions D x W x H":
                               "160D x 200W x 35H centimeters"}))
        assert "implausible_for_category" not in p.flags
        assert p.usable is True


# --------------------------------------------------------------------------
# price plausibility
#
# B0FG1849P3 is a 185,350.38 SAR "coffee table" from seller "ZZZXCDSX". The
# live page was checked by hand: that really is the listed price, not a
# scraping bug. 36 items in this capture are over 15,000 SAR and 26 of them
# publish no dimensions at all — the expensive end of this assortment is
# where the data is worst, which is exactly backwards from where trust should
# go. Same treatment as a dimension conflict: flag it and keep it, so an
# agent can find it and dismiss it, rather than quietly ranking it.
# --------------------------------------------------------------------------

class TestPricePlausibility:
    def test_the_zzzxcdsx_coffee_table_is_flagged(self):
        p = next(x for x in parse_capture() if x.asin == "B0FG1849P3")
        assert p.price_sar == pytest.approx(185350.38)
        assert "implausible_price" in p.flags

    def test_an_ordinary_priced_item_is_not_flagged(self):
        for p in parse_capture():
            if p.category == "sofa" and p.price_sar and p.price_sar < 4000:
                assert "implausible_price" not in p.flags

    def test_a_flagged_item_is_still_in_the_catalogue(self):
        """Flag, don't drop. An agent has to be able to see the 185,350 SAR
        coffee table in order to say out loud that it is dismissing it."""
        assert any("implausible_price" in p.flags for p in parse_capture())

    def test_a_category_with_too_thin_a_sample_is_not_judged(self):
        """A median over two prices is not a market rate, and flagging against
        it would invent an outlier out of a small sample.

        Counts every priced listing, matching the baseline the check itself
        uses. It used to count only `usable` ones, which stopped agreeing with
        the implementation the moment that baseline widened."""
        from app.catalog import MIN_PRICED_SAMPLE_FOR_MEDIAN
        import collections
        counts = collections.Counter(
            p.category for p in parse_capture() if p.price_sar)
        for p in parse_capture():
            if counts[p.category] < MIN_PRICED_SAMPLE_FOR_MEDIAN:
                assert "implausible_price" not in p.flags, p.asin


# --------------------------------------------------------------------------
# "Sofa" as an adjective
#
# The sofa pattern is `\bsofa\b`, and sellers use the word to say what a piece
# stands *next to*: "Clear Sofa End Table" (B0FQ37LJYH), "Sofa End TableTop"
# (B0FKMVD68V), "Side Table for Sofa & Couch" (B0G7KHHNDH). All five affected
# listings are C-shaped acrylic side tables and all five classified as `sofa`.
#
# They were kept out of plans only because `implausible_for_category` caught
# them on size, which is luck rather than correctness: a side table that
# happened to land inside the 120-400 cm sofa band would have been placed in
# the primary seating slot of a living room. Same fix as the armchair-before-
# sofa ordering above — the narrower reading has to be tried first.
#
# They are classified `coffee_table` because that is where this capture
# already puts the identical product when the title happens to say "coffee"
# too (B0H58F48XJ, B0B6PG26ZF), and inventing a `side_table` category for
# five listings would split one kind of object across two names.
# --------------------------------------------------------------------------

class TestSofaIsNotAnAdjective:
    def test_the_two_reported_side_tables_are_not_sofas(self):
        catalog = {p.asin: p for p in parse_capture()}
        for asin in ("B0FQ37LJYH", "B0FKMVD68V"):
            assert catalog[asin].category != "sofa", asin

    def test_no_listing_classified_sofa_is_a_side_table(self):
        """The whole `sofa` class, not just the two that were reported."""
        for p in parse_capture():
            if p.category == "sofa":
                assert not re.search(r"\b(side|end)\s+tables?\b", p.title, re.I), p.asin
                assert not re.search(r"c[\s-]?shaped", p.title, re.I), p.asin

    def test_a_sofa_end_table_reads_as_a_table(self):
        p = parse_item(item(cat="nightstand",
                            title="Clear Acrylic C Shaped Side Table 30x30x65 cm Sofa End Table"))
        assert p.category == "coffee_table"

    def test_a_plain_sofa_is_still_a_sofa(self):
        p = parse_item(item(cat="sofa", title="Interwood Kissel 3 Seater Sofa"))
        assert p.category == "sofa"

    def test_an_accent_chair_marketed_as_a_single_sofa_is_still_an_armchair(self):
        """The ordering this must not regress."""
        p = parse_item(item(cat="sofa", title="Single Sofa Accent Chair Velvet"))
        assert p.category == "armchair"


class TestPriceOutliersYieldToRealEvidence:
    """A median-multiple test is a proxy for "nobody vouches for this price".

    Hundreds of buyers at that price ARE the vouching, so the proxy has to
    yield to them. Found by tracing why a 30,000 SAR bedroom brief still
    bought a 329 SAR bed: `B08569KN5F` — a ZINUS platform bed, 15,385 SAR,
    4.6 stars, 1,313 reviews, dimensions stated — was flagged
    `implausible_price` for sitting 46x above a category median set by
    300 SAR metal frames, and `candidates_for` drops flagged items. The
    guardrail was deleting the single best-evidenced expensive item in the
    catalogue, which is exactly the item the tier was meant to buy.
    """

    def test_a_well_reviewed_expensive_listing_is_not_called_implausible(self):
        zinus = next(p for p in parse_capture() if p.asin == "B08569KN5F")
        assert zinus.reviews >= 1000 and zinus.rating
        assert "implausible_price" not in zinus.flags

    def test_an_unreviewed_expensive_listing_is_still_flagged(self):
        """The 185,350 SAR coffee table from seller ZZZXCDSX has no reviews
        at all. Nothing vouches for it, so the proxy still applies."""
        junk = next(p for p in parse_capture() if p.asin == "B0FG1849P3")
        assert junk.reviews == 0
        assert "implausible_price" in junk.flags


class TestThePriceOutlierBaselineIsNotSkewedByMissingDimensions:
    """The cutoff was a multiple of the median over *usable* listings only.

    That subset skews cheap, because dear listings omit their dimensions far
    more often than cheap ones do — so the baseline was set by the bargain
    tail and the cutoff landed inside the normal range. A wardrobe category
    whose usable median was 664 SAR called anything over 3,984 implausible,
    which is an ordinary price for a wardrobe. Six dimensioned, sensibly-sized,
    genuinely-reviewed items were being kept out of recommendations by it.

    Taking the median over every priced listing in the category removes the
    bias without loosening what the check is for.
    """

    def test_a_real_dining_set_priced_above_a_single_chair_is_not_implausible(self):
        """Yaheetech dining chairs, set of 4, 3,445 SAR, 44 reviews. The
        `dining_chairs` median is set by single chairs, so a set of four looks
        like an outlier against it and is not one."""
        p = next(x for x in parse_capture() if x.asin == "B0C4KTZ1H6")
        assert "implausible_price" not in p.flags

    def test_the_grey_market_coffee_table_is_still_caught(self):
        p = next(x for x in parse_capture() if x.asin == "B0FG1849P3")
        assert p.price_sar > 180_000
        assert "implausible_price" in p.flags


class TestAnUnmatchedTitleIsNotTakenOnTrust:
    """`_classify` falls back to the search term when no pattern matches, and
    that fallback was being treated as a fact.

    Searching "wall mirror decorative" returned `B0G3X188XF`, an aluminium
    artist's easel. Nothing in its title says mirror, so it was flagged
    `unclassified` — and then planned as a mirror anyway, because a flag that
    nothing acts on is decoration. Three easels were eligible to be hung on a
    wall as mirrors.

    The fix keeps them in the index (an agent must be able to find and dismiss
    one) and out of recommendations, which is exactly how `implausible_price`
    already behaves.
    """

    def test_an_easel_returned_by_a_mirror_search_is_not_recommendable(self):
        from app.planner import candidates_for
        easel = next(p for p in parse_capture() if p.asin == "B0G3X188XF")
        assert "unclassified" in easel.flags
        pool = candidates_for({"category": easel.category}, list(parse_capture()),
                              100_000, [], None)
        assert easel.asin not in {c.asin for c in pool}

    def test_it_is_still_in_the_catalogue_to_be_dismissed(self):
        assert any(p.asin == "B0G3X188XF" for p in parse_capture())


class TestAnAdjectiveDoesNotHideAPlant:
    """`artificial (plant|tree)` needs the two words adjacent, so every
    "Artificial Olive Tree" in the capture fell through to the search-term
    fallback and was flagged unclassified — six real plants, described exactly
    as a seller would describe them."""

    def test_an_artificial_olive_tree_classifies_as_a_plant(self):
        p = next(x for x in parse_capture() if x.asin == "B0H4LVT6QB")
        assert p.category == "plant"
        assert "unclassified" not in p.flags
