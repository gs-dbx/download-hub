"""Unit tests for the pure error classifier in ``app.errors``.

No fastapi, no databricks.sdk, no network — stdlib-only.
"""

from app.errors import ReportDataError, friendly_error


def _base(raw: str) -> str:
    """friendly_error without the appended detail, for message-only assertions."""
    return friendly_error(raw, include_detail=False)


def test_report_data_error_is_runtimeerror():
    """ReportDataError stays catchable by existing ``except RuntimeError``."""
    assert issubclass(ReportDataError, RuntimeError)


def test_table_not_found_classified():
    assert "could not be found" in _base("[TABLE_OR_VIEW_NOT_FOUND] blah")


def test_unresolved_column_classified():
    assert "column configured" in _base("UNRESOLVED_COLUMN: `foo`")


def test_warehouse_classified():
    assert "SQL warehouse" in _base("No active SQL warehouse is available")


def test_syntax_classified():
    assert "invalid SQL" in _base("PARSE_SYNTAX_ERROR near 'SELCT'")


def test_timeout_classified():
    assert "too long" in _base("Statement timed out after 120s")


def test_permission_classified_on_genuine_signal():
    for raw in (
        "PERMISSION_DENIED: user lacks SELECT",
        "User is not authorized to perform this action",
        "does not have privilege SELECT on table",
        "Insufficient privileges to access table",
    ):
        assert "do not have permission" in _base(raw), raw


# --- Regression: the bare token "requires" must NOT be read as a permission error.
def test_requires_is_not_a_permission_error():
    """A non-permission error containing 'requires' must not be mislabeled.

    This is the reported bug: users with native access saw a spurious permission
    message because the classifier matched the substring 'requires'.
    """
    raw = "AnalysisException: grouping expression requires that ..."
    assert "do not have permission" not in _base(raw)


def test_requires_running_warehouse_not_permission():
    raw = "This operation requires a running warehouse to be attached"
    msg = _base(raw)
    assert "do not have permission" not in msg


def test_unknown_falls_back_to_generic():
    assert _base("some totally novel failure") .startswith(
        "The report could not be loaded"
    )


# --- Detail appended so the cause is visible -------------------------------
def test_detail_appended_for_generic():
    out = friendly_error("Weird boom [SOME_UNMAPPED_CLASS] happened")
    assert "The report could not be loaded" in out
    assert "Details:" in out
    assert "SOME_UNMAPPED_CLASS" in out


def test_detail_can_be_suppressed():
    assert "Details:" not in friendly_error("boom", include_detail=False)


def test_detail_prefers_error_class_token():
    out = friendly_error("[TABLE_OR_VIEW_NOT_FOUND] table `x` missing")
    assert "TABLE_OR_VIEW_NOT_FOUND" in out


def test_detail_includes_sqlstate_when_present():
    out = friendly_error("boom happened SQLSTATE: 42P01 details")
    assert "42P01" in out


def test_none_is_safe():
    assert friendly_error(None).startswith("The report could not be loaded")


def test_detail_is_single_line_and_bounded():
    raw = "line one is the cause\n" + ("x" * 500)
    out = friendly_error(raw)
    assert "\n" not in out
    assert len(out) < 700
