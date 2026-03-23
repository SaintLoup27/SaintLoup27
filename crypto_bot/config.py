"""
Configuration du bot de trading crypto — v3 optimisée.
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
#  STRATÉGIE ACTIVE (RSI + MACD + BB + Volume)
#  Scan périodique sur bougies courtes
# ─────────────────────────────────────────────
ACTIVE_CONFIG = {
    # Paires et capital alloué par trade (USDT)
    "assets": {
        "BTCUSDT": 50,
        "ETHUSDT": 30,
        "SOLUSDT": 20,
        "BNBUSDT": 15,
    },

    # Timeframe des bougies
    # "1m" | "3m" | "5m" (recommandé) | "15m" (plus sûr, moins de trades)
    "timeframe": "5m",

    # Intervalle de scan (secondes) — 60 = toutes les minutes
    "scan_interval_seconds": 60,

    # ── Indicateurs ───────────────────────────
    "rsi_period":    14,
    "rsi_buy":       35,   # Acheter si RSI < 35
    "rsi_sell":      65,   # Vendre si RSI > 65

    "ema_fast":       9,
    "ema_slow":      21,

    "bb_period":     20,   # Bandes de Bollinger
    "bb_std":         2.0,

    "atr_period":    14,   # ATR pour le stop-loss adaptatif
    "atr_sl_multiplier": 1.5,  # Stop = prix_entrée - 1.5 × ATR

    "volume_ma_period": 20,   # Moyenne de volume sur 20 bougies
    "trend_period":     14,   # Période pour la force de tendance (ADX simplifié)

    # ── Filtres qualité de signal ──────────────
    # Score minimum pour déclencher un ordre (0-100)
    # 60 = équilibre signal/fréquence | 70 = plus sélectif | 50 = plus de trades
    "min_signal_score":   60,

    # Volume minimum par rapport à la moyenne (1.0 = normal, 1.2 = +20%)
    "min_volume_ratio":   1.0,

    # Suspendre le trading si la force de tendance est trop faible (marché en range)
    # 20 = très permissif | 25 = recommandé | 30 = très sélectif
    "min_trend_strength": 20,

    # ── Sorties ───────────────────────────────
    "take_profit_pct":  3.0,  # Take-profit fixe en dernier recours (+3%)
    # Le trailing stop ATR gère la sortie principale
}

# ─────────────────────────────────────────────
#  STRATÉGIE SCALPING TEMPS RÉEL (WebSocket)
#  Réagit en < 1 seconde sur chaque tick de prix
# ─────────────────────────────────────────────
SCALP_CONFIG = {
    # Mettre True pour activer (désactive le mode actif)
    "enabled": False,

    # Paires et capital par trade
    "assets": {
        "BTCUSDT": 30,
        "ETHUSDT": 20,
    },

    # ── Signal d'entrée ───────────────────────
    # Fenêtre glissante pour le calcul du momentum (secondes)
    "window_seconds":    30,

    # Momentum minimum pour déclencher un achat
    # 0.08 = prix a monté de 0.08% en 30s → achat
    "momentum_buy_pct":  0.08,

    # RSI maximum au moment de l'entrée (évite d'acheter en surachat)
    "max_rsi_entry":     65,

    # VWAP : n'acheter que si prix < VWAP + X% (valeur par défaut = 0 = strict)
    "max_above_vwap_pct": 0.1,

    # Déséquilibre carnet d'ordres minimum (0.6 = 60% bids vs asks)
    "min_ob_imbalance":  0.55,

    # Filtres de volatilité (% écart-type / prix sur 60s)
    "min_volatility_pct": 0.01,   # trop calme = pas de mouvement à capturer
    "max_volatility_pct": 0.5,    # trop violent = risque trop élevé

    # ── Sorties ───────────────────────────────
    "stop_loss_pct":      0.5,    # Stop-loss initial
    "trailing_stop_pct":  0.4,    # Trailing stop (suit le prix peak)
    "take_profit_pct":    0.8,    # Take-profit fixe

    # Durée max de détention (secondes)
    "max_hold_seconds":   120,

    # Délai minimum entre deux trades sur la même paire (secondes)
    "cooldown_seconds":   30,
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
#  SÉCURITÉ GLOBALE
# ─────────────────────────────────────────────
MAX_PORTFOLIO_USDT      = 10_000
STOP_LOSS_PORTFOLIO_PCT = 30
