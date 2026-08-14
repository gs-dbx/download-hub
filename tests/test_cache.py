"""Unit tests for the pure per-user snapshot cache in ``app.cache``.

No fastapi, no databricks.sdk, no pyspark, no network — runs offline
(LOCKED DECISION L2).
"""

import time

from app.cache import (
    Snapshot,
    SnapshotCache,
    apply_filters,
    apply_search,
    distinct_values,
    make_key,
    paginate,
)


def _snap(rows=None) -> Snapshot:
    """Build a trivial Snapshot with the current fetch time."""
    return Snapshot(columns=["a"], rows=rows or [], fetched_at=time.time())


# --- make_key ------------------------------------------------------------


def test_make_key_is_tuple():
    """make_key returns the (user, report, date) tuple."""
    assert make_key("u@x", "r1", "2026-01-12 00:00:00") == (
        "u@x",
        "r1",
        "2026-01-12 00:00:00",
    )


# --- SnapshotCache LRU / MRU / TTL ---------------------------------------


def test_cache_lru_eviction_beyond_max_size():
    """Adding beyond max_size evicts the least-recently-used entry."""
    c = SnapshotCache(max_size=2)
    c.put(make_key("u", "r", "d1"), _snap())
    c.put(make_key("u", "r", "d2"), _snap())
    c.put(make_key("u", "r", "d3"), _snap())
    assert len(c) == 2
    assert c.get(make_key("u", "r", "d1")) is None  # d1 was LRU -> evicted
    assert c.get(make_key("u", "r", "d2")) is not None
    assert c.get(make_key("u", "r", "d3")) is not None


def test_cache_get_moves_to_mru():
    """get() marks an entry MRU so a later insert evicts the other one."""
    c = SnapshotCache(max_size=2)
    c.put(make_key("u", "r", "d1"), _snap())
    c.put(make_key("u", "r", "d2"), _snap())
    # Touch d1 so it becomes MRU; d2 is now the LRU.
    assert c.get(make_key("u", "r", "d1")) is not None
    c.put(make_key("u", "r", "d3"), _snap())
    assert c.get(make_key("u", "r", "d2")) is None  # d2 evicted, not d1
    assert c.get(make_key("u", "r", "d1")) is not None


def test_cache_ttl_expiry():
    """A snapshot older than ttl_seconds is treated as a miss and dropped."""
    c = SnapshotCache(max_size=8, ttl_seconds=10.0)
    key = make_key("u", "r", "d1")
    # fetched_at far in the past -> expired.
    c.put(key, Snapshot(columns=["a"], rows=[{"a": "1"}], fetched_at=time.time() - 100.0))
    assert c.get(key) is None
    assert len(c) == 0  # expired entry removed


def test_cache_ttl_fresh_hit():
    """A fresh snapshot within the TTL is returned."""
    c = SnapshotCache(max_size=8, ttl_seconds=100.0)
    key = make_key("u", "r", "d1")
    c.put(key, _snap([{"a": "1"}]))
    got = c.get(key)
    assert got is not None
    assert got.rows == [{"a": "1"}]


def test_cache_evict_removes():
    """evict() removes a key; a second evict is a no-op."""
    c = SnapshotCache(max_size=8)
    key = make_key("u", "r", "d1")
    c.put(key, _snap())
    c.evict(key)
    assert c.get(key) is None
    c.evict(key)  # no error on missing key
    assert len(c) == 0


# --- apply_filters -------------------------------------------------------


_FILTER_ROWS = [
    {"channel": "ALL", "region": "NE", "metric_name": "X"},
    {"channel": "E", "region": "NE", "metric_name": "Y"},
    {"channel": "E", "region": "SW", "metric_name": "Z"},
]


def test_apply_filters_equality_and_across_two_fields():
    """Two non-empty filters compose with AND."""
    out = apply_filters(_FILTER_ROWS, {"channel": "E", "region": "NE"})
    assert len(out) == 1
    assert out[0]["metric_name"] == "Y"


def test_apply_filters_empty_value_is_no_constraint():
    """An empty-string value places no constraint on that field."""
    out = apply_filters(_FILTER_ROWS, {"channel": "", "region": "NE"})
    assert {r["metric_name"] for r in out} == {"X", "Y"}


def test_apply_filters_all_empty_returns_all():
    """All-empty filters return every row."""
    out = apply_filters(_FILTER_ROWS, {"channel": "", "region": ""})
    assert len(out) == len(_FILTER_ROWS)


def test_apply_filters_missing_field_matches_nothing():
    """A filter on an absent field compares against '' and matches nothing."""
    out = apply_filters(_FILTER_ROWS, {"nope": "x"})
    assert out == []


# --- apply_search --------------------------------------------------------


def _hay(r: dict) -> str:
    return r["metric_name"]


def test_apply_search_substring_hit_case_insensitive():
    """A substring match is case-insensitive over the haystack text."""
    rows = [{"metric_name": "ERO Accepted"}, {"metric_name": "Online"}]
    out = apply_search(rows, "ero", _hay)
    assert len(out) == 1
    assert out[0]["metric_name"] == "ERO Accepted"


def test_apply_search_miss_returns_empty():
    """A query that matches nothing returns an empty list."""
    rows = [{"metric_name": "ERO"}]
    assert apply_search(rows, "zzz", _hay) == []


def test_apply_search_empty_query_returns_all():
    """An empty/whitespace query returns all rows unchanged."""
    rows = [{"metric_name": "A"}, {"metric_name": "B"}]
    assert apply_search(rows, "   ", _hay) == rows


# --- distinct_values -----------------------------------------------------


def test_distinct_values_sorted_dedup_null_skip():
    """Distinct values are sorted, de-duplicated, and skip None/absent."""
    rows = [
        {"channel": "E"},
        {"channel": "ALL"},
        {"channel": "E"},
        {"channel": None},
        {},  # absent field
    ]
    assert distinct_values(rows, "channel") == ["ALL", "E"]


# --- paginate ------------------------------------------------------------


def test_paginate_page_slicing():
    """A middle page slices the right window and reports totals."""
    rows, total, pages = paginate(list(range(10)), 2, 3)
    assert rows == [3, 4, 5]
    assert total == 10
    assert pages == 4


def test_paginate_clamps_beyond_last_page():
    """A page number beyond the last page clamps to the last page."""
    rows, total, pages = paginate(list(range(10)), 99, 3)
    assert rows == [9]
    assert pages == 4


def test_paginate_size_none_is_all_single_page():
    """size=None returns all rows on a single page."""
    rows, total, pages = paginate(list(range(10)), 1, None)
    assert rows == list(range(10))
    assert total == 10
    assert pages == 1


def test_paginate_size_zero_is_all():
    """size<=0 is treated as 'All' (one page)."""
    rows, total, pages = paginate(list(range(5)), 1, 0)
    assert rows == list(range(5))
    assert pages == 1
