"""
Stratégie DCA (Dollar Cost Averaging).

Principe :
  - Acheter une quantité fixe en USDT à intervalles réguliers.
  - Prendre des bénéfices si le prix dépasse le seuil configuré.
  - Journaliser chaque achat/vente dans le portfolio local.
"""

import logging
from datetime import datetime, timezone

from utils.notifier  import send_telegram
from utils.portfolio import record_buy, record_sell, get_positions
import config as cfg

log = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
#  Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _get_price(client, symbol: str) -> float:
    ticker = client.get_symbol_ticker(symbol=symbol)
    return float(ticker["price"])


def _get_change_24h(client, symbol: str) -> float:
    """Retourne la variation du prix en % sur 24h."""
    info = client.get_ticker(symbol=symbol)
    return float(info["priceChangePercent"])


def _qty_precision(client, symbol: str) -> int:
    """Retourne la précision décimale de la quantité pour un symbole."""
    info = client.get_symbol_info(symbol)
    for f in info["filters"]:
        if f["filterType"] == "LOT_SIZE":
            step = f["stepSize"]
            return len(step.rstrip("0").split(".")[-1]) if "." in step else 0
    return 6


def _min_notional(client, symbol: str) -> float:
    info = client.get_symbol_info(symbol)
    for f in info["filters"]:
        if f["filterType"] in ("MIN_NOTIONAL", "NOTIONAL"):
            return float(f.get("minNotional", f.get("notional", 5)))
    return 5.0


# ──────────────────────────────────────────────────────────────────────────────
#  Actions
# ──────────────────────────────────────────────────────────────────────────────

def execute_buy(client, symbol: str, usdt_amount: float, dry_run: bool = False) -> dict | None:
    """
    Achète `usdt_amount` USDT de `symbol`.
    Retourne le détail de l'ordre, ou None en cas d'échec.
    """
    price    = _get_price(client, symbol)
    min_not  = _min_notional(client, symbol)
    precision = _qty_precision(client, symbol)

    if usdt_amount < min_not:
        log.warning(f"{symbol}: montant {usdt_amount} USDT < minimum {min_not} USDT — ignoré.")
        return None

    raw_qty = usdt_amount / price
    qty     = round(raw_qty, precision)

    log.info(f"[{'DRY-RUN' if dry_run else 'BUY'}] {symbol} | prix={price:.4f} | qty={qty} | usdt={usdt_amount}")

    if dry_run:
        record_buy(symbol, qty, price, usdt_amount)
        _notify_buy(symbol, qty, price, usdt_amount, dry_run=True)
        return {"symbol": symbol, "qty": qty, "price": price, "usdt": usdt_amount}

    try:
        order = client.order_market_buy(symbol=symbol, quoteOrderQty=usdt_amount)
        filled_qty   = float(order["executedQty"])
        filled_usdt  = float(order["cummulativeQuoteQty"])
        filled_price = filled_usdt / filled_qty if filled_qty > 0 else price

        record_buy(symbol, filled_qty, filled_price, filled_usdt)
        _notify_buy(symbol, filled_qty, filled_price, filled_usdt)

        log.info(f"Ordre exécuté : {filled_qty} {symbol[:-4]} à {filled_price:.4f} USDT")
        return {"symbol": symbol, "qty": filled_qty, "price": filled_price, "usdt": filled_usdt}

    except Exception as e:
        log.error(f"Erreur achat {symbol}: {e}")
        return None


def execute_take_profit(client, symbol: str, dry_run: bool = False) -> dict | None:
    """
    Vend la totalité d'un actif si le prix a dépassé le seuil take-profit.
    """
    tp_pct = cfg.DCA_CONFIG.get("take_profit_pct")
    if tp_pct is None:
        return None

    positions = get_positions()
    pos = positions.get(symbol)
    if not pos or pos["total_qty"] <= 0:
        return None

    current_price = _get_price(client, symbol)
    avg_price     = pos["avg_price"]
    pnl_pct       = (current_price - avg_price) / avg_price * 100

    if pnl_pct < tp_pct:
        return None

    qty       = pos["total_qty"]
    precision = _qty_precision(client, symbol)
    qty       = round(qty, precision)

    log.info(f"[TAKE-PROFIT] {symbol} | avg={avg_price:.4f} | now={current_price:.4f} | +{pnl_pct:.1f}%")

    if dry_run:
        usdt = qty * current_price
        record_sell(symbol, qty, current_price, usdt)
        _notify_sell(symbol, qty, current_price, usdt, pnl_pct, dry_run=True)
        return {"symbol": symbol, "qty": qty, "price": current_price, "pnl_pct": pnl_pct}

    try:
        order = client.order_market_sell(symbol=symbol, quantity=qty)
        filled_qty  = float(order["executedQty"])
        filled_usdt = float(order["cummulativeQuoteQty"])
        filled_price = filled_usdt / filled_qty if filled_qty > 0 else current_price

        record_sell(symbol, filled_qty, filled_price, filled_usdt)
        _notify_sell(symbol, filled_qty, filled_price, filled_usdt, pnl_pct)

        log.info(f"Take-profit exécuté : {filled_qty} {symbol[:-4]} vendu à {filled_price:.4f} USDT (+{pnl_pct:.1f}%)")
        return {"symbol": symbol, "qty": filled_qty, "price": filled_price, "pnl_pct": pnl_pct}

    except Exception as e:
        log.error(f"Erreur vente take-profit {symbol}: {e}")
        return None


# ──────────────────────────────────────────────────────────────────────────────
#  Notifications
# ──────────────────────────────────────────────────────────────────────────────

def _notify_buy(symbol, qty, price, usdt, dry_run=False):
    if not cfg.NOTIFY_ON_BUY:
        return
    tag = " (simulation)" if dry_run else ""
    msg = (
        f"<b>📈 Achat DCA{tag}</b>\n"
        f"• Paire : <code>{symbol}</code>\n"
        f"• Quantité : <code>{qty}</code>\n"
        f"• Prix : <code>{price:.4f} USDT</code>\n"
        f"• Dépensé : <code>{usdt:.2f} USDT</code>\n"
        f"• Heure : {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
    )
    send_telegram(cfg.TELEGRAM_TOKEN, cfg.TELEGRAM_CHAT_ID, msg)


def _notify_sell(symbol, qty, price, usdt, pnl_pct, dry_run=False):
    if not cfg.NOTIFY_ON_TAKE_PROFIT:
        return
    tag = " (simulation)" if dry_run else ""
    msg = (
        f"<b>💰 Take-Profit{tag}</b>\n"
        f"• Paire : <code>{symbol}</code>\n"
        f"• Quantité vendue : <code>{qty}</code>\n"
        f"• Prix : <code>{price:.4f} USDT</code>\n"
        f"• Reçu : <code>{usdt:.2f} USDT</code>\n"
        f"• Gain : <code>+{pnl_pct:.1f}%</code> ✅"
    )
    send_telegram(cfg.TELEGRAM_TOKEN, cfg.TELEGRAM_CHAT_ID, msg)
