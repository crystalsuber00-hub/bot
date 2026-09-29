"""The research loop: Claude proposes, the backtester judges.

Phases run in one conversation (research -> hypotheses -> backtest/iterate -> attack -> memo).
Every tool call is logged, and the final report is written from the log, not from Claude's
own claims.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import anthropic

from .tools import TOOLS, Toolbox
from .validate import Criteria

log = logging.getLogger("quantbot")

MODEL = "claude-opus-5-5"
# $ per million tokens (input, output, cache write, cache read) for the cost line in the report
PRICES = {"claude-opus-5-5": (4.0, 20.0, 5.0, 0.20)}


def system_prompt(tb: Toolbox, c: Criteria) -> str:
    return f"""You are a quantitative researcher. Goal: find a US equity strategy worth deploying, and prove it works before claiming it does.

How this works:
- You propose; an independent validator judges. You cannot declare a strategy successful. Only a PASS from submit_for_validation counts, and a PASS is still only a candidate for a human to review.
- Exploration tools (sector_scan, asset_stats, run_backtest) see prices up to the research cutoff {tb.research.dates[-1]}. The validator also sees the held-out period after it (up to {tb.full.dates[-1]}), so you can't tune on the data that judges you.
- Budgets: {tb.max_backtests} exploratory backtests, {tb.max_submissions} validation submissions. Every submission is logged in a rejection log that the human will read; a long log of honest rejections is expected and fine.

Acceptance criteria, fixed before research started:
- Beat SPY after costs over the trailing {c.trailing_days} trading days
- Still beat SPY with costs multiplied by {c.cost_stress_mult:g}
- Max drawdown under {c.max_drawdown:.0%}
- Sharpe above {c.min_sharpe}
- No single stock more than {c.max_top_contributor_share:.0%} of returns
- Deterministic (identical on two runs)
{"- Also beat SPY over the trailing " + str(c.long_window_days) + " trading days, with drawdown under the limit" if c.require_long_window_beat else ""}
{"- Still beat SPY with the top-contributing stock removed" if c.require_without_top_name else ""}
{"- Still beat SPY when the rebalance day is shifted (timing luck check)" if c.require_rebalance_offset else ""}

Known limits you should account for, not hide: prices only (no fundamentals, so quality is proxied from price behavior); the universe is today's large caps (survivorship bias); long-only, no leverage; costs are modeled, not measured.

Only cite numbers that came from tool results. If nothing passes, say so plainly; that is a valid result."""


PHASES = [
    ("research",
     "Step 1 - research only. Analyze the US sectors over the last 3-6 months. Find sectors showing unusual relative "
     "strength, momentum, or structural catalysts you can see in the data. Do NOT propose a trading strategy yet. "
     "Rank the opportunities and explain the evidence, including what would make you wrong."),
    ("hypotheses",
     "Step 2 - turn the thesis into something testable. Pick the most promising sector (or two), list the tradable names, "
     "and design {n} fundamentally different strategies. For each give the full spec (universe, factors and weights, "
     "top_n, weighting, rebalance, band, costs) and why the edge might actually exist. Don't say which is best - the "
     "backtest will answer that. Don't run backtests yet."),
    ("backtest",
     "Step 3 - code and backtest everything. Test each hypothesis with run_backtest, diagnose failures (costs, "
     "concentration, recent-period breakdown, drawdown), and improve. Submit to the validator only specs that you "
     "expect to pass on reasoning, not on luck. Keep a short rejection log as you go: version, change, result, "
     "and why it was rejected. Stop when something passes or your budgets run out."),
    ("attack",
     "Step 4 - assume every PASSED candidate is overfit. Your job is to prove it is bad. Use stress_test (start with "
     "standard_suite, then remove its best names, raise costs, try bear and rising-rate periods, shift parameters) "
     "and report every condition under which it fails. If nothing passed, say so and skip this step."),
    ("memo",
     "Step 5 - write the final research memo: the thesis, every version tried with the reason it was rejected, the "
     "survivor (if any) with its validator numbers and the attacks it did and didn't survive, and what a human should "
     "check before deploying anything. Cite only numbers from tool results."),
]


@dataclass
class Usage:
    input: int = 0
    output: int = 0
    cache_write: int = 0
    cache_read: int = 0
    calls: int = 0

    def add(self, u) -> None:
        self.calls += 1
        self.input += u.input_tokens or 0
        self.output += u.output_tokens or 0
        self.cache_write += getattr(u, "cache_creation_input_tokens", 0) or 0
        self.cache_read += getattr(u, "cache_read_input_tokens", 0) or 0

    def cost(self, model: str) -> float | None:
        p = PRICES.get(model)
        if not p:
            return None
        return (self.input * p[0] + self.output * p[1] + self.cache_write * p[2] + self.cache_read * p[3]) / 1e6


@dataclass
class Run:
    toolbox: Toolbox
    model: str = MODEL
    effort: str = "high"
    max_turns_per_phase: int = 40
    n_hypotheses: int = 4
    out_dir: Path = field(default_factory=lambda: Path("runs") / datetime.now().strftime("%Y%m%d-%H%M%S"))
    client: object = None
    messages: list = field(default_factory=list)
    transcript: list = field(default_factory=list)   # (phase, text) Claude wrote
    usage: Usage = field(default_factory=Usage)
    stopped: str = ""

    def _create(self, system: str):
        return self.client.beta.messages.create(
            model=self.model,
            max_tokens=16000,
            system=system,
            tools=TOOLS,
            messages=self.messages,
            output_config={"effort": self.effort},
            cache_control={"type": "ephemeral"},
            # on a safety-classifier decline, the API retries on Anthropic's recommended fallback model
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )

    def run_phase(self, phase: str, prompt: str, system: str) -> None:
        self.toolbox.phase = phase
        self.messages.append({"role": "user", "content": prompt})
        for _ in range(self.max_turns_per_phase):
            resp = self._create(system)
            self.usage.add(resp.usage)
            self.messages.append({"role": "assistant", "content": resp.content})
            text = "\n".join(b.text for b in resp.content if b.type == "text").strip()
            if text:
                self.transcript.append((phase, text))
                log.info("[%s] %s", phase, text[:400].replace("\n", " "))
            if resp.stop_reason == "refusal":
                self.stopped = f"model declined in {phase} phase"
                return
            if resp.stop_reason == "max_tokens":
                self.messages.append({"role": "user", "content": "Continue."})
                continue
            if resp.stop_reason != "tool_use":
                return
            results = []
            for b in resp.content:
                if b.type == "tool_use":
                    out, err = self.toolbox.call(b.name, b.input)
                    log.info("  tool %s -> %s", b.name, out[:160])
                    results.append({"type": "tool_result", "tool_use_id": b.id, "content": out, "is_error": err})
            self.messages.append({"role": "user", "content": results})
        self.stopped = f"turn limit reached in {phase} phase"
        # close the dangling tool results so the conversation stays valid for the next phase
        self.messages.append({"role": "user", "content": f"Turn limit for the {phase} phase reached; wrap up this phase now."})
        resp = self._create(system)
        self.usage.add(resp.usage)
        self.messages.append({"role": "assistant", "content": resp.content})

    def execute(self, phases: list[tuple[str, str]] | None = None) -> Path:
        self.client = self.client or anthropic.Anthropic()
        system = system_prompt(self.toolbox, self.toolbox.criteria)
        for phase, prompt in phases or PHASES:
            if self.stopped.startswith("model declined"):
                break
            if phase == "attack" and not self.passed():
                continue
            self.run_phase(phase, prompt.format(n=self.n_hypotheses), system)
        return self.write()

    def passed(self) -> dict:
        return {k: v for k, v in self.toolbox.candidates.items() if v["result"]["verdict"] == "PASS"}

    def write(self) -> Path:
        from .report import render
        self.out_dir.mkdir(parents=True, exist_ok=True)
        (self.out_dir / "tool_log.jsonl").write_text(
            "\n".join(json.dumps(e, default=str) for e in self.toolbox.log) + "\n")
        for cid, c in self.passed().items():
            (self.out_dir / f"candidate_{cid}.json").write_text(json.dumps(c["spec"], indent=2))
        report = self.out_dir / "report.md"
        report.write_text(render(self))
        return report
