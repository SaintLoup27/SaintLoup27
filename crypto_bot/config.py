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
TESTNET            = os.getenv("TESTNET", "true").lower() == "true"

# ─────────────────────────────────────────────
#  STRATÉGIE ACTIVE (RSI + EMA)
#  Scan toutes les N minutes sur bougies courtes
# ─────────────────────────────────────────────
ACTIVE_CONFIG = {
    # Paires à trader et capital alloué par trade (en USDT)
    "assets": {
        "BTCUSDT": 50,
        "ETHUSDT": 30,
        "SOLUSDT": 20,
        "BNBUSDT": 15,
    },

    # Timeframe des bougies pour les indicateurs
    # "1m" = 1 minute | "3m" | "5m" | "15m"
    "timeframe": "5m",

    # Intervalle de scan (en secondes)
    # 60 = toutes les minutes, 300 = toutes les 5 min
    "scan_interval_seconds": 60,

    # RSI
    "rsi_period": 14,
    "rsi_buy":    35,    # Acheter si RSI < 35 (survente)
    "rsi_sell":   65,    # Vendre si RSI > 65 (surachat)

    # EMA crossover
    "ema_fast": 9,
    "ema_slow": 21,

    # Stop-loss et take-profit par trade (en %)
    "stop_loss_pct":   1.5,   # Couper la perte à -1.5%
    "take_profit_pct": 2.5,   # Prendre bénéfice à +2.5%
}

# ─────────────────────────────────────────────
#  STRATÉGIE SCALPING TEMPS RÉEL (WebSocket)
#  Réagit en quelques secondes sur flux de prix
# ─────────────────────────────────────────────
SCALP_CONFIG = {
    # Activer le scalping (désactive le mode scan si True)
    "enabled": False,

    # Paires et capital par trade
    "assets": {
        "BTCUSDT": 30,
        "ETHUSDT": 20,
    },

    # Fenêtre glissante en secondes pour calculer la tendance
    "window_seconds": 30,

    # Acheter si le prix monte de X% dans la fenêtre (momentum)
    "momentum_buy_pct": 0.08,

    # Vendre si le prix baisse de X% depuis l'entrée (stop-loss)
    "stop_loss_pct": 0.5,

    # Prendre bénéfice si +X% depuis l'entrée
    "take_profit_pct": 0.8,

    # Temps max de détention d'une position (secondes) — sortie forcée
    "max_hold_seconds": 120,

    # Délai minimum entre deux trades sur la même paire (secondes)
    "cooldown_seconds": 30,
}

# ─────────────────────────────────────────────
#  NOTIFICATIONS TELEGRAM
# ─────────────────────────────────────────────
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
NOTIFY_ON_BUY         = True
NOTIFY_ON_TAKE_PROFIT = True
NOTIFY_DAILY_REPORT   = True

# ─────────────────────────────────────────────
#  SÉCURITÉ
# ─────────────────────────────────────────────
MAX_PORTFOLIO_USDT      = 10_000
STOP_LOSS_PORTFOLIO_PCT = 30
