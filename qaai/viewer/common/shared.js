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
