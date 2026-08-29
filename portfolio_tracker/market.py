"""
Récupération des cours (actions, ETF, devises).

Source principale  : Yahoo Finance (endpoint chart public, sans clé API).
                     Cours différé de ~15 min sur la plupart des places
                     européennes, temps réel sur certaines.
Source de secours  : Stooq (CSV) pour les tickers américains.
Dernier recours    : prix saisi manuellement dans l'interface, ou dernier
                     prix connu mis en cache (utilisable hors ligne).

Un cache disque (data/quotes.json) évite de marteler l'API et permet
d'afficher le portefeuille même sans connexion.
"""

import csv
import io
import json
import time
from pathlib import Path
from threading import Lock

import requests

DATA_DIR = Path(__file__).parent / "data"
CACHE_FILE = DATA_DIR / "quotes.json"

YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
YAHOO_SEARCH = "https://query1.finance.yahoo.com/v1/finance/search"
STOOQ_CSV = "https://stooq.com/q/l/?s={symbol}&f=sd2t2ohlcv&h&e=csv"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
    ),
    "Accept": "application/json,text/csv,*/*",
}

# Durée de vie du cache : les cours étant différés, 3 min suffisent largement
CACHE_TTL = 180
TIMEOUT = 8

_LOCK = Lock()
_CACHE: dict | None = None


# ──────────────────────────────────────────────────────────────────────────────
#  Cache disque
# ──────────────────────────────────────────────────────────────────────────────

def _cache() -> dict:
    global _CACHE
    if _CACHE is None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        try:
            with open(CACHE_FILE, encoding="utf-8") as f:
                _CACHE = json.load(f)
        except (OSError, json.JSONDecodeError):
            _CACHE = {}
    return _CACHE


def _cache_write() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(_cache(), f, indent=2, ensure_ascii=False)
    except OSError:
        pass


# ──────────────────────────────────────────────────────────────────────────────
#  Fournisseurs
# ──────────────────────────────────────────────────────────────────────────────

def _fetch_yahoo(symbol: str) -> dict | None:
    try:
        r = requests.get(
            YAHOO_CHART.format(symbol=requests.utils.quote(symbol)),
            params={"interval": "1d", "range": "5d"},
            headers=HEADERS,
            timeout=TIMEOUT,
        )
        if r.status_code != 200:
            return None
        meta = r.json()["chart"]["result"][0]["meta"]
    except (requests.RequestException, ValueError, KeyError, IndexError, TypeError):
        return None

    price = meta.get("regularMarketPrice")
    if price is None:
        return None
    prev = meta.get("chartPreviousClose") or meta.get("previousClose") or price
    return {
        "symbol": meta.get("symbol", symbol),
        "price": float(price),
        "previous_close": float(prev),
        "currency": meta.get("currency") or "",  # brut : "GBp" = pence
        "name": meta.get("longName") or meta.get("shortName") or "",
        "yahoo_type": meta.get("instrumentType", ""),
        "exchange": meta.get("fullExchangeName") or meta.get("exchangeName") or "",
        "market_time": meta.get("regularMarketTime"),
        "source": "Yahoo Finance",
    }


def _fetch_stooq(symbol: str) -> dict | None:
    """Repli pour les tickers US (Stooq attend le suffixe .us)."""
    if "." in symbol or "-" in symbol or "=" in symbol:
        return None
    try:
        r = requests.get(STOOQ_CSV.format(symbol=f"{symbol.lower()}.us"), headers=HEADERS, timeout=TIMEOUT)
        if r.status_code != 200:
            return None
        row = next(csv.DictReader(io.StringIO(r.text)), None)
    except (requests.RequestException, csv.Error, StopIteration):
        return None
    if not row or row.get("Close") in (None, "", "N/D"):
        return None
    try:
        close = float(row["Close"])
        open_ = float(row.get("Open") or close)
    except ValueError:
        return None
    return {
        "symbol": symbol,
        "price": close,
        "previous_close": open_,
        "currency": "USD",
        "name": "",
        "yahoo_type": "",
        "exchange": "Stooq",
        "market_time": None,
        "source": "Stooq",
    }


# ──────────────────────────────────────────────────────────────────────────────
#  API publique
# ──────────────────────────────────────────────────────────────────────────────

def get_quote(symbol: str, force: bool = False) -> dict:
    """Cours d'un ticker, avec cache. Renvoie toujours un dict (price=None si échec)."""
    symbol = symbol.strip().upper()
    cache = _cache()
    entry = cache.get(symbol)
    age = time.time() - entry["fetched_at"] if entry else None

    if entry and not force and age is not None and age < CACHE_TTL:
        return {**entry, "age": round(age), "stale": False}

    quote = _fetch_yahoo(symbol) or _fetch_stooq(symbol)
    if quote is None:
        if entry:  # hors ligne : on ressort le dernier cours connu
            return {**entry, "age": round(age or 0), "stale": True}
        return {
            "symbol": symbol, "price": None, "previous_close": None, "currency": "",
            "name": "", "yahoo_type": "", "exchange": "", "source": "indisponible",
            "fetched_at": time.time(), "age": 0, "stale": True,
        }

    quote["fetched_at"] = time.time()
    with _LOCK:
        cache[symbol] = quote
        _cache_write()
    return {**quote, "age": 0, "stale": False}


def get_quotes(symbols, force: bool = False) -> dict:
    return {s: get_quote(s, force=force) for s in dict.fromkeys(s.upper() for s in symbols)}


def get_fx_rate(src: str, dst: str, force: bool = False) -> float | None:
    """Taux de change src → dst (1 src = X dst)."""
    src, dst = (src or "").upper(), (dst or "").upper()
    if not src or not dst or src == dst:
        return 1.0
    # Les places britanniques cotent en pence
    if src == "GBP" and dst != "GBP":
        pass
    quote = get_quote(f"{src}{dst}=X", force=force)
    if quote.get("price"):
        return float(quote["price"])
    inverse = get_quote(f"{dst}{src}=X", force=force)
    if inverse.get("price"):
        return 1 / float(inverse["price"])
    return None


def normalize_price(price: float, currency: str) -> tuple[float, str]:
    """Certaines places cotent en centimes (GBp, ILA, ZAc) : on repasse en unité."""
    sub = {"GBP": ("GBP", 100), "GBX": ("GBP", 100), "ILA": ("ILS", 100), "ZAC": ("ZAR", 100)}
    if currency in ("GBp", "GBX", "ILA", "ZAc", "ZAC"):
        real, factor = sub[currency.upper()]
        return (price / factor if price is not None else None), real
    return price, (currency or "").upper()


def search(query: str, limit: int = 8) -> list[dict]:
    """Recherche de tickers (nom d'entreprise, ISIN, ticker...)."""
    query = query.strip()
    if len(query) < 2:
        return []
    try:
        r = requests.get(
            YAHOO_SEARCH,
            params={"q": query, "quotesCount": limit, "newsCount": 0, "listsCount": 0},
            headers=HEADERS,
            timeout=TIMEOUT,
        )
        quotes = r.json().get("quotes", []) if r.status_code == 200 else []
    except (requests.RequestException, ValueError):
        return []
    out = []
    for q in quotes:
        if not q.get("symbol"):
            continue
        out.append({
            "symbol": q["symbol"],
            "name": q.get("longname") or q.get("shortname") or "",
            "type": q.get("quoteType", ""),
            "exchange": q.get("exchDisp") or q.get("exchange") or "",
        })
    return out[:limit]
