"""
Notifications Telegram.
"""

import logging
import requests

log = logging.getLogger(__name__)


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
