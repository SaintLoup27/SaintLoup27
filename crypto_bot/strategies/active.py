"""
Stratégie de trading actif optimisée — RSI + MACD + Bollinger + Volume + Multi-TF

Améliorations vs version précédente :
  ✦ Score de signal composite (0-100) : entre seulement sur les setups de haute qualité
  ✦ Confirmation multi-timeframe : la tendance doit être alignée sur TF supérieur
  ✦ Filtre de volume : ignore les signaux sur faible volume (faux breakouts)
  ✦ ATR-based stop-loss : stop adapté à la volatilité réelle (évite les stop trop serrés)
  ✦ Trailing stop-loss : suit le prix à la hausse pour maximiser les gains
  ✦ Bollinger Bands : confirme les zones de survente/surachat
  ✦ MACD : confirme le momentum et l'inversion de tendance
  ✦ Détection de marché en range : suspend le trading si ADX < seuil
"""

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from binance.client import Client

import config as cfg
from strategies.indicators import (
    ema, rsi, macd, bollinger, bollinger_pct_b,
    atr, vwap, volume_ratio, trend_strength, signal_score
)
from utils.notifier  import send_telegram
from utils.portfolio import record_buy, record_sell

log = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
#  Position
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class Position:
    symbol:      str
    qty:         float
    entry_price: float
    usdt_spent:  float
    trailing_sl: float          # stop-loss qui monte avec le prix
    atr_at_entry: float         # ATR au moment de l'entrée
    score:       float = 0.0    # score du signal d'entrée
    opened_at:   str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


_open_positions: dict[str, Position | None] = {}


def get_position(symbol: str) -> Position | None:
    return _open_positions.get(symbol)


def _set_position(symbol: str, pos: Position | None) -> None:
    _open_positions[symbol] = pos


# ──────────────────────────────────────────────────────────────────────────────
#  Récupération des données OHLCV
# ──────────────────────────────────────────────────────────────────────────────

def _fetch_ohlcv(client: Client, symbol: str, interval: str, limit: int = 150
                 ) -> dict[str, list[float]]:
    """Retourne opens, highs, lows, closes, volumes."""
    klines = client.get_klines(symbol=symbol, interval=interval, limit=limit)
    return {
        "opens":   [float(k[1]) for k in klines],
        "highs":   [float(k[2]) for k in klines],
        "lows":    [float(k[3]) for k in klines],
        "closes":  [float(k[4]) for k in klines],
        "volumes": [float(k[5]) for k in klines],
    }


# ──────────────────────────────────────────────────────────────────────────────
#  Calcul des signaux sur un timeframe
# ──────────────────────────────────────────────────────────────────────────────

def _compute_tf_signals(ohlcv: dict, conf: dict) -> dict:
    closes  = ohlcv["closes"]
    highs   = ohlcv["highs"]
    lows    = ohlcv["lows"]
    volumes = ohlcv["volumes"]
    price   = closes[-1]

    rsi_val              = rsi(closes, conf["rsi_period"])
    ema_f                = ema(closes, conf["ema_fast"])
    ema_s                = ema(closes, conf["ema_slow"])
    macd_line, sig_line, hist = macd(closes)
    bb_upper, bb_mid, bb_lower = bollinger(closes, conf["bb_period"], conf["bb_std"])
    pct_b                = bollinger_pct_b(price, bb_upper, bb_lower)
    vol_rat              = volume_ratio(volumes, conf["volume_ma_period"])
    t_strength           = trend_strength(closes, conf["trend_period"])
    atr_val              = atr(highs, lows, closes, conf["atr_period"])
    vwap_val             = vwap(highs, lows, closes, volumes)

    trend_up   = ema_f > ema_s and macd_line > sig_line
    trend_down = ema_f < ema_s and macd_line < sig_line

    buy_score  = signal_score(rsi_val, hist, pct_b, vol_rat, t_strength, "buy")
    sell_score = signal_score(rsi_val, hist, pct_b, vol_rat, t_strength, "sell")

    return {
        "price":       price,
        "rsi":         rsi_val,
        "ema_fast":    ema_f,
        "ema_slow":    ema_s,
        "macd_line":   macd_line,
        "macd_hist":   hist,
        "bb_upper":    bb_upper,
        "bb_lower":    bb_lower,
        "pct_b":       pct_b,
        "vol_ratio":   vol_rat,
        "trend_str":   t_strength,
        "atr":         atr_val,
        "vwap":        vwap_val,
        "trend_up":    trend_up,
        "trend_down":  trend_down,
        "buy_score":   buy_score,
        "sell_score":  sell_score,
        "ranging":     t_strength < conf["min_trend_strength"],
    }


# ──────────────────────────────────────────────────────────────────────────────
#  Confirmation multi-timeframe
# ──────────────────────────────────────────────────────────────────────────────

_TF_UPPER = {"1m": "5m", "3m": "15m", "5m": "15m", "15m": "1h", "1h": "4h"}


def _higher_tf_trend(client: Client, symbol: str, base_tf: str) -> str:
    """
    Retourne "up", "down" ou "neutral" sur le timeframe supérieur.
    Utilisé pour ne trader QUE dans le sens de la tendance principale.
    """
    htf = _TF_UPPER.get(base_tf, "1h")
    try:
        ohlcv = _fetch_ohlcv(client, symbol, htf, limit=60)
        closes = ohlcv["closes"]
        ema_f  = ema(closes, 9)
        ema_s  = ema(closes, 21)
        m_line, s_line, _ = macd(closes)
        if ema_f > ema_s and m_line > s_line:
            return "up"
        if ema_f < ema_s and m_line < s_line:
            return "down"
    except Exception:
        pass
    return "neutral"


# ──────────────────────────────────────────────────────────────────────────────
#  Point d'entrée public : analyse complète d'une paire
# ──────────────────────────────────────────────────────────────────────────────

def compute_signals(client: Client, symbol: str) -> dict:
    conf  = cfg.ACTIVE_CONFIG
    tf    = conf["timeframe"]
    ohlcv = _fetch_ohlcv(client, symbol, tf, limit=150)
    sig   = _compute_tf_signals(ohlcv, conf)
    sig["htf_trend"] = _higher_tf_trend(client, symbol, tf)

    # Signal final : score + confirmation HTF
    sig["buy_confirmed"]  = (
        sig["buy_score"] >= conf["min_signal_score"]
        and sig["trend_up"]
        and sig["htf_trend"] != "down"       # ne pas acheter contre la tendance principale
        and not sig["ranging"]
        and sig["vol_ratio"] >= conf["min_volume_ratio"]
    )
    sig["sell_confirmed"] = (
        sig["sell_score"] >= conf["min_signal_score"]
        or sig["trend_down"]
        or sig["htf_trend"] == "down"
    )
    return sig


# ──────────────────────────────────────────────────────────────────────────────
#  Helpers exchange
# ──────────────────────────────────────────────────────────────────────────────

def _qty_precision(client: Client, symbol: str) -> int:
    for f in client.get_symbol_info(symbol)["filters"]:
        if f["filterType"] == "LOT_SIZE":
            step = f["stepSize"]
            return len(step.rstrip("0").split(".")[-1]) if "." in step else 0
    return 6


def _min_notional(client: Client, symbol: str) -> float:
    for f in client.get_symbol_info(symbol)["filters"]:
        if f["filterType"] in ("MIN_NOTIONAL", "NOTIONAL"):
            return float(f.get("minNotional", f.get("notional", 5)))
    return 5.0


# ──────────────────────────────────────────────────────────────────────────────
#  Entrée
# ──────────────────────────────────────────────────────────────────────────────

def open_trade(client: Client, symbol: str, usdt_amount: float,
               sig: dict, dry_run: bool = False) -> Position | None:

    if get_position(symbol) is not None:
        return None

    conf      = cfg.ACTIVE_CONFIG
    price     = sig["price"]
    atr_val   = sig["atr"]
    precision = _qty_precision(client, symbol)
    min_not   = _min_notional(client, symbol)

    if usdt_amount < min_not:
        log.warning(f"{symbol}: {usdt_amount} USDT < minimum {min_not} — ignoré.")
        return None

    # Stop-loss basé sur l'ATR (2× ATR sous le prix d'entrée)
    sl_price = price - atr_val * conf["atr_sl_multiplier"]

    qty = round(usdt_amount / price, precision)

    log.info(
        f"[{'DRY' if dry_run else 'BUY'}] {symbol} | score={sig['buy_score']:.0f} "
        f"| RSI={sig['rsi']:.1f} | HTF={sig['htf_trend']} "
        f"| {qty} @ {price:.4f} | SL={sl_price:.4f}"
    )

    if not dry_run:
        try:
            order       = client.order_market_buy(symbol=symbol, quoteOrderQty=usdt_amount)
            qty         = float(order["executedQty"])
            usdt_amount  = float(order["cummulativeQuoteQty"])
            price        = usdt_amount / qty if qty > 0 else price
            sl_price     = price - atr_val * conf["atr_sl_multiplier"]
        except Exception as e:
            log.error(f"Erreur BUY {symbol}: {e}")
            return None

    record_buy(symbol, qty, price, usdt_amount)
    pos = Position(
        symbol=symbol, qty=qty, entry_price=price,
        usdt_spent=usdt_amount, trailing_sl=sl_price,
        atr_at_entry=atr_val, score=sig["buy_score"],
    )
    _set_position(symbol, pos)
    _notify_open(pos, sig, dry_run)
    return pos


# ──────────────────────────────────────────────────────────────────────────────
#  Sortie
# ──────────────────────────────────────────────────────────────────────────────

def close_trade(client: Client, symbol: str, reason: str,
                dry_run: bool = False) -> dict | None:
    pos = get_position(symbol)
    if pos is None:
        return None

    precision     = _qty_precision(client, symbol)
    qty           = round(pos.qty, precision)
    current_price = float(client.get_symbol_ticker(symbol=symbol)["price"])

    log.info(f"[{'DRY' if dry_run else 'SELL'}] {symbol} | {qty} @ {current_price:.4f} | {reason}")

    if not dry_run:
        try:
            order         = client.order_market_sell(symbol=symbol, quantity=qty)
            qty           = float(order["executedQty"])
            usdt_received = float(order["cummulativeQuoteQty"])
            current_price  = usdt_received / qty if qty > 0 else current_price
        except Exception as e:
            log.error(f"Erreur SELL {symbol}: {e}")
            return None
    else:
        usdt_received = qty * current_price

    pnl     = usdt_received - pos.usdt_spent
    pnl_pct = pnl / pos.usdt_spent * 100

    record_sell(symbol, qty, current_price, usdt_received)
    _set_position(symbol, None)
    _notify_close(symbol, qty, current_price, usdt_received, pnl, pnl_pct, reason, dry_run)
    return {"symbol": symbol, "pnl": pnl, "pnl_pct": pnl_pct, "reason": reason}


# ──────────────────────────────────────────────────────────────────────────────
#  Gestion du trailing stop et des sorties
# ──────────────────────────────────────────────────────────────────────────────

def check_exits(client: Client, symbol: str, current_price: float,
                dry_run: bool = False) -> str | None:
    pos = get_position(symbol)
    if pos is None:
        return None

    conf    = cfg.ACTIVE_CONFIG
    pnl_pct = (current_price - pos.entry_price) / pos.entry_price * 100

    # Mise à jour du trailing stop (monte uniquement, ne descend jamais)
    new_sl = current_price - pos.atr_at_entry * conf["atr_sl_multiplier"]
    if new_sl > pos.trailing_sl:
        pos.trailing_sl = new_sl
        log.debug(f"{symbol}: trailing SL → {new_sl:.4f}")

    # Vérification stop-loss trailing
    if current_price <= pos.trailing_sl:
        close_trade(client, symbol, f"TRAILING STOP ({pnl_pct:.1f}%)", dry_run)
        return "trailing_stop"

    # Take-profit fixe en dernier recours
    if pnl_pct >= conf["take_profit_pct"]:
        close_trade(client, symbol, f"TAKE-PROFIT ({pnl_pct:.1f}%)", dry_run)
        return "take_profit"

    return None


# ──────────────────────────────────────────────────────────────────────────────
#  Boucle principale par paire
# ──────────────────────────────────────────────────────────────────────────────

def scan_pair(client: Client, symbol: str, usdt_amount: float,
              dry_run: bool = False) -> None:
    try:
        sig = compute_signals(client, symbol)
    except Exception as e:
        log.warning(f"{symbol}: erreur signaux: {e}")
        return

    price = sig["price"]
    pos   = get_position(symbol)

    log.info(
        f"{symbol} | {price:.4f} | RSI={sig['rsi']:.1f} | "
        f"score_buy={sig['buy_score']:.0f} | vol={sig['vol_ratio']:.2f}x | "
        f"trend={sig['trend_str']:.0f} | HTF={sig['htf_trend']} | "
        f"ranging={'OUI' if sig['ranging'] else 'non'} | "
        f"pos={'OUI' if pos else 'non'}"
    )

    if pos is not None:
        exited = check_exits(client, symbol, price, dry_run=dry_run)
        if exited:
            return
        if sig["sell_confirmed"]:
            close_trade(client, symbol, "SIGNAL VENTE CONFIRMÉ", dry_run=dry_run)
        return

    if sig["buy_confirmed"]:
        open_trade(client, symbol, usdt_amount, sig, dry_run=dry_run)


# ──────────────────────────────────────────────────────────────────────────────
#  Notifications
# ──────────────────────────────────────────────────────────────────────────────

def _notify_open(pos: Position, sig: dict, dry_run: bool) -> None:
    conf = cfg.ACTIVE_CONFIG
    tag  = " (simulation)" if dry_run else ""
    msg = (
        f"<b>📈 Trade ouvert{tag}</b>\n"
        f"• Paire   : <code>{pos.symbol}</code>\n"
        f"• Score   : <code>{pos.score:.0f}/100</code>\n"
        f"• Entrée  : <code>{pos.entry_price:.4f} USDT</code>\n"
        f"• Capital : <code>{pos.usdt_spent:.2f} USDT</code>\n"
        f"• Trail SL: <code>{pos.trailing_sl:.4f}</code>\n"
        f"• RSI={sig['rsi']:.1f} | Vol={sig['vol_ratio']:.1f}x | HTF={sig['htf_trend']}\n"
        f"• TP fixe : <code>+{conf['take_profit_pct']}%</code>"
    )
    send_telegram(cfg.TELEGRAM_TOKEN, cfg.TELEGRAM_CHAT_ID, msg)


def _notify_close(symbol, qty, price, usdt, pnl, pnl_pct, reason, dry_run) -> None:
    tag   = " (simulation)" if dry_run else ""
    emoji = "✅" if pnl >= 0 else "❌"
    msg = (
        f"<b>{emoji} Trade fermé{tag}</b>\n"
        f"• Paire  : <code>{symbol}</code>\n"
        f"• Raison : <code>{reason}</code>\n"
        f"• Sortie : <code>{price:.4f} USDT</code>\n"
        f"• Reçu   : <code>{usdt:.2f} USDT</code>\n"
        f"• P&L    : <code>{pnl:+.2f} USDT ({pnl_pct:+.1f}%)</code>"
    )
    send_telegram(cfg.TELEGRAM_TOKEN, cfg.TELEGRAM_CHAT_ID, msg)
