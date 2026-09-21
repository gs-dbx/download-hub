"""Unit tests for the pure async export-job builders in ``app.jobs``.

No fastapi, no databricks.sdk, no network — runs offline (the export pipeline's
I/O lives in ``main.py``, which is untested per the boundary rule).
"""

import pytest

from app.jobs import (
    JOB_COLUMNS,
    STATUS_EXPIRED,
    STATUS_FAILED,
    STATUS_QUEUED,
    STATUS_READY,
    STATUS_RUNNING,
    build_active_export_job_query,
    build_export_job_by_id_query,
    build_export_job_insert,
    build_export_job_row,
    build_export_job_status_update,
    build_export_jobs_expire_update,
    build_export_jobs_query,
    build_export_jobs_reconcile_query,
    export_job_fingerprint,
    export_retrieve_subpath,
    job_view_model,
    normalize_job_status,
    parse_export_job_row,
    reconcile_job_status,
    select_expired_files,
)

CAT, SCH = "irs_synthetic", "download_hub"


def _pmap(params):
    """Return a {name: (value, type)} map for a param-dict list."""
    return {p["name"]: (p["value"], p["type"]) for p in params}


# --- normalize_job_status -------------------------------------------------


def test_normalize_job_status_legal_and_fallback():
    for s in (STATUS_QUEUED, STATUS_RUNNING, STATUS_READY, STATUS_FAILED, STATUS_EXPIRED):
        assert normalize_job_status(s) == s
    assert normalize_job_status("READY") == STATUS_READY  # case-insensitive
    for bad in ("", None, "bogus", "done"):
        assert normalize_job_status(bad) == STATUS_QUEUED


# --- fingerprint / subpath ------------------------------------------------


def test_fingerprint_deterministic_and_sensitive():
    base = export_job_fingerprint("u@x", "r1", "csv", "region=NE", "acme")
    assert base == export_job_fingerprint("u@x", "r1", "csv", "region=NE", "acme")
    # Any input change flips it.
    assert base != export_job_fingerprint("v@x", "r1", "csv", "region=NE", "acme")
    assert base != export_job_fingerprint("u@x", "r2", "csv", "region=NE", "acme")
    assert base != export_job_fingerprint("u@x", "r1", "xlsx", "region=NE", "acme")
    assert base != export_job_fingerprint("u@x", "r1", "csv", "region=SW", "acme")
    assert base != export_job_fingerprint("u@x", "r1", "csv", "region=NE", "other")


def test_export_retrieve_subpath_shape():
    assert export_retrieve_subpath("slug-abc", "job-1", "daily.csv") == "slug-abc/job-1/daily.csv"


# --- build_export_job_row -------------------------------------------------


def _row(**kw):
    kw.setdefault("audit_id", "aud-1")
    kw.setdefault("user_email", "u@x")
    kw.setdefault("email_slug", "u-slug")
    kw.setdefault("report_id", "r1")
    kw.setdefault("report_title", "Report One")
    kw.setdefault("export_format", "CSV")
    kw.setdefault("row_count", "42")
    kw.setdefault("retrieve_path", "u-slug/j1/r1.csv")
    kw.setdefault("fingerprint", "fp")
    return build_export_job_row(**kw)


def test_build_export_job_row_defaults_and_coercion():
    row = _row()
    assert row["job_id"]  # default uuid
    assert row["status"] == STATUS_QUEUED
    assert row["row_count"] == 42 and isinstance(row["row_count"], int)
    assert row["export_format"] == "csv"  # lowercased
    assert row["message"] == ""


def test_build_export_job_row_explicit_job_id_and_status():
    row = _row(job_id="fixed", status="RUNNING")
    assert row["job_id"] == "fixed"
    assert row["status"] == STATUS_RUNNING


# --- build_export_job_insert ----------------------------------------------


def test_build_export_job_insert_shape():
    row = _row(job_id="j1")
    sql, params = build_export_job_insert(CAT, SCH, row)
    assert f"INSERT INTO {CAT}.{SCH}.export_jobs" in sql
    assert "current_timestamp(), current_timestamp()" in sql  # created + updated
    pm = _pmap(params)
    assert pm["job_id"] == ("j1", "STRING")
    assert pm["row_count"] == ("42", "BIGINT")  # value stringified, typed BIGINT
    assert pm["export_format"] == ("csv", "STRING")
    # No bound timestamp params.
    assert "created_ts" not in pm and "updated_ts" not in pm


def test_build_export_job_insert_requires_catalog_schema():
    with pytest.raises(ValueError):
        build_export_job_insert("", SCH, _row())
    with pytest.raises(ValueError):
        build_export_job_insert(CAT, "", _row())


# --- build_export_job_status_update ---------------------------------------


def test_status_update_sets_status_message_and_updated_ts():
    sql, params = build_export_job_status_update(
        CAT, SCH, job_id="j1", status="running"
    )
    assert sql.startswith(f"UPDATE {CAT}.{SCH}.export_jobs SET")
    assert "updated_ts = current_timestamp()" in sql
    assert "retrieve_path" not in sql  # not set unless provided
    assert sql.strip().endswith("WHERE job_id = :job_id")
    pm = _pmap(params)
    assert pm["status"] == (STATUS_RUNNING, "STRING")
    assert pm["job_id"] == ("j1", "STRING")


def test_status_update_includes_retrieve_path_when_given():
    sql, params = build_export_job_status_update(
        CAT, SCH, job_id="j1", status="ready", retrieve_path="u/j1/r.csv"
    )
    assert "retrieve_path = :retrieve_path" in sql
    assert _pmap(params)["retrieve_path"] == ("u/j1/r.csv", "STRING")


def test_status_update_normalizes_illegal_status():
    _sql, params = build_export_job_status_update(CAT, SCH, job_id="j1", status="nope")
    assert _pmap(params)["status"] == (STATUS_QUEUED, "STRING")


# --- SELECT builders ------------------------------------------------------


def test_build_export_jobs_query_scopes_and_orders():
    sql, params = build_export_jobs_query(CAT, SCH, "u-slug", limit=9999)
    assert "WHERE email_slug = :email_slug" in sql
    assert "ORDER BY created_ts DESC LIMIT 500" in sql  # clamped to 500
    assert _pmap(params)["email_slug"] == ("u-slug", "STRING")
    assert all(c in sql for c in JOB_COLUMNS)


def test_build_export_job_by_id_query():
    sql, params = build_export_job_by_id_query(CAT, SCH, "j1")
    assert "WHERE job_id = :job_id LIMIT 1" in sql
    assert _pmap(params)["job_id"] == ("j1", "STRING")


def test_build_active_export_job_query_dedupe():
    sql, params = build_active_export_job_query(CAT, SCH, "u-slug", "fp1")
    assert "email_slug = :email_slug" in sql and "fingerprint = :fingerprint" in sql
    assert f"status IN ('{STATUS_QUEUED}', '{STATUS_RUNNING}')" in sql
    pm = _pmap(params)
    assert pm["email_slug"] == ("u-slug", "STRING")
    assert pm["fingerprint"] == ("fp1", "STRING")


def test_build_export_jobs_reconcile_query():
    sql = build_export_jobs_reconcile_query(CAT, SCH)
    assert "SELECT job_id, retrieve_path, status" in sql
    assert f"status IN ('{STATUS_QUEUED}', '{STATUS_RUNNING}')" in sql


def test_build_export_jobs_expire_update_clamps_interval():
    sql = build_export_jobs_expire_update(CAT, SCH, ttl_hours=24)
    assert f"SET status = '{STATUS_EXPIRED}'" in sql
    assert "INTERVAL 24 HOURS" in sql
    assert f"status <> '{STATUS_EXPIRED}'" in sql
    # Clamp bounds.
    assert "INTERVAL 1 HOURS" in build_export_jobs_expire_update(CAT, SCH, 0)
    assert "INTERVAL 8760 HOURS" in build_export_jobs_expire_update(CAT, SCH, 99999)


def test_select_builders_require_catalog_schema():
    for fn in (
        lambda: build_export_jobs_query("", SCH, "s"),
        lambda: build_export_job_by_id_query("", SCH, "j"),
        lambda: build_active_export_job_query("", SCH, "s", "f"),
        lambda: build_export_jobs_reconcile_query("", SCH),
        lambda: build_export_jobs_expire_update("", SCH),
    ):
        with pytest.raises(ValueError):
            fn()


# --- reconcile_job_status -------------------------------------------------


def test_reconcile_active_with_file_becomes_ready():
    status, msg = reconcile_job_status("running", file_exists=True)
    assert status == STATUS_READY and msg == ""
    status, msg = reconcile_job_status("queued", file_exists=True)
    assert status == STATUS_READY


def test_reconcile_active_without_file_becomes_failed():
    status, msg = reconcile_job_status("running", file_exists=False)
    assert status == STATUS_FAILED and "request it again" in msg.lower()


def test_reconcile_terminal_unchanged():
    for s in (STATUS_READY, STATUS_FAILED, STATUS_EXPIRED):
        assert reconcile_job_status(s, file_exists=False) == (s, "")


# --- parse_export_job_row / job_view_model --------------------------------


def test_parse_export_job_row_maps_and_defaults():
    cols = ["job_id", "status", "row_count"]
    parsed = parse_export_job_row(cols, ["j1", "ready", "10"])
    assert parsed["job_id"] == "j1" and parsed["status"] == "ready"
    # Missing columns default to "".
    assert parsed["report_title"] == ""


def test_job_view_model_ready():
    vm = job_view_model(
        {
            "job_id": "j1",
            "report_title": "R",
            "export_format": "csv",
            "row_count": "123",
            "status": "ready",
            "created_ts": "2026-09-21T13:04:05.123Z",
        }
    )
    assert vm["is_ready"] and vm["is_terminal"] and not vm["is_active"]
    assert vm["row_count"] == 123
    assert vm["status_label"] == "Ready"
    assert vm["retrieve_href"] == "/download/retrieve?job_id=j1"
    assert vm["created_display"] == "2026-09-21 13:04:05"


def test_job_view_model_active_has_no_retrieve_href():
    vm = job_view_model({"job_id": "j2", "status": "queued", "row_count": "x"})
    assert vm["is_active"] and not vm["is_ready"]
    assert vm["retrieve_href"] == ""
    assert vm["row_count"] == 0  # unparseable -> 0
    assert vm["status_label"] == "Queued"


# --- select_expired_files -------------------------------------------------


def test_select_expired_files_by_age():
    now = 1_700_000_000.0  # realistic epoch seconds (so ms values exceed 1e11)
    ttl = 24
    old = now - 25 * 3600  # older than 24h -> expired
    fresh = now - 1 * 3600  # within 24h -> kept
    entries = [
        {"path": "a/old.csv", "modified": old},
        {"path": "b/fresh.csv", "modified": fresh},
        {"path": "c/nots.csv", "modified": None},  # unparseable -> never deleted
        {"path": "d/ms.csv", "modified": old * 1000.0},  # epoch ms -> also expired
    ]
    doomed = select_expired_files(entries, now, ttl)
    assert doomed == ["a/old.csv", "d/ms.csv"]


def test_select_expired_files_boundary_and_ttl_clamp():
    now = 1_700_000_000.0
    # Exactly ttl old is NOT strictly older -> kept.
    at_boundary = [{"path": "x", "modified": now - 24 * 3600}]
    assert select_expired_files(at_boundary, now, 24) == []
    # ttl clamped to >= 1 hour.
    entries = [{"path": "x", "modified": now - 2 * 3600}]
    assert select_expired_files(entries, now, 0) == ["x"]
