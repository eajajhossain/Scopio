from app.services.discovery.normalizer import classify, normalize_element, normalize_many


def test_classify_food_and_health_and_retail():
    assert classify({"amenity": "restaurant"}) == "food"
    assert classify({"shop": "bakery"}) == "food"
    assert classify({"amenity": "pharmacy"}) == "health"
    assert classify({"amenity": "bank"}) == "finance"
    assert classify({"office": "lawyer"}) == "services"
    assert classify({"craft": "tailor"}) == "services"
    assert classify({"shop": "clothes"}) == "retail"
    assert classify({"tourism": "hotel"}) == "hospitality"
    assert classify({"healthcare": "physiotherapist"}) == "health"
    assert classify({"leisure": "fitness_centre"}) == "services"
    assert classify({"man_made": "tower"}) == "other"


def test_normalize_node_with_contact():
    element = {
        "type": "node",
        "id": 123,
        "lat": 22.7211,
        "lon": 88.4827,
        "tags": {
            "name": "Maa Tara Sweets",
            "shop": "confectionery",
            "addr:street": "Jessore Rd",
            "addr:postcode": "700125",
            "contact:phone": "+91 90000 00000",
        },
    }
    norm = normalize_element(element)
    assert norm is not None
    assert norm["name"] == "Maa Tara Sweets"
    assert norm["category"] == "retail"
    assert norm["phone"] == "+91 90000 00000"
    assert norm["source_ref"] == "node/123"
    assert "Jessore Rd" in norm["address"]
    assert norm["dedup_key"]


def test_normalize_way_uses_center():
    element = {
        "type": "way",
        "id": 9,
        "center": {"lat": 22.72, "lon": 88.48},
        "tags": {"name": "City Bank", "amenity": "bank"},
    }
    norm = normalize_element(element)
    assert norm["lat"] == 22.72 and norm["lng"] == 88.48
    assert norm["category"] == "finance"


def test_unnamed_element_is_dropped():
    assert normalize_element({"type": "node", "id": 1, "tags": {"shop": "kiosk"}}) is None


def test_normalize_many_filters_unnamed():
    elements = [
        {"type": "node", "id": 1, "tags": {"name": "A", "shop": "x"}},
        {"type": "node", "id": 2, "tags": {"shop": "y"}},  # no name -> dropped
    ]
    assert len(normalize_many(elements)) == 1
