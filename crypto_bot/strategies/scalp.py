"""
Stratégie de scalping temps réel via WebSocket Binance.

Principe :
  - Écoute les trades en direct (flux websocket) pour chaque paire.
  - Maintient une fenêtre glissante des derniers prix (ex: 30 secondes).
  - Achète si momentum haussier détecté dans la fenêtre.
  - Vend dès que stop-loss, take-profit ou durée max atteinte.

Réaction en < 1 seconde après chaque tick de prix.

Attention : les frais Binance (0.1% aller + 0.1% retour = 0.2% minimum)
            exigent un take-profit > 0.2%. Les valeurs par défaut (0.8%)
            laissent une marge confortable.
"""

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

from binance import ThreadedWebsocketManager
from binance.client import Client

import config as cfg
from utils.notifier  import send_telegram
from utils.portfolio import record_buy, record_sell

log = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
#  État d'une paire en scalping
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class ScalpState:
    symbol:       str
    price_window: deque = field(default_factory=lambda: deque(maxlen=500))
    position:     dict | None = None      # {"qty", "entry_price", "usdt", "opened_at"}
    last_trade_ts: float = 0.0            # timestamp du dernier trade (cooldown)
    lock:         threading.Lock = field(default_factory=threading.Lock)


# ──────────────────────────────────────────────────────────────────────────────
#  Helpers exchange
# ──────────────────────────────────────────────────────────────────────────────

def _qty_precision(client: Client, symbol: str) -> int:
    info = client.get_symbol_info(symbol)
    for f in info["filters"]:
        if f["filterType"] == "LOT_SIZE":
            step = f["stepSize"]
            return len(step.rstrip("0").split(".")[-1]) if "." in step else 0
    return 6


def _min_notional(client: Client, symbol: str) -> float:
    info = client.get_symbol_info(symbol)
    for f in info["filters"]:
        if f["filterType"] in ("MIN_NOTIONAL", "NOTIONAL"):
            return float(f.get("minNotional", f.get("notional", 5)))
    return 5.0


# ──────────────────────────────────────────────────────────────────────────────
#  Calcul du momentum sur la fenêtre glissante
# ──────────────────────────────────────────────────────────────────────────────

def _momentum(state: ScalpState) -> float | None:
    """
    Retourne la variation en % entre le prix le plus ancien et le plus récent
    dans la fenêtre de temps `window_seconds`.
    Retourne None si pas assez de données.
    """
    now = time.time()
    window = cfg.SCALP_CONFIG["window_seconds"]
    relevant = [(ts, p) for ts, p in state.price_window if now - ts <= window]

    if len(relevant) < 2:
        return None

    oldest_price  = relevant[0][1]
    current_price = relevant[-1][1]

    if oldest_price == 0:
        return None
    return (current_price - oldest_price) / oldest_price * 100


# ──────────────────────────────────────────────────────────────────────────────
#  Ordres
# ──────────────────────────────────────────────────────────────────────────────

def _open_position(client: Client, state: ScalpState, price: float,
                   usdt_amount: float, dry_run: bool) -> None:
    conf      = cfg.SCALP_CONFIG
    precision = _qty_precision(client, state.symbol)
    min_not   = _min_notional(client, state.symbol)

    if usdt_amount < min_not:
        return

    qty = round(usdt_amount / price, precision)

    if not dry_run:
        try:
            order       = client.order_market_buy(symbol=state.symbol, quoteOrderQty=usdt_amount)
            qty         = float(order["executedQty"])
            usdt_amount  = float(order["cummulativeQuoteQty"])
            price        = usdt_amount / qty if qty > 0 else price
        except Exception as e:
            log.error(f"BUY error {state.symbol}: {e}")
            return

    record_buy(state.symbol, qty, price, usdt_amount)
    state.position = {
        "qty":         qty,
        "entry_price": price,
        "usdt":        usdt_amount,
        "opened_at":   time.time(),
    }
    state.last_trade_ts = time.time()

    tag = " (sim)" if dry_run else ""
    log.info(f"SCALP OPEN{tag} {state.symbol} | {qty} @ {price:.4f} | SL={conf['stop_loss_pct']}% TP={conf['take_profit_pct']}%")
    send_telegram(cfg.TELEGRAM_TOKEN, cfg.TELEGRAM_CHAT_ID,
        f"<b>⚡ Scalp ouvert{tag}</b> <code>{state.symbol}</code>\n"
        f"• Prix entrée : <code>{price:.4f}</code>\n"
        f"• Capital     : <code>{usdt_amount:.2f} USDT</code>\n"
        f"• SL: -{conf['stop_loss_pct']}%  TP: +{conf['take_profit_pct']}%"
    )


def _close_position(client: Client, state: ScalpState, price: float,
                    reason: str, dry_run: bool) -> None:
    pos       = state.position
    precision = _qty_precision(client, state.symbol)
    qty       = round(pos["qty"], precision)

    if not dry_run:
        try:
            order        = client.order_market_sell(symbol=state.symbol, quantity=qty)
            qty          = float(order["executedQty"])
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
    state.position      = None
    state.last_trade_ts = time.time()

    tag   = " (sim)" if dry_run else ""
    emoji = "✅" if pnl >= 0 else "❌"
    log.info(f"SCALP CLOSE{tag} {state.symbol} | {reason} | P&L {pnl:+.2f} USDT ({pnl_pct:+.1f}%)")
    send_telegram(cfg.TELEGRAM_TOKEN, cfg.TELEGRAM_CHAT_ID,
        f"<b>{emoji} Scalp fermé{tag}</b> <code>{state.symbol}</code>\n"
        f"• Raison : <code>{reason}</code>\n"
        f"• Sortie : <code>{price:.4f}</code>\n"
        f"• P&L    : <code>{pnl:+.2f} USDT ({pnl_pct:+.1f}%)</code>"
    )


# ──────────────────────────────────────────────────────────────────────────────
#  Callback WebSocket — appelé à chaque trade Binance (~millisecondes)
# ──────────────────────────────────────────────────────────────────────────────

def _make_callback(client: Client, state: ScalpState,
                   usdt_amount: float, dry_run: bool):
    conf = cfg.SCALP_CONFIG

    def on_message(msg: dict) -> None:
        if msg.get("e") == "error":
            log.warning(f"WS error {state.symbol}: {msg}")
            return

        price = float(msg["p"])  # prix du dernier trade
        ts    = time.time()

        with state.lock:
            state.price_window.append((ts, price))

            pos = state.position

            # ── Gestion position ouverte ───────────────────────────────────
            if pos is not None:
                pnl_pct    = (price - pos["entry_price"]) / pos["entry_price"] * 100
                hold_secs  = ts - pos["opened_at"]

                if pnl_pct <= -conf["stop_loss_pct"]:
                    _close_position(client, state, price, f"STOP-LOSS ({pnl_pct:.2f}%)", dry_run)
                elif pnl_pct >= conf["take_profit_pct"]:
                    _close_position(client, state, price, f"TAKE-PROFIT ({pnl_pct:.2f}%)", dry_run)
                elif hold_secs >= conf["max_hold_seconds"]:
                    _close_position(client, state, price, f"TIMEOUT ({hold_secs:.0f}s)", dry_run)
                return

            # ── Pas de position → chercher signal d'entrée ────────────────
            cooldown_ok = (ts - state.last_trade_ts) >= conf["cooldown_seconds"]
            if not cooldown_ok:
                return

            mom = _momentum(state)
            if mom is None:
                return

            if mom >= conf["momentum_buy_pct"]:
                _open_position(client, state, price, usdt_amount, dry_run)

    return on_message


# ──────────────────────────────────────────────────────────────────────────────
#  Point d'entrée : lancer le scalping sur toutes les paires
# ──────────────────────────────────────────────────────────────────────────────

def run_scalper(client: Client, dry_run: bool = False) -> None:
    """
    Lance les flux WebSocket pour chaque paire configurée.
    Bloquant — tourne indéfiniment jusqu'à Ctrl+C.
    """
    conf   = cfg.SCALP_CONFIG
    assets = conf["assets"]

    log.info(f"Scalper démarré sur {list(assets.keys())} | dry_run={dry_run}")

    twm    = ThreadedWebsocketManager(api_key=cfg.BINANCE_API_KEY,
                                      api_secret=cfg.BINANCE_API_SECRET,
                                      testnet=cfg.TESTNET)
    twm.start()

    states = {}
    for symbol, usdt_amount in assets.items():
        state = ScalpState(symbol=symbol)
        states[symbol] = state
        cb = _make_callback(client, state, usdt_amount, dry_run)
        twm.start_trade_socket(callback=cb, symbol=symbol)
        log.info(f"WebSocket actif : {symbol}")

    send_telegram(cfg.TELEGRAM_TOKEN, cfg.TELEGRAM_CHAT_ID,
        f"<b>⚡ Scalper démarré{'  (simulation)' if dry_run else ''}</b>\n"
        + "\n".join(f"• {s}: {u} USDT/trade" for s, u in assets.items())
    )

    try:
        while True:
            time.sleep(10)
            # Log de statut toutes les 5 minutes
            if int(time.time()) % 300 < 10:
                for symbol, st in states.items():
                    pos = st.position
                    if pos:
                        pnl = (list(st.price_window)[-1][1] - pos["entry_price"]) / pos["entry_price"] * 100 if st.price_window else 0
                        log.info(f"  {symbol}: position ouverte | entrée={pos['entry_price']:.4f} | P&L≈{pnl:+.2f}%")
                    else:
                        log.info(f"  {symbol}: en attente de signal")
    except KeyboardInterrupt:
        log.info("Scalper arrêté.")
    finally:
        twm.stop()
