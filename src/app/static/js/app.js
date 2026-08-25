// app.js — config-driven multi-tab report interactivity (LOCKED DECISIONS L4/L6/L7).
// Vanilla JS, no framework, no npm, no remote assets. Served locally at /static/js/app.js.
//
// Reads the active report id + control refs from stable data hooks and wires
// every control back through the fragment endpoint /report/{id}/table:
//   - search (debounced 250ms), report-date, per-filter selects, row-count size,
//     refresh, and pager — all server-side over the per-user snapshot cache.
//   - the response's X-Total-Rows / X-Total-Pages / X-Page / X-Fetched-At headers
//     redraw the pager + "Last updated" label without polluting the row markup.
//   - the generic download form's hidden date/search + one field per filter stay
//     synced to the live controls (guarded; absent for non-members).
// All controls no-op gracefully when absent.
(function () {
  "use strict";

  // ---- Copy/paste deterrent (whole page; form fields stay usable) ----------
  // CSS sets `user-select:none` on the body and re-enables it on form controls;
  // here we block copy/cut/right-click/drag anywhere OUTSIDE an editable field
  // (so the admin SQL box, justification, etc. still work). This is a deterrent
  // only — screenshots and dev-tools cannot be prevented by any web app.
  function inEditable(t) {
    return !!(t && t.closest && t.closest('input,textarea,select,[contenteditable="true"]'));
  }
  ["copy", "cut", "contextmenu", "dragstart"].forEach(function (ev) {
    document.addEventListener(
      ev,
      function (e) {
        if (!inEditable(e.target)) e.preventDefault();
      },
      { capture: true }
    );
  });

  // ---- View switcher: navigate to the selected view's first report ---------
  var viewSwitcher = document.querySelector('[data-role="view-switcher"]');
  if (viewSwitcher)
    viewSwitcher.addEventListener("change", function () {
      if (viewSwitcher.value) window.location.href = viewSwitcher.value;
    });

  // ---- Warehouse status badge (global) — poll /health/warehouse -------------
  // Serverless warehouses auto-suspend; the badge tells the user whether the
  // first query will be slow (cold start). Degrades to "unknown" on any error.
  var whBadge = document.querySelector('[data-role="wh-badge"]');
  if (whBadge) {
    var whDot = whBadge.querySelector('[data-role="wh-dot"]');
    var whText = whBadge.querySelector('[data-role="wh-text"]');
    var pollWarehouse = function () {
      fetch("/health/warehouse", { headers: { Accept: "application/json" } })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          var status = (d && d.status) || "unknown";
          if (whDot) whDot.className = "app-whstatus__dot app-whstatus__dot--" + status;
          if (whText) whText.textContent = (d && d.label) || "Warehouse status unknown";
          whBadge.setAttribute("data-status", status);
        })
        .catch(function () {
          if (whDot) whDot.className = "app-whstatus__dot app-whstatus__dot--unknown";
          if (whText) whText.textContent = "Warehouse status unavailable";
        });
    };
    pollWarehouse();
    setInterval(pollWarehouse, 30000); // re-check every 30s
  }

  // ---- Navigation overlay (global) — instant feedback on internal links -----
  // A server-rendered page can wait on a cold warehouse, so show a spinner the
  // moment the user navigates via an internal link/submit (not downloads, modal
  // toggles, new-tab, or hash links). Hidden again if the page is restored
  // from the bfcache (pageshow).
  var navOverlay = document.querySelector('[data-role="nav-overlay"]');
  if (navOverlay) {
    var showNav = function () {
      navOverlay.hidden = false;
      navOverlay.setAttribute("aria-hidden", "false");
    };
    document.addEventListener("click", function (e) {
      var a = e.target && e.target.closest ? e.target.closest("a[href]") : null;
      if (!a) return;
      var href = a.getAttribute("href") || "";
      if (
        a.hasAttribute("download") ||
        a.hasAttribute("data-open-modal") ||
        a.hasAttribute("data-close-modal") ||
        a.getAttribute("target") === "_blank" ||
        a.getAttribute("aria-controls") ||
        href.charAt(0) === "#" ||
        href.indexOf("/") !== 0 || // only same-origin absolute paths
        e.metaKey || e.ctrlKey || e.shiftKey || e.altKey
      )
        return;
      showNav();
    });
    window.addEventListener("pageshow", function () {
      navOverlay.hidden = true;
      navOverlay.setAttribute("aria-hidden", "true");
    });
  }

  var container = document.querySelector("[data-report-id]");
  if (!container) return; // report-specific wiring below only runs on report pages
  var reportId = container.getAttribute("data-report-id");

  function byRole(role) {
    return container.querySelector('[data-role="' + role + '"]');
  }

  var searchEl = byRole("report-search");
  var dateEl = byRole("report-date");
  var sizeEl = byRole("report-size");
  var refreshEl = byRole("report-refresh");
  var tbody = byRole("report-tbody");
  var pager = byRole("report-pager");
  var updated = byRole("report-updated");
  var spinner = byRole("report-spinner");
  var tableWrap = byRole("report-table-wrap");
  var filterEls = container.querySelectorAll('[data-role="report-filter"]');

  // Download panel hidden inputs (present only for download-group members).
  var dlDate = document.getElementById("download-date");
  var dlSearch = document.getElementById("download-search");

  var currentPage = 1;
  var sortKey = ""; // active sort column name ("" = server default order)
  var sortDir = "asc"; // "asc" | "desc"

  // Show/hide the loading spinner over the table (guarded — absent is a no-op).
  // A minimum on-screen time keeps the spinner perceptible even when the fetch
  // completes almost instantly (in-memory filter/search/paginate).
  var _spinnerShownAt = 0;
  var _spinnerHideTimer = null;
  var MIN_SPINNER_MS = 350;
  function showSpinner() {
    if (_spinnerHideTimer) {
      clearTimeout(_spinnerHideTimer);
      _spinnerHideTimer = null;
    }
    _spinnerShownAt = Date.now();
    if (spinner) {
      spinner.hidden = false;
      spinner.setAttribute("aria-hidden", "false");
    }
    if (tableWrap) tableWrap.setAttribute("aria-busy", "true");
  }
  function hideSpinner() {
    var elapsed = Date.now() - _spinnerShownAt;
    var wait = Math.max(0, MIN_SPINNER_MS - elapsed);
    _spinnerHideTimer = setTimeout(function () {
      if (spinner) {
        spinner.hidden = true;
        spinner.setAttribute("aria-hidden", "true");
      }
      if (tableWrap) tableWrap.setAttribute("aria-busy", "false");
    }, wait);
  }

  // Keep the download panel's hidden fields synced to the live controls so the
  // export matches what is on screen (LOCKED DECISION L7). Guarded: the panel is
  // absent for non-members, so each assignment is skipped. Loops every live
  // filter and copies its value into the matching hidden input by data-field.
  function syncDownloadFields() {
    if (dlDate && dateEl) dlDate.value = dateEl.value;
    if (dlSearch && searchEl) dlSearch.value = searchEl.value;
    filterEls.forEach(function (sel) {
      var field = sel.getAttribute("data-field");
      if (!field) return;
      var hidden = document.getElementById("download-filter-" + field);
      if (hidden) hidden.value = sel.value;
    });
  }

  function buildQuery(extra) {
    var params = new URLSearchParams();
    if (dateEl) params.set("date", dateEl.value);
    filterEls.forEach(function (sel) {
      var field = sel.getAttribute("data-field");
      if (field) params.set(field, sel.value);
    });
    if (searchEl) params.set("q", searchEl.value || "");
    params.set("page", String(currentPage));
    if (sizeEl) params.set("size", sizeEl.value);
    if (sortKey) {
      params.set("sort", sortKey);
      params.set("dir", sortDir);
    }
    if (extra) {
      Object.keys(extra).forEach(function (k) {
        params.set(k, extra[k]);
      });
    }
    return params.toString();
  }

  // Deep-linkable state: mirror the current controls into the address bar so the
  // view is bookmarkable/shareable and survives a reload. Uses replaceState (no
  // history spam) and excludes transient extras like refresh=1.
  function updateUrl() {
    try {
      window.history.replaceState(
        null,
        "",
        window.location.pathname + "?" + buildQuery()
      );
    } catch (e) {
      /* history API unavailable — non-fatal */
    }
  }

  function drawPager(totalRows, totalPages, page) {
    if (!pager) return;
    pager.setAttribute("data-total-rows", String(totalRows));
    pager.setAttribute("data-total-pages", String(totalPages));
    pager.setAttribute("data-page", String(page));
    if (totalPages <= 1) {
      pager.innerHTML = "";
      return;
    }
    pager.innerHTML =
      '<span class="app-pager__info">Page ' +
      page +
      " of " +
      totalPages +
      " — " +
      totalRows +
      " rows</span>" +
      '<span class="app-pager__controls">' +
      '<button type="button" class="usa-button usa-button--outline" data-page-action="prev"' +
      (page <= 1 ? " disabled" : "") +
      ">Prev</button>" +
      '<button type="button" class="usa-button usa-button--outline" data-page-action="next"' +
      (page >= totalPages ? " disabled" : "") +
      ">Next</button>" +
      "</span>";
    var prev = pager.querySelector('[data-page-action="prev"]');
    var next = pager.querySelector('[data-page-action="next"]');
    if (prev)
      prev.addEventListener("click", function () {
        currentPage = Math.max(1, page - 1);
        refreshFragment();
      });
    if (next)
      next.addEventListener("click", function () {
        currentPage = page + 1;
        refreshFragment();
      });
  }

  async function refreshFragment(extra) {
    if (!tbody) return;
    var url =
      "/report/" +
      encodeURIComponent(reportId) +
      "/table?" +
      buildQuery(extra);
    updateUrl(); // keep the address bar in sync with the current controls
    showSpinner();
    try {
      var resp = await fetch(url, { headers: { Accept: "text/html" } });
      var html = await resp.text();
      // The server returns row fragments even for handled errors (401/403/503).
      // But an UNhandled error would be a non-row body (JSON/HTML) — never dump
      // that into the table; show a clean inline message instead of blanking.
      if (!resp.ok && html.lastIndexOf("<tr", 0) !== 0) {
        html =
          '<tr><td colspan="99">Could not load the data (HTTP ' +
          resp.status +
          "). Try again, or narrow the date/filters.</td></tr>";
      }
      tbody.innerHTML = html; // swap the server-rendered fragment
      var totalRows = parseInt(resp.headers.get("X-Total-Rows") || "0", 10);
      var totalPages = parseInt(resp.headers.get("X-Total-Pages") || "1", 10);
      var page = parseInt(resp.headers.get("X-Page") || "1", 10);
      currentPage = page;
      drawPager(totalRows, totalPages, page);
      var fetchedAt = resp.headers.get("X-Fetched-At");
      if (updated && fetchedAt)
        updated.textContent = "Last updated " + fetchedAt;
      syncDownloadFields(); // keep the export in sync with the new view
    } catch (err) {
      // Network failure — surface a concise inline message, never a blank table.
      tbody.innerHTML =
        '<tr><td colspan="99">Could not reach the server. Check your ' +
        "connection and try again.</td></tr>";
    } finally {
      hideSpinner();
    }
  }

  function resetAndFetch() {
    currentPage = 1;
    refreshFragment();
  }

  // Search: debounce 250ms then reset to page 1 (server-side now — LOCKED L7).
  var searchTimer = null;
  if (searchEl)
    searchEl.addEventListener("input", function () {
      if (searchTimer) clearTimeout(searchTimer);
      searchTimer = setTimeout(resetAndFetch, 250);
    });

  // Date / filter / size changes reset to page 1 and re-fetch.
  if (dateEl) dateEl.addEventListener("change", resetAndFetch);
  filterEls.forEach(function (sel) {
    sel.addEventListener("change", resetAndFetch);
  });
  if (sizeEl) sizeEl.addEventListener("change", resetAndFetch);

  // Refresh evicts the server snapshot (refresh=1) and re-reads OBO.
  if (refreshEl)
    refreshEl.addEventListener("click", function () {
      currentPage = 1;
      refreshFragment({ refresh: "1" });
    });

  // ---- Download form: intercept submit for a spinner + explicit errors ----
  // A plain form POST navigates away and shows a raw browser error page on
  // failure. We POST via fetch instead so we can (a) show the spinner while the
  // export builds, (b) trigger the file download from the returned blob, and
  // (c) render the server's specific error message in the modal rather than a
  // full-page error. All same-origin, no external asset (air-gap safe).
  var dlForm = document.getElementById("download-form");
  var dlSubmit = byRole("download-submit");
  var dlError = byRole("download-error");
  var dlErrorText = byRole("download-error-text");

  var dlReady = byRole("download-ready");
  var dlReadyText = byRole("download-ready-text");
  var dlReadyLink = byRole("download-ready-link");

  function showDownloadError(msg) {
    if (dlErrorText) dlErrorText.textContent = msg;
    if (dlError) dlError.hidden = false;
  }
  function clearDownloadError() {
    if (dlError) dlError.hidden = true;
    if (dlErrorText) dlErrorText.textContent = "";
    if (dlReady) dlReady.hidden = true;
  }

  // Over-cap export was saved to the volume — reveal the "ready" panel with a
  // link to GET /download/retrieve (a normal attachment link, not a blob).
  function showDownloadReady(info) {
    if (dlReadyText) dlReadyText.textContent = info.message || "Your export is ready.";
    if (dlReadyLink) {
      dlReadyLink.href =
        "/download/retrieve?path=" + encodeURIComponent(info.retrieve_path || "");
      if (info.filename) dlReadyLink.setAttribute("download", info.filename);
    }
    if (dlReady) dlReady.hidden = false;
  }

  // Pull the filename the server set in Content-Disposition (fallback given).
  function filenameFromDisposition(header, fallback) {
    if (!header) return fallback;
    var m = /filename="?([^"]+)"?/.exec(header);
    return m ? m[1] : fallback;
  }

  function triggerBlobDownload(blob, filename) {
    var url = URL.createObjectURL(blob);
    var a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    // Revoke on the next tick so the download has started.
    setTimeout(function () {
      URL.revokeObjectURL(url);
    }, 1000);
  }

  // Parse an error body: FastAPI HTTPException -> {"detail": "..."}.
  async function errorMessageFromResponse(resp) {
    try {
      var data = await resp.clone().json();
      if (data && data.detail) return String(data.detail);
    } catch (e) {
      /* not JSON — fall through */
    }
    try {
      var text = await resp.text();
      if (text) return text;
    } catch (e) {
      /* ignore */
    }
    return "The download could not be completed (HTTP " + resp.status + ").";
  }

  if (dlForm) {
    dlForm.addEventListener("submit", async function (evt) {
      evt.preventDefault();
      clearDownloadError();
      syncDownloadFields(); // ensure hidden fields match the live view
      // Loading state ON THE BUTTON — the modal overlay hides the table spinner,
      // so feedback must live inside the modal.
      var _origBtnHTML = dlSubmit ? dlSubmit.innerHTML : "";
      if (dlSubmit) {
        dlSubmit.disabled = true;
        dlSubmit.setAttribute("aria-disabled", "true");
        dlSubmit.innerHTML =
          '<span class="app-btn-spinner" aria-hidden="true"></span> Preparing download…';
      }
      showSpinner();
      try {
        var resp = await fetch(dlForm.action, {
          method: "POST",
          body: new FormData(dlForm),
        });
        if (!resp.ok) {
          showDownloadError(await errorMessageFromResponse(resp));
          return;
        }
        // Over-cap exports return JSON (saved to the volume), not a file blob —
        // show the "ready" panel with a retrieve link and keep the modal open.
        var ctype = resp.headers.get("Content-Type") || "";
        if (ctype.indexOf("application/json") !== -1) {
          var info = await resp.json();
          if (info && info.spilled) {
            showDownloadReady(info);
            return;
          }
        }
        var blob = await resp.blob();
        var filename = filenameFromDisposition(
          resp.headers.get("Content-Disposition"),
          reportId + "_export"
        );
        triggerBlobDownload(blob, filename);
        // Success — close the modal (click any close control).
        var closer = document.querySelector(
          "#download-modal [data-close-modal]"
        );
        if (closer) closer.click();
      } catch (err) {
        showDownloadError(
          "Could not reach the server to build the download. Check your " +
            "connection and try again."
        );
      } finally {
        hideSpinner();
        if (dlSubmit) {
          dlSubmit.disabled = false;
          dlSubmit.removeAttribute("aria-disabled");
          dlSubmit.innerHTML = _origBtnHTML;
        }
      }
    });
  }

  // ---- Click-to-sort column headers ---------------------------------------
  // Headers live in the static thead (only the tbody is swapped by fragments),
  // so we wire them once. Clicking a header sorts server-side over the snapshot;
  // clicking the active header toggles asc/desc. aria-sort drives the ▲/▼ marker.
  var headerBtns = container.querySelectorAll('[data-role="sort-header"]');
  function updateSortIndicators() {
    headerBtns.forEach(function (btn) {
      var th = btn.closest("th");
      if (!th) return;
      var key = btn.getAttribute("data-sort-key");
      if (sortKey && key === sortKey) {
        th.setAttribute("aria-sort", sortDir === "desc" ? "descending" : "ascending");
      } else {
        th.setAttribute("aria-sort", "none");
      }
    });
  }
  headerBtns.forEach(function (btn) {
    btn.addEventListener("click", function () {
      var key = btn.getAttribute("data-sort-key");
      if (sortKey === key) {
        sortDir = sortDir === "asc" ? "desc" : "asc";
      } else {
        sortKey = key;
        sortDir = "asc";
      }
      updateSortIndicators();
      currentPage = 1;
      refreshFragment();
    });
  });

  // ---- "SQL" button: self-controlled overlay showing the CURRENT view's query
  // Not a USWDS modal (that didn't open reliably in this build) — app.js toggles
  // [hidden] on the overlay directly. buildQuery() makes the fetched SQL reflect
  // the live date + filters + sort; the textarea shows "Loading…" then the query.
  // Dismissed by the close button, a backdrop click, or Escape.
  var sqlBtn = byRole("report-sql-btn");
  var sqlModal = byRole("report-sql-modal");
  var sqlText = document.getElementById("report-sql-text");
  var sqlClose = byRole("report-sql-close");
  function openSqlModal() {
    if (!sqlModal) return;
    sqlModal.hidden = false;
    if (sqlText) {
      sqlText.value = "Loading…";
      fetch("/report/" + encodeURIComponent(reportId) + "/sql?" + buildQuery(), {
        headers: { Accept: "application/json" },
      })
        .then(function (r) {
          if (!r.ok) throw new Error("http " + r.status);
          return r.json();
        })
        .then(function (d) {
          sqlText.value = (d && d.sql) || "(no SQL available)";
        })
        .catch(function () {
          sqlText.value = "Could not load the SQL for this view.";
        });
    }
  }
  function closeSqlModal() {
    if (sqlModal) sqlModal.hidden = true;
  }
  if (sqlBtn) sqlBtn.addEventListener("click", openSqlModal);
  if (sqlClose) sqlClose.addEventListener("click", closeSqlModal);
  if (sqlModal)
    sqlModal.addEventListener("click", function (e) {
      if (e.target === sqlModal) closeSqlModal(); // backdrop click (outside panel)
    });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && sqlModal && !sqlModal.hidden) closeSqlModal();
  });

  // ---- Deep-link init: hydrate controls + sort from the URL on first load ---
  // The server already applied ?date= to the initial render; here we restore
  // search/filters/size/sort/page so a shared or reloaded URL shows that exact
  // view. We also pass the URL params straight through on the first fetch so the
  // DATA is correct even when a filter's option isn't in the default snapshot.
  (function initFromUrl() {
    var p = new URLSearchParams(window.location.search);
    var extra = {};
    var hasState = false;
    p.forEach(function (v, k) {
      extra[k] = v;
    });
    if (dateEl && p.has("date")) dateEl.value = p.get("date");
    if (searchEl && p.has("q")) {
      searchEl.value = p.get("q");
      if (p.get("q")) hasState = true;
    }
    if (sizeEl && p.has("size")) sizeEl.value = p.get("size");
    filterEls.forEach(function (sel) {
      var field = sel.getAttribute("data-field");
      if (field && p.has(field)) {
        sel.value = p.get(field);
        if (p.get(field)) hasState = true;
      }
    });
    if (p.has("sort")) {
      sortKey = p.get("sort") || "";
      sortDir = p.get("dir") === "desc" ? "desc" : "asc";
      if (sortKey) hasState = true;
    }
    if (p.has("page")) {
      currentPage = Math.max(1, parseInt(p.get("page"), 10) || 1);
      if (currentPage > 1) hasState = true;
    }
    updateSortIndicators();
    // Only re-fetch when the URL asked for something beyond the server's default
    // page-1 render (date was already honored server-side).
    if (hasState) refreshFragment(extra);
  })();

  syncDownloadFields(); // initialize the hidden fields on load
})();

// ===========================================================================
// Volume browser — a SEPARATE, self-contained module for the volume-report page
// (data-volume-report-id). Independent of the report IIFE above so it never
// interferes with the report/sort/download logic; it no-ops when its container
// is absent. Server-rendered folder/file listing swapped via a fragment endpoint:
//   GET  /volume/{report_id}/list?path=<root-relative subpath>   -> _volume_rows.html
//        (response header X-Volume-Path echoes the canonical subpath)
//   POST /volume/{report_id}/download  (report_id, path, acknowledged, justification)
//        -> file bytes (Content-Disposition) or JSON {detail} error.
// Folder clicks drill in + mirror ?path= to the address bar (replaceState) and the
// page hydrates from ?path= on load. File Download opens the ack/justification modal
// (same pattern as the report download), then fetch-intercepts the submit.
// ===========================================================================
(function () {
  "use strict";

  var vRoot = document.querySelector("[data-volume-report-id]");
  if (!vRoot) return; // only runs on the volume-report page
  var vReportId = vRoot.getAttribute("data-volume-report-id");
  var rootLabel = vRoot.getAttribute("data-volume-root-label") || "Home";

  function vByRole(role) {
    return vRoot.querySelector('[data-role="' + role + '"]');
  }

  var vTbody = vByRole("volume-tbody");
  var vCrumbs = vByRole("volume-breadcrumbs");
  var vSpinner = vByRole("volume-spinner");
  var vWrap = vByRole("volume-table-wrap");
  var vPathInput = vByRole("volume-path");
  var vNameOut = vByRole("volume-download-name");
  var vForm = vByRole("volume-download-form");
  var vSubmit = vByRole("volume-download-submit");
  var vError = vByRole("volume-download-error");
  var vErrorText = vByRole("volume-download-error-text");

  var currentPath = vRoot.getAttribute("data-volume-current-path") || "";

  // ---- Spinner over the listing (own copy; min on-screen time) -------------
  var _vShownAt = 0;
  var _vHideTimer = null;
  var V_MIN_SPINNER_MS = 350;
  function vShowSpinner() {
    if (_vHideTimer) {
      clearTimeout(_vHideTimer);
      _vHideTimer = null;
    }
    _vShownAt = Date.now();
    if (vSpinner) {
      vSpinner.hidden = false;
      vSpinner.setAttribute("aria-hidden", "false");
    }
    if (vWrap) vWrap.setAttribute("aria-busy", "true");
  }
  function vHideSpinner() {
    var wait = Math.max(0, V_MIN_SPINNER_MS - (Date.now() - _vShownAt));
    _vHideTimer = setTimeout(function () {
      if (vSpinner) {
        vSpinner.hidden = true;
        vSpinner.setAttribute("aria-hidden", "true");
      }
      if (vWrap) vWrap.setAttribute("aria-busy", "false");
    }, wait);
  }

  // ---- Small download helpers (local copies; module is self-contained) -----
  function vFilenameFromDisposition(header, fallback) {
    if (!header) return fallback;
    var m = /filename="?([^"]+)"?/.exec(header);
    return m ? m[1] : fallback;
  }
  function vTriggerBlobDownload(blob, filename) {
    var url = URL.createObjectURL(blob);
    var a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(function () {
      URL.revokeObjectURL(url);
    }, 1000);
  }
  async function vErrorMessageFromResponse(resp) {
    try {
      var data = await resp.clone().json();
      if (data && data.detail) return String(data.detail);
    } catch (e) {
      /* not JSON */
    }
    try {
      var text = await resp.text();
      if (text) return text;
    } catch (e) {
      /* ignore */
    }
    return "The download could not be completed (HTTP " + resp.status + ").";
  }
  function vShowError(msg) {
    if (vErrorText) vErrorText.textContent = msg;
    if (vError) vError.hidden = false;
  }
  function vClearError() {
    if (vError) vError.hidden = true;
    if (vErrorText) vErrorText.textContent = "";
  }

  // ---- Deep link: mirror the current folder path into the address bar ------
  function vUpdateUrl() {
    try {
      var qs = currentPath ? "?path=" + encodeURIComponent(currentPath) : "";
      window.history.replaceState(null, "", window.location.pathname + qs);
    } catch (e) {
      /* history API unavailable — non-fatal */
    }
  }

  // ---- Breadcrumbs: rebuilt from the current path on every navigation ------
  // Root crumb + one crumb per path segment; all but the last are drill buttons.
  function vBuildCrumbs(path) {
    if (!vCrumbs) return;
    var parts = path ? path.split("/").filter(Boolean) : [];
    var frag = document.createDocumentFragment();

    function crumb(subpath, label, isCurrent) {
      if (isCurrent) {
        var span = document.createElement("span");
        span.className = "app-breadcrumbs__current";
        span.setAttribute("aria-current", "true");
        span.textContent = label;
        return span;
      }
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "app-crumb";
      btn.setAttribute("data-role", "volume-crumb");
      btn.setAttribute("data-subpath", subpath);
      btn.textContent = label;
      btn.addEventListener("click", function () {
        loadFolder(subpath);
      });
      return btn;
    }
    function sep() {
      var s = document.createElement("span");
      s.className = "app-breadcrumbs__sep";
      s.setAttribute("aria-hidden", "true");
      s.textContent = "/";
      return s;
    }

    frag.appendChild(crumb("", rootLabel, parts.length === 0));
    var acc = "";
    parts.forEach(function (seg, i) {
      acc = acc ? acc + "/" + seg : seg;
      frag.appendChild(sep());
      frag.appendChild(crumb(acc, seg, i === parts.length - 1));
    });
    vCrumbs.innerHTML = "";
    vCrumbs.appendChild(frag);
  }

  // ---- Wire the rows in the current listing fragment -----------------------
  function wireRows() {
    if (!vTbody) return;
    vTbody.querySelectorAll('[data-role="volume-folder"]').forEach(function (el) {
      el.addEventListener("click", function () {
        loadFolder(el.getAttribute("data-subpath") || "");
      });
    });
    // File Download buttons carry data-open-modal (USWDS opens the modal); here we
    // just populate the modal's hidden path + displayed name before it opens.
    vTbody.querySelectorAll('[data-role="volume-file-download"]').forEach(function (btn) {
      btn.addEventListener("click", function () {
        if (vPathInput) vPathInput.value = btn.getAttribute("data-subpath") || "";
        if (vNameOut) vNameOut.textContent = btn.getAttribute("data-name") || "";
        vClearError();
      });
    });
  }

  // ---- Load a folder listing fragment --------------------------------------
  async function loadFolder(path) {
    if (!vTbody) return;
    currentPath = path || "";
    vUpdateUrl();
    vBuildCrumbs(currentPath);
    vShowSpinner();
    try {
      var url =
        "/volume/" +
        encodeURIComponent(vReportId) +
        "/list?path=" +
        encodeURIComponent(currentPath);
      var resp = await fetch(url, { headers: { Accept: "text/html" } });
      if (!resp.ok) {
        vTbody.innerHTML =
          '<tr><td colspan="99">This folder could not be opened. ' +
          "It may have moved, or you may not have access.</td></tr>";
        return;
      }
      vTbody.innerHTML = await resp.text();
      // Honor the server's canonical path (it may normalize/strip the subpath).
      var serverPath = resp.headers.get("X-Volume-Path");
      if (serverPath !== null) {
        currentPath = serverPath;
        vBuildCrumbs(currentPath);
        vUpdateUrl();
      }
      wireRows();
    } catch (err) {
      vTbody.innerHTML =
        '<tr><td colspan="99">Could not reach the server. Check your ' +
        "connection and try again.</td></tr>";
    } finally {
      vHideSpinner();
    }
  }

  // ---- Download form: fetch-intercept for spinner + explicit errors --------
  if (vForm) {
    vForm.addEventListener("submit", async function (evt) {
      evt.preventDefault();
      vClearError();
      var _orig = vSubmit ? vSubmit.innerHTML : "";
      if (vSubmit) {
        vSubmit.disabled = true;
        vSubmit.setAttribute("aria-disabled", "true");
        vSubmit.innerHTML =
          '<span class="app-btn-spinner" aria-hidden="true"></span> Preparing download…';
      }
      try {
        var resp = await fetch(vForm.action, {
          method: "POST",
          body: new FormData(vForm),
        });
        if (!resp.ok) {
          vShowError(await vErrorMessageFromResponse(resp));
          return;
        }
        var blob = await resp.blob();
        var filename = vFilenameFromDisposition(
          resp.headers.get("Content-Disposition"),
          (vNameOut && vNameOut.textContent) || "download"
        );
        vTriggerBlobDownload(blob, filename);
        var closer = document.querySelector(
          "#volume-download-modal [data-close-modal]"
        );
        if (closer) closer.click();
      } catch (err) {
        vShowError(
          "Could not reach the server to build the download. Check your " +
            "connection and try again."
        );
      } finally {
        if (vSubmit) {
          vSubmit.disabled = false;
          vSubmit.removeAttribute("aria-disabled");
          vSubmit.innerHTML = _orig;
        }
      }
    });
  }

  // ---- Init: build crumbs, wire the server-rendered rows, hydrate ?path= ----
  (function vInit() {
    vBuildCrumbs(currentPath);
    wireRows();
    var p = new URLSearchParams(window.location.search);
    if (p.has("path") && p.get("path") !== currentPath) {
      loadFolder(p.get("path"));
    }
  })();
})();
