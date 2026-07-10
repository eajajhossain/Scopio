from app.services.discovery.geoapify import classify, normalize_feature


def test_classify_maps_geoapify_prefixes():
    assert classify(["catering.restaurant"]) == "food"
    assert classify(["healthcare.hospital"]) == "health"
    assert classify(["commercial.supermarket"]) == "retail"
    assert classify(["accommodation.hotel"]) == "hospitality"
    assert classify(["office.company"]) == "services"
    assert classify(["something.unknown"]) == "other"


def test_normalize_feature_extracts_fields():
    feature = {
        "properties": {
            "name": "Maa Tara Sweets",
            "lat": 22.7211, "lon": 88.4827,
            "formatted": "Jessore Rd, Barasat, 700125",
            "categories": ["catering.restaurant"],
            "place_id": "abc123",
            "website": "https://maatara.in",
            "datasource": {"raw": {"phone": "+91 90000 11111", "email": "hi@maatara.in"}},
        }
    }
    b = normalize_feature(feature)
    assert b["source"] == "geoapify"
    assert b["name"] == "Maa Tara Sweets"
    assert b["category"] == "food"
    assert b["phone"] == "+91 90000 11111"
    assert b["email"] == "hi@maatara.in"
    assert b["website"] == "https://maatara.in"
    assert b["dedup_key"]   # same scheme as OSM, so cross-source dedup works


def test_normalize_feature_skips_unnamed():
    assert normalize_feature({"properties": {"categories": ["commercial"]}}) is None
