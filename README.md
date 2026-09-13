# Gold Desk

An always-on economic-calendar and session display for XAU/USD, built to run on an
old iPad. GitHub Actions rebuilds a single static page every 15 minutes and
publishes it to GitHub Pages; the iPad just points Safari at it.

Everything you tune lives in **`config.yaml`**. You should not need to touch the
Python or the HTML.

---

## 1. Create the repository

```bash
cd gold-desk
git init -b main
git add .
git commit -m "Gold Desk display"
gh repo create gold-desk --public --source=. --push
```

No `gh` CLI? Create an empty repo named `gold-desk` on github.com, then:

```bash
git remote add origin https://github.com/<your-username>/gold-desk.git
git push -u origin main
```

**Make it public.** Public repos get unlimited Actions minutes; a private repo
would burn roughly half its free monthly allowance on the 15-minute schedule.
Nothing here is secret — no keys, no credentials.

## 2. Turn on Pages

Repo → **Settings → Pages → Source: GitHub Actions**. That's the only setting.

## 3. Run it once

Repo → **Actions → Build and deploy Gold Desk → Run workflow**. It takes about
40 seconds. When it's green, your page is at:

```
https://<your-username>.github.io/gold-desk/
```

From then on it rebuilds every 15 minutes on its own.

## 4. Set up the iPad

1. Open the URL in Safari.
2. **Share → Add to Home Screen.** Launch it from that icon — you get the full
   screen with no Safari chrome.
3. **Settings → Display & Brightness → Auto-Lock → Never**, and leave it on
   the charger.
4. **Settings → General → Accessibility → Guided Access → On.** Open the app,
   triple-click the Home button, then Start. The iPad is now locked to this one
   screen until you triple-click and enter your passcode.
5. Optional: turn brightness down. It's a reference screen, not a monitor.

Landscape, 1024×768. The layout is built for exactly that.

---

## What updates when

| Thing | Refresh |
|---|---|
| Clock, countdown, session bar, "now" marker | every second, in the page |
| Rows greying out as they pass; routine advancing | every second, in the page |
| Events, forecasts, actuals, prices | every 15 min, via the rebuild |
| The page itself reloading to pick up a rebuild | every 5 min |

GitHub's scheduler is best-effort and often runs a few minutes late when it's
busy. The footer shows the build time so you can always see how stale you are.

**Prices are delayed.** They're fetched at build time, so the gold and DXY tiles
can be up to ~15 minutes old — the tile is labelled `delayed` so you never
mistake it for a live quote. It's context, not a feed to trade off. Set
`prices.enabled: false` in `config.yaml` to drop those tiles entirely.

## Editing your routine

In `config.yaml`:

```yaml
routine:
  - time: "13:30"
    text: "Pre-London: HTF bias, H4 levels, invalidation"
  - time: "19:15"
    text: "High-impact stand-aside — flat into the print"
    kind: block            # renders red
    days: [Tue, Wed, Thu]  # optional; omit for every day
```

Commit and push — a push to `main` triggers a rebuild, so the change is live in
about a minute.

## Changing what events appear

```yaml
filters:
  currencies:
    USD: [High, Medium]
    EUR: [High]
  always_keep: [FOMC, CPI, Non-Farm]   # shown whatever the rules above say
  never_show: [Bank Holiday]
```

Impact levels come from the feed: `High`, `Medium`, `Low`, `Holiday`.

## Running it locally

```bash
pip install pyyaml
python3 build.py                              # hits the live feed
python3 build.py --offline sample_feed.json   # no network; fixed test data
open docs/index.html
```

`sample_feed.json` is a hand-made week with a CPI day and an FOMC day in it —
useful for checking layout changes against a busy screen without waiting for a
busy week.

## Files

| File | What it is |
|---|---|
| `config.yaml` | everything you tune |
| `build.py` | fetches, filters, converts to Bangkok time, renders |
| `template.html` | the page — ES5 and flexbox only, for iOS 12 Safari |
| `.github/workflows/build.yml` | the 15-minute schedule and Pages deploy |
| `docs/index.html` | generated output; don't edit by hand |
| `sample_feed.json` | offline test data |

## Data source

Calendar: the ForexFactory weekly JSON feed at
`nfs.faireconomy.media/ff_calendar_thisweek.json` — free, no key, no account.
Prices: Yahoo Finance's public chart endpoint. Neither is an official API, so
either could change shape without notice. If the calendar fetch fails the build
fails loudly (and the last good page stays up); if a price fetch fails the build
continues and the tile shows `—`.

## When it breaks

- **Page never updates** — check Actions. GitHub disables scheduled workflows on
  repos with no activity for 60 days; pushing any commit re-enables them.
- **Page is blank on the iPad but fine on your Mac** — something in the template
  used syntax iOS 12 doesn't know. Keep it ES5: no arrow functions, no `const`,
  no template literals, no `fetch`.
- **Times look wrong** — everything converts through `timezone:` in
  `config.yaml`. The session bands convert from each market's own zone, so they
  follow that market's DST automatically; Bangkok has no DST.
