// Test catalog client. Reads records from <script id="DATA">, renders a
// searchable / filterable / sortable table, with a per-row I/O modal and
// Markdown / JSON export. Vanilla JS, no dependencies.

const RECORDS = JSON.parse(document.getElementById("DATA").textContent || "[]");

const state = {
  q: "",
  type: new Set(),        // empty set = "all"
  component: new Set(),
  sortKey: "name",
  sortDir: 1,             // 1 asc, -1 desc
};

function escapeHTML(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[c]));
}

function uniqueValues(key) {
  return [...new Set(RECORDS.map(r => r[key] || "unlabeled"))].sort();
}

function fixtureText(rec) {
  return (rec.fixtures || []).map(f => f.name).join(" ");
}

// ── Filtering + sorting ──
function visibleRecords() {
  const q = state.q.trim().toLowerCase();
  let rows = RECORDS.filter(r => {
    if (state.type.size && !state.type.has(r.type || "unlabeled")) return false;
    if (state.component.size && !state.component.has(r.component || "other")) return false;
    if (q) {
      const hay = [
        r.name, r.summary, r.file, r.component, r.type,
        fixtureText(r), r.param_id
      ].join(" ").toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
  const k = state.sortKey;
  rows = rows.slice().sort((a, b) => {
    const av = (k === "fixtures" ? fixtureText(a) : (a[k] ?? "")).toString().toLowerCase();
    const bv = (k === "fixtures" ? fixtureText(b) : (b[k] ?? "")).toString().toLowerCase();
    if (av < bv) return -1 * state.sortDir;
    if (av > bv) return 1 * state.sortDir;
    return 0;
  });
  return rows;
}

// ── Rendering ──
function renderFacet(containerId, key) {
  const container = document.getElementById(containerId);
  const set = state[key];
  container.innerHTML = uniqueValues(key).map(v =>
    `<span class="facet-chip ${set.has(v) ? "active" : ""}" data-val="${escapeHTML(v)}">${escapeHTML(v)}</span>`
  ).join("");
  container.querySelectorAll(".facet-chip").forEach(chip => {
    chip.addEventListener("click", () => {
      const v = chip.getAttribute("data-val");
      set.has(v) ? set.delete(v) : set.add(v);
      renderFacet(containerId, key);
      renderRows();
    });
  });
}

function ioButton(rec, i) {
  const has = rec.example_input != null || rec.example_output != null
    || (rec.fixtures && rec.fixtures.length);
  if (!has) return `<button class="io-btn none" disabled>—</button>`;
  return `<button class="io-btn" data-idx="${i}">view</button>`;
}

function renderRows() {
  const rows = visibleRecords();
  const tbody = document.getElementById("rows");
  const empty = document.getElementById("empty");
  document.getElementById("count").textContent =
    `${rows.length} of ${RECORDS.length} tests`;
  empty.hidden = rows.length > 0;

  tbody.innerHTML = rows.map(rec => {
    const i = RECORDS.indexOf(rec);
    const type = rec.type || "unlabeled";
    const parid = rec.param_id ? `<span class="parid">[${escapeHTML(rec.param_id)}]</span>` : "";
    const curated = rec.curated ? `<span class="curated-flag" title="Curated via @pytest.mark.catalog">★</span>` : "";
    const skip = rec.skip_reason ? `<span class="skip-flag" title="${escapeHTML(rec.skip_reason)}">skip</span>` : "";
    const loc = rec.file ? `${escapeHTML(rec.file)}${rec.line ? ":" + rec.line : ""}` : "";
    const fx = (rec.fixtures || []).slice(0, 6).map(f =>
      `<li><span class="fx-name">${escapeHTML(f.name)}</span></li>`).join("");
    const more = (rec.fixtures || []).length > 6
      ? `<li class="fx-name">+${rec.fixtures.length - 6} more…</li>` : "";
    return `<tr>
      <td><span class="t-name">${escapeHTML(rec.base_name || rec.name)}${parid}</span>${curated}${skip}
          <span class="t-file">${loc}</span></td>
      <td><span class="chip chip-${escapeHTML(type)}">${escapeHTML(type)}</span></td>
      <td>${escapeHTML(rec.component || "other")}</td>
      <td class="t-summary">${escapeHTML(rec.summary || "")}</td>
      <td><ul class="fx-list">${fx || '<li class="muted">—</li>'}${more}</ul></td>
      <td>${ioButton(rec, i)}</td>
    </tr>`;
  }).join("");

  tbody.querySelectorAll(".io-btn[data-idx]").forEach(btn => {
    btn.addEventListener("click", () => openModal(parseInt(btn.getAttribute("data-idx"), 10)));
  });
}

function jsonBlock(value, emptyLabel) {
  if (value == null) return `<p class="muted">${escapeHTML(emptyLabel)}</p>`;
  const text = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  return `<pre>${escapeHTML(text)}</pre>`;
}

// ── Modal ──
function openModal(i) {
  const rec = RECORDS[i];
  const fixtures = (rec.fixtures || []).map(f =>
    `<tr><td class="fx-name">${escapeHTML(f.name)}</td>
         <td>${escapeHTML(f.defined_in || "")}</td>
         <td>${escapeHTML(f.doc || "")}</td></tr>`).join("")
    || `<tr><td colspan="3" class="muted">No fixtures requested</td></tr>`;

  document.getElementById("modal-body").innerHTML = `
    <h3>${escapeHTML(rec.name)}</h3>
    <p class="sub">${escapeHTML(rec.nodeid || "")}</p>
    <p>${escapeHTML(rec.summary || "")}</p>
    ${rec.skip_reason ? `<p class="muted">Skips: ${escapeHTML(rec.skip_reason)}</p>` : ""}

    <h4>Fixtures / where inputs come from</h4>
    <table class="detail">
      <tr><th>Fixture</th><th>Defined in</th><th>What it provides</th></tr>
      ${fixtures}
    </table>

    <h4>Example input${rec.curated ? " (curated)" : rec.param_id ? " (parametrize row)" : ""}</h4>
    ${jsonBlock(rec.example_input, "No literal example input. This test builds inputs from the fixtures above.")}

    <h4>Example output${rec.curated ? " (curated)" : ""}</h4>
    ${jsonBlock(rec.example_output, "No literal example output recorded. Add one with @pytest.mark.catalog(example_output=...).")}
  `;
  document.getElementById("modal").classList.add("open");
}

function closeModal() {
  document.getElementById("modal").classList.remove("open");
}

// ── Export ──
function toMarkdown(rows) {
  const head = "| Test | Type | Component | Summary | Fixtures |\n|---|---|---|---|---|";
  const body = rows.map(r =>
    `| ${(r.name || "").replace(/\|/g, "\\|")} | ${r.type} | ${r.component} | `
    + `${(r.summary || "").replace(/\|/g, "\\|")} | ${fixtureText(r).replace(/\|/g, "\\|")} |`
  ).join("\n");
  return head + "\n" + body + "\n";
}

function download(filename, text, mime) {
  const blob = new Blob([text], { type: mime });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
}

// ── Theme ──
function toggleTheme() {
  const root = document.documentElement;
  const next = root.getAttribute("data-theme") === "dark" ? "light" : "dark";
  root.setAttribute("data-theme", next);
  try { localStorage.setItem("testcatalog-theme", next); } catch (e) {}
}

// ── Wiring ──
function init() {
  try {
    const saved = localStorage.getItem("testcatalog-theme");
    if (saved) document.documentElement.setAttribute("data-theme", saved);
  } catch (e) {}

  document.getElementById("search").addEventListener("input", e => {
    state.q = e.target.value;
    renderRows();
  });

  document.querySelectorAll("th.sortable").forEach(th => {
    th.addEventListener("click", () => {
      const key = th.getAttribute("data-sort");
      if (state.sortKey === key) state.sortDir *= -1;
      else { state.sortKey = key; state.sortDir = 1; }
      document.querySelectorAll("th .arrow").forEach(a => a.textContent = "");
      th.querySelector(".arrow").textContent = state.sortDir === 1 ? "▲" : "▼";
      renderRows();
    });
  });

  document.getElementById("theme-btn").addEventListener("click", toggleTheme);
  document.getElementById("copy-btn").addEventListener("click", () => {
    const md = toMarkdown(visibleRecords());
    navigator.clipboard?.writeText(md).catch(() => {});
    download("test_catalog.md", md, "text/markdown");
  });
  document.getElementById("export-btn").addEventListener("click", () => {
    download("test_catalog.json", JSON.stringify(visibleRecords(), null, 2), "application/json");
  });
  document.getElementById("modal").addEventListener("click", e => {
    if (e.target.id === "modal") closeModal();
  });
  document.addEventListener("keydown", e => { if (e.key === "Escape") closeModal(); });

  renderFacet("type-filters", "type");
  renderFacet("component-filters", "component");
  renderRows();
}

init();
