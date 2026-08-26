"""Unit tests for the pure config model + query builders in ``app.reports``.

No fastapi, no databricks.sdk, no pyspark, no network — runs offline.
"""

import json

import pytest

from app.reports import (
    AUDIT_LOG_COLUMNS,
    CONFIG_AUDIT_COLUMNS,
    VALID_AGGS,
    VALID_FORMATS,
    VALID_KINDS,
    ColumnSpec,
    FilterSpec,
    ReportConfig,
    ReportView,
    build_app_config_query,
    build_app_config_upsert,
    build_audit_log_query,
    build_config_audit_analytics_query,
    build_config_audit_insert,
    build_config_audit_query,
    build_config_audit_row,
    build_columns_probe_query,
    build_distinct_values_query,
    build_preview_query,
    build_report_count_query,
    build_report_page_query,
    build_report_config_delete,
    build_report_config_query,
    build_report_config_upsert,
    build_report_dates_query,
    build_report_query,
    decide_action,
    normalize_agg,
    normalize_format,
    split_columns,
    build_report_view_delete,
    build_report_view_query,
    build_report_view_upsert,
    normalize_kind,
    parse_report_config,
    parse_report_view,
    resolve_columns,
    validate_fqn,
    validate_identifier,
    validate_query,
    validate_volume_root,
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


# --- kind / volume reports ----------------------------------------------


_VOLUME_ROW = {
    "report_id": "case_docs",
    "title": "Case Documents",
    "kind": "volume",
    "volume_root": "/Volumes/main/default/docs",
    "display_order": 2,
    "enabled": True,
    "view_key": "ops",
}


def test_parse_report_config_defaults_kind_query_backcompat():
    """A row with no `kind`/`volume_root` parses as a query report unchanged."""
    rc = parse_report_config(_SEED_ROW)
    assert rc.kind == "query"
    assert rc.volume_root == ""
    assert rc.source_query == _SRC


def test_parse_report_config_query_kind_requires_source_query():
    """A query report with an empty source_query raises ValueError."""
    row = dict(_SEED_ROW)
    row["source_query"] = ""
    with pytest.raises(ValueError):
        parse_report_config(row)
    row2 = dict(_SEED_ROW)
    del row2["source_query"]
    with pytest.raises(ValueError):
        parse_report_config(row2)


def test_parse_report_config_volume_kind_parses():
    """A volume report parses with kind/volume_root and no query needed."""
    rc = parse_report_config(_VOLUME_ROW)
    assert isinstance(rc, ReportConfig)
    assert rc.kind == "volume"
    assert rc.volume_root == "/Volumes/main/default/docs"
    assert rc.source_query == ""  # optional for a volume report
    assert rc.date_field is None
    assert rc.columns == []
    assert rc.filters == []
    # shared gating still applies
    assert rc.view_key == "ops"


def test_parse_report_config_volume_kind_normalizes_case_and_trailing_slash():
    """`kind` is lowercased/stripped and volume_root loses a trailing slash."""
    row = dict(_VOLUME_ROW, kind="  VOLUME  ", volume_root="/Volumes/main/default/docs/")
    rc = parse_report_config(row)
    assert rc.kind == "volume"
    assert rc.volume_root == "/Volumes/main/default/docs"


@pytest.mark.parametrize(
    "bad_root",
    [None, "", "   ", "/mnt/data/docs", "/Volumes/main/default", "/Volumes/main"],
)
def test_parse_report_config_volume_kind_bad_root_raises(bad_root):
    """A volume report with a missing/blank/malformed volume_root raises."""
    row = dict(_VOLUME_ROW)
    if bad_root is None:
        del row["volume_root"]
    else:
        row["volume_root"] = bad_root
    with pytest.raises(ValueError):
        parse_report_config(row)


def test_parse_report_config_unknown_kind_raises():
    """An explicit unknown kind value raises ValueError."""
    row = dict(_SEED_ROW, kind="folder")
    with pytest.raises(ValueError):
        parse_report_config(row)


def test_normalize_kind_tolerant_and_strict():
    """normalize_kind defaults blank -> query, lowercases, rejects unknown."""
    assert normalize_kind(None) == "query"
    assert normalize_kind("") == "query"
    assert normalize_kind("  ") == "query"
    assert normalize_kind("Query") == "query"
    assert normalize_kind(" VOLUME ") == "volume"
    assert VALID_KINDS == frozenset({"query", "volume"})
    with pytest.raises(ValueError):
        normalize_kind("bogus")


def test_validate_volume_root_accepts_valid_and_normalizes():
    """A valid /Volumes/cat/schema/vol[/sub] path is returned normalized."""
    assert validate_volume_root("/Volumes/main/default/docs") == "/Volumes/main/default/docs"
    assert validate_volume_root("/Volumes/main/default/docs/sub/") == "/Volumes/main/default/docs/sub"


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "   ",
        "s3://bucket/docs",
        "/Volumes/main/default",          # missing volume segment
        "/Volumes/main",                  # missing schema+volume
        "/Volumes/main/default/../secret/x",  # path escape
        "/Volumes/1bad/default/docs",     # non-identifier catalog
    ],
)
def test_validate_volume_root_rejects_bad(bad):
    """Non-Volumes paths, too-shallow paths, '..', and bad identifiers raise."""
    with pytest.raises(ValueError):
        validate_volume_root(bad)


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


def test_build_report_config_query_selects_kind_and_volume_root():
    """The registry query surfaces the volume-report columns for main.py."""
    sql = build_report_config_query("main", "default")
    assert "kind" in sql
    assert "volume_root" in sql


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


def test_build_report_config_upsert_volume_kind_no_query():
    """A volume-kind upsert needs no source_query; it binds kind + volume_root."""
    row = {
        "report_id": "case_docs",
        "title": "Case Documents",
        "kind": "volume",
        "volume_root": "/Volumes/main/default/docs",
        "display_order": "2",
        "enabled": True,
        "view_key": "ops",
        "updated_by": "admin@x.y",
    }
    sql, params = build_report_config_upsert("main", "default", row)
    assert "MERGE INTO main.default.report_config" in sql
    assert "kind=:kind" in sql
    assert "volume_root=NULLIF(:volume_root,'')" in sql
    by_name = {p["name"]: p["value"] for p in params}
    assert by_name["kind"] == "volume"
    assert by_name["volume_root"] == "/Volumes/main/default/docs"
    assert by_name["source_query"] == ""  # tolerated / stored empty for volume


def test_build_report_config_upsert_volume_bad_root_raises():
    """A volume-kind upsert with a malformed volume_root raises ValueError."""
    row = {
        "report_id": "case_docs",
        "title": "Case Documents",
        "kind": "volume",
        "volume_root": "/not/a/volume",
        "display_order": "1",
        "enabled": True,
        "view_key": "ops",
    }
    with pytest.raises(ValueError):
        build_report_config_upsert("main", "default", row)


def test_build_report_config_upsert_query_kind_still_binds_kind():
    """A default (query) upsert binds kind='query' and keeps validating the query."""
    sql, params = build_report_config_upsert("main", "default", dict(_ADMIN_ROW))
    by_name = {p["name"]: p["value"] for p in params}
    assert by_name["kind"] == "query"
    assert by_name["volume_root"] == ""


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


# --- config audit (admin mutation log) ----------------------------------------


def test_build_config_audit_row_basic():
    """build_config_audit_row returns dict with all expected keys."""
    row = build_config_audit_row(
        actor_email="admin@example.com",
        entity_type="report_config",
        entity_key="my_report",
        action="upsert",
        summary="My Report",
        payload_json='{"title":"My Report"}',
        app_version="1.0.0",
    )
    assert row["actor_email"] == "admin@example.com"
    assert row["entity_type"] == "report_config"
    assert row["entity_key"] == "my_report"
    assert row["action"] == "upsert"
    assert row["summary"] == "My Report"
    assert row["payload_json"] == '{"title":"My Report"}'
    assert row["app_version"] == "1.0.0"
    # audit_id should be a uuid string
    assert isinstance(row["audit_id"], str)
    assert len(row["audit_id"]) == 36  # uuid4 format


def test_build_config_audit_row_explicit_audit_id():
    """build_config_audit_row accepts an explicit audit_id."""
    custom_id = "custom-id-123"
    row = build_config_audit_row(
        actor_email="a@b",
        entity_type="report_view",
        entity_key="vk",
        action="upsert",
        summary="View",
        payload_json="{}",
        app_version="1.0",
        audit_id=custom_id,
    )
    assert row["audit_id"] == custom_id


def test_build_config_audit_insert_shape_and_params():
    """build_config_audit_insert returns SQL + params with all fields bound."""
    row = build_config_audit_row(
        actor_email="admin@ex.com",
        entity_type="report_config",
        entity_key="r1",
        action="upsert",
        summary="Report 1",
        payload_json='{}',
        app_version="1.0",
    )
    sql, params = build_config_audit_insert("main", "default", row)
    assert "INSERT INTO main.default.config_audit" in sql
    assert "current_timestamp()" in sql
    assert len(params) == 8
    by_name = {p["name"]: p for p in params}
    assert by_name["actor_email"]["value"] == "admin@ex.com"
    assert by_name["entity_type"]["value"] == "report_config"
    assert by_name["entity_key"]["value"] == "r1"
    assert by_name["action"]["value"] == "upsert"
    assert by_name["summary"]["value"] == "Report 1"


def test_build_config_audit_insert_requires_catalog_schema():
    """Empty catalog/schema raises ValueError."""
    row = build_config_audit_row(
        "a@b", "report_config", "r1", "upsert", "R1", "{}", "1.0"
    )
    with pytest.raises(ValueError):
        build_config_audit_insert("", "default", row)
    with pytest.raises(ValueError):
        build_config_audit_insert("main", "", row)


def test_build_config_audit_query_shape_and_clamp():
    """build_config_audit_query selects CONFIG_AUDIT_COLUMNS, newest first, clamped."""
    sql = build_config_audit_query("main", "default", 100)
    assert "FROM main.default.config_audit" in sql
    assert "ORDER BY event_ts DESC" in sql
    assert sql.strip().endswith("LIMIT 100")
    for c in CONFIG_AUDIT_COLUMNS:
        assert c in sql
    # clamp
    assert build_config_audit_query("main", "default", 99999).strip().endswith(
        "LIMIT 5000"
    )
    assert build_config_audit_query("main", "default", 0).strip().endswith("LIMIT 1")


def test_build_config_audit_query_requires_catalog_schema():
    """Empty catalog/schema raises ValueError."""
    with pytest.raises(ValueError):
        build_config_audit_query("", "default")
    with pytest.raises(ValueError):
        build_config_audit_query("main", "")


def test_build_config_audit_analytics_query_shape_and_clamp():
    """build_config_audit_analytics_query groups by entity_type/action."""
    sql = build_config_audit_analytics_query("main", "default", 30)
    assert "FROM main.default.config_audit" in sql
    assert "GROUP BY entity_type, action" in sql
    assert "ORDER BY n DESC" in sql
    assert "INTERVAL 30 DAYS" in sql
    # clamp
    assert "INTERVAL 365 DAYS" in build_config_audit_analytics_query(
        "main", "default", 99999
    )
    assert "INTERVAL 1 DAYS" in build_config_audit_analytics_query(
        "main", "default", 0
    )


def test_build_config_audit_analytics_query_requires_catalog_schema():
    """Empty catalog/schema raises ValueError."""
    with pytest.raises(ValueError):
        build_config_audit_analytics_query("", "default")
    with pytest.raises(ValueError):
        build_config_audit_analytics_query("main", "")


# --- float/double display format -----------------------------------------


def test_valid_formats_includes_float():
    """float is a recognized display format; double is an accepted alias."""
    assert "float" in VALID_FORMATS
    assert normalize_format("float") == "float"
    assert normalize_format("double") == "float"
    assert normalize_format("DOUBLE") == "float"
    assert normalize_format("") == "text"
    assert normalize_format(None) == "text"
    assert normalize_format("int") == "int"


def test_parse_column_spec_normalizes_double_to_float():
    """A column with format 'double' parses to format 'float'."""
    row = {
        "report_id": "r",
        "title": "R",
        "source_query": "SELECT * FROM t",
        "display_order": 1,
        "enabled": True,
        "view_key": "v",
        "columns_json": json.dumps([{"name": "amt", "label": "Amt", "format": "double"}]),
    }
    cfg = parse_report_config(row)
    assert cfg.columns[0].format == "float"


# --- aggregation columns --------------------------------------------------


def test_normalize_agg_valid_blank_and_invalid():
    """normalize_agg lowercases known aggs, blanks to '', raises on unknown."""
    assert VALID_AGGS == {"sum", "min", "avg", "max", "first", "last"}
    assert normalize_agg("SUM") == "sum"
    assert normalize_agg("") == ""
    assert normalize_agg(None) == ""
    with pytest.raises(ValueError):
        normalize_agg("median")


def test_decide_action_create_vs_update():
    """decide_action returns 'create' or 'update' based on key presence."""
    # Key exists -> "update"
    assert decide_action({"report_1", "report_2"}, "report_1") == "update"
    assert decide_action({"report_1", "report_2"}, "report_2") == "update"

    # Key missing -> "create"
    assert decide_action({"report_1", "report_2"}, "report_3") == "create"

    # Empty set -> always "create"
    assert decide_action(set(), "any_key") == "create"


def test_parse_agg_column_and_derived_name():
    """An agg column parses agg+source; name derives from source+agg when absent."""
    cols = json.dumps([
        {"label": "Total", "agg": "sum", "source": "revenue"},  # no explicit name
        {"name": "avg_v", "label": "Avg", "agg": "avg", "source": "visits", "format": "float"},
    ])
    row = {
        "report_id": "r", "title": "R", "source_query": "SELECT * FROM t",
        "display_order": 1, "enabled": True, "view_key": "v", "columns_json": cols,
    }
    cfg = parse_report_config(row)
    assert cfg.columns[0].name == "revenue_sum"  # derived source_agg
    assert cfg.columns[0].agg == "sum"
    assert cfg.columns[0].source == "revenue"
    assert cfg.columns[1].name == "avg_v"
    assert cfg.columns[1].source == "visits"
    assert cfg.columns[1].format == "float"


def test_split_columns_separates_plain_and_agg():
    """split_columns returns plain names + aggregate specs (output = name)."""
    cols = [
        ColumnSpec(name="channel", label="Channel"),
        ColumnSpec(name="rev_total", label="Rev", agg="sum", source="revenue"),
    ]
    plain, aggs = split_columns(cols)
    assert plain == ["channel"]
    assert aggs == [{"func": "sum", "source": "revenue", "output": "rev_total"}]


def test_build_report_query_mixed_agg_groups_by_plain():
    """A mixed agg/non-agg select GROUP BYs every non-agg column."""
    sql, params = build_report_query(
        "SELECT * FROM main.default.t",
        columns=["channel"],
        aggregates=[{"func": "sum", "source": "revenue", "output": "rev_total"}],
    )
    assert "SELECT channel, SUM(revenue) AS rev_total FROM (" in sql
    assert sql.strip().endswith("GROUP BY channel")
    assert params == []


def test_build_report_query_all_agg_no_group_by():
    """All-aggregate select emits no GROUP BY (single-row result)."""
    sql, _ = build_report_query(
        "SELECT * FROM t",
        columns=[],
        aggregates=[
            {"func": "min", "source": "a", "output": "a_min"},
            {"func": "max", "source": "a", "output": "a_max"},
        ],
    )
    assert "MIN(a) AS a_min" in sql
    assert "MAX(a) AS a_max" in sql
    assert "GROUP BY" not in sql


def test_build_report_query_agg_with_date_scope_orders_where_before_group():
    """WHERE (date scope) precedes GROUP BY; ORDER BY follows it."""
    sql, params = build_report_query(
        "SELECT * FROM t",
        columns=["channel"],
        date_field="report_date",
        report_date="2026-01-12 00:00:00",
        aggregates=[{"func": "avg", "source": "v", "output": "v_avg"}],
        order_by="channel",
    )
    assert sql.index("WHERE") < sql.index("GROUP BY") < sql.index("ORDER BY")
    assert params[0]["name"] == "report_date"


def test_build_report_query_first_last_emit_functions():
    """first/last are emitted (non-deterministic without ORDER BY, by design)."""
    sql, _ = build_report_query(
        "SELECT * FROM t", columns=["g"],
        aggregates=[
            {"func": "first", "source": "x", "output": "x_first"},
            {"func": "last", "source": "x", "output": "x_last"},
        ],
    )
    assert "FIRST(x) AS x_first" in sql
    assert "LAST(x) AS x_last" in sql


def test_build_report_query_rejects_bad_agg_and_identifiers():
    """Unknown agg func or non-identifier source/output raises ValueError."""
    with pytest.raises(ValueError):
        build_report_query("SELECT * FROM t", columns=[],
                            aggregates=[{"func": "median", "source": "a", "output": "b"}])
    with pytest.raises(ValueError):
        build_report_query("SELECT * FROM t", columns=[],
                            aggregates=[{"func": "sum", "source": "a; DROP", "output": "b"}])
    with pytest.raises(ValueError):
        build_report_query("SELECT * FROM t", columns=[],
                            aggregates=[{"func": "sum", "source": "a", "output": "b c"}])
    with pytest.raises(ValueError):
        build_report_query("SELECT * FROM t", columns=[],
                            aggregates=[{"func": "", "source": "a", "output": "b"}])


def test_build_report_query_plain_unchanged_backcompat():
    """With no aggregates, behavior is unchanged (no GROUP BY, plain select)."""
    sql, _ = build_report_query("SELECT * FROM t", columns=["a", "b"])
    assert sql == "SELECT a, b FROM ( SELECT * FROM t ) AS _q"
    star, _ = build_report_query("SELECT * FROM t")
    assert star == "SELECT * FROM ( SELECT * FROM t ) AS _q"


def test_upsert_validates_agg_columns():
    """A save with a valid agg column passes; a bad agg / missing source raises."""
    good = json.dumps([{"name": "s", "label": "S", "agg": "sum", "source": "revenue"}])
    sql, _ = build_report_config_upsert("main", "default", {
        "report_id": "r", "title": "R", "source_query": "SELECT * FROM t",
        "view_key": "v", "columns_json": good, "display_order": 1, "enabled": True,
    })
    assert "MERGE INTO main.default.report_config" in sql
    bad_fn = json.dumps([{"name": "s", "label": "S", "agg": "median", "source": "r"}])
    with pytest.raises(ValueError):
        build_report_config_upsert("main", "default", {
            "report_id": "r", "title": "R", "source_query": "SELECT * FROM t",
            "view_key": "v", "columns_json": bad_fn, "display_order": 1, "enabled": True,
        })
    no_src = json.dumps([{"name": "s", "label": "S", "agg": "sum"}])
    with pytest.raises(ValueError):
        build_report_config_upsert("main", "default", {
            "report_id": "r", "title": "R", "source_query": "SELECT * FROM t",
            "view_key": "v", "columns_json": no_src, "display_order": 1, "enabled": True,
        })


# --- delete report --------------------------------------------------------


def test_build_report_config_delete():
    """DELETE is parameterized on report_id; identifier validated."""
    sql, params = build_report_config_delete("main", "default", "daily_metrics")
    assert sql == "DELETE FROM main.default.report_config WHERE report_id = :report_id"
    assert params == [{"name": "report_id", "value": "daily_metrics", "type": "STRING"}]


def test_build_report_config_delete_validates():
    """Empty catalog/schema or a non-identifier report_id raises ValueError."""
    with pytest.raises(ValueError):
        build_report_config_delete("", "default", "r")
    with pytest.raises(ValueError):
        build_report_config_delete("main", "", "r")
    with pytest.raises(ValueError):
        build_report_config_delete("main", "default", "bad; DROP")


def test_build_report_view_delete():
    """DELETE is parameterized on view_key; identifier validated."""
    sql, params = build_report_view_delete("main", "default", "efile_ops")
    assert sql == "DELETE FROM main.default.report_view WHERE view_key = :view_key"
    assert params == [{"name": "view_key", "value": "efile_ops", "type": "STRING"}]


def test_build_report_view_delete_validates():
    """Empty catalog/schema or a non-identifier view_key raises ValueError."""
    with pytest.raises(ValueError):
        build_report_view_delete("", "default", "v")
    with pytest.raises(ValueError):
        build_report_view_delete("main", "", "v")
    with pytest.raises(ValueError):
        build_report_view_delete("main", "default", "bad; DROP")


# --- server-side paging builders (build_report_page_query / count / probe) ---

_PSRC = "SELECT report_date, region, amount FROM cat.sch.tbl"


def _pnames(params):
    return [p["name"] for p in params]


def test_page_query_shape_limit_offset_and_star():
    sql, params = build_report_page_query(_PSRC, limit=25, offset=50)
    # Wraps the inner report query as a subquery, selects *, paginates.
    assert "SELECT * FROM ( SELECT * FROM ( " + _PSRC in sql
    assert sql.rstrip().endswith("LIMIT 25 OFFSET 50")
    assert params == []  # no filters/date/search


def test_page_query_limit_offset_are_clamped_ints_not_bound():
    # LIMIT/OFFSET are interpolated integers (Databricks doesn't bind them), and
    # are coerced so a huge/negative value can't produce unbounded/invalid SQL.
    sql, _ = build_report_page_query(_PSRC, limit=10**9, offset=-5)
    assert "LIMIT 1000000 OFFSET 0" in sql
    # Non-int-ish input must raise rather than interpolate arbitrary text.
    with pytest.raises((ValueError, TypeError)):
        build_report_page_query(_PSRC, limit="25; DROP TABLE x", offset=0)


def test_page_query_filters_and_date_are_bound_in_inner():
    sql, params = build_report_page_query(
        _PSRC, date_field="report_date", report_date="2026-01-01",
        filters={"region": "NE"}, limit=50, offset=0,
    )
    names = _pnames(params)
    assert "report_date" in names and "flt_region" in names
    # Values are bound, never interpolated.
    assert "NE" not in sql and "2026-01-01" not in sql
    assert ":report_date" in sql and ":flt_region" in sql


def test_page_query_search_is_bound_and_ors_columns():
    sql, params = build_report_page_query(
        _PSRC, search="Ne'w", search_columns=["region", "amount"], limit=50, offset=0,
    )
    # One bound search param, OR'd, case-insensitive, cast to string.
    assert ":q_search" in sql and sql.count(":q_search") == 2
    assert "lower(CAST(region AS STRING)) LIKE :q_search" in sql
    assert " OR " in sql and "WHERE (" in sql
    qp = [p for p in params if p["name"] == "q_search"][0]
    assert qp["value"] == "%ne'w%"  # lowered, wrapped; raw value bound (no injection)
    assert "Ne'w" not in sql


def test_page_query_blank_search_adds_no_predicate():
    sql, params = build_report_page_query(_PSRC, search="   ", search_columns=["region"])
    assert ":q_search" not in sql and "WHERE" not in sql
    assert params == []


def test_page_query_sort_key_overrides_order_by_with_direction():
    sql, _ = build_report_page_query(_PSRC, order_by="region", sort_key="amount", sort_dir="desc")
    assert "ORDER BY amount DESC" in sql
    # falls back to configured order_by when no sort_key
    sql2, _ = build_report_page_query(_PSRC, order_by="region")
    assert "ORDER BY region ASC" in sql2


def test_page_query_numeric_sort_uses_try_cast():
    sql, _ = build_report_page_query(_PSRC, sort_key="amount", numeric_sort=True)
    assert "ORDER BY TRY_CAST(amount AS DOUBLE) ASC" in sql
    sql2, _ = build_report_page_query(_PSRC, sort_key="region", numeric_sort=False)
    assert "ORDER BY region ASC" in sql2


def test_page_query_rejects_bad_identifiers():
    with pytest.raises(ValueError):
        build_report_page_query(_PSRC, sort_key="amount; DROP TABLE x")
    with pytest.raises(ValueError):
        build_report_page_query(_PSRC, search="x", search_columns=["a b"])
    with pytest.raises(ValueError):
        build_report_page_query(_PSRC, filters={"1bad": "v"})


def test_count_query_matches_filters_and_search():
    sql, params = build_report_count_query(
        _PSRC, filters={"region": "NE"}, search="acme", search_columns=["region"],
    )
    assert sql.startswith("SELECT COUNT(*) FROM ( SELECT * FROM ( " + _PSRC)
    assert ":flt_region" in sql and ":q_search" in sql
    assert "ORDER BY" not in sql and "LIMIT" not in sql
    assert {"flt_region", "q_search"} <= set(_pnames(params))


def test_count_query_wraps_aggregate_so_it_counts_groups():
    aggs = [{"func": "sum", "source": "amount", "output": "total"}]
    sql, _ = build_report_count_query(_PSRC, columns=["region"], aggregates=aggs)
    assert "GROUP BY region" in sql
    assert sql.startswith("SELECT COUNT(*) FROM (")


def test_columns_probe_is_zero_row_select_star():
    sql = build_columns_probe_query(_PSRC)
    assert sql == "SELECT * FROM ( " + _PSRC + " ) AS _q LIMIT 0"
    with pytest.raises(ValueError):
        build_columns_probe_query("SELECT 1; DROP TABLE x")
