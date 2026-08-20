---
phase: 1-foundation-schema-gold-table-synthetic-data
plan: 1
type: execute
status: planned
workspace_url: https://field-eng-aws-gov-demo-irs-demo.cloud.databricks.us
default_catalog: irs
skill_references:
  - ~/.ai-dev-kit/repo/.claude/skills/python-dev/SKILL.md
  - ~/.ai-dev-kit/repo/.claude/skills/databricks-python-sdk/SKILL.md
  - ~/.ai-dev-kit/repo/databricks-skills/asset-bundles/SKILL.md
  - ~/.ai-dev-kit/repo/databricks-skills/synthetic-data-generation/SKILL.md
  - ~/.ai-dev-kit/repo/databricks-skills/databricks-unity-catalog/README.md
wave_count: 7
---

# Phase 1: Foundation — schema, gold table & synthetic data

## Goal
Deploy a serverless DAB Job that creates the `irs.efile` schema, materializes the
synthetic gold table `irs.efile.daily_efile_glance` (17 metrics × E/M/N/ALL × 6 fixed
early-2026 report dates), and creates the empty typed audit table
`irs.efile.download_audit` — all backed by a pure, Spark-free, unit-tested generator.

## Workspace Context
- **Catalog:** `irs`
- **Target Schema:** `irs.efile` (NEW — confirmed absent by live scan; created this phase)
- **Compute:** serverless (serverless jobs compute; no `new_cluster`, no policy)
- **Warehouse:** Serverless Starter Warehouse (ID: `2f225c0740dcd22b`) — NOT used by the seed job; reserved for the app in later phases

## Prerequisites
- [ ] WORKSPACE.md has `default_warehouse_id: 2f225c0740dcd22b` (present)
- [ ] Databricks CLI OAuth profile `DEFAULT` authenticated (live scan succeeded 2026-08-12; re-auth with `databricks auth login --profile DEFAULT` only if a call returns 401)
- [ ] Asset Bundle `download_hub` initialized — `/Users/greg.skinner/Documents/IRS/download_hub/databricks.yml` exists (has bundle name, variables, workspace host, dev/staging/prod targets; NO `include:`/`resources:` yet — Wave 5 adds them)
- [ ] `irs` catalog exists; deploying user (Greg Skinner) has CREATE SCHEMA on `irs` (owns `irs.ocfo`, `irs.cost_forecast`)
- [ ] Do NOT touch existing schemas `irs.ocfo`, `irs.demo`, `irs.cost_forecast`

## Skills to Read Before Executing
- `~/.ai-dev-kit/repo/.claude/skills/python-dev/SKILL.md` — pure functions, type hints, Google-style docstrings, pytest-only tests in `./tests/`
- `~/.ai-dev-kit/repo/.claude/skills/databricks-python-sdk/SKILL.md` — `spark.sql` DDL and `spark.createDataFrame(...).write` patterns
- `~/.ai-dev-kit/repo/databricks-skills/asset-bundles/SKILL.md` — job resource shape, the `../src/` path rule for `resources/*.yml`, serverless notebook task (omit `new_cluster`), validate/deploy/run commands
- `~/.ai-dev-kit/repo/databricks-skills/synthetic-data-generation/SKILL.md` — determinism + config-constants structure; APPLY WITH 3 DELIBERATE DEVIATIONS: (1) pre-aggregated gold Delta table (not raw parquet in a Volume), (2) NO Faker/numpy/holidays — stdlib + Spark only (air-gap), (3) fixed early-2026 dates (not "last 6 months")
- `~/.ai-dev-kit/repo/databricks-skills/databricks-unity-catalog/README.md` — 3-level namespace conventions (light touch this phase)

---

## LOCKED DECISIONS (resolve the two open research questions — executor MUST follow verbatim)

### L1 — Concrete `report_date` values (6 fixed early-2026 business days)
Use exactly these six timestamps, all at `00:00:00`, as Python `datetime.datetime` objects
inside the pure generator (stdlib `datetime` only):

```
2026-01-05 00:00:00
2026-01-06 00:00:00
2026-01-07 00:00:00
2026-01-08 00:00:00
2026-01-09 00:00:00
2026-01-12 00:00:00
```

(Mon–Fri of the week of Jan 5, plus Mon Jan 12 — all business days.) Prior-year (`value_py`)
represents the analogous 2025 filing-season value; it is generated in the same row (not a
separate report_date). No date is computed relative to "today".

### L2 — Metric → `metric_group` → `sort_order` mapping (all 17, unique 1..17, PROJECT.md display order)
This is the CANONICAL ordering. It lives as `METRICS` in `src/efile_glance/generator.py` and is
the single source of truth Phase 2's UI mirrors. `metric_group` ∈ {original, amended, combined, pin, other}.

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

### L3 — `pct_change` math + the load-bearing `drain='ALL'` rule
- Per row: `pct_change = round((value_cy - value_py) / value_py * 100, 1)`.
- If `value_py == 0`: `pct_change = None` (NULL). Applies to E/M/N AND ALL slices.
- **ALL slice (must be recomputed, never averaged):** for each `(report_date, metric)`,
  `ALL.value_cy = E.value_cy + M.value_cy + N.value_cy` (same for `value_py`), then
  `ALL.pct_change` is computed from those SUMS via the same formula above. NEVER sum or
  average the three per-drain `pct_change` values — that is the known bug this phase guards
  against with a dedicated unit test.

### L4 — Air-gap dependency rule
- **Runtime (the job/notebook):** built-in Spark + Python stdlib ONLY (`random`, `datetime`,
  `hashlib`, `uuid` if needed). NO Faker, numpy, holidays, pandas, or any pip install.
- **Dev-time:** `pytest` is the SOLE dependency (in `requirements-dev.txt`). The pure generator
  has ZERO third-party imports so pytest runs with no Spark/JVM.

### L5 — Determinism (stable across reruns and machines)
- Seed each `(report_date, drain, metric)` draw with a value derived deterministically via
  `hashlib` (e.g. `int.from_bytes(hashlib.md5(key.encode("utf-8")).digest()[:8], "big")`),
  passed to `random.Random(seed)`. Do NOT use the builtin `hash()` of strings (it is salted by
  `PYTHONHASHSEED` and is not reproducible across processes).
- Gold table is written with `mode("overwrite")`, so deterministic generation ⇒ stable reruns.

---

## Wave 1: Foundation (scaffolding + dev deps)

<task type="auto">
  <name>Create package/test scaffolding and dev requirements</name>
  <wave>1</wave>
  <files>src/efile_glance/__init__.py, src/notebooks/.gitkeep, tests/__init__.py, requirements-dev.txt</files>
  <read_first>
    - ~/.ai-dev-kit/repo/.claude/skills/python-dev/SKILL.md (tests/ layout with __init__.py; pytest-only)
    - .planning/phases/1-RESEARCH.md (directory structure section)
  </read_first>
  <action>
    Create the directory + package skeleton the later waves fill in. All paths relative to
    the bundle root /Users/greg.skinner/Documents/IRS/download_hub.
    - src/efile_glance/__init__.py : empty package marker (a one-line module docstring is fine).
    - src/notebooks/.gitkeep : placeholder so the dir exists before Wave 3 writes the notebook.
    - tests/__init__.py : empty (makes tests an importable package per python-dev skill).
    - requirements-dev.txt : contains exactly one line `pytest` (air-gap: pre-stageable). Do NOT
      add any runtime deps here — runtime is Spark + stdlib only (see L4).
    Do NOT create /Volumes/... or any UC objects in this wave. Do NOT create a runtime
    requirements.txt (there are no third-party runtime deps).
  </action>
  <verify>ls src/efile_glance/__init__.py src/notebooks/.gitkeep tests/__init__.py requirements-dev.txt</verify>
  <acceptance_criteria>
    - All four files exist at the paths above.
    - requirements-dev.txt contains `pytest` and nothing else (no runtime packages).
    - No Faker/numpy/pandas/holidays anywhere in the repo.
  </acceptance_criteria>
</task>

---

## Wave 2: Core logic — the pure, Spark-free generator

<task type="auto">
  <name>Implement pure synthetic-data generator (generator.py)</name>
  <wave>2</wave>
  <files>src/efile_glance/generator.py</files>
  <read_first>
    - ~/.ai-dev-kit/repo/.claude/skills/python-dev/SKILL.md (pure functions, type hints, Google docstrings, small functions)
    - ~/.ai-dev-kit/repo/databricks-skills/synthetic-data-generation/SKILL.md (determinism + config constants; apply the 3 deviations noted above — pre-aggregated gold, air-gap stdlib, fixed dates)
    - .planning/phases/1-PLAN.md (LOCKED DECISIONS L1–L5)
  </read_first>
  <action>
    Write a PURE module — NO `pyspark` import (so pytest runs without Spark/JVM). Stdlib only:
    `datetime`, `random`, `hashlib` (see L4/L5).

    Module-level constants (single source of truth for Phase 2):
    - METRICS: an ordered list of 17 entries, each carrying (metric_name, metric_group, sort_order)
      EXACTLY as in LOCKED DECISION L2. Represent as a list of tuples or small dataclass/dict;
      sort_order must be 1..17 in that order. Recommend also attaching a per-metric realistic
      base magnitude for value_cy (see below) so generation is centralized.
    - REPORT_DATES: the 6 `datetime.datetime` values from LOCKED DECISION L1 (year 2026, 00:00:00).
    - DRAINS_BASE = ["E", "M", "N"]; the "ALL" drain is DERIVED, not drawn.
    - Choose production-realistic base magnitudes by metric_group so numbers look like IRS e-file
      volumes: volume/count metrics (accepted, PIN, ERO, Online, "PY Filed In 2026") in the
      millions (e.g. base ~ 1_000_000 .. 80_000_000 depending on metric); "Balance Due" may be a
      dollar-ish magnitude in the billions. Values must be BIGINT-friendly integers >= 0.

    Functions (all typed, Google-style docstrings):
    - `pct_change(value_cy: int, value_py: int) -> float | None`
      Return `round((value_cy - value_py) / value_py * 100, 1)`; return `None` when value_py == 0.
      (LOCKED DECISION L3.)
    - `_seed_for(report_date, drain, metric_name) -> int`
      Deterministic seed via hashlib (L5); NEVER Python builtin hash().
    - `build_glance_rows(report_dates=REPORT_DATES, seed_salt: str = "efile-glance-v1") -> list[dict]`
      Build every row. For each report_date × each drain in E/M/N × each of the 17 METRICS:
        * value_cy: deterministic draw around the metric's base magnitude (seeded per L5), int >= 0.
        * value_py: prior-year analogue — derive from value_cy via a per-metric/per-draw factor that
          keeps pct_change in a realistic band (roughly -15%..+25%), int >= 0. Occasionally allow
          value_py == 0 for at least one metric so the NULL pct_change branch is exercised in tests.
        * pct_change: from `pct_change(value_cy, value_py)`.
        * Row dict keys EXACTLY: report_date (datetime), drain (str), metric_name (str),
          metric_group (str), sort_order (int), value_cy (int), value_py (int), pct_change (float|None).
      Then MATERIALIZE the ALL slice: for each (report_date, metric), sum E+M+N value_cy and value_py,
      set drain="ALL", metric_group/sort_order copied from the metric, and RECOMPUTE pct_change from
      the summed cy/py via `pct_change(...)` (LOCKED DECISION L3 — never average per-drain pct).
      Return the full list (E/M/N rows + ALL rows). Cardinality = 17 × 4 × 6 = 408 rows.
    Keep functions small (single responsibility). No print/side effects. No file or network I/O.
  </action>
  <verify>python -m py_compile src/efile_glance/generator.py && python -c "import sys; sys.path.insert(0,'src'); from efile_glance.generator import build_glance_rows, METRICS, pct_change; rows=build_glance_rows(); print(len(rows), len(METRICS), pct_change(120,100), pct_change(5,0))"</verify>
  <acceptance_criteria>
    - `import pyspark` does NOT appear in the file; only stdlib imports (datetime, random, hashlib).
    - METRICS has 17 entries with unique sort_order 1..17 matching LOCKED DECISION L2 exactly (names + groups).
    - `build_glance_rows()` returns 408 rows (17 × {E,M,N,ALL} × 6 dates).
    - Every ALL row's value_cy/value_py equals the sum of the matching E+M+N rows for the same (report_date, metric).
    - Every ALL row's pct_change equals `pct_change(sum_cy, sum_py)` — recomputed, not averaged.
    - `pct_change(120,100) == 20.0` and `pct_change(5,0) is None`.
    - All value_cy and value_py are ints >= 0; no None in report_date/drain/metric_name/sort_order.
    - Two calls with the same args produce identical rows (determinism).
  </acceptance_criteria>
</task>

---

## Wave 3: Notebook driver (schema + audit DDL + gold overwrite)

<task type="auto">
  <name>Write serverless notebook driver generate_efile_glance.py</name>
  <wave>3</wave>
  <files>src/notebooks/generate_efile_glance.py</files>
  <read_first>
    - ~/.ai-dev-kit/repo/.claude/skills/databricks-python-sdk/SKILL.md (spark.sql DDL + createDataFrame().write patterns)
    - .planning/phases/1-CONTEXT.md (DDL + idempotency + job params decisions)
    - .planning/PROJECT.md (exact column names/types for both tables)
  </read_first>
  <action>
    Write a Databricks notebook source file — first line MUST be `# Databricks notebook source`.
    Cells separated by `# COMMAND ----------`. This is a THIN driver; all row math lives in
    generator.py. Steps:

    1. Widgets/params: read `catalog` (default "irs") and `schema` (default "efile") via
       `dbutils.widgets.text(...)` + `dbutils.widgets.get(...)`. Build
       `schema_fqn = f"{catalog}.{schema}"`, `gold_fqn = f"{schema_fqn}.daily_efile_glance"`,
       `audit_fqn = f"{schema_fqn}.download_audit"`. Do NOT hardcode `irs`/`efile` anywhere except
       as the widget default.
    2. Import the pure generator. Because the bundle syncs `src/`, add the src path so
       `from efile_glance.generator import build_glance_rows` resolves (e.g. append the notebook's
       parent-of-parent `.../src` to `sys.path`, or `%pip`-free relative import). Do NOT reimplement
       any generation logic here.
    3. `spark.sql(f"CREATE SCHEMA IF NOT EXISTS {schema_fqn}")`.
    4. Create the AUDIT table with EXPLICIT typed DDL, idempotent, exact columns (PROJECT.md), incl.
       the required `justification STRING` column:
       ```
       CREATE TABLE IF NOT EXISTS {audit_fqn} (
         audit_id STRING,
         event_ts TIMESTAMP,
         user_email STRING,
         report_date TIMESTAMP,
         drain_filter STRING,
         search_filter STRING,
         row_count BIGINT,
         export_format STRING,
         justification STRING,
         acknowledged BOOLEAN,
         app_version STRING
       ) USING DELTA
       ```
       IF NOT EXISTS + USING DELTA so reruns never clobber future app-written rows. Insert NO data.
    5. Build the gold rows: `rows = build_glance_rows()`. Define an explicit
       `pyspark.sql.types.StructType` matching the gold shape:
       report_date TimestampType (nullable False), drain StringType (False),
       metric_name StringType (False), metric_group StringType (False),
       sort_order IntegerType (False), value_cy LongType (False), value_py LongType (False),
       pct_change DoubleType (True — NULL allowed). Then
       `spark.createDataFrame(rows, schema).write.format("delta").mode("overwrite").saveAsTable(gold_fqn)`.
       (LongType ⇒ BIGINT; IntegerType ⇒ INT.) mode("overwrite") for idempotency (L5).
    6. End with a lightweight verification cell: print row count and
       `spark.table(gold_fqn).groupBy("drain").count().show()` so a run visibly confirms 408 rows / 4 drains.
    Serverless: the notebook must not reference any cluster. No pip installs (air-gap, L4).
  </action>
  <verify>python -m py_compile src/notebooks/generate_efile_glance.py</verify>
  <acceptance_criteria>
    - File begins with `# Databricks notebook source`.
    - Reads catalog/schema from widgets; no hardcoded `irs`/`efile` except widget defaults; no hardcoded workspace URL/token.
    - Imports build_glance_rows from efile_glance.generator (no duplicated generation logic).
    - Runs `CREATE SCHEMA IF NOT EXISTS {catalog}.{schema}`.
    - Creates `{catalog}.{schema}.download_audit` via CREATE TABLE IF NOT EXISTS ... USING DELTA with all 11 columns incl. `justification STRING`; inserts no rows.
    - Writes `{catalog}.{schema}.daily_efile_glance` via Delta `mode("overwrite")` using an explicit StructType (LongType for value_cy/value_py, DoubleType nullable for pct_change).
    - No `new_cluster`, `%pip install`, or third-party import in the notebook.
  </acceptance_criteria>
</task>

---

## Wave 4: Unit tests for the generator

<task type="auto">
  <name>Write pytest unit tests for generator.py</name>
  <wave>4</wave>
  <files>tests/test_generator.py</files>
  <read_first>
    - ~/.ai-dev-kit/repo/.claude/skills/python-dev/SKILL.md (pytest-only, tests/ layout, TDD)
    - src/efile_glance/generator.py (the module under test)
    - .planning/phases/1-PLAN.md (LOCKED DECISIONS L2/L3/L5 — the invariants to assert)
  </read_first>
  <action>
    Write pytest tests (no Spark, no cluster) importing from `efile_glance.generator`. Add
    `src` to sys.path at test-module top (or rely on a conftest/pytest.ini rootdir) so the import
    resolves. Cover at minimum:
    - test_metric_count_and_sort_order: METRICS has 17 entries; sort_order values are exactly the
      set {1..17}, unique; names + groups match LOCKED DECISION L2; every group ∈
      {original, amended, combined, pin, other}.
    - test_row_cardinality: build_glance_rows() returns 408 rows; per (report_date, drain) there are
      exactly 17 rows with a complete unique sort_order 1..17.
    - test_all_four_drains_present: drains present are exactly {E, M, N, ALL}; E, M, N each carry the
      full 17 metrics for every report_date.
    - test_all_slice_is_elementwise_sum: for each (report_date, metric), ALL.value_cy == E+M+N value_cy
      and ALL.value_py == E+M+N value_py.
    - test_all_slice_pct_recomputed_not_averaged: for each ALL row, pct_change == pct_change(sum_cy,
      sum_py); AND assert it does NOT equal the mean of the three per-drain pct_change values in a
      constructed case where those differ (guards the known averaging bug).
    - test_pct_change_formula: pct_change(120,100)==20.0; pct_change(90,100)==-10.0; rounding to 1 dp.
    - test_pct_change_zero_py_is_none: pct_change(x, 0) is None; and any generated row (E/M/N or ALL)
      with value_py==0 has pct_change None.
    - test_non_negative_and_no_null_keys: all value_cy/value_py >= 0; report_date/drain/metric_name/
      sort_order never None.
    - test_determinism: two build_glance_rows() calls with identical args produce identical output.
    No third-party imports beyond pytest. Do NOT import pyspark.
  </action>
  <verify>cd /Users/greg.skinner/Documents/IRS/download_hub && PYTHONPATH=src python -m pytest tests/ -v</verify>
  <acceptance_criteria>
    - All tests pass.
    - A dedicated test asserts the ALL-slice pct_change is recomputed from summed cy/py, NOT averaged.
    - A test exercises the value_py==0 → NULL pct_change branch for a generated row.
    - No hardcoded workspace URLs/tokens; no pyspark import in tests.
  </acceptance_criteria>
</task>

---

## Wave 5: Asset Bundle config (serverless job + include)

<task type="auto">
  <name>Add resources/seed_job.yml and wire include: into databricks.yml</name>
  <wave>5</wave>
  <files>resources/seed_job.yml, databricks.yml</files>
  <read_first>
    - ~/.ai-dev-kit/repo/databricks-skills/asset-bundles/SKILL.md (job resource shape; ../src path rule; serverless ⇒ omit new_cluster; validate/deploy/run)
    - ~/.databricks-gsd/references/deployment.md (job resource + email_notifications patterns)
    - .planning/WORKSPACE.md (bundle name, variables, notification email)
    - /Users/greg.skinner/Documents/IRS/download_hub/databricks.yml (existing scaffold to extend)
  </read_first>
  <action>
    (1) Create resources/seed_job.yml defining ONE serverless job:
      ```
      resources:
        jobs:
          efile_seed:
            name: "[${bundle.target}] download_hub — e-file glance seed"
            tasks:
              - task_key: generate
                notebook_task:
                  notebook_path: ../src/notebooks/generate_efile_glance.py   # relative to resources/
                  base_parameters:
                    catalog: ${var.catalog}
                    schema: ${var.schema}
            email_notifications:
              on_failure:
                - greg.skinner@databricks.com
      ```
      SERVERLESS: do NOT add `new_cluster`, `existing_cluster_id`, or `job_clusters` — a notebook
      task with no compute block runs on serverless jobs compute. No `schedule` (on-demand only).
      Path MUST be `../src/...` because the file lives in resources/ (SKILL path-resolution rule).
    (2) Edit databricks.yml to add, right after the `bundle:` block (before `variables:`):
      ```
      include:
        - resources/*.yml
      ```
      Do NOT change bundle.name (stays download_hub), the variables, the workspace.host, or the
      dev/staging/prod targets. Do NOT add the apps resource or DATABRICKS_BUNDLE_ENGINE=direct
      (that is Phase 2). Leave the trailing comment block or remove it — either is fine.
  </action>
  <verify>cd /Users/greg.skinner/Documents/IRS/download_hub && databricks bundle validate -t dev -p DEFAULT</verify>
  <acceptance_criteria>
    - `databricks bundle validate -t dev` passes with no errors.
    - resources/seed_job.yml defines resources.jobs.efile_seed with a single notebook task pointing at ../src/notebooks/generate_efile_glance.py.
    - The job has NO new_cluster/job_clusters/existing_cluster_id (serverless) and NO schedule.
    - base_parameters pass catalog=${var.catalog} and schema=${var.schema} (not hardcoded).
    - databricks.yml has `include: [resources/*.yml]`; bundle.name still `download_hub`; workspace host unchanged; dev/staging/prod targets intact.
    - on_failure email is greg.skinner@databricks.com.
  </acceptance_criteria>
</task>

---

## Wave 6: Unity Catalog grants

<task type="auto">
  <name>Author and apply UC grants for the new schema and tables</name>
  <wave>6</wave>
  <files>resources/grants.sql</files>
  <read_first>
    - ~/.databricks-gsd/references/deployment.md (GRANT statement patterns; grants as explicit tasks)
    - ~/.ai-dev-kit/repo/databricks-skills/databricks-unity-catalog/README.md (3-level namespace)
    - .planning/WORKSPACE.md (group names: efile_glance_app_users, efile_glance_download_users)
    - .planning/phases/1-CONTEXT.md (note: app SP write grant on download_audit is DEFERRED to Phase 4)
  </read_first>
  <action>
    Create resources/grants.sql containing idempotent GRANT statements that let app users READ the
    gold data via OBO (Phase 1 scope). Use 3-level names and the app-users group:
      ```
      GRANT USE CATALOG ON CATALOG irs TO `efile_glance_app_users`;
      GRANT USE SCHEMA ON SCHEMA irs.efile TO `efile_glance_app_users`;
      GRANT SELECT ON TABLE irs.efile.daily_efile_glance TO `efile_glance_app_users`;
      ```
    Do NOT grant write on download_audit and do NOT grant to the app service principal — that
    is explicitly deferred to Phase 4 (the SP does not exist until app deploy). Do NOT grant to
    the download group here (download gating is app-side + Phase 4/5). Keep the file to exactly the
    three statements above.
    Apply the grants against dev after the tables exist (run AFTER the Wave-final job run, or note
    that the checkpoint applies them). Apply via CLI using the shared warehouse:
      `databricks sql query --warehouse 2f225c0740dcd22b -p DEFAULT --query "<each statement>"`
    (or execute the file statement-by-statement). If the group `efile_glance_app_users` does not yet
    exist in the workspace, note it and defer the SELECT grant application to Phase 4 — but still
    commit resources/grants.sql so the intended grants are captured.
  </action>
  <verify>cd /Users/greg.skinner/Documents/IRS/download_hub && databricks sql query --warehouse 2f225c0740dcd22b -p DEFAULT --query "SHOW GRANTS ON TABLE irs.efile.daily_efile_glance"</verify>
  <acceptance_criteria>
    - resources/grants.sql exists with exactly the three GRANT statements above, all 3-level namespaced.
    - No grant to the app service principal and no write/MODIFY on download_audit (deferred to Phase 4).
    - SHOW GRANTS ON TABLE irs.efile.daily_efile_glance lists SELECT for efile_glance_app_users (OR the file documents that the group is not yet created and application is deferred to Phase 4).
  </acceptance_criteria>
</task>

---

## Checkpoint: validate, deploy to dev, run, verify tables

<task type="checkpoint:human">
  <name>Deploy to dev, run seed job, verify tables and grants</name>
  <wave>7</wave>
  <action>
    From /Users/greg.skinner/Documents/IRS/download_hub:
    1. cd /Users/greg.skinner/Documents/IRS/download_hub && PYTHONPATH=src python -m pytest tests/ -v
    2. databricks bundle validate -t dev -p DEFAULT
    3. databricks bundle deploy --target dev -p DEFAULT
    4. databricks bundle run efile_seed -t dev -p DEFAULT
    5. Verify gold table row count = 408 and 4 drains:
       databricks sql query --warehouse 2f225c0740dcd22b -p DEFAULT --query "SELECT drain, COUNT(*) FROM irs.efile.daily_efile_glance GROUP BY drain ORDER BY drain"
    6. Spot-check the ALL-slice recomputed pct_change for one (report_date, metric):
       databricks sql query --warehouse 2f225c0740dcd22b -p DEFAULT --query "SELECT drain, value_cy, value_py, pct_change FROM irs.efile.daily_efile_glance WHERE report_date = TIMESTAMP'2026-01-08 00:00:00' AND metric_name = 'Total Accepted (combined)' ORDER BY drain"
       (Confirm ALL.value_cy == E+M+N and ALL.pct_change == round((ALL.cy-ALL.py)/ALL.py*100,1).)
    7. Verify the audit table exists and is empty with the justification column:
       databricks sql query --warehouse 2f225c0740dcd22b -p DEFAULT --query "DESCRIBE TABLE irs.efile.download_audit"
       databricks sql query --warehouse 2f225c0740dcd22b -p DEFAULT --query "SELECT COUNT(*) FROM irs.efile.download_audit"
    8. Apply resources/grants.sql (Wave 6) if the app-users group exists.
  </action>
  <acceptance_criteria>
    - pytest: all generator tests pass.
    - `databricks bundle validate -t dev` and `deploy --target dev` complete without errors.
    - `databricks bundle run efile_seed -t dev` finishes with SUCCESS.
    - irs.efile.daily_efile_glance has 408 rows across exactly 4 drains (E, M, N, ALL); the ALL row for the spot-checked (report_date, metric) equals the summed E+M+N cy/py with a recomputed pct_change.
    - irs.efile.download_audit exists, has 0 rows, and DESCRIBE shows all 11 columns including `justification STRING`.
    - SHOW GRANTS on the gold table reflects SELECT for efile_glance_app_users, or grant application is documented as deferred to Phase 4.
  </acceptance_criteria>
</task>

---

## Must-Haves

```yaml
truths:
  - Runtime code uses only Spark + Python stdlib — no Faker/numpy/holidays/pandas (air-gap).
  - pytest is the sole dev-time dependency (requirements-dev.txt).
  - Generation math lives in a pure, Spark-free, importable generator.py; the notebook is a thin driver.
  - drain='ALL' recomputes pct_change from summed E+M+N cy/py — never averages per-drain percentages.
  - pct_change = round((cy-py)/py*100, 1); value_py==0 => NULL.
  - 6 fixed early-2026 report_dates (2026-01-05..09, 2026-01-12), all 00:00:00; prior-year uses 2025 values in value_py.
  - All 17 metrics have unique sort_order 1..17 in PROJECT.md display order with metric_group in {original,amended,combined,pin,other}.
  - Serverless job only (no new_cluster); on-demand (no schedule).
  - All table references are 3-level: irs.efile.daily_efile_glance, irs.efile.download_audit.
  - No hardcoded workspace URLs/tokens; catalog/schema come from job params/variables.

artifacts:
  - src/efile_glance/__init__.py
  - src/efile_glance/generator.py
  - src/notebooks/generate_efile_glance.py
  - tests/__init__.py
  - tests/test_generator.py
  - requirements-dev.txt
  - resources/seed_job.yml
  - resources/grants.sql
  - databricks.yml   (add include: resources/*.yml)

uc_targets:
  - irs.efile                          (schema, CREATE IF NOT EXISTS)
  - irs.efile.daily_efile_glance       (gold, Delta overwrite, 408 rows)
  - irs.efile.download_audit           (audit, CREATE TABLE IF NOT EXISTS, empty)
```
