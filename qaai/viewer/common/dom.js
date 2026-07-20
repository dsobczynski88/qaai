// Schema-agnostic DOM helpers, concatenated ahead of every viewer bundle.
//
// Extracted from shared.js so the dataset-studio editor — which replaces the
// reviewer feedback pane entirely and therefore cannot use shared.js — can still
// reuse the escaping and modal primitives. Keeping one definition of escapeHTML
// matters: it is the viewer's only XSS barrier for LLM-authored text.

function escapeHTML(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[c]));
}

// Read a JSON payload embedded by the renderer as <script id="..." type="application/json">.
function readJSONScript(id) {
  const el = document.getElementById(id);
  if (!el) return null;
  try { return JSON.parse(el.textContent || "null"); } catch (_) { return null; }
}

function readData() { return readJSONScript("DATA") || []; }

function openModal(bodyHTML) {
  document.getElementById("modal-body").innerHTML = bodyHTML;
  document.getElementById("modal").classList.add("open");
}

function closeModal() { document.getElementById("modal").classList.remove("open"); }
