from app.services.discovery.pipeline import _chunks


def test_chunks_splits_evenly():
    out = list(_chunks(list(range(10)), 4))
    assert out == [[0, 1, 2, 3], [4, 5, 6, 7], [8, 9]]


def test_chunks_empty():
    assert list(_chunks([], 500)) == []


def test_chunks_large_stays_under_param_cap():
    # 3000 businesses * 16 cols would be 48000 params in one INSERT (over 32767).
    # Batches of 500 keep each INSERT at 8000 params — safe.
    rows = list(range(3000))
    batches = list(_chunks(rows, 500))
    assert all(len(b) * 16 <= 32767 for b in batches)
    assert sum(len(b) for b in batches) == 3000
