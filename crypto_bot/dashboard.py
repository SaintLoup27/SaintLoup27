"""
Dashboard web — interface visuelle du bot de trading.

Lancer :
    python dashboard.py

Puis ouvrir : http://localhost:5000

Affiche :
  - Valeur totale du portfolio + P&L global
  - Prix en direct des actifs configurés
  - Positions ouvertes avec P&L temps réel
  - Historique des 50 derniers trades
  - Statut du bot (actif/inactif) et logs récents
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, render_template_string
from binance.client import Client

# Ajouter le dossier parent au path pour importer config/utils
sys.path.insert(0, str(Path(__file__).parent))

import config as cfg
from utils.portfolio import get_positions, get_history, get_initial_value

app = Flask(__name__)

# ──────────────────────────────────────────────────────────────────────────────
#  Client Binance (read-only, pas besoin de clés si testnet désactivé)
# ──────────────────────────────────────────────────────────────────────────────

def _client() -> Client | None:
    if not cfg.BINANCE_API_KEY:
        return None
    try:
        return Client(cfg.BINANCE_API_KEY, cfg.BINANCE_API_SECRET, testnet=cfg.TESTNET)
    except Exception:
        return None


def _get_price(client: Client, symbol: str) -> float:
    try:
        return float(client.get_symbol_ticker(symbol=symbol)["price"])
    except Exception:
        return 0.0


def _get_change_24h(client: Client, symbol: str) -> float:
    try:
        return float(client.get_ticker(symbol=symbol)["priceChangePercent"])
    except Exception:
        return 0.0


# ──────────────────────────────────────────────────────────────────────────────
#  API JSON
# ──────────────────────────────────────────────────────────────────────────────

@app.route("/api/portfolio")
def api_portfolio():
    client    = _client()
    positions = get_positions()
    history   = get_history()
    initial   = get_initial_value() or 0

    assets = []
    total_val = total_inv = 0.0

    # Prix en direct pour les actifs configurés (même sans position ouverte)
    all_symbols = set(cfg.ACTIVE_CONFIG["assets"].keys()) | set(positions.keys())

    for symbol in sorted(all_symbols):
        price    = _get_price(client, symbol) if client else 0.0
        change   = _get_change_24h(client, symbol) if client else 0.0
        pos      = positions.get(symbol, {})
        qty      = pos.get("total_qty", 0.0)
        invested = pos.get("total_usdt", 0.0)
        avg      = pos.get("avg_price", 0.0)
        val      = qty * price if qty > 0 else 0.0
        pnl      = val - invested
        pnl_pct  = pnl / invested * 100 if invested > 0 else 0.0

        total_val += val
        total_inv += invested

        assets.append({
            "symbol":    symbol,
            "price":     price,
            "change_24h": change,
            "qty":       qty,
            "avg_price": avg,
            "value":     val,
            "invested":  invested,
            "pnl":       pnl,
            "pnl_pct":   pnl_pct,
            "has_position": qty > 0,
        })

    # Stats globales
    total_pnl     = total_val - total_inv
    total_pnl_pct = total_pnl / total_inv * 100 if total_inv > 0 else 0
    since_initial = total_val - initial if initial > 0 else 0

    nb_wins  = sum(1 for h in history if h["action"] == "SELL" and
                   _trade_pnl(h, history) >= 0)
    nb_total = sum(1 for h in history if h["action"] == "SELL")
    win_rate = nb_wins / nb_total * 100 if nb_total > 0 else 0

    return jsonify({
        "assets":       assets,
        "total_value":  total_val,
        "total_invested": total_inv,
        "total_pnl":    total_pnl,
        "total_pnl_pct": total_pnl_pct,
        "since_initial": since_initial,
        "win_rate":     win_rate,
        "nb_trades":    nb_total,
        "testnet":      cfg.TESTNET,
        "updated_at":   datetime.now(timezone.utc).strftime("%H:%M:%S UTC"),
    })


def _trade_pnl(sell_trade: dict, history: list) -> float:
    """Approximation du P&L d'un trade (vente - dernier achat précédent)."""
    symbol = sell_trade["symbol"]
    sell_ts = sell_trade["ts"]
    buys = [h for h in history
            if h["action"] == "BUY" and h["symbol"] == symbol and h["ts"] < sell_ts]
    if not buys:
        return 0.0
    last_buy = buys[-1]
    return sell_trade["usdt"] - last_buy["usdt"]


@app.route("/api/history")
def api_history():
    history = list(reversed(get_history()[-50:]))  # 50 derniers, plus récent en premier
    return jsonify(history)


@app.route("/api/logs")
def api_logs():
    log_file = Path(__file__).parent / "data" / "bot.log"
    if not log_file.exists():
        return jsonify({"lines": ["Aucun log disponible."]})
    try:
        with open(log_file, encoding="utf-8") as f:
            lines = f.readlines()
        return jsonify({"lines": [l.rstrip() for l in lines[-30:]][::-1]})
    except Exception as e:
        return jsonify({"lines": [f"Erreur lecture logs: {e}"]})


@app.route("/api/status")
def api_status():
    """Vérifie si le bot est en cours d'exécution."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", "bot.py"], capture_output=True, text=True
        )
        running = result.returncode == 0
    except Exception:
        running = False

    return jsonify({
        "running":  running,
        "testnet":  cfg.TESTNET,
        "mode":     "SCALP" if cfg.SCALP_CONFIG.get("enabled") else "ACTIF",
        "pairs":    list(cfg.ACTIVE_CONFIG["assets"].keys()),
        "interval": cfg.ACTIVE_CONFIG["scan_interval_seconds"],
    })


# ──────────────────────────────────────────────────────────────────────────────
#  Page principale
# ──────────────────────────────────────────────────────────────────────────────

HTML = """<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Crypto Bot Dashboard</title>
  <style>
    :root {
      --bg:      #0d1117;
      --card:    #161b22;
      --border:  #30363d;
      --text:    #e6edf3;
      --muted:   #8b949e;
      --green:   #3fb950;
      --red:     #f85149;
      --yellow:  #d29922;
      --blue:    #58a6ff;
      --purple:  #bc8cff;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { background: var(--bg); color: var(--text); font-family: 'Segoe UI', system-ui, sans-serif; font-size: 14px; }

    /* ── Layout ── */
    .topbar { background: var(--card); border-bottom: 1px solid var(--border); padding: 12px 24px;
              display: flex; align-items: center; justify-content: space-between; }
    .topbar h1 { font-size: 18px; font-weight: 600; color: var(--blue); }
    .topbar .status-pill { padding: 4px 12px; border-radius: 20px; font-size: 12px; font-weight: 600; }
    .pill-running { background: rgba(63,185,80,.15); color: var(--green); border: 1px solid var(--green); }
    .pill-stopped { background: rgba(248,81,73,.15); color: var(--red);   border: 1px solid var(--red); }
    .pill-testnet { background: rgba(210,153,34,.15); color: var(--yellow); border: 1px solid var(--yellow); margin-left: 8px; }

    main { padding: 24px; max-width: 1400px; margin: 0 auto; }

    /* ── Cards ── */
    .grid-4 { display: grid; grid-template-columns: repeat(4,1fr); gap: 16px; margin-bottom: 24px; }
    .grid-2 { display: grid; grid-template-columns: repeat(2,1fr); gap: 16px; margin-bottom: 24px; }
    @media (max-width: 1100px) { .grid-4 { grid-template-columns: repeat(2,1fr); } }
    @media (max-width: 700px)  { .grid-4,.grid-2 { grid-template-columns: 1fr; } }

    .card { background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 20px; }
    .card h2 { font-size: 12px; color: var(--muted); text-transform: uppercase; letter-spacing: .08em; margin-bottom: 8px; }
    .card .value { font-size: 28px; font-weight: 700; }
    .card .sub   { font-size: 12px; color: var(--muted); margin-top: 4px; }

    .card-full { background: var(--card); border: 1px solid var(--border); border-radius: 10px;
                 padding: 20px; margin-bottom: 24px; }
    .card-full h2 { font-size: 14px; font-weight: 600; margin-bottom: 16px; color: var(--muted);
                    text-transform: uppercase; letter-spacing: .08em; }

    /* ── Couleurs ── */
    .green  { color: var(--green); }
    .red    { color: var(--red); }
    .yellow { color: var(--yellow); }
    .blue   { color: var(--blue); }
    .muted  { color: var(--muted); }

    /* ── Table actifs ── */
    table { width: 100%; border-collapse: collapse; }
    th { text-align: left; font-size: 11px; color: var(--muted); text-transform: uppercase;
         letter-spacing: .06em; padding: 8px 12px; border-bottom: 1px solid var(--border); }
    td { padding: 10px 12px; border-bottom: 1px solid rgba(48,54,61,.5); }
    tr:last-child td { border-bottom: none; }
    tr:hover td { background: rgba(255,255,255,.02); }
    .badge { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 11px; font-weight: 600; }
    .badge-green { background: rgba(63,185,80,.15); color: var(--green); }
    .badge-red   { background: rgba(248,81,73,.15);  color: var(--red); }
    .badge-gray  { background: rgba(139,148,158,.1); color: var(--muted); }

    /* ── Historique ── */
    .trade-list { display: flex; flex-direction: column; gap: 8px; max-height: 380px; overflow-y: auto; }
    .trade-item { display: flex; justify-content: space-between; align-items: center;
                  padding: 10px 14px; border-radius: 8px; background: rgba(255,255,255,.02);
                  border: 1px solid var(--border); }
    .trade-item .left  { display: flex; align-items: center; gap: 12px; }
    .trade-item .right { text-align: right; }
    .trade-symbol { font-weight: 600; }
    .trade-time   { font-size: 11px; color: var(--muted); }

    /* ── Logs ── */
    .log-box { background: #010409; border: 1px solid var(--border); border-radius: 8px;
               padding: 14px; font-family: 'Cascadia Code', 'Fira Code', monospace; font-size: 12px;
               max-height: 300px; overflow-y: auto; line-height: 1.7; }
    .log-INFO    { color: var(--text); }
    .log-WARNING { color: var(--yellow); }
    .log-ERROR   { color: var(--red); }
    .log-CRITICAL{ color: var(--red); font-weight: 700; }

    /* ── Refresh ── */
    .refresh-bar { display: flex; justify-content: flex-end; margin-bottom: 16px; font-size: 12px; color: var(--muted); }
    .refresh-bar span { background: var(--card); border: 1px solid var(--border); border-radius: 6px; padding: 4px 10px; }

    /* ── Scrollbar ── */
    ::-webkit-scrollbar { width: 6px; height: 6px; }
    ::-webkit-scrollbar-track { background: transparent; }
    ::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }
  </style>
</head>
<body>

<div class="topbar">
  <h1>🤖 Crypto Bot Dashboard</h1>
  <div style="display:flex;align-items:center;gap:8px;">
    <span id="bot-status" class="status-pill pill-stopped">...</span>
    <span id="testnet-pill" class="status-pill pill-testnet" style="display:none">TESTNET</span>
    <span id="updated-at" style="font-size:12px;color:var(--muted)"></span>
  </div>
</div>

<main>
  <!-- Refresh indicator -->
  <div class="refresh-bar">
    <span>Actualisation auto toutes les <b>10 secondes</b></span>
  </div>

  <!-- KPIs -->
  <div class="grid-4">
    <div class="card">
      <h2>Valeur portfolio</h2>
      <div class="value blue" id="total-value">—</div>
      <div class="sub" id="since-initial"></div>
    </div>
    <div class="card">
      <h2>P&L Global</h2>
      <div class="value" id="total-pnl">—</div>
      <div class="sub" id="total-pnl-pct"></div>
    </div>
    <div class="card">
      <h2>Win Rate</h2>
      <div class="value" id="win-rate">—</div>
      <div class="sub" id="nb-trades"></div>
    </div>
    <div class="card">
      <h2>Capital investi</h2>
      <div class="value" id="total-invested">—</div>
      <div class="sub muted">en positions</div>
    </div>
  </div>

  <!-- Actifs -->
  <div class="card-full">
    <h2>Actifs suivis</h2>
    <table>
      <thead>
        <tr>
          <th>Paire</th>
          <th>Prix</th>
          <th>24h</th>
          <th>Quantité</th>
          <th>Prix moy.</th>
          <th>Valeur</th>
          <th>Investi</th>
          <th>P&L</th>
          <th>Statut</th>
        </tr>
      </thead>
      <tbody id="assets-table">
        <tr><td colspan="9" class="muted" style="text-align:center;padding:30px">Chargement...</td></tr>
      </tbody>
    </table>
  </div>

  <div class="grid-2">
    <!-- Historique des trades -->
    <div class="card-full" style="margin-bottom:0">
      <h2>Derniers trades</h2>
      <div class="trade-list" id="trade-list">
        <div class="muted" style="text-align:center;padding:20px">Chargement...</div>
      </div>
    </div>

    <!-- Logs -->
    <div class="card-full" style="margin-bottom:0">
      <h2>Logs récents</h2>
      <div class="log-box" id="log-box">Chargement...</div>
    </div>
  </div>
</main>

<script>
  const fmt  = (n, d=2) => n == null ? '—' : Number(n).toFixed(d);
  const fmtU = (n) => fmt(n,2) + ' USDT';
  const clr  = (n) => n >= 0 ? 'green' : 'red';
  const sign = (n) => n >= 0 ? '+' : '';

  async function loadPortfolio() {
    try {
      const r = await fetch('/api/portfolio');
      const d = await r.json();

      // KPIs
      document.getElementById('total-value').textContent     = fmtU(d.total_value);
      document.getElementById('total-invested').textContent  = fmtU(d.total_invested);

      const pnlEl = document.getElementById('total-pnl');
      pnlEl.textContent  = sign(d.total_pnl) + fmtU(d.total_pnl);
      pnlEl.className    = 'value ' + clr(d.total_pnl);

      document.getElementById('total-pnl-pct').textContent =
        sign(d.total_pnl_pct) + fmt(d.total_pnl_pct,1) + '%';
      document.getElementById('total-pnl-pct').className = 'sub ' + clr(d.total_pnl_pct);

      const wr = document.getElementById('win-rate');
      wr.textContent = fmt(d.win_rate,0) + '%';
      wr.className   = 'value ' + (d.win_rate >= 50 ? 'green' : 'red');
      document.getElementById('nb-trades').textContent = d.nb_trades + ' trades fermés';

      if (d.since_initial !== 0) {
        document.getElementById('since-initial').textContent =
          sign(d.since_initial) + fmtU(d.since_initial) + ' depuis le début';
        document.getElementById('since-initial').className = 'sub ' + clr(d.since_initial);
      }

      document.getElementById('updated-at').textContent = d.updated_at;
      if (d.testnet) document.getElementById('testnet-pill').style.display = '';

      // Table actifs
      const tbody = document.getElementById('assets-table');
      tbody.innerHTML = d.assets.map(a => `
        <tr>
          <td><b>${a.symbol.replace('USDT','')}</b><span class="muted">/USDT</span></td>
          <td><b>${fmt(a.price, a.price > 100 ? 2 : 4)}</b></td>
          <td class="${clr(a.change_24h)}">${sign(a.change_24h)}${fmt(a.change_24h,2)}%</td>
          <td class="${a.has_position ? '' : 'muted'}">${a.has_position ? fmt(a.qty,6) : '—'}</td>
          <td class="muted">${a.has_position ? fmt(a.avg_price, a.avg_price > 100 ? 2 : 4) : '—'}</td>
          <td>${a.has_position ? fmtU(a.value) : '—'}</td>
          <td class="muted">${a.has_position ? fmtU(a.invested) : '—'}</td>
          <td class="${a.has_position ? clr(a.pnl) : 'muted'}">
            ${a.has_position ? sign(a.pnl)+fmtU(a.pnl)+' ('+sign(a.pnl_pct)+fmt(a.pnl_pct,1)+'%)' : '—'}
          </td>
          <td>${a.has_position
            ? `<span class="badge badge-green">EN POSITION</span>`
            : `<span class="badge badge-gray">EN ATTENTE</span>`}
          </td>
        </tr>
      `).join('');
    } catch(e) { console.error('portfolio:', e); }
  }

  async function loadHistory() {
    try {
      const r  = await fetch('/api/history');
      const data = await r.json();
      const el = document.getElementById('trade-list');
      if (!data.length) { el.innerHTML = '<div class="muted" style="text-align:center;padding:20px">Aucun trade enregistré.</div>'; return; }

      el.innerHTML = data.map(h => {
        const isBuy  = h.action === 'BUY';
        const symShort = h.symbol.replace('USDT','');
        const dt = new Date(h.ts).toLocaleString('fr-FR', {day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'});
        return `
        <div class="trade-item">
          <div class="left">
            <span class="badge ${isBuy ? 'badge-green' : 'badge-red'}">${h.action}</span>
            <div>
              <div class="trade-symbol">${symShort}/USDT</div>
              <div class="trade-time">${dt}</div>
            </div>
          </div>
          <div class="right">
            <div>${fmt(h.qty, 6)} ${symShort}</div>
            <div class="muted">@ ${fmt(h.price, h.price > 100 ? 2 : 4)} USDT</div>
            <div class="${isBuy ? 'red' : 'green'}">${isBuy ? '-' : '+'}${fmt(h.usdt,2)} USDT</div>
          </div>
        </div>`;
      }).join('');
    } catch(e) { console.error('history:', e); }
  }

  async function loadLogs() {
    try {
      const r = await fetch('/api/logs');
      const d = await r.json();
      const el = document.getElementById('log-box');
      el.innerHTML = d.lines.map(line => {
        let cls = 'log-INFO';
        if (line.includes('| WARNING ')) cls = 'log-WARNING';
        else if (line.includes('| ERROR '))   cls = 'log-ERROR';
        else if (line.includes('| CRITICAL')) cls = 'log-CRITICAL';
        return `<div class="${cls}">${line.replace(/</g,'&lt;')}</div>`;
      }).join('');
    } catch(e) { console.error('logs:', e); }
  }

  async function loadStatus() {
    try {
      const r = await fetch('/api/status');
      const d = await r.json();
      const el = document.getElementById('bot-status');
      if (d.running) {
        el.textContent = '● BOT ACTIF — ' + d.mode;
        el.className   = 'status-pill pill-running';
      } else {
        el.textContent = '● BOT ARRÊTÉ';
        el.className   = 'status-pill pill-stopped';
      }
    } catch(e) {}
  }

  function refresh() {
    loadPortfolio();
    loadHistory();
    loadLogs();
    loadStatus();
  }

  refresh();
  setInterval(refresh, 10000);
</script>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(HTML)


# ──────────────────────────────────────────────────────────────────────────────
#  Lancement
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    os.makedirs(Path(__file__).parent / "data", exist_ok=True)
    print("\n  Dashboard démarré → http://localhost:5000\n")
    app.run(host="0.0.0.0", port=5000, debug=False)
