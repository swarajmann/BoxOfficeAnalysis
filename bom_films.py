"""
Film details for every release in daily_box_office.csv, plus IMDb genres and ratings.

For each release it pulls the Box Office Mojo release page (distributor, MPAA
rating, running time, genres, widest release, opening gross and the IMDb ID),
then joins IMDb's free datasets on that exact ID, so no fuzzy title matching.

One-time setup:
    Download these two files from https://datasets.imdbws.com/ into this
    folder and leave them zipped:
        title.basics.tsv.gz
        title.ratings.tsv.gz

Usage (run after bom_daily.py combine):
    python bom_films.py scrape --min-theaters 100 --limit 10   # quick test
    python bom_films.py scrape --min-theaters 100              # full run
    python bom_films.py retry-failed
    python bom_films.py build

Outputs:
    films.csv        one row per release (your Films table in Power BI)
    film_genres.csv  one row per release per genre (for genre slicers)
"""

import argparse
import random
import re
import sys
import time
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup

from bom_daily import MAX_DELAY, MIN_DELAY, fetch_html, to_number
from bom_daily import OUT_CSV as DAILY_CSV

RELEASE_URL = "https://www.boxofficemojo.com/release/{rid}/"
RAW_DIR = Path("raw_releases")
FAILED_LOG = Path("failed_releases.txt")
FILMS_CSV = Path("films.csv")
GENRES_CSV = Path("film_genres.csv")
MPAA_OVERRIDES = Path("mpaa_overrides.csv")   # you fill this in: release_id, mpaa
MPAA_MISSING = Path("mpaa_missing.csv")       # written by build: standard releases with no rating

# Distributors that mostly handle events, concerts and special screenings
EVENT_DISTRIBUTORS = ("fathom", "amc theatres", "trafalgar", "iconic events", "screenvision")
IMDB_BASICS = Path("title.basics.tsv.gz")
IMDB_RATINGS = Path("title.ratings.tsv.gz")

SUMMARY_LABELS = {
    "Distributor": "distributor",
    "Opening": "opening_text",
    "Release Date": "release_date_text",
    "MPAA": "mpaa",
    "MPA": "mpaa",   # current label on Box Office Mojo
    "Running Time": "running_time_text",
    "Genres": "genres_bom",
    "In Release": "in_release_text",
    "Widest Release": "widest_release_text",
}


# ---------- Parsing ----------
def parse_release(html, rid):
    """Read the label/value summary block on a release page."""
    soup = BeautifulSoup(html, "lxml")
    rec = {"release_id": rid}

    for div in soup.find_all("div"):
        spans = div.find_all("span", recursive=False)
        if not spans:
            continue
        label = " ".join(spans[0].get_text().split())
        key = SUMMARY_LABELS.get(label)
        if not key or rec.get(key):
            continue  # not a summary label, or already filled with a real value

        # Read everything in the block after the label, however it's nested
        lines = [t.strip() for t in div.get_text("\n").split("\n") if t.strip()]
        if lines and lines[0] == label:
            lines = lines[1:]
        lines = [x for x in lines if x != "See full company information"]
        if not lines:
            continue  # empty block; keep looking for the real one

        if key == "genres_bom":
            parts = []
            for line in lines:
                parts.extend(w for w in line.split() if w not in ("·", "&middot;", "|"))
            rec[key] = "|".join(parts)
        else:
            rec[key] = " ".join(" ".join(lines).split())

    link = soup.find("a", href=re.compile(r"/title/tt\d+"))
    rec["tconst"] = re.search(r"tt\d+", link["href"]).group(0) if link else None
    return rec


# ---------- Scraping ----------
def releases_from_daily(min_theaters):
    daily = pd.read_csv(DAILY_CSV, usecols=["release_id", "theaters"])
    widest = daily.dropna(subset=["release_id"]).groupby("release_id")["theaters"].max()
    return sorted(widest[widest.fillna(0) >= min_theaters].index)


def scrape_releases(ids):
    RAW_DIR.mkdir(exist_ok=True)
    session = requests.Session()
    todo = [r for r in ids if not (RAW_DIR / f"{r}.csv").exists()]
    print(f"{len(ids)} releases requested, {len(todo)} still to fetch")

    for i, rid in enumerate(todo, 1):
        try:
            html = fetch_html(session, RELEASE_URL.format(rid=rid))
            rec = parse_release(html, rid) if html else {"release_id": rid}
            pd.DataFrame([rec]).to_csv(RAW_DIR / f"{rid}.csv", index=False)
            print(f"[{i}/{len(todo)}] {rid}: MPAA {rec.get('mpaa', '-')} | "
                  f"{rec.get('genres_bom', '-')} | {rec.get('tconst', '-')}")
        except Exception as e:
            print(f"[{i}/{len(todo)}] {rid}: FAILED ({e})", file=sys.stderr)
            with FAILED_LOG.open("a") as f:
                f.write(f"{rid}\n")
        time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))


def retry_failed():
    if not FAILED_LOG.exists():
        print("No failed releases.")
        return
    ids = sorted({x.strip() for x in FAILED_LOG.read_text().split() if x.strip()})
    FAILED_LOG.unlink()
    scrape_releases(ids)


# ---------- Building ----------
def runtime_minutes(text):
    if not isinstance(text, str):
        return None
    h = re.search(r"(\d+)\s*hr", text)
    m = re.search(r"(\d+)\s*min", text)
    if not h and not m:
        return None
    return (int(h.group(1)) if h else 0) * 60 + (int(m.group(1)) if m else 0)


def load_imdb(tconsts):
    """Pull only the matching rows out of IMDb's large files."""
    keep = []
    for chunk in pd.read_csv(IMDB_BASICS, sep="\t", dtype=str, na_values="\\N", quoting=3,
                             usecols=["tconst", "genres", "runtimeMinutes", "startYear"],
                             chunksize=500_000):
        keep.append(chunk[chunk["tconst"].isin(tconsts)])
    basics = pd.concat(keep).rename(columns={"genres": "imdb_genres",
                                             "runtimeMinutes": "imdb_runtime_min",
                                             "startYear": "imdb_year"})
    ratings = pd.read_csv(IMDB_RATINGS, sep="\t", dtype={"tconst": str},
                          na_values="\\N", quoting=3)
    ratings = ratings[ratings["tconst"].isin(tconsts)].rename(
        columns={"averageRating": "imdb_rating", "numVotes": "imdb_votes"})
    return basics.merge(ratings, on="tconst", how="left")


def audience_segment(row):
    """Simple grouping for showtime and day-of-week analysis."""
    genres = row["genre_list"]
    mpaa = row.get("mpaa")
    if "Animation" in genres or "Family" in genres or mpaa in ("G", "PG"):
        return "Family"
    if mpaa == "R":
        return "Adult (R)"
    if mpaa == "PG-13":
        return "General (PG-13)"
    return "Other / Unrated"


def build():
    frames = []
    for p in sorted(RAW_DIR.glob("*.csv")):
        try:
            frames.append(pd.read_csv(p, dtype=str))
        except pd.errors.EmptyDataError:
            pass
    if not frames:
        print("No release files found. Run scrape first.")
        return
    films = pd.concat(frames, ignore_index=True)

    # Summary of each release's run from the daily table
    daily = pd.read_csv(DAILY_CSV, usecols=["release_id", "title", "date", "theaters", "daily_gross"],
                        parse_dates=["date"]).sort_values("date")
    runs = daily.groupby("release_id").agg(
        title=("title", "first"),
        first_date_in_data=("date", "min"),
        last_date_in_data=("date", "max"),
        days_in_data=("date", "nunique"),
        widest_theaters_in_data=("theaters", "max"),
        gross_in_data=("daily_gross", "sum"),
    ).reset_index()
    films = runs.merge(films, on="release_id", how="right")

    # Clean Box Office Mojo fields
    films["opening_gross"] = to_number(films.get("opening_text", pd.Series(dtype=str)).str.split().str[0])
    films["widest_release_theaters"] = to_number(
        films.get("widest_release_text", pd.Series(dtype=str)).str.split().str[0])
    films["runtime_min"] = films.get("running_time_text", pd.Series(dtype=str)).apply(runtime_minutes)

    # IMDb join on exact ID
    if IMDB_BASICS.exists() and IMDB_RATINGS.exists():
        tconsts = set(films["tconst"].dropna())
        films = films.merge(load_imdb(tconsts), on="tconst", how="left")
        films["imdb_rating"] = pd.to_numeric(films["imdb_rating"], errors="coerce")
        films["imdb_votes"] = pd.to_numeric(films["imdb_votes"], errors="coerce")
    else:
        print("IMDb files not found; skipping ratings join (genres come from Box Office Mojo).")
        films["imdb_genres"] = pd.NA

    # One genre list per film: IMDb first, Box Office Mojo as fallback
    def genre_list(row):
        if isinstance(row.get("imdb_genres"), str):
            return [g.strip() for g in row["imdb_genres"].split(",") if g.strip()]
        if isinstance(row.get("genres_bom"), str):
            return [g.strip() for g in row["genres_bom"].split("|") if g.strip()]
        return []

    films["genre_list"] = films.apply(genre_list, axis=1)
    films["primary_genre"] = films["genre_list"].str[0]

    # Release type: keep re-releases and events out of most analyses
    def release_type(row):
        title = str(row.get("title") or "")
        if re.search(r"re-?release|anniversary", title, re.I):
            return "Re-release"
        dist = str(row.get("distributor") or "").lower()
        if any(x in dist for x in EVENT_DISTRIBUTORS):
            return "Event"
        g = row["genre_list"]
        if "Music" in g and ("Documentary" in g or len(g) == 1):
            return "Event"   # concert films
        return "Standard"

    films["release_type"] = films.apply(release_type, axis=1)

    # Fill missing ratings from another release of the same film (same IMDb ID)
    films["mpaa"] = films["mpaa"] if "mpaa" in films.columns else pd.NA
    known = films.dropna(subset=["mpaa", "tconst"]).groupby("tconst")["mpaa"].first()
    films["mpaa"] = films["mpaa"].fillna(films["tconst"].map(known))

    # Apply your manual fixes, if you've made them
    if MPAA_OVERRIDES.exists():
        ov = pd.read_csv(MPAA_OVERRIDES, dtype=str).dropna(subset=["release_id", "mpaa"])
        ov["mpaa"] = ov["mpaa"].str.strip()
        ov = ov[ov["mpaa"] != ""].set_index("release_id")["mpaa"]
        films["mpaa"] = films["release_id"].map(ov).fillna(films["mpaa"])
        print(f"Applied {len(ov):,} manual ratings from {MPAA_OVERRIDES}")

    films["audience_segment"] = films.apply(audience_segment, axis=1)

    # List standard releases still missing a rating, biggest first, for you to fill in
    missing = films[(films["release_type"] == "Standard") & films["mpaa"].isna()]
    missing = missing.sort_values("widest_theaters_in_data", ascending=False)
    missing[["release_id", "title", "distributor", "widest_theaters_in_data", "genres_bom"]].assign(
        mpaa="").to_csv(MPAA_MISSING, index=False)
    films["genres"] = films["genre_list"].str.join(", ")

    # Long genre table for many-to-many slicing in Power BI
    genres = films[["release_id", "genre_list"]].explode("genre_list").dropna()
    genres = genres.rename(columns={"genre_list": "genre"})

    cols = ["release_id", "tconst", "title", "distributor", "release_type", "mpaa", "audience_segment",
            "primary_genre", "genres", "runtime_min", "opening_gross",
            "widest_release_theaters", "release_date_text", "imdb_rating", "imdb_votes",
            "first_date_in_data", "last_date_in_data", "days_in_data",
            "widest_theaters_in_data", "gross_in_data"]
    films = films[[c for c in cols if c in films.columns]]

    films.to_csv(FILMS_CSV, index=False)
    genres.to_csv(GENRES_CSV, index=False)
    matched = films["imdb_rating"].notna().sum() if "imdb_rating" in films else 0
    print(f"Saved {len(films):,} films ({matched:,} with IMDb ratings) -> {FILMS_CSV}")
    print(f"Saved {len(genres):,} film-genre rows -> {GENRES_CSV}")
    print("Release types: " + ", ".join(f"{k} {v:,}" for k, v in films["release_type"].value_counts().items()))
    print(f"{len(missing):,} standard releases still have no rating -> {MPAA_MISSING}")


# ---------- CLI ----------
def main():
    parser = argparse.ArgumentParser(description="Film details + IMDb join")
    sub = parser.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scrape", help="fetch release pages for films in daily_box_office.csv")
    s.add_argument("--min-theaters", type=int, default=100,
                   help="only films whose widest release reached this many theatres (default 100)")
    s.add_argument("--limit", type=int, default=None, help="only fetch the first N (for testing)")

    sub.add_parser("retry-failed", help="re-try releases listed in failed_releases.txt")
    sub.add_parser("build", help="clean, join IMDb, and write films.csv and film_genres.csv")

    args = parser.parse_args()
    if args.cmd == "scrape":
        ids = releases_from_daily(args.min_theaters)
        scrape_releases(ids[: args.limit] if args.limit else ids)
    elif args.cmd == "retry-failed":
        retry_failed()
    elif args.cmd == "build":
        build()


if __name__ == "__main__":
    main()
