from __future__ import annotations

import math

from .config import Pricing


def round_99(x: float) -> float:
    return round(math.ceil(x) - 0.01, 2)


def profit(price: float, cost: float, ship: float, p: Pricing) -> float:
    return round(price * (1 - p.card_fee_pct) - p.card_fee_fixed - cost - ship, 2)


def multiplier(landed: float, p: Pricing) -> float:
    for max_cost, mult in sorted(p.markup_tiers):
        if landed <= max_cost:
            return float(mult)
    return float(sorted(p.markup_tiers)[-1][1]) if p.markup_tiers else 2.0


def retail(cost: float, ship: float, p: Pricing) -> tuple[float, float]:
    """(price, compare_at) for one unit with free shipping to the customer.

    Price is the larger of the tiered markup price, the store minimum, and whatever keeps min_profit after card fees,
    as a price ending in .99.
    """
    floor_for_profit = (cost + ship + p.card_fee_fixed + p.min_profit) / (1 - p.card_fee_pct)
    price = round_99(max((cost + ship) * multiplier(cost + ship, p), p.min_price, floor_for_profit))
    compare_at = round_99(price * p.compare_at_markup) if p.compare_at_markup > 1 else 0.0
    return price, compare_at
