# Phase 5 Context

**Phase:** Hardening, branding & docs (expanded from roadmap "air-gap packaging, groups & documentation")
**Discussed:** 2026-08-13
**Status:** ready for planning

Scope expanded per user during the session: (a) an operational **download kill switch**, and
(b) **UI branding** — IRS logo, professional colors, remove all OCFO references — in addition to
the roadmap's air-gap packaging + groups/BEARS documentation + docs.

## Locked Decisions

### A. Download kill switch (operational)
- New env var **`EFILE_DOWNLOADS_ENABLED`** (default `"true"`). When set to a falsey value
  (`false/0/no/off`), downloads are globally disabled: `can_download=False` (panel hidden) AND
  `POST /download` returns 403 with a clear "downloads are temporarily disabled" message —
  independent of group membership. Flipping it is a config change in `app.yaml` env + app restart
  (no code change to toggle). Parsing is a PURE helper (`downloads_enabled(env_value)`) in the app,
  unit-tested. `can_download = downloads_enabled AND is_member(...)`.

### B. UI branding / polish
- **Remove ALL OCFO references** from user-facing surfaces: `base.html` logo text
  ("Internal Revenue Service — OCFO" → "Internal Revenue Service"), `exports.py` DISCLAIMER text,
  `resources/app.yml` description, and the `<title>`/headings/PROJECT-facing strings in templates &
  main.py. Planning `.md` files (historical record of the original OCFO-derived spec) are left as-is
  (they document provenance) — only shipping/user-facing text is scrubbed.
- **IRS logo:** vendor a LOCAL SVG at `src/app/static/img/irs-logo.svg` (air-gap: no CDN/hotlink).
  Since the official IRS seal asset isn't in hand, ship a clean, dignified placeholder mark
  (typographic "IRS" badge + "Internal Revenue Service" wordmark) that is TRIVIALLY swappable —
  documented: drop the approved asset at that path to replace. Reference it in the header via
  `<img src="/static/img/irs-logo.svg">`.
- **Professional color palette via a LOCAL overlay** `src/app/static/css/app.css` (loaded AFTER
  uswds.min.css). USWDS compiled dist can't be re-themed via Sass here, so override with a small,
  well-scoped stylesheet: a Treasury/IRS-appropriate palette — deep navy primary
  (~`#0b2265`/`#1a4480`), clean white surfaces, a restrained gold/teal accent, refined header band,
  hero title, and table styling (zebra rows, right-aligned numerics, subtle borders, colored %
  change). Keep it tasteful and accessible (WCAG contrast); no heavy redesign, no external fonts
  (USWDS Public Sans already vendored). Referenced as a LOCAL path only.
- Header becomes a branded band: logo + "Internal Revenue Service" + app name "Daily E-File at a
  Glance". Keep the USWDS gov banner + the "synthetic data — demonstration only" footer.

### C. Air-gap dependency packaging
- **Pin direct deps** in `src/app/requirements.txt` to the resolved runtime versions (captured live
  from the app build): `fastapi==0.115.0`, `uvicorn==0.30.6`, `jinja2==3.1.6`,
  `databricks-sdk==0.33.0`, `openpyxl==3.1.5`, `python-multipart==0.0.30`. Pin
  `requirements-dev.txt` `pytest` too.
- Provide a **fully-pinned transitive lock** `requirements.lock` (the complete closure, from the
  build log: starlette 0.38.6, pydantic 2.13.4, pydantic-core 2.46.4, typing-extensions 4.15.0,
  click 8.4.1, h11 0.16.0, MarkupSafe 2.1.5, requests 2.34.2, google-auth 2.53.0, et-xmlfile 2.0.0,
  anyio 4.13.0, sniffio, idna 3.17, urllib3 2.7.0, certifi, charset-normalizer 3.4.7,
  cryptography 48.0.0, cffi 2.0.0, pycparser, pyasn1 0.6.3, pyasn1-modules 0.4.2,
  annotated-types 0.7.0, typing-inspection 0.4.2) as a reference manifest for the ops mirror.
- Provide `scripts/build_wheelhouse.sh` documenting the `pip download` → wheelhouse →
  `pip install --no-index --find-links` offline flow (target: linux manylinux, cpython 3.11).
  **Do NOT commit binary wheels** to git (platform-specific + heavy); the wheelhouse is built by ops
  from the lock. Document this in the offline note.
- Confirm zero CDN/external URLs anywhere (USWDS + logo + app.css all local).

### D. Groups + BEARS mapping documentation
- Document the two groups (`efile_glance_app_users` id 2120470953002429;
  `efile_glance_download_users` id 2123868542399307), each mapping 1:1 to a BEARS entitlement, and
  the membership→access model (app-users = SELECT on gold via OBO; download-users = download gate).
- Document the KNOWN follow-up: the `efile_glance_app_users` UC SELECT grant failed under GovCloud
  account-level identity federation (SCIM workspace group not UC-resolvable). Remediation: create/
  federate the group at ACCOUNT level, then re-run the 3 app-users GRANTs in `resources/grants.sql`.

### E. Documentation set (repo `docs/` + top-level README)
- `README.md` — what the app is, architecture (server-rendered FastAPI + USWDS + OBO), the 4-phase
  build, how data flows, links to the other docs.
- `docs/DEPLOY.md` — bundle validate/deploy/run, group creation + membership, applying grants,
  app restart, the slow-venv note.
- `docs/PERMISSIONS.md` — OBO (auth_type=pat), group gating + kill switch, audit (SP write,
  audit-first), the BEARS 1:1 model, the account-federation follow-up.
- `docs/OFFLINE.md` — air-gap dependency story: pinned requirements + requirements.lock +
  build_wheelhouse.sh + `--no-index` install; vendored USWDS + logo; no CDN.

### Component / targets / testing / deploy
- All app-side (extends `download-hub`); no new UC objects, no new bundle resources.
- No new runtime dependency (branding uses local CSS/SVG; kill switch is stdlib env parsing).
- **Testing:** pytest for `downloads_enabled` parsing + a test asserting no OCFO string remains in
  shipping code (grep-style) + a test that no template/css references an external URL. Existing 66
  tests stay green. Visual branding verified at the browser checkpoint (SSO-gated).
- **Deploy:** DAB standard engine redeploy + app restart (kill switch env + branding). E2E verified
  on dev; staging promotion documented in DEPLOY.md (not deployed tonight to avoid a 2nd app).

## Open Questions (resolved best-guess; adjust at checkpoint)
- Exact palette / logo styling — best-guess professional navy+accent; user eyeballs at checkpoint and
  can tweak tokens in app.css.
- Whether to scrub OCFO from planning docs — decided NO (historical provenance); shipping text only.
- Committing wheels vs wheelhouse-by-ops — decided: pin + lock + script + doc, ops builds wheelhouse.

## Workspace Scan Summary
- Resolved dep versions captured live from `databricks apps logs download-hub` build output (above).
- OCFO references located: exports.py (disclaimer), base.html (logo text), resources/app.yml
  (description) + 6 historical planning docs. App is ACTIVE/healthy on Phase 4 code.
