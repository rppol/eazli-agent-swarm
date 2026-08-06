"""Catalog parser tests, driven entirely by real amazon.sa listings.

Every fixture below is a verbatim attribute set captured from a live product
page. The messiness is the point: units drift between metres, millimetres and
inches, the same listing contradicts itself across fields, axis labels are
sometimes wrong, and search results contain products from the wrong category
entirely. A parser that assumes clean input produces confident nonsense.
"""

import pytest

from app.catalog import parse_capture, parse_item


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

def test_package_dimensions_become_the_carton():
    p = parse_item(item(cat="wardrobe", title="MALMO 2 DOOR WARDROBE",
                        **{"Product Dimensions": "84 x 53 x 198 cm; 64 kg"}))
    assert p.carton is not None
    assert max(p.carton.w, p.carton.d, p.carton.h) == 198


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
    assert len(catalog) == 75


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
    assert len({p.asin for p in catalog}) == 75
    assert all(p.url.startswith("https://www.amazon.sa/") for p in catalog)
