# Morning Board

One page, every weekday at 8:15 AM IST, on your phone: overnight news for the
stocks you follow, crude oil, market mood, and a plain-language lean for each
stock ("Yes Bank, leaning down: court case in the news").

It is a single HTML file. GitHub builds it for you every morning for free and
hosts it at a link you can add to your phone's home screen. No server, no API
keys, no laptop left on.

## What you see

- A one-line headline: how many of your stocks have **serious negative news**
  (court cases, data breaches, regulator action, probes).
- Market mood: Nifty 50, the US market overnight, and USD/INR.
- One card per stock: price and 5-day sparkline, a verdict sentence, headlines
  from the last two days tagged good or bad, and a "Why" list explaining the lean.
- Crude oil: price, headlines, and what it usually means for Indian stocks.

Each card also shows a "Today" line: a direction with a percentage (capped at 75%) and the usual day range from recent volatility. The lean is **not a forecast**. It adds up headline tone, trend (20- and 50-day
averages), yesterday's move, RSI and the US close, and caps the result at
"leaning down" when serious negative news is present. Read the headlines it
flags; that is the real value.

## Set up in 10 minutes

1. **Create a GitHub account** (free) if you don't have one, at github.com.
2. **Create a new repository** called `morning-board`. Tick "Public" (needed for
   free Pages hosting) and upload all the files in this folder, keeping the
   `.github/workflows/` and `docs/` folders as they are.
3. **Turn on hosting:** repository → *Settings* → *Pages* → under "Build and
   deployment", set *Source* to "Deploy from a branch", *Branch* to `main` and
   folder to `/docs`. Save. After a minute your page is at
   `https://<your-username>.github.io/morning-board/`.
4. **Allow the robot to write:** *Settings* → *Actions* → *General* → "Workflow
   permissions" → choose "Read and write permissions". Save.
5. **Run it once now:** *Actions* tab → "Morning board" → *Run workflow*. In about
   a minute the page updates with live news. From then on it runs itself every
   weekday at 8:15 AM IST.
6. **On your phone:** open the link in Chrome or Safari and choose "Add to Home
   Screen". It opens like an app.

## Choose your stocks

Edit `config.json`. Each stock needs the NSE symbol, a display name, and a news
search phrase. Quotes make the search exact, which keeps out junk:

```json
{"symbol": "RELIANCE", "name": "Reliance", "search": "\"Reliance Industries\""}
```

Add as many as you like. Commit the file and the next run picks it up. The
`market_mood` and `commodities` lists work the same way, using Yahoo Finance
tickers (`BZ=F` is Brent crude, `CL=F` is WTI, `GC=F` is gold).

## Your options

List the contracts you hold in `config.json`:

```json
{"symbol": "SBIN", "strike": 990, "type": "CE", "expiry": "2026-10-27"}
```

Each morning the board shows, per contract: sessions left, how far the strike
is in "usual days of movement", whether today's lean helps or hurts it, and a
verdict: Healthy, Reasonable, Watch, Weak or Lottery ticket. The premium is not
available for free, so your loss-exit price still comes from your broker app.

## Optional: Telegram message every morning

Repository → *Settings* → *Secrets and variables* → *Actions* → add
`TELEGRAM_BOT_TOKEN` (from @BotFather) and `TELEGRAM_CHAT_ID` (your chat id).
The run then sends a short summary with the page link.

## Run on your computer instead

```bash
pip install httpx
python build_board.py            # writes docs/index.html with live data
python build_board.py --demo     # canned data, clearly labelled, to see the layout
python -m pytest -q test_board.py
```

Open `docs/index.html` in any browser.

## Things to know

- Prices are **previous closes** from Yahoo Finance. The page is a pre-market
  briefing, not a live ticker.
- Headlines come from Google News and are tagged by keyword matching. It will
  sometimes mis-tag a headline, so the tags are there to draw your eye, not to be
  trusted blindly.
- If a run fails (Yahoo or Google hiccup), the previous page stays up and the
  page shows a yellow banner once it is more than 30 hours old. Check the
  *Actions* tab for the error and press *Run workflow* again.
- Nothing here is investment advice. Everything is fetched fresh on each run.
