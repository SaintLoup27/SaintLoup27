"""
Bot de trading crypto — Point d'entrée principal.

MODES DISPONIBLES :
──────────────────
  python bot.py                   → Trading actif RSI+EMA (scan toutes les N min)
  python bot.py --scalp           → Scalping temps réel via WebSocket (secondes)
  python bot.py --dry-run         → Simulation (avec l'un ou l'autre mode)
  python bot.py --scalp --dry-run → Scalping en simulation
  python bot.py --report          → Rapport P&L et quitte

STRATÉGIES :
────────────
  • Mode actif  : scan périodique (configurable, défaut 60s), signaux RSI+EMA
                  sur bougies 5m. Idéal pour du swing intraday.
  • Mode scalp  : WebSocket temps réel, réagit en < 1 seconde sur chaque tick.
                  Achète sur momentum, vend sur SL/TP/timeout.
                  Take-profit par défaut : +0.8% | Stop-loss : -0.5%

FRAIS :
───────
  Binance facture 0.1% par ordre (0.2% aller-retour).
  → Le take-profit DOIT être > 0.2% pour être rentable.
  → Avec BNB pour frais : 0.075% par ordre (0.15% aller-retour).
"""

import argparse
import logging
import sys
import time
from datetime import datetime, timezone

import schedule
from binance.client import Client
from binance.exceptions import BinanceAPIException

import config as cfg
from strategies.active import scan_pair, get_position
from strategies.scalp  import run_scalper
from utils.notifier    import send_telegram
from utils.portfolio   import (
    get_positions, get_history, get_initial_value, set_initial_value
)

# ──────────────────────────────────────────────────────────────────────────────
#  Logging
# ──────────────────────────────────────────────────────────────────────────────

import os; os.makedirs("data", exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("data/bot.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
#  Client Binance
# ──────────────────────────────────────────────────────────────────────────────

def create_client() -> Client:
    if not cfg.BINANCE_API_KEY or not cfg.BINANCE_API_SECRET:
        log.error("Clés API Binance manquantes dans le fichier .env !")
        sys.exit(1)
    client = Client(cfg.BINANCE_API_KEY, cfg.BINANCE_API_SECRET, testnet=cfg.TESTNET)
    log.info(f"Connecté à Binance {'[TESTNET]' if cfg.TESTNET else '[PRODUCTION]'}")
    return client


# ──────────────────────────────────────────────────────────────────────────────
#  Valeur portfolio
# ──────────────────────────────────────────────────────────────────────────────

def portfolio_value_usdt(client: Client) -> float:
    total = 0.0
    for symbol, pos in get_positions().items():
        if pos["total_qty"] <= 0:
            continue
        try:
            price = float(client.get_symbol_ticker(symbol=symbol)["price"])
            total += pos["total_qty"] * price
        except Exception:
            pass
    return total


# ──────────────────────────────────────────────────────────────────────────────
#  Vérification stop-loss global
# ──────────────────────────────────────────────────────────────────────────────

def check_global_stop_loss(client: Client) -> None:
    current = portfolio_value_usdt(client)
    initial = get_initial_value()
    if initial and initial > 0:
        loss_pct = (initial - current) / initial * 100
        if loss_pct >= cfg.STOP_LOSS_PORTFOLIO_PCT:
            msg = (
                f"🚨 <b>STOP-LOSS GLOBAL DÉCLENCHÉ</b>\n"
                f"Portfolio : {current:.2f} USDT (perte {loss_pct:.1f}%)\n"
                f"Le bot s'arrête pour protéger votre capital."
            )
            log.critical(msg.replace("<b>", "").replace("</b>", ""))
            send_telegram(cfg.TELEGRAM_TOKEN, cfg.TELEGRAM_CHAT_ID, msg)
            sys.exit(1)


# ──────────────────────────────────────────────────────────────────────────────
#  Mode actif — scan périodique
# ──────────────────────────────────────────────────────────────────────────────

def active_scan(client: Client, dry_run: bool) -> None:
    """Un cycle de scan sur toutes les paires configurées."""
    check_global_stop_loss(client)
    for symbol, usdt_amount in cfg.ACTIVE_CONFIG["assets"].items():
        scan_pair(client, symbol, usdt_amount, dry_run=dry_run)


# ──────────────────────────────────────────────────────────────────────────────
#  Rapport quotidien Telegram
# ──────────────────────────────────────────────────────────────────────────────

def daily_report(client: Client) -> None:
    if not cfg.NOTIFY_DAILY_REPORT:
        return
    positions = get_positions()
    history   = get_history()
    if not positions:
        return

    lines = ["<b>📊 Rapport quotidien</b>\n"]
    total_val = total_inv = 0.0

    for symbol, pos in positions.items():
        if pos["total_qty"] <= 0:
            continue
        try:
            price = float(client.get_symbol_ticker(symbol=symbol)["price"])
        except Exception:
            price = pos["avg_price"]
        val    = pos["total_qty"] * price
        pnl    = val - pos["total_usdt"]
        pnl_pct = pnl / pos["total_usdt"] * 100 if pos["total_usdt"] > 0 else 0
        total_val += val
        total_inv += pos["total_usdt"]
        arrow = "📈" if pnl >= 0 else "📉"
        lines.append(f"{arrow} <code>{symbol}</code>  {val:.2f} USDT  ({pnl:+.2f} / {pnl_pct:+.1f}%)")

    total_pnl     = total_val - total_inv
    total_pnl_pct = total_pnl / total_inv * 100 if total_inv > 0 else 0
    nb_b = sum(1 for h in history if h["action"] == "BUY")
    nb_s = sum(1 for h in history if h["action"] == "SELL")

    lines.append(
        f"\n<b>Total : {total_val:.2f} USDT</b>  P&L : {total_pnl:+.2f} ({total_pnl_pct:+.1f}%)\n"
        f"Trades : {nb_b} achats / {nb_s} ventes"
    )
    send_telegram(cfg.TELEGRAM_TOKEN, cfg.TELEGRAM_CHAT_ID, "\n".join(lines))


# ──────────────────────────────────────────────────────────────────────────────
#  Rapport CLI
# ──────────────────────────────────────────────────────────────────────────────

def print_report(client: Client) -> None:
    positions = get_positions()
    history   = get_history()

    if not positions:
        print("Aucune position enregistrée.")
        return

    print("\n" + "═" * 65)
    print("  RAPPORT PORTFOLIO")
    print("═" * 65)

    total_val = total_inv = 0.0
    for symbol, pos in positions.items():
        if pos["total_qty"] <= 0:
            continue
        try:
            price = float(client.get_symbol_ticker(symbol=symbol)["price"])
        except Exception:
            price = pos["avg_price"]
        val    = pos["total_qty"] * price
        pnl    = val - pos["total_usdt"]
        pnl_pct = pnl / pos["total_usdt"] * 100 if pos["total_usdt"] > 0 else 0
        total_val += val
        total_inv += pos["total_usdt"]
        arrow = "▲" if pnl >= 0 else "▼"
        print(f"  {symbol:<10} {arrow}  {val:>10.2f} USDT  |  P&L {pnl:>+8.2f} ({pnl_pct:>+6.1f}%)")

    pnl_total     = total_val - total_inv
    pnl_total_pct = pnl_total / total_inv * 100 if total_inv > 0 else 0
    nb_b = sum(1 for h in history if h["action"] == "BUY")
    nb_s = sum(1 for h in history if h["action"] == "SELL")

    print("─" * 65)
    print(f"  {'TOTAL':<10}    {total_val:>10.2f} USDT  |  P&L {pnl_total:>+8.2f} ({pnl_total_pct:>+6.1f}%)")
    print(f"  Trades : {nb_b} achats / {nb_s} ventes")
    print("═" * 65 + "\n")


# ──────────────────────────────────────────────────────────────────────────────
#  Main
# ──────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Crypto Trading Bot")
    parser.add_argument("--dry-run", action="store_true", help="Simulation sans ordres réels")
    parser.add_argument("--scalp",   action="store_true", help="Mode scalping WebSocket (secondes)")
    parser.add_argument("--report",  action="store_true", help="Rapport P&L et quitte")
    args = parser.parse_args()

    client = create_client()

    if args.report:
        print_report(client)
        return

    # ── Mode scalping temps réel ──────────────────────────────────────────────
    if args.scalp or cfg.SCALP_CONFIG.get("enabled"):
        mode = "SCALP DRY-RUN" if args.dry_run else ("SCALP TESTNET" if cfg.TESTNET else "SCALP PRODUCTION")
        log.info(f"Bot démarré : {mode}")
        run_scalper(client, dry_run=args.dry_run)
        return

    # ── Mode actif (scan périodique) ─────────────────────────────────────────
    interval = cfg.ACTIVE_CONFIG["scan_interval_seconds"]
    mode = "ACTIF DRY-RUN" if args.dry_run else ("ACTIF TESTNET" if cfg.TESTNET else "ACTIF PRODUCTION")
    log.info(f"Bot démarré : {mode} | scan toutes les {interval}s")

    if not args.dry_run and not cfg.TESTNET:
        send_telegram(cfg.TELEGRAM_TOKEN, cfg.TELEGRAM_CHAT_ID,
            f"🤖 <b>Bot démarré (mode actif)</b>\n"
            f"Paires : {', '.join(cfg.ACTIVE_CONFIG['assets'].keys())}\n"
            f"Timeframe : {cfg.ACTIVE_CONFIG['timeframe']} | Scan : {interval}s"
        )

    # Premier scan immédiat
    active_scan(client, dry_run=args.dry_run)
    if get_initial_value() is None:
        set_initial_value(portfolio_value_usdt(client))

    # Planification rapport quotidien
    schedule.every().day.at("08:00").do(daily_report, client=client)

    log.info(f"Prochain scan dans {interval}s. En attente...")
    last_scan = time.time()

    while True:
        schedule.run_pending()
        if time.time() - last_scan >= interval:
            active_scan(client, dry_run=args.dry_run)
            last_scan = time.time()
        time.sleep(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Bot arrêté manuellement.")
    except BinanceAPIException as e:
        log.critical(f"Erreur API Binance : {e}")
        sys.exit(1)
