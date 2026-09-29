"""Render a day's logged spread mid price as a self-contained HTML/SVG chart."""
from __future__ import annotations

import csv
from pathlib import Path

from .config import Config
from .state import State

W, H, PAD_L, PAD_R, PAD_T, PAD_B = 900, 420, 60, 20, 20, 40


def make_chart(cfg: Config, state: State, date: str | None = None) -> str:
    hist = Path(cfg.history_dir)
    if date is None:
        files = sorted(hist.glob("*.csv"))
        if not files:
            raise SystemExit(f"no history in {hist}/ yet")
        date = files[-1].stem
    path = hist / f"{date}.csv"
    if not path.exists():
        raise SystemExit(f"no history for {date}")
    rows = list(csv.DictReader(path.open()))
    pos = state.position_for(date)
    credit = pos.credit if pos else None
    target = credit * (1 - cfg.strategy.profit_target) if credit else None

    ys = [float(r["spread_mid"]) for r in rows] + [v for v in (credit, target) if v is not None]
    lo, hi = min(ys + [0]), max(ys) * 1.1 or 1
    n = max(len(rows) - 1, 1)
    x = lambda i: PAD_L + (W - PAD_L - PAD_R) * i / n
    y = lambda v: PAD_T + (H - PAD_T - PAD_B) * (1 - (v - lo) / (hi - lo))

    line = " ".join(f"{x(i):.1f},{y(float(r['spread_mid'])):.1f}" for i, r in enumerate(rows))
    grid = "".join(
        f'<line x1="{PAD_L}" x2="{W-PAD_R}" y1="{y(v):.1f}" y2="{y(v):.1f}" class="grid"/>'
        f'<text x="{PAD_L-8}" y="{y(v)+4:.1f}" text-anchor="end">{v:.2f}</text>'
        for v in [lo + (hi - lo) * k / 5 for k in range(6)])
    step = max(len(rows) // 6, 1)
    ticks = "".join(f'<text x="{x(i):.1f}" y="{H-PAD_B+18}" text-anchor="middle">{rows[i]["time"][:5]}</text>'
                    for i in range(0, len(rows), step))
    marks = ""
    for v, label, cls in ((credit, f"entry credit {credit}", "credit"), (target, f"exit target {target:.2f}" if target else "", "target")):
        if v is not None:
            marks += (f'<line x1="{PAD_L}" x2="{W-PAD_R}" y1="{y(v):.1f}" y2="{y(v):.1f}" class="{cls}"/>'
                      f'<text x="{W-PAD_R-4}" y="{y(v)-5:.1f}" text-anchor="end" class="{cls}t">{label}</text>')

    desc = (f"{pos.side.replace('_', ' ')} {pos.short_strike:g}/{pos.long_strike:g}, credit {pos.credit:.2f}"
            + (f", closed at {pos.exit_debit:.2f} ({pos.exit_reason})" if pos.status == "closed" else "")) if pos else ""
    html = f"""<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Spread {date}</title>
<style>
:root{{--bg:#fff;--fg:#1a1a1a;--grid:#e3e3e3;--line:#2563eb;--credit:#6b7280;--target:#16a34a}}
@media(prefers-color-scheme:dark){{:root{{--bg:#111;--fg:#e5e5e5;--grid:#333;--line:#60a5fa;--credit:#9ca3af;--target:#4ade80}}}}
body{{background:var(--bg);color:var(--fg);font:14px system-ui;margin:16px}}svg{{width:100%;max-width:{W}px;height:auto}}
text{{fill:var(--fg);font-size:12px}}.grid{{stroke:var(--grid)}}.credit{{stroke:var(--credit);stroke-dasharray:6 4}}
.target{{stroke:var(--target);stroke-dasharray:6 4}}.creditt{{fill:var(--credit)}}.targett{{fill:var(--target)}}
polyline{{fill:none;stroke:var(--line);stroke-width:2}}</style>
<h2>Spread mid price, {date}</h2><p>{desc}<br>Lower = more profit. Exit when the line reaches the green target.</p>
<svg viewBox="0 0 {W} {H}" role="img" aria-label="Spread mid price over time">{grid}{ticks}{marks}<polyline points="{line}"/></svg>"""
    out = hist / f"{date}.html"
    out.write_text(html)
    return str(out)
