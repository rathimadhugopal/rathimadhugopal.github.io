import datetime as dt
from zoneinfo import ZoneInfo

import build_board as b

NOW = dt.datetime(2026, 10, 23, 8, 15, tzinfo=ZoneInfo("Asia/Kolkata"))


def test_severe_news_flags_and_dominates():
    s, bad, good = b.score_headline("Yes Bank gains 3% despite Supreme Court verdict against it")
    assert "Court case" in bad and good == [] and s <= -2


def test_good_news_scores_positive():
    s, bad, good = b.score_headline("Titan Q2 profit rises 20%, brokerage raises target")
    assert s >= 2 and bad == []


def test_lean_is_capped_down_when_flagged():
    news = {"score": 0.5, "tone": "positive", "flags": ["Data breach"], "count": 3}
    tech = {"last": 100, "sma20": 95, "sma50": 90, "change_pct": 2.0, "rsi": 55}
    lean = b.lean_for(tech, news, {"^GSPC": 1.0})
    assert lean["label"] == "Leaning down"


def test_no_news_sideways_is_unclear():
    news = {"score": 0.0, "tone": "quiet", "flags": [], "count": 0}
    tech = {"last": 100, "sma20": 101, "sma50": 99, "change_pct": 0.2, "rsi": 50}
    assert b.lean_for(tech, news, {})["label"] == "No clear lean"


def test_technicals_and_render_escape():
    hist = [(NOW.date() - dt.timedelta(days=60 - i), 100 + i * 0.5) for i in range(60)]
    t = b.technicals(hist)
    assert t["last"] > t["sma20"] > t["sma50"] and len(t["spark"]) == 15
    fp, fn = b.demo_fetchers(NOW)
    cfg = {"stocks": [{"symbol": "X", "name": "<script>alert(1)</script>", "search": "x"}], "market_mood": [], "commodities": []}
    data = b.build(cfg, NOW, fp, fn, log=lambda m: None)
    page = b.render(data, demo=True)
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;" in page


def test_fetch_failure_does_not_crash():
    def boom(*a, **k):
        raise RuntimeError("offline")
    cfg = {"stocks": [{"symbol": "SBIN", "name": "SBI", "search": "sbi"}], "market_mood": [{"ticker": "^NSEI", "name": "Nifty"}], "commodities": []}
    data = b.build(cfg, NOW, boom, boom, log=lambda m: None)
    html = b.render(data)
    assert data["stocks"][0]["tech"] is None and "price unavailable" in html and "unavailable" in html


def test_probability_is_capped_and_consistent():
    news = {"score": -1.0, "tone": "negative", "flags": ["Court case"], "count": 3}
    tech = {"last": 100, "sma20": 105, "sma50": 110, "change_pct": -3, "rsi": 40}
    lean = b.lean_for(tech, news, {"^GSPC": -1})
    assert 0.25 <= lean["p_up"] <= 0.75 and lean["p_up"] < 0.5
    calm = b.lean_for({"last": 100, "sma20": 101, "sma50": 99, "change_pct": 0.1, "rsi": 50}, {"score": 0, "tone": "quiet", "flags": [], "count": 0}, {})
    assert abs(calm["p_up"] - 0.5) < 0.05


def test_option_check_flags_lottery_and_counts_sessions():
    today = dt.date(2026, 10, 23)  # Friday; expiry Tuesday 27th -> Fri, Mon, Tue = 3
    stock = {"tech": {"last": 21.0, "day_vol_pct": 1.5}, "lean": {"p_up": 0.3}, "news": {"flags": ["Court case"]}}
    o = b.option_check({"symbol": "YESBANK", "strike": 24, "type": "CE", "expiry": "2026-10-27"}, stock, today)
    assert o["sessions"] == 3 and o["verdict"] == "Lottery ticket"
    near = b.option_check({"symbol": "T", "strike": 4400, "type": "PE", "expiry": "2026-11-24"},
                          {"tech": {"last": 4423.0, "day_vol_pct": 1.2}, "lean": {"p_up": 0.35}, "news": {"flags": []}}, today)
    assert near["verdict"] == "Reasonable"
