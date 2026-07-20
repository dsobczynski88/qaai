// Dataset Studio editor runtime.
//
// Two halves:
//   1. A generic JSON-Schema form renderer. The input pane is built from the
//      projected Pydantic row model's schema, so field coverage follows the live
//      reviewer state with no per-type form code here. Add a field to
//      RTMReviewState and it appears in the editor.
//   2. A CONFIG-driven output pane. Every rubric fact (codes, which may be N-A,
//      which are advisory) comes from the eval spec via CONFIG — there are no
//      rubric literals in this file.
//
// Depends on common/dom.js (escapeHTML, readJSONScript, openModal, closeModal).

const CONFIG = readJSONScript("CONFIG") || {};
const INPUT_SCHEMA = readJSONScript("INPUT_SCHEMA") || {};
const ROWS = readData();

let idx = 0;
let edits = [];                 // EditRecord-shaped, flushed to the server on save
const dirty = new Set();        // row indices with unsaved changes
const reviewed = new Set();     // row indices accepted or edited this session

const DRAFT_KEY = `dataset-studio/${CONFIG.dataset_dir || "unknown"}`;

// Field names rendered as textareas regardless of current length: these hold prose
// or multi-line protocol text in every reviewer schema.
const LONG_TEXT_KEYS = new Set([
  "text", "description", "setup", "steps", "expectedResults", "rationale",
  "assessment", "comments", "acceptance_criteria", "hazard", "harm",
  "hazardous_situation", "hazardous_sequence_of_events", "software_related_causes",
  "risk_control_measures", "demonstration_of_effectiveness",
  "residual_risk_acceptability",
]);

// ── path helpers ───────────────────────────────────────────────────────────
// Paths look like `requirement.text` or `test_cases[0].steps` — the same
// rendering the validator uses for Finding.path, so a finding greps out of edits.log.

function parsePath(path) {
  const parts = [];
  for (const seg of String(path).split(".")) {
    const m = seg.match(/^([^[\]]*)((\[\d+\])*)$/);
    if (!m) { parts.push(seg); continue; }
    if (m[1]) parts.push(m[1]);
    for (const i of (m[2] || "").matchAll(/\[(\d+)\]/g)) parts.push(Number(i[1]));
  }
  return parts;
}

function getPath(obj, path) {
  let cur = obj;
  for (const key of parsePath(path)) {
    if (cur == null) return undefined;
    cur = cur[key];
  }
  return cur;
}

function setPath(obj, path, value) {
  const parts = parsePath(path);
  let cur = obj;
  for (let i = 0; i < parts.length - 1; i++) {
    const key = parts[i];
    if (cur[key] == null) cur[key] = typeof parts[i + 1] === "number" ? [] : {};
    cur = cur[key];
  }
  cur[parts[parts.length - 1]] = value;
}

// ── JSON Schema helpers ────────────────────────────────────────────────────

function deref(schema) {
  let s = schema;
  let guard = 0;
  while (s && s.$ref && guard++ < 20) {
    const key = String(s.$ref).replace("#/$defs/", "");
    s = (INPUT_SCHEMA.$defs || {})[key];
  }
  return s || {};
}

// Optional fields arrive as anyOf:[X, {type:"null"}]; unwrap to X so the form shows
// the real editor rather than a union placeholder.
function unwrapNullable(schema) {
  const s = deref(schema);
  if (Array.isArray(s.anyOf)) {
    const real = s.anyOf.filter(x => deref(x).type !== "null");
    if (real.length === 1) return { ...deref(real[0]), title: s.title || deref(real[0]).title };
  }
  return s;
}

function schemaKind(schema) {
  const s = unwrapNullable(schema);
  if (Array.isArray(s.enum) || Array.isArray(s.const)) return "enum";
  if (s.type === "array") return "array";
  if (s.type === "object" || s.properties) return "object";
  if (s.type === "boolean") return "boolean";
  if (s.type === "integer" || s.type === "number") return "number";
  return "string";
}

function labelFor(key, schema) {
  const s = unwrapNullable(schema);
  return s.title || key.replace(/_/g, " ");
}

// ── generic form rendering ─────────────────────────────────────────────────
// Every control carries data-path; a single delegated listener reads it back.

function fieldId(path) { return "f-" + String(path).replace(/[^\w]/g, "_"); }

function renderField(key, schema, value, path) {
  const s = unwrapNullable(schema);
  const kind = schemaKind(s);
  const label = escapeHTML(labelFor(key, s));

  if (kind === "object") return renderObject(s, value || {}, path, label);
  if (kind === "array") return renderArray(s, Array.isArray(value) ? value : [], path, label);

  let control;
  if (kind === "enum") {
    const opts = (s.enum || [s.const]).map(o =>
      `<option value="${escapeHTML(o)}"${o === value ? " selected" : ""}>${escapeHTML(o)}</option>`
    ).join("");
    control = `<select data-path="${escapeHTML(path)}" data-kind="enum">${opts}</select>`;
  } else if (kind === "boolean") {
    control = `<label class="inline-check"><input type="checkbox" data-path="${escapeHTML(path)}"
      data-kind="boolean"${value ? " checked" : ""}> ${label}</label>`;
    return `<div class="field field-bool">${control}</div>`;
  } else if (kind === "number") {
    control = `<input type="number" data-path="${escapeHTML(path)}" data-kind="number"
      value="${escapeHTML(value ?? "")}">`;
  } else {
    const text = value == null ? "" : String(value);
    const multiline = LONG_TEXT_KEYS.has(key) || text.includes("\n") || text.length > 80;
    control = multiline
      ? `<textarea data-path="${escapeHTML(path)}" data-kind="string" rows="${Math.min(14, Math.max(3, text.split("\n").length + 1))}">${escapeHTML(text)}</textarea>`
      : `<input type="text" data-path="${escapeHTML(path)}" data-kind="string" value="${escapeHTML(text)}">`;
  }
  return `<div class="field">
    <label for="${fieldId(path)}">${label}</label>
    ${control}
  </div>`;
}

function renderObject(schema, value, path, label) {
  const s = unwrapNullable(schema);
  const props = s.properties || {};
  const body = Object.keys(props)
    .map(k => renderField(k, props[k], (value || {})[k], path ? `${path}.${k}` : k))
    .join("");
  if (!path) return body;  // top level: no wrapping fieldset
  return `<fieldset class="group"><legend>${label}</legend>${body}</fieldset>`;
}

function renderArray(schema, items, path, label) {
  const s = unwrapNullable(schema);
  const itemSchema = unwrapNullable(s.items || {});
  const rows = items.map((item, i) => `
    <div class="array-item">
      <div class="array-item-head">
        <span class="array-index">${i + 1}</span>
        <button type="button" class="mini danger" data-action="remove-item"
                data-path="${escapeHTML(path)}" data-index="${i}">Remove</button>
      </div>
      ${renderField(String(i), itemSchema, item, `${path}[${i}]`)}
    </div>`).join("");
  return `<fieldset class="group array">
    <legend>${label} <span class="count">(${items.length})</span></legend>
    ${rows || `<p class="empty">None.</p>`}
    <button type="button" class="mini" data-action="add-item"
            data-path="${escapeHTML(path)}">+ Add</button>
  </fieldset>`;
}

// A blank instance for "+ Add", built from the item schema so a new test case comes
// out with the right keys rather than an empty object the validator will reject.
function blankFor(schema) {
  const s = unwrapNullable(schema);
  const kind = schemaKind(s);
  if (kind === "object") {
    const out = {};
    for (const [k, sub] of Object.entries(s.properties || {})) out[k] = blankFor(sub);
    return out;
  }
  if (kind === "array") return [];
  if (kind === "boolean") return false;
  if (kind === "number") return null;
  if (kind === "enum") return (s.enum || [""])[0];
  return "";
}

function schemaAtPath(path) {
  let s = INPUT_SCHEMA;
  for (const key of parsePath(path)) {
    s = unwrapNullable(s);
    s = typeof key === "number" ? unwrapNullable(s.items || {}) : unwrapNullable((s.properties || {})[key] || {});
  }
  return s;
}

// ── output pane (spec-driven, not schema-driven) ───────────────────────────

function verdictOptions(code, current) {
  const opts = [CONFIG.labels.positive, CONFIG.labels.negative];
  if ((CONFIG.na_allowed || []).includes(code)) opts.push(CONFIG.labels.na);
  return opts.map(o =>
    `<option value="${escapeHTML(o)}"${o === current ? " selected" : ""}>${escapeHTML(o)}</option>`
  ).join("");
}

// Editable list of short strings — citation lists (cited_test_case_ids,
// uncovered_spec_ids, ...) and any other array-of-string field an assessment carries.
// Keyed on the value's shape rather than on field names, so nothing here has to know
// a rubric's vocabulary.
function isStringList(value) {
  return Array.isArray(value) && value.every(v => typeof v === "string");
}

function renderStringList(basePath, values, label) {
  const items = values.map((v, j) => `
    <li>
      <input type="text" data-target="output" data-kind="string"
             data-path="${escapeHTML(basePath + "[" + j + "]")}" value="${escapeHTML(v)}">
      <button type="button" class="mini" data-action="remove-item"
              data-target="output" data-path="${escapeHTML(basePath)}"
              data-index="${j}" title="Remove">&times;</button>
    </li>`).join("");
  return `
    <div class="strlist">
      <span class="strlist-label">${escapeHTML(label)}</span>
      <ul>${items || `<li class="muted">none</li>`}</ul>
      <button type="button" class="mini" data-action="add-item"
              data-target="output" data-path="${escapeHTML(basePath)}">+ add</button>
    </div>`;
}

// Everything on the assessment that is neither the overall verdict nor the rubric list
// (comments, clarification_questions, requirement, ...). Without this the reviewer can
// correct a verdict but not the prose the report shows next to it.
function renderAssessmentExtras(row) {
  const parts = CONFIG.verdict_path.split(".");
  const rootPath = parts.slice(0, -1).join(".");
  const verdictLeaf = parts[parts.length - 1];
  const rubricLeaf = (CONFIG.rubric_list_path || "").split(".").pop();
  const assessment = rootPath ? getPath(row.output, rootPath) : row.output;
  if (!assessment || typeof assessment !== "object") return "";

  const blocks = Object.keys(assessment).filter(k => k !== verdictLeaf && k !== rubricLeaf)
    .map(key => {
      const value = assessment[key];
      const path = rootPath ? `${rootPath}.${key}` : key;
      if (typeof value === "string") {
        return `<div class="field"><label>${escapeHTML(key)}</label>
          <textarea data-target="output" data-kind="string" rows="3"
                    data-path="${escapeHTML(path)}">${escapeHTML(value)}</textarea></div>`;
      }
      if (isStringList(value)) return renderStringList(path, value, key);
      // Structured values (nested objects, mixed lists) are shown so nothing is hidden,
      // but editing them safely needs a schema this pane does not have.
      return `<div class="field"><label>${escapeHTML(key)} <span class="muted">(read-only)</span></label>
        <pre class="ro">${escapeHTML(JSON.stringify(value, null, 2))}</pre></div>`;
    }).join("");

  if (!blocks) return "";
  return `<details class="extras"><summary>Assessment fields</summary>${blocks}</details>`;
}

function renderOutputForm(row) {
  const cells = getPath(row.output, CONFIG.rubric_list_path) || [];
  const byCode = new Map(cells.map((c, i) => [String(c[CONFIG.code_field]), { cell: c, i }]));
  const overall = row.label[CONFIG.verdict_key] ?? getPath(row.output, CONFIG.verdict_path);

  // Deliberately no N-A: every reviewer's overall verdict is a plain Yes/No
  // (derive_overall_verdict never returns N-A), so offering it would write a value
  // the models reject.
  const overallOpts = [CONFIG.labels.positive, CONFIG.labels.negative].map(o =>
    `<option value="${escapeHTML(o)}"${o === overall ? " selected" : ""}>${escapeHTML(o)}</option>`
  ).join("");

  const rows = (CONFIG.codes || []).map(code => {
    const hit = byCode.get(code);
    if (!hit) {
      return `<tr class="absent"><td class="cited">${escapeHTML(code)}</td>
        <td colspan="2"><span class="muted">not in this row</span></td></tr>`;
    }
    const { cell, i } = hit;
    const advisory = (CONFIG.advisory_codes || []).includes(code);
    const base = `${CONFIG.rubric_list_path}[${i}]`;
    const verdict = cell[CONFIG.verdict_field];
    const hasRationale = "rationale" in cell || "assessment" in cell;
    const rationaleKey = "assessment" in cell ? "assessment" : "rationale";
    const showPartial = "partial" in cell;

    return `<tr class="${advisory ? "recommended" : ""}">
      <td class="cited">${escapeHTML(code)}${advisory ? '<br><span class="muted">advisory</span>' : ""}</td>
      <td>
        <select data-target="output" data-path="${escapeHTML(base + "." + CONFIG.verdict_field)}"
                data-kind="enum" data-code="${escapeHTML(code)}" class="verdict-select">
          ${verdictOptions(code, verdict)}
        </select>
        ${showPartial ? `<label class="inline-check"><input type="checkbox" data-target="output"
            data-kind="boolean" data-path="${escapeHTML(base + ".partial")}"
            ${cell.partial ? "checked" : ""}> partial</label>` : ""}
      </td>
      <td>${hasRationale
        ? `<textarea data-target="output" data-kind="string" rows="2"
             data-path="${escapeHTML(base + "." + rationaleKey)}">${escapeHTML(cell[rationaleKey] ?? "")}</textarea>`
        : `<span class="muted">&mdash;</span>`}
        ${Object.keys(cell).filter(k => isStringList(cell[k]))
            .map(k => renderStringList(`${base}.${k}`, cell[k], k)).join("")}</td>
    </tr>`;
  }).join("");

  return `
    <div class="field">
      <label>Overall verdict</label>
      <select id="overall-verdict" data-kind="enum" class="verdict-select">${overallOpts}</select>
    </div>
    <table class="findings">
      <thead><tr><th>Cell</th><th>Verdict</th><th>Rationale &amp; citations</th></tr></thead>
      <tbody>${rows || `<tr><td colspan="3" class="muted">No rubric cells in this row.</td></tr>`}</tbody>
    </table>
    ${renderAssessmentExtras(row)}`;
}

// Mirrors qaai.dataset_studio.rules.derive_overall_verdict. Advisory codes and the
// N-A pass rule both come from CONFIG, so this cannot drift from the server.
function deriveVerdict(row) {
  const cells = getPath(row.output, CONFIG.rubric_list_path) || [];
  const byCode = new Map(cells.map(c => [String(c[CONFIG.code_field]), c]));
  const mandatory = (CONFIG.mandatory_codes || []).filter(c => byCode.has(c));
  if (!mandatory.length) return null;
  const passing = new Set([CONFIG.labels.positive]);
  if (CONFIG.na_counts_as_pass) passing.add(CONFIG.labels.na);
  return mandatory.every(c => passing.has(byCode.get(c)[CONFIG.verdict_field]))
    ? CONFIG.labels.positive : CONFIG.labels.negative;
}

function renderDerived(row) {
  const box = document.getElementById("derived-verdict");
  const derived = deriveVerdict(row);
  const stated = row.label[CONFIG.verdict_key];
  if (derived == null) { box.innerHTML = ""; box.className = "derived"; return; }
  const agree = derived === stated;
  box.className = "derived " + (agree ? "agree" : "disagree");
  box.innerHTML = agree
    ? `Derived from the cells: <strong>${escapeHTML(derived)}</strong> &mdash; consistent.`
    : `Derived from the cells: <strong>${escapeHTML(derived)}</strong>, but the overall
       verdict says <strong>${escapeHTML(stated ?? "unset")}</strong>. Fix one of them before saving.`;
}

// ── edit recording ─────────────────────────────────────────────────────────

const FILE_OF = {
  input: "actual_inputs.jsonl",
  output: "actual_outputs.jsonl",
  label: "actual_labels.jsonl",
};

function recordEdit(target, path, before, after) {
  if (JSON.stringify(before) === JSON.stringify(after)) return;
  edits.push({
    action: "edit", index: idx, file: FILE_OF[target], path,
    before: before === undefined ? null : before,
    after: after === undefined ? null : after,
    at: new Date().toISOString(), by: CONFIG.reviewer || "",
  });
  dirty.add(idx);
  markReviewed();
  saveDraft();
  refreshStatus();
}

function markReviewed() {
  reviewed.add(idx);
  const row = ROWS[idx];
  // Provenance, not content: stamped on the row but not logged as individual edits.
  row.label.reviewed_by = CONFIG.reviewer || "";
  row.label.reviewed_at = new Date().toISOString();
}

function applyChange(target, path, value) {
  const row = ROWS[idx];
  const before = getPath(row[target], path);
  setPath(row[target], path, value);
  recordEdit(target, path, before, value);
}

function readControl(el) {
  const kind = el.dataset.kind;
  if (kind === "boolean") return el.checked;
  if (kind === "number") return el.value === "" ? null : Number(el.value);
  return el.value;
}

// ── drafts ─────────────────────────────────────────────────────────────────

function saveDraft() {
  try {
    localStorage.setItem(DRAFT_KEY, JSON.stringify({
      rows: ROWS, edits, reviewed: [...reviewed], at: new Date().toISOString(),
    }));
  } catch (_) { /* quota — the server is the real store */ }
}

function clearDraft() {
  try { localStorage.removeItem(DRAFT_KEY); } catch (_) {}
}

// ── rendering ──────────────────────────────────────────────────────────────

function render() {
  const row = ROWS[idx];
  document.getElementById("input-form").innerHTML =
    renderObject(INPUT_SCHEMA, row.input, "", "");
  document.getElementById("output-form").innerHTML = renderOutputForm(row);
  document.getElementById("reviewer-note").value = row.label.reviewer_note || "";
  renderDerived(row);

  document.getElementById("prev-btn").disabled = idx === 0;
  document.getElementById("next-btn").disabled = idx === ROWS.length - 1;
  refreshStatus();
}

function refreshStatus() {
  const row = ROWS[idx];
  const id = rowLabel(row, idx);
  document.getElementById("progress").innerHTML =
    `Record <strong>${idx + 1}</strong> / ${ROWS.length} &nbsp;&middot;&nbsp; ${escapeHTML(id)}
     &nbsp;&middot;&nbsp; reviewed ${reviewed.size}/${ROWS.length}`;
  const status = document.getElementById("save-status");
  status.textContent = dirty.size
    ? `${dirty.size} row${dirty.size === 1 ? "" : "s"} with unsaved edits`
    : "";
  status.className = "save-status" + (dirty.size ? " unsaved" : "");
}

function rowLabel(row, i) {
  const inp = row.input || {};
  return (inp.requirement && inp.requirement.req_id)
    || (inp.test_case && inp.test_case.test_id)
    || (inp.hazard && (inp.hazard.hazard_id || inp.hazard["SHA ID Number"]))
    || `record ${i + 1}`;
}

// ── server calls ───────────────────────────────────────────────────────────

async function post(endpoint, extra) {
  const res = await fetch(`${CONFIG.save_url}${endpoint}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-QAAI-Token": CONFIG.token },
    body: JSON.stringify({
      dataset_type: CONFIG.dataset_type,
      dataset_dir: CONFIG.dataset_dir,
      rows: ROWS.map((r, i) => ({ index: i, input: r.input, output: r.output, label: r.label })),
      edits, reviewer: CONFIG.reviewer || "", ...(extra || {}),
    }),
  });
  return { status: res.status, body: await res.json().catch(() => ({})) };
}

function showFindings(title, validation) {
  const findings = (validation && validation.findings) || [];
  if (!findings.length) {
    openModal(`<h3>${escapeHTML(title)}</h3><p>No findings &mdash; the dataset is consistent.</p>`);
    return;
  }
  const rows = findings.map(f => `<tr class="log-${f.severity === "error" ? "error" : "warning"}">
    <td><span class="log-badge log-badge-${f.severity === "error" ? "error" : "warning"}">${escapeHTML(f.code)}</span></td>
    <td class="cited">${f.row == null ? "&mdash;" : f.row + 1}</td>
    <td class="cited">${escapeHTML(f.path || "")}</td>
    <td>${escapeHTML(f.message)}</td>
  </tr>`).join("");
  openModal(`<h3>${escapeHTML(title)}</h3>
    <p class="muted">${validation.errors || 0} error(s), ${validation.warnings || 0} warning(s).</p>
    <table class="detail log-table">
      <thead><tr><th>Check</th><th>Row</th><th>Path</th><th>Message</th></tr></thead>
      <tbody>${rows}</tbody></table>`);
}

async function doSave(endpoint, force) {
  const status = document.getElementById("save-status");
  status.textContent = "Saving…";
  let res;
  try {
    res = await post(endpoint, force ? { force: true } : null);
  } catch (err) {
    status.textContent = "Save failed - is the server still running?";
    status.className = "save-status unsaved";
    return;
  }
  if (res.status === 200) {
    edits = [];
    dirty.clear();
    clearDraft();
    if (res.body.dataset_dir) CONFIG.dataset_dir = res.body.dataset_dir;
    document.getElementById("save-status").textContent =
      `Saved ${res.body.rows} rows to ${res.body.dataset_dir} (${res.body.edits_logged} edits logged)`;
    document.getElementById("save-status").className = "save-status saved";
    const v = res.body.validation;
    if (v && v.warnings) showFindings("Saved, with warnings", v);
  } else if (res.status === 422) {
    showFindings("Not saved - validation errors", res.body.validation || {});
    refreshStatus();
  } else {
    status.textContent = `Save failed (${res.status}): ${escapeHTML(res.body.error || "unknown error")}`;
    status.className = "save-status unsaved";
  }
}

// ── init ───────────────────────────────────────────────────────────────────

function initEditor() {
  if (!ROWS.length) {
    document.getElementById("input-form").innerHTML =
      `<p class="empty">This dataset has no rows yet. Generate them first, then reopen the editor.</p>`;
    document.getElementById("output-form").innerHTML = "";
    return;
  }

  // One delegated listener per pane covers every generated control.
  document.getElementById("input-form").addEventListener("change", e => {
    const el = e.target.closest("[data-path]");
    if (!el || !el.dataset.kind) return;
    applyChange("input", el.dataset.path, readControl(el));
  });

  document.getElementById("input-form").addEventListener("click", e => {
    const btn = e.target.closest("[data-action]");
    if (!btn) return;
    const path = btn.dataset.path;
    const list = getPath(ROWS[idx].input, path) || [];
    const before = JSON.parse(JSON.stringify(list));
    if (btn.dataset.action === "add-item") {
      list.push(blankFor(schemaAtPath(path).items || {}));
    } else if (btn.dataset.action === "remove-item") {
      list.splice(Number(btn.dataset.index), 1);
    }
    setPath(ROWS[idx].input, path, list);
    recordEdit("input", path, before, JSON.parse(JSON.stringify(list)));
    render();
  });

  document.getElementById("output-form").addEventListener("change", e => {
    const el = e.target.closest("[data-path], #overall-verdict");
    if (!el) return;
    if (el.id === "overall-verdict") {
      // The overall verdict lives in two places; keep them in lockstep and log both.
      applyChange("label", CONFIG.verdict_key, el.value);
      applyChange("output", CONFIG.verdict_path, el.value);
    } else {
      applyChange("output", el.dataset.path, readControl(el));
      if (el.dataset.code) {
        applyChange("label", el.dataset.code, readControl(el));
      }
    }
    renderDerived(ROWS[idx]);
  });

  // Add/remove on the output pane's string lists (citations, clarification questions).
  // New entries are always "" — these lists hold ids and short prose, and unlike the
  // input pane there is no JSON Schema here to synthesize a typed blank from.
  document.getElementById("output-form").addEventListener("click", e => {
    const btn = e.target.closest("[data-action]");
    if (!btn) return;
    const path = btn.dataset.path;
    const list = getPath(ROWS[idx].output, path) || [];
    const before = JSON.parse(JSON.stringify(list));
    if (btn.dataset.action === "add-item") {
      list.push("");
    } else if (btn.dataset.action === "remove-item") {
      list.splice(Number(btn.dataset.index), 1);
    }
    setPath(ROWS[idx].output, path, list);
    recordEdit("output", path, before, JSON.parse(JSON.stringify(list)));
    render();
  });

  document.getElementById("reviewer-note").addEventListener("change", e => {
    applyChange("label", "reviewer_note", e.target.value);
  });

  document.getElementById("prev-btn").addEventListener("click", () => {
    if (idx > 0) { idx -= 1; render(); }
  });
  document.getElementById("next-btn").addEventListener("click", () => {
    if (idx < ROWS.length - 1) { idx += 1; render(); }
  });
  document.getElementById("accept-btn").addEventListener("click", () => {
    markReviewed();
    edits.push({
      action: "accept", index: idx, at: new Date().toISOString(),
      by: CONFIG.reviewer || "",
    });
    dirty.add(idx);
    saveDraft();
    if (idx < ROWS.length - 1) { idx += 1; render(); } else { refreshStatus(); }
  });

  document.getElementById("validate-btn").addEventListener("click", async () => {
    const res = await post("/validate");
    showFindings("Validation", res.body.validation || {});
  });
  document.getElementById("save-btn").addEventListener("click", () => doSave("/save"));
  document.getElementById("save-as-btn").addEventListener("click", () => doSave("/save-as"));

  document.getElementById("modal").addEventListener("click", e => {
    if (e.target.id === "modal") closeModal();
  });

  if (CONFIG.read_only) {
    const banner = document.getElementById("readonly-banner");
    if (banner) banner.hidden = false;
    for (const id of ["save-btn", "save-as-btn"]) {
      const b = document.getElementById(id);
      if (b) b.disabled = true;
    }
  }

  window.addEventListener("beforeunload", e => {
    if (dirty.size) { e.preventDefault(); e.returnValue = ""; }
  });

  render();
}
