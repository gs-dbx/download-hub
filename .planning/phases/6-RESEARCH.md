# Phase 6 Research

**Date:** 2026-08-13
**Phase:** Report config layer + generic per-user OBO data access (Milestone 2 foundation)
**Domain:** Unity Catalog (new registry table) + pure Python (config model + generic parameterized query builder). Backend-only; no UI/route change (wired Phase 7).
**MCP Available:** no (browser OAuth unusable) — used CLI + Statement Execution API on warehouse `2f225c0740dcd22b`.
**CLI Available:** yes — live scan SUCCEEDED against profile `DEFAULT`.

---

## Live Findings

Observed live (not assumed) on 2026-08-13:

- **`SHOW TABLES IN irs.efile`** returns exactly two tables: `daily_efile_glance` and `download_audit`. **`report_config` does NOT exist yet** — it is created fresh this phase. Nothing was created during this scan.
- **Warehouse `2f225c0740dcd22b`** ("Serverless Starter Warehouse"): `state = RUNNING`, `num_clusters = 1` — HEALTHY.
- **App service principal** appId = `97898a88-5dfd-4c75-bd0b-a6279a13ea08` (already named in `resources/grants.sql`; already has `USE CATALOG irs` + `USE SCHEMA irs.efile`).
- Existing pure app modules confirmed present and Spark/SDK-free: `src/app/queries.py`, `src/app/shaping.py`, `src/app/audit.py`, `src/app/config.py`. Tests live in `tests/` (pytest, `__init__.py` present). The param-dict → `StatementParameterListItem` pattern is already established in `audit.py` + `main.py`.

---

## Current Seed Notebook (what to extend)

`src/notebooks/generate_efile_glance.py` is a thin serverless driver with numbered `# COMMAND ----------` sections:

1. **Parameters** — `dbutils.widgets.text("catalog","irs")` / `("schema","efile")`; builds `schema_fqn`, `gold_fqn`, `audit_fqn`. (Use these same widgets — do NOT hardcode.)
2. Import the pure generator (`_add_src_to_path()` + `from efile_glance.generator import build_glance_rows`).
3. `CREATE SCHEMA IF NOT EXISTS {schema_fqn}`.
4. `CREATE TABLE IF NOT EXISTS {audit_fqn} (...) USING DELTA` (idempotent, never clobbers).
5. Build + **overwrite** `gold_fqn` (`spark.createDataFrame(...).write.format("delta").mode("overwrite").saveAsTable(...)`).
6. Verify (count + groupBy).

**Where to add the new work:** a new section **7 (Create + seed `report_config`)** appended **after** the gold write / verify. It defines `config_fqn = f"{schema_fqn}.report_config"` (from the widgets), does `CREATE TABLE IF NOT EXISTS` + an idempotent **MERGE** of report #1. The gold `overwrite` is unchanged and does not touch `report_config`. `report_config` is **create-if-not-exists + MERGE — never overwrite** (so later-added report rows survive reruns).

The notebook is **air-gapped, built-in Spark only** (LOCKED DECISION L4 from Phase 1) — no `%pip`. `delta.tables.DeltaTable` and `pyspark.sql.functions` are available in the serverless runtime (no install), and `json` is stdlib.

---

## report_config DDL + MERGE sketch

### DDL (idempotent — matches the CONTEXT schema exactly)

```python
config_fqn = f"{schema_fqn}.report_config"

spark.sql(
    f"""
    CREATE TABLE IF NOT EXISTS {config_fqn} (
      report_id      STRING,
      title          STRING,
      source_fqn     STRING,
      date_field     STRING,
      columns_json   STRING,
      filters_json   STRING,
      order_by       STRING,
      display_order  INT,
      enabled        BOOLEAN,
      download_group STRING,
      updated_at     TIMESTAMP
    ) USING DELTA
    """
)
```

### Idempotent upsert — recommend DeltaTable API + `json.dumps` (NOT a hand-escaped SQL literal)

**Recommendation:** build the seed row in Python, serialize the list-valued config with `json.dumps(...)` (guarantees valid, compact JSON and eliminates all SQL string-escaping risk), stamp `updated_at` with `F.current_timestamp()`, and MERGE via the Delta API. This is the cleaner path for a notebook because it avoids embedding a JSON string literal inside a `spark.sql` statement (where an apostrophe in a label would break the literal).

```python
import json
from delta.tables import DeltaTable
from pyspark.sql import functions as F
from pyspark.sql.types import (
    BooleanType, IntegerType, StringType, StructField, StructType,
)

columns_json = json.dumps([
    {"name": "metric_name", "label": "Metric",   "format": "text"},
    {"name": "value_cy",    "label": "2026",     "format": "int"},
    {"name": "value_py",    "label": "2025",     "format": "int"},
    {"name": "pct_change",  "label": "% Change", "format": "pct"},
])
filters_json = json.dumps([{"field": "drain", "label": "DRAIN"}])

seed_schema = StructType([
    StructField("report_id",      StringType(),  False),
    StructField("title",          StringType(),  False),
    StructField("source_fqn",     StringType(),  False),
    StructField("date_field",     StringType(),  False),
    StructField("columns_json",   StringType(),  False),
    StructField("filters_json",   StringType(),  False),
    StructField("order_by",       StringType(),  True),
    StructField("display_order",  IntegerType(), False),
    StructField("enabled",        BooleanType(), False),
    StructField("download_group", StringType(),  True),
])

seed_rows = [{
    "report_id": "efile_glance",
    "title": "Daily E-File at a Glance",
    "source_fqn": f"{schema_fqn}.daily_efile_glance",
    "date_field": "report_date",
    "columns_json": columns_json,
    "filters_json": filters_json,
    "order_by": "sort_order",
    "display_order": 1,
    "enabled": True,
    "download_group": None,   # uses the global download group (RESERVED column)
}]

# updated_at set at merge time (not in the static row) so every upsert re-stamps it.
src_df = spark.createDataFrame(seed_rows, seed_schema).withColumn(
    "updated_at", F.current_timestamp()
)

(
    DeltaTable.forName(spark, config_fqn).alias("t")
    .merge(src_df.alias("s"), "t.report_id = s.report_id")
    .whenMatchedUpdateAll()
    .whenNotMatchedInsertAll()
    .execute()
)
```

Note `source_fqn` is built from the `schema_fqn` widget value (`f"{schema_fqn}.daily_efile_glance"`), not hardcoded — so a non-default target catalog/schema stays consistent.

**Alternative (spark.sql MERGE)** — acceptable but slightly riskier because the JSON must be embedded as a single-quoted SQL literal:

```python
spark.sql(f"""
  MERGE INTO {config_fqn} AS t
  USING (SELECT
           'efile_glance' AS report_id, 'Daily E-File at a Glance' AS title,
           '{schema_fqn}.daily_efile_glance' AS source_fqn, 'report_date' AS date_field,
           '{columns_json}' AS columns_json, '{filters_json}' AS filters_json,
           'sort_order' AS order_by, 1 AS display_order, true AS enabled,
           CAST(NULL AS STRING) AS download_group, current_timestamp() AS updated_at
        ) AS s
  ON t.report_id = s.report_id
  WHEN MATCHED THEN UPDATE SET *
  WHEN NOT MATCHED THEN INSERT *
""")
```
This works because the `json.dumps` output contains only double quotes (safe inside a single-quoted SQL literal). It breaks the moment a label contains an apostrophe — hence the DeltaTable API is preferred. If this variant is used, still build the JSON via `json.dumps` (never hand-write it).

**Idempotency guarantee:** `CREATE TABLE IF NOT EXISTS` + MERGE-on-`report_id` means a rerun re-stamps `updated_at` on report #1 and leaves every other row untouched. There is no `overwrite` anywhere near `report_config`.

---

## Generic Query Builder + validation sketch

**Placement decision: new module `src/app/reports.py`** (do NOT extend `queries.py`).
Justification: `queries.py` is intentionally *report-specific* — it hardcodes the `daily_efile_glance` table name, the fixed 17-column SELECT list, and the `VALID_DRAINS` enum. Phase 6's builder is *generic and config-driven* (arbitrary `source_fqn`, arbitrary columns/filters from `report_config`). Mixing the two would blur single-responsibility and force `queries.py`'s report-specific validators to coexist with generic identifier validation. A separate `reports.py` keeps each module coherent, and both remain pure (no SDK/Spark import) so they import in the pytest-only `.venv`. `queries.py` stays as-is for Milestone-1 back-compat.

### Injection rule (critical)
- **VALUES** (the selected `report_date`, filter selections) are **only ever bound** as `:named` params via `{"name","value","type"}` dicts → `StatementParameterListItem` (mirrors `audit.py`/`main.py`). Never interpolated.
- **IDENTIFIERS** (column names, filter fields, `order_by`, each dotted part of `source_fqn`) come from admin-authored CONFIG and **cannot** be bound params — they are interpolated, so **every** identifier is validated against a strict allowlist regex and rejected otherwise (defense in depth + clean 400s).

### Validators

```python
import re

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

def validate_identifier(name: str) -> str:
    """Return `name` if it is a bare SQL identifier, else raise ValueError."""
    if not name or not _IDENT_RE.match(name):
        raise ValueError(f"invalid identifier {name!r}")
    return name

def validate_fqn(fqn: str) -> str:
    """Validate a dotted table name; each part must be a bare identifier.

    Accepts 1-3 dotted parts (3-level FQN expected, e.g. irs.efile.daily_efile_glance);
    validates each part with validate_identifier and returns the re-joined FQN.
    """
    parts = fqn.split(".")
    if not 1 <= len(parts) <= 3:
        raise ValueError(f"invalid source_fqn {fqn!r}")
    return ".".join(validate_identifier(p) for p in parts)
```

### Query builders

```python
def build_report_query(
    source_fqn: str,
    columns: list[str],                 # column NAMES (ColumnSpec.name); at least one
    date_field: str,
    report_date: str,                   # the bound VALUE (never interpolated)
    filters: dict[str, str] | None = None,   # field -> selected value (values bound)
    order_by: str | None = None,
) -> tuple[str, list[dict]]:
    """SELECT <cols> FROM <fqn> WHERE <date_field> = :report_date
       [AND <f> = :flt_<f> ...] [ORDER BY <order_by>], plus bound-param dicts.

    Every identifier is validated (allowlist regex); every value is bound as a param.
    """
    fqn = validate_fqn(source_fqn)
    date_field = validate_identifier(date_field)
    if not columns:
        raise ValueError("columns must be non-empty")
    col_list = ", ".join(validate_identifier(c) for c in columns)

    sql = f"SELECT {col_list} FROM {fqn} WHERE {date_field} = :report_date"
    params = [{"name": "report_date", "value": report_date, "type": "TIMESTAMP"}]

    for field, value in (filters or {}).items():
        f = validate_identifier(field)
        sql += f" AND {f} = :flt_{f}"
        params.append({"name": f"flt_{f}", "value": value, "type": "STRING"})

    if order_by:
        sql += f" ORDER BY {validate_identifier(order_by)}"
    return sql, params


def build_report_dates_query(source_fqn: str, date_field: str) -> str:
    """DISTINCT report-date values, newest first (feeds the date selector). No params."""
    fqn = validate_fqn(source_fqn)
    df = validate_identifier(date_field)
    return f"SELECT DISTINCT {df} FROM {fqn} ORDER BY {df} DESC"


def build_distinct_values_query(
    source_fqn: str,
    field: str,
    date_field: str | None = None,
    report_date: str | None = None,
) -> tuple[str, list[dict]]:
    """DISTINCT values of a filter `field` (for the dropdown), optionally scoped to a date.

    If `date_field` + `report_date` are both given, adds `WHERE <date_field> = :report_date`.
    """
    fqn = validate_fqn(source_fqn)
    fld = validate_identifier(field)
    sql = f"SELECT DISTINCT {fld} FROM {fqn}"
    params: list[dict] = []
    if date_field and report_date is not None:
        dfld = validate_identifier(date_field)
        sql += f" WHERE {dfld} = :report_date"
        params.append({"name": "report_date", "value": report_date, "type": "TIMESTAMP"})
    sql += f" ORDER BY {fld}"
    return sql, params


def build_report_config_query(catalog: str, schema: str) -> str:
    """Fixed-identifier SELECT of the registry (run as the app SP in Phase 7)."""
    if not catalog or not schema:
        raise ValueError("catalog and schema are required and must be non-empty")
    return (
        "SELECT report_id, title, source_fqn, date_field, columns_json, filters_json, "
        "order_by, display_order, enabled, download_group "
        f"FROM {catalog}.{schema}.report_config WHERE enabled = true ORDER BY display_order"
    )
```

**Contract note:** unlike `queries.build_glance_query_for_date` (which returns only a SQL string and lets the route assemble params), the generic builders return `(sql, params)` fully assembled — matching `audit.build_audit_insert`. This keeps all param assembly inside the pure, unit-tested module and keeps the Phase-7 route thin. `report_date` values bind as `TIMESTAMP`; filter values bind as `STRING` (equality single-select, matches today's DRAIN behavior).

---

## Config Model / Loader

Pure dataclasses + parser, living in `src/app/reports.py` (stdlib `json` + `dataclasses` only — no SDK). The live `SELECT * FROM report_config` runs as the **app SP** and is the I/O boundary wired in **Phase 7**; Phase 6 ships only the pure parser + the `build_report_config_query` string.

```python
import json
from dataclasses import dataclass, field

VALID_FORMATS: frozenset[str] = frozenset({"int", "pct", "text"})

@dataclass(frozen=True)
class ColumnSpec:
    name: str
    label: str
    format: str = "text"   # extensible; unknown formats tolerated (default/text)

@dataclass(frozen=True)
class FilterSpec:
    field: str
    label: str

@dataclass(frozen=True)
class ReportConfig:
    report_id: str
    title: str
    source_fqn: str
    date_field: str
    columns: list[ColumnSpec]
    filters: list[FilterSpec]
    order_by: str | None
    display_order: int
    enabled: bool
    download_group: str | None = None

def parse_report_config(row: dict) -> ReportConfig:
    """Parse a report_config row dict (JSON columns -> dataclasses).

    Raises ValueError on malformed columns_json/filters_json (wrap
    json.JSONDecodeError) for a clean error rather than a raw stack trace.
    """
    try:
        raw_cols = json.loads(row["columns_json"])
        raw_filters = json.loads(row.get("filters_json") or "[]")
    except json.JSONDecodeError as exc:
        raise ValueError(f"malformed report_config JSON for {row.get('report_id')!r}: {exc}") from exc
    columns = [ColumnSpec(name=c["name"], label=c["label"], format=c.get("format", "text")) for c in raw_cols]
    filters = [FilterSpec(field=f["field"], label=f["label"]) for f in raw_filters]
    return ReportConfig(
        report_id=row["report_id"], title=row["title"], source_fqn=row["source_fqn"],
        date_field=row["date_field"], columns=columns, filters=filters,
        order_by=row.get("order_by"), display_order=int(row["display_order"]),
        enabled=bool(row["enabled"]), download_group=row.get("download_group"),
    )
```

Keep the parser **tolerant** of unknown `format` values (the column is documented extensible) — do NOT reject on format; identifier/injection validation happens at query-build time (`validate_identifier` on `name`/`field`/`order_by`). A unit test should assert the seed-row JSON parses into 4 `ColumnSpec` + 1 `FilterSpec` and that bad JSON raises `ValueError`.

---

## Grant

Add one line to `resources/grants.sql` (the SP already has `USE CATALOG irs` + `USE SCHEMA irs.efile` from the Phase-4 block, so only the table SELECT is new):

```sql
-- App service principal reads the report registry (metadata, not user data).
GRANT SELECT ON TABLE irs.efile.report_config TO `97898a88-5dfd-4c75-bd0b-a6279a13ea08`;
```

End users do NOT get `report_config` access — the app SP reads the registry; per-user OBO still governs the report DATA. Apply at the checkpoint via the Statement Execution API (same pattern documented at the top of `grants.sql`).

---

## Files to Add / Modify

| File | Action | Notes |
|---|---|---|
| `src/app/reports.py` | **ADD** | Pure: `ColumnSpec`/`FilterSpec`/`ReportConfig` + `parse_report_config` + `validate_identifier`/`validate_fqn` + `build_report_query`/`build_report_dates_query`/`build_distinct_values_query`/`build_report_config_query`. No SDK/Spark import. |
| `tests/test_reports.py` | **ADD** | pytest, offline: JSON parse (good + bad), query SQL shape + `(sql, params)` correctness, identifier-validation rejects bad identifiers (e.g. `"a; DROP"`, `"1col"`, `""`), FQN validation, distinct-values + dates builders, seed-row JSON parses. |
| `src/notebooks/generate_efile_glance.py` | **MODIFY** | Append section 7: `report_config` `CREATE TABLE IF NOT EXISTS` + DeltaTable MERGE of report #1 (uses `schema_fqn` from widgets; no overwrite). Add `import json`, `from delta.tables import DeltaTable`, `from pyspark.sql import functions as F` in that cell. |
| `resources/grants.sql` | **MODIFY** | Add the `GRANT SELECT ON TABLE irs.efile.report_config ...` line. |
| `resources/seed_job.yml` | **NO CHANGE** | Notebook is already parameterized (`catalog`/`schema`) and runs serverless; no new bundle resource (per CONTEXT). |

No route/UI change (`main.py`, templates, `queries.py` untouched) — that is Phase 7.

---

## Recommended References (for the executor)

- `~/.ai-dev-kit/repo/.claude/skills/python-dev/SKILL.md` — pure functions, type hints, Google docstrings, pytest in `./tests/`, specific-exception handling.
- `~/.ai-dev-kit/repo/.claude/skills/databricks-python-sdk/SKILL.md` — Statement Execution + `StatementParameterListItem` param binding; SDK is the I/O boundary (Phase 7), Phase 6 stays pure.

Existing repo files to mirror for style:
- `src/app/audit.py` — the canonical `(sql, params)` builder + `{"name","value","type"}` param-dict pattern (reports.py should match it).
- `src/app/queries.py` — validator/`ValueError` style and named-placeholder SQL (`:report_date`).
- `src/app/config.py` — pure, stdlib-only module header/docstring convention.
- `src/notebooks/generate_efile_glance.py` — where/how to add the new idempotent section.

---

## Risks / Notes

- **JSON escaping in the notebook (top risk).** Always build `columns_json`/`filters_json` with `json.dumps(...)`, never by hand, and prefer the **DeltaTable MERGE + `createDataFrame`** path so the JSON is a plain Python string column — this sidesteps SQL string-literal escaping entirely. The `spark.sql` MERGE variant only works while labels contain no apostrophes.
- **MERGE idempotency / never overwrite.** `report_config` must be `CREATE TABLE IF NOT EXISTS` + `MERGE ON report_id`. Do NOT use `mode("overwrite")` — a rerun must preserve any reports added after report #1. Guard: MERGE upserts exactly one row and re-stamps `updated_at`.
- **Identifier injection.** Config is admin-authored but the builder MUST still validate every interpolated identifier (`^[A-Za-z_][A-Za-z0-9_]*$`, per-part for `source_fqn`) and bind all VALUES as params. A dedicated test must prove bad identifiers raise (defense in depth + clean 400s in Phase 7).
- **Param types.** Bind `report_date` as `TIMESTAMP` (matches `queries`/`audit` and the gold table's `report_date TIMESTAMP`); bind filter values as `STRING`. The API requires all param values be strings in the dict — same as `audit.py` (`str(...)`, `"true"/"false"`).
- **`download_group` is RESERVED** — seed it `NULL` (global group). Do not wire per-report groups this phase.
- **`report_config` vs report DATA boundary.** The registry read runs as the **app SP** (metadata); the per-report data read stays **per-user OBO** (Phase 7). Phase 6 ships only pure builders/parser — no live read is performed or required.
- **Air-gap.** Notebook uses only pre-installed runtime libs (`delta`, `pyspark.sql.functions`) + stdlib `json`. `reports.py` is stdlib-only (`re`, `json`, `dataclasses`) — no new dev dependency; `requirements-dev.txt` (pytest) unchanged.
