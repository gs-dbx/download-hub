# Phase 1 Research

**Date:** 2026-08-12
**Phase:** Foundation — `irs.efile` schema + synthetic gold table + empty audit table via DAB serverless Job
**Domain:** Unity Catalog setup + synthetic (pre-aggregated) data generation + Asset Bundles scaffold (batch; no DLT, no ingestion)
**MCP Available:** yes (databricks-v2)
**CLI Available:** yes — **live scan SUCCEEDED** (the anticipated expired-token failure did NOT occur; `databricks ... -p DEFAULT` worked)

---

## Workspace State

**Live scan performed 2026-08-12 against profile `DEFAULT`.** All values below are observed, not assumed.

**Workspace URL:** https://field-eng-aws-gov-demo-irs-demo.cloud.databricks.us (AWS GovCloud)
**Default Catalog:** `irs`
**Default Schema (target):** `efile`

**Target schema `irs.efile`:** **DOES NOT EXIST** — confirmed live (`Error: Schema 'irs.efile' does not exist.`). Everything in Phase 1 is created fresh.

**Existing schemas in `irs` (do NOT touch):**
- `irs.ocfo` (greg.skinner) — OCFO Genie project
- `irs.demo` (alex.hunt) — existing contract data
- `irs.cost_forecast` (greg.skinner) — separate Cost/DBU forecaster demo
- `irs.default`, `irs.information_schema` (system/auto)
- `irs.test_schema_01` … `irs.test_schema_10` (scratch)

**SQL Warehouse:** `2f225c0740dcd22b` "Serverless Starter Warehouse" — **HEALTHY**, Small, Photon on, serverless on, `auto_stop_mins=10`, max 1 cluster. (Not required for the Phase 1 seed job itself — the job runs on serverless *jobs* compute; the warehouse is for the app in later phases.)

**Existing Jobs / Pipelines matching efile/download/glance/hub:** NONE found. Create fresh.

**Serverless:** enabled (jobs + pipelines + SQL). No cluster policy (serverless only).

**Existing bundle scaffold (already in repo, partially built):**
- `/Users/greg.skinner/Documents/IRS/download_hub/databricks.yml` — `bundle.name: download_hub`; variables `catalog=irs`, `schema=efile`, `warehouse_id=2f225c0740dcd22b`, `app_users_group`, `download_users_group`; `workspace.host` set; targets `dev` (default, development), `staging` (development), `prod` (production). **No `include:` and no `resources:` yet** — Phase 1 must add these.
- `/Users/greg.skinner/Documents/IRS/download_hub/.databricks-ai-dev-kit.yaml` — project tags.
- No `resources/`, `src/`, or `tests/` directories exist yet.

---

## Relevant Skill Patterns

### From `asset-bundles/SKILL.md`
- **Bundle layout:** `databricks.yml` (root) + `resources/*.yml` + `src/`. Add `include: [resources/*.yml]` to the existing `databricks.yml`.
- **Path resolution (critical):** notebook/source paths in `resources/*.yml` are **relative to `resources/`**, so use `../src/...`. (In `databricks.yml` itself it would be `./src/...`.)
- **Job resource shape:** `resources.jobs.<key>` with `tasks: [{task_key, notebook_task: {notebook_path: ../src/...}}]`. The SKILL example shows `new_cluster`, but for **serverless** omit `new_cluster`/`existing_cluster_id` on a notebook task so it runs on serverless jobs compute.
- **Variables:** parameterize catalog/schema/warehouse (already done in the existing `databricks.yml`).
- **Bundle engine:** the seed job alone does NOT need `DATABRICKS_BUNDLE_ENGINE=direct`. Standard engine is fine for Phase 1. (Phase 2's `apps` resource is what triggers the direct-engine requirement — do not prematurely adopt it here.)
- **Commands:** `databricks bundle validate -t dev`, `databricks bundle deploy -t dev`, `databricks bundle run <job_key> -t dev`.

### From `synthetic-data-generation/SKILL.md` — **applies with deliberate deviations**
The skill's defaults are written for a different scenario (MCP-executed Faker scripts feeding a downstream medallion pipeline). Phase 1 intentionally departs on three points — the executor must NOT blindly follow the skill:
1. **Pre-aggregated gold, not raw.** The skill says "raw transactional data only, no sums/totals, save parquet to a Volume." Phase 1 explicitly wants a **pre-aggregated gold Delta table** (`value_cy`, `value_py`, `pct_change`, plus a summed `ALL` drain). This is the documented exception ("If the user specifically requests aggregated fields or summary tables, follow their instructions").
2. **No Faker/numpy/holidays (air-gap).** Those libs are not pre-installed and cannot be fetched. Use **built-in Spark + Python stdlib only** (`random` with a fixed seed, or deterministic formulas). Keep magnitudes production-realistic by hand (millions of returns, billions where applicable).
3. **Fixed early-2026 report dates**, not "last 6 months from today" — 5–7 hand-picked business-day snapshots (per 1-CONTEXT.md), formatted `YYYY-MM-DD 00:00:00`.

Retained from the skill: **deterministic seed for reproducibility**, config constants at top, generate with plain Python then hand to Spark, `CREATE ... IF NOT EXISTS` infrastructure inside the script.

### From `databricks-python-sdk/SKILL.md` + `python-dev/SKILL.md`
- Pure generation logic must be a **pure, importable function with type hints and Google-style docstrings**, returning `list[dict]` (row dicts) — **no `pyspark` import** so pytest runs without Spark/JVM.
- Tests live in `./tests/` with `__init__.py`; pytest only; TDD.
- `spark.sql("CREATE SCHEMA IF NOT EXISTS ...")` and `spark.createDataFrame(rows, schema).write.format("delta").mode("overwrite").saveAsTable(...)` are the write primitives. SDK `w.schemas.create` / `w.tables.exists` exist but are unnecessary inside a notebook where `spark` is present — prefer `spark.sql` DDL (matches locked decision).

### Unity Catalog
- `irs` catalog already exists; only `CREATE SCHEMA IF NOT EXISTS irs.efile` + two `CREATE TABLE`/`saveAsTable` calls are needed. No catalog creation, no external location, no volume required for Phase 1. (The `/Volumes/irs/efile/app_assets` volume in WORKSPACE.md is for later app-asset staging, not this phase.)

---

## Recommended Approach

**Overall:** Add a single DAB **Job** (notebook task, serverless) to the existing `download_hub` bundle. The notebook (a) creates the schema, (b) creates the empty audit table via explicit typed DDL, and (c) builds the gold rows from a **pure, Spark-free generator function** and writes them Delta-overwrite. All generation math is unit-tested via pytest against the pure function — no live cluster needed for tests.

### Directory structure to create
```
download_hub/
├── databricks.yml                       # EXISTS — add `include: [resources/*.yml]`
├── resources/
│   └── seed_job.yml                     # jobs.efile_seed : notebook task, serverless, params catalog/schema
├── src/
│   ├── notebooks/
│   │   └── generate_efile_glance.py     # Databricks notebook (`# Databricks notebook source`)
│   └── efile_glance/
│       ├── __init__.py
│       └── generator.py                 # PURE: METRICS list + build_glance_rows() + pct_change()
├── tests/
│   ├── __init__.py
│   └── test_generator.py
└── requirements-dev.txt                 # pytest only (air-gap: pre-stage-able)
```

**Why `src/efile_glance/generator.py` is separate from the notebook:** pytest must `import` it and exercise the row-building math without a SparkSession. The notebook is a thin driver: `from efile_glance.generator import build_glance_rows, METRICS`, then `spark.createDataFrame(build_glance_rows(...), schema)` → Delta overwrite. Keep `METRICS` (ordered list of `(metric_name, metric_group, sort_order)`) in `generator.py` as the single source of truth so Phase 2's UI can mirror ordering.

### Notebook / job wiring
- Job key `efile_seed`, task `generate`, `notebook_task.notebook_path: ../src/notebooks/generate_efile_glance.py` (relative to `resources/`).
- **Serverless:** omit `new_cluster`; a notebook task with no compute block runs on serverless jobs compute (feature flag `serverless_jobs: true` confirmed).
- **Job parameters:** `catalog` (default `${var.catalog}` = `irs`), `schema` (default `${var.schema}` = `efile`) so targets aren't hardcoded; notebook reads via `dbutils.widgets.get(...)`.
- **Trigger:** on-demand only (no schedule) — run with `databricks bundle run efile_seed -t dev`.
- **Notifications:** on-failure email `greg.skinner@databricks.com` (dev).
- **Run-as:** deploying user (Greg) for dev.

### Gold table — DDL & content (mirror PROJECT.md + 1-CONTEXT.md)

Table `irs.efile.daily_efile_glance`, written via `spark.createDataFrame(rows, schema).write.format("delta").mode("overwrite").saveAsTable(...)`. Columns:

| Column | Type | Notes |
|---|---|---|
| report_date | TIMESTAMP | 5–7 fixed early-2026 business days, `YYYY-MM-DD 00:00:00` |
| drain | STRING | `E` \| `M` \| `N` \| `ALL` (ALL is materialized) |
| metric_name | STRING | one of the 17 labels below |
| metric_group | STRING | `original` \| `amended` \| `combined` \| `pin` \| `other` |
| sort_order | INT | 1..17, fixed, unique per (report_date, drain) |
| value_cy | BIGINT | 2026 value, ≥ 0 |
| value_py | BIGINT | 2025 value, ≥ 0 |
| pct_change | DOUBLE | precomputed |

**Row cardinality:** 17 metrics × 4 drains (`E`,`M`,`N`,`ALL`) × N report_dates (5–7) ≈ 340–476 rows.

**Suggested metric list with group + sort_order** (executor/planner to confirm labels/groups — PROJECT.md lists the 17 names and the group vocabulary but not the exact name→group map; below is the natural mapping):

| sort_order | metric_name | metric_group |
|---|---|---|
| 1 | PY Filed In 2026 | other |
| 2 | ERO Accepted (original) | original |
| 3 | Online Accepted (original) | original |
| 4 | Total Accepted (original) | original |
| 5 | ERO Accepted (amended) | amended |
| 6 | Online Accepted (amended) | amended |
| 7 | Total Accepted (amended) | amended |
| 8 | ERO Accepted (combined) | combined |
| 9 | Online Accepted (combined) | combined |
| 10 | Total Accepted (combined) | combined |
| 11 | Balance Due | other |
| 12 | ERO Self Select PIN Return Vol | pin |
| 13 | ERO PIN Return Vol | pin |
| 14 | Online PIN Return Vol | pin |
| 15 | ERO | other |
| 16 | Online | other |
| 17 | ERO Self-Select PIN Total | pin |

**`pct_change` math:** `round((value_cy - value_py) / value_py * 100, 1)`; when `value_py == 0`, set `pct_change` to NULL (or 0.0) — pick NULL and be consistent; document it.

**`drain='ALL'` materialization (key requirement):** for each `(report_date, metric)`, `ALL.value_cy = E.value_cy + M.value_cy + N.value_cy` (same for `value_py`), and `ALL.pct_change` is **recomputed from the summed cy/py** — NOT the sum/average of the three drains' pct_change. This is a common bug; unit-test it explicitly.

### Audit table — DDL (created empty here, written by the app in Phase 4)

`CREATE TABLE IF NOT EXISTS irs.efile.download_audit` with explicit typed columns (append-only, no data inserted this phase):

| Column | Type |
|---|---|
| audit_id | STRING |
| event_ts | TIMESTAMP |
| user_email | STRING |
| report_date | TIMESTAMP |
| drain_filter | STRING |
| search_filter | STRING |
| row_count | BIGINT |
| export_format | STRING |
| justification | STRING |
| acknowledged | BOOLEAN |
| app_version | STRING |

Use `USING DELTA` and `IF NOT EXISTS` so reruns are idempotent and never clobber future app-written rows.

### Idempotency
- Schema + audit table: `IF NOT EXISTS` (safe reruns; audit rows preserved).
- Gold table: `mode("overwrite")` + a **fixed random seed** → deterministic, stable across reruns.

### Testing (pytest, air-gap-friendly)
Unit-test the pure `generator.py` (no Spark):
- Exactly 17 metrics present, in unique/complete `sort_order` 1..17 per (report_date, drain).
- All four drain values present; `E`,`M`,`N` each carry the full 17.
- `ALL` value_cy/value_py == elementwise sum of E+M+N per (report_date, metric).
- `pct_change == round((cy-py)/py*100, 1)`; `py==0` handled (NULL/0 per chosen rule), including for the `ALL` slice.
- `value_cy >= 0` and `value_py >= 0`; no nulls in key columns (report_date, drain, metric_name, sort_order).
- Determinism: two calls with the same seed produce identical rows.

### Air-gap / dependency implications
- **Runtime (job):** built-in Spark only — no `faker`, `numpy`, `holidays`, or any pip install. Use stdlib `random`/`datetime`.
- **Dev-time:** `pytest` is the ONLY dev dependency for Phase 1 → put it in `requirements-dev.txt` so it can be pre-staged; the pure function has zero third-party imports.

---

## Existing Resources (Reuse vs Create)

| Resource | Status | Action |
|---|---|---|
| `download_hub` bundle (`databricks.yml`) | Exists (scaffold, no resources) | **Reuse** — add `include:` + `resources/seed_job.yml` |
| `irs.efile` schema | Does not exist (confirmed live) | **Create** (`CREATE SCHEMA IF NOT EXISTS`) |
| `irs.efile.daily_efile_glance` | Does not exist | **Create** (Delta overwrite) |
| `irs.efile.download_audit` | Does not exist | **Create** empty (typed DDL, `IF NOT EXISTS`) |
| Seed Job | No matching job found | **Create** (`resources/seed_job.yml`) |
| Warehouse `2f225c0740dcd22b` | Exists, HEALTHY | Reference only (not used by seed job; for app in later phases) |
| `irs.ocfo`, `irs.demo`, `irs.cost_forecast` | Exist (other projects) | **Do NOT touch** |

---

## Constraints Identified

- **Compute:** serverless jobs compute for the seed job (no cluster, no policy). Do not add `new_cluster`.
- **Bundle engine:** standard engine OK for Phase 1; reserve `DATABRICKS_BUNDLE_ENGINE=direct` for the Phase 2 `apps` resource.
- **Air-gap:** no network package installs at runtime; stdlib + Spark only. Dev = pytest only.
- **Naming:** bundle `download_hub`; tables under `irs.efile.*`; keep `bundle.name` = `download_hub` (matches WORKSPACE.md).
- **Bundle targets:** `dev` (default), `staging`, `prod` — deploy `dev` first.
- **UC permissions:** none required to *create* under `irs` for the deploying user (Greg owns `irs.ocfo`/`cost_forecast`, so has create rights in `irs`). App SP grant on `download_audit` (write) is **deferred to Phase 4** — do not add grants this phase.
- **Data classification:** synthetic only, but magnitudes must look production-realistic (millions of returns).

---

## Recommended References (for the executor to read before coding)

- `~/.ai-dev-kit/repo/.claude/skills/python-dev/SKILL.md` — pure functions, type hints, pytest in `./tests/`
- `~/.ai-dev-kit/repo/.claude/skills/databricks-python-sdk/SKILL.md` — spark/DDL patterns, schema/table APIs
- `~/.ai-dev-kit/repo/databricks-skills/asset-bundles/SKILL.md` — job resource shape, `../src/` path rule, serverless notebook task, validate/deploy/run commands
- `~/.ai-dev-kit/repo/databricks-skills/synthetic-data-generation/SKILL.md` — generation structure & determinism (apply with the 3 documented deviations above: pre-aggregated gold, no Faker/air-gap, fixed dates)
- `~/.ai-dev-kit/repo/databricks-skills/databricks-unity-catalog/README.md` — UC namespace / volume path conventions (light touch this phase)

Existing repo files the executor should build on:
- `/Users/greg.skinner/Documents/IRS/download_hub/databricks.yml` (add `include:` + resources)
- `/Users/greg.skinner/Documents/IRS/download_hub/.databricks-ai-dev-kit.yaml` (tags applied to created resources)

---

## Risks / Notes

- **Auth was NOT expired.** The task brief warned the DEFAULT OAuth token was expired; in fact `databricks ... -p DEFAULT` succeeded and returned live results (schemas, warehouse, jobs). The planner can rely on live introspection; no `databricks auth login` needed right now. (If it does expire later, re-auth with `databricks auth login --profile DEFAULT`.)
- **Metric→group mapping is inferred**, not explicitly given in PROJECT.md (which lists the 17 names and the group vocabulary separately). The table above is a reasonable mapping; the executor should confirm and lock it in `generator.py` as the canonical ordering for Phase 2 to mirror.
- **Synthetic-data skill mismatch:** the skill strongly steers toward raw parquet in Volumes + Faker + "last 6 months." This phase deliberately does the opposite (aggregated gold Delta table, air-gapped stdlib, fixed 2026 dates). Flagged so the executor doesn't over-apply the skill.
- **`ALL` pct_change** is the most error-prone spot — recompute from summed cy/py, never aggregate the per-drain percentages. Covered by a dedicated unit test.
- **`report_date` dates** are a deferred open question (1-CONTEXT.md): planner/executor should pick 5–7 concrete early-2026 business days unless told otherwise.
- **Volume `/Volumes/irs/efile/app_assets`** referenced in WORKSPACE.md is NOT needed in Phase 1 (creating the schema first is a prerequisite for it, but the volume itself is for app-asset staging in later phases). Do not create it here unless the planner scopes it in.
