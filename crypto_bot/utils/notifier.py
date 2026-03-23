"""
Notifications Telegram.
"""

import logging
import requests

log = logging.getLogger(__name__)


def get_updates(token: str, offset: int = 0, timeout: int = 30) -> list:
    """Récupère les nouveaux messages Telegram via long-polling."""
    if not token:
        return []
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    try:
        r = requests.get(url, params={"offset": offset, "timeout": timeout}, timeout=timeout + 5)
        r.raise_for_status()
        return r.json().get("result", [])
    except Exception as e:
        log.warning(f"Telegram getUpdates error: {e}")
        return []


def send_telegram(token: str, chat_id: str, message: str) -> bool:
    """Envoie un message Telegram. Retourne True si succès."""
    if not token or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"}, timeout=10)
        r.raise_for_status()
        return True
    except Exception as e:
        log.warning(f"Telegram error: {e}")
        return False
