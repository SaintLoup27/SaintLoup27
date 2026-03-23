"""
Suivi du portfolio — sauvegarde locale en JSON.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

DB_FILE = Path(__file__).parent.parent / "data" / "portfolio.json"


def _load() -> dict:
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not DB_FILE.exists():
        return {"history": [], "positions": {}, "initial_usdt": None}
    with open(DB_FILE) as f:
        return json.load(f)


def _save(data: dict) -> None:
    with open(DB_FILE, "w") as f:
        json.dump(data, f, indent=2)


def record_buy(symbol: str, qty: float, price: float, usdt_spent: float) -> None:
    data = _load()
    pos = data["positions"].setdefault(symbol, {"total_qty": 0, "total_usdt": 0, "avg_price": 0, "buys": 0})
    pos["total_qty"]   += qty
    pos["total_usdt"]  += usdt_spent
    pos["avg_price"]    = pos["total_usdt"] / pos["total_qty"]
    pos["buys"]        += 1
    data["history"].append({
        "ts":     datetime.now(timezone.utc).isoformat(),
        "action": "BUY",
        "symbol": symbol,
        "qty":    qty,
        "price":  price,
        "usdt":   usdt_spent,
    })
    _save(data)


def record_sell(symbol: str, qty: float, price: float, usdt_received: float) -> None:
    data = _load()
    pos = data["positions"].get(symbol)
    if pos:
        pos["total_qty"]  = max(0, pos["total_qty"] - qty)
        pos["total_usdt"] = max(0, pos["total_usdt"] - usdt_received)
        if pos["total_qty"] > 0:
            pos["avg_price"] = pos["total_usdt"] / pos["total_qty"]
    data["history"].append({
        "ts":     datetime.now(timezone.utc).isoformat(),
        "action": "SELL",
        "symbol": symbol,
        "qty":    qty,
        "price":  price,
        "usdt":   usdt_received,
    })
    _save(data)


def set_initial_value(usdt: float) -> None:
    data = _load()
    if data["initial_usdt"] is None:
        data["initial_usdt"] = usdt
        _save(data)


def get_positions() -> dict:
    return _load()["positions"]


def get_initial_value() -> float | None:
    return _load()["initial_usdt"]


def get_history() -> list:
    return _load()["history"]
