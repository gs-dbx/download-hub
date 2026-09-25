"""Pure diagnostics helpers for blind-deploy debugging (L8).

Stdlib-only — no SDK, fastapi, or network import — so this module is unit-testable
in the pytest-only dev ``.venv``. The I/O boundary (``main.py``) runs the live
probes (service-principal identity, warehouse state, config-table read, export
volume) and feeds their raw results / exception text through these builders to
produce the structured report the ``/health/diag`` JSON endpoint and the ``/_diag``
HTML page render, plus the banner printed to stdout at startup.

Why this exists: some deployment targets do not surface app logs, so a failed
deploy shows only a bare "Internal Server Error" with no cause. These helpers let
the app explain itself in the browser instead. Everything here is safe to expose:
secret env values are masked to a presence + length summary, and no builder
raises (a probe's exception text is passed in as a string), so one broken check
never hides the rest.
"""

from __future__ import annotations

import platform
import sys
import traceback

# Env vars surfaced on the diagnostics page. ``is_secret`` => the value is masked
# (only presence + length shown, never the content). Tuple order is display order.
REPORTED_ENV: tuple[tuple[str, bool], ...] = (
    ("APP_VERSION", False),
    ("DATABRICKS_HOST", False),
    ("DATABRICKS_WAREHOUSE_ID", False),
    ("APP_CATALOG", False),
    ("APP_SCHEMA", False),
    ("APP_EXPORT_VOLUME", False),
    ("DOWNLOADS_ENABLED", False),
    ("ADMIN_GROUP", False),
    ("SYSTEM_ADMIN_GROUP", False),
    ("APP_NAME", False),
    ("MAX_DOWNLOAD_ROWS", False),
    ("MAX_XLSX_ROWS", False),
    ("MAX_SPILL_ROWS", False),
    ("EXPORT_PAGE_ROWS", False),
    ("DATABRICKS_CLIENT_ID", True),
    ("DATABRICKS_CLIENT_SECRET", True),
    ("DATABRICKS_TOKEN", True),
)

# Distribution names whose installed version is worth reporting (import failures
# in these are a common cause of a boot-time 500 in a fresh environment).
REPORTED_PACKAGES: tuple[str, ...] = (
    "databricks-sdk",
    "fastapi",
    "starlette",
    "uvicorn",
    "jinja2",
    "openpyxl",
    "python-multipart",
)

# Single-check status values.
OK = "ok"
WARN = "warn"
FAIL = "fail"


def mask_secret(value: str | None) -> str:
    """Summarize a secret value as presence + length — never its content.

    Args:
        value: The raw env value, or ``None`` if unset.

    Returns:
        ``"(unset)"``, ``"(set: empty)"``, or ``"(set: N chars)"``.
    """
    if value is None:
        return "(unset)"
    n = len(value)
    if n == 0:
        return "(set: empty)"
    return f"(set: {n} chars)"


def env_report(
    environ: dict[str, str], spec: tuple[tuple[str, bool], ...] = REPORTED_ENV
) -> list[dict]:
    """Build the env table: one row per reported var (secret values masked).

    Args:
        environ: The process environment (a plain mapping).
        spec: ``(name, is_secret)`` pairs to report, in display order.

    Returns:
        A list of ``{"name", "present", "display", "secret"}`` dicts. ``present``
        is ``False`` for an unset OR empty value; ``display`` is the masked
        summary for secrets and the literal value (or ``"(unset)"``) otherwise.
    """
    rows: list[dict] = []
    for name, is_secret in spec:
        raw = environ.get(name)
        present = raw is not None and raw != ""
        if is_secret:
            display = mask_secret(raw)
        else:
            display = raw if present else "(unset)"
        rows.append(
            {"name": name, "present": present, "display": display, "secret": is_secret}
        )
    return rows


def make_check(name: str, ok: bool, detail: str = "", *, warn: bool = False) -> dict:
    """Build a single check-result dict.

    Args:
        name: Short check identifier (e.g. ``"warehouse"``).
        ok: Whether the check passed.
        detail: A one-line human explanation (safe to display).
        warn: When ``ok`` is ``False``, mark it a soft ``warn`` (not a hard fail).

    Returns:
        ``{"name", "status", "ok", "detail"}`` where ``status`` is one of
        :data:`OK` / :data:`WARN` / :data:`FAIL`.
    """
    status = OK if ok else (WARN if warn else FAIL)
    return {"name": name, "status": status, "ok": ok, "detail": detail}


def check_from_exc(name: str, exc_text: str | None, ok_detail: str = "OK") -> dict:
    """Build a check from optional exception text: empty/absent => passed.

    Args:
        name: Short check identifier.
        exc_text: The captured exception text, or ``None``/``""`` if the probe
            succeeded.
        ok_detail: Detail to show when the probe succeeded.

    Returns:
        A check dict (failed, with ``exc_text`` as the detail, when text is given).
    """
    text = (exc_text or "").strip()
    if not text:
        return make_check(name, True, ok_detail)
    return make_check(name, False, text)


def summarize(checks: list[dict]) -> dict:
    """Roll up check statuses into overall counts + a single ``ok`` flag.

    Args:
        checks: The list of check dicts from :func:`make_check`.

    Returns:
        ``{"ok", "total", "passed", "warned", "failed"}``. ``ok`` is ``True`` only
        when there are zero hard failures (warnings do not fail the rollup).
    """
    passed = sum(1 for c in checks if c.get("status") == OK)
    warned = sum(1 for c in checks if c.get("status") == WARN)
    failed = sum(1 for c in checks if c.get("status") == FAIL)
    return {
        "ok": failed == 0,
        "total": len(checks),
        "passed": passed,
        "warned": warned,
        "failed": failed,
    }


def runtime_info() -> dict:
    """Return interpreter + platform facts (no network, never raises)."""
    return {
        "python": sys.version.split()[0],
        "python_full": " ".join(sys.version.split()),
        "platform": platform.platform(),
        "executable": sys.executable,
    }


def package_versions(names: tuple[str, ...] = REPORTED_PACKAGES) -> list[dict]:
    """Return the installed version for each distribution name (best-effort).

    Args:
        names: Distribution names to look up.

    Returns:
        A list of ``{"name", "version"}`` dicts; ``version`` is ``"not installed"``
        when the distribution is absent and ``"unknown"`` if metadata is
        unavailable — this never raises, so a missing package can't hide the rest.
    """
    try:
        from importlib import metadata as _md
    except Exception:  # pragma: no cover - importlib.metadata always present on 3.8+
        return [{"name": n, "version": "unknown"} for n in names]
    out: list[dict] = []
    for n in names:
        try:
            out.append({"name": n, "version": _md.version(n)})
        except Exception:  # noqa: BLE001 - PackageNotFoundError and anything else
            out.append({"name": n, "version": "not installed"})
    return out


def format_traceback(exc: BaseException, limit: int = 40) -> str:
    """Return a formatted traceback string for an exception (bounded frames)."""
    return "".join(
        traceback.format_exception(type(exc), exc, exc.__traceback__, limit=limit)
    )


def exc_summary(exc: BaseException) -> str:
    """Return a one-line ``Type: message`` summary of an exception."""
    msg = " ".join(str(exc).split())
    return f"{type(exc).__name__}: {msg}" if msg else type(exc).__name__


def missing_required(environ: dict[str, str], required: tuple[str, ...]) -> list[str]:
    """Return the required env names that are unset or empty (in order)."""
    return [n for n in required if not (environ.get(n) or "").strip()]


def boot_banner(summary: dict, checks: list[dict], version: str) -> str:
    """Build a compact multi-line startup banner for stdout.

    Useful in environments that DO surface logs; the same data is on ``/_diag``
    for environments that do not.

    Args:
        summary: The :func:`summarize` rollup.
        checks: The list of check dicts.
        version: The app version string.

    Returns:
        A newline-joined banner, one line per check.
    """
    marks = {OK: "OK  ", WARN: "WARN", FAIL: "FAIL"}
    lines = [
        f"[download-hub] startup diagnostics — v{version} — "
        f"{summary['passed']}/{summary['total']} ok, "
        f"{summary['failed']} failed, {summary['warned']} warn"
    ]
    for c in checks:
        mark = marks.get(c.get("status", ""), "?   ")
        detail = f" — {c['detail']}" if c.get("detail") else ""
        lines.append(f"[download-hub]   [{mark}] {c['name']}{detail}")
    return "\n".join(lines)
