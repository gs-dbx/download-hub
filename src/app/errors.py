"""Pure error classification for the report portal (explicit, user-facing text).

Stdlib-only, no SDK/fastapi/network import, so it is unit-testable in the
pytest-only dev ``.venv``. The I/O boundary (``main.py``) catches the raw
exceptions the Statement Execution API raises and routes their messages through
:func:`friendly_error` so a missing table, dropped column, denied grant, or
absent warehouse surfaces as a concise, specific explanation instead of a bare
HTTP 500 / stack trace.

:class:`ReportDataError` subclasses :class:`RuntimeError` so the existing
``except RuntimeError`` handlers keep catching it; its message is already the
friendly text.
"""

from __future__ import annotations


class ReportDataError(RuntimeError):
    """A data read/registry access failed with a user-facing explanation.

    The message is the already-friendly text from :func:`friendly_error`; routes
    surface ``str(exc)`` directly to the UI (as an alert or inline notice).
    """


# Ordered (substring-in-lowered-message -> friendly text) rules. First match
# wins, so put the most specific signals first. Signals are drawn from the
# Databricks SQL / Spark error surface (error classes + human messages).
_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        ("table_or_view_not_found", "table or view not found", "cannot be found"),
        "This report's data table or view could not be found. It may have been "
        "moved, renamed, or not yet created. Contact an administrator.",
    ),
    (
        ("unresolved_column", "cannot be resolved", "unresolved column",
         "no such struct field", "unresolved_routine", "unresolved column"),
        "A column configured for this report does not exist in its query. "
        "Check the report configuration.",
    ),
    (
        ("permission_denied", "permission denied", "access denied",
         "does not have privilege", "is not authorized", "requires", "no permission"),
        "You do not have permission to read this report's data.",
    ),
    (
        ("warehouse", "sql endpoint", "no cluster"),
        "The SQL warehouse for this app is unavailable or not started. "
        "Contact an administrator.",
    ),
    (
        ("parse_syntax_error", "syntax error", "parse error", "cannot parse"),
        "This report's configured query is invalid SQL. Check the report "
        "configuration.",
    ),
    (
        ("timed out", "timeout", "deadline"),
        "The report took too long to load. Try a narrower date or filter, or try "
        "again shortly.",
    ),
)

_GENERIC: str = (
    "The report could not be loaded right now. If this persists, contact an "
    "administrator."
)


def friendly_error(raw: str | None) -> str:
    """Map a raw DB/SDK error message to a concise, user-facing explanation.

    Case-insensitive substring match against an ordered rule set; the first
    matching rule wins. An unrecognized message falls back to a generic (but
    still non-technical) explanation.

    Args:
        raw: The raw exception/error text (or ``None``).

    Returns:
        A short, specific, user-facing message. Never includes a stack trace.
    """
    low = (raw or "").lower()
    for signals, message in _RULES:
        if any(s in low for s in signals):
            return message
    return _GENERIC
