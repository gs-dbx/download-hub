"""Unit tests for the pure OBO token extraction in ``app.auth``.

No fastapi, no databricks.sdk, no network (LOCKED L3).
"""

from types import SimpleNamespace

import pytest

from app.auth import (
    DEFAULT_VIEW_GROUP,
    USER_TOKEN_HEADER,
    can_admin_any,
    can_view,
    can_view_report,
    collection_admin_group,
    effective_view_group,
    extract_user_email,
    extract_user_token,
    group_display_names,
    is_admin,
    is_collection_admin,
    is_member,
    is_system_admin,
    parse_scim_user_id,
)


def test_parse_scim_user_id_matches_raw_forwarded_id():
    """A numeric <user_id>@<workspace_id> yields the leading user_id."""
    assert parse_scim_user_id("1234567890@9876543210") == "1234567890"


def test_parse_scim_user_id_passthrough_for_emails_and_blank():
    """A real email/name (or blank) is not a SCIM id — returns None."""
    for v in ("alice@example.gov", "Alice Smith", "", "12345", "12345@abc", "@123"):
        assert parse_scim_user_id(v) is None, v


def _report(**kw):
    """A minimal report-like object (only the attrs auth reads)."""
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


def test_default_view_group_constant():
    """DEFAULT_VIEW_GROUP is the generic default access-group display name."""
    assert DEFAULT_VIEW_GROUP == "download_hub_download_users"


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
            SimpleNamespace(display=DEFAULT_VIEW_GROUP),
        ]
    )
    assert is_member(user, DEFAULT_VIEW_GROUP) is True


def test_is_member_false_when_not_in_groups():
    """is_member is False when the group is not among the user's groups."""
    user = SimpleNamespace(groups=[SimpleNamespace(display="users")])
    assert is_member(user, DEFAULT_VIEW_GROUP) is False


def test_is_member_false_empty_groups():
    """is_member is False for a user with no groups."""
    assert is_member(SimpleNamespace(groups=[]), DEFAULT_VIEW_GROUP) is False


def test_effective_view_group():
    """The access group is the view_key, falling back to DEFAULT_VIEW_GROUP."""
    assert effective_view_group(_report(view_key="efile_ops")) == "efile_ops"
    assert effective_view_group(_report(view_key=None)) == DEFAULT_VIEW_GROUP


def test_can_view_via_view_group():
    """A member of the access group can see (and download) the report."""
    r = _report(view_key="efile_ops")
    assert can_view(_user("efile_ops"), r) is True


def test_can_view_no_separate_download_tier():
    """There is no separate download group: only the access group grants access."""
    r = _report(view_key="efile_ops")
    # The old derived "<view_key>_dl" group no longer grants anything.
    assert can_view(_user("efile_ops_dl"), r) is False


def test_can_view_denied_when_not_in_access_group():
    """A user outside the access group cannot see the report."""
    r = _report(view_key="efile_ops")
    assert can_view(_user("some_other_group"), r) is False


def test_is_admin():
    """is_admin checks membership of the given admin group."""
    assert is_admin(_user("download_hub_admin_users")) is True
    assert is_admin(_user("nope")) is False
    assert is_admin(_user("custom_admins"), "custom_admins") is True


def _view(view_key="ops", admin_group=None):
    """A minimal ReportView-like object (only attrs auth reads)."""
    return SimpleNamespace(view_key=view_key, admin_group=admin_group)


def test_is_system_admin():
    """is_system_admin checks membership of the (env-configurable) system group."""
    assert is_system_admin(_user("sys_admins"), "sys_admins") is True
    assert is_system_admin(_user("other"), "sys_admins") is False
    # Defaults to ADMIN_GROUP for backward compatibility.
    assert is_system_admin(_user("download_hub_admin_users")) is True


def test_collection_admin_group_strips_and_defaults_empty():
    """collection_admin_group returns the stripped group or '' when unset."""
    assert collection_admin_group(_view(admin_group="  ops_admins ")) == "ops_admins"
    assert collection_admin_group(_view(admin_group=None)) == ""
    assert collection_admin_group(_view(admin_group="")) == ""
    assert collection_admin_group(SimpleNamespace()) == ""  # no attr -> ""


def test_is_collection_admin_requires_group_and_membership():
    """A delegated admin must belong to the collection's admin_group."""
    v = _view(admin_group="ops_admins")
    assert is_collection_admin(_user("ops_admins"), v) is True
    assert is_collection_admin(_user("other"), v) is False
    # A collection with no admin_group has no delegated admins.
    assert is_collection_admin(_user("ops_admins"), _view(admin_group=None)) is False


def test_can_admin_any_system_or_any_collection():
    """can_admin_any is True for a system admin OR any collection admin."""
    views = [_view("ops", "ops_admins"), _view("fin", "fin_admins")]
    # System admin: True regardless of collection membership.
    assert can_admin_any(_user("sys"), views, "sys") is True
    # Delegated admin of one collection: True.
    assert can_admin_any(_user("fin_admins"), views, "sys") is True
    # Member of no admin group: False.
    assert can_admin_any(_user("plain_user"), views, "sys") is False
    # No collections and not system: False (empty/None tolerated).
    assert can_admin_any(_user("x"), [], "sys") is False
    assert can_admin_any(_user("x"), None, "sys") is False


def test_can_view_report_governs_download_for_access_member():
    """Any member of the access group may download (download == access now)."""
    r = _report(view_key="efile_ops")
    assert can_view_report(_user("efile_ops"), r, system_admin_group="sys") is True


def test_can_view_report_denies_download_for_non_member():
    """A user outside the access group and not a system admin cannot download."""
    r = _report(view_key="efile_ops")
    assert can_view_report(_user("other"), r, system_admin_group="sys") is False


def test_can_view_report_system_admin_always_allowed():
    """A system admin may access/download every report regardless of its group."""
    r = _report(view_key="efile_ops")
    # Only in the system-admin group, not the report's access group.
    assert can_view_report(_user("sys"), r, system_admin_group="sys") is True


def test_can_view_report_group_member():
    """A member of the report's access group can see it."""
    r = _report(view_key="efile_ops")
    assert can_view_report(_user("efile_ops"), r, system_admin_group="sys") is True


def test_can_view_report_system_admin_sees_any_collection():
    """A system admin sees every report even without its access group."""
    r = _report(view_key="new_collection")
    assert can_view_report(_user("sys"), r, system_admin_group="sys") is True


def test_can_view_report_non_member_non_admin_denied():
    """A non-member who is not a system admin cannot see the report."""
    r = _report(view_key="new_collection")
    assert can_view_report(_user("other"), r, system_admin_group="sys") is False


def test_group_display_names_tolerates_missing_attrs():
    """group_display_names tolerates a missing .groups and missing .display."""
    # No .groups attribute at all.
    assert group_display_names(SimpleNamespace()) == []
    # A group entry with no .display is skipped.
    user = SimpleNamespace(
        groups=[SimpleNamespace(), SimpleNamespace(display="ok")]
    )
    assert group_display_names(user) == ["ok"]
