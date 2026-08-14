# Offline / air-gap packaging

The **Data Download Hub** app is built to deploy with **zero external network dependencies** — no CDN, no hotlinked assets, and a reproducible, offline-installable Python dependency set.

## Pinned dependencies

- `src/app/requirements.txt` pins the six direct runtime dependencies to exact
  resolved versions: `fastapi==0.115.0`, `uvicorn==0.30.6`, `jinja2==3.1.6`,
  `databricks-sdk==0.33.0`, `openpyxl==3.1.5`, `python-multipart==0.0.30`.
- `requirements-dev.txt` pins the test dependency (`pytest`).
- `requirements.lock` is the **full transitive closure** (direct deps + every
  resolved sub-dependency, e.g. `starlette==0.38.6`, `pydantic==2.13.4`,
  `pydantic-core==2.46.4`, `cryptography==48.0.0`, …). It is the offline mirror
  source and is regenerated from a clean `pip freeze`.

## The wheelhouse ships committed (no mirror needed)

`src/app/wheelhouse/` (linux/CPython-3.11 wheels for the full `requirements.lock`)
**is committed to this repo**, so the target installs the app's Python
dependencies from local files — **no PyPI, no internal mirror, no network**:

```bash
pip install --no-index --find-links src/app/wheelhouse -r requirements.lock
```

This is the only step that touches a package installer, and it reads local wheel
files only. At runtime the app needs no installer at all.

**Why it lives under `src/app/`:** the Databricks Apps bundle syncs only the app
source directory (`source_code_path: ../src/app`). Keeping the wheelhouse inside
that directory means it is uploaded with the app, so the Apps build can install
from it — see the Databricks Apps section below.

## How the Databricks Apps build installs offline (verified)

`src/app/requirements.txt` begins with two pip directives:

```
--no-index
--find-links ./wheelhouse
```

The Apps build runs `pip install -r requirements.txt` from the deployed app
source root, so `./wheelhouse` resolves to the synced wheels. Confirmed from a
live deploy's build log:

```
[BUILD] Looking in links: /app/python/source_code/./wheelhouse
[BUILD] [INFO] Requirements installed successfully.
```

No index is contacted (`--no-index`), and the relative `./wheelhouse` resolves
against the app source root — no absolute container path needed, so it is
portable across workspaces. Paths in `requirements.txt` must be hard-coded
(Databricks Apps does not expand env-var references there).

## Rebuilding the wheelhouse (only after a dependency change)

`scripts/build_wheelhouse.sh` re-downloads every wheel in `requirements.lock` into
`src/app/wheelhouse/`, targeting the Databricks Apps runtime (linux, CPython 3.11,
wheels only). Run it on any networked host, then commit the refreshed directory:

```bash
./scripts/build_wheelhouse.sh            # download wheels into src/app/wheelhouse/
./scripts/build_wheelhouse.sh --install  # download, then offline-install
```

Under the hood:

```bash
pip download -r requirements.lock -d src/app/wheelhouse/ \
  --platform manylinux2014_x86_64 --python-version 311 --only-binary=:all:
```

## Installing offline (plain host / container, no network)

Ship the repo (the `src/app/wheelhouse/` directory travels with it), then install
strictly from it:

```bash
pip install --no-index --find-links src/app/wheelhouse -r requirements.lock
```

`--no-index` guarantees pip makes **no** network call; `--find-links
src/app/wheelhouse` resolves everything from the local mirror.

> **Wheels are committed to git.** `src/app/wheelhouse/` is intentionally **not**
> ignored (see `.gitignore`) so the wheels travel with the repo and the target
> installs with no package index or mirror. Rebuild them from
> `requirements.lock` with `scripts/build_wheelhouse.sh`, then commit the
> refreshed directory.

## Vendored front-end assets — no CDN

Every front-end asset is served from a local `/static/...` path:

- **USWDS 3.13.0** is vendored under `src/app/static/uswds/` (CSS, JS, fonts,
  icons). No unpkg/jsdelivr/cdnjs.
- The color overlay `src/app/static/css/app.css` loads **after** `uswds.min.css`
  and references only local assets and CSS custom properties (no external fonts).
- Interactivity is one local file, `src/app/static/js/app.js` — vanilla JS, no
  framework, no npm, no remote calls.

## Swapping in your organization's logo

The header logo is a self-contained placeholder SVG at `src/app/static/img/logo.svg`. To use your organization's asset, **replace that file in place** — same path, same filename `logo.svg`. No template or CSS change is needed; `base.html` references it as `<img src="/static/img/logo.svg">`. Keep the replacement self-contained (no external references) to preserve the air-gap guarantee.

Alternatively, set `APP_LOGO` in `src/app/app.yaml` to a different `/static/...` path if you want to use a different image file.
