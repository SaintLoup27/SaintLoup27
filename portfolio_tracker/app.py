"""
Suivi de portefeuille actions / ETF — application web locale.

Lancer :
    pip install -r requirements.txt
    python app.py

Puis ouvrir http://localhost:5001

L'appli ne passe aucun ordre : elle sert uniquement à suivre les lignes que
tu as achetées chez ton courtier, à les valoriser au cours du marché, et à
te donner des repères de gestion.
"""

import csv
import io
import sys
from pathlib import Path

from flask import Flask, jsonify, render_template, request, Response

sys.path.insert(0, str(Path(__file__).parent))

import advisor
import market
import portfolio
import store

app = Flask(__name__)
app.json.ensure_ascii = False


# ──────────────────────────────────────────────────────────────────────────────
#  Pages
# ──────────────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html", asset_classes=store.ASSET_CLASSES)


# ──────────────────────────────────────────────────────────────────────────────
#  API
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/api/portfolio")
def api_portfolio():
    snap = portfolio.compute(refresh=request.args.get("refresh") == "1")
    snap["advice"] = advisor.analyse(snap)
    snap["asset_classes"] = store.ASSET_CLASSES
    return jsonify(snap)


@app.post("/api/transactions")
def api_add_transaction():
    body = request.get_json(force=True, silent=True) or {}
    try:
        tx = store.add_transaction(
            symbol=body.get("symbol", ""),
            kind=body.get("type", "BUY"),
            qty=body.get("qty", 0) or 0,
            price=body.get("price", 0) or 0,
            trade_date=body.get("date", ""),
            fees=body.get("fees", 0) or 0,
            currency=body.get("currency", ""),
            note=body.get("note", ""),
        )
    except (ValueError, TypeError) as exc:
        return jsonify({"error": str(exc)}), 400

    # Renseigne nom + classe d'actif automatiquement à la première saisie
    inst = store.load()["instruments"].get(tx["symbol"], {})
    if not inst.get("name"):
        quote = market.get_quote(tx["symbol"])
        if quote.get("name") or quote.get("yahoo_type"):
            store.update_instrument(tx["symbol"], {
                "name": quote.get("name", ""),
                "asset_class": store.guess_asset_class(quote.get("yahoo_type", "")),
            })
    return jsonify(tx), 201


@app.delete("/api/transactions/<tx_id>")
def api_delete_transaction(tx_id):
    if not store.delete_transaction(tx_id):
        return jsonify({"error": "Transaction introuvable"}), 404
    return jsonify({"ok": True})


@app.post("/api/instruments/<symbol>")
def api_update_instrument(symbol):
    body = request.get_json(force=True, silent=True) or {}
    try:
        return jsonify(store.update_instrument(symbol, body))
    except (ValueError, TypeError) as exc:
        return jsonify({"error": str(exc)}), 400


@app.post("/api/settings")
def api_update_settings():
    body = request.get_json(force=True, silent=True) or {}
    try:
        return jsonify(store.update_settings(body))
    except (ValueError, TypeError) as exc:
        return jsonify({"error": str(exc)}), 400


@app.get("/api/search")
def api_search():
    return jsonify(market.search(request.args.get("q", "")))


@app.get("/api/quote/<symbol>")
def api_quote(symbol):
    quote = market.get_quote(symbol, force=request.args.get("refresh") == "1")
    price, currency = market.normalize_price(quote.get("price"), quote.get("currency", ""))
    return jsonify({**quote, "price": price, "currency": currency})


@app.get("/api/export.csv")
def api_export():
    snap = portfolio.compute()
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")
    writer.writerow(["Date", "Type", "Ticker", "Quantite", "Prix", "Frais", "Devise", "Note"])
    for t in sorted(snap["transactions"], key=lambda t: t["date"]):
        writer.writerow([t["date"], t["type"], t["symbol"], t["qty"], t["price"],
                         t.get("fees", 0), t.get("currency", ""), t.get("note", "")])
    return Response(
        buf.getvalue().encode("utf-8-sig"),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=transactions.csv"},
    )


if __name__ == "__main__":
    print("Portefeuille → http://localhost:5001")
    app.run(host="127.0.0.1", port=5001, debug=False)
