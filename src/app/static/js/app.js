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

  var container = document.querySelector("[data-report-id]");
  if (!container) return;
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
  var filterEls = container.querySelectorAll('[data-role="report-filter"]');

  // Download panel hidden inputs (present only for download-group members).
  var dlDate = document.getElementById("download-date");
  var dlSearch = document.getElementById("download-search");

  var currentPage = 1;

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
    var resp = await fetch(url, { headers: { Accept: "text/html" } });
    tbody.innerHTML = await resp.text(); // swap the server-rendered fragment
    var totalRows = parseInt(resp.headers.get("X-Total-Rows") || "0", 10);
    var totalPages = parseInt(resp.headers.get("X-Total-Pages") || "1", 10);
    var page = parseInt(resp.headers.get("X-Page") || "1", 10);
    currentPage = page;
    drawPager(totalRows, totalPages, page);
    var fetchedAt = resp.headers.get("X-Fetched-At");
    if (updated && fetchedAt) updated.textContent = "Last updated " + fetchedAt;
    syncDownloadFields(); // keep the export in sync with the new view
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
