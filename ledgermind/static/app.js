/* Ledgermind UI: vanilla JS, no build step. Talks to the FastAPI server in ledgermind/server.py. */
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const state = { status: null, currency: "INR", busy: false, dashboard: null, showAllNotices: false };

const TOOL_LABELS = {
  spending_summary: "Spending summary", detect_subscriptions: "Subscription scan", detect_anomalies: "Anomaly check",
  categorize_transactions: "Categorise", correct_category: "Correct category", import_transactions: "Import",
  set_budget: "Set budget", budget_check: "Budget check", set_goal: "Set goal", goal_progress: "Goal progress",
  what_if_simulator: "What-if simulator", monthly_review: "Monthly review", remember: "Remember", forget: "Forget",
  recall_memories: "Recall memories",
};
const TYPE_LABELS = {
  profile: "Profile", goal: "Goals", habit: "Habits", recurring: "Recurring", preference: "Preferences",
  correction: "Corrections", decision: "Decisions",
};
const GROUP_ORDER = ["goal", "profile", "preference", "correction", "decision", "habit", "recurring"];
const SOURCE_LABELS = { user: "you told me", chat: "learned in chat", consolidation: "summarised", detector: "spotted in your data" };
const ROLE_INFO = {
  fast: ["Fast", "Bulk and background work: categorising merchants, extracting memories."],
  agent: ["Agent", "The conversation: picks tools, chains them, writes most answers."],
  deep: ["Deep reasoning", "What-if scenarios, goal plans, anomaly explanations and monthly reviews."],
};

// ------------------------------------------------------------------ helpers

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k === "html") node.innerHTML = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

async function api(path, opts = {}) {
  const init = { method: opts.method || "GET", headers: {} };
  if (opts.json !== undefined) {
    init.body = JSON.stringify(opts.json);
    init.headers["Content-Type"] = "application/json";
  } else if (opts.body) init.body = opts.body;
  const res = await fetch(path, init);
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (_) { /* not JSON */ }
    const err = new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    err.status = res.status;
    throw err;
  }
  const type = res.headers.get("content-type") || "";
  return type.includes("json") ? res.json() : res.text();
}

function money(x, opts = {}) {
  if (x === null || x === undefined || Number.isNaN(Number(x))) return "–";
  const v = Number(x);
  const cur = state.currency;
  const abs = Math.abs(v);
  const digits = opts.decimals ?? (abs < 1000 && Math.round(abs) !== abs ? 2 : 0);
  return new Intl.NumberFormat(cur === "INR" ? "en-IN" : "en-US", {
    style: "currency", currency: cur, minimumFractionDigits: digits, maximumFractionDigits: digits,
  }).format(v);
}

/** Short axis label. `scale` is the largest value on the axis, so every tick uses the same unit. */
function compact(x, scale = x) {
  const v = Math.abs(Number(x)), top = Math.abs(Number(scale));
  const sym = state.currency === "INR" ? "₹" : "$";
  if (v === 0) return `${sym}0`;
  const units = state.currency === "INR" ? [[1e7, "Cr"], [1e5, "L"], [1e3, "k"]] : [[1e6, "M"], [1e3, "k"]];
  const [div, unit] = units.find(([d]) => top >= d) || [1, ""];
  return `${sym}${+(v / div).toFixed(div === 1 ? 0 : 1)}${unit}`;
}

function pct(x, { signed = true } = {}) {
  if (x === null || x === undefined) return "";
  return `${signed && x > 0 ? "+" : ""}${x}%`;
}

function ms(n) {
  if (n === null || n === undefined) return "";
  return n >= 1000 ? `${(n / 1000).toFixed(1)} s` : `${n} ms`;
}

function monthLabel(ym, opts = { month: "short" }) {
  const [y, m] = ym.split("-").map(Number);
  return new Date(y, m - 1, 1).toLocaleDateString("en-GB", opts);
}

function prettyDate(iso) {
  if (!iso) return "";
  return new Date(iso.length === 10 ? iso + "T00:00:00" : iso).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

function modelClass(label = "") {
  const l = label.toLowerCase();
  return ["nano", "super", "ultra", "lightning"].find((k) => l.includes(k)) || "";
}

function modelBadge(label, latency) {
  return el("span", { class: `model-badge ${modelClass(label)}`, title: label },
    label.replace(/^Nemotron\s*/, "Nemotron "), latency !== undefined && latency !== null ? el("span", { class: "ms" }, ms(latency)) : null);
}

let toastTimer;
function toast(message, kind = "") {
  const t = $("#toast");
  t.textContent = message;
  t.className = `toast ${kind}`;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, kind === "error" ? 7000 : 4500);
}

/** Small, safe Markdown: escapes HTML first, then headings, lists, tables, bold, italics, code and local links. */
function md(src) {
  const inline = (s) => esc(s)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[\s(])_([^_]+)_(?=[\s).,;:!?]|$)/g, "$1<em>$2</em>")
    .replace(/(^|[\s(])\*([^*\s][^*]*)\*(?=[\s).,;:!?]|$)/g, "$1<em>$2</em>")
    .replace(/\[([^\]]+)\]\((\/[^)\s]*|https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  const lines = String(src || "").replace(/\r/g, "").split("\n");
  const out = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }
    let m;
    if ((m = line.match(/^(#{1,3})\s+(.*)$/))) { out.push(`<h${m[1].length}>${inline(m[2])}</h${m[1].length}>`); i++; continue; }
    if (/^\s*(---|\*\*\*)\s*$/.test(line)) { out.push("<hr>"); i++; continue; }
    if (/^\s*\|.*\|\s*$/.test(line)) {
      const rows = [];
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) rows.push(lines[i++]);
      const cells = (r) => r.trim().slice(1, -1).split("|").map((c) => c.trim());
      const sep = rows[1] && /^[\s|:-]+$/.test(rows[1]);
      const align = sep ? cells(rows[1]).map((c) => (c.endsWith(":") ? ' class="num"' : "")) : [];
      const head = sep ? `<thead><tr>${cells(rows[0]).map((c, j) => `<th${align[j] || ""}>${inline(c)}</th>`).join("")}</tr></thead>` : "";
      const body = rows.slice(sep ? 2 : 0).map((r) => `<tr>${cells(r).map((c, j) => `<td${align[j] || ""}>${inline(c)}</td>`).join("")}</tr>`).join("");
      out.push(`<div class="table-wrap"><table>${head}<tbody>${body}</tbody></table></div>`);
      continue;
    }
    if (/^\s*([-*•]|\d+[.)])\s+/.test(line)) {
      const ordered = /^\s*\d+[.)]\s+/.test(line);
      const items = [];
      while (i < lines.length && /^\s*([-*•]|\d+[.)])\s+/.test(lines[i])) items.push(lines[i++].replace(/^\s*([-*•]|\d+[.)])\s+/, ""));
      out.push(`<${ordered ? "ol" : "ul"}>${items.map((t) => `<li>${inline(t)}</li>`).join("")}</${ordered ? "ol" : "ul"}>`);
      continue;
    }
    const para = [];
    while (i < lines.length && lines[i].trim() && !/^(#{1,3}\s|\s*\||\s*([-*•]|\d+[.)])\s+)/.test(lines[i])) para.push(lines[i++]);
    out.push(`<p>${para.map(inline).join("<br>")}</p>`);
  }
  return out.join("");
}

function pretty(obj) {
  return JSON.stringify(obj, null, 2);
}

// ------------------------------------------------------------------ tabs + menu

function showTab(name) {
  const tabs = ["overview", "memory", "privacy", "models"];
  const narrow = window.matchMedia("(max-width: 900px)").matches;
  if (name === "chat") {
    if (narrow) $("#tab-chat").scrollIntoView({ behavior: "smooth" });
    $("#input").focus();
    return;
  }
  if (!tabs.includes(name)) name = "overview";
  for (const t of tabs) $(`#tab-${t}`).hidden = t !== name;
  document.querySelectorAll(".tabs a").forEach((a) => a.classList.toggle("active", a.dataset.tab === name));
  if (name === "memory") loadMemories();
  if (name === "privacy") loadPrivacy();
  if (name === "models") loadModels();
}

window.addEventListener("hashchange", () => showTab(location.hash.slice(1)));

function closeMenu() {
  $("#menuList").hidden = true;
  $("#menuBtn").setAttribute("aria-expanded", "false");
}

document.addEventListener("click", (e) => {
  if (e.target.closest("#menuBtn")) {
    const list = $("#menuList");
    list.hidden = !list.hidden;
    $("#menuBtn").setAttribute("aria-expanded", String(!list.hidden));
    return;
  }
  if (!e.target.closest(".menu")) closeMenu();
  const action = e.target.closest("[data-action]");
  if (action) { closeMenu(); runAction(action.dataset.action); }
  const ask = e.target.closest("[data-ask]");
  if (ask) { askPrefill(ask.dataset.ask); }
});

async function runAction(action) {
  if (action === "import") $("#csvInput").click();
  if (action === "demo") {
    if (state.dashboard && !state.dashboard.empty && !confirm("Replace everything with the demo data? Your ledger, memories, goals and budgets will be cleared.")) return;
    toast("Loading 12 months of demo data…");
    try {
      const r = await api("/api/demo", { method: "POST" });
      toast(`Loaded ${r.added} demo transactions (${prettyDate(r.ledger_covers.from)} to ${prettyDate(r.ledger_covers.to)}).`);
      $("#messages").innerHTML = "";
      renderIntro();
      await refreshAll();
    } catch (err) { toast(err.message, "error"); }
  }
  if (action === "export") location.href = "/api/export";
  if (action === "wipe") {
    if (!confirm("Delete every transaction, memory, goal, budget and log from this machine? This cannot be undone.")) return;
    await api("/api/wipe", { method: "POST" });
    $("#messages").innerHTML = "";
    renderIntro();
    toast("Everything was deleted.");
    await refreshAll();
  }
}

// ------------------------------------------------------------------ import

async function importFile(file) {
  if (!file) return;
  const form = new FormData();
  form.append("file", file);
  form.append("account", file.name.replace(/\.[^.]+$/, "").slice(0, 60) || "default");
  toast(`Importing ${file.name}…`);
  try {
    const r = await api("/api/import", { method: "POST", body: form });
    const parts = [`Imported ${r.added} new transactions`];
    if (r.already_imported) parts.push(`${r.already_imported} were already here`);
    if (r.skipped_rows) parts.push(`${r.skipped_rows} rows skipped`);
    toast(parts.join(", ") + ".");
    await refreshAll();
  } catch (err) { toast(err.message, "error"); }
}

$("#csvInput").addEventListener("change", (e) => { importFile(e.target.files[0]); e.target.value = ""; });
let dragDepth = 0;
document.addEventListener("dragenter", (e) => { e.preventDefault(); dragDepth++; document.body.classList.add("dragging"); });
document.addEventListener("dragleave", () => { if (--dragDepth <= 0) { dragDepth = 0; document.body.classList.remove("dragging"); } });
document.addEventListener("dragover", (e) => e.preventDefault());
document.addEventListener("drop", (e) => {
  e.preventDefault();
  dragDepth = 0;
  document.body.classList.remove("dragging");
  if (e.dataTransfer.files[0]) importFile(e.dataTransfer.files[0]);
});

// ------------------------------------------------------------------ status

async function loadStatus() {
  const s = await api("/api/status");
  state.status = s;
  state.currency = s.currency;
  const chip = $("#modeChip");
  if (s.mock) {
    chip.textContent = "Offline demo models";
    chip.className = "chip mode mock";
    chip.title = "No NEBIUS_API_KEY is set, so model replies are simulated. Every figure is still computed from your ledger.";
  } else {
    chip.textContent = "Live: Nebius Token Factory";
    chip.className = "chip mode live";
    chip.title = s.base_url;
  }
  setStrict(s.strict_mode);
  $("#memCount").textContent = s.memories || "";
  return s;
}

function setStrict(on) {
  $("#strictToggle").checked = on;
  $("#strictToggle2").checked = on;
  $("#strictLabel").textContent = on ? "On: aggregates only" : "Off";
}

async function toggleStrict(on) {
  const r = await api("/api/privacy/strict", { method: "POST", json: { enabled: on } });
  setStrict(r.strict_mode);
  toast(r.strict_mode ? "Strict privacy mode on: only totals and counts reach the model." : "Strict privacy mode off.");
}
$("#strictToggle").addEventListener("change", (e) => toggleStrict(e.target.checked));
$("#strictToggle2").addEventListener("change", (e) => toggleStrict(e.target.checked));

// ------------------------------------------------------------------ overview

async function loadDashboard() {
  const d = await api("/api/dashboard");
  state.dashboard = d;
  $("#welcome").hidden = !d.empty;
  $("#dashboard").hidden = d.empty;
  if (d.empty) return;
  state.currency = d.currency;
  renderKpis(d);
  renderTrend(d.trend, d.as_of);
  renderCategories(d.this_month);
  renderBudgets(d.budgets);
  renderGoals(d.goals);
  renderSubs(d.subscriptions);
  renderRecent(d.recent);
  $("#asOf").textContent = `as of ${prettyDate(d.as_of)}`;
}

function kpi(label, value, sub, cls = "") {
  return el("div", { class: `kpi ${cls}` }, el("div", { class: "label" }, label), el("div", { class: "value" }, value),
    el("div", { class: "sub" }, sub));
}

function renderKpis(d) {
  const tm = d.this_month, lm = d.last_month, base = d.baseline, subs = d.subscriptions;
  const box = $("#kpis");
  box.innerHTML = "";
  if (!tm.empty) {
    const ch = tm.compared_with.spent_change_pct;
    box.append(kpi(`Spent, ${tm.period.label}`, money(tm.spent),
      ch === null ? "no comparison yet" : el("span", {}, el("span", { class: ch > 0 ? "up" : "down" }, `${ch > 0 ? "▲" : "▼"} ${Math.abs(ch)}%`), ` vs ${tm.compared_with.period.label}`)));
  }
  if (!lm.empty) {
    box.append(kpi(`${lm.period.label} income`, money(lm.income), `spent ${money(lm.spent)}, invested ${money(lm.saved_or_invested)}`));
  }
  box.append(kpi("Typical monthly surplus", money(base.avg_surplus), `income minus spending, last ${base.months} full months`));
  const flags = [];
  if (subs.price_hikes.length) flags.push(`${subs.price_hikes.length} price rise`);
  if (subs.possibly_forgotten.length) flags.push(`${subs.possibly_forgotten.length} possibly forgotten`);
  box.append(kpi("Subscriptions", `${money(subs.monthly_total, { decimals: 0 })}/mo`,
    `${subs.count} active${flags.length ? ", " + flags.join(", ") : ""}`));
}

function renderTrend(trend, asOf) {
  const W = 680, H = 230, padL = 46, padB = 26, padT = 10;
  const max = Math.max(1, ...trend.flatMap((t) => [t.income, t.spent]));
  const nice = (() => { const p = 10 ** Math.floor(Math.log10(max)); return Math.ceil(max / p) * p; })();
  const plotW = W - padL - 6, plotH = H - padB - padT;
  const gw = plotW / trend.length, bw = Math.min(14, gw / 3);
  const y = (v) => padT + plotH - (v / nice) * plotH;
  const current = asOf.slice(0, 7);
  let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Income, spending and savings for the last 12 months">`;
  for (let k = 0; k <= 4; k++) {
    const v = (nice / 4) * k, yy = y(v);
    svg += `<line class="gridline" x1="${padL}" x2="${W - 6}" y1="${yy}" y2="${yy}"/><text x="${padL - 6}" y="${yy + 4}" text-anchor="end">${compact(v, nice)}</text>`;
  }
  const pts = [];
  trend.forEach((t, i) => {
    const x0 = padL + i * gw + gw / 2;
    const partial = t.month === current ? " partial" : "";
    const tip = `${monthLabel(t.month, { month: "long", year: "numeric" })}${partial ? " (so far)" : ""}: income ${money(t.income)}, spent ${money(t.spent)}, saved ${money(t.saved)}`;
    svg += `<g><title>${esc(tip)}</title>`;
    svg += `<rect class="bar-income${partial}" x="${x0 - bw - 1}" y="${y(t.income)}" width="${bw}" height="${Math.max(0, y(0) - y(t.income))}" rx="3"/>`;
    svg += `<rect class="bar-spent${partial}" x="${x0 + 1}" y="${y(t.spent)}" width="${bw}" height="${Math.max(0, y(0) - y(t.spent))}" rx="3"/></g>`;
    svg += `<text x="${x0}" y="${H - 8}" text-anchor="middle">${monthLabel(t.month)}</text>`;
    pts.push([x0, y(t.saved)]);
  });
  svg += `<polyline class="line-saved" points="${pts.map((p) => p.join(",")).join(" ")}"/>`;
  svg += pts.map(([px, py]) => `<circle class="dot-saved" cx="${px}" cy="${py}" r="2.5"/>`).join("");
  $("#trend").innerHTML = svg + "</svg>";
}

function renderCategories(tm) {
  const box = $("#categories");
  box.innerHTML = "";
  if (tm.empty) { box.append(el("p", { class: "empty-note" }, "No spending yet this month.")); return; }
  $("#catTitle").textContent = `${tm.period.label} by category`;
  const rows = tm.by_category.slice(0, 7);
  const top = Math.max(...rows.map((r) => r.spent), 1);
  const list = el("div", { class: "bars" });
  for (const r of rows) {
    const change = r.change_pct === null ? "new" : `${r.change_pct > 0 ? "▲" : r.change_pct < 0 ? "▼" : ""}${Math.abs(r.change_pct)}%`;
    list.append(el("div", { class: "bar-row", title: `${r.category}: ${money(r.spent)} (${r.share_pct}% of spending), previously ${money(r.previous)}` },
      el("span", { class: "name" }, r.category),
      el("div", { class: "bar-track" }, el("div", { class: "bar-fill", style: `width:${(r.spent / top) * 100}%` })),
      el("span", { class: "amt" }, money(r.spent), el("small", { class: r.change_pct > 0 ? "up" : r.change_pct < 0 ? "down" : "" }, change))));
  }
  box.append(list);
}

function renderBudgets(rows) {
  const box = $("#budgets");
  box.innerHTML = "";
  if (!rows.length) {
    box.append(el("p", { class: "empty-note" }, "No budgets yet. Try “Set a dining budget of 10000” in the chat."));
    return;
  }
  const d = state.dashboard;
  const day = Number(d.as_of.slice(8, 10)), days = Number(d.month_end.slice(8, 10));
  for (const b of rows) {
    const label = { on_track: "On track", at_risk: "At risk", over: "Over" }[b.status];
    box.append(el("div", { class: "item" },
      el("div", { class: "item-head" }, el("span", { class: "title" }, b.category), el("span", { class: `status ${b.status}` }, label)),
      el("div", { class: `progress ${b.status}`, title: `${b.pct_used}% used; the marker shows how far through the month we are` },
        el("span", { style: `width:${Math.min(b.pct_used, 100)}%` }), el("i", { class: "pace", style: `left:${(day / days) * 100}%` })),
      el("div", { class: "meta" }, `${money(b.spent)} of ${money(b.monthly_limit)} · on pace for ${money(b.projected_month_end)}`)));
  }
}

function renderGoals(g) {
  const box = $("#goals");
  box.innerHTML = "";
  if (!g.goals || !g.goals.length) {
    box.append(el("p", { class: "empty-note" }, "No goals yet. Tell Ledgermind what you are saving for."));
    return;
  }
  for (const goal of g.goals) {
    const fits = goal.needed_per_month === null ? null : g.fits_in_surplus;
    box.append(el("div", { class: "item" },
      el("div", { class: "item-head" }, el("span", { class: "title" }, goal.name),
        fits === null ? el("span", { class: "status" }, "no deadline") : el("span", { class: `status ${fits ? "fits" : "tight"}` }, fits ? "Fits your surplus" : "Tight")),
      el("div", { class: "progress" }, el("span", { style: `width:${Math.min(goal.pct_done, 100)}%` })),
      el("div", { class: "meta" }, `${money(goal.saved)} of ${money(goal.target)}`,
        goal.deadline ? ` · ${money(goal.needed_per_month)}/month until ${prettyDate(goal.deadline)}` : "",
        goal.finish_date_using_full_surplus ? ` · ${goal.months_to_finish_using_full_surplus} months at your full surplus` : "")));
  }
}

function renderSubs(s) {
  const box = $("#subs");
  box.innerHTML = "";
  if (!s.items.length) { box.append(el("p", { class: "empty-note" }, "No subscriptions detected yet.")); return; }
  for (const it of s.items.slice(0, 6)) {
    const flags = el("div", { class: "flags" });
    if (it.flags.includes("price_hike") && it.price_change) flags.append(el("span", { class: "flag price_hike" }, `Price up ${it.price_change.change_pct}%`));
    if (it.flags.includes("possibly_forgotten")) flags.append(el("span", { class: "flag possibly_forgotten" }, "Possibly forgotten"));
    box.append(el("div", { class: "sub-row" },
      el("span", {}, el("strong", {}, it.merchant), el("span", { class: "muted small" }, ` · ${it.cadence}`)),
      el("span", {}, money(it.amount)), flags.childNodes.length ? flags : null));
  }
  box.append(el("div", { class: "subs-total" }, el("span", {}, "Per year"), el("span", {}, money(s.yearly_total, { decimals: 0 }))));
}

function renderRecent(rows) {
  const t = el("table", {}, el("thead", {}, el("tr", {}, el("th", {}, "Date"), el("th", {}, "Merchant"), el("th", {}, "Category"), el("th", { class: "num" }, "Amount"))));
  const body = el("tbody");
  for (const r of rows) {
    body.append(el("tr", {}, el("td", {}, prettyDate(r.date)), el("td", {}, r.merchant), el("td", {}, el("span", { class: "cat-pill" }, r.category)),
      el("td", { class: `num ${r.amount > 0 ? "down" : ""}` }, money(r.amount))));
  }
  t.append(body);
  $("#recent").replaceChildren(t);
}

// ------------------------------------------------------------------ proactive notices

async function loadNotices() {
  const box = $("#notices");
  let data;
  try { data = await api("/api/notices"); } catch (_) { box.innerHTML = ""; return; }
  box.innerHTML = "";
  if (state.dashboard?.empty) return;
  const head = el("div", { class: "notices-head" }, el("h2", {}, "Ledgermind noticed"),
    el("button", { class: "link", onclick: runChecks, title: "Run the proactive checks now instead of waiting for the schedule" }, "Run checks now"));
  box.append(head);
  if (!data.notices.length) { box.append(el("p", { class: "empty-note" }, "Nothing new. Checks run in the background every hour.")); return; }
  const shown = state.showAllNotices ? data.notices : data.notices.slice(0, 3);
  for (const n of shown) {
    const icon = { budget: "%", anomaly: "!", subscription: "↻", digest: "≡", price_hike: "↑" }[n.kind] || "•";
    box.append(el("div", { class: `notice ${n.severity || ""}` },
      el("div", { class: "notice-icon" }, icon),
      el("div", {}, el("div", { class: "title" }, n.title), el("div", { class: "body" }, n.body),
        el("div", { class: "eyebrow" }, prettyDate(n.created_at))),
      el("div", { class: "actions" },
        n.ask ? el("button", { onclick: () => askPrefill(n.ask, true) }, "Ask about this") : null,
        el("button", { onclick: () => dismissNotice(n.id), title: "Dismiss" }, "Dismiss"))));
  }
  if (data.notices.length > 3) {
    box.append(el("button", { class: "link", style: "justify-self:start", onclick: () => { state.showAllNotices = !state.showAllNotices; loadNotices(); } },
      state.showAllNotices ? "Show fewer" : `Show ${data.notices.length - 3} more`));
  }
}

async function runChecks() {
  const r = await api("/api/proactive/run", { method: "POST" });
  toast(r.created ? `${r.created} new notice${r.created > 1 ? "s" : ""}.` : "Checks ran; nothing new.");
  await loadNotices();
  loadMemories();
}

async function dismissNotice(id) {
  await api(`/api/notices/${id}/dismiss`, { method: "POST" });
  loadNotices();
}

// ------------------------------------------------------------------ memory

async function loadMemories(flashIds = []) {
  const data = await api("/api/memories");
  $("#memCount").textContent = data.memories.length || "";
  const sel = $("#memType");
  if (!sel.options.length) {
    for (const t of data.types) sel.append(el("option", { value: t }, TYPE_LABELS[t].replace(/s$/, "")));
    sel.value = "preference";
  }
  const list = $("#memList");
  list.innerHTML = "";
  if (!data.memories.length) {
    list.append(el("p", { class: "empty-note" }, "Nothing yet. Chat with Ledgermind, set a goal or a budget, or add a memory above."));
    return;
  }
  // What the user said first; bills and subscriptions spotted in the data last, folded away when there are many.
  for (const type of GROUP_ORDER.filter((t) => data.types.includes(t))) {
    const items = data.memories.filter((m) => m.type === type);
    if (!items.length) continue;
    const rows = items.map((m) => memoryRow(m, flashIds.includes(m.id)));
    const title = `${TYPE_LABELS[type]} (${items.length})`;
    list.append(type === "recurring" && items.length > 4
      ? el("details", { class: "mem-group" }, el("summary", {}, el("h2", { style: "display:inline" }, title)), ...rows)
      : el("div", { class: "mem-group" }, el("h2", {}, title), ...rows));
  }
}

function memoryRow(m, flash) {
  const dots = el("span", { class: "imp", title: `importance ${m.importance}` });
  for (let k = 1; k <= 5; k++) dots.append(el("i", { class: m.importance >= k / 5 - 0.05 ? "on" : "" }));
  const content = el("div", { class: "content" }, m.content);
  const row = el("div", { class: `mem${m.pinned ? " pinned" : ""}${flash ? " flash" : ""}`, "data-id": m.id }, content,
    el("div", { class: "tools" },
      el("button", { class: m.pinned ? "on" : "", title: m.pinned ? "Unpin" : "Pin: always consider, never prune", onclick: () => patchMemory(m.id, { pinned: !m.pinned }) }, m.pinned ? "Pinned" : "Pin"),
      el("button", { title: "Edit", onclick: () => editMemory(row, m) }, "Edit"),
      el("button", { title: "Delete", onclick: () => deleteMemory(m.id) }, "Delete")),
    el("div", { class: "meta" }, dots, el("span", {}, SOURCE_LABELS[m.source] || m.source),
      el("span", {}, m.use_count ? `used ${m.use_count}×` : "not used yet"), el("span", {}, `updated ${prettyDate(m.updated_at)}`)));
  return row;
}

function editMemory(row, m) {
  const input = el("input", { value: m.content, maxlength: 300 });
  const type = el("select", {}, ...Object.keys(TYPE_LABELS).map((t) => el("option", { value: t, selected: t === m.type }, TYPE_LABELS[t].replace(/s$/, ""))));
  const save = async () => { await patchMemory(m.id, { content: input.value.trim(), type: type.value }); };
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") save(); if (e.key === "Escape") loadMemories(); });
  const box = row.querySelector(".content");
  box.replaceChildren(el("div", { style: "display:flex;gap:6px" }, type, input, el("button", { class: "primary", onclick: save }, "Save")));
  input.focus();
}

async function patchMemory(id, body) {
  try { await api(`/api/memories/${id}`, { method: "PATCH", json: body }); } catch (err) { toast(err.message, "error"); }
  loadMemories();
}

async function deleteMemory(id) {
  await api(`/api/memories/${id}`, { method: "DELETE" });
  toast("Forgotten.");
  loadMemories();
}

$("#memForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const content = $("#memText").value.trim();
  if (!content) return;
  const r = await api("/api/memories", { method: "POST", json: { content, type: $("#memType").value } });
  $("#memText").value = "";
  toast(r.action === "merged" ? "Merged with an existing memory." : r.action === "unchanged" ? "Already remembered." : "Remembered.");
  loadMemories([r.memory.id]);
});

$("#consolidateBtn").addEventListener("click", async () => {
  const r = await api("/api/memories/consolidate", { method: "POST" });
  const parts = [];
  const summarised = Object.keys(r.summarised || {}).length;
  if (r.merged) parts.push(`${r.merged} merged`);
  if (r.pruned?.length) parts.push(`${r.pruned.length} pruned`);
  if (summarised) parts.push(`${summarised} list${summarised > 1 ? "s" : ""} summarised`);
  toast(parts.length ? `Tidied up: ${parts.join(", ")}.` : "Memory is already tidy.");
  loadMemories();
});

// ------------------------------------------------------------------ privacy inspector

const REDACTION_RE = /\[(email|upi_id|iban|ifsc|pan|card|phone|aadhaar|account|name)\]/g;

function highlightRedactions(text) {
  return esc(text).replace(REDACTION_RE, (m) => `<mark class="redacted">${m}</mark>`);
}

async function loadPrivacy() {
  const p = await api("/api/privacy/last");
  setStrict(p.strict);
  const never = $("#neverSent");
  never.innerHTML = "";
  const local = p.never_sent?.transactions_in_local_ledger ?? 0;
  for (const line of [
    `${local.toLocaleString()} transactions stay in the local SQLite ledger`,
    "Raw bank descriptions, UPI handles and reference numbers",
    "Account numbers, card numbers, IFSC, PAN and Aadhaar",
    "Names of people you pay, emails and phone numbers (redacted if typed)",
    "No bank connections at all: CSV import only, by design",
  ]) never.append(el("li", {}, line));
  const box = $("#privacyCalls");
  box.innerHTML = "";
  if (!p.calls.length) { box.append(el("p", { class: "empty-note" }, "No model calls yet. Ask something in the chat, then come back here.")); return; }
  box.append(el("h2", { class: "muted small", style: "margin:16px 0 8px;text-transform:uppercase;letter-spacing:.06em" },
    `Last turn: ${p.calls.length} model call${p.calls.length > 1 ? "s" : ""}`));
  for (const c of p.calls) {
    const reds = Object.entries(c.redactions || {});
    const card = el("article", { class: "card pcall" },
      el("div", { class: "pcall-head" }, el("span", { class: "task" }, c.task), el("span", { class: "tag" }, `${c.chars.toLocaleString()} characters`),
        c.strict ? el("span", { class: "tag held" }, "strict mode") : null,
        reds.length ? reds.map(([k, v]) => el("span", { class: "tag red" }, `${v} ${k.replace("_", " ")} redacted`)) : el("span", { class: "tag" }, "nothing needed redacting")),
      c.withheld.length ? el("div", { class: "small muted", style: "margin-top:6px" }, el("strong", {}, "Kept local: "), c.withheld.join("; ")) : null);
    const payload = el("div", { class: "payload" });
    for (const m of c.payload) {
      const text = typeof m.content === "string" ? m.content : pretty(m.content ?? m.tool_calls ?? "");
      const label = `${m.role}${m.name ? ` · ${m.name}` : ""}${m.tool_calls ? " · tool calls" : ""} · ${text.length.toLocaleString()} chars`;
      const pre = el("pre", { html: highlightRedactions(m.tool_calls ? pretty(m.tool_calls) : text) });
      payload.append(el("details", { class: "pmsg", open: m.role === "user" || m.role === "tool" }, el("summary", {}, label), pre));
    }
    card.append(payload);
    box.append(card);
  }
}
$("#privacyRefresh").addEventListener("click", loadPrivacy);

// ------------------------------------------------------------------ models

async function loadModels() {
  const s = state.status || (await loadStatus());
  const roles = $("#roles");
  roles.innerHTML = "";
  for (const [role, chain] of Object.entries(s.roles)) {
    const [title, desc] = ROLE_INFO[role] || [role, ""];
    const ch = el("div", { class: "chain" });
    chain.forEach((m, i) => { if (i) ch.append(el("span", { class: "arrow" }, "→")); ch.append(modelBadge(m.label)); });
    roles.append(el("article", { class: "card role-card" }, el("h3", {}, title), el("p", {}, desc), ch,
      el("div", { class: "small muted", style: "margin-top:8px;font-family:var(--mono);font-size:11px" }, chain[0].id)));
  }
  const calls = await api("/api/calls?limit=200");
  const stats = {};
  for (const c of calls) {
    const d = JSON.parse(c.detail || "{}");
    const key = d.label || c.model;
    stats[key] ??= { n: 0, ms: 0, tokens: 0, cost: 0, failed: 0 };
    const st = stats[key];
    st.n++; st.ms += c.latency_ms; st.tokens += c.prompt_tokens + c.completion_tokens; st.cost += c.cost_usd; st.failed += c.ok ? 0 : 1;
  }
  const head = el("thead", {}, el("tr", {}, ...["Model", "Calls", "Avg latency", "Tokens", "Cost (USD)", "Failures"].map((h, i) => el("th", { class: i ? "num" : "" }, h))));
  const body = el("tbody");
  for (const [label, st] of Object.entries(stats).sort((a, b) => b[1].n - a[1].n)) {
    body.append(el("tr", {}, el("td", {}, modelBadge(label)), el("td", { class: "num" }, st.n), el("td", { class: "num" }, ms(Math.round(st.ms / st.n))),
      el("td", { class: "num" }, st.tokens.toLocaleString()), el("td", { class: "num" }, `$${st.cost.toFixed(5)}`), el("td", { class: "num" }, st.failed)));
  }
  $("#modelStats").replaceChildren(calls.length ? el("table", {}, head, body) : el("p", { class: "empty-note" }, "No calls yet."));
  const t = el("table", {}, el("thead", {}, el("tr", {}, ...["Time", "Role", "Task", "Model", "Latency", "Tokens", "Cost", "Notes"].map((h) => el("th", {}, h)))));
  const tb = el("tbody");
  for (const c of calls.slice(0, 60)) {
    const d = JSON.parse(c.detail || "{}");
    const notes = [];
    if (!c.ok) notes.push(d.error || "failed");
    if (d.fallback_from?.length) notes.push(`fell back from ${d.fallback_from.join(", ")}`);
    if (d.attempts > 1) notes.push(`${d.attempts} attempts`);
    if (d.mock) notes.push("simulated");
    tb.append(el("tr", {}, el("td", {}, new Date(c.ts).toLocaleTimeString()), el("td", {}, c.role), el("td", {}, c.task),
      el("td", {}, modelBadge(d.label || c.model)), el("td", { class: "num" }, ms(c.latency_ms)),
      el("td", { class: "num" }, (c.prompt_tokens + c.completion_tokens).toLocaleString()), el("td", { class: "num" }, `$${c.cost_usd.toFixed(5)}`),
      el("td", { class: notes.length && !c.ok ? "err" : "muted" }, notes.join("; "))));
  }
  t.append(tb);
  $("#calls").replaceChildren(calls.length ? t : el("p", { class: "empty-note" }, "No calls yet."));
}
$("#callsRefresh").addEventListener("click", loadModels);

// ------------------------------------------------------------------ chat

const SUGGESTIONS = [
  "Where did my money go last month?",
  "Find my subscriptions",
  "Anything unusual lately?",
  "I'm saving ₹3 lakh for a Japan trip by March",
  "What if I cut dining by 30%?",
  "Give me a monthly review for September",
  "Toit Brewpub is dining, not entertainment",
  "What do you remember about me?",
];

function renderSuggestions() {
  const box = $("#suggestions");
  box.innerHTML = "";
  const list = state.currency === "USD" ? SUGGESTIONS.map((s) => s.replace("₹3 lakh", "$5,000")) : SUGGESTIONS;
  for (const s of list) box.append(el("button", { type: "button", onclick: () => send(s) }, s));
}

function renderIntro() {
  const box = $("#messages");
  if (box.children.length) return;
  box.append(el("div", { class: "msg assistant msg-intro" }, el("div", { class: "bubble md", html: md(
    "**Hi, I'm Ledgermind.** Ask me about your spending, subscriptions, budgets or goals. I compute every figure from " +
    "your ledger, remember what you tell me, and only send summaries to the model.") })));
}

function askPrefill(text, sendNow = false) {
  if (sendNow) { send(text); return; }
  const input = $("#input");
  input.value = text;
  autosize();
  showTab("chat");
  input.focus();
}

function scrollChat() {
  const box = $("#messages");
  box.scrollTop = box.scrollHeight;
}

function renderBadges(container, calls, verified, totalMs) {
  const answerCalls = calls.filter((c) => ["agent", "deep_answer", "guard_retry"].includes(c.task));
  const main = answerCalls[answerCalls.length - 1];
  container.innerHTML = "";
  if (main) container.append(modelBadge(main.label, main.latency_ms));
  if (verified === true) container.append(el("span", { class: "verified", title: "Every figure in this answer was found in the tool results or your messages" }, "✓ figures checked"));
  if (verified === false) container.append(el("span", { class: "unverified", title: "Some figures could not be matched to your ledger" }, "⚠ figures not verified"));
  if (calls.length) {
    const cost = calls.reduce((s, c) => s + (c.cost_usd || 0), 0);
    const t = el("table", {}, el("thead", {}, el("tr", {}, ...["Task", "Model", "Latency", "Tokens", "Cost"].map((h) => el("th", {}, h)))));
    const tb = el("tbody");
    for (const c of calls) {
      tb.append(el("tr", {}, el("td", {}, c.task), el("td", {}, modelBadge(c.label), c.fallback_from?.length ? el("span", { class: "muted" }, ` (fallback)`) : null),
        el("td", { class: "num" }, ms(c.latency_ms)), el("td", { class: "num" }, c.tokens), el("td", { class: "num" }, `$${(c.cost_usd || 0).toFixed(5)}`)));
    }
    t.append(tb);
    container.append(el("details", { class: "calls-detail" },
      el("summary", {}, `${calls.length} model call${calls.length > 1 ? "s" : ""}${totalMs && !calls.some((c) => c.mock) ? ` · ${ms(totalMs)} total` : ""} · $${cost.toFixed(4)}`), t));
  }
}

function stepNode(name, args) {
  let argText = "";
  try { const a = JSON.parse(args || "{}"); argText = Object.entries(a).filter(([, v]) => v !== null && v !== "").map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : v}`).join(" "); } catch (_) { argText = args; }
  const node = el("details", { class: "step running" },
    el("summary", {}, el("span", { class: "s-icon" }), el("span", { class: "s-name" }, TOOL_LABELS[name] || name),
      el("span", { class: "s-args" }, argText), el("span", { class: "s-ms" }, "running…")),
    el("div", { class: "step-body" }));
  return node;
}

function finishStep(node, data) {
  node.classList.remove("running");
  node.classList.toggle("failed", !data.ok);
  node.querySelector(".s-icon").textContent = data.ok ? "✓" : "!";
  node.querySelector(".s-ms").textContent = ms(data.ms);
  const body = node.querySelector(".step-body");
  body.append(el("h4", {}, "What the model saw"), el("pre", { class: "json" }, pretty(data.result)));
  if (data.local) body.append(el("h4", {}, "Full result (kept on this device)"), el("pre", { class: "json" }, pretty(data.local)));
}

function renderUser(text) {
  $("#messages").append(el("div", { class: "msg user" }, text));
}

function renderStored(m) {
  if (m.role === "user") { renderUser(m.content); return; }
  let meta = {};
  try { meta = JSON.parse(m.meta || "{}"); } catch (_) { /* old row */ }
  const wrap = el("div", { class: "msg assistant" });
  if (meta.tools?.length) {
    const steps = el("div", { class: "steps" });
    for (const t of meta.tools) {
      const s = stepNode(t.name, t.arguments);
      s.classList.remove("running");
      s.classList.toggle("failed", !t.ok);
      s.querySelector(".s-icon").textContent = t.ok ? "✓" : "!";
      s.querySelector(".s-ms").textContent = ms(t.ms);
      s.querySelector(".step-body").append(el("p", { class: "small muted" }, "Result details are shown for the live conversation only."));
      steps.append(s);
    }
    wrap.append(steps);
  }
  wrap.append(el("div", { class: "bubble md", html: md(m.content) }));
  const badges = el("div", { class: "badges" });
  renderBadges(badges, meta.calls || [], meta.verified);
  wrap.append(badges);
  $("#messages").append(wrap);
}

async function loadHistory() {
  const rows = await api("/api/history?limit=30");
  const box = $("#messages");
  box.innerHTML = "";
  renderIntro();
  for (const m of rows) renderStored(m);
  scrollChat();
}

async function* sseEvents(response) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const block = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      let type = "message", data = "";
      for (const line of block.split("\n")) {
        if (line.startsWith("event: ")) type = line.slice(7);
        else if (line.startsWith("data: ")) data += line.slice(6);
      }
      try { yield { type, data: JSON.parse(data || "{}") }; } catch (_) { /* ignore malformed */ }
    }
  }
}

async function send(text) {
  text = (text ?? $("#input").value).trim();
  if (!text || state.busy) return;
  state.busy = true;
  $("#sendBtn").disabled = true;
  $("#input").value = "";
  autosize();
  showTab("chat");
  renderUser(text);
  const wrap = el("div", { class: "msg assistant" });
  const memUsed = el("div", { class: "mem-used", hidden: true });
  const steps = el("div", { class: "steps" });
  const bubble = el("div", { class: "bubble md" }, el("span", { class: "thinking" }, el("i"), el("i"), el("i")));
  const extras = el("div", {});
  const badges = el("div", { class: "badges" });
  const learned = el("div", { class: "learned" });
  wrap.append(memUsed, steps, bubble, extras, badges, learned);
  $("#messages").append(wrap);
  scrollChat();
  const stepsById = {};
  let raw = "", review = null, mutated = false;
  const paint = (streaming) => {
    bubble.innerHTML = md(raw);
    bubble.classList.toggle("streaming", streaming);
  };
  try {
    const res = await fetch("/api/chat/stream", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message: text }) });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
    for await (const ev of sseEvents(res)) {
      const d = ev.data;
      if (ev.type === "memories" && d.used.length) {
        memUsed.hidden = false;
        memUsed.replaceChildren(el("details", {}, el("summary", {}, `◆ Using ${d.used.length} memor${d.used.length > 1 ? "ies" : "y"}`),
          el("ul", {}, ...d.used.map((m) => el("li", {}, el("span", { class: "type-pill" }, m.type), " ", m.content)))));
      } else if (ev.type === "token") {
        raw += d.text;
        paint(true);
      } else if (ev.type === "reset") {
        raw = "";
        bubble.replaceChildren(el("span", { class: "thinking" }, el("i"), el("i"), el("i")));
      } else if (ev.type === "tool_call") {
        const node = stepNode(d.name, d.arguments);
        stepsById[d.id] = node;
        steps.append(node);
        bubble.replaceChildren(el("span", { class: "thinking" }, el("i"), el("i"), el("i")));
        raw = "";
      } else if (ev.type === "tool_result") {
        if (stepsById[d.id]) finishStep(stepsById[d.id], d);
        if (d.name === "monthly_review" && d.ok && d.result.export_markdown) review = d.result.export_markdown;
        if (["set_goal", "set_budget", "correct_category", "categorize_transactions", "remember", "forget"].includes(d.name)) mutated = true;
      } else if (ev.type === "replace") {
        raw = d.text;
        paint(false);
      } else if (ev.type === "answer") {
        raw = d.text;
        paint(false);
      } else if (ev.type === "memory_saved") {
        for (const m of d.memories) {
          learned.append(el("span", { class: "chip", title: "Open the memory panel", onclick: () => { location.hash = "#memory"; } },
            `${m.action === "merged" ? "Updated memory" : "Remembered"}: ${m.content}`));
        }
        mutated = true;
      } else if (ev.type === "done") {
        renderBadges(badges, d.calls, d.verified, d.total_ms);
      } else if (ev.type === "error") {
        bubble.classList.add("err");
      }
      scrollChat();
    }
    if (review) {
      const month = review.match(/(\d{4}-\d{2})/)[1];
      extras.append(el("button", { class: "review-btn", onclick: () => openReview(month) }, "Open review · Download or print"));
    }
  } catch (err) {
    bubble.classList.remove("streaming");
    bubble.replaceChildren(el("span", { class: "err" }, `Something went wrong: ${err.message}`));
  } finally {
    state.busy = false;
    $("#sendBtn").disabled = false;
    bubble.classList.remove("streaming");
    scrollChat();
  }
  loadStatus();
  if (mutated) { loadDashboard(); loadMemories(); }
  if (!$("#tab-privacy").hidden) loadPrivacy();
  if (!$("#tab-models").hidden) loadModels();
}

function autosize() {
  const t = $("#input");
  t.style.height = "auto";
  t.style.height = Math.min(t.scrollHeight, 140) + "px";
}

$("#composer").addEventListener("submit", (e) => { e.preventDefault(); send(); });
$("#input").addEventListener("input", autosize);
$("#input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
});

// ------------------------------------------------------------------ monthly review modal

async function openReview(month) {
  const path = `/api/review/${month}.md`;
  try {
    const text = await api(path);
    $("#modalTitle").textContent = `Monthly review: ${monthLabel(month, { month: "long", year: "numeric" })}`;
    $("#modalBody").innerHTML = md(text);
    $("#modalDownload").href = path;
    $("#modalDownload").setAttribute("download", `ledgermind-review-${month}.md`);
    $("#modal").hidden = false;
    $("#modalClose").focus();
  } catch (err) { toast(err.message, "error"); }
}

function closeModal() { $("#modal").hidden = true; }
$("#modalClose").addEventListener("click", closeModal);
$("#modalPrint").addEventListener("click", () => window.print());
$("#modal").addEventListener("click", (e) => { if (e.target.id === "modal") closeModal(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#modal").hidden) closeModal(); });

// ------------------------------------------------------------------ boot

async function refreshAll() {
  await loadStatus();
  await loadDashboard();
  renderSuggestions();
  loadNotices();
  loadMemories();
  if (!$("#tab-privacy").hidden) loadPrivacy();
  if (!$("#tab-models").hidden) loadModels();
}

(async function boot() {
  try {
    await refreshAll();
    await loadHistory();
  } catch (err) {
    toast(`Could not reach the Ledgermind server: ${err.message}`, "error");
  }
  showTab(location.hash.slice(1) || "overview");
})();
