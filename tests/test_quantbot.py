from types import SimpleNamespace

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("anthropic")

from quantbot import backtest as bt
from quantbot import factors as F
from quantbot.agent import Run
from quantbot.data import Panel
from quantbot.tools import Toolbox
from quantbot.validate import Criteria, validate

NAMES = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"]


def panel(n=900, seed=0, drifts=(0.0015, 0.0012, 0.0004, 0.0002, -0.0002, 0.0)):
    rng = np.random.default_rng(seed)
    dates = np.arange(np.datetime64("2022-01-03"), np.datetime64("2022-01-03") + n * 2, dtype="datetime64[D]")
    dates = dates[np.is_busday(dates)][:n]
    spy = 100 * np.cumprod(1 + 0.0004 + 0.01 * rng.standard_normal(n))
    cols = [spy]
    for d in drifts:
        cols.append(50 * np.cumprod(1 + d + 0.015 * rng.standard_normal(n)))
    return Panel(dates, ["SPY", *NAMES], np.column_stack(cols))


def spec(**kw):
    d = {"name": "t", "universe": NAMES, "factors": {"mom_6_1": 1.0}, "top_n": 2, "max_weight": 0.5,
         "rebalance": "monthly"}
    d.update(kw)
    return bt.Spec.from_dict(d)


def test_single_asset_tracks_price_after_first_trade():
    p = panel()
    s = bt.Spec.from_dict({"name": "spy", "universe": ["SPY"], "factors": {"mom_3m": 1}, "top_n": 1,
                           "max_weight": 1.0, "rebalance": "daily", "commission_bps": 0, "slippage_bps": 0})
    start = str(p.dates[400])
    r = bt.run(s, p, start=start)
    spy = p.col("SPY")
    # decided at close 399, traded at close 400, earns from 401 on
    assert r["total_return"] == pytest.approx(spy[-1] / spy[400] - 1, abs=1e-4)


def test_no_lookahead():
    p = panel()
    base = bt.run(spec(), p, start=str(p.dates[300]), end=str(p.dates[600]))
    q = Panel(p.dates, p.symbols, p.px.copy())
    q.px[602:, 1:] *= np.linspace(1, 5, len(q.px) - 602)[:, None] * np.arange(1, 7)  # rewrite the future
    after = bt.run(spec(), q, start=str(p.dates[300]), end=str(p.dates[600]))
    assert base["equity_hash"] == after["equity_hash"]


def test_costs_and_determinism():
    p = panel()
    a = bt.run(spec(rebalance="weekly"), p, start=str(p.dates[400]))
    b = bt.run(spec(rebalance="weekly"), p, start=str(p.dates[400]), cost_mult=3)
    assert a["equity_hash"] == bt.run(spec(rebalance="weekly"), p, start=str(p.dates[400]))["equity_hash"]
    assert b["total_return"] < a["total_return"]
    assert a["commissions_usd"] > 0


def test_band_reduces_turnover():
    p = panel()
    no_band = bt.run(spec(rebalance="daily"), p, start=str(p.dates[400]))
    band = bt.run(spec(rebalance="daily", band=2), p, start=str(p.dates[400]))
    assert band["annual_turnover"] < no_band["annual_turnover"]


def test_weight_cap_leaves_cash():
    w = bt._cap_weights(np.array([1.0, 0, 0]), 0.25)
    assert w.max() == pytest.approx(0.25) and w.sum() == pytest.approx(0.25)


def test_cs_rank():
    x = np.array([[3.0, 1.0, np.nan, 2.0]])
    assert F.cs_rank(x)[0].tolist()[:2] == [1.0, pytest.approx(1 / 3)] and np.isnan(F.cs_rank(x)[0, 2])


def test_spec_validation():
    with pytest.raises(ValueError):
        spec(factors={"made_up": 1})
    with pytest.raises(ValueError):
        spec(top_n=99)
    assert spec(factors=[{"name": "low_beta", "weight": 1}]).factors == {"low_beta": 1.0}


def test_validator_rejects_loser_and_reports_every_check():
    p = panel(drifts=(-0.001, -0.001, -0.001, -0.001, -0.001, -0.001))
    res = validate(spec(), p, Criteria(long_window_days=500))
    assert res["verdict"] == "REJECTED"
    assert "beats_spy_after_costs_trailing_1y" in res["failed"]
    assert {c["check"] for c in res["checks"]} >= {"deterministic", "max_drawdown_under_limit", "sharpe_above_min"}


# --- the Claude loop, with a scripted fake client --------------------------------------------
def block(kind, **kw):
    return SimpleNamespace(type=kind, **kw)


def reply(content, stop):
    usage = SimpleNamespace(input_tokens=10, output_tokens=5, cache_creation_input_tokens=0, cache_read_input_tokens=0)
    return SimpleNamespace(content=content, stop_reason=stop, usage=usage)


class FakeClient:
    def __init__(self, script):
        self.script = list(script)
        self.requests = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kw):
        self.requests.append(kw)
        return self.script.pop(0) if self.script else reply([block("text", text="done")], "end_turn")


def test_agent_loop_gates_tools_and_writes_report(tmp_path):
    p = panel()
    tb = Toolbox(p, p.slice(end=p.dates[-64]), Criteria(long_window_days=500), max_submissions=2)
    good = {"name": "mom", "hypothesis": "h", "universe": NAMES,
            "factors": [{"name": "mom_6_1", "weight": 1}], "top_n": 2, "max_weight": 0.5}
    script = [
        # research phase: tries to backtest too early -> gated, then scans
        reply([block("tool_use", id="1", name="run_backtest", input={"spec": good}),
               block("tool_use", id="2", name="sector_scan", input={})], "tool_use"),
        reply([block("text", text="Energy leads.")], "end_turn"),
        reply([block("text", text="Four hypotheses...")], "end_turn"),          # hypotheses
        reply([block("tool_use", id="3", name="submit_for_validation", input={"spec": good})], "tool_use"),
        reply([block("text", text="Rejection log ...")], "end_turn"),           # backtest
    ]
    client = FakeClient(script)
    run = Run(tb, client=client, out_dir=tmp_path / "run")
    report = run.execute()
    # messages[0] prompt, [1] assistant tool calls, [2] their results
    first_tool_results = run.messages[2]["content"]
    assert first_tool_results[0]["is_error"] and "not available" in first_tool_results[0]["content"]
    assert not first_tool_results[1]["is_error"]
    assert tb.submissions == 1 and "v1" in tb.candidates
    text = report.read_text()
    assert "Rejection log" in text and "| v1 | mom |" in text
    assert (tmp_path / "run" / "tool_log.jsonl").exists()
    req = client.requests[0]
    assert req["model"] == "claude-opus-5-5" and req["fallbacks"] == "default"
    assert "thinking" not in req  # Opus 5.5: thinking is always on; effort is the control


def test_xstrategy_script_matches_builtin_factor(tmp_path):
    script = tmp_path / "s.py"
    script.write_text(
        "from xstrategy import *\n"
        "from xstrategy.factors_library.momentum import jkp_ret_6_1\n"
        "class Strategy(XStrategy):\n"
        "    def alpha(self, d):\n"
        "        return combine(cs_rank(jkp_ret_6_1(d)), weights=[1.0])\n")
    p = panel()
    builtin = bt.run(spec(), p, start=str(p.dates[400]))
    scripted = bt.run(spec(factors=None, alpha_script=str(script)), p, start=str(p.dates[400]))
    assert scripted["equity_hash"] == builtin["equity_hash"]


def test_article_strategy_file_runs():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    s = spec(universe=["XOM", "CVX", "DNN", "AAA"], alpha_script=str(root / "strategies" / "article_energy.py"))
    p = Panel(panel().dates, ["SPY", "XOM", "CVX", "DNN", "AAA"], panel().px[:, :5])
    r = bt.run(s, p, start=str(p.dates[400]))
    assert "AAA" not in dict(r["contribution_pct_by_name"])  # not in the article's eligible list
