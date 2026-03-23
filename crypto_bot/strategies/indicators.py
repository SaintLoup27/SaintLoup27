"""
Indicateurs techniques — bibliothèque centralisée.

Tous les calculs sont faits en Python pur (pas de dépendance TA-Lib)
pour une installation simple.

Indicateurs disponibles :
  ema(closes, period)              → float
  rsi(closes, period)              → float
  macd(closes, fast, slow, signal) → (macd_line, signal_line, histogram)
  bollinger(closes, period, std)   → (upper, middle, lower)
  atr(highs, lows, closes, period) → float   ← volatilité réelle
  vwap(highs, lows, closes, vols)  → float   ← prix moyen pondéré volume
  volume_ratio(volumes, period)    → float   ← volume actuel / moyenne
  trend_strength(closes, period)   → float   ← ADX simplifié 0-100
"""

from __future__ import annotations
import math


# ──────────────────────────────────────────────────────────────────────────────
#  EMA
# ──────────────────────────────────────────────────────────────────────────────

def ema(closes: list[float], period: int) -> float:
    """Exponential Moving Average — retourne la dernière valeur."""
    if len(closes) < period:
        return closes[-1]
    k   = 2 / (period + 1)
    val = sum(closes[:period]) / period  # SMA pour initialiser
    for price in closes[period:]:
        val = price * k + val * (1 - k)
    return val


def ema_series(closes: list[float], period: int) -> list[float]:
    """Retourne la série EMA complète (même longueur que closes)."""
    if len(closes) < period:
        return closes[:]
    k      = 2 / (period + 1)
    result = [None] * (period - 1)
    val    = sum(closes[:period]) / period
    result.append(val)
    for price in closes[period:]:
        val = price * k + val * (1 - k)
        result.append(val)
    return result


# ──────────────────────────────────────────────────────────────────────────────
#  RSI (méthode Wilder)
# ──────────────────────────────────────────────────────────────────────────────

def rsi(closes: list[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    # Wilder smoothing
    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_g = (avg_g * (period - 1) + gains[i]) / period
        avg_l = (avg_l * (period - 1) + losses[i]) / period
    if avg_l == 0:
        return 100.0
    return 100 - 100 / (1 + avg_g / avg_l)


# ──────────────────────────────────────────────────────────────────────────────
#  MACD
# ──────────────────────────────────────────────────────────────────────────────

def macd(closes: list[float],
         fast: int = 12, slow: int = 26, signal_period: int = 9
         ) -> tuple[float, float, float]:
    """
    Retourne (macd_line, signal_line, histogram).
    macd_line > 0  → tendance haussière
    histogram > 0  → momentum haussier (MACD accélère vers le haut)
    """
    fast_s  = ema_series(closes, fast)
    slow_s  = ema_series(closes, slow)

    macd_s = [
        (f - s) if (f is not None and s is not None) else None
        for f, s in zip(fast_s, slow_s)
    ]

    valid_macd = [v for v in macd_s if v is not None]
    if len(valid_macd) < signal_period:
        return 0.0, 0.0, 0.0

    sig_val = sum(valid_macd[:signal_period]) / signal_period
    k       = 2 / (signal_period + 1)
    for v in valid_macd[signal_period:]:
        sig_val = v * k + sig_val * (1 - k)

    macd_val = valid_macd[-1]
    hist     = macd_val - sig_val
    return macd_val, sig_val, hist


# ──────────────────────────────────────────────────────────────────────────────
#  Bandes de Bollinger
# ──────────────────────────────────────────────────────────────────────────────

def bollinger(closes: list[float], period: int = 20, num_std: float = 2.0
              ) -> tuple[float, float, float]:
    """
    Retourne (bande_haute, bande_milieu, bande_basse).
    Prix proche de bande_basse → zone de survente potentielle (achat).
    Prix proche de bande_haute → zone de surachat potentielle (vente).
    """
    window = closes[-period:]
    middle = sum(window) / period
    std    = math.sqrt(sum((p - middle) ** 2 for p in window) / period)
    return middle + num_std * std, middle, middle - num_std * std


def bollinger_pct_b(price: float, upper: float, lower: float) -> float:
    """
    %B = (prix - bande_basse) / (bande_haute - bande_basse)
    < 0.2 → survente  |  > 0.8 → surachat
    """
    band_width = upper - lower
    if band_width == 0:
        return 0.5
    return (price - lower) / band_width


# ──────────────────────────────────────────────────────────────────────────────
#  ATR — Average True Range (mesure la volatilité réelle)
# ──────────────────────────────────────────────────────────────────────────────

def atr(highs: list[float], lows: list[float], closes: list[float],
        period: int = 14) -> float:
    """
    ATR = moyenne des True Ranges.
    Utile pour placer un stop-loss adapté à la volatilité courante.
    Stop-loss suggéré = prix_entrée - (ATR × multiplicateur)
    """
    if len(closes) < 2:
        return 0.0
    trs = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i]  - closes[i - 1]),
        )
        trs.append(tr)

    if len(trs) < period:
        return sum(trs) / len(trs) if trs else 0.0

    # Wilder smoothing
    val = sum(trs[:period]) / period
    for tr in trs[period:]:
        val = (val * (period - 1) + tr) / period
    return val


# ──────────────────────────────────────────────────────────────────────────────
#  VWAP — Volume Weighted Average Price
# ──────────────────────────────────────────────────────────────────────────────

def vwap(highs: list[float], lows: list[float],
         closes: list[float], volumes: list[float]) -> float:
    """
    VWAP = Σ(prix_typique × volume) / Σ(volume)
    Prix < VWAP → actif sous-évalué par rapport aux acheteurs récents → signal achat.
    Prix > VWAP → actif sur-évalué → signal vente.
    """
    total_pv = total_v = 0.0
    for h, l, c, v in zip(highs, lows, closes, volumes):
        typical = (h + l + c) / 3
        total_pv += typical * v
        total_v  += v
    return total_pv / total_v if total_v > 0 else closes[-1]


# ──────────────────────────────────────────────────────────────────────────────
#  Volume Ratio (volume courant vs moyenne)
# ──────────────────────────────────────────────────────────────────────────────

def volume_ratio(volumes: list[float], period: int = 20) -> float:
    """
    > 1.5 → volume fort (signal fiable)
    < 0.7 → volume faible (signal peu fiable, à ignorer)
    """
    if len(volumes) < period + 1:
        return 1.0
    avg_vol = sum(volumes[-period - 1:-1]) / period
    if avg_vol == 0:
        return 1.0
    return volumes[-1] / avg_vol


# ──────────────────────────────────────────────────────────────────────────────
#  ADX simplifié — force de tendance (0-100)
# ──────────────────────────────────────────────────────────────────────────────

def trend_strength(closes: list[float], period: int = 14) -> float:
    """
    Valeur approximative de la force de tendance.
    > 25 → tendance forte  (bon pour trader)
    < 20 → marché en range (éviter, faux signaux)
    """
    if len(closes) < period * 2:
        return 25.0  # valeur neutre

    # Calcul simplifié basé sur l'écart-type de la direction
    changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    recent  = changes[-period:]
    mean_ch = sum(recent) / period
    variance = sum((c - mean_ch) ** 2 for c in recent) / period
    std_ch  = math.sqrt(variance) if variance > 0 else 0.0001

    # Netteté de la tendance : direction moyenne / dispersion
    directional = abs(mean_ch) / std_ch if std_ch > 0 else 0
    # Normaliser sur 0-100
    return min(directional * 25, 100)


# ──────────────────────────────────────────────────────────────────────────────
#  Score de signal composite (0-100)
# ──────────────────────────────────────────────────────────────────────────────

def signal_score(
    rsi_val:    float,
    macd_hist:  float,
    pct_b:      float,
    vol_ratio:  float,
    trend_str:  float,
    direction:  str = "buy",   # "buy" ou "sell"
) -> float:
    """
    Agrège tous les indicateurs en un score de conviction 0-100.
    Seuls les scores > 60 déclenchent un ordre.

    Critères BUY :
      RSI bas (survente) + MACD haussier + prix bas dans BB + volume fort + tendance
    """
    score = 0.0

    if direction == "buy":
        # RSI : max points si RSI très bas (survente)
        score += max(0, (50 - rsi_val) / 50) * 30      # 0-30 pts
        # MACD histogram positif (momentum haussier)
        score += min(20, max(0, macd_hist * 5000))       # 0-20 pts
        # %B bas (prix près de la bande basse)
        score += max(0, (0.5 - pct_b) / 0.5) * 20       # 0-20 pts
        # Volume fort
        score += min(15, max(0, (vol_ratio - 1) * 15))   # 0-15 pts
        # Force de tendance
        score += min(15, trend_str / 100 * 15)            # 0-15 pts

    else:  # sell
        score += max(0, (rsi_val - 50) / 50) * 30
        score += min(20, max(0, -macd_hist * 5000))
        score += max(0, (pct_b - 0.5) / 0.5) * 20
        score += min(15, max(0, (vol_ratio - 1) * 15))
        score += min(15, trend_str / 100 * 15)

    return min(score, 100)
