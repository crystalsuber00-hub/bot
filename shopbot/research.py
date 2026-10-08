"""Finds products worth selling, lists them, and keeps prices and stock in sync with the supplier."""
from __future__ import annotations

import json
import logging
import math
import re
import time

from .config import Config
from .copywriter import Copywriter
from .db import DB, jdump
from .pricing import profit, retail

log = logging.getLogger("shopbot")


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "product"


def blocked(name: str, words: list[str]) -> bool:
    low = name.lower()
    return any(re.search(rf"\b{re.escape(w.lower())}\b", low) for w in words)


def score(unit_profit: float, listed_num: int, ship_days: int) -> float:
    """Profit per sale, boosted by how many other stores already sell it (proven demand), penalised by slow shipping."""
    return round(unit_profit * (1 + math.log1p(listed_num)) / (1 + ship_days / 10), 3)


def evaluate(cfg: Config, supplier, keyword: str, candidate) -> dict | None:
    r = cfg.research
    detail = supplier.product(candidate.pid)
    if not detail or not detail.variants:
        return None
    if blocked(detail.name, r.blocked_words):
        return None
    in_stock = [v for v in detail.variants if v.stock > 0 and v.cost > 0]
    if not in_stock:
        return None
    cheapest = min(in_stock, key=lambda v: v.cost)
    ship = supplier.shipping([(cheapest.vid, 1)], cfg.store.ship_to_countries[0], r.ship_from)
    if not ship or ship.days_max > r.max_shipping_days:
        return None
    variants = [v for v in in_stock if r.min_cost <= v.cost + ship.cost <= r.max_cost]
    if not variants:
        return None
    price, _ = retail(cheapest.cost, ship.cost, cfg.pricing)
    unit_profit = profit(price, cheapest.cost, ship.cost, cfg.pricing)
    return {"keyword": keyword, "product": detail, "ship": ship, "variants": variants, "price": price,
            "profit": unit_profit, "score": score(unit_profit, max(detail.listed_num, candidate.listed_num), ship.days_max)}


def list_product(cfg: Config, db: DB, supplier_name: str, copy: Copywriter, cand: dict) -> int:
    p, ship = cand["product"], cand["ship"]
    text = copy.write(p.name, p.description, cand["keyword"], p.category)
    slug, n = slugify(text["title"]), 2
    base = slug
    while db.one("SELECT 1 FROM products WHERE slug = ?", (slug,)):
        slug, n = f"{base}-{n}", n + 1
    now = time.time()
    pid = db.x(
        """INSERT INTO products (supplier, supplier_pid, slug, title, description, bullets, seo_description, keyword,
           category, image, images, ship_cost, logistic_name, ship_days, score, active, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
        (supplier_name, p.pid, slug, text["title"], text["description"], jdump(text["bullets"]), text["seo_description"],
         cand["keyword"], p.category, p.image, jdump(p.images), ship.cost, ship.name, ship.days_text, cand["score"], now, now))
    for v in cand["variants"]:
        price, compare = retail(v.cost, ship.cost, cfg.pricing)
        db.x("INSERT INTO variants (product_id, supplier_vid, name, image, cost, price, compare_at, stock, active) "
             "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)", (pid, v.vid, v.name, v.image, v.cost, price, compare, v.stock))
    db.log("research", f"Listed '{text['title']}' at ${cand['price']:.2f} (profit ${cand['profit']:.2f}, score {cand['score']})")
    return pid


def run_research(cfg: Config, db: DB, supplier, copy: Copywriter) -> list[int]:
    r = cfg.research
    count = db.one("SELECT COUNT(*) AS n FROM products WHERE active = 1")["n"]
    room = min(r.new_per_run, r.max_products - count)
    if room <= 0:
        log.info("research: catalogue full (%d products)", count)
        return []
    known = {row["supplier_pid"] for row in db.q("SELECT supplier_pid FROM products WHERE supplier = ?", (supplier.name,))}
    pool, seen = [], set()
    for kw in r.keywords:
        try:
            results = supplier.search(kw, r.candidates_per_keyword)
        except Exception as e:  # one bad keyword shouldn't stop the run
            log.warning("research: search %r failed: %s", kw, e)
            continue
        for c in results:
            if c.pid in known or c.pid in seen or blocked(c.name, r.blocked_words):
                continue
            if c.cost and not (r.min_cost * 0.5 <= c.cost <= r.max_cost):
                continue
            seen.add(c.pid)
            pool.append((kw, c))
    # detail + freight lookups are rate limited, so only check the most promising few
    pool.sort(key=lambda kc: kc[1].listed_num, reverse=True)
    scored = []
    for kw, c in pool[: room * 4]:
        try:
            cand = evaluate(cfg, supplier, kw, c)
        except Exception as e:
            log.warning("research: evaluating %s failed: %s", c.pid, e)
            continue
        if cand:
            scored.append(cand)
    scored.sort(key=lambda c: c["score"], reverse=True)
    added = [list_product(cfg, db, supplier.name, copy, c) for c in scored[:room]]
    log.info("research: %d candidates, %d evaluated, %d listed", len(pool), len(scored), len(added))
    return added


def sync_products(cfg: Config, db: DB, supplier) -> dict:
    """Refresh cost and stock for every listed product; reprice, hide sold-out items, bring restocked ones back."""
    stats = {"checked": 0, "repriced": 0, "hidden": 0, "restored": 0}
    for prod in db.q("SELECT * FROM products WHERE supplier = ? AND inactive_reason != 'manual'", (supplier.name,)):
        stats["checked"] += 1
        try:
            detail = supplier.product(prod["supplier_pid"])
        except Exception as e:
            log.warning("sync: %s failed: %s", prod["supplier_pid"], e)
            continue
        if detail is None:
            if prod["active"]:
                db.x("UPDATE products SET active = 0, inactive_reason = 'gone', updated_at = ? WHERE id = ?", (time.time(), prod["id"]))
                db.log("sync", f"Hid '{prod['title']}': no longer available from supplier")
                stats["hidden"] += 1
            continue
        by_vid = {v.vid: v for v in detail.variants}
        any_active = False
        for var in db.q("SELECT * FROM variants WHERE product_id = ?", (prod["id"],)):
            sv = by_vid.get(var["supplier_vid"])
            if sv is None:
                db.x("UPDATE variants SET active = 0, stock = 0 WHERE id = ?", (var["id"],))
                continue
            ok = sv.stock > 0 and sv.cost + prod["ship_cost"] <= cfg.research.max_cost * 1.2
            price, compare = retail(sv.cost, prod["ship_cost"], cfg.pricing)
            if abs(sv.cost - var["cost"]) >= 0.01:
                stats["repriced"] += 1
                db.log("sync", f"'{prod['title']}' {var['name']}: cost ${var['cost']:.2f} -> ${sv.cost:.2f}, price ${var['price']:.2f} -> ${price:.2f}")
            db.x("UPDATE variants SET cost = ?, price = ?, compare_at = ?, stock = ?, active = ? WHERE id = ?",
                 (sv.cost, price, compare, sv.stock, int(ok), var["id"]))
            any_active |= ok
        if any_active and not prod["active"]:
            stats["restored"] += 1
            db.log("sync", f"Restored '{prod['title']}' (back in stock)")
        elif not any_active and prod["active"]:
            stats["hidden"] += 1
            db.log("sync", f"Hid '{prod['title']}': out of stock or too expensive")
        db.x("UPDATE products SET active = ?, inactive_reason = ?, updated_at = ? WHERE id = ?",
             (int(any_active), "" if any_active else "stock", time.time(), prod["id"]))
    return stats


def product_bullets(prod: dict) -> list[str]:
    return json.loads(prod.get("bullets") or "[]")
