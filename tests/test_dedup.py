from app.services.discovery.dedup import area_geohash, dedup_key, merge_by_dedup_key


def test_same_name_same_cell_same_key():
    a = dedup_key("Maa Tara Sweets", 22.7211, 88.4827)
    b = dedup_key("Maa Tara  Sweets", 22.72111, 88.48271)  # tiny jitter, same ~150m cell
    assert a == b


def test_different_name_different_key():
    a = dedup_key("Maa Tara Sweets", 22.7211, 88.4827)
    b = dedup_key("City Bank", 22.7211, 88.4827)
    assert a != b


def test_far_apart_same_name_different_key():
    a = dedup_key("Cafe", 22.7211, 88.4827)
    b = dedup_key("Cafe", 19.0760, 72.8777)  # Mumbai
    assert a != b


def test_missing_geo_uses_nogeo_suffix():
    assert dedup_key("Shop", None, None).endswith("_nogeo")


def test_area_geohash_is_coarser():
    gh = area_geohash(22.7211, 88.4827)
    assert isinstance(gh, str) and len(gh) == 6


def test_merge_collapses_inbatch_duplicates_and_merges_contacts():
    # Same dedup_key twice (node + way of the same place) — must collapse to one
    # so the ON CONFLICT insert doesn't hit the same row twice.
    batch = [
        {"dedup_key": "k1", "name": "Cafe A", "phone": None, "email": None, "website": "a.com"},
        {"dedup_key": "k1", "name": "Cafe A", "phone": "+91 90000 00000", "email": None, "website": None},
        {"dedup_key": "k2", "name": "Bank B", "phone": None, "email": None, "website": None},
    ]
    out = merge_by_dedup_key(batch)
    assert len(out) == 2  # k1 collapsed
    k1 = next(b for b in out if b["dedup_key"] == "k1")
    assert k1["phone"] == "+91 90000 00000"  # merged from the second row
    assert k1["website"] == "a.com"          # kept from the first row


def test_merge_keeps_distinct_keys():
    batch = [{"dedup_key": "a", "name": "X"}, {"dedup_key": "b", "name": "Y"}]
    assert len(merge_by_dedup_key(batch)) == 2
