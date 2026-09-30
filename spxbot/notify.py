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
            if c.ntfy_topic:
                title, _, body = text.partition("\n")
                event = (payload or {}).get("event")
                headers = {
                    "Title": title.encode("latin-1", "replace").decode("latin-1"),  # HTTP headers are latin-1
                    "Tags": {"entry": "chart_with_upwards_trend", "exit": "moneybag", "skip": "zzz"}.get(event, "bell"),
                    "Priority": "default" if event == "skip" else "high",
                }
                if c.ntfy_token:
                    headers["Authorization"] = f"Bearer {c.ntfy_token}"
                requests.post(f"{c.ntfy_server.rstrip('/')}/{c.ntfy_topic}",
                              data=(body or title).encode(), headers=headers, timeout=10)
            if c.webhook_url and payload is not None:
                requests.post(c.webhook_url, json=payload, timeout=10)
        except Exception as e:  # never let alerting kill the bot
            log.warning("notification failed: %s", e)
