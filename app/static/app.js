// UI logic. All dynamic values are inserted with textContent / safe attributes only.
"use strict";

const $ = (s) => document.querySelector(s);
const STATUS_LABEL = {
  FOUND: "Migrated", MIGRATION_FAILED: "Migration failed", NOT_FOUND: "Not found",
  UNSUPPORTED: "Unsupported link", INVALID: "Invalid link",
};

function el(tag, text, cls) {
  const e = document.createElement(tag);
  if (text !== undefined && text !== null) e.textContent = text;
  if (cls) e.className = cls;
  return e;
}

function safeLink(href, text) {
  // Only allow https links to Google or SharePoint hosts.
  try {
    const u = new URL(href);
    const ok = u.protocol === "https:" &&
      (/(^|\.)google\.com$/.test(u.hostname) || /\.sharepoint\.com$/.test(u.hostname));
    if (!ok) return el("span", href);
  } catch (_) { return el("span", href); }
  const a = el("a", text || href);
  a.href = href; a.target = "_blank"; a.rel = "noopener noreferrer";
  return a;
}

function fmtTime(iso) {
  return iso ? new Date(iso).toLocaleString() : "";
}

async function api(path, opts) {
  const r = await fetch(path, Object.assign({ credentials: "same-origin" }, opts || {}));
  let body = null;
  try { body = await r.json(); } catch (_) { /* non-JSON */ }
  if (!r.ok) throw new Error((body && body.detail) || ("HTTP " + r.status));
  return body;
}

function renderResults(results) {
  const tbody = $("#results tbody");
  tbody.textContent = "";
  for (const r of results) {
    const tr = document.createElement("tr");
    tr.appendChild(el("td", r.source_uri || r.input));
    const st = el("td");
    st.appendChild(el("span", STATUS_LABEL[r.status] || r.status, "badge " + r.status));
    tr.appendChild(st);
    const tgt = el("td");
    if (r.target_uri) {
      tgt.appendChild(safeLink(r.target_uri, r.target_uri));
      const copy = el("button", "Copy", "link");
      copy.type = "button";
      copy.addEventListener("click", () => navigator.clipboard.writeText(r.target_uri));
      tgt.appendChild(document.createTextNode(" "));
      tgt.appendChild(copy);
    } else {
      tgt.appendChild(el("span", r.message || "", "muted"));
    }
    tr.appendChild(tgt);
    const d = el("td", null, "small");
    if (r.item_type) d.appendChild(el("div", r.item_type === "FOLDER" ? "Folder" : "File"));
    if (r.batch_name) d.appendChild(el("div", "Batch: " + r.batch_name));
    if (r.migrated_at) d.appendChild(el("div", "Migrated: " + fmtTime(r.migrated_at)));
    if (r.warning) d.appendChild(el("div", "Warning: " + r.warning, "warn"));
    if (r.via === "live") d.appendChild(el("div", "Just migrated (live log)", "muted"));
    if (r.status === "FOUND" && r.item_type === "FOLDER") {
      const b = el("button", "Show folder contents", "link");
      b.type = "button";
      b.addEventListener("click", () => showFolder(r.source_uri));
      d.appendChild(b);
    }
    tr.appendChild(d);
    tbody.appendChild(tr);
  }
  $("#results-card").classList.remove("hidden");
  $("#copy-all").onclick = () => navigator.clipboard.writeText(
    results.filter((r) => r.target_uri).map((r) => r.target_uri).join("\n"));
}

function renderError(msg) {
  renderResults([]);
  const tr = document.createElement("tr");
  const td = el("td", msg, "error");
  td.colSpan = 4;
  tr.appendChild(td);
  $("#results tbody").appendChild(tr);
}

async function showFolder(url) {
  const card = $("#folder-card");
  const tbody = $("#folder tbody");
  tbody.textContent = "";
  try {
    const data = await api("/api/folder?url=" + encodeURIComponent(url));
    $("#folder-title").textContent = "Contents of " + url + " (" + data.children.length + ")";
    for (const c of data.children) {
      const tr = document.createElement("tr");
      tr.appendChild(el("td", c.item_type === "FOLDER" ? "Folder" : "File"));
      tr.appendChild(el("td", c.source_uri));
      const t = el("td"); t.appendChild(safeLink(c.target_uri)); tr.appendChild(t);
      tr.appendChild(el("td", c.warning_title ? c.event_status + " · " + c.warning_title : c.event_status));
      tbody.appendChild(tr);
    }
    card.classList.remove("hidden");
    card.scrollIntoView({ behavior: "smooth" });
  } catch (e) {
    $("#folder-title").textContent = "Could not load folder: " + e.message;
    card.classList.remove("hidden");
  }
}

async function withBusy(form, fn) {
  const btn = form.querySelector("button[type=submit]");
  btn.disabled = true;
  try { await fn(); } catch (e) { renderError(e.message); } finally { btn.disabled = false; }
}

$("#single-form").addEventListener("submit", (ev) => {
  ev.preventDefault();
  withBusy(ev.target, async () => {
    const r = await api("/api/lookup?url=" + encodeURIComponent($("#url").value.trim()));
    renderResults([r]);
  });
});

$("#bulk-form").addEventListener("submit", (ev) => {
  ev.preventDefault();
  withBusy(ev.target, async () => {
    const urls = $("#urls").value.split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
    const data = await api("/api/bulk", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ urls }),
    });
    renderResults(data.results);
  });
});

(async () => {
  try {
    const me = await api("/api/me");
    $("#max-bulk").textContent = me.max_bulk;
    if (me.app_title) {
      $("#app-title").textContent = me.app_title;
      document.title = me.app_title;
    }
    const age = me.index_age_seconds;
    $("#me").textContent = "Signed in as " + me.email +
      (age !== null ? " · index synced " + Math.max(0, Math.round(age / 60)) + " min ago" : "");
  } catch (e) {
    $("#me").textContent = e.message;
  }
})();
