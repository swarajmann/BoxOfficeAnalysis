"""
Daily domestic (US + Canada) box office collector for Box Office Mojo.

Pulls one page per date (every film that played that day), saves each day
as its own CSV so the run can stop and resume, then combines and cleans
everything into one table for Power BI.

Setup:
    pip install requests beautifulsoup4 lxml pandas

Usage:
    python bom_daily.py scrape --start 2021-11-01 --end 2026-11-10
    python bom_daily.py retry-failed
    python bom_daily.py combine

Notes:
    - Re-running "scrape" skips dates already saved in raw_daily/.
    - Keep the delay in place: this is a slow, one-time personal pull.
    - Box Office Mojo's terms restrict automated collection; keep the raw
      data private and credit the source in your write-up.
"""

import argparse
import random
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup

# ---------- Settings ----------
BASE_URL = "https://www.boxofficemojo.com/date/{d}/"
RAW_DIR = Path("raw_daily")              # one CSV per date
OUT_CSV = Path("daily_box_office.csv")   # final combined table
FAILED_LOG = Path("failed_dates.txt")
HEADERS = {"User-Agent": "Mozilla/5.0 (personal portfolio project; low volume)"}
MIN_DELAY, MAX_DELAY = 2.0, 4.0          # seconds between requests
MAX_RETRIES = 4

# Page headers -> clean column names
COLUMN_MAP = {
    "TD": "rank_today",
    "YD": "rank_yesterday",
    "Release": "title",
    "Daily": "daily_gross",
    "%± YD": "pct_change_yd",
    "%± LW": "pct_change_lw",
    "Theaters": "theaters",
    "Avg": "per_theater_avg",
    "To Date": "gross_to_date",
    "Days": "days_in_release",
    "Distributor": "distributor",
}


# ---------- Fetching ----------
def fetch_html(session, url):
    """Return page HTML, None if the date has no page, or raise after retries."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = session.get(url, headers=HEADERS, timeout=30)
            if r.status_code == 200:
                return r.text
            if r.status_code == 404:
                return None
            # Back off hard if the site signals we're going too fast
            wait = 60 * attempt if r.status_code in (429, 503) else 10 * attempt
            reason = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            wait = 10 * attempt
            reason = type(e).__name__
        print(f"    {reason}, retry {attempt}/{MAX_RETRIES} in {wait}s", file=sys.stderr)
        time.sleep(wait)
    raise RuntimeError(f"gave up after {MAX_RETRIES} attempts")


# ---------- Parsing ----------
def parse_day(html, d):
    """Turn one date page into a DataFrame (one row per film)."""
    soup = BeautifulSoup(html, "lxml")

    # Find the main chart table (the one with a "Release" header)
    table = None
    for t in soup.find_all("table"):
        if any(th.get_text(strip=True) == "Release" for th in t.find_all("th")):
            table = t
            break
    if table is None:
        return pd.DataFrame()

    rows = table.find_all("tr")
    headers = [" ".join(th.get_text().split()) for th in rows[0].find_all("th")]

    records = []
    for tr in rows[1:]:
        cells = tr.find_all("td")
        if len(cells) != len(headers):
            continue
        rec = {
            COLUMN_MAP.get(h, h.lower().replace(" ", "_")): " ".join(c.get_text().split())
            for h, c in zip(headers, cells)
        }
        # Release ID keeps re-releases separate from the original run
        link = tr.find("a", href=re.compile(r"/release/rl\d+"))
        rec["release_id"] = re.search(r"rl\d+", link["href"]).group(0) if link else None
        rec["date"] = d.isoformat()
        records.append(rec)

    return pd.DataFrame(records)


# ---------- Scraping ----------
def scrape_dates(dates):
    RAW_DIR.mkdir(exist_ok=True)
    session = requests.Session()
    todo = [d for d in dates if not (RAW_DIR / f"{d}.csv").exists()]
    print(f"{len(dates)} dates requested, {len(todo)} still to fetch")

    for i, d in enumerate(todo, 1):
        try:
            html = fetch_html(session, BASE_URL.format(d=d.isoformat()))
            df = parse_day(html, d) if html else pd.DataFrame()
            df.to_csv(RAW_DIR / f"{d}.csv", index=False)   # empty file marks "done, no data"
            print(f"[{i}/{len(todo)}] {d}: {len(df)} films")
        except Exception as e:
            print(f"[{i}/{len(todo)}] {d}: FAILED ({e})", file=sys.stderr)
            with FAILED_LOG.open("a") as f:
                f.write(f"{d}\n")
        time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))


def date_range(start, end):
    return [start + timedelta(days=n) for n in range((end - start).days + 1)]


def retry_failed():
    if not FAILED_LOG.exists():
        print("No failed dates.")
        return
    dates = sorted({date.fromisoformat(x.strip()) for x in FAILED_LOG.read_text().split() if x.strip()})
    FAILED_LOG.unlink()
    scrape_dates(dates)


# ---------- Cleaning ----------
def to_number(series):
    """'$1,234,567' / '+30.4%' / '-' -> float (NaN when blank)."""
    cleaned = series.astype(str).str.replace(r"[$,%+<]", "", regex=True).str.strip()
    return pd.to_numeric(cleaned, errors="coerce")


def combine():
    frames = []
    for p in sorted(RAW_DIR.glob("*.csv")):
        try:
            frames.append(pd.read_csv(p, dtype=str))
        except pd.errors.EmptyDataError:
            pass  # date with no data
    if not frames:
        print("No data found in raw_daily/. Run scrape first.")
        return

    df = pd.concat(frames, ignore_index=True)

    numeric_cols = ["rank_today", "rank_yesterday", "daily_gross", "pct_change_yd",
                    "pct_change_lw", "theaters", "per_theater_avg", "gross_to_date",
                    "days_in_release"]
    for c in numeric_cols:
        if c in df.columns:
            df[c] = to_number(df[c])

    df["distributor"] = df.get("distributor", pd.Series(dtype=str)).replace("-", pd.NA)
    df["date"] = pd.to_datetime(df["date"])
    df["day_of_week"] = df["date"].dt.day_name()
    df["is_fri_to_sun"] = df["date"].dt.dayofweek >= 4
    # Approximate opening date: day 1 of the run
    df["release_date_est"] = df["date"] - pd.to_timedelta(df["days_in_release"] - 1, unit="D")

    df = df.drop_duplicates(subset=["date", "release_id", "title"])
    df = df.sort_values(["date", "rank_today"])

    cols = ["date", "day_of_week", "is_fri_to_sun", "release_id", "title", "distributor",
            "rank_today", "rank_yesterday", "daily_gross", "pct_change_yd", "pct_change_lw",
            "theaters", "per_theater_avg", "gross_to_date", "days_in_release", "release_date_est"]
    df = df[[c for c in cols if c in df.columns]]

    df.to_csv(OUT_CSV, index=False)
    print(f"Saved {len(df):,} rows, {df['release_id'].nunique():,} releases, "
          f"{df['date'].min().date()} to {df['date'].max().date()} -> {OUT_CSV}")


# ---------- CLI ----------
def main():
    parser = argparse.ArgumentParser(description="Box Office Mojo daily collector")
    sub = parser.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scrape", help="fetch daily pages")
    s.add_argument("--start", required=True, help="YYYY-MM-DD")
    s.add_argument("--end", default=(date.today() - timedelta(days=1)).isoformat(),
                   help="YYYY-MM-DD (default: yesterday)")

    sub.add_parser("retry-failed", help="re-try dates listed in failed_dates.txt")
    sub.add_parser("combine", help="merge and clean saved days into one CSV")

    args = parser.parse_args()
    if args.cmd == "scrape":
        start = datetime.strptime(args.start, "%Y-%m-%d").date()
        end = datetime.strptime(args.end, "%Y-%m-%d").date()
        scrape_dates(date_range(start, end))
    elif args.cmd == "retry-failed":
        retry_failed()
    elif args.cmd == "combine":
        combine()


if __name__ == "__main__":
    main()