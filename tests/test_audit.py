"""Unit tests for the pure audit builders in ``app.audit``.

No fastapi, no databricks.sdk, no network (LOCKED DECISION L2).
"""

import uuid

import pytest

from app.audit import build_audit_insert, build_audit_row
from app.cache import filters_summary

_KWARGS = dict(
    user_email="a@b.c",
    report_date="2026-01-12 00:00:00",
    filter_summary="channel=ALL",
    search_filter="",
    row_count=17,
    export_format="csv",
    justification="Daily metrics export",
    app_version="0.4.0",
    report_id="daily_metrics",
    report_title="Daily Metrics Overview",
)


def test_build_audit_row_has_all_logical_fields():
    """The row carries all logical fields (event_ts is added by the SQL)."""
    row = build_audit_row(**_KWARGS, source_query="SELECT * FROM t")
    expected = {
        "audit_id",
        "user_email",
        "report_date",
        "filter_summary",
        "search_filter",
        "row_count",
        "export_format",
        "justification",
        "acknowledged",
        "app_version",
        "report_id",
        "report_title",
        "source_query",
    }
    assert set(row) == expected
    assert row["acknowledged"] is True
    assert row["row_count"] == 17
    assert row["report_id"] == "daily_metrics"
    assert row["report_title"] == "Daily Metrics Overview"
    assert row["source_query"] == "SELECT * FROM t"


def test_build_audit_row_source_query_defaults_empty():
    """source_query defaults to '' when not supplied (back-compat)."""
    assert build_audit_row(**_KWARGS)["source_query"] == ""


def test_filters_summary_sorts_joins_and_drops_empty():
    """filters_summary sorts by field, joins 'k=v; ...', drops empty, '' when none."""
    assert filters_summary({"channel": "ALL", "z": ""}) == "channel=ALL"
    assert filters_summary({"b": "2", "a": "1"}) == "a=1; b=2"
    assert filters_summary({}) == ""
    assert filters_summary({"x": ""}) == ""


def test_build_audit_row_audit_id_is_uuid_and_unique():
    """audit_id defaults to a valid uuid4 and differs per call."""
    a = build_audit_row(**_KWARGS)["audit_id"]
    b = build_audit_row(**_KWARGS)["audit_id"]
    assert a != b
    uuid.UUID(a)  # parses => valid uuid
    uuid.UUID(b)


def test_build_audit_row_search_filter_defaults_empty():
    """A None search_filter is normalized to the empty string (never NULL)."""
    kwargs = dict(_KWARGS)
    kwargs["search_filter"] = None
    assert build_audit_row(**kwargs)["search_filter"] == ""


def test_build_audit_row_explicit_audit_id():
    """An explicit audit_id is respected."""
    row = build_audit_row(**_KWARGS, audit_id="fixed-id")
    assert row["audit_id"] == "fixed-id"


def test_build_audit_insert_fqn_and_placeholders():
    """INSERT uses the 3-level FQN and :named placeholders; event_ts is SQL."""
    row = build_audit_row(**_KWARGS)
    sql, params = build_audit_insert("main", "default", row)
    assert "INSERT INTO main.default.download_audit" in sql
    # event_ts is set server-side, not bound.
    assert "current_timestamp()" in sql
    assert ":event_ts" not in sql
    for name in (
        "audit_id",
        "user_email",
        "report_date",
        "filter_summary",
        "search_filter",
        "row_count",
        "export_format",
        "justification",
        "acknowledged",
        "app_version",
        "report_id",
        "report_title",
        "source_query",
    ):
        assert f":{name}" in sql
    # 13 bound params (event_ts uses current_timestamp()).
    assert len(params) == 13
    assert {p["name"] for p in params} == {
        "audit_id",
        "user_email",
        "report_date",
        "filter_summary",
        "search_filter",
        "row_count",
        "export_format",
        "justification",
        "acknowledged",
        "app_version",
        "report_id",
        "report_title",
        "source_query",
    }


def test_build_audit_insert_all_values_are_strings():
    """Every param value is a string (row_count cast, acknowledged 'true')."""
    row = build_audit_row(**_KWARGS)
    _sql, params = build_audit_insert("main", "default", row)
    by_name = {p["name"]: p for p in params}
    assert all(isinstance(p["value"], str) for p in params)
    assert by_name["row_count"]["value"] == "17"
    assert by_name["row_count"]["type"] == "BIGINT"
    assert by_name["acknowledged"]["value"] == "true"
    assert by_name["acknowledged"]["type"] == "BOOLEAN"
    # report_date is bound as STRING and cast (CAST(NULLIF(...) AS TIMESTAMP)) so
    # the "All dates" export (empty date) stores NULL rather than failing.
    assert by_name["report_date"]["type"] == "STRING"


def test_build_audit_insert_empty_report_date_casts_to_null():
    """An empty report_date (All dates) is wrapped so it stores NULL, not ''."""
    row = build_audit_row(**{**_KWARGS, "report_date": ""})
    sql, params = build_audit_insert("main", "default", row)
    assert "CAST(NULLIF(:report_date, '') AS TIMESTAMP)" in sql
    rd = next(p for p in params if p["name"] == "report_date")
    assert rd["value"] == "" and rd["type"] == "STRING"


def test_build_audit_insert_acknowledged_false():
    """acknowledged=False renders as the string 'false'."""
    row = build_audit_row(**_KWARGS, acknowledged=False)
    _sql, params = build_audit_insert("main", "default", row)
    ack = next(p for p in params if p["name"] == "acknowledged")
    assert ack["value"] == "false"


def test_build_audit_insert_requires_catalog_schema():
    """Empty catalog/schema raises ValueError."""
    row = build_audit_row(**_KWARGS)
    with pytest.raises(ValueError):
        build_audit_insert("", "default", row)
    with pytest.raises(ValueError):
        build_audit_insert("main", "", row)
