#!/usr/bin/env python3
"""Publish scheduled Read This Twice posts to Instagram.

Uses the Instagram API with Instagram Login (graph.instagram.com). Instagram
downloads each image or video from a public URL, so the media in posts/ and
reels/ must be served by GitHub Pages (see .github/workflows/pages.yml).

Commands:
  run [--dry-run]    publish what's due now (at most one post per run)
  check              verify the token, config and the next posts' public URLs
  upcoming [-n N]    list the next posts and their status
  refresh --out F    refresh the 60-day token and write the new one to F

Needs IG_ACCESS_TOKEN in the environment (except for `upcoming` and dry runs).
Every published, recovered or missed post is appended to autopost/published.csv,
which is how the next run knows not to post it again.
"""
import argparse
import csv
import json
import os
import sys
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
LOG_FIELDS = ["key", "due", "status", "media_id", "permalink", "logged_at", "note"]
GRAPH = "https://graph.instagram.com"


@dataclass
class Item:
    key: str            # RTT001, or REEL-RTT002 for a Reel
    kind: str           # image | carousel | reel
    due: datetime       # timezone-aware
    files: list         # repo-relative paths, in slide order
    caption: str
    alt_text: str = ""
    cover: str = ""


class APIError(Exception):
    pass


# --- schedule -------------------------------------------------------------------

def load_config(root=ROOT):
    with open(root / "autopost" / "config.toml", "rb") as f:
        cfg = tomllib.load(f)
    ZoneInfo(cfg["timezone"])  # fails early on a typo
    return cfg


def build_queue(cfg, root=ROOT):
    """Every post on or after start_date, oldest first."""
    tz = ZoneInfo(cfg["timezone"])
    start = date.fromisoformat(cfg["start_date"])
    items = []
    with open(root / "output" / "schedule.csv", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            due = datetime.fromisoformat(f"{row['date']}T{row['time']}").replace(tzinfo=tz)
            if due.date() < start:
                continue
            base = f"posts/{row['date']}_{row['time'].replace(':', '')}_{row['post_id']}"
            slides = int(row["slides"])
            if slides == 1:
                items.append(Item(row["post_id"], "image", due, [f"{base}.jpg"], row["caption"],
                                  alt_text=row["on_image_text"][:1000]))
            else:
                items.append(Item(row["post_id"], "carousel", due,
                                  [f"{base}-{i}.jpg" for i in range(1, slides + 1)], row["caption"]))
    if cfg.get("post_reels"):
        for mp4 in sorted((root / "reels").glob("*_REEL-*.mp4")):
            day, hhmm, key = mp4.stem.split("_", 2)
            due = datetime.strptime(day + hhmm, "%Y-%m-%d%H%M").replace(tzinfo=tz)
            if due.date() < start:
                continue
            caption = mp4.with_suffix(".txt").read_text(encoding="utf-8").strip()
            items.append(Item(key, "reel", due, [f"reels/{mp4.name}"], caption,
                              cover=f"reels/{mp4.stem}-cover.jpg"))
    items.sort(key=lambda i: i.due)
    return items


def read_log(root=ROOT):
    path = root / "autopost" / "published.csv"
    if not path.exists():
        return []
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def append_log(root, item, status, media_id="", permalink="", note=""):
    path = root / "autopost" / "published.csv"
    new = not path.exists()
    with open(path, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LOG_FIELDS)
        if new:
            w.writeheader()
        w.writerow({"key": item.key, "due": item.due.isoformat(), "status": status, "media_id": media_id,
                    "permalink": permalink, "logged_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "note": note})


# --- Instagram API ---------------------------------------------------------------

class InstagramAPI:
    def __init__(self, token, version, request=None, sleep=time.sleep):
        self.token = token
        self.base = f"{GRAPH}/{version}"
        self.request = request or self._http
        self.sleep = sleep
        self._user = None

    def _http(self, method, path, params):
        query = urllib.parse.urlencode({**params, "access_token": self.token})
        if method == "GET":
            req = urllib.request.Request(f"{self.base}/{path}?{query}")
        else:
            req = urllib.request.Request(f"{self.base}/{path}", data=query.encode(), method="POST")
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            try:
                err = json.loads(body)["error"]
                body = f"{err.get('message')} (code {err.get('code')}, subcode {err.get('error_subcode')})"
            except (ValueError, KeyError, TypeError):
                pass
            raise APIError(f"{method} {path} failed: HTTP {e.code}: {body}") from None

    def me(self):
        return self.request("GET", "me", {"fields": "user_id,username"})

    def user_id(self):
        if self._user is None:
            self._user = self.me()["user_id"]
        return self._user

    def create(self, **params):
        return self.request("POST", f"{self.user_id()}/media", params)["id"]

    def wait(self, container, timeout=120, every=3):
        """Wait until Instagram has fetched and processed the media."""
        deadline = time.monotonic() + timeout
        while True:
            status = self.request("GET", container, {"fields": "status_code,status"})
            code = status.get("status_code")
            if code == "FINISHED":
                return
            if code in ("ERROR", "EXPIRED"):
                raise APIError(f"container {container} {code}: {status.get('status', '')}")
            if time.monotonic() > deadline:
                raise APIError(f"container {container} still {code} after {timeout}s")
            self.sleep(every)

    def publish(self, container):
        return self.request("POST", f"{self.user_id()}/media_publish", {"creation_id": container})["id"]

    def permalink(self, media_id):
        return self.request("GET", media_id, {"fields": "permalink"}).get("permalink", "")

    def recent(self, limit=5):
        return self.request("GET", f"{self.user_id()}/media",
                            {"fields": "id,caption,timestamp,permalink", "limit": limit}).get("data", [])

    def quota(self):
        data = self.request("GET", f"{self.user_id()}/content_publishing_limit",
                            {"fields": "quota_usage,config"}).get("data", [{}])
        return data[0] if data else {}


def refresh_token(token):
    """Swap a long-lived token (at least 24h old, not expired) for a fresh 60-day one."""
    query = urllib.parse.urlencode({"grant_type": "ig_refresh_token", "access_token": token})
    try:
        with urllib.request.urlopen(f"{GRAPH}/refresh_access_token?{query}", timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise APIError(f"token refresh failed: HTTP {e.code}: {e.read().decode(errors='replace')}") from None


# --- publishing ------------------------------------------------------------------

def public_url(cfg, rel):
    return cfg["public_base_url"].rstrip("/") + "/" + urllib.parse.quote(rel)


def publish_item(api, cfg, item):
    if item.kind == "image":
        params = {"image_url": public_url(cfg, item.files[0]), "caption": item.caption}
        if item.alt_text:
            params["alt_text"] = item.alt_text
        container = api.create(**params)
        api.wait(container)
    elif item.kind == "carousel":
        children = [api.create(image_url=public_url(cfg, f), is_carousel_item="true") for f in item.files]
        for child in children:
            api.wait(child)
        container = api.create(media_type="CAROUSEL", children=",".join(children), caption=item.caption)
        api.wait(container)
    else:
        container = api.create(media_type="REELS", video_url=public_url(cfg, item.files[0]),
                               cover_url=public_url(cfg, item.cover), share_to_feed="true", caption=item.caption)
        api.wait(container, timeout=600, every=10)
    media_id = api.publish(container)
    return media_id, api.permalink(media_id)


def already_published(api, item, now):
    """A post from the last hour with the same caption means an earlier run
    published this item but couldn't save the log. Posts are hours apart, so
    a caption match inside an hour can only be this item."""
    for m in api.recent():
        ts = datetime.strptime(m["timestamp"], "%Y-%m-%dT%H:%M:%S%z")
        if (m.get("caption") or "").strip() == item.caption.strip() and now - ts < timedelta(hours=1):
            return m
    return None


def run(cfg, api, now, root=ROOT, dry_run=False, out=print):
    """Log anything too late as missed, then publish the oldest due post. One per run,
    so a backlog never goes out as a burst."""
    queue = build_queue(cfg, root)
    done = {r["key"] for r in read_log(root)}
    max_late = timedelta(minutes=cfg.get("max_late_minutes", 180))
    pending = [i for i in queue if i.key not in done]
    due = [i for i in pending if i.due <= now]
    published = False
    for item in due:
        late = now - item.due
        if late > max_late:
            if not dry_run:
                append_log(root, item, "missed", note=f"{int(late.total_seconds() // 60)} min late; not posted")
            out(f"MISSED {item.key} (due {item.due:%a %b %d %H:%M}, {int(late.total_seconds() // 60)} min ago)")
            continue
        if published:
            out(f"Next run: {item.key} (due {item.due:%H:%M})")
            break
        missing = [f for f in item.files + ([item.cover] if item.cover else []) if not (root / f).exists()]
        if missing:
            raise FileNotFoundError(f"{item.key}: missing {', '.join(missing)}")
        if dry_run:
            out(f"WOULD PUBLISH {item.key} ({item.kind}, {len(item.files)} file(s)) due {item.due:%a %b %d %H:%M}")
            published = True
            continue
        dup = already_published(api, item, now)
        if dup:
            append_log(root, item, "published", dup["id"], dup.get("permalink", ""),
                       note="found on Instagram; an earlier run posted it")
            out(f"RECOVERED {item.key}: already on Instagram ({dup.get('permalink', dup['id'])})")
        else:
            media_id, link = publish_item(api, cfg, item)
            append_log(root, item, "published", media_id, link)
            out(f"PUBLISHED {item.key} -> {link or media_id}")
        published = True
    if not due:
        nxt = next(iter(pending), None)
        out(f"Nothing due. Next: {nxt.key} at {nxt.due:%a %b %d %H:%M %Z}" if nxt else
            "Nothing left in the schedule. Add next month's posts.")
    return published


# --- commands --------------------------------------------------------------------

def head(url):
    req = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except urllib.error.URLError as e:
        return 0, str(e.reason)


def cmd_check(cfg, api, now):
    ok = True
    me = api.me()
    print(f"Token works: @{me.get('username')} (Instagram user id {me.get('user_id')})")
    q = api.quota()
    if q:
        print(f"Posts published through the API in the last 24h: {q.get('quota_usage')} of "
              f"{q.get('config', {}).get('quota_total', 100)}")
    if "<" in cfg["public_base_url"]:
        print("PROBLEM: set public_base_url in autopost/config.toml")
        return False
    print(f"Timezone {cfg['timezone']}, posting from {cfg['start_date']}, Reels {'on' if cfg.get('post_reels') else 'off'}")
    done = {r["key"] for r in read_log()}
    upcoming = [i for i in build_queue(cfg) if i.key not in done][:3]
    if not upcoming:
        print("Nothing scheduled on or after start_date yet.")
    for item in upcoming:
        for rel in item.files[:1] + ([item.cover] if item.cover else []):
            status, ctype = head(public_url(cfg, rel))
            good = status == 200 and ctype.split(";")[0] in ("image/jpeg", "video/mp4")
            ok &= good
            print(f"{'ok ' if good else 'BAD'} {item.key} {rel}: HTTP {status} {ctype}")
    return ok


def cmd_upcoming(cfg, now, n):
    log = {r["key"]: r for r in read_log()}
    for item in build_queue(cfg)[:max(n, 0) or None]:
        status = log.get(item.key, {}).get("status") or ("due now" if item.due <= now else "")
        print(f"{item.due:%a %b %d %H:%M}  {item.key:<12} {item.kind:<8} {status}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run"); r.add_argument("--dry-run", action="store_true")
    sub.add_parser("check")
    u = sub.add_parser("upcoming"); u.add_argument("-n", type=int, default=15)
    f = sub.add_parser("refresh"); f.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    cfg = load_config()
    now = datetime.now(ZoneInfo(cfg["timezone"]))
    if args.cmd == "upcoming":
        return cmd_upcoming(cfg, now, args.n)

    token = os.environ.get("IG_ACCESS_TOKEN", "").strip()
    if not token and not (args.cmd == "run" and args.dry_run):
        sys.exit("IG_ACCESS_TOKEN is not set. Add it as a repository secret (see AUTOPOST.md).")
    api = InstagramAPI(token, cfg.get("api_version", "v25.0"))

    if args.cmd == "run":
        run(cfg, api, now, dry_run=args.dry_run)
    elif args.cmd == "check":
        sys.exit(0 if cmd_check(cfg, api, now) else 1)
    elif args.cmd == "refresh":
        res = refresh_token(token)
        new = res["access_token"]
        if os.environ.get("GITHUB_ACTIONS"):
            print(f"::add-mask::{new}")
        Path(args.out).write_text(new, encoding="utf-8")
        print(f"Token refreshed; valid for {int(res.get('expires_in', 0)) // 86400} more days.")


if __name__ == "__main__":
    main()
