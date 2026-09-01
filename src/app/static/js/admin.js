// admin.js — resource/collection admin console (admin-group only pages).
// Vanilla JS, no framework, no remote assets. Served at /static/js/admin.js.
//
// Two forms:
//   * view-form   -> POST /admin/view   (view_key, title, order, enabled)
//   * report-form -> POST /admin/report (full report; columns/filters assembled
//                    from the picker that "Run query" builds via /admin/preview)
// Edit buttons repopulate each form from data-* attributes on the table rows.
(function () {
  "use strict";

  var root = document.querySelector(".app-admin");
  if (!root) return;
  var dlSuffix = root.getAttribute("data-dl-suffix") || "_dl";

  function byRole(role, ctx) {
    return (ctx || document).querySelector('[data-role="' + role + '"]');
  }

  function escapeHtml(value) {
    var node = document.createElement("div");
    node.textContent = value == null ? "" : String(value);
    return node.innerHTML;
  }

  // ---- Tabs: toggle the active button + show the matching panel ------------
  var tabs = document.querySelectorAll(".app-admintab");
  var panels = document.querySelectorAll("[data-panel]");
  tabs.forEach(function (tab) {
    tab.addEventListener("click", function () {
      var name = tab.getAttribute("data-tab");
      tabs.forEach(function (x) {
        x.classList.toggle("app-admintab--active", x === tab);
      });
      panels.forEach(function (p) {
        p.hidden = p.getAttribute("data-panel") !== name;
      });
    });
  });

  // ---- shared: post a form via fetch, show status; optional reload ---------
  async function postForm(url, form, statusEl, reload) {
    statusEl.textContent = "Saving…";
    statusEl.className = "app-admin__status";
    try {
      var resp = await fetch(url, { method: "POST", body: new FormData(form) });
      var data = {};
      try {
        data = await resp.json();
      } catch (e) {
        /* non-JSON */
      }
      if (resp.ok && data.ok) {
        if (reload === false) {
          statusEl.textContent = "Saved.";
          statusEl.className = "app-admin__status app-admin__status--ok";
        } else {
          statusEl.textContent = "Saved. Reloading…";
          statusEl.className = "app-admin__status app-admin__status--ok";
          setTimeout(function () {
            window.location.reload();
          }, 500);
        }
      } else {
        statusEl.textContent = "Error: " + (data.error || "HTTP " + resp.status);
        statusEl.className = "app-admin__status app-admin__status--err";
      }
    } catch (err) {
      statusEl.textContent = "Network error — could not reach the server.";
      statusEl.className = "app-admin__status app-admin__status--err";
    }
  }

  // ---- System Config: save the disclaimer (no reload) ----------------------
  var configForm = byRole("config-form");
  if (configForm) {
    configForm.addEventListener("submit", function (evt) {
      evt.preventDefault();
      postForm("/admin/config", configForm, byRole("config-status"), false);
    });
  }

  // ===================== Resource collections =============================
  var viewForm = byRole("view-form");
  if (viewForm) {
    viewForm.addEventListener("submit", function (evt) {
      evt.preventDefault();
      postForm("/admin/view", viewForm, byRole("view-status"));
    });
  }
  document.querySelectorAll('[data-role="view-edit"]').forEach(function (btn) {
    btn.addEventListener("click", function () {
      document.getElementById("view-key").value = btn.getAttribute("data-view-key") || "";
      document.getElementById("view-title").value = btn.getAttribute("data-title") || "";
      document.getElementById("view-order").value = btn.getAttribute("data-order") || "1";
      document.getElementById("view-enabled").checked =
        btn.getAttribute("data-enabled") === "true";
      document.getElementById("view-key").focus();
    });
  });
  var viewReset = byRole("view-reset");
  if (viewReset && viewForm) {
    viewReset.addEventListener("click", function () {
      viewForm.reset();
      byRole("view-status").textContent = "";
    });
  }

  // ============================ Reports ====================================
  var reportForm = byRole("report-form");
  if (!reportForm) return;

  var columnsWrap = byRole("columns-wrap");
  var columnsBody = byRole("columns-body");
  var orderSel = byRole("order-by");
  var queryStatus = byRole("query-status");
  var dlGroupInput = byRole("download-group");
  var dlHint = byRole("download-hint");
  var viewSel = document.getElementById("r-view");

  // Show the derived download group as a hint when the collection changes.
  function refreshDownloadHint() {
    var vk = viewSel ? viewSel.value : "";
    if (dlHint) {
      dlHint.textContent = vk
        ? "Leave blank to derive: " + vk + dlSuffix
        : "Leave blank to derive from the collection key.";
    }
  }
  if (viewSel) viewSel.addEventListener("change", refreshDownloadHint);

  // Report type: query reports use the SQL builder; volume reports use a single
  // volume_root field. Toggle which section shows.
  var kindSel = byRole("report-kind");
  var querySection = byRole("query-section");
  var volumeSection = byRole("volume-section");
  var volumeRootInput = byRole("volume-root");
  function applyKind() {
    var isVolume = kindSel && kindSel.value === "volume";
    if (querySection) querySection.hidden = isVolume;
    if (volumeSection) volumeSection.hidden = !isVolume;
  }
  if (kindSel) kindSel.addEventListener("change", applyKind);
  applyKind();

  // Build one picker row for a column, with optional preset state.
  // preset = {show:bool, label:str, format:str, filter:bool} or undefined.
  function makeRow(colName, preset, inferredFormat, sqlType) {
    var tr = document.createElement("tr");
    tr.setAttribute("data-col", colName);
    var show = preset ? preset.show : false;
    var label = preset && preset.label != null ? preset.label : colName;
    var fmt = (preset && preset.format) || inferredFormat || "text";
    var isFilter = preset ? !!preset.filter : false;
    var agg = (preset && preset.agg) || "";

    var fmtOpts = ["text", "int", "float", "pct"]
      .map(function (f) {
        return '<option value="' + f + '"' + (f === fmt ? " selected" : "") + ">" + f + "</option>";
      })
      .join("");
    // Aggregation applied to this column; "" = plain column (grouped, not aggregated).
    var aggOpts = ["", "sum", "min", "avg", "max", "first", "last"]
      .map(function (a) {
        var lbl = a === "" ? "—" : a;
        return '<option value="' + a + '"' + (a === agg ? " selected" : "") + ">" + lbl + "</option>";
      })
      .join("");

    tr.innerHTML =
      '<td><input type="checkbox" class="usa-checkbox__input--inline" data-cell="show"' +
      (show ? " checked" : "") + "></td>" +
      "<td><code>" + escapeHtml(colName) + "</code>" +
      (sqlType ? '<span class="usa-hint display-block">' + escapeHtml(sqlType) + "</span>" : "") +
      "</td>" +
      '<td><input class="usa-input usa-input--inline" data-cell="label" value=""></td>' +
      '<td><select class="usa-select usa-select--inline" data-cell="format">' + fmtOpts + "</select></td>" +
      '<td><select class="usa-select usa-select--inline" data-cell="agg" ' +
      'title="Aggregate this column (injects SUM/MIN/AVG/… + GROUP BY the others)">' + aggOpts + "</select></td>" +
      '<td><input type="checkbox" class="usa-checkbox__input--inline" data-cell="filter"' +
      (isFilter ? " checked" : "") + "></td>";
    // Set the label value via property (avoids HTML-escaping issues).
    tr.querySelector('[data-cell="label"]').value = label;
    return tr;
  }

  // Render the picker from a list of column names + an optional preset map.
  function renderPicker(columns, presetByName) {
    columnsBody.innerHTML = "";
    columns.forEach(function (c) {
      var meta = typeof c === "string" ? { name: c } : c;
      var name = meta.name;
      columnsBody.appendChild(
        makeRow(
          name,
          presetByName ? presetByName[name] : undefined,
          meta.format,
          meta.type
        )
      );
    });
    columnsWrap.hidden = columns.length === 0;

    // Populate the order-by select while preserving its current value.
    [orderSel].forEach(function (sel) {
      if (!sel) return;
      var current = sel.value;
      sel.innerHTML = '<option value="">— none —</option>';
      columns.forEach(function (c) {
        var name = typeof c === "string" ? c : c.name;
        var o = document.createElement("option");
        o.value = name;
        o.textContent = name;
        if (name === current) o.selected = true;
        sel.appendChild(o);
      });
    });
  }

  // "Run query" — preview the query and build the picker from returned columns.
  var runBtn = byRole("run-query");
  if (runBtn) {
    runBtn.addEventListener("click", async function () {
      var q = byRole("report-query").value || "";
      queryStatus.textContent = "Running…";
      queryStatus.className = "app-admin__status";
      var fd = new FormData();
      fd.set("source_query", q);
      fd.set("limit", "50");
      try {
        var resp = await fetch("/admin/preview", { method: "POST", body: fd });
        var data = await resp.json();
        if (!resp.ok || data.error) {
          queryStatus.textContent = "Error: " + (data.error || "HTTP " + resp.status);
          queryStatus.className = "app-admin__status app-admin__status--err";
          return;
        }
        renderPicker(data.column_metadata || data.columns || [], null);
        queryStatus.textContent =
          (data.columns || []).length + " columns, " + (data.rows || []).length + " sample rows.";
        queryStatus.className = "app-admin__status app-admin__status--ok";
      } catch (err) {
        queryStatus.textContent = "Network error — could not run the query.";
        queryStatus.className = "app-admin__status app-admin__status--err";
      }
    });
  }

  // Assemble columns_json + filters_json from the picker on submit.
  function assembleJson() {
    var cols = [];
    var filters = [];
    columnsBody.querySelectorAll("tr").forEach(function (tr) {
      var name = tr.getAttribute("data-col");
      var show = tr.querySelector('[data-cell="show"]').checked;
      var label = tr.querySelector('[data-cell="label"]').value || name;
      var fmt = tr.querySelector('[data-cell="format"]').value || "text";
      var aggSel = tr.querySelector('[data-cell="agg"]');
      var agg = aggSel ? aggSel.value : "";
      var isFilter = tr.querySelector('[data-cell="filter"]').checked;
      if (show) {
        if (agg) {
          // Aggregated column: `source` is this query column; the server derives
          // the output alias (source_agg). GROUP BY covers the plain columns.
          cols.push({ label: label, format: fmt, agg: agg, source: name });
        } else {
          cols.push({ name: name, label: label, format: fmt });
        }
      }
      if (isFilter) filters.push({ field: name, label: label });
    });
    byRole("columns-json").value = JSON.stringify(cols);
    byRole("filters-json").value = JSON.stringify(filters);
  }

  reportForm.addEventListener("submit", function (evt) {
    evt.preventDefault();
    assembleJson();
    postForm("/admin/report", reportForm, byRole("report-status"));
  });

  // Edit an existing report — repopulate the form + picker from data-* attrs.
  function loadReport(btn) {
    document.getElementById("r-report-id").value = btn.getAttribute("data-report-id") || "";
    document.getElementById("r-title").value = btn.getAttribute("data-title") || "";
    byRole("report-query").value = btn.getAttribute("data-source-query") || "";
    document.getElementById("r-order").value = btn.getAttribute("data-display-order") || "1";
    document.getElementById("r-enabled").checked = btn.getAttribute("data-enabled") === "true";
    if (dlGroupInput) dlGroupInput.value = btn.getAttribute("data-download-group") || "";
    if (viewSel) viewSel.value = btn.getAttribute("data-view-key") || "";
    if (kindSel) kindSel.value = btn.getAttribute("data-kind") || "query";
    if (volumeRootInput) volumeRootInput.value = btn.getAttribute("data-volume-root") || "";
    applyKind();
    refreshDownloadHint();

    var cols = [];
    var presetByName = {};
    try {
      JSON.parse(btn.getAttribute("data-columns-json") || "[]").forEach(function (c) {
        // Aggregated columns key the picker by their source query column; plain
        // columns key by name. `agg` restores the per-row aggregation dropdown.
        var key = c.agg ? (c.source || c.name) : c.name;
        cols.push(key);
        presetByName[key] = {
          show: true, label: c.label, format: c.format || "text",
          filter: false, agg: c.agg || "",
        };
      });
      JSON.parse(btn.getAttribute("data-filters-json") || "[]").forEach(function (f) {
        if (!presetByName[f.field]) {
          cols.push(f.field);
          presetByName[f.field] = { show: false, label: f.label, format: "text", filter: true };
        } else {
          presetByName[f.field].filter = true;
        }
      });
    } catch (e) {
      /* ignore malformed stored JSON */
    }
    renderPicker(cols, presetByName);
    // Restore order_by after the picker rebuilt the select.
    if (orderSel) orderSel.value = btn.getAttribute("data-order-by") || "";
    queryStatus.textContent = "Loaded from saved config — Run query to refresh columns.";
    queryStatus.className = "app-admin__status";
    window.scrollTo({ top: reportForm.offsetTop - 20, behavior: "smooth" });
  }
  document.querySelectorAll('[data-role="report-edit"]').forEach(function (btn) {
    btn.addEventListener("click", function () {
      loadReport(btn);
    });
  });

  // "New / clear" — reset the report form + picker.
  var resetBtn = byRole("report-reset");
  if (resetBtn) {
    resetBtn.addEventListener("click", function () {
      reportForm.reset();
      columnsBody.innerHTML = "";
      columnsWrap.hidden = true;
      queryStatus.textContent = "";
      applyKind();
      refreshDownloadHint();
    });
  }

  refreshDownloadHint();
})();
