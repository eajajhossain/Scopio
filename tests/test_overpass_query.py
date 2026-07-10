"""build_query() targets only the seller's business types when osm_filters are given."""
from app.services.discovery.overpass import build_query

LAT, LNG, R = 51.51, -0.13, 2000


def test_broad_query_without_filters():
    q = build_query(LAT, LNG, R)
    assert 'nwr["shop"]' in q
    assert 'nwr["amenity"~"' in q
    assert f"around:{R},{LAT},{LNG}" in q


def test_targeted_query_uses_only_given_filters():
    q = build_query(LAT, LNG, R, {"amenity": ["cafe", "restaurant"], "shop": ["bakery"]})
    assert 'nwr["amenity"~"cafe|restaurant"]' in q
    assert 'nwr["shop"~"bakery"]' in q
    # Broad catch-alls must NOT appear when targeting.
    assert 'nwr["office"]' not in q
    assert 'nwr["shop"](' not in q  # no untyped shop clause


def test_unsafe_values_are_dropped_and_fall_back_to_broad():
    # All values are unsafe (regex/injection chars) -> sanitized away -> broad default.
    q = build_query(LAT, LNG, R, {"amenity": ['cafe"]; out; //']})
    assert 'nwr["shop"]' in q  # fell back to broad
    assert '//' not in q
