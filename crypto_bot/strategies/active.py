"""
Stratégie de trading actif : RSI + EMA Crossover

Signaux d'achat  : RSI < seuil_achat  ET EMA_rapide > EMA_lente (tendance haussière)
Signaux de vente : RSI > seuil_vente  OU stop-loss / take-profit atteint

Timeframes recommandés : "5m", "15m" (équilibre signal/bruit)
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

import config as cfg
from utils.notifier  import send_telegram
from utils.portfolio import record_buy, record_sell

log = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
#  État en mémoire des positions ouvertes par paire
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class Position:
    symbol:     str
    qty:        float
    entry_price: float
    usdt_spent: float
    opened_at:  str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# Dictionnaire global : symbol → Position | None
_open_positions: dict[str, Position | None] = {}


def get_position(symbol: str) -> Position | None:
    return _open_positions.get(symbol)


def _set_position(symbol: str, pos: Position | None) -> None:
    _open_positions[symbol] = pos


# ──────────────────────────────────────────────────────────────────────────────
#  Indicateurs techniques
# ──────────────────────────────────────────────────────────────────────────────

def _fetch_closes(client, symbol: str, interval: str, limit: int = 100) -> list[float]:
    """Retourne les N derniers prix de clôture."""
    klines = client.get_klines(symbol=symbol, interval=interval, limit=limit)
    return [float(k[4]) for k in klines]


def _ema(closes: list[float], period: int) -> float:
    """Calcule l'EMA sur la liste de clôtures, retourne la dernière valeur."""
    k = 2 / (period + 1)
    ema = closes[0]
    for price in closes[1:]:
        ema = price * k + ema * (1 - k)
    return ema


def _rsi(closes: list[float], period: int = 14) -> float:
    """Calcule le RSI (Wilder) sur les N dernières bougies."""
    if len(closes) < period + 1:
        return 50.0  # valeur neutre si pas assez de données
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))

    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def compute_signals(client, symbol: str) -> dict:
    """
    Retourne un dict avec les indicateurs et les signaux BUY/SELL/HOLD.
    """
    tf   = cfg.ACTIVE_CONFIG["timeframe"]
    conf = cfg.ACTIVE_CONFIG

    closes = _fetch_closes(client, symbol, tf, limit=max(conf["ema_slow"] * 3, 100))

    rsi       = _rsi(closes, conf["rsi_period"])
    ema_fast  = _ema(closes, conf["ema_fast"])
    ema_slow  = _ema(closes, conf["ema_slow"])
    price     = closes[-1]

    trend_up   = ema_fast > ema_slow        # tendance haussière
    trend_down = ema_fast < ema_slow        # tendance baissière

    buy_signal  = (rsi < conf["rsi_buy"])  and trend_up
    sell_signal = (rsi > conf["rsi_sell"]) or trend_down

    return {
        "price":      price,
        "rsi":        rsi,
        "ema_fast":   ema_fast,
        "ema_slow":   ema_slow,
        "trend_up":   trend_up,
        "buy_signal": buy_signal,
        "sell_signal":sell_signal,
    }


# ──────────────────────────────────────────────────────────────────────────────
#  Précision / validations exchange
# ──────────────────────────────────────────────────────────────────────────────

def _qty_precision(client, symbol: str) -> int:
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
#  Entrée (BUY)
# ──────────────────────────────────────────────────────────────────────────────

def open_trade(client, symbol: str, usdt_amount: float, dry_run: bool = False) -> Position | None:
    """Ouvre un trade en achetant `usdt_amount` USDT de `symbol`."""

    if get_position(symbol) is not None:
        log.debug(f"{symbol}: position déjà ouverte, ignoré.")
        return None

    price     = _fetch_closes(client, symbol, cfg.ACTIVE_CONFIG["timeframe"], limit=1)[0]
    min_not   = _min_notional(client, symbol)
    precision = _qty_precision(client, symbol)

    if usdt_amount < min_not:
        log.warning(f"{symbol}: {usdt_amount} USDT < minimum {min_not} — ignoré.")
        return None

    qty = round(usdt_amount / price, precision)

    log.info(f"[{'DRY' if dry_run else 'BUY'}] {symbol} | {qty} @ {price:.4f} | {usdt_amount:.2f} USDT")

    if not dry_run:
        try:
            order      = client.order_market_buy(symbol=symbol, quoteOrderQty=usdt_amount)
            qty        = float(order["executedQty"])
            usdt_amount = float(order["cummulativeQuoteQty"])
            price      = usdt_amount / qty if qty > 0 else price
        except Exception as e:
            log.error(f"Erreur BUY {symbol}: {e}")
            return None

    record_buy(symbol, qty, price, usdt_amount)
    pos = Position(symbol=symbol, qty=qty, entry_price=price, usdt_spent=usdt_amount)
    _set_position(symbol, pos)

    _notify_open(pos, dry_run)
    return pos


# ──────────────────────────────────────────────────────────────────────────────
#  Sortie (SELL)
# ──────────────────────────────────────────────────────────────────────────────

def close_trade(client, symbol: str, reason: str, dry_run: bool = False) -> dict | None:
    """Ferme le trade ouvert sur `symbol`."""

    pos = get_position(symbol)
    if pos is None:
        return None

    precision    = _qty_precision(client, symbol)
    qty          = round(pos.qty, precision)
    current_price = _fetch_closes(client, symbol, cfg.ACTIVE_CONFIG["timeframe"], limit=1)[0]

    log.info(f"[{'DRY' if dry_run else 'SELL'}] {symbol} | {qty} @ {current_price:.4f} | raison: {reason}")

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
#  Vérification stop-loss / take-profit sur position ouverte
# ──────────────────────────────────────────────────────────────────────────────

def check_exits(client, symbol: str, current_price: float, dry_run: bool = False) -> str | None:
    """
    Vérifie si stop-loss ou take-profit est atteint.
    Retourne la raison de sortie, ou None.
    """
    pos = get_position(symbol)
    if pos is None:
        return None

    pnl_pct = (current_price - pos.entry_price) / pos.entry_price * 100

    if pnl_pct <= -cfg.ACTIVE_CONFIG["stop_loss_pct"]:
        close_trade(client, symbol, reason=f"STOP-LOSS ({pnl_pct:.1f}%)", dry_run=dry_run)
        return "stop_loss"

    if pnl_pct >= cfg.ACTIVE_CONFIG["take_profit_pct"]:
        close_trade(client, symbol, reason=f"TAKE-PROFIT ({pnl_pct:.1f}%)", dry_run=dry_run)
        return "take_profit"

    return None


# ──────────────────────────────────────────────────────────────────────────────
#  Boucle principale pour une paire
# ──────────────────────────────────────────────────────────────────────────────

def scan_pair(client, symbol: str, usdt_amount: float, dry_run: bool = False) -> None:
    """Analyse une paire et exécute les ordres si signaux détectés."""
    try:
        sig = compute_signals(client, symbol)
    except Exception as e:
        log.warning(f"{symbol}: erreur calcul signaux: {e}")
        return

    pos = get_position(symbol)
    price = sig["price"]

    log.debug(
        f"{symbol} | {price:.4f} | RSI={sig['rsi']:.1f} | "
        f"EMA{cfg.ACTIVE_CONFIG['ema_fast']}={'↑' if sig['trend_up'] else '↓'} | "
        f"pos={'OUI' if pos else 'NON'}"
    )

    # Si position ouverte → vérifier sorties (SL/TP/signal)
    if pos is not None:
        exited = check_exits(client, symbol, price, dry_run=dry_run)
        if exited:
            return
        if sig["sell_signal"]:
            close_trade(client, symbol, reason="SIGNAL VENTE (RSI+EMA)", dry_run=dry_run)
        return

    # Pas de position → vérifier signal d'achat
    if sig["buy_signal"]:
        open_trade(client, symbol, usdt_amount, dry_run=dry_run)


# ──────────────────────────────────────────────────────────────────────────────
#  Notifications
# ──────────────────────────────────────────────────────────────────────────────

def _notify_open(pos: Position, dry_run: bool) -> None:
    tag = " (simulation)" if dry_run else ""
    msg = (
        f"<b>📈 Trade ouvert{tag}</b>\n"
        f"• Paire  : <code>{pos.symbol}</code>\n"
        f"• Entrée : <code>{pos.entry_price:.4f} USDT</code>\n"
        f"• Qté    : <code>{pos.qty}</code>\n"
        f"• Capital: <code>{pos.usdt_spent:.2f} USDT</code>\n"
        f"• SL: -{cfg.ACTIVE_CONFIG['stop_loss_pct']}% | TP: +{cfg.ACTIVE_CONFIG['take_profit_pct']}%"
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
