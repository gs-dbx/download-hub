"""Unit tests for the pure config model + query builders in ``app.reports``.

No fastapi, no databricks.sdk, no pyspark, no network — runs offline.
"""

import json

import pytest

from app.reports import (
    AUDIT_LOG_COLUMNS,
    ColumnSpec,
    FilterSpec,
    ReportConfig,
    ReportView,
    build_app_config_query,
    build_app_config_upsert,
    build_audit_log_query,
    build_distinct_values_query,
    build_preview_query,
    build_report_config_query,
    build_report_config_upsert,
    build_report_dates_query,
    build_report_query,
    build_report_view_query,
    build_report_view_upsert,
    parse_report_config,
    parse_report_view,
    resolve_columns,
    validate_fqn,
    validate_identifier,
    validate_query,
)

# A representative source query (a full SELECT the app wraps as a subquery).
_SRC = "SELECT * FROM main.default.daily_metrics"

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
    "source_query": _SRC,
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
    """The CONTEXT seed row parses to 4 ColumnSpec + 1 FilterSpec."""
    rc = parse_report_config(_SEED_ROW)
    assert isinstance(rc, ReportConfig)
    assert rc.report_id == "daily_metrics"
    assert rc.title == "Daily Metrics Overview"
    assert rc.source_query == _SRC
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
    """An unknown format value is kept as-is (tolerant; default text)."""
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


def test_parse_report_config_empty_columns_json_defaults_empty():
    """An empty/absent columns_json yields an empty columns list (= show all)."""
    row = dict(_SEED_ROW)
    row["columns_json"] = ""
    assert parse_report_config(row).columns == []
    row2 = dict(_SEED_ROW)
    del row2["columns_json"]
    assert parse_report_config(row2).columns == []


def test_parse_report_config_absent_date_field_is_none():
    """An empty/absent date_field parses to None (no date scope)."""
    row = dict(_SEED_ROW)
    row["date_field"] = ""
    assert parse_report_config(row).date_field is None
    row2 = dict(_SEED_ROW)
    del row2["date_field"]
    assert parse_report_config(row2).date_field is None


def test_parse_report_config_malformed_columns_json_raises():
    """Malformed columns_json raises ValueError."""
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


# --- resolve_columns -----------------------------------------------------


def test_resolve_columns_configured_wins():
    """Configured columns win verbatim; result columns are ignored."""
    configured = [ColumnSpec("a", "A", "int")]
    assert resolve_columns(configured, ["a", "b", "c"]) == configured


def test_resolve_columns_defaults_to_all_result_columns():
    """No configured columns -> one text column per result column, labelled by name."""
    out = resolve_columns([], ["metric_name", "value_cy"])
    assert [c.name for c in out] == ["metric_name", "value_cy"]
    assert [c.label for c in out] == ["metric_name", "value_cy"]
    assert all(c.format == "text" for c in out)


def test_resolve_columns_empty_both_yields_empty():
    """No config and no result columns -> empty (nothing to show)."""
    assert resolve_columns([], []) == []


# --- validate_query ------------------------------------------------------


def test_validate_query_trims_and_strips_trailing_semicolon():
    """Surrounding whitespace and a single trailing ';' are stripped."""
    assert validate_query("  SELECT * FROM t ;  ") == "SELECT * FROM t"


@pytest.mark.parametrize("bad", ["", "   ", ";", "SELECT 1; DROP TABLE t"])
def test_validate_query_rejects_empty_or_multi_statement(bad):
    """Empty or multi-statement source queries raise ValueError."""
    with pytest.raises(ValueError):
        validate_query(bad)


# --- validate_identifier / validate_fqn ---------------------------------


@pytest.mark.parametrize("good", ["value_cy", "report_date", "sort_order", "_x", "A1"])
def test_validate_identifier_accepts_bare(good):
    """A bare SQL identifier is returned unchanged."""
    assert validate_identifier(good) == good


@pytest.mark.parametrize(
    "bad", ["a; DROP--", "1col", "", "drop table", "col-name", "a.b"]
)
def test_validate_identifier_rejects_bad(bad):
    """Anything that is not a bare identifier raises ValueError."""
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
    """Too many parts, empty parts, or an injected part raises ValueError."""
    with pytest.raises(ValueError):
        validate_fqn(bad)


# --- build_report_query --------------------------------------------------


def test_build_report_query_wraps_source_as_subquery():
    """The source query is wrapped as an aliased subquery."""
    sql, _params = build_report_query(_SRC, ["metric_name", "value_cy"])
    assert f"FROM ( {_SRC} ) AS _q" in sql
    assert sql.startswith("SELECT metric_name, value_cy FROM (")


def test_build_report_query_shape_and_params():
    """Wrapped subquery, bound report_date TIMESTAMP, filter AND, ORDER BY."""
    sql, params = build_report_query(
        _SRC,
        ["metric_name", "value_cy"],
        "report_date",
        "2026-01-12 00:00:00",
        {"channel": "ALL"},
        "sort_order",
    )
    assert f"SELECT metric_name, value_cy FROM ( {_SRC} ) AS _q" in sql
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


def test_build_report_query_none_columns_selects_star():
    """No columns -> SELECT * (show every column the query returns)."""
    sql, params = build_report_query(_SRC)
    assert sql.startswith("SELECT * FROM (")
    assert "WHERE" not in sql
    assert params == []


def test_build_report_query_no_date_no_filters_no_order():
    """Without date_field/filters/order_by there is no WHERE and no params."""
    sql, params = build_report_query(_SRC, ["value_cy"])
    assert "WHERE" not in sql
    assert "ORDER BY" not in sql
    assert params == []


def test_build_report_query_filters_without_date_scope():
    """Filters alone (no date_field) still build a WHERE with only filter params."""
    sql, params = build_report_query(
        _SRC, ["value_cy"], filters={"channel": "E"}
    )
    assert "WHERE channel = :flt_channel" in sql
    assert len(params) == 1
    assert params[0]["name"] == "flt_channel"


def test_build_report_query_multiple_filters():
    """Each filter adds its own bound STRING param."""
    sql, params = build_report_query(
        _SRC,
        ["value_cy"],
        "report_date",
        "2026-01-12 00:00:00",
        {"channel": "E", "region": "NE"},
    )
    assert "channel = :flt_channel" in sql
    assert "region = :flt_region" in sql
    assert len(params) == 3


def test_build_report_query_date_field_without_report_date_raises():
    """A date_field with no report_date VALUE raises (never an unbound clause)."""
    with pytest.raises(ValueError):
        build_report_query(_SRC, ["value_cy"], "report_date")


def test_build_report_query_bad_column_raises():
    """A bad column is never interpolated — it raises."""
    with pytest.raises(ValueError):
        build_report_query(_SRC, ["value_cy; DROP"])


def test_build_report_query_bad_filter_field_raises():
    """A bad filter field is never interpolated — it raises."""
    with pytest.raises(ValueError):
        build_report_query(
            _SRC, ["value_cy"], filters={"channel; DROP": "ALL"}
        )


def test_build_report_query_bad_order_by_raises():
    """A bad order_by is never interpolated — it raises."""
    with pytest.raises(ValueError):
        build_report_query(_SRC, ["value_cy"], order_by="sort_order; DROP")


def test_build_report_query_multi_statement_source_raises():
    """A source query with an embedded ';' raises (single statement only)."""
    with pytest.raises(ValueError):
        build_report_query("SELECT * FROM t; DROP TABLE t", ["value_cy"])


def test_build_report_query_bad_date_field_raises():
    """A bad date_field is never interpolated — it raises."""
    with pytest.raises(ValueError):
        build_report_query(_SRC, ["value_cy"], "report_date; DROP", "2026-01-12")


# --- build_report_dates_query --------------------------------------------


def test_build_report_dates_query_shape():
    """DISTINCT date_field from the wrapped source, newest first."""
    sql = build_report_dates_query(_SRC, "report_date")
    assert f"SELECT DISTINCT report_date FROM ( {_SRC} ) AS _q" in sql
    assert sql.strip().endswith("ORDER BY report_date DESC")


def test_build_report_dates_query_bad_identifier_raises():
    """A bad date_field raises ValueError."""
    with pytest.raises(ValueError):
        build_report_dates_query(_SRC, "report_date; DROP")


# --- build_distinct_values_query -----------------------------------------


def test_build_distinct_values_query_unscoped():
    """Without date scoping there are no params and no WHERE clause."""
    sql, params = build_distinct_values_query(_SRC, "channel")
    assert f"SELECT DISTINCT channel FROM ( {_SRC} ) AS _q" in sql
    assert "WHERE" not in sql
    assert sql.strip().endswith("ORDER BY channel")
    assert params == []


def test_build_distinct_values_query_scoped():
    """With date scoping a bound report_date TIMESTAMP param is added."""
    sql, params = build_distinct_values_query(
        _SRC, "channel", "report_date", "2026-01-12 00:00:00"
    )
    assert "WHERE report_date = :report_date" in sql
    assert sql.strip().endswith("ORDER BY channel")
    assert len(params) == 1
    assert params[0]["name"] == "report_date"
    assert params[0]["type"] == "TIMESTAMP"


def test_build_distinct_values_query_bad_field_raises():
    """A bad filter field raises ValueError."""
    with pytest.raises(ValueError):
        build_distinct_values_query(_SRC, "channel; DROP")


# --- build_report_config_query -------------------------------------------


def test_build_report_config_query_shape():
    """Registry SELECT names report_config, source_query, enabled = true, ordered."""
    sql = build_report_config_query("main", "default")
    assert "FROM main.default.report_config" in sql
    assert "source_query" in sql
    assert "source_fqn" not in sql
    assert "WHERE enabled = true" in sql
    assert sql.strip().endswith("ORDER BY display_order")


def test_build_report_config_query_requires_catalog_schema():
    """Empty catalog/schema raises ValueError."""
    with pytest.raises(ValueError):
        build_report_config_query("", "default")
    with pytest.raises(ValueError):
        build_report_config_query("main", "")


def test_build_report_config_query_selects_view_key():
    """The registry query includes view_key + updated_by."""
    sql = build_report_config_query("main", "default")
    assert "view_key" in sql
    assert "updated_by" in sql


# --- views: parse + query -----------------------------------------------


def test_parse_report_config_reads_view_key():
    """view_key parses (stripped -> None when blank)."""
    row = dict(_SEED_ROW)
    row["view_key"] = " efile_ops "
    assert parse_report_config(row).view_key == "efile_ops"
    row2 = dict(_SEED_ROW)
    row2["view_key"] = ""
    assert parse_report_config(row2).view_key is None
    assert parse_report_config(dict(_SEED_ROW)).view_key is None  # absent -> None


def test_parse_report_view():
    """A report_view row parses to a ReportView."""
    rv = parse_report_view(
        {"view_key": "efile_ops", "title": "Operations", "display_order": 2, "enabled": True}
    )
    assert isinstance(rv, ReportView)
    assert rv.view_key == "efile_ops"
    assert rv.title == "Operations"
    assert rv.display_order == 2
    assert rv.enabled is True


def test_build_report_view_query_shape():
    """The view registry query selects view_key/title, enabled, ordered."""
    sql = build_report_view_query("main", "default")
    assert "FROM main.default.report_view" in sql
    assert "view_key" in sql and "title" in sql
    assert "WHERE enabled = true" in sql
    assert sql.strip().endswith("ORDER BY display_order")


def test_build_report_view_query_requires_catalog_schema():
    """Empty catalog/schema raises ValueError."""
    with pytest.raises(ValueError):
        build_report_view_query("", "default")


# --- admin upserts -------------------------------------------------------

_ADMIN_ROW = {
    "report_id": "r1",
    "title": "R1",
    "source_query": "SELECT * FROM main.default.t",
    "date_field": "report_date",
    "columns_json": '[{"name":"a","label":"A","format":"int"}]',
    "filters_json": '[{"field":"b","label":"B"}]',
    "order_by": "a",
    "display_order": "3",
    "enabled": True,
    "download_group": "",
    "view_key": "efile_ops",
    "updated_by": "admin@x.y",
}


def test_build_report_config_upsert_shape_and_params():
    """The MERGE targets report_config on report_id and binds every value."""
    sql, params = build_report_config_upsert("main", "default", dict(_ADMIN_ROW))
    assert "MERGE INTO main.default.report_config" in sql
    assert "ON t.report_id = s.report_id" in sql
    assert "WHEN MATCHED THEN UPDATE SET" in sql
    assert "WHEN NOT MATCHED THEN INSERT" in sql
    by_name = {p["name"]: p["value"] for p in params}
    assert by_name["report_id"] == "r1"
    assert by_name["view_key"] == "efile_ops"
    assert by_name["display_order"] == "3"
    assert by_name["enabled"] == "true"


def test_build_report_config_upsert_rejects_bad_identifiers():
    """A bad report_id / view_key / column / query raises ValueError."""
    bad_id = dict(_ADMIN_ROW, report_id="1bad")
    with pytest.raises(ValueError):
        build_report_config_upsert("main", "default", bad_id)
    bad_view = dict(_ADMIN_ROW, view_key="a b")
    with pytest.raises(ValueError):
        build_report_config_upsert("main", "default", bad_view)
    bad_col = dict(_ADMIN_ROW, columns_json='[{"name":"a; DROP","label":"x"}]')
    with pytest.raises(ValueError):
        build_report_config_upsert("main", "default", bad_col)
    bad_query = dict(_ADMIN_ROW, source_query="SELECT 1; DROP TABLE t")
    with pytest.raises(ValueError):
        build_report_config_upsert("main", "default", bad_query)


def test_build_report_view_upsert_shape():
    """The view MERGE targets report_view on view_key and binds values."""
    sql, params = build_report_view_upsert(
        "main", "default",
        {"view_key": "efile_ops", "title": "Operations", "display_order": "1", "enabled": True, "updated_by": "a@b"},
    )
    assert "MERGE INTO main.default.report_view" in sql
    assert "ON t.view_key = s.view_key" in sql
    by_name = {p["name"]: p["value"] for p in params}
    assert by_name["view_key"] == "efile_ops"
    assert by_name["title"] == "Operations"


def test_build_report_view_upsert_rejects_bad_view_key():
    """A non-identifier view_key raises ValueError."""
    with pytest.raises(ValueError):
        build_report_view_upsert("main", "default", {"view_key": "a-b", "title": "x", "display_order": "1", "enabled": True})


# --- preview -------------------------------------------------------------


def test_build_preview_query_wraps_and_limits():
    """The preview wraps the source as a subquery and applies a clamped LIMIT."""
    assert build_preview_query(_SRC, 10) == f"SELECT * FROM ( {_SRC} ) AS _q LIMIT 10"
    # clamp
    assert build_preview_query(_SRC, 99999).endswith("LIMIT 1000")
    assert build_preview_query(_SRC, 0).endswith("LIMIT 1")


def test_build_preview_query_rejects_multi_statement():
    """A multi-statement source raises ValueError."""
    with pytest.raises(ValueError):
        build_preview_query("SELECT 1; DROP TABLE t")


# --- system config + audit log ------------------------------------------


def test_build_app_config_query_shape():
    """The config query selects key/value from app_config."""
    sql = build_app_config_query("main", "default")
    assert "config_key, config_value FROM main.default.app_config" in sql


def test_build_app_config_upsert_shape_and_params():
    """The config MERGE targets app_config on config_key and binds value."""
    sql, params = build_app_config_upsert(
        "main", "default", "download_disclaimer", "Handle with care.", "a@b"
    )
    assert "MERGE INTO main.default.app_config" in sql
    assert "ON t.config_key = s.config_key" in sql
    by_name = {p["name"]: p["value"] for p in params}
    assert by_name["config_key"] == "download_disclaimer"
    assert by_name["config_value"] == "Handle with care."


def test_build_app_config_upsert_rejects_bad_key():
    """A non-identifier config key raises ValueError."""
    with pytest.raises(ValueError):
        build_app_config_upsert("main", "default", "bad key", "x", "a@b")


def test_build_audit_log_query_shape_and_clamp():
    """The audit query selects the fixed columns, newest first, clamped limit."""
    sql = build_audit_log_query("main", "default", 10)
    assert "FROM main.default.download_audit" in sql
    assert "ORDER BY event_ts DESC" in sql
    assert sql.strip().endswith("LIMIT 10")
    for c in AUDIT_LOG_COLUMNS:
        assert c in sql
    # clamp
    assert build_audit_log_query("main", "default", 99999).strip().endswith("LIMIT 5000")
    assert build_audit_log_query("main", "default", 0).strip().endswith("LIMIT 1")


def test_build_audit_log_query_requires_catalog_schema():
    """Empty catalog/schema raises ValueError."""
    with pytest.raises(ValueError):
        build_audit_log_query("", "default")
