"""Unit tests for the pure OBO token extraction in ``app.auth``.

No fastapi, no databricks.sdk, no network (LOCKED L3).
"""

from types import SimpleNamespace

import pytest

from app.auth import (
    DEFAULT_DOWNLOAD_SUFFIX,
    DOWNLOAD_GROUP,
    USER_TOKEN_HEADER,
    can_view,
    derive_download_group,
    effective_download_group,
    effective_view_group,
    extract_user_email,
    extract_user_token,
    group_display_names,
    is_admin,
    is_member,
)


def _report(**kw):
    """A minimal report-like object (only the attrs auth reads)."""
    kw.setdefault("download_group", None)
    kw.setdefault("view_key", None)
    return SimpleNamespace(**kw)


def _user(*group_names):
    """A SCIM me()-like user with the given group display names."""
    return SimpleNamespace(groups=[SimpleNamespace(display=g) for g in group_names])


def test_extract_user_token_present_lowercase():
    """Lowercase header (as Starlette normalizes) yields the token."""
    headers = {USER_TOKEN_HEADER: "tok-abc123"}
    assert extract_user_token(headers) == "tok-abc123"


def test_extract_user_token_present_any_case():
    """A differently-cased header key is matched case-insensitively."""
    headers = {"X-Forwarded-Access-Token": "tok-XYZ"}
    assert extract_user_token(headers) == "tok-XYZ"


def test_extract_user_token_absent_raises():
    """Absent header raises PermissionError (no fallback, LOCKED L3)."""
    with pytest.raises(PermissionError):
        extract_user_token({})


def test_extract_user_token_empty_raises():
    """Empty header value is treated as absent and raises PermissionError."""
    with pytest.raises(PermissionError):
        extract_user_token({USER_TOKEN_HEADER: ""})


def test_download_group_constant():
    """DOWNLOAD_GROUP is the generic default group display name."""
    assert DOWNLOAD_GROUP == "download_hub_download_users"


def test_extract_user_email_present():
    """x-forwarded-user is read case-insensitively."""
    assert extract_user_email({"X-Forwarded-User": "a@b.c"}) == "a@b.c"


def test_extract_user_email_absent_returns_empty():
    """Absent email header yields '' (best-effort, not an auth gate)."""
    assert extract_user_email({}) == ""


def test_is_member_true():
    """is_member is True when a group's .display matches."""
    user = SimpleNamespace(
        groups=[
            SimpleNamespace(display="users"),
            SimpleNamespace(display=DOWNLOAD_GROUP),
        ]
    )
    assert is_member(user, DOWNLOAD_GROUP) is True


def test_is_member_false_when_not_in_groups():
    """is_member is False when the group is not among the user's groups."""
    user = SimpleNamespace(groups=[SimpleNamespace(display="users")])
    assert is_member(user, DOWNLOAD_GROUP) is False


def test_is_member_false_empty_groups():
    """is_member is False for a user with no groups."""
    assert is_member(SimpleNamespace(groups=[]), DOWNLOAD_GROUP) is False


def test_effective_download_group_uses_report_group_when_set():
    """An explicit download_group wins (stripped), regardless of view_key."""
    assert effective_download_group(_report(download_group="grp_x", view_key="v")) == "grp_x"
    assert effective_download_group(_report(download_group="  grp_y  ")) == "grp_y"


def test_effective_download_group_derives_from_view_key():
    """No explicit group -> derive <view_key> + suffix (naming convention)."""
    assert (
        effective_download_group(_report(view_key="efile_ops"))
        == "efile_ops" + DEFAULT_DOWNLOAD_SUFFIX
    )
    assert effective_download_group(_report(view_key="ops"), "_download") == "ops_download"


def test_effective_download_group_no_view_no_explicit_uses_default_group():
    """No explicit group AND no view_key -> derive from the DOWNLOAD_GROUP fallback."""
    assert (
        effective_download_group(_report())
        == DOWNLOAD_GROUP + DEFAULT_DOWNLOAD_SUFFIX
    )


def test_effective_view_group():
    """The view group is the view_key, falling back to DOWNLOAD_GROUP when unset."""
    assert effective_view_group(_report(view_key="efile_ops")) == "efile_ops"
    assert effective_view_group(_report(view_key=None)) == DOWNLOAD_GROUP


def test_derive_download_group():
    """derive_download_group appends the suffix."""
    assert derive_download_group("efile_ops") == "efile_ops" + DEFAULT_DOWNLOAD_SUFFIX
    assert derive_download_group("x", "_rw") == "x_rw"


def test_can_view_via_view_group():
    """A member of the view group can see the report."""
    r = _report(view_key="efile_ops")
    assert can_view(_user("efile_ops"), r) is True


def test_can_view_via_download_group():
    """A member of only the (derived) download group can still see the report."""
    r = _report(view_key="efile_ops")  # download group -> efile_ops_dl
    assert can_view(_user("efile_ops_dl"), r) is True


def test_can_view_denied_when_in_neither():
    """A user in neither the view nor download group cannot see the report."""
    r = _report(view_key="efile_ops")
    assert can_view(_user("some_other_group"), r) is False


def test_is_admin():
    """is_admin checks membership of the given admin group."""
    assert is_admin(_user("download_hub_admin_users")) is True
    assert is_admin(_user("nope")) is False
    assert is_admin(_user("custom_admins"), "custom_admins") is True


def test_group_display_names_tolerates_missing_attrs():
    """group_display_names tolerates a missing .groups and missing .display."""
    # No .groups attribute at all.
    assert group_display_names(SimpleNamespace()) == []
    # A group entry with no .display is skipped.
    user = SimpleNamespace(
        groups=[SimpleNamespace(), SimpleNamespace(display="ok")]
    )
    assert group_display_names(user) == ["ok"]
