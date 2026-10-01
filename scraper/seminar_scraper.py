#!/usr/bin/env python3
"""
Seminar scraper for the Scholarship Tracker dashboard.

It visits the event pages and scholarship pages listed in scraper_sources.json,
finds webinars, info sessions, fairs and open days, and writes them to
seminars.json. Import that file in the dashboard's Seminars tab.

Setup (once):
    pip install requests beautifulsoup4

Run:
    python seminar_scraper.py --sources scraper_sources.json --out seminars.json

Options:
    --include-past     keep events whose date has already passed
    --days 180         only keep events within this many days from today
    --delay 2          seconds to wait between websites (be polite)
    --verbose          print what was found on each page

How it finds events, in order of reliability:
    1. schema.org Event data embedded in the page (JSON-LD)
    2. calendar files (.ics) linked from the page
    3. text blocks that mention an event word (webinar, fair, info session ...)
       and contain a date. These are best-effort, so check them before you
       register.

Some sites block automated visitors (for example si.se). Those show up in the
"errors" list in seminars.json; check them by hand.
"""

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
import time
import urllib.robotparser
from pathlib import Path
from urllib.parse import urljoin, urlparse

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover
    sys.exit("Missing packages. Run: pip install requests beautifulsoup4")

USER_AGENT = "ScholarshipTrackerSeminarScraper/1.0 (personal use; checks public event pages)"
TIMEOUT = 20

EVENT_WORDS = re.compile(
    r"\b(webinar|web-?seminar|seminar|info(?:rmation)?\s+session|info\s+day|open\s+(?:day|house)|"
    r"virtual\s+fair|online\s+fair|education\s+fair|study\s+fair|fair|q\s*&\s*a|live\s+session|"
    r"youtube\s+live|instagram\s+live|meet\s+us|workshop|masterclass|information\s+event)\b",
    re.I,
)

MONTHS = {m.lower(): i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"], 1)}
MONTHS.update({k[:3]: v for k, v in list(MONTHS.items())})
MONTHS["sept"] = 9
MON_RE = r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
WEEKDAY_RE = r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*,?\s+"

DATE_PATTERNS = [
    # 2026-10-07
    (re.compile(r"\b(20\d\d)-(\d{1,2})-(\d{1,2})\b"), "ymd"),
    # 7 October 2026 / Wednesday 7 October 2026 / 7 Oct
    (re.compile(r"\b(?:" + WEEKDAY_RE + r")?(\d{1,2})(?:st|nd|rd|th)?\.?\s+" + MON_RE + r"\.?,?(?:\s+(20\d\d))?\b", re.I), "dmy"),
    # October 7, 2026 / Oct 7
    (re.compile(r"\b(?:" + WEEKDAY_RE + r")?" + MON_RE + r"\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?(?:\s+(20\d\d))?\b", re.I), "mdy"),
]
TIME_RE = re.compile(
    r"\b(\d{1,2}[:.]\d{2}\s*(?:[-–—]|to)\s*\d{1,2}[:.]\d{2}|\d{1,2}[:.]\d{2})\s*"
    r"(CEST|CET|GMT|UTC|BST|EET|EEST|IST|BDT|ET|PT)?", re.I)


# ---------------------------------------------------------------- helpers

def today():
    return dt.date.today()


def make_date(y, m, d, ref):
    """Build a date. If the year is missing, pick the next occurrence after ref."""
    try:
        if y:
            return dt.date(int(y), int(m), int(d))
        cand = dt.date(ref.year, int(m), int(d))
        if cand < ref - dt.timedelta(days=30):
            cand = dt.date(ref.year + 1, int(m), int(d))
        return cand
    except ValueError:
        return None


def find_date(text, ref):
    for rx, kind in DATE_PATTERNS:
        m = rx.search(text)
        if not m:
            continue
        if kind == "ymd":
            return make_date(m.group(1), m.group(2), m.group(3), ref), m
        if kind == "dmy":
            mon = MONTHS.get(m.group(2).lower().rstrip("."))
            return make_date(m.group(3), mon, m.group(1), ref), m
        if kind == "mdy":
            mon = MONTHS.get(m.group(1).lower().rstrip("."))
            return make_date(m.group(3), mon, m.group(2), ref), m
    return None, None


def find_time(text):
    m = TIME_RE.search(text)
    if not m:
        return "", ""
    t = re.sub(r"\s*(?:[-–—]|to)\s*", "–", m.group(1)).replace(".", ":")
    return t, (m.group(2) or "").upper()


def clean(text, limit=200):
    text = re.sub(r"\s+", " ", text or "").strip()
    return text[:limit].rstrip(" ,;:-")


def event_id(title, date):
    h = hashlib.sha1(f"{date}|{title.lower()}".encode()).hexdigest()[:10]
    return f"scr-{date}-{h}"


def https_only(url):
    return url if url and url.startswith("https://") else None


# ---------------------------------------------------------------- fetching

_robots = {}


def allowed_by_robots(url):
    parts = urlparse(url)
    base = f"{parts.scheme}://{parts.netloc}"
    if base not in _robots:
        rp = urllib.robotparser.RobotFileParser()
        try:
            r = requests.get(base + "/robots.txt", headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
            rp.parse(r.text.splitlines() if r.ok else [])
        except requests.RequestException:
            rp.parse([])
        _robots[base] = rp
    return _robots[base].can_fetch(USER_AGENT, url)


def fetch(url):
    """Return (html, final_url). Supports local files for testing."""
    if url.startswith("file://") or Path(url).exists():
        path = url[7:] if url.startswith("file://") else url
        return Path(path).read_text(encoding="utf-8", errors="replace"), url
    if not allowed_by_robots(url):
        raise RuntimeError("robots.txt asks automated visitors not to read this page")
    r = requests.get(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "en"}, timeout=TIMEOUT)
    if r.status_code in (401, 403, 429):
        raise RuntimeError(f"site refused the request (HTTP {r.status_code}); check this page by hand")
    r.raise_for_status()
    return r.text, r.url


# ---------------------------------------------------------------- extractors

def from_jsonld(soup, page_url, ref):
    out = []

    def walk(node):
        if isinstance(node, list):
            for n in node:
                walk(n)
            return
        if not isinstance(node, dict):
            return
        if "@graph" in node:
            walk(node["@graph"])
        types = node.get("@type")
        types = types if isinstance(types, list) else [types]
        if any(t and "Event" in str(t) for t in types):
            start = str(node.get("startDate") or "")
            m = re.match(r"(20\d\d)-(\d\d)-(\d\d)(?:T(\d\d:\d\d))?", start)
            if not m:
                return
            loc = node.get("location") or {}
            if isinstance(loc, list):
                loc = loc[0] if loc else {}
            online = "Online" if "Online" in str(node.get("eventAttendanceMode", "")) or (
                isinstance(loc, dict) and "Virtual" in str(loc.get("@type", ""))) else ""
            out.append({
                "title": clean(node.get("name") or "Event"),
                "organizer": clean((node.get("organizer") or {}).get("name", "") if isinstance(node.get("organizer"), dict) else ""),
                "date": f"{m.group(1)}-{m.group(2)}-{m.group(3)}",
                "time": m.group(4) or "",
                "timezone": "",
                "mode": online,
                "location": clean(loc.get("name", "") if isinstance(loc, dict) else str(loc)),
                "url": https_only(urljoin(page_url, node.get("url") or page_url)),
                "confidence": "high",
            })
        for v in node.values():
            if isinstance(v, (dict, list)):
                walk(v)

    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            walk(json.loads(tag.string or "{}"))
        except (json.JSONDecodeError, TypeError):
            continue
    return out


def from_ics_links(soup, page_url, ref):
    out = []
    links = [a.get("href") for a in soup.find_all("a", href=True)
             if a["href"].lower().split("?")[0].endswith(".ics") or a["href"].startswith("webcal:")]
    for href in links[:10]:
        url = urljoin(page_url, href.replace("webcal:", "https:", 1))
        try:
            text, _ = fetch(url)
        except Exception:
            continue
        for block in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", text, re.S):
            summ = re.search(r"^SUMMARY[^:]*:(.*)$", block, re.M)
            start = re.search(r"^DTSTART[^:]*:(\d{8})(?:T(\d{2})(\d{2}))?", block, re.M)
            if not (summ and start):
                continue
            d = start.group(1)
            out.append({
                "title": clean(summ.group(1)),
                "organizer": "",
                "date": f"{d[:4]}-{d[4:6]}-{d[6:]}",
                "time": f"{start.group(2)}:{start.group(3)}" if start.group(2) else "",
                "timezone": "UTC" if block.find("Z\n") > 0 else "",
                "mode": "",
                "location": "",
                "url": https_only(page_url),
                "confidence": "high",
            })
    return out


def from_text(soup, page_url, ref):
    """Best-effort: look at headings, list items and short blocks."""
    out = []
    for bad in soup(["script", "style", "nav", "footer", "header", "noscript", "form"]):
        bad.decompose()
    candidates = soup.find_all(["h2", "h3", "h4", "li", "p", "tr", "article", "div"])
    seen_text = set()
    for el in candidates:
        if el.name == "div" and el.find(["div", "article", "li"]):
            continue  # only leaf-ish divs
        text = clean(el.get_text(" ", strip=True), 400)
        if len(text) < 8 or len(text) > 400 or text in seen_text:
            continue
        seen_text.add(text)
        # An event word may be in this block or in the nearest heading above it.
        heading = el.find_previous(["h2", "h3", "h4"]) if el.name not in ("h2", "h3", "h4") else None
        heading_text = clean(heading.get_text(" ", strip=True)) if heading else ""
        parent_text = ""
        anc = el.parent
        for _ in range(2):  # look up to two levels up for an event word
            if anc is None or anc.name in ("body", "html", "main"):
                break
            parent_text += " " + clean(anc.get_text(" ", strip=True), 800)
            anc = anc.parent
        context = f"{heading_text} {text}"
        if not EVENT_WORDS.search(context) and not EVENT_WORDS.search(parent_text):
            continue
        date, m = find_date(text, ref)
        if not date:
            continue
        title = text
        if m:
            title = clean((text[:m.start()] + " " + text[m.end():]))
        title = re.sub(r"^[\s,:\-–—|]+", "", title)
        title = TIME_RE.sub("", title)
        title = re.sub(r"(\s*,)+", ",", title)
        title = re.sub(r"[,\s]*\b(online|virtual|on campus|in person)\b[,\s]*$", "", title, flags=re.I)
        title = clean(re.split(r"(?<=[a-z0-9)])\.\s", title)[0], 140)
        if len(title) < 6 and heading_text:
            title = clean(heading_text, 140)
        elif not EVENT_WORDS.search(title) and heading_text and EVENT_WORDS.search(heading_text):
            title = clean(heading_text, 140)
        if len(title) < 6:
            continue
        t, tz = find_time(text)
        link = el.find("a", href=True) if hasattr(el, "find") else None
        url = https_only(urljoin(page_url, link["href"])) if link else https_only(page_url)
        out.append({
            "title": title,
            "organizer": "",
            "date": date.isoformat(),
            "time": t,
            "timezone": tz,
            "mode": "Online" if re.search(r"\b(online|virtual|zoom|teams|webinar|youtube)\b", context, re.I) else "",
            "location": "",
            "url": url,
            "confidence": "check",
        })
    return out


# ---------------------------------------------------------------- main

def scrape(sources, include_past=False, horizon_days=365, delay=2.0, verbose=False):
    ref = today()
    found, errors = {}, []
    last_host = None
    for src in sources:
        url = src.get("url", "")
        if not url:
            continue
        host = urlparse(url).netloc
        if last_host and host != last_host and not url.startswith("file://"):
            time.sleep(delay)
        last_host = host
        try:
            html, final_url = fetch(url)
        except Exception as e:  # noqa: BLE001
            errors.append({"url": url, "error": str(e)[:200]})
            if verbose:
                print(f"  ! {url}: {e}")
            continue
        soup = BeautifulSoup(html, "html.parser")
        events = from_jsonld(soup, final_url, ref) + from_ics_links(soup, final_url, ref)
        events += from_text(BeautifulSoup(html, "html.parser"), final_url, ref)
        kept = 0
        for ev in events:
            try:
                d = dt.date.fromisoformat(ev["date"])
            except ValueError:
                continue
            if not include_past and d < ref:
                continue
            if (d - ref).days > horizon_days:
                continue
            ev["organizer"] = ev["organizer"] or src.get("organizer", "")
            ev["related_entry_ids"] = src.get("related_entry_ids", [])
            ev["source"] = f"Scraped {ref.isoformat()} from {url}"
            ev["notes"] = "" if ev.pop("confidence") == "high" else "Found by text matching. Check the date and title on the page."
            ev["id"] = event_id(ev["title"], ev["date"])
            ev["registered"] = False
            key = (ev["date"], re.sub(r"[^a-z0-9]+", " ", ev["title"].lower()).strip())
            if key in found:
                # keep the higher-confidence copy; merge related ids
                old = found[key]
                old["related_entry_ids"] = sorted(set(old["related_entry_ids"]) | set(ev["related_entry_ids"]))
                if old["notes"] and not ev["notes"]:
                    ev["related_entry_ids"] = old["related_entry_ids"]
                    found[key] = ev
                continue
            found[key] = ev
            kept += 1
        if verbose:
            print(f"  {url}: {kept} event(s)")
    seminars = sorted(found.values(), key=lambda e: (e["date"], e["title"]))
    return seminars, errors


def main(argv=None):
    ap = argparse.ArgumentParser(description="Collect seminar and webinar details for the Scholarship Tracker.")
    ap.add_argument("--sources", default="scraper_sources.json", help="file downloaded from the dashboard")
    ap.add_argument("--out", default="seminars.json", help="file to import into the dashboard")
    ap.add_argument("--include-past", action="store_true")
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--delay", type=float, default=2.0)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    data = json.loads(Path(args.sources).read_text(encoding="utf-8"))
    sources = data["sources"] if isinstance(data, dict) else data
    print(f"Checking {len(sources)} page(s)...")
    seminars, errors = scrape(sources, args.include_past, args.days, args.delay, args.verbose)
    Path(args.out).write_text(json.dumps({
        "scraped_at": dt.datetime.now().isoformat(timespec="seconds"),
        "seminars": seminars,
        "errors": errors,
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Found {len(seminars)} seminar(s). Saved to {args.out}.")
    if errors:
        print(f"{len(errors)} page(s) could not be read. See the 'errors' list in {args.out}.")


if __name__ == "__main__":
    main()
