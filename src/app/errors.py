"""Pure error classification for the report portal (explicit, user-facing text).

Stdlib-only, no SDK/fastapi/network import, so it is unit-testable in the
pytest-only dev ``.venv``. The I/O boundary (``main.py``) catches the raw
exceptions the Statement Execution API raises and routes their messages through
:func:`friendly_error` so a missing table, dropped column, denied grant, or
absent warehouse surfaces as a concise, specific explanation instead of a bare
HTTP 500 / stack trace.

Each classified message is followed by a short, safe ``(Details: …)`` excerpt of
the raw error (an error-class token, SQLSTATE, or the trimmed first line) so a
user or admin can see WHAT actually failed — the classification alone hid too
much (a bare "could not be loaded" is not actionable). No stack traces leak.

:class:`ReportDataError` subclasses :class:`RuntimeError` so the existing
``except RuntimeError`` handlers keep catching it; its message is already the
friendly text.
"""

from __future__ import annotations

import re


class ReportDataError(RuntimeError):
    """A data read/registry access failed with a user-facing explanation.

    The message is the already-friendly text from :func:`friendly_error`; routes
    surface ``str(exc)`` directly to the UI (as an alert or inline notice).
    """


# Ordered (substring-in-lowered-message -> friendly text) rules. First match
# wins, so put the most specific signals first. Signals are drawn from the
# Databricks SQL / Spark error surface (error classes + human messages).
#
# IMPORTANT: signals must be SPECIFIC. A prior version matched the bare token
# "requires" for permission, which mislabeled many non-permission errors (e.g.
# "...requires a running warehouse", "GROUP BY ... requires ...") as a permission
# denial — users with full native access saw "you do not have permission". Every
# signal below is a strong, unambiguous phrase; permission is matched ONLY by
# genuine authorization phrases.
_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        ("table_or_view_not_found", "table or view not found",
         "schema_not_found", "catalog_not_found"),
        "This report's data table or view could not be found. It may have been "
        "moved, renamed, or not yet created. Contact an administrator.",
    ),
    (
        ("unresolved_column", "unresolved column", "unresolved_routine",
         "no such struct field", "column_not_found", "cannot be resolved"),
        "A column configured for this report does not exist in its query. "
        "Check the report configuration.",
    ),
    (
        ("no active sql warehouse", "sql endpoint", "warehouse is stopped",
         "no cluster", "warehouse_not_found", "endpoint is not running"),
        "The SQL warehouse for this app is unavailable or not started. "
        "Contact an administrator.",
    ),
    (
        ("parse_syntax_error", "syntax error", "parse error", "cannot parse",
         "unclosed", "extraneous input"),
        "This report's configured query is invalid SQL. Check the report "
        "configuration.",
    ),
    (
        ("timed out", "timeout", "deadline exceeded", "deadline",
         "statement_timeout", "query execution timed out"),
        "The report took too long to load. Try a narrower date or filter, or try "
        "again shortly.",
    ),
    (
        # Genuine authorization signals ONLY — no broad tokens like "requires".
        ("permission_denied", "permission denied", "access denied",
         "does not have privilege", "insufficient privileges", "is not authorized",
         "unauthorized", "no permission to", "requires privilege"),
        "You do not have permission to read this report's data. If you can read "
        "it in Databricks directly, the app may be querying it as you without the "
        "needed grant — contact an administrator.",
    ),
)

_GENERIC: str = (
    "The report could not be loaded right now. If this persists, contact an "
    "administrator."
)

# An uppercase error-class token the Databricks error surface emits, e.g.
# [TABLE_OR_VIEW_NOT_FOUND] or [INSUFFICIENT_PERMISSIONS].
_ERROR_CLASS_RE = re.compile(r"\[([A-Z][A-Z0-9_.]{3,})\]")
_SQLSTATE_RE = re.compile(r"SQLSTATE[:=]?\s*([0-9A-Z]{5})", re.IGNORECASE)
_MAX_DETAIL = 240


def _extract_detail(raw: str) -> str:
    """Pull a short, safe diagnostic excerpt from a raw error (no stack trace).

    Prefers a bracketed error-class token, then a SQLSTATE code, then the first
    non-empty line — always collapsed to one line and truncated. Returns ``""``
    when nothing useful is present.

    Args:
        raw: The raw exception/error text.

    Returns:
        A concise one-line detail (<= ~240 chars), or ``""``.
    """
    m = _ERROR_CLASS_RE.search(raw)
    cls = m.group(1) if m else ""
    sm = _SQLSTATE_RE.search(raw)
    sqlstate = sm.group(1) if sm else ""

    # First meaningful line, whitespace-collapsed.
    first_line = ""
    for line in raw.splitlines():
        s = " ".join(line.split()).strip()
        if s:
            first_line = s
            break

    parts: list[str] = []
    if cls:
        parts.append(cls)
    if sqlstate and sqlstate not in cls:
        parts.append(f"SQLSTATE {sqlstate}")
    detail = " ".join(parts)
    # If we only have an error class, still add the first line for context when it
    # adds information beyond the class name.
    if first_line and first_line.lower() not in detail.lower():
        detail = f"{detail}: {first_line}" if detail else first_line

    detail = detail.strip(" :")
    if len(detail) > _MAX_DETAIL:
        detail = detail[: _MAX_DETAIL - 1].rstrip() + "…"
    return detail


def friendly_error(raw: str | None, *, include_detail: bool = True) -> str:
    """Map a raw DB/SDK error message to a concise, user-facing explanation.

    Case-insensitive substring match against an ordered rule set; the first
    matching rule wins. An unrecognized message falls back to a generic (but
    still non-technical) explanation. Unless ``include_detail`` is ``False``, a
    short ``(Details: …)`` excerpt of the raw error is appended so the user/admin
    can see the actual cause.

    Args:
        raw: The raw exception/error text (or ``None``).
        include_detail: Append the safe ``(Details: …)`` excerpt (default True).

    Returns:
        A short, specific, user-facing message. Never includes a stack trace.
    """
    text = raw or ""
    low = text.lower()
    message = _GENERIC
    for signals, msg in _RULES:
        if any(s in low for s in signals):
            message = msg
            break

    if not include_detail:
        return message
    detail = _extract_detail(text)
    if detail:
        return f"{message} (Details: {detail})"
    return message
