"""Unit tests for the pure error classifier in ``app.errors``.

No fastapi, no databricks.sdk, no network — runs offline.
"""

import pytest

from app.errors import ReportDataError, friendly_error


def test_report_data_error_is_runtime_error():
    """ReportDataError subclasses RuntimeError so existing handlers catch it."""
    assert issubclass(ReportDataError, RuntimeError)
    with pytest.raises(RuntimeError):
        raise ReportDataError("boom")


@pytest.mark.parametrize(
    "raw",
    [
        "[TABLE_OR_VIEW_NOT_FOUND] The table or view `main.default.x` cannot be found",
        "Table or view not found: main.default.gone",
    ],
)
def test_friendly_error_missing_table(raw):
    """A missing-table error maps to the table/view explanation."""
    msg = friendly_error(raw)
    assert "table or view" in msg.lower()
    assert "administrator" in msg.lower()


def test_friendly_error_missing_column():
    """An unresolved-column error maps to the column explanation."""
    msg = friendly_error("[UNRESOLVED_COLUMN] A column `foo` cannot be resolved")
    assert "column" in msg.lower()
    assert "configuration" in msg.lower()


@pytest.mark.parametrize(
    "raw",
    [
        "PERMISSION_DENIED: user does not have privilege SELECT",
        "User is not authorized to perform this action",
        "Access denied for table main.default.secret",
    ],
)
def test_friendly_error_permission(raw):
    """Permission errors map to the permission explanation."""
    assert "permission" in friendly_error(raw).lower()


def test_friendly_error_warehouse():
    """A warehouse error maps to the warehouse explanation."""
    assert "warehouse" in friendly_error("SQL warehouse 123 not found").lower()


def test_friendly_error_syntax():
    """A parse/syntax error maps to the invalid-query explanation."""
    assert "invalid sql" in friendly_error("PARSE_SYNTAX_ERROR near '('").lower()


def test_friendly_error_timeout():
    """A timeout maps to the took-too-long explanation."""
    assert "too long" in friendly_error("statement timed out after 30s").lower()


@pytest.mark.parametrize("raw", [None, "", "some totally novel backend error"])
def test_friendly_error_generic_fallback(raw):
    """Unrecognized/empty messages fall back to the generic explanation."""
    msg = friendly_error(raw)
    assert "could not be loaded" in msg.lower()
    # Never leaks a raw/technical token.
    if raw:
        assert raw not in msg
