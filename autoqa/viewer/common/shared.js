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
