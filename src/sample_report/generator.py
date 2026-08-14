"""Pure, Spark-free synthetic-data generator for the daily metrics gold table.

This module is the single source of truth for the metric list, ordering, and row math
that populates the daily metrics table. It has ZERO third-party imports (stdlib
only) so it can be unit-tested without Spark/JVM and executed inside an air-gapped
Databricks serverless job (see LOCKED DECISION L4).

Cardinality: 17 metrics x {Web, Mobile, Partner, ALL} x 6 report dates = 408 rows.
The ``ALL`` channel is DERIVED (element-wise sum of Web+Mobile+Partner) with its ``pct_change``
RECOMPUTED from the summed values — never averaged from the per-channel percentages
(LOCKED DECISION L3).
"""

from __future__ import annotations

import datetime
import hashlib
import random

# ---------------------------------------------------------------------------
# Config constants — single source of truth mirrored by the Phase 2 UI.
# ---------------------------------------------------------------------------

# LOCKED DECISION L2 — canonical metric -> group -> sort_order mapping (unique 1..17,
# PROJECT.md display order). ``base`` is a production-realistic magnitude for value_cy.
# Each entry: (metric_name, metric_group, sort_order, base_magnitude)
METRICS: list[tuple[str, str, int, int]] = [
    ("Visits", "traffic", 1, 2_500_000),
    ("Unique Visitors", "traffic", 2, 1_800_000),
    ("New Visitors", "traffic", 3, 1_200_000),
    ("Returning Visitors", "traffic", 4, 3_000_000),
    ("Signups", "engagement", 5, 120_000),
    ("Logins", "engagement", 6, 80_000),
    ("Active Sessions", "engagement", 7, 200_000),
    ("Add to Cart", "conversion", 8, 1_920_000),
    ("Checkouts", "conversion", 9, 1_280_000),
    ("Orders", "conversion", 10, 3_200_000),
    ("Revenue", "revenue", 11, 4_500_000_000),
    ("Refunds", "revenue", 12, 900_000),
    ("Net Revenue", "revenue", 13, 1_500_000),
    ("Avg Order Value", "revenue", 14, 1_100_000),
    ("Support Tickets", "other", 15, 1_800_000),
    ("Chat Sessions", "other", 16, 1_200_000),
    ("Email Opens", "other", 17, 2_400_000),
]

# LOCKED DECISION L1 — six fixed early-2026 business days, all at 00:00:00. No date is
# computed relative to "today". Prior-year (value_py) is the analogous 2025 value carried
# in the same row (not a separate report_date).
REPORT_DATES: list[datetime.datetime] = [
    datetime.datetime(2026, 1, 5, 0, 0, 0),
    datetime.datetime(2026, 1, 6, 0, 0, 0),
    datetime.datetime(2026, 1, 7, 0, 0, 0),
    datetime.datetime(2026, 1, 8, 0, 0, 0),
    datetime.datetime(2026, 1, 9, 0, 0, 0),
    datetime.datetime(2026, 1, 12, 0, 0, 0),
]

# The three real channels that are drawn; "ALL" is derived (summed), never drawn.
CHANNELS_BASE: list[str] = ["Web", "Mobile", "Partner"]
CHANNEL_ALL: str = "ALL"

# A single metric whose prior-year value is zero on the first report date, so the
# value_py == 0 -> NULL pct_change branch is deterministically exercised (both the
# per-channel rows and the derived ALL row for that date).
_ZERO_PY_METRIC: str = "Logins"


def pct_change(value_cy: int, value_py: int) -> float | None:
    """Compute percent change of current vs prior year, rounded to 1 decimal.

    Args:
        value_cy: Current-year (2026) value.
        value_py: Prior-year (2025) value.

    Returns:
        ``round((value_cy - value_py) / value_py * 100, 1)``, or ``None`` when
        ``value_py`` is 0 (LOCKED DECISION L3 — NULL, never divide by zero).
    """
    if value_py == 0:
        return None
    return round((value_cy - value_py) / value_py * 100, 1)


def _seed_for(report_date: datetime.datetime, channel: str, metric_name: str, salt: str) -> int:
    """Derive a deterministic integer seed for one (report_date, channel, metric) draw.

    Uses ``hashlib`` — NOT the builtin ``hash()`` — because builtin string hashing is
    salted by ``PYTHONHASHSEED`` and is not reproducible across processes (LOCKED
    DECISION L5).

    Args:
        report_date: The report snapshot date.
        channel: One of ``Web``, ``Mobile``, ``Partner``.
        metric_name: The metric label.
        salt: Global salt to version the whole dataset.

    Returns:
        A stable 64-bit unsigned integer seed.
    """
    key = f"{salt}|{report_date.isoformat()}|{channel}|{metric_name}"
    digest = hashlib.md5(key.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def _build_channel_rows(
    report_dates: list[datetime.datetime], seed_salt: str
) -> list[dict]:
    """Build the Web/Mobile/Partner rows (the real, drawn channels).

    For each (report_date, channel in Web/Mobile/Partner, metric): draw a deterministic ``value_cy``
    around the metric's base magnitude, derive ``value_py`` via a per-draw ratio that
    keeps ``pct_change`` in a realistic band (roughly -15%..+25%), and compute
    ``pct_change``. The designated zero-prior-year metric is forced to ``value_py == 0``
    on the first report date to exercise the NULL branch.

    Args:
        report_dates: The report snapshot dates to generate.
        seed_salt: Global determinism salt.

    Returns:
        A list of row dicts for channels Web, Mobile and Partner.
    """
    rows: list[dict] = []
    for report_date in report_dates:
        for channel in CHANNELS_BASE:
            for metric_name, metric_group, sort_order, base in METRICS:
                rng = random.Random(_seed_for(report_date, channel, metric_name, seed_salt))
                value_cy = int(round(base * rng.uniform(0.85, 1.15)))
                if metric_name == _ZERO_PY_METRIC and report_date == report_dates[0]:
                    value_py = 0
                else:
                    # ratio in [0.85, 1.25] keeps pct_change within roughly -15%..+25%.
                    ratio = rng.uniform(0.85, 1.25)
                    value_py = int(round(value_cy / ratio))
                rows.append(
                    {
                        "report_date": report_date,
                        "channel": channel,
                        "metric_name": metric_name,
                        "metric_group": metric_group,
                        "sort_order": sort_order,
                        "value_cy": value_cy,
                        "value_py": value_py,
                        "pct_change": pct_change(value_cy, value_py),
                    }
                )
    return rows


def _build_all_rows(channel_rows: list[dict]) -> list[dict]:
    """Materialize the ``ALL`` slice as the element-wise sum of Web+Mobile+Partner.

    For each (report_date, metric), sum ``value_cy`` and ``value_py`` across the three
    real channels, then RECOMPUTE ``pct_change`` from those sums (LOCKED DECISION L3 —
    never average the per-channel percentages).

    Args:
        channel_rows: The Web/Mobile/Partner rows produced by :func:`_build_channel_rows`.

    Returns:
        A list of derived ``ALL`` row dicts, one per (report_date, metric).
    """
    sums: dict[tuple[datetime.datetime, str], dict] = {}
    for row in channel_rows:
        key = (row["report_date"], row["metric_name"])
        agg = sums.get(key)
        if agg is None:
            sums[key] = {
                "report_date": row["report_date"],
                "channel": CHANNEL_ALL,
                "metric_name": row["metric_name"],
                "metric_group": row["metric_group"],
                "sort_order": row["sort_order"],
                "value_cy": row["value_cy"],
                "value_py": row["value_py"],
            }
        else:
            agg["value_cy"] += row["value_cy"]
            agg["value_py"] += row["value_py"]

    all_rows: list[dict] = []
    for agg in sums.values():
        agg["pct_change"] = pct_change(agg["value_cy"], agg["value_py"])
        all_rows.append(agg)
    return all_rows


def build_metric_rows(
    report_dates: list[datetime.datetime] = REPORT_DATES,
    seed_salt: str = "daily-metrics-v1",
) -> list[dict]:
    """Build every gold-table row (Web/Mobile/Partner drawn + ALL derived).

    Args:
        report_dates: Report snapshot dates (defaults to the six fixed early-2026 days).
        seed_salt: Global determinism salt; changing it reshuffles all draws.

    Returns:
        A list of row dicts with keys exactly: ``report_date`` (datetime), ``channel``
        (str), ``metric_name`` (str), ``metric_group`` (str), ``sort_order`` (int),
        ``value_cy`` (int), ``value_py`` (int), ``pct_change`` (float | None). For the
        default inputs this is 17 x 4 x 6 = 408 rows.
    """
    channel_rows = _build_channel_rows(report_dates, seed_salt)
    all_rows = _build_all_rows(channel_rows)
    return channel_rows + all_rows
