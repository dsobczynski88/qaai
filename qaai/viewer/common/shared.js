// Shared viewer utilities, concatenated ahead of each reviewer's script.js.
// Relies on RECORDS / idx / feedback / STORAGE_KEY declared in that script, and
// on each reviewer defining feedbackKey(rec, idx) to extract its record's id.

function escapeHTML(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[c]));
}

function renderRatings() {
  const box = document.getElementById("rating");
  box.innerHTML = [1,2,3,4,5].map(n =>
    `<label><input type="radio" name="rating" value="${n}"><span>${n}</span></label>`
  ).join("");
}

function save() {
  const rec = RECORDS[idx];
  const key = (typeof feedbackKey === "function" ? feedbackKey(rec, idx) : null) || `rec-${idx}`;
  const rating = document.querySelector('input[name="rating"]:checked');
  feedback[key] = {
    rating: rating ? parseInt(rating.value, 10) : null,
    notes: document.getElementById("notes").value || "",
    saved_at: new Date().toISOString(),
  };
  localStorage.setItem(STORAGE_KEY, JSON.stringify(feedback));
}

// ── Shared right-pane + modal + navigation ──
// renderLeft() is reviewer-specific and defined in each script.js; everything
// below is identical across the three viewers. Function declarations hoist
// across the concatenated shared.js + script.js block, but initViewer() is
// only *called* from the end of each script.js — after RECORDS / idx /
// feedback (declared with const/let in script.js) are initialized.
function renderRight() {
  const rec = RECORDS[idx];
  const key = (typeof feedbackKey === "function" ? feedbackKey(rec, idx) : null) || `rec-${idx}`;
  const saved = feedback[key] || {};
  document.getElementById("notes").value = saved.notes || "";
  document.querySelectorAll('input[name="rating"]').forEach(el => el.checked = false);
  if (saved.rating) {
    const el = document.querySelector(`input[name="rating"][value="${saved.rating}"]`);
    if (el) el.checked = true;
  }
  document.getElementById("progress").textContent = `Progress: ${idx+1} / ${RECORDS.length}`;
  document.getElementById("prev-btn").disabled = idx === 0;
  document.getElementById("next-btn").textContent = idx === RECORDS.length - 1 ? "Save" : "Save & Next";
}

function render() { renderLeft(); renderRight(); }

function openModal(bodyHTML) {
  document.getElementById("modal-body").innerHTML = bodyHTML;
  document.getElementById("modal").classList.add("open");
}
function closeModal() { document.getElementById("modal").classList.remove("open"); }

// Wire the nav/export/modal controls and paint the first record. Each
// reviewer's script.js calls this once, at the end, after defining renderLeft.
function initViewer() {
  document.getElementById("prev-btn").addEventListener("click", () => {
    save();
    if (idx > 0) { idx -= 1; render(); }
  });
  document.getElementById("next-btn").addEventListener("click", () => {
    save();
    if (idx < RECORDS.length - 1) { idx += 1; render(); }
  });
  document.getElementById("export-btn").addEventListener("click", () => {
    save();
    const blob = new Blob([JSON.stringify(feedback, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `feedback_{{REVIEW_TYPE}}_{{RUN_KEY}}.json`;
    a.click();
  });
  document.getElementById("modal").addEventListener("click", e => {
    if (e.target.id === "modal") closeModal();
  });
  renderRatings();
  render();
}

// ── Run log ("View log") ──
// The run's problem notes (errored / incomplete / missing-input items) are
// embedded as a JSON array in <script id="LOG">; the button echoes exactly the
// messages the user saw live in the app during the run. Relies on openModal()
// (defined in each reviewer's script.js, hoisted into this same script block).
function readLog() {
  const el = document.getElementById("LOG");
  if (!el) return [];
  try { return JSON.parse(el.textContent || "[]"); } catch (_) { return []; }
}

function openLog() {
  const log = readLog();
  if (!log.length) {
    openModal(`<h3>Run log</h3><p>No issues were recorded — all items completed cleanly.</p>`);
    return;
  }
  const rows = log.map(e => `
    <tr class="log-${escapeHTML(e.level)}">
      <td><span class="log-badge log-badge-${escapeHTML(e.level)}">${escapeHTML(e.level)}</span></td>
      <td class="cited">${escapeHTML(e.item_id ?? "—")}</td>
      <td>${escapeHTML(e.text)}</td>
    </tr>`).join("");
  openModal(`
    <h3>Run log <span style="font-weight:normal;color:var(--mute)">(${log.length} issue${log.length === 1 ? "" : "s"})</span></h3>
    <p style="color:var(--mute);margin:0 0 8px">Items that errored, produced incomplete output, or were missing required input records during this run.</p>
    <table class="detail log-table">
      <thead><tr><th>Level</th><th>Item</th><th>Message</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
  `);
}

// Reveal + wire the "View log" button only when the run logged at least one issue.
(function initViewLog() {
  const btn = document.getElementById("view-log-btn");
  if (!btn) return;
  const log = readLog();
  if (log.length) {
    btn.hidden = false;
    btn.textContent = `View log (${log.length})`;
    btn.addEventListener("click", openLog);
  }
})();

// ── Missing-required-fields warning ──
// The input gate (qaai.agents.shared.gate) marks records review_status ==
// "skipped" with skip_reason / missing_fields when required inputs were absent
// and the graph short-circuited (no review produced). When any record in this
// batch was skipped for that reason, reveal the warning banner near the top and
// wire its "Details" button to a modal listing which fields were missing per
// record. Reads the embedded <script id="DATA"> directly so it does not depend
// on the per-reviewer script.js load order.
function readData() {
  const el = document.getElementById("DATA");
  if (!el) return [];
  try { return JSON.parse(el.textContent || "[]"); } catch (_) { return []; }
}

function recordLabel(rec, i) {
  const r = rec || {};
  const id = (r.requirement && r.requirement.req_id)
    || (r.test_case && r.test_case.test_id)
    || (r.hazard && r.hazard.hazard_id);
  return id || `record ${i + 1}`;
}

function openMissingFields() {
  const skipped = readData()
    .map((rec, i) => ({ rec, i }))
    .filter(({ rec }) => rec && rec.review_status === "skipped");
  const rows = skipped.map(({ rec, i }) => {
    const fields = Array.isArray(rec.missing_fields) ? rec.missing_fields : [];
    const chips = fields.length
      ? fields.map(f => `<code>${escapeHTML(f)}</code>`).join(" ")
      : escapeHTML(rec.skip_reason || "—");
    return `<tr>
      <td class="cited">${escapeHTML(recordLabel(rec, i))}</td>
      <td>${chips}</td>
    </tr>`;
  }).join("");
  openModal(`
    <h3>Missing required fields <span style="font-weight:normal;color:var(--mute)">(${skipped.length} record${skipped.length === 1 ? "" : "s"})</span></h3>
    <p style="color:var(--mute);margin:0 0 8px">These records were skipped because required inputs were absent, so no review was produced.</p>
    <table class="detail">
      <thead><tr><th>Record</th><th>Missing fields</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
  `);
}

(function initMissingWarning() {
  const banner = document.getElementById("missing-warning");
  const btn = document.getElementById("missing-details-btn");
  if (!banner) return;
  const anySkipped = readData().some(rec => rec && rec.review_status === "skipped");
  if (anySkipped) {
    banner.hidden = false;
    if (btn) btn.addEventListener("click", openMissingFields);
  }
})();
