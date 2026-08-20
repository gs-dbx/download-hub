---
phase: 5-hardening-branding-docs
plan: 5
type: execute
status: planned
workspace_url: https://field-eng-aws-gov-demo-irs-demo.cloud.databricks.us
default_catalog: irs
skill_references:
  - ~/.ai-dev-kit/repo/.claude/skills/python-dev/SKILL.md
  - ~/.ai-dev-kit/repo/.claude/skills/databricks-python-sdk/SKILL.md
wave_count: 6
---

# Phase 5: Hardening, branding & docs

## Goal
Ship the `download-hub` app as a polished, documented, air-gap-ready deliverable: an operational
download **kill switch**, **IRS branding** (local logo + professional color overlay, all OCFO
references removed), **pinned/offline-ready dependencies** with a wheelhouse procedure, and a
**documentation set** (README + deploy + permissions + offline). No new UC objects, no new bundle
resources, no new runtime dependency.

## Workspace Context
- Extends the deployed `download-hub` app (ACTIVE on Phase 4 code). Redeploy + restart at checkpoint.
- Resolved runtime dep versions captured live (pin targets in L3). OCFO refs: exports.py, base.html,
  resources/app.yml (+ historical planning docs, left as-is).

## Prerequisites
- [ ] Phases 1–4 complete; branch dbx/download-hub-phase-1; CLI profile DEFAULT valid.

## Skills to Read Before Executing
- `~/.ai-dev-kit/repo/.claude/skills/python-dev/SKILL.md` — pure functions, type hints, pytest.
- `~/.ai-dev-kit/repo/.claude/skills/databricks-python-sdk/SKILL.md` — env/config patterns (context only).
- In-repo: src/app/main.py, exports.py, auth.py, templates/base.html, templates/glance.html,
  static/js/app.js, resources/app.yml, src/app/requirements.txt, requirements-dev.txt.

---

## LOCKED DECISIONS (executor MUST follow verbatim)

### L1 — Kill switch
- Env var `EFILE_DOWNLOADS_ENABLED` (default `"true"`). PURE helper `downloads_enabled(value: str
  | None) -> bool` returns False for `{"false","0","no","off",""}` (case-insensitive, stripped),
  True otherwise (default true when unset). Put it in a pure module (e.g. add to `queries.py` or a
  new `config.py` — pick one, keep it importable in the pytest-only venv) and UNIT TEST it.
- In main.py `/`: `can_download = downloads_enabled(os.environ.get("EFILE_DOWNLOADS_ENABLED")) and
  is_member(me_user, DOWNLOAD_GROUP)`. In `POST /download`: if NOT downloads_enabled → HTTP 403 with
  a clear "downloads are temporarily disabled" message, checked BEFORE/alongside the membership
  re-check. Add `EFILE_DOWNLOADS_ENABLED: "true"` to `src/app/app.yaml` env.

### L2 — Branding, remove OCFO
- **Remove every OCFO reference from shipping code**: base.html logo text →
  "Internal Revenue Service"; exports.py DISCLAIMER wording (drop "(OCFO)"); resources/app.yml
  description (drop "IRS OCFO"); any `<title>`/heading/main.py title string. Do NOT touch planning
  `.md` files. After this wave, `grep -riI ocfo src resources` returns nothing.
- **Local IRS logo** at `src/app/static/img/irs-logo.svg`: a clean, dignified placeholder — an "IRS"
  typographic badge (navy roundel + "IRS" monogram) with an accompanying "Internal Revenue Service"
  wordmark rendered in the header. Self-contained SVG (no external refs). Add an HTML comment in
  base.html noting the official approved asset can replace this file at the same path.
- **Local color overlay** `src/app/static/css/app.css`, loaded in base.html AFTER uswds.min.css.
  Professional IRS/Treasury palette via CSS custom properties: deep navy primary (e.g.
  `--irs-navy:#0b2265`), a secondary blue (`#1a4480`), a restrained accent (gold `#b8860b` or teal),
  near-white surface, dark slate text. Style: a branded header band (navy bg, white text, logo left),
  a hero with the app title, and table polish (bold header row, zebra rows, right-aligned numeric
  cells, subtle row borders, color the % change — green positive / red negative / muted em dash).
  Keep WCAG AA contrast. NO external fonts, NO CDN — reference only local assets. Keep the USWDS gov
  banner + the synthetic-data footer.
- base.html/glance.html stay valid Jinja (keep {% block %}s, the download panel, all ids/data-roles
  the JS depends on — glance-search, glance-report-date, glance-drain, glance-tbody, hidden fields).

### L3 — Dependency pinning + offline packaging
- Pin `src/app/requirements.txt` exactly: `fastapi==0.115.0`, `uvicorn==0.30.6`, `jinja2==3.1.6`,
  `databricks-sdk==0.33.0`, `openpyxl==3.1.5`, `python-multipart==0.0.30`. Pin `requirements-dev.txt`
  `pytest` to the installed version (check `.venv/bin/python -m pytest --version`).
- Add `requirements.lock` — the full transitive closure (versions from 5-CONTEXT §C) as a reference
  manifest, header-commented that it's the offline mirror source and is regenerated from a clean
  install.
- Add `scripts/build_wheelhouse.sh` (documented, executable): `pip download -r requirements.lock -d
  wheelhouse/ --platform manylinux2014_x86_64 --python-version 311 --only-binary=:all:` then offline
  install `pip install --no-index --find-links wheelhouse -r requirements.lock`. Do NOT commit wheels;
  add `wheelhouse/` to .gitignore (create/append). Comment the platform assumptions.

### L4 — Documentation
- `README.md` (top-level), `docs/DEPLOY.md`, `docs/PERMISSIONS.md`, `docs/OFFLINE.md` per 5-CONTEXT §E.
  Accurate to the built app; 3-level table names; include the account-federation follow-up in
  PERMISSIONS. No secrets/tokens in docs.

### L5 — No external URLs / no new deps
- Every asset (USWDS, logo, app.css, app.js) referenced by a LOCAL `/static/...` path. No CDN. No new
  runtime dependency (branding is CSS/SVG; kill switch is stdlib).

---

## Wave 1: kill switch

<task type="auto">
  <name>Add downloads_enabled helper, wire into main.py, add env to app.yaml</name>
  <wave>1</wave>
  <files>src/app/config.py, src/app/main.py, src/app/app.yaml</files>
  <read_first>
    - src/app/main.py (/ route can_download, POST /download gating)
    - .planning/phases/5-PLAN.md (LOCKED L1)
  </read_first>
  <action>
    - Create src/app/config.py (PURE, stdlib only): `downloads_enabled(value: str | None) -> bool`
      — strip/lower; False for {"false","0","no","off",""}; True otherwise (unset → True). Google docstring.
    - main.py: import downloads_enabled; in `/` set can_download = downloads_enabled(os.environ.get(
      "EFILE_DOWNLOADS_ENABLED")) and is_member(...). In POST /download, add an early check: if not
      downloads_enabled(...) → HTTPException(403, "Downloads are temporarily disabled.") before/at the
      membership re-check. Keep everything else intact.
    - app.yaml: add env `EFILE_DOWNLOADS_ENABLED` value "true".
  </action>
  <verify>cd /Users/greg.skinner/Documents/IRS/download_hub && .venv/bin/python -m py_compile src/app/config.py src/app/main.py && PYTHONPATH=src .venv/bin/python -c "from app.config import downloads_enabled as d; print(d(None), d('true'), d('false'), d('0'), d('off'), d(''))" && grep -q EFILE_DOWNLOADS_ENABLED src/app/app.yaml && grep -c downloads_enabled src/app/main.py</verify>
  <acceptance_criteria>
    - downloads_enabled(None)=True, ('true')=True, ('false')=False, ('0')=False, ('off')=False, ('')=False.
    - main.py: can_download ANDs downloads_enabled with is_member; POST /download 403s when disabled (independent of membership).
    - app.yaml has EFILE_DOWNLOADS_ENABLED: "true"; config.py imports with stdlib only.
  </acceptance_criteria>
</task>

---

## Wave 2: IRS branding + remove OCFO

<task type="auto">
  <name>Add logo + color overlay, rebrand header, remove all OCFO from shipping code</name>
  <wave>2</wave>
  <files>src/app/static/img/irs-logo.svg, src/app/static/css/app.css, src/app/templates/base.html, src/app/templates/glance.html, src/app/exports.py, resources/app.yml, src/app/main.py</files>
  <read_first>
    - src/app/templates/base.html (header/banner/footer structure), glance.html (hero/table)
    - src/app/exports.py (DISCLAIMER), resources/app.yml (description)
    - .planning/phases/5-PLAN.md (LOCKED L2/L5)
  </read_first>
  <action>
    - Create static/img/irs-logo.svg: self-contained, dignified placeholder — a navy roundel with an
      "IRS" monogram (Public Sans-ish bold), no external refs. Add a base.html comment: replace this
      file with the approved official asset at the same path.
    - Create static/css/app.css: CSS custom properties (--irs-navy #0b2265, --irs-blue #1a4480, an
      accent, surface, text) + a branded header band (navy, white text, logo left + "Internal Revenue
      Service" wordmark + app name), hero title, and table polish (bold header, zebra rows,
      right-aligned numerics, subtle borders, % change colored green/red/muted). WCAG AA contrast. No
      external fonts/CDN.
    - base.html: add `<link rel="stylesheet" href="/static/css/app.css">` AFTER the uswds.min.css link;
      rebuild the header to show the logo + "Internal Revenue Service" (NO OCFO); keep the gov banner,
      the {% block %}s, footer, and the uswds.min.js + app.js script tags.
    - glance.html: add a hero/title treatment ("Daily E-File at a Glance"); keep the search input,
      report-date/drain selects, table with glance-tbody + _rows.html include, and the download panel
      with all ids/data-roles + hidden fields UNCHANGED (JS depends on them).
    - Remove OCFO: exports.py DISCLAIMER (drop "(OCFO)", keep meaning); resources/app.yml description
      (e.g. "Daily E-File at a Glance (IRS) — server-rendered FastAPI + USWDS"); main.py FastAPI title /
      any heading string. After: no "ocfo" (case-insensitive) anywhere under src/ or resources/.
  </action>
  <verify>cd /Users/greg.skinner/Documents/IRS/download_hub && test -f src/app/static/img/irs-logo.svg && test -f src/app/static/css/app.css && grep -q "app.css" src/app/templates/base.html && ! grep -riI "ocfo" src resources && ! grep -rniE "unpkg|jsdelivr|cdnjs|https?://[^\"' )]+" src/app/static/css src/app/templates && echo ok</verify>
  <acceptance_criteria>
    - irs-logo.svg + app.css exist and are self-contained (no external refs); base.html links app.css after uswds and shows the logo + "Internal Revenue Service" (no OCFO).
    - `grep -riI ocfo src resources` returns NOTHING.
    - No CDN/external URL in templates or app.css.
    - glance.html still has glance-search / glance-report-date / glance-drain / glance-tbody / the download panel + hidden report_date/drain/search (JS contract intact).
  </acceptance_criteria>
</task>

---

## Wave 3: dependency pinning + offline packaging

<task type="auto">
  <name>Pin requirements, add requirements.lock + build_wheelhouse.sh + .gitignore</name>
  <wave>3</wave>
  <files>src/app/requirements.txt, requirements-dev.txt, requirements.lock, scripts/build_wheelhouse.sh, .gitignore</files>
  <read_first>
    - src/app/requirements.txt, requirements-dev.txt (current unpinned)
    - .planning/phases/5-CONTEXT.md (§C exact versions), .planning/phases/5-PLAN.md (LOCKED L3)
  </read_first>
  <action>
    - Pin src/app/requirements.txt: fastapi==0.115.0, uvicorn==0.30.6, jinja2==3.1.6,
      databricks-sdk==0.33.0, openpyxl==3.1.5, python-multipart==0.0.30.
    - Pin requirements-dev.txt: pytest==<installed> (get via `.venv/bin/python -m pytest --version`).
    - Create requirements.lock: full transitive closure from 5-CONTEXT §C (one pkg==ver per line),
      header comment: offline mirror source; regenerate from a clean `pip freeze`.
    - Create scripts/build_wheelhouse.sh (chmod +x): pip download -r requirements.lock -d wheelhouse/
      --platform manylinux2014_x86_64 --python-version 311 --only-binary=:all:  ; then the offline
      install line `pip install --no-index --find-links wheelhouse -r requirements.lock`. Comment the
      platform/python assumptions (linux, cpython 3.11 — matches the Apps runtime).
    - .gitignore: add `wheelhouse/`, `.venv/`, `__pycache__/`, `*.pyc` (create or append; do not remove
      existing entries).
  </action>
  <verify>cd /Users/greg.skinner/Documents/IRS/download_hub && grep -q "fastapi==0.115.0" src/app/requirements.txt && grep -q "databricks-sdk==0.33.0" src/app/requirements.txt && grep -q "pydantic-core==2.46.4" requirements.lock && test -x scripts/build_wheelhouse.sh && grep -q "wheelhouse/" .gitignore && echo ok</verify>
  <acceptance_criteria>
    - requirements.txt fully pinned to the 6 resolved versions; requirements-dev.txt pins pytest.
    - requirements.lock lists the full transitive closure (incl. pydantic-core==2.46.4, starlette==0.38.6, etc.).
    - build_wheelhouse.sh is executable and documents the pip download + --no-index install; wheelhouse/ gitignored (no wheels committed).
  </acceptance_criteria>
</task>

---

## Wave 4: documentation

<task type="auto">
  <name>Write README + docs/DEPLOY + docs/PERMISSIONS + docs/OFFLINE</name>
  <wave>4</wave>
  <files>README.md, docs/DEPLOY.md, docs/PERMISSIONS.md, docs/OFFLINE.md</files>
  <read_first>
    - .planning/PROJECT.md, .planning/phases/5-CONTEXT.md (§D/§E)
    - resources/app.yml, resources/grants.sql, databricks.yml (accurate names/ids)
    - reference the app structure under src/app/
  </read_first>
  <action>
    - README.md: what the app is (Daily E-File at a Glance, IRS); architecture (server-rendered FastAPI
      + Jinja2 + vendored USWDS + vanilla JS; OBO reads via user token auth_type=pat; audit writes as
      app SP; synthetic gold table irs.efile.daily_efile_glance; audit irs.efile.download_audit); the
      feature set (table, search/report-date/DRAIN filters, gated CSV/Excel download with
      acknowledgement + justification + audit, kill switch); repo layout; links to docs/.
    - docs/DEPLOY.md: bundle validate/deploy/run (standard engine), app restart + slow-venv note,
      group creation (efile_glance_app_users 2120470953002429 / efile_glance_download_users
      2123868542399307) + add-member SCIM patch, applying resources/grants.sql via Statement Execution,
      the EFILE_DOWNLOADS_ENABLED kill switch, staging promotion notes.
    - docs/PERMISSIONS.md: OBO model (X-Forwarded-Access-Token, auth_type=pat, UC-enforced reads),
      download gating (group membership via me() + kill switch, re-checked server-side, never fail
      open), audit (SP write, audit-first, one row per download, app-log line, only-non-native events),
      BEARS 1:1 group mapping, and the account-level federation FOLLOW-UP for the app_users SELECT grant.
    - docs/OFFLINE.md: air-gap story — pinned requirements.txt + requirements.lock + build_wheelhouse.sh
      + `pip install --no-index --find-links wheelhouse`; vendored USWDS 3.13.0 + local logo/app.css;
      zero CDN; how to swap the official IRS logo asset.
    No secrets/tokens in any doc; UC refs 3-level.
  </action>
  <verify>test -f README.md && test -f docs/DEPLOY.md && test -f docs/PERMISSIONS.md && test -f docs/OFFLINE.md && grep -qi "kill switch\|EFILE_DOWNLOADS_ENABLED" docs/DEPLOY.md && grep -qi "wheelhouse" docs/OFFLINE.md && grep -qi "account" docs/PERMISSIONS.md && echo ok</verify>
  <acceptance_criteria>
    - All four docs exist and are accurate to the built app (names/ids/flows correct, 3-level tables).
    - DEPLOY covers deploy+groups+grants+kill switch; PERMISSIONS covers OBO+gating+audit+BEARS+the federation follow-up; OFFLINE covers pinned deps+wheelhouse+vendored assets+logo swap.
    - No secrets/tokens in docs.
  </acceptance_criteria>
</task>

---

## Wave 5: tests (kill switch + guard tests)

<task type="auto">
  <name>Test downloads_enabled + guard against OCFO and external URLs</name>
  <wave>5</wave>
  <files>tests/test_config.py, tests/test_branding_guards.py</files>
  <read_first>
    - src/app/config.py; tests/test_auth.py (style)
    - .planning/phases/5-PLAN.md (LOCKED L1/L2/L5)
  </read_first>
  <action>
    - tests/test_config.py: downloads_enabled truth table (None/absent→True; "true"/"TRUE"/"1"/"yes"→
      True; "false"/"0"/"no"/"off"/""/" "→False).
    - tests/test_branding_guards.py (repo-hygiene, pure file reads — no app import):
      * assert NO case-insensitive "ocfo" appears under src/ or resources/ (walk files, skip binary).
      * assert NO external URL (http(s)://, unpkg, jsdelivr, cdnjs) in src/app/templates/*.html or
        src/app/static/css/*.css or src/app/static/js/*.js (USWDS min files may contain URLs in
        comments/source maps — restrict the scan to OUR authored files: templates, static/css, static/js;
        do NOT scan static/uswds).
      Resolve paths relative to the repo root from the test file location.
  </action>
  <verify>cd /Users/greg.skinner/Documents/IRS/download_hub && PYTHONPATH=src .venv/bin/python -m pytest tests/ -q</verify>
  <acceptance_criteria>
    - All tests pass (prior 66 + new). downloads_enabled truth table covered.
    - Guard test fails if any OCFO reference is reintroduced under src/ or resources/.
    - URL guard scans only our authored files (templates, static/css, static/js) — not vendored USWDS — and passes.
  </acceptance_criteria>
</task>

---

## Checkpoint: redeploy, verify branding + kill switch + OCFO-gone

<task type="checkpoint:human">
  <name>Deploy, verify IRS branding renders, kill switch toggles, no OCFO</name>
  <wave>6</wave>
  <action>
    From repo root (branch dbx/download-hub-phase-1):
    1. PYTHONPATH=src .venv/bin/python -m pytest tests/ -q
    2. databricks bundle validate -t dev -p DEFAULT && databricks bundle deploy --target dev -p DEFAULT
    3. databricks bundle run download_hub -t dev -p DEFAULT   (slow venv rebuild)
    4. databricks apps get download-hub -p DEFAULT → ACTIVE/SUCCEEDED
    5. Browser (as greg.skinner): the page shows the IRS logo + "Internal Revenue Service" branding,
       the professional palette, and NO "OCFO" anywhere; the table/filters/download panel still work.
    6. Kill-switch check: set EFILE_DOWNLOADS_ENABLED to "false" (edit app.yaml env → redeploy+run, OR
       set via the app's env in the workspace), reload → download panel gone + POST /download 403;
       set back to "true" → panel returns. (Document the chosen toggle path in DEPLOY.md.)
    7. Confirm app logs show static/css/app.css + static/img/irs-logo.svg served 200.
  </action>
  <acceptance_criteria>
    - pytest passes; deploy+run succeed (standard engine); app ACTIVE.
    - Branding renders (logo + palette), no OCFO visible; table/filters/download still function.
    - Kill switch: "false" hides the panel and 403s /download; "true" restores it.
    - app.css + irs-logo.svg load 200 (local assets).
  </acceptance_criteria>
</task>

---

## Must-Haves
```yaml
truths:
  - Kill switch EFILE_DOWNLOADS_ENABLED (default true) globally disables downloads (panel hidden + /download 403), independent of group membership; pure downloads_enabled() helper, unit-tested.
  - All OCFO references removed from shipping code (src/ + resources/); planning docs left as history.
  - IRS branding via LOCAL vendored logo (swappable placeholder) + local app.css color overlay loaded after USWDS; professional navy palette; WCAG AA; no CDN, no external fonts.
  - glance.html JS contract intact (glance-search/report-date/drain/tbody + hidden report_date/drain/search).
  - Deps pinned (requirements.txt) + full requirements.lock + build_wheelhouse.sh; wheelhouse/ gitignored (no wheels committed); offline install via --no-index.
  - Docs: README + docs/DEPLOY + docs/PERMISSIONS + docs/OFFLINE, accurate, no secrets.
  - No new UC objects, no new bundle resources, no new runtime dependency.
artifacts:
  - src/app/config.py (downloads_enabled), src/app/main.py (wired), src/app/app.yaml (env)
  - src/app/static/img/irs-logo.svg, src/app/static/css/app.css
  - src/app/templates/base.html, glance.html (rebranded), src/app/exports.py (disclaimer), resources/app.yml (description)
  - src/app/requirements.txt (pinned), requirements-dev.txt (pinned), requirements.lock, scripts/build_wheelhouse.sh, .gitignore
  - README.md, docs/DEPLOY.md, docs/PERMISSIONS.md, docs/OFFLINE.md
  - tests/test_config.py, tests/test_branding_guards.py
uc_targets:
  - irs.efile.daily_efile_glance (READ, OBO) ; irs.efile.download_audit (WRITE as SP) — unchanged
```
