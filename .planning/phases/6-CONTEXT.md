# Phase 6 Context

**Phase:** Report config layer + generic per-user OBO data access (Milestone 2 foundation)
**Discussed:** 2026-08-13
**Status:** ready for planning

Backend-only phase: create the report registry table, a config loader, and a generic
parameterized OBO query builder. NO UI/route change yet (wired in Phase 7).

## Locked Decisions

### Component type & scope
- Additive backend to the `download-hub` app + one new UC table. No pipeline/DLT.
- Delivers: `irs.efile.report_config` (+ seed row for report #1), a pure config model/loader,
  and a pure generic query builder. Not yet referenced by any route.

### UC targets
- **NEW:** `irs.efile.report_config` — the report registry (read by the app service principal).
- Reads (generic, per-user OBO at runtime — used from Phase 7 on): each report's `source_fqn`
  (e.g. `irs.efile.daily_efile_glance`).

### report_config schema (Delta)
| Column | Type | Notes |
|---|---|---|
| report_id | STRING | natural key (e.g. `efile_glance`) |
| title | STRING | tab name |
| source_fqn | STRING | 3-level view/table (e.g. `irs.efile.daily_efile_glance`) |
| date_field | STRING | field driving the report-date selector (e.g. `report_date`) |
| columns_json | STRING | JSON list of `{name,label,format}`; format ∈ {int, pct, text} (extensible) |
| filters_json | STRING | JSON list of `{field,label}` — equality single-select filters |
| order_by | STRING | optional column to ORDER BY (e.g. `sort_order`); nullable |
| display_order | INT | tab ordering |
| enabled | BOOLEAN | show/hide the tab |
| download_group | STRING | nullable; RESERVED — Milestone 2 uses the single global download group; per-report is future |
| updated_at | TIMESTAMP | bookkeeping (current_timestamp() on upsert) |

- **Storage decision:** list-valued config held as **JSON string columns** (`columns_json`,
  `filters_json`) — easy hand-authoring via INSERT, matches the repo's csv_ingestion JSON convention.
- **Column config richness:** `name + label + optional format` (int/pct/text). Report #1 reuses its
  CY/PY/%-change formatting via formats.
- **Filter operators:** equality only (single-select dropdown), exactly like today's DRAIN.

### Seed row (report #1 — E-File at a Glance)
```
report_id     = 'efile_glance'
title         = 'Daily E-File at a Glance'
source_fqn    = 'irs.efile.daily_efile_glance'
date_field    = 'report_date'
columns_json  = [{"name":"metric_name","label":"Metric","format":"text"},
                 {"name":"value_cy","label":"2026","format":"int"},
                 {"name":"value_py","label":"2025","format":"int"},
                 {"name":"pct_change","label":"% Change","format":"pct"}]
filters_json  = [{"field":"drain","label":"DRAIN"}]
order_by      = 'sort_order'
display_order = 1
enabled       = true
download_group= NULL   (uses the global download group)
```

### Create + seed mechanism
- **Extend the existing `efile_seed` notebook/job:** add `CREATE TABLE IF NOT EXISTS
  irs.efile.report_config (...)` and an **idempotent MERGE** (upsert by `report_id`) of the report #1
  row — so re-running the seed never clobbers reports added later. (The gold-table overwrite is
  unchanged; report_config is create-if-not-exists + merge, NOT overwrite.)

### Generic query builder (pure, the core deliverable)
- `build_report_query(source_fqn, columns, date_field, filters, order_by) -> (sql, params)`:
  `SELECT <cols> FROM <source_fqn> WHERE <date_field> = :report_date [AND <f> = :flt_<f> ...]
  [ORDER BY <order_by>]`.
- `build_report_dates_query(source_fqn, date_field)` — DISTINCT dates DESC (report-date selector).
- `build_distinct_values_query(source_fqn, field, date_field)` — DISTINCT values of a filter field
  (optionally scoped to the selected date) for building the dropdown.
- **Injection rule (critical):** SQL parameters bind only VALUES (date, filter selections).
  Identifiers (column names, filter fields, `order_by`, `source_fqn` parts) come from CONFIG and
  **cannot** be bound params — they are interpolated, so the builder MUST validate every identifier
  against a strict allowlist regex (`^[A-Za-z_][A-Za-z0-9_]*$` for columns/fields; each dotted part
  of `source_fqn` likewise) and reject anything else. Config is admin-authored, but validate anyway
  (defense in depth + clean errors).
- Date default = latest (`MAX(date_field)`) resolved via the dates query; the row query always binds
  a specific `report_date`.

### Config loader
- `ReportConfig` dataclass (report_id, title, source_fqn, date_field, columns:[ColumnSpec],
  filters:[FilterSpec], order_by, display_order, enabled) + pure `parse_report_config(row)` that
  parses the JSON columns. The actual `SELECT * FROM report_config` runs as the **app SP** (metadata,
  not user data) — that SP read is the I/O boundary (Phase 7 wires it); Phase 6 ships the pure parser
  + the query string builder for it.

### Access / grants
- App SP (`97898a88-...`) needs `SELECT` on `irs.efile.report_config` — add to `resources/grants.sql`.
- End users do NOT need report_config access (app SP reads the registry). Per-user OBO still governs
  the report DATA.

### Testing
- Pure pytest (offline, no SDK/Spark): JSON parsing (columns/filters → dataclasses, bad JSON handling);
  query builder SQL shape + parameterization; **identifier validation rejects bad identifiers**;
  distinct-values + dates query builders; seed-row JSON is valid and parses.

### Compute / deployment
- Serverless. Extend `resources/seed_job.yml` notebook (no new bundle resource). Standard engine.
- `report_config` DDL/seed applied by running `efile_seed` in dev; SP grant applied via grants.sql.

## Open Questions (Deferred)
- Hiding tabs a user can't read (vs rendering empty) → Phase 7 decision.
- Per-report download groups (the `download_group` column) → future; Milestone 2 uses global group.
- Range/multi-select filters → future (Phase 6 is equality single-select).

## Workspace Scan Summary
- `irs.efile` exists (Phase 1); `daily_efile_glance` populated. `report_config` does NOT exist yet
  (created this phase). Warehouse `2f225c0740dcd22b` healthy. App SP appId `97898a88-...`.
