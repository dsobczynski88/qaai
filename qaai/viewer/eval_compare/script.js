// Actual-vs-predicted diff viewer — reviewer-specific renderLeft() + modals.
// Shared helpers (save, escapeHTML, renderRatings, renderRight, render, openModal,
// closeModal, initViewer) come from common/shared.js, concatenated ahead of this file.
//
// Each record (built by qaai/eval/compare.py) carries:
//   entity_id, inputs, verdict_key, codes[], run_meta,
//   actual   = {verdict, rubric:{code:verdict}},
//   predicted= {verdict|null, rubric:{code:verdict}},
//   actual_output, predicted_output (raw graph-shape objects; predicted_output may be null),
//   diff = [{cell, actual, predicted, mandatory}],  verdict_match, predicted_skipped
const RECORDS = JSON.parse(document.getElementById("DATA").textContent);
const STORAGE_KEY = "eval-compare/{{RUN_KEY}}";
let idx = 0;
const feedback = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");

function feedbackKey(rec, idx) {
  return rec.entity_id || `rec-${idx}`;
}

function chip(v) {
  const val = (v === null || v === undefined || v === "") ? "—" : v;
  const cls = ["Yes", "No", "N-A"].includes(val) ? `chip chip-${val}` : "chip chip-N-A";
  return `<span class="${cls}">${escapeHTML(val)}</span>`;
}

// Study-wide summary, computed from the loaded records (n, verdict mismatches, per-cell
// mismatch counts). Rendered once at the top of every record so it is always visible.
function studySummaryHTML() {
  const n = RECORDS.length;
  let verdictMiss = 0;
  const cellMiss = {};
  const codes = RECORDS[0]?.codes || [];
  for (const rec of RECORDS) {
    if (!rec.verdict_match) verdictMiss += 1;
    for (const d of (rec.diff || [])) {
      if (d.cell === rec.verdict_key) continue;
      cellMiss[d.cell] = (cellMiss[d.cell] || 0) + 1;
    }
  }
  const meta = RECORDS[0]?.run_meta || {};
  const metaBits = [
    ["model", meta.model], ["prompt_set", meta.prompt_set],
    ["git", meta.git_sha], ["mlflow_run", meta.mlflow_run_id], ["spec", meta.spec],
  ].filter(([, v]) => v)
   .map(([k, v]) => `<span>${escapeHTML(k)}: <code>${escapeHTML(v)}</code></span>`).join("");
  const cellBits = codes.map(c => {
    const m = cellMiss[c] || 0;
    return `<span>${escapeHTML(c)}: ${m ? `<span class="miss">${m}</span>` : "0"}</span>`;
  }).join(" · ");
  const verdictCls = verdictMiss ? "bad" : "ok";
  return `
    <div class="study-summary">
      <div class="headline">
        ${n} records · verdict mismatches: <span class="${verdictCls}">${verdictMiss}</span>
        (${n ? Math.round((verdictMiss / n) * 100) : 0}%)
      </div>
      ${cellBits ? `<div class="cell-counts">per-cell mismatches — ${cellBits}</div>` : ""}
      ${metaBits ? `<div class="meta">${metaBits}</div>` : ""}
    </div>`;
}

function renderLeft() {
  const rec = RECORDS[idx];
  const diffByCell = Object.fromEntries((rec.diff || []).map(d => [d.cell, d]));
  const codes = rec.codes || [];

  // verdict row + one row per rubric code, actual | predicted
  const verdictDiff = diffByCell[rec.verdict_key];
  const verdictRow = `
    <tr class="verdict-row${verdictDiff ? " diff" : ""}">
      <td class="cell-code">${escapeHTML(rec.verdict_key)}</td>
      <td>${chip(rec.actual.verdict)}</td>
      <td>${chip(rec.predicted.verdict)}${verdictDiff ? '<span class="diff-marker">← diff</span>' : ""}</td>
      <td></td>
    </tr>`;
  const cellRows = codes.map(code => {
    const d = diffByCell[code];
    const advisory = d ? !d.mandatory : false;
    const cls = d ? (advisory ? "diff advisory" : "diff") : "";
    return `<tr class="${cls}">
      <td class="cell-code">${escapeHTML(code)}</td>
      <td>${chip(rec.actual.rubric[code])}</td>
      <td>${chip(rec.predicted.rubric[code])}${d ? '<span class="diff-marker">← diff</span>' : ""}</td>
      <td class="advisory-tag">${advisory ? "advisory" : ""}</td>
    </tr>`;
  }).join("");

  const statusCls = rec.predicted_skipped ? "skipped" : (rec.verdict_match ? "match" : "mismatch");
  const statusLabel = rec.predicted_skipped ? "NO PREDICTION (skipped)"
    : (rec.verdict_match ? "VERDICT MATCH" : "VERDICT MISMATCH");

  document.getElementById("left").innerHTML = `
    ${studySummaryHTML()}
    <h2>Record</h2>
    <h1><span class="req-id">${escapeHTML(rec.entity_id || `#${idx + 1}`)}</span></h1>
    <div class="record-status">
      <span class="match-flag ${statusCls}">${statusLabel}</span>
      <span class="link-like" onclick="openInputs()">Graph inputs →</span>
      <span class="link-like" onclick="openRaw()">Raw actual vs predicted output →</span>
    </div>

    <h2>Verdict &amp; rubric — actual vs predicted</h2>
    <table class="cmp">
      <thead><tr><th>Cell</th><th>Actual</th><th>Predicted</th><th></th></tr></thead>
      <tbody>${verdictRow}${cellRows}</tbody>
    </table>
  `;
}

function openInputs() {
  const rec = RECORDS[idx];
  const inp = rec.inputs || {};
  let friendly = "";
  if (inp.requirement) {
    friendly += `<table class="detail">
      <tr><th>Requirement ID</th><td>${escapeHTML(inp.requirement.req_id ?? "—")}</td></tr>
      <tr><th>Text</th><td>${escapeHTML(inp.requirement.text ?? "")}</td></tr>
    </table>`;
  }
  if (Array.isArray(inp.test_cases) && inp.test_cases.length) {
    const rows = inp.test_cases.map(tc => `<tr>
      <td class="cited">${escapeHTML(tc.test_id ?? "—")}</td>
      <td>${escapeHTML(tc.description ?? "")}</td>
    </tr>`).join("");
    friendly += `<h3 style="margin-top:16px">Test cases (${inp.test_cases.length})</h3>
      <table class="detail"><thead><tr><th>ID</th><th>Description</th></tr></thead><tbody>${rows}</tbody></table>`;
  }
  openModal(`
    <h3>Graph inputs — ${escapeHTML(rec.entity_id || `#${idx + 1}`)}</h3>
    ${friendly}
    <details ${friendly ? "" : "open"} style="margin-top:14px">
      <summary>Raw input JSON</summary>
      <div class="json-panes"><div class="pane"><pre>${escapeHTML(JSON.stringify(inp, null, 2))}</pre></div></div>
    </details>
  `);
}

function openRaw() {
  const rec = RECORDS[idx];
  const actualPre = escapeHTML(JSON.stringify(rec.actual_output ?? null, null, 2));
  const predObj = rec.predicted_output;
  const predEmpty = predObj === null || predObj === undefined;
  const predPre = predEmpty
    ? "(no prediction — this record was skipped / soft-failed)"
    : escapeHTML(JSON.stringify(predObj, null, 2));
  openModal(`
    <h3>Raw output — actual vs predicted — ${escapeHTML(rec.entity_id || `#${idx + 1}`)}</h3>
    <div class="json-panes">
      <div class="pane"><h4>actual_output (answer key)</h4><pre>${actualPre}</pre></div>
      <div class="pane${predEmpty ? " empty" : ""}"><h4>predicted_output</h4><pre>${predPre}</pre></div>
    </div>
  `);
}

initViewer();
