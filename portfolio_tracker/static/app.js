/* Suivi de portefeuille — logique d'interface (vanilla JS, aucune dépendance). */

const $  = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

let SNAP = null;             // dernier instantané du portefeuille
let autoTimer = null;

const COLORS = ["#4f8cff", "#2ecc71", "#ffb020", "#b06bff", "#ff5f56", "#22c9c9", "#8b98a9"];

// ── Formatage ────────────────────────────────────────────────────────────────
const money = (v, cur) =>
  v === null || v === undefined || Number.isNaN(v)
    ? "—"
    : new Intl.NumberFormat("fr-FR", { style: "currency", currency: cur || "EUR",
        maximumFractionDigits: Math.abs(v) < 10 ? 2 : 2 }).format(v);

const num = (v, d = 2) =>
  v === null || v === undefined || Number.isNaN(v)
    ? "—"
    : new Intl.NumberFormat("fr-FR", { minimumFractionDigits: d, maximumFractionDigits: d }).format(v);

const pct = (v) => (v === null || v === undefined ? "—" : `${v > 0 ? "+" : ""}${num(v, 2)} %`);
const sign = (v) => (v > 0 ? "pos" : v < 0 ? "neg" : "");
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// ── Chargement ───────────────────────────────────────────────────────────────
async function load(refresh = false) {
  const btn = $("#refreshBtn");
  btn.disabled = true;
  btn.textContent = refresh ? "↻ Mise à jour…" : "↻ Chargement…";
  try {
    const res = await fetch(`/api/portfolio${refresh ? "?refresh=1" : ""}`);
    SNAP = await res.json();
    renderAll();
  } catch (e) {
    $("#quoteInfo").textContent = "Impossible de joindre le serveur.";
  } finally {
    btn.disabled = false;
    btn.textContent = "↻ Actualiser les cours";
  }
}

function renderAll() {
  renderKpis();
  renderPositions();
  renderHistory();
  renderAdvice();
  renderAllocation();
  renderSettings();
}

// ── En-tête / KPIs ───────────────────────────────────────────────────────────
function renderKpis() {
  const t = SNAP.totals, cur = SNAP.base_currency;
  const items = [
    { label: "Valorisation", value: money(t.total, cur) },
    { label: "Investi", value: money(t.invested, cur), small: true },
    { label: "+/- value latente", value: `${money(t.pnl, cur)} (${pct(t.pnl_pct)})`, cls: sign(t.pnl) },
    { label: "Variation du jour", value: `${money(t.day_pnl, cur)} (${pct(t.day_pnl_pct)})`, cls: sign(t.day_pnl), small: true },
    { label: "Réalisé + dividendes", value: money(t.realized + t.dividends, cur), cls: sign(t.realized + t.dividends), small: true },
  ];
  $("#kpis").innerHTML = items.map((k) => `
    <div class="kpi">
      <div class="label">${k.label}</div>
      <div class="value ${k.small ? "small" : ""} ${k.cls || ""}">${k.value}</div>
    </div>`).join("");
  $("#asof").textContent = `${t.n_lines} ligne(s) — cours ${SNAP.stale ? "partiellement indisponibles" : "à jour"}`;
}

// ── Tableau des positions ────────────────────────────────────────────────────
function renderPositions() {
  const rows = SNAP.positions.filter((p) => !p.closed);
  const tbody = $("#positions tbody");
  $("#emptyPositions").hidden = rows.length > 0;
  $("#positions").hidden = rows.length === 0;

  tbody.innerHTML = rows.map((p) => {
    const srcCls = p.source === "manuel" ? "manual" : p.stale ? "stale" : "";
    const options = window.ASSET_CLASSES.map(
      (c) => `<option ${c === p.asset_class ? "selected" : ""}>${c}</option>`).join("");
    return `
    <tr data-symbol="${esc(p.symbol)}">
      <td class="ticker">${esc(p.symbol)}<small title="${esc(p.name)}">${esc(p.name)}</small></td>
      <td><select class="tag class-select">${options}</select></td>
      <td class="num">${num(p.qty, p.qty % 1 ? 4 : 0)}</td>
      <td class="num">${num(p.pru)} ${esc(p.currency)}</td>
      <td class="num">
        <div class="price-cell">
          <input class="price-input" type="number" step="any" value="${p.price ?? ""}"
                 title="Modifier pour saisir un cours à la main">
          <span class="src ${srcCls}" title="${esc(p.source)}${p.quote_age != null ? ` — il y a ${p.quote_age}s` : ""}">
            ${p.source === "manuel" ? "✎" : p.stale ? "⚠" : "●"}
          </span>
        </div>
      </td>
      <td class="num ${sign(p.day_pnl_base)}">${pct(p.change_pct)}</td>
      <td class="num">${money(p.value_base, SNAP.base_currency)}</td>
      <td class="num ${sign(p.pnl_base)}">${money(p.pnl_base, SNAP.base_currency)}<br>
          <span class="muted ${sign(p.pnl_base)}">${pct(p.pnl_pct)}</span></td>
      <td class="num">${num(p.weight, 1)} %</td>
      <td class="num"><button class="btn icon reset-price" title="Revenir au cours du marché">↺</button></td>
    </tr>`;
  }).join("");

  // Cours saisi à la main
  tbody.querySelectorAll(".price-input").forEach((input) => {
    input.addEventListener("change", async (e) => {
      const symbol = e.target.closest("tr").dataset.symbol;
      const value = e.target.value === "" ? null : parseFloat(e.target.value);
      await api(`/api/instruments/${encodeURIComponent(symbol)}`, {
        manual_price: value, pin_manual: value !== null,
      });
      load();
    });
  });
  tbody.querySelectorAll(".reset-price").forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      const symbol = e.target.closest("tr").dataset.symbol;
      await api(`/api/instruments/${encodeURIComponent(symbol)}`, { manual_price: null, pin_manual: false });
      load(true);
    });
  });
  tbody.querySelectorAll(".class-select").forEach((sel) => {
    sel.addEventListener("change", async (e) => {
      const symbol = e.target.closest("tr").dataset.symbol;
      await api(`/api/instruments/${encodeURIComponent(symbol)}`, { asset_class: e.target.value });
      load();
    });
  });

  const sources = [...new Set(SNAP.positions.filter((p) => !p.closed).map((p) => p.source))];
  $("#quoteInfo").textContent = rows.length
    ? `Source : ${sources.join(", ")} — cours différés (~15 min). Clique un cours pour le saisir à la main.`
    : "";
}

// ── Historique ───────────────────────────────────────────────────────────────
function renderHistory() {
  const tx = SNAP.transactions;
  $("#emptyHistory").hidden = tx.length > 0;
  $("#history").hidden = tx.length === 0;
  const labels = { BUY: "Achat", SELL: "Vente", DIV: "Dividende" };
  $("#history tbody").innerHTML = tx.map((t) => `
    <tr>
      <td>${esc(t.date)}</td>
      <td><span class="tag">${labels[t.type] || t.type}</span></td>
      <td class="ticker">${esc(t.symbol)}</td>
      <td class="num">${num(t.qty, t.qty % 1 ? 4 : 0)}</td>
      <td class="num">${num(t.price)}</td>
      <td class="num">${num(t.fees || 0)}</td>
      <td class="num">${num((t.qty || 1) * t.price + (t.type === "BUY" ? (t.fees || 0) : 0))}</td>
      <td class="muted">${esc(t.note || "")}</td>
      <td class="num"><button class="btn icon del" data-id="${t.id}" title="Supprimer">✕</button></td>
    </tr>`).join("");

  $$("#history .del").forEach((btn) => btn.addEventListener("click", async () => {
    if (!confirm("Supprimer cette opération ?")) return;
    await fetch(`/api/transactions/${btn.dataset.id}`, { method: "DELETE" });
    load();
  }));
}

// ── Encadré conseils ─────────────────────────────────────────────────────────
function renderAdvice() {
  const a = SNAP.advice;
  $("#score").textContent = a.score === null ? "" : `santé ${a.score}/100`;
  $("#score").style.borderColor =
    a.score === null ? "" : a.score >= 75 ? "var(--green)" : a.score >= 50 ? "var(--amber)" : "var(--red)";

  $("#tips").innerHTML = a.tips.map((t) => `
    <div class="tip ${t.level}">
      <h3>${esc(t.title)}</h3>
      <p>${esc(t.detail)}</p>
      ${t.action ? `<p class="action">${esc(t.action)}</p>` : ""}
    </div>`).join("");

  $("#checklist").innerHTML = a.checklist.map(
    (c) => `<li class="${c.ok ? "ok" : ""}">${esc(c.label)}</li>`).join("");
}

// ── Répartition (donut SVG maison) ───────────────────────────────────────────
function renderAllocation() {
  const data = Object.entries(SNAP.allocation.by_class).filter(([, v]) => v > 0);
  const total = data.reduce((s, [, v]) => s + v, 0);
  const svg = $("#donut");

  if (!total) {
    svg.innerHTML = `<circle cx="21" cy="21" r="15.9" fill="none" stroke="var(--line)" stroke-width="6"/>`;
    $("#legend").innerHTML = `<span class="muted">Rien à afficher.</span>`;
    $("#byCurrency").innerHTML = "";
    return;
  }

  let offset = 25;                       // décalage pour démarrer en haut
  const circ = 100;                      // circonférence normalisée
  svg.innerHTML = data.map(([label, value], i) => {
    const share = (value / total) * circ;
    const el = `<circle cx="21" cy="21" r="15.9" fill="none" stroke="${COLORS[i % COLORS.length]}"
      stroke-width="6" stroke-dasharray="${share} ${circ - share}" stroke-dashoffset="${offset}">
      <title>${esc(label)} — ${num((value / total) * 100, 1)} %</title></circle>`;
    offset = (offset - share + circ) % circ;
    return el;
  }).join("");

  $("#legend").innerHTML = data.map(([label, value], i) => `
    <div class="row">
      <span class="dot" style="background:${COLORS[i % COLORS.length]}"></span>
      <span>${esc(label)}</span>
      <span class="pct">${num((value / total) * 100, 1)} %</span>
    </div>`).join("");

  $("#byCurrency").innerHTML = Object.entries(SNAP.allocation.by_currency)
    .map(([cur, v]) => `<span class="chip">${esc(cur)} ${num((v / total) * 100, 0)} %</span>`).join("");
}

// ── Réglages ─────────────────────────────────────────────────────────────────
function renderSettings() {
  const s = SNAP.settings, form = $("#settingsForm");
  if (document.activeElement && form.contains(document.activeElement)) return;  // ne pas écraser une saisie
  form.base_currency.value = s.base_currency;
  form.cash.value = s.cash ?? 0;
  form.monthly_contribution.value = s.monthly_contribution ?? 0;
  $("#targets").innerHTML = Object.entries(s.targets || {}).map(([cls, v]) => `
    <label style="flex-direction:row;align-items:center">${esc(cls)}</label>
    <input type="number" step="1" min="0" max="100" data-target="${esc(cls)}" value="${v}">`).join("");
}

$("#settingsForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = e.target;
  const targets = {};
  $$("#targets input").forEach((i) => (targets[i.dataset.target] = parseFloat(i.value || 0)));
  await api("/api/settings", {
    base_currency: form.base_currency.value,
    cash: parseFloat(form.cash.value || 0),
    monthly_contribution: parseFloat(form.monthly_contribution.value || 0),
    targets,
  });
  $("#savedFlag").hidden = false;
  setTimeout(() => ($("#savedFlag").hidden = true), 1800);
  load();
});

// ── Formulaire d'opération ───────────────────────────────────────────────────
$("#txForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  const body = {
    symbol: f.symbol.value.trim().toUpperCase(),
    type: f.type.value,
    qty: parseFloat(f.qty.value || (f.type.value === "DIV" ? 1 : 0)),
    price: parseFloat(f.price.value || 0),
    fees: parseFloat(f.fees.value || 0),
    date: f.date.value,
    note: f.note.value,
  };
  const res = await fetch("/api/transactions", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const data = await res.json();
  if (!res.ok) {
    $("#formError").textContent = data.error || "Erreur lors de l'ajout.";
    $("#formError").hidden = false;
    return;
  }
  $("#formError").hidden = true;
  f.reset();
  hintSymbol = null;
  $("#date").value = new Date().toISOString().slice(0, 10);
  $("#symbolHint").textContent = "";
  load();
});

$("#type").addEventListener("change", (e) => {
  const isDiv = e.target.value === "DIV";
  $("#priceLabel").textContent = isDiv ? "Montant par part" : "Prix unitaire";
  $("#qty").required = !isDiv;
});

// ── Recherche de ticker (autocomplétion Yahoo) ───────────────────────────────
let searchTimer = null;
const box = $("#suggestions");

$("#symbol").addEventListener("input", (e) => {
  clearTimeout(searchTimer);
  const q = e.target.value.trim();
  hintSymbol = null;
  if (q.length < 2) { box.hidden = true; return; }
  searchTimer = setTimeout(async () => {
    const res = await fetch(`/api/search?q=${encodeURIComponent(q)}`);
    const items = await res.json();
    if (!items.length) { box.hidden = true; return; }
    box.innerHTML = items.map((i) => `
      <div data-symbol="${esc(i.symbol)}">
        <strong>${esc(i.symbol)}</strong> — ${esc(i.name)}
        <small>(${esc(i.type)} · ${esc(i.exchange)})</small>
      </div>`).join("");
    box.hidden = false;
    box.querySelectorAll("div[data-symbol]").forEach((d) =>
      d.addEventListener("click", () => pickSymbol(d.dataset.symbol)));
  }, 300);
});

$("#symbol").addEventListener("blur", () => setTimeout(() => (box.hidden = true), 180));
$("#symbol").addEventListener("change", () => {
  const v = $("#symbol").value.trim();
  if (v && !box.querySelector(`div[data-symbol="${v.toUpperCase()}"]`)) showQuoteHint(v.toUpperCase());
});

async function pickSymbol(symbol) {
  $("#symbol").value = symbol;
  box.hidden = true;
  showQuoteHint(symbol);
}

let hintSymbol = null;   // évite deux appels concurrents pour le même ticker

async function showQuoteHint(symbol) {
  if (symbol === hintSymbol) return;
  hintSymbol = symbol;
  $("#symbolHint").textContent = "Recherche du cours…";
  const res = await fetch(`/api/quote/${encodeURIComponent(symbol)}`);
  const q = await res.json();
  if (q.price) {
    $("#symbolHint").textContent = `${q.name || symbol} — ${num(q.price)} ${q.currency} (${q.source})`;
    if (!$("#price").value) $("#price").value = q.price;
  } else {
    $("#symbolHint").textContent = "Cours introuvable — tu pourras le saisir à la main.";
  }
}

// ── Divers ───────────────────────────────────────────────────────────────────
async function api(url, body) {
  return fetch(url, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
}

$("#refreshBtn").addEventListener("click", () => load(true));
$("#autoRefresh").addEventListener("change", (e) => {
  clearInterval(autoTimer);
  autoTimer = e.target.checked ? setInterval(() => load(true), 60_000) : null;
});

$("#date").value = new Date().toISOString().slice(0, 10);
load();
