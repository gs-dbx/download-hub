# Migrating the Data Download Hub to another environment

This app is built to move into an air-gapped environment and run **without a
package index or mirror**. The Python wheels ship **committed** in
`src/app/wheelhouse/` (linux / CPython 3.11), so the only install step reads
local files.

---

## What "no package manager" means here

- **Runtime:** none needed. The app just runs `uvicorn main:app` against an already-populated virtualenv. No pip, no npm, no internet.
- **Front end:** fully self-contained — USWDS CSS/JS, all fonts, the logo SVG, `app.css`, and `app.js` are committed local assets. No CDN, ever.
- **Install (one step):** `pip` is used once to populate the venv, but with `--no-index --find-links src/app/wheelhouse` it reads the committed wheels only — it never contacts PyPI or a mirror. There is **no Node/npm toolchain** anywhere.

---

## What to push

Push the whole repo (git already excludes `.venv/`, `__pycache__/`). It carries:

| Area | Paths |
|------|-------|
| Bundle | `databricks.yml`, `resources/{app.yml,seed_job.yml,grants.sql}` |
| App | `src/app/**` (FastAPI + pure modules + templates + `static/uswds` + `static/img/logo.svg` + `static/css` + `static/js`) |
| Data | `src/sample_report/**`, `src/notebooks/**` (synthetic data generator + seed) |
| Deps | `src/app/requirements.txt`, `requirements.lock`, **`src/app/wheelhouse/`** (committed wheels, synced with the app), `scripts/build_wheelhouse.sh` |
| Docs/tests | `README.md`, `docs/**`, `tests/**` |

`.planning/**` is internal planning history — optional; omit for a lean deliverable.

---

## Deploying to a plain host / container (you control the shell)

Fully offline, no mirror:

```bash
python3.11 -m venv .venv && . .venv/bin/activate
pip install --no-index --find-links src/app/wheelhouse -r requirements.lock   # local wheels only
cd src/app
DATABRICKS_HOST=... DATABRICKS_WAREHOUSE_ID=... APP_CATALOG=main APP_SCHEMA=default \
  uvicorn main:app --host 0.0.0.0 --port 8000
```

The app authenticates to the workspace with the injected app-SP OAuth env
(`DATABRICKS_CLIENT_ID`/`SECRET`) and honours the user's `X-Forwarded-Access-Token`
for OBO reads — those are internal workspace calls, not public internet.

---

## Deploying to Databricks Apps in the target workspace

The bundle retargets cleanly. In `databricks.yml`, set the target's `workspace.host` and the `catalog` / `schema` / `warehouse_id` variables, then:

```bash
databricks bundle deploy --target <env>
databricks bundle run metrics_seed -t <env>   # seed report_config, download_audit, daily_metrics
databricks bundle run download_hub -t <env>   # start the app
```

Then recreate the non-code pieces in the new workspace:

1. **Groups** — create `download_hub_app_users` and `download_hub_download_users`; add the intended members (see `docs/DEPLOY.md`).
2. **Grants** — `resources/grants.sql`. **Update the service-principal client ID** first: every workspace assigns the app a different service principal. After deploy, read it from `databricks apps get download-hub` (`service_principal_client_id`) and replace all occurrences of `<APP-SERVICE-PRINCIPAL-CLIENT-ID>` in `grants.sql`, then apply the statements.
3. **Account-level group federation** — if UC rejects a group grant with `PRINCIPAL_DOES_NOT_EXIST`, the group must exist/federate at the **account level** (not just the workspace SCIM). See `docs/PERMISSIONS.md`.

### How the Databricks Apps build installs offline (no infra decision needed)

Databricks Apps auto-runs `pip install -r requirements.txt` during its build. This
repo makes that build fully offline with **no target-side infra config**: the
committed wheels live at `src/app/wheelhouse/` (inside the synced app source), and
`src/app/requirements.txt` begins with:

```
--no-index
--find-links ./wheelhouse
```

The Apps build runs pip from the deployed app source root, so `./wheelhouse`
resolves to the synced wheels. Verified from a live deploy's build log:

```
[BUILD] Looking in links: /app/python/source_code/./wheelhouse
[BUILD] [INFO] Requirements installed successfully.
```

No index is contacted and no absolute container path is required, so it works
unchanged in any target workspace. (Requirement: the wheels match the Apps runtime
— linux `manylinux2014_x86_64` / CPython 3.11 — which `build_wheelhouse.sh`
targets.) If you would rather resolve from an internal mirror instead of the
committed wheels, remove the two directives and point the build at the mirror via
`app.yaml` env (`PIP_INDEX_URL`), which Apps applies during the build phase.

---

## Refreshing the wheelhouse (after a dependency bump)

```bash
PYTHON=python3 ./scripts/build_wheelhouse.sh   # re-downloads into src/app/wheelhouse/
git add src/app/wheelhouse requirements.lock && git commit -m "deps: refresh wheelhouse"
```
Build it on any networked host; it targets linux/CPython-3.11 wheels regardless of
your build host's OS.
