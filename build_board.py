#!/usr/bin/env python3
"""Morning Board: one page with overnight news and a lean for each stock you follow.

    python build_board.py                      # fetch live data, write docs/index.html
    python build_board.py --demo               # canned data, clearly labelled, for a preview
    python build_board.py --config my.json --out board.html

Data: Yahoo Finance (daily prices) and Google News RSS (headlines). No API keys.
Everything is fetched fresh on each run; nothing is stored between runs.
The "lean" is a weighted sum of news tone, trend and overnight cues. It is a hint, not a forecast.
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import math
import os
import re
import sys
import urllib.parse
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

import httpx

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) MorningBoard/1.0"}
TIMEOUT = 20

# ------------------------------------------------------------------ fetchers
def yahoo_history(ticker: str) -> list[tuple[dt.date, float]]:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(ticker)}?range=3mo&interval=1d"
    r = httpx.get(url, headers=UA, timeout=TIMEOUT, follow_redirects=True)
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    stamps = res.get("timestamp") or []
    closes = res["indicators"]["quote"][0].get("close") or []
    out = []
    for t, c in zip(stamps, closes):
        if c is not None:
            out.append((dt.datetime.fromtimestamp(t, tz=dt.timezone.utc).date(), float(c)))
    if not out:
        raise ValueError("no price data")
    return out


def google_news(query: str, window: str, limit: int) -> list[dict]:
    q = urllib.parse.quote_plus(f"{query} {window}".strip())
    url = f"https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en"
    r = httpx.get(url, headers=UA, timeout=TIMEOUT, follow_redirects=True)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    items, seen = [], set()
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        src = it.find("source")
        source = (src.text or "").strip() if src is not None else ""
        # Google appends " - Source" to titles; drop it when we know the source
        if source and title.endswith(" - " + source):
            title = title[: -len(source) - 3].strip()
        key = re.sub(r"\W+", "", title.lower())[:60]
        if not title or key in seen:
            continue
        seen.add(key)
        pub = it.findtext("pubDate") or ""
        try:
            when = dt.datetime.strptime(pub, "%a, %d %b %Y %H:%M:%S %Z").replace(tzinfo=dt.timezone.utc)
        except ValueError:
            when = None
        link = it.findtext("link") or ""
        items.append({"title": title, "source": source, "link": link if link.startswith("https://") else "", "when": when})
        if len(items) >= limit:
            break
    return items


# ------------------------------------------------------------------ scoring
BAD = {
    "Court case": (["supreme court", "high court", "apex court", "tribunal", "lawsuit", "nclt", "court order", "sc ruling", "verdict against", "court rules against"], 2),
    "Data breach": (["data breach", "breach", "hacked", "cyber attack", "cyberattack", "data leak", "ransomware"], 2),
    "Regulator action": (["rbi penalty", "rbi imposes", "rbi bars", "sebi order", "sebi bars", "penalty", "show cause", "banned", "restricts"], 2),
    "Probe": (["probe", "investigation", "ed raid", "ed searches", "cbi", "fraud", "scam", "money laundering"], 2),
    "Weak results": (["misses estimates", "profit falls", "profit drops", "profit declines", "loss widens", "npa rises", "slippages", "margin pressure"], 1),
    "Downgrade": (["downgrade", "downgrades", "cuts target", "sell rating", "underperform", "reduce rating"], 1),
    "Selling pressure": (["plunge", "plunges", "tumbles", "slumps", "crashes", "falls", "fall ", "slides", "slips", "slip ", "drops", "sinks", "52-week low", "hits low"], 1),
    "Leadership exit": (["resigns", "steps down", "quits", "exit of"], 1),
    "Stake sale": (["offloads stake", "sells stake", "block deal", "promoter sells", "dumps"], 1),
}
GOOD = {
    "Strong results": (["beats estimates", "profit rises", "profit jumps", "profit surges", "record profit", "strong quarter", "net profit up"], 1),
    "Upgrade": (["upgrade", "upgrades", "raises target", "buy rating", "outperform", "overweight"], 1),
    "Deal or investment": (["acquires", "acquisition", "picks up stake", "stake purchase", "buys stake", "raises stake", "hikes stake", "to invest", "order win", "wins order", "bags order", "partnership"], 1),
    "Approval": (["approval", "approves", "gets nod", "cleared", "green light"], 1),
    "Shareholder reward": (["dividend", "buyback", "bonus issue"], 1),
    "Buying momentum": (["surge", "surges", "rallies", "rally", "jumps", "soars", "gains", "record high", "52-week high", "hits high"], 1),
}
SEVERE = {"Court case", "Data breach", "Regulator action", "Probe"}


def score_headline(title: str) -> tuple[int, list[str], list[str]]:
    t = " " + re.sub(r"\s+", " ", title.lower()) + " "
    score, bad_tags, good_tags = 0, [], []
    for tag, (words, w) in BAD.items():
        if any(k in t for k in words):
            score -= w
            bad_tags.append(tag)
    for tag, (words, w) in GOOD.items():
        if any(k in t for k in words):
            score += w
            good_tags.append(tag)
    # "falls after court ruling" is bad, not mixed: severe tags dominate
    if set(bad_tags) & SEVERE:
        good_tags = []
        score = min(score, -2)
    return max(-3, min(3, score)), bad_tags, good_tags


def news_summary(items: list[dict], now: dt.datetime) -> dict:
    total, weight_sum = 0.0, 0.0
    flags: list[str] = []
    for it in items:
        s, bad, good = score_headline(it["title"])
        it["score"], it["bad_tags"], it["good_tags"] = s, bad, good
        age_h = (now - it["when"]).total_seconds() / 3600 if it["when"] else 36
        w = 1.5 if age_h <= 24 else 1.0
        total += s * w
        weight_sum += w
        for b in bad:
            if b in SEVERE and b not in flags:
                flags.append(b)
    avg = total / weight_sum if weight_sum else 0.0
    tone = "negative" if avg <= -0.5 else "positive" if avg >= 0.5 else "mixed" if items else "quiet"
    return {"score": round(avg, 2), "tone": tone, "flags": flags, "count": len(items)}


def technicals(hist: list[tuple[dt.date, float]]) -> dict:
    closes = [c for _, c in hist]
    last, prev = closes[-1], closes[-2] if len(closes) > 1 else closes[-1]
    sma = lambda n: sum(closes[-n:]) / min(n, len(closes))
    rsi = None
    if len(closes) >= 15:
        gains = [max(closes[i] - closes[i - 1], 0) for i in range(-14, 0)]
        losses = [max(closes[i - 1] - closes[i], 0) for i in range(-14, 0)]
        ag, al = sum(gains) / 14, sum(losses) / 14
        rsi = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    rets = [closes[i] / closes[i - 1] - 1 for i in range(max(1, len(closes) - 20), len(closes))]
    mean = sum(rets) / len(rets) if rets else 0.0
    vol = math.sqrt(sum((r - mean) ** 2 for r in rets) / len(rets)) if len(rets) > 1 else 0.01
    return {
        "last": last, "prev": prev, "date": hist[-1][0].isoformat(),
        "day_vol_pct": vol * 100,
        "range_lo": last * (1 - vol), "range_hi": last * (1 + vol),
        "change_pct": (last / prev - 1) * 100,
        "change_5d_pct": (last / closes[-6] - 1) * 100 if len(closes) > 5 else 0.0,
        "sma20": sma(20), "sma50": sma(50), "rsi": rsi,
        "high_3m": max(closes), "low_3m": min(closes),
        "spark": closes[-15:],
    }


def lean_for(tech: dict | None, news: dict, mood: dict) -> dict:
    """Weighted hint for today. Deliberately simple and fully explained."""
    score, why = 0.0, []
    if news["count"]:
        score += news["score"] * 2
        why.append(f"headlines are {news['tone']} ({news['score']:+.1f})")
    if news["flags"]:
        why.append("serious negative news: " + ", ".join(news["flags"]).lower())
    if tech:
        if tech["last"] > tech["sma20"] > tech["sma50"]:
            score += 1; why.append("price above its 20- and 50-day averages (uptrend)")
        elif tech["last"] < tech["sma20"] < tech["sma50"]:
            score -= 1; why.append("price below its 20- and 50-day averages (downtrend)")
        else:
            why.append("trend is sideways")
        if tech["change_pct"] >= 1.5:
            score += 0.5; why.append(f"closed {tech['change_pct']:+.1f}% yesterday (momentum)")
        elif tech["change_pct"] <= -1.5:
            score -= 0.5; why.append(f"closed {tech['change_pct']:+.1f}% yesterday (pressure)")
        if tech["rsi"] is not None:
            if tech["rsi"] >= 72:
                score -= 0.5; why.append(f"RSI {tech['rsi']:.0f}: stretched, pullback risk")
            elif tech["rsi"] <= 28:
                score += 0.5; why.append(f"RSI {tech['rsi']:.0f}: oversold, bounce possible")
    us = mood.get("^GSPC")
    if us is not None:
        if us >= 0.6:
            score += 0.5; why.append(f"US market closed {us:+.1f}% overnight")
        elif us <= -0.6:
            score -= 0.5; why.append(f"US market closed {us:+.1f}% overnight")
    if news["flags"]:
        score = min(score, -1.5)
    label = "Leaning up" if score >= 1.5 else "Leaning down" if score <= -1.5 else "No clear lean"
    conf = "high" if abs(score) >= 3 else "medium" if abs(score) >= 1.5 else "low"
    # logistic map of the score to a probability, capped at 25-75%: a heuristic cannot justify more
    p_up = 1 / (1 + math.exp(-0.6 * score))
    p_up = max(0.25, min(0.75, p_up))
    return {"label": label, "score": round(score, 1), "confidence": conf, "reasons": why, "p_up": round(p_up, 2)}


# ------------------------------------------------------------------ options
def sessions_left(today: dt.date, expiry: dt.date) -> int:
    d, n = today, 0
    while d <= expiry:
        if d.weekday() < 5:
            n += 1
        d += dt.timedelta(days=1)
    return n


def option_check(opt: dict, stock: dict | None, today: dt.date) -> dict:
    """Structural health of a held option. Premium is not available for free; check it in Kite."""
    out = {"symbol": opt["symbol"], "strike": float(opt["strike"]), "type": opt["type"].upper(), "expiry": opt["expiry"]}
    expiry = dt.date.fromisoformat(opt["expiry"])
    out["sessions"] = sessions_left(today, expiry)
    tech = stock["tech"] if stock else None
    if not tech:
        out["verdict"], out["notes"] = "No price", ["price unavailable"]
        return out
    last, vol = tech["last"], max(tech["day_vol_pct"], 0.05)
    dist_pct = (out["strike"] - last) / last * 100 if out["type"] == "CE" else (last - out["strike"]) / last * 100
    ranges = dist_pct / vol  # distance to strike in "usual day ranges"
    out["last"], out["dist_pct"], out["ranges"] = last, dist_pct, ranges
    notes = []
    if dist_pct <= 0:
        notes.append("in the money")
    else:
        notes.append(f"{dist_pct:.1f}% away = {ranges:.1f} usual days of movement")
    helps = (stock["lean"]["p_up"] >= 0.55) if out["type"] == "CE" else (stock["lean"]["p_up"] <= 0.45)
    hurts = (stock["lean"]["p_up"] <= 0.45) if out["type"] == "CE" else (stock["lean"]["p_up"] >= 0.55)
    if stock["news"]["flags"] and out["type"] == "CE":
        notes.append("serious negative news against a call")
    if out["sessions"] <= 2:
        notes.append(f"{out['sessions']} session(s) left: decay is fastest now")
    elif out["sessions"] <= 5:
        notes.append(f"{out['sessions']} sessions left")
    if dist_pct <= 0:
        verdict = "Watch" if (hurts or (stock["news"]["flags"] and out["type"] == "CE")) else "Healthy"
    elif ranges > out["sessions"] * 1.0 or ranges > 6:
        verdict = "Lottery ticket"
    elif hurts or ranges > out["sessions"] * 0.5:
        verdict = "Weak"
    elif helps:
        verdict = "Reasonable"
    else:
        verdict = "Watch"
    out["verdict"], out["notes"] = verdict, notes
    return out


# ------------------------------------------------------------------ build
def build(config: dict, now: dt.datetime, fetch_prices, fetch_news, log=print) -> dict:
    tz = now.tzinfo
    mood, mood_rows = {}, []
    for m in config.get("market_mood", []):
        try:
            t = technicals(fetch_prices(m["ticker"]))
            mood[m["ticker"]] = t["change_pct"]
            mood_rows.append({"name": m["name"], "last": t["last"], "change_pct": t["change_pct"], "date": t["date"]})
        except Exception as e:
            log(f"mood {m['ticker']}: {e}")
            mood_rows.append({"name": m["name"], "error": str(e)})

    stocks = []
    for s in config["stocks"]:
        entry = {"symbol": s["symbol"], "name": s["name"]}
        try:
            entry["tech"] = technicals(fetch_prices(s.get("ticker") or f"{s['symbol']}.NS"))
        except Exception as e:
            log(f"price {s['symbol']}: {e}"); entry["tech"] = None; entry["price_error"] = str(e)
        try:
            items = fetch_news(s["search"], config.get("news_window", "when:2d"), config.get("headlines_per_item", 5))
        except Exception as e:
            log(f"news {s['symbol']}: {e}"); items = []; entry["news_error"] = str(e)
        entry["news"] = news_summary(items, now)
        entry["headlines"] = items
        entry["lean"] = lean_for(entry["tech"], entry["news"], mood)
        stocks.append(entry)

    commodities = []
    for c in config.get("commodities", []):
        entry = {"name": c["name"], "unit": c.get("unit", "")}
        try:
            entry["tech"] = technicals(fetch_prices(c["ticker"]))
        except Exception as e:
            log(f"price {c['ticker']}: {e}"); entry["tech"] = None
        try:
            items = fetch_news(c["search"], config.get("news_window", "when:2d"), config.get("headlines_per_item", 5))
        except Exception as e:
            log(f"news {c['name']}: {e}"); items = []
        entry["news"] = news_summary(items, now)
        entry["headlines"] = items
        commodities.append(entry)

    by_symbol = {st["symbol"]: st for st in stocks}
    options = [option_check(o, by_symbol.get(o["symbol"]), now.date()) for o in config.get("options", [])]
    return {"built_at": now.isoformat(), "tz": str(tz), "mood": mood_rows, "stocks": stocks, "commodities": commodities, "options": options}


# ------------------------------------------------------------------ render
def esc(s) -> str:
    return html.escape(str(s), quote=True)


def fmt(n: float, d: int = 2) -> str:
    return f"{n:,.{d}f}"


def pct(n: float) -> str:
    return f"{n:+.2f}%"


def spark(values: list[float], up: bool) -> str:
    if len(values) < 2:
        return ""
    lo, hi = min(values), max(values)
    rng = hi - lo or 1
    w, h = 120, 32
    pts = " ".join(f"{i * w / (len(values) - 1):.1f},{h - 2 - (v - lo) / rng * (h - 4):.1f}" for i, v in enumerate(values))
    return (f'<svg class="spark" viewBox="0 0 {w} {h}" aria-hidden="true">'
            f'<polyline points="{pts}" fill="none" stroke="{"var(--up)" if up else "var(--down)"}" stroke-width="2" stroke-linejoin="round"/></svg>')


def key_headline(entry: dict) -> dict | None:
    """The one headline that most explains the lean, shown as the verdict."""
    heads = entry["headlines"]
    if not heads:
        return None
    flagged = [h for h in heads if set(h.get("bad_tags", [])) & SEVERE]
    if flagged:
        return flagged[0]
    if entry["news"]["tone"] in ("positive", "negative"):
        return max(heads, key=lambda h: abs(h.get("score", 0)))
    return None


def verdict(entry: dict) -> str:
    news, tech = entry["news"], entry["tech"]
    key = key_headline(entry)
    if key:
        return key["title"]
    if tech and tech["last"] > tech["sma20"] > tech["sma50"]:
        reason = "quiet news, price in an uptrend"
    elif tech and tech["last"] < tech["sma20"] < tech["sma50"]:
        reason = "quiet news, price in a downtrend"
    else:
        reason = "quiet news, sideways price"
    return reason[0].upper() + reason[1:] + "."


def headline_html(it: dict, now: dt.datetime) -> str:
    tags = "".join(f'<span class="tag bad">{esc(t)}</span>' for t in it.get("bad_tags", [])[:1])
    tags += "".join(f'<span class="tag good">{esc(t)}</span>' for t in it.get("good_tags", [])[:1])
    when = ""
    if it.get("when"):
        h = (now - it["when"]).total_seconds() / 3600
        when = f"{h:.0f}h ago" if h < 48 else it["when"].astimezone(now.tzinfo).strftime("%d %b")
    title = f'<a href="{esc(it["link"])}" target="_blank" rel="noopener">{esc(it["title"])}</a>' if it.get("link") else esc(it["title"])
    meta = ", ".join(x for x in (esc(it.get("source", "")), when) if x)
    return f'<li>{title}<span class="meta">{meta}</span>{tags}</li>'


def render(data: dict, demo: bool = False) -> str:
    now = dt.datetime.fromisoformat(data["built_at"])
    day = now.strftime("%a %d %b %Y")
    at = now.strftime("%H:%M:%S")
    bad_count = sum(1 for s in data["stocks"] if s["news"]["flags"])

    mood = ""
    for m in data["mood"]:
        if "error" in m:
            mood += f'<div class="mood"><span>{esc(m["name"])}</span><b class="muted">unavailable</b></div>'
        else:
            cls = "up" if m["change_pct"] >= 0 else "down"
            mood += f'<div class="mood"><span>{esc(m["name"])}</span><b class="{cls}">{pct(m["change_pct"])}</b><small>{fmt(m["last"])}</small></div>'

    cards = ""
    for s in data["stocks"]:
        t, lean = s["tech"], s["lean"]
        cls = {"Leaning up": "up", "Leaning down": "down", "No clear lean": "flat"}[lean["label"]]
        price = (f'<div class="price"><b>₹{fmt(t["last"])}</b><span class="{"up" if t["change_pct"] >= 0 else "down"}">{pct(t["change_pct"])}</span>'
                 f'<small>close {esc(t["date"])}</small></div>' if t
                 else '<div class="price muted">price unavailable</div>')
        flags = "".join(f'<span class="flag">{esc(f)}</span>' for f in s["news"]["flags"])
        key = key_headline(s)
        rest = [h for h in s["headlines"] if h is not key]
        heads = "".join(headline_html(h, now) for h in rest) or ("" if key else f'<li class="muted">{esc(s.get("news_error", "No headlines in the last two days."))}</li>')
        p_up = lean["p_up"]
        dir_word, p_show = ("up", p_up) if p_up >= 0.5 else ("down", 1 - p_up)
        rng = f' · usual day range ₹{fmt(t["range_lo"])} to ₹{fmt(t["range_hi"])}' if t else ""
        forecast = (f'<p class="forecast">Today: <b class="{"up" if dir_word == "up" else "down"}">{p_show * 100:.0f}% {dir_word}</b>{esc(rng)}</p>'
                    if abs(p_up - 0.5) >= 0.05 else f'<p class="forecast">Today: <b class="flat">coin toss</b>{esc(rng)}</p>')
        v = esc(verdict(s))
        if key and key.get("link"):
            v = f'<a href="{esc(key["link"])}" target="_blank" rel="noopener">{v}</a>'
        if key:
            v += f'<span class="meta">{esc(key.get("source", ""))}</span>'
        reasons = "".join(f"<li>{esc(r)}</li>" for r in lean["reasons"])
        cards += f'''
<section class="card tone-{cls}">
  <div class="top"><h2>{esc(s["name"])} <small>{esc(s["symbol"])}</small></h2><span class="lean {cls}">{esc(lean["label"])}</span></div>
  <p class="verdict">{v}</p>
  {flags}
  <div class="row">{price}{spark(t["spark"], t["change_5d_pct"] >= 0) if t else ""}</div>
  {forecast}
  <ul class="heads">{heads}</ul>
  <p class="why">{esc("; ".join(lean["reasons"][:3]).capitalize())}.</p>
</section>'''

    comms = ""
    for c in data["commodities"]:
        t = c["tech"]
        price = (f'<div class="price"><b>{fmt(t["last"])}</b><span class="{"up" if t["change_pct"] >= 0 else "down"}">{pct(t["change_pct"])}</span>'
                 f'<small>{esc(c["unit"])}</small></div>' if t else '<div class="price muted">price unavailable</div>')
        heads = "".join(headline_html(h, now) for h in c["headlines"]) or '<li class="muted">No headlines in the last two days.</li>'
        comms += f'''
<section class="card tone-flat">
  <div class="top"><h2>{esc(c["name"])}</h2><span class="lean flat">{esc(c["news"]["tone"])} headlines</span></div>
  <div class="row">{price}{spark(t["spark"], t["change_5d_pct"] >= 0) if t else ""}</div>
  <p class="note">{"Rising oil: bad for airlines, paints, OMCs, banks." if t and t["change_5d_pct"] > 0 else "Falling oil: good for airlines, paints, OMCs, banks." if t else ""}</p>
  <ul class="heads">{heads}</ul>
</section>'''

    opts = ""
    for o in data.get("options", []):
        cls = {"Healthy": "up", "Reasonable": "up", "Watch": "flat", "Weak": "down", "Lottery ticket": "down", "No price": "flat"}[o["verdict"]]
        opts += (f'<div class="opt"><div><b>{esc(o["symbol"])} {fmt(o["strike"], 0 if o["strike"] == int(o["strike"]) else 2)} {esc(o["type"])}</b>'
                 f'<span class="meta">expires {esc(o["expiry"])}, {", ".join(esc(n) for n in o["notes"])}</span></div>'
                 f'<span class="lean {cls}">{esc(o["verdict"])}</span></div>')
    options_html = f'<h3>Your options</h3><div class="card tone-flat">{opts}<p class="why">Premium is not available here: check it in Kite and keep your exit price written down.</p></div>' if opts else ""

    demo_banner = '<div class="banner">Demo page with canned data. Run without --demo for live headlines and prices.</div>' if demo else ""
    headline = (f"{bad_count} of your stocks {'has' if bad_count == 1 else 'have'} serious negative news this morning."
                if bad_count else "No serious negative news on your stocks this morning.")

    return f'''<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="color-scheme" content="light dark"><title>Morning Board</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Source+Serif+4:opsz,wght@8..60,500;8..60,600&display=swap" rel="stylesheet">
<style>
:root,:root[data-theme="light"]{{--bg:#F6F7F4;--card:#FFFFFF;--ink:#1B2A2F;--muted:#67757A;--line:#DCE1DD;--up:#1F8A5B;--down:#C23B3B;--flat:#5B6B72;--amber:#C98A2B;--amber-bg:#FBF1DF;--up-bg:#E4F3EB;--down-bg:#F9E3E3;color-scheme:light}}
@media(prefers-color-scheme:dark){{:root:not([data-theme="light"]){{--bg:#131A1D;--card:#1C262B;--ink:#E8EDEE;--muted:#9AA8AD;--line:#2E3B41;--up:#4CC28D;--down:#F07A7A;--flat:#9AA8AD;--amber:#E6AE55;--amber-bg:#3A2E16;--up-bg:#173426;--down-bg:#3D1F1F;color-scheme:dark}}}}
:root[data-theme="dark"]{{--bg:#131A1D;--card:#1C262B;--ink:#E8EDEE;--muted:#9AA8AD;--line:#2E3B41;--up:#4CC28D;--down:#F07A7A;--flat:#9AA8AD;--amber:#E6AE55;--amber-bg:#3A2E16;--up-bg:#173426;--down-bg:#3D1F1F;color-scheme:dark}}
.theme{{float:right;margin-top:-2px;font:inherit;font-size:13px;color:var(--muted);background:var(--card);border:1px solid var(--line);border-radius:999px;padding:3px 10px;cursor:pointer}}
*{{box-sizing:border-box}}html,body{{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 -apple-system,"Segoe UI",Roboto,sans-serif;font-variant-numeric:tabular-nums}}
body{{padding:env(safe-area-inset-top,0) 0 env(safe-area-inset-bottom,0)}}
.wrap{{max-width:680px;margin:0 auto;padding:20px 16px 48px}}
.date{{color:var(--muted);font-size:14px;margin:0}}.date b{{color:var(--ink);font-weight:600}}
h1{{font-family:"Source Serif 4",Georgia,serif;font-weight:500;font-size:24px;line-height:1.25;margin:4px 0 14px;letter-spacing:-.01em}}
.banner{{background:var(--amber-bg);color:var(--amber);padding:10px 14px;border-radius:8px;font-size:14px;margin-bottom:14px}}
.moods{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px;margin-bottom:22px}}
.mood{{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px 12px;display:flex;flex-direction:column;font-size:13px;color:var(--muted)}}
.mood b{{font-size:17px}}.mood small{{font-size:12px}}
.up{{color:var(--up)}}.down{{color:var(--down)}}.flat{{color:var(--flat)}}.muted{{color:var(--muted)}}
h3{{font-size:14px;font-weight:600;color:var(--muted);margin:24px 0 10px}}
.card{{background:var(--card);border:1px solid var(--line);border-left:5px solid var(--flat);border-radius:8px;padding:14px;margin-bottom:12px}}
.card.tone-up{{border-left-color:var(--up)}}.card.tone-down{{border-left-color:var(--down)}}
.top{{display:flex;justify-content:space-between;align-items:baseline;gap:10px;flex-wrap:wrap}}
h2{{margin:0;font-size:19px;font-weight:600}}h2 small{{font-weight:400;color:var(--muted);font-size:13px;margin-left:4px}}
.lean{{font-size:13px;font-weight:600;padding:3px 10px;border-radius:999px;white-space:nowrap}}
.lean.up{{background:var(--up-bg)}}.lean.down{{background:var(--down-bg)}}.lean.flat{{background:var(--bg)}}
.verdict a{{color:inherit;text-decoration:none}}.verdict .meta{{font-family:-apple-system,"Segoe UI",Roboto,sans-serif}}.verdict{{font-family:"Source Serif 4",Georgia,serif;font-size:18px;line-height:1.35;margin:8px 0 6px}}
.flag{{display:inline-block;background:var(--amber-bg);color:var(--amber);font-size:13px;font-weight:600;padding:3px 10px;border-radius:6px;margin:0 6px 8px 0}}
.row{{display:flex;justify-content:space-between;align-items:center;gap:12px;margin:6px 0 4px}}
.price b{{font-size:20px;font-weight:600;margin-right:8px}}.price span{{font-weight:600}}.price small{{display:block;color:var(--muted);font-size:12px}}
.spark{{width:120px;height:32px;flex:none}}
.forecast{{margin:6px 0 2px;font-size:14px;color:var(--muted)}}.forecast b{{font-weight:600}}
.heads{{list-style:none;margin:10px 0 0;padding:0}}.heads li{{padding:7px 0;border-top:1px solid var(--line);font-size:14px}}
.heads a{{color:var(--ink);text-decoration:none}}.heads a:hover{{text-decoration:underline}}
.meta{{display:block;color:var(--muted);font-size:12px;margin-top:2px}}
.tag{{display:inline-block;font-size:11px;font-weight:600;padding:1px 7px;border-radius:4px;margin:4px 4px 0 0}}
.tag.bad{{background:var(--down-bg);color:var(--down)}}.tag.good{{background:var(--up-bg);color:var(--up)}}
.why{{margin:4px 0 0;font-size:13px;color:var(--muted)}}
.opt{{display:flex;justify-content:space-between;align-items:center;gap:10px;padding:8px 0;border-top:1px solid var(--line);font-size:15px}}.opt:first-child{{border-top:0}}
.note{{font-size:14px;color:var(--muted);margin:6px 0 0}}
.foot{{font-size:13px;color:var(--muted);margin-top:28px;border-top:1px solid var(--line);padding-top:12px}}
#stale{{display:none}}
</style></head><body><div class="wrap">
<button class="theme" id="theme" type="button" aria-label="Switch theme">Theme: auto</button>
<p class="date"><b>Data updated: {esc(day)}, {esc(at)} IST</b> <span id="ago"></span></p>
<h1>{esc(headline)}</h1>
<div id="stale" class="banner">This page is more than a day old. The morning run may have failed; check the GitHub Actions tab.</div>
{demo_banner}
<div class="moods">{mood}</div>
<h3>Your stocks</h3>
{cards}
{options_html}
<h3>Crude oil</h3>
{comms}
<p class="foot">"Today" is a lean from news tone and trend, capped at 75%, not a guarantee. Prices: Yahoo Finance closes. Headlines: Google News. Not investment advice.</p>
</div>
<script>
(function(){{var r=document.documentElement,b=document.getElementById("theme"),m=["auto","light","dark"],k="board.theme";
function ap(v){{if(v==="auto")r.removeAttribute("data-theme");else r.setAttribute("data-theme",v);b.textContent="Theme: "+v;}}
var cur="auto";try{{cur=localStorage.getItem(k)||"auto";}}catch(e){{}}ap(cur);
b.onclick=function(){{cur=m[(m.indexOf(cur)+1)%3];try{{localStorage.setItem(k,cur);}}catch(e){{}}ap(cur);}};}})();
(function(){{var t=new Date("{esc(data["built_at"])}").getTime();function tick(){{var m=Math.round((Date.now()-t)/60000),s=m<1?"just now":m<60?m+" min ago":m<2880?Math.round(m/60)+" h ago":Math.round(m/1440)+" days ago";document.getElementById("ago").textContent="("+s+")";if(Date.now()-t>30*3600*1000)document.getElementById("stale").style.display="block";}}try{{tick();setInterval(tick,60000);}}catch(e){{}}}})();
</script></body></html>'''


# ------------------------------------------------------------------ demo data
def demo_fetchers(now: dt.datetime):
    import random
    rnd = random.Random(3)
    base = {"YESBANK.NS": 21.4, "SBIN.NS": 955.0, "TITAN.NS": 4423.0, "BZ=F": 84.2, "^NSEI": 25100.0, "^GSPC": 5800.0, "INR=X": 88.1}

    def prices(ticker):
        p = base.get(ticker, 100.0)
        out, d = [], now.date() - dt.timedelta(days=90)
        drift = -0.004 if ticker == "YESBANK.NS" else 0.001
        while d <= now.date() - dt.timedelta(days=1):
            if d.weekday() < 5:
                p *= 1 + rnd.gauss(drift, 0.012)
                out.append((d, round(p, 2)))
            d += dt.timedelta(days=1)
        return out

    canned = {
        "Yes Bank": ["Supreme Court rules against Yes Bank in AT-1 bond write-off case", "Yes Bank shares fall 6% after apex court verdict", "SMBC completes additional stake purchase in Yes Bank"],
        "State Bank": ["SBI confirms probe into alleged customer data breach", "SBI shares slip as investors weigh cyber incident", "SBI Q2 preview: analysts expect steady loan growth"],
        "Titan": ["Titan jewellery sales rise ahead of festive season", "Brokerage raises target on Titan after strong update", "Gold price dip seen helping Titan demand"],
        "Brent": ["Brent crude slips as OPEC+ signals output increase", "Oil steadies after US inventory draw"],
    }

    def news(query, window, limit):
        key = next((k for k in canned if k.lower() in query.lower()), None)
        items = canned.get(key, [])
        return [{"title": t, "source": "Demo wire", "link": "", "when": now - dt.timedelta(hours=3 + i * 5)} for i, t in enumerate(items)][:limit]

    return prices, news


# ------------------------------------------------------------------ telegram (optional)
def telegram_summary(data: dict, url: str | None) -> None:
    tok, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not tok or not chat:
        return
    lines = ["Morning Board"]
    for s in data["stocks"]:
        flag = " ⚠️ " + ", ".join(s["news"]["flags"]) if s["news"]["flags"] else ""
        chg = f" {s['tech']['change_pct']:+.1f}%" if s["tech"] else ""
        lines.append(f"{s['name']}{chg}: {s['lean']['label']}{flag}")
    if url:
        lines.append(url)
    try:
        httpx.post(f"https://api.telegram.org/bot{tok}/sendMessage", json={"chat_id": chat, "text": "\n".join(lines)}, timeout=TIMEOUT)
    except Exception as e:
        print(f"telegram: {e}", file=sys.stderr)


# ------------------------------------------------------------------ main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--out", default="docs/index.html")
    ap.add_argument("--json", default="docs/data.json", help="also write the raw data here ('' to skip)")
    ap.add_argument("--demo", action="store_true", help="use canned data, clearly labelled")
    ap.add_argument("--page-url", default=os.getenv("PAGE_URL"), help="link to include in the Telegram summary")
    a = ap.parse_args(argv)

    with open(a.config, encoding="utf-8") as f:
        config = json.load(f)
    now = dt.datetime.now(ZoneInfo(config.get("timezone", "Asia/Kolkata")))
    fp, fn = demo_fetchers(now) if a.demo else (yahoo_history, google_news)
    data = build(config, now, fp, fn, log=lambda m: print(m, file=sys.stderr))

    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(render(data, demo=a.demo))
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(data, f, default=str, indent=1)
    if not a.demo:
        telegram_summary(data, a.page_url)
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
