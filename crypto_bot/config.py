"""
Configuration du bot de trading crypto.
Modifiez ce fichier selon vos préférences.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ─────────────────────────────────────────────
#  EXCHANGE (Binance)
# ─────────────────────────────────────────────
BINANCE_API_KEY    = os.getenv("BINANCE_API_KEY", "")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET", "")
TESTNET            = os.getenv("TESTNET", "true").lower() == "true"  # Mettre "false" en production

# ─────────────────────────────────────────────
#  STRATÉGIE DCA (Dollar Cost Averaging)
# ─────────────────────────────────────────────
DCA_CONFIG = {
    # Paires à acheter et montant en USDT par achat
    "assets": {
        "BTCUSDT":  20,   # 20 $ de BTC à chaque cycle
        "ETHUSDT":  15,   # 15 $ d'ETH à chaque cycle
        "SOLUSDT":  10,   # 10 $ de SOL à chaque cycle
        "BNBUSDT":  5,    # 5 $ de BNB à chaque cycle
    },
    # Intervalle entre chaque achat (en heures)
    # 168 = une fois par semaine, 24 = quotidien, 1 = horaire
    "interval_hours": 168,

    # Ne pas acheter si le prix a baissé de plus de X% en 24h (panic dip guard)
    # Mettre None pour désactiver
    "max_dip_24h_pct": None,

    # Prendre des bénéfices si un actif a gagné X% depuis le prix moyen d'achat
    # Mettre None pour désactiver
    "take_profit_pct": 50,
}

# ─────────────────────────────────────────────
#  STRATÉGIE GRILLE (Grid Trading) — optionnel
# ─────────────────────────────────────────────
GRID_CONFIG = {
    "enabled": False,
    "pair":        "BTCUSDT",
    "lower_price": 50000,
    "upper_price": 80000,
    "num_grids":   10,
    "amount_per_grid": 10,   # USDT par ordre de grille
}

# ─────────────────────────────────────────────
#  NOTIFICATIONS TELEGRAM
# ─────────────────────────────────────────────
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
NOTIFY_ON_BUY         = True
NOTIFY_ON_TAKE_PROFIT = True
NOTIFY_DAILY_REPORT   = True   # Rapport quotidien à 08h00

# ─────────────────────────────────────────────
#  SÉCURITÉ
# ─────────────────────────────────────────────
MAX_PORTFOLIO_USDT = 10_000   # Limite absolue (en USDT) que le bot peut déployer
STOP_LOSS_PORTFOLIO_PCT = 30  # Stopper le bot si le portfolio perd X% de sa valeur initiale
