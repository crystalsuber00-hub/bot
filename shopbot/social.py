"""Auto-posting products to Pinterest, a Facebook Page and Instagram, paced so accounts look human and stay within limits."""
from __future__ import annotations

import json
import logging
import time

import requests

from .config import Config
from .copywriter import Copywriter
from .db import DB, jdump
from .research import product_bullets

log = logging.getLogger("shopbot")


class SocialError(Exception):
    pass


def _graph_error(r: requests.Response) -> str:
    try:
        return r.json().get("error", {}).get("message") or r.text[:200]
    except ValueError:
        return f"HTTP {r.status_code}"


class Pinterest:
    API = "https://api.pinterest.com/v5"
    name = "pinterest"

    def __init__(self, cfg: Config, db: DB):
        self.s, self.db = cfg.social, db

    @property
    def enabled(self) -> bool:
        return bool(self.s.pinterest_board_id and (self._token() or self.s.pinterest_refresh_token))

    def _token(self) -> str:
        # a token renewed by refresh() is newer than the one in the config
        return self.db.get_setting("pinterest_access_token") or self.s.pinterest_access_token

    def refresh(self) -> None:
        s = self.s
        refresh = self.db.get_setting("pinterest_refresh_token") or s.pinterest_refresh_token
        if not (refresh and s.pinterest_client_id and s.pinterest_client_secret):
            raise SocialError("Pinterest token expired and no refresh token / app id / app secret is configured")
        r = requests.post(f"{self.API}/oauth/token", auth=(s.pinterest_client_id, s.pinterest_client_secret),
                          data={"grant_type": "refresh_token", "refresh_token": refresh}, timeout=30)
        if not r.ok:
            raise SocialError(f"Pinterest token refresh failed: {r.text[:200]}")
        body = r.json()
        self.db.set_setting("pinterest_access_token", body["access_token"])
        if body.get("refresh_token"):
            self.db.set_setting("pinterest_refresh_token", body["refresh_token"])

    def post(self, title: str, text: str, link: str, image: str, alt: str) -> str:
        body = {"board_id": self.s.pinterest_board_id, "title": title[:100], "description": text[:500], "link": link,
                "alt_text": alt[:500], "media_source": {"source_type": "image_url", "url": image}}
        for attempt in range(2):
            token = self._token()
            if not token:
                self.refresh()
                continue
            r = requests.post(f"{self.API}/pins", json=body, headers={"Authorization": f"Bearer {token}"}, timeout=30)
            if r.status_code == 401 and attempt == 0:
                self.refresh()
                continue
            if not r.ok:
                raise SocialError(f"Pinterest: {r.text[:300]}")
            return str(r.json().get("id", ""))
        raise SocialError("Pinterest: unauthorized")


class FacebookPage:
    name = "facebook"

    def __init__(self, cfg: Config):
        self.s = cfg.social

    @property
    def enabled(self) -> bool:
        return bool(self.s.facebook_page_id and self.s.facebook_page_token)

    def post(self, title: str, text: str, link: str, image: str, alt: str) -> str:
        r = requests.post(f"https://graph.facebook.com/{self.s.graph_version}/{self.s.facebook_page_id}/photos",
                          data={"url": image, "message": f"{text}\n\nShop: {link}", "access_token": self.s.facebook_page_token},
                          timeout=60)
        if not r.ok:
            raise SocialError(f"Facebook: {_graph_error(r)}")
        body = r.json()
        return str(body.get("post_id") or body.get("id", ""))


class Instagram:
    name = "instagram"

    def __init__(self, cfg: Config, wait: float = 5.0):
        self.s, self.wait = cfg.social, wait

    @property
    def enabled(self) -> bool:
        return bool(self.s.instagram_user_id and self.s.facebook_page_token)

    def post(self, title: str, text: str, link: str, image: str, alt: str) -> str:
        base = f"https://graph.facebook.com/{self.s.graph_version}/{self.s.instagram_user_id}"
        token = self.s.facebook_page_token
        r = requests.post(f"{base}/media", data={"image_url": image, "caption": text, "access_token": token}, timeout=60)
        if not r.ok:
            raise SocialError(f"Instagram: {_graph_error(r)}")
        container = r.json()["id"]
        for _ in range(4):   # Instagram downloads the image asynchronously; publishing too early fails
            time.sleep(self.wait)
            r = requests.post(f"{base}/media_publish", data={"creation_id": container, "access_token": token}, timeout=60)
            if r.ok:
                return str(r.json().get("id", ""))
        raise SocialError(f"Instagram publish: {_graph_error(r)}")


def utm(url: str, source: str, medium: str, campaign: str) -> str:
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}utm_source={source}&utm_medium={medium}&utm_campaign={campaign}"


class SocialPoster:
    def __init__(self, cfg: Config, db: DB, copy: Copywriter, channels: list | None = None):
        self.cfg, self.db, self.copy = cfg, db, copy
        self.channels = channels if channels is not None else [Pinterest(cfg, db), FacebookPage(cfg), Instagram(cfg)]
        s = cfg.social
        self.per_day = {"pinterest": s.pinterest_per_day, "facebook": s.facebook_per_day, "instagram": s.instagram_per_day}

    def captions(self, product: dict) -> dict:
        if product.get("social_copy"):
            return json.loads(product["social_copy"])
        caps = self.copy.social(product, product_bullets(product), self.cfg.social.hashtags)
        self.db.x("UPDATE products SET social_copy = ? WHERE id = ?", (jdump(caps), product["id"]))
        return caps

    def _due(self, channel: str, now: float) -> bool:
        """Spread posts evenly through the day instead of bursting them."""
        per_day = self.per_day.get(channel, 0)
        if per_day <= 0:
            return False
        last = self.db.one("SELECT MAX(posted_at) AS t FROM social_posts WHERE channel = ?", (channel,))["t"]
        return last is None or now - last >= 86400 / per_day

    def _pick(self, channel: str, products: list[dict], now: float) -> dict | None:
        posted = {r["product_id"]: r for r in self.db.q(
            "SELECT product_id, MAX(posted_at) AS last, SUM(status = 'error') AS errors FROM social_posts "
            "WHERE channel = ? GROUP BY product_id", (channel,))}
        fresh = [p for p in products if p["id"] not in posted]
        if fresh:
            return fresh[0]   # products arrive sorted best-first
        if channel == "pinterest" and self.cfg.social.pinterest_repin_after_days > 0:
            cutoff = now - self.cfg.social.pinterest_repin_after_days * 86400
            old = [p for p in products if not posted[p["id"]]["errors"] and posted[p["id"]]["last"] < cutoff]
            if old:
                return min(old, key=lambda p: posted[p["id"]]["last"])
        return None

    def run(self, products: list[dict], now: float | None = None) -> int:
        now = now or time.time()
        base, done = self.cfg.store.base_url, 0
        for ch in self.channels:
            if not ch.enabled or not self._due(ch.name, now):
                continue
            p = self._pick(ch.name, products, now)
            if p is None:
                continue
            repin = bool(self.db.one("SELECT 1 FROM social_posts WHERE channel = ? AND product_id = ?", (ch.name, p["id"])))
            # a re-pin gets fresh wording; Pinterest treats identical repeats as duplicates
            caps = self.copy.social(p, product_bullets(p), self.cfg.social.hashtags) if repin else self.captions(p)
            link = utm(f"{base}/p/{p['slug']}", ch.name, "social", "autopost")
            title, text = {
                "pinterest": (caps["pinterest_title"], caps["pinterest_description"]),
                "facebook": (p["title"], caps["facebook"]),
                "instagram": (p["title"], caps["instagram"]),
            }[ch.name]
            try:
                ext = ch.post(title, text, link, p["image"], p["title"])
            except (SocialError, requests.RequestException, KeyError) as e:
                # recorded so the same product isn't retried forever on this channel
                self.db.x("INSERT INTO social_posts (channel, product_id, status, error, posted_at) VALUES (?, ?, 'error', ?, ?)",
                          (ch.name, p["id"], str(e)[:500], now))
                self.db.log("social", f"{ch.name} post failed for '{p['title']}': {e}")
                log.warning("%s post failed: %s", ch.name, e)
                continue
            self.db.x("INSERT INTO social_posts (channel, product_id, status, external_id, posted_at) VALUES (?, ?, 'posted', ?, ?)",
                      (ch.name, p["id"], ext, now))
            self.db.log("social", f"Posted '{p['title']}' to {ch.name}")
            done += 1
        return done
