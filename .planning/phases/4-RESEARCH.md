# Phase 4 Research — Gated Download (acknowledgement, justification, audit, CSV/Excel)

**Date:** 2026-08-13
**Phase:** 4 — download panel gated on group membership; first WRITE path (audit); CSV/XLSX export
**Domain:** Databricks App (server-rendered FastAPI + Jinja2) — extends the ALREADY-DEPLOYED `download-hub` app. Adds OBO group-membership check, SP-authored audit INSERT, and file downloads.
**MCP Available:** databricks-v2 present but NOT usable (browser OAuth). Used **CLI (profile DEFAULT)** + Statement Execution API for all live checks.
**CLI Available:** yes — CLI v0.299.2, all live scans SUCCEEDED.
**Auth:** CLI profile DEFAULT valid (greg.skinner@databricks.com).

---

## Current App Code (what exists to extend)

All under `src/app/`. The app runs from `src/app/` with FLAT imports (`from queries import ...`); tests import via a `sys.path` shim and run offline (dev `.venv` has ONLY `pytest` — no fastapi/jinja2/starlette/databricks-sdk/openpyxl). Any new module the tests import must therefore be **pure stdlib** at import time.

### `main.py` — the sole I/O boundary (reuse; add `/download` POST + a `me()` check)
- `_user_client(token)` builds a per-request `WorkspaceClient(config=Config(host=os.environ["DATABRICKS_HOST"], token=token, auth_type="pat"))`. The `auth_type="pat"` pin stops the SDK tripping "more than one authorization method configured: oauth and pat" when the Apps runtime injects the SP's OAuth env. **Reuse verbatim for the OBO read AND the `me()` group check.**
- `_run_sql(token, sql, parameters=None) -> (columns, data)` wraps `statement_execution.execute_statement(warehouse_id, statement, parameters, wait_timeout="30s")` in `asyncio.to_thread`, checks `StatementState.SUCCEEDED`, returns column names + `data_array`. Already forwards `parameters` (added Phase 3). **Reuse for the audit INSERT and the re-query.**
- Imports already present: `StatementParameterListItem, StatementState` from `databricks.sdk.service.sql`; `WorkspaceClient`; `Config`; `HTTPException`; `HTMLResponse`.
- `_env(name, default=None)` reads env (`EFILE_CATALOG`, `EFILE_SCHEMA`, `DATABRICKS_WAREHOUSE_ID`, `APP_VERSION`).
- Routes: `/health`, `/` (glance page), `/table` (parameterized `_rows.html` fragment). `error.html` used for 401/403.
- `DRAIN_OPTIONS = ["ALL", "E", "M", "N"]`.

### `auth.py` — `extract_user_token(headers) -> str` (reuse; ADD `extract_user_email`)
- Reads `x-forwarded-access-token` case-insensitively via `_get_case_insensitive`; raises `PermissionError` if absent. Pure, `from typing import Any` only — no starlette import (so it's testable in the pytest-only venv). **Add `extract_user_email(headers)` reading `x-forwarded-user`, reusing `_get_case_insensitive`.**

### `queries.py` — pure SQL builders (reuse; the audit INSERT builder goes in NEW `audit.py`)
- `VALID_DRAINS = ("E","M","N","ALL")`, `_fqn(catalog, schema)`, `build_glance_query`, `build_glance_query_for_date` (parameterized `:report_date`/`:drain`), `validate_drain`, `validate_report_date(report_date, allowed)`, `build_report_dates_query`. **Reuse the parameterized read builder for the re-query in `/download`.**

### `shaping.py` — pure formatting (reuse UNCHANGED)
- `rows_to_context(columns, data_array)` → ordered dicts with `metric_name`, `metric_group`, `sort_order`, `value_cy`, `value_py`, `value_cy_fmt`, `value_py_fmt`, `pct_change`, `pct_fmt` (NULL pct → `EM_DASH` `—`). `format_report_date` → `"%Y-%m-%d %H:%M:%S"`. `format_pct`, `format_count`, `METRIC_ORDER`, `EM_DASH`. **The export builds directly off `rows_to_context` output** (same dicts the template uses), so CSV/XLSX rows match the on-screen table exactly.

### `templates/`
- `base.html` — USWDS head/banner/footer; already references `/static/js/app.js`. Content block: `{% block content %}`. **Add the download panel inside `glance.html`'s content block.**
- `glance.html` — search input, report-date `<select id="glance-report-date">`, drain `<select id="glance-drain">`, table with `<tbody id="glance-tbody">{% include "_rows.html" %}`. Controls carry `selected_report_date` / `selected_drain`. **Add a conditional `{% if can_download %}` download panel** (acknowledgement checkbox + justification textarea + format selector + hidden `report_date`/`drain`/`search`).
- `_rows.html` — the `<tr>` loop; `error.html` — 401/403 page.

### `resources/grants.sql` — Phase-1 SELECT grants to `efile_glance_app_users` (extend this phase)
- Currently 3 statements granting USE CATALOG / USE SCHEMA / SELECT-on-daily_efile_glance to `efile_glance_app_users`. Header notes the group did not exist and the SP grant was deferred to Phase 4. **Extend with the app-SP audit grants (below) and apply the app-users SELECT now that groups get created.**

### `requirements.txt` — `fastapi, uvicorn, jinja2, databricks-sdk`
- **Add `openpyxl` (XLSX) and `python-multipart` (form parsing — see FastAPI Form note).**

---

## Live Findings (verified 2026-08-13 via CLI, profile DEFAULT)

### Admin status — greg.skinner IS a workspace admin
`databricks current-user me -p DEFAULT` returns `groups` including `{"display":"admins","type":"indirect"}` (plus `users` direct, `irs-sa` direct). Membership in `admins` (even indirect) = workspace admin. **→ The executor CAN create groups and apply UC grants. Group-creation is not blocked.**

Also confirms **`current_user.me()` returns `.groups` populated** (this CLI hits the SCIM `/Me` endpoint the SDK's `me()` wraps) — each entry has `display`, `type` (`direct`/`indirect`), `value`, `$ref`.

### App service-principal identity (from `databricks apps get download-hub -p DEFAULT`)
The app is RUNNING (deployed 2026-08-13, MEDIUM compute) at `https://download-hub-2460574726701099.aws-gov.databricksapps.us`.

| Field | Value |
|---|---|
| `service_principal_client_id` (**appId — the runtime identity / injected `DATABRICKS_CLIENT_ID`**) | **`97898a88-5dfd-4c75-bd0b-a6279a13ea08`** |
| `service_principal_name` | `app-2oca4n download-hub` |
| `service_principal_id` (numeric) | `78643680913034` |
| `oauth2_app_client_id` / `oauth2_app_integration_id` | `c013d181-95b0-410c-ab95-d64226e911d6` (OAuth *integration*, NOT the run identity) |
| `effective_user_api_scopes` | `["iam.access-control:read", "iam.current-user:read", "sql"]` |
| Bound warehouse resource | `2f225c0740dcd22b` (CAN_USE) |

**The audit INSERT runs as `service_principal_client_id = 97898a88-5dfd-4c75-bd0b-a6279a13ea08`.** That is the appId injected as `DATABRICKS_CLIENT_ID`, and it is the principal the UC GRANTs must target — **NOT** the numeric id, **NOT** the display name, **NOT** `oauth2_app_client_id`. The `iam.current-user:read` scope is already effective → the OBO `me()` group check is supported without adding scopes.

### Groups CLI (v0.299.2) — subcommands available
`databricks groups`: **`create`, `delete`, `get`, `list`, `patch`, `update`**.

Existing workspace groups (live): `Metastore Admins`, `admins`, `irs-sa`, `one-group`, `users`.
**Neither `efile_glance_app_users` NOR `efile_glance_download_users` exists** → both must be created this phase.

Exact (feasible, do-NOT-run-yet) commands:
```bash
# Create the two groups (admin confirmed available)
databricks groups create --display-name efile_glance_app_users      -p DEFAULT
databricks groups create --display-name efile_glance_download_users -p DEFAULT

# Add greg.skinner to the download group so the feature is demoable.
# SCIM PATCH via `groups patch`; needs the group id + the user's numeric id (75113935367499 from current-user me).
databricks groups patch <download_group_id> -p DEFAULT --json '{
  "Operations": [
    {"op": "add", "path": "members", "value": [{"value": "75113935367499"}]}
  ],
  "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"]
}'
```
The group id comes from `databricks groups list -p DEFAULT --output json` (match `displayName`) or the `create` response `id`. greg.skinner's numeric id is **`75113935367499`** (from `current-user me`).

### `irs.efile.download_audit` schema — CONFIRMED (live `DESCRIBE`, 11 columns, empty)
Exact column order (the INSERT must match):

| # | Column | Type |
|---|---|---|
| 1 | audit_id | string |
| 2 | event_ts | timestamp |
| 3 | user_email | string |
| 4 | report_date | timestamp |
| 5 | drain_filter | string |
| 6 | search_filter | string |
| 7 | row_count | bigint |
| 8 | export_format | string |
| 9 | justification | string |
| 10 | acknowledged | boolean |
| 11 | app_version | string |

Table is writable in principle (DESCRIBE succeeded; UC will enforce the SP's MODIFY grant at INSERT time). **Per the task, NO rows were inserted — the table stays empty for the verify checkpoint.**

### Dependency availability
`openpyxl`, `databricks-sdk`, `fastapi`, `jinja2`, `starlette`, `python-multipart` are ALL **absent** from the dev `.venv` (Python 3.14, only `pytest` 9.1.1 installed). Unit tests must import only stdlib + the app's pure modules; the openpyxl-dependent test must `pytest.importorskip("openpyxl")`.

---

## Group-Membership Approach

### Primary (verified path) — OBO `current_user.me().groups`, no admin/SCIM, no SP
The signed-in user's OBO client (`_user_client(token)`, `auth_type="pat"`) calls `me()`; the returned `User` has `.groups: list[ComplexValue]`, each with `.display`. The `iam.current-user:read` scope (already effective) covers `/Me`, and the live CLI proved `/Me` returns groups. **Confidence: HIGH** on `.groups`/`.display` shape (stable across recent SDK versions; class `databricks.sdk.service.iam.ComplexValue`); could not introspect the class locally (SDK not in venv) but the REST `/Me` payload is confirmed live.

Keep the `me()` call in `main.py` (I/O boundary, wrapped in `asyncio.to_thread`) and the pure name-match test in a new helper so it is unit-testable offline:

```python
# auth.py (PURE — add alongside extract_user_token)
DOWNLOAD_GROUP = "efile_glance_download_users"

def group_display_names(me_user: Any) -> list[str]:
    """Return the display names of the groups on a SCIM me() User (empty if none)."""
    groups = getattr(me_user, "groups", None) or []
    return [g.display for g in groups if getattr(g, "display", None)]

def is_member(me_user: Any, group_display: str) -> bool:
    """True if the user belongs to a group whose display name == group_display."""
    return group_display in group_display_names(me_user)
```

```python
# main.py — inside the "/" route (I/O boundary), wrap the sync SDK call:
me_user = await asyncio.to_thread(_user_client(token).current_user.me)
can_download = is_member(me_user, DOWNLOAD_GROUP)
# pass can_download into the glance.html context; the panel renders only if true.
```

`POST /download` **re-checks server-side** (defense in depth — never trust the hidden UI): if `not is_member(...)` → `HTTPException(status_code=403)`.

Note both direct and indirect memberships appear in `me().groups` (Greg's `admins` is `indirect`), so a directly-added download-group membership will be present regardless of type.

### Fallback (only if `me().groups` is empty/unavailable) — app SP → SCIM Groups
Build the SP client (`WorkspaceClient()`, below) and query SCIM:
```python
matches = list(w_sp.groups.list(filter=f'displayName eq "{DOWNLOAD_GROUP}"'))
grp = w_sp.groups.get(id=matches[0].id)          # grp.members: list[ComplexValue]
member = any(m.value == user_scim_id for m in (grp.members or []))
```
**Permission cost:** SCIM Groups read requires the SP to be a **workspace admin** (or hold group-read entitlement). The app SP is NOT admin by default, so this fallback needs an explicit admin grant to the SP — heavier and less desirable. **Prefer the primary path; only wire the fallback if the checkpoint shows `me().groups` empty.**

**Degrade-safe default:** if the group cannot be resolved at all (e.g. group not yet created), `can_download` is `False` → the panel is simply hidden and `/download` returns 403. Never fail open.

---

## Audit Write Approach

### The SP client — plain `WorkspaceClient()` (uses injected SP OAuth)
This is the ONE place the app acts as itself, not the user. The Apps runtime injects `DATABRICKS_HOST` + `DATABRICKS_CLIENT_ID` (`97898a88-…`) + `DATABRICKS_CLIENT_SECRET` (SP OAuth M2M). A plain `WorkspaceClient()` with **no explicit config and no token** auto-detects those and authenticates as the SP. Do NOT pass `auth_type="pat"` here (that pin is only for the user-token client — contrast: the user client passes an explicit `token`; the SP client passes nothing). Construct once (module-level is fine — the SP creds are long-lived, unlike the per-request user token) and wrap calls in `asyncio.to_thread`.

```python
# main.py
_sp_client: WorkspaceClient | None = None
def _app_sp_client() -> WorkspaceClient:
    """WorkspaceClient authenticated as the app service principal (injected OAuth)."""
    global _sp_client
    if _sp_client is None:
        _sp_client = WorkspaceClient()   # picks up DATABRICKS_CLIENT_ID/SECRET/HOST
    return _sp_client
```

### Parameterized INSERT — new PURE module `audit.py`
`build_audit_insert()` returns the SQL string + a `parameters` list, mirroring `queries.py` / the `StatementParameterListItem` style. `audit_id` is a Python `uuid.uuid4()`; `event_ts` is set by `current_timestamp()` in SQL (no bound param — server-side, monotonic). Keep the builder pure (no SDK import): it returns `(sql, list[dict])` where each dict is `{"name","value","type"}`; `main.py` maps those to `StatementParameterListItem(**d)`. That keeps `audit.py` importable in the pytest-only venv.

```python
# audit.py (PURE)
import uuid

def build_audit_row(*, user_email, report_date, drain_filter, search_filter,
                    row_count, export_format, justification, app_version,
                    acknowledged=True, audit_id=None) -> dict:
    return {
        "audit_id": audit_id or str(uuid.uuid4()),
        "user_email": user_email,
        "report_date": report_date,          # "%Y-%m-%d %H:%M:%S"
        "drain_filter": drain_filter,
        "search_filter": search_filter or "",
        "row_count": int(row_count),
        "export_format": export_format,       # "csv" | "xlsx"
        "justification": justification,
        "acknowledged": bool(acknowledged),
        "app_version": app_version,
    }

def build_audit_insert(catalog: str, schema: str, row: dict) -> tuple[str, list[dict]]:
    fqn = f"{catalog}.{schema}.download_audit"
    sql = (
        f"INSERT INTO {fqn} "
        "(audit_id, event_ts, user_email, report_date, drain_filter, search_filter, "
        " row_count, export_format, justification, acknowledged, app_version) "
        "VALUES (:audit_id, current_timestamp(), :user_email, :report_date, :drain_filter, "
        " :search_filter, :row_count, :export_format, :justification, :acknowledged, :app_version)"
    )
    params = [
        {"name": "audit_id",      "value": row["audit_id"],            "type": "STRING"},
        {"name": "user_email",    "value": row["user_email"],          "type": "STRING"},
        {"name": "report_date",   "value": row["report_date"],         "type": "TIMESTAMP"},
        {"name": "drain_filter",  "value": row["drain_filter"],        "type": "STRING"},
        {"name": "search_filter", "value": row["search_filter"],       "type": "STRING"},
        {"name": "row_count",     "value": str(row["row_count"]),      "type": "BIGINT"},
        {"name": "export_format", "value": row["export_format"],       "type": "STRING"},
        {"name": "justification", "value": row["justification"],       "type": "STRING"},
        {"name": "acknowledged",  "value": str(row["acknowledged"]).lower(), "type": "BOOLEAN"},
        {"name": "app_version",   "value": row["app_version"],         "type": "STRING"},
    ]
    return sql, params
```
Notes: every `StatementParameterListItem.value` must be a **string** (hence `str(row_count)` / `"true"`/`"false"`). `report_date` binds as `TIMESTAMP` with the same `"%Y-%m-%d %H:%M:%S"` string proven live in Phase 3. `search_filter` defaults to `""` (never NULL) so the column is always populated.

### Audit-first ordering (NFR-5)
In `POST /download`: validate → re-check group → OBO re-query → build file → **write audit as SP, then** return the file. If the audit INSERT does not reach `SUCCEEDED` → `HTTPException(status_code=500)`; NO un-audited download. Also emit an app-log line (`print(...)` / `logging`) so the event surfaces in `databricks apps logs` (FR-9).

### UC grants the SP needs — target the appId `97898a88-5dfd-4c75-bd0b-a6279a13ea08`
Add to `resources/grants.sql` (apply via CLI at the checkpoint). In UC, a service principal is named in GRANT by its **application ID** (the client_id), backtick-quoted:
```sql
GRANT USE CATALOG ON CATALOG irs                       TO `97898a88-5dfd-4c75-bd0b-a6279a13ea08`;
GRANT USE SCHEMA  ON SCHEMA  irs.efile                 TO `97898a88-5dfd-4c75-bd0b-a6279a13ea08`;
GRANT MODIFY      ON TABLE   irs.efile.download_audit   TO `97898a88-5dfd-4c75-bd0b-a6279a13ea08`;
GRANT SELECT      ON TABLE   irs.efile.download_audit   TO `97898a88-5dfd-4c75-bd0b-a6279a13ea08`;
```
`MODIFY` covers INSERT; `SELECT` lets the checkpoint verify the row landed (and is harmless). The warehouse `CAN_USE` the SP needs is already granted via the app's bound `sql_warehouse` resource. **Confidence HIGH** that the appId string is the correct grantee (UC standard); if a future run ever errors on the principal, the alternative principal form is `service_principal_name` — but appId is the documented and expected form.

Also apply now (groups now exist): the Phase-1 `GRANT ... TO \`efile_glance_app_users\`` SELECT lines.

---

## Exports (CSV / XLSX) + Disclaimer

New PURE module `exports.py` — a module-level `DISCLAIMER` constant (the exact text the checkbox acknowledges), `filename_for(report_date, drain, fmt)`, `to_csv_bytes(rows, disclaimer)`, `to_xlsx_bytes(rows, disclaimer)`. Columns exported (matching the UI): **Metric, 2026 (value_cy), 2025 (value_py), % Change (pct_fmt; `—` for NULL)**. `rows` are the `rows_to_context` dicts.

### CSV — stdlib only (`csv` + `io.StringIO`, encode to bytes)
Disclaimer rides at the TOP as leading comment/quoted single-cell rows before the header:
```python
import csv, io
def to_csv_bytes(rows: list[dict], disclaimer: str) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    for line in disclaimer.splitlines():
        w.writerow([f"# {line}"])        # csv quotes as needed; leading rows before header
    w.writerow([])                        # blank separator row
    w.writerow(["Metric", "2026", "2025", "% Change"])
    for r in rows:
        w.writerow([r["metric_name"], r["value_cy_fmt"], r["value_py_fmt"], r["pct_fmt"]])
    return buf.getvalue().encode("utf-8")
```

### XLSX — `openpyxl` (pure-Python, in-memory `io.BytesIO`)
Import openpyxl **inside the function** (lazy) so `exports.py` imports fine in the pytest-only venv; the XLSX unit test uses `pytest.importorskip("openpyxl")`.
```python
def to_xlsx_bytes(rows: list[dict], disclaimer: str) -> bytes:
    import io
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font
    wb = Workbook()
    ws = wb.active
    ws.title = "Daily E-File at a Glance"
    r = 1
    for line in disclaimer.splitlines():          # disclaimer rows above the table
        ws.cell(row=r, column=1, value=line)
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=4)
        c = ws.cell(row=r, column=1)
        c.alignment = Alignment(wrap_text=True, vertical="top")
        c.font = Font(italic=True)
        r += 1
    r += 1                                          # blank spacer row
    headers = ["Metric", "2026", "2025", "% Change"]
    for col, h in enumerate(headers, start=1):
        hc = ws.cell(row=r, column=col, value=h); hc.font = Font(bold=True)
    r += 1
    for row in rows:
        ws.cell(row=r, column=1, value=row["metric_name"])
        ws.cell(row=r, column=2, value=row["value_cy"])   # numeric cells for CY/PY
        ws.cell(row=r, column=3, value=row["value_py"])
        ws.cell(row=r, column=4, value=row["pct_fmt"])    # keep the "—"/signed string
        r += 1
    bio = io.BytesIO(); wb.save(bio); return bio.getvalue()
```
Notes: for XLSX put the raw ints (`value_cy`/`value_py`) so Excel treats them as numbers; keep `pct_fmt` as the display string (so `—` and the signed `%` match the screen). For CSV the `_fmt` strings are fine (thousands-separated, matching the table). `row_count` for the audit = `len(rows)`.

### Filename
`daily_efile_glance_<report_date>_<drain>.<csv|xlsx>` where `<report_date>` is the date portion sanitized (e.g. `2026-01-12`; strip the `00:00:00` and any spaces/colons). e.g. `daily_efile_glance_2026-01-12_ALL.csv`.

---

## FastAPI Download + Form Handling

### Form parsing needs `python-multipart`
FastAPI/Starlette require the `python-multipart` package to parse form bodies (both `Form(...)` params and `request.form()`). It is NOT currently in `requirements.txt` → **add it** or `POST /download` will 500 at form parse. The form is `application/x-www-form-urlencoded` (normal submit, no file upload).

### The POST route — `Form(...)` params (explicit + typed)
```python
from fastapi import Form
from fastapi.responses import Response

@app.post("/download")
async def download(
    request: Request,
    acknowledged: str = Form(default=""),   # checkbox: present ("on"/"true") only if checked
    justification: str = Form(default=""),
    format: str = Form(default="csv"),      # "csv" | "xlsx"
    report_date: str = Form(...),
    drain: str = Form(...),
    search: str = Form(default=""),
):
    # 1) auth: token = extract_user_token(request.headers); email = extract_user_email(request.headers)
    # 2) group re-check: me_user = await asyncio.to_thread(_user_client(token).current_user.me)
    #    if not is_member(me_user, DOWNLOAD_GROUP): raise HTTPException(403)
    # 3) validate: acknowledged truthy AND justification.strip() != "" else HTTPException(400)
    #    validate_drain(drain); validate_report_date(report_date, allowed_set)
    # 4) OBO re-query (parameterized, AS USER) -> rows_to_context
    # 5) apply the metric-name `search` substring filter server-side (match the on-screen view)
    # 6) build file bytes + media type by `format`
    # 7) audit-first: SP INSERT (build_audit_insert); if not SUCCEEDED -> HTTPException(500)
    # 8) log the event; return the attachment
    ...
    return Response(
        content=file_bytes,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )
```
- **Checkbox semantics:** an unchecked HTML checkbox sends NO field; a checked one sends its `value` (use `value="true"`). Treat `acknowledged` truthy = `acknowledged.strip().lower() in {"true","on","1","yes"}`. Server MUST also confirm `justification.strip()` is non-empty → else 400 (re-render the panel with a message, planner's discretion).
- **Media types:** CSV → `text/csv`; XLSX → `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`.
- `Response(content=bytes, media_type=..., headers={Content-Disposition})` is the simplest correct return for in-memory bytes. (Equivalent: `StreamingResponse(io.BytesIO(bytes), media_type=...)` with the same header — but plain `Response` is cleaner for fully-built small files.)
- Hidden fields `report_date`, `drain`, `search` carry the exact view being exported; they round-trip the same `"%Y-%m-%d %H:%M:%S"` strings the selects use.

### Template panel (glance.html, conditional)
```jinja
{% if can_download %}
<form class="usa-form usa-form--large margin-top-4" method="post" action="/download">
  <input type="hidden" name="report_date" value="{{ selected_report_date }}">
  <input type="hidden" name="drain" value="{{ selected_drain }}">
  <input type="hidden" name="search" value="">   {# JS mirrors the live search box into this #}
  <div class="usa-checkbox">
    <input class="usa-checkbox__input" id="ack" type="checkbox" name="acknowledged" value="true" required>
    <label class="usa-checkbox__label" for="ack">{{ disclaimer }}</label>
  </div>
  <label class="usa-label" for="just">Justification</label>
  <textarea class="usa-textarea" id="just" name="justification" required></textarea>
  <fieldset class="usa-fieldset">
    <label><input type="radio" name="format" value="csv" checked> CSV</label>
    <label><input type="radio" name="format" value="xlsx"> Excel</label>
  </fieldset>
  <button class="usa-button" type="submit">Download</button>
</form>
{% endif %}
```
The hidden `report_date`/`drain` reflect the CURRENTLY-selected view. If Phase-3 JS lets the user change the date/drain client-side, the JS should also update these hidden inputs (and the `search` hidden field) so the export matches the on-screen selection — flag for the planner.

---

## Disclaimer Placement (concrete)
- Define one module constant `DISCLAIMER` in `exports.py` (also passed to the template so the checkbox label == the text embedded in the file — single source of truth).
- **CSV:** each disclaimer line as a leading `# `-prefixed single-cell row (properly quoted by `csv.writer`), then a blank row, then the `Metric,2026,2025,% Change` header. Consumers see the disclaimer at the very top.
- **XLSX:** disclaimer lines occupy the top rows, each merged across columns A–D with `wrap_text=True` + italic, a blank spacer row, then a bold header row, then the data. The acknowledged text visibly rides above the table.

---

## Files to Add / Modify

### Add
| File | Purpose |
|---|---|
| `src/app/exports.py` | PURE: `DISCLAIMER`, `filename_for`, `to_csv_bytes`, `to_xlsx_bytes` (lazy openpyxl import). |
| `src/app/audit.py` | PURE: `build_audit_row`, `build_audit_insert` → (SQL, params) for `download_audit`. |
| `tests/test_exports.py` | CSV/XLSX bytes contain disclaimer + header + a known row; NULL pct → `—`; `pytest.importorskip("openpyxl")` guards the XLSX test. |
| `tests/test_audit.py` | 11-field row, parameterized INSERT (3-level FQN, `:named` placeholders, `current_timestamp()` for event_ts, value types). |

### Modify
| File | Change |
|---|---|
| `src/app/auth.py` | Add `extract_user_email(headers)`, `group_display_names`, `is_member`, `DOWNLOAD_GROUP` (all pure). |
| `src/app/main.py` | `/` route: `me()` check → `can_download` in context. Add `POST /download` (validate → re-check group → OBO re-query → filter → build file → SP audit INSERT audit-first → `Response` attachment). Add `_app_sp_client()`. Import `Form`, `Response`, `extract_user_email`, `is_member`, `DOWNLOAD_GROUP`, `build_audit_insert`, `to_csv_bytes`/`to_xlsx_bytes`/`DISCLAIMER`/`filename_for`. |
| `src/app/templates/glance.html` | Add `{% if can_download %}` download panel (ack checkbox + justification + format + hidden fields). Pass `disclaimer` into context. |
| `src/app/requirements.txt` | Add `openpyxl` and `python-multipart`. |
| `resources/grants.sql` | Add the 4 SP grants to appId `97898a88-5dfd-4c75-bd0b-a6279a13ea08`; apply the app-users SELECT lines (groups now exist). |
| `tests/test_auth.py` | Extend for `extract_user_email`, `is_member`/`group_display_names` (feed a stub object with `.groups[].display`). |

### Reuse unchanged
`shaping.py` (all), `queries.py` builders/validators, `_user_client`/`_run_sql`/`_env`, `error.html`, `_rows.html`, vendored USWDS, `resources/app.yml` (no new resource), `resources/seed_job.yml`.

### Setup / deploy (CLI, admin confirmed)
- Create both groups; add greg.skinner (id `75113935367499`) to `efile_glance_download_users`.
- Apply `resources/grants.sql` (SP grants + app-users SELECT) via `databricks api post /api/2.0/sql/statements -p DEFAULT` on warehouse `2f225c0740dcd22b`.
- Redeploy app: `databricks bundle deploy --target dev` (standard engine) then `databricks bundle run download_hub` / restart (slow — venv rebuild now installs openpyxl + python-multipart).

---

## Recommended References (for the executor)
- `~/.ai-dev-kit/repo/.claude/skills/databricks-python-sdk/SKILL.md` — Statement Execution, the CRITICAL `asyncio.to_thread` rule (SDK is fully synchronous), auth patterns.
- `~/.ai-dev-kit/repo/.claude/skills/python-dev/SKILL.md` — pure functions, type hints, Google docstrings, pytest in `./tests/`.
- In-repo (authoritative — read before coding): `src/app/main.py`, `src/app/auth.py`, `src/app/queries.py`, `src/app/shaping.py`, `src/app/templates/glance.html`, `src/app/templates/base.html`, `resources/grants.sql`, `tests/test_auth.py`, `tests/test_shaping.py`.
- `.planning/phases/2-RESEARCH.md` + `.planning/phases/3-RESEARCH.md` — OBO / `auth_type="pat"` / `StatementParameterListItem` parameter-binding patterns this phase reuses.
- Memory: `reference_databricks_apps_obo_auth` — the user-token (`auth_type="pat"`) vs SP (plain `WorkspaceClient()`) distinction.

---

## Risks / Notes
- **`me().groups` under OBO — verify at checkpoint.** HIGH confidence it populates (live `/Me` returns groups; `iam.current-user:read` scope effective), but the SDK class wasn't introspectable in the pytest-only venv. If the OBO `me()` returns empty `.groups`, switch to the SP→SCIM fallback (needs the SP made workspace admin — heavier). Never fail open: unresolved membership ⇒ `can_download=False`, panel hidden, `/download` 403.
- **Grant to the appId, not the numeric id/name.** The runtime identity is `service_principal_client_id = 97898a88-5dfd-4c75-bd0b-a6279a13ea08` (≠ `oauth2_app_client_id` `c013d181-…`). Grant MODIFY+SELECT to that appId.
- **`python-multipart` is a hard dependency** for the POST form; missing it → 500 on `/download`. Add it to requirements.
- **openpyxl absent in dev venv** — lazy-import it inside `to_xlsx_bytes`; guard the XLSX test with `pytest.importorskip("openpyxl")`. `databricks-sdk`/`fastapi` also absent → keep `exports.py`/`audit.py`/`auth.py` free of SDK/fastapi imports at module scope so tests import them cleanly.
- **Audit-first ordering (NFR-5):** INSERT (as SP) must succeed BEFORE the file is returned; on INSERT failure return 500, never a file. Emit an app-log line for `databricks apps logs`.
- **Value-must-be-string:** every `StatementParameterListItem.value` is a string — cast `row_count` (`str(...)`) and `acknowledged` (`"true"`/`"false"`); `event_ts` uses SQL `current_timestamp()` (no param).
- **Do NOT insert test rows into `download_audit`** — it must stay empty for the verify checkpoint (this research inserted nothing; only DESCRIBE was run).
- **UC still gates data (FR-7):** the re-query in `/download` runs AS THE USER via OBO, so a download-group member who lacks `SELECT` on the gold table still gets nothing — both conditions required. The audit row still lands (SP write).
- **Search filter parity:** the export must apply the same metric-name substring filter the user sees on screen (client-side JS filters the table; the server re-applies `search` to the re-queried rows so the file matches). Ensure the hidden `search` field is kept in sync by the JS.
- **Groups don't exist yet** — create both this phase; the app-users SELECT grant (deferred from Phase 1) applies once `efile_glance_app_users` exists.
```
