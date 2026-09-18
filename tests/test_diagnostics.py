"""Unit tests for the pure diagnostics helpers (no SDK required)."""

from __future__ import annotations

try:
    from app.diagnostics import (
        FAIL,
        OK,
        WARN,
        boot_banner,
        check_from_exc,
        env_report,
        exc_summary,
        format_traceback,
        make_check,
        mask_secret,
        missing_required,
        package_versions,
        runtime_info,
        summarize,
    )
except ImportError:  # pragma: no cover - path shim (mirrors other tests)
    from diagnostics import (  # type: ignore
        FAIL,
        OK,
        WARN,
        boot_banner,
        check_from_exc,
        env_report,
        exc_summary,
        format_traceback,
        make_check,
        mask_secret,
        missing_required,
        package_versions,
        runtime_info,
        summarize,
    )


def test_mask_secret_never_reveals_value():
    assert mask_secret(None) == "(unset)"
    assert mask_secret("") == "(set: empty)"
    assert mask_secret("supersecret") == "(set: 11 chars)"
    # The actual secret text is never in the masked output.
    assert "supersecret" not in mask_secret("supersecret")


def test_env_report_masks_secrets_and_marks_presence():
    env = {"DATABRICKS_HOST": "https://x", "DATABRICKS_CLIENT_SECRET": "abc123"}
    spec = (("DATABRICKS_HOST", False), ("DATABRICKS_CLIENT_SECRET", True),
            ("APP_CATALOG", False))
    rows = {r["name"]: r for r in env_report(env, spec)}
    assert rows["DATABRICKS_HOST"]["display"] == "https://x"
    assert rows["DATABRICKS_HOST"]["present"] is True
    # Secret shows only presence + length, not the value.
    assert rows["DATABRICKS_CLIENT_SECRET"]["display"] == "(set: 6 chars)"
    assert "abc123" not in rows["DATABRICKS_CLIENT_SECRET"]["display"]
    # Unset var is reported as absent with a placeholder display.
    assert rows["APP_CATALOG"]["present"] is False
    assert rows["APP_CATALOG"]["display"] == "(unset)"


def test_env_report_empty_string_is_not_present():
    rows = {r["name"]: r for r in env_report({"APP_CATALOG": ""}, (("APP_CATALOG", False),))}
    assert rows["APP_CATALOG"]["present"] is False


def test_make_check_status_mapping():
    assert make_check("a", True, "fine")["status"] == OK
    assert make_check("b", False, "broke")["status"] == FAIL
    assert make_check("c", False, "soft", warn=True)["status"] == WARN


def test_check_from_exc_passes_when_no_text():
    assert check_from_exc("x", None)["ok"] is True
    assert check_from_exc("x", "   ")["ok"] is True
    bad = check_from_exc("x", "boom")
    assert bad["ok"] is False and bad["detail"] == "boom"


def test_summarize_counts_and_ok_flag():
    checks = [
        make_check("a", True),
        make_check("b", False, warn=True),
        make_check("c", False),
    ]
    s = summarize(checks)
    assert s == {"ok": False, "total": 3, "passed": 1, "warned": 1, "failed": 1}
    # Warnings alone do not fail the rollup.
    ok = summarize([make_check("a", True), make_check("b", False, warn=True)])
    assert ok["ok"] is True and ok["failed"] == 0


def test_missing_required_returns_unset_or_empty_in_order():
    env = {"A": "set", "B": "  ", "C": ""}
    assert missing_required(env, ("A", "B", "C", "D")) == ["B", "C", "D"]


def test_runtime_info_has_python_and_platform():
    info = runtime_info()
    assert info["python"] and "." in info["python"]
    assert "platform" in info and info["platform"]


def test_package_versions_reports_missing_gracefully():
    out = {p["name"]: p["version"] for p in package_versions(("definitely-not-a-real-dist-xyz",))}
    assert out["definitely-not-a-real-dist-xyz"] == "not installed"


def test_format_traceback_and_exc_summary():
    try:
        raise ValueError("bad value")
    except ValueError as exc:
        tb = format_traceback(exc)
        assert "ValueError" in tb and "bad value" in tb
        assert exc_summary(exc) == "ValueError: bad value"


def test_boot_banner_lists_each_check():
    checks = [make_check("boot", True, "ok"), make_check("warehouse", False, "stopped")]
    banner = boot_banner(summarize(checks), checks, "0.9.0")
    assert "v0.9.0" in banner
    assert "boot" in banner and "warehouse" in banner
    assert "[FAIL]" in banner and "[OK" in banner
