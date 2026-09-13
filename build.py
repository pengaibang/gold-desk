#!/usr/bin/env python3
"""
Gold Desk — builds a static wall-display page for an old iPad.

Pulls the ForexFactory weekly calendar (free, no key), filters it down to what
moves XAU/USD, converts everything to Bangkok time, and renders template.html
into docs/index.html.

Run locally:   python3 build.py [--offline sample_feed.json] [--out docs/index.html]
In CI:         python3 build.py
"""

import argparse
import html
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, time as dtime
from zoneinfo import ZoneInfo

import yaml

FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
YF_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=5m&range=1d"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 gold-desk/1.0"

HERE = os.path.dirname(os.path.abspath(__file__))


# ----------------------------------------------------------------- fetching


def fetch_json(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def fetch_calendar(offline=None):
    if offline:
        with open(offline, encoding="utf-8") as f:
            return json.load(f)
    return fetch_json(FF_URL)


def fetch_quote(symbol):
    """Return (last, change_abs, change_pct) or None. Never raises."""
    try:
        d = fetch_json(YF_URL.format(sym=urllib.parse.quote(symbol)))
        res = d["chart"]["result"][0]
        meta = res["meta"]
        last = meta.get("regularMarketPrice")
        prev = meta.get("chartPreviousClose") or meta.get("previousClose")
        if last is None:
            return None
        if prev:
            return (last, last - prev, (last - prev) / prev * 100.0)
        return (last, None, None)
    except Exception as e:  # network, shape change, symbol retired — all non-fatal
        print("  quote %s failed: %s" % (symbol, e), file=sys.stderr)
        return None


# ----------------------------------------------------------------- calendar


def parse_event_time(raw):
    """ForexFactory gives ISO8601 with a numeric offset, e.g. 2026-09-16T08:30:00-04:00."""
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def keep_event(ev, filters):
    title = ev.get("title") or ""
    for bad in filters.get("never_show") or []:
        if bad.lower() in title.lower():
            return False
    for good in filters.get("always_keep") or []:
        if good.lower() in title.lower():
            return True
    allowed = (filters.get("currencies") or {}).get(ev.get("country"))
    if not allowed:
        return False
    return (ev.get("impact") or "") in allowed


def load_events(raw, cfg, tz):
    out = []
    for ev in raw:
        if not keep_event(ev, cfg.get("filters") or {}):
            continue
        when = parse_event_time(ev.get("date") or "")
        if when is None:
            continue
        local = when.astimezone(tz)
        out.append(
            {
                "dt": local,
                "epoch": int(when.timestamp()),
                "title": (ev.get("title") or "").strip(),
                "ccy": ev.get("country") or "",
                "impact": ev.get("impact") or "Low",
                "forecast": (ev.get("forecast") or "").strip() or "—",
                "previous": (ev.get("previous") or "").strip() or "—",
                "actual": (ev.get("actual") or "").strip() or "—",
            }
        )
    out.sort(key=lambda e: e["epoch"])
    return out


def split_today_week(events, now_local, rows_today, rows_week):
    """'Today' runs to 06:00 the next morning — the Asia open, not midnight,
    is where a Bangkok trading day actually ends."""
    day_end = datetime.combine(now_local.date() + timedelta(days=1), dtime(6, 0), now_local.tzinfo)
    day_start = datetime.combine(now_local.date(), dtime(0, 0), now_local.tzinfo)

    today = [e for e in events if day_start <= e["dt"] < day_end]
    later = [e for e in events if e["dt"] >= day_end]

    # If the day is quiet, backfill from the days after so the panel is never
    # empty — those rows get a day tag so they can't be misread as today's.
    if len(today) < rows_today:
        need = rows_today - len(today)
        today = today + later[:need]
        later = later[need:]

    # Prefer keeping the next upcoming events in view rather than the whole day.
    upcoming = [e for e in today if e["epoch"] >= now_local.timestamp()]
    if len(today) > rows_today:
        keep_past = max(0, rows_today - len(upcoming))
        past = [e for e in today if e["epoch"] < now_local.timestamp()]
        today = past[-keep_past:] + upcoming
        today = today[:rows_today]

    week = [e for e in later if e["impact"] in ("High", "Medium")][:rows_week]
    return today, week


# ----------------------------------------------------------------- sessions


def session_bands(cfg, now_local, tz):
    """Convert each session's local open/close into minutes-of-day in Bangkok,
    for the date currently on screen. zoneinfo handles each market's DST, and a
    session that straddles Bangkok midnight (New York always does) is emitted as
    two segments so neither end falls off the bar."""
    bands = []
    today = now_local.date()
    midnight = datetime.combine(today, dtime(0, 0), tz)
    tomorrow = midnight + timedelta(days=1)
    for s in cfg.get("sessions") or []:
        stz = ZoneInfo(s["tz"])
        oh, om = [int(x) for x in s["open"].split(":")]
        ch, cm = [int(x) for x in s["close"].split(":")]
        # Anchor on the market's own local date that overlaps our day.
        anchor = now_local.astimezone(stz).date()
        for delta in (-1, 0, 1):
            d = anchor + timedelta(days=delta)
            start = datetime.combine(d, dtime(oh, om), stz).astimezone(tz)
            end = datetime.combine(d, dtime(ch, cm), stz).astimezone(tz)
            if end <= start:
                end += timedelta(days=1)
            lo = max(start, midnight)
            hi = min(end, tomorrow)
            if hi <= lo:
                continue
            a = (lo - midnight).total_seconds() / 60.0
            b = (hi - midnight).total_seconds() / 60.0
            bands.append(
                {
                    "name": s["name"],
                    "color": s.get("color", "#8FB3A3"),
                    "row": int(s.get("row", 1)),
                    "left": a / 1440.0 * 100.0,
                    "width": (b - a) / 1440.0 * 100.0,
                    "start_min": a,
                    "end_min": b,
                }
            )
    return bands


def active_session(bands, now_local):
    mins = now_local.hour * 60 + now_local.minute
    live = [b for b in bands if b["start_min"] <= mins < b["end_min"]]
    if not live:
        return "Closed", ""
    live.sort(key=lambda b: b["start_min"])
    seen, names_list = set(), []
    for b in live:
        if b["name"] not in seen:
            seen.add(b["name"])
            names_list.append(b["name"])
    names = " + ".join(names_list)
    nxt = sorted([b for b in bands if b["start_min"] > mins], key=lambda b: b["start_min"])
    sub = ""
    if nxt:
        n = nxt[0]
        sub = "%s opens %02d:%02d" % (n["name"], int(n["start_min"]) // 60, int(n["start_min"]) % 60)
    return names, sub


# ----------------------------------------------------------------- routine

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def routine_rows(cfg, now_local):
    today_abbr = WEEKDAYS[now_local.weekday()]
    rows = []
    for r in cfg.get("routine") or []:
        days = r.get("days")
        if days and today_abbr not in days:
            continue
        h, m = [int(x) for x in r["time"].split(":")]
        rows.append(
            {
                "time": r["time"],
                "text": r["text"],
                "kind": r.get("kind", "normal"),
                "minute": h * 60 + m,
            }
        )
    rows.sort(key=lambda r: r["minute"])
    return rows


# ----------------------------------------------------------------- rendering


def esc(s):
    return html.escape(str(s), quote=True)


def fmt_price(q, digits=2):
    if not q:
        return ("—", "", "")
    last, chg, pct = q
    v = "{:,.{d}f}".format(last, d=digits)
    if chg is None:
        return (v, "", "")
    cls = "up" if chg >= 0 else "dn"
    s = "{:+,.{d}f}  {:+.2f}%".format(chg, pct, d=digits)
    return (v, s, cls)


def impact_class(imp):
    return {"High": "h", "Medium": "m"}.get(imp, "l")


def day_tag(e, today_date):
    """A row pulled in from a later day is labelled, so a time alone is never
    mistaken for today's."""
    if today_date is None or e["dt"].date() == today_date:
        return ""
    return '<span class="d">%s</span>' % e["dt"].strftime("%a")


def render_event_rows(events, now_epoch, stand_aside, today_date=None):
    if not events:
        return '<div class="row"><div class="body"><div class="name">No filtered events today.</div></div></div>'
    next_high = None
    for e in events:
        if e["impact"] == "High" and e["epoch"] >= now_epoch:
            next_high = e["epoch"]
            break
    parts = []
    for e in events:
        cls = ["row"]
        if e["epoch"] < now_epoch:
            cls.append("past")
        if next_high is not None and e["epoch"] == next_high:
            cls.append("next")
        meta = ""
        if next_high is not None and e["epoch"] == next_high:
            a = e["dt"] - timedelta(minutes=stand_aside)
            b = e["dt"] + timedelta(minutes=stand_aside)
            meta = '<div class="meta">stand-aside %s&ndash;%s</div>' % (
                a.strftime("%H:%M"),
                b.strftime("%H:%M"),
            )
        parts.append(
            '<div class="%s" data-epoch="%d" data-impact="%s">'
            '<div class="time">%s</div><div class="imp %s"></div>'
            '<span class="ccy %s">%s</span>'
            '<div class="body"><div class="name">%s</div>%s</div>'
            '<div class="fig">'
            '<div><span class="k">Act</span><span class="v act">%s</span></div>'
            '<div><span class="k">Fcst</span><span class="v">%s</span></div>'
            '<div><span class="k">Prev</span><span class="v">%s</span></div>'
            "</div></div>"
            % (
                " ".join(cls),
                e["epoch"],
                e["impact"],
                e["dt"].strftime("%H:%M"),
                impact_class(e["impact"]),
                "usd" if e["ccy"] == "USD" else "",
                esc(e["ccy"]),
                day_tag(e, today_date) + esc(e["title"]),
                meta,
                esc(e["actual"]),
                esc(e["forecast"]),
                esc(e["previous"]),
            )
        )
    return "\n".join(parts)


def render_week_rows(events, now_local):
    if not events:
        return '<div class="wrow"><div class="n">Nothing scheduled.</div></div>'
    parts = []
    for e in events:
        colors = {"High": "var(--hi)", "Medium": "var(--med)"}
        key = " key" if ("FOMC" in e["title"] or "Federal Funds" in e["title"]) else ""
        pip = "var(--green)" if key else colors.get(e["impact"], "var(--low)")
        parts.append(
            '<div class="wrow%s"><div class="t">%s</div>'
            '<div class="pip" style="background:%s;"></div>'
            '<div class="n"><span class="d">%s</span>%s</div></div>'
            % (key, e["dt"].strftime("%H:%M"), pip, e["dt"].strftime("%a"), esc(e["title"]))
        )
    return "\n".join(parts)


def render_routine_rows(rows, now_local):
    if not rows:
        return '<div class="task"><div class="txt">No routine set for today.</div></div>'
    mins = now_local.hour * 60 + now_local.minute
    # The row whose slot we are currently inside: latest one already started.
    active_idx = -1
    for i, r in enumerate(rows):
        if r["minute"] <= mins:
            active_idx = i
    parts = []
    for i, r in enumerate(rows):
        cls = ["task"]
        if r["kind"] == "block":
            cls.append("block")
        elif i == active_idx:
            cls.append("active")
        elif i < active_idx:
            cls.append("done")
        parts.append(
            '<div class="%s" data-minute="%d"><div class="t">%s</div>'
            '<div class="pip"></div><div class="txt">%s</div></div>'
            % (" ".join(cls), r["minute"], r["time"], esc(r["text"]))
        )
    return "\n".join(parts)


def render_bands(bands):
    tops = {1: 18, 2: 42}
    return "\n".join(
        '<div class="band" style="left:%.2f%%;width:%.2f%%;top:%dpx;background:%s;"><span>%s</span></div>'
        % (b["left"], b["width"], tops.get(b["row"], 18), b["color"], esc(b["name"]))
        for b in bands
    )


def build(offline=None, out_path=None):
    with open(os.path.join(HERE, "config.yaml"), encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    tz = ZoneInfo(cfg.get("timezone", "Asia/Bangkok"))
    now_local = datetime.now(tz)
    layout = cfg.get("layout") or {}

    print("Building for %s" % now_local.strftime("%Y-%m-%d %H:%M %Z"))

    raw = fetch_calendar(offline)
    print("  feed: %d events" % len(raw))
    events = load_events(raw, cfg, tz)
    print("  after filter: %d" % len(events))

    today, week = split_today_week(
        events, now_local, int(layout.get("today_rows", 7)), int(layout.get("week_rows", 6))
    )

    bands = session_bands(cfg, now_local, tz)
    sess_name, sess_sub = active_session(bands, now_local)
    routine = routine_rows(cfg, now_local)[: int(layout.get("routine_rows", 6))]

    # Prices — optional, delayed, and never allowed to fail the build.
    pcfg = cfg.get("prices") or {}
    gold = dollar = None
    if pcfg.get("enabled", True):
        g = pcfg.get("gold") or {}
        gold = fetch_quote(g.get("symbol", "XAUUSD=X"))
        if gold is None and g.get("fallback"):
            gold = fetch_quote(g["fallback"])
        d = pcfg.get("dollar") or {}
        dollar = fetch_quote(d.get("symbol", "DX-Y.NYB"))

    gv, gs, gc = fmt_price(gold)
    dv, ds, dc = fmt_price(dollar)

    next_high = next(
        (e for e in today if e["impact"] == "High" and e["epoch"] >= now_local.timestamp()), None
    )

    now_min = now_local.hour * 60 + now_local.minute

    with open(os.path.join(HERE, "template.html"), encoding="utf-8") as f:
        tpl = f.read()

    has_today = any(e["dt"].date() == now_local.date() for e in today)
    head = ("Today — " + now_local.strftime("%A %-d %B")) if has_today else "Next up"

    subs = {
        "DATE_LONG": now_local.strftime("%a %-d %b %Y"),
        "DATE_HEAD": head,
        "CLOCK": now_local.strftime("%H:%M:%S"),
        "GOLD_LABEL": esc((pcfg.get("gold") or {}).get("label", "XAU/USD")),
        "GOLD_VALUE": gv,
        "GOLD_SUB": gs,
        "GOLD_CLASS": gc,
        "DXY_LABEL": esc((pcfg.get("dollar") or {}).get("label", "DXY")),
        "DXY_VALUE": dv,
        "DXY_SUB": ds,
        "DXY_CLASS": dc,
        "PRICE_NOTE": "delayed" if (gold or dollar) else "no feed",
        "NEXT_EPOCH": str(next_high["epoch"]) if next_high else "0",
        "NEXT_LABEL": esc(next_high["title"]) + " · " + next_high["dt"].strftime("%H:%M")
        if next_high
        else "nothing high-impact left today",
        "SESSION": esc(sess_name),
        "SESSION_SUB": esc(sess_sub),
        "FILTER_NOTE": esc(filter_note(cfg)),
        "EVENT_ROWS": render_event_rows(
            today,
            now_local.timestamp(),
            int(cfg.get("stand_aside_minutes", 15)),
            now_local.date(),
        ),
        "WEEK_ROWS": render_week_rows(week, now_local),
        "ROUTINE_ROWS": render_routine_rows(routine, now_local),
        "BANDS": render_bands(bands),
        "NOW_PCT": "%.3f" % (now_min / 1440.0 * 100.0),
        "NOW_HHMM": now_local.strftime("%H:%M"),
        "BUILT_AT": now_local.strftime("%H:%M"),
        "TZ_OFFSET_MIN": str(int(now_local.utcoffset().total_seconds() // 60)),
        "WEEK_TAG": "wk%s" % now_local.strftime("%V"),
    }

    page = tpl
    for k, v in subs.items():
        page = page.replace("{{%s}}" % k, str(v))

    leftover = re.findall(r"\{\{([A-Z_]+)\}\}", page)
    if leftover:
        print("  WARNING unfilled placeholders: %s" % sorted(set(leftover)), file=sys.stderr)

    out_path = out_path or os.path.join(HERE, "docs", "index.html")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(page)
    print("  wrote %s (%d bytes)" % (out_path, len(page)))
    return out_path


def filter_note(cfg):
    cur = (cfg.get("filters") or {}).get("currencies") or {}
    short = {"High": "high", "Medium": "med", "Low": "low"}
    bits = []
    for c, lv in cur.items():
        bits.append("%s %s" % (c, "+".join(short.get(x, x.lower()) for x in lv)))
    return " · ".join(bits)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", help="path to a saved ForexFactory JSON, skips the network")
    ap.add_argument("--out", help="output path (default docs/index.html)")
    args = ap.parse_args()

    try:
        build(args.offline, args.out)
    except urllib.error.URLError as e:
        print("FATAL: could not reach the calendar feed: %s" % e, file=sys.stderr)
        sys.exit(1)
