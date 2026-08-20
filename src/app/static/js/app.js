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
    if (extra) {
      Object.keys(extra).forEach(function (k) {
        params.set(k, extra[k]);
      });
    }
    return params.toString();
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
    showSpinner();
    try {
      var resp = await fetch(url, { headers: { Accept: "text/html" } });
      tbody.innerHTML = await resp.text(); // swap the server-rendered fragment
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

  function showDownloadError(msg) {
    if (dlErrorText) dlErrorText.textContent = msg;
    if (dlError) dlError.hidden = false;
  }
  function clearDownloadError() {
    if (dlError) dlError.hidden = true;
    if (dlErrorText) dlErrorText.textContent = "";
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

  // Initialize the pager from the server-rendered page-1 data attributes.
  if (pager) {
    var tr = parseInt(pager.getAttribute("data-total-rows") || "0", 10);
    var tp = parseInt(pager.getAttribute("data-total-pages") || "1", 10);
    var pg = parseInt(pager.getAttribute("data-page") || "1", 10);
    currentPage = pg;
    drawPager(tr, tp, pg);
  }

  syncDownloadFields(); // initialize the hidden fields on load
})();
