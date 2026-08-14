"""Pure OBO token extraction for the Data Download Hub app.

Databricks Apps forward the signed-in user's OAuth access token in the
``X-Forwarded-Access-Token`` request header (LOCKED DECISION L3). This module
extracts that token so the app can query the gold table AS THE USER. It has NO
SDK import and no network call, so it is unit-testable offline.

There is NO fallback: if the header is absent/empty, a ``PermissionError`` is
raised (no CLI profile, no mock data).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # typing only — auth never runtime-imports reports (no cycle)
    from reports import ReportConfig

USER_TOKEN_HEADER: str = "x-forwarded-access-token"
USER_EMAIL_HEADER: str = "x-forwarded-user"

# Generic default Databricks group whose members may use the gated download
# feature when a report does not name its own group. Per-report control lives in
# the report_config ``download_group`` column (see effective_download_group);
# this constant is only the fallback. Membership is re-checked server-side on
# POST /download.
DOWNLOAD_GROUP: str = "download_hub_download_users"


def effective_download_group(report: "ReportConfig") -> str:
    """Return the per-report download group when set, else the code default.

    Treats ``None`` / whitespace-only ``download_group`` as unset and falls back
    to :data:`DOWNLOAD_GROUP`. Used by BOTH the button-visibility gate and the
    server-side ``POST /download`` enforcement.

    Args:
        report: The active report config (only its ``download_group`` is read).

    Returns:
        The report's ``download_group`` (stripped) if non-empty, else
        :data:`DOWNLOAD_GROUP`.
    """
    return (getattr(report, "download_group", None) or "").strip() or DOWNLOAD_GROUP


def _get_case_insensitive(headers: Any, key: str) -> str | None:
    """Look up ``key`` in a headers-like object, case-insensitively.

    Works for Starlette ``Headers`` (whose ``.get`` is already case-insensitive)
    and for plain mappings with arbitrary key casing.

    Args:
        headers: A mapping/Headers-like object exposing ``get`` and/or ``items``.
        key: The (lowercase) header name to look up.

    Returns:
        The header value if found and non-empty, else ``None``.
    """
    key_l = key.lower()
    get = getattr(headers, "get", None)
    if callable(get):
        value = get(key)
        if value:
            return value
    items = getattr(headers, "items", None)
    if callable(items):
        for k, v in items():
            if isinstance(k, str) and k.lower() == key_l and v:
                return v
    return None


def extract_user_token(headers: Any) -> str:
    """Return the signed-in user's OBO access token from the request headers.

    Args:
        headers: A mapping/Headers-like object with a case-insensitive ``get``
            (e.g. Starlette ``request.headers``) or a plain mapping.

    Returns:
        The user's OAuth access token from ``X-Forwarded-Access-Token``.

    Raises:
        PermissionError: If the header is absent or empty (LOCKED DECISION L3 —
            no CLI/mock fallback).
    """
    token = _get_case_insensitive(headers, USER_TOKEN_HEADER)
    if not token:
        raise PermissionError(
            "Missing X-Forwarded-Access-Token header. This app must run with "
            "user authorization (scope 'sql') enabled so the gold table is read "
            "as the signed-in user. No fallback is available."
        )
    return token


def extract_user_email(headers: Any) -> str:
    """Return the signed-in user's email from the request headers, or ``""``.

    The Databricks Apps runtime forwards the signed-in user's email in the
    ``X-Forwarded-User`` header. This value is best-effort for the audit row —
    it is NOT an authorization gate — so an absent header yields ``""`` rather
    than raising.

    Args:
        headers: A mapping/Headers-like object with a case-insensitive ``get``
            (e.g. Starlette ``request.headers``) or a plain mapping.

    Returns:
        The user's email from ``X-Forwarded-User``, or ``""`` if absent/empty.
    """
    return _get_case_insensitive(headers, USER_EMAIL_HEADER) or ""


def group_display_names(me_user: Any) -> list[str]:
    """Return the display names of the groups on a SCIM ``me()`` User.

    Tolerates a user object with no ``groups`` attribute and group entries with
    no ``display`` attribute (via ``getattr``), so it is safe to call on any
    SCIM ``User``-like object.

    Args:
        me_user: The ``User`` object returned by ``current_user.me()`` (or any
            object exposing an optional ``groups`` list whose entries expose an
            optional ``display``).

    Returns:
        The list of non-empty group display names (empty if none).
    """
    groups = getattr(me_user, "groups", None) or []
    return [g.display for g in groups if getattr(g, "display", None)]


def is_member(me_user: Any, group_display: str) -> bool:
    """Return whether the user belongs to a group with the given display name.

    Pure name-match helper (unit-testable offline); the ``me()`` I/O call lives
    in ``main.py`` (LOCKED DECISION L1).

    Args:
        me_user: The ``User`` object from ``current_user.me()``.
        group_display: The group display name to check (e.g.
            :data:`DOWNLOAD_GROUP`).

    Returns:
        ``True`` if ``group_display`` is among the user's group display names,
        else ``False``.
    """
    return group_display in group_display_names(me_user)
