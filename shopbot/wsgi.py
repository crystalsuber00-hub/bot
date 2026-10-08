"""Entry point for production WSGI servers, e.g. `gunicorn -w 2 -b 0.0.0.0:8000 shopbot.wsgi:app`.
Run `shopbot worker` alongside it for the automation."""
import logging
import os

from .config import load
from .shop import Shop
from .web import App

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
app = App(Shop(load(os.environ.get("SHOPBOT_CONFIG", "config.shop.toml"))))
