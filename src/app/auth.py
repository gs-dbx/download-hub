"""Pure OBO token extraction for the Data Download Hub app.

Databricks Apps forward the signed-in user's OAuth access token in the
``X-Forwarded-Access-Token`` request header (LOCKED DECISION L3). This module
extracts that token so the app can query the gold table AS THE USER. It has NO
SDK import and no network call, so it is unit-testable offline.

There is NO fallback: if the header is absent/empty, a ``PermissionError`` is
raised (no CLI profile, no mock data).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # typing only — auth never runtime-imports reports (no cycle)
    from reports import ReportConfig

USER_TOKEN_HEADER: str = "x-forwarded-access-token"
USER_EMAIL_HEADER: str = "x-forwarded-user"

# On some workspaces (e.g. GovCloud) X-Forwarded-User is a numeric SCIM identity
# "<user_id>@<workspace_id>" rather than an email; the leading user_id can be
# resolved to an email via SCIM for display.
_SCIM_ID_RE = re.compile(r"^(\d+)@\d+$")


def parse_scim_user_id(value: str) -> str | None:
    """Return the numeric SCIM user_id when ``value`` is a raw forwarded id.

    A stored identity of the form ``"<user_id>@<workspace_id>"`` (both numeric)
    is the raw ``X-Forwarded-User`` header, not an email; this returns its
    leading ``user_id`` so the caller can resolve it to an email. Any real
    email/display name (or empty) returns ``None`` (nothing to resolve).

    Args:
        value: The stored identity string.

    Returns:
        The numeric ``user_id`` string, or ``None`` when ``value`` is not a raw
        SCIM id.
    """
    if not value:
        return None
    m = _SCIM_ID_RE.match(value)
    return m.group(1) if m else None

# Generic default Databricks group used as the ultimate fallback for a report's
# view group when it has no ``view_key``. In normal operation every report names
# a ``view_key``, so this is only a defensive fallback. Membership is re-checked
# server-side.
DEFAULT_VIEW_GROUP: str = "download_hub_download_users"

# Default admin group whose members may use the report/view admin console. The
# name is overridable via the ``ADMIN_GROUP`` env var (read in ``main.py``).
ADMIN_GROUP: str = "download_hub_admin_users"


def effective_view_group(report: "ReportConfig") -> str:
    """Return the Databricks group that grants access to a report.

    The access group is the report's ``view_key`` (the key doubles as the group).
    Falls back to :data:`DEFAULT_VIEW_GROUP` only when ``view_key`` is unset
    (which should not happen for a well-formed report). This single group governs
    BOTH viewing and downloading — there is no separate download tier.

    Args:
        report: The report config (only its ``view_key`` is read).

    Returns:
        The report's ``view_key`` (stripped) if non-empty, else
        :data:`DEFAULT_VIEW_GROUP`.
    """
    return (getattr(report, "view_key", None) or "").strip() or DEFAULT_VIEW_GROUP


def can_view(me_user: Any, report: "ReportConfig") -> bool:
    """Return whether the user may SEE (and therefore download) a report.

    A user has access iff they belong to the report's access group
    (:func:`effective_view_group`). There is no separate "read-only" tier: any
    user who can see a report may also download it (subject to the global
    download kill switch, layered on in ``main.py``).

    Args:
        me_user: The ``User`` object from ``current_user.me()``.
        report: The report config.

    Returns:
        ``True`` if the user is a member of the report's access group; ``False``
        otherwise.
    """
    return effective_view_group(report) in group_display_names(me_user)


def is_admin(me_user: Any, admin_group: str = ADMIN_GROUP) -> bool:
    """Return whether the user belongs to the report/view admin group.

    Args:
        me_user: The ``User`` object from ``current_user.me()``.
        admin_group: The admin group display name (env-configurable).

    Returns:
        ``True`` if the user is a member of ``admin_group``.
    """
    return is_member(me_user, admin_group)


# ---- Two-tier administration (system admins + per-collection admins) --------
#
# * SYSTEM admins administer EVERY resource collection's config and the
#   collection -> admin-group mapping itself. Their group is env-configurable via
#   ``SYSTEM_ADMIN_GROUP`` (read in ``main.py``); it defaults to
#   :data:`SYSTEM_ADMIN_GROUP` below, which is :data:`ADMIN_GROUP`, so an existing
#   single-tier install keeps working (its one admin group becomes the system
#   tier).
# * COLLECTION admins administer ONLY the reports in a resource collection whose
#   ``admin_group`` names a group they belong to. They cannot create collections,
#   edit the mapping, or move a report to a different collection (system-only).
#
# All three helpers are pure name-match (unit-testable offline); the ``me()`` I/O
# lives in ``main.py`` (LOCKED DECISION L1).

# Default app-wide ("system") admin group; overridable via SYSTEM_ADMIN_GROUP.
SYSTEM_ADMIN_GROUP: str = ADMIN_GROUP


def is_system_admin(me_user: Any, system_admin_group: str = SYSTEM_ADMIN_GROUP) -> bool:
    """Return whether the user is an app-wide (system) administrator.

    Args:
        me_user: The ``User`` object from ``current_user.me()``.
        system_admin_group: The system-admin group display name (env-configurable).

    Returns:
        ``True`` if the user is a member of ``system_admin_group``.
    """
    return is_member(me_user, system_admin_group)


def collection_admin_group(view: Any) -> str:
    """Return the admin group that administers a resource collection (or "").

    Args:
        view: A ``ReportView``-like object exposing an optional ``admin_group``.

    Returns:
        The collection's ``admin_group`` (stripped); ``""`` if unset — meaning no
        delegated admins, so only system admins may edit that collection.
    """
    return (getattr(view, "admin_group", None) or "").strip()


def is_collection_admin(me_user: Any, view: Any) -> bool:
    """Return whether the user is a delegated admin of one resource collection.

    This is the collection-scoped tier ONLY; it does NOT count system admins
    (check :func:`is_system_admin` separately). ``True`` iff the collection has a
    non-empty ``admin_group`` and the user belongs to it.

    Args:
        me_user: The ``User`` object from ``current_user.me()``.
        view: The ``ReportView``-like collection (its ``admin_group`` is read).

    Returns:
        ``True`` if the user administers this specific collection.
    """
    group = collection_admin_group(view)
    return bool(group) and is_member(me_user, group)


def can_admin_any(
    me_user: Any, views: Any, system_admin_group: str = SYSTEM_ADMIN_GROUP
) -> bool:
    """Return whether the user can administer at least one collection.

    ``True`` for system admins (all collections) or for a delegated admin of any
    collection in ``views``. Used to decide whether to surface the Admin nav link.

    Args:
        me_user: The ``User`` object from ``current_user.me()``.
        views: An iterable of ``ReportView``-like collections (may be empty/None).
        system_admin_group: The system-admin group display name.

    Returns:
        ``True`` if the user administers the app or any collection.
    """
    if is_system_admin(me_user, system_admin_group):
        return True
    return any(is_collection_admin(me_user, v) for v in (views or ()))


def can_view_report(
    me_user: Any,
    report: "ReportConfig",
    system_admin_group: str = SYSTEM_ADMIN_GROUP,
) -> bool:
    """Return whether the user may ACCESS a report — group member OR system admin.

    This single predicate governs BOTH viewing and downloading: every user who
    can access a report may also download it (the separate download group / the
    ``_dl`` naming convention have been removed). A system administrator can
    always access every report (and therefore every resource collection); data
    reads still run OBO, so a source they cannot read shows the usual access
    notice. The global download kill switch (``config.downloads_enabled``) is
    layered on top by the caller in ``main.py``. Pure name-match; the ``me()``
    I/O lives in ``main.py``.

    Args:
        me_user: The ``User`` object from ``current_user.me()``.
        report: The report config.
        system_admin_group: The system-admin group display name (env-configurable).

    Returns:
        ``True`` if the user is a system admin OR a member of the report's access
        group.
    """
    return is_system_admin(me_user, system_admin_group) or can_view(me_user, report)


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
            :data:`DEFAULT_VIEW_GROUP`).

    Returns:
        ``True`` if ``group_display`` is among the user's group display names,
        else ``False``.
    """
    return group_display in group_display_names(me_user)
