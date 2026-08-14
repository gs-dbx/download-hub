"""Unit tests for the pure, Spark-free daily metrics generator.

These tests import ``sample_report.generator`` directly (no Spark, no cluster) and assert
the invariants locked in the phase plan (L2 metric mapping, L3 pct_change + ALL-slice
recompute rule, L5 determinism).
"""

import os
import sys

# Make the src package importable without relying on PYTHONPATH.
_SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from sample_report.generator import (  # noqa: E402
    CHANNELS_BASE,
    METRICS,
    REPORT_DATES,
    build_metric_rows,
    pct_change,
)

_VALID_GROUPS = {"traffic", "engagement", "conversion", "revenue", "other"}

# Exact metric_name -> (metric_group, sort_order) from LOCKED DECISION L2.
_EXPECTED_METRICS = [
    ("Visits", "traffic", 1),
    ("Unique Visitors", "traffic", 2),
    ("New Visitors", "traffic", 3),
    ("Returning Visitors", "traffic", 4),
    ("Signups", "engagement", 5),
    ("Logins", "engagement", 6),
    ("Active Sessions", "engagement", 7),
    ("Add to Cart", "conversion", 8),
    ("Checkouts", "conversion", 9),
    ("Orders", "conversion", 10),
    ("Revenue", "revenue", 11),
    ("Refunds", "revenue", 12),
    ("Net Revenue", "revenue", 13),
    ("Avg Order Value", "revenue", 14),
    ("Support Tickets", "other", 15),
    ("Chat Sessions", "other", 16),
    ("Email Opens", "other", 17),
]


def _group_by_date_metric(rows: list[dict]) -> dict:
    """Group rows by (report_date, metric_name) -> {channel: row}."""
    grouped: dict = {}
    for row in rows:
        key = (row["report_date"], row["metric_name"])
        grouped.setdefault(key, {})[row["channel"]] = row
    return grouped


def test_metric_count_and_sort_order():
    """METRICS has 17 entries with unique sort_order 1..17 and exact L2 names/groups."""
    assert len(METRICS) == 17
    sort_orders = [m[2] for m in METRICS]
    assert sorted(sort_orders) == list(range(1, 18))
    assert len(set(sort_orders)) == 17

    actual = [(name, group, order) for (name, group, order, _base) in METRICS]
    assert actual == _EXPECTED_METRICS

    for _name, group, _order, _base in METRICS:
        assert group in _VALID_GROUPS


def test_row_cardinality():
    """build_metric_rows() returns 408 rows; 17 unique sort_orders per (report_date, channel)."""
    rows = build_metric_rows()
    assert len(rows) == 408

    per_slice: dict = {}
    for row in rows:
        per_slice.setdefault((row["report_date"], row["channel"]), []).append(row["sort_order"])

    # 6 dates x 4 channels = 24 slices, each with a complete unique 1..17.
    assert len(per_slice) == 24
    for orders in per_slice.values():
        assert sorted(orders) == list(range(1, 18))


def test_all_four_channels_present():
    """Channels are exactly {Web, Mobile, Partner, ALL}; Web/Mobile/Partner each carry 17 metrics for every date."""
    rows = build_metric_rows()
    assert {row["channel"] for row in rows} == {"Web", "Mobile", "Partner", "ALL"}

    for report_date in REPORT_DATES:
        for channel in CHANNELS_BASE:
            slice_rows = [
                r for r in rows if r["report_date"] == report_date and r["channel"] == channel
            ]
            assert len(slice_rows) == 17
            assert {r["metric_name"] for r in slice_rows} == {m[0] for m in METRICS}


def test_all_slice_is_elementwise_sum():
    """For each (report_date, metric), ALL.value_cy/py == sum of Web+Mobile+Partner."""
    grouped = _group_by_date_metric(build_metric_rows())
    for channels in grouped.values():
        sum_cy = channels["Web"]["value_cy"] + channels["Mobile"]["value_cy"] + channels["Partner"]["value_cy"]
        sum_py = channels["Web"]["value_py"] + channels["Mobile"]["value_py"] + channels["Partner"]["value_py"]
        assert channels["ALL"]["value_cy"] == sum_cy
        assert channels["ALL"]["value_py"] == sum_py


def test_all_slice_pct_recomputed_not_averaged():
    """ALL.pct_change is recomputed from summed cy/py — never the mean of per-channel pcts."""
    grouped = _group_by_date_metric(build_metric_rows())

    found_distinguishing_case = False
    for channels in grouped.values():
        sum_cy = channels["Web"]["value_cy"] + channels["Mobile"]["value_cy"] + channels["Partner"]["value_cy"]
        sum_py = channels["Web"]["value_py"] + channels["Mobile"]["value_py"] + channels["Partner"]["value_py"]
        expected = pct_change(sum_cy, sum_py)
        assert channels["ALL"]["pct_change"] == expected

        per_channel = [channels[c]["pct_change"] for c in CHANNELS_BASE]
        if all(p is not None for p in per_channel) and expected is not None:
            mean_of_pcts = round(sum(per_channel) / 3, 1)
            if abs(mean_of_pcts - expected) > 0.05:
                # A real generated row where averaging would give a different answer.
                found_distinguishing_case = True

    assert found_distinguishing_case, "expected at least one row where recompute != mean-of-pcts"

    # Constructed case: recompute must differ from the naive average of per-channel pcts.
    e_cy, e_py = 100, 50    # +100.0%
    m_cy, m_py = 100, 100   #    0.0%
    n_cy, n_py = 100, 200   #  -50.0%
    per_channel = [pct_change(e_cy, e_py), pct_change(m_cy, m_py), pct_change(n_cy, n_py)]
    mean_of_pcts = round(sum(per_channel) / 3, 1)
    recomputed = pct_change(e_cy + m_cy + n_cy, e_py + m_py + n_py)
    assert recomputed != mean_of_pcts


def test_pct_change_formula():
    """pct_change follows round((cy-py)/py*100, 1)."""
    assert pct_change(120, 100) == 20.0
    assert pct_change(90, 100) == -10.0
    assert pct_change(100, 100) == 0.0
    # rounds to one decimal place
    assert pct_change(1001, 1000) == 0.1


def test_pct_change_zero_py_is_none():
    """value_py == 0 yields None, both for the function and for generated rows."""
    assert pct_change(5, 0) is None
    assert pct_change(0, 0) is None

    rows = build_metric_rows()
    zero_py_rows = [r for r in rows if r["value_py"] == 0]
    assert zero_py_rows, "expected at least one generated row with value_py == 0"
    for row in zero_py_rows:
        assert row["pct_change"] is None


def test_non_negative_and_no_null_keys():
    """All values are non-negative ints; key columns are never None."""
    for row in build_metric_rows():
        assert isinstance(row["value_cy"], int) and row["value_cy"] >= 0
        assert isinstance(row["value_py"], int) and row["value_py"] >= 0
        assert row["report_date"] is not None
        assert row["channel"] is not None
        assert row["metric_name"] is not None
        assert row["sort_order"] is not None


def test_determinism():
    """Two calls with identical args produce identical output (LOCKED DECISION L5)."""
    assert build_metric_rows() == build_metric_rows()
    assert build_metric_rows(seed_salt="other") != build_metric_rows(seed_salt="daily-metrics-v1")
