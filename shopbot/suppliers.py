"""Supplier integrations. Each supplier can search its catalogue, quote shipping, place and pay orders, and report tracking."""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from html import unescape
from pathlib import Path

import requests

log = logging.getLogger("shopbot")


@dataclass
class SupplierVariant:
    vid: str
    name: str
    cost: float
    image: str = ""
    stock: int = 0


@dataclass
class SupplierProduct:
    pid: str
    name: str
    cost: float                  # cheapest known unit cost (search results) before variants are loaded
    image: str = ""
    images: list = field(default_factory=list)
    description: str = ""
    category: str = ""
    listed_num: int = 0          # how many other stores sell it: a demand signal
    variants: list = field(default_factory=list)


@dataclass
class Shipping:
    name: str
    cost: float
    days_max: int
    days_text: str


@dataclass
class SupplierOrder:
    order_id: str
    status: str                  # placed | processing | shipped | delivered | cancelled
    raw_status: str = ""
    tracking_number: str = ""
    tracking_url: str = ""
    carrier: str = ""
    cost: float = 0.0


class SupplierError(Exception):
    pass


def first_price(v) -> float:
    """CJ prices come as numbers, '3.20', or ranges like '1.50 -- 3.20'; take the highest (conservative)."""
    nums = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", str(v or ""))]
    return max(nums) if nums else 0.0


def strip_html(s: str) -> str:
    s = re.sub(r"<(br|/p|/li|/div)[^>]*>", "\n", s or "", flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = unescape(s)
    return "\n".join(" ".join(line.split()) for line in s.splitlines() if line.strip())


def max_days(text: str) -> int:
    nums = [int(n) for n in re.findall(r"\d+", str(text or ""))]
    return max(nums) if nums else 99


CJ_STATUS = {
    "CREATED": "placed", "IN_CART": "placed", "UNPAID": "placed",
    "PENDING": "processing", "PROCESSING": "processing", "UNSHIPPED": "processing",
    "SHIPPED": "shipped", "DELIVERED": "delivered", "CANCELLED": "cancelled",
}


class CJSupplier:
    """CJdropshipping API 2.0 (https://developers.cjdropshipping.com). Get an API key in CJ: My CJ > Authorization > API."""

    name = "cj"

    def __init__(self, api_key: str, base_url: str, token_cache: str, sandbox: bool = True):
        if not api_key:
            raise SupplierError("CJ_API_KEY is not set")
        self.api_key, self.base, self.sandbox = api_key, base_url.rstrip("/"), sandbox
        self.cache = Path(token_cache)
        self._lock = threading.Lock()
        self._last_call = 0.0
        self._token = None

    # --- plumbing -------------------------------------------------------
    def _throttle(self):
        wait = 1.1 - (time.time() - self._last_call)   # CJ allows 1 request/second
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.time()

    def _get_token(self) -> str:
        if self._token:
            return self._token
        if self.cache.exists():
            data = json.loads(self.cache.read_text())
            if data.get("expires", 0) > time.time() + 86400:
                self._token = data["token"]
                return self._token
        self._throttle()
        r = requests.post(f"{self.base}/authentication/getAccessToken", json={"apiKey": self.api_key}, timeout=30)
        body = r.json()
        if not body.get("result"):
            raise SupplierError(f"CJ login failed: {body.get('message')}")
        self._token = body["data"]["accessToken"]
        # tokens last 180 days per the docs; refresh well before that
        self.cache.write_text(json.dumps({"token": self._token, "expires": time.time() + 150 * 86400}))
        return self._token

    def _call(self, method: str, path: str, params=None, body=None):
        with self._lock:
            for attempt in range(2):
                self._throttle()
                r = requests.request(method, f"{self.base}/{path}", params=params, json=body, timeout=45,
                                     headers={"CJ-Access-Token": self._get_token()})
                if r.status_code == 401 and attempt == 0:
                    self._token = None
                    self.cache.unlink(missing_ok=True)
                    continue
                try:
                    data = r.json()
                except ValueError:
                    raise SupplierError(f"CJ {path}: HTTP {r.status_code}")
                if not data.get("result") and data.get("code") != 200:
                    raise SupplierError(f"CJ {path}: {data.get('message')} (code {data.get('code')})")
                return data.get("data")
            raise SupplierError(f"CJ {path}: unauthorized")

    # --- catalogue ------------------------------------------------------
    def search(self, keyword: str, limit: int = 20) -> list[SupplierProduct]:
        data = self._call("GET", "product/listV2", params={"keyWord": keyword, "page": 1, "size": min(limit, 100)}) or {}
        out = []
        for block in data.get("content") or []:
            for p in block.get("productList") or []:
                out.append(SupplierProduct(
                    pid=str(p.get("id")), name=p.get("nameEn") or "", cost=first_price(p.get("sellPrice")),
                    image=p.get("bigImage") or "", category=p.get("categoryName") or p.get("threeCategoryName") or "",
                    listed_num=int(p.get("listedNum") or 0)))
        return out[:limit]

    def product(self, pid: str) -> SupplierProduct | None:
        try:
            p = self._call("GET", "product/query", params={"pid": pid})
        except SupplierError as e:
            log.warning("CJ product %s: %s", pid, e)
            return None
        if not p:
            return None
        images = p.get("productImageSet") or []
        if isinstance(images, str):
            images = [i for i in re.split(r"[,\s]+", images.strip("[]")) if i.startswith("http")]
        variants = []
        for v in p.get("variants") or []:
            stock = sum(int(i.get("totalInventory") or 0) for i in (v.get("inventories") or []))
            variants.append(SupplierVariant(
                vid=str(v.get("vid")), name=v.get("variantKey") or v.get("variantNameEn") or "",
                cost=first_price(v.get("variantSellPrice")), image=v.get("variantImage") or "",
                stock=stock if v.get("inventories") is not None else 999))
        return SupplierProduct(
            pid=str(p.get("pid")), name=p.get("productNameEn") or "", cost=first_price(p.get("sellPrice")),
            image=p.get("bigImage") or (images[0] if images else ""), images=images[:8],
            description=strip_html(p.get("description") or ""), category=p.get("categoryName") or "",
            listed_num=int(p.get("listedNum") or 0), variants=variants)

    def shipping(self, items: list[tuple[str, int]], country: str, from_country: str = "CN") -> Shipping | None:
        """Cheapest shipping option for these (vid, quantity) items."""
        try:
            options = self._call("POST", "logistic/freightCalculate", body={
                "startCountryCode": from_country, "endCountryCode": country,
                "products": [{"vid": vid, "quantity": qty} for vid, qty in items]}) or []
        except SupplierError as e:
            log.warning("CJ freight %s: %s", items, e)
            return None
        quotes = [Shipping(o.get("logisticName", ""), float(o.get("logisticPrice") or 0),
                           max_days(o.get("logisticAging")), str(o.get("logisticAging") or "")) for o in options]
        quotes = [q for q in quotes if q.name]
        # cheapest, breaking ties on speed
        return min(quotes, key=lambda q: (q.cost, q.days_max)) if quotes else None

    # --- orders ---------------------------------------------------------
    def place_order(self, order_number: str, ship: dict, items: list[tuple[str, int]], logistic_name: str,
                    from_country: str = "CN") -> SupplierOrder:
        body = {
            "orderNumber": order_number,
            "shippingCountryCode": ship["country"], "shippingCountry": ship.get("country_name") or ship["country"],
            "shippingProvince": ship.get("state", ""), "shippingCity": ship.get("city", ""),
            "shippingZip": ship.get("postal_code", ""), "shippingAddress": ship.get("line1", ""),
            "shippingAddress2": ship.get("line2", ""), "shippingCustomerName": ship.get("name", ""),
            "shippingPhone": ship.get("phone", ""), "email": ship.get("email", ""),
            "logisticName": logistic_name, "fromCountryCode": from_country,
            "payType": 3,   # create only; paid separately with pay()
            "products": [{"vid": vid, "quantity": qty} for vid, qty in items],
        }
        if self.sandbox:
            body["isSandbox"] = 1
        d = self._call("POST", "shopping/order/createOrderV2", body=body) or {}
        if d.get("interceptOrderReasons"):
            raise SupplierError(f"CJ intercepted order: {d['interceptOrderReasons']}")
        return SupplierOrder(order_id=str(d.get("orderId")), status=CJ_STATUS.get(str(d.get("orderStatus")), "placed"),
                             raw_status=str(d.get("orderStatus") or ""), cost=first_price(d.get("orderAmount")))

    def pay(self, order_id: str) -> None:
        self._call("POST", "shopping/pay/payBalance", body={"orderId": order_id})

    def order(self, order_id: str) -> SupplierOrder:
        d = self._call("GET", "shopping/order/getOrderDetail", params={"orderId": order_id}) or {}
        raw = str(d.get("orderStatus") or "")
        return SupplierOrder(order_id=order_id, status=CJ_STATUS.get(raw, "processing"), raw_status=raw,
                             tracking_number=d.get("trackNumber") or "", tracking_url=d.get("trackingUrl") or "",
                             carrier=d.get("logisticName") or d.get("trackingProvider") or "",
                             cost=first_price(d.get("orderAmount")))


class DemoSupplier:
    """Made-up catalogue so the whole store can be tried without accounts. Orders ship after a minute, deliver after three."""

    name = "demo"
    ADJ = ["Compact", "Foldable", "Rechargeable", "Silicone", "Adjustable", "Magnetic", "Portable", "Stainless Steel"]
    SHIP_AFTER, DELIVER_AFTER = 60, 180

    def _rng(self, seed: str) -> int:
        return int(hashlib.sha256(seed.encode()).hexdigest()[:8], 16)

    def search(self, keyword: str, limit: int = 20) -> list[SupplierProduct]:
        kw = re.sub(r"[^a-z0-9]+", "_", keyword.lower()).strip("_")
        return [self._make(f"demo-{kw}-{i}") for i in range(min(limit, 12))]

    def _make(self, pid: str) -> SupplierProduct:
        _, kw, i = pid.split("-")
        keyword, r = kw.replace("_", " "), self._rng(pid)
        cost = round(2 + (r % 1200) / 100, 2)
        name = f"{self.ADJ[r % len(self.ADJ)]} {keyword.title()} 2026 New Hot Sale Free Shipping Model {chr(65 + int(i))}"
        img = f"https://picsum.photos/seed/{pid}/600/600"
        variants = [SupplierVariant(f"{pid}-v{j}", c, round(cost + j * 0.5, 2), f"https://picsum.photos/seed/{pid}{j}/600/600",
                                    0 if (r >> j) % 11 == 0 else 50 + r % 400)
                    for j, c in enumerate(["Black", "White", "Blue"][: 1 + r % 3])]
        return SupplierProduct(pid=pid, name=name, cost=cost, image=img, images=[img] + [v.image for v in variants],
                               description=f"Material: ABS + silicone\nPackage includes: 1 x {keyword}\nUse: home, travel, gift",
                               category=keyword.title(), listed_num=r % 900, variants=variants)

    def product(self, pid: str) -> SupplierProduct | None:
        return self._make(pid) if re.fullmatch(r"demo-[a-z0-9_]+-\d+", pid) else None

    def shipping(self, items: list[tuple[str, int]], country: str, from_country: str = "CN") -> Shipping | None:
        r = self._rng(items[0][0])
        days = 8 + r % 15
        units = sum(q for _, q in items)
        return Shipping("CJPacket Ordinary", round((2 + (r % 500) / 100) * (1 + 0.4 * (units - 1)), 2), days, f"{days - 5}-{days}")

    def place_order(self, order_number, ship, items, logistic_name, from_country="CN") -> SupplierOrder:
        return SupplierOrder(order_id=f"DEMO-{int(time.time())}-{order_number}", status="placed", raw_status="CREATED")

    def pay(self, order_id: str) -> None:
        pass

    def order(self, order_id: str) -> SupplierOrder:
        age = time.time() - int(order_id.split("-")[1])
        status = "delivered" if age >= self.DELIVER_AFTER else "shipped" if age >= self.SHIP_AFTER else "processing"
        tracking = f"DM{self._rng(order_id) % 10**10:010d}US" if status != "processing" else ""
        return SupplierOrder(order_id=order_id, status=status, raw_status=status.upper(), tracking_number=tracking,
                             tracking_url=f"https://t.17track.net/en#nums={tracking}" if tracking else "",
                             carrier="CJPacket" if tracking else "")


def make_supplier(cfg) -> CJSupplier | DemoSupplier:
    if cfg.supplier.name == "cj":
        return CJSupplier(cfg.supplier.cj_api_key, cfg.supplier.cj_base_url,
                          str(Path(cfg.db_path).with_suffix(".cj_token.json")), cfg.fulfillment.sandbox)
    if cfg.supplier.name == "demo":
        return DemoSupplier()
    raise SupplierError(f"unknown supplier {cfg.supplier.name!r}")
