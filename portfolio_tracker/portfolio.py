"""
Moteur de calcul du portefeuille.

À partir des transactions brutes on reconstitue :
  - les positions (quantité, prix de revient unitaire, valorisation)
  - les plus/moins-values latentes et réalisées
  - la répartition par classe d'actif, devise et ligne

Méthode de valorisation : prix moyen pondéré (PMP / PRU), frais d'achat
inclus dans le prix de revient — c'est la convention utilisée par le fisc
français et par la plupart des courtiers.
"""

from datetime import date

import market
import store


def _rate(cache: dict, src: str, dst: str, force: bool = False) -> float:
    """Taux de change mémoïsé sur la durée d'un calcul."""
    key = (src, dst)
    if key not in cache:
        cache[key] = market.get_fx_rate(src, dst, force=force) or 1.0
    return cache[key]


def _build_lots(transactions: list[dict]) -> dict:
    """Rejoue l'historique pour obtenir, par ticker, la position courante."""
    lots: dict[str, dict] = {}
    for tx in sorted(transactions, key=lambda t: (t["date"], t.get("created_at", ""))):
        sym = tx["symbol"]
        pos = lots.setdefault(sym, {
            "symbol": sym, "qty": 0.0, "cost": 0.0, "realized": 0.0,
            "dividends": 0.0, "fees": 0.0, "currency": tx.get("currency") or "",
            "first_date": tx["date"], "last_date": tx["date"], "n_tx": 0,
        })
        pos["n_tx"] += 1
        pos["last_date"] = tx["date"]
        pos["fees"] += tx.get("fees", 0.0)
        if tx.get("currency") and not pos["currency"]:
            pos["currency"] = tx["currency"]

        if tx["type"] == "BUY":
            pos["qty"] += tx["qty"]
            pos["cost"] += tx["qty"] * tx["price"] + tx.get("fees", 0.0)
        elif tx["type"] == "SELL":
            pru = pos["cost"] / pos["qty"] if pos["qty"] > 0 else tx["price"]
            sold = min(tx["qty"], pos["qty"]) if pos["qty"] > 0 else tx["qty"]
            pos["realized"] += sold * (tx["price"] - pru) - tx.get("fees", 0.0)
            pos["qty"] = max(0.0, pos["qty"] - sold)
            pos["cost"] = max(0.0, pos["cost"] - sold * pru)
        elif tx["type"] == "DIV":
            # qty = 0 → le champ prix porte le montant total encaissé
            pos["dividends"] += tx["price"] * (tx["qty"] or 1)
    return lots


def compute(refresh: bool = False) -> dict:
    """Photo complète du portefeuille, prête à être affichée."""
    data = store.load()
    settings = data["settings"]
    base = settings.get("base_currency", "EUR").upper()
    instruments = data.get("instruments", {})

    lots = _build_lots(data["transactions"])
    symbols = [s for s, p in lots.items() if p["qty"] > 1e-9 or p["realized"] or p["dividends"]]
    quotes = market.get_quotes(symbols, force=refresh) if symbols else {}
    fx: dict = {}

    positions = []
    stale_any = False
    for sym in sorted(symbols):
        pos = lots[sym]
        inst = instruments.get(sym, {})
        quote = quotes.get(sym, {})

        price, currency = market.normalize_price(quote.get("price"), quote.get("currency", ""))
        prev, _ = market.normalize_price(quote.get("previous_close"), quote.get("currency", ""))
        source = quote.get("source", "indisponible")

        # Prix manuel : prioritaire si épinglé, sinon utilisé quand le marché ne répond pas
        manual = inst.get("manual_price")
        if manual is not None and (inst.get("pin_manual") or price is None):
            price, prev, source = float(manual), prev if prev else float(manual), "manuel"

        currency = currency or pos["currency"] or base
        rate = _rate(fx, currency, base, force=refresh)

        qty = pos["qty"]
        pru = pos["cost"] / qty if qty > 1e-9 else 0.0
        value = (price or 0.0) * qty
        pnl = value - pos["cost"] if price is not None else 0.0
        day_pnl = ((price - prev) * qty) if (price is not None and prev) else 0.0

        asset_class = inst.get("asset_class") or store.guess_asset_class(quote.get("yahoo_type", ""))
        stale = quote.get("stale") and source not in ("manuel",)
        stale_any = stale_any or bool(stale)

        positions.append({
            "symbol": sym,
            "name": inst.get("name") or quote.get("name") or sym,
            "asset_class": asset_class,
            "exchange": quote.get("exchange", ""),
            "currency": currency,
            "qty": round(qty, 8),
            "pru": round(pru, 4),
            "price": round(price, 4) if price is not None else None,
            "previous_close": round(prev, 4) if prev else None,
            "change_pct": round((price / prev - 1) * 100, 2) if (price and prev) else None,
            "invested": round(pos["cost"], 2),
            "value": round(value, 2),
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl / pos["cost"] * 100, 2) if pos["cost"] > 0 else None,
            "day_pnl": round(day_pnl, 2),
            "realized": round(pos["realized"], 2),
            "dividends": round(pos["dividends"], 2),
            "fees": round(pos["fees"], 2),
            # Montants convertis dans la devise de référence
            "fx_rate": round(rate, 6),
            "value_base": round(value * rate, 2),
            "invested_base": round(pos["cost"] * rate, 2),
            "pnl_base": round(pnl * rate, 2),
            "day_pnl_base": round(day_pnl * rate, 2),
            "realized_base": round(pos["realized"] * rate, 2),
            "dividends_base": round(pos["dividends"] * rate, 2),
            "source": source,
            "quote_age": quote.get("age"),
            "stale": bool(stale),
            "manual_price": manual,
            "pin_manual": bool(inst.get("pin_manual")),
            "first_date": pos["first_date"],
            "last_date": pos["last_date"],
            "closed": qty <= 1e-9,
        })

    open_pos = [p for p in positions if not p["closed"]]
    cash = float(settings.get("cash", 0) or 0)

    invested = sum(p["invested_base"] for p in open_pos)
    value = sum(p["value_base"] for p in open_pos)
    day_pnl = sum(p["day_pnl_base"] for p in open_pos)
    realized = sum(p["realized_base"] for p in positions)
    dividends = sum(p["dividends_base"] for p in positions)
    total = value + cash

    for p in positions:
        p["weight"] = round(p["value_base"] / total * 100, 2) if total > 0 and not p["closed"] else 0.0

    by_class: dict[str, float] = {}
    by_currency: dict[str, float] = {}
    for p in open_pos:
        by_class[p["asset_class"]] = by_class.get(p["asset_class"], 0) + p["value_base"]
        by_currency[p["currency"]] = by_currency.get(p["currency"], 0) + p["value_base"]
    if cash > 0:
        by_class["Liquidités"] = by_class.get("Liquidités", 0) + cash
        by_currency[base] = by_currency.get(base, 0) + cash

    return {
        "base_currency": base,
        "settings": settings,
        "positions": positions,
        "open_positions": open_pos,
        "transactions": sorted(data["transactions"], key=lambda t: t["date"], reverse=True),
        "totals": {
            "invested": round(invested, 2),
            "value": round(value, 2),
            "cash": round(cash, 2),
            "total": round(total, 2),
            "pnl": round(value - invested, 2),
            "pnl_pct": round((value - invested) / invested * 100, 2) if invested > 0 else 0.0,
            "day_pnl": round(day_pnl, 2),
            "day_pnl_pct": round(day_pnl / (value - day_pnl) * 100, 2) if value - day_pnl > 0 else 0.0,
            "realized": round(realized, 2),
            "dividends": round(dividends, 2),
            "fees": round(sum(p["fees"] for p in positions), 2),
            "n_lines": len(open_pos),
        },
        "allocation": {
            "by_class": {k: round(v, 2) for k, v in sorted(by_class.items(), key=lambda kv: -kv[1])},
            "by_currency": {k: round(v, 2) for k, v in sorted(by_currency.items(), key=lambda kv: -kv[1])},
        },
        "stale": stale_any,
        "as_of": date.today().isoformat(),
    }
