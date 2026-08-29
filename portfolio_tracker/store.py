"""
Persistance locale du portefeuille — un simple fichier JSON.

Tout est stocké dans data/portfolio.json :
  - settings      : devise de référence, allocation cible, liquidités
  - transactions  : historique brut (achats / ventes / dividendes)
  - instruments   : métadonnées par ticker (nom, classe d'actif, prix manuel)

Aucune donnée ne sort de la machine : le fichier reste en local.
"""

import json
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from threading import Lock

DATA_DIR = Path(__file__).parent / "data"
DB_FILE = DATA_DIR / "portfolio.json"

_LOCK = Lock()

DEFAULTS = {
    "settings": {
        "base_currency": "EUR",
        "cash": 0.0,
        # Allocation cible en % par classe d'actif (sert au rééquilibrage)
        "targets": {"ETF": 70.0, "Action": 25.0, "Liquidités": 5.0},
        "monthly_contribution": 0.0,
    },
    "transactions": [],
    "instruments": {},
}

# Classes d'actifs reconnues (déduites du type Yahoo, modifiables à la main)
ASSET_CLASSES = ["ETF", "Action", "Obligation", "Matières premières", "Crypto", "Autre"]

_YAHOO_TYPE_MAP = {
    "ETF": "ETF",
    "MUTUALFUND": "ETF",
    "EQUITY": "Action",
    "CRYPTOCURRENCY": "Crypto",
    "FUTURE": "Matières premières",
    "INDEX": "Autre",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _merge_defaults(data: dict) -> dict:
    out = json.loads(json.dumps(DEFAULTS))
    out.update({k: v for k, v in data.items() if k in out})
    out["settings"] = {**DEFAULTS["settings"], **data.get("settings", {})}
    return out


def load() -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not DB_FILE.exists():
        return json.loads(json.dumps(DEFAULTS))
    try:
        with open(DB_FILE, encoding="utf-8") as f:
            return _merge_defaults(json.load(f))
    except (json.JSONDecodeError, OSError):
        # Fichier corrompu : on le met de côté plutôt que de perdre l'historique
        if DB_FILE.exists():
            DB_FILE.rename(DB_FILE.with_suffix(".json.bak"))
        return json.loads(json.dumps(DEFAULTS))


def save(data: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = DB_FILE.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    tmp.replace(DB_FILE)


# ──────────────────────────────────────────────────────────────────────────────
#  Transactions
# ──────────────────────────────────────────────────────────────────────────────

def add_transaction(
    symbol: str,
    kind: str,
    qty: float,
    price: float,
    trade_date: str = "",
    fees: float = 0.0,
    currency: str = "",
    note: str = "",
) -> dict:
    """Enregistre un achat (BUY), une vente (SELL) ou un dividende (DIV)."""
    symbol = symbol.strip().upper()
    kind = kind.strip().upper()
    if kind not in ("BUY", "SELL", "DIV"):
        raise ValueError(f"Type d'opération inconnu : {kind}")
    if not symbol:
        raise ValueError("Le ticker est obligatoire")
    qty = float(qty)
    price = float(price)
    if kind != "DIV" and qty <= 0:
        raise ValueError("La quantité doit être positive")
    if price < 0:
        raise ValueError("Le prix ne peut pas être négatif")

    tx = {
        "id": uuid.uuid4().hex[:12],
        "date": (trade_date or date.today().isoformat())[:10],
        "symbol": symbol,
        "type": kind,
        "qty": qty,
        "price": price,
        "fees": float(fees or 0),
        "currency": (currency or "").upper(),
        "note": note.strip(),
        "created_at": now_iso(),
    }

    with _LOCK:
        data = load()
        data["transactions"].append(tx)
        data["transactions"].sort(key=lambda t: (t["date"], t["created_at"]))
        data["instruments"].setdefault(symbol, {"name": "", "asset_class": "", "manual_price": None})
        save(data)
    return tx


def delete_transaction(tx_id: str) -> bool:
    with _LOCK:
        data = load()
        before = len(data["transactions"])
        data["transactions"] = [t for t in data["transactions"] if t["id"] != tx_id]
        changed = len(data["transactions"]) != before
        if changed:
            save(data)
    return changed


# ──────────────────────────────────────────────────────────────────────────────
#  Instruments & réglages
# ──────────────────────────────────────────────────────────────────────────────

def update_instrument(symbol: str, fields: dict) -> dict:
    """Met à jour les métadonnées d'un ticker (nom, classe d'actif, prix manuel)."""
    symbol = symbol.strip().upper()
    allowed = {"name", "asset_class", "manual_price", "pin_manual", "currency"}
    with _LOCK:
        data = load()
        inst = data["instruments"].setdefault(symbol, {})
        for key, value in fields.items():
            if key not in allowed:
                continue
            if key == "manual_price":
                inst["manual_price"] = None if value in (None, "") else float(value)
                inst["manual_ts"] = now_iso() if inst["manual_price"] is not None else None
            elif key == "pin_manual":
                inst["pin_manual"] = bool(value)
            else:
                inst[key] = value
        save(data)
    return inst


def update_settings(fields: dict) -> dict:
    allowed = {"base_currency", "cash", "targets", "monthly_contribution"}
    with _LOCK:
        data = load()
        for key, value in fields.items():
            if key not in allowed:
                continue
            if key in ("cash", "monthly_contribution"):
                data["settings"][key] = float(value or 0)
            elif key == "targets":
                data["settings"]["targets"] = {
                    str(k): float(v) for k, v in dict(value).items() if float(v) >= 0
                }
            else:
                data["settings"][key] = str(value).upper()
        save(data)
        return data["settings"]


def guess_asset_class(yahoo_type: str) -> str:
    return _YAHOO_TYPE_MAP.get((yahoo_type or "").upper(), "Autre")
