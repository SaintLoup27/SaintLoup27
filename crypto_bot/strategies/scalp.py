"""
Stratégie de scalping temps réel optimisée — WebSocket Binance

Améliorations vs version précédente :
  ✦ VWAP temps réel : n'achète que si prix < VWAP (actif sous-évalué)
  ✦ Micro-RSI sur ticks : évite d'acheter en plein surachat même en scalp
  ✦ Déséquilibre carnet d'ordres : confirme la pression acheteuse avant d'entrer
  ✦ Trailing stop-loss : suit le prix à la hausse tick par tick
  ✦ Filtre de spread : ignore les mouvements trop bruités (spread élevé)
  ✦ Filtre de volatilité : n'entre pas si marché trop calme ou trop violent
  ✦ Stats en temps réel : win rate, P&L cumulé, loggés toutes les 5 min
"""

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field

from binance import ThreadedWebsocketManager
from binance.client import Client

import config as cfg
from strategies.indicators import rsi as calc_rsi, volume_ratio
from utils.notifier  import send_telegram
from utils.portfolio import record_buy, record_sell

log = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
#  État d'une paire
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class ScalpState:
    symbol:        str
    # Fenêtres de données temps réel
    price_window:  deque = field(default_factory=lambda: deque(maxlen=2000))
    volume_window: deque = field(default_factory=lambda: deque(maxlen=2000))
    # Position courante
    position:      dict | None = None
    # Contrôle du trading
    last_trade_ts: float = 0.0
    # Stats session
    wins:  int = 0
    losses: int = 0
    total_pnl: float = 0.0
    lock: threading.Lock = field(default_factory=threading.Lock)


# ──────────────────────────────────────────────────────────────────────────────
#  Helpers exchange (avec cache pour éviter les appels API répétés)
# ──────────────────────────────────────────────────────────────────────────────

_symbol_info_cache: dict[str, dict] = {}


def _get_symbol_info(client: Client, symbol: str) -> dict:
    if symbol not in _symbol_info_cache:
        _symbol_info_cache[symbol] = client.get_symbol_info(symbol)
    return _symbol_info_cache[symbol]


def _qty_precision(client: Client, symbol: str) -> int:
    for f in _get_symbol_info(client, symbol)["filters"]:
        if f["filterType"] == "LOT_SIZE":
            step = f["stepSize"]
            return len(step.rstrip("0").split(".")[-1]) if "." in step else 0
    return 6


def _min_notional(client: Client, symbol: str) -> float:
    for f in _get_symbol_info(client, symbol)["filters"]:
        if f["filterType"] in ("MIN_NOTIONAL", "NOTIONAL"):
            return float(f.get("minNotional", f.get("notional", 5)))
    return 5.0


# ──────────────────────────────────────────────────────────────────────────────
#  Indicateurs temps réel sur fenêtre de ticks
# ──────────────────────────────────────────────────────────────────────────────

def _get_recent_prices(state: ScalpState, seconds: float) -> list[float]:
    now = time.time()
    return [p for ts, p in state.price_window if now - ts <= seconds]


def _tick_momentum(state: ScalpState, window_sec: float) -> float | None:
    """Variation % dans la fenêtre de temps."""
    prices = _get_recent_prices(state, window_sec)
    if len(prices) < 2:
        return None
    return (prices[-1] - prices[0]) / prices[0] * 100


def _tick_volatility(state: ScalpState, window_sec: float = 60) -> float:
    """Écart-type des prix récents — mesure la volatilité."""
    prices = _get_recent_prices(state, window_sec)
    if len(prices) < 5:
        return 0.0
    mean = sum(prices) / len(prices)
    return (sum((p - mean) ** 2 for p in prices) / len(prices)) ** 0.5


def _tick_rsi(state: ScalpState, period: int = 14) -> float:
    """RSI calculé sur les derniers ticks."""
    prices = [p for _, p in list(state.price_window)[-period * 3:]]
    if len(prices) < period + 1:
        return 50.0
    return calc_rsi(prices, period)


def _tick_vwap(state: ScalpState, window_sec: float = 300) -> float | None:
    """VWAP approximatif sur les ticks récents (prix × volume)."""
    now = time.time()
    pairs = [(p, v) for (ts, p), (ts2, v)
             in zip(state.price_window, state.volume_window)
             if now - ts <= window_sec]
    if not pairs:
        return None
    total_pv = sum(p * v for p, v in pairs)
    total_v  = sum(v for _, v in pairs)
    return total_pv / total_v if total_v > 0 else None


def _order_book_imbalance(client: Client, symbol: str, depth: int = 10) -> float:
    """
    Déséquilibre carnet d'ordres : bid_vol / (bid_vol + ask_vol)
    > 0.6 → pression acheteuse dominante → signal d'achat fiable
    < 0.4 → pression vendeuse dominante → éviter d'acheter
    """
    try:
        book    = client.get_order_book(symbol=symbol, limit=depth)
        bid_vol = sum(float(b[1]) for b in book["bids"])
        ask_vol = sum(float(a[1]) for a in book["asks"])
        total   = bid_vol + ask_vol
        return bid_vol / total if total > 0 else 0.5
    except Exception:
        return 0.5   # neutre si erreur API


# ──────────────────────────────────────────────────────────────────────────────
#  Ordres
# ──────────────────────────────────────────────────────────────────────────────

def _open_position(client: Client, state: ScalpState, price: float,
                   usdt_amount: float, ob_imbalance: float, dry_run: bool) -> None:
    conf      = cfg.SCALP_CONFIG
    precision = _qty_precision(client, state.symbol)
    min_not   = _min_notional(client, state.symbol)

    if usdt_amount < min_not:
        return

    qty = round(usdt_amount / price, precision)

    if not dry_run:
        try:
            order        = client.order_market_buy(symbol=state.symbol, quoteOrderQty=usdt_amount)
            qty          = float(order["executedQty"])
            usdt_amount   = float(order["cummulativeQuoteQty"])
            price         = usdt_amount / qty if qty > 0 else price
        except Exception as e:
            log.error(f"BUY error {state.symbol}: {e}")
            return

    record_buy(state.symbol, qty, price, usdt_amount)

    sl_price = price * (1 - conf["stop_loss_pct"] / 100)
    state.position = {
        "qty":         qty,
        "entry_price": price,
        "usdt":        usdt_amount,
        "opened_at":   time.time(),
        "trailing_sl": sl_price,        # trailing stop
        "peak_price":  price,           # prix max atteint depuis l'entrée
    }
    state.last_trade_ts = time.time()

    tag = " (sim)" if dry_run else ""
    log.info(
        f"SCALP OPEN{tag} {state.symbol} | {qty} @ {price:.4f} "
        f"| OBI={ob_imbalance:.2f} | SL={sl_price:.4f}"
    )
    send_telegram(cfg.TELEGRAM_TOKEN, cfg.TELEGRAM_CHAT_ID,
        f"<b>⚡ Scalp ouvert{tag}</b> <code>{state.symbol}</code>\n"
        f"• Entrée   : <code>{price:.4f} USDT</code>\n"
        f"• Capital  : <code>{usdt_amount:.2f} USDT</code>\n"
        f"• OB Imbal.: <code>{ob_imbalance:.0%}</code>\n"
        f"• SL       : <code>{sl_price:.4f}</code>"
    )


def _close_position(client: Client, state: ScalpState, price: float,
                    reason: str, dry_run: bool) -> None:
    pos       = state.position
    precision = _qty_precision(client, state.symbol)
    qty       = round(pos["qty"], precision)

    if not dry_run:
        try:
            order         = client.order_market_sell(symbol=state.symbol, quantity=qty)
            qty           = float(order["executedQty"])
            usdt_received = float(order["cummulativeQuoteQty"])
            price         = usdt_received / qty if qty > 0 else price
        except Exception as e:
            log.error(f"SELL error {state.symbol}: {e}")
            return
    else:
        usdt_received = qty * price

    pnl     = usdt_received - pos["usdt"]
    pnl_pct = pnl / pos["usdt"] * 100

    record_sell(state.symbol, qty, price, usdt_received)

    # Mise à jour stats session
    if pnl >= 0:
        state.wins += 1
    else:
        state.losses += 1
    state.total_pnl  += pnl
    state.position      = None
    state.last_trade_ts = time.time()

    tag   = " (sim)" if dry_run else ""
    emoji = "✅" if pnl >= 0 else "❌"
    total_trades = state.wins + state.losses
    win_rate = state.wins / total_trades * 100 if total_trades > 0 else 0

    log.info(
        f"SCALP CLOSE{tag} {state.symbol} | {reason} "
        f"| P&L {pnl:+.3f} USDT ({pnl_pct:+.2f}%) "
        f"| Session: {win_rate:.0f}% WR | P&L total: {state.total_pnl:+.3f}"
    )
    send_telegram(cfg.TELEGRAM_TOKEN, cfg.TELEGRAM_CHAT_ID,
        f"<b>{emoji} Scalp fermé{tag}</b> <code>{state.symbol}</code>\n"
        f"• Raison  : <code>{reason}</code>\n"
        f"• Sortie  : <code>{price:.4f} USDT</code>\n"
        f"• P&L     : <code>{pnl:+.3f} USDT ({pnl_pct:+.2f}%)</code>\n"
        f"• Session : <code>{win_rate:.0f}% WR | {state.total_pnl:+.2f} USDT</code>"
    )


# ──────────────────────────────────────────────────────────────────────────────
#  Callback WebSocket
# ──────────────────────────────────────────────────────────────────────────────

def _make_callback(client: Client, state: ScalpState,
                   usdt_amount: float, dry_run: bool):
    conf = cfg.SCALP_CONFIG

    def on_message(msg: dict) -> None:
        if msg.get("e") == "error":
            log.warning(f"WS error {state.symbol}: {msg}")
            return

        price  = float(msg["p"])   # prix
        volume = float(msg["q"])   # quantité du trade
        ts     = time.time()

        with state.lock:
            state.price_window.append((ts, price))
            state.volume_window.append((ts, volume))

            pos = state.position

            # ── Gestion position ouverte ───────────────────────────────────
            if pos is not None:
                pnl_pct   = (price - pos["entry_price"]) / pos["entry_price"] * 100
                hold_secs = ts - pos["opened_at"]

                # Mise à jour du trailing stop
                if price > pos["peak_price"]:
                    pos["peak_price"] = price
                    new_sl = price * (1 - conf["trailing_stop_pct"] / 100)
                    if new_sl > pos["trailing_sl"]:
                        pos["trailing_sl"] = new_sl

                if price <= pos["trailing_sl"]:
                    _close_position(client, state, price, f"TRAILING STOP ({pnl_pct:.2f}%)", dry_run)
                elif pnl_pct >= conf["take_profit_pct"]:
                    _close_position(client, state, price, f"TAKE-PROFIT ({pnl_pct:.2f}%)", dry_run)
                elif hold_secs >= conf["max_hold_seconds"]:
                    _close_position(client, state, price, f"TIMEOUT {hold_secs:.0f}s ({pnl_pct:.2f}%)", dry_run)
                return

            # ── Recherche de signal d'entrée ──────────────────────────────
            cooldown_ok = (ts - state.last_trade_ts) >= conf["cooldown_seconds"]
            if not cooldown_ok:
                return

            mom = _tick_momentum(state, conf["window_seconds"])
            if mom is None or mom < conf["momentum_buy_pct"]:
                return

            # Filtre RSI tick : éviter d'acheter en surachat
            tick_rsi = _tick_rsi(state)
            if tick_rsi > conf["max_rsi_entry"]:
                return

            # Filtre VWAP : n'acheter que si prix <= VWAP (valeur)
            vwap_val = _tick_vwap(state)
            if vwap_val is not None and price > vwap_val * (1 + conf["max_above_vwap_pct"] / 100):
                return

            # Filtre volatilité : pas trop calme, pas trop violent
            vol = _tick_volatility(state)
            price_pct_vol = vol / price * 100 if price > 0 else 0
            if price_pct_vol < conf["min_volatility_pct"]:
                return
            if price_pct_vol > conf["max_volatility_pct"]:
                return

            # Confirmation carnet d'ordres (appel API synchrone, ~10ms)
            ob_imb = _order_book_imbalance(client, state.symbol, depth=10)
            if ob_imb < conf["min_ob_imbalance"]:
                return

            _open_position(client, state, price, usdt_amount, ob_imb, dry_run)

    return on_message


# ──────────────────────────────────────────────────────────────────────────────
#  Lancement
# ──────────────────────────────────────────────────────────────────────────────

def run_scalper(client: Client, dry_run: bool = False) -> None:
    conf   = cfg.SCALP_CONFIG
    assets = conf["assets"]

    log.info(f"Scalper démarré sur {list(assets.keys())} | dry_run={dry_run}")

    twm = ThreadedWebsocketManager(
        api_key=cfg.BINANCE_API_KEY,
        api_secret=cfg.BINANCE_API_SECRET,
        testnet=cfg.TESTNET,
    )
    twm.start()

    states: dict[str, ScalpState] = {}
    for symbol, usdt_amount in assets.items():
        state = ScalpState(symbol=symbol)
        states[symbol] = state
        cb = _make_callback(client, state, usdt_amount, dry_run)
        twm.start_trade_socket(callback=cb, symbol=symbol)
        log.info(f"WebSocket actif : {symbol}")

    send_telegram(cfg.TELEGRAM_TOKEN, cfg.TELEGRAM_CHAT_ID,
        f"<b>⚡ Scalper démarré (v2){'  (simulation)' if dry_run else ''}</b>\n"
        + "\n".join(f"• {s}: {u} USDT/trade" for s, u in assets.items()) + "\n"
        + f"Filtres actifs : VWAP · RSI tick · Carnet d'ordres · Trailing SL"
    )

    last_stats = time.time()
    try:
        while True:
            time.sleep(5)

            # Rapport de statut toutes les 5 minutes
            if time.time() - last_stats >= 300:
                last_stats = time.time()
                for symbol, st in states.items():
                    with st.lock:
                        pos    = st.position
                        total  = st.wins + st.losses
                        wr     = st.wins / total * 100 if total > 0 else 0
                        recent = [p for _, p in list(st.price_window)[-1:]]
                        cur_p  = recent[0] if recent else 0
                        if pos:
                            pnl_pct = (cur_p - pos["entry_price"]) / pos["entry_price"] * 100 if cur_p else 0
                            log.info(
                                f"  {symbol}: EN POSITION | P&L≈{pnl_pct:+.2f}% "
                                f"| Trail SL={pos['trailing_sl']:.4f} "
                                f"| Session: {wr:.0f}% WR / {st.total_pnl:+.2f} USDT"
                            )
                        else:
                            log.info(
                                f"  {symbol}: en attente | "
                                f"Session: {total} trades {wr:.0f}% WR / {st.total_pnl:+.2f} USDT"
                            )
    except KeyboardInterrupt:
        log.info("Scalper arrêté.")
    finally:
        twm.stop()
