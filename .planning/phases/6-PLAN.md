---
phase: 6-report-config-layer-generic-obo-query
plan: 6
type: execute
status: planned
workspace_url: https://field-eng-aws-gov-demo-irs-demo.cloud.databricks.us
default_catalog: irs
skill_references:
  - ~/.ai-dev-kit/repo/.claude/skills/python-dev/SKILL.md
  - ~/.ai-dev-kit/repo/.claude/skills/databricks-python-sdk/SKILL.md
wave_count: 5
---

# Phase 6: Report config layer + generic per-user OBO query builder

## Goal
Lay the Milestone-2 foundation: create the `irs.efile.report_config` registry (seeded with
report #1 = E-File at a Glance), and ship a PURE, unit-tested config model/parser + a generic
parameterized query builder (`src/app/reports.py`). Backend only — no route/UI change (wired
in Phase 7).

## Workspace Context (live, 2026-08-13)
- `irs.efile` has only `daily_efile_glance` + `download_audit`; **`report_config` does NOT exist**
  (created this phase). Warehouse `2f225c0740dcd22b` healthy.
- App SP `97898a88-5dfd-4c75-bd0b-a6279a13ea08` already has `USE CATALOG irs` + `USE SCHEMA irs.efile`
  — only a table-level SELECT on `report_config` is new.

## Prerequisites
- [ ] Milestone 1 (Phases 1–5) complete; branch dbx/download-hub-phase-1; CLI profile DEFAULT valid.

## Skills to Read Before Executing
- `~/.ai-dev-kit/repo/.claude/skills/python-dev/SKILL.md` — pure funcs, type hints, Google docstrings, pytest.
- `~/.ai-dev-kit/repo/.claude/skills/databricks-python-sdk/SKILL.md` — StatementParameterListItem param pattern (I/O is Phase 7; Phase 6 stays pure).
- In-repo to mirror: `src/app/audit.py` (the `(sql, params)` + `{"name","value","type"}` pattern), `src/app/queries.py` (validator/ValueError style), `src/app/config.py` (pure module style), `src/notebooks/generate_efile_glance.py` (where to add section 7).
- `.planning/phases/6-RESEARCH.md` — full DDL/MERGE + builder/validator/parser code sketches (authoritative).

---

## LOCKED DECISIONS (executor MUST follow verbatim)

### L1 — reports.py is a NEW pure module (do not extend queries.py)
`src/app/reports.py`: stdlib only (`re`, `json`, `dataclasses`) — NO fastapi/databricks/pyspark
import at module scope (must import in the pytest-only venv). `queries.py` stays report-specific,
untouched.

### L2 — Injection rule
VALUES (report_date, filter selections) are ONLY ever bound as `:named` params via
`{"name","value","type"}` dicts (mirror `audit.py`). IDENTIFIERS (columns, filter fields, order_by,
each dotted part of source_fqn) come from config and CANNOT be bound — they are interpolated, so
every identifier is validated against `^[A-Za-z_][A-Za-z0-9_]*$` (per-part for source_fqn) and
raises ValueError otherwise. Bind `report_date` as TIMESTAMP, filter values as STRING; all param
values are strings.

### L3 — report_config created + seeded idempotently in the seed notebook
Extend `src/notebooks/generate_efile_glance.py` with a new section (after the gold write):
`CREATE TABLE IF NOT EXISTS {schema_fqn}.report_config (...)` then an idempotent **DeltaTable MERGE
on report_id** (build the row with `spark.createDataFrame` + `json.dumps` for columns_json/
filters_json, stamp `updated_at` with `F.current_timestamp()`). NEVER `overwrite` report_config
(preserve later-added reports). Use the `catalog`/`schema` widgets — no hardcoding. `source_fqn`
built from `schema_fqn`. Runtime libs only (`delta`, `pyspark.sql.functions`, stdlib `json`) — no %pip.

### L4 — Grant
Add to `resources/grants.sql`: `GRANT SELECT ON TABLE irs.efile.report_config TO
\`97898a88-5dfd-4c75-bd0b-a6279a13ea08\`;` (3-level; SP already has catalog/schema USE).

### L5 — Config model tolerant; identifier safety at build time
`parse_report_config` parses JSON → dataclasses, tolerant of unknown `format` (default "text"),
raises ValueError on malformed JSON. It does NOT enforce identifier safety — that is enforced by the
query builders (`validate_identifier`) at build time.

---

## Wave 1: pure reports.py (model + validators + builders)

<task type="auto">
  <name>Implement src/app/reports.py (config model, validators, generic query builders)</name>
  <wave>1</wave>
  <files>src/app/reports.py</files>
  <read_first>
    - ~/.ai-dev-kit/repo/.claude/skills/python-dev/SKILL.md
    - .planning/phases/6-RESEARCH.md (Generic Query Builder + Config Model sketches — copy them)
    - src/app/audit.py (the (sql, params) + param-dict pattern to mirror)
    - src/app/queries.py (validator/ValueError style)
  </read_first>
  <action>
    Write PURE `src/app/reports.py` (stdlib `re`/`json`/`dataclasses` only; NO SDK/Spark/fastapi import):
    - `VALID_FORMATS = frozenset({"int","pct","text"})`.
    - Frozen dataclasses `ColumnSpec(name,label,format="text")`, `FilterSpec(field,label)`,
      `ReportConfig(report_id,title,source_fqn,date_field,columns,filters,order_by,display_order,enabled,download_group=None)`.
    - `parse_report_config(row: dict) -> ReportConfig` — json.loads columns_json + (filters_json or "[]");
      wrap json.JSONDecodeError in ValueError with report_id; tolerant of unknown format.
    - `validate_identifier(name) -> str` (regex `^[A-Za-z_][A-Za-z0-9_]*$`, else ValueError) and
      `validate_fqn(fqn) -> str` (1–3 dotted parts, each validated).
    - `build_report_query(source_fqn, columns, date_field, report_date, filters=None, order_by=None)
      -> (sql, params)`: SELECT validated cols FROM validated fqn WHERE date_field = :report_date
      [AND f = :flt_f ...] [ORDER BY order_by]; report_date bound TIMESTAMP, filter values bound STRING.
    - `build_report_dates_query(source_fqn, date_field) -> str` (DISTINCT dates DESC).
    - `build_distinct_values_query(source_fqn, field, date_field=None, report_date=None) -> (sql, params)`
      (optional date scoping).
    - `build_report_config_query(catalog, schema) -> str` (fixed-identifier registry SELECT WHERE
      enabled = true ORDER BY display_order).
    All typed + Google docstrings; small functions; no side effects.
  </action>
  <verify>cd /Users/greg.skinner/Documents/IRS/download_hub && .venv/bin/python -m py_compile src/app/reports.py && PYTHONPATH=src .venv/bin/python -c "
from app.reports import parse_report_config, build_report_query, validate_identifier
import json
r={'report_id':'efile_glance','title':'T','source_fqn':'irs.efile.daily_efile_glance','date_field':'report_date','columns_json':json.dumps([{'name':'value_cy','label':'2026','format':'int'}]),'filters_json':json.dumps([{'field':'drain','label':'DRAIN'}]),'order_by':'sort_order','display_order':1,'enabled':True}
rc=parse_report_config(r); print(len(rc.columns), rc.filters[0].field)
sql,p=build_report_query('irs.efile.daily_efile_glance',['value_cy'],'report_date','2026-01-12 00:00:00',{'drain':'ALL'},'sort_order'); print(':report_date' in sql, 'flt_drain' in sql, len(p))
try: validate_identifier('a; DROP'); print('FAIL')
except ValueError: print('rejected-bad-ident')"</verify>
  <acceptance_criteria>
    - No fastapi/databricks/pyspark import in reports.py (stdlib only).
    - parse_report_config returns ColumnSpec/FilterSpec lists; malformed JSON raises ValueError; unknown format tolerated (default text).
    - build_report_query interpolates ONLY validated identifiers and binds report_date (TIMESTAMP) + filter values (STRING) as params; returns (sql, params).
    - validate_identifier/validate_fqn reject bad identifiers ("a; DROP", "1col", "", multi-dot > 3).
    - build_report_dates_query / build_distinct_values_query / build_report_config_query produce the documented SQL.
  </acceptance_criteria>
</task>

---

## Wave 2: unit tests

<task type="auto">
  <name>Write tests/test_reports.py</name>
  <wave>2</wave>
  <files>tests/test_reports.py</files>
  <read_first>
    - src/app/reports.py (module under test)
    - tests/test_queries.py, tests/test_audit.py (style to mirror)
    - .planning/phases/6-PLAN.md (LOCKED L2/L5)
  </read_first>
  <action>
    pytest (offline, no SDK/Spark). Cover:
    - parse_report_config: the CONTEXT seed row (columns_json with 4 cols + filters_json with drain)
      parses to 4 ColumnSpec + 1 FilterSpec with correct name/label/format; unknown format tolerated;
      malformed columns_json raises ValueError; missing/empty filters_json -> [].
    - validate_identifier: accepts value_cy/report_date/sort_order; rejects "a; DROP--", "1col", "",
      "drop table", "col-name". validate_fqn: accepts irs.efile.daily_efile_glance and daily_efile_glance;
      rejects "a.b.c.d" and "a..b" and "irs.efile.tbl; DROP".
    - build_report_query: 3-level fqn present, ":report_date" bound (TIMESTAMP), each filter adds
      "AND <f> = :flt_<f>" with a STRING param, ORDER BY when order_by given; params list length matches;
      a bad column/filter/order_by/source raises ValueError (not interpolated).
    - build_report_dates_query: DISTINCT <date_field> ... ORDER BY DESC.
    - build_distinct_values_query: with and without date scoping (param present only when scoped).
    - build_report_config_query: contains report_config, enabled = true, ORDER BY display_order.
  </action>
  <verify>cd /Users/greg.skinner/Documents/IRS/download_hub && PYTHONPATH=src .venv/bin/python -m pytest tests/ -q</verify>
  <acceptance_criteria>
    - All tests pass (prior 89 + new). A test proves bad identifiers raise for every interpolation point (column, filter field, order_by, source_fqn).
    - A test parses the exact CONTEXT seed-row JSON into 4 ColumnSpec + 1 FilterSpec.
    - No pyspark/fastapi/databricks import in the tests.
  </acceptance_criteria>
</task>

---

## Wave 3: extend the seed notebook (create + seed report_config)

<task type="auto">
  <name>Append report_config CREATE + idempotent MERGE to generate_efile_glance.py</name>
  <wave>3</wave>
  <files>src/notebooks/generate_efile_glance.py</files>
  <read_first>
    - src/notebooks/generate_efile_glance.py (existing sections + widgets)
    - .planning/phases/6-RESEARCH.md (DDL + DeltaTable MERGE sketch)
    - src/app/reports.py (keep the seed columns_json/filters_json shape consistent with parse_report_config)
  </read_first>
  <action>
    Append a new `# COMMAND ----------` section AFTER the gold write/verify. In it:
    - `config_fqn = f"{schema_fqn}.report_config"` (from the existing catalog/schema widgets).
    - `CREATE TABLE IF NOT EXISTS {config_fqn} (...)` with the 11 columns from LOCKED L3 / CONTEXT
      (report_id, title, source_fqn, date_field, columns_json, filters_json, order_by, display_order,
      enabled, download_group, updated_at) USING DELTA.
    - Build the report #1 seed row via `spark.createDataFrame([...], seed_schema)` (StructType for the
      10 data cols) with `columns_json = json.dumps([...])` (metric_name/value_cy/value_py/pct_change
      with labels Metric/2026/2025/% Change + formats text/int/int/pct) and
      `filters_json = json.dumps([{"field":"drain","label":"DRAIN"}])`; `source_fqn =
      f"{schema_fqn}.daily_efile_glance"`; order_by "sort_order"; display_order 1; enabled True;
      download_group None. Add `.withColumn("updated_at", F.current_timestamp())`.
    - MERGE via `DeltaTable.forName(spark, config_fqn).alias("t").merge(src.alias("s"),
      "t.report_id = s.report_id").whenMatchedUpdateAll().whenNotMatchedInsertAll().execute()`.
    - Add imports in that cell: `import json`, `from delta.tables import DeltaTable`,
      `from pyspark.sql import functions as F`, and the needed pyspark.sql.types.
    - A short verify line: print `spark.table(config_fqn).count()` and show report_id/title.
    Do NOT overwrite report_config; do NOT touch the gold write; no %pip; no hardcoded irs/efile.
  </action>
  <verify>cd /Users/greg.skinner/Documents/IRS/download_hub && .venv/bin/python -m py_compile src/notebooks/generate_efile_glance.py && grep -q "report_config" src/notebooks/generate_efile_glance.py && grep -q "whenNotMatchedInsertAll" src/notebooks/generate_efile_glance.py && ! grep -q "report_config.*overwrite" src/notebooks/generate_efile_glance.py && echo ok</verify>
  <acceptance_criteria>
    - Notebook compiles; new section does CREATE TABLE IF NOT EXISTS report_config + DeltaTable MERGE on report_id (no overwrite).
    - columns_json/filters_json built via json.dumps; seed row shape matches parse_report_config (4 cols + drain filter); source_fqn from schema_fqn widget.
    - Gold write unchanged; no %pip; no hardcoded catalog/schema.
  </acceptance_criteria>
</task>

---

## Wave 4: grant

<task type="auto">
  <name>Add report_config SELECT grant to the app SP in grants.sql</name>
  <wave>4</wave>
  <files>resources/grants.sql</files>
  <read_first>
    - resources/grants.sql (existing SP grants block + appId)
    - .planning/phases/6-RESEARCH.md (Grant section)
  </read_first>
  <action>
    Append one statement (3-level) to resources/grants.sql, with a short comment:
    `GRANT SELECT ON TABLE irs.efile.report_config TO \`97898a88-5dfd-4c75-bd0b-a6279a13ea08\`;`
    Do NOT grant report_config to end users (app SP reads the registry). Keep all existing statements.
  </action>
  <verify>cd /Users/greg.skinner/Documents/IRS/download_hub && grep -q "report_config" resources/grants.sql && grep -q "97898a88-5dfd-4c75-bd0b-a6279a13ea08" resources/grants.sql && echo ok</verify>
  <acceptance_criteria>
    - grants.sql has GRANT SELECT ON TABLE irs.efile.report_config to the appId (3-level, backtick-quoted); existing grants intact; no user grant on report_config.
  </acceptance_criteria>
</task>

---

## Checkpoint: run seed, apply grant, verify registry

<task type="checkpoint:human">
  <name>Deploy + run seed to create/seed report_config, apply grant, verify</name>
  <wave>5</wave>
  <action>
    From repo root (branch dbx/download-hub-phase-1):
    1. PYTHONPATH=src .venv/bin/python -m pytest tests/ -q
    2. databricks bundle validate -t dev -p DEFAULT && databricks bundle deploy --target dev -p DEFAULT
    3. databricks bundle run efile_seed -t dev -p DEFAULT   (creates + seeds report_config; gold overwrite unchanged)
    4. Verify the registry (Statement Execution API, warehouse 2f225c0740dcd22b):
       - SHOW TABLES IN irs.efile  → now includes report_config
       - SELECT report_id, title, source_fqn, date_field, order_by, display_order, enabled FROM irs.efile.report_config
         → one row: efile_glance / Daily E-File at a Glance / irs.efile.daily_efile_glance / report_date / sort_order / 1 / true
       - SELECT columns_json, filters_json FROM irs.efile.report_config WHERE report_id='efile_glance'
         → valid JSON (4 columns + drain filter)
    5. Idempotency: run efile_seed AGAIN → report_config still exactly 1 row (updated_at re-stamped), no error.
    6. Apply the grant: databricks api post /api/2.0/sql/statements -p DEFAULT --json
       '{"warehouse_id":"2f225c0740dcd22b","statement":"GRANT SELECT ON TABLE irs.efile.report_config TO `97898a88-5dfd-4c75-bd0b-a6279a13ea08`","wait_timeout":"30s"}'
       then SHOW GRANTS ON TABLE irs.efile.report_config → SP has SELECT.
    7. (Optional) round-trip: pull the seed row and confirm parse_report_config parses it (4 ColumnSpec + 1 FilterSpec).
  </action>
  <acceptance_criteria>
    - pytest passes; bundle validate+deploy succeed; efile_seed run SUCCEEDS.
    - irs.efile.report_config exists with exactly 1 enabled row (efile_glance) whose columns_json/filters_json are valid JSON matching the seed.
    - Re-running efile_seed keeps report_config at 1 row (idempotent MERGE, no clobber).
    - SP has SELECT on report_config (SHOW GRANTS).
  </acceptance_criteria>
</task>

---

## Must-Haves
```yaml
truths:
  - reports.py is a NEW pure module (stdlib only); queries.py untouched.
  - Values bound as params (report_date TIMESTAMP, filters STRING); identifiers validated (regex) then interpolated — bad identifiers raise.
  - report_config created via CREATE TABLE IF NOT EXISTS + DeltaTable MERGE on report_id (never overwrite); JSON via json.dumps; seeded from the seed notebook using catalog/schema widgets.
  - parse_report_config tolerant of unknown format, raises on malformed JSON; identifier safety enforced at build time.
  - App SP granted SELECT on report_config; end users not granted (per-user OBO still governs report DATA).
  - Backend-only: no route/UI/template change this phase.
artifacts:
  - src/app/reports.py (model + validators + builders)
  - tests/test_reports.py
  - src/notebooks/generate_efile_glance.py (append report_config create+MERGE)
  - resources/grants.sql (report_config SELECT to app SP)
uc_targets:
  - irs.efile.report_config (NEW registry; CREATE IF NOT EXISTS + MERGE; 1 seed row)
  - irs.efile.daily_efile_glance (referenced as source_fqn in the seed row; unchanged)
```
