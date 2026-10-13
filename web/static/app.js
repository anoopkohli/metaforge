// metaforge demo UI. Every piece of data is inserted with textContent (via el()); this file never
// builds HTML from strings, so tenant-written text (labels, documents, log payloads, model output)
// cannot become markup or script.
"use strict";

const CSRF = { "X-Metaforge-CSRF": "1", "Content-Type": "application/json" };
const state = { session: null, tab: "overview", epoch: 0 };

// Every element that shows tenant data. All of them are emptied whenever the company or role
// changes, so nothing from the previous session stays on screen. tests/test_web.py checks this list
// against index.html, so a new panel cannot be left out.
const TENANT_VIEWS = [
  "object-list", "object-detail", "gen-example", "gen-result", "doc-list", "ask-suggestions",
  "ask-result", "biz-name", "biz-desc", "schema-list", "data-examples", "data-result", "builder",
  "alert-list", "charts", "log-list", "triage-result", "lab-result", "lab-status", "evals-result",
  "evals-status", "who-am-i", "asst-examples", "asst-result", "asst-proposals", "audit-result",
  "audit-status",
];
const TENANT_INPUTS = ["gen-text", "ask-text", "data-question", "asst-question"];

// A reply to a request made under an earlier session is dropped, never rendered.
class StaleResponse extends Error {
  constructor() { super(""); this.stale = true; }
}
const AUTH_TABS = new Set(["assistant", "data", "docs", "ask-data", "incident", "lab", "evals", "audit"]);

// ---- helpers ----------------------------------------------------------------------------------

async function api(path, body, method) {
  const epoch = state.epoch;
  const opts = { credentials: "same-origin", method: method || (body === undefined ? "GET" : "POST") };
  if (opts.method !== "GET") {
    opts.headers = CSRF;
    opts.body = JSON.stringify(body === undefined ? {} : body);
  }
  const res = await fetch(path, opts);
  let data;
  try { data = await res.json(); } catch { data = { error: "unexpected response" }; }
  if (epoch !== state.epoch) throw new StaleResponse();
  if (!res.ok) throw new Error(data.error || `request failed (${res.status})`);
  return data;
}

function el(tag, props, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = String(value);
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    // The CSP forbids style *attributes*; setting styles through the CSSOM is allowed.
    else if (key === "style") node.style.cssText = String(value);
    else node.setAttribute(key, String(value));
  }
  for (const child of children.flat()) {
    if (child === undefined || child === null || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

const $ = (id) => document.getElementById(id);

// Replace a node's children, skipping null / undefined / false and flattening arrays, exactly like
// el(). Calling node.replaceChildren directly would render a null as the text "null".
function setKids(node, ...children) {
  node.replaceChildren(...children.flat().filter((c) => c !== null && c !== undefined && c !== false));
}
const show = (node, on) => node.classList.toggle("hidden", !on);
const fmt = (n) => (Math.abs(n) >= 100 ? Math.round(n).toLocaleString() : String(Math.round(n * 1000) / 1000));

function errorBox(message) {
  if (!message) return document.createTextNode("");  // stale reply: show nothing
  return el("div", { class: "result-card" }, el("span", { class: "result-bad", text: "Error: " }), message);
}

async function withBusy(button, fn) {
  const label = button.textContent;
  button.disabled = true;
  button.textContent = "Working…";
  try { return await fn(); } finally { button.disabled = false; button.textContent = label; }
}

// ---- session and tabs -------------------------------------------------------------------------

async function loadSession() {
  state.session = await api("/api/session");
  const select = $("tenant-select");
  const biz = state.session.businesses || {};
  setKids(select, state.session.tenants.map((t) =>
    el("option", { value: t, text: biz[t] ? `${t} · ${biz[t].name}` : t })));
  if (state.session.tenant) select.value = state.session.tenant;
  await loadPeople(state.session.user ? state.session.user.id : null);
  const live = state.session.live;
  const badge = $("mode-badge");
  badge.textContent = live ? `live · ${state.session.model}` : "offline · stand-in AI";
  badge.className = "badge " + (live ? "accent" : "warn");
  $("footer-mode").textContent = live
    ? `AI calls go to ${state.session.model} (budget-capped)`
    : "no API key: AI steps use recorded or scripted stand-ins";
  const u = state.session.user;
  $("login-btn").textContent = u ? `Signed in: ${u.name} (${state.session.role})` : "Sign in";
}

// The demo identity provider: pick a person from the company directory. Their roles are decided
// by the server at sign-in, never by the browser.
async function loadPeople(selected) {
  const { people } = await api(`/api/directory?tenant_id=${encodeURIComponent($("tenant-select").value)}`);
  const sel = $("person-select");
  setKids(sel, ...people.map((p) => {
    const tags = [p.roles.includes("manager") ? "manager" : "staff"];
    if (p.roles.some((r) => r.startsWith("own:"))) tags.push("own rows only");
    return el("option", { value: p.id, text: `${p.name} · ${p.title} (${tags.join(", ")})` });
  }));
  if (selected && people.some((p) => p.id === selected)) sel.value = selected;
}

function resetTenantViews() {
  state.epoch += 1;
  for (const id of TENANT_VIEWS) $(id).replaceChildren();
  for (const id of TENANT_INPUTS) $(id).value = "";
  show($("incident-view"), false);
  schema = null;
}

async function login() {
  resetTenantViews();
  await api("/api/login", { tenant_id: $("tenant-select").value, worker_id: $("person-select").value });
  await loadSession();
  await refreshTab();
}

function selectTab(name) {
  state.tab = name;
  for (const t of document.querySelectorAll(".tab")) t.classList.toggle("active", t.dataset.tab === name);
  for (const p of document.querySelectorAll(".panel")) p.classList.toggle("active", p.id === `tab-${name}`);
  refreshTab();
}

async function refreshTab() {
  const needsLogin = AUTH_TABS.has(state.tab) && !(state.session && state.session.tenant);
  show($("login-hint"), needsLogin);
  for (const p of document.querySelectorAll(".panel")) {
    if (p.id === `tab-${state.tab}`) p.style.opacity = needsLogin ? "0.45" : "1";
  }
  if (needsLogin) return;
  try {
    if (state.tab === "data") await loadDataTab();
    if (state.tab === "docs") await loadDocsTab();
    if (state.tab === "ask-data") await loadAskData();
    if (state.tab === "assistant") await loadAssistant();
    if (state.tab === "audit") await loadAudit();
    if (state.tab === "incident") await loadIncident();
    if (state.tab === "lab") await loadLabCatalogue();
  } catch (e) {
    console.error(e);
  }
}

// ---- data model (stages 1-2) ------------------------------------------------------------------

async function loadDataTab() {
  const { objects } = await api("/api/objects");
  const list = $("object-list");
  setKids(list, 
    ...objects.map((o) =>
      el("li", { onclick: (ev) => describeObject(o.name, ev.currentTarget) },
        el("span", {}, el("span", { class: "mono", text: o.name }), o.label ? el("span", { class: "muted", text: `  ${o.label}` }) : null),
        el("span", { class: "badge" + (o.kind === "custom" ? " accent" : ""), text: o.kind })))
  );
  $("object-detail").replaceChildren();
  const { examples } = await api("/api/generate/examples");
  const sel = $("gen-example");
  setKids(sel, 
    el("option", { value: "", text: "Choose an example…" }),
    ...examples.map((x) => el("option", { value: x.description, text: x.description }))
  );
  $("gen-mode").textContent = state.session.live ? "Claude drafts from any request" : "offline: pick an example (recorded AI replies)";
}

async function describeObject(name, li) {
  for (const n of $("object-list").children) n.classList.remove("selected");
  li.classList.add("selected");
  const obj = await api(`/api/objects/${encodeURIComponent(name)}`);
  setKids($("object-detail"), 
    el("h3", { text: `${obj.name} (${obj.kind})` }),
    el("div", { class: "table-wrap" },
      el("table", {},
        el("thead", {}, el("tr", {}, ["Field", "Type", "Required", "Access"].map((h) => el("th", { text: h })))),
        el("tbody", {}, obj.fields.map((f) =>
          el("tr", {},
            el("td", { class: "mono", text: f.name }),
            el("td", { text: f.reference_to ? `${f.type} → ${f.reference_to}` : f.type }),
            el("td", { text: f.required ? "yes" : "" }),
            el("td", {}, f.restricted ? el("span", { class: "lock", text: "🔒 managers only" }) : "everyone")))))),
    obj.kind === "platform"
      ? el("p", { class: "muted small", text: "Fields your company has hidden are not shown, and never sent to the AI." })
      : null,
    el("div", { id: "record-preview" })
  );
  try {
    const rec = await api(`/api/objects/${encodeURIComponent(name)}/records`);
    setKids($("record-preview"), 
      el("h3", { text: `Records (${rec.matched})`, style: "margin-top:1rem" }),
      rec.rows.length ? resultTable(rec) : el("p", { class: "muted", text: "No records." }),
      state.session.role !== "manager" && obj.fields.some((f) => f.restricted)
        ? el("p", { class: "muted small", text: "🔒 columns are left out because you are logged in as staff." })
        : null);
  } catch (e) { setKids($("record-preview"), errorBox(e.message)); }
}

function resultTable(result) {
  const cell = (v) => (v === null || v === undefined ? "" : typeof v === "number" ? fmt(v) : String(v));
  return el("div", { class: "table-wrap" },
    el("table", {},
      el("thead", {}, el("tr", {}, result.columns.map((c) => el("th", { text: c })))),
      el("tbody", {}, result.rows.map((r) => el("tr", {}, result.columns.map((c) => el("td", { text: cell(r[c]) })))))),
    result.truncated ? el("p", { class: "muted small", text: `Showing ${result.rows.length} of ${result.matched}.` }) : null);
}

function renderAttempts(attempts) {
  return attempts.map((a, i) =>
    el("div", { class: "attempt " + (a.errors.length ? "rejected" : "ok") },
      el("div", { class: "row" },
        el("strong", { text: `Attempt ${i + 1}` }),
        el("span", { class: "badge " + (a.errors.length ? "bad" : "good"), text: a.errors.length ? "rejected" : "accepted" })),
      el("details", {}, el("summary", { text: "What the AI replied" }), el("pre", { text: a.text })),
      a.errors.map((e) => el("div", { class: "error-line", text: `✗ ${e}` })),
      a.errors.length ? el("div", { class: "muted small", text: "These errors were sent back to the AI to fix." }) : null));
}

async function draftChange() {
  const out = $("gen-result");
  const description = $("gen-text").value.trim() || $("gen-example").value;
  if (!description) { setKids(out, errorBox("Type a request or choose an example.")); return; }
  try {
    const res = await api("/api/generate", { description });
    if (!res.attempts) { setKids(out, el("div", { class: "result-card", text: res.message })); return; }
    const parts = [el("h3", { text: "How the AI's draft was checked" }), ...renderAttempts(res.attempts)];
    if (res.ok && res.proposal.action === "refuse") {
      parts.push(el("div", { class: "result-card" }, el("strong", { text: "The AI declined: " }), res.proposal.reason));
    } else if (res.ok) {
      parts.push(el("div", { class: "result-card" },
        el("strong", { text: "Draft passed every check. " }),
        "Nothing has changed yet.",
        el("div", { class: "row" }, el("button", { class: "btn btn-primary", onclick: (ev) => submitChange(res.proposal, ev.currentTarget) }, "Submit for approval"))));
    } else {
      parts.push(errorBox("The AI did not produce a valid draft within 3 attempts, so nothing was proposed."));
    }
    setKids(out, ...parts);
  } catch (e) { setKids(out, errorBox(e.message)); }
}

async function submitChange(proposal, button) {
  const out = $("gen-result");
  try {
    const res = await withBusy(button, () => api("/api/changes", { proposal }));
    if (!res.accepted) { out.append(errorBox((res.errors || []).join("; "))); return; }
    const card = el("div", { class: "result-card" },
      el("strong", { text: "Waiting for your approval" }),
      el("p", { text: res.summary }),
      el("div", { class: "row" },
        el("button", { class: "btn btn-good", onclick: async (ev) => {
          try {
            await withBusy(ev.currentTarget, () => api(`/api/changes/${encodeURIComponent(res.change_id)}/approve`, {}));
            setKids(card, el("span", { class: "result-good", text: "Applied. " }), "Only your company sees this change; switch company to check.");
            await loadDataTab();
          } catch (e) { card.append(errorBox(e.message)); }
        } }, "Approve and apply"),
        el("span", { class: "muted small", text: `change id ${res.change_id} · expires in ${Math.round(res.expires_in_s / 60)} min` })));
    out.append(card);
    button.disabled = true;
  } catch (e) { out.append(errorBox(e.message)); }
}

// ---- documents (stage 3) ----------------------------------------------------------------------

const SUGGESTIONS = [
  "What is the expense approval limit?",
  "How many unused leave days can be carried over?",
  "What is the database connection pool size?",
  "How long does the buddy program last?",
  "Which desk do I report a stolen asset to?",
];

async function loadDocsTab() {
  const { documents } = await api("/api/documents");
  setKids($("doc-list"), ...documents.map((d) => {
    const text = el("div", { class: "doc-text hidden", text: d.text });
    return el("li", { style: "display:block", onclick: () => show(text, text.classList.contains("hidden")) },
      el("div", { class: "hit-head" }, el("span", { text: d.title }), el("span", { class: "mono muted small", text: d.doc_id })), text);
  }));
  setKids($("ask-suggestions"), ...SUGGESTIONS.map((q) =>
    el("button", { class: "chip", onclick: () => { $("ask-text").value = q; ask(); } }, q)));
}

async function ask() {
  const out = $("ask-result");
  const question = $("ask-text").value.trim();
  if (!question) return;
  setKids(out, el("p", { class: "muted", text: "Searching your documents…" }));
  try {
    const res = await api("/api/ask", { question });
    const maxScore = Math.max(0.0001, ...res.hits.map((h) => h.score));
    setKids(out, 
      el("div", { class: "result-card" },
        el("div", { class: "row wrap" },
          el("strong", { text: "Answer" }),
          res.cached ? el("span", { class: "badge", text: "from cache (your company only)" }) : null,
          el("span", { class: "badge " + (res.mode === "live" ? "accent" : "warn"), text: res.mode === "live" ? "Claude" : "offline: quotes the best passage" })),
        el("div", { class: "answer", text: res.answer }),
        el("div", { class: "chips" }, res.citations.map((c) => el("span", { class: "chip static mono", text: `cites ${c}` })))),
      el("h3", { text: "Passages the AI was given", style: "margin-top:1rem" }),
      el("p", { class: "muted small", text: "Every passage is checked to belong to your company before the AI sees it." }),
      ...res.hits.map((h) =>
        el("div", { class: "hit" },
          el("div", { class: "hit-head" },
            el("span", {}, el("strong", { text: h.title }), el("span", { class: "mono muted small", text: `  ${h.chunk_id}` })),
            el("span", {}, el("span", { class: "badge good", text: `company: ${h.tenant_id}` }), " ",
              el("span", { class: "scorebar", title: `relevance ${h.score}` }, el("span", { style: `width:${Math.max(4, (h.score / maxScore) * 100)}%` })))),
          el("div", { class: "small", text: h.text }))));
  } catch (e) { setKids(out, errorBox(e.message)); }
}

// ---- Ask data (stage 6) -----------------------------------------------------------------------

let schema = null;

async function loadAskData() {
  schema = await api("/api/data/schema");
  $("biz-name").textContent = schema.business ? schema.business.name : "Your data";
  $("biz-desc").textContent = (schema.business ? schema.business.description + " " : "") +
    `You are logged in as ${schema.role}.`;
  setKids($("schema-list"), ...schema.objects.map((o) =>
    el("details", { class: "schema-obj" },
      el("summary", {}, el("span", { class: "mono", text: o.name }), el("span", { class: "muted small", text: `  ${o.records} records` })),
      el("ul", { class: "schema-fields" }, o.fields.map((f) =>
        el("li", {},
          el("span", { class: "mono", text: f.name }),
          el("span", { class: f.readable ? "muted" : "lock", text: (f.reference_to ? `→ ${f.reference_to}` : f.type) + (f.restricted ? (f.readable ? " · 🔓 restricted" : " · 🔒 managers only") : "") })))))));
  const { examples } = await api("/api/data/examples");
  setKids($("data-examples"), ...examples.map((q) =>
    el("button", { class: "chip", onclick: () => { $("data-question").value = q; askData(); } }, q)));
  $("data-question").placeholder = state.session.live ? "Ask anything about your records" : "Offline: pick an example below, or build a query";
  buildBuilder();
}

function statusBadge(status) {
  const cls = { answered: "good", denied: "bad", refused: "warn", invalid: "bad", offline: "warn" }[status] || "";
  const label = { answered: "answered", denied: "denied: restricted data", refused: "the AI declined", invalid: "could not answer", offline: "offline" }[status] || status;
  return el("span", { class: `badge ${cls}`, text: label });
}

function renderDataResult(res) {
  const parts = [el("div", { class: "result-card" },
    el("div", { class: "row wrap" }, statusBadge(res.status),
      res.mode ? el("span", { class: "badge " + (res.mode === "live" ? "accent" : "warn"), text: res.mode === "live" ? "Claude" : "offline: recorded plan" }) : null),
    el("div", { class: "answer", text: res.answer || "" }),
    res.description ? el("p", { class: "muted small", text: `Query: ${res.description}` }) : null)];
  if (res.attempts && res.attempts.length) {
    parts.push(el("h3", { text: "How the AI's query was checked", style: "margin-top:1rem" }), ...renderAttempts(res.attempts));
  }
  if (res.result) parts.push(el("h3", { text: "Result", style: "margin-top:1rem" }), resultTable(res.result));
  setKids($("data-result"), ...parts);
}

async function askData() {
  const question = $("data-question").value.trim();
  if (!question) return;
  setKids($("data-result"), el("p", { class: "muted", text: "Planning a query…" }));
  try { renderDataResult(await api("/api/data/ask", { question })); }
  catch (e) { setKids($("data-result"), errorBox(e.message)); }
}

// The builder: the user writes the query, so no AI is involved. The server checks it the same way.
function buildBuilder() {
  const box = $("builder");
  const objSel = el("select", {}, schema.objects.map((o) => el("option", { value: o.name, text: o.name })));
  const fieldsBox = el("div", { class: "field-checks" });
  const conds = el("div", {});
  const groupSel = el("select", {});
  const aggFn = el("select", {}, ["", "count", "sum", "avg", "min", "max"].map((f) => el("option", { value: f, text: f || "none" })));
  const aggField = el("select", {});
  const orderSel = el("select", {});
  const desc = el("input", { type: "checkbox" });
  const limit = el("input", { type: "text", value: "20", style: "width:5rem" });

  const fieldsOf = () => schema.objects.find((o) => o.name === objSel.value).fields;
  const options = (sel, withNone) => setKids(sel, 
    ...(withNone ? [el("option", { value: "", text: "none" })] : []),
    ...fieldsOf().map((f) => el("option", { value: f.name, text: f.readable ? f.name : `🔒 ${f.name}` })));

  function addCond() {
    const f = el("select", {}); options(f, false);
    const op = el("select", {}, ["=", "!=", ">", ">=", "<", "<=", "contains"].map((o) => el("option", { value: o, text: o })));
    const v = el("input", { type: "text", placeholder: "value" });
    const rm = el("button", { class: "btn btn-small", onclick: () => row.remove() }, "✕");
    const row = el("div", { class: "cond-row" }, f, op, v, rm);
    row.read = () => {
      const field = fieldsOf().find((x) => x.name === f.value);
      let value = v.value;
      if (field && (field.type === "integer" || field.type === "decimal") && value !== "" && !isNaN(Number(value))) value = Number(value);
      if (field && field.type === "boolean") value = value === "true";
      return { field: f.value, op: op.value, value };
    };
    conds.append(row);
  }

  function refresh() {
    setKids(fieldsBox, ...fieldsOf().map((f) =>
      el("label", { class: f.readable ? "" : "locked", title: f.readable ? "" : "restricted to managers" },
        el("input", { type: "checkbox", value: f.name }), f.readable ? f.name : `🔒 ${f.name}`)));
    conds.replaceChildren();
    options(groupSel, true); options(aggField, true); options(orderSel, true);
    orderSel.prepend(el("option", { value: "value", text: "value (aggregate)" }));
    orderSel.value = "";
  }
  objSel.addEventListener("change", refresh);

  async function run(ev) {
    const query = { object: objSel.value };
    const select = [...fieldsBox.querySelectorAll("input:checked")].map((c) => c.value);
    if (select.length) query.select = select;
    const where = [...conds.children].map((r) => r.read());
    if (where.length) query.where = where;
    if (groupSel.value) query.group_by = groupSel.value;
    if (aggFn.value) query.aggregate = aggFn.value === "count" && !aggField.value ? { fn: "count" } : { fn: aggFn.value, field: aggField.value };
    if (orderSel.value) query.order_by = { field: orderSel.value, desc: desc.checked };
    const n = parseInt(limit.value, 10);
    if (!isNaN(n)) query.limit = n;
    try {
      const res = await withBusy(ev.currentTarget, () => api("/api/data/query", { query }));
      setKids($("data-result"), el("div", { class: "result-card" },
        el("div", { class: "row wrap" }, statusBadge("answered"), el("span", { class: "badge", text: "your query (no AI)" })),
        el("p", { class: "muted small", text: `Query: ${res.description}` }),
        el("details", {}, el("summary", { text: "query as sent" }), el("pre", { text: JSON.stringify(res.query, null, 2) }))),
        el("h3", { text: "Result", style: "margin-top:1rem" }), resultTable(res.result));
    } catch (e) {
      setKids($("data-result"), errorBox(e.message), el("pre", { text: JSON.stringify(query, null, 2) }));
    }
  }

  setKids(box, el("div", { class: "builder-grid" },
    el("label", { text: "Object" }), objSel,
    el("label", { text: "Show fields" }), fieldsBox,
    el("label", { text: "Filters" }), el("div", {}, conds, el("button", { class: "btn btn-small", onclick: addCond }, "+ filter")),
    el("label", { text: "Group by" }), groupSel,
    el("label", { text: "Aggregate" }), el("div", { class: "cond-row" }, aggFn, aggField),
    el("label", { text: "Sort by" }), el("div", { class: "cond-row" }, orderSel, el("label", { class: "check" }, desc, "highest first")),
    el("label", { text: "Limit" }), limit),
    el("div", { class: "row" }, el("button", { class: "btn btn-primary", onclick: run }, "Run query"),
      el("span", { class: "muted small", text: "🔒 fields are refused for staff in every part of a query." })));
  refresh();
}

// ---- assistant (stage 7) ----------------------------------------------------------------------

async function loadAssistant() {
  const s = state.session;
  $("who-am-i").textContent = `You are ${s.user.name}, ${s.user.title} (${s.role}).` +
    (s.scopes.length ? ` Row access: ${s.scopes.join("; ")}.` : " You see all rows of every object.");
  const { examples } = await api("/api/assistant/examples");
  setKids($("asst-examples"), ...examples.map((e) =>
    el("button", {
      class: "chip", title: e.available ? "" : `recorded for a ${e.title}; sign in as one to replay it`,
      onclick: () => { $("asst-question").value = e.question; askAssistant(); },
    }, e.available ? e.question : `${e.question} (as ${e.title})`)));
  await loadProposals();
}

function proposalRow(p) {
  const actions = p.status === "proposed"
    ? el("span", {},
        el("button", { class: "btn btn-good btn-small", onclick: (ev) => decide(p.id, "approve", ev.currentTarget) }, "Approve"), " ",
        el("button", { class: "btn btn-small", onclick: (ev) => decide(p.id, "reject", ev.currentTarget) }, "Reject"))
    : el("span", { class: "badge " + (p.status === "executed" ? "good" : ""), text: p.status });
  return el("div", { class: "proposal" },
    el("span", {}, el("strong", { text: p.kind.replace("_", " ") }), " ", el("span", { class: "small", text: p.summary })),
    actions);
}

async function loadProposals() {
  const { proposals } = await api("/api/assistant/proposals");
  setKids($("asst-proposals"), ...(proposals.length
    ? proposals.slice().reverse().map(proposalRow)
    : [el("p", { class: "muted", text: "Nothing proposed yet. Changes the assistant suggests appear here for you to approve." })]));
}

async function decide(id, decision, button) {
  try {
    await withBusy(button, () => api(`/api/assistant/proposals/${encodeURIComponent(id)}/${decision}`, {}));
  } catch (e) { $("asst-proposals").prepend(errorBox(e.message)); return; }
  await loadProposals();
}

async function askAssistant() {
  const question = $("asst-question").value.trim();
  if (!question) return;
  setKids($("asst-result"), el("p", { class: "muted", text: "Thinking…" }));
  try {
    const res = await api("/api/assistant/ask", { question });
    const blocked = res.steps.filter((s) => s.observation && s.observation.error).length;
    setKids($("asst-result"),
      el("div", { class: "result-card" },
        el("div", { class: "row wrap" },
          statusBadge(res.status === "finished" ? "answered" : res.status === "offline" ? "offline" : "invalid"),
          res.mode ? el("span", { class: "badge " + (res.mode === "live" ? "accent" : "warn"), text: res.mode === "live" ? "Claude" : "offline: recorded conversation" }) : null,
          res.steps.length ? el("span", { class: "muted small", text: `${res.steps.length} steps · ${blocked} blocked or rejected` }) : null),
        el("div", { class: "answer", text: res.answer || "" }),
        res.sources && res.sources.length ? el("div", { class: "chips" }, res.sources.map((x) => el("span", { class: "chip static mono", text: x }))) : null),
      res.proposals.length ? el("h3", { text: "Waiting for your approval", style: "margin-top:1rem" }) : null,
      ...res.proposals.map(proposalRow),
      res.steps.length ? el("h3", { text: "Step by step", style: "margin-top:1rem" }) : null,
      el("div", { class: "steps" }, res.steps.map((s) => {
        const err = s.observation && s.observation.error;
        const tool = s.action ? s.action.tool : "(unreadable reply)";
        return el("div", { class: "step" + (err ? " blocked" : "") },
          el("div", { class: "step-n", text: String(s.n) }),
          el("div", {},
            el("div", {}, el("span", { class: "step-tool mono", text: tool }), " ",
              s.action ? el("span", { class: "mono small muted", text: JSON.stringify(s.action.args).slice(0, 300) }) : null),
            err ? el("div", { class: "error-line", text: `blocked: ${err}` }) : null,
            !err && !s.observation.finished
              ? el("details", {}, el("summary", { text: "what the assistant saw" }), el("pre", { text: JSON.stringify(s.observation, null, 2).slice(0, 4000) }))
              : null));
      })),
    );
    await loadProposals();
  } catch (e) { setKids($("asst-result"), errorBox(e.message)); }
}

// ---- audit log (stage 7) ----------------------------------------------------------------------

async function loadAudit() {
  if (state.session.role !== "manager") {
    setKids($("audit-result"), el("div", { class: "result-card", text: "The audit log is for managers only. Sign in as a manager to see it." }));
    return;
  }
  try {
    const { entries } = await api("/api/audit");
    const detail = (d) => Object.entries(d).filter(([, v]) => v !== null && v !== undefined)
      .map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(", ") : v}`).join(" · ");
    setKids($("audit-result"), el("div", { class: "table-wrap" },
      el("table", {},
        el("thead", {}, el("tr", {}, ["#", "When", "Who", "What", "Details", "Hash"].map((h) => el("th", { text: h })))),
        el("tbody", {}, entries.map((e) =>
          el("tr", {},
            el("td", { class: "mono", text: String(e.seq) }),
            el("td", { class: "small", text: new Date(e.ts * 1000).toLocaleTimeString() }),
            el("td", { class: "small", text: e.actor_name }),
            el("td", { class: "mono small", text: e.action }),
            el("td", { class: "detail-kv", text: detail(e.detail) }),
            el("td", { class: "mono small muted", text: e.hash })))))));
  } catch (e) { setKids($("audit-result"), errorBox(e.message)); }
}

async function verifyAudit(button) {
  try {
    const r = await withBusy(button, () => api("/api/audit/verify", {}));
    setKids($("audit-status"), r.ok
      ? el("span", { class: "audit-ok", text: `✓ chain intact: ${r.checked} entries verified` })
      : el("span", { class: "audit-bad", text: `✗ broken at entry ${r.first_bad}: ${r.reason}` }));
  } catch (e) { setKids($("audit-status"), errorBox(e.message)); }
}

// ---- incident (stage 5) -----------------------------------------------------------------------

const METRIC_LABELS = {
  "store.latency_p95_ms": "DB latency p95 (ms)",
  "store.pool_in_use": "DB connections in use",
  "queue.backlog": "Queue backlog",
  "requests.error_rate": "Request error rate",
};

function chart(name, values, threshold) {
  const NS = "http://www.w3.org/2000/svg";
  const w = 300, h = 70, pad = 4;
  const max = Math.max(threshold || 0, ...values) * 1.1 || 1;
  const x = (i) => pad + (i * (w - 2 * pad)) / Math.max(1, values.length - 1);
  const y = (v) => h - pad - (v / max) * (h - 2 * pad);
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
  svg.setAttribute("preserveAspectRatio", "none");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", `${METRIC_LABELS[name]} over time`);
  const mk = (tag, attrs) => { const n = document.createElementNS(NS, tag); for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v); svg.append(n); };
  if (values.length > 15) mk("line", { class: "fault-mark", x1: x(15), x2: x(15), y1: 0, y2: h });
  if (threshold) mk("line", { class: "threshold", x1: 0, x2: w, y1: y(threshold), y2: y(threshold) });
  mk("polyline", { class: "line", points: values.map((v, i) => `${x(i)},${y(v)}`).join(" ") });
  const last = values.length ? values[values.length - 1] : 0;
  return el("div", { class: "chart" },
    el("div", { class: "chart-title" }, el("span", { text: METRIC_LABELS[name] }), el("span", { class: "mono", text: fmt(last) })),
    svg);
}

function renderIncident(view) {
  show($("incident-view"), view.active);
  if (!view.active) return;
  setKids($("alert-list"), 
    ...(view.alerts.length
      ? view.alerts.map((a) => el("div", { class: "alert" },
          el("span", { text: `⚠ ${METRIC_LABELS[a.metric] || a.metric}` }),
          el("span", { class: "mono", text: `${fmt(a.value)} > ${fmt(a.threshold)}` })))
      : [el("div", { class: "all-clear", text: "✓ No alerts. Everything is within its runbook threshold." })]));
  setKids($("charts"), ...Object.keys(METRIC_LABELS).map((m) =>
    chart(m, view.metrics[m], m === "store.pool_in_use" ? view.thresholds[m] : view.thresholds[m])));
  setKids($("log-list"), ...view.logs.slice().reverse().map((l) =>
    el("div", { class: "log" },
      el("span", { class: "mono muted", text: l.id }),
      el("span", { class: "muted", text: `min ${l.minute}` }),
      el("span", { class: `lvl-${l.level}`, text: `${l.level} ${l.component}` }),
      el("span", { class: "msg", text: l.message }))));
  if (view.triage) renderTriage(view.triage, view.proposals);
  else $("triage-result").replaceChildren();
}

function renderTriage(t, proposals) {
  const blocked = t.steps.filter((s) => s.observation && s.observation.error).length;
  const parts = [
    el("div", { class: "row wrap" },
      el("span", { class: "badge " + (t.status === "finished" ? "good" : "bad"), text: t.status === "finished" ? "finished" : "stopped at the 10-step limit" }),
      el("span", { class: "muted small", text: `${t.steps.length} steps · ${blocked} blocked or rejected` })),
  ];
  if (t.diagnosis) {
    parts.push(el("div", { class: "result-card" },
      el("strong", { text: `Diagnosis: ${t.diagnosis.fault_type.replaceAll("_", " ")}` }),
      el("p", { text: t.diagnosis.summary }),
      el("div", { class: "chips" }, t.diagnosis.evidence.map((e) => el("span", { class: "chip static mono", text: e })))));
  }
  parts.push(el("h3", { text: "Proposed fixes (nothing runs until you approve)", style: "margin-top:1rem" }));
  if (!proposals.length) parts.push(el("p", { class: "muted", text: "No fixes proposed." }));
  for (const p of proposals) {
    parts.push(el("div", { class: "proposal" },
      el("span", {}, el("strong", { text: p.action.replaceAll("_", " ") }), " ", el("span", { class: "mono small", text: JSON.stringify(p.params) })),
      p.status === "proposed"
        ? el("button", { class: "btn btn-good btn-small", onclick: (ev) => approveFix(p.id, ev.currentTarget) }, "Approve")
        : el("span", { class: "badge good", text: p.status })));
  }
  parts.push(el("h3", { text: "Step by step", style: "margin-top:1rem" }),
    el("div", { class: "steps" }, t.steps.map((s) => {
      const err = s.observation && s.observation.error;
      const tool = s.action ? s.action.tool : "(unreadable reply)";
      return el("div", { class: "step" + (err ? " blocked" : "") },
        el("div", { class: "step-n", text: String(s.n) }),
        el("div", {},
          el("div", {}, el("span", { class: "step-tool mono", text: tool }), " ",
            s.action ? el("span", { class: "mono small muted", text: JSON.stringify(s.action.args) }) : null),
          err ? el("div", { class: "error-line", text: `blocked: ${err}` }) : null,
          s.observation && s.observation.ignored_arguments
            ? el("div", { class: "error-line", text: `ignored ${s.observation.ignored_arguments.join(", ")}: it can only see its own company` })
            : null,
          !err && !s.observation.finished
            ? el("details", {}, el("summary", { text: "what the AI saw" }), el("pre", { text: JSON.stringify(s.observation, null, 2).slice(0, 4000) }))
            : null));
    })));
  setKids($("triage-result"), ...parts);
}

async function loadIncident() { renderIncident(await api("/api/incident")); }

async function startIncident(button) {
  try {
    const view = await withBusy(button, () => api("/api/incident/start", {
      fault: $("fault-select").value,
      malicious_payload: $("malicious-check").checked,
    }));
    renderIncident(view);
  } catch (e) { setKids($("triage-result"), errorBox(e.message)); }
}

async function runTriage(button) {
  try {
    await withBusy(button, () => api("/api/incident/triage", { agent: $("agent-select").value }));
    await loadIncident();
  } catch (e) { setKids($("triage-result"), errorBox(e.message)); }
}

async function approveFix(id, button) {
  try {
    const view = await withBusy(button, () => api(`/api/incident/proposals/${encodeURIComponent(id)}/approve`, {}));
    renderIncident(view);
  } catch (e) { $("triage-result").prepend(errorBox(e.message)); }
}

// ---- security lab -----------------------------------------------------------------------------

let catalogue = null;

async function loadLabCatalogue() {
  if (!catalogue) catalogue = (await api("/api/lab/attacks")).suites;
}

function verdict(leaked, expectedLeak) {
  if (leaked === undefined) return el("span", { class: "muted", text: "n/a" });
  if (!leaked) return el("span", { class: "result-good", text: "✓ blocked" });
  return el("span", { class: "result-bad", text: expectedLeak === false ? "✗ LEAKED (unexpected)" : "✗ leaked" });
}

async function runLab(suite, button) {
  await loadLabCatalogue();
  $("lab-status").textContent = "Running every attack twice (secure, then unsafe) in a separate process. This can take up to a minute.";
  try {
    const data = await withBusy(button, () => api("/api/lab/run", { suite }));
    const blocked = data.results.filter((r) => !r.secure).length;
    const naiveLeaks = data.results.filter((r) => r.naive).length;
    $("lab-status").textContent = "";
    const rows = data.results.map((r) =>
      el("tr", {},
        el("td", {}, el("strong", { text: r.title }), el("div", { class: "muted small", text: r.attempt })),
        el("td", {}, verdict(r.secure, false)),
        el("td", {}, verdict(r.naive, r.naive_expected),
          !r.naive && !r.naive_expected ? el("div", { class: "muted small", text: "unsafe mode does not remove this check" }) : null),
        el("td", { class: "small", text: r.defence })));
    setKids($("lab-result"), 
      el("div", { class: "row wrap" },
        el("span", { class: "badge good", text: `secure: ${blocked}/${data.results.length} blocked` }),
        el("span", { class: "badge bad", text: `unsafe: ${naiveLeaks} leaked` }),
        suite === "rag" ? el("span", { class: "muted small", text: data.pooled ? "includes the Postgres (row-level security) configurations" : "Postgres not running: siloed configurations only (make pg-up adds the pooled ones)" }) : null),
      el("div", { class: "table-wrap" },
        el("table", {},
          el("thead", {}, el("tr", {}, ["Attack", "Secure", "Unsafe", "What stops it"].map((h) => el("th", { text: h })))),
          el("tbody", {}, rows))));
  } catch (e) { $("lab-status").textContent = ""; setKids($("lab-result"), errorBox(e.message)); }
}

// ---- evals ------------------------------------------------------------------------------------

async function runEvals(button) {
  $("evals-status").textContent = "Running every offline suite…";
  try {
    const { suites } = await withBusy(button, () => api("/api/evals/run", {}));
    $("evals-status").textContent = "";
    setKids($("evals-result"), el("div", { class: "table-wrap" },
      el("table", {},
        el("thead", {}, el("tr", {}, ["Suite", "Cases", "Pass rate", "Tokens in / out", "Gate", "Notes"].map((h) => el("th", { text: h })))),
        el("tbody", {}, suites.map((s) =>
          el("tr", {},
            el("td", { class: "mono", text: s.suite }),
            el("td", { text: String(s.n) }),
            el("td", { text: `${Math.round(s.pass_rate * 1000) / 10}%` }),
            el("td", { class: "mono", text: `${s.tokens_in} / ${s.tokens_out}` }),
            el("td", {}, el("span", { class: "badge " + (s.gate === "pass" ? "good" : "bad"), text: s.gate })),
            el("td", { class: "small" },
              s.problems.map((p) => el("div", { class: "error-line", text: p })),
              s.failing_cases.length ? el("div", { class: "muted", text: `known failing (in baseline): ${s.failing_cases.join(", ")}` }) : null)))))));
  } catch (e) { $("evals-status").textContent = ""; setKids($("evals-result"), errorBox(e.message)); }
}

// ---- wiring -----------------------------------------------------------------------------------

document.addEventListener("DOMContentLoaded", async () => {
  for (const t of document.querySelectorAll(".tab")) t.addEventListener("click", () => selectTab(t.dataset.tab));
  $("login-btn").addEventListener("click", login);
  $("gen-example").addEventListener("change", () => { $("gen-text").value = $("gen-example").value; });
  $("gen-btn").addEventListener("click", (ev) => withBusy(ev.currentTarget, draftChange));
  $("ask-btn").addEventListener("click", ask);
  $("ask-text").addEventListener("keydown", (ev) => { if (ev.key === "Enter") ask(); });
  $("data-ask-btn").addEventListener("click", askData);
  $("data-question").addEventListener("keydown", (ev) => { if (ev.key === "Enter") askData(); });
  $("tenant-select").addEventListener("change", async () => { await loadPeople(null); await login(); });
  $("person-select").addEventListener("change", login);
  $("asst-ask-btn").addEventListener("click", askAssistant);
  $("asst-question").addEventListener("keydown", (ev) => { if (ev.key === "Enter") askAssistant(); });
  $("audit-refresh").addEventListener("click", loadAudit);
  $("audit-verify").addEventListener("click", (ev) => verifyAudit(ev.currentTarget));
  $("incident-start").addEventListener("click", (ev) => startIncident(ev.currentTarget));
  $("triage-btn").addEventListener("click", (ev) => runTriage(ev.currentTarget));
  $("evals-btn").addEventListener("click", (ev) => runEvals(ev.currentTarget));
  for (const b of document.querySelectorAll(".lab-btn")) b.addEventListener("click", (ev) => runLab(b.dataset.suite, ev.currentTarget));
  await loadSession();
  await refreshTab();
});
