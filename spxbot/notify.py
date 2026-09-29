from __future__ import annotations

import logging

import requests

from .config import Notify

log = logging.getLogger("spxbot")


class Notifier:
    def __init__(self, cfg: Notify):
        self.cfg = cfg

    def send(self, text: str, payload: dict | None = None) -> None:
        c = self.cfg
        if c.console:
            log.info("SIGNAL | %s", text)
        try:
            if c.telegram_bot_token and c.telegram_chat_id:
                requests.post(
                    f"https://api.telegram.org/bot{c.telegram_bot_token}/sendMessage",
                    json={"chat_id": c.telegram_chat_id, "text": text}, timeout=10)
            if c.discord_webhook_url:
                requests.post(c.discord_webhook_url, json={"content": text}, timeout=10)
            if c.webhook_url and payload is not None:
                requests.post(c.webhook_url, json=payload, timeout=10)
        except requests.RequestException as e:  # never let alerting kill the bot
            log.warning("notification failed: %s", e)
