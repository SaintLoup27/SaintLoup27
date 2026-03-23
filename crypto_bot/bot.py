"""
Bot de trading crypto — Point d'entrée principal.

Lancer :
    python bot.py            # production (TESTNET=false dans .env)
    python bot.py --dry-run  # simulation sans argent réel
    python bot.py --report   # affiche le rapport du portfolio et quitte

Fonctionnement :
    1. Toutes les N heures (DCA_CONFIG["interval_hours"]) → achète les actifs configurés.
    2. À chaque cycle → vérifie si un take-profit doit être déclenché.
    3. Chaque jour à 08h00 → envoie un rapport Telegram.
    4. Si le portfolio perd plus de STOP_LOSS_PORTFOLIO_PCT → arrête le bot.
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
from strategies.dca import execute_buy, execute_take_profit
from utils.notifier import send_telegram
from utils.portfolio import (
    get_positions, get_history, get_initial_value, set_initial_value
)

# ──────────────────────────────────────────────────────────────────────────────
#  Logging
# ──────────────────────────────────────────────────────────────────────────────

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
    client = Client(
        cfg.BINANCE_API_KEY,
        cfg.BINANCE_API_SECRET,
        testnet=cfg.TESTNET,
    )
    log.info(f"Connecté à Binance {'[TESTNET]' if cfg.TESTNET else '[PRODUCTION]'}")
    return client


# ──────────────────────────────────────────────────────────────────────────────
#  Valeur totale du portfolio
# ──────────────────────────────────────────────────────────────────────────────

def portfolio_value_usdt(client: Client) -> float:
    """Calcule la valeur totale du portfolio en USDT."""
    total = 0.0
    positions = get_positions()
    for symbol, pos in positions.items():
        if pos["total_qty"] <= 0:
            continue
        try:
            ticker = client.get_symbol_ticker(symbol=symbol)
            price  = float(ticker["price"])
            total += pos["total_qty"] * price
        except Exception as e:
            log.warning(f"Impossible de récupérer le prix de {symbol}: {e}")
    return total


# ──────────────────────────────────────────────────────────────────────────────
#  Tâches planifiées
# ──────────────────────────────────────────────────────────────────────────────

def dca_cycle(client: Client, dry_run: bool) -> None:
    log.info("=== Début du cycle DCA ===")

    # Vérification stop-loss portfolio
    current_val = portfolio_value_usdt(client)
    initial_val = get_initial_value()

    if initial_val is not None and initial_val > 0:
        loss_pct = (initial_val - current_val) / initial_val * 100
        if loss_pct >= cfg.STOP_LOSS_PORTFOLIO_PCT:
            msg = (
                f"🚨 <b>STOP-LOSS DÉCLENCHÉ</b>\n"
                f"Portfolio : {current_val:.2f} USDT (perte de {loss_pct:.1f}%)\n"
                f"Le bot s'arrête pour protéger votre capital."
            )
            log.critical(msg.replace("<b>", "").replace("</b>", ""))
            send_telegram(cfg.TELEGRAM_TOKEN, cfg.TELEGRAM_CHAT_ID, msg)
            sys.exit(1)

    # Vérification take-profit sur chaque actif
    for symbol in cfg.DCA_CONFIG["assets"]:
        execute_take_profit(client, symbol, dry_run=dry_run)

    # Achat DCA sur chaque actif
    total_usdt_deployed = sum(portfolio_value_usdt(client) for _ in [1])  # valeur actuelle
    if total_usdt_deployed >= cfg.MAX_PORTFOLIO_USDT:
        log.warning(f"Limite portfolio atteinte ({cfg.MAX_PORTFOLIO_USDT} USDT) — aucun achat ce cycle.")
        return

    for symbol, usdt_amount in cfg.DCA_CONFIG["assets"].items():
        # Vérification dip guard
        max_dip = cfg.DCA_CONFIG.get("max_dip_24h_pct")
        if max_dip is not None:
            try:
                from strategies.dca import _get_change_24h
                change = _get_change_24h(client, symbol)
                if change <= -abs(max_dip):
                    log.info(f"{symbol}: chute de {change:.1f}% en 24h → achat sauté (dip guard).")
                    continue
            except Exception as e:
                log.warning(f"Impossible de vérifier le dip pour {symbol}: {e}")

        execute_buy(client, symbol, usdt_amount, dry_run=dry_run)

    # Enregistrer la valeur initiale au premier cycle
    if get_initial_value() is None:
        set_initial_value(portfolio_value_usdt(client))

    log.info("=== Fin du cycle DCA ===")


def daily_report(client: Client) -> None:
    if not cfg.NOTIFY_DAILY_REPORT:
        return

    positions = get_positions()
    history   = get_history()

    if not positions:
        return

    lines = ["<b>📊 Rapport quotidien — Crypto Bot</b>\n"]
    total_usdt = 0.0
    total_invested = 0.0

    for symbol, pos in positions.items():
        if pos["total_qty"] <= 0:
            continue
        try:
            price = float(client.get_symbol_ticker(symbol=symbol)["price"])
        except Exception:
            price = pos["avg_price"]

        value    = pos["total_qty"] * price
        invested = pos["total_usdt"]
        pnl      = value - invested
        pnl_pct  = (pnl / invested * 100) if invested > 0 else 0

        total_usdt     += value
        total_invested += invested

        emoji = "📈" if pnl >= 0 else "📉"
        lines.append(
            f"{emoji} <code>{symbol}</code>\n"
            f"   Qty : {pos['total_qty']:.6f}  |  Prix moy. : {pos['avg_price']:.4f}\n"
            f"   Valeur : {value:.2f} USDT  |  P&L : {pnl:+.2f} USDT ({pnl_pct:+.1f}%)\n"
        )

    total_pnl     = total_usdt - total_invested
    total_pnl_pct = (total_pnl / total_invested * 100) if total_invested > 0 else 0
    nb_achats     = sum(1 for h in history if h["action"] == "BUY")
    nb_ventes     = sum(1 for h in history if h["action"] == "SELL")

    lines.append(
        f"\n<b>Total portfolio : {total_usdt:.2f} USDT</b>\n"
        f"Investi : {total_invested:.2f} USDT\n"
        f"P&L global : {total_pnl:+.2f} USDT ({total_pnl_pct:+.1f}%)\n"
        f"Achats : {nb_achats}  |  Ventes : {nb_ventes}"
    )

    send_telegram(cfg.TELEGRAM_TOKEN, cfg.TELEGRAM_CHAT_ID, "\n".join(lines))
    log.info("Rapport quotidien envoyé.")


# ──────────────────────────────────────────────────────────────────────────────
#  Rapport CLI
# ──────────────────────────────────────────────────────────────────────────────

def print_report(client: Client) -> None:
    positions = get_positions()
    if not positions:
        print("Aucune position enregistrée.")
        return

    print("\n" + "═" * 60)
    print("  RAPPORT PORTFOLIO")
    print("═" * 60)

    total_usdt = 0.0
    total_invested = 0.0

    for symbol, pos in positions.items():
        if pos["total_qty"] <= 0:
            continue
        try:
            price = float(client.get_symbol_ticker(symbol=symbol)["price"])
        except Exception:
            price = pos["avg_price"]

        value  = pos["total_qty"] * price
        pnl    = value - pos["total_usdt"]
        pnl_pct = (pnl / pos["total_usdt"] * 100) if pos["total_usdt"] > 0 else 0

        total_usdt     += value
        total_invested += pos["total_usdt"]

        arrow = "▲" if pnl >= 0 else "▼"
        print(f"  {symbol:<10} {arrow}  Valeur: {value:>10.2f} USDT  |  P&L: {pnl:>+8.2f} ({pnl_pct:+.1f}%)")

    total_pnl     = total_usdt - total_invested
    total_pnl_pct = (total_pnl / total_invested * 100) if total_invested > 0 else 0
    print("─" * 60)
    print(f"  {'TOTAL':<10}    Valeur: {total_usdt:>10.2f} USDT  |  P&L: {total_pnl:>+8.2f} ({total_pnl_pct:+.1f}%)")
    print("═" * 60 + "\n")


# ──────────────────────────────────────────────────────────────────────────────
#  Main
# ──────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Crypto DCA Bot")
    parser.add_argument("--dry-run", action="store_true", help="Simulation sans ordres réels")
    parser.add_argument("--report",  action="store_true", help="Affiche le rapport et quitte")
    args = parser.parse_args()

    import os; os.makedirs("data", exist_ok=True)

    client = create_client()

    if args.report:
        print_report(client)
        return

    mode = "DRY-RUN (simulation)" if args.dry_run else ("TESTNET" if cfg.TESTNET else "PRODUCTION RÉELLE")
    log.info(f"Bot démarré en mode : {mode}")

    if not args.dry_run and not cfg.TESTNET:
        send_telegram(
            cfg.TELEGRAM_TOKEN, cfg.TELEGRAM_CHAT_ID,
            f"🤖 <b>Crypto Bot démarré</b> — mode PRODUCTION\n"
            f"Actifs : {', '.join(cfg.DCA_CONFIG['assets'].keys())}\n"
            f"Intervalle : {cfg.DCA_CONFIG['interval_hours']}h"
        )

    # Premier cycle immédiat
    dca_cycle(client, dry_run=args.dry_run)

    # Planification
    interval = cfg.DCA_CONFIG["interval_hours"]
    schedule.every(interval).hours.do(dca_cycle, client=client, dry_run=args.dry_run)
    schedule.every().day.at("08:00").do(daily_report, client=client)

    log.info(f"Prochain cycle DCA dans {interval} heure(s). En attente...")

    while True:
        schedule.run_pending()
        time.sleep(60)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Bot arrêté manuellement.")
    except BinanceAPIException as e:
        log.critical(f"Erreur API Binance : {e}")
        sys.exit(1)
