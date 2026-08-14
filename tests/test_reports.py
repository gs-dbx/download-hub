"""Unit tests for the pure config model + query builders in ``app.reports``.

No fastapi, no databricks.sdk, no pyspark, no network — runs offline
(LOCKED DECISIONS L1/L2/L5).
"""

import json

import pytest

from app.reports import (
    ColumnSpec,
    FilterSpec,
    ReportConfig,
    build_distinct_values_query,
    build_report_config_query,
    build_report_dates_query,
    build_report_query,
    parse_report_config,
    validate_fqn,
    validate_identifier,
)

# The exact CONTEXT seed-row shape (4 columns + channel filter).
_SEED_COLUMNS = [
    {"name": "metric_name", "label": "Metric", "format": "text"},
    {"name": "value_cy", "label": "2026", "format": "int"},
    {"name": "value_py", "label": "2025", "format": "int"},
    {"name": "pct_change", "label": "% Change", "format": "pct"},
]
_SEED_FILTERS = [{"field": "channel", "label": "CHANNEL"}]

_SEED_ROW = {
    "report_id": "daily_metrics",
    "title": "Daily Metrics Overview",
    "source_fqn": "main.default.daily_metrics",
    "date_field": "report_date",
    "columns_json": json.dumps(_SEED_COLUMNS),
    "filters_json": json.dumps(_SEED_FILTERS),
    "order_by": "sort_order",
    "display_order": 1,
    "enabled": True,
    "download_group": None,
}


# --- parse_report_config -------------------------------------------------


def test_parse_report_config_parses_seed_row():
    """The CONTEXT seed row parses to 4 ColumnSpec + 1 FilterSpec (L5)."""
    rc = parse_report_config(_SEED_ROW)
    assert isinstance(rc, ReportConfig)
    assert rc.report_id == "daily_metrics"
    assert rc.title == "Daily Metrics Overview"
    assert rc.source_fqn == "main.default.daily_metrics"
    assert rc.date_field == "report_date"
    assert rc.order_by == "sort_order"
    assert rc.display_order == 1
    assert rc.enabled is True
    assert rc.download_group is None
    assert len(rc.columns) == 4
    assert all(isinstance(c, ColumnSpec) for c in rc.columns)
    assert [c.name for c in rc.columns] == [
        "metric_name",
        "value_cy",
        "value_py",
        "pct_change",
    ]
    assert [c.label for c in rc.columns] == ["Metric", "2026", "2025", "% Change"]
    assert [c.format for c in rc.columns] == ["text", "int", "int", "pct"]
    assert len(rc.filters) == 1
    assert isinstance(rc.filters[0], FilterSpec)
    assert rc.filters[0].field == "channel"
    assert rc.filters[0].label == "CHANNEL"


def test_parse_report_config_tolerates_unknown_format():
    """An unknown format value is kept as-is (tolerant; default text) (L5)."""
    row = dict(_SEED_ROW)
    row["columns_json"] = json.dumps(
        [{"name": "value_cy", "label": "2026", "format": "currency"}]
    )
    rc = parse_report_config(row)
    assert rc.columns[0].format == "currency"


def test_parse_report_config_defaults_missing_format_to_text():
    """A column with no format defaults to 'text'."""
    row = dict(_SEED_ROW)
    row["columns_json"] = json.dumps([{"name": "value_cy", "label": "2026"}])
    rc = parse_report_config(row)
    assert rc.columns[0].format == "text"


def test_parse_report_config_malformed_columns_json_raises():
    """Malformed columns_json raises ValueError (L5)."""
    row = dict(_SEED_ROW)
    row["columns_json"] = "{not valid json"
    with pytest.raises(ValueError):
        parse_report_config(row)


def test_parse_report_config_missing_filters_json_defaults_empty():
    """A missing filters_json key yields an empty filters list."""
    row = dict(_SEED_ROW)
    del row["filters_json"]
    rc = parse_report_config(row)
    assert rc.filters == []


def test_parse_report_config_empty_filters_json_defaults_empty():
    """An empty/None filters_json yields an empty filters list."""
    row = dict(_SEED_ROW)
    row["filters_json"] = ""
    rc = parse_report_config(row)
    assert rc.filters == []


# --- validate_identifier / validate_fqn ---------------------------------


@pytest.mark.parametrize("good", ["value_cy", "report_date", "sort_order", "_x", "A1"])
def test_validate_identifier_accepts_bare(good):
    """A bare SQL identifier is returned unchanged."""
    assert validate_identifier(good) == good


@pytest.mark.parametrize(
    "bad", ["a; DROP--", "1col", "", "drop table", "col-name", "a.b"]
)
def test_validate_identifier_rejects_bad(bad):
    """Anything that is not a bare identifier raises ValueError (L2)."""
    with pytest.raises(ValueError):
        validate_identifier(bad)


@pytest.mark.parametrize(
    "good", ["main.default.daily_metrics", "a.b.tbl", "c.d.e"]
)
def test_validate_fqn_accepts_valid(good):
    """A 1-3 part FQN of bare identifiers is returned re-joined."""
    assert validate_fqn(good) == good


@pytest.mark.parametrize("bad", ["a.b.c.d", "a..b", "a.b.c; DROP", ""])
def test_validate_fqn_rejects_bad(bad):
    """Too many parts, empty parts, or an injected part raises ValueError (L2)."""
    with pytest.raises(ValueError):
        validate_fqn(bad)


# --- build_report_query --------------------------------------------------


def test_build_report_query_shape_and_params():
    """3-level FQN, bound report_date TIMESTAMP, filter AND, ORDER BY (L2)."""
    sql, params = build_report_query(
        "main.default.daily_metrics",
        ["metric_name", "value_cy"],
        "report_date",
        "2026-01-12 00:00:00",
        {"channel": "ALL"},
        "sort_order",
    )
    assert "SELECT metric_name, value_cy FROM main.default.daily_metrics" in sql
    assert "WHERE report_date = :report_date" in sql
    assert "AND channel = :flt_channel" in sql
    assert sql.strip().endswith("ORDER BY sort_order")
    by_name = {p["name"]: p for p in params}
    assert by_name["report_date"]["type"] == "TIMESTAMP"
    assert by_name["report_date"]["value"] == "2026-01-12 00:00:00"
    assert by_name["flt_channel"]["type"] == "STRING"
    assert by_name["flt_channel"]["value"] == "ALL"
    assert len(params) == 2
    assert all(isinstance(p["value"], str) for p in params)


def test_build_report_query_no_filters_no_order():
    """Without filters/order_by only report_date is bound and no ORDER BY."""
    sql, params = build_report_query(
        "main.default.daily_metrics", ["value_cy"], "report_date", "2026-01-12 00:00:00"
    )
    assert "ORDER BY" not in sql
    assert "AND" not in sql
    assert len(params) == 1
    assert params[0]["name"] == "report_date"


def test_build_report_query_multiple_filters():
    """Each filter adds its own bound STRING param."""
    sql, params = build_report_query(
        "main.default.daily_metrics",
        ["value_cy"],
        "report_date",
        "2026-01-12 00:00:00",
        {"channel": "E", "region": "NE"},
    )
    assert "AND channel = :flt_channel" in sql
    assert "AND region = :flt_region" in sql
    assert len(params) == 3


def test_build_report_query_empty_columns_raises():
    """An empty columns list raises ValueError."""
    with pytest.raises(ValueError):
        build_report_query(
            "main.default.daily_metrics", [], "report_date", "2026-01-12 00:00:00"
        )


def test_build_report_query_bad_column_raises():
    """A bad column is never interpolated — it raises (L2)."""
    with pytest.raises(ValueError):
        build_report_query(
            "main.default.daily_metrics",
            ["value_cy; DROP"],
            "report_date",
            "2026-01-12 00:00:00",
        )


def test_build_report_query_bad_filter_field_raises():
    """A bad filter field is never interpolated — it raises (L2)."""
    with pytest.raises(ValueError):
        build_report_query(
            "main.default.daily_metrics",
            ["value_cy"],
            "report_date",
            "2026-01-12 00:00:00",
            {"channel; DROP": "ALL"},
        )


def test_build_report_query_bad_order_by_raises():
    """A bad order_by is never interpolated — it raises (L2)."""
    with pytest.raises(ValueError):
        build_report_query(
            "main.default.daily_metrics",
            ["value_cy"],
            "report_date",
            "2026-01-12 00:00:00",
            None,
            "sort_order; DROP",
        )


def test_build_report_query_bad_source_raises():
    """A bad source_fqn is never interpolated — it raises (L2)."""
    with pytest.raises(ValueError):
        build_report_query(
            "a.b.tbl; DROP",
            ["value_cy"],
            "report_date",
            "2026-01-12 00:00:00",
        )


def test_build_report_query_bad_date_field_raises():
    """A bad date_field is never interpolated — it raises (L2)."""
    with pytest.raises(ValueError):
        build_report_query(
            "main.default.daily_metrics",
            ["value_cy"],
            "report_date; DROP",
            "2026-01-12 00:00:00",
        )


# --- build_report_dates_query --------------------------------------------


def test_build_report_dates_query_shape():
    """DISTINCT date_field from the FQN, newest first."""
    sql = build_report_dates_query("main.default.daily_metrics", "report_date")
    assert "SELECT DISTINCT report_date FROM main.default.daily_metrics" in sql
    assert sql.strip().endswith("ORDER BY report_date DESC")


def test_build_report_dates_query_bad_identifier_raises():
    """A bad date_field raises ValueError."""
    with pytest.raises(ValueError):
        build_report_dates_query("main.default.daily_metrics", "report_date; DROP")


# --- build_distinct_values_query -----------------------------------------


def test_build_distinct_values_query_unscoped():
    """Without date scoping there are no params and no WHERE clause."""
    sql, params = build_distinct_values_query("main.default.daily_metrics", "channel")
    assert "SELECT DISTINCT channel FROM main.default.daily_metrics" in sql
    assert "WHERE" not in sql
    assert sql.strip().endswith("ORDER BY channel")
    assert params == []


def test_build_distinct_values_query_scoped():
    """With date scoping a bound report_date TIMESTAMP param is added."""
    sql, params = build_distinct_values_query(
        "main.default.daily_metrics", "channel", "report_date", "2026-01-12 00:00:00"
    )
    assert "WHERE report_date = :report_date" in sql
    assert sql.strip().endswith("ORDER BY channel")
    assert len(params) == 1
    assert params[0]["name"] == "report_date"
    assert params[0]["type"] == "TIMESTAMP"


def test_build_distinct_values_query_bad_field_raises():
    """A bad filter field raises ValueError."""
    with pytest.raises(ValueError):
        build_distinct_values_query("main.default.daily_metrics", "channel; DROP")


# --- build_report_config_query -------------------------------------------


def test_build_report_config_query_shape():
    """Registry SELECT names report_config, enabled = true, ordered."""
    sql = build_report_config_query("main", "default")
    assert "FROM main.default.report_config" in sql
    assert "WHERE enabled = true" in sql
    assert sql.strip().endswith("ORDER BY display_order")


def test_build_report_config_query_requires_catalog_schema():
    """Empty catalog/schema raises ValueError."""
    with pytest.raises(ValueError):
        build_report_config_query("", "default")
    with pytest.raises(ValueError):
        build_report_config_query("main", "")
